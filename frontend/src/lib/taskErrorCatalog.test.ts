import { describe, expect, it } from "vitest";
import { messageForTaskErrorCode, TASK_ERROR_MESSAGES } from "./taskErrorCatalog";

describe("messageForTaskErrorCode", () => {
  it("maps every catalog code to a distinct non-empty message", () => {
    const messages = new Set<string>();
    for (const code of Object.keys(TASK_ERROR_MESSAGES)) {
      const msg = messageForTaskErrorCode(code);
      expect(msg.length).toBeGreaterThan(10);
      messages.add(msg);
    }
    expect(messages.size).toBe(Object.keys(TASK_ERROR_MESSAGES).length);
  });

  it("falls back to backend message when code is unknown", () => {
    expect(messageForTaskErrorCode("not_a_real_code", "Backend said no")).toBe("Backend said no");
  });
});
