import { describe, expect, it } from "vitest";
import { normalizeResearchDepth, withResearchDepth } from "./researchDepth";

describe("researchDepth", () => {
  it("accepts only quick|standard|deep", () => {
    expect(normalizeResearchDepth("quick")).toBe("quick");
    expect(normalizeResearchDepth("standard")).toBe("standard");
    expect(normalizeResearchDepth("deep")).toBe("deep");
    expect(normalizeResearchDepth("turbo")).toBeUndefined();
  });

  it("attaches research_depth only when valid", () => {
    expect(withResearchDepth({ action: "run_task" }, "deep")).toEqual({
      action: "run_task",
      research_depth: "deep",
    });
    expect(withResearchDepth({ action: "run_task" }, "nope")).toEqual({ action: "run_task" });
  });
});
