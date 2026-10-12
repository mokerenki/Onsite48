/**
 * Normalization for events received over the agent WebSocket.
 *
 * The socket is the one boundary where data the UI did not author arrives at
 * runtime, so it is parsed and validated here rather than cast at the call
 * site. Three failure modes this exists to prevent:
 *
 * 1. A cast (`payload as StreamedEvent`) turns a malformed frame into a
 *    component that renders `undefined` as text, or worse, spreads an array
 *    into props and crashes the tree.
 * 2. Provider and tool output is attacker-influenced (a page title, an email
 *    body, a Slack message). Text rendered from it must stay text, so every
 *    field is coerced to a bounded string and nothing is ever treated as markup.
 * 3. Unbounded strings from a stream are a memory and layout hazard: a runaway
 *    `answer_delta` or a 10 MB tool output would be stored in React state
 *    forever.
 *
 * Unknown event types are *dropped with a reason* rather than rendered: a type
 * this build does not understand is a version mismatch, and rendering it by
 * guesswork is how a malformed payload becomes a broken workspace.
 */

/** A normalized event, or the reason it was rejected. */
export type NormalizationResult =
  | { ok: true; event: StreamedEvent }
  | { ok: false; reason: string };

/** Fields a single text field may occupy. Bounded, not "as large as the peer sends". */
export const MAX_TEXT_LENGTH = 20_000;
/** `answer_delta` accumulates, so a single chunk is bounded far more tightly. */
export const MAX_DELTA_LENGTH = 8_000;
/** Bound on the number of items in any list-shaped field. */
export const MAX_LIST_ITEMS = 200;

/** A plan step as the timeline needs it. Structural so this module has no UI imports. */
export interface PlanStepRecord {
  step_id?: string | number;
  description: string;
  tool_name?: string | null;
  tool_args?: Record<string, unknown>;
  depends_on?: string[];
}

/** One executed step, shaped exactly as the timeline renders it. */
export interface StepResultRecord {
  step: string;
  tool: string | null;
  output: string;
  step_index: number;
}

/** A downloadable report artifact, shaped exactly as the download button needs it. */
export interface ReportArtifactRecord {
  attachment_id: string;
  name: string;
  download_url: string;
  reason: string;
  summary: string;
  content_type: string;
  artifact_type?: string;
}

export interface StreamedEvent {
  type: string;
  task_id?: string;
  thread_id?: string;
  message?: string;
  timestamp: string;
  content?: unknown;
  status?: string;
  final_answer?: string;
  error?: string;
  error_code?: string;
  error_id?: string;
  retryable?: boolean;
  /** Actionable recovery hint supplied with structured errors. */
  suggestion?: string;
  trace_id?: string;
  trace?: string[];
  tool_name?: string;
  tool_args?: Record<string, unknown>;
  step_description?: string;
  step_index?: number;
  task_scoped_read_only_available?: boolean;
  reason?: string;
  question?: string;
  url?: string;
  title?: string;
  handoff_options?: string[];
  handoff_surface?: string;
  is_follow_up?: boolean;
  domain?: string;
  response_mode?: string;
  mode?: string;
  session_id?: string;
  current_url?: string | null;
  current_observation?: Record<string, unknown>;
  tab_ids?: number[];
  primary_tab_id?: number | null;
  paused_at?: string;
  resumed_at?: string;
  /** Restored graph state on a reconnect; a record, never trusted field-by-field. */
  state?: Record<string, unknown>;
  plan?: PlanStepRecord[];
  verification?: Record<string, unknown>;
  completion_proof?: Record<string, unknown>;
  artifact?: Record<string, unknown>;
  artifacts?: Record<string, unknown>[];
  preview_id?: string;
  preview_url?: string;
  project_name?: string;
  expires_at?: number;
  cost_metrics?: Record<string, unknown>;
}

