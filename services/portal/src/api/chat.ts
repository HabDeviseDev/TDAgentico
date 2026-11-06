// Contrato de POST /v1/chat y GET /v1/chat/status (servicio agent).

export type ChatStatus =
  | "answered"
  | "limit_reached"
  | "model_refused"
  | "timeout"
  | "not_configured"
  | "busy"
  | "model_error"
  | "internal_error";

export interface ChatWarning {
  code: string;
  message: string;
  tool: string | null;
}

export interface ChatToolCall {
  name: string;
  arguments: Record<string, unknown>;
  status: string;
  partial: boolean;
  truncated: boolean;
  duration_ms: number;
  attempts: number;
  source: string | null;
  source_time_utc: string | null;
  counts: Record<string, number>;
  error: { kind?: string; message?: string } | null;
}

export interface ChatSource {
  tool: string;
  source: string;
  time_utc: string | null;
  status: string;
}

export interface ChatEvidence {
  alerts: Array<{
    alert_id: string;
    vehicle_id: string;
    zone_id: string;
    zone_name: string;
    observed_duration_min: number;
    last_sample_at_utc: string;
    start_unknown: boolean;
  }>;
  vehicles: Array<{
    vehicle_id: string;
    captured_at_utc: string;
    lat: number;
    lon: number;
    speed_kmh: number;
    age_s: number;
    stale: boolean;
  }>;
  zones: Array<{ zone_id: string; name: string; radius_m: number; is_demo: boolean }>;
  insufficient_evidence: Array<{
    vehicle_id: string;
    reason: string;
    detail: string;
    last_sample_at_utc: string | null;
  }>;
}

export interface ChatResponse {
  request_id: string;
  status: ChatStatus;
  answer: string;
  provider: string;
  model: string;
  tools: ChatToolCall[];
  sources: ChatSource[];
  evidence: ChatEvidence;
  vehicle_ids: string[];
  zone_ids: string[];
  warnings: ChatWarning[];
  usage: { steps: number; max_steps: number; tool_calls: number; max_tool_calls: number; duration_ms: number };
}

export interface ChatServiceStatus {
  configured: boolean;
  problem: string | null;
  provider: string;
  model: string;
  conversation: "independent";
  limits: { max_question_chars: number; request_timeout_s: number };
}

export function isChatResponse(value: unknown): value is ChatResponse {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as ChatResponse).request_id === "string" &&
    typeof (value as ChatResponse).status === "string" &&
    Array.isArray((value as ChatResponse).warnings)
  );
}
