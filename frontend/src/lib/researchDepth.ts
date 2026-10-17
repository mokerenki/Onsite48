export type ResearchDepth = "quick" | "standard" | "deep";
export const DEFAULT_RESEARCH_DEPTH: ResearchDepth = "standard";

export function normalizeResearchDepth(value: unknown): ResearchDepth | undefined {
  if (value === "quick" || value === "standard" || value === "deep") return value;
  return undefined;
}

export function withResearchDepth<T extends Record<string, unknown>>(
  payload: T,
  depth: unknown,
): T & { research_depth?: ResearchDepth } {
  const d = normalizeResearchDepth(depth);
  if (!d) return payload;
  return { ...payload, research_depth: d };
}
