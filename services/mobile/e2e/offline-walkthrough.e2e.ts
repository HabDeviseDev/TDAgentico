/**
 * Recorrido sin conexión con el NÚCLEO REAL de la app (cola SQLite real,
 * coordinador, contrato) contra la API REAL del stack. Lo único simulado es
 * el corte de red: un `fetch` que falla mientras `offline` es verdadero, y
 * una respuesta que "se pierde" después de que la API aceptó el lote.
 *
 * No reemplaza la prueba en un teléfono (captura GPS, NetInfo, cierre real).
 *
 *   API_URL=http://api:8000 npx vitest run --config vitest.e2e.mts
 */
import { randomUUID } from "node:crypto";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, it } from "vitest";
import { sendBatch, type FetchLike } from "../src/core/api";
import { DEFAULT_BACKOFF } from "../src/core/backoff";
import { buildSample } from "../src/core/contract";
import { migrate } from "../src/core/migrations";
import { Outbox } from "../src/core/outbox";
import { SyncCoordinator } from "../src/core/sync";
import { openNodeDb } from "../test/nodeDb";

const API_URL = process.env.API_URL ?? "http://localhost:8000";
const VEHICLE = process.env.DEMO_VEHICLE ?? `MOVIL-DEMO-${randomUUID().slice(0, 6)}`;

const net = { offline: false, loseNextResponse: false };
const realFetch: FetchLike = async (url, init) => {
  if (net.offline) throw new TypeError("Network request failed (sin conexión simulada)");
  const response = await fetch(url, init);
  if (net.loseNextResponse) {
    net.loseNextResponse = false;
    await response.text();                          // la API sí respondió…
    throw new TypeError("Network request failed (respuesta perdida simulada)");
  }
  return response;
};

async function openApp(path: string, clock: { now: number }) {
  const db = openNodeDb(path);
  await migrate(db);
  const outbox = new Outbox(db, () => clock.now, Math.random, DEFAULT_BACKOFF);
  const sync = new SyncCoordinator(outbox, (p) => sendBatch(API_URL, p, realFetch, 10_000),
                                   () => clock.now, { batchSize: 50, maxBatchesPerRun: 5 },
                                   () => (net.offline ? false : true));
  const recovered = await sync.start();
  return { db, outbox, sync, recovered };
}

let step = 0;
const log = (msg: string) => console.log(`[${++step}] ${msg}`);

it("captura, corte de red, cierre, reconexión y sincronización", async () => {
  const path = join(mkdtempSync(join(tmpdir(), "walkthrough-")), "driver.db");
  const clock = { now: Date.now() };
  let fixTime = Date.now() - 10 * 60_000;           // posiciones de los últimos 10 min
  const captured: string[] = [];
  const captureFix = async (app: Awaited<ReturnType<typeof openApp>>) => {
    fixTime += 30_000;
    const row = await app.outbox.record(buildSample({
      eventId: randomUUID(), vehicleId: VEHICLE, timestampMs: fixTime,
      latitude: 4.6486 + captured.length * 1e-4, longitude: -74.0628, speedMps: 8 }), false);
    captured.push(row.event_id);
    return row;
  };

  let app = await openApp(path, clock);
  log(`Vehículo ${VEHICLE}, base local ${path}`);

  for (let i = 0; i < 3; i++) await captureFix(app);
  await app.sync.requestSync("capture");
  log(`Con conexión: ${JSON.stringify(await app.outbox.counts())}`);
  expect((await app.outbox.counts()).accepted).toBe(3);

  net.offline = true;
  for (let i = 0; i < 5; i++) {
    await captureFix(app);
    await app.sync.requestSync("capture");
  }
  log(`Sin conexión, 5 capturas: ${JSON.stringify(await app.outbox.counts())}`);
  expect((await app.outbox.counts()).pending).toBe(5);

  await app.db.close();
  log("App cerrada");
  app = await openApp(path, clock);
  log(`App reabierta: ${JSON.stringify(await app.outbox.counts())}, recuperados ${app.recovered}`);
  expect((await app.outbox.counts()).pending).toBe(5);

  net.offline = false;
  net.loseNextResponse = true;                      // la API acepta, la respuesta se pierde
  await app.sync.requestSync("reconnect");
  log(`Reconexión con respuesta perdida: ${JSON.stringify(await app.outbox.counts())}`);
  expect((await app.outbox.counts()).pending).toBe(5);

  clock.now += DEFAULT_BACKOFF.maxMs + 1;
  await app.sync.requestSync("manual");
  const counts = await app.outbox.counts();
  log(`Reenvío con los mismos event_id: ${JSON.stringify(counts)}`);
  expect(counts).toMatchObject({ accepted: 8, pending: 0, sending: 0 });

  const lastFix = new Date(fixTime).toISOString();
  let latest: { captured_at: string } | undefined;
  for (let i = 0; i < 60 && latest?.captured_at.slice(0, 19) !== lastFix.slice(0, 19); i++) {
    const body = (await (await fetch(`${API_URL}/v1/vehicles`)).json()) as {
      vehicles: { vehicle_id: string; captured_at: string }[] };
    latest = body.vehicles.find((v) => v.vehicle_id === VEHICLE);
    await new Promise((r) => setTimeout(r, 1000));
  }
  log(`Posición más reciente en la API: ${latest?.captured_at} (última captura ${lastFix})`);
  expect(latest?.captured_at.slice(0, 19)).toBe(lastFix.slice(0, 19));
  console.log(`EVENT_IDS=${captured.join(",")}`);
  console.log(`VEHICLE=${VEHICLE}`);
  await app.db.close();
}, 180_000);
