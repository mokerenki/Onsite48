# Onsite48 New Engineer Onboarding Guide

**Audience:** Backend, frontend, platform, extension, and product engineers joining the Onsite48 team.  
**Repository:** `Onsite48_hardened`  
**Purpose:** Explain how to run the system, trace a task, make safe changes, debug failures, and validate work before submitting it.

> **The most important idea:** Onsite48 is a durable agent runtime with a conversational interface. It is not only a chat application. A user prompt may become a quick answer, a sourced web response, or a multi-step workflow with typed tools, approvals, browser control, verification, checkpoints, artifacts, and audit events.

## 1. Start here

A new engineer should first understand four boundaries:

1. **The frontend renders intent and progress.** It does not own authorization or final task state.
2. **The backend owns orchestration and policy.** The model may propose actions, but registered tools, permissions, approvals, and verification determine what can execute.
3. **PostgreSQL owns durable product truth.** Task history, event records, approvals, agent runs, step attempts, evidence, artifacts, browser sessions, and connector state belong there.
4. **Onsite48 Bridge is a controlled execution surface.** It lets the runtime operate in a user-selected local browser context, but it is not the planner and it is not an unrestricted remote-control channel.

The fastest orientation path is to read the repository [README](./README.md), then the [Recruit Architecture Guide](./SYNTHAI_RECRUIT_ARCHITECTURE_GUIDE.md), and finally this document. The architecture guide explains the complete system. This guide focuses on the decisions a new engineer must make during daily development.

## 2. Repository map

| Area | Responsibility | Start with |
|---|---|---|
| `backend/app/main.py` | FastAPI application and service startup | Application wiring and route registration |
| `backend/app/api/` | HTTP and WebSocket API surfaces | `realtime.py`, browser sessions, Bridge, auth |
| `backend/app/graph.py` | Agent graph and runtime state | Planner, executor, verifier transitions |
| `backend/app/agents/` | Planning, execution, verification, computer use | `planner.py`, `executor.py`, `verifier.py` |
| `backend/app/core/` | Contracts, policy, budgets, lifecycle, memory, routing helpers | `agent_contracts.py`, `task_state_machine.py`, `plan_quality.py` |
| `backend/app/tools/` | Registered tools and tool schemas | `registry.py`, connector tools, browser tools |
| `backend/app/services/` | Persistence, Bridge coordination, reports, memory, attachments | `browser_bridge.py`, task persistence, run ledger |
| `backend/app/models.py` | SQLAlchemy durable models | Tasks, events, runs, attempts, evidence, artifacts |
| `backend/alembic/versions/` | Database migrations | Latest migration before changing models |
| `backend/tests/` | Backend regression and contract tests | Focused tests before the full suite |
| `frontend/src/app/` | Next.js pages and route-level data loading | Home, Bridge, history, settings pages |
| `frontend/src/components/` | Chat, sidebar, plan, answer, loader, and shared UI | `Chatinterface.tsx`, `Sidebar.tsx`, `DataLoader.tsx` |
| `frontend/src/hooks/` | Typed client-side resource and interaction hooks | `useDataResource.ts` |
| `extension/onsite48-bridge/` | Chrome MV3 extension | `background.js`, popup, pairing, command execution |
| `docker-compose.yml` | Local service topology | Frontend, backend, database, Redis, Qdrant, worker |

## 3. Local development

### 3.1 Preferred full-stack path

The supported path is Docker Compose. Copy `.env.example` to `.env`, generate local secrets, configure the required model credentials, and start the stack.

```bash
cp .env.example .env
# Edit .env without committing it.
docker compose config
docker compose up --build -d
docker compose ps