# Onsite48 Manus-Level Agent Capability Roadmap

**Audience:** engineering leadership, senior backend/frontend engineers, agent-runtime engineers, product managers, security engineers, and new contributors.

**Scope:** Whole-repository audit of the planner, router, graph runtime, tool registry, executor, verifier, connectors, browser automation, Onsite48 Bridge, memory, persistence, scheduling, model routing, frontend task lifecycle, and tests.

**Objective:** Move Onsite48 from a broad collection of promising agent features to a dependable agent platform that behaves correctly on ordinary requests, understands user intent semantically, selects the right capability, executes with authority boundaries, verifies the actual outcome, and improves through measurable evaluation.

---

## Executive verdict

Onsite48 already has the outline of a serious agent platform: authenticated tasks, durable runs, a LangGraph-style planner/executor/verifier loop, typed tool registration, approvals, browser sessions, Onsite48 Bridge, connectors, memory, schedules, checkpoints, events, and operator replay.

However, the current system is not yet Manus- or Claude-Cowork-level reliable because the components do not share a sufficiently strong semantic contract. The planner frequently reasons in terms of **keywords and tool names**, while the executor and verifier mostly reason in terms of **step shape and field presence**. This allows a request such as “How many email drafts do I have?” to be routed to a tool that returns ten received messages.

That failure class will recur across every domain unless the platform establishes a typed model of:

```text
user intent
→ resource
→ operation
→ constraints
→ authority
→ capability
→ arguments
→ expected result
→ evidence
→ final answer