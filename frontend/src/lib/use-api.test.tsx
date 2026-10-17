import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useApi } from "./use-api";

afterEach(() => vi.unstubAllGlobals());

describe("useApi", () => {
  it("never shows one path's data while another path loads", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(Response.json({ name: "first" }))
      .mockReturnValueOnce(new Promise(() => {}));
    vi.stubGlobal("fetch", fetchMock);
    const { result, rerender } = renderHook(({ path }) => useApi<{ name: string }>(path), {
      initialProps: { path: "/conversations/a" },
    });
    await waitFor(() => expect(result.current.data).toEqual({ name: "first" }));
    rerender({ path: "/conversations/b" });
    expect(result.current.data).toBeNull();
    expect(result.current.loading).toBe(true);
  });

  it("keeps the last good data when a reload fails", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(Response.json([{ id: "d1" }]))
      .mockResolvedValueOnce(
        Response.json({ error: { code: "service_unavailable", message: "x", trace_id: "t" } }, { status: 503 }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useApi<{ id: string }[]>("/documents"));
    await waitFor(() => expect(result.current.data).toEqual([{ id: "d1" }]));
    act(() => result.current.reload());
    await waitFor(() => expect(result.current.error?.status).toBe(503));
    expect(result.current.data).toEqual([{ id: "d1" }]);
  });
});
