import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  api,
  ApiError,
  isStartingUp,
  onStartingUp,
  SLOW_AFTER_MS,
  WAKE_GIVE_UP_MS,
  WAKE_RETRY_MS,
} from "./api";

function gateway(status: number) {
  return new Response("<html>Bad Gateway</html>", {
    status,
    headers: { "Content-Type": "text/html" },
  });
}

describe("a sleeping API", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("retries reads through gateway errors and reports that it is starting up", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(gateway(502))
      .mockResolvedValueOnce(gateway(504))
      .mockResolvedValueOnce(Response.json({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    const seen: boolean[] = [];
    const stop = onStartingUp((value) => seen.push(value));

    const result = api.get<{ ok: boolean }>("/auth/me");
    await vi.advanceTimersByTimeAsync(0);
    expect(isStartingUp()).toBe(true);
    await vi.advanceTimersByTimeAsync(WAKE_RETRY_MS * 2);

    await expect(result).resolves.toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(isStartingUp()).toBe(false);
    expect(seen).toEqual([true, false]);
    stop();
  });

  it("never resends a change; it says the API is starting up", async () => {
    const fetchMock = vi.fn().mockResolvedValue(gateway(504));
    vi.stubGlobal("fetch", fetchMock);
    const error = await api.post("/auth/login", {}).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).code).toBe("starting_up");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(isStartingUp()).toBe(false);
  });

  it("gives up on reads after the wake-up window", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() => Promise.resolve(gateway(502))),
    );
    const result = api.get("/documents").catch((e: unknown) => e);
    await vi.advanceTimersByTimeAsync(WAKE_GIVE_UP_MS + WAKE_RETRY_MS);
    expect(((await result) as ApiError).code).toBe("starting_up");
    expect(isStartingUp()).toBe(false);
  });

  it("treats Veyl's own 503 as an error, not as waking up", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      Response.json(
        { error: { code: "service_unavailable", message: "x", trace_id: "t" } },
        { status: 503 },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const error = (await api.get("/documents").catch((e: unknown) => e)) as ApiError;
    expect(error.code).toBe("service_unavailable");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("reports a slow request while it is pending", async () => {
    let finish: (response: Response) => void = () => {};
    vi.stubGlobal(
      "fetch",
      vi.fn().mockReturnValue(new Promise<Response>((resolve) => (finish = resolve))),
    );
    const result = api.get("/documents");
    await vi.advanceTimersByTimeAsync(SLOW_AFTER_MS - 1);
    expect(isStartingUp()).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(isStartingUp()).toBe(true);
    finish(Response.json([]));
    await result;
    expect(isStartingUp()).toBe(false);
  });
});
