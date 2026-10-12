"""Task-scoped execution evidence recording and replay.

Records are append-only JSONL files so a failed task can be inspected after the
WebSocket disconnects or the process restarts. Sensitive command arguments and
large non-image payloads are redacted before persistence.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SENSITIVE = re.compile(r"password|token|secret|authorization|cookie|api[_-]?key|credential", re.I)


def _safe(value: Any, *, key: str = "") -> Any:
    if _SENSITIVE.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _safe(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_safe(v, key=key) for v in value[:100]]
    if isinstance(value, str):
        if value.startswith("data:image/") and "," in value:
            header, encoded = value.split(",", 1)
            try:
                raw = base64.b64decode(encoded, validate=False)
                return {"content_type": header, "sha256": hashlib.sha256(raw).hexdigest(), "base64": encoded}
            except Exception:
                return {"content_type": header, "sha256": hashlib.sha256(value.encode()).hexdigest()}
        return value[:8000]
    return value


class EvalViewer:
    def __init__(self, root: str | None = None) -> None:
        self.root = Path(root or os.getenv("EVAL_VIEWER_STORAGE", os.path.join(tempfile.gettempdir(), "onsite48", "eval")))

    def record(self, thread_id: str, event: dict[str, Any]) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "thread_id": thread_id,
            "event": _safe(event),
        }
        path = self.root / f"{thread_id}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        return payload

    def replay(self, thread_id: str, *, failed_only: bool = False) -> list[dict[str, Any]]:
        path = self.root / f"{thread_id}.jsonl"
        if not path.exists():
            return []
        records: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            event = record.get("event", {})
            if failed_only and not (event.get("failure") or event.get("task_error") or event.get("type") in {"task_error", "tool_error"}):
                continue
            records.append(record)
        return records


_eval_viewer = EvalViewer()


def get_eval_viewer() -> EvalViewer:
    return _eval_viewer