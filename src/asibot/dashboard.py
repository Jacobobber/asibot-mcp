"""Asibot analytics dashboard — Starlette ASGI app on port 8081.

Run:
    python -m asibot.dashboard
    asibot-dashboard          (via entry-point)
"""

import logging
import secrets
import stat
import time
from dataclasses import dataclass
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from asibot.analytics import get_summary
from asibot.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

_PUBLIC_PATHS: set[str] = {"/health"}

_TOKEN: str | None = None


def _load_or_create_token() -> str:
    """Return the dashboard bearer token, creating one if needed."""
    global _TOKEN
    if _TOKEN is not None:
        return _TOKEN

    token_path = settings.data_dir / "dashboard_token"
    if token_path.exists():
        _TOKEN = token_path.read_text().strip()
    else:
        _TOKEN = secrets.token_urlsafe(32)
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        token_path.write_text(_TOKEN + "\n")
        token_path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 600
        logger.debug("Dashboard token created: %s...", _TOKEN[:8])
        logger.debug("Dashboard access: http://localhost:%s/?token=<see %s>", _dashboard_port(), token_path)

    return _TOKEN


def _dashboard_port() -> int:
    return getattr(settings, "dashboard_port", 8081)


def _dashboard_host() -> str:
    return getattr(settings, "dashboard_host", "0.0.0.0")


@dataclass(frozen=True)
class AuthContext:
    """Result of dashboard authentication."""
    role: str  # "admin" or "user"
    user_id: str | None  # None for legacy admin token (full access)


def _check_auth(request: Request) -> AuthContext | None:
    """Validate bearer token from header or query param.

    Returns AuthContext on success, None on failure.
    Legacy static token → admin with no user scope.
    Per-user token → role and user_id from token entry.
    """
    # Extract token
    token = None
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
    if not token:
        token = request.query_params.get("token")
    if not token:
        return None

    # Check legacy static admin token
    legacy = _load_or_create_token()
    if token == legacy:
        return AuthContext(role="admin", user_id=None)

    # Check per-user dashboard token
    from asibot import dashboard_tokens
    entry = dashboard_tokens.validate_token(token)
    if entry is not None:
        return AuthContext(role=entry.role, user_id=entry.user_id)

    return None


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------

_cache: dict[int, tuple[dict, float]] = {}
_CACHE_TTL = 60  # seconds


async def _get_cached_summary(days: int, user_id: str | None = None) -> dict:
    cache_key = (days, user_id)
    now = time.time()
    if cache_key in _cache:
        result, ts = _cache[cache_key]
        if now - ts < _CACHE_TTL:
            return result
    result = await get_summary(days=days, user_id=user_id)
    _cache[cache_key] = (result, now)
    return result


def _parse_days(request: Request) -> int:
    try:
        days = int(request.query_params.get("days", "30"))
    except (ValueError, TypeError):
        days = 30
    return max(1, min(days, 365))


# ---------------------------------------------------------------------------
# HTML dashboard
# ---------------------------------------------------------------------------

_HTML_CACHE: str | None = None


def _read_html() -> str:
    global _HTML_CACHE
    if _HTML_CACHE is None:
        html_path = Path(__file__).parent / "static" / "dashboard.html"
        _HTML_CACHE = html_path.read_text(encoding="utf-8")
    return _HTML_CACHE


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


async def index(request: Request) -> Response:
    if _check_auth(request) is None:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    return HTMLResponse(_read_html())


async def health(request: Request) -> Response:
    return JSONResponse({"status": "ok"})


async def api_summary(request: Request) -> Response:
    ctx = _check_auth(request)
    if ctx is None:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    days = _parse_days(request)
    scope_user = None if ctx.role == "admin" else ctx.user_id
    s = await _get_cached_summary(days, user_id=scope_user)

    time_saved = s.get("time_saved_minutes", 0)
    return JSONResponse({
        "total_calls": s.get("total_calls", 0),
        "unique_users": s.get("unique_users", 0),
        "active_services": s.get("active_services", 0),
        "error_rate": s.get("error_rate", 0.0),
        "avg_latency_ms": s.get("avg_latency_ms", 0),
        "p50_latency_ms": s.get("p50_latency_ms", 0),
        "p95_latency_ms": s.get("p95_latency_ms", 0),
        "time_saved_minutes": time_saved,
        "time_saved_hours": round(time_saved / 60, 1) if time_saved else 0,
        "period": {
            "start": s.get("period_start", ""),
            "end": s.get("period_end", ""),
            "days": s.get("days", days),
        },
        "lifecycle": s.get("lifecycle", {}),
    })


