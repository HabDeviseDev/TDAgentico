import { randomUUID } from "node:crypto";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { sendBatch } from "../src/core/api";
import { DEFAULT_BACKOFF } from "../src/core/backoff";
import { buildSample } from "../src/core/contract";
import type { SqlDb } from "../src/core/db";
import { migrate } from "../src/core/migrations";
import { Outbox } from "../src/core/outbox";
import { SyncCoordinator, type SyncConfig } from "../src/core/sync";
import { FakeIngest } from "./fakeIngest";
import { openNodeDb } from "./nodeDb";

export const T0 = Date.UTC(2026, 10, 6, 15, 0, 0);

export class Clock {
  now = T0;
  advance(ms: number) {
    this.now += ms;
  }
}

export interface Env {
  path: string;
  db: SqlDb;
  clock: Clock;
  outbox: Outbox;
  server: FakeIngest;
  sync: SyncCoordinator;
  online: { value: boolean | null };
}

export function tempDbPath(): string {
  return join(mkdtempSync(join(tmpdir(), "driver-")), "driver.db");
}

/** Base SQLite real en disco + cola + coordinador contra el doble de ingesta. */
export async function openEnv(path = tempDbPath(), config: SyncConfig = { batchSize: 3, maxBatchesPerRun: 10 },
                              server = new FakeIngest(), clock = new Clock()): Promise<Env> {
  const db = openNodeDb(path);
  await migrate(db);
  const outbox = new Outbox(db, () => clock.now, () => 0.5, DEFAULT_BACKOFF);
  const online = { value: null as boolean | null };
  const sync = new SyncCoordinator(
    outbox,
    (payloads) => sendBatch("http://api.test", payloads, server.fetch, 1_000),
    () => clock.now,
    config,
    () => online.value,
  );
  return { path, db, clock, outbox, server, sync, online };
}

export async function capture(env: Env, vehicleId = "MOVIL-001", speedMps: number | null = 5, i = 0) {
  return env.outbox.record(
    buildSample({
      eventId: randomUUID(),
      vehicleId,
      timestampMs: env.clock.now + i,
      latitude: 4.65 + i * 1e-4,
      longitude: -74.08,
      speedMps,
    }),
    false,
  );
}

/** Avanza el reloj más allá de cualquier espera de reintento. */
export function pastBackoff(env: Env) {
  env.clock.advance(DEFAULT_BACKOFF.maxMs + 1);
}
