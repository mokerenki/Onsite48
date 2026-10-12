# Onsite48 Recruit Architecture Guide

**Audience:** engineers, product engineers, operators, and technical product managers joining the Onsite48 team.  
**Repository reviewed:** `Onsite48_hardened` from `Onsite48_worldclass_redesigned(1).zip`.  
**Review focus:** system architecture, user-prompt-to-result execution, durable data, Onsite48 Bridge, direct control plane, security boundaries, deployment, and extension points.

> **Core idea:** Onsite48 is an authenticated, stateful agent runtime rather than a stateless chatbot. A request may be answered directly, researched through read-only evidence collection, or executed as a durable workflow that plans typed tool calls, verifies postconditions, pauses for approval or human takeover, and records the result.

## 1. Executive summary

Onsite48 is organized around five cooperating surfaces:

| Surface | Responsibility | Primary code |
|---|---|---|
| Conversational workspace | Accept prompts, upload files, display plans and events, show answers, and expose pause/approval/takeover controls. | `frontend/src/components/Chatinterface.tsx` |
| API and realtime gateway | Authenticate users, enforce organization scope, expose REST resources, and carry task WebSocket messages. | `backend/app/main.py`, `backend/app/api/`, `backend/app/api/realtime.py` |
| Agent runtime | Classify intent, route the domain, create a plan, execute registered tools, verify results, and recover or stop. | `backend/app/graph.py`, `backend/app/agents/`, `backend/app/core/` |
| Durable product state | Store users, organizations, task threads, events, approvals, agent runs, step attempts, browser sessions, Bridge commands, schedules, connectors, and artifacts. | `backend/app/models.py`, Alembic migrations, PostgreSQL |
| Execution surfaces | Operate isolated server browsers, the user’s local browser through Onsite48 Bridge, connectors, memory, files, Python, and sandbox services. | `backend/app/tools/`, `backend/app/services/`, `extension/onsite48-bridge/` |

The most important architectural rule is the separation of **planning** from **authority**. The model can propose a step, but it cannot grant itself permission. Server-side identity, organization membership, typed tool schemas, approval records, browser-session authorization, device affinity, leases, and extension-side validation decide whether a step can run.

## 2. System architecture

The default deployment is a Docker Compose application behind Nginx. The frontend is a Next.js application. The backend is a FastAPI process. PostgreSQL is the system of record. Redis supplies checkpoints and coordination. Qdrant supports vector retrieval for the memory/source-library subsystem. A separate proactive worker owns scheduled and signal-driven execution when enabled.

```mermaid
flowchart TB
    User[User] --> Browser[Web browser]
    Browser --> Nginx[Nginx reverse proxy]
    Nginx --> Frontend[Next.js frontend]
    Nginx --> API[FastAPI backend]
    Nginx --> WS[Authenticated task WebSocket]

    Frontend -->|REST: auth, history, files, sessions, controls| API
    Frontend -->|run_task, follow-up, pause, resume, approve| WS
    WS --> Runtime[Agent runtime / LangGraph]
    API --> Auth[JWT, refresh session, organization scope]
    WS --> Auth

    Runtime --> Models[Model router]
    Runtime --> Registry[Typed ToolRegistry]
    Runtime --> Policy[Preflight, approval, takeover, budget policy]
    Runtime --> Postgres[(PostgreSQL)]
    Runtime --> Redis[(Redis checkpoints / coordination)]

    Registry --> AgentComputer[Isolated agent computer]
    Registry --> BridgeService[Direct control-plane Bridge service]
    Registry --> Connectors[OAuth/API/MCP connectors]
    Registry --> Memory[Memory and source library]
    Registry --> Files[Attachment and report services]
    Registry --> Python[Controlled Python / charts]

    BridgeService <-->|Bridge WebSocket or authenticated poll| Extension[Onsite48 Bridge MV3 extension]
    Extension --> LocalBrowser[User-selected local browser tab]
    Memory --> Qdrant[(Qdrant vectors)]
    Proactive[Proactive worker] --> Runtime
    Proactive --> Postgres