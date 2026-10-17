"use client";

import { useCallback, useEffect, useState } from "react";

import { api, ApiError } from "./api";

interface Result<T> {
  key: string;
  data: T | null;
  error: ApiError | null;
}

function asApiError(error: unknown): ApiError {
  return error instanceof ApiError ? error : new ApiError(0, "network_error", "Network error.", null);
}

/** Loads `path` (or nothing when null). Keeps the previous data while reloading. */
export function useApi<T>(path: string | null) {
  const [version, setVersion] = useState(0);
  const [result, setResult] = useState<Result<T> | null>(null);
  const key = path === null ? null : `${path}#${version}`;

  useEffect(() => {
    if (path === null || key === null) return;
    let active = true;
    api.get<T>(path).then(
      (data) => {
        if (active) setResult({ key, data, error: null });
      },
      (error: unknown) => {
        if (active) setResult({ key, data: null, error: asApiError(error) });
      },
    );
    return () => {
      active = false;
    };
  }, [key, path]);

  const reload = useCallback(() => setVersion((v) => v + 1), []);
  const current = result !== null && result.key === key ? result : null;
  return {
    data: path === null ? null : (current?.data ?? result?.data ?? null),
    error: current?.error ?? null,
    loading: key !== null && current === null,
    reload,
  };
}
