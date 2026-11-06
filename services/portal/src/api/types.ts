// Contrato de /v1/vehicles/stream. Las fechas llegan en UTC (ISO-8601 con Z).

export interface Vehicle {
  vehicle_id: string;
  event_id: string;
  captured_at: string;
  lat: number;
  lon: number;
  speed_kmh: number;
  updated_at: string;
}

export interface SnapshotEvent {
  type: "snapshot";
  sequence: number;
  generated_at: string;
  count: number;
  vehicles: Vehicle[];
}

export interface StatusEvent {
  type: "status";
  status: "ok" | "degraded";
  generated_at: string;
  detail: string | null;
  last_snapshot_at: string | null;
}

export interface HeartbeatEvent {
  type: "heartbeat";
  generated_at: string;
}
