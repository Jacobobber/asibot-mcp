"""Asibot MCP server entry point."""

import asyncio
import atexit
import functools
import json
import logging
import secrets
import signal
import time
from collections import deque
from contextlib import asynccontextmanager
from typing import Any, Awaitable, Callable

import httpx
from mcp.server.fastmcp import Context, FastMCP

from asibot import audit, auth, db, distributed_cache, http_pool, metrics, migrate, token_store, user_session, validation
from asibot.connectors import microsoft
from asibot.config import settings, validate_for_production
from asibot.connectors import registry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app):
    """Server lifespan: load sessions from DB on startup, close pool on shutdown."""
    await user_session.load_sessions_from_db()
    yield {}
    await db.close_pool()


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
    lifespan=_lifespan,
)

# --- Concurrency Controls ---

# Global semaphore: limits total concurrent tool calls across all users
_request_semaphore: asyncio.Semaphore | None = None

# Per-user semaphores: limits concurrent tool calls per user
_user_semaphores: dict[str, asyncio.Semaphore] = {}
_user_semaphores_lock = asyncio.Lock()

# Per-service semaphores: limits concurrent calls to each external service
_service_semaphores: dict[str, asyncio.Semaphore] = {}
_service_semaphores_lock = asyncio.Lock()

# Metrics counters for concurrency
requests_queued = 0
requests_rejected = 0
concurrent_active = 0


def _get_request_semaphore() -> asyncio.Semaphore:
    """Lazy-init the global request semaphore (must be called in async context)."""
    global _request_semaphore
    if _request_semaphore is None:
        _request_semaphore = asyncio.Semaphore(settings.max_concurrent_requests)
    return _request_semaphore


async def _get_user_semaphore(user_id: str) -> asyncio.Semaphore:
    """Get or create a per-user semaphore."""
    async with _user_semaphores_lock:
        if user_id not in _user_semaphores:
            _user_semaphores[user_id] = asyncio.Semaphore(settings.max_concurrent_per_user)
        return _user_semaphores[user_id]


async def _get_service_semaphore(service: str) -> asyncio.Semaphore:
    """Get or create a per-service semaphore."""
    async with _service_semaphores_lock:
        if service not in _service_semaphores:
            _service_semaphores[service] = asyncio.Semaphore(settings.max_concurrent_per_service)
        return _service_semaphores[service]


def concurrency_limited(service: str | None = None):
    """Decorator that enforces global and per-user concurrency limits on tool handlers.

    Args:
        service: Optional external service name for per-service limiting.
    """
    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            global requests_queued, requests_rejected, concurrent_active

            # Extract user_id from ctx if present
            ctx = kwargs.get("ctx") or next(
                (a for a in args if isinstance(a, Context)), None
            )
            user_id = None
            if ctx:
                uid, _ = user_session.require_user(ctx)
                user_id = uid

            # Try global semaphore (non-blocking check)
            global_sem = _get_request_semaphore()
            if global_sem.locked() and global_sem._value == 0:
                requests_rejected += 1
                return "Server at capacity, please retry in a moment"

            requests_queued += 1
            try:
                # Acquire global semaphore
                try:
                    await asyncio.wait_for(global_sem.acquire(), timeout=5.0)
                except asyncio.TimeoutError:
                    requests_rejected += 1
                    return "Server at capacity, please retry in a moment"

                try:
                    # Acquire per-user semaphore
                    if user_id:
                        user_sem = await _get_user_semaphore(user_id)
                        try:
                            await asyncio.wait_for(user_sem.acquire(), timeout=5.0)
                        except asyncio.TimeoutError:
                            requests_rejected += 1
                            return "Server at capacity, please retry in a moment"
                    else:
                        user_sem = None

                    try:
                        # Acquire per-service semaphore
                        if service:
                            svc_sem = await _get_service_semaphore(service)
                            try:
                                await asyncio.wait_for(svc_sem.acquire(), timeout=5.0)
                            except asyncio.TimeoutError:
                                requests_rejected += 1
                                return "Server at capacity, please retry in a moment"
                        else:
                            svc_sem = None

                        try:
                            concurrent_active += 1
                            return await fn(*args, **kwargs)
                        finally:
                            concurrent_active -= 1
                            if svc_sem is not None:
                                svc_sem.release()
                    finally:
                        if user_sem is not None:
                            user_sem.release()
                finally:
                    global_sem.release()
            finally:
                requests_queued -= 1

        return wrapper
    return decorator


