import { describe, expect, it } from "vitest";
import { ageSeconds, isStale } from "./freshness";

const captured = "2026-11-06T10:00:00Z";
const at = (iso: string) => Date.parse(iso);

describe("freshness", () => {
  it("calcula la antigüedad desde captured_at", () => {
    expect(ageSeconds(captured, at("2026-11-06T10:01:30Z"))).toBe(90);
  });

  it("marca desactualizado solo al superar el umbral", () => {
    expect(isStale(captured, at("2026-11-06T10:02:00Z"), 120)).toBe(false);
    expect(isStale(captured, at("2026-11-06T10:02:01Z"), 120)).toBe(true);
  });

  it("el umbral es configurable", () => {
    expect(isStale(captured, at("2026-11-06T10:00:31Z"), 30)).toBe(true);
    expect(isStale(captured, at("2026-11-06T10:00:31Z"), 600)).toBe(false);
  });

  it("no da antigüedad negativa si el reloj del navegador va atrasado", () => {
    expect(ageSeconds(captured, at("2026-11-06T09:59:00Z"))).toBe(0);
  });
});
