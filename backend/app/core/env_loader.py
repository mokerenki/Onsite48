"""Load .env at the very top of the import graph.

This module MUST be imported before any other app modules so that
``os.getenv()`` calls inside ``config.py`` and ``model_router_kimi_deepseek.py``
see missing values from the project's .env file without replacing values injected
by the deployment environment, such as Compose service hostnames.

Usage: ``import app.core.env_loader  # noqa: F401`` at the top of any entrypoint.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ENV_PATH = _PROJECT_ROOT / ".env"

if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH, override=False)
else:
    load_dotenv(override=False)