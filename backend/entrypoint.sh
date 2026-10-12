#!/bin/sh
set -eu

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
  echo "Applying SynthAI database migrations…"
  alembic upgrade head
fi

# Compose may supply an explicit command for a dedicated worker process.
if [ "$#" -gt 0 ]; then
  exec "$@"
fi

exec uvicorn app.main:app --host "${WS_HOST:-0.0.0.0}" --port "${WS_PORT:-8000}"