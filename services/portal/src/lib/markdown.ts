/**
 * Markdown mínimo para las respuestas del asistente: párrafos con saltos de
 * línea, listas con viñetas o numeradas, **negrita**, *cursiva* y `código`.
 *
 * Devuelve un árbol de datos que `ChatMarkdown` convierte en elementos de
 * React. Nunca produce HTML: el texto (incluido cualquier `<script>` o
 * `<img onerror>` que traiga la respuesta) llega a React como texto y React
 * lo escapa. Los enlaces, imágenes, tablas y HTML no se interpretan y quedan
 * como texto literal; los encabezados (`# …`) se muestran como un párrafo en
 * negrita.
 *
 * Se implementa aquí en lugar de agregar una dependencia porque el modelo
 * solo debe usar este subconjunto (ver el prompt del agente) y así no hay
 * HTML intermedio que sanitizar.
 */

export type Inline =
  | { kind: "text"; text: string }
  | { kind: "strong"; children: Inline[] }
  | { kind: "em"; children: Inline[] }
  | { kind: "code"; text: string };

export type Block =
  | { kind: "paragraph"; lines: Inline[][] }
  | { kind: "list"; ordered: boolean; start: number; items: Inline[][][] };

const BULLET = /^\s{0,3}[-*+]\s+(.*)$/;
const NUMBERED = /^\s{0,3}(\d{1,9})[.)]\s+(.*)$/;
const HEADING = /^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$/;
const RULE = /^\s{0,3}([-*_])(\s*\1){2,}\s*$/;
const CONTINUATION = /^\s{2,}\S/;
const WORD = /[\p{L}\p{N}]/u;

export function parseMarkdown(source: string): Block[] {
  const blocks: Block[] = [];
  let paragraph: Inline[][] | null = null;
  let list: Extract<Block, { kind: "list" }> | null = null;

  const closeParagraph = () => {
    if (paragraph) blocks.push({ kind: "paragraph", lines: paragraph });
    paragraph = null;
  };
  const closeList = () => {
    if (list) blocks.push(list);
    list = null;
  };

  for (const raw of source.replace(/\r\n?/g, "\n").split("\n")) {
    const line = raw.replace(/\s+$/, "");
    if (line.trim() === "" || RULE.test(line)) {
      closeParagraph();
      closeList();
      continue;
    }
    const bullet = BULLET.exec(line);
    const numbered = bullet ? null : NUMBERED.exec(line);
    if (bullet || numbered) {
      closeParagraph();
      const ordered = numbered !== null;
      if (!list || list.ordered !== ordered) {
        closeList();
        list = { kind: "list", ordered, start: numbered ? Number(numbered[1]) : 1, items: [] };
      }
      list.items.push([parseInline(bullet ? bullet[1] : numbered![2])]);
      continue;
    }
    if (list && CONTINUATION.test(line)) {
      // Línea con sangría dentro de un elemento de lista: nueva línea del mismo elemento.
      list.items[list.items.length - 1].push(parseInline(line.trim()));
      continue;
    }
    closeList();
    const heading = HEADING.exec(line);
    if (heading) {
      closeParagraph();
      blocks.push({ kind: "paragraph", lines: [[{ kind: "strong", children: parseInline(heading[1]) }]] });
      continue;
    }
    (paragraph ??= []).push(parseInline(line.trim()));
  }
  closeParagraph();
  closeList();
  return blocks;
}

export function parseInline(source: string): Inline[] {
  const out: Inline[] = [];
  let text = "";
  const flush = () => {
    if (text) out.push({ kind: "text", text });
    text = "";
  };

  let i = 0;
  while (i < source.length) {
    const ch = source[i];
    if (ch === "\\" && i + 1 < source.length && "\\`*_#-+.!".includes(source[i + 1])) {
      text += source[i + 1];
      i += 2;
      continue;
    }
    if (ch === "`") {
      const end = source.indexOf("`", i + 1);
      if (end > i + 1) {
        flush();
        out.push({ kind: "code", text: source.slice(i + 1, end) });
        i = end + 1;
        continue;
      }
    }
    if ((ch === "*" || ch === "_") && source[i + 1] === ch) {
      const marker = ch + ch;
      const end = source.indexOf(marker, i + 2);
      // "__" solo marca negrita fuera de palabras (no en window.__xss=1).
      const intraword = ch === "_" && (WORD.test(source[i - 1] ?? "") || WORD.test(source[end + 2] ?? ""));
      if (end > i + 2 && !intraword && !/\s/.test(source[i + 2]) && !/\s/.test(source[end - 1])) {
        flush();
        out.push({ kind: "strong", children: parseInline(source.slice(i + 2, end)) });
        i = end + 2;
        continue;
      }
    }
    // Cursiva solo con "*": "_" aparece en identificadores (p. ej. captured_at).
    if (ch === "*" && source[i + 1] !== "*") {
      const end = findSingle(source, i + 1);
      if (end > i + 1 && !/\s/.test(source[i + 1]) && !/\s/.test(source[end - 1])) {
        flush();
        out.push({ kind: "em", children: parseInline(source.slice(i + 1, end)) });
        i = end + 1;
        continue;
      }
    }
    text += ch;
    i += 1;
  }
  flush();
  return out;
}

/** Siguiente "*" que no forma parte de "**". */
function findSingle(source: string, from: number): number {
  for (let j = from; j < source.length; j += 1) {
    if (source[j] !== "*") continue;
    if (source[j + 1] === "*") {
      j += 1;
      continue;
    }
    return j;
  }
  return -1;
}