async def api_usage(request: Request) -> Response:
    ctx = _check_auth(request)
    if ctx is None:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    days = _parse_days(request)
    scope_user = None if ctx.role == "admin" else ctx.user_id
    s = await _get_cached_summary(days, user_id=scope_user)

    return JSONResponse({
        "calls_by_day": s.get("calls_by_day", []),
        "calls_by_hour": s.get("calls_by_hour", [0] * 24),
        "calls_by_service": s.get("calls_by_service", {}),
        "top_tools": s.get("top_tools", []),
    })


async def api_services(request: Request) -> Response:
    ctx = _check_auth(request)
    if ctx is None:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    days = _parse_days(request)
    scope_user = None if ctx.role == "admin" else ctx.user_id
    s = await _get_cached_summary(days, user_id=scope_user)

    calls_by_service = s.get("calls_by_service", {})
    errors_by_service = s.get("errors_by_service", {})
    latency_by_service = s.get("latency_by_service", {})
    calls_by_user = s.get("calls_by_user", {})

    # Build per-user service sets from the raw data if available
    # We approximate users per service from available data
    services = []
    for svc, calls in calls_by_service.items():
        errs = errors_by_service.get(svc, 0)
        lat = latency_by_service.get(svc, {})
        err_rate = round(errs / calls, 4) if calls else 0.0

        # Count users who have used this service — approximate from tool names
        user_count = 0
        if calls_by_user:
            # We don't have per-service-per-user data in the summary,
            # so we use unique_users as a fallback
            user_count = s.get("unique_users", 0)

        services.append({
            "name": svc,
            "calls": calls,
            "errors": errs,
            "error_rate": err_rate,
            "avg_latency_ms": lat.get("avg", 0) if isinstance(lat, dict) else 0,
            "p95_latency_ms": lat.get("p95", 0) if isinstance(lat, dict) else 0,
            "users": user_count,
        })

    services.sort(key=lambda x: x["calls"], reverse=True)
    return JSONResponse({"services": services})


async def api_users(request: Request) -> Response:
    ctx = _check_auth(request)
    if ctx is None:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    days = _parse_days(request)
    scope_user = None if ctx.role == "admin" else ctx.user_id
    s = await _get_cached_summary(days, user_id=scope_user)

    top_users = s.get("top_users", [])
    calls_by_user = s.get("calls_by_user", {})

    users = []
    for entry in top_users:
        user_id = entry.get("user", "unknown")
        users.append({
            "user": user_id,
            "calls": entry.get("count", 0),
            "services_used": entry.get("services_used", 0),
            "last_active": entry.get("last_active", ""),
            "top_tool": entry.get("top_tool", ""),
        })

    # If top_users is empty but we have calls_by_user, build from that
    if not users and calls_by_user:
        for uid, cnt in sorted(calls_by_user.items(), key=lambda x: x[1], reverse=True):
            users.append({
                "user": uid,
                "calls": cnt,
                "services_used": 0,
                "last_active": "",
                "top_tool": "",
            })

    return JSONResponse({"users": users})


async def api_errors(request: Request) -> Response:
    ctx = _check_auth(request)
    if ctx is None:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    days = _parse_days(request)
    scope_user = None if ctx.role == "admin" else ctx.user_id
    s = await _get_cached_summary(days, user_id=scope_user)

    return JSONResponse({
        "error_count": s.get("error_count", 0),
        "error_rate": s.get("error_rate", 0.0),
        "errors_by_service": s.get("errors_by_service", {}),
        "errors_by_type": s.get("errors_by_type", {}),
    })


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = Starlette(
    routes=[
        Route("/", index),
        Route("/health", health),
        Route("/api/summary", api_summary),
        Route("/api/usage", api_usage),
        Route("/api/services", api_services),
        Route("/api/users", api_users),
        Route("/api/errors", api_errors),
    ],
)


def main() -> None:
    """Entry point: ``python -m asibot.dashboard`` or ``asibot-dashboard``."""
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    token = _load_or_create_token()
    host = _dashboard_host()
    port = _dashboard_port()
    display_host = "localhost" if host == "0.0.0.0" else host
    url = f"http://{display_host}:{port}/?token={token}"

    # OSC 8 hyperlink: \e]8;;URL\e\\LABEL\e]8;;\e\\
    link = f"\033]8;;{url}\033\\{url}\033]8;;\033\\"
    print(f"\n  Dashboard: {link}\n")

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
