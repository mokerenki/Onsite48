"""Low-latency response routing for safe, self-contained Synth requests.

This module deliberately does *not* replace the durable autonomous workflow.
It identifies narrow low-risk requests that can be answered through one model
call, or one public web search followed by one model call. Requests involving
business decisions, actions, sensitive domains, attachments, or multi-step work
remain in the full planner/executor/verifier path.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, AsyncGenerator, Literal

if TYPE_CHECKING:
    from app.core.model_router_kimi_deepseek import KimiDeepSeekRouter
    from app.tools.registry import ToolRegistry

_GMAIL_INBOX_PATTERN = _GMAIL_DRAFT_PATTERN = _GMAIL_SEND_PATTERN = None  # type: ignore
from app.core.content_safety import annotate_external_content

FastMode = Literal["quick", "web_research", "deterministic"]


@dataclass(frozen=True)
class FastRouteDecision:
    mode: FastMode
    label: str
    reason: str


HIGH_ASSURANCE_TERMS = re.compile(
    r"\b("
    r"send|publish|post|delete|remove|pay|purchase|buy|sell|transfer|"
    r"change|update|edit|create\s+(?:an?\s+)?(?:campaign|ad|invoice|record)|"
    r"log\s*in|sign\s*in|dashboard|crm|hubspot|salesforce|meta\s*ads|"
    r"google\s*ads|analytics|linkedin|shopify|slack|email|calendar|"
    r"research|analyse|analyze|compare|competitor|market\s+analysis|strategy|"
    r"recommend|recommendation|decision|advice|assess|evaluate|audit|risk|impact|"
    r"forecast|budget|revenue|financial|investment|legal|contract|compliance|"
    r"medical|diagnos|tax|insurance|hire|fire|security|password|credential|"
    r"build|deploy|code|website|presentation|slide|report|spreadsheet|"
    r"plan\s+(?:a|an|the|my)|step.by.step|workflow|automate"
    r")\b",
    re.IGNORECASE,
)

CURRENT_INFORMATION_TERMS = re.compile(
    r"\b(latest|current|today|yesterday|tomorrow|news|recent|live|"
    r"price|weather|score|exchange\s+rate|stock|ceo|president|who\s+is|"
    r"when\s+did|where\s+is)\b",
    re.IGNORECASE,
)

QUICK_WRITING_TERMS = re.compile(
    r"\b(write|draft|rewrite|rephrase|summarize|summary|explain|define|"
    r"brainstorm|outline|bullet|list|translate|paraphrase|improve|"
    r"proofread|edit\s+this|make\s+this|help\s+me\s+word)\b",
    re.IGNORECASE,
)

DIAGRAM_REQUEST_PATTERN = re.compile(
    r"\b(diagram|flowchart|mind\s*map|ascii\s+art)\b", re.IGNORECASE
)

DETERMINISTIC_UTILITY_TERMS = re.compile(
    r"\b(what\s+time|current\s+time|time\s+is\s+it|utc\s+time|timezone)\b",
    re.IGNORECASE,
)

LOCAL_BROWSER_TERMS = re.compile(
    r"\b(my\s+browser|open\s+tab|browser\s+tab|local\s+browser)\b", re.IGNORECASE
)

PUBLIC_SITE_OR_COMPARISON_TERMS = re.compile(
    r"\b(https?://|www\.|compare|versus|\bvs\.?\b|website|site)\b", re.IGNORECASE
)

COMPOUND_OR_NAMED_ENTITY_TERMS = re.compile(
    r"\b(and\s+then|after\s+that|multi-?step|workflow)\b", re.IGNORECASE
)

MAX_FAST_CHARACTERS = 520
MAX_FAST_WORDS = 90


def select_fast_route(
    message: str,
    *,
    has_attachments: bool = False,
    is_follow_up: bool = False,
    depth: str | None = None,
) -> FastRouteDecision | None:
    """Return a safe low-latency mode, or ``None`` for full autonomy.

    Follow-ups and attachments stay in the durable workflow so that prior
    conversation context and uploaded evidence are never silently ignored.
    Explicit deep/standard research depth always takes the durable path.
    """
    if depth in ("deep", "standard"):
        return None
    text = message or ""
    for _pat in (_GMAIL_INBOX_PATTERN, _GMAIL_DRAFT_PATTERN, _GMAIL_SEND_PATTERN):
        if _pat is not None and _pat.search(text):
            return None
    normalized = " ".join(message.split())
    if not normalized or has_attachments or is_follow_up:
        return None
    if len(normalized) > MAX_FAST_CHARACTERS or len(normalized.split()) > MAX_FAST_WORDS:
        return None
    if DETERMINISTIC_UTILITY_TERMS.search(normalized) and not re.search(
        r"\b(?:and|also|then)\b", normalized, re.IGNORECASE
    ):
        return FastRouteDecision(
            mode="deterministic",
            label="Instant answer",
            reason="safe local deterministic utility request",
        )
    if (
        DETERMINISTIC_UTILITY_TERMS.search(normalized)
        or LOCAL_BROWSER_TERMS.search(normalized)
        or PUBLIC_SITE_OR_COMPARISON_TERMS.search(normalized)
        or COMPOUND_OR_NAMED_ENTITY_TERMS.search(normalized)
        or HIGH_ASSURANCE_TERMS.search(normalized)
        or CURRENT_INFORMATION_TERMS.search(normalized)
    ):
        return None
    if QUICK_WRITING_TERMS.search(normalized) or DIAGRAM_REQUEST_PATTERN.search(normalized):
        return FastRouteDecision(
            mode="quick",
            label="Quick answer",
            reason="short, self-contained low-risk request",
        )
    return None


def _quick_system_prompt() -> str:
    return (
        "You are Synth AI. Give a useful, direct answer to this low-risk request.\n"
        "Do not claim to have searched the web, accessed private systems, or "
        "taken external actions. Keep the answer concise."
    )


async def stream_fast_answer(
    message: str,
    *,
    decision: FastRouteDecision,
    model_router: Any = None,
    tool_registry: Any = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Stream a low-latency answer for a previously accepted fast route."""
    messages = [
        {"role": "system", "content": _quick_system_prompt()},
        {"role": "user", "content": message},
    ]

    if decision.mode == "deterministic":
        if tool_registry is None:
            yield {
                "type": "task_error",
                "error_code": "deterministic_tool_missing",
                "message": "The local utility path is unavailable.",
            }
            return
        tool = None
        for name in ("current_time", "CurrentTimeTool"):
            try:
                tool = tool_registry.get(name)
            except Exception:
                tool = None
            if tool is not None:
                break
        if tool is None:
            yield {
                "type": "task_error",
                "error_code": "deterministic_tool_missing",
                "message": "No local current-time utility is registered.",
            }
            return
        try:
            hint_match = re.search(r"timezone[=:\s]+([A-Za-z/_]+)", message, re.I)
            result = await tool.execute(
                timezone=hint_match.group(1).strip() if hint_match else None
            )
        except Exception as exc:
            yield {
                "type": "task_error",
                "error_code": "deterministic_tool_failed",
                "message": "The local current-time utility could not complete the request.",
                "error": str(exc),
            }
            return
        answer = str(result.get("output", "")) if isinstance(result, dict) else str(result)
        if not answer:
            yield {
                "type": "task_error",
                "error_code": "deterministic_tool_empty",
                "message": "The local utility returned no answer.",
            }
            return
        yield {"type": "answer_delta", "content": answer, "transient": True}
        yield {
            "type": "task_complete",
            "status": "completed",
            "final_answer": answer,
            "response_mode": decision.mode,
            "verification": {
                "status": "NOT_REQUIRED",
                "score": None,
                "feedback": "Deterministic local utility; no external action was taken.",
            },
        }
        return

    if model_router is None:
        yield {
            "type": "task_error",
            "error_code": "fast_answer_unavailable",
            "message": "Quick-answer model path is unavailable.",
        }
        return

    chunks: list[str] = []
    try:
        async for delta in model_router.stream("fallback", messages, max_tokens=900):
            if not delta:
                continue
            chunks.append(delta)
            yield {"type": "answer_delta", "content": delta, "transient": True}
    except Exception as exc:
        yield {
            "type": "task_error",
            "error_code": "fast_answer_failed",
            "message": "Synth could not complete the quick response. Please retry or run a full research task.",
            "error": str(exc),
        }
        return

    answer = "".join(chunks).strip()
    if not answer:
        yield {
            "type": "task_error",
            "error_code": "fast_answer_empty",
            "message": "Synth received no usable quick-answer content. Please retry the request.",
        }
        return

    yield {
        "type": "task_complete",
        "status": "completed",
        "final_answer": answer,
        "response_mode": decision.mode,
        "verification": {
            "status": "NOT_REQUIRED",
            "score": None,
            "feedback": "Low-risk quick-answer path; no external action was taken.",
        },
    }


__all__ = ["FastRouteDecision", "select_fast_route", "stream_fast_answer"]
