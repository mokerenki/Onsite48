# Onsite48 Codebase Guide

**Audience:** engineers, operators, and technical contributors joining the Onsite48 project.

**Repository baseline:** the verified `Onsite48_worldclass_complete.zip` redesign with runtime correlation, browser sessions, proactive scheduling, chat-native automation configuration, and Mermaid rendering.

**Purpose:** explain what the system does, how the code is organized, how a request travels through the runtime, how the database and APIs fit together, how Onsite48 Bridge interacts with the local browser, and what must be understood before changing production behavior.

> **Core idea:** Onsite48 is not only a chatbot. It is an authenticated, stateful agent runtime that plans work, executes typed tools, observes results, verifies postconditions, pauses for approval or human takeover, persists evidence, and can later resume or repeat work through schedules.

---

## 1. Executive architecture

Onsite48 has five cooperating execution surfaces:

| Surface | Primary responsibility | Main implementation area |
|---|---|---|
| Conversational workspace | Accept prompts, show plans and progress, render answers, expose controls | `frontend/src/components/Chatinterface.tsx` |
| API and realtime gateway | Authenticate users, expose REST routes, accept task WebSocket commands, stream events | `backend/app/main.py`, `backend/app/api/` |
| Agent runtime | Route domains, plan steps, execute tools, verify results, recover or pause | `backend/app/graph.py`, `backend/app/agents/` |
| Persistence and coordination | Store users, tasks, runs, events, approvals, schedules, sessions, and artifacts | `backend/app/models.py`, `backend/alembic/`, PostgreSQL, Redis |
| Browser and external tools | Operate isolated browsers, the user’s local browser, connectors, memory, files, and sandbox tools | `backend/app/tools/`, `extension/onsite48-bridge/` |

The high-level request path is:

```mermaid
flowchart LR
    User[User] --> Chat[Next.js ChatInterface]
    Chat -->|REST authentication and resources| API[FastAPI API]
    Chat -->|Authenticated WebSocket| Realtime[Realtime gateway]
    API --> Auth[JWT and organization authorization]
    Realtime --> Auth
    Realtime --> Graph[LangGraph task graph]
    Worker[Proactive worker] --> Graph

    Graph --> Router[DomainRouter]
    Graph --> Planner[Planner]
    Graph --> Executor[ExecutorNode]
    Graph --> Verifier[Verifier]

    Planner --> ModelRouter[Model router]
    Router --> ModelRouter
    Verifier --> ModelRouter
    Executor --> Registry[ToolRegistry]

    Registry --> Web[Web search and research]
    Registry --> Computer[Isolated agent computer]
    Registry --> Bridge[Onsite48 Bridge service]
    Registry --> Connectors[OAuth and MCP connectors]
    Registry --> Files[Files, reports, and sandbox]
    Registry --> Memory[Memory retrieval]

    Graph --> Checkpoints[(Redis checkpoints)]
    API --> Database[(PostgreSQL)]
    Graph --> Database
    Memory --> Vectors[(Qdrant and graph memory)]
    Bridge <--> Extension[Chrome Onsite48 Bridge]