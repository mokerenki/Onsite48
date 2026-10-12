# Onsite48 R8 redesign release notes

## Implemented

This release adds a bounded **Wide Research** coordinator that runs up to six independent source-specialist branches concurrently, each using the existing read-only public browser search path and the configured fast model lane for concise evidence dossiers. Branch results preserve source URLs, raw evidence, status, model/provider metadata, latency, and caveats for final synthesis. The planner deterministically selects `wide_research` for explicit requests such as “wide research,” “deep research,” “multi-source research,” and “research thoroughly.” The server-authoritative plan policy classifies it as internal, read-only, non-approval work with one attempt.

The application startup path now separates core L1 readiness from optional capabilities. Redis, model clients, checkpointer, browser automation, local tools, and the domain router are initialized for task readiness. Cognee, MCP discovery/tool registration, and persistent sandbox warm-up run concurrently in a supervised background task. Detailed health output exposes capability states for memory, MCP, and persistent sandbox rather than hiding them behind one monolithic startup wait.

The model router now applies an absolute per-call deadline from `MODEL_CALL_DEADLINE_SECONDS` (default 45 seconds) and bounds fallback calls to a shorter deadline. This prevents one provider from consuming an unbounded retry/fallback chain. The existing role configuration remains supported, including NVIDIA planning, Kimi verification, and DeepSeek fallback.

The existing Onsite48 Bridge remains in place with structured surface observation, bounded read-only scroll iteration, evidence/receipt handling, selected-tab control, reconnect support, and mutation approval boundaries. No screenshot-only regression was introduced.

## Configuration guidance

Set `MODEL_CALL_DEADLINE_SECONDS=30` for a latency-sensitive deployment, or keep the default 45 seconds while measuring provider p95 latency. Keep `SYNTHAI_PLANNING_PROVIDER=nvidia` if the NVIDIA endpoint is the intended low-cost planning provider, but cap planner output in the provider configuration and monitor rate limits. Keep connector actions out of any low-risk quick lane.

Remove duplicate `.env` keys, especially `CORS_ORIGINS` and `OAUTH_REDIRECT_BASE_URL`, leaving one canonical value for each. Never include `.env` in release archives or source-control commits.

## Verification

- Backend focused Wide Research/planner/quick-route suite: 20 passed.
- Complete backend suite: 205 passed.
- Extension JavaScript syntax checks: passed for the modified protocol and background runtime.
- Frontend production build: passed with type checking and linting.
- Playwright browser integration tests: passed after installing the Chromium runtime.

## Known operational boundary

Wide Research is read-only and bounded. It can search public sources and summarize returned evidence, but it cannot guarantee access to paywalled, private, or dynamically hidden content. It must report failed branches and disagreements rather than inventing missing evidence. External mutations such as sending email, creating Calendar events, clicking, filling, or submitting forms remain subject to the existing approval and intervention policies.

## Suggested smoke tests

1. Submit: `Wide research the autonomous browser-agent market. Compare current capabilities, reliability, safety, and pricing evidence across at least four independent angles.` Confirm several branch dossiers and URLs appear.
2. Submit: `Draft an email to test@example.com with subject "R8 test" and body "Test only; do not send."` Confirm durable planning and a draft result without send.
3. Submit: `Create a calendar event titled "R8 test" tomorrow at 3 PM for 30 minutes.` Confirm the task pauses for explicit approval before creation.
4. Open Facebook notifications in the paired tab and submit: `Read the visible notifications without clicking anything.` Confirm structured entries and no mutation.
5. Start with MCP/Qdrant unavailable and check that the API reaches core readiness while optional capability status reports degraded/warming.