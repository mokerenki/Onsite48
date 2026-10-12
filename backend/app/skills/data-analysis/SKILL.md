---
name: data-analysis
description: Load tabular data, compute statistics, and produce charts (matplotlib/pandas) as deliverable files.
---

# Data Analysis Skill

## Procedure
1. Load CSV/JSON with pandas.
2. Profile: shape, dtypes, nulls, describe().
3. Answer the analytic question with explicit aggregations.
4. Chart: matplotlib or pandas.plot; savefig to `out/chart.png`.
5. Write a short markdown findings file; deliver both chart and findings.

## Example
```python
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
df = pd.read_csv("data.csv")
summary = df.groupby("category")["value"].sum()
summary.plot(kind="bar")
plt.tight_layout()
plt.savefig("out/chart.png")
summary.to_csv("out/summary.csv")
```
