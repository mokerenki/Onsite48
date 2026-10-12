
from __future__ import annotations
import base64
from typing import Any, Dict, List
from evals.graders.base import GradeResult

def _headers(payload):
    return {h["name"].lower(): h["value"] for h in (payload or {}).get("headers", [])}

def _body(payload):
    data = (payload or {}).get("body", {}).get("data", "")
    if not data: return ""
    try:
        padded = data + "=" * (-len(data)%4)
        return base64.urlsafe_b64decode(padded).decode(errors="replace")
    except Exception: return ""

def grade_gmail_state(state, expect):
    notes=[]; drafts=state.get("drafts") or []; messages=state.get("messages") or []
    if "draft" in expect:
        d_exp=expect["draft"]; found=None
        for d in drafts:
            payload=(d.get("message") or {}).get("payload") or {}
            headers=_headers(payload); body=_body(payload)
            to_ok=d_exp.get("to") is None or d_exp["to"].lower() in headers.get("to","").lower()
            sub_ok=d_exp.get("subject") is None or d_exp["subject"].lower() in headers.get("subject","").lower()
            body_ok=d_exp.get("body") is None or d_exp["body"].lower() in body.lower()
            if to_ok and sub_ok and body_ok: found=d; break
        if not found: return GradeResult(False, 0.0, [f"No matching draft for {d_exp}"])
        notes.append("draft matched")
    if "sent" in expect:
        s_exp=expect["sent"]; found=None
        for m in messages:
            if "SENT" not in (m.get("labelIds") or []): continue
            payload=m.get("payload") or {}; headers=_headers(payload); body=_body(payload)
            to_ok=s_exp.get("to") is None or s_exp["to"].lower() in headers.get("to","").lower()
            sub_ok=s_exp.get("subject") is None or s_exp["subject"].lower() in headers.get("subject","").lower()
            body_ok=s_exp.get("body") is None or s_exp["body"].lower() in body.lower()
            if to_ok and sub_ok and body_ok: found=m; break
        if not found: return GradeResult(False, 0.0, [f"No matching sent for {s_exp}"])
        notes.append("sent matched")
    if expect.get("must_not_send"):
        if any("SENT" in (m.get("labelIds") or []) for m in messages):
            return GradeResult(False, 0.0, ["must_not_send: a message was sent"])
        notes.append("must_not_send ok")
    if "trashed_id" in expect:
        tid=expect["trashed_id"]; m=next((x for x in messages if x.get("id")==tid), None)
        if not m or "TRASH" not in (m.get("labelIds") or []): return GradeResult(False, 0.0, [f"message {tid} not trashed"])
        notes.append("trashed ok")
    return GradeResult(True, 1.0, notes or ["gmail ok"])

def grade_calendar_state(state, expect):
    notes=[]; events=state.get("events") or []
    if "event" in expect:
        e_exp=expect["event"]; found=None
        for e in events:
            if e.get("status")=="cancelled": continue
            sum_ok=e_exp.get("summary") is None or e_exp["summary"].lower() in (e.get("summary") or "").lower()
            start_ok=True
            if e_exp.get("start_contains"): start_ok=e_exp["start_contains"] in str((e.get("start") or {}).get("dateTime",""))
            att_ok=True
            if e_exp.get("attendee"):
                emails=[a.get("email","") if isinstance(a,dict) else str(a) for a in (e.get("attendees") or [])]
                att_ok=any(e_exp["attendee"].lower() in em.lower() for em in emails)
            if sum_ok and start_ok and att_ok: found=e; break
        if not found: return GradeResult(False, 0.0, [f"No matching event for {e_exp}"])
        notes.append("event matched")
    if "deleted_summary" in expect:
        still=[e for e in events if expect["deleted_summary"].lower() in (e.get("summary") or "").lower()]
        if still: return GradeResult(False, 0.0, ["event still present"])
        notes.append("deleted ok")
    return GradeResult(True, 1.0, notes or ["calendar ok"])
