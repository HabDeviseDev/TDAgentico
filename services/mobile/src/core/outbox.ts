import { backoffDelayMs, type BackoffConfig } from "./backoff";
import type { SampleDecision } from "./contract";
import type { SqlDb, SqlExecutor } from "./db";

/**
 * Cola local (tabla `outbox`). Cada posición se guarda aquí ANTES de enviarse.
 *
 * Estados:
 * - pending:  pendiente de confirmación del servidor (con próximo intento).
 * - sending:  incluida en un envío en curso. Si la app se cierra en ese
 *             momento, `recoverInterrupted` la devuelve a pending al abrir.
 * - accepted: el servidor confirmó la recepción (ack de Kafka). Esto es
 *             "recibido por el servidor", no "procesado en la base de datos".
 * - rejected: el servidor la rechazó por validación; se conserva con el motivo.
 * - skipped:  no se envía (p. ej. velocidad desconocida); se conserva el motivo.
 *
 * El `payload` (JSON del evento) se fija al guardar: cada reintento envía el
 * mismo event_id y el mismo contenido, aunque cambie el vehículo en la interfaz.
 */

export type OutboxStatus = "pending" | "sending" | "accepted" | "rejected" | "skipped";

export interface OutboxRow {
  id: number;
  event_id: string;
  vehicle_id: string;
  captured_at: string | null;
  lat: number | null;
  lon: number | null;
  speed_kmh: number | null;
  payload: string | null;
  simulated: number;
  status: OutboxStatus;
  attempts: number;
  next_attempt_at: number;
  last_error: string | null;
  created_at: number;
  updated_at: number;
  accepted_at: number | null;
}

export interface ClaimedEvent {
  id: number;
  event_id: string;
  payload: string;
  attempts: number;
}

export type BatchOutcome =
  | { kind: "results"; results: unknown }
  | { kind: "failure"; error: string };

export interface ApplySummary {
  accepted: number;
  rejected: number;
  retry: number;
}

export type Counts = Record<OutboxStatus, number>;

const RESULT_STATUSES = new Set(["accepted", "rejected", "retryable"]);

interface ParsedResult {
  index: number;
  event_id: string;
  status: "accepted" | "rejected" | "retryable";
  error: string | null;
}

function parseResult(value: unknown): ParsedResult | null {
  if (typeof value !== "object" || value === null) return null;
  const r = value as Record<string, unknown>;
  if (!Number.isInteger(r.index) || typeof r.event_id !== "string") return null;
  if (typeof r.status !== "string" || !RESULT_STATUSES.has(r.status)) return null;
  const error = typeof r.error === "string" ? r.error.slice(0, 300) : null;
  return { index: r.index as number, event_id: r.event_id, status: r.status as ParsedResult["status"], error };
}

export class Outbox {
  constructor(
    private readonly db: SqlDb,
    private readonly clock: () => number,
    private readonly random: () => number,
    private readonly backoff: BackoffConfig,
  ) {}

  /** Guarda una muestra: pendiente si es un evento válido, omitida si no. */
  async record(sample: SampleDecision, simulated: boolean): Promise<OutboxRow> {
    const now = this.clock();
    if (sample.kind === "event") {
      const e = sample.event;
      await this.db.run(
        `INSERT INTO outbox (event_id, vehicle_id, captured_at, lat, lon, speed_kmh, payload, simulated,
                             status, attempts, next_attempt_at, created_at, updated_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?)`,
        [e.event_id, e.vehicle_id, e.captured_at, e.lat, e.lon, e.speed_kmh, JSON.stringify(e),
         simulated ? 1 : 0, now, now, now],
      );
      return (await this.byEventId(e.event_id))!;
    }
    await this.db.run(
      `INSERT INTO outbox (event_id, vehicle_id, captured_at, lat, lon, speed_kmh, payload, simulated,
                           status, attempts, next_attempt_at, last_error, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, 'skipped', 0, 0, ?, ?, ?)`,
      [sample.eventId, sample.vehicleId, sample.capturedAt, sample.lat, sample.lon,
       simulated ? 1 : 0, sample.reason, now, now],
    );
    return (await this.byEventId(sample.eventId))!;
  }

  byEventId(eventId: string): Promise<OutboxRow | null> {
    return this.db.get<OutboxRow>("SELECT * FROM outbox WHERE event_id = ?", [eventId]);
  }

  /** Envíos que quedaron a medias al cerrarse la app: vuelven a pendientes. */
  async recoverInterrupted(): Promise<number> {
    const now = this.clock();
    const { changes } = await this.db.run(
      `UPDATE outbox SET status = 'pending', next_attempt_at = ?, updated_at = ?,
              last_error = 'Envío interrumpido (la app se cerró); se reenviará con el mismo event_id'
       WHERE status = 'sending'`,
      [now, now],
    );
    return changes;
  }

