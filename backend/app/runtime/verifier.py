"""Independent completion verifier v2 (rubric-armed).

Runs on a fresh context with no tools. Checks the *goal outcome* against the
evidence ledger, now augmented with per-deliverable-type acceptance rubrics —
the same rigor the del-* eval graders apply, enforced inside the runtime.
"""
from __future__ import annotations

import json
import re
from typing import Any

import structlog

from app.runtime.models import complete as model_complete

logger = structlog.get_logger("onsite48.runtime.verifier")

MAX_FINISH_ATTEMPTS = 2

# Terminal finish statuses that the verifier respects without re-evaluation.
TERMINAL_STATES = frozenset(
    ("completed", "completed_unverified", "partial", "needs_input", "waiting_approval", "failed", "cancelled")
)


def user_facing_message(status: str, summary: str) -> str:
    """Format a summary for user-facing output based on finish status."""
    if status == "failed":
        return f"[Failed] {summary}" if summary else "[Failed] Task failed"
    if status == "cancelled":
        return f"[Cancelled] {summary}" if summary else "[Cancelled] Task cancelled"
    if status == "waiting_approval":
        return f"[Waiting for approval] {summary}" if summary else "[Waiting for approval]"
    if status == "needs_input":
        return f"[Needs input] {summary}" if summary else "[Needs input] Awaiting user response"
    # completed, completed_unverified, partial: return summary as-is
    return summary or "Task completed"

# ---------------------------------------------------------------------------
# Per-deliverable-type rubrics. classify_deliverable() picks one from the
# goal; its checklist is injected into the verifier's prompt so the verdict
# is judged against concrete acceptance criteria, not vibes.
# ---------------------------------------------------------------------------
RUBRICS: dict[str, dict[str, Any]] = {
    "research": {
        "signals": ("research", "compare", "list the top", "find ", "who are", "market", "vendors", "options"),
        "rubric": (
            "RESEARCH RUBRIC:\n"
            "- Every load-bearing claim carries a source (URL or file path) in the evidence.\n"
            "- Load-bearing facts are corroborated by >=2 independent sources or flagged as single-source.\n"
            "- The answer addresses sub-questions implied by the goal; partial coverage is acceptable if findings are actionable — note which sub-questions remain unanswered.\n"
            "- No fabricated citations: each cited source appears in the ledger.\n"
            "- Conflicting sources are reported, not silently averaged."
        ),
    },
    "document": {
        "signals": ("report", "document", "docx", "pdf", "write up", "memo", "brief", "proposal"),
        "rubric": (
            "DOCUMENT RUBRIC:\n"
            "- The deliverable file exists in the artifact list and is non-trivial in size.\n"
            "- Structure matches the request (sections requested are present — check names).\n"
            "- Every quantitative claim traces to a tool result in the ledger.\n"
            "- It answers the question asked; a well-written document about the wrong topic is a fail."
        ),
    },
    "presentation": {
        "signals": ("deck", "slides", "pptx", "presentation", "pitch"),
        "rubric": (
            "PRESENTATION RUBRIC:\n"
            "- Slide count is plausible for the ask (not 1 slide for a full pitch, not 60 for a 5-slide ask).\n"
            "- Slide titles assert conclusions rather than label topics.\n"
            "- No overflowing text placeholders, no lorem ipsum, no unresolved TODO markers."
        ),
    },
    "spreadsheet": {
        "signals": ("spreadsheet", "xlsx", "excel", "csv", "table of", "model"),
        "rubric": (
            "SPREADSHEET RUBRIC:\n"
            "- File exists; headers present on every populated sheet.\n"
            "- Row/column counts match the source extraction described in the ledger.\n"
            "- At least one aggregate figure was recomputed and matches the sheet."
        ),
    },
    "website": {
        "signals": ("website", "landing page", "site", "web app", "page"),
        "rubric": (
            "WEBSITE RUBRIC:\n"
            "- Build/test evidence exists and is green (or issues are fixed after preview).\n"
            "- Packaged source artifact exists; preview was used and reported issues were addressed.\n"
            "- No publish side-effect unless explicitly requested (publishing without request = fail).\n"
            "- Content matches the brief: no placeholder sections left."
        ),
    },
    "browser_workflow": {
        "signals": ("log in", "fill the form", "book", "schedule", "submit", "download from", "on the site"),
        "rubric": (
            "BROWSER WORKFLOW RUBRIC:\n"
            "- The final state was confirmed by observation AFTER the last mutation.\n"
            "- No credential invention: any login/CAPTCHA hit resulted in takeover/approval, not retries.\n"
            "- Read-backs or confirmations corroborate the claimed end state."
        ),
    },
    "communication": {
        "signals": ("email", "draft", "message", "slack", "notify", "send"),
        "rubric": (
            "COMMUNICATION RUBRIC:\n"
            "- Drafts contain the requested content elements (recipient context, subject, body points).\n"
            "- Sends happened only with approval evidence in the ledger.\n"
            "- The summary quotes or enumerates what was actually sent/drafted."
        ),
    },
}

