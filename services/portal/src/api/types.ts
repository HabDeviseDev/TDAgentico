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

// GET /v1/zones
export interface Zone {
  zone_id: string;
  name: string;
  center_lat: number;
  center_lon: number;
  radius_m: number;
  is_demo: boolean;
}

export interface ZonesResponse {
  generated_at: string;
  count: number;
  zones: Zone[];
}

// Evento SSE `alerts` (mismo contenido que GET /v1/alerts + type/sequence).
export interface StopAlert {
  alert_id: string;
  vehicle_id: string;
  zone_id: string;
  zone_name: string;
  started_at: string;
  last_sample_at: string;
  observed_duration_s: number;
  start_unknown: boolean;
  sample_count: number;
  lat: number;
  lon: number;
}

export interface InsufficientEvidence {
  vehicle_id: string;
  reason: string;
  detail: string;
  last_sample_at: string;
}

export interface AlertParameters {
  max_speed_kmh: number;
  max_displacement_m: number;
  max_gap_s: number;
  max_sample_age_s: number;
  alert_after_s: number;
  window_s: number;
  max_vehicles: number;
  max_rows: number;
}

export interface AlertsEvent {
  type: "alerts";
  sequence: number;
  evaluated_at: string;
  window_start: string;
  parameters: AlertParameters;
  complete: boolean;
  incomplete_reasons: string[];
  coverage: {
    vehicles_in_window: number;
    vehicles_evaluated: number;
    vehicles_not_evaluated: number;
    vehicles_without_recent_samples: number;
  };
  zone_ids: string[];
  alert_count: number;
  alerts: StopAlert[];
  insufficient_evidence_count: number;
  insufficient_evidence: InsufficientEvidence[];
}

export interface AlertsStatusEvent {
  type: "alerts_status";
  status: "ok" | "degraded";
  generated_at: string;
  detail: string | null;
  last_evaluation_at: string | null;
}
