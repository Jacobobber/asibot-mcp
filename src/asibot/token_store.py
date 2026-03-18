"""Per-user credential storage, preferences, and permission enforcement.

Per-user files at ~/.asibot/users/{user_id}/:
  credentials.json  — service credentials (encrypted at rest, versioned)
  preferences.json  — per-connector enabled/mode settings (encrypted at rest, versioned)
  microsoft_token.json — Microsoft OAuth (managed by microsoft.py, encrypted)
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import httpx

from asibot import user_session
from asibot.crypto import load_encrypted, save_encrypted

logger = logging.getLogger(__name__)

# --- S2S Token Cache (centralized, uses distributed cache) ---

_TOKEN_MARGIN = 300  # refresh 5 min before expiry

# Per-key locks for in-process deduplication (prevents two coroutines from
# fetching the same token simultaneously within one process)
_s2s_locks_guard = asyncio.Lock()
_s2s_token_locks: dict[str, asyncio.Lock] = {}


async def get_s2s_token(
    *,
    cache_key: str,
    token_url: str,
    grant_data: dict,
    auth: tuple[str, str],
    service_name: str,
    send_as_params: bool = False,
) -> str:
    """Fetch an S2S OAuth token with distributed caching and per-key locking.

    Args:
        cache_key: Unique cache key (e.g., "zoom:<account_id>")
        token_url: OAuth token endpoint URL
        grant_data: Form data or params for the token request
        auth: (client_id, client_secret) tuple for HTTP Basic auth
        service_name: Human-readable service name for error messages
        send_as_params: If True, send grant_data as query params instead of form body

    Returns:
        The access token string.

    Raises:
        httpx.HTTPStatusError: On token endpoint HTTP errors
        ValueError: If the response is missing access_token
    """
    from asibot.distributed_cache import get_cache

    cache = get_cache()

    # Per-key lock: prevents duplicate fetches within the same process
    async with _s2s_locks_guard:
        lock = _s2s_token_locks.get(cache_key)
        if lock is None:
            lock = asyncio.Lock()
            _s2s_token_locks[cache_key] = lock

    async with lock:
        # Check distributed cache
        cached = await cache.get_s2s_token(cache_key)
        if cached is not None:
            token, expires_at = cached
            if time.time() < expires_at - _TOKEN_MARGIN:
                return token

        # Fetch fresh token from OAuth endpoint
        async with httpx.AsyncClient(timeout=30.0) as client:
            kwargs: dict = {"auth": auth}
            if send_as_params:
                kwargs["params"] = grant_data
            else:
                kwargs["data"] = grant_data
            r = await client.post(token_url, **kwargs)
            r.raise_for_status()
            data = r.json()

        token = data.get("access_token")
        if not token:
            raise ValueError(f"{service_name} OAuth response missing access_token")

        expires_in = data.get("expires_in", 3600)
        expires_at = time.time() + expires_in

        # Store in distributed cache
        await cache.put_s2s_token(cache_key, token, expires_at)
        return token


# --- Service Rate Limiting (uses distributed cache) ---


async def check_service_rate_limit(
    service: str,
    limit: int = 100,
    window_seconds: int = 60,
) -> bool:
    """Check if a service is under its rate limit.

    Args:
        service: Service name (e.g., "zoom", "paylocity")
        limit: Max requests per window (default: 100)
        window_seconds: Sliding window size in seconds (default: 60)

    Returns:
        True if under limit (request allowed), False if rate-limited.
    """
    from asibot.distributed_cache import get_cache

    cache = get_cache()
    return await cache.check_rate_limit(f"service:{service}", limit, window_seconds)

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


def _apply_defaults(service: str, creds: dict, user_id: str = "") -> dict:
    """Merge server-level business defaults into user credentials.

    Admin-configured values (org, domain, subdomain, etc.) are injected so
    users only need to provide personal secrets (tokens/keys).
    """
    from asibot.config import settings

    defaults: dict[str, str] = {}

    if service == "github" and settings.github_org:
        defaults["org"] = settings.github_org
    elif service in ("atlassian", "confluence") and settings.atlassian_domain:
        defaults["domain"] = settings.atlassian_domain
    elif service == "zendesk" and settings.zendesk_subdomain:
        defaults["subdomain"] = settings.zendesk_subdomain
    elif service == "salesforce" and settings.salesforce_instance_url:
        defaults["instance_url"] = settings.salesforce_instance_url
    elif service == "sharefile" and settings.sharefile_subdomain:
        defaults["subdomain"] = settings.sharefile_subdomain
    elif service == "sap" and settings.sap_base_url:
        defaults["base_url"] = settings.sap_base_url
    elif service == "roboflow" and settings.roboflow_workspace:
        defaults["workspace"] = settings.roboflow_workspace

    # Auto-fill email for basic-auth services from the user's SSO profile
    if service in ("atlassian", "confluence", "zendesk") and user_id and "email" not in creds:
        from asibot import auth
        user = auth.get_user_by_email(user_id)
        if user:
            defaults["email"] = user["user_id"]

    # User-provided values always win over defaults
    merged = {**defaults, **creds}
    return merged


def get_credentials(user_id: str, service: str) -> dict:
    """Get credentials for a service with business defaults merged in.

    Returns empty dict if no credentials stored.
    """
    raw = _load_creds(user_id).get(service, {})
    if not raw:
        return {}
    return _apply_defaults(service, raw, user_id)


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
    "github": {"fields": ["token"], "labels": ["Personal Access Token"], "server_fields": ["org"]},
    "atlassian": {"fields": ["api_token"], "labels": ["API Token"], "server_fields": ["email", "domain"]},
    "notion": {"fields": ["token"], "labels": ["Integration Token"]},
    "zendesk": {"fields": ["api_token"], "labels": ["API Token"], "server_fields": ["email", "subdomain"]},
    "hubspot": {"fields": ["token"], "labels": ["Private App Access Token"]},
    "figma": {"fields": ["token"], "labels": ["Personal Access Token"]},
    "salesforce": {"fields": ["token"], "labels": ["Access Token"], "server_fields": ["instance_url"]},
    "google": {"fields": ["token"], "labels": ["OAuth Token"]},
    "zapier": {"fields": ["api_key"], "labels": ["NLA API Key"]},
    "adobe_sign": {"fields": ["token"], "labels": ["OAuth Token"]},
    "ringcentral": {"fields": ["token"], "labels": ["OAuth Token"]},
    "roboflow": {"fields": ["api_key"], "labels": ["API Key"], "server_fields": ["workspace"]},
    "smartsheet": {"fields": ["token"], "labels": ["API Token"]},
    "zoom": {"fields": ["account_id", "client_id", "client_secret"], "labels": ["Account ID", "Client ID", "Client Secret"]},
    "concur": {"fields": ["token"], "labels": ["OAuth Token"]},
    "paylocity": {"fields": ["client_id", "client_secret", "company_id"], "labels": ["Client ID", "Client Secret", "Company ID"]},
    "sharefile": {"fields": ["token"], "labels": ["OAuth Token"], "server_fields": ["subdomain"]},
    "sap": {"fields": ["token"], "labels": ["API Token"], "server_fields": ["base_url"]},
    "linksquares": {"fields": ["token"], "labels": ["API Token"]},
}


def get_required_fields(service: str) -> tuple[list[str], list[str]]:
    """Return (fields, labels) the user actually needs to provide.

    Server-configured fields are excluded if their config value is set.
    """
    from asibot.config import settings

    schema = SERVICE_SCHEMAS.get(service)
    if not schema:
        return [], []

    fields = list(schema["fields"])
    labels = list(schema["labels"])

    # Check which server_fields are NOT yet configured — user must provide those
    for sf in schema.get("server_fields", []):
        has_default = False
        if sf == "org" and settings.github_org:
            has_default = True
        elif sf == "domain" and settings.atlassian_domain:
            has_default = True
        elif sf == "subdomain":
            if service == "zendesk" and settings.zendesk_subdomain:
                has_default = True
            elif service == "sharefile" and settings.sharefile_subdomain:
                has_default = True
        elif sf == "instance_url" and settings.salesforce_instance_url:
            has_default = True
        elif sf == "base_url" and settings.sap_base_url:
            has_default = True
        elif sf == "workspace" and settings.roboflow_workspace:
            has_default = True
        elif sf == "email":
            has_default = True  # always auto-filled from SSO profile

        if not has_default:
            # Config not set — user must provide this field
            label_map = {
                "org": "Organization name",
                "domain": "Domain (e.g., company.atlassian.net)",
                "subdomain": "Subdomain",
                "instance_url": "Instance URL",
                "base_url": "Base URL (HTTPS)",
                "workspace": "Workspace",
                "email": "Email",
            }
            fields.append(sf)
            labels.append(label_map.get(sf, sf))

    return fields, labels

# Microsoft services (auth handled by microsoft.py, not credentials.json)
MICROSOFT_SERVICES = ["sharepoint", "outlook", "calendar", "teams"]
