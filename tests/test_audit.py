"""Tests for audit logging."""

import json
import logging
from unittest.mock import patch, MagicMock

from asibot import audit
from asibot.config import settings


def test_log_tool_call_writes_entry(tmp_path):
    """Audit log should write a JSON entry to the audit log file."""
    audit._audit_logger = None  # Reset singleton
    with patch.object(settings, "data_dir", tmp_path):
        audit.log_tool_call("alice@example.com", "search_documents", {"query": "hello"})

    log_file = tmp_path / "audit.log"
    assert log_file.exists()
    entry = json.loads(log_file.read_text().strip())
    assert entry["user"] == "alice@example.com"
    assert entry["tool"] == "search_documents"
    assert entry["args"]["query"] == "hello"
    assert "ts" in entry
    audit._audit_logger = None  # Cleanup


def test_log_tool_call_redacts_secrets(tmp_path):
    """Credential-like args should be redacted."""
    audit._audit_logger = None
    with patch.object(settings, "data_dir", tmp_path):
        audit.log_tool_call("bob@example.com", "asibot_set_credentials", {
            "service": "github",
            "token": "ghp_secretvalue",
            "api_key": "sk-12345",
        })

    log_file = tmp_path / "audit.log"
    entry = json.loads(log_file.read_text().strip())
    assert entry["args"]["service"] == "github"
    assert entry["args"]["token"] == "***"
    assert entry["args"]["api_key"] == "***"
    audit._audit_logger = None


def test_log_tool_call_anonymous(tmp_path):
    """Anonymous user should be recorded as 'anonymous'."""
    audit._audit_logger = None
    with patch.object(settings, "data_dir", tmp_path):
        audit.log_tool_call("", "list_sources")

    log_file = tmp_path / "audit.log"
    entry = json.loads(log_file.read_text().strip())
    assert entry["user"] == "anonymous"
    audit._audit_logger = None


def test_log_tool_call_no_args(tmp_path):
    """Tool call without args should not include args key."""
    audit._audit_logger = None
    with patch.object(settings, "data_dir", tmp_path):
        audit.log_tool_call("user@example.com", "asibot_whoami")

    log_file = tmp_path / "audit.log"
    entry = json.loads(log_file.read_text().strip())
    assert "args" not in entry
    audit._audit_logger = None


def test_redacts_access_token(tmp_path):
    """access_token should be redacted."""
    audit._audit_logger = None
    with patch.object(settings, "data_dir", tmp_path):
        audit.log_tool_call("u@e.com", "tool", {"access_token": "secret123"})
    entry = json.loads((tmp_path / "audit.log").read_text().strip())
    assert entry["args"]["access_token"] == "***"
    audit._audit_logger = None


def test_redacts_client_secret(tmp_path):
    """client_secret should be redacted."""
    audit._audit_logger = None
    with patch.object(settings, "data_dir", tmp_path):
        audit.log_tool_call("u@e.com", "tool", {"client_secret": "cs_123"})
    entry = json.loads((tmp_path / "audit.log").read_text().strip())
    assert entry["args"]["client_secret"] == "***"
    audit._audit_logger = None


def test_redacts_authorization_header(tmp_path):
    """authorization header should be redacted."""
    audit._audit_logger = None
    with patch.object(settings, "data_dir", tmp_path):
        audit.log_tool_call("u@e.com", "tool", {"authorization": "Bearer xyz"})
    entry = json.loads((tmp_path / "audit.log").read_text().strip())
    assert entry["args"]["authorization"] == "***"
    audit._audit_logger = None


def test_redacts_refresh_token(tmp_path):
    """refresh_token should be redacted."""
    audit._audit_logger = None
    with patch.object(settings, "data_dir", tmp_path):
        audit.log_tool_call("u@e.com", "tool", {"refresh_token": "rt_abc"})
    entry = json.loads((tmp_path / "audit.log").read_text().strip())
    assert entry["args"]["refresh_token"] == "***"
    audit._audit_logger = None


