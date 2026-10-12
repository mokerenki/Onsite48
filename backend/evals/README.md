# Trajectory evaluation harness

This is the outcome-level layer the unit tests and `tests/golden_prompts/v0.json`
do not cover. The golden prompts assert *intent and one deterministic capability*.
This harness runs realistic user goals through the **real** `Planner` and the
**real** `ToolRegistry`, then asserts what the agent actually did:

- which capability it reached,
- which arguments it passed,
- whether it guessed or asked,
- whether an irreversible action was gated by a visible confirmation.

It runs fully offline and deterministically. No network, no API keys, no docker.

## Run it

```bash
cd backend
python scripts/run_trajectory_eval.py