/** Event types this client knows how to act on. */
const KNOWN_EVENT_TYPES = new Set([
  "task_start",
  "task_paused",
  "task_resumed",
  "task_complete",
  "task_error",
  "error",
  "answer_delta",
  "fast_mode",
  "planner_output",
  "executor_output",
  "executor_output_delta",
  "cost_update",
  "verifier_output",
  "needs_confirmation",
  "needs_clarification",
  "needs_user_intervention",
  "user_intervention_required",
  "browser_session_state",
  "pong",
  "artifact_created",
  "website_preview",
]);

function asString(value: unknown, max = MAX_TEXT_LENGTH): string | undefined {
  if (typeof value !== "string") return undefined;
  return value.length > max ? value.slice(0, max) : value;
}

function asFiniteNumber(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  return undefined;
}

function asBoolean(value: unknown): boolean | undefined {
  return typeof value === "boolean" ? value : undefined;
}

function asStringArray(value: unknown, max = MAX_LIST_ITEMS): string[] | undefined {
  if (!Array.isArray(value)) return undefined;
  return value
    .filter((item): item is string => typeof item === "string")
    .slice(0, max);
}

function asNumberArray(value: unknown): number[] | undefined {
  if (!Array.isArray(value)) return undefined;
  return value.filter((item): item is number => typeof item === "number" && Number.isFinite(item));
}

/**
 * A list of plan-step-shaped records.
 *
 * Elements that are not step-shaped are dropped rather than passed through: the
 * timeline renders `step.description` and `step.tool_name`, and a record missing
 * them would render `undefined` in the user's plan.
 */
function asRecordArray(value: unknown): Record<string, unknown>[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const records = value
    .map((item) => asRecord(item))
    .filter((item): item is Record<string, unknown> => item !== undefined)
    .slice(0, MAX_LIST_ITEMS);
  return records.length ? records : undefined;
}

/**
 * A plain record, or `undefined`.
 *
 * Arrays are rejected deliberately: `typeof [] === "object"`, so without this a
 * list arriving where a `tool_args` record is expected would be stored as an
 * object and spread into a props object somewhere downstream.
 */
function asRecord(value: unknown): Record<string, unknown> | undefined {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return undefined;
  return value as Record<string, unknown>;
}

/**
 * Parse and validate one WebSocket frame.
 *
 * Accepts the raw `MessageEvent.data`, so JSON parsing failures surface here as
 * a rejection rather than as an exception inside a socket handler.
 */
