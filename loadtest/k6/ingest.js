// Escenario principal: POST /v1/positions (endpoint individual) con llegada a
// tasa constante. Cada iteración usa el elemento `iterationInTest` del conjunto
// preparado: el mismo índice nunca se envía dos veces aunque haya concurrencia.
//
//   docker run --rm --network fleet_default -v <runs>:/runs -v <k6>:/scripts grafana/k6:2.3.0 run \
//     -e RUN_ID=short-01 -e API_URL=http://api:8000 -e RATE=10 -e DURATION=30 \
//     --out json=/runs/short-01/k6-points-ingest.json /scripts/ingest.js
import exec from "k6/execution";
import http from "k6/http";
import { SharedArray } from "k6/data";
import { API_URL, RUN_DIR, classify, latency, num, overflow, record, summary, thresholds } from "./common.js";

const data = new SharedArray("requests", () => JSON.parse(open(`${RUN_DIR}/${__ENV.DATASET || "dataset.json"}`)));
const RATE = num("RATE", 10);
const DURATION = num("DURATION", 30);
const TIMEOUT = __ENV.REQUEST_TIMEOUT || "10s";

export const options = {
  scenarios: {
    ingest: {
      executor: "constant-arrival-rate",
      rate: RATE,
      timeUnit: "1s",
      duration: `${DURATION}s`,
      // Con picos de ~1 s de latencia, una tasa R necesita ~R VUs en vuelo:
      // se preasignan 2R para no descartar iteraciones al crear VUs tarde.
      preAllocatedVUs: num("PRE_VUS", Math.max(20, Math.ceil(RATE * 2))),
      maxVUs: num("MAX_VUS", Math.max(60, Math.ceil(RATE * 6))),
      gracefulStop: "15s",
    },
  },
  thresholds: thresholds(),
  summaryTrendStats: ["avg", "min", "med", "p(90)", "p(95)", "p(99)", "max", "count"],
  discardResponseBodies: false,
};

const headers = { "Content-Type": "application/json" };

export default function () {
  const i = exec.scenario.iterationInTest;
  if (i >= data.length) {
    overflow.add(1);
    return;
  }
  const item = data[i];
  const res = http.post(`${API_URL}/v1/positions`, item.body, {
    headers,
    timeout: TIMEOUT,
    tags: { name: "POST /v1/positions", kind: item.kind },
    responseCallback: http.expectedStatuses(202, 422),
  });
  let status;
  if (res.status === 0) status = res.error_code === 1050 ? "timeout" : "connection";
  else if (res.status === 202) status = "accepted";
  else if (res.status === 422) status = "rejected";
  else status = String(res.status);
  const outcome = classify(item.kind, status);
  latency.add(res.timings.duration, { kind: item.kind });
  record(item, res.status === 0 ? status : res.status, outcome);
}

export function handleSummary(d) {
  return summary(d, __ENV.SCENARIO_NAME || "ingest");
}