# --- Instrumentation ---


def _install_tool_tracking() -> None:
    """Monkey-patch ToolManager.call_tool to audit-log every tool invocation.

    This is the single convergence point for ALL tool calls (system + connector),
    so we get universal coverage without modifying individual tool functions.
    """
    original_call_tool = mcp._tool_manager.call_tool

    async def _tracked_call_tool(name, arguments, *, context=None, **kwargs):
        # Resolve user_id from context (hits session cache first — lightweight)
        user_id = "anonymous"
        if context is not None:
            try:
                uid, _ = user_session.require_user(context)
                if uid:
                    user_id = uid
            except Exception:
                pass  # Don't let auth errors block the tool call

        start = time.monotonic()
        success = True
        error_type = None
        try:
            result = await original_call_tool(name, arguments, context=context, **kwargs)
            return result
        except Exception as e:
            success = False
            error_type = type(e).__name__
            raise
        finally:
            latency_ms = (time.monotonic() - start) * 1000
            service = audit._infer_service(name)

            # JSONL audit log (rotating file)
            audit.log_tool_call(
                user_id, name, arguments,
                latency_ms=latency_ms,
                success=success,
                error_type=error_type,
                service=service,
            )
            # SQLite audit log (primary store for analytics)
            try:
                await db.log_audit(
                    user_id, name, json.dumps(arguments or {}),
                    service=service,
                    success=success,
                    latency_ms=latency_ms,
                    error_type=error_type,
                )
            except Exception:
                pass  # Best-effort; JSONL is the backup record
            # Prometheus metrics
            status = "ok" if success else "error"
            metrics.request_duration.labels(
                service=service or "system", status=status,
            ).observe(latency_ms / 1000)
            metrics.request_total.labels(
                service=service or "system", status=status,
            ).inc()

    mcp._tool_manager.call_tool = _tracked_call_tool
    logger.info("Universal tool tracking installed")


def _install_session_tracking() -> None:
    """Wrap FastMCP.list_tools to log session activity.

    Clients send ListToolsRequest on connect and reconnect, so this serves
    as a proxy for "user X started a session at time T" — even when no
    tools are actually called.
    """
    original_list_tools = mcp.list_tools

    async def _tracked_list_tools() -> list:
        result = await original_list_tools()

        # Resolve user from request context (best-effort)
        ctx = mcp.get_context()
        user_id = "anonymous"
        try:
            uid, _ = user_session.require_user(ctx)
            if uid:
                user_id = uid
        except Exception:
            pass

        try:
            await db.log_event(user_id, "session_start", metadata={
                "tools_available": len(result),
            })
        except Exception:
            pass
        metrics.active_sessions.inc()

        return result

    mcp.list_tools = _tracked_list_tools
    # Re-register with the low-level MCP server so it uses our wrapper
    mcp._mcp_server.list_tools()(_tracked_list_tools)
    logger.info("Session tracking installed")


# --- Setup & Identity ---


@mcp.tool()
@concurrency_limited()
async def asibot_setup(ctx: Context) -> str:
    """One-time account setup. Signs you in with Microsoft SSO and creates your API key.

    After setup, you'll get a config snippet to paste into your Claude Desktop settings.
    You only need to do this once — after that, you're automatically authenticated.
    """
    _ensure_cleanup_tasks()
    rate_err = _check_setup_rate_limit()
    if rate_err:
        return rate_err
    # Check if already authenticated
    user_id, _ = user_session.require_user(ctx)
    if user_id:
        user = await auth.get_user_by_email(user_id)
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

    device_code = data.get("device_code")
    user_code = data.get("user_code")
    verification_uri = data.get("verification_uri")
    if not all([device_code, user_code, verification_uri]):
        return "Microsoft device code flow returned an incomplete response. Please try again."
    expires_in = data.get("expires_in", 900)
    interval = data.get("interval", 5)

    # Generate a cryptographic setup_id (not derived from device_code)
    setup_id = secrets.token_urlsafe(32)

    # Enforce size cap and clean up expired entries before adding
    async with _pending_setups_lock:
        _cleanup_pending_setups()
        if len(_pending_setups) >= _MAX_PENDING_SETUPS:
            return "Too many pending setups. Please try again later."
        await _persist_setup(setup_id, {"status": "pending", "_created_at": time.time()})

    # Enforce concurrent polling task limit
    global _active_polling_tasks
    async with _active_polling_lock:
        if _active_polling_tasks >= settings.max_concurrent_setups:
            return "Too many pending setups. Please try again later."
        _active_polling_tasks += 1

    # Poll for token in background
    task = asyncio.create_task(_complete_setup(
        tenant_id, client_id, device_code, expires_in, interval, setup_id
    ))
    task.add_done_callback(lambda t: _on_task_done(t, setup_id))

    return (
        f"Welcome to Asibot! Let's set up your account.\n\n"
        f"1. Go to: {verification_uri}\n"
        f"2. Enter code: {user_code}\n"
        f"3. Sign in with your Microsoft account\n\n"
        f"After signing in, call asibot_setup_status with setup_id=\"{setup_id}\" "
        f"to get your API key and config.\n"
        f"(Waiting up to {expires_in // 60} minutes...)"
    )


