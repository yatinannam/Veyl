import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import AskPage from "./page";

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: ReactNode }) => <a href={href}>{children}</a>,
}));

type Route = (method: string) => Promise<Response> | undefined;

function serve(routes: Record<string, Route>) {
  const fetchMock = vi.fn((url: string, init?: RequestInit) => {
    const path = url.replace("/api/v1", "");
    const reply = routes[path]?.(init?.method ?? "GET");
    return reply ?? Promise.resolve(Response.json({}));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => vi.unstubAllGlobals());

describe("AskPage", () => {
  it("keeps the question on screen until its new conversation has loaded", async () => {
    const fetchMock = serve({
      "/conversations": () => Promise.resolve(Response.json([])),
      "/query": () =>
        Promise.resolve(
          Response.json({
            conversation_id: "c1",
            message_id: "m1",
            answer: "Economy.",
            citations: [],
            trace_id: "t1",
          }),
        ),
      "/conversations/c1": () => new Promise<Response>(() => {}),
    });
    render(<AskPage />);
    fireEvent.change(screen.getByLabelText("Your question"), {
      target: { value: "What flights may I book?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([url]) => url === "/api/v1/conversations/c1")).toBe(true),
    );
    expect(screen.getByText("What flights may I book?")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Ask a question" })).not.toBeInTheDocument();
  });

  it("explains when a conversation cannot be opened", async () => {
    serve({
      "/conversations": () =>
        Promise.resolve(
          Response.json([
            { id: "c9", title: "Old question", created_at: "2026-10-01T10:00:00Z", updated_at: "2026-10-01T10:00:00Z" },
          ]),
        ),
      "/conversations/c9": () =>
        Promise.resolve(
          Response.json({ error: { code: "not_found", message: "x", trace_id: "t" } }, { status: 404 }),
        ),
    });
    render(<AskPage />);
    fireEvent.click(await screen.findByText("Old question"));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "That item doesn't exist or you no longer have access to it.",
    );
  });
});
