import { describe, expect, it } from "vitest";
import type { SnapshotEvent, Vehicle } from "../api/types";
import { fleetReducer, initialState, type FleetState } from "./fleetState";

const vehicle = (id: string, overrides: Partial<Vehicle> = {}): Vehicle => ({
  vehicle_id: id,
  event_id: `${id}-evt`,
  captured_at: "2026-11-06T10:00:00.000000Z",
  lat: 4.65,
  lon: -74.08,
  speed_kmh: 30,
  updated_at: "2026-11-06T10:00:01.000000Z",
  ...overrides,
});

const snapshot = (sequence: number, vehicles: Vehicle[]): SnapshotEvent => ({
  type: "snapshot",
  sequence,
  generated_at: "2026-11-06T10:00:02.000000Z",
  count: vehicles.length,
  vehicles,
});

const apply = (state: FleetState, s: SnapshotEvent) =>
  fleetReducer(state, { type: "snapshot", event: s, at: 1 });

describe("fleetReducer", () => {
  it("reemplaza el estado completo con cada snapshot", () => {
    let state = apply(initialState, snapshot(1, [vehicle("A"), vehicle("B")]));
    state = apply(state, snapshot(2, [vehicle("C")]));
    expect(state.vehicles.map((v) => v.vehicle_id)).toEqual(["C"]);
    expect(state.sequence).toBe(2);
  });

  it("conserva la selección si el vehículo sigue existiendo", () => {
    let state = apply(initialState, snapshot(1, [vehicle("A"), vehicle("B")]));
    state = fleetReducer(state, { type: "select", vehicleId: "B" });
    state = apply(state, snapshot(2, [vehicle("B", { speed_kmh: 0 }), vehicle("A")]));
    expect(state.selectedId).toBe("B");
  });

  it("limpia la selección si el vehículo desaparece del snapshot", () => {
    let state = apply(initialState, snapshot(1, [vehicle("A")]));
    state = fleetReducer(state, { type: "select", vehicleId: "A" });
    state = apply(state, snapshot(2, [vehicle("Z")]));
    expect(state.selectedId).toBeNull();
  });

  it("al perder la conexión conserva los últimos datos visibles", () => {
    let state = apply(initialState, snapshot(1, [vehicle("A")]));
    state = fleetReducer(state, { type: "lost" });
    expect(state.connection).toBe("lost");
    expect(state.vehicles).toHaveLength(1);
    // Mientras reintenta sigue indicando la pérdida.
    state = fleetReducer(state, { type: "connecting" });
    expect(state.connection).toBe("lost");
  });

  it("tras reconectar, el snapshot completo restablece la conexión", () => {
    let state = apply(initialState, snapshot(1, [vehicle("A")]));
    state = fleetReducer(state, { type: "lost" });
    state = apply(state, snapshot(5, [vehicle("A"), vehicle("B")]));
    expect(state.connection).toBe("open");
    expect(state.vehicles).toHaveLength(2);
  });

  it("distingue la conexión abierta de una BD degradada", () => {
    let state = apply(initialState, snapshot(1, [vehicle("A")]));
    state = fleetReducer(state, {
      type: "status",
      at: 2,
      event: {
        type: "status", status: "degraded", generated_at: "2026-11-06T10:01:00Z",
        detail: "No se pudo leer la base de datos", last_snapshot_at: null,
      },
    });
    expect(state.connection).toBe("open");
    expect(state.data).toBe("degraded");
    expect(state.vehicles).toHaveLength(1);
  });
});
