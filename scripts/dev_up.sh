#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example — fill secrets before live runs."
fi
docker compose up -d postgres redis qdrant
echo "Infra up. Start backend: cd backend && pip install -e '.[dev]' && uvicorn app.main:app --reload"
echo "Start frontend: cd frontend && npm install && npm run dev"
