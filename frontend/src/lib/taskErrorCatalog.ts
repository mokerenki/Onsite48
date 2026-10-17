export const TASK_ERROR_MESSAGES: Record<string, string> = {
  execution_timeout: "This task hit its time limit before it could finish.",
  worker_execution_failed: "The worker crashed while running this task. Nothing was left half-done.",
  websocket_disconnected: "The live connection dropped while the task was still running.",
  websocket_closed: "The live connection closed before the task finished.",
  model_configuration_error: "The model is not configured correctly for this workspace.",
  model_error: "The model failed while generating a response.",
  resume_checkpoint_missing: "This task cannot be resumed — its checkpoint is gone.",
  task_context_missing: "Task context is missing. Start a new run.",
  rate_limited: "Model rate limit hit. Retry in a moment.",
};

export function messageForTaskErrorCode(
  errorCode: string | null | undefined,
  fallback?: string | null,
): string {
  const code = (errorCode || "").trim();
  if (code && TASK_ERROR_MESSAGES[code]) return TASK_ERROR_MESSAGES[code];
  const lower = code.toLowerCase();
  for (const [k, v] of Object.entries(TASK_ERROR_MESSAGES)) {
    if (lower.includes(k) || k.includes(lower)) return v;
  }
  return (fallback || "").trim() || "This task could not be completed.";
}

export function formatTraceHint(traceId: string | null | undefined): string | null {
  const id = (traceId || "").trim();
  return id || null;
}
