# Runtime targets (honest)

These are **targets**, not measured results. Simulated lane-sweep / long-horizon
numbers are **not** production evidence.

## Product models
- Primary loop: Kimi (`KIMI_MODEL` / kimi-latest)
- Verification / fallback: DeepSeek (`DEEPSEEK_MODEL` / deepseek-chat)
- Do not default to gpt-4o / claude-3-5-sonnet / gemini-1.5 until clients exist

## Functional targets
| Target | Metric | Notes |
|--------|--------|-------|
| Loop terminates | finish tool or budget | Unit: fake model |
| No Unknown role: loop | router aliases loop → planning | Unit |
| Import health | `import app.main` | Collection |
| Legacy vs loop eval | runner supports both | Live: NOT RUN without keys |
| Long-horizon | real credentials | NOT RUN |
| Held-out / GAIA | held-out dir | NOT RUN until loop+model proven |

## Non-targets (explicitly NOT claimed)
- Fabricated pass rates from hash(lane, model)
- Simulated budget-only long-horizon passes
- Frontier model rankings without API clients
