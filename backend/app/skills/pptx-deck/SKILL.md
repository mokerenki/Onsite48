---
name: pptx-deck
description: Build PowerPoint decks (.pptx) with title slides, content slides, and charts using python-pptx.
---

# PPTX Deck Skill

## Procedure
1. `pip install python-pptx==1.0.2` if needed.
2. Script pattern:
   ```python
   from pptx import Presentation
   from pptx.util import Inches, Pt
   prs = Presentation()
   # title slide
   slide = prs.slides.add_slide(prs.slide_layouts[0])
   slide.shapes.title.text = "Title"
   # content
   slide2 = prs.slides.add_slide(prs.slide_layouts[1])
   slide2.shapes.title.text = "Section"
   slide2.placeholders[1].text = "Bullet points"
   prs.save("out/deck.pptx")
   ```
3. Verify slide count; deliver_file.

## Quality
- At least title + 2 content slides for non-trivial asks
- Readable font sizes (title >= 28pt)
