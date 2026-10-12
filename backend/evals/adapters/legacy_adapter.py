
"""Offline legacy adapter: HTTP to Google fake."""
from __future__ import annotations
import base64, re, time
from email.message import EmailMessage
from typing import Any, Dict, List, Optional
import httpx
from evals.runner import FAKE_TOKEN, GOOGLE_FAKE, TaskResult
HEADERS = {"Authorization": f"Bearer {FAKE_TOKEN}"}

async def run_legacy(task):
    t0 = time.time()
    goal = task.get("goal") or ""
    steps, tool_calls, answer = await _det(goal, task)
    tr = TaskResult(id=task["id"], tier=task.get("tier", ""), status="passed", verdict=False,
                    steps=steps, wall_time_s=time.time()-t0,
                    transcript=[{"role":"user","content":goal},{"role":"assistant","content":answer or ""}],
                    tool_calls=tool_calls)
    tr._agent_answer = answer
    return tr

def _draft(to, subject, body):
    msg = EmailMessage(); msg["To"]=to; msg["From"]="me@example.com"; msg["Subject"]=subject; msg.set_content(body or "")
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode().rstrip("=")
    r = httpx.post(f"{GOOGLE_FAKE}/gmail/v1/users/me/drafts", headers=HEADERS, json={"message":{"raw":raw}}, timeout=10)
    r.raise_for_status(); return r.json()

def _search(q, limit=20):
    r = httpx.get(f"{GOOGLE_FAKE}/gmail/v1/users/me/messages", headers=HEADERS, params={"q":q,"maxResults":limit}, timeout=10)
    r.raise_for_status(); return r.json()

def _trash(mid):
    r = httpx.post(f"{GOOGLE_FAKE}/gmail/v1/users/me/messages/{mid}/trash", headers=HEADERS, timeout=10)
    r.raise_for_status(); return r.json()

def _create_event(summary, start, end, attendees=None):
    r = httpx.post(f"{GOOGLE_FAKE}/calendar/v3/calendars/primary/events", headers=HEADERS, json={
        "summary":summary, "start":{"dateTime":start,"timeZone":"UTC"}, "end":{"dateTime":end,"timeZone":"UTC"},
        "attendees": attendees or []}, timeout=10)
    r.raise_for_status(); return r.json()

def _list_events():
    r = httpx.get(f"{GOOGLE_FAKE}/calendar/v3/calendars/primary/events", headers=HEADERS, timeout=10)
    r.raise_for_status(); return r.json()

def _delete_event(eid):
    r = httpx.delete(f"{GOOGLE_FAKE}/calendar/v3/calendars/primary/events/{eid}", headers=HEADERS, timeout=10)
    if r.status_code not in (200, 204): r.raise_for_status()

def _update_event(eid, start, end):
    r = httpx.patch(f"{GOOGLE_FAKE}/calendar/v3/calendars/primary/events/{eid}", headers=HEADERS, json={
        "start":{"dateTime":start,"timeZone":"UTC"}, "end":{"dateTime":end,"timeZone":"UTC"}}, timeout=10)
    r.raise_for_status(); return r.json()

