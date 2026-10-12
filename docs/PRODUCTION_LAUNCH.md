# Onsite48 Production Launch Guide

## What This Implementation Delivers

This upgrade turns the prototype into an authenticated agent workspace. It introduces user and organization records, short-lived access tokens, refresh-token sessions, user-scoped WebSocket task execution, persistent task history, encrypted connector credentials, OAuth connection flows, approval-gated external write tools, and an application workspace with sign-in, connector, and task-history experiences.

> **Release boundary:** The product now supports a credible first connector cohort—GitHub, Gmail, and Slack—rather than claiming that every marketing-page integration is already live. Other catalog connectors can be connected through their configured OAuth or API-key flows, but tools should only be advertised as executable after their provider-specific tool adapter has been implemented and tested.

| Product area | Included in this release | Operator action before public launch |
|---|---|---|
| Identity | Email/password sign-up, sign-in, session refresh, logout, Google and GitHub sign-in code paths | Set production secrets and register Google/GitHub OAuth applications. |
| Workspace security | User-scoped REST APIs, authenticated WebSockets, organization ownership checks | Enforce HTTPS and do not expose PostgreSQL, Redis, or Qdrant ports publicly. |
| Tasks | Durable task records, ordered events, pause, resume, cancellation, history, and confirmation records | Configure models, sandbox runtime, and capacity limits. |
| Connectors | Encrypted credentials, OAuth state and PKCE, GitHub/Gmail/Slack tools, confirmation before GitHub issues, Gmail sends, and Slack sends | Provide OAuth client IDs/secrets; enable and review the requested provider scopes. |
| Interface | Sign-in, account creation, workspace, connector marketplace, task history | Configure `FRONTEND_URL`, deploy behind TLS, and test the entire user journey. |

## Deployment Choices

The repository’s current agent runtime includes Python services, Redis, PostgreSQL, Qdrant, browser automation, and optional sandbox execution. That makes a Docker-capable server the practical path for the delivered feature set. A lighter managed deployment can work only after the sandbox and browser subsystems have been separated or replaced.

| Approach | Tradeoffs | Cost | Setup complexity |
|---|---|---:|---|
| **Managed web hosting for the interface, with a managed database and queue** | Lowest operational burden, but the present Python/Docker browser-sandbox runtime must be redesigned or moved to a separate worker before all features work. | Varies by provider and usage. | Medium to high because the current stack must be split. |
| **Docker-capable production server for the supplied Compose stack** | Runs the application as delivered, including the API, data services, and optional isolated-agent runtime. You own monitoring, backups, and TLS configuration. | Varies by hosting provider and server size. | Medium; this is the recommended launch path for the current repository. |

## 1. Prepare the Production Host

Use a dedicated Linux server with Docker Engine and Docker Compose installed. Point a subdomain such as `app.onsite48.world` to the server. Keep the marketing landing page (`onsite48.world`) separate from the signed-in product workspace (`app.onsite48.world`).

Clone this branch onto the server, then create the runtime configuration:

```bash
git clone --branch branch2 https://github.com/Synthai-official/Onsite48.git onsite48
cd onsite48
cp .env.example .env