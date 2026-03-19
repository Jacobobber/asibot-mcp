"""Asibot configuration with validation. All data stored under ~/.asibot/."""

import logging
import warnings
from pathlib import Path

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ASIBOT_",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    data_dir: Path = Path.home() / ".asibot"

    # Server
    transport: str = "stdio"  # "stdio" or "streamable-http"
    host: str = "0.0.0.0"
    port: int = 8080
    allow_insecure_http: bool = False  # Must be True to run HTTP without TLS

    # Dashboard
    dashboard_host: str = "0.0.0.0"
    dashboard_port: int = 8081
    dashboard_enabled: bool = True
    dashboard_bearer_token: str = ""  # optional Bearer token for dashboard auth
    dashboard_token_ttl: int = 86400  # per-user token TTL in seconds (default 24h)
    dashboard_min_role: str = "user"  # minimum role to access dashboard ("user" or "admin")

    # Azure AD role sync
    admin_group_id: str = ""  # Azure AD security group ID — members get admin role

    # Database / sessions
    db_pool_size: int = 10
    database_url: str = ""  # e.g., "postgresql://user:pass@host:5432/asibot"
    database_read_url: str = ""  # Read replica URL (optional, falls back to database_url)
    postgres_password: str = ""  # Extracted from database_url at runtime, or set directly
    # Pool sizing: 100 max supports ~1000 concurrent users assuming 10:1
    # user-to-connection ratio with short-lived queries. Min 10 keeps warm
    # connections ready for burst traffic without cold-start latency.
    pg_pool_min_size: int = 10
    pg_pool_max_size: int = 100
    session_ttl: int = 3600  # seconds (sliding window — inactivity timeout)
    absolute_session_ttl: int = 28800  # seconds (hard cap — max session lifetime regardless of activity)

    # Session / cache backend
    session_backend: str = "memory"  # "memory" or "redis"
    redis_url: str = ""  # e.g., "redis://localhost:6379/0"

    # Microsoft SSO (delegated auth via device code flow — used by all MS365 connectors)
    ms365_tenant_id: str = ""
    ms365_client_id: str = ""
    sharepoint_site_url: str = ""  # e.g., "company.sharepoint.com"

    # GitHub OAuth (device code flow — zero user input)
    github_client_id: str = ""  # GitHub OAuth App client ID

    # Google OAuth (device code flow — zero user input)
    google_client_id: str = ""
    google_client_secret: str = ""

    # Concurrency limits (HTTP transport defaults — scaled for 1000+ users)
    max_concurrent_requests: int = 2000  # Global cap on simultaneous tool calls
    max_concurrent_per_user: int = 10  # Per-user concurrent tool call limit
    max_concurrent_per_service: int = 200  # Per-external-service concurrent call limit
    max_concurrent_setups: int = 100  # Max concurrent device-code polling tasks

    # Stdio transport overrides (single-user, lower limits)
    max_concurrent_requests_stdio: int = 50
    max_concurrent_per_user_stdio: int = 5
    max_concurrent_per_service_stdio: int = 20

    # Session / Redis
    session_backend: str = "memory"  # "memory" or "redis"
    redis_url: str = ""  # e.g., "redis://localhost:6379/0"

    # Business defaults — admin sets once, users never need to provide these
    github_org: str = ""  # e.g., "mycompany"
    atlassian_domain: str = ""  # e.g., "mycompany.atlassian.net"
    zendesk_subdomain: str = ""  # e.g., "mycompany"
    salesforce_instance_url: str = ""  # e.g., "https://mycompany.my.salesforce.com"
    sharefile_subdomain: str = ""  # e.g., "mycompany"
    sap_base_url: str = ""  # e.g., "https://api.sap.mycompany.com"
    roboflow_workspace: str = ""  # e.g., "mycompany"

    # Database (SQLite fallback)
    db_path: Path | None = None  # default: data_dir / "asibot.db"

    # HTTP connection pool
    pool_max_connections: int = 20
    pool_max_clients: int = 200

    # Circuit breaker
    circuit_failure_threshold: int = 5
    circuit_recovery_timeout: float = 60.0

    # Metrics
    metrics_enabled: bool = True
    metrics_host: str = "127.0.0.1"  # localhost-only by default; use reverse proxy to expose
    metrics_port: int = 9090
    metrics_bearer_token: str = ""  # optional Bearer token for metrics endpoint auth

    # Sessions
    session_cache_size: int = 1000

    # Audit
    audit_retention_days: int = 365

    # Global per-service rate limits (requests per minute)
    global_rate_limit_default: int = 200
    global_rate_limits: dict[str, int] = {}  # e.g., {"github": 80, "salesforce": 100}

    # Per-user rate limiting (requests per minute per user per service)
    per_user_rate_limit_default: int = 30

    # Key Management — optional external KMS (falls back to local file)
    kms_provider: str = ""  # "aws", "vault", or "" (local file)
    kms_key_id: str = ""  # AWS KMS key ARN or Vault path
    vault_addr: str = ""  # HashiCorp Vault address
    vault_token: str = ""  # Vault auth token (use env var in production)

    # Retry / backoff for connector API calls
    max_retries: int = 3
    retry_base_delay: float = 1.0

    # ---- Field Validators ----

    @field_validator("transport")
    @classmethod
    def _validate_transport(cls, v: str) -> str:
        allowed = ("stdio", "streamable-http")
        if v not in allowed:
            raise ValueError(f"transport must be one of {allowed}, got {v!r}")
        return v

    @field_validator("kms_provider")
    @classmethod
    def _validate_kms_provider(cls, v: str) -> str:
        allowed = ("", "aws", "vault")
        if v not in allowed:
            raise ValueError(f"kms_provider must be one of {allowed}, got {v!r}")
        return v

    # ---- Model Validator (cross-field checks) ----

    @model_validator(mode="after")
    def _validate_settings(self) -> "Settings":
        errors: list[str] = []

        # pg_pool_min_size <= pg_pool_max_size
        if self.pg_pool_min_size > self.pg_pool_max_size:
            errors.append(
                f"pg_pool_min_size ({self.pg_pool_min_size}) must be "
                f"<= pg_pool_max_size ({self.pg_pool_max_size})"
            )

        # postgres_password warning for HTTP transport (non-blocking)
        if self.transport == "streamable-http" and not self.postgres_password:
            # Check if password is embedded in database_url
            if self.database_url:
                _has_password = "://" in self.database_url and "@" in self.database_url
                if _has_password:
                    # Extract user:pass part
                    after_scheme = self.database_url.split("://", 1)[1]
                    userinfo = after_scheme.split("@", 1)[0]
                    if ":" in userinfo:
                        pw = userinfo.split(":", 1)[1]
                        if not pw:
                            warnings.warn(
                                "postgres_password is empty and database_url has no password — "
                                "database access may fail in production",
                                UserWarning,
                                stacklevel=2,
                            )
                else:
                    warnings.warn(
                        "postgres_password is empty for HTTP transport — "
                        "database access may fail in production",
                        UserWarning,
                        stacklevel=2,
                    )

        # Rate limit values must be positive
        if self.global_rate_limit_default <= 0:
            errors.append(
                f"global_rate_limit_default must be a positive integer, "
                f"got {self.global_rate_limit_default}"
            )
        if self.per_user_rate_limit_default <= 0:
            errors.append(
                f"per_user_rate_limit_default must be a positive integer, "
                f"got {self.per_user_rate_limit_default}"
            )
        for svc, limit in self.global_rate_limits.items():
            if limit <= 0:
                errors.append(
                    f"global_rate_limits[{svc!r}] must be a positive integer, got {limit}"
                )

        # metrics_port != port (can't bind same port twice)
        if self.metrics_port == self.port:
            errors.append(
                f"metrics_port ({self.metrics_port}) must differ from "
                f"port ({self.port}) — cannot bind the same port twice"
            )

        # session_ttl > 0
        if self.session_ttl <= 0:
            errors.append(f"session_ttl must be > 0, got {self.session_ttl}")

        # absolute_session_ttl > session_ttl
        if self.absolute_session_ttl <= self.session_ttl:
            errors.append(
                f"absolute_session_ttl ({self.absolute_session_ttl}) must be "
                f"> session_ttl ({self.session_ttl})"
            )

        # circuit_failure_threshold > 0
        if self.circuit_failure_threshold <= 0:
            errors.append(
                f"circuit_failure_threshold must be > 0, got {self.circuit_failure_threshold}"
            )

        # circuit_recovery_timeout > 0
        if self.circuit_recovery_timeout <= 0:
            errors.append(
                f"circuit_recovery_timeout must be > 0, got {self.circuit_recovery_timeout}"
            )

        # max_retries >= 0
        if self.max_retries < 0:
            errors.append(f"max_retries must be >= 0, got {self.max_retries}")

        # audit_retention_days > 0
        if self.audit_retention_days <= 0:
            errors.append(
                f"audit_retention_days must be > 0, got {self.audit_retention_days}"
            )

        # KMS provider cross-checks
        if self.kms_provider == "vault" and not self.vault_addr:
            errors.append(
                "kms_provider is 'vault' but vault_addr is not set — "
                "Vault address is required"
            )
        if self.kms_provider == "aws" and not self.kms_key_id:
            errors.append(
                "kms_provider is 'aws' but kms_key_id is not set — "
                "AWS KMS key ARN is required"
            )

        if errors:
            raise ValueError(
                "Invalid configuration:\n  - " + "\n  - ".join(errors)
            )

        return self

    @property
    def effective_concurrency(self) -> dict[str, int]:
        """Return concurrency limits appropriate for the active transport."""
        if self.transport == "stdio":
            return {
                "global": self.max_concurrent_requests_stdio,
                "per_user": self.max_concurrent_per_user_stdio,
                "per_service": self.max_concurrent_per_service_stdio,
            }
        return {
            "global": self.max_concurrent_requests,
            "per_user": self.max_concurrent_per_user,
            "per_service": self.max_concurrent_per_service,
        }

    @property
    def users_dir(self) -> Path:
        return self.data_dir / "users"

    @property
    def resolved_db_path(self) -> Path:
        return self.db_path if self.db_path is not None else self.data_dir / "asibot.db"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.users_dir.mkdir(parents=True, exist_ok=True)

    def validate_for_production(self) -> list[str]:
        """Return a list of production readiness warnings."""
        warns: list[str] = []
        if self.session_backend == "memory" and self.transport == "streamable-http":
            warns.append(
                "session_backend=memory with HTTP transport: S2S tokens and rate limits "
                "will not be shared across replicas. Set ASIBOT_SESSION_BACKEND=redis for production."
            )
        return warns


def validate_for_production(s: Settings) -> list[str]:
    """Return a list of warnings for production-risky configuration.

    These are not hard errors — the server will still start — but they
    indicate settings that are unsafe or under-provisioned for production.
    """
    warns: list[str] = []

    if s.allow_insecure_http:
        warns.append(
            "allow_insecure_http=True — API keys will be transmitted in plaintext. "
            "Use a TLS-terminating reverse proxy in production."
        )

    if s.dashboard_enabled and not s.dashboard_bearer_token:
        warns.append(
            "dashboard_enabled=True but dashboard_bearer_token is empty — "
            "the dashboard is accessible without authentication."
        )

    if not s.metrics_bearer_token:
        warns.append(
            "metrics_bearer_token is empty — the metrics endpoint is unauthenticated. "
            "Set ASIBOT_METRICS_BEARER_TOKEN in production."
        )

    if s.transport == "streamable-http" and s.pg_pool_max_size < 50:
        warns.append(
            f"pg_pool_max_size={s.pg_pool_max_size} is low for HTTP transport. "
            f"Consider >= 50 for 1000+ user deployments."
        )

    return warns


settings = Settings()
