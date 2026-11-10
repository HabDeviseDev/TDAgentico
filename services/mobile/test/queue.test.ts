/**
 * Cola y sincronización con SQLite REAL (node:sqlite, archivo en disco) y un
 * DOBLE del endpoint de lotes (FakeIngest).
 */
import { describe, expect, it } from "vitest";
import { sendBatch, type FetchLike } from "../src/core/api";
import { backoffDelayMs, DEFAULT_BACKOFF } from "../src/core/backoff";
import { migrate } from "../src/core/migrations";
import { SettingsStore } from "../src/core/settings";
import { capture, openEnv, pastBackoff, tempDbPath, type Env } from "./harness";

async function rows(env: Env) {
  return env.db.all<{ event_id: string; payload: string | null; status: string; attempts: number;
                      last_error: string | null; next_attempt_at: number; vehicle_id: string }>(
    "SELECT event_id, payload, status, attempts, last_error, next_attempt_at, vehicle_id FROM outbox ORDER BY id");
}

describe("persistencia", () => {
  it("la cola sobrevive a cerrar y volver a abrir SQLite", async () => {
    const env = await openEnv();
    await capture(env, "MOVIL-001", 5, 0);
    await capture(env, "MOVIL-001", 5, 1);
    await capture(env, "MOVIL-001", null, 2); // omitida: velocidad desconocida
    const before = await rows(env);
    await env.db.close();

    const reopened = await openEnv(env.path);
    expect(await rows(reopened)).toEqual(before);
    expect(await reopened.outbox.counts()).toMatchObject({ pending: 2, skipped: 1 });
    await reopened.db.close();
  });

  it("las migraciones son reproducibles e idempotentes", async () => {
    const env = await openEnv();
    expect(await migrate(env.db)).toBe(2);
    const tables = await env.db.all<{ name: string }>(
      "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('outbox', 'settings') ORDER BY name");
    expect(tables.map((t) => t.name)).toEqual(["outbox", "settings"]);
    await env.db.close();
  });

  it("la configuración se guarda y cambiar de vehículo no modifica pendientes", async () => {
    const env = await openEnv();
    const settings = new SettingsStore(env.db);
    await settings.save({ apiUrl: "http://192.168.1.20:8000", vehicleId: "CAMION-A" });
    const a = await capture(env, "CAMION-A");
    await settings.save({ apiUrl: "http://192.168.1.20:8000", vehicleId: "CAMION-B" });
    await capture(env, "CAMION-B");
    expect((await settings.load()).vehicleId).toBe("CAMION-B");
    const first = (await env.outbox.byEventId(a.event_id))!;
    expect(first.vehicle_id).toBe("CAMION-A");
    expect(JSON.parse(first.payload!).vehicle_id).toBe("CAMION-A");
    await env.sync.requestSync("manual");
    expect(JSON.parse(env.server.stored.get(a.event_id)!).vehicle_id).toBe("CAMION-A");
    await env.db.close();
  });
});

