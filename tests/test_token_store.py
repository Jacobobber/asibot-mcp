"""Tests for credential storage and permission enforcement."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx

from asibot import crypto, token_store, user_session
from asibot.config import settings


def _mock_ctx(user_id: str | None = "testuser@example.com"):
    """Create a mock MCP Context that resolves to the given user_id."""
    return MagicMock()


def _setup_user_dir(tmp_path: Path, user_id: str = "testuser@example.com") -> Path:
    """Create a temp user data dir and patch user_session to use it."""
    user_dir = tmp_path / "users" / user_id.replace("@", "_at_")
    user_dir.mkdir(parents=True)
    return user_dir


def _patch_crypto(tmp_path: Path):
    """Patch settings.data_dir and reset Fernet for fresh encryption key."""
    crypto.reset_fernet()
    return patch.object(settings, "data_dir", tmp_path)


class TestCredentials:
    def test_set_and_get_credentials(self, tmp_path):
        user_dir = _setup_user_dir(tmp_path)
        with _patch_crypto(tmp_path), patch.object(user_session, "get_user_data_dir", return_value=user_dir):
            token_store.set_credentials("testuser@example.com", "github", {"token": "ghp_xxx", "org": "myorg"})
            creds = token_store.get_credentials("testuser@example.com", "github")
            assert creds["token"] == "ghp_xxx"
            assert creds["org"] == "myorg"

    def test_get_credentials_not_set(self, tmp_path):
        user_dir = _setup_user_dir(tmp_path)
        with _patch_crypto(tmp_path), patch.object(user_session, "get_user_data_dir", return_value=user_dir):
            assert token_store.get_credentials("testuser@example.com", "github") == {}

    def test_remove_credentials(self, tmp_path):
        user_dir = _setup_user_dir(tmp_path)
        with _patch_crypto(tmp_path), patch.object(user_session, "get_user_data_dir", return_value=user_dir):
            token_store.set_credentials("testuser@example.com", "github", {"token": "ghp_xxx"})
            token_store.remove_credentials("testuser@example.com", "github")
            assert token_store.get_credentials("testuser@example.com", "github") == {}

    def test_list_connected(self, tmp_path):
        user_dir = _setup_user_dir(tmp_path)
        with _patch_crypto(tmp_path), patch.object(user_session, "get_user_data_dir", return_value=user_dir):
            token_store.set_credentials("testuser@example.com", "github", {"token": "x"})
            token_store.set_credentials("testuser@example.com", "notion", {"token": "y"})
            connected = token_store.list_connected("testuser@example.com")
            assert "github" in connected
            assert "notion" in connected

    def test_corrupted_credentials_file(self, tmp_path):
        user_dir = _setup_user_dir(tmp_path)
        (user_dir / "credentials.json").write_bytes(b"\x00\x01\x02 garbage")
        with _patch_crypto(tmp_path), patch.object(user_session, "get_user_data_dir", return_value=user_dir):
            assert token_store.get_credentials("testuser@example.com", "github") == {}

    def test_multiple_services_isolated(self, tmp_path):
        user_dir = _setup_user_dir(tmp_path)
        with _patch_crypto(tmp_path), patch.object(user_session, "get_user_data_dir", return_value=user_dir):
            token_store.set_credentials("testuser@example.com", "github", {"token": "gh"})
            token_store.set_credentials("testuser@example.com", "notion", {"token": "nt"})
            assert token_store.get_credentials("testuser@example.com", "github")["token"] == "gh"
            assert token_store.get_credentials("testuser@example.com", "notion")["token"] == "nt"

    def test_credentials_encrypted_on_disk(self, tmp_path):
        user_dir = _setup_user_dir(tmp_path)
        with _patch_crypto(tmp_path), patch.object(user_session, "get_user_data_dir", return_value=user_dir):
            token_store.set_credentials("testuser@example.com", "github", {"token": "ghp_supersecret"})
        raw = (user_dir / "credentials.json").read_bytes()
        assert b"ghp_supersecret" not in raw


class TestPreferences:
    def test_set_and_get_prefs(self, tmp_path):
        user_dir = _setup_user_dir(tmp_path)
        with _patch_crypto(tmp_path), patch.object(user_session, "get_user_data_dir", return_value=user_dir):
            token_store.set_service_prefs("testuser@example.com", "github", enabled=True, mode="readwrite")
            prefs = token_store.get_service_prefs("testuser@example.com", "github")
            assert prefs["enabled"] is True
            assert prefs["mode"] == "readwrite"

    def test_default_prefs_empty(self, tmp_path):
        user_dir = _setup_user_dir(tmp_path)
        with _patch_crypto(tmp_path), patch.object(user_session, "get_user_data_dir", return_value=user_dir):
            assert token_store.get_service_prefs("testuser@example.com", "github") == {}

    def test_disable_service(self, tmp_path):
        user_dir = _setup_user_dir(tmp_path)
        with _patch_crypto(tmp_path), patch.object(user_session, "get_user_data_dir", return_value=user_dir):
            token_store.set_service_prefs("testuser@example.com", "github", enabled=False, mode="read")
            prefs = token_store.get_service_prefs("testuser@example.com", "github")
            assert prefs["enabled"] is False


class TestPermissions:
    def test_check_permission_enabled_read(self, tmp_path):
        user_dir = _setup_user_dir(tmp_path)
        ctx = _mock_ctx()
        with (
            _patch_crypto(tmp_path),
            patch.object(user_session, "get_user_data_dir", return_value=user_dir),
            patch.object(user_session, "require_user", return_value=("testuser@example.com", None)),
        ):
            token_store.set_service_prefs("testuser@example.com", "github", enabled=True, mode="read")
            uid, err = token_store.check_permission(ctx, "github", "read")
            assert uid == "testuser@example.com"
            assert err is None

    def test_check_permission_disabled(self, tmp_path):
        user_dir = _setup_user_dir(tmp_path)
        ctx = _mock_ctx()
        with (
            _patch_crypto(tmp_path),
            patch.object(user_session, "get_user_data_dir", return_value=user_dir),
            patch.object(user_session, "require_user", return_value=("testuser@example.com", None)),
        ):
            token_store.set_service_prefs("testuser@example.com", "github", enabled=False, mode="read")
            uid, err = token_store.check_permission(ctx, "github", "read")
            assert uid is None
            assert "disabled" in err

    def test_check_permission_write_blocked_in_read_mode(self, tmp_path):
        user_dir = _setup_user_dir(tmp_path)
        ctx = _mock_ctx()
        with (
            _patch_crypto(tmp_path),
            patch.object(user_session, "get_user_data_dir", return_value=user_dir),
            patch.object(user_session, "require_user", return_value=("testuser@example.com", None)),
        ):
            token_store.set_service_prefs("testuser@example.com", "github", enabled=True, mode="read")
            uid, err = token_store.check_permission(ctx, "github", "write")
            assert uid is None
            assert "read-only" in err

    def test_check_permission_write_allowed_in_readwrite(self, tmp_path):
        user_dir = _setup_user_dir(tmp_path)
        ctx = _mock_ctx()
        with (
            _patch_crypto(tmp_path),
            patch.object(user_session, "get_user_data_dir", return_value=user_dir),
            patch.object(user_session, "require_user", return_value=("testuser@example.com", None)),
        ):
            token_store.set_service_prefs("testuser@example.com", "github", enabled=True, mode="readwrite")
            uid, err = token_store.check_permission(ctx, "github", "write")
            assert uid == "testuser@example.com"
            assert err is None

    def test_check_permission_unauthenticated(self, tmp_path):
        ctx = _mock_ctx()
        with _patch_crypto(tmp_path), patch.object(user_session, "require_user", return_value=(None, "Not authenticated")):
            uid, err = token_store.check_permission(ctx, "github", "read")
            assert uid is None
            assert "Not authenticated" in err

    def test_require_service_no_credentials(self, tmp_path):
        user_dir = _setup_user_dir(tmp_path)
        ctx = _mock_ctx()
        with (
            _patch_crypto(tmp_path),
            patch.object(user_session, "get_user_data_dir", return_value=user_dir),
            patch.object(user_session, "require_user", return_value=("testuser@example.com", None)),
        ):
            client, uid, err = token_store.require_service(ctx, "github", lambda c: None, "read")
            assert client is None
            assert "Not connected" in err

    def test_require_service_success(self, tmp_path):
        user_dir = _setup_user_dir(tmp_path)
        ctx = _mock_ctx()
        mock_client = MagicMock()
        with (
            _patch_crypto(tmp_path),
            patch.object(user_session, "get_user_data_dir", return_value=user_dir),
            patch.object(user_session, "require_user", return_value=("testuser@example.com", None)),
        ):
            token_store.set_credentials("testuser@example.com", "github", {"token": "ghp_xxx"})
            client, uid, err = token_store.require_service(ctx, "github", lambda c: mock_client, "read")
            assert client is mock_client
            assert uid == "testuser@example.com"
            assert err is None


class TestServiceSchemas:
    def test_all_schemas_have_required_keys(self):
        for name, schema in token_store.SERVICE_SCHEMAS.items():
            assert "fields" in schema, f"{name} missing 'fields'"
            assert "labels" in schema, f"{name} missing 'labels'"
            assert len(schema["fields"]) == len(schema["labels"]), f"{name} fields/labels length mismatch"
            assert len(schema["fields"]) > 0, f"{name} has no fields"


class TestClientSpec:
    def test_build_bearer_client(self):
        spec = token_store.ClientSpec(required_fields=("token",))
        client = token_store.build_client(spec, {"token": "abc123"})
        assert client is not None
        assert client.headers["Authorization"] == "Bearer abc123"

    def test_build_bearer_missing_field(self):
        spec = token_store.ClientSpec(required_fields=("token",))
        assert token_store.build_client(spec, {}) is None
        assert token_store.build_client(spec, {"token": ""}) is None

    def test_build_basic_client(self):
        spec = token_store.ClientSpec(
            required_fields=("email", "api_token", "domain"),
            auth_type="basic",
            base_url="https://{domain}/api",
        )
        client = token_store.build_client(spec, {"email": "a@b.com", "api_token": "tk", "domain": "example.com"})
        assert client is not None
        assert str(client.base_url).rstrip("/") == "https://example.com/api"

    def test_build_basic_with_suffix(self):
        spec = token_store.ClientSpec(
            required_fields=("email", "api_token", "subdomain"),
            auth_type="basic",
            basic_user_suffix="/token",
            base_url="https://{subdomain}.zendesk.com/api/v2",
        )
        client = token_store.build_client(
            spec, {"email": "a@b.com", "api_token": "tk", "subdomain": "myco"}
        )
        assert client is not None

    def test_build_api_key_client(self):
        spec = token_store.ClientSpec(
            required_fields=("api_key",),
            auth_type="api_key",
            api_key_header="X-API-Key",
            api_key_field="api_key",
        )
        client = token_store.build_client(spec, {"api_key": "sk_123"})
        assert client is not None
        assert client.headers["X-API-Key"] == "sk_123"

    def test_build_none_auth_client(self):
        spec = token_store.ClientSpec(
            required_fields=("account_id", "client_id", "client_secret"),
            auth_type="none",
        )
        client = token_store.build_client(
            spec, {"account_id": "a", "client_id": "b", "client_secret": "c"}
        )
        assert client is not None
        assert "Authorization" not in client.headers

    def test_build_with_extra_headers(self):
        spec = token_store.ClientSpec(
            required_fields=("token",),
            headers={"Accept": "application/json", "X-Custom": "val"},
        )
        client = token_store.build_client(spec, {"token": "t"})
        assert client.headers["Accept"] == "application/json"
        assert client.headers["X-Custom"] == "val"

    def test_all_registered_specs_valid(self):
        """Every spec in CLIENT_SPECS should have at least one required field."""
        for name, spec in token_store.CLIENT_SPECS.items():
            assert len(spec.required_fields) > 0, f"{name} has no required_fields"
            assert spec.auth_type in ("bearer", "basic", "api_key", "none"), (
                f"{name} has unknown auth_type: {spec.auth_type}"
            )

    def test_specs_cover_all_schemas(self):
        """Every non-Microsoft service in SERVICE_SCHEMAS should have a CLIENT_SPEC."""
        ms_services = set(token_store.MICROSOFT_SERVICES)
        for name in token_store.SERVICE_SCHEMAS:
            if name in ms_services:
                continue
            # "google" in schemas maps to "google" in specs
            assert name in token_store.CLIENT_SPECS or name == "google", (
                f"SERVICE_SCHEMAS has '{name}' but CLIENT_SPECS does not"
            )


class TestRequireServiceWithSpec:
    def test_spec_based_require_service(self, tmp_path):
        user_dir = _setup_user_dir(tmp_path)
        ctx = _mock_ctx()
        with (
            _patch_crypto(tmp_path),
            patch.object(user_session, "get_user_data_dir", return_value=user_dir),
            patch.object(user_session, "require_user", return_value=("testuser@example.com", None)),
        ):
            token_store.set_credentials("testuser@example.com", "github", {"token": "ghp_test"})
            client, uid, err = token_store.require_service(ctx, "github", level="read")
            assert client is not None
            assert uid == "testuser@example.com"
            assert err is None
            assert client.headers["Authorization"] == "Bearer ghp_test"

    def test_spec_based_no_spec_registered(self, tmp_path):
        user_dir = _setup_user_dir(tmp_path)
        ctx = _mock_ctx()
        with (
            _patch_crypto(tmp_path),
            patch.object(user_session, "get_user_data_dir", return_value=user_dir),
            patch.object(user_session, "require_user", return_value=("testuser@example.com", None)),
        ):
            token_store.set_credentials("testuser@example.com", "unknown_svc", {"token": "x"})
            client, uid, err = token_store.require_service(ctx, "unknown_svc", level="read")
            assert client is None
            assert "No client configuration" in err

    def test_legacy_callback_still_works(self, tmp_path):
        user_dir = _setup_user_dir(tmp_path)
        ctx = _mock_ctx()
        mock_client = MagicMock()
        with (
            _patch_crypto(tmp_path),
            patch.object(user_session, "get_user_data_dir", return_value=user_dir),
            patch.object(user_session, "require_user", return_value=("testuser@example.com", None)),
        ):
            token_store.set_credentials("testuser@example.com", "github", {"token": "ghp_xxx"})
            client, uid, err = token_store.require_service(ctx, "github", lambda c: mock_client, "read")
            assert client is mock_client
            assert err is None


class TestFormatApiError:
    def test_http_status_error(self):
        response = MagicMock()
        response.status_code = 404
        error = httpx.HTTPStatusError("not found", request=MagicMock(), response=response)
        msg = token_store.format_api_error("GitHub", "get issue", error)
        assert msg == "GitHub get issue failed: HTTP 404"

    def test_request_error(self):
        error = httpx.RequestError("connection refused")
        msg = token_store.format_api_error("Jira", "search", error)
        assert msg == "Jira search failed: network error"

    def test_generic_error(self):
        error = ValueError("bad input")
        msg = token_store.format_api_error("Zoom", "list meetings", error)
        assert msg == "Zoom list meetings failed: bad input"


class TestSchemaVersioning:
    def test_new_credentials_get_version(self, tmp_path):
        user_dir = _setup_user_dir(tmp_path)
        with _patch_crypto(tmp_path), patch.object(user_session, "get_user_data_dir", return_value=user_dir):
            token_store.set_credentials("testuser@example.com", "github", {"token": "x"})
            data = token_store._load_creds("testuser@example.com")
            assert data.get("_schema_version") == token_store.CURRENT_SCHEMA_VERSION

    def test_legacy_data_migrated(self, tmp_path):
        user_dir = _setup_user_dir(tmp_path)
        with _patch_crypto(tmp_path), patch.object(user_session, "get_user_data_dir", return_value=user_dir):
            # Simulate legacy data without version
            from asibot.crypto import save_encrypted
            save_encrypted(user_dir / "credentials.json", {"github": {"token": "old"}})
            data = token_store._load_creds("testuser@example.com")
            assert data["_schema_version"] == token_store.CURRENT_SCHEMA_VERSION
            assert data["github"]["token"] == "old"

    def test_empty_data_not_migrated(self, tmp_path):
        user_dir = _setup_user_dir(tmp_path)
        with _patch_crypto(tmp_path), patch.object(user_session, "get_user_data_dir", return_value=user_dir):
            data = token_store._load_creds("testuser@example.com")
            assert data == {}


class TestApplyDefaults:
    """Test that server-configured business defaults are merged into credentials."""

    def test_github_org_injected(self, tmp_path):
        user_dir = _setup_user_dir(tmp_path)
        with (
            _patch_crypto(tmp_path),
            patch.object(user_session, "get_user_data_dir", return_value=user_dir),
            patch.object(settings, "github_org", "mycompany"),
        ):
            token_store.set_credentials("testuser@example.com", "github", {"token": "ghp_xxx"})
            creds = token_store.get_credentials("testuser@example.com", "github")
            assert creds["token"] == "ghp_xxx"
            assert creds["org"] == "mycompany"

    def test_user_value_overrides_default(self, tmp_path):
        user_dir = _setup_user_dir(tmp_path)
        with (
            _patch_crypto(tmp_path),
            patch.object(user_session, "get_user_data_dir", return_value=user_dir),
            patch.object(settings, "github_org", "default-org"),
        ):
            token_store.set_credentials("testuser@example.com", "github", {"token": "t", "org": "custom-org"})
            creds = token_store.get_credentials("testuser@example.com", "github")
            assert creds["org"] == "custom-org"  # user wins

    def test_atlassian_domain_and_email_injected(self, tmp_path):
        user_dir = _setup_user_dir(tmp_path)
        with (
            _patch_crypto(tmp_path),
            patch.object(user_session, "get_user_data_dir", return_value=user_dir),
            patch.object(settings, "atlassian_domain", "myco.atlassian.net"),
        ):
            from asibot import auth as real_auth
            with patch.object(real_auth, "_users", {}), patch.object(real_auth, "_store_path", tmp_path / "users.json"):
                real_auth.create_user("alice@myco.com", "Alice")
                token_store.set_credentials("alice@myco.com", "atlassian", {"api_token": "tok123"})
                creds = token_store.get_credentials("alice@myco.com", "atlassian")
                assert creds["api_token"] == "tok123"
                assert creds["domain"] == "myco.atlassian.net"
                assert creds["email"] == "alice@myco.com"

    def test_salesforce_instance_url_injected(self, tmp_path):
        user_dir = _setup_user_dir(tmp_path)
        with (
            _patch_crypto(tmp_path),
            patch.object(user_session, "get_user_data_dir", return_value=user_dir),
            patch.object(settings, "salesforce_instance_url", "https://myco.my.salesforce.com"),
        ):
            token_store.set_credentials("testuser@example.com", "salesforce", {"token": "sf_tok"})
            creds = token_store.get_credentials("testuser@example.com", "salesforce")
            assert creds["instance_url"] == "https://myco.my.salesforce.com"

    def test_no_defaults_when_config_empty(self, tmp_path):
        user_dir = _setup_user_dir(tmp_path)
        with (
            _patch_crypto(tmp_path),
            patch.object(user_session, "get_user_data_dir", return_value=user_dir),
            patch.object(settings, "github_org", ""),
        ):
            token_store.set_credentials("testuser@example.com", "github", {"token": "t"})
            creds = token_store.get_credentials("testuser@example.com", "github")
            assert "org" not in creds  # no default configured

    def test_empty_creds_not_merged(self, tmp_path):
        """get_credentials returns {} when no creds stored, even with defaults configured."""
        user_dir = _setup_user_dir(tmp_path)
        with (
            _patch_crypto(tmp_path),
            patch.object(user_session, "get_user_data_dir", return_value=user_dir),
            patch.object(settings, "github_org", "mycompany"),
        ):
            creds = token_store.get_credentials("testuser@example.com", "github")
            assert creds == {}


class TestGetRequiredFields:
    def test_github_token_only_when_org_configured(self):
        with patch.object(settings, "github_org", "mycompany"):
            fields, labels = token_store.get_required_fields("github")
            assert fields == ["token"]
            assert "org" not in fields

    def test_github_needs_org_when_not_configured(self):
        with patch.object(settings, "github_org", ""):
            fields, labels = token_store.get_required_fields("github")
            assert "token" in fields
            assert "org" in fields

    def test_atlassian_token_only_when_domain_configured(self):
        with patch.object(settings, "atlassian_domain", "myco.atlassian.net"):
            fields, labels = token_store.get_required_fields("atlassian")
            assert fields == ["api_token"]
            assert "domain" not in fields
            assert "email" not in fields

    def test_unknown_service_returns_empty(self):
        fields, labels = token_store.get_required_fields("nonexistent")
        assert fields == []
        assert labels == []
