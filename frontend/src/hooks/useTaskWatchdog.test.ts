import { describe, expect, it } from "vitest";
import { isTaskProgressEventType, SILENCE_THRESHOLD_MS, RECONNECT_GRACE_MS } from "./useTaskWatchdog";

describe("useTaskWatchdog constants & helpers", () => {
  it("uses the required silence and reconnect thresholds", () => {
    expect(SILENCE_THRESHOLD_MS).toBe(45_000);
    expect(RECONNECT_GRACE_MS).toBe(10_000);
  });

  it("treats pong/ping as transport-only, not task progress", () => {
    expect(isTaskProgressEventType("pong")).toBe(false);
    expect(isTaskProgressEventType("ping")).toBe(false);
    expect(isTaskProgressEventType("executor_output")).toBe(true);
  });
});