FALLBACK_RUBRIC = (
    "GENERAL RUBRIC:\n"
    "- The finish summary directly answers the user's request as worded.\n"
    "- At least one piece of corroborating evidence supports the central claim.\n"
    "- No contradictions between the summary and the tool results in the ledger."
)


def classify_deliverable(goal: str) -> tuple[str, str]:
    """Return (deliverable_type, rubric_text) for the goal."""
    g = (goal or "").lower()
    best, best_hits = None, 0
    for dtype, spec in RUBRICS.items():
        hits = sum(1 for s in spec["signals"] if s in g)
        if hits > best_hits:
            best, best_hits = dtype, hits
    if best is None:
        return "general", FALLBACK_RUBRIC
    return best, RUBRICS[best]["rubric"]


VERIFIER_SYSTEM = """You are an independent outcome verifier for an autonomous agent.
You receive: the original user goal, a deliverable-type acceptance rubric, the
agent's finish summary, and an evidence ledger (read-backs, artifacts, key tool
results). You have NO tools and must not invent evidence.

Respond with a single JSON object only, no markdown:
{
  "verdict": "pass" | "fail" | "unverifiable",
  "unmet_requirements": ["..."],
  "missing_evidence": ["..."],
  "rubric_checks": [{"criterion": "...", "met": true|false, "evidence": "..."}],
  "notes": "short explanation"
}

Rules:
- Judge the goal against the rubric criteria first; verdict follows from rubric_checks.
- pass: every rubric criterion is met with corroborating evidence.
- fail: any rubric criterion is demonstrably unmet, or evidence contradicts the claim.
- unverifiable: evidence is insufficient to decide; list exactly what is missing.
- Check the goal, not whether a plan was followed.
- A mutation without a successful read-back is unverified.
- Do not trust the finish summary alone; require corroborating evidence.
"""


def _build_evidence_ledger(
    *,
    goal: str,
    finish_summary: str,
    finish_status: str,
    journal_events: list[dict[str, Any]] | None = None,
    readbacks: list[dict[str, Any]] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    key_results: list[dict[str, Any]] | None = None,
) -> str:
    dtype, rubric = classify_deliverable(goal)
    parts = [
        f"GOAL:\n{goal}",
        f"DELIVERABLE_TYPE: {dtype}",
        f"ACCEPTANCE RUBRIC:\n{rubric}",
        f"FINISH_STATUS: {finish_status}",
        f"FINISH_SUMMARY:\n{finish_summary}",
    ]
    if readbacks:
        parts.append("READBACKS:\n" + json.dumps(readbacks, default=str, ensure_ascii=False)[:12000])
    if artifacts:
        parts.append("ARTIFACTS:\n" + json.dumps(artifacts, default=str, ensure_ascii=False)[:8000])
    if key_results:
        parts.append("KEY_TOOL_RESULTS:\n" + json.dumps(key_results, default=str, ensure_ascii=False)[:12000])
    if journal_events:
        interesting = [
            e for e in journal_events
            if (e.get("type") or e.get("event_type")) in (
                "tool_call_result", "readback_result", "run_finished",
                "approval_requested", "approval_resolved", "run_failed",
            )
        ][-40:]
        parts.append("JOURNAL_EXCERPT:\n" + json.dumps(interesting, default=str, ensure_ascii=False)[:16000])
    return "\n\n".join(parts)


def _parse_verdict(text: str | None) -> dict[str, Any]:
    if not text:
        return {
            "verdict": "unverifiable",
            "unmet_requirements": [],
            "missing_evidence": ["empty verifier response"],
            "rubric_checks": [],
            "notes": "verifier returned no content",
        }
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    obj: Any = None
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", cleaned)
        if m:
            try:
                obj = json.loads(m.group(0))
            except json.JSONDecodeError:
                obj = None
    if not isinstance(obj, dict):
        return {
            "verdict": "unverifiable",
            "unmet_requirements": [],
            "missing_evidence": ["verifier response not valid JSON"],
            "rubric_checks": [],
            "notes": cleaned[:500],
        }
    verdict = str(obj.get("verdict") or "unverifiable").lower()
    if verdict not in ("pass", "fail", "unverifiable"):
        verdict = "unverifiable"
    checks = obj.get("rubric_checks")
    return {
        "verdict": verdict,
        "unmet_requirements": list(obj.get("unmet_requirements") or []),
        "missing_evidence": list(obj.get("missing_evidence") or []),
        "rubric_checks": checks if isinstance(checks, list) else [],
        "notes": str(obj.get("notes") or "")[:2000],
    }


