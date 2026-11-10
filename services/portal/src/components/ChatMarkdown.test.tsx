import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ChatMarkdown } from "./ChatMarkdown";

const render = (text: string) => renderToStaticMarkup(<ChatMarkdown className="chat-answer" text={text} />);

describe("ChatMarkdown", () => {
  it("muestra negritas, listas y saltos de línea sin asteriscos literales", () => {
    const html = render(
      "**Ubicación**\nlat 4.60000, lon -74.10000\n\n- **Velocidad**: 0 km/h\n- Última lectura: 09/11/2026 10:26:28 America/Bogota\n\n**Este dato está desactualizado.**",
    );
    expect(html).toBe(
      '<div class="chat-answer">'
      + "<p><strong>Ubicación</strong><br/>lat 4.60000, lon -74.10000</p>"
      + "<ul><li><strong>Velocidad</strong>: 0 km/h</li>"
      + "<li>Última lectura: 09/11/2026 10:26:28 America/Bogota</li></ul>"
      + "<p><strong>Este dato está desactualizado.</strong></p>"
      + "</div>",
    );
    expect(html).not.toContain("**");
  });

  it("no interpreta HTML ni scripts: llegan como texto escapado", () => {
    const html = render(
      '<script>alert(1)</script>\n**<img src=x onerror="alert(2)">**\n- <a href="javascript:alert(3)">x</a>',
    );
    expect(html).not.toMatch(/<script/i);
    expect(html).not.toMatch(/<img/i);
    expect(html).not.toMatch(/<a /i);
    expect(html).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
    expect(html).toContain("<strong>&lt;img src=x onerror=&quot;alert(2)&quot;&gt;</strong>");
    expect(html).toContain("&lt;a href=&quot;javascript:alert(3)&quot;&gt;x&lt;/a&gt;");
  });

  it("deja los enlaces e imágenes de Markdown como texto", () => {
    const html = render("[portal](javascript:alert(1)) ![x](http://x/y.png)");
    expect(html).toBe('<div class="chat-answer"><p>[portal](javascript:alert(1)) ![x](http://x/y.png)</p></div>');
  });

  it("numera desde el inicio indicado", () => {
    expect(render("2. b\n3. c")).toBe('<div class="chat-answer"><ol start="2"><li>b</li><li>c</li></ol></div>');
  });
});