# Temporary storage for pending setups, keyed by setup_id.
# This in-memory dict acts as a fast cache; when a PostgreSQL backend is
# configured the state is also persisted so server restarts don't lose
# in-flight OAuth flows.
_pending_setups: dict[str, dict] = {}
_pending_setups_lock = asyncio.Lock()
_SETUP_TTL = 900  # 15 minutes — matches device code expiry
_MAX_PENDING_SETUPS = 100
_HARD_POLL_TIMEOUT = 15 * 60  # 15 minutes absolute max for any polling task
_POLL_BACKOFF_INITIAL = 5  # Initial poll interval in seconds
_POLL_BACKOFF_MAX = 30  # Maximum poll interval in seconds
_active_polling_tasks = 0
_active_polling_lock = asyncio.Lock()

# Global rate limit for setup/OAuth flow requests (prevents enumeration & DoS)
_SETUP_RATE_LIMIT = 10  # max new setups per window
_SETUP_RATE_WINDOW = 60  # seconds
_setup_timestamps: deque[float] = deque()


def _check_setup_rate_limit() -> str | None:
    """Check global rate limit for setup endpoint. Returns error msg or None."""
    now = time.time()
    cutoff = now - _SETUP_RATE_WINDOW
    while _setup_timestamps and _setup_timestamps[0] < cutoff:
        _setup_timestamps.popleft()  # O(1) vs list.pop(0) O(n)
    if len(_setup_timestamps) >= _SETUP_RATE_LIMIT:
        return "Too many setup requests. Please wait a minute and try again."
    _setup_timestamps.append(now)
    return None


# Allowed OAuth token endpoint prefixes — prevents redirection attacks
_ALLOWED_TOKEN_HOSTS = frozenset({
    "https://login.microsoftonline.com",
    "https://github.com",
    "https://oauth2.googleapis.com",
})

# Optional DB backend — initialised lazily via _get_db().
_db_backend = None


def _get_db():
    """Return the PostgresBackend instance, or None if not configured."""
    global _db_backend
    return _db_backend


async def init_db_backend() -> None:
    """Initialise the PostgresBackend if ASIBOT_DATABASE_URL is set.

    Called once at server startup (see ``main()``).
    """
    global _db_backend
    if not settings.database_url:
        return
    try:
        from asibot.db_postgres import PostgresBackend
        _db_backend = PostgresBackend(
            settings.database_url,
            min_size=settings.pg_pool_min_size,
            max_size=settings.pg_pool_max_size,
            read_url=settings.database_read_url,
        )
        await _db_backend.initialize()
        logger.info("PostgresBackend initialised for OAuth state persistence")
    except Exception as exc:
        logger.error("Failed to initialise PostgresBackend: %s — OAuth state will be volatile", exc)
        _db_backend = None


async def _persist_setup(setup_id: str, state: dict, *, user_id: str | None = None, service: str | None = None) -> None:
    """Write a pending setup entry to both the in-memory cache and (optionally) the DB."""
    _pending_setups[setup_id] = state
    db = _get_db()
    if db is not None:
        try:
            await db.store_pending_setup(
                setup_id, state, user_id=user_id, service=service, ttl=_SETUP_TTL,
            )
        except Exception as exc:
            logger.warning("Failed to persist setup %s to DB: %s", setup_id, exc)


async def _load_setup_from_db(setup_id: str) -> dict | None:
    """Fall back to DB if an entry is missing from the in-memory cache."""
    db = _get_db()
    if db is None:
        return None
    try:
        row = await db.get_pending_setup(setup_id)
        if row is None:
            return None
        state = row["state"]
        # Re-populate the in-memory cache
        _pending_setups[setup_id] = state
        return state
    except Exception as exc:
        logger.warning("Failed to load setup %s from DB: %s", setup_id, exc)
        return None