export function normalizeStreamEvent(raw: unknown): NormalizationResult {
  let payload: unknown = raw;

  if (typeof raw === "string") {
    try {
      payload = JSON.parse(raw);
    } catch {
      return { ok: false, reason: "frame is not valid JSON" };
    }
  }

  const record = asRecord(payload);
  if (!record) return { ok: false, reason: "frame is not an object" };

  const type = typeof record.type === "string" ? record.type : "";
  if (!type) return { ok: false, reason: "frame has no event type" };
  if (!KNOWN_EVENT_TYPES.has(type)) {
    // Not an error the user should see: it is a version mismatch, and the
    // correct response is to ignore it rather than guess at its shape.
    return { ok: false, reason: `unknown event type: ${type}` };
  }

  const timestamp = asString(record.timestamp, 64) ?? new Date().toISOString();

  const event: StreamedEvent = { type, timestamp };

  const assign = <K extends keyof StreamedEvent>(key: K, value: StreamedEvent[K]) => {
    if (value !== undefined) event[key] = value;
  };

  assign("task_id", asString(record.task_id, 128));
  assign("thread_id", asString(record.thread_id, 128));
  assign("message", asString(record.message));
  assign("status", asString(record.status, 64));
  assign("final_answer", asString(record.final_answer));
  assign("error", asString(record.error));
  assign("error_code", asString(record.error_code, 128));
  assign("retryable", asBoolean(record.retryable));
  assign("suggestion", asString(record.suggestion));
  assign("trace_id", asString(record.trace_id, 128));
  assign("tool_name", asString(record.tool_name, 256));
  assign("tool_args", asRecord(record.tool_args));
  assign("step_description", asString(record.step_description));
  assign("step_index", asFiniteNumber(record.step_index));
  assign("task_scoped_read_only_available", asBoolean(record.task_scoped_read_only_available));
  assign("reason", asString(record.reason));
  assign("question", asString(record.question));
  assign("url", asString(record.url, 2048));
  assign("title", asString(record.title, 512));
  assign("handoff_options", asStringArray(record.handoff_options));
  assign("handoff_surface", asString(record.handoff_surface, 64));
  assign("is_follow_up", asBoolean(record.is_follow_up));
  assign("domain", asString(record.domain, 64));
  assign("response_mode", asString(record.response_mode, 64));
  assign("mode", asString(record.mode, 64));
  assign("session_id", asString(record.session_id, 128));
  assign("current_observation", asRecord(record.current_observation));
  assign("tab_ids", asNumberArray(record.tab_ids));
  assign("paused_at", asString(record.paused_at, 64));
  assign("resumed_at", asString(record.resumed_at, 64));
  assign("error_id", asString(record.error_id, 128));
  assign("trace", asStringArray(record.trace, 200));
  assign("state", asRecord(record.state));
  assign("plan", narrowPlanSteps(record.plan) ?? undefined);
  assign("verification", asRecord(record.verification));
  assign("completion_proof", asRecord(record.completion_proof));
  assign("artifact", asRecord(record.artifact));
  assign("artifacts", asRecordArray(record.artifacts));
  assign("preview_id", asString(record.preview_id, 128));
  assign("preview_url", asString(record.preview_url, 2048));
  assign("project_name", asString(record.project_name, 512));
  assign("expires_at", asFiniteNumber(record.expires_at));
  assign("cost_metrics", asRecord(record.cost_metrics));

  if (typeof record.primary_tab_id === "number" || record.primary_tab_id === null) {
    assign("primary_tab_id", record.primary_tab_id);
  }
  if (typeof record.current_url === "string" || record.current_url === null) {
    assign("current_url", record.current_url);
  }

  // `content` carries a different shape per event type, so it is bounded but
  // not reshaped here; each `case` below validates what it needs.
  if (record.content !== undefined) {
    if (typeof record.content === "string") {
      assign("content", asString(record.content, type === "answer_delta" ? MAX_DELTA_LENGTH : MAX_TEXT_LENGTH));
    } else {
      assign("content", record.content);
    }
  }

  return { ok: true, event };
}

/**
 * Accumulated answer text, bounded.
 *
 * A delta stream is the one place an unbounded payload grows without limit, so
 * the total is capped rather than trusting the peer to stop.
 */
export function appendBounded(previous: string, delta: string, limit = 400_000): string {
  const combined = previous + delta;
  return combined.length > limit ? combined.slice(combined.length - limit) : combined;
}

/** Task statuses the workspace has a screen for. */
const TASK_STATUSES = new Set([
  "idle",
  "connecting",
  "running",
  "paused",
  "completed",
  "partial",
  "cancelled",
  "failed",
  "disconnected",
  "error",
]);

/**
 * Task status, narrowed to a state the UI actually knows.
 *
 * An unrecognised status is dropped rather than stored, so a version mismatch
 * leaves the workspace showing a known state instead of silently freezing on
 * whatever view it was in.
 */
export function narrowTaskStatus(value: unknown): string | null {
  return typeof value === "string" && TASK_STATUSES.has(value) ? value : null;
}

/**
 * Response mode, narrowed to what the UI actually renders.
 *
 * An unrecognised mode is dropped rather than shown: the badges are a fixed set,
 * and passing an arbitrary string through would render a label the product does
 * not have.
 */
export function narrowResponseMode(value: unknown): "quick" | "web_research" | "autonomous" | null {
  return value === "quick" || value === "web_research" || value === "autonomous" ? value : null;
}

