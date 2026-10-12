# Onsite48

**Onsite48** is a specialized autonomous-agent platform for business and marketing operations. It combines a conversational workspace with durable task threads, authenticated tool use, file-aware reasoning, proactive operations, an isolated agent browser, local-browser bridging, shared work management, and a calibrated fast-response path.

> **Operating principle:** simple, low-risk questions receive a fast response; research, private-context work, consequential actions, and multi-step tasks use the full auditable agent workflow.

## Contents

- [What Onsite48 provides](#what-onsite48-provides)
- [Architecture](#architecture)
- [Requirements](#requirements)
- [Local installation with Docker Desktop](#local-installation-with-docker-desktop)
- [Configuration](#configuration)
- [Using Onsite48](#using-onsite48)
- [Response modes and trust model](#response-modes-and-trust-model)
- [Connectors and MCP servers](#connectors-and-mcp-servers)
- [OAuth setup](#oauth-setup)
- [Operations, health, and troubleshooting](#operations-health-and-troubleshooting)
- [Security model](#security-model)
- [Upgrade procedure](#upgrade-procedure)

## What Onsite48 provides

| Area | Capability |
|---|---|
| Identity | Email/password authentication, refresh sessions, Google OAuth, GitHub OAuth, account menu, organization scoping, and JWT-protected API/WebSocket access. |
| Agent conversations | Durable PostgreSQL-backed task threads, follow-up messages in the same thread, transcript restoration, historical-answer repair, task status, and semantic sidebar state. |
| Agent work | LangGraph planning, tool execution, verification, re-planning safeguards, approval gates, costs, background execution, and durable event history. |
| Fast responses | One-model **Quick answer** mode for eligible low-risk requests; direct Tavily search plus one synthesis call for eligible current public lookups; streamed answer drafts. |
| Marketing Operations | A specialized marketing workspace with connectors, proactive insights, campaign-oriented work, and human approval paths. |
| Agent computer | Isolated Playwright-based browsing for public web interaction, navigation, scrolling, pagination, and task-specific browser actions. |
| Onsite48 Bridge | Chrome extension for pairing Onsite48 with a user’s local browser when work requires an existing signed-in session, local network, or `localhost` context. |
| Knowledge | File upload and extraction for TXT, Markdown, CSV, JSON, PDF, DOCX, PPTX, and XLSX; source-library indexing and organization-scoped retrieval. |
| Collaboration | Command Center projects, work items, comments, approvals, notifications, and shared operational context. |
| Automation | Morning briefings, signal monitoring, approval inboxes, managed schedules, and signed event triggers. |
| Output delivery | Table-heavy or large answers are stored as user-isolated downloadable Markdown reports instead of breaking the chat layout. |

## Architecture

Onsite48 is deployed as a Docker Compose application behind Nginx at `http://localhost` by default.

| Service | Responsibility |
|---|---|
| `nginx` | Public reverse proxy on port 80; routes frontend, API, and WebSocket traffic. |
| `frontend` | Next.js 15 / React workspace and chat interface. |
| `backend` | FastAPI API, authentication, WebSocket gateway, task orchestration, connectors, file handling, and agent services. |
| `proactive-worker` | Separate worker for scheduled and signal-driven proactive operations. |
| `postgres` | System of record for users, organizations, task threads, events, approvals, notifications, library records, and work management. |
| `redis` | Cache and asynchronous coordination support. |
| `qdrant` | Vector storage for knowledge and source-library retrieval. |

Persistent Docker volumes protect PostgreSQL, Redis, Qdrant, attachments, and agent runtime state. Do not remove these volumes during a normal upgrade.

## Requirements

A local Windows installation requires Docker Desktop with Compose support and the Linux container engine enabled. Allocate at least **8 GB RAM** to Docker Desktop; more is recommended for browser-based agent work. Port **80** must be available locally unless you deliberately change the Nginx host-port mapping.

Model-provider credentials are configured in `.env`. The supplied Compose configuration validates `KIMI_API_KEY` and `DEEPSEEK_API_KEY`; Nvidia may also be configured as the active route with `NVIDIA_API_KEY`. A Tavily key enables fast public web answers and normal web-search tasks.

## Local installation with Docker Desktop

### 1. Create your environment file

In PowerShell, from the project folder:

```powershell
Copy-Item .env.example .env