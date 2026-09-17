import { Fragment, type ReactNode } from "react";

// Small, dependency-free Markdown renderer — enough for the generated RCA docs
// (headings, lists, bold/italic/code, hr, blockquotes, paragraphs). We control
// the input (Gemini prose), so we don't need a full CommonMark parser.

function inline(text: string, keyBase: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  // Tokenize on `code`, **bold**, *italic* in one pass.
  const re = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    const tok = m[0];
    const key = `${keyBase}-${i++}`;
    if (tok.startsWith("`")) {
      nodes.push(
        <code key={key} className="rounded bg-white/10 px-1 py-0.5 font-mono text-[12px] text-signal-blue">
          {tok.slice(1, -1)}
        </code>
      );
    } else if (tok.startsWith("**")) {
      nodes.push(
        <strong key={key} className="font-semibold text-slate-100">
          {tok.slice(2, -2)}
        </strong>
      );
    } else {
      nodes.push(
        <em key={key} className="text-slate-300">
          {tok.slice(1, -1)}
        </em>
      );
    }
    last = m.index + tok.length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

export default function Markdown({ source }: { source: string }) {
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  let key = 0;

  const flushList = () => {
    if (!list) return;
    const items = list.items.map((it, idx) => (
      <li key={idx} className="ml-1 leading-relaxed">
        {inline(it, `li-${key}-${idx}`)}
      </li>
    ));
    blocks.push(
      list.ordered ? (
        <ol key={key++} className="mb-3 list-decimal space-y-1 pl-5 text-[13px] text-slate-300">
          {items}
        </ol>
      ) : (
        <ul key={key++} className="mb-3 list-disc space-y-1 pl-5 text-[13px] text-slate-300 marker:text-slate-600">
          {items}
        </ul>
      )
    );
    list = null;
  };

  for (const raw of lines) {
    const line = raw.trimEnd();
    if (!line.trim()) {
      flushList();
      continue;
    }
    const h = /^(#{1,4})\s+(.*)$/.exec(line);
    if (h) {
      flushList();
      const level = h[1].length;
      const cls = [
        "text-lg font-bold text-slate-50",
        "text-base font-semibold text-slate-100",
        "text-sm font-semibold text-slate-200",
        "text-[13px] font-semibold uppercase tracking-wide text-slate-400",
      ][level - 1];
      blocks.push(
        <div key={key++} className={`mb-2 mt-4 first:mt-0 ${cls}`}>
          {inline(h[2], `h-${key}`)}
        </div>
      );
      continue;
    }
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(line)) {
      flushList();
      blocks.push(<hr key={key++} className="my-4 border-white/10" />);
      continue;
    }
    const ol = /^\d+[.)]\s+(.*)$/.exec(line);
    const ul = /^[-*+]\s+(.*)$/.exec(line);
    if (ol) {
      if (!list || !list.ordered) {
        flushList();
        list = { ordered: true, items: [] };
      }
      list.items.push(ol[1]);
      continue;
    }
    if (ul) {
      if (!list || list.ordered) {
        flushList();
        list = { ordered: false, items: [] };
      }
      list.items.push(ul[1]);
      continue;
    }
    if (line.startsWith(">")) {
      flushList();
      blocks.push(
        <blockquote key={key++} className="mb-3 border-l-2 border-signal-blue/50 pl-3 text-[13px] italic text-slate-400">
          {inline(line.replace(/^>\s?/, ""), `bq-${key}`)}
        </blockquote>
      );
      continue;
    }
    flushList();
    blocks.push(
      <p key={key++} className="mb-3 text-[13px] leading-relaxed text-slate-300">
        {inline(line, `p-${key}`)}
      </p>
    );
  }
  flushList();

  return <Fragment>{blocks}</Fragment>;
}
