"use client";

import { Loader2, RefreshCw } from "lucide-react";
import React, { type ReactNode } from "react";
import type { DataResourceState } from "@/hooks/useDataResource";

type DataLoaderProps<T> = {
  state: DataResourceState<T>;
  isEmpty?: (data: T) => boolean;
  emptyMessage: string;
  errorTitle?: string;
  onRetry: () => void;
  children: (data: T) => ReactNode;
  className?: string;
};

export function DataLoader<T>({
  state,
  isEmpty = (data) => Array.isArray(data) && data.length === 0,
  emptyMessage,
  errorTitle = "We could not load this section",
  onRetry,
  children,
  className = "",
}: DataLoaderProps<T>) {
  if (state.status === "loading" && state.data === null) {
    return (
      <div className={`flex min-h-20 items-center justify-center gap-2 px-3 py-5 text-xs text-gray-500 ${className}`} role="status" aria-live="polite">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        <span>Loading…</span>
      </div>
    );
  }

  if (state.status === "error" && state.data === null) {
    return (
      <div className={`rounded-lg border border-red-200 bg-red-50 px-3 py-3 text-xs text-red-700 ${className}`} role="alert">
        <p className="font-medium">{errorTitle}</p>
        <p className="mt-1 text-red-600">{state.error}</p>
        <button type="button" onClick={onRetry} className="mt-2 inline-flex items-center gap-1.5 rounded-md border border-red-300 bg-white px-2.5 py-1.5 font-medium text-red-700 hover:bg-red-100 focus:outline-none focus:ring-2 focus:ring-red-400">
          <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" /> Retry
        </button>
      </div>
    );
  }

  const data = state.data as T;
  if (isEmpty(data)) {
    return <div className={`px-2 py-5 text-center text-xs text-gray-400 ${className}`}>{emptyMessage}</div>;
  }

  return (
    <div className={className}>
      {state.status === "error" && (
        <div className="mb-2 flex items-center justify-between gap-2 rounded-md border border-amber-200 bg-amber-50 px-2.5 py-2 text-xs text-amber-800" role="alert">
          <span>{state.error} Showing the last successful result.</span>
          <button type="button" onClick={onRetry} className="inline-flex shrink-0 items-center gap-1 font-medium hover:underline">
            <RefreshCw className="h-3 w-3" aria-hidden="true" /> Retry
          </button>
        </div>
      )}
      {children(data)}
    </div>
  );
}