async def _delete_setup(setup_id: str) -> None:
    """Remove a pending setup from both cache and DB."""
    _pending_setups.pop(setup_id, None)
    db = _get_db()
    if db is not None:
        try:
            await db.delete_pending_setup(setup_id)
        except Exception as exc:
            logger.warning("Failed to delete setup %s from DB: %s", setup_id, exc)


def _on_task_done(task: asyncio.Task, setup_id: str) -> None:
    """Log unhandled exceptions from background setup tasks and mark them failed."""
    global _active_polling_tasks
    _active_polling_tasks = max(0, _active_polling_tasks - 1)
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        logger.error("Background setup task %s failed: %s", setup_id, exc)
        # Done callbacks are synchronous — schedule a locked write + DB persist
        state = {
            "status": "failed",
            "error": f"Internal error: {exc}",
            "_created_at": time.time(),
        }
        task.get_loop().create_task(_set_pending_setup(setup_id, state))
        # Best-effort DB persist (fire-and-forget via the event loop)
        db = _get_db()
        if db is not None:
            try:
                loop = asyncio.get_event_loop()
                loop.create_task(db.store_pending_setup(setup_id, state, ttl=_SETUP_TTL))
            except RuntimeError:
                pass


def _cleanup_pending_setups() -> None:
    """Remove expired entries from _pending_setups. Caller must hold _pending_setups_lock."""
    now = time.time()
    expired = [k for k, v in _pending_setups.items() if now - v.get("_created_at", 0) > _SETUP_TTL]
    for k in expired:
        _pending_setups.pop(k, None)


async def _set_pending_setup(setup_id: str, value: dict) -> None:
    """Write to _pending_setups under the lock."""
    async with _pending_setups_lock:
        _pending_setups[setup_id] = value


# --- Background Task Management ---

_background_tasks: set[asyncio.Task] = set()


def _schedule_periodic(coro_factory: Callable[[], Awaitable], interval: float, name: str) -> asyncio.Task:
    """Schedule a repeating background task with error isolation."""
    async def _loop() -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await coro_factory()
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("Background task %s failed", name)

    task = asyncio.create_task(_loop(), name=name)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


_cleanup_started = False


async def _init_db_once() -> None:
    """Initialize database and run migration if needed (idempotent)."""
    await db.init_db()
    if await migrate.needs_migration():
        logger.info("Migrating existing data to SQLite...")
        summary = await migrate.migrate_from_files()
        logger.info("Migration summary: %s", summary)


def _ensure_cleanup_tasks() -> None:
    """Start all periodic background tasks (idempotent)."""
    global _cleanup_started
    if _cleanup_started:
        return
    _cleanup_started = True

    # Initialize database (fire-and-forget on first call, safe because
    # db.init_db() is idempotent and subsequent DB calls will await it)
    asyncio.ensure_future(_init_db_once())

    async def _cleanup_setups() -> None:
        async with _pending_setups_lock:
            _cleanup_pending_setups()

    async def _prune_audit() -> None:
        await db.prune_audit()

    async def _cleanup_sessions() -> None:
        await db.cleanup_expired_sessions()

    _schedule_periodic(_cleanup_setups, 300, "setup-cleanup")
    _schedule_periodic(http_pool.cleanup_idle, 60, "pool-idle-cleanup")
    _schedule_periodic(_cleanup_rate_limits, 300, "rate-limit-cleanup")
    _schedule_periodic(_cleanup_ms_clients, 300, "ms-client-cleanup")
    _schedule_periodic(_prune_audit, 3600, "audit-prune")
    _schedule_periodic(_cleanup_sessions, 300, "session-cleanup")


async def _cleanup_rate_limits() -> None:
    """Prune stale rate-limit and S2S token cache entries."""
    token_store.cleanup_rate_limits()


async def _cleanup_ms_clients() -> None:
    """Prune idle Microsoft Graph clients."""
    await microsoft.cleanup_idle_clients()


