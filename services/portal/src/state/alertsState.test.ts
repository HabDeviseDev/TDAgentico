import { describe, expect, it } from "vitest";
import type { AlertsEvent, AlertsStatusEvent, StopAlert } from "../api/types";
import { fleetReducer, initialState, type FleetState } from "./fleetState";

const alert = (vehicleId: string): StopAlert => ({
  alert_id: `id-${vehicleId}`,
  vehicle_id: vehicleId,
  zone_id: "DEMO-Z01",
  zone_name: "Demo · Zona",
  started_at: "2026-11-06T10:00:00.000000Z",
  last_sample_at: "2026-11-06T10:25:00.000000Z",
  observed_duration_s: 1500,
  start_unknown: false,
  sample_count: 51,
  lat: 4.62,
  lon: -74.11,
});

const result = (sequence: number, alerts: StopAlert[], complete = true): AlertsEvent => ({
  type: "alerts",
  sequence,
  evaluated_at: `2026-11-06T10:25:0${sequence}.000000Z`,
  window_start: "2026-11-06T08:25:00.000000Z",
  parameters: {
    max_speed_kmh: 3, max_displacement_m: 50, max_gap_s: 120, max_sample_age_s: 120,
    alert_after_s: 1200, window_s: 7200, max_vehicles: 1000, max_rows: 100000,
  },
  complete,
  incomplete_reasons: complete ? [] : ["max_vehicles"],
  coverage: {
    vehicles_in_window: 2, vehicles_evaluated: complete ? 2 : 1,
    vehicles_not_evaluated: complete ? 0 : 1, vehicles_without_recent_samples: 0,
    vehicles_with_only_future_samples: 0,
  },
  zone_ids: ["DEMO-Z01"],
  alert_count: alerts.length,
  alerts,
  insufficient_evidence_count: 0,
  insufficient_evidence: [],
});

const status = (s: "ok" | "degraded", last: string | null): AlertsStatusEvent => ({
  type: "alerts_status",
  status: s,
  generated_at: "2026-11-06T10:26:00.000000Z",
  detail: s === "degraded" ? "La base de datos no respondió a tiempo" : null,
  last_evaluation_at: last,
});

const apply = (state: FleetState, event: AlertsEvent) =>
  fleetReducer(state, { type: "alerts", event, at: 1 });

describe("alertas en fleetReducer", () => {
  it("sin resultado todavía no hay lista (no es lo mismo que cero alertas)", () => {
    expect(initialState.alerts).toBeNull();
  });

  it("una alerta que deja de cumplirse desaparece en el siguiente resultado", () => {
    let state = apply(initialState, result(1, [alert("A"), alert("B")]));
    state = apply(state, result(2, [alert("B")]));
    expect(state.alerts?.alerts.map((a) => a.vehicle_id)).toEqual(["B"]);
  });

  it("si la evaluación falla conserva el último resultado y lo marca degradado", () => {
    let state = apply(initialState, result(1, [alert("A")]));
    state = fleetReducer(state, {
      type: "alerts_status", at: 2, event: status("degraded", "2026-11-06T10:25:01.000000Z"),
    });
    expect(state.alertsState).toBe("degraded");
    expect(state.alerts?.alerts).toHaveLength(1);
    expect(state.alertsLastEvaluationAt).toBe("2026-11-06T10:25:01.000000Z");
  });

  it("un fallo sin resultado previo no crea una lista vacía", () => {
    const state = fleetReducer(initialState, {
      type: "alerts_status", at: 1, event: status("degraded", null),
    });
    expect(state.alerts).toBeNull();
    expect(state.alertsState).toBe("degraded");
  });

  it("conserva el indicador de evaluación parcial", () => {
    const state = apply(initialState, result(1, [alert("A")], false));
    expect(state.alerts?.complete).toBe(false);
    expect(state.alerts?.coverage.vehicles_not_evaluated).toBe(1);
  });

  it("las alertas no cambian la selección ni los vehículos", () => {
    let state = fleetReducer(initialState, { type: "select", vehicleId: "A" });
    state = apply(state, result(1, [alert("A")]));
    expect(state.selectedId).toBe("A");
    expect(state.vehicles).toEqual([]);
  });
});
