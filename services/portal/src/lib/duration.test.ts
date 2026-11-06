import { describe, expect, it } from "vitest";
import { formatDuration, formatObservedDuration, reasonLabel } from "./duration";

describe("formatDuration", () => {
  it("formatea segundos, minutos y horas", () => {
    expect(formatDuration(42)).toBe("42 s");
    expect(formatDuration(20 * 60)).toBe("20 min");
    expect(formatDuration(21 * 60 + 30)).toBe("21 min 30 s");
    expect(formatDuration(2 * 3600 + 5 * 60 + 10)).toBe("2 h 05 min");
  });

  it("marca la duración como mínimo cuando el inicio es desconocido", () => {
    expect(formatObservedDuration(7200, true)).toBe("≥ 2 h 00 min");
    expect(formatObservedDuration(1260, false)).toBe("21 min");
  });

  it("traduce los motivos conocidos y deja los demás como vienen", () => {
    expect(reasonLabel("max_rows")).toContain("filas");
    expect(reasonLabel("otro")).toBe("otro");
  });
});
