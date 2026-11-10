import type { BatchOutcome, ClaimedEvent, Outbox } from "./outbox";

export type SyncTrigger = "open" | "reconnect" | "manual" | "periodic" | "capture";

export interface SyncConfig {
  batchSize: number;
  /** Lotes como máximo por ronda (evita monopolizar la red). */
  maxBatchesPerRun: number;
}

export const DEFAULT_SYNC: SyncConfig = { batchSize: 50, maxBatchesPerRun: 5 };

export interface SyncReport {
  trigger: SyncTrigger;
  batches: number;
  accepted: number;
  rejected: number;
  retry: number;
  skippedOffline: boolean;
  lastError: string | null;
  finishedAt: number;
}

/**
 * Único coordinador de sincronización.
 * - Nunca hay dos envíos a la vez: si llega un disparador mientras corre, se
 *   anota y se hace UNA ronda más al terminar (los disparadores se agrupan).
 * - La conectividad solo decide si vale la pena intentar; la confirmación la
 *   da la respuesta HTTP por evento.
 * - Al arrancar, `start()` recupera los envíos interrumpidos por un cierre.
 */
export class SyncCoordinator {
  private running: Promise<SyncReport> | null = null;
  private rerun: SyncTrigger | null = null;

  constructor(
    private readonly outbox: Outbox,
    private readonly send: (payloads: string[]) => Promise<BatchOutcome>,
    private readonly clock: () => number,
    private readonly config: SyncConfig,
    private readonly isOnline: () => boolean | null = () => null,
    private readonly onProgress: () => void = () => {},
  ) {}

  get busy(): boolean {
    return this.running !== null;
  }

  async start(): Promise<number> {
    return this.outbox.recoverInterrupted();
  }

  requestSync(trigger: SyncTrigger): Promise<SyncReport> {
    if (this.running) {
      this.rerun = trigger;
      return this.running;
    }
    this.running = this.loop(trigger).finally(() => {
      this.running = null;
    });
    return this.running;
  }

  private async loop(first: SyncTrigger): Promise<SyncReport> {
    let trigger: SyncTrigger | null = first;
    let report = await this.runOnce(first);
    while ((trigger = this.rerun) !== null) {
      this.rerun = null;
      const next = await this.runOnce(trigger);
      report = {
        ...next,
        batches: report.batches + next.batches,
        accepted: report.accepted + next.accepted,
        rejected: report.rejected + next.rejected,
        retry: report.retry + next.retry,
        lastError: next.lastError ?? report.lastError,
      };
    }
    return report;
  }

  private async runOnce(trigger: SyncTrigger): Promise<SyncReport> {
    const report: SyncReport = {
      trigger, batches: 0, accepted: 0, rejected: 0, retry: 0,
      skippedOffline: false, lastError: null, finishedAt: 0,
    };
    if (this.isOnline() === false) {
      report.skippedOffline = true;
      report.finishedAt = this.clock();
      return report;
    }
    for (let i = 0; i < this.config.maxBatchesPerRun; i++) {
      const claimed: ClaimedEvent[] = await this.outbox.claimDue(this.config.batchSize);
      if (claimed.length === 0) break;
      let outcome: BatchOutcome;
      try {
        outcome = await this.send(claimed.map((c) => c.payload));
      } catch (err) {
        outcome = { kind: "failure", error: `Error inesperado al enviar: ${String(err)}` };
      }
      const applied = await this.outbox.applyOutcome(claimed, outcome);
      report.batches++;
      report.accepted += applied.accepted;
      report.rejected += applied.rejected;
      report.retry += applied.retry;
      this.onProgress();
      if (outcome.kind === "failure") {
        report.lastError = outcome.error;
        break; // no insistir en esta ronda: el backoff decide el próximo intento
      }
    }
    report.finishedAt = this.clock();
    return report;
  }
}
