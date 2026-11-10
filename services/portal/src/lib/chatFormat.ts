import type { ChatResponse, ChatSource, ChatStatus } from "../api/chat";

const toolLabels: Record<string, string> = {
  consultar_posiciones: "Posiciones actuales",
  consultar_zonas: "Zonas activas",
  consultar_alertas: "Alertas de detención",
};

const toolStatusLabels: Record<string, string> = {
  ok: "con resultados",
  no_matches: "sin coincidencias",
  insufficient_evidence: "evidencia insuficiente",
  unavailable: "no disponible",
  invalid_arguments: "argumentos inválidos",
  unsupported: "no soportado",
  rejected: "rechazada",
};

const statusLabels: Partial<Record<ChatStatus, string>> = {
  limit_reached: "Límite de pasos alcanzado",
  model_refused: "El modelo declinó responder",
  timeout: "Tiempo agotado",
  not_configured: "Agente sin configurar",
  busy: "Agente ocupado",
  model_error: "Modelo no disponible",
  internal_error: "Error interno",
};

export const toolLabel = (name: string) => toolLabels[name] ?? name;
export const toolStatusLabel = (status: string) => toolStatusLabels[status] ?? status;
export const chatStatusLabel = (status: ChatStatus) => statusLabels[status] ?? null;

/** Advertencias graves (fallos) frente a informativas (cobertura, truncado). */
const severeCodes = new Set([
  "tool_failed",
  "circuit_open",
  "request_timeout",
  "step_limit",
  "tool_call_limit",
  "model_refusal",
  "agent_not_configured",
  "internal_error",
  "tool_rejected",
  "invalid_tool_arguments",
]);

export function warningSeverity(code: string): "error" | "warn" {
  return severeCodes.has(code) || code.startsWith("model_") ? "error" : "warn";
}

/**
 * Hora de una fuente para mostrar: el texto local que calculó el agente (con
 * su zona), sin volver a convertirlo, y el UTC original para trazabilidad.
 */
export function sourceTime(s: ChatSource): { label: string; utc: string } | null {
  if (!s.time_utc || !s.time_local) return null;
  const verb = s.tool === "consultar_alertas" ? "evaluado" : "consultado";
  return { label: `${verb} el ${s.time_local}`, utc: s.time_utc };
}

/** Vehículos a ofrecer para selección: los que devolvió el agente (no los del texto). */
export function selectableVehicles(response: ChatResponse, max = 12): { shown: string[]; hidden: number } {
  const ids = response.vehicle_ids;
  return { shown: ids.slice(0, max), hidden: Math.max(0, ids.length - max) };
}

export const EXAMPLE_QUESTIONS = [
  "¿Qué vehículos llevan detenidos más de 20 minutos en zonas críticas?",
  "¿Qué vehículos tienen datos desactualizados?",
  "¿Qué zonas de demostración están activas?",
  "¿Cuál es la última posición del vehículo BOG-001?",
  "¿En qué zona está la alerta del vehículo BOG-001?",
];
