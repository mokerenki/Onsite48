"""In-memory Gmail + Calendar REST fake for outcome evals."""
from __future__ import annotations
import base64, email
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, Header, HTTPException, Query, Request, Response

app = FastAPI(title="Onsite48 Google API Fake")
_messages: Dict[str, Dict[str, Any]] = {}
_drafts: Dict[str, Dict[str, Any]] = {}
_events: Dict[str, Dict[str, Any]] = {}
_next_msg = _next_draft = _next_event = 1

def reset_state(seed=None):
    global _messages, _drafts, _events, _next_msg, _next_draft, _next_event
    _messages, _drafts, _events = {}, {}, {}
    _next_msg = _next_draft = _next_event = 1
    if not seed:
        return
    for m in seed.get("messages", []):
        mid = m.get("id") or f"msg_{_next_msg}"; _next_msg += 1
        _messages[mid] = {
            "id": mid, "threadId": m.get("threadId", mid),
            "labelIds": list(m.get("labelIds", ["INBOX"])),
            "snippet": m.get("snippet", ""),
            "payload": m.get("payload") or _build_payload(
                m.get("from", "s@x.com"), m.get("to", "me@x.com"),
                m.get("subject", ""), m.get("body", "")),
            "internalDate": m.get("internalDate", str(int(datetime.now(timezone.utc).timestamp() * 1000))),
        }
    for d in seed.get("drafts", []):
        did = d.get("id") or f"draft_{_next_draft}"; _next_draft += 1
        mid = f"msg_{_next_msg}"; _next_msg += 1
        _drafts[did] = {"id": did, "message": {
            "id": mid, "threadId": mid, "labelIds": ["DRAFT"],
            "payload": _build_payload("me@x.com", d.get("to", ""), d.get("subject", ""), d.get("body", "")),
        }}
    for e in seed.get("events", []):
        eid = e.get("id") or f"evt_{_next_event}"; _next_event += 1
        _events[eid] = {
            "id": eid, "status": e.get("status", "confirmed"),
            "summary": e.get("summary", e.get("title", "")),
            "description": e.get("description", ""), "location": e.get("location", ""),
            "start": e.get("start", {"dateTime": e.get("start_time", ""), "timeZone": "UTC"}),
            "end": e.get("end", {"dateTime": e.get("end_time", ""), "timeZone": "UTC"}),
            "attendees": e.get("attendees", []),
            "created": datetime.now(timezone.utc).isoformat(),
        }

