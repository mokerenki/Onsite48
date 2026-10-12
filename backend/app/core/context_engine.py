"""Context engineering for long-running agent tasks.

Context is the binding constraint on agent quality, and the previous approach
treated it as a formatting problem: clip the transcript, cap each result, drop
the oldest entries. That is lossy in the worst way, because what gets discarded
is exactly the material that makes a follow-up answerable — the decision the
agent made four turns ago, the identifier the user is now referring to, the
artifact that was produced.

Three mechanisms replace that clipping:

1. A stable, cacheable prompt prefix. Everything invariant about how the agent
   behaves (identity, safety rules, execution policy) is rendered once in a fixed
   byte sequence, so a provider-side prompt cache can reuse it across turns. Only
   volatile material (goal, history, results) follows the prefix. A fingerprint
   lets an operator verify that the prefix did not drift between turns.

2. A task ledger. Instead of re-deriving state from a truncated transcript, the
   ledger carries the goal, the steps completed and pending, the identifiers the
   agent has seen, the artifacts it produced, and the questions it is waiting on.
   It is derived deterministically from plan and results, and it survives
   summarization because it is not summarization.

3. Result handles. A large tool result is replaced in the prompt by a compact
   handle: where the full payload lives, how many rows it has, what its keys are,
   and a short preview. The agent can re-expand a handle on demand instead of
   being forced to choose between a truncated blob and the whole payload.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, List, Optional, Sequence

MAX_HANDLE_ROWS = 200
MAX_PREVIEW_ROWS = 3
MAX_LABEL_CHARS = 160
_LIST_KEYS = ("messages", "drafts", "events", "issues", "repositories", "results", "items", "rows")
_LABEL_KEYS = ("subject", "title", "name", "summary", "full_name", "key", "id", "email", "label")


def fingerprint(prefix: str) -> str:
    """Stable identity of a rendered prefix, for cache verification."""
    return hashlib.sha256(prefix.encode("utf-8")).hexdigest()[:16]


# ─── Stable prefix ───────────────────────────────────────────────

IDENTITY_SECTION = (
    "You are Synth, an autonomous AI workforce agent. You plan, execute and "
    "verify real tasks across connected services, browsers and schedules."
)

EXECUTION_POLICY_SECTION = (
    "Execution policy (server-authoritative, never negotiable):\n"
    "- Produce the smallest plan that satisfies the exact request. No optional work.\n"
    "- Never invent a tool name or an argument. Use only the schemas provided.\n"
    "- Never change the requested operation, and never broaden the user's scope.\n"
    "- A required argument is never guessed. Ask the user when it is missing.\n"
    "- An action that deletes, sends, or publishes is shown to the user and "
    "requires explicit approval before it runs.\n"
    "- A destructive action with an ambiguous target is resolved by listing the "
    "candidates, never by choosing one."
)

OUTPUT_CONTRACT_SECTION = (
    "Output contract:\n"
    "- Claims must be supported by a step result present in this context.\n"
    "- If evidence is missing, say what is missing instead of guessing.\n"
    "- Report the concrete outcome (identifiers, counts, paths), not a description "
    "of the process."
)

EVIDENCE_SECTION = (
    "Evidence handling:\n"
    "- A compact [result:ref] handle means the full payload is still available; "
    "re-read it with its reference rather than assuming its contents.\n"
    "- Prefer the task ledger over re-deriving state from the transcript."
)


def render_stable_prefix(
    *,
    tool_catalog_digest: str = "",
    tool_count: int = 0,
    extra_sections: Optional[Sequence[str]] = None,
) -> str:
    """Render the invariant prompt prefix in a fixed order.

    The output is byte-identical for identical inputs so that a provider prompt
    cache can hit across turns. Anything that varies per turn belongs after it.
    """
    sections = [IDENTITY_SECTION, EXECUTION_POLICY_SECTION, OUTPUT_CONTRACT_SECTION, EVIDENCE_SECTION]
    if tool_count:
        sections.append(f"Tool catalog: {int(tool_count)} registered tools (digest {tool_catalog_digest or 'unknown'}).")
    sections.extend(str(section) for section in (extra_sections or []) if section)
    return "\n\n".join(sections)


def tool_catalog_digest(tool_schemas: Iterable[Dict[str, Any]]) -> str:
    """Digest of the tool catalog in a stable order.

    Sorting by name makes the digest independent of registration order, so an
    unrelated registration does not invalidate the cache prefix.
    """
    normalized = sorted(
        (
            {
                "name": str(schema.get("name") or ""),
                "description": str(schema.get("description") or ""),
                "parameters": schema.get("parameters") or {},
            }
            for schema in tool_schemas
            if isinstance(schema, dict)
        ),
        key=lambda item: item["name"],
    )
    return fingerprint(json.dumps(normalized, sort_keys=True, default=str))


# ─── Result handles ──────────────────────────────────────────────


def _row_label(row: Any) -> str:
    if not isinstance(row, dict):
        return _clip(str(row), MAX_LABEL_CHARS)
    for key in _LABEL_KEYS:
        value = row.get(key)
        if value:
            return _clip(str(value), MAX_LABEL_CHARS)
    return _clip(json.dumps(row, sort_keys=True, default=str), MAX_LABEL_CHARS)


def _clip(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def result_handle(
    tool_name: str,
    output: Any,
    *,
    step_id: str = "",
    threshold: int = 8,
) -> Optional[Dict[str, Any]]:
    """Return a compact handle for a large result, or ``None`` when it is small.

    A small result is inlined as-is; passing one through a handle would make the
    agent do a pointless round trip to read data that already fits.
    """
    rows: Optional[List[Any]] = None
    list_key = ""
    if isinstance(output, dict):
        for key in _LIST_KEYS:
            value = output.get(key)
            if isinstance(value, list):
                rows = value
                list_key = key
                break
    if rows is None or len(rows) <= threshold:
        return None
    return {
        "ref": f"{step_id or tool_name}:{list_key}",
        "tool": tool_name,
        "list_key": list_key,
        "row_count": len(rows),
        "keys": sorted({key for row in rows[:20] if isinstance(row, dict) for key in row})[:25],
        "preview": [_row_label(row) for row in rows[:MAX_PREVIEW_ROWS]],
        "truncated": True,
    }


def render_handle(handle: Dict[str, Any]) -> str:
    return (
        f"[result:{handle.get('ref')}] {handle.get('tool')}: {handle.get('row_count')} "
        f"{handle.get('list_key')} available (keys: {', '.join(handle.get('keys') or [])}). "
        f"First rows: {'; '.join(handle.get('preview') or [])}"
    )


def rehydrate(handle: Dict[str, Any], stored: Any) -> Any:
    """Recover a full payload from its handle.

    ``stored`` is normally ``state["tool_data"]``, which retains the complete
    structured output of every executed step.
    """
    ref = str(handle.get("ref") or "")
    step_id = ref.split(":", 1)[0]
    for candidate in (stored, stored.get(step_id) if isinstance(stored, dict) else None):
        if isinstance(candidate, dict) and isinstance(candidate.get(handle.get("list_key")), list):
            return candidate[handle["list_key"]]
    if isinstance(stored, dict):
        for value in stored.values():
            if isinstance(value, dict) and isinstance(value.get(handle.get("list_key")), list):
                return value[handle["list_key"]]
    return None


# ─── Task ledger ─────────────────────────────────────────────────


def build_task_ledger(
    *,
    task: str = "",
    plan: Optional[Sequence[Dict[str, Any]]] = None,
    results: Optional[Sequence[Dict[str, Any]]] = None,
    tool_data: Optional[Dict[str, Any]] = None,
    artifacts: Optional[Sequence[Dict[str, Any]]] = None,
    final_answer: str = "",
    max_labels: int = 12,
) -> Dict[str, Any]:
    """Derive durable task state from execution, deterministically.

    The ledger is the answer to "what does the agent know right now", and it is
    computed from the plan and the accumulated results rather than from a
    conversation summary, so it stays correct no matter how old the task is.
    """
    steps = [step for step in (plan or []) if isinstance(step, dict)]
    completed: List[str] = []
    pending: List[str] = []
    for step in steps:
        step_id = str(step.get("step_id") or "")
        if not step_id:
            continue
        tool_name = step.get("tool_name")
        if step_id in completed:
            continue
        pending.append(step_id)

    entities: List[Dict[str, Any]] = []
    for result in results or []:
        if not isinstance(result, dict):
            continue
        tool = str(result.get("tool") or "")
        if not tool:
            continue
        if result.get("observation") is not None or result.get("error"):
            continue
        output = result.get("output") or result.get("tool_data")
        if not isinstance(output, dict):
            continue
        for key in _LIST_KEYS:
            value = output.get(key)
            if not isinstance(value, list) or not value:
                continue
            entities.append({
                "tool": tool,
                "step": str(result.get("step") or ""),
                "kind": key,
                "count": len(value),
                "labels": [_row_label(row) for row in value[:max_labels]],
                "ref": f"{result.get('step') or tool}:{key}",
            })
            break

    completed_steps = [
        str(result.get("step"))
        for result in (results or [])
        if isinstance(result, dict) and not result.get("error")
    ]
    remaining = [step_id for step_id in pending if step_id not in completed_steps]

    return {
        "goal": _clip(task, 400),
        "plan_step_count": len(steps),
        "completed_steps": completed_steps,
        "remaining_steps": remaining,
        "entities": entities[:10],
        "artifacts": [
            {
                "kind": str(artifact.get("kind") or artifact.get("type") or "artifact"),
                "name": _clip(artifact.get("name") or artifact.get("title") or "", 160),
            }
            for artifact in (artifacts or [])
            if isinstance(artifact, dict)
        ][:10],
        "waiting_on_user": _waiting_on_user(steps, results or []),
        "delivered": bool(final_answer),
    }


def _waiting_on_user(
    steps: Sequence[Dict[str, Any]], results: Sequence[Dict[str, Any]]
) -> List[str]:
    answered = {
        str(result.get("step"))
        for result in results
        if isinstance(result, dict) and not result.get("error")
    }
    questions: List[str] = []
    for step in steps:
        if not isinstance(step, dict) or step.get("tool_name"):
            continue
        if str(step.get("step_id") or "") in answered:
            continue
        args = step.get("tool_args")
        if isinstance(args, dict) and args.get("question"):
            questions.append(_clip(args["question"], 300))
    return questions[:5]


def render_task_ledger(ledger: Dict[str, Any], *, max_chars: int = 1600) -> str:
    """Render the ledger as a compact, prompt-safe block."""
    if not ledger:
        return ""
    lines = [f"Goal: {ledger.get('goal') or '(unspecified)'}"]
    completed = ledger.get("completed_steps") or []
    remaining = ledger.get("remaining_steps") or []
    lines.append(
        f"Steps: {ledger.get('plan_step_count', 0)} planned, "
        f"{len(completed)} completed, {len(remaining)} pending"
    )
    for entity in ledger.get("entities") or []:
        labels = "; ".join(entity.get("labels") or [])
        lines.append(
            f"- {entity.get('tool')} returned {entity.get('count')} {entity.get('kind')} "
            f"[result:{entity.get('ref')}]: {labels}"
        )
    for artifact in ledger.get("artifacts") or []:
        lines.append(f"- artifact: {artifact.get('kind')} {artifact.get('name')}".rstrip())
    for question in ledger.get("waiting_on_user") or []:
        lines.append(f"- awaiting the user: {question}")
    block = "Task ledger (durable state, prefer this over re-reading the transcript):\n" + "\n".join(lines)
    if len(block) > max_chars:
        block = block[: max_chars - 1].rstrip() + "…"
    return block


__all__ = [
    "build_task_ledger",
    "fingerprint",
    "rehydrate",
    "render_handle",
    "render_stable_prefix",
    "render_task_ledger",
    "result_handle",
    "tool_catalog_digest",
]