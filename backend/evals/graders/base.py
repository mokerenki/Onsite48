
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

@dataclass
class GradeResult:
    passed: bool
    score: float
    notes: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)

def grade_task(grader_spec, *, google_state=None, browser_state=None, artifacts=None, agent_answer=None, workspace_dir=None):
    gtype = (grader_spec or {}).get("type", "")
    expect = (grader_spec or {}).get("expect", {})
    if gtype == "gmail_state":
        from evals.graders.google_graders import grade_gmail_state
        return grade_gmail_state(google_state or {}, expect)
    if gtype == "calendar_state":
        from evals.graders.google_graders import grade_calendar_state
        return grade_calendar_state(google_state or {}, expect)
    if gtype == "browser_state":
        from evals.graders.browser_graders import grade_browser_state
        return grade_browser_state(browser_state or {}, expect)
    if gtype == "research_answer":
        from evals.graders.research_graders import grade_research_answer
        return grade_research_answer(agent_answer or "", expect)
    if gtype == "exact_answer":
        return _exact(agent_answer or "", expect)
    if gtype in ("docx","pdf","pptx","xlsx","html"):
        from evals.graders import file_graders
        path = (artifacts or {}).get(expect.get("artifact_key","file")) or expect.get("path")
        if not path and workspace_dir and expect.get("filename"):
            import os; path = os.path.join(workspace_dir, expect["filename"])
        return getattr(file_graders, f"grade_{gtype}")(path, expect)
    if gtype == "compound":
        results = [grade_task(s, google_state=google_state, browser_state=browser_state, artifacts=artifacts, agent_answer=agent_answer, workspace_dir=workspace_dir) for s in grader_spec.get("graders",[])]
        passed = all(r.passed for r in results)
        notes = [n for r in results for n in r.notes]
        score = sum(r.score for r in results)/max(len(results),1)
        return GradeResult(passed=passed, score=score, notes=notes)
    return GradeResult(passed=False, score=0.0, notes=[f"Unknown grader type: {gtype}"])

def _exact(answer, expect):
    target = str(expect.get("value","")).strip()
    got = answer.strip()
    if expect.get("normalize", True):
        got = " ".join(got.lower().split()); target = " ".join(target.lower().split())
    if got == target or target in got:
        return GradeResult(passed=True, score=1.0, notes=["exact match"])
    if expect.get("numeric"):
        try:
            g=float(got.replace(",","")); t=float(target.replace(",","")); tol=float(expect.get("tolerance",0.01))
            if abs(g-t)<=tol: return GradeResult(passed=True, score=1.0, notes=["numeric match"])
        except ValueError: pass
    return GradeResult(passed=False, score=0.0, notes=[f"expected '{target}', got '{answer[:200]}'"])
