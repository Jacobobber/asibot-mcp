"""Audit logging: records tool invocations with user identity and timestamps.

Writes structured log entries to ~/.asibot/audit.log (append-only).
"""

import json
import logging
import logging.handlers
import time
from pathlib import Path

from asibot.config import settings

logger = logging.getLogger(__name__)

_audit_logger: logging.Logger | None = None

# Rotation settings
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_BACKUP_COUNT = 5


def _get_audit_logger() -> logging.Logger:
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = logging.getLogger("asibot.audit")
        _audit_logger.setLevel(logging.INFO)
        _audit_logger.propagate = False
        log_path = settings.data_dir / "audit.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            str(log_path),
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        _audit_logger.addHandler(handler)
    return _audit_logger


def log_tool_call(user_id: str, tool_name: str, args: dict | None = None) -> None:
    """Record a tool invocation."""
    entry = {
        "ts": time.time(),
        "user": user_id or "anonymous",
        "tool": tool_name,
    }
    if args:
        # Redact credential-like values
        safe_args = {}
        for k, v in args.items():
            if any(secret in k.lower() for secret in (
                "token", "key", "secret", "password", "credential",
                "authorization", "bearer", "apikey", "api_key",
                "access_token", "refresh_token", "client_secret",
                "private_key", "session_id", "cookie",
            )):
                safe_args[k] = "***"
            else:
                safe_args[k] = v
        entry["args"] = safe_args
    try:
        _get_audit_logger().info(json.dumps(entry))
    except Exception:
        logger.debug("Failed to write audit log entry", exc_info=True)
