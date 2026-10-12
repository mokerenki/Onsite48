// Observation helpers — used by injected page functions and tests.
// Prefer computePageDigest from digest.js for any new code paths.

const clip = (value, limit = 2000) => String(value || "").replace(/\s+/g, " ").trim().slice(0, limit);

export function observePage(root = document) {
  const body = root.body;
  const media = [...root.querySelectorAll("video,audio")].slice(0, 8).map((item) => ({
    tag: item.tagName.toLowerCase(),
    paused: Boolean(item.paused),
    currentTime: Number(item.currentTime || 0),
    duration: Number(item.duration || 0),
    src: clip(item.currentSrc || item.src, 240),
  }));
  const controls = [...root.querySelectorAll("button,a,input,textarea,select,[role=button]")]
    .slice(0, 80)
    .map((item) => ({
      tag: item.tagName.toLowerCase(),
      role: item.getAttribute("role"),
      text: clip(item.innerText || item.getAttribute("aria-label") || item.getAttribute("placeholder"), 180),
      disabled: Boolean(item.disabled || item.getAttribute("aria-disabled") === "true"),
    }));
  const text = clip(body?.innerText, 12000);
  const result = {
    url: location.href,
    origin: location.origin,
    title: document.title,
    text,
    loading: document.readyState !== "complete",
    dialog: Boolean(root.querySelector("[role=dialog],dialog")),
    media,
    controls,
    scrollY: Math.round(window.scrollY),
    scrollHeight: document.documentElement.scrollHeight,
  };
  // Consistent digest format: url|title|textLength|controlsCount|scrollY
  result.page_digest = `${result.url}|${result.title}|${result.text.length}|${result.controls.length}|${result.scrollY}`;
  return result;
}

function mediaElement(root = document) {
  const item = root.querySelector("video,audio");
  if (!item) throw new Error("No HTML5 media element is present on this page.");
  return item;
}

export function mediaState(root = document) {
  const item = mediaElement(root);
  return {
    paused: Boolean(item.paused),
    currentTime: Number(item.currentTime || 0),
    duration: Number(item.duration || 0),
    tag: item.tagName.toLowerCase(),
  };
}

export async function controlMedia(command, root = document) {
  const item = mediaElement(root);
  const before = mediaState(root);
  const action = String(command || "pause");
  if (action === "pause") await item.pause();
  else if (action === "play") await item.play();
  else if (action === "toggle") {
    if (item.paused) await item.play();
    else item.pause();
  } else throw new Error(`Unsupported media action: ${action}`);
  await new Promise((resolve) => setTimeout(resolve, 60));
  const after = mediaState(root);
  const expectedPaused = action === "pause" || (action === "toggle" && !before.paused);
  return { before, after, verified: expectedPaused === after.paused };
}

export function revalidateTarget(expected = {}, root = document) {
  if (expected.url) {
    const normalize = (u) => {
      try {
        const parsed = new URL(String(u));
        parsed.hash = "";
        let path = parsed.pathname;
        if (path.length > 1 && path.endsWith("/")) path = path.slice(0, -1);
        parsed.pathname = path;
        return parsed.toString();
      } catch {
        return String(u || "");
      }
    };
    if (normalize(location.href) !== normalize(expected.url)) {
      throw new Error("The browser target URL changed before the action ran.");
    }
  }
  if (expected.page_digest) {
    // Stable digest ignores scrollY so pure scroll does not invalidate
    const textLen = (root.body?.innerText || "").replace(/\s+/g, " ").trim().length;
    const stable = `${location.href}|${document.title}|${textLen}`;
    const expectedStable = String(expected.page_digest).split("|").slice(0, 3).join("|");
    if (stable !== expectedStable) {
      throw new Error("The browser page changed before the action ran.");
    }
  }
  return true;
}
