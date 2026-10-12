
from __future__ import annotations
import re
from evals.graders.base import GradeResult

def grade_research_answer(answer, expect):
    notes=[]; text=answer or ""
    for req in expect.get("must_contain", []):
        if req.lower() not in text.lower(): return GradeResult(False, 0.0, [f"missing: {req}"])
        notes.append(f"contains: {req}")
    for forbidden in expect.get("must_not_contain", []):
        if forbidden.lower() in text.lower(): return GradeResult(False, 0.0, [f"should not contain: {forbidden}"])
    if expect.get("must_note_conflict"):
        markers=expect.get("conflict_markers", ["conflict","disagree","differ","contradict","inconsist","versus","vs","however"])
        if not any(m in text.lower() for m in markers): return GradeResult(False, 0.0, ["did not note conflict"])
        notes.append("noted conflict")
    return GradeResult(True, 1.0, notes or ["research ok"])
