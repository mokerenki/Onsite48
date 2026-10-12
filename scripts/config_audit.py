from __future__ import annotations

import re
from pathlib import Path

import yaml


def main() -> None:
    compose = yaml.safe_load(Path("docker-compose.yml").read_text())
    assert set(compose["services"]) >= {"postgres", "redis", "qdrant", "backend", "frontend", "nginx"}
    assert compose["services"]["frontend"]["environment"]["BACKEND_URL"] == "http://backend:8000"
    assert compose["services"]["nginx"]["ports"] == ["80:80"]

    env_text = Path(".env").read_text()
    env_keys = {line.split("=", 1)[0] for line in env_text.splitlines() if line and not line.startswith("#") and "=" in line}
    required = {"POSTGRES_PASSWORD", "FRONTEND_URL", "CORS_ORIGINS", "KIMI_API_KEY", "DEEPSEEK_API_KEY", "NVIDIA_API_KEY", "JWT_SECRET", "CONNECTOR_ENCRYPTION_KEY"}
    assert required <= env_keys, sorted(required - env_keys)

    migrations = {}
    for path in sorted(Path("backend/alembic/versions").glob("*.py")):
        text = path.read_text()
        revision = re.search(r"^revision\s*=\s*['\"]([^'\"]+)", text, re.M)
        down = re.search(r"^down_revision\s*=\s*(None|['\"][^'\"]+['\"])", text, re.M)
        if revision:
            migrations[revision.group(1)] = None if not down or down.group(1) == "None" else down.group(1).strip("'\"")
    assert migrations, "no migration revisions found"
    heads = set(migrations) - {value for value in migrations.values() if value}
    assert len(heads) == 1, sorted(heads)

    print(f"services={len(compose['services'])} env_required={len(required)} migrations={len(migrations)} head={next(iter(heads))} config=ok")


if __name__ == "__main__":
    main()