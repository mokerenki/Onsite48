# Onsite48 Production Package — Release Notes

**Release date:** 12 August 2026  
**Audience:** Operators deploying Onsite48 with Docker Desktop on Windows  
**Author:** Manus AI

## Release Summary

This release turns the existing Onsite48 codebase into a **Marketing Operations agent platform foundation**. It provides organization-scoped identity, durable task conversations, approval-gated external actions, a connector marketplace, proactive briefing workflows, an isolated task-scoped browser computer, authenticated attachments, and production operations controls.

The product is intentionally honest about its maturity. Gmail, Slack, and GitHub have provider-specific runtime tools. HubSpot, Google Analytics, Meta Ads, Mailchimp, and Brand24 are marketplace contracts and onboarding paths rather than completed live adapters. The Marketing Operations workspace can safely display signals, generate briefings, and collect approval decisions; it does not claim autonomous external marketing execution.

| Area | Included in this release |
|---|---|
| Identity and tenancy | Local registration/login, Google and GitHub sign-in, JWT access/refresh sessions, organization-scoped records, and OAuth diagnostics. |
| Agent task experience | Durable task threads, same-thread follow-up messages, task history, pause/resume/cancel, and approval gates for irreversible tool actions. |
| Connector marketplace | Versioned manifests, connection lifecycle, encrypted credential storage, GitHub/Gmail/Slack provider adapters, and contract-ready marketing providers. |
| Marketing Operations | Signal feed, approval inbox, manual and scheduled morning briefings, and schedule-recovery state in the UI. |
| Agent computer | Per-task isolated Playwright contexts, private-network URL blocking, browser snapshot/read/inspect, and approval-gated click/fill tools. |
| Attachments | Authenticated, user-isolated upload store with text extraction and chat-composer attachment support. |
| Reliability | Per-subscription transaction isolation, bounded exponential retry, five-failure dead-letter state, deliberate recovery action, and worker heartbeat. |
| Deployment operations | Dedicated worker container, restart policies, resource limits, log rotation, liveness/readiness/metrics endpoints, backup and restore runbook. |

## Windows Docker Desktop Deployment

Copy `.env.example` to `.env` in PowerShell, generate the required secrets locally, and enter provider credentials only in that file. The application is served at `http://localhost`; use this origin for `FRONTEND_URL` and `CORS_ORIGINS`.

```powershell
Copy-Item .env.example .env
# Edit .env in a local editor and set the required placeholders.
docker compose config
docker compose up --build -d
docker compose ps