async def _poll_device_code(
    *,
    token_url: str,
    token_data: dict,
    token_headers: dict | None,
    on_success: Callable[[dict], Awaitable[dict]],
    setup_id: str,
    expires_in: int,
    interval: int,
    display_name: str,
) -> None:
    """Generic device code polling loop.

    Polls *token_url* until an access token is returned, then calls *on_success*
    with the token response. *on_success* should return the dict to store in
    ``_pending_setups`` (must include ``"status": "complete"``).

    Uses a hard timeout of _HARD_POLL_TIMEOUT and exponential backoff.
    """
    # Validate token URL is a known OAuth provider (prevents redirection attacks)
    if not any(token_url.startswith(host) for host in _ALLOWED_TOKEN_HOSTS):
        await _set_pending_setup(setup_id, {
            "status": "failed",
            "error": f"Untrusted token endpoint: {token_url}",
            "_created_at": time.time(),
        })
        logger.error("%s: untrusted token endpoint %s", display_name, token_url)
        return

    # Use the lesser of device code expiry and the hard timeout
    effective_timeout = min(expires_in, _HARD_POLL_TIMEOUT)
    deadline = time.time() + effective_timeout
    current_interval = max(interval, _POLL_BACKOFF_INITIAL)

    while time.time() < deadline:
        await asyncio.sleep(current_interval)

        try:
            async with httpx.AsyncClient() as http:
                resp = await http.post(token_url, data=token_data, headers=token_headers)
        except httpx.RequestError as e:
            logger.warning("%s poll request failed: %s", display_name, e)
            current_interval = min(current_interval * 2, _POLL_BACKOFF_MAX)
            continue

        data = resp.json()

        if "access_token" in data:
            try:
                result = await on_success(data)
            except Exception as e:
                await _set_pending_setup(setup_id, {
                    "status": "failed",
                    "error": f"{display_name} setup failed: {e}",
                    "_created_at": time.time(),
                })
                logger.error("%s setup failed: %s", display_name, e)
                return

            await _set_pending_setup(setup_id, result)
            logger.info("%s setup complete", display_name)
            return

        error = data.get("error", "")
        if error == "authorization_pending":
            # Exponential backoff on continued waiting
            current_interval = min(current_interval * 1.5, _POLL_BACKOFF_MAX)
            continue
        elif error == "slow_down":
            current_interval = min(current_interval + 5, _POLL_BACKOFF_MAX)
        else:
            await _set_pending_setup(setup_id, {
                "status": "failed",
                "error": data.get("error_description", error),
                "_created_at": time.time(),
            })
            logger.error("%s failed: %s", display_name, data.get("error_description", error))
            return

    await _set_pending_setup(setup_id, {"status": "expired", "_created_at": time.time()})
    logger.error("%s timed out", display_name)


async def _complete_setup(
    tenant_id: str, client_id: str, device_code: str, expires_in: int, interval: int,
    setup_id: str = "",
) -> None:
    """Poll Microsoft for token, then create user."""
    if not setup_id:
        setup_id = secrets.token_urlsafe(32)

    async def on_success(data: dict) -> dict:
        async with httpx.AsyncClient() as http:
            profile_resp = await http.get(
                "https://graph.microsoft.com/v1.0/me",
                headers={"Authorization": f"Bearer {data['access_token']}"},
            )
            profile_resp.raise_for_status()
            profile = profile_resp.json()

        email = profile.get("mail") or profile.get("userPrincipalName", "unknown")
        name = profile.get("displayName", email)
        user = await auth.create_user(email, name)
        try:
            await db.log_event(email, "user_created")
        except Exception:
            pass

        microsoft.save_token(email, {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token", ""),
            "expires_at": time.time() + data.get("expires_in", 3600),
        })
        return {"user": user, "status": "complete", "_created_at": time.time()}

    await _poll_device_code(
        token_url=f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
        token_data={
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": client_id,
            "device_code": device_code,
        },
        token_headers=None,
        on_success=on_success,
        setup_id=setup_id,
        expires_in=expires_in,
        interval=interval,
        display_name="Microsoft SSO",
    )


# --- Device Code OAuth (shared by GitHub, Google, etc.) ---


def _github_extract_creds(data: dict) -> dict:
    """Extract credentials from a GitHub OAuth token response."""
    return {"token": data["access_token"]}


def _google_extract_creds(data: dict) -> dict:
    """Extract credentials from a Google OAuth token response."""
    creds: dict[str, str] = {"token": data["access_token"]}
    if data.get("refresh_token"):
        creds["refresh_token"] = data["refresh_token"]
    if data.get("expires_in"):
        creds["expires_at"] = str(time.time() + data["expires_in"])
    return creds


