import type { SnapshotEvent, StatusEvent, Vehicle } from "../api/types";

/** Conexión con el servidor (independiente de la frescura de los datos). */
export type ConnectionState = "connecting" | "open" | "lost";

/** Salud de la lectura de la BD informada por el servidor. */
export type DataState = "unknown" | "ok" | "degraded";

export interface FleetState {
  connection: ConnectionState;
  data: DataState;
  dataDetail: string | null;
  vehicles: Vehicle[];
  /** Hora de generación del último snapshot recibido (UTC). */
  snapshotAt: string | null;
  sequence: number | null;
  hasSnapshot: boolean;
  selectedId: string | null;
  lastMessageAt: number | null;
}

export const initialState: FleetState = {
  connection: "connecting",
  data: "unknown",
  dataDetail: null,
  vehicles: [],
  snapshotAt: null,
  sequence: null,
  hasSnapshot: false,
  selectedId: null,
  lastMessageAt: null,
};

export type FleetAction =
  | { type: "connecting" }
  | { type: "open"; at: number }
  | { type: "lost" }
  | { type: "snapshot"; event: SnapshotEvent; at: number }
  | { type: "status"; event: StatusEvent; at: number }
  | { type: "heartbeat"; at: number }
  | { type: "select"; vehicleId: string | null };

export function fleetReducer(state: FleetState, action: FleetAction): FleetState {
  switch (action.type) {
    case "connecting":
      return { ...state, connection: state.hasSnapshot ? "lost" : "connecting" };
    case "open":
      return { ...state, connection: "open", lastMessageAt: action.at };
    case "lost":
      // Se conservan los últimos datos visibles.
      return { ...state, connection: "lost" };
    case "snapshot": {
      // Cada snapshot reemplaza el estado completo.
      const vehicles = action.event.vehicles;
      const stillExists =
        state.selectedId !== null && vehicles.some((v) => v.vehicle_id === state.selectedId);
      return {
        ...state,
        connection: "open",
        vehicles,
        snapshotAt: action.event.generated_at,
        sequence: action.event.sequence,
        hasSnapshot: true,
        selectedId: stillExists ? state.selectedId : null,
        lastMessageAt: action.at,
      };
    }
    case "status":
      return {
        ...state,
        connection: "open",
        data: action.event.status,
        dataDetail: action.event.detail,
        lastMessageAt: action.at,
      };
    case "heartbeat":
      return { ...state, connection: "open", lastMessageAt: action.at };
    case "select":
      return { ...state, selectedId: action.vehicleId };
  }
}
