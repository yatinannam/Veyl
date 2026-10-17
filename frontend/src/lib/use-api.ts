"use client";

import { useCallback, useEffect, useState } from "react";

import { api, ApiError } from "./api";

interface Result<T> {
  key: string;
  path: string;
  data: T | null;
  error: ApiError | null;
}

function asApiError(error: unknown): ApiError {
  return error instanceof ApiError ? error : new ApiError(0, "network_error", "Network error.", null);
}

/** Loads `path` (or nothing when null). While reloading the same path, and after a failed
 * reload, the last good data stays visible; data from a different path is never shown. */
export function useApi<T>(path: string | null) {
  const [version, setVersion] = useState(0);
  const [result, setResult] = useState<Result<T> | null>(null);
  const key = path === null ? null : `${path}#${version}`;

  useEffect(() => {
    if (path === null || key === null) return;
    let active = true;
    api.get<T>(path).then(
      (data) => {
        if (active) setResult({ key, path, data, error: null });
      },
      (error: unknown) => {
        if (active) {
          setResult((previous) => ({
            key,
            path,
            data: previous?.path === path ? previous.data : null,
            error: asApiError(error),
          }));
        }
      },
    );
    return () => {
      active = false;
    };
  }, [key, path]);

  const reload = useCallback(() => setVersion((v) => v + 1), []);
  const samePath = result !== null && result.path === path;
  const current = result !== null && result.key === key ? result : null;
  return {
    data: samePath ? result.data : null,
    error: current?.error ?? null,
    loading: key !== null && current === null,
    reload,
  };
}
