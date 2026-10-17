import { describe, expect, it } from "vitest";
import {
  appendBounded,
  MAX_DELTA_LENGTH,
  normalizeStreamEvent,
  narrowCostMetrics,
  narrowPlanSteps,
  narrowReportArtifacts,
  narrowResponseMode,
  narrowStepResults,
  narrowTaskStatus,
} from "./streamEvent";

const frame = (payload: unknown) => JSON.stringify(payload);

describe("normalizeStreamEvent", () => {
  it("accepts a well-formed event and coerces its fields", () => {
    const result = normalizeStreamEvent(
      frame({ type: "task_complete", timestamp: "2026-01-01T00:00:00Z", final_answer: "done", status: "completed" })
    );
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.event.type).toBe("task_complete");
    expect(result.event.final_answer).toBe("done");
    expect(result.event.status).toBe("completed");
  });

  it("rejects a frame that is not JSON instead of throwing", () => {
    const result = normalizeStreamEvent("{not json");
    expect(result).toEqual({ ok: false, reason: "frame is not valid JSON" });
  });

  it("rejects non-object frames", () => {
    expect(normalizeStreamEvent(frame([1, 2, 3])).ok).toBe(false);
    expect(normalizeStreamEvent(frame(null)).ok).toBe(false);
    expect(normalizeStreamEvent(frame("a string")).ok).toBe(false);
  });

  it("accepts an unknown event type as an opaque frame for a muted UI line", () => {
    const result = normalizeStreamEvent(frame({ type: "task_teleported", timestamp: "t", message: "x" }));
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.event.type).toBe("task_teleported");
  });

  it("requires an event type", () => {
    const result = normalizeStreamEvent(frame({ timestamp: "t" }));
    expect(result.ok).toBe(false);
  });

  it("supplies a timestamp when the peer omits one", () => {
    const result = normalizeStreamEvent(frame({ type: "answer_delta", content: "hi" }));
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(Number.isNaN(Date.parse(result.event.timestamp))).toBe(false);
  });

  it("refuses an array where a record is expected", () => {
    const result = normalizeStreamEvent(
      frame({ type: "needs_confirmation", timestamp: "t", tool_args: ["a", "b"] })
    );
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.event.tool_args).toBeUndefined();
  });

  it("drops fields whose type is wrong rather than coercing them", () => {
    const result = normalizeStreamEvent(
      frame({
        type: "task_error",
        timestamp: "t",
        step_index: "3",
        retryable: "yes",
        retryable_note: 1,
        tab_ids: [1, "two", 3],
      })
    );
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.event.step_index).toBeUndefined();
    expect(result.event.retryable).toBeUndefined();
    expect(result.event.tab_ids).toEqual([1, 3]);
  });

  it("bounds a single answer delta", () => {
    const huge = "x".repeat(MAX_DELTA_LENGTH + 5_000);
    const result = normalizeStreamEvent(frame({ type: "answer_delta", timestamp: "t", content: huge }));
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect((result.event.content as string).length).toBe(MAX_DELTA_LENGTH);
  });

  it("never throws on any JSON value", () => {
    for (const value of [0, "", true, [], {}, { type: 1 }, { type: "task_error", content: [] }]) {
      expect(() => normalizeStreamEvent(JSON.stringify(value))).not.toThrow();
    }
  });
});

describe("appendBounded", () => {
  it("keeps the tail when the accumulated answer exceeds the limit", () => {
    const result = appendBounded("a".repeat(10), "b".repeat(10), 12);
    expect(result.length).toBe(12);
    expect(result.endsWith("b".repeat(10))).toBe(true);
  });
});

describe("narrowPlanSteps", () => {
  it("keeps step-shaped records and drops the rest", () => {
    const steps = narrowPlanSteps([
      { description: "Read the inbox", tool_name: "gmail_search_messages" },
      { tool_name: "gmail_send_message" },
      "not a step",
      null,
    ]);
    expect(steps).toHaveLength(1);
    expect(steps?.[0].description).toBe("Read the inbox");
  });

  it("returns null rather than an empty plan for a non-array", () => {
    expect(narrowPlanSteps({ description: "x" })).toBeNull();
    expect(narrowPlanSteps([])).toBeNull();
  });
});

