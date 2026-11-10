/**
 * DOBLE de POST /v1/positions/batch (no es la API real). Guarda cada evento
 * recibido por event_id (como la deduplicación del consumidor) y permite
 * provocar: rechazo, "retryable", respuesta perdida tras aceptar, respuestas
 * mal formadas y envíos lentos para medir concurrencia.
 */
import type { FetchLike } from "../src/core/api";

type Status = "accepted" | "rejected" | "retryable";

export class FakeIngest {
  /** Contenido aceptado por event_id (una sola vez, aunque llegue repetido). */
  readonly stored = new Map<string, string>();
  /** Cada evento recibido, en orden, incluidas las repeticiones. */
  readonly receipts: { event_id: string; raw: string }[] = [];
  requests = 0;
  inFlight = 0;
  maxInFlight = 0;
  decide: (event: Record<string, unknown>) => Status = () => "accepted";
  /** Acepta y guarda, pero la respuesta "se pierde" (fetch falla). */
  loseResponse = false;
  /** Reemplaza el cuerpo de la respuesta (para resultados mal formados). */
  rewrite: ((results: unknown[]) => unknown) | null = null;
  networkDown = false;
  gate: Promise<void> | null = null;

  fetch: FetchLike = async (_url, init) => {
    this.requests++;
    this.inFlight++;
    this.maxInFlight = Math.max(this.maxInFlight, this.inFlight);
    try {
      if (this.gate) await this.gate;
      if (this.networkDown) throw new TypeError("Network request failed");
      const body = JSON.parse(init.body) as { events: Record<string, unknown>[] };
      const rawEvents = splitRawEvents(init.body);
      const results = body.events.map((event, index) => {
        const status = this.decide(event);
        const id = String(event.event_id);
        this.receipts.push({ event_id: id, raw: rawEvents[index] });
        if (status === "accepted" && !this.stored.has(id)) this.stored.set(id, rawEvents[index]);
        return { index, event_id: id, status, error: status === "accepted" ? null : `motivo ${status}` };
      });
      if (this.loseResponse) throw new TypeError("Network request failed (respuesta perdida)");
      const payload = this.rewrite ? this.rewrite(results) : { received: results.length, results };
      const text = JSON.stringify(payload);
      return { ok: true, status: 200, text: async () => text };
    } finally {
      this.inFlight--;
    }
  };
}

/** Extrae el texto exacto de cada evento del cuerpo `{"events":[a,b,...]}`. */
function splitRawEvents(body: string): string[] {
  const inner = body.slice('{"events":['.length, -2);
  const out: string[] = [];
  let depth = 0;
  let start = 0;
  let inString = false;
  for (let i = 0; i < inner.length; i++) {
    const ch = inner[i];
    if (inString) {
      if (ch === "\\") i++;
      else if (ch === '"') inString = false;
      continue;
    }
    if (ch === '"') inString = true;
    else if (ch === "{") depth++;
    else if (ch === "}") depth--;
    else if (ch === "," && depth === 0) {
      out.push(inner.slice(start, i));
      start = i + 1;
    }
  }
  out.push(inner.slice(start));
  return out;
}
