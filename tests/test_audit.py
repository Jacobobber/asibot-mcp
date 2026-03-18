"""Tests for audit logging."""

import json
from unittest.mock import patch

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