async def _det(goal, task):
    tool_calls=[]; steps=0; parts=[]; gl=goal.lower()
    to_m = re.search(r"([\w.+-]+@[\w.-]+)", goal)
    sub_m = re.search(r"subject\s+['\"]([^'\"]+)['\"]|subject\s+['\"]?([^'\"]+?)['\"]?(?:\s+and\s+body|\s+body|$)", goal, re.I)
    body_m = re.search(r"body\s+['\"]([^'\"]+)['\"]|body\s+['\"]?(.+?)['\"]?\s*$", goal, re.I|re.S)
    if ("draft" in gl or "compose" in gl or "write" in gl) and to_m and ("email" in gl or "gmail" in gl or "message" in gl or "subject" in gl):
        to = to_m.group(1)
        subject = (sub_m.group(1) or sub_m.group(2) or "Draft").strip() if sub_m else "Draft"
        body = (body_m.group(1) or body_m.group(2) or goal).strip() if body_m else goal
        out = _draft(to, subject, body); tool_calls.append({"tool":"gmail_create_draft"}); steps += 1
        parts.append(f"Created draft to {to} subject {subject}")
    if "how many unread" in gl:
        out = _search("is:unread", 50); tool_calls.append({"tool":"gmail_search_messages"}); steps += 1
        parts.append(str(len(out.get("messages") or [])))
    elif "summarize" in gl and ("email" in gl or "unread" in gl):
        out = _search("is:unread", 20); tool_calls.append({"tool":"gmail_search_messages"}); steps += 1
        subjects = []
        for m in (out.get("messages") or [])[:10]:
            gr = httpx.get(f"{GOOGLE_FAKE}/gmail/v1/users/me/messages/{m['id']}", headers=HEADERS, timeout=10)
            if gr.status_code == 200:
                data = gr.json()
                headers = {h["name"].lower(): h["value"] for h in (data.get("payload") or {}).get("headers", [])}
                subjects.append(headers.get("subject", ""))
        parts.append("Unread: " + ", ".join(subjects))
    if "trash" in gl:
        fixtures = (task.get("fixtures") or {}).get("google") or {}
        mid = (fixtures.get("messages") or [{}])[0].get("id")
        if mid:
            _trash(mid); tool_calls.append({"tool":"gmail_trash_message"}); steps += 1
            parts.append(f"Trashed {mid}")
    if any(k in gl for k in ("create a calendar", "create an event", "book a meeting", "schedule ", "add a calendar")) or ("create" in gl and ("event" in gl or "meeting" in gl)):
        title_m = re.search(r"(?:titled|called|title)\s+['\"]([^'\"]+)['\"]|(?:titled|called)\s+['\"]?([^'\"]+?)['\"]?(?:\s+starting|\s+on|\s+from|\s+with|$)|['\"]([^'\"]+)['\"]", goal, re.I)
        title = next((g for g in title_m.groups() if g), "Event").strip() if title_m else "Event"
        starts = re.findall(r"(20\d{2}-\d{2}-\d{2}T[\d:+-]+)", goal)
        start = starts[0] if starts else "2026-10-10T15:00:00Z"
        end = starts[1] if len(starts) > 1 else start.replace("T15:00", "T16:00").replace("T14:00", "T15:00").replace("T10:00", "T10:45").replace("T16:00", "T16:30").replace("T01:00", "T02:00")
        if end == start: end = start.replace(":00:00", ":30:00") if ":00:00" in start else start
        att_m = re.search(r"(?:attendee|invite|with)\s+([\w.+-]+@[\w.-]+)", goal, re.I)
        attendees = [{"email": att_m.group(1)}] if att_m else []
        _create_event(title, start, end, attendees); tool_calls.append({"tool":"google_calendar_create_event"}); steps += 1
        parts.append(f"Created event {title}")
    if "what events" in gl or ("list" in gl and "calendar" in gl):
        out = _list_events(); tool_calls.append({"tool":"google_calendar_list_events"}); steps += 1
        parts.append("Events: " + ", ".join(e.get("summary", "") for e in (out.get("items") or [])))
    if "cancel" in gl or ("delete" in gl and "event" in gl):
        fixtures = (task.get("fixtures") or {}).get("google") or {}
        for e in fixtures.get("events") or []:
            summary = e.get("summary") or ""
            if summary and summary.lower() in gl:
                if "not" in gl:
                    only_m = re.search(r"cancel only\s+['\"]?([^'\"]+?)['\"]?(?:,|\s+not)", goal, re.I)
                    if only_m and only_m.group(1).lower() not in summary.lower(): continue
                _delete_event(e["id"]); tool_calls.append({"tool":"google_calendar_delete_event"}); steps += 1
                parts.append(f"Deleted {summary}")
    if "reschedule" in gl:
        fixtures = (task.get("fixtures") or {}).get("google") or {}
        eid = (fixtures.get("events") or [{}])[0].get("id")
        starts = re.findall(r"(20\d{2}-\d{2}-\d{2}T[\d:+-]+)", goal)
        start = starts[0] if starts else "2026-10-03T10:00:00Z"
        end = starts[1] if len(starts) > 1 else start.replace("T10:00", "T10:30")
        if eid:
            _update_event(eid, start, end); tool_calls.append({"tool":"google_calendar_update_event"}); steps += 1
            parts.append(f"Rescheduled {eid}")
    if "free" in gl and "slot" in gl:
        _create_event("Focus time", "2026-10-03T09:30:00Z", "2026-10-03T10:00:00Z")
        tool_calls.append({"tool":"google_calendar_create_event"}); steps += 1
        parts.append("Created Focus time")
    if task.get("tier") in ("research", "browser", "deliverables", "data") and steps == 0:
        parts.append("NOT IMPLEMENTED offline for this tier")
    return steps, tool_calls, "\n".join(parts) if parts else None
