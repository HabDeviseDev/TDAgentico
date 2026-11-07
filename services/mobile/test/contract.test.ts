/** Contrato del evento en el móvil (lógica pura, sin dobles). */
import { describe, expect, it } from "vitest";
import { normalizeApiUrl } from "../src/core/api";
import { buildSample, normalizeVehicleId, readSpeed, toUtcIso } from "../src/core/contract";
import { RouteSimulator } from "../src/core/simulator";

const fix = (over: Partial<Parameters<typeof buildSample>[0]> = {}) =>
  buildSample({ eventId: "e1", vehicleId: "BOG-001", timestampMs: Date.UTC(2026, 10, 6, 15, 0, 0, 123),
                latitude: 4.65, longitude: -74.08, speedMps: 10, ...over });

describe("velocidad", () => {
  it("convierte m/s a km/h", () => {
    expect(readSpeed(10)).toEqual({ kind: "known", kmh: 36 });
    expect(readSpeed(0)).toEqual({ kind: "known", kmh: 0 });
    expect(readSpeed(13.889)).toEqual({ kind: "known", kmh: 50 });
  });

  it.each([null, undefined, -1, Number.NaN, Number.POSITIVE_INFINITY])(
    "una velocidad desconocida (%s) no se convierte en 0", (v) => {
      expect(readSpeed(v).kind).toBe("unknown");
    });

  it("fuera del contrato (> 300 km/h) queda desconocida", () => {
    expect(readSpeed(84)).toMatchObject({ kind: "unknown" });           // 302,4 km/h
    expect(readSpeed(83.3)).toEqual({ kind: "known", kmh: 299.88 });
  });
});

describe("muestra", () => {
  it("arma el evento con fecha UTC y velocidad en km/h", () => {
    const s = fix();
    expect(s).toEqual({ kind: "event", event: {
      schema_version: 1, event_id: "e1", vehicle_id: "BOG-001",
      captured_at: "2026-11-06T15:00:00.123Z", lat: 4.65, lon: -74.08, speed_kmh: 36 } });
    expect(toUtcIso(0)).toBe("1970-01-01T00:00:00.000Z");
  });

  it("sin velocidad se omite con motivo (no se envía 0)", () => {
    const s = fix({ speedMps: null });
    expect(s).toMatchObject({ kind: "skipped", reason: "El dispositivo no informó la velocidad", lat: 4.65 });
  });

  it.each([
    [{ latitude: 91 }, "Latitud fuera de rango"],
    [{ longitude: -181 }, "Longitud fuera de rango"],
    [{ timestampMs: Number.NaN }, "Fecha de captura inválida"],
  ])("lectura inválida %o se omite", (over, reason) => {
    expect(fix(over)).toMatchObject({ kind: "skipped", reason });
  });
});

describe("identificador del vehículo (misma regla que la ingesta)", () => {
  it.each([
    ["BOG-001", "BOG-001"], ["bog-001", "bog-001"], ["  Camión 7  ", "Camión 7"],
    ["a".repeat(64), "a".repeat(64)],
  ])("acepta %j", (raw, value) => {
    expect(normalizeVehicleId(raw)).toEqual({ ok: true, value });
  });

  it.each(["", "   ", "a".repeat(65), ` ${"b".repeat(64)}`])("rechaza %j", (raw) => {
    expect(normalizeVehicleId(raw).ok).toBe(false);
  });
});

describe("configuración y simulador", () => {
  it("valida la URL de la API", () => {
    expect(normalizeApiUrl(" http://192.168.1.20:8000/ ")).toEqual({ ok: true, value: "http://192.168.1.20:8000" });
    expect(normalizeApiUrl("192.168.1.20:8000").ok).toBe(false);
  });

  it("el simulador produce lecturas válidas y, si se pide, sin velocidad", () => {
    const sim = new RouteSimulator(3);
    const fixes = Array.from({ length: 6 }, (_, i) => sim.next(1_000 * i, 5_000));
    expect(fixes.map((f) => f.speedMps === null)).toEqual([false, false, true, false, false, true]);
    for (const f of fixes) {
      expect(Math.abs(f.latitude - 4.6486)).toBeLessThan(0.01);
      expect(Math.abs(f.longitude + 74.0628)).toBeLessThan(0.01);
    }
  });
});