describe("narrowStepResults", () => {
  it("keeps only records with a usable step index", () => {
    const results = narrowStepResults([
      { step_index: 0, step: "Read", output: "3 messages" },
      { step: "No index" },
      { step_index: "2", step: "Bad index type" },
    ]);
    expect(Object.keys(results ?? {})).toEqual(["0"]);
  });

  it("always produces text output, stringifying a non-string", () => {
    const results = narrowStepResults([{ step_index: 1, output: { unexpected: true } }]);
    expect(typeof results?.[1].output).toBe("string");
  });

  it("drops tool output that is too large to render", () => {
    const results = narrowStepResults([{ step_index: 0, output: "y".repeat(50_000) }]);
    expect((results?.[0].output ?? "").length).toBeLessThanOrEqual(20_000);
  });
});

describe("narrowReportArtifacts", () => {
  it("requires an attachment id so a download button is never dead", () => {
    const artifacts = narrowReportArtifacts([
      { attachment_id: "a1", name: "report.md" },
      { name: "no-id.md" },
    ]);
    expect(artifacts).toHaveLength(1);
    expect(artifacts?.[0].attachment_id).toBe("a1");
  });

  it("accepts a single artifact record", () => {
    expect(narrowReportArtifacts({ attachment_id: "a2" })).toHaveLength(1);
    expect(narrowReportArtifacts(undefined)).toBeNull();
  });
});

describe("narrowResponseMode / narrowTaskStatus / narrowCostMetrics", () => {
  it("accepts only known response modes", () => {
    expect(narrowResponseMode("autonomous")).toBe("autonomous");
    expect(narrowResponseMode("turbo")).toBeNull();
  });

  it("accepts only statuses the workspace can render", () => {
    expect(narrowTaskStatus("paused")).toBe("paused");
    expect(narrowTaskStatus("vibing")).toBeNull();
  });

  it("accepts a finite cost total and rejects anything else", () => {
    expect(narrowCostMetrics({ total_cost_usd: 0.25 })).toEqual({ total_cost_usd: 0.25 });
    expect(narrowCostMetrics({ total_cost_usd: "free" })).toBeNull();
    expect(narrowCostMetrics({ total_cost_usd: Number.NaN })).toBeNull();
    expect(narrowCostMetrics("0.25")).toBeNull();
  });
});

describe("F0+ known backend phase events", () => {
  it("accepts mode_applied, step_assessment, step_retry, plan_revised", () => {
    for (const type of ["mode_applied", "step_assessment", "step_retry", "plan_revised"]) {
      const result = normalizeStreamEvent(frame({ type, research_depth: "deep", step_index: 1, verdict: "partial", attempt: 2 }));
      expect(result.ok).toBe(true);
      if (result.ok) expect(result.event.type).toBe(type);
    }
  });
});

describe("task_complete partial status", () => {
  it("preserves status partial for badge rendering", () => {
    const result = normalizeStreamEvent(
      frame({ type: "task_complete", status: "partial", final_answer: "partial answer" }),
    );
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.event.status).toBe("partial");
      expect(result.event.final_answer).toBe("partial answer");
    }
  });
});

describe("task_error catalog fields", () => {
  it("preserves new error_code values and trace_id", () => {
    for (const error_code of [
      "execution_timeout",
      "worker_execution_failed",
      "websocket_disconnected",
      "websocket_closed",
      "model_configuration_error",
      "model_error",
      "resume_checkpoint_missing",
      "task_context_missing",
    ]) {
      const result = normalizeStreamEvent(
        frame({ type: "task_error", error_code, message: "x", trace_id: "tr-1", retryable: true }),
      );
      expect(result.ok).toBe(true);
      if (result.ok) {
        expect(result.event.error_code).toBe(error_code);
        expect(result.event.trace_id).toBe("tr-1");
      }
    }
  });
});
