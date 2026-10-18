import { render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { Answer } from "./answer";
import type { Citation } from "@/lib/types";

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

const citations: Citation[] = [
  {
    label: "S1",
    document_id: "doc-travel",
    document_title: "Travel policy",
    chunk_id: "c1",
    snippet: "Employees may book economy flights.",
    classification: "internal",
  },
];

describe("Answer", () => {
  it("turns source markers into links to the matching source", () => {
    render(<Answer content="Economy only [S1]." citations={citations} idPrefix="m1" />);
    const marker = screen.getByRole("link", { name: "Source 1: Travel policy" });
    expect(marker).toHaveAttribute("href", "#m1-S1");
    const sources = screen.getByRole("list", { name: "Sources" });
    expect(within(sources).getByRole("link", { name: "Travel policy" })).toHaveAttribute(
      "href",
      "/documents/doc-travel",
    );
    expect(within(sources).getByText("Internal")).toBeInTheDocument();
  });

  it("drops markers that have no citation", () => {
    render(<Answer content="Something [S9] here." citations={citations} idPrefix="m2" />);
    expect(screen.queryByText("[S9]")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Source 9/ })).not.toBeInTheDocument();
  });

  it("shows no source list when nothing was cited", () => {
    render(<Answer content="I could not find that." citations={[]} idPrefix="m3" />);
    expect(screen.queryByRole("list", { name: "Sources" })).not.toBeInTheDocument();
  });

  it("renders the model's bold text and bullet lists without Markdown symbols", () => {
    render(
      <Answer
        content={"**Economy** only [S1].\n\n- Hotels up to **260 USD**\n- Book early"}
        citations={citations}
        idPrefix="m4"
      />,
    );
    expect(screen.getByText("Economy").tagName).toBe("STRONG");
    expect(screen.getByText("260 USD").tagName).toBe("STRONG");
    expect(screen.getByText("Book early").tagName).toBe("LI");
    expect(screen.queryByText(/\*\*/)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Source 1: Travel policy" })).toBeInTheDocument();
  });
});

describe("Answer snippets", () => {
  it("hides Markdown heading markers in source snippets", () => {
    render(
      <Answer
        content="Economy only [S1]."
        citations={[{ ...citations[0], snippet: "# Travel policy Employees may book economy flights." }]}
        idPrefix="m5"
      />,
    );
    expect(screen.getByText("Travel policy Employees may book economy flights.")).toBeInTheDocument();
  });

  it("links source markers inside bold text and keeps numbered lists numbered", () => {
    render(
      <Answer
        content={"**Economy only [S1]**\n\n1. Book early\n2. Keep receipts"}
        citations={citations}
        idPrefix="m6"
      />,
    );
    expect(screen.getByRole("link", { name: "Source 1: Travel policy" })).toBeInTheDocument();
    expect(screen.queryByText(/\[S1\]/)).not.toBeInTheDocument();
    expect(screen.getByText("Book early").closest("ol")).not.toBeNull();
  });
});
