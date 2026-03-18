"""Asibot configuration. All data stored under ~/.asibot/."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # Database / sessions
    db_pool_size: int = 10
    session_ttl: int = 3600  # seconds

    # Microsoft SSO (delegated auth via device code flow — used by all MS365 connectors)
    ms365_tenant_id: str = ""
    ms365_client_id: str = ""
    sharepoint_site_url: str = ""  # e.g., "company.sharepoint.com"

    # GitHub OAuth (device code flow — zero user input)
    github_client_id: str = ""  # GitHub OAuth App client ID

    # Google OAuth (device code flow — zero user input)
    google_client_id: str = ""
    google_client_secret: str = ""

    # Concurrency limits
    max_concurrent_requests: int = 200  # Global cap on simultaneous tool calls
    max_concurrent_per_user: int = 10  # Per-user concurrent tool call limit
    max_concurrent_per_service: int = 50  # Per-external-service concurrent call limit
    max_concurrent_setups: int = 100  # Max concurrent device-code polling tasks

    # Business defaults — admin sets once, users never need to provide these
    github_org: str = ""  # e.g., "mycompany"
    atlassian_domain: str = ""  # e.g., "mycompany.atlassian.net"
    zendesk_subdomain: str = ""  # e.g., "mycompany"
    salesforce_instance_url: str = ""  # e.g., "https://mycompany.my.salesforce.com"
    sharefile_subdomain: str = ""  # e.g., "mycompany"
    sap_base_url: str = ""  # e.g., "https://api.sap.mycompany.com"
    roboflow_workspace: str = ""  # e.g., "mycompany"

    # Database
    db_path: Path | None = None  # default: data_dir / "asibot.db"

    # Connection pool
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
    session_ttl: int = 3600
    session_cache_size: int = 1000

    # Audit
    audit_retention_days: int = 90

    @property
    def users_dir(self) -> Path:
        return self.data_dir / "users"

    @property
    def resolved_db_path(self) -> Path:
        return self.db_path if self.db_path is not None else self.data_dir / "asibot.db"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.users_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
