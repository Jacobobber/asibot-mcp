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
    chroma_collection_name: str = "documents"
    chunk_size: int = 1000
    chunk_overlap: int = 200
    default_top_k: int = 5

    # Server
    transport: str = "stdio"  # "stdio" or "streamable-http"
    host: str = "0.0.0.0"
    port: int = 8080

    # SharePoint (Microsoft Graph API — delegated auth via device code flow)
    sharepoint_tenant_id: str = ""
    sharepoint_client_id: str = ""
    sharepoint_site_url: str = ""  # e.g., "autonomoussolutions.sharepoint.com"

    # Notion
    notion_token: str = ""

    # ZenDesk
    zendesk_subdomain: str = ""
    zendesk_email: str = ""
    zendesk_api_token: str = ""

    # Atlassian (Jira + Confluence)
    atlassian_email: str = ""
    atlassian_api_token: str = ""
    atlassian_domain: str = ""

    # GitHub
    github_token: str = ""
    github_org: str = ""

    # HubSpot
    hubspot_token: str = ""

    # Figma
    figma_token: str = ""

    # Salesforce
    salesforce_instance_url: str = ""  # e.g., "https://asirobots.my.salesforce.com"
    salesforce_token: str = ""

    # Google Workspace
    google_token: str = ""

    # Zapier NLA
    zapier_api_key: str = ""

    # Adobe Sign
    adobe_sign_token: str = ""

    # RingCentral
    ringcentral_token: str = ""

    # Roboflow
    roboflow_api_key: str = ""
    roboflow_workspace: str = ""

    # Smartsheet
    smartsheet_token: str = ""

    # Zoom
    zoom_account_id: str = ""
    zoom_client_id: str = ""
    zoom_client_secret: str = ""

    # Concur
    concur_token: str = ""

    # Paylocity
    paylocity_client_id: str = ""
    paylocity_client_secret: str = ""
    paylocity_company_id: str = ""

    # Citrix ShareFile
    sharefile_token: str = ""
    sharefile_subdomain: str = ""

    # SAP
    sap_base_url: str = ""
    sap_token: str = ""

    # LinkSquares
    linksquares_token: str = ""

    @property
    def chroma_dir(self) -> Path:
        return self.data_dir / "chroma"

    @property
    def users_dir(self) -> Path:
        return self.data_dir / "users"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.chroma_dir.mkdir(parents=True, exist_ok=True)
        self.users_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
