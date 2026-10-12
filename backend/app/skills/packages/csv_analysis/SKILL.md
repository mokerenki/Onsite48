# CSV Analysis

Clean, summarize, and chart tabular CSV data using shell + Python in the workspace.

## When to use
- User provides or points at a CSV file
- Need row counts, schema inference, aggregates, or a cleaned export

## Steps
1. `list_dir` / `read_file` to locate the CSV
2. Use `shell` with python to infer dtypes and null rates
3. Write cleaned output under `/workspace/cleaned.csv`
4. Write a short summary markdown with key stats
