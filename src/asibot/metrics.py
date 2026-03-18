"""Prometheus metrics for monitoring and observability.

Optional dependency: metrics are no-ops if prometheus_client is not installed.
When enabled, exposes a /metrics endpoint on a configurable host/port
(default 127.0.0.1:9090 — localhost only for security).

Instrumented:
- Request duration and count by tool/service/status
- Circuit breaker states per service
- Active sessions and cache hit/miss rates
- Auth failures
- HTTP connection pool stats
"""

import logging
import time
import uuid
from contextlib import contextmanager
from http.server import HTTPServer
from typing import Generator

logger = logging.getLogger(__name__)

try:
    from prometheus_client import Counter, Gauge, Histogram, MetricsHandler

    _HAS_PROMETHEUS = True
except ImportError:
    _HAS_PROMETHEUS = False


# --- No-op fallbacks when prometheus_client is not installed ---


class _NoOpMetric:
    """Quacks like a prometheus_client metric but does nothing."""

    def labels(self, **_kw):
        return self

    def inc(self, _amount=1):
        pass

    def dec(self, _amount=1):
        pass

    def set(self, _value):
        pass

    def observe(self, _value):
        pass


_NOOP = _NoOpMetric()

# --- Metrics definitions ---

if _HAS_PROMETHEUS:
    request_duration = Histogram(
        "asibot_request_duration_seconds",
        "Tool call HTTP request duration",
        ["service", "status"],
        buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0],
    )
    request_total = Counter(
        "asibot_requests_total",
        "Total HTTP requests made by connectors",
        ["service", "status"],
    )
    circuit_state = Gauge(
        "asibot_circuit_state",
        "Circuit breaker state (0=closed, 1=half_open, 2=open)",
        ["service"],
    )
    active_sessions = Gauge(
        "asibot_active_sessions",
        "Number of active user sessions",
    )
    auth_failures_total = Counter(
        "asibot_auth_failures_total",
        "Authentication failures",
        ["reason"],
    )
    session_cache_hits = Counter(
        "asibot_session_cache_hits_total",
        "Session cache hits (in-memory)",
    )
    session_cache_misses = Counter(
        "asibot_session_cache_misses_total",
        "Session cache misses (fell through to DB)",
    )
else:
    request_duration = _NOOP
    request_total = _NOOP
    circuit_state = _NOOP
    active_sessions = _NOOP
    auth_failures_total = _NOOP
    session_cache_hits = _NOOP
    session_cache_misses = _NOOP


# --- Request correlation IDs ---


def new_request_id() -> str:
    """Generate a short correlation ID for request tracing."""
    return uuid.uuid4().hex[:8]


# --- Timer context manager ---


@contextmanager
def track_request(service: str) -> Generator[dict, None, None]:
    """Context manager that tracks request duration and status.

    Usage:
        with track_request("GitHub") as ctx:
            # ... make request ...
            ctx["status"] = "ok"  # or "error", "circuit_open", etc.
    """
    ctx = {"status": "ok"}
    start = time.monotonic()
    try:
        yield ctx
    finally:
        duration = time.monotonic() - start
        status = ctx.get("status", "error")
        request_duration.labels(service=service, status=status).observe(duration)
        request_total.labels(service=service, status=status).inc()


# --- Metrics server ---

_metrics_started = False


def _make_auth_handler(bearer_token: str):
    """Create a MetricsHandler subclass that checks Bearer token auth."""

    class AuthMetricsHandler(MetricsHandler):
        def do_GET(self):
            auth_header = self.headers.get("Authorization", "")
            if auth_header != f"Bearer {bearer_token}":
                self.send_response(401)
                self.send_header("WWW-Authenticate", "Bearer")
                self.end_headers()
                self.wfile.write(b"Unauthorized\n")
                return
            return super().do_GET()

        def log_message(self, format, *args):
            # Suppress per-request log noise from the metrics server
            pass

    return AuthMetricsHandler


def start_metrics_server(
    port: int = 9090,
    host: str = "127.0.0.1",
    bearer_token: str = "",
) -> None:
    """Start the Prometheus metrics HTTP server.

    Args:
        port: Port to listen on (default 9090).
        host: Bind address (default "127.0.0.1" — localhost only).
              Use "0.0.0.0" only behind a reverse proxy or firewall.
        bearer_token: If set, require ``Authorization: Bearer <token>``
              on every request to the metrics endpoint.

    Safe to call if prometheus_client is not installed (no-op).
    Safe to call multiple times (idempotent).
    """
    global _metrics_started
    if _metrics_started or not _HAS_PROMETHEUS:
        return
    try:
        if bearer_token:
            handler = _make_auth_handler(bearer_token)
        else:
            handler = MetricsHandler
        server = HTTPServer((host, port), handler)
        from threading import Thread
        t = Thread(target=server.serve_forever, daemon=True)
        t.start()
        _metrics_started = True
        logger.info("Metrics server started on %s:%d%s", host, port,
                     " (bearer auth enabled)" if bearer_token else "")
    except OSError as e:
        logger.warning("Failed to start metrics server on %s:%d: %s", host, port, e)
