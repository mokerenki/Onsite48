---
name: docx-report
description: Produce polished Word (.docx) reports with headings, tables, and figures using python-docx inside the workspace.
---

# DOCX Report Skill

## When to use
User asks for a Word document, report, memo, or letter as a `.docx` deliverable.

## Procedure (verified in workspace)
1. Ensure package: `python3 -c "import docx"` — if missing, `pip install python-docx==1.1.2` via shell_exec.
2. Write a Python script (file_write) that:
   - Creates `Document()`
   - Adds title via `doc.add_heading(..., level=0)`
   - Uses styles for Heading 1/2, Normal
   - Adds tables with `doc.add_table(rows=r, cols=c)` and fills cells
   - Saves to `out/report.docx`
3. Run with `python_exec` or `shell_exec("python3 build_report.py")`.
4. Verify: `shell_exec("python3 -c \"from docx import Document; d=Document('out/report.docx'); print(len(d.paragraphs), len(d.tables))\"")`
5. `deliver_file(path="out/report.docx")`.

## Quality checklist
- Title + at least one heading level
- No empty paragraphs spam
- Table headers bold if present
- File size > 5 KB for non-trivial content

## Anti-patterns
- Do not emit markdown and claim it is docx.
- Do not use host paths outside the workspace.
