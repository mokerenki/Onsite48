const WEBSITE_OBJECT = String.raw`(?:website|web\s*app|webpage|web\s*page|landing\s*page|site)`;
const BUILD_ACTION = String.raw`(?:build|create|make|design|develop|code|scaffold|generate|implement|update|edit|fix|redesign)`;

/** Build tasks take precedence over the broad browser/browser-session heuristics. */
export function looksLikeWebsiteBuild(prompt: string): boolean {
  const text = prompt || "";
  return new RegExp(`\\b${BUILD_ACTION}\\b.{0,100}\\b${WEBSITE_OBJECT}\\b|\\b${WEBSITE_OBJECT}\\b.{0,100}\\b${BUILD_ACTION}\\b`, "i").test(text);
}

/** Only explicit browsing actions create an Agent Computer session. */
export function looksLikeBrowserTask(prompt: string): boolean {
  const text = prompt || "";
  if (looksLikeWebsiteBuild(text)) return false;
  if (/\bhttps?:\/\/\S+/i.test(text)) return true;
  return (
    /\b(?:youtube|browser|web\s?page|tabs?|click|scroll|pause|play|screenshot)\b/i.test(text) ||
    /\b(?:browse|visit|navigate\s+to|open|go\s+to|inspect|read|summarize)\b.{0,80}\b(?:website|site|webpage|web\s?page|url|domain)\b/i.test(text)
  );
}