_OAUTH_PROVIDERS: dict[str, dict[str, Any]] = {
    "github": {
        "display_name": "GitHub",
        "device_code_url": "https://github.com/login/device/code",
        "device_code_data": lambda: {"client_id": settings.github_client_id, "scope": "repo read:org"},
        "device_code_headers": {"Accept": "application/json"},
        "verification_url_key": "verification_uri",
        "default_expires_in": 900,
        "token_url": "https://github.com/login/oauth/access_token",
        "token_data": lambda dc: {
            "client_id": settings.github_client_id,
            "device_code": dc,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        },
        "token_headers": {"Accept": "application/json"},
        "extract_creds": _github_extract_creds,
        "auth_prompt": "Authorize the app",
    },
    "google": {
        "display_name": "Google Workspace",
        "device_code_url": "https://oauth2.googleapis.com/device/code",
        "device_code_data": lambda: {
            "client_id": settings.google_client_id,
            "scope": "https://www.googleapis.com/auth/drive.readonly https://www.googleapis.com/auth/calendar.readonly",
        },
        "device_code_headers": {},
        "verification_url_key": "verification_url",
        "default_expires_in": 1800,
        "token_url": "https://oauth2.googleapis.com/token",
        "token_data": lambda dc: {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "device_code": dc,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        },
        "token_headers": {},
        "extract_creds": _google_extract_creds,
        "auth_prompt": "Sign in with your Google account",
    },
}


async def _start_device_code_flow(service: str, user_id: str) -> str:
    """Start a device code OAuth flow for any configured provider."""
    rate_err = _check_setup_rate_limit()
    if rate_err:
        return rate_err
    provider = _OAUTH_PROVIDERS[service]

    async with httpx.AsyncClient() as http:
        resp = await http.post(
            provider["device_code_url"],
            data=provider["device_code_data"](),
            headers=provider["device_code_headers"] or None,
        )
        resp.raise_for_status()
        data = resp.json()

    device_code = data.get("device_code")
    user_code = data.get("user_code")
    verification_url = data.get(provider["verification_url_key"])
    if not all([device_code, user_code, verification_url]):
        return f"{provider['display_name']} device code flow returned an incomplete response. Please try again."
    expires_in = data.get("expires_in", provider["default_expires_in"])
    interval = data.get("interval", 5)

    setup_id = secrets.token_urlsafe(32)

    async with _pending_setups_lock:
        _cleanup_pending_setups()
        if len(_pending_setups) >= _MAX_PENDING_SETUPS:
            return "Too many pending setups. Please try again later."
        await _persist_setup(
            setup_id, {"status": "pending", "_created_at": time.time()},
            user_id=user_id, service=service,
        )

    # Enforce concurrent polling task limit
    global _active_polling_tasks
    async with _active_polling_lock:
        if _active_polling_tasks >= settings.max_concurrent_setups:
            return "Too many pending setups. Please try again later."
        _active_polling_tasks += 1

    task = asyncio.create_task(_complete_device_code_oauth(
        service, device_code, expires_in, interval, setup_id, user_id,
    ))
    task.add_done_callback(lambda t: _on_task_done(t, setup_id))

    display_name = provider["display_name"]
    return (
        f"Let's connect {display_name}!\n\n"
        f"1. Go to: {verification_url}\n"
        f"2. Enter code: **{user_code}**\n"
        f"3. {provider['auth_prompt']}\n\n"
        f"Call asibot_setup_status with setup_id=\"{setup_id}\" when done.\n"
        f"(Waiting up to {expires_in // 60} minutes...)"
    )


async def _complete_device_code_oauth(
    service: str, device_code: str, expires_in: int, interval: int,
    setup_id: str, user_id: str,
) -> None:
    """Poll an OAuth provider for a token, then store credentials.

    Uses a hard timeout of _HARD_POLL_TIMEOUT and exponential backoff.
    """
    provider = _OAUTH_PROVIDERS[service]

    async def on_success(data: dict) -> dict:
        creds = provider["extract_creds"](data)
        token_store.set_credentials(user_id, service, creds)
        try:
            await db.log_event(user_id, "service_connected", service=service)
        except Exception:
            pass
        return {
            "status": "complete",
            "user": {"name": user_id, "user_id": user_id, "api_key": ""},
            "_service": service,
            "_created_at": time.time(),
        }

    await _poll_device_code(
        token_url=provider["token_url"],
        token_data=provider["token_data"](device_code),
        token_headers=provider["token_headers"] or None,
        on_success=on_success,
        setup_id=setup_id,
        expires_in=expires_in,
        interval=interval,
        display_name=provider["display_name"],
    )


