// Métricas y clasificación compartidas por los escenarios de carga.
import { Counter, Trend } from "k6/metrics";

export const RUN_ID = __ENV.RUN_ID;
export const RUN_DIR = __ENV.RUN_DIR || `/runs/${RUN_ID}`;
export const API_URL = __ENV.API_URL;
if (!RUN_ID) throw new Error("RUN_ID es obligatorio");
// Sin valor por defecto: cargar un entorno exige una URL explícita.
if (!API_URL) throw new Error("API_URL es obligatoria (p. ej. http://api:8000 dentro de la red de Compose)");

export const num = (name, fallback) => (__ENV[name] !== undefined ? Number(__ENV[name]) : fallback);

// Envíos por tipo (planificados en el conjunto de datos).
export const sentNew = new Counter("lt_sent_new");
export const sentDup = new Counter("lt_sent_dup");
export const sentInvalid = new Counter("lt_sent_invalid");
// Resultados.
export const confirmed = new Counter("lt_confirmed");                 // 202 / accepted de un evento válido
export const expectedRejections = new Counter("lt_expected_rejections"); // 422 / rejected de un inválido
export const unexpected = new Counter("lt_unexpected_failures");      // cualquier otra combinación
export const ambiguous = new Counter("lt_ambiguous");                 // timeout, conexión, 503/retryable
export const overflow = new Counter("lt_overflow");                   // iteraciones sin dato (no debería ocurrir)
// Un punto por evento con su secuencia: la reconciliación lo correlaciona con el conjunto de datos.
export const result = new Counter("lt_result");
export const latency = new Trend("lt_latency", true);

export function countSent(kind) {
  if (kind === "new") sentNew.add(1);
  else if (kind === "dup") sentDup.add(1);
  else sentInvalid.add(1);
}

/**
 * Resultado de un evento:
 * - confirmed: evento válido con recepción confirmada (202 o "accepted").
 * - expected_rejection: evento inválido rechazado por validación (422 o "rejected").
 * - ambiguous: sin confirmación; el evento pudo haberse publicado
 *   (timeout, error de conexión, 503 de la API o "retryable").
 * - unexpected: cualquier otra combinación (p. ej. un válido rechazado).
 */
export function classify(kind, status) {
  const valid = kind !== "invalid";
  if (status === "accepted") return valid ? "confirmed" : "unexpected";
  if (status === "rejected") return valid ? "unexpected" : "expected_rejection";
  if (status === "retryable" || status === "timeout" || status === "connection" || status === "503") return "ambiguous";
  return "unexpected";
}

export function record(item, status, outcome) {
  countSent(item.kind);
  if (outcome === "confirmed") confirmed.add(1);
  else if (outcome === "expected_rejection") expectedRejections.add(1);
  else if (outcome === "ambiguous") ambiguous.add(1);
  else unexpected.add(1);
  result.add(1, { seq: String(item.seq), kind: item.kind, outcome, status: String(status) });
}

export function thresholds() {
  return {
    lt_unexpected_failures: [`count<=${num("MAX_UNEXPECTED", 0)}`],
    lt_ambiguous: [`count<=${num("MAX_AMBIGUOUS", 0)}`],
    dropped_iterations: [`count<=${num("MAX_DROPPED", 0)}`],
    "lt_latency{kind:new}": [`p(95)<${num("P95_MS", 500)}`, `p(99)<${num("P99_MS", 1500)}`],
  };
}

function metric(data, name, stat) {
  const m = data.metrics[name];
  if (!m) return 0;
  return m.values[stat] ?? 0;
}

/** Resumen propio: JSON en la carpeta de la ejecución y texto breve en stdout. */
export function summary(data, scenarioName) {
  const f = (v) => (typeof v === "number" ? Math.round(v * 100) / 100 : v);
  const lines = [
    `Ejecución ${RUN_ID} (${scenarioName})`,
    `  enviados: nuevos=${metric(data, "lt_sent_new", "count")} reenvíos=${metric(data, "lt_sent_dup", "count")} inválidos=${metric(data, "lt_sent_invalid", "count")}`,
    `  confirmados=${metric(data, "lt_confirmed", "count")} rechazos_esperados=${metric(data, "lt_expected_rejections", "count")} ambiguos=${metric(data, "lt_ambiguous", "count")} inesperados=${metric(data, "lt_unexpected_failures", "count")}`,
    `  iteraciones=${metric(data, "iterations", "count")} tasa_real=${f(metric(data, "iterations", "rate"))}/s descartadas=${metric(data, "dropped_iterations", "count")}`,
    `  latencia (ms) p50=${f(metric(data, "lt_latency", "med"))} p95=${f(metric(data, "lt_latency", "p(95)"))} p99=${f(metric(data, "lt_latency", "p(99)"))} max=${f(metric(data, "lt_latency", "max"))}`,
  ];
  const failed = Object.entries(data.metrics)
    .filter(([, m]) => m.thresholds && Object.values(m.thresholds).some((t) => !t.ok))
    .map(([name]) => name);
  lines.push(failed.length ? `  UMBRALES NO CUMPLIDOS: ${failed.join(", ")}` : "  umbrales: cumplidos");
  return {
    stdout: lines.join("\n") + "\n",
    [`${RUN_DIR}/k6-summary-${scenarioName}.json`]: JSON.stringify(data, null, 2),
  };
}
