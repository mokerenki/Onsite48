"""Central, environment-driven configuration for Onsite48.

No production secret has a fallback. Startup validates all settings required by
the active deployment mode, while local development remains easy to boot.
"""

from __future__ import annotations

import app.core.env_loader  # noqa: F401  -- must precede any os.getenv() call

import base64
import os
from dataclasses import dataclass, field
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

# The set of environment names that mean "deployed", not a local checkout. It is
# defined once because it was previously written out twice with two different
# contents, which is how `ENVIRONMENT=prod` came to demand a DATABASE_URL while
# still accepting the default JWT secret.
DEPLOYED_ENVIRONMENTS = frozenset({"production", "prod", "staging"})


def normalize_environment(raw: str | None) -> str:
    """Reduce an environment name to the form every check compares against.

    Surrounding whitespace is stripped as well as case folded. A deployment
    that sets `ENVIRONMENT=" production "` is production; treating that as
    development silently accepts development secrets in production. Padding is
    easy to produce and hard to see: a YAML block scalar, a Helm value, or a
    quoted line in a `.env` file all keep the space.
    """
    return (raw or "development").strip().lower()


def is_deployed(environment: str | None) -> bool:
    """True when this environment must not fall back to any default secret."""
    return normalize_environment(environment) in DEPLOYED_ENVIRONMENTS


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    api_key: str
    base_url: Optional[str] = None
    model: str = "default"
    temperature: float = 0.3
    max_tokens: int = 4096
    timeout: float = 120.0
    # None means pricing is not verified for this provider/model; zero is a
    # deliberate verified free route and must not be conflated with unknown.
    input_cost_per_1m: Optional[float] = None
    output_cost_per_1m: Optional[float] = None


@dataclass(frozen=True)
class DBConfig:
    host: str = "localhost"
    port: int = 5432
    database: str = "onsite48"
    user: str = "onsite48"
    password: str = "onsite48"

    @property
    def async_url(self) -> str:
        return f"postgresql+asyncpg://{self.user}:{self.password}@{self.host}:{self.port}/{self.database}"


@dataclass(frozen=True)
class MemoryConfig:
    cognee_enabled: bool = False
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    kuzu_db_path: str = "/tmp/onsite48/kuzu.db"
    graphiti_enabled: bool = False
    redis_url: str = "redis://localhost:6379/0"


@dataclass(frozen=True)
class SandboxConfig:
    enabled: bool = False
    runtime: str = "docker"
    sandbox_root: str = "/tmp/onsite48/sandbox"
    k8s_namespace: str = "onsite48-sandboxes"
    max_concurrent: int = 5
    e2b_api_key: str = ""


@dataclass(frozen=True)
class SecurityConfig:
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30
    credential_encryption_key: str = ""
    cookie_secure: bool = False
    cookie_domain: Optional[str] = None
    rbac_enabled: bool = True

    def validate(self, environment: str) -> None:
        # Membership in the shared deployed set, not `== "production"`. The
        # previous equality test skipped validation for `prod` and `staging`
        # and for any value with surrounding whitespace, while
        # `_resolve_database_url` already refused to guess for all of those.
        if is_deployed(environment):
            resolved = normalize_environment(environment)
            unsafe_values = {"", "change-me", "change-me-in-production", "replace-with-a-long-random-secret"}
            if self.jwt_secret in unsafe_values or len(self.jwt_secret) < 32:
                raise RuntimeError(
                    f"JWT_SECRET must be a unique secret of at least 32 characters "
                    f"when ENVIRONMENT resolves to {resolved!r}."
                )
            if not self.credential_encryption_key:
                raise RuntimeError(
                    f"CONNECTOR_ENCRYPTION_KEY is required when ENVIRONMENT "
                    f"resolves to {resolved!r}."
                )
        if self.credential_encryption_key:
            try:
                decoded = base64.urlsafe_b64decode(self.credential_encryption_key.encode())
                if len(decoded) != 32:
                    raise ValueError("key length")
            except Exception as exc:
                raise RuntimeError("CONNECTOR_ENCRYPTION_KEY must be a valid Fernet key.") from exc


