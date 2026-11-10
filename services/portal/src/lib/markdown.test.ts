import { describe, expect, it } from "vitest";
import { parseInline, parseMarkdown } from "./markdown";

describe("parseInline", () => {
  it("reconoce negrita, cursiva y código", () => {
    expect(parseInline("**Ubicación**: lat *aprox.* `BOG-001`")).toEqual([
      { kind: "strong", children: [{ kind: "text", text: "Ubicación" }] },
      { kind: "text", text: ": lat " },
      { kind: "em", children: [{ kind: "text", text: "aprox." }] },
      { kind: "text", text: " " },
      { kind: "code", text: "BOG-001" },
    ]);
  });

  it("anida cursiva dentro de negrita", () => {
    expect(parseInline("**dato *muy* viejo**")).toEqual([
      { kind: "strong", children: [
        { kind: "text", text: "dato " },
        { kind: "em", children: [{ kind: "text", text: "muy" }] },
        { kind: "text", text: " viejo" },
      ] },
    ]);
  });

  it("deja como texto los marcadores sin cerrar, las multiplicaciones y los guiones bajos", () => {
    expect(parseInline("**sin cerrar")).toEqual([{ kind: "text", text: "**sin cerrar" }]);
    expect(parseInline("2 * 3 = 6")).toEqual([{ kind: "text", text: "2 * 3 = 6" }]);
    expect(parseInline("campo captured_at_local")).toEqual([{ kind: "text", text: "campo captured_at_local" }]);
  });

  it("solo usa __ como negrita fuera de palabras", () => {
    expect(parseInline("window.__xss=1 y window.__xss=2")).toEqual([
      { kind: "text", text: "window.__xss=1 y window.__xss=2" },
    ]);
    expect(parseInline("un __dato__ clave")).toEqual([
      { kind: "text", text: "un " },
      { kind: "strong", children: [{ kind: "text", text: "dato" }] },
      { kind: "text", text: " clave" },
    ]);
  });

  it("respeta los escapes", () => {
    expect(parseInline("\\*\\*literal\\*\\*")).toEqual([{ kind: "text", text: "**literal**" }]);
  });
});

describe("parseMarkdown", () => {
  it("separa párrafos y conserva los saltos de línea", () => {
    expect(parseMarkdown("**Ubicación**\nlat 4.6\n\nOtro párrafo")).toEqual([
      { kind: "paragraph", lines: [
        [{ kind: "strong", children: [{ kind: "text", text: "Ubicación" }] }],
        [{ kind: "text", text: "lat 4.6" }],
      ] },
      { kind: "paragraph", lines: [[{ kind: "text", text: "Otro párrafo" }]] },
    ]);
  });

  it("reconoce listas con viñetas y numeradas, con su inicio", () => {
    const blocks = parseMarkdown("- **Velocidad**: 0 km/h\n* Zona DEMO-Z01\n\n3. uno\n4. dos");
    expect(blocks).toEqual([
      { kind: "list", ordered: false, start: 1, items: [
        [[{ kind: "strong", children: [{ kind: "text", text: "Velocidad" }] }, { kind: "text", text: ": 0 km/h" }]],
        [[{ kind: "text", text: "Zona DEMO-Z01" }]],
      ] },
      { kind: "list", ordered: true, start: 3, items: [
        [[{ kind: "text", text: "uno" }]],
        [[{ kind: "text", text: "dos" }]],
      ] },
    ]);
  });

  it("une las líneas con sangría al elemento de lista y cierra la lista con texto normal", () => {
    const blocks = parseMarkdown("- BOG-001\n  sin movimiento\nFin");
    expect(blocks).toEqual([
      { kind: "list", ordered: false, start: 1, items: [
        [[{ kind: "text", text: "BOG-001" }], [{ kind: "text", text: "sin movimiento" }]],
      ] },
      { kind: "paragraph", lines: [[{ kind: "text", text: "Fin" }]] },
    ]);
  });

  it("muestra los encabezados como negrita e ignora las líneas horizontales", () => {
    expect(parseMarkdown("## Estado\n---\nok")).toEqual([
      { kind: "paragraph", lines: [[{ kind: "strong", children: [{ kind: "text", text: "Estado" }] }]] },
      { kind: "paragraph", lines: [[{ kind: "text", text: "ok" }]] },
    ]);
  });

  it("acepta finales de línea de Windows", () => {
    expect(parseMarkdown("a\r\nb")).toEqual([
      { kind: "paragraph", lines: [[{ kind: "text", text: "a" }], [{ kind: "text", text: "b" }]] },
    ]);
  });
});
