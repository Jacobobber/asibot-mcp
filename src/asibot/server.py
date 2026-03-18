"""Asibot MCP server entry point."""

import asyncio
import atexit
import json
import logging
import secrets
import time

import httpx
from mcp.server.fastmcp import Context, FastMCP

from asibot import audit, auth, token_store, user_session
from asibot.connectors import microsoft
from asibot.config import settings
from asibot.connectors import registry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

mcp = FastMCP(
    "asibot",
    instructions=(
        "Asibot connects you to your enterprise tools. "
        "New users: call asibot_setup to create your account (one-time only). "
        "Use asibot_services to see available connectors. "
        "Use asibot_connect to link a service. "
        "Always cite sources in your responses."
    ),
    host=settings.host,
    port=settings.port,
)


# --- Setup & Identity ---


@mcp.tool()
async def asibot_setup(ctx: Context) -> str:
    """One-time account setup. Signs you in with Microsoft SSO and creates your API key.

    After setup, you'll get a config snippet to paste into your Claude Desktop settings.
    You only need to do this once — after that, you're automatically authenticated.
    """
    # Check if already authenticated
    user_id, _ = user_session.require_user(ctx)
    if user_id:
        user = auth.get_user_by_email(user_id)
        if user:
            return (
                f"You're already set up as {user['name']} ({user['user_id']}).\n\n"
                f"Your API key: {user['api_key']}\n\n"
                f"Claude Desktop config:\n"
                f'{_config_snippet(user["api_key"])}'
            )

    tenant_id = settings.ms365_tenant_id
    client_id = settings.ms365_client_id

    if not all([tenant_id, client_id]):
        return "Server not configured for SSO. Set ASIBOT_MS365_TENANT_ID and ASIBOT_MS365_CLIENT_ID."

    # Start device code flow
    async with httpx.AsyncClient() as http:
        resp = await http.post(
            f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/devicecode",
            data={
                "client_id": client_id,
                "scope": microsoft.SCOPES,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    device_code = data["device_code"]
    user_code = data["user_code"]
    verification_uri = data["verification_uri"]
    expires_in = data.get("expires_in", 900)
    interval = data.get("interval", 5)

    # Generate a cryptographic setup_id (not derived from device_code)
    setup_id = secrets.token_urlsafe(16)

    # Enforce size cap and clean up expired entries before adding
    _cleanup_pending_setups()
    if len(_pending_setups) >= _MAX_PENDING_SETUPS:
        return "Too many pending setups. Please try again later."

    # Poll for token in background
    asyncio.create_task(_complete_setup(
        tenant_id, client_id, device_code, expires_in, interval, setup_id
    ))

    return (
        f"Welcome to Asibot! Let's set up your account.\n\n"
        f"1. Go to: {verification_uri}\n"
        f"2. Enter code: {user_code}\n"
        f"3. Sign in with your Microsoft account\n\n"
        f"After signing in, call asibot_setup_status with setup_id=\"{setup_id}\" "
        f"to get your API key and config.\n"
        f"(Waiting up to {expires_in // 60} minutes...)"
    )


# Temporary storage for pending setups, keyed by setup_id
_pending_setups: dict[str, dict] = {}
_SETUP_TTL = 900  # 15 minutes — matches device code expiry
_MAX_PENDING_SETUPS = 100


def _cleanup_pending_setups() -> None:
    """Remove expired entries from _pending_setups."""
    now = time.time()
    expired = [k for k, v in _pending_setups.items() if now - v.get("_created_at", 0) > _SETUP_TTL]
    for k in expired:
        _pending_setups.pop(k, None)


async def _complete_setup(
    tenant_id: str, client_id: str, device_code: str, expires_in: int, interval: int,
    setup_id: str = "",
) -> None:
    """Poll Microsoft for token, then create user."""
    if not setup_id:
        setup_id = secrets.token_urlsafe(16)
    deadline = time.time() + expires_in
    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"

    while time.time() < deadline:
        await asyncio.sleep(interval)

        try:
            async with httpx.AsyncClient() as http:
                resp = await http.post(token_url, data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "client_id": client_id,
                    "device_code": device_code,
                })
        except httpx.RequestError as e:
            logger.warning("Setup poll request failed: %s", e)
            continue

        data = resp.json()

        if "access_token" in data:
            try:
                # Get user profile from Microsoft
                async with httpx.AsyncClient() as http:
                    profile_resp = await http.get(
                        "https://graph.microsoft.com/v1.0/me",
                        headers={"Authorization": f"Bearer {data['access_token']}"},
                    )
                    profile_resp.raise_for_status()
                    profile = profile_resp.json()
            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                _pending_setups[setup_id] = {
                    "status": "failed",
                    "error": f"Failed to fetch Microsoft profile: {e}",
                    "_created_at": time.time(),
                }
                logger.error("Setup failed fetching profile: %s", e)
                return

            email = profile.get("mail") or profile.get("userPrincipalName", "unknown")
            name = profile.get("displayName", email)

            # Create user
            user = auth.create_user(email, name)

            # Store Microsoft token for this user (covers all MS365 services)
            token_data = {
                "access_token": data["access_token"],
                "refresh_token": data.get("refresh_token", ""),
                "expires_at": time.time() + data.get("expires_in", 3600),
            }
            microsoft.save_token(email, token_data)

            _pending_setups[setup_id] = {
                "user": user,
                "status": "complete",
                "_created_at": time.time(),
            }
            logger.info("Setup complete for %s (%s)", name, email)
            return

        error = data.get("error", "")
        if error == "authorization_pending":
            continue
        elif error == "slow_down":
            interval += 5
        else:
            _pending_setups[setup_id] = {
                "status": "failed",
                "error": data.get("error_description", error),
                "_created_at": time.time(),
            }
            logger.error("Setup failed: %s", data.get("error_description", error))
            return

    _pending_setups[setup_id] = {"status": "expired", "_created_at": time.time()}
    logger.error("Setup timed out")


# --- GitHub Device Code OAuth ---


async def _github_device_flow(user_id: str) -> str:
    """Start a GitHub device code OAuth flow."""
    client_id = settings.github_client_id
    async with httpx.AsyncClient() as http:
        resp = await http.post(
            "https://github.com/login/device/code",
            data={"client_id": client_id, "scope": "repo read:org"},
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()

    device_code = data["device_code"]
    user_code = data["user_code"]
    verification_uri = data["verification_uri"]
    expires_in = data.get("expires_in", 900)
    interval = data.get("interval", 5)

    setup_id = secrets.token_urlsafe(16)

    _cleanup_pending_setups()
    if len(_pending_setups) >= _MAX_PENDING_SETUPS:
        return "Too many pending setups. Please try again later."

    asyncio.create_task(_complete_github_oauth(
        client_id, device_code, expires_in, interval, setup_id, user_id,
    ))

    audit.log_tool_call(user_id, "asibot_connect", {"service": "github"})
    return (
        f"Let's connect GitHub!\n\n"
        f"1. Go to: {verification_uri}\n"
        f"2. Enter code: **{user_code}**\n"
        f"3. Authorize the app\n\n"
        f"Call asibot_setup_status with setup_id=\"{setup_id}\" when done.\n"
        f"(Waiting up to {expires_in // 60} minutes...)"
    )


async def _complete_github_oauth(
    client_id: str, device_code: str, expires_in: int, interval: int,
    setup_id: str, user_id: str,
) -> None:
    """Poll GitHub for OAuth token, then store credentials."""
    deadline = time.time() + expires_in

    while time.time() < deadline:
        await asyncio.sleep(interval)
        try:
            async with httpx.AsyncClient() as http:
                resp = await http.post(
                    "https://github.com/login/oauth/access_token",
                    data={
                        "client_id": client_id,
                        "device_code": device_code,
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    },
                    headers={"Accept": "application/json"},
                )
        except httpx.RequestError as e:
            logger.warning("GitHub OAuth poll failed: %s", e)
            continue

        data = resp.json()
        if "access_token" in data:
            token = data["access_token"]
            # Store the token as GitHub credentials
            token_store.set_credentials(user_id, "github", {"token": token})
            _pending_setups[setup_id] = {
                "status": "complete",
                "user": {"name": user_id, "user_id": user_id, "api_key": ""},
                "_service": "github",
                "_created_at": time.time(),
            }
            logger.info("GitHub OAuth complete for %s", user_id)
            return

        error = data.get("error", "")
        if error == "authorization_pending":
            continue
        elif error == "slow_down":
            interval += 5
        else:
            _pending_setups[setup_id] = {
                "status": "failed",
                "error": data.get("error_description", error),
                "_created_at": time.time(),
            }
            logger.error("GitHub OAuth failed: %s", data.get("error_description", error))
            return

    _pending_setups[setup_id] = {"status": "expired", "_created_at": time.time()}


# --- Google Device Code OAuth ---


async def _google_device_flow(user_id: str) -> str:
    """Start a Google device code OAuth flow."""
    client_id = settings.google_client_id
    scopes = "https://www.googleapis.com/auth/drive.readonly https://www.googleapis.com/auth/calendar.readonly"
    async with httpx.AsyncClient() as http:
        resp = await http.post(
            "https://oauth2.googleapis.com/device/code",
            data={"client_id": client_id, "scope": scopes},
        )
        resp.raise_for_status()
        data = resp.json()

    device_code = data["device_code"]
    user_code = data["user_code"]
    verification_url = data["verification_url"]
    expires_in = data.get("expires_in", 1800)
    interval = data.get("interval", 5)

    setup_id = secrets.token_urlsafe(16)

    _cleanup_pending_setups()
    if len(_pending_setups) >= _MAX_PENDING_SETUPS:
        return "Too many pending setups. Please try again later."

    asyncio.create_task(_complete_google_oauth(
        device_code, expires_in, interval, setup_id, user_id,
    ))

    audit.log_tool_call(user_id, "asibot_connect", {"service": "google"})
    return (
        f"Let's connect Google Workspace!\n\n"
        f"1. Go to: {verification_url}\n"
        f"2. Enter code: **{user_code}**\n"
        f"3. Sign in with your Google account\n\n"
        f"Call asibot_setup_status with setup_id=\"{setup_id}\" when done.\n"
        f"(Waiting up to {expires_in // 60} minutes...)"
    )


async def _complete_google_oauth(
    device_code: str, expires_in: int, interval: int,
    setup_id: str, user_id: str,
) -> None:
    """Poll Google for OAuth token, then store credentials."""
    client_id = settings.google_client_id
    client_secret = settings.google_client_secret
    deadline = time.time() + expires_in

    while time.time() < deadline:
        await asyncio.sleep(interval)
        try:
            async with httpx.AsyncClient() as http:
                resp = await http.post(
                    "https://oauth2.googleapis.com/token",
                    data={
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "device_code": device_code,
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    },
                )
        except httpx.RequestError as e:
            logger.warning("Google OAuth poll failed: %s", e)
            continue

        data = resp.json()
        if "access_token" in data:
            creds = {"token": data["access_token"]}
            if data.get("refresh_token"):
                creds["refresh_token"] = data["refresh_token"]
            if data.get("expires_in"):
                creds["expires_at"] = str(time.time() + data["expires_in"])
            token_store.set_credentials(user_id, "google", creds)
            _pending_setups[setup_id] = {
                "status": "complete",
                "user": {"name": user_id, "user_id": user_id, "api_key": ""},
                "_service": "google",
                "_created_at": time.time(),
            }
            logger.info("Google OAuth complete for %s", user_id)
            return

        error = data.get("error", "")
        if error == "authorization_pending":
            continue
        elif error == "slow_down":
            interval += 5
        else:
            _pending_setups[setup_id] = {
                "status": "failed",
                "error": data.get("error_description", error),
                "_created_at": time.time(),
            }
            logger.error("Google OAuth failed: %s", data.get("error_description", error))
            return

    _pending_setups[setup_id] = {"status": "expired", "_created_at": time.time()}


@mcp.tool()
async def asibot_setup_status(setup_id: str = "") -> str:
    """Check if your account setup is complete. Call this after signing in via browser.

    Returns your API key and Claude Desktop config once sign-in is done.

    Args:
        setup_id: The setup ID returned by asibot_setup. If empty, checks the most recent setup.
    """
    if setup_id:
        result = _pending_setups.get(setup_id)
    elif _pending_setups:
        # Fallback: use the most recent setup for single-user convenience
        setup_id = next(reversed(_pending_setups))
        result = _pending_setups[setup_id]
    else:
        result = None

    if not result:
        return "No setup in progress. Call asibot_setup first."

    if result["status"] == "complete":
        user = result["user"]
        svc = result.get("_service")
        _pending_setups.pop(setup_id, None)

        # Service OAuth completions (GitHub, Google) — no API key to show
        if svc:
            return f"Connected to {svc} successfully! You can now use {svc} tools."

        # Account setup completion (Microsoft SSO) — show API key + config
        return (
            f"Setup complete!\n\n"
            f"Name: {user['name']}\n"
            f"Email: {user['user_id']}\n"
            f"API Key: {user['api_key']}\n\n"
            f"Add this to your Claude Desktop config at %APPDATA%\\Claude\\claude_desktop_config.json:\n\n"
            f"{_config_snippet(user['api_key'])}\n\n"
            f"After updating the config, restart Claude Desktop. You're all set — "
            f"every future session will authenticate automatically."
        )

    if result["status"] == "failed":
        error = result.get("error", "Unknown error")
        _pending_setups.pop(setup_id, None)
        return f"Setup failed: {error}\n\nTry asibot_setup again."

    if result["status"] == "expired":
        _pending_setups.pop(setup_id, None)
        return "Setup timed out. Try asibot_setup again."

    return "Still waiting for you to sign in via browser..."


@mcp.tool()
async def asibot_whoami(ctx: Context) -> str:
    """Check which user you're authenticated as."""
    user_id, err = user_session.require_user(ctx)
    if err:
        return err
    user = auth.get_user_by_email(user_id)
    if user:
        return f"Authenticated as {user['name']} ({user['user_id']})"
    return f"Authenticated as {user_id}"


@mcp.tool()
async def asibot_rotate_key(ctx: Context) -> str:
    """Generate a new API key. The old key stops working immediately.

    After rotation, update your Claude Desktop config with the new key.
    """
    user_id, err = user_session.require_user(ctx)
    if err:
        return err
    user = auth.rotate_key(user_id)
    if not user:
        return "Could not rotate key — user not found."
    audit.log_tool_call(user_id, "asibot_rotate_key")
    return (
        f"API key rotated successfully.\n\n"
        f"New API key: {user['api_key']}\n\n"
        f"Update your Claude Desktop config:\n"
        f"{_config_snippet(user['api_key'])}\n\n"
        f"The old key no longer works. Restart Claude Desktop after updating."
    )


@mcp.tool()
async def asibot_connect(service: str, ctx: Context, **kwargs) -> str:
    """Connect a service by providing your credentials. One-time per service.

    Args:
        service: Service name (e.g., "github", "atlassian", "notion", "zendesk", "figma", etc.)
    """
    user_id, err = user_session.require_user(ctx)
    if err:
        return err

    # OAuth services — start device code flow directly
    if service == "github" and settings.github_client_id:
        existing = token_store.get_credentials(user_id, "github")
        if existing:
            return f"Already connected to GitHub. To reconnect, disconnect first."
        return await _github_device_flow(user_id)

    if service == "google" and settings.google_client_id:
        existing = token_store.get_credentials(user_id, "google")
        if existing:
            return f"Already connected to Google. To reconnect, disconnect first."
        return await _google_device_flow(user_id)

    schema = token_store.SERVICE_SCHEMAS.get(service)
    if not schema:
        available = ", ".join(sorted(token_store.SERVICE_SCHEMAS.keys()))
        return f"Unknown service '{service}'. Available: {available}"

    # Check if already connected
    existing = token_store.get_credentials(user_id, service)
    if existing:
        return (
            f"Already connected to {service}. To reconnect, ask me to "
            f"'disconnect from {service}' first, then connect again."
        )

    fields, labels = token_store.get_required_fields(service)
    if not fields:
        # All fields are server-configured — nothing for user to do (shouldn't happen)
        return f"{service} is fully configured by the server. No credentials needed."

    instructions = "\n".join(f"  - {label}" for label in labels)
    audit.log_tool_call(user_id, "asibot_connect", {"service": service})
    return (
        f"To connect {service}, I need:\n{instructions}\n\n"
        f"Call asibot_set_credentials with:\n"
        f"  service: \"{service}\"\n"
        f"  credentials: {{{', '.join(f'\"{f}\": \"...\"' for f in fields)}}}\n\n"
        f"Stored securely per-user on the server."
    )


@mcp.tool()
async def asibot_set_credentials(service: str, credentials: str, ctx: Context) -> str:
    """Store credentials for a service. Called after asibot_connect explains what's needed.

    Args:
        service: Service name (e.g., "github", "atlassian")
        credentials: JSON string with the required fields (e.g., '{"token": "ghp_xxx", "org": "myorg"}')
    """
    user_id, err = user_session.require_user(ctx)
    if err:
        return err

    schema = token_store.SERVICE_SCHEMAS.get(service)
    if not schema:
        return f"Unknown service '{service}'."

    try:
        creds = json.loads(credentials)
    except json.JSONDecodeError:
        return "Invalid JSON. Please provide credentials as a valid JSON string."

    if not isinstance(creds, dict):
        return "Credentials must be a JSON object, not an array or other type."

    required_fields, _ = token_store.get_required_fields(service)
    missing = [f for f in required_fields if not creds.get(f)]
    if missing:
        return f"Missing required fields: {', '.join(missing)}"

    token_store.set_credentials(user_id, service, creds)
    audit.log_tool_call(user_id, "asibot_set_credentials", {"service": service})
    return f"Connected to {service} successfully. Your credentials are stored securely."


@mcp.tool()
async def asibot_disconnect(service: str, ctx: Context) -> str:
    """Remove your credentials for a service.

    Args:
        service: Service name to disconnect
    """
    user_id, err = user_session.require_user(ctx)
    if err:
        return err

    token_store.remove_credentials(user_id, service)
    audit.log_tool_call(user_id, "asibot_disconnect", {"service": service})
    return f"Disconnected from {service}. Credentials removed."


@mcp.tool()
async def asibot_services(ctx: Context) -> str:
    """List all available services with connection status, enabled/disabled, and read/readwrite mode."""
    user_id, err = user_session.require_user(ctx)
    if err:
        return err

    connected = set(token_store.list_connected(user_id))
    ms_token = microsoft.load_token(user_id)
    ms_connected = bool(ms_token.get("access_token"))

    lines = ["Your services:\n"]
    lines.append(f"{'Service':<20} {'Auth':<15} {'Status':<12} {'Mode':<12}")
    lines.append("-" * 59)

    # Microsoft services
    for svc in token_store.MICROSOFT_SERVICES:
        prefs = token_store.get_service_prefs(user_id, svc)
        enabled = prefs.get("enabled", True)
        mode = prefs.get("mode", "read")
        auth = "connected" if ms_connected else "not set up"
        status = "enabled" if enabled else "DISABLED"
        lines.append(f"{svc:<20} {auth:<15} {status:<12} {mode:<12}")

    # Other services
    for service in sorted(token_store.SERVICE_SCHEMAS.keys()):
        prefs = token_store.get_service_prefs(user_id, service)
        enabled = prefs.get("enabled", True)
        mode = prefs.get("mode", "read")
        auth = "connected" if service in connected else "not set up"
        status = "enabled" if enabled else "DISABLED"
        lines.append(f"{service:<20} {auth:<15} {status:<12} {mode:<12}")

    lines.append("")
    lines.append("Commands: 'connect to X', 'enable X', 'disable X', 'set X to readwrite'")
    return "\n".join(lines)


@mcp.tool()
async def asibot_enable(service: str, ctx: Context) -> str:
    """Enable a service connector.

    Args:
        service: Service name (e.g., "github", "sharepoint")
    """
    user_id, err = user_session.require_user(ctx)
    if err:
        return err
    prefs = token_store.get_service_prefs(user_id, service)
    mode = prefs.get("mode", "read")
    token_store.set_service_prefs(user_id, service, enabled=True, mode=mode)
    return f"{service} enabled ({mode} mode)."


@mcp.tool()
async def asibot_disable(service: str, ctx: Context) -> str:
    """Disable a service connector. Tools for this service will not respond.

    Args:
        service: Service name (e.g., "github", "sharepoint")
    """
    user_id, err = user_session.require_user(ctx)
    if err:
        return err
    prefs = token_store.get_service_prefs(user_id, service)
    mode = prefs.get("mode", "read")
    token_store.set_service_prefs(user_id, service, enabled=False, mode=mode)
    return f"{service} disabled. No {service} tools will respond until re-enabled."


@mcp.tool()
async def asibot_set_mode(service: str, mode: str, ctx: Context) -> str:
    """Set a service to read-only or read-write mode.

    Args:
        service: Service name (e.g., "github", "outlook")
        mode: "read" for read-only, "readwrite" for full access
    """
    user_id, err = user_session.require_user(ctx)
    if err:
        return err
    if mode not in ("read", "readwrite"):
        return "Mode must be 'read' or 'readwrite'."
    prefs = token_store.get_service_prefs(user_id, service)
    enabled = prefs.get("enabled", True)
    token_store.set_service_prefs(user_id, service, enabled=enabled, mode=mode)
    audit.log_tool_call(user_id, "asibot_set_mode", {"service": service, "mode": mode})
    mode_desc = "read-only" if mode == "read" else "read + write"
    return f"{service} set to {mode_desc} mode."


def _config_snippet(api_key: str) -> str:
    """Generate Claude Desktop config JSON for the user."""
    scheme = "https" if settings.port == 443 else "http"
    host = settings.host if settings.host != "0.0.0.0" else "localhost"
    config = {
        "mcpServers": {
            "asibot": {
                "command": "npx",
                "args": [
                    "mcp-remote",
                    f"{scheme}://{host}:{settings.port}/mcp",
                    "--header",
                    f"Authorization:Bearer {api_key}",
                ],
            }
        }
    }
    return json.dumps(config, indent=2)


# --- Connector Setup ---


def _setup_connectors() -> None:
    """Auto-discover and register all connector modules."""
    import importlib
    import pkgutil
    import asibot.connectors as connectors_pkg

    for _, module_name, _ in pkgutil.iter_modules(connectors_pkg.__path__):
        if module_name in ("__init__", "base", "registry", "microsoft"):
            continue
        try:
            mod = importlib.import_module(f"asibot.connectors.{module_name}")
            # Find Connector subclasses in the module
            for attr_name in dir(mod):
                attr = getattr(mod, attr_name)
                if (isinstance(attr, type)
                    and issubclass(attr, connectors_pkg.base.Connector)
                    and attr is not connectors_pkg.base.Connector):
                    connector = attr()
                    registry.register(connector)
        except (ImportError, AttributeError, TypeError) as e:
            logger.warning("Failed to load connector %s: %s", module_name, e)

    registry.register_all_tools(mcp)


# --- Entry Point ---


def _cleanup() -> None:
    """Synchronous cleanup hook for atexit."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(microsoft.close_all_clients())
        else:
            loop.run_until_complete(microsoft.close_all_clients())
    except RuntimeError:
        pass


def main() -> None:
    settings.ensure_dirs()
    _setup_connectors()
    atexit.register(_cleanup)
    transport = settings.transport
    logger.info("Asibot MCP server starting (transport=%s, data_dir=%s)", transport, settings.data_dir)
    if transport == "streamable-http":
        logger.info("Listening on http://%s:%d/mcp", settings.host, settings.port)
        if settings.port != 443:
            logger.warning(
                "Running HTTP transport without TLS. API keys are transmitted in plaintext. "
                "Use a reverse proxy (nginx, Caddy) with TLS termination in production."
            )
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
