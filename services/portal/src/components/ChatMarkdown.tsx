import { Fragment, type ReactNode } from "react";
import { parseMarkdown, type Inline } from "../lib/markdown";

/**
 * Respuesta del asistente con Markdown mínimo (ver `lib/markdown.ts`).
 * Solo crea elementos de React (p, ul, ol, li, strong, em, code, br) con el
 * texto como hijo: no usa dangerouslySetInnerHTML ni interpreta HTML.
 */
export function ChatMarkdown({ text, className }: { text: string; className?: string }) {
  const blocks = parseMarkdown(text);
  return (
    <div className={className}>
      {blocks.map((block, b) =>
        block.kind === "paragraph" ? (
          <p key={b}>{lines(block.lines)}</p>
        ) : block.ordered ? (
          <ol key={b} start={block.start === 1 ? undefined : block.start}>
            {block.items.map((item, i) => <li key={i}>{lines(item)}</li>)}
          </ol>
        ) : (
          <ul key={b}>
            {block.items.map((item, i) => <li key={i}>{lines(item)}</li>)}
          </ul>
        ),
      )}
    </div>
  );
}

function lines(rows: Inline[][]): ReactNode {
  return rows.map((row, i) => (
    <Fragment key={i}>
      {i > 0 && <br />}
      {inline(row)}
    </Fragment>
  ));
}

function inline(nodes: Inline[]): ReactNode {
  return nodes.map((node, i) => {
    switch (node.kind) {
      case "text":
        return <Fragment key={i}>{node.text}</Fragment>;
      case "strong":
        return <strong key={i}>{inline(node.children)}</strong>;
      case "em":
        return <em key={i}>{inline(node.children)}</em>;
      case "code":
        return <code key={i}>{node.text}</code>;
    }
  });
}
