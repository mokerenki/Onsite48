# Onsite48 R7 Deployment Guide

This guide deploys the verified Onsite48 R7 archive on a Docker-capable Linux server. The supplied Compose stack runs PostgreSQL, Redis, Qdrant, the FastAPI backend, the proactive worker, the Next.js frontend, and Nginx. It is intended for a staging or production host where the application is served from a public HTTPS origin.

> **Important:** Keep the `.env` file outside source control and outside any archive you share. This guide uses placeholders only. Never paste API keys, OAuth secrets, JWT secrets, connector-encryption keys, database passwords, refresh tokens, or browser session data into tickets, logs, screenshots, or chat.

## 1. Deployment architecture

The public browser connects to Nginx on ports 80/443. Nginx routes the interface to the frontend and API/WebSocket traffic to the backend. PostgreSQL, Redis, and Qdrant remain private on the Docker network and must not be published to the Internet.

| Service | Function | Publicly exposed by Compose | Persistent data |
|---|---|---:|---|
| `postgres` | Durable users, tasks, events, connectors, approvals, and schedules | No | `postgres_data` |
| `redis` | Queues, WebSocket coordination, worker heartbeat, and persistence | No | `redis_data` |
| `qdrant` | Vector/search storage | No | `qdrant_data` |
| `backend` | FastAPI API, task runtime, migrations, connector and browser-operator coordination | No; internal port 8000 | `onsite48_kuzu`, `onsite48_sandbox` |
| `proactive-worker` | Scheduled and heartbeat-driven autonomous work | No | Uses shared services |
| `frontend` | Next.js application | No; internal port 3000 | None |
| `nginx` | Public reverse proxy | Port 80; terminate TLS in front of it for production | None |

## 2. Prerequisites

Use a dedicated Linux server with a current Docker Engine and the Docker Compose v2 plugin. Allocate enough memory for the backend, browser/sandbox features, Qdrant, and the model workload; the Compose resource limits reserve approximately 5–6 GB across the application services before PostgreSQL and host overhead are considered. A staging deployment should use separate OAuth applications and provider accounts from production.

Install or verify Docker:

```bash
docker --version
docker compose version