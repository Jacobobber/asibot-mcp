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

    # Microsoft SSO (delegated auth via device code flow — used by all MS365 connectors)
    sharepoint_tenant_id: str = ""
    sharepoint_client_id: str = ""
    sharepoint_site_url: str = ""  # e.g., "autonomoussolutions.sharepoint.com"

    @property
    def users_dir(self) -> Path:
        return self.data_dir / "users"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.users_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
