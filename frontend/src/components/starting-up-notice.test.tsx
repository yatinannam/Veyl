import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api, SLOW_AFTER_MS } from "@/lib/api";

import { StartingUpNotice } from "./starting-up-notice";

describe("StartingUpNotice", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("appears while the API is slow and goes away when it answers", async () => {
    vi.useFakeTimers();
    let finish: (response: Response) => void = () => {};
    vi.stubGlobal(
      "fetch",
      vi.fn().mockReturnValue(new Promise<Response>((resolve) => (finish = resolve))),
    );
    render(<StartingUpNotice />);
    expect(screen.getByRole("status")).toBeEmptyDOMElement();

    const result = api.get("/auth/me");
    await act(() => vi.advanceTimersByTimeAsync(SLOW_AFTER_MS));
    expect(screen.getByRole("status")).toHaveTextContent("Starting up, this can take a minute.");

    await act(async () => {
      finish(Response.json({}));
      await result;
    });
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
  });
});