def _resolve_database_url() -> str:
    """Resolve DATABASE_URL, refusing to guess in a deployed environment.

    The previous fallback was a hardcoded `onsite48:onsite48@localhost` URL applied
    everywhere. In production that means an environment that failed to inject
    its database setting starts up and quietly talks to a local database
    instead of erroring, which is how you get writes against the wrong data.
    `docker-compose.yml` already fails closed via
    `${POSTGRES_PASSWORD:?Set POSTGRES_PASSWORD in .env}`; this restores the
    same intent on the application side.

    Development keeps a default so a local checkout runs without ceremony.
    """
    configured = os.getenv("DATABASE_URL", "").strip()
    if configured:
        return configured

    environment = normalize_environment(os.getenv("ENVIRONMENT"))
    if is_deployed(environment):
        raise RuntimeError(
            "DATABASE_URL is not set. Refusing to start with a default database "
            "URL: in a deployed environment a missing setting silently points "
            "the application at a local database rather than failing. Set "
            "DATABASE_URL explicitly."
        )
    return "postgresql+asyncpg://onsite48:onsite48@localhost:5432/onsite48"


@dataclass(frozen=True)
class AetherConfig:
    environment: str = normalize_environment(os.getenv("ENVIRONMENT"))
    app_name: str = os.getenv("APP_NAME", "Onsite48")
    # Docker Desktop exposes the public app through Nginx on port 80.
    # Avoid a fallback to the internal Next.js development port, which causes
    # OAuth providers to redirect users to an unserved localhost:3000 callback.
    frontend_url: str = os.getenv("FRONTEND_URL", "http://localhost").rstrip("/")
    cors_origins: tuple[str, ...] = tuple(
        origin.strip()
        for origin in os.getenv("CORS_ORIGINS", "http://localhost").split(",")
        if origin.strip()
    )
    database_url: str = field(default_factory=_resolve_database_url)
    kimi_k2: ModelConfig = field(default_factory=lambda: ModelConfig(
        provider="kimi",
        api_key=os.getenv("KIMI_API_KEY", "").strip(),
        base_url=os.getenv("KIMI_BASE_URL", "https://api.moonshot.ai/v1").strip(),
        model=os.getenv("KIMI_MODEL", os.getenv("KIMI_K2_MODEL", "kimi-k2.6")).strip(),
        temperature=1.0,
        max_tokens=4096,
        input_cost_per_1m=0.56,
        output_cost_per_1m=2.24,
    ))
    deepseek: ModelConfig = field(default_factory=lambda: ModelConfig(
        provider="deepseek",
        api_key=os.getenv("DEEPSEEK_API_KEY", "").strip(),
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").strip(),
        model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip(),
        temperature=0.1,
        # deepseek-chat supports up to 8192 output tokens. A larger
        # budget lets a single tool call carry a full file write;
        # 4096 truncated oversized arguments mid-JSON.
        max_tokens=int(os.getenv("DEEPSEEK_MAX_TOKENS", "8192")),
        input_cost_per_1m=0.028,
        output_cost_per_1m=0.028,
    ))
    kimi_k3: ModelConfig = field(default_factory=lambda: ModelConfig(
        provider="nvidia",
        api_key=os.getenv("NVIDIA_API_KEY", "").strip(),
        base_url=os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1").strip(),
        model=os.getenv("NVIDIA_MODEL", "moonshotai/kimi-k3").strip(),
        temperature=1.0,
        max_tokens=16384,
        input_cost_per_1m=float(os.getenv("NVIDIA_INPUT_COST_PER_1M") or "0") or None,
        output_cost_per_1m=float(os.getenv("NVIDIA_OUTPUT_COST_PER_1M") or "0") or None,
    ))
    planner: ModelConfig = field(default_factory=lambda: ModelConfig(
        provider=os.getenv("PLANNER_PROVIDER", "openai" if os.getenv("OPENAI_API_KEY") else "nvidia").strip(),
        api_key=os.getenv("PLANNER_API_KEY", os.getenv("OPENAI_API_KEY", "")).strip(),
        base_url=os.getenv("PLANNER_BASE_URL", os.getenv("OPENAI_API_BASE", "https://integrate.api.nvidia.com/v1")).strip(),
        model=os.getenv("PLANNER_MODEL", "claude-opus-4-7" if os.getenv("OPENAI_API_KEY") else "nvidia/nemotron-3.5-lightning-30b-a3b").strip(),
        temperature=0.2,
        max_tokens=16384,
        input_cost_per_1m=float(os.getenv("PLANNER_INPUT_COST_PER_1M") or "0") or None,
        output_cost_per_1m=float(os.getenv("PLANNER_OUTPUT_COST_PER_1M") or "0") or None,
    ))
    db: DBConfig = field(default_factory=lambda: DBConfig(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        database=os.getenv("POSTGRES_DB", "onsite48"),
        user=os.getenv("POSTGRES_USER", "onsite48"),
        password=os.getenv("POSTGRES_PASSWORD", "onsite48"),
    ))
    memory: MemoryConfig = field(default_factory=lambda: MemoryConfig(
        cognee_enabled=os.getenv("COGNEE_ENABLED", "false").lower() == "true",
        qdrant_host=os.getenv("QDRANT_HOST", "localhost"),
        qdrant_port=int(os.getenv("QDRANT_PORT", "6333")),
        kuzu_db_path=os.getenv("KUZU_DB_PATH", "/tmp/onsite48/kuzu.db"),
        graphiti_enabled=os.getenv("GRAPHITI_ENABLED", "false").lower() == "true",
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
    ))
    sandbox: SandboxConfig = field(default_factory=lambda: SandboxConfig(
        enabled=os.getenv("SANDBOX_ENABLED", "false").lower() == "true",
        runtime=os.getenv("SANDBOX_RUNTIME", "docker"),
        sandbox_root=os.getenv("SANDBOX_ROOT", "/tmp/onsite48/sandbox"),
        k8s_namespace=os.getenv("K8S_NAMESPACE", "onsite48-sandboxes"),
        max_concurrent=int(os.getenv("SANDBOX_MAX_CONCURRENT", "5")),
        e2b_api_key=os.getenv("E2B_API_KEY", "").strip(),
    ))
    security: SecurityConfig = field(default_factory=lambda: SecurityConfig(
        jwt_secret=os.getenv("JWT_SECRET", "change-me-in-development"),
        jwt_algorithm=os.getenv("JWT_ALGORITHM", "HS256"),
        access_token_expire_minutes=int(os.getenv("JWT_ACCESS_EXPIRE_MINUTES", "15")),
        refresh_token_expire_days=int(os.getenv("JWT_REFRESH_EXPIRE_DAYS", "30")),
        credential_encryption_key=os.getenv("CONNECTOR_ENCRYPTION_KEY", ""),
        cookie_secure=os.getenv("COOKIE_SECURE", "false").lower() == "true",
        cookie_domain=os.getenv("COOKIE_DOMAIN") or None,
        rbac_enabled=os.getenv("RBAC_ENABLED", "true").lower() == "true",
    ))
    ws_host: str = os.getenv("WS_HOST", "0.0.0.0")
    ws_port: int = int(os.getenv("WS_PORT", "8000"))
    max_tool_retries: int = int(os.getenv("MAX_TOOL_RETRIES", "3"))
    tool_timeout: float = float(os.getenv("TOOL_TIMEOUT", "60"))
    max_planning_steps: int = int(os.getenv("MAX_PLANNING_STEPS", "10"))
    planning_model_override: str | None = os.getenv("PLANNING_MODEL_OVERRIDE") or None

    def validate(self) -> None:
        self.security.validate(self.environment)
        _validate_tls_transport(
            environment=self.environment,
            cookie_secure=self.security.cookie_secure,
            cors_origins=self.cors_origins,
        )