describe("envío y resultados", () => {
  it("un reintento conserva el event_id y el contenido exacto", async () => {
    const env = await openEnv();
    const row = await capture(env);
    env.server.networkDown = true;
    const failed = await env.sync.requestSync("manual");
    expect(failed.lastError).toContain("Error de red");
    const pending = (await env.outbox.byEventId(row.event_id))!;
    expect(pending).toMatchObject({ status: "pending", attempts: 1, payload: row.payload });
    expect(pending.next_attempt_at).toBeGreaterThan(env.clock.now);

    // Antes del próximo intento no se reenvía.
    env.server.networkDown = false;
    await env.sync.requestSync("periodic");
    expect(env.server.requests).toBe(1);

    pastBackoff(env);
    await env.sync.requestSync("periodic");
    expect((await env.outbox.byEventId(row.event_id))!.status).toBe("accepted");
    expect(env.server.receipts.map((r) => r.raw)).toEqual([row.payload]); // mismos bytes
    await env.db.close();
  });

  it("respuesta perdida después de aceptar: se reenvía con la misma identidad", async () => {
    const env = await openEnv();
    const captured = [await capture(env, "M", 5, 0), await capture(env, "M", 5, 1)];
    env.server.loseResponse = true;
    await env.sync.requestSync("manual");
    expect(env.server.stored.size).toBe(2);                       // el servidor sí recibió
    expect((await env.outbox.counts()).pending).toBe(2);          // el móvil no lo sabe

    env.server.loseResponse = false;
    pastBackoff(env);
    await env.sync.requestSync("manual");
    expect((await env.outbox.counts()).accepted).toBe(2);
    expect(env.server.stored.size).toBe(2);                       // ninguna identidad nueva
    const ids = env.server.receipts.map((r) => r.event_id);
    expect(ids).toEqual([...captured.map((c) => c.event_id), ...captured.map((c) => c.event_id)]);
    expect(env.server.receipts.slice(0, 2).map((r) => r.raw))
      .toEqual(env.server.receipts.slice(2).map((r) => r.raw));
    await env.db.close();
  });

  it("aceptación parcial de un lote", async () => {
    const env = await openEnv();
    const [ok, retry, bad] = [await capture(env, "OK"), await capture(env, "REINTENTO"), await capture(env, "MALO")];
    env.server.decide = (e) => (e.vehicle_id === "REINTENTO" ? "retryable" : e.vehicle_id === "MALO" ? "rejected" : "accepted");
    const report = await env.sync.requestSync("manual");
    expect(report).toMatchObject({ accepted: 1, rejected: 1, retry: 1 });
    expect((await env.outbox.byEventId(ok.event_id))!.status).toBe("accepted");
    const r = (await env.outbox.byEventId(retry.event_id))!;
    expect(r.status).toBe("pending");
    expect(r.last_error).toBe("motivo retryable");
    expect(r.next_attempt_at).toBeGreaterThan(env.clock.now);
    expect((await env.outbox.byEventId(bad.event_id))!.status).toBe("rejected");
    await env.db.close();
  });

  it("un rechazo definitivo se conserva con su motivo y no se reenvía", async () => {
    const env = await openEnv();
    const bad = await capture(env, "MALO");
    env.server.decide = () => "rejected";
    await env.sync.requestSync("manual");
    pastBackoff(env);
    await env.sync.requestSync("manual");
    const [kept] = await env.outbox.list("rejected");
    expect(kept).toMatchObject({ event_id: bad.event_id, payload: bad.payload, last_error: "motivo rejected" });
    expect(env.server.receipts).toHaveLength(1);
    await env.db.close();
  });

  const malformed: [string, (results: any[]) => unknown][] = [
    ["sin resultado para el índice 1", (r) => ({ results: [r[0], r[2]] })],
    ["event_id cambiado en el índice 1", (r) => ({ results: [r[0], { ...r[1], event_id: "otro" }, r[2]] })],
    ["índice 1 duplicado", (r) => ({ results: [r[0], r[1], { ...r[1], status: "rejected" }, r[2]] })],
    ["estado desconocido en el índice 1", (r) => ({ results: [r[0], { ...r[1], status: "ok" }, r[2]] })],
    ["índice no numérico en el 1", (r) => ({ results: [r[0], { ...r[1], index: "1" }, r[2]] })],
  ];
  it.each(malformed)("resultado ausente o inválido (%s) deja el evento pendiente", async (_name, rewrite) => {
    const env = await openEnv();
    const events = [await capture(env, "A"), await capture(env, "B"), await capture(env, "C")];
    env.server.rewrite = rewrite;
    await env.sync.requestSync("manual");
    const statuses = await Promise.all(events.map(async (e) => (await env.outbox.byEventId(e.event_id))!.status));
    expect(statuses).toEqual(["accepted", "pending", "accepted"]);
    expect((await env.outbox.byEventId(events[1].event_id))!.last_error).toContain("Resultado ausente o inválido");
    await env.db.close();
  });

  it.each([
    ["sin lista de resultados", () => ({ received: 3 })],
    ["resultados no son una lista", () => ({ results: { 0: "accepted" } })],
  ])("respuesta 200 %s: todo queda pendiente", async (_name, rewrite) => {
    const env = await openEnv();
    await capture(env, "A");
    await capture(env, "B");
    env.server.rewrite = rewrite;
    await env.sync.requestSync("manual");
    expect(await env.outbox.counts()).toMatchObject({ pending: 2, accepted: 0 });
    await env.db.close();
  });

  it("errores HTTP, JSON inválido y timeout del envío completo", async () => {
    const http503: FetchLike = async () => ({ ok: false, status: 503, text: async () => '{"detail":"Kafka caído"}' });
    expect(await sendBatch("http://x", ["{}"], http503, 500)).toEqual({ kind: "failure", error: "HTTP 503: Kafka caído" });
    const notJson: FetchLike = async () => ({ ok: true, status: 200, text: async () => "<html>" });
    expect(await sendBatch("http://x", ["{}"], notJson, 500)).toEqual({
      kind: "failure", error: "Respuesta del servidor no es JSON válido" });
    const never: FetchLike = (_url, init) => new Promise((_, reject) => {
      init.signal.addEventListener("abort", () => reject(new Error("aborted")));
    });
    expect(await sendBatch("http://x", ["{}"], never, 50)).toEqual({ kind: "failure", error: "Sin respuesta en 0 s" });
  });
});