def test_redacts_private_key(tmp_path):
    """private_key should be redacted."""
    audit._audit_logger = None
    with patch.object(settings, "data_dir", tmp_path):
        audit.log_tool_call("u@e.com", "tool", {"private_key": "-----BEGIN RSA"})
    entry = json.loads((tmp_path / "audit.log").read_text().strip())
    assert entry["args"]["private_key"] == "***"
    audit._audit_logger = None


def test_does_not_redact_safe_keys(tmp_path):
    """Non-secret keys should not be redacted."""
    audit._audit_logger = None
    with patch.object(settings, "data_dir", tmp_path):
        audit.log_tool_call("u@e.com", "tool", {"query": "hello", "service": "github", "limit": 10})
    entry = json.loads((tmp_path / "audit.log").read_text().strip())
    assert entry["args"]["query"] == "hello"
    assert entry["args"]["service"] == "github"
    assert entry["args"]["limit"] == 10
    audit._audit_logger = None


def test_db_failure_falls_back_to_jsonl(tmp_path):
    """When primary audit logger fails, entry should be written to JSONL fallback."""
    audit._audit_logger = None
    audit.audit_write_failures_total = 0

    with patch.object(settings, "data_dir", tmp_path):
        # Set up an audit logger that will raise on .info()
        mock_logger = MagicMock()
        mock_logger.info.side_effect = OSError("disk full")
        with patch.object(audit, "_get_audit_logger", return_value=mock_logger):
            with patch("asibot.audit.time.sleep"):  # Skip the retry delay
                audit.log_tool_call("u@e.com", "tool", {"query": "test"})

    # Should have fallen back to JSONL
    fallback_file = tmp_path / "audit_fallback.jsonl"
    assert fallback_file.exists()
    entry = json.loads(fallback_file.read_text().strip())
    assert entry["user"] == "u@e.com"
    assert entry["tool"] == "tool"
    assert audit.audit_write_failures_total == 1
    audit._audit_logger = None


def test_both_failures_logged_as_critical(tmp_path, caplog):
    """When both primary and JSONL fallback fail, a CRITICAL log should be emitted."""
    audit._audit_logger = None
    audit.audit_write_failures_total = 0

    with patch.object(settings, "data_dir", tmp_path):
        mock_logger = MagicMock()
        mock_logger.info.side_effect = OSError("disk full")
        with (
            patch.object(audit, "_get_audit_logger", return_value=mock_logger),
            patch.object(audit, "_write_jsonl_fallback", side_effect=OSError("fallback also broken")),
            patch("asibot.audit.time.sleep"),  # Skip the retry delay
            caplog.at_level(logging.CRITICAL, logger="asibot.audit"),
        ):
            audit.log_tool_call("u@e.com", "critical_tool")

    # Should have logged a CRITICAL message with the entry details
    critical_msgs = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert len(critical_msgs) >= 1
    assert "AUDIT LOSS" in critical_msgs[0].message
    assert "critical_tool" in critical_msgs[0].message
    assert audit.audit_write_failures_total >= 1
    audit._audit_logger = None


def test_retry_succeeds_on_second_attempt(tmp_path):
    """Transient error on first attempt should succeed on retry."""
    audit._audit_logger = None

    with patch.object(settings, "data_dir", tmp_path):
        mock_logger = MagicMock()
        # First call raises, second succeeds
        mock_logger.info.side_effect = [OSError("transient"), None]
        with (
            patch.object(audit, "_get_audit_logger", return_value=mock_logger),
            patch("asibot.audit.time.sleep"),  # Skip the retry delay
        ):
            audit.log_tool_call("u@e.com", "retry_tool")

    # Should have called info twice (first failed, second succeeded)
    assert mock_logger.info.call_count == 2
    # No fallback file should be created since retry succeeded
    fallback_file = tmp_path / "audit_fallback.jsonl"
    assert not fallback_file.exists()
    audit._audit_logger = None