@mcp.tool()
@concurrency_limited()
async def asibot_setup_status(setup_id: str = "") -> str:
    """Check if your account setup is complete. Call this after signing in via browser.

    Returns your API key and Claude Desktop config once sign-in is done.

    Args:
        setup_id: The setup ID returned by asibot_setup. If empty, checks the most recent setup.
    """
    async with _pending_setups_lock:
        _cleanup_pending_setups()
        if setup_id:
            result = _pending_setups.get(setup_id)
            # Fall back to DB if not in memory (e.g., after server restart)
            if result is None:
                result = await _load_setup_from_db(setup_id)
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
            await _delete_setup(setup_id)

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
            await _delete_setup(setup_id)
            return f"Setup failed: {error}\n\nTry asibot_setup again."

        if result["status"] == "expired":
            await _delete_setup(setup_id)
            return "Setup timed out. Try asibot_setup again."

        return "Still waiting for you to sign in via browser..."


@mcp.tool()
@concurrency_limited()
async def asibot_whoami(ctx: Context) -> str:
    """Check which user you're authenticated as."""
    user_id, err = user_session.require_user(ctx)
    if err:
        return err
    user = await auth.get_user_by_email(user_id)
    if user:
        return f"Authenticated as {user['name']} ({user['user_id']})"
    return f"Authenticated as {user_id}"


@mcp.tool()
@concurrency_limited()
async def asibot_rotate_key(ctx: Context) -> str:
    """Generate a new API key. The old key stops working immediately.

    After rotation, update your Claude Desktop config with the new key.
    """
    user_id, err = user_session.require_user(ctx)
    if err:
        return err
    user = await auth.rotate_key(user_id)
    if not user:
        return "Could not rotate key — user not found."
    user_session.invalidate_user_sessions(user_id)
    try:
        await db.log_event(user_id, "key_rotated")
    except Exception:
        pass
    return (
        f"API key rotated successfully.\n\n"
        f"New API key: {user['api_key']}\n\n"
        f"Update your Claude Desktop config:\n"
        f"{_config_snippet(user['api_key'])}\n\n"
        f"The old key no longer works. Restart Claude Desktop after updating."
    )


@mcp.tool()
@concurrency_limited()
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
        return await _start_device_code_flow("github", user_id)

    if service == "google" and settings.google_client_id:
        existing = token_store.get_credentials(user_id, "google")
        if existing:
            return f"Already connected to Google. To reconnect, disconnect first."
        return await _start_device_code_flow("google", user_id)

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
    return (
        f"To connect {service}, I need:\n{instructions}\n\n"
        f"Call asibot_set_credentials with:\n"
        f"  service: \"{service}\"\n"
        f"  credentials: {{{', '.join(f'\"{f}\": \"...\"' for f in fields)}}}\n\n"
        f"Stored securely per-user on the server."
    )


@mcp.tool()
@concurrency_limited()
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

    # Strip whitespace from all credential values
    creds = validation.strip_credential_values(creds)

    required_fields, _ = token_store.get_required_fields(service)
    missing = [f for f in required_fields if not creds.get(f)]
    if missing:
        return f"Missing required fields: {', '.join(missing)}"

    # Validate credential format before storing
    val_err = validation.validate_credentials(service, creds)
    if val_err:
        return val_err

    token_store.set_credentials(user_id, service, creds)
    try:
        await db.log_event(user_id, "service_connected", service=service)
    except Exception:
        pass
    return f"Connected to {service} successfully. Your credentials are stored securely."


@mcp.tool()
@concurrency_limited()
async def asibot_disconnect(service: str, ctx: Context) -> str:
    """Remove your credentials for a service.

    Args:
        service: Service name to disconnect
    """
    user_id, err = user_session.require_user(ctx)
    if err:
        return err

    token_store.remove_credentials(user_id, service)
    try:
        await db.log_event(user_id, "service_disconnected", service=service)
    except Exception:
        pass
    return f"Disconnected from {service}. Credentials removed."


@mcp.tool()
@concurrency_limited()
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
@concurrency_limited()
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
@concurrency_limited()
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
@concurrency_limited()
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


