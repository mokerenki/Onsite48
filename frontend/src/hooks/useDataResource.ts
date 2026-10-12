"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export type DataResourceState<T> =
  | { status: "loading"; data: T | null; error: null }
  | { status: "success"; data: T; error: null }
  | { status: "error"; data: T | null; error: string };

type Loader<T> = (signal: AbortSignal) => Promise<T>;
type CacheEntry = { value: unknown; storedAt: number };
const resourceCache = new Map<string, CacheEntry>();

export function useDataResource<T>(
  loader: Loader<T>,
  dependencies: readonly unknown[] = [],
  enabled = true,
  options: { cacheKey?: string; staleMs?: number; timeoutMs?: number } = {},
) {
  const [state, setState] = useState<DataResourceState<T>>({ status: "loading", data: null, error: null });
  const [reloadToken, setReloadToken] = useState(0);
  const loaderRef = useRef(loader);
  loaderRef.current = loader;

  const retry = useCallback(() => setReloadToken((value) => value + 1), []);

  useEffect(() => {
    if (!enabled) {
      setState((current) => current.data === null
        ? { status: "loading", data: null, error: null }
        : { status: "success", data: current.data, error: null });
      return;
    }

    const cached = options.cacheKey ? resourceCache.get(options.cacheKey) : undefined;
    const staleMs = options.staleMs ?? 15_000;
    const isRetry = reloadToken > 0;
    if (cached && !isRetry) {
      setState({ status: "success", data: cached.value as T, error: null });
      if (Date.now() - cached.storedAt < staleMs) return;
    }
    const controller = new AbortController();
    let active = true;
    let timedOut = false;
    const timeout = window.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, options.timeoutMs ?? 15_000);
    setState((current) => ({ status: "loading", data: current.data, error: null }));

    loaderRef.current(controller.signal)
      .then((data) => {
        window.clearTimeout(timeout);
        if (options.cacheKey) resourceCache.set(options.cacheKey, { value: data, storedAt: Date.now() });
        if (active) setState({ status: "success", data, error: null });
      })
      .catch((reason: unknown) => {
        window.clearTimeout(timeout);
        if (!active || (controller.signal.aborted && !timedOut)) return;
        setState((current) => ({
          status: "error",
          data: current.data,
          error: timedOut ? "The API did not respond in time. Check that the backend is running, then retry." : reason instanceof Error ? reason.message : "Unable to load this data.",
        }));
      });

    return () => {
      active = false;
      window.clearTimeout(timeout);
      controller.abort();
    };
    // The caller controls when a resource is reloaded through dependencies/retry.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, reloadToken, options.cacheKey, options.staleMs, options.timeoutMs, ...dependencies]);

  return { ...state, retry };
}