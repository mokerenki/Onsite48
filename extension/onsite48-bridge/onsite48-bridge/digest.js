// Shared page digest for observation and action revalidation.
// Single source of truth so observation and revalidation never disagree.

/**
 * Normalize text the same way everywhere: collapse whitespace, trim, clip.
 * @param {string} value
 * @param {number} limit
 */
export function normalizeText(value, limit = 12000) {
  return String(value || "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, limit);
}

/**
 * Compute a stable page digest from an observation-like object or live DOM values.
 *
 * Format: url|title|textLength|controlsCount|scrollY
 * (scrollY is included for observability but action-safety revalidation
 *  should prefer URL+title+textLength when scroll is expected to change.)
 *
 * @param {object} obs
 * @param {string} [obs.url]
 * @param {string} [obs.title]
 * @param {string|number} [obs.text] - raw or already-normalized text, or length
 * @param {number} [obs.textLength]
 * @param {number|Array} [obs.controls]
 * @param {number} [obs.scrollY]
 */
export function computePageDigest(obs = {}) {
  const url = String(obs.url || "");
  const title = String(obs.title || "");
  let textLength = 0;
  if (typeof obs.textLength === "number") {
    textLength = obs.textLength;
  } else if (typeof obs.text === "number") {
    textLength = obs.text;
  } else {
    textLength = normalizeText(obs.text || "", 12000).length;
  }
  let controlsCount = 0;
  if (typeof obs.controls === "number") {
    controlsCount = obs.controls;
  } else if (Array.isArray(obs.controls)) {
    controlsCount = obs.controls.length;
  }
  const scrollY = Math.round(Number(obs.scrollY) || 0);
  return `${url}|${title}|${textLength}|${controlsCount}|${scrollY}`;
}

/**
 * Lightweight digest used only for action-safety revalidation
 * (ignores scroll position so pure scroll does not invalidate the target).
 */
export function computeStableDigest(obs = {}) {
  const url = String(obs.url || "");
  const title = String(obs.title || "");
  let textLength = 0;
  if (typeof obs.textLength === "number") {
    textLength = obs.textLength;
  } else if (typeof obs.text === "number") {
    textLength = obs.text;
  } else {
    textLength = normalizeText(obs.text || "", 12000).length;
  }
  return `${url}|${title}|${textLength}`;
}

/**
 * Normalize a URL for comparison (strip trailing slash and hash).
 */
export function normalizeUrlForCompare(url) {
  try {
    const u = new URL(String(url || ""));
    u.hash = "";
    let path = u.pathname;
    if (path.length > 1 && path.endsWith("/")) path = path.slice(0, -1);
    u.pathname = path;
    return u.toString();
  } catch {
    return String(url || "");
  }
}