@mcp.tool()
async def asibot_health() -> str:
    """Check server health status. No authentication required."""
    connected_connectors = len(registry.list_all())
    async with _pending_setups_lock:
        pending = len(_pending_setups)
    checks = {
        "status": "ok",
        "data_dir_exists": settings.data_dir.exists(),
        "connectors_loaded": connected_connectors,
        "pending_setups": pending,
        "transport": settings.transport,
        "http_pool": http_pool.pool_stats(),
        "background_tasks": len(_background_tasks),
    }
    return json.dumps(checks, indent=2)


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
    _install_tool_tracking()
    _install_session_tracking()


# --- Entry Point ---


async def _prune_expired_setups_background() -> None:
    """Periodically prune expired setups from the DB (runs every 5 minutes)."""
    while True:
        await asyncio.sleep(300)
        db_backend = _get_db()
        if db_backend is not None:
            try:
                count = await db_backend.prune_expired_setups()
                if count:
                    logger.info("Pruned %d expired pending setup(s) from DB", count)
            except Exception as exc:
                logger.warning("Failed to prune expired setups: %s", exc)
        # Also clean the in-memory cache
        _cleanup_pending_setups()


async def _async_shutdown() -> None:
    """Gracefully shut down all resources."""
    logger.info("Shutting down: cancelling %d background tasks", len(_background_tasks))
    # Cancel all background tasks
    for task in list(_background_tasks):
        task.cancel()
    if _background_tasks:
        await asyncio.gather(*_background_tasks, return_exceptions=True)

    # Close connection pools, cached clients, and database
    await http_pool.close_all()
    await microsoft.close_all_clients()
    await db.close_pool()
    await db.close_db()
    logger.info("Shutdown complete")


def _cleanup() -> None:
    """Synchronous cleanup hook for atexit — runs async shutdown."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_async_shutdown())
        else:
            loop.run_until_complete(_async_shutdown())
    except RuntimeError:
        pass


async def _async_init() -> None:
    """Async initialisation tasks run once at startup."""
    await init_db_backend()
    # Start background cleanup task
    asyncio.create_task(_prune_expired_setups_background())


def _start_dashboard() -> None:
    """Start the analytics dashboard in a background daemon thread."""
    try:
        import threading
        import uvicorn
        from asibot.dashboard import app, _load_or_create_token, _dashboard_host, _dashboard_port

        token = _load_or_create_token()
        host = _dashboard_host()
        port = _dashboard_port()
        display_host = "localhost" if host == "0.0.0.0" else host
        url = f"http://{display_host}:{port}/?token={token}"
        link = f"\033]8;;{url}\033\\{url}\033]8;;\033\\"
        logger.info("Dashboard: %s", link)

        thread = threading.Thread(
            target=uvicorn.run,
            args=(app,),
            kwargs={"host": host, "port": port, "log_level": "warning"},
            daemon=True,
        )
        thread.start()
    except Exception:
        logger.warning("Failed to start dashboard", exc_info=True)


def main() -> None:
    settings.ensure_dirs()

    # Log production warnings (non-fatal)
    for warning in validate_for_production(settings):
        logger.warning("CONFIG: %s", warning)

    # Initialize distributed cache (S2S token cache + rate limiter)
    asyncio.run(distributed_cache.init_cache())

    _setup_connectors()
    metrics.start_metrics_server(
        port=settings.metrics_port,
        host=settings.metrics_host,
        bearer_token=settings.metrics_bearer_token,
    )
    atexit.register(_cleanup)

    # Run async init (DB backend, background tasks)
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    loop.run_until_complete(_async_init())

    transport = settings.transport
    logger.info("Asibot MCP server starting (transport=%s, data_dir=%s)", transport, settings.data_dir)

    # Log production readiness warnings
    for warn in settings.validate_for_production():
        logger.warning(warn)

    if transport == "streamable-http":
        if settings.port != 443 and not settings.allow_insecure_http:
            logger.error(
                "Refusing to start HTTP transport without TLS — API keys would be "
                "transmitted in plaintext. Either: (1) use a TLS-terminating reverse "
                "proxy on port 443, or (2) set ASIBOT_ALLOW_INSECURE_HTTP=true to override."
            )
            raise SystemExit(1)
        scheme = "https" if settings.port == 443 else "http"
        logger.info("Listening on %s://%s:%d/mcp", scheme, settings.host, settings.port)
        if settings.port != 443:
            logger.warning(
                "Running HTTP without TLS (ASIBOT_ALLOW_INSECURE_HTTP=true). "
                "Use a reverse proxy with TLS termination in production."
            )
        if settings.dashboard_enabled:
            _start_dashboard()
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
