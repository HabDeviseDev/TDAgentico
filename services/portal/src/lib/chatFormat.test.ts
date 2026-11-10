import { describe, expect, it } from "vitest";
import { isChatResponse, type ChatResponse } from "../api/chat";
import { chatStatusLabel, selectableVehicles, sourceTime, toolStatusLabel, warningSeverity } from "./chatFormat";

const response = (vehicleIds: string[]): ChatResponse => ({
  request_id: "r1",
  status: "answered",
  answer: "<img src=x onerror=alert(1)>",
  provider: "anthropic",
  model: "m",
  display_timezone: "America/Bogota",
  tools: [],
  sources: [],
  evidence: { alerts: [], vehicles: [], zones: [], insufficient_evidence: [] },
  vehicle_ids: vehicleIds,
  zone_ids: [],
  warnings: [],
  usage: { steps: 1, max_steps: 5, tool_calls: 0, max_tool_calls: 6, duration_ms: 10 },
});

describe("sourceTime", () => {
  it("muestra la hora local del agente tal cual y conserva el UTC original", () => {
    const s = {
      tool: "consultar_posiciones", source: "GET /v1/vehicles", status: "ok",
      time_utc: "2026-11-09T15:30:34.000000Z", time_local: "09/11/2026 10:30:34 America/Bogota",
    };
    expect(sourceTime(s)).toEqual({
      label: "consultado el 09/11/2026 10:30:34 America/Bogota",
      utc: "2026-11-09T15:30:34.000000Z",
    });
    expect(sourceTime({ ...s, tool: "consultar_alertas" })?.label).toMatch(/^evaluado el /);
  });

  it("no muestra hora si falta alguno de los dos valores", () => {
    const s = { tool: "t", source: "GET /x", status: "ok", time_utc: null, time_local: null };
    expect(sourceTime(s)).toBeNull();
  });
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
