/** Same-origin client for the Veyl API. The session cookie travels automatically; the
 * readable CSRF cookie is echoed in a header on state-changing requests. */

const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly traceId: string | null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

type SessionLostHandler = () => void;
const sessionLostHandlers = new Set<SessionLostHandler>();

/** Called whenever the API says the session is gone (401), except for the sign-in request
 * itself. Returns a function that stops listening. */
export function onSessionLost(handler: SessionLostHandler): () => void {
  sessionLostHandlers.add(handler);
  return () => {
    sessionLostHandlers.delete(handler);
  };
}

function readCookie(name: string): string | null {
  const prefix = `${name}=`;
  const match = document.cookie.split("; ").find((part) => part.startsWith(prefix));
  return match ? decodeURIComponent(match.slice(prefix.length)) : null;
}

/* A hosted API on a free plan sleeps when idle and takes up to a minute to wake. While a
 * request is slow, or the gateway in front of the API answers for it, listeners hear that
 * the API is starting up. */
export const SLOW_AFTER_MS = 4000;
export const WAKE_RETRY_MS = 3000;
export const WAKE_GIVE_UP_MS = 90_000;
const GATEWAY_STATUSES = new Set([502, 503, 504]);

type StartingUpListener = (startingUp: boolean) => void;
const startingUpListeners = new Set<StartingUpListener>();
let slowRequests = 0;

export function isStartingUp(): boolean {
  return slowRequests > 0;
}

/** Notified when the API starts or stops looking like it is waking up. */
export function onStartingUp(listener: StartingUpListener): () => void {
  startingUpListeners.add(listener);
  return () => {
    startingUpListeners.delete(listener);
  };
}

function changeSlowRequests(delta: number) {
  const before = isStartingUp();
  slowRequests += delta;
  if (isStartingUp() !== before) startingUpListeners.forEach((listener) => listener(isStartingUp()));
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function apiErrorBody(data: unknown) {
  return (data as { error?: { code?: string; message?: string; trace_id?: string } } | null)?.error;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let slow = false;
  const markSlow = () => {
    if (slow) return;
    slow = true;
    changeSlowRequests(1);
  };
  const timer = setTimeout(markSlow, SLOW_AFTER_MS);
  try {
    return await attempt<T>(method, path, body, Date.now(), markSlow);
  } finally {
    clearTimeout(timer);
    if (slow) changeSlowRequests(-1);
  }
}

async function attempt<T>(
  method: string,
  path: string,
  body: unknown,
  startedAt: number,
  markSlow: () => void,
): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  let payload: BodyInit | undefined;
  if (body instanceof FormData) {
    payload = body;
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  if (UNSAFE_METHODS.has(method)) {
    const token = readCookie("veyl_csrf");
    if (token) headers["X-CSRF-Token"] = token;
  }
  const response = await fetch(`/api/v1${path}`, {
    method,
    headers,
    body: payload,
    credentials: "same-origin",
  });
  if (response.status === 204) return undefined as T;
  const data: unknown = await response.json().catch(() => null);
  if (response.status === 401 && path !== "/auth/login") {
    sessionLostHandlers.forEach((handler) => handler());
  }
  const error = apiErrorBody(data);
  if (GATEWAY_STATUSES.has(response.status) && !error?.code) {
    // The gateway answered, not Veyl: the API is still waking. Reads are retried; a
    // change is never resent automatically, since it may already have been applied.
    markSlow();
    if (method === "GET" && Date.now() - startedAt < WAKE_GIVE_UP_MS) {
      await sleep(WAKE_RETRY_MS);
      return attempt<T>(method, path, body, startedAt, markSlow);
    }
    throw new ApiError(response.status, "starting_up", "The API is starting up.", null);
  }
  if (!response.ok) {
    throw new ApiError(
      response.status,
      error?.code ?? "http_error",
      error?.message ?? `Request failed with status ${response.status}.`,
      error?.trace_id ?? null,
    );
  }
  return data as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  delete: (path: string) => request<void>("DELETE", path),
};
