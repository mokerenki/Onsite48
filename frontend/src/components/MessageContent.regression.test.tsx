import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import React from "react";
import { MessageContent } from "./MessageContent";

describe("MessageContent", () => {
  it("renders headings and bullet lists instead of a raw paragraph", () => {
    const html = renderToStaticMarkup(
      <MessageContent markdown={"## Meetings\n\n- 09:00 Standup\n- 14:00 Investor call"} />
    );
    expect(html).toContain("Meetings");
    expect(html).toContain("<ul");
    expect(html).not.toContain("## Meetings");
  });

  it("renders fenced code safely", () => {
    const html = renderToStaticMarkup(
      <MessageContent markdown={"```python\nprint('hello')\n```"} />
    );
    expect(html).toContain("print(&#x27;hello&#x27;)");
    expect(html).toContain("<pre");
  });
});