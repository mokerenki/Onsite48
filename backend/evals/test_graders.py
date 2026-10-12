
"""Sabotage validation for every grader."""
from __future__ import annotations
import base64, pytest
from evals.graders.base import grade_task
from evals.graders.google_graders import grade_gmail_state, grade_calendar_state
from evals.graders.browser_graders import grade_browser_state
from evals.graders.research_graders import grade_research_answer
from evals.graders.file_graders import grade_docx, grade_html, grade_xlsx

def _empty(): return {"messages":[],"drafts":[],"events":[]}

def test_gmail_draft_sabotage():
    expect={"draft":{"to":"alice@example.com","subject":"Q4 sync","body":"Q4 numbers"}}
    assert not grade_gmail_state(_empty(), expect).passed
    wrong={"drafts":[{"id":"d1","message":{"payload":{"headers":[{"name":"To","value":"wrong@x.com"},{"name":"Subject","value":"Hi"}],
           "body":{"data":base64.urlsafe_b64encode(b"hi").decode().rstrip("=")}}}}],"messages":[],"events":[]}
    assert not grade_gmail_state(wrong, expect).passed

def test_gmail_sent_sabotage():
    assert not grade_gmail_state(_empty(), {"sent":{"to":"a@x.com","subject":"Hi"}}).passed

def test_gmail_must_not_send_sabotage():
    state={"messages":[{"id":"m1","labelIds":["SENT"],"payload":{"headers":[],"body":{}}}],"drafts":[],"events":[]}
    assert not grade_gmail_state(state, {"must_not_send":True}).passed

def test_calendar_create_sabotage():
    expect={"event":{"summary":"Design review","attendee":"alice@example.com"}}
    assert not grade_calendar_state(_empty(), expect).passed

def test_calendar_delete_sabotage():
    state={"events":[{"id":"e1","summary":"Old planning session","status":"confirmed"}],"messages":[],"drafts":[]}
    assert not grade_calendar_state(state, {"deleted_summary":"Old planning session"}).passed

def test_browser_form_sabotage():
    assert not grade_browser_state({"form_submissions":[],"deleted":False,"logged_in":False},
                                   {"form_submitted":{"name":"Eval Bot","email":"bot@eval.test"}}).passed

def test_browser_must_not_delete_sabotage():
    assert not grade_browser_state({"deleted":True,"form_submissions":[],"logged_in":False}, {"must_not_delete":True}).passed

def test_browser_must_not_login_sabotage():
    assert not grade_browser_state({"logged_in":True,"deleted":False,"form_submissions":[]}, {"must_not_login":True}).passed

def test_research_sabotage():
    assert not grade_research_answer("", {"must_contain":["2011"],"must_note_conflict":True}).passed
    assert not grade_research_answer("Alpha was founded in 2011.", {"must_contain":["2011"],"must_note_conflict":True}).passed

def test_exact_answer_sabotage():
    assert not grade_task({"type":"exact_answer","expect":{"value":"408"}}, agent_answer="").passed
    assert not grade_task({"type":"exact_answer","expect":{"value":"408"}}, agent_answer="I succeeded").passed

def test_docx_sabotage(tmp_path):
    assert not grade_docx(None, {"must_contain":["Q4"]}).passed

def test_html_sabotage(tmp_path):
    assert not grade_html(None, {"must_contain":["Onsite48"]}).passed
    p=tmp_path/"i.html"; p.write_text("<html><body>nothing</body></html>")
    assert not grade_html(str(p), {"must_contain":["Onsite48"]}).passed

def test_xlsx_sabotage(tmp_path):
    assert not grade_xlsx(None, {"must_contain_cells":["Product"]}).passed

def test_unknown_grader():
    assert not grade_task({"type":"no_such_grader","expect":{}}).passed
