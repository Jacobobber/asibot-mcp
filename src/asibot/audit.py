"""Audit logging: records tool invocations with user identity and timestamps.

Writes structured log entries to ~/.asibot/audit.log (append-only).
Falls back to a JSONL file if the primary audit logger fails.
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

# Metrics counters
audit_write_failures_total = 0


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


def _write_jsonl_fallback(entry: dict) -> None:
    """Append an audit entry to the JSONL fallback file."""
    fallback_path = settings.data_dir / "audit_fallback.jsonl"
    fallback_path.parent.mkdir(parents=True, exist_ok=True)
    with open(fallback_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _redact_args(args: dict) -> dict:
    """Redact credential-like values from args."""
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
    return safe_args


def log_tool_call(user_id: str, tool_name: str, args: dict | None = None) -> None:
    """Record a tool invocation.

    Tries the primary rotating audit log first.  On failure, retries once after
    a short delay.  If the primary log still fails, falls back to a JSONL file.
    If both fail, logs a CRITICAL error so the event is never silently lost.
    """
    global audit_write_failures_total

    entry = {
        "ts": time.time(),
        "user": user_id or "anonymous",
        "tool": tool_name,
    }
    if args:
        entry["args"] = _redact_args(args)

    line = json.dumps(entry)

    # Attempt primary write with one retry for transient errors
    for attempt in range(2):
        try:
            _get_audit_logger().info(line)
            return  # Success
        except Exception as primary_exc:
            if attempt == 0:
                logger.warning(
                    "Audit log write failed (attempt 1), retrying: %s", primary_exc
                )
                time.sleep(1)
            else:
                logger.error(
                    "Audit log write failed after retry: %s", primary_exc
                )

    # Primary failed — try JSONL fallback
    audit_write_failures_total += 1
    try:
        _write_jsonl_fallback(entry)
        logger.warning(
            "Audit entry written to JSONL fallback after primary failure"
        )
        return
    except Exception as fallback_exc:
        # Both primary and fallback failed — log critical with full details
        logger.critical(
            "AUDIT LOSS: both primary and fallback writes failed. "
            "Primary error: N/A (retries exhausted). "
            "Fallback error: %s. Entry: %s",
            fallback_exc,
            line,
        )
