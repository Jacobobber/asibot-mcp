"""Per-user credential storage, preferences, and permission enforcement.

Per-user files at ~/.asibot/users/{user_id}/:
  credentials.json  — service credentials (encrypted at rest, versioned)
  preferences.json  — per-connector enabled/mode settings (encrypted at rest, versioned)
  microsoft_token.json — Microsoft OAuth (managed by microsoft.py, encrypted)
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import httpx

from asibot import user_session
from asibot.crypto import load_encrypted, save_encrypted

logger = logging.getLogger(__name__)

# --- Schema Versioning ---

CURRENT_SCHEMA_VERSION = 2  # Bump when credential/pref structure changes


def _migrate_data(data: dict) -> dict:
    """Run schema migrations on loaded data. Returns the (possibly updated) dict."""
    version = data.get("_schema_version", 1)
    if version >= CURRENT_SCHEMA_VERSION:
        return data
    # v1 -> v2: add version field (no structural changes, just stamp)
    data["_schema_version"] = CURRENT_SCHEMA_VERSION
    return data


# --- Client Specification & Factory ---


@dataclass(frozen=True)
class ClientSpec:
    """Declarative specification for building an httpx.AsyncClient from credentials."""

    required_fields: tuple[str, ...]
    auth_type: str = "bearer"  # "bearer", "basic", "api_key", "none"
    token_field: str = "token"
    base_url: str | None = None  # May contain {field} placeholders for creds
    headers: dict[str, str] = field(default_factory=dict)
    # Basic auth
    basic_user_field: str = "email"
    basic_pass_field: str = "api_token"
    basic_user_suffix: str = ""
    # API key
    api_key_header: str = "X-API-Key"
    api_key_field: str = "api_key"
    timeout: float = 30.0


def build_client(spec: ClientSpec, creds: dict) -> httpx.AsyncClient | None:
    """Build an httpx.AsyncClient from a spec and credentials dict."""
    for f in spec.required_fields:
        if not creds.get(f):
            return None

    kwargs: dict = {"timeout": spec.timeout}
    all_headers: dict[str, str] = dict(spec.headers)

    if spec.auth_type == "bearer":
        all_headers["Authorization"] = f"Bearer {creds[spec.token_field]}"
    elif spec.auth_type == "basic":
        user = creds[spec.basic_user_field] + spec.basic_user_suffix
        kwargs["auth"] = (user, creds[spec.basic_pass_field])
    elif spec.auth_type == "api_key":
        all_headers[spec.api_key_header] = creds[spec.api_key_field]
    # "none" — no auth headers (e.g., zoom/paylocity fetch token async)

    if all_headers:
        kwargs["headers"] = all_headers
    if spec.base_url:
        kwargs["base_url"] = spec.base_url.format(**creds)

    return httpx.AsyncClient(**kwargs)


# Central registry of client specs — connectors no longer need _make_client()
CLIENT_SPECS: dict[str, ClientSpec] = {
    "github": ClientSpec(
        required_fields=("token",),
        headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
    ),
    "atlassian": ClientSpec(
        required_fields=("email", "api_token", "domain"),
        auth_type="basic",
        base_url="https://{domain}/rest/api/3",
        headers={"Accept": "application/json"},
    ),
    "confluence": ClientSpec(
        required_fields=("email", "api_token", "domain"),
        auth_type="basic",
        base_url="https://{domain}/wiki/rest/api",
        headers={"Accept": "application/json"},
    ),
    "notion": ClientSpec(
        required_fields=("token",),
        base_url="https://api.notion.com",
        headers={"Notion-Version": "2022-06-28", "Content-Type": "application/json"},
    ),
    "salesforce": ClientSpec(
        required_fields=("token", "instance_url"),
        base_url="{instance_url}/services/data/v59.0",
    ),
    "zendesk": ClientSpec(
        required_fields=("email", "api_token", "subdomain"),
        auth_type="basic",
        basic_user_suffix="/token",
        base_url="https://{subdomain}.zendesk.com/api/v2",
        headers={"Accept": "application/json"},
    ),
    "hubspot": ClientSpec(required_fields=("token",)),
    "figma": ClientSpec(required_fields=("token",)),
    "adobe_sign": ClientSpec(required_fields=("token",)),
    "smartsheet": ClientSpec(required_fields=("token",)),
    "concur": ClientSpec(required_fields=("token",)),
    "linksquares": ClientSpec(required_fields=("token",)),
    "ringcentral": ClientSpec(required_fields=("token",)),
    "google": ClientSpec(required_fields=("token",)),
    "roboflow": ClientSpec(
        required_fields=("api_key",),
        token_field="api_key",
        headers={"Accept": "application/json"},
    ),
    "zapier": ClientSpec(
        required_fields=("api_key",),
        auth_type="api_key",
        api_key_field="api_key",
    ),
    "sap": ClientSpec(
        required_fields=("token", "base_url"),
        headers={"Accept": "application/json"},
    ),
    "sharefile": ClientSpec(
        required_fields=("token", "subdomain"),
        headers={"Accept": "application/json"},
    ),
    "zoom": ClientSpec(
        required_fields=("account_id", "client_id", "client_secret"),
        auth_type="none",
    ),
    "paylocity": ClientSpec(
        required_fields=("client_id", "client_secret", "company_id"),
        auth_type="none",
        headers={"Accept": "application/json"},
    ),
}


# --- Error Formatting ---


def format_api_error(service: str, action: str, error: Exception) -> str:
    """Format a consistent error message for API failures."""
    if isinstance(error, httpx.HTTPStatusError):
        return f"{service} {action} failed: HTTP {error.response.status_code}"
    if isinstance(error, httpx.RequestError):
        return f"{service} {action} failed: network error"
    return f"{service} {action} failed: {error}"


async def safe_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    service: str,
    action: str,
    **kwargs,
) -> tuple[httpx.Response | None, str | None]:
    """Execute an HTTP request with standardized error handling.

    Args:
        client: httpx.AsyncClient to use
        method: HTTP method ("get", "post", "put", "patch", "delete")
        url: Request URL or path (relative if client has base_url)
        service: Service name for error messages (e.g., "GitHub")
        action: Action name for error messages (e.g., "search repos")
        **kwargs: Passed to client.request()

    Returns: (response, error_message). If error_message is set, response is None.
    """
    try:
        r = await client.request(method, url, **kwargs)
        r.raise_for_status()
        return r, None
    except (httpx.HTTPStatusError, httpx.RequestError, ValueError) as e:
        return None, format_api_error(service, action, e)


# --- Credentials ---


def _creds_path(user_id: str) -> Path:
    return user_session.get_user_data_dir(user_id) / "credentials.json"


def _load_creds(user_id: str) -> dict:
    data = load_encrypted(_creds_path(user_id))
    return _migrate_data(data) if data else data


def _save_creds(user_id: str, data: dict) -> None:
    save_encrypted(_creds_path(user_id), data)


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
    return [k for k in _load_creds(user_id) if not k.startswith("_")]


# --- Preferences ---


def _prefs_path(user_id: str) -> Path:
    return user_session.get_user_data_dir(user_id) / "preferences.json"


def _load_prefs(user_id: str) -> dict:
    data = load_encrypted(_prefs_path(user_id))
    return _migrate_data(data) if data else data


def _save_prefs(user_id: str, data: dict) -> None:
    save_encrypted(_prefs_path(user_id), data)


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
    ctx,
    service: str,
    auth_builder: Callable[[dict], httpx.AsyncClient | None] | None = None,
    level: str = "read",
) -> tuple[httpx.AsyncClient | None, str | None, str | None]:
    """Full check: user identity + permissions + credentials + client creation.

    Args:
        ctx: MCP Context
        service: Service name
        auth_builder: Optional legacy callback. If None, uses the registered ClientSpec.
        level: "read" or "write"

    Returns: (client, user_id, error_message)
    """
    user_id, err = check_permission(ctx, service, level)
    if err:
        return None, None, err

    creds = get_credentials(user_id, service)
    if not creds:
        return None, None, f"Not connected to {service}. Say 'connect to {service}' to set up your credentials."

    if auth_builder is not None:
        client = auth_builder(creds)
    else:
        spec = CLIENT_SPECS.get(service)
        if spec is None:
            return None, None, f"No client configuration registered for {service}."
        client = build_client(spec, creds)

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
