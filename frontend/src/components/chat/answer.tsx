import Link from "next/link";
import { Fragment } from "react";

import { ClassificationBadge, ClassificationBand } from "@/components/classification";
import type { Citation } from "@/lib/types";

const MARKER = /^\[(S\d+)\]$/;
const BOLD = /^\*\*([^*]+)\*\*$/;
const LIST_ITEM = /^\s*(?:[-*]|\d+\.)\s+/;
const HEADING = /^#{1,6}\s+/;

/** Snippets are raw document text; drop Markdown heading markers so they read as prose. */
function plainSnippet(snippet: string): string {
  return snippet.replace(/(^|\s)#{1,6}\s+/g, "$1");
}

type Block = { kind: "paragraph"; text: string } | { kind: "list"; items: string[] };

/** Models answer in light Markdown. Only paragraphs, bullet or numbered lists, headings
 * (shown as paragraphs), and bold are recognised; everything stays text, never HTML. */
function toBlocks(content: string): Block[] {
  return content
    .split(/\n\s*\n/)
    .map((chunk) => chunk.trim())
    .filter(Boolean)
    .map((chunk) => {
      const lines = chunk.split("\n");
      if (lines.every((line) => LIST_ITEM.test(line))) {
        return { kind: "list", items: lines.map((line) => line.replace(LIST_ITEM, "")) };
      }
      return { kind: "paragraph", text: chunk.replace(HEADING, "") };
    });
}

/** An answer in document type, with superscript source numbers linked to source cards. */
export function Answer({
  content,
  citations,
  idPrefix,
}: {
  content: string;
  citations: Citation[];
  idPrefix: string;
}) {
  const byLabel = new Map(citations.map((c) => [c.label, c]));

  function inline(text: string) {
    return text.split(/(\[S\d+\]|\*\*[^*]+\*\*)/g).map((part, index) => {
      const bold = BOLD.exec(part);
      if (bold) return <strong key={index} className="font-semibold">{bold[1]}</strong>;
      const match = MARKER.exec(part);
      if (!match) return <Fragment key={index}>{part}</Fragment>;
      const citation = byLabel.get(match[1]);
      if (!citation) return null;
      const number = citation.label.slice(1);
      return (
        <sup key={index}>
          <a
            href={`#${idPrefix}-${citation.label}`}
            aria-label={`Source ${number}: ${citation.document_title}`}
            className="ml-0.5 rounded-sm px-0.5 font-sans text-xs font-medium text-primary outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring/40"
          >
            {number}
          </a>
        </sup>
      );
    });
  }

  return (
    <div className="space-y-4">
      <div className="space-y-3 font-serif text-[1.0625rem] leading-relaxed">
        {toBlocks(content).map((block, index) =>
          block.kind === "list" ? (
            <ul key={index} className="list-disc space-y-1 pl-6">
              {block.items.map((item, itemIndex) => (
                <li key={itemIndex}>{inline(item)}</li>
              ))}
            </ul>
          ) : (
            <p key={index} className="whitespace-pre-line">
              {inline(block.text)}
            </p>
          ),
        )}
      </div>
      {citations.length > 0 ? (
        <ul aria-label="Sources" className="space-y-2">
          {citations.map((citation) => (
            <li
              key={citation.label}
              id={`${idPrefix}-${citation.label}`}
              className="flex gap-3 rounded-md border border-border bg-card p-3 scroll-mt-24"
            >
              <ClassificationBand level={citation.classification} />
              <div className="min-w-0 space-y-1">
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  <span className="text-xs font-medium text-muted-foreground">
                    {citation.label.slice(1)}
                  </span>
                  <Link
                    href={`/documents/${citation.document_id}`}
                    className="text-sm font-medium hover:underline"
                  >
                    {citation.document_title}
                  </Link>
                  <ClassificationBadge level={citation.classification} />
                </div>
                <p className="text-sm text-muted-foreground">{plainSnippet(citation.snippet)}</p>
              </div>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
