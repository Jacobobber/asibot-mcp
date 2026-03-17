"""Per-user credential storage, preferences, and permission enforcement.

Per-user files at ~/.asibot/users/{user_id}/:
  credentials.json  — service credentials
  preferences.json  — per-connector enabled/mode settings
  microsoft_token.json — Microsoft OAuth (managed by microsoft.py)
"""

import json
import logging
from pathlib import Path
from typing import Callable

import httpx

from asibot import user_session

logger = logging.getLogger(__name__)


# --- Credentials ---


def _creds_path(user_id: str) -> Path:
    return user_session.get_user_data_dir(user_id) / "credentials.json"


def _load_creds(user_id: str) -> dict:
    path = _creds_path(user_id)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}
    return {}


def _save_creds(user_id: str, data: dict) -> None:
    path = _creds_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def get_credentials(user_id: str, service: str) -> dict:
    """Get credentials for a service. Returns empty dict if not set."""
    return _load_creds(user_id).get(service, {})


def set_credentials(user_id: str, service: str, creds: dict) -> None:
    """Store credentials for a service."""
    data = _load_creds(user_id)
    data[service] = creds
    _save_creds(user_id, data)
    # Auto-enable the service with read mode on first connect
    prefs = get_service_prefs(user_id, service)
    if not prefs:
        set_service_prefs(user_id, service, enabled=True, mode="read")
    logger.info("Stored %s credentials for user '%s'", service, user_id)


def remove_credentials(user_id: str, service: str) -> None:
    """Remove credentials for a service."""
    data = _load_creds(user_id)
    data.pop(service, None)
    _save_creds(user_id, data)


def list_connected(user_id: str) -> list[str]:
    """List services the user has credentials for."""
    return list(_load_creds(user_id).keys())


# --- Preferences ---


def _prefs_path(user_id: str) -> Path:
    return user_session.get_user_data_dir(user_id) / "preferences.json"


def _load_prefs(user_id: str) -> dict:
    path = _prefs_path(user_id)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}
    return {}


def _save_prefs(user_id: str, data: dict) -> None:
    path = _prefs_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def get_service_prefs(user_id: str, service: str) -> dict:
    """Get preferences for a service. Returns {} if not set."""
    return _load_prefs(user_id).get("connectors", {}).get(service, {})


def set_service_prefs(user_id: str, service: str, enabled: bool, mode: str) -> None:
    """Set preferences for a service. Mode: 'read' or 'readwrite'."""
    data = _load_prefs(user_id)
    if "connectors" not in data:
        data["connectors"] = {}
    data["connectors"][service] = {"enabled": enabled, "mode": mode}
    _save_prefs(user_id, data)
    logger.info("Set %s prefs for user '%s': enabled=%s, mode=%s", service, user_id, enabled, mode)


def get_all_prefs(user_id: str) -> dict:
    """Get all preferences."""
    return _load_prefs(user_id)


# --- Permission Enforcement ---


def check_permission(ctx, service: str, level: str = "read") -> tuple[str | None, str | None]:
    """Check if user has permission to use a service tool.

    Args:
        ctx: MCP Context
        service: Service name (e.g., "github")
        level: "read" or "write"

    Returns: (user_id, error_message). If error_message is set, deny the action.
    """
    user_id, err = user_session.require_user(ctx)
    if err:
        return None, err

    prefs = get_service_prefs(user_id, service)

    # Default: enabled in read mode
    enabled = prefs.get("enabled", True)
    mode = prefs.get("mode", "read")

    if not enabled:
        return None, f"{service} is disabled. Say 'enable {service}' to turn it on."

    if level == "write" and mode != "readwrite":
        return None, f"{service} is in read-only mode. Say 'set {service} to readwrite' to enable write actions."

    return user_id, None


def require_service(
    ctx, service: str, auth_builder: Callable[[dict], httpx.AsyncClient | None], level: str = "read"
) -> tuple[httpx.AsyncClient | None, str | None, str | None]:
    """Full check: user identity + permissions + credentials + client creation.

    Args:
        ctx: MCP Context
        service: Service name
        auth_builder: Function(creds_dict) -> httpx.AsyncClient or None
        level: "read" or "write"

    Returns: (client, user_id, error_message)
    """
    user_id, err = check_permission(ctx, service, level)
    if err:
        return None, None, err

    creds = get_credentials(user_id, service)
    if not creds:
        return None, None, f"Not connected to {service}. Say 'connect to {service}' to set up your credentials."

    client = auth_builder(creds)
    if client is None:
        return None, None, f"Incomplete {service} credentials. Say 'connect to {service}' to update."

    return client, user_id, None


# --- Service Credential Schemas ---


SERVICE_SCHEMAS: dict[str, dict] = {
    "github": {"fields": ["token", "org"], "labels": ["Personal Access Token", "Organization name"]},
    "atlassian": {"fields": ["email", "api_token", "domain"], "labels": ["Email", "API Token", "Domain (e.g., company.atlassian.net)"]},
    "notion": {"fields": ["token"], "labels": ["Integration Token"]},
    "zendesk": {"fields": ["subdomain", "email", "api_token"], "labels": ["Subdomain", "Email", "API Token"]},
    "hubspot": {"fields": ["token"], "labels": ["Private App Access Token"]},
    "figma": {"fields": ["token"], "labels": ["Personal Access Token"]},
    "salesforce": {"fields": ["instance_url", "token"], "labels": ["Instance URL", "Access Token"]},
    "google": {"fields": ["token"], "labels": ["OAuth Token"]},
    "zapier": {"fields": ["api_key"], "labels": ["NLA API Key"]},
    "adobe_sign": {"fields": ["token"], "labels": ["OAuth Token"]},
    "ringcentral": {"fields": ["token"], "labels": ["OAuth Token"]},
    "roboflow": {"fields": ["api_key", "workspace"], "labels": ["API Key", "Workspace"]},
    "smartsheet": {"fields": ["token"], "labels": ["API Token"]},
    "zoom": {"fields": ["account_id", "client_id", "client_secret"], "labels": ["Account ID", "Client ID", "Client Secret"]},
    "concur": {"fields": ["token"], "labels": ["OAuth Token"]},
    "paylocity": {"fields": ["client_id", "client_secret", "company_id"], "labels": ["Client ID", "Client Secret", "Company ID"]},
    "sharefile": {"fields": ["token", "subdomain"], "labels": ["OAuth Token", "Subdomain"]},
    "sap": {"fields": ["base_url", "token"], "labels": ["Base URL", "API Token"]},
    "linksquares": {"fields": ["token"], "labels": ["API Token"]},
}

# Microsoft services (auth handled by microsoft.py, not credentials.json)
MICROSOFT_SERVICES = ["sharepoint", "outlook", "calendar", "teams"]