async def verify_completion(
    *,
    goal: str,
    finish_summary: str,
    finish_status: str = "completed",
    journal_events: list[dict[str, Any]] | None = None,
    readbacks: list[dict[str, Any]] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    key_results: list[dict[str, Any]] | None = None,
    model_override: str | None = None,
) -> dict[str, Any]:
    """Run the independent verifier. Returns strict JSON verdict dict."""
    ledger = _build_evidence_ledger(
        goal=goal,
        finish_summary=finish_summary,
        finish_status=finish_status,
        journal_events=journal_events,
        readbacks=readbacks,
        artifacts=artifacts,
        key_results=key_results,
    )
    messages = [
        {"role": "system", "content": VERIFIER_SYSTEM},
        {"role": "user", "content": ledger},
    ]
    try:
        result = await model_complete(
            messages,
            tools=[],
            role="verifier",
            tool_choice="none",
            temperature=0.0,
            max_tokens=1536,
            model_override=model_override,
        )
        parsed = _parse_verdict(result.content)
        parsed["model"] = result.model
        parsed["cost_usd"] = result.cost_usd
        logger.info(
            "runtime.verifier.done",
            verdict=parsed["verdict"],
            unmet=len(parsed["unmet_requirements"]),
            missing=len(parsed["missing_evidence"]),
            rubric_checks=len(parsed["rubric_checks"]),
        )
        return parsed
    except Exception as exc:
        logger.warning("runtime.verifier.failed", error=str(exc))
        return {
            "verdict": "unverifiable",
            "unmet_requirements": [],
            "missing_evidence": [f"verifier exception: {exc}"],
            "rubric_checks": [],
            "notes": "verifier lane failed",
        }


def apply_verdict_to_terminal(
    finish_status: str,
    verdict: dict[str, Any],
    finish_summary: str,
    finish_attempts: int = 1,
) -> dict[str, Any]:
    """Map verifier verdict + agent finish into an honest terminal payload."""
    v = verdict.get("verdict") or "unverifiable"
    unmet = list(verdict.get("unmet_requirements") or [])
    missing = list(verdict.get("missing_evidence") or [])

    if finish_status in ("needs_input", "needs_user", "waiting_approval", "cancelled", "failed"):
        status = {
            "needs_user": "needs_input",
            "needs_input": "needs_input",
            "waiting_approval": "waiting_approval",
            "cancelled": "cancelled",
            "failed": "failed",
        }.get(finish_status, finish_status)
        return {
            "status": status,
            "summary": finish_summary,
            "verifier": verdict,
            "unmet_requirements": unmet,
            "missing_evidence": missing,
        }

    if v == "pass":
        return {
            "status": "completed",
            "summary": finish_summary,
            "verifier": verdict,
            "unmet_requirements": [],
            "missing_evidence": [],
        }

    if v == "fail":
        if finish_attempts < MAX_FINISH_ATTEMPTS:
            failed_checks = [
                c.get("criterion", "?")
                for c in (verdict.get("rubric_checks") or [])
                if isinstance(c, dict) and c.get("met") is False
            ]
            hint = "; ".join(failed_checks) if failed_checks else "; ".join(unmet) or "see evidence"
            return {
                "status": "_retry_finish",
                "summary": finish_summary,
                "verifier": verdict,
                "unmet_requirements": unmet or failed_checks,
                "missing_evidence": missing,
                "retry_hint": (
                    "The independent verifier failed this completion. "
                    f"Unmet rubric criteria: {hint}. "
                    "Close these gaps with tool evidence, then finish again."
                ),
            }
        return {
            "status": "failed",
            "summary": (
                f"{finish_summary}\n\n[Verifier] Goal not met. "
                f"Unmet: {'; '.join(unmet) or 'see evidence'}."
            ),
            "verifier": verdict,
            "unmet_requirements": unmet,
            "missing_evidence": missing,
        }

    note = (
        f"{finish_summary}\n\n[Verifier] Could not fully verify outcome. "
        f"Missing evidence: {'; '.join(missing) or 'insufficient ledger'}."
    )
    return {
        "status": "completed_unverified",
        "summary": note,
        "verifier": verdict,
        "unmet_requirements": unmet,
        "missing_evidence": missing,
    }