  /** Toma hasta `limit` pendientes vencidos y los marca como en envío. */
  async claimDue(limit: number): Promise<ClaimedEvent[]> {
    const now = this.clock();
    return this.db.transaction(async (tx) => {
      const rows = await tx.all<{ id: number; event_id: string; payload: string; attempts: number }>(
        `SELECT id, event_id, payload, attempts FROM outbox
         WHERE status = 'pending' AND next_attempt_at <= ?
         ORDER BY id LIMIT ?`,
        [now, limit],
      );
      for (const r of rows) {
        await tx.run(
          "UPDATE outbox SET status = 'sending', attempts = attempts + 1, updated_at = ? WHERE id = ?",
          [now, r.id],
        );
      }
      return rows.map((r) => ({ ...r, attempts: r.attempts + 1 }));
    });
  }

  /**
   * Aplica el resultado de un envío en una sola transacción. Cada resultado
   * se correlaciona por posición en el lote (`index`) Y por `event_id`; uno
   * ausente, duplicado o mal formado deja el evento pendiente.
   */
  async applyOutcome(claimed: ClaimedEvent[], outcome: BatchOutcome): Promise<ApplySummary> {
    const now = this.clock();
    const summary: ApplySummary = { accepted: 0, rejected: 0, retry: 0 };
    const byIndex = new Map<number, ParsedResult>();
    const duplicated = new Set<number>();
    if (outcome.kind === "results" && Array.isArray(outcome.results)) {
      for (const raw of outcome.results) {
        const parsed = parseResult(raw);
        if (!parsed) continue;
        if (byIndex.has(parsed.index)) duplicated.add(parsed.index);
        byIndex.set(parsed.index, parsed);
      }
    }
    const malformed =
      outcome.kind === "results" && !Array.isArray(outcome.results)
        ? "Respuesta del servidor sin lista de resultados"
        : null;

    await this.db.transaction(async (tx) => {
      for (const [i, ev] of claimed.entries()) {
        let result: ParsedResult | undefined;
        let missing: string;
        if (outcome.kind === "failure") {
          missing = outcome.error;
        } else if (malformed) {
          missing = malformed;
        } else {
          result = duplicated.has(i) ? undefined : byIndex.get(i);
          if (result && result.event_id !== ev.event_id) result = undefined;
          missing = "Resultado ausente o inválido para este evento; se reintentará";
        }
        if (result?.status === "accepted") {
          summary.accepted++;
          await tx.run(
            `UPDATE outbox SET status = 'accepted', accepted_at = ?, last_error = NULL, updated_at = ?
             WHERE id = ? AND status = 'sending'`,
            [now, now, ev.id],
          );
        } else if (result?.status === "rejected") {
          summary.rejected++;
          await tx.run(
            `UPDATE outbox SET status = 'rejected', last_error = ?, updated_at = ?
             WHERE id = ? AND status = 'sending'`,
            [result.error ?? "Rechazado por el servidor", now, ev.id],
          );
        } else {
          summary.retry++;
          await this.retryLater(tx, ev, result?.status === "retryable"
            ? (result.error ?? "El servidor no pudo confirmar la publicación")
            : missing, now);
        }
      }
    });
    return summary;
  }

  private async retryLater(tx: SqlExecutor, ev: ClaimedEvent, error: string, now: number) {
    const next = now + backoffDelayMs(ev.attempts, this.backoff, this.random);
    await tx.run(
      `UPDATE outbox SET status = 'pending', next_attempt_at = ?, last_error = ?, updated_at = ?
       WHERE id = ? AND status = 'sending'`,
      [next, error.slice(0, 300), now, ev.id],
    );
  }

  async counts(): Promise<Counts> {
    const rows = await this.db.all<{ status: OutboxStatus; n: number }>(
      "SELECT status, count(*) AS n FROM outbox GROUP BY status",
    );
    const counts: Counts = { pending: 0, sending: 0, accepted: 0, rejected: 0, skipped: 0 };
    for (const r of rows) counts[r.status] = r.n;
    return counts;
  }

  /** Próximo intento programado entre los pendientes (epoch ms) o null. */
  async nextAttemptAt(): Promise<number | null> {
    const row = await this.db.get<{ next: number | null }>(
      "SELECT min(next_attempt_at) AS next FROM outbox WHERE status = 'pending'",
    );
    return row?.next ?? null;
  }

  list(status: OutboxStatus, limit = 20): Promise<OutboxRow[]> {
    return this.db.all<OutboxRow>(
      "SELECT * FROM outbox WHERE status = ? ORDER BY id DESC LIMIT ?",
      [status, limit],
    );
  }

  latest(): Promise<OutboxRow | null> {
    return this.db.get<OutboxRow>("SELECT * FROM outbox ORDER BY id DESC LIMIT 1");
  }
}
