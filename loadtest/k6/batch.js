// Escenario de lotes: POST /v1/positions/batch. El conjunto se prepara con
// `--mode batch --batch-size N`; la iteración k envía los elementos
// [k*N, (k+1)*N). Cada evento del lote registra su propio resultado.
import exec from "k6/execution";
import http from "k6/http";
import { SharedArray } from "k6/data";
import { API_URL, RUN_DIR, classify, latency, num, overflow, record, summary, thresholds } from "./common.js";

const data = new SharedArray("requests", () => JSON.parse(open(`${RUN_DIR}/dataset.json`)));
const BATCH = num("BATCH_SIZE", 20);
const RATE = num("RATE", 2);
const DURATION = num("DURATION", 30);

export const options = {
  scenarios: {
    batch: {
      executor: "constant-arrival-rate",
      rate: RATE,
      timeUnit: "1s",
      duration: `${DURATION}s`,
      preAllocatedVUs: num("PRE_VUS", 5),
      maxVUs: num("MAX_VUS", 30),
      gracefulStop: "15s",
    },
  },
  thresholds: thresholds(),
  summaryTrendStats: ["avg", "min", "med", "p(90)", "p(95)", "p(99)", "max", "count"],
};

const headers = { "Content-Type": "application/json" };

export default function () {
  const start = exec.scenario.iterationInTest * BATCH;
  if (start >= data.length) {
    overflow.add(1);
    return;
  }
  const items = [];
  for (let k = start; k < Math.min(start + BATCH, data.length); k++) items.push(data[k]);
  const res = http.post(`${API_URL}/v1/positions/batch`, `{"events":[${items.map((x) => x.body).join(",")}]}`, {
    headers,
    timeout: __ENV.REQUEST_TIMEOUT || "15s",
    tags: { name: "POST /v1/positions/batch" },
    responseCallback: http.expectedStatuses(200),
  });
  latency.add(res.timings.duration, { kind: "batch" });

  let results = null;
  if (res.status === 200) {
    try {
      results = res.json("results");
    } catch (_e) {
      results = null;
    }
  }
  items.forEach((item, index) => {
    let status;
    if (res.status === 0) status = res.error_code === 1050 ? "timeout" : "connection";
    else if (res.status !== 200) status = String(res.status);
    else {
      // Correlación por índice Y event_id; ausente o distinto = sin confirmación.
      const r = Array.isArray(results) ? results.find((x) => x && x.index === index) : null;
      status = r && r.event_id === item.event_id ? r.status : "missing";
    }
    const outcome = status === "missing" ? "ambiguous" : classify(item.kind, status);
    record(item, status, outcome);
  });
}

export function handleSummary(d) {
  return summary(d, __ENV.SCENARIO_NAME || "batch");
}