describe("coordinador", () => {
  it("disparadores simultáneos no generan envíos simultáneos ni duplicados", async () => {
    const env = await openEnv(tempDbPath(), { batchSize: 2, maxBatchesPerRun: 10 });
    for (let i = 0; i < 5; i++) await capture(env, "C", 5, i);
    let release!: () => void;
    env.server.gate = new Promise((r) => (release = r));
    const triggers = (["open", "reconnect", "manual", "periodic", "capture", "manual"] as const)
      .map((t) => env.sync.requestSync(t));
    expect(env.sync.busy).toBe(true);
    await new Promise((r) => setTimeout(r, 20));
    release();
    env.server.gate = null;
    const reports = await Promise.all(triggers);
    expect(new Set(reports).size).toBe(1);           // todos esperan la misma ronda
    expect(env.server.maxInFlight).toBe(1);
    expect(env.server.receipts).toHaveLength(5);      // cada evento una sola vez
    expect((await env.outbox.counts()).accepted).toBe(5);
    expect(env.sync.busy).toBe(false);
    await env.db.close();
  });

  it("recupera un envío interrumpido por el cierre de la app", async () => {
    const env = await openEnv();
    const events = [await capture(env, "R", 5, 0), await capture(env, "R", 5, 1)];
    await env.outbox.claimDue(10);                    // envío en curso…
    expect((await env.outbox.counts()).sending).toBe(2);
    await env.db.close();                             // …y la app se cierra

    const reopened = await openEnv(env.path);
    expect(await reopened.sync.start()).toBe(2);
    const recovered = (await reopened.outbox.byEventId(events[0].event_id))!;
    expect(recovered).toMatchObject({ status: "pending", attempts: 1, payload: events[0].payload });
    await reopened.sync.requestSync("open");
    expect((await reopened.outbox.counts()).accepted).toBe(2);
    expect(reopened.server.receipts.map((r) => r.raw)).toEqual(events.map((e) => e.payload));
    await reopened.db.close();
  });

  it("sin conexión no intenta enviar; al reconectar sí", async () => {
    const env = await openEnv();
    await capture(env);
    env.online.value = false;
    expect((await env.sync.requestSync("periodic")).skippedOffline).toBe(true);
    expect(env.server.requests).toBe(0);
    env.online.value = true;
    await env.sync.requestSync("reconnect");
    expect((await env.outbox.counts()).accepted).toBe(1);
    await env.db.close();
  });

  it("las muestras omitidas no se envían", async () => {
    const env = await openEnv();
    const skipped = await capture(env, "S", null);
    await env.sync.requestSync("manual");
    expect(env.server.requests).toBe(0);
    expect((await env.outbox.byEventId(skipped.event_id))).toMatchObject({
      status: "skipped", speed_kmh: null, payload: null, last_error: "El dispositivo no informó la velocidad" });
    await env.db.close();
  });
});

describe("espera entre reintentos", () => {
  it("crece con los intentos, tiene jitter y respeta el tope", () => {
    const cfg = DEFAULT_BACKOFF;
    expect(backoffDelayMs(1, cfg, () => 0)).toBe(1_000);
    expect(backoffDelayMs(1, cfg, () => 1)).toBe(2_000);
    expect(backoffDelayMs(3, cfg, () => 0)).toBe(4_000);
    expect(backoffDelayMs(3, cfg, () => 1)).toBe(8_000);
    expect(backoffDelayMs(50, cfg, () => 1)).toBe(cfg.maxMs);
    expect(backoffDelayMs(50, cfg, () => 0)).toBe(cfg.maxMs / 2);
  });
});
