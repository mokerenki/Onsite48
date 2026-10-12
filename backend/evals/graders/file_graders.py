
from __future__ import annotations
import os
from evals.graders.base import GradeResult

def grade_docx(path, expect):
    if not path or not os.path.isfile(path): return GradeResult(False, 0.0, [f"docx missing: {path}"])
    try: import docx
    except ImportError: return GradeResult(False, 0.0, ["python-docx not installed"])
    doc=docx.Document(path); text="\n".join(p.text for p in doc.paragraphs)
    for req in expect.get("must_contain", []):
        if req.lower() not in text.lower(): return GradeResult(False, 0.0, [f"missing: {req}"])
    return GradeResult(True, 1.0, ["docx ok"])

def grade_pdf(path, expect):
    if not path or not os.path.isfile(path): return GradeResult(False, 0.0, [f"pdf missing: {path}"])
    try:
        from pypdf import PdfReader
        text="\n".join(p.extract_text() or "" for p in PdfReader(path).pages)
    except Exception as e:
        return GradeResult(False, 0.0, [f"pdf parse failed: {e}"])
    for req in expect.get("must_contain", []):
        if req.lower() not in text.lower(): return GradeResult(False, 0.0, [f"missing: {req}"])
    return GradeResult(True, 1.0, ["pdf ok"])

def grade_pptx(path, expect):
    if not path or not os.path.isfile(path): return GradeResult(False, 0.0, [f"pptx missing: {path}"])
    try: from pptx import Presentation
    except ImportError: return GradeResult(False, 0.0, ["python-pptx not installed"])
    prs=Presentation(path)
    if expect.get("min_slides") and len(prs.slides)<int(expect["min_slides"]):
        return GradeResult(False, 0.0, ["too few slides"])
    return GradeResult(True, 1.0, ["pptx ok"])

def grade_xlsx(path, expect):
    if not path or not os.path.isfile(path): return GradeResult(False, 0.0, [f"xlsx missing: {path}"])
    try: import openpyxl
    except ImportError: return GradeResult(False, 0.0, ["openpyxl not installed"])
    wb=openpyxl.load_workbook(path)
    for req in expect.get("must_contain_cells", []):
        found=False
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                if any(req.lower() in str(c).lower() for c in row if c is not None): found=True; break
            if found: break
        if not found: return GradeResult(False, 0.0, [f"missing cell: {req}"])
    return GradeResult(True, 1.0, ["xlsx ok"])

def grade_html(path, expect):
    if not path or not os.path.isfile(path): return GradeResult(False, 0.0, [f"html missing: {path}"])
    text=open(path, encoding="utf-8", errors="replace").read()
    for req in expect.get("must_contain", []):
        if req.lower() not in text.lower(): return GradeResult(False, 0.0, [f"missing: {req}"])
    return GradeResult(True, 1.0, ["html ok"])
