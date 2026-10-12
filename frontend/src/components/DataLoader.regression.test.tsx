import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import React from "react";
import { DataLoader } from "./DataLoader";
import type { DataResourceState } from "@/hooks/useDataResource";

describe("DataLoader", () => {
  it("does not present loading as an empty result", () => {
    const state: DataResourceState<string[]> = { status: "loading", data: null, error: null };
    const html = renderToStaticMarkup(<DataLoader state={state} emptyMessage="No items" onRetry={vi.fn()}>{() => <p>data</p>}</DataLoader>);
    expect(html).toContain("Loading");
    expect(html).not.toContain("No items");
  });

  it("renders an error and retry action distinctly", () => {
    const state: DataResourceState<string[]> = { status: "error", data: null, error: "Network unavailable" };
    const html = renderToStaticMarkup(<DataLoader state={state} emptyMessage="No items" onRetry={vi.fn()}>{() => <p>data</p>}</DataLoader>);
    expect(html).toContain("Network unavailable");
    expect(html).toContain("Retry");
    expect(html).not.toContain("No items");
  });

  it("renders empty only after a successful empty response", () => {
    const state: DataResourceState<string[]> = { status: "success", data: [], error: null };
    const html = renderToStaticMarkup(<DataLoader state={state} emptyMessage="No items" onRetry={vi.fn()}>{() => <p>data</p>}</DataLoader>);
    expect(html).toContain("No items");
  });
});