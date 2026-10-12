import { describe, expect, it } from "vitest";
import { isSafeMermaidSvg } from "./MermaidDiagram";

describe("isSafeMermaidSvg", () => {
  it("accepts the ordinary SVG Mermaid produces", () => {
    expect(isSafeMermaidSvg('<svg viewBox="0 0 10 10"><g><path d="M0 0"/></g></svg>')).toBe(true);
  });

  it("accepts internal fragment references", () => {
    expect(isSafeMermaidSvg('<svg><use href="#arrowhead"/></svg>')).toBe(true);
  });

  it("rejects a script element", () => {
    expect(isSafeMermaidSvg('<svg><script>alert(1)</script></svg>')).toBe(false);
  });

  it("rejects an inline event handler", () => {
    expect(isSafeMermaidSvg('<svg><rect onload="alert(1)"/></svg>')).toBe(false);
  });

  it("rejects a javascript: URL", () => {
    expect(isSafeMermaidSvg('<svg><a href="javascript:alert(1)">x</a></svg>')).toBe(false);
  });

  it("rejects foreignObject, which can carry arbitrary HTML", () => {
    expect(isSafeMermaidSvg('<svg><foreignObject><img src=x onerror=alert(1)></foreignObject></svg>')).toBe(false);
  });

  it("rejects external references", () => {
    expect(isSafeMermaidSvg('<svg><image href="https://evil.example/x.png"/></svg>')).toBe(false);
  });

  it("rejects an empty string rather than injecting nothing silently", () => {
    expect(isSafeMermaidSvg("")).toBe(false);
  });
});