def _build_payload(frm, to, subject, body):
    return {
        "headers": [
            {"name": "From", "value": frm},
            {"name": "To", "value": to},
            {"name": "Subject", "value": subject},
        ],
        "body": {"data": base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")},
        "mimeType": "text/plain",
    }

def _auth(a):
    if not a or not a.lower().startswith("bearer "):
        raise HTTPException(401, "auth")

def _decode_raw(raw_b64):
    try:
        padded = raw_b64 + "=" * (-len(raw_b64) % 4)
        msg = email.message_from_bytes(base64.urlsafe_b64decode(padded))
        body = ""
        if msg.is_multipart():
            for p in msg.walk():
                if p.get_content_type() == "text/plain":
                    body = p.get_payload(decode=True).decode(errors="replace")
                    break
        else:
            pl = msg.get_payload(decode=True)
            body = pl.decode(errors="replace") if isinstance(pl, bytes) else str(msg.get_payload())
        return {"from": msg.get("From", ""), "to": msg.get("To", ""),
                "subject": msg.get("Subject", ""), "body": body}
    except Exception:
        return {"from": "", "to": "", "subject": "", "body": ""}

@app.get("/__health")
def health():
    return {"status": "ok"}

@app.get("/__state")
def state():
    return {
        "messages": list(_messages.values()),
        "drafts": list(_drafts.values()),
        "events": list(_events.values()),
        "message_count": len(_messages),
        "draft_count": len(_drafts),
        "event_count": len(_events),
    }

@app.post("/__reset")
async def reset(request: Request):
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    reset_state(body if isinstance(body, dict) else None)
    return {"status": "reset"}

@app.post("/__seed")
async def seed(request: Request):
    body = await request.json()
    reset_state(body if isinstance(body, dict) else None)
    return {"status": "seeded", "messages": len(_messages), "drafts": len(_drafts), "events": len(_events)}

@app.get("/gmail/v1/users/me/messages")
def list_messages(authorization: Optional[str] = Header(None), q: Optional[str] = Query(None), maxResults: int = Query(50)):
    _auth(authorization)
    results = list(_messages.values())
    if q:
        ql = q.lower()
        filtered = []
        for m in results:
            headers = {h["name"].lower(): h["value"] for h in m.get("payload", {}).get("headers", [])}
            hay = " ".join([headers.get("subject", ""), headers.get("from", ""), " ".join(m.get("labelIds", []))]).lower()
            if "is:unread" in ql and "UNREAD" not in m.get("labelIds", []):
                continue
            if "in:inbox" in ql and "INBOX" not in m.get("labelIds", []):
                continue
            ok = True
            for token in ql.split():
                if token.startswith("subject:") and token[8:].strip('"') not in headers.get("subject", "").lower():
                    ok = False
                elif token.startswith("from:") and token[5:].strip('"') not in headers.get("from", "").lower():
                    ok = False
                elif token not in ("is:unread", "in:inbox", "in:trash", "is:read") and not token.startswith(("subject:", "from:")) and token.strip('"') not in hay:
                    ok = False
            if ok:
                filtered.append(m)
        results = filtered
    results.sort(key=lambda m: int(m.get("internalDate") or 0), reverse=True)
    limited = results[: max(1, min(maxResults, 100))]
    return {"messages": [{"id": m["id"], "threadId": m["threadId"]} for m in limited], "resultSizeEstimate": len(results)}

@app.get("/gmail/v1/users/me/messages/{message_id}")
def get_message(message_id: str, authorization: Optional[str] = Header(None)):
    _auth(authorization)
    m = _messages.get(message_id)
    if not m:
        raise HTTPException(404, "not found")
    return m

@app.delete("/gmail/v1/users/me/messages/{message_id}")
def delete_message(message_id: str, authorization: Optional[str] = Header(None)):
    _auth(authorization)
    if message_id not in _messages:
        raise HTTPException(404)
    del _messages[message_id]
    return Response(status_code=204)

@app.post("/gmail/v1/users/me/messages/{message_id}/trash")
def trash_message(message_id: str, authorization: Optional[str] = Header(None)):
    _auth(authorization)
    m = _messages.get(message_id)
    if not m:
        raise HTTPException(404)
    labels = set(m.get("labelIds", []))
    labels.discard("INBOX")
    labels.add("TRASH")
    m["labelIds"] = list(labels)
    return m

@app.post("/gmail/v1/users/me/messages/send")
async def send_message(request: Request, authorization: Optional[str] = Header(None)):
    global _next_msg
    _auth(authorization)
    body = await request.json()
    raw = body.get("raw", "")
    parsed = _decode_raw(raw) if raw else {"from": "me@x.com", "to": "", "subject": "", "body": ""}
    mid = f"msg_{_next_msg}"; _next_msg += 1
    msg = {
        "id": mid, "threadId": body.get("threadId", mid), "labelIds": ["SENT"],
        "snippet": (parsed.get("body") or "")[:100],
        "payload": _build_payload(parsed.get("from") or "me@x.com", parsed.get("to", ""), parsed.get("subject", ""), parsed.get("body", "")),
        "internalDate": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
    }
    _messages[mid] = msg
    return {"id": mid, "threadId": msg["threadId"], "labelIds": ["SENT"]}

@app.get("/gmail/v1/users/me/drafts")
def list_drafts(authorization: Optional[str] = Header(None), maxResults: int = Query(50)):
    _auth(authorization)
    items = list(_drafts.values())[: max(1, min(maxResults, 100))]
    return {"drafts": items, "resultSizeEstimate": len(_drafts)}

@app.post("/gmail/v1/users/me/drafts")
async def create_draft(request: Request, authorization: Optional[str] = Header(None)):
    global _next_draft, _next_msg
    _auth(authorization)
    body = await request.json()
    message = body.get("message") or {}
    raw = message.get("raw", "")
    parsed = _decode_raw(raw) if raw else {"from": "me@x.com", "to": "", "subject": "", "body": ""}
    did = f"draft_{_next_draft}"; _next_draft += 1
    mid = f"msg_{_next_msg}"; _next_msg += 1
    draft = {"id": did, "message": {
        "id": mid, "threadId": message.get("threadId", mid), "labelIds": ["DRAFT"],
        "payload": _build_payload(parsed.get("from") or "me@x.com", parsed.get("to", ""), parsed.get("subject", ""), parsed.get("body", "")),
    }}
    _drafts[did] = draft
    return draft

@app.post("/gmail/v1/users/me/drafts/send")
async def send_draft(request: Request, authorization: Optional[str] = Header(None)):
    global _next_msg
    _auth(authorization)
    body = await request.json()
    draft_id = body.get("id")
    if not draft_id or draft_id not in _drafts:
        raise HTTPException(404, "draft not found")
    draft = _drafts.pop(draft_id)
    mid = f"msg_{_next_msg}"; _next_msg += 1
    msg = {
        "id": mid, "threadId": draft["message"].get("threadId", mid), "labelIds": ["SENT"], "snippet": "",
        "payload": draft["message"].get("payload"),
        "internalDate": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
    }
    _messages[mid] = msg
    return {"id": mid, "threadId": msg["threadId"], "labelIds": ["SENT"]}

@app.delete("/gmail/v1/users/me/drafts/{draft_id}")
def delete_draft(draft_id: str, authorization: Optional[str] = Header(None)):
    _auth(authorization)
    if draft_id not in _drafts:
        raise HTTPException(404)
    del _drafts[draft_id]
    return Response(status_code=204)

@app.get("/calendar/v3/calendars/primary/events")
def list_events(authorization: Optional[str] = Header(None), timeMin: Optional[str] = Query(None),
                timeMax: Optional[str] = Query(None), maxResults: int = Query(50), q: Optional[str] = Query(None)):
    _auth(authorization)
    items = list(_events.values())
    if q:
        ql = q.lower()
        items = [e for e in items if ql in (e.get("summary") or "").lower()]
    if timeMin:
        items = [e for e in items if (e.get("start") or {}).get("dateTime", "") >= timeMin]
    if timeMax:
        items = [e for e in items if (e.get("start") or {}).get("dateTime", "") <= timeMax]
    return {"kind": "calendar#events", "items": items[: max(1, min(maxResults, 250))], "summary": "primary"}

@app.post("/calendar/v3/calendars/primary/events")
async def create_event(request: Request, authorization: Optional[str] = Header(None)):
    global _next_event
    _auth(authorization)
    body = await request.json()
    eid = f"evt_{_next_event}"; _next_event += 1
    event = {
        "id": eid, "status": "confirmed", "summary": body.get("summary", ""),
        "description": body.get("description", ""), "location": body.get("location", ""),
        "start": body.get("start", {}), "end": body.get("end", {}),
        "attendees": body.get("attendees", []), "recurrence": body.get("recurrence"),
        "created": datetime.now(timezone.utc).isoformat(),
        "updated": datetime.now(timezone.utc).isoformat(),
        "htmlLink": f"https://calendar.google.com/event?eid={eid}",
    }
    _events[eid] = event
    return event

@app.patch("/calendar/v3/calendars/primary/events/{event_id}")
async def update_event(event_id: str, request: Request, authorization: Optional[str] = Header(None)):
    _auth(authorization)
    if event_id not in _events:
        raise HTTPException(404)
    body = await request.json()
    ev = _events[event_id]
    for k in ("summary", "description", "location", "start", "end", "attendees", "recurrence", "status"):
        if k in body:
            ev[k] = body[k]
    ev["updated"] = datetime.now(timezone.utc).isoformat()
    return ev

@app.delete("/calendar/v3/calendars/primary/events/{event_id}")
def delete_event(event_id: str, authorization: Optional[str] = Header(None)):
    _auth(authorization)
    if event_id not in _events:
        raise HTTPException(404)
    del _events[event_id]
    return Response(status_code=204)

@app.get("/calendar/v3/calendars/primary/events/{event_id}")
def get_event(event_id: str, authorization: Optional[str] = Header(None)):
    _auth(authorization)
    ev = _events.get(event_id)
    if not ev:
        raise HTTPException(404)
    return ev

@app.post("/calendar/v3/freeBusy")
async def free_busy(request: Request, authorization: Optional[str] = Header(None)):
    _auth(authorization)
    body = await request.json()
    time_min, time_max = body.get("timeMin", ""), body.get("timeMax", "")
    busy = []
    for e in _events.values():
        start = (e.get("start") or {}).get("dateTime", "")
        end = (e.get("end") or {}).get("dateTime", "")
        if start and end and (not time_min or end > time_min) and (not time_max or start < time_max):
            busy.append({"start": start, "end": end})
    return {"kind": "calendar#freeBusy", "timeMin": time_min, "timeMax": time_max,
            "calendars": {"primary": {"busy": busy}}}

reset_state({
    "messages": [
        {"id": "msg_seed_1", "from": "alice@example.com", "to": "me@example.com",
         "subject": "Q4 planning notes", "body": "Here are the Q4 planning notes.",
         "labelIds": ["INBOX", "UNREAD"], "internalDate": "1700000000000"},
        {"id": "msg_seed_2", "from": "bob@example.com", "to": "me@example.com",
         "subject": "Invoice #4421", "body": "Please find invoice 4421.",
         "labelIds": ["INBOX"], "internalDate": "1699000000000"},
    ],
    "events": [{
        "id": "evt_seed_1", "summary": "Team standup",
        "start": {"dateTime": "2026-10-03T09:00:00Z", "timeZone": "UTC"},
        "end": {"dateTime": "2026-10-03T09:30:00Z", "timeZone": "UTC"},
        "attendees": [{"email": "alice@example.com"}],
    }],
})
