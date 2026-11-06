import { describe, expect, it } from "vitest";
import { isChatResponse, type ChatResponse } from "../api/chat";
import { chatStatusLabel, selectableVehicles, toolStatusLabel, warningSeverity } from "./chatFormat";

const response = (vehicleIds: string[]): ChatResponse => ({
  request_id: "r1",
  status: "answered",
  answer: "<img src=x onerror=alert(1)>",
  provider: "anthropic",
  model: "m",
  tools: [],
  sources: [],
  evidence: { alerts: [], vehicles: [], zones: [], insufficient_evidence: [] },
  vehicle_ids: vehicleIds,
  zone_ids: [],
  warnings: [],
  usage: { steps: 1, max_steps: 5, tool_calls: 0, max_tool_calls: 6, duration_ms: 10 },
});

describe("chatFormat", () => {
  it("ofrece seleccionar solo los vehículos devueltos por el agente, acotados", () => {
    const ids = Array.from({ length: 15 }, (_, i) => "V-" + i);
    expect(selectableVehicles(response(ids))).toEqual({ shown: ids.slice(0, 12), hidden: 3 });
  });

  it("distingue advertencias graves de informativas", () => {
    expect(warningSeverity("tool_failed")).toBe("error");
    expect(warningSeverity("circuit_open")).toBe("error");
    expect(warningSeverity("model_auth_failed")).toBe("error");
    expect(warningSeverity("partial_evaluation")).toBe("warn");
    expect(warningSeverity("insufficient_evidence")).toBe("warn");
  });

  it("traduce estados conocidos y deja visibles los desconocidos", () => {
    expect(toolStatusLabel("no_matches")).toBe("sin coincidencias");
    expect(toolStatusLabel("otro")).toBe("otro");
    expect(chatStatusLabel("answered")).toBeNull();
    expect(chatStatusLabel("timeout")).toBe("Tiempo agotado");
  });

  it("reconoce respuestas estructuradas del agente", () => {
    expect(isChatResponse(response([]))).toBe(true);
    expect(isChatResponse({ detail: "x" })).toBe(false);
    expect(isChatResponse(null)).toBe(false);
  });
});
