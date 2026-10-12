import { describe, expect, it } from "vitest";
import { looksLikeBrowserTask, looksLikeWebsiteBuild } from "./taskIntent";

describe("task intent routing", () => {
  it.each([
    "Build a website",
    "Create a responsive landing page for a bakery",
    "Develop a web app for tracking projects",
    "Update the website header and fix the mobile navigation",
    "Build a browser-based game website",
  ])("routes website creation to the build workflow: %s", (prompt) => {
    expect(looksLikeWebsiteBuild(prompt)).toBe(true);
    expect(looksLikeBrowserTask(prompt)).toBe(false);
  });

  it.each([
    "Visit the company website and summarize its pricing page",
    "Open https://example.com and inspect the page",
    "Click the sign-in button in the browser",
    "Take a screenshot of the current web page",
  ])("keeps explicit browsing tasks on Agent Computer: %s", (prompt) => {
    expect(looksLikeBrowserTask(prompt)).toBe(true);
  });

  it("does not treat a general website mention as a browser request", () => {
    expect(looksLikeBrowserTask("I need ideas for my website")).toBe(false);
  });
});
