import React from "react";

/**
 * The identifiers a user can quote when reporting a problem.
 *
 * Both are shown because they answer different questions. The error id names
 * the failure itself; the trace id names the whole run that led to it, which is
 * what is needed when the complaint is "it has been stuck for ten minutes" and
 * nothing errored at all.
 *
 * The trace id is backend-supplied and already sanitised to hex at the edge
 * (see `app/core/tracing.py`), so it is rendered as text.
 */
export function SupportIds({
  errorId,
  traceId,
}: {
  errorId?: string | null;
  traceId?: string | null;
}) {
  if (!errorId && !traceId) return null;
  return (
    <>
      {errorId ? <p className="mt-2 text-xs text-red-200/60">Error ID: {errorId}</p> : null}
      {traceId ? <p className="mt-1 text-xs text-red-200/60">Trace ID: {traceId}</p> : null}
    </>
  );
}