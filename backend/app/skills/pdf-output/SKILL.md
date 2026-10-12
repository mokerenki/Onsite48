---
name: pdf-output
description: Produce PDFs via reportlab or by converting Office/HTML with LibreOffice headless / pdftoppm for previews.
---

# PDF Output Skill

## Paths
- **reportlab** for programmatic PDFs (text, tables, simple graphics).
- **LibreOffice headless** for converting docx/pptx/odt → pdf: `soffice --headless --convert-to pdf --outdir out input.docx`
- **poppler** for page previews: `pdftoppm -png -r 120 out/doc.pdf out/preview`

## reportlab minimal
```python
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
c = canvas.Canvas("out/report.pdf", pagesize=letter)
c.drawString(72, 720, "Title")
c.save()
```

## Verify
`shell_exec("file out/report.pdf")` should contain PDF.