/** Cost metrics, only when the total is actually a finite number to format. */
export function narrowCostMetrics(value: unknown): { total_cost_usd?: number } | null {
  const record = asRecord(value);
  if (!record) return null;
  const total = record.total_cost_usd;
  // A non-numeric or non-finite total is dropped rather than shown: the UI
  // formats this with `.toFixed(6)`, so NaN would render "NaN" as a price.
  if (total === undefined) return {};
  return typeof total === "number" && Number.isFinite(total) ? { total_cost_usd: total } : null;
}

/**
 * Plan steps from any untrusted value.
 *
 * Shared by the live `planner_output` path and the `task_resumed` state
 * restore, because both arrive from outside the component and both end up
 * rendered as the user's plan.
 */
export function narrowPlanSteps(value: unknown): PlanStepRecord[] | null {
  if (!Array.isArray(value)) return null;
  const steps: PlanStepRecord[] = [];
  for (const item of value.slice(0, MAX_LIST_ITEMS)) {
    const record = asRecord(item);
    // The timeline renders `description`; a step without one is not a step.
    if (!record || typeof record.description !== "string") continue;
    const step: PlanStepRecord = { description: record.description };
    if (typeof record.step_id === "string" || typeof record.step_id === "number") {
      step.step_id = record.step_id;
    }
    if (typeof record.tool_name === "string" || record.tool_name === null) {
      step.tool_name = record.tool_name;
    }
    const toolArgs = asRecord(record.tool_args);
    if (toolArgs) step.tool_args = toolArgs;
    const dependsOn = asStringArray(record.depends_on);
    if (dependsOn) step.depends_on = dependsOn;
    steps.push(step);
  }
  return steps.length ? steps : null;
}

/** Step results from any untrusted value, keyed by step index. */
export function narrowStepResults(value: unknown): Record<number, StepResultRecord> | null {
  if (!Array.isArray(value)) return null;
  const results: Record<number, StepResultRecord> = {};
  for (const item of value.slice(0, MAX_LIST_ITEMS)) {
    const record = asRecord(item);
    if (!record) continue;
    const index = record.step_index;
    if (typeof index !== "number" || !Number.isFinite(index)) continue;
    results[index] = {
      step: typeof record.step === "string" ? record.step : "",
      tool: typeof record.tool === "string" ? record.tool : null,
      // `output` is rendered verbatim. A non-string is stringified rather than
      // stored as-is so the surface is always text, never an object React will
      // try to render.
      output:
        typeof record.output === "string"
          ? record.output.slice(0, MAX_TEXT_LENGTH)
          : JSON.stringify(record.output ?? null).slice(0, MAX_TEXT_LENGTH),
      step_index: index,
    };
  }
  return Object.keys(results).length ? results : null;
}

/**
 * Report artifacts from any untrusted value.
 *
 * `attachment_id` is required because it is what the download button resolves;
 * an artifact without one cannot be acted on and is dropped rather than
 * rendered as a button that does nothing.
 */
export function narrowReportArtifacts(value: unknown): ReportArtifactRecord[] | null {
  const records = Array.isArray(value)
    ? value.slice(0, MAX_LIST_ITEMS).map((item) => asRecord(item))
    : [asRecord(value)];
  const artifacts: ReportArtifactRecord[] = [];
  for (const record of records) {
    if (!record || typeof record.attachment_id !== "string" || !record.attachment_id) continue;
    const artifact: ReportArtifactRecord = {
      attachment_id: record.attachment_id,
      name: asString(record.name, 512) ?? record.attachment_id,
      download_url: asString(record.download_url, 2048) ?? "",
      reason: asString(record.reason) ?? "",
      summary: asString(record.summary) ?? "",
      content_type: asString(record.content_type, 256) ?? "application/octet-stream",
    };
    const artifactType = asString(record.artifact_type, 128);
    if (artifactType) artifact.artifact_type = artifactType;
    artifacts.push(artifact);
  }
  return artifacts.length ? artifacts : null;
}
