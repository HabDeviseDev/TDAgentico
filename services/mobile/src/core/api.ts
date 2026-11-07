import type { BatchOutcome } from "./outbox";

export type FetchLike = (
  url: string,
  init: { method: string; headers: Record<string, string>; body: string; signal: AbortSignal },
) => Promise<{ ok: boolean; status: number; text(): Promise<string> }>;

/** Valida que la URL base sea http(s) y la normaliza sin "/" final. */
export function normalizeApiUrl(raw: string): { ok: true; value: string } | { ok: false; error: string } {
  const value = raw.trim().replace(/\/+$/, "");
  if (!/^https?:\/\/[^\s/]+/i.test(value)) {
    return { ok: false, error: "La URL debe empezar con http:// o https:// e incluir el host" };
  }
  return { ok: true, value };
}

/**
 * Envía un lote a POST /v1/positions/batch. Cada `payload` es el JSON exacto
 * guardado en SQLite: se concatena tal cual, así un reintento envía los
 * mismos bytes de cada evento.
 *
 * - 200 con {"results": [...]}: resultados por evento (se validan al aplicar).
 * - Cualquier otra cosa (red, timeout, HTTP de error, JSON inválido): fallo
 *   del envío completo; los eventos quedan pendientes. Si el servidor llegó
 *   a aceptarlos, el reenvío con el mismo event_id no crea duplicados.
 */
export async function sendBatch(
  baseUrl: string,
  payloads: string[],
  fetchFn: FetchLike,
  timeoutMs: number,
): Promise<BatchOutcome> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetchFn(`${baseUrl}/v1/positions/batch`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: `{"events":[${payloads.join(",")}]}`,
      signal: controller.signal,
    });
    const text = await response.text();
    if (!response.ok) {
      let detail = "";
      try {
        const parsed = JSON.parse(text) as { detail?: unknown };
        if (typeof parsed.detail === "string") detail = `: ${parsed.detail}`;
      } catch {
        /* cuerpo no JSON */
      }
      return { kind: "failure", error: `HTTP ${response.status}${detail}`.slice(0, 300) };
    }
    let body: unknown;
    try {
      body = JSON.parse(text);
    } catch {
      return { kind: "failure", error: "Respuesta del servidor no es JSON válido" };
    }
    const results = typeof body === "object" && body !== null ? (body as { results?: unknown }).results : undefined;
    return { kind: "results", results };
  } catch (err) {
    const aborted = controller.signal.aborted;
    return {
      kind: "failure",
      error: aborted ? `Sin respuesta en ${Math.round(timeoutMs / 1000)} s` : `Error de red: ${String(err)}`.slice(0, 300),
    };
  } finally {
    clearTimeout(timer);
  }
}
