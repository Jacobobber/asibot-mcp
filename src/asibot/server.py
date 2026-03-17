"""Asibot MCP server entry point."""

import json
import logging
import time

import httpx
from mcp.server.fastmcp import Context, FastMCP

from asibot import auth, token_store, user_session
from asibot.connectors import microsoft
from asibot.config import settings
from asibot.connectors import registry
# Connectors are imported dynamically as they're built
# from asibot.connectors.X import XConnector
from asibot.rag import ingest, search

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

mcp = FastMCP(
    "asibot",
    instructions=(
        "Asibot is a personal RAG agent. "
        "New users: call asibot_setup to create your account (one-time only). "
        "Use search_documents to find information from ingested documents. "
        "Use ingest_file or ingest_directory to add new documents. "
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

    tenant_id = settings.sharepoint_tenant_id
    client_id = settings.sharepoint_client_id

    if not all([tenant_id, client_id]):
        return "Server not configured for SSO. Set ASIBOT_SHAREPOINT_TENANT_ID and ASIBOT_SHAREPOINT_CLIENT_ID."

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

    # Poll for token in background
    import asyncio
    asyncio.create_task(_complete_setup(
        tenant_id, client_id, device_code, expires_in, interval
    ))

    return (
        f"Welcome to Asibot! Let's set up your account.\n\n"
        f"1. Go to: {verification_uri}\n"
        f"2. Enter code: {user_code}\n"
        f"3. Sign in with your Microsoft account\n\n"
        f"After signing in, call asibot_setup_status to get your API key and config.\n"
        f"(Waiting up to {expires_in // 60} minutes...)"
    )


# Temporary storage for pending setups
_pending_setups: dict[str, dict] = {}  # "latest" -> setup result


async def _complete_setup(
    tenant_id: str, client_id: str, device_code: str, expires_in: int, interval: int
) -> None:
    """Poll Microsoft for token, then create user."""
    import asyncio

    deadline = time.time() + expires_in
    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"

    while time.time() < deadline:
        await asyncio.sleep(interval)

        async with httpx.AsyncClient() as http:
            resp = await http.post(token_url, data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": client_id,
                "device_code": device_code,
            })

        data = resp.json()

        if "access_token" in data:
            # Get user profile from Microsoft
            async with httpx.AsyncClient() as http:
                profile_resp = await http.get(
                    "https://graph.microsoft.com/v1.0/me",
                    headers={"Authorization": f"Bearer {data['access_token']}"},
                )
                profile_resp.raise_for_status()
                profile = profile_resp.json()

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

            _pending_setups["latest"] = {
                "user": user,
                "status": "complete",
            }
            logger.info("Setup complete for %s (%s)", name, email)
            return

        error = data.get("error", "")
        if error == "authorization_pending":
            continue
        elif error == "slow_down":
            interval += 5
        else:
            _pending_setups["latest"] = {
                "status": "failed",
                "error": data.get("error_description", error),
            }
            logger.error("Setup failed: %s", data.get("error_description", error))
            return

    _pending_setups["latest"] = {"status": "expired"}
    logger.error("Setup timed out")


@mcp.tool()
async def asibot_setup_status() -> str:
    """Check if your account setup is complete. Call this after signing in via browser.

    Returns your API key and Claude Desktop config once sign-in is done.
    """
    result = _pending_setups.get("latest")

    if not result:
        return "No setup in progress. Call asibot_setup first."

    if result["status"] == "complete":
        user = result["user"]
        _pending_setups.pop("latest", None)
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
        _pending_setups.pop("latest", None)
        return f"Setup failed: {error}\n\nTry asibot_setup again."

    if result["status"] == "expired":
        _pending_setups.pop("latest", None)
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
async def asibot_connect(service: str, ctx: Context, **kwargs) -> str:
    """Connect a service by providing your credentials. One-time per service.

    Args:
        service: Service name (e.g., "github", "atlassian", "notion", "zendesk", "figma", etc.)
    """
    user_id, err = user_session.require_user(ctx)
    if err:
        return err

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

    fields = schema["fields"]
    labels = schema["labels"]
    instructions = "\n".join(f"  - {label}" for label in labels)
    return (
        f"To connect {service}, I need these credentials:\n{instructions}\n\n"
        f"Please provide them by calling asibot_set_credentials with:\n"
        f"  service: \"{service}\"\n"
        f"  credentials: a JSON string like {{{', '.join(f'\"{f}\": \"...\"' for f in fields)}}}\n\n"
        f"These are stored securely per-user on the server."
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

    missing = [f for f in schema["fields"] if not creds.get(f)]
    if missing:
        return f"Missing required fields: {', '.join(missing)}"

    token_store.set_credentials(user_id, service, creds)
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
    mode_desc = "read-only" if mode == "read" else "read + write"
    return f"{service} set to {mode_desc} mode."


def _config_snippet(api_key: str) -> str:
    """Generate Claude Desktop config JSON for the user."""
    config = {
        "mcpServers": {
            "asibot": {
                "command": "npx",
                "args": [
                    "mcp-remote",
                    f"http://localhost:8080/mcp",
                    "--header",
                    f"Authorization:Bearer {api_key}",
                ],
            }
        }
    }
    return json.dumps(config, indent=2)


# --- Core RAG Tools ---


@mcp.tool()
async def search_documents(query: str, top_k: int = 5) -> str:
    """Search ingested documents for information relevant to the query.

    Returns the most relevant text chunks with source citations.

    Args:
        query: The search query (natural language question or keywords)
        top_k: Number of results to return (default: 5)
    """
    results = search.search_documents(query, top_k=top_k)

    if not results:
        return "No relevant documents found. The knowledge base may be empty — try ingesting some documents first."

    output_parts = []
    for i, hit in enumerate(results, 1):
        output_parts.append(
            f"--- Result {i} (score: {hit['score']}) ---\n"
            f"Source: {hit['source_name']} ({hit['source']})\n"
            f"Chunk {hit['chunk_index'] + 1}/{hit['total_chunks']}\n"
            f"\n{hit['text']}\n"
        )

    return "\n".join(output_parts)


@mcp.tool()
async def ingest_file(file_path: str) -> str:
    """Ingest a single file into the knowledge base.

    Supported formats: PDF, DOCX, Markdown, plain text, CSV.
    Re-ingesting the same file replaces previous chunks.

    Args:
        file_path: Absolute or relative path to the file
    """
    result = ingest.ingest_file(file_path)
    return json.dumps(result, indent=2)


@mcp.tool()
async def ingest_directory(directory_path: str, pattern: str = "**/*") -> str:
    """Ingest all supported files in a directory into the knowledge base.

    Recursively finds and ingests PDF, DOCX, Markdown, text, and CSV files.

    Args:
        directory_path: Path to the directory
        pattern: Glob pattern for file matching (default: all files recursively)
    """
    result = ingest.ingest_directory(directory_path, pattern=pattern)
    return json.dumps(result, indent=2)


@mcp.tool()
async def list_sources() -> str:
    """List all documents that have been ingested into the knowledge base."""
    sources = search.list_sources()

    if not sources:
        return "No documents ingested yet. Use ingest_file or ingest_directory to add documents."

    total_chunks = sum(s["chunk_count"] for s in sources)
    lines = [f"Knowledge base: {len(sources)} sources, {total_chunks} total chunks\n"]
    for s in sources:
        lines.append(f"- {s['source_name']} ({s['chunk_count']} chunks) — {s['source']}")

    return "\n".join(lines)


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
        except Exception as e:
            logger.warning("Failed to load connector %s: %s", module_name, e)

    registry.register_all_tools(mcp)


# --- Entry Point ---


def main() -> None:
    settings.ensure_dirs()
    _setup_connectors()
    transport = settings.transport
    logger.info("Asibot MCP server starting (transport=%s, data_dir=%s)", transport, settings.data_dir)
    if transport == "streamable-http":
        logger.info("Listening on http://%s:%d/mcp", settings.host, settings.port)
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