def _validate_tls_transport(
    *, environment: str, cookie_secure: bool, cors_origins: tuple[str, ...]
) -> None:
    """Refuse to start a deployed environment that can be used over plaintext.

    Two distinct failures are covered, and both are silent in the sense that
    nothing errors at boot without this check:

    * ``COOKIE_SECURE`` defaults to false, so a deployed deployment that never
      set it issues session cookies that any network observer can read and
      replay. The application works perfectly; it is just not private.
    * ``CORS_ORIGINS`` defaults to ``http://localhost`` and nothing required
      https, so a deployed origin could be plain http and a browser origin of
      plain http would be accepted alongside it.

    Development is untouched: an http origin is normal on a laptop, and this is
    the only place that would reject one.
    """
    if not is_deployed(environment):
        return
    resolved = normalize_environment(environment)

    if not cookie_secure:
        raise RuntimeError(
            f"COOKIE_SECURE must be true when ENVIRONMENT resolves to "
            f"{resolved!r}. With it false, session cookies are sent without the "
            f"Secure attribute and can be read and replayed over plaintext HTTP."
        )

    insecure = [origin for origin in cors_origins if not origin.startswith("https://")]
    if insecure:
        raise RuntimeError(
            f"every CORS_ORIGINS entry must use https:// when ENVIRONMENT "
            f"resolves to {resolved!r}; these are not: {sorted(insecure)}."
        )


CONFIG = AetherConfig()


class Settings(BaseSettings):
    """Compatibility configuration for components that use Pydantic settings."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    database_url: str = CONFIG.database_url
    redis_url: str = CONFIG.memory.redis_url
    kimi_api_key: str = ""
    deepseek_api_key: str = ""


settings = Settings()