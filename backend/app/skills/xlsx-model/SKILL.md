---
name: xlsx-model
description: Build Excel models with formulas, multiple sheets, and charts using openpyxl.
---

# XLSX Model Skill

## Procedure
1. `pip install openpyxl==3.1.5` if needed.
2. Prefer formulas over pre-computed values for inputs the user may change.
3. Example:
   ```python
   from openpyxl import Workbook
   from openpyxl.chart import BarChart, Reference
   wb = Workbook()
   ws = wb.active
   ws.title = "Model"
   ws["A1"] = "Item"; ws["B1"] = "Value"
   ws["A2"] = "Revenue"; ws["B2"] = 1000
   ws["A3"] = "Cost"; ws["B3"] = 400
   ws["A4"] = "Profit"; ws["B4"] = "=B2-B3"
   chart = BarChart()
   chart.add_data(Reference(ws, min_col=2, min_row=1, max_row=3), titles_from_data=True)
   ws.add_chart(chart, "D2")
   wb.save("out/model.xlsx")
   ```
4. Verify formulas evaluate (read B4 after data_only load if needed); deliver_file.
