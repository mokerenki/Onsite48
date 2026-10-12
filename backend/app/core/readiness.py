"""Per-feature readiness, so one dead dependency does not take out the rest.

`/api/health/detailed` folded every dependency into a single boolean. Anything
that made it false returned 503 for the whole process, and a load balancer
reacting to 503 by removing the instance would then stop serving chat, task
submission and auth along with the one feature that was actually broken.

The distinctions this module makes:

* **Required** features gate readiness. Without the database or Redis the
  process cannot serve a request at all, so 503 is correct and the instance
  should leave the pool.
* **Optional** features are reported but never gate. A Qdrant outage degrades
  memory recall; the agent still plans, still executes, still answers. The
  response says so, so an operator can see the degradation without paying for it
  with an outage.
* **Disabled** is not **failed**. An optional feature that was deliberately
  turned off must not appear as a fault, or the alert is noise forever and gets
  ignored, which is how the real outages get ignored too.

The classification itself is pure so it can be tested without a database, Redis
or a running server.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

OK = "ok"
FAILED = "error"
DISABLED = "disabled"
WARMING = "warming"

#: Features without which the process cannot serve any request.
REQUIRED_FEATURES = frozenset({"database", "redis", "migrations"})


@dataclass(frozen=True)
class FeatureCheck:
    name: str
    status: str
    required: bool
    detail: str = ""

    @property
    def ok(self) -> bool:
        """Disabled and warming are not failures."""
        return self.status in (OK, DISABLED, WARMING)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "required": self.required,
            **({"detail": self.detail} if self.detail else {}),
        }


def is_required(name: str) -> bool:
    return name in REQUIRED_FEATURES


def failed_required(checks: Iterable[FeatureCheck]) -> list[str]:
    return sorted(c.name for c in checks if c.required and c.status == FAILED)


def degraded_optional(checks: Iterable[FeatureCheck]) -> list[str]:
    return sorted(c.name for c in checks if not c.required and c.status == FAILED)


def ready(checks: Iterable[FeatureCheck]) -> bool:
    """Ready when no required feature has failed.

    Optional failures and warming features do not gate: the response reports
    them under `degraded` so they are visible without being fatal.
    """
    return not failed_required(checks)


def readiness_payload(
    checks: Iterable[FeatureCheck],
    *,
    environment: str,
) -> dict[str, Any]:
    materialised = list(checks)
    return {
        "status": "ok" if ready(materialised) else "degraded",
        "service": "onsite48-api",
        "environment": environment,
        "ready": ready(materialised),
        "failed_required": failed_required(materialised),
        "degraded_optional": degraded_optional(materialised),
        "checks": {c.name: c.as_dict() for c in materialised},
    }


def from_mapping(
    raw: Mapping[str, Mapping[str, Any]], *, required: Iterable[str] | None = None
) -> list[FeatureCheck]:
    """Build checks from a plain dict, for callers that already probed."""
    required_names = set(REQUIRED_FEATURES if required is None else required)
    return [
        FeatureCheck(
            name=name,
            status=str(value.get("status", FAILED)),
            required=bool(value.get("required", name in required_names)),
            detail=str(value.get("detail", "")),
        )
        for name, value in raw.items()
    ]