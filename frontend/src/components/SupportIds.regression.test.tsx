import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import React from "react";
import { SupportIds } from "./SupportIds";

describe("SupportIds", () => {
  it("renders nothing when there is nothing to quote", () => {
    expect(renderToStaticMarkup(<SupportIds />)).toBe("");
    expect(renderToStaticMarkup(<SupportIds errorId={null} traceId={null} />)).toBe("");
    expect(renderToStaticMarkup(<SupportIds errorId="" traceId="" />)).toBe("");
  });

  it("shows the error id alone when that is all there is", () => {
    const html = renderToStaticMarkup(<SupportIds errorId="err-42" traceId={null} />);
    expect(html).toContain("Error ID: err-42");
    expect(html).not.toContain("Trace ID");
  });

  it("shows the trace id when a task stalled without erroring", () => {
    // The case an error id cannot cover: nothing failed, the work just never
    // finished, and the user needs an identifier for the whole run.
    const html = renderToStaticMarkup(<SupportIds errorId={null} traceId="a1b2c3d4e5f60718" />);
    expect(html).toContain("Trace ID: a1b2c3d4e5f60718");
    expect(html).not.toContain("Error ID");
  });

  it("shows both, in that order, when both exist", () => {
    const html = renderToStaticMarkup(<SupportIds errorId="err-42" traceId="a1b2c3d4" />);
    expect(html).toContain("Error ID: err-42");
    expect(html).toContain("Trace ID: a1b2c3d4");
    expect(html.indexOf("Error ID")).toBeLessThan(html.indexOf("Trace ID"));
  });
});