from typing import Any, Dict, List, Union
import re


_URL_RE = re.compile(r"https?://[^\s)>\]\"']+")
_PATH_RE = re.compile(r"(?:/tmp/aether/sandbox/[^\s)>\]\"']+|/[A-Za-z0-9_./-]+)")


def _extract_references(text: str) -> List[str]:
    refs: List[str] = []
    for m in _URL_RE.finditer(text):
        ref = m.group(0).rstrip(".,;!?)")
        if ref not in refs:
            refs.append(ref)
    for m in _PATH_RE.finditer(text):
        ref = m.group(0)
        if ref not in refs:
            refs.append(ref)
    return refs


def _truncate_with_references(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text

    refs = _extract_references(text)
    truncated = text[:max_chars].rstrip()
    note = ""
    if refs:
        note = " [references: " + ", ".join(refs[:8]) + "]"
    elif len(text) > max_chars:
        note = "...[truncated]"
    return truncated + note


def compress_context(
    results: List[Union[Dict[str, Any], str]],
    max_total_chars: int = 5000,
    max_per_result: int = 1200,
) -> str:
    """
    Build a "Previous Results" context string from a task's accumulated
    step results, keeping it bounded on two axes:

    - max_per_result: caps any single step's output so one bulky tool
      result (e.g. a large page of search results) can't dominate the
      whole context.  When a result is truncated, any URLs, file paths,
      or sandbox paths it contained are preserved as references so the
      agent can re-fetch the source if needed.
    - max_total_chars: caps the combined string. If capping individual
      results still isn't enough, older entries are dropped first (most
      recent steps are usually most relevant to what happens next),
      replaced with a short note so it's clear something was omitted.
    """
    if not results:
        return ""

    formatted: List[str] = []
    for r in results:
        if isinstance(r, dict):
            step = r.get("step", "")
            output = str(r.get("model_output") or r.get("output", ""))
        else:
            step = ""
            output = str(r)

        output = _truncate_with_references(output, max_per_result)
        entry = f"  - {step}: {output}" if step else f"  - {output}"
        formatted.append(entry)

    header = "Previous Results:"
    body = "\n".join(formatted)
    full_text = f"{header}\n{body}"

    if len(full_text) <= max_total_chars:
        return full_text

    dropped = 0
    kept = list(formatted)
    while kept and len(f"{header}\n" + "\n".join(kept)) > max_total_chars:
        kept.pop(0)
        dropped += 1

    note = f"  - [{dropped} earlier step(s) omitted for length]" if dropped else ""
    parts = [header]
    if note:
        parts.append(note)
    parts.append("\n".join(kept))
    return "\n".join(p for p in parts if p)