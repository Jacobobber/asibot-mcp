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
    ms365_tenant_id: str = ""
    ms365_client_id: str = ""
    sharepoint_site_url: str = ""  # e.g., "company.sharepoint.com"

    # GitHub OAuth (device code flow — zero user input)
    github_client_id: str = ""  # GitHub OAuth App client ID

    # Google OAuth (device code flow — zero user input)
    google_client_id: str = ""
    google_client_secret: str = ""

    # Business defaults — admin sets once, users never need to provide these
    github_org: str = ""  # e.g., "mycompany"
    atlassian_domain: str = ""  # e.g., "mycompany.atlassian.net"
    zendesk_subdomain: str = ""  # e.g., "mycompany"
    salesforce_instance_url: str = ""  # e.g., "https://mycompany.my.salesforce.com"
    sharefile_subdomain: str = ""  # e.g., "mycompany"
    sap_base_url: str = ""  # e.g., "https://api.sap.mycompany.com"
    roboflow_workspace: str = ""  # e.g., "mycompany"

    @property
    def users_dir(self) -> Path:
        return self.data_dir / "users"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.users_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
