"""Tests for configuration validation rules and production warnings."""

import warnings

import pytest

from asibot.config import Settings, production_errors, validate_for_production


def _make(**overrides) -> Settings:
    """Create a Settings instance with test-friendly defaults.

    Overrides are applied on top of minimal valid defaults so that the
    model validator passes unless we intentionally break something.
    """
    defaults = {
        "transport": "stdio",
        "port": 8080,
        "metrics_port": 9090,
        "pg_pool_min_size": 5,
        "pg_pool_max_size": 50,
        "session_ttl": 3600,
        "absolute_session_ttl": 28800,
        "circuit_failure_threshold": 5,
        "circuit_recovery_timeout": 60.0,
        "max_retries": 3,
        "audit_retention_days": 365,
        "global_rate_limit_default": 200,
        "per_user_rate_limit_default": 30,
        "kms_provider": "",
    }
    defaults.update(overrides)
    return Settings(**defaults)


# ---- Default config passes ----


class TestDefaultConfigValid:
    def test_default_settings_pass(self):
        """The default configuration (stdio transport) should be valid."""
        s = _make()
        assert s.transport == "stdio"

    def test_http_transport_valid(self):
        s = _make(transport="streamable-http")
        assert s.transport == "streamable-http"


# ---- transport field validator ----


class TestTransportValidator:
    def test_valid_stdio(self):
        s = _make(transport="stdio")
        assert s.transport == "stdio"

    def test_valid_streamable_http(self):
        s = _make(transport="streamable-http")
        assert s.transport == "streamable-http"

    def test_invalid_transport(self):
        with pytest.raises(ValueError, match="transport must be one of"):
            _make(transport="websocket")

    def test_empty_transport(self):
        with pytest.raises(ValueError, match="transport must be one of"):
            _make(transport="")


# ---- pg_pool_min_size <= pg_pool_max_size ----


class TestPoolSizeValidation:
    def test_min_equals_max(self):
        s = _make(pg_pool_min_size=20, pg_pool_max_size=20)
        assert s.pg_pool_min_size == s.pg_pool_max_size

    def test_min_less_than_max(self):
        s = _make(pg_pool_min_size=5, pg_pool_max_size=100)
        assert s.pg_pool_min_size < s.pg_pool_max_size

    def test_min_greater_than_max_raises(self):
        with pytest.raises(ValueError, match="pg_pool_min_size.*pg_pool_max_size"):
            _make(pg_pool_min_size=100, pg_pool_max_size=10)

    def test_zero_min_valid(self):
        """Zero min pool size is technically valid (no warm connections)."""
        s = _make(pg_pool_min_size=0, pg_pool_max_size=10)
        assert s.pg_pool_min_size == 0


# ---- postgres_password warning (non-blocking) ----


class TestPostgresPasswordWarning:
    def test_no_warning_for_stdio(self):
        """stdio transport should not warn about empty postgres_password."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _make(transport="stdio", postgres_password="")
            pg_warnings = [x for x in w if "postgres_password" in str(x.message)]
            assert len(pg_warnings) == 0

    def test_no_warning_when_password_set(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _make(
                transport="streamable-http",
                postgres_password="secret",
            )
            pg_warnings = [x for x in w if "postgres_password" in str(x.message)]
            assert len(pg_warnings) == 0

    def test_warning_when_http_and_no_password(self):
        """HTTP transport with no password in database_url should warn."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _make(
                transport="streamable-http",
                postgres_password="",
                database_url="postgresql://asibot:@localhost:5432/asibot",
            )
            pg_warnings = [x for x in w if "postgres_password" in str(x.message)]
            assert len(pg_warnings) == 1

    def test_no_warning_when_password_in_url(self):
        """HTTP transport with password embedded in database_url should not warn."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _make(
                transport="streamable-http",
                postgres_password="",
                database_url="postgresql://asibot:goodpassword@localhost:5432/asibot",
            )
            pg_warnings = [x for x in w if "postgres_password" in str(x.message)]
            assert len(pg_warnings) == 0


# ---- Rate limits must be positive ----


class TestRateLimitValidation:
    def test_valid_rate_limits(self):
        s = _make(global_rate_limit_default=100, per_user_rate_limit_default=10)
        assert s.global_rate_limit_default == 100

    def test_zero_global_rate_limit_raises(self):
        with pytest.raises(ValueError, match="global_rate_limit_default must be a positive"):
            _make(global_rate_limit_default=0)

    def test_negative_global_rate_limit_raises(self):
        with pytest.raises(ValueError, match="global_rate_limit_default must be a positive"):
            _make(global_rate_limit_default=-5)

    def test_zero_per_user_rate_limit_raises(self):
        with pytest.raises(ValueError, match="per_user_rate_limit_default must be a positive"):
            _make(per_user_rate_limit_default=0)

    def test_negative_per_user_rate_limit_raises(self):
        with pytest.raises(ValueError, match="per_user_rate_limit_default must be a positive"):
            _make(per_user_rate_limit_default=-1)

    def test_invalid_service_rate_limit(self):
        with pytest.raises(ValueError, match="global_rate_limits.*github.*positive"):
            _make(global_rate_limits={"github": 0})

    def test_negative_service_rate_limit(self):
        with pytest.raises(ValueError, match="global_rate_limits.*salesforce.*positive"):
            _make(global_rate_limits={"salesforce": -10})

    def test_valid_service_rate_limits(self):
        s = _make(global_rate_limits={"github": 80, "salesforce": 100})
        assert s.global_rate_limits["github"] == 80


# ---- metrics_port != port ----


class TestMetricsPortValidation:
    def test_different_ports_valid(self):
        s = _make(port=8080, metrics_port=9090)
        assert s.port != s.metrics_port

    def test_same_port_raises(self):
        with pytest.raises(ValueError, match="metrics_port.*must differ from.*port"):
            _make(port=8080, metrics_port=8080)

    def test_same_port_non_default(self):
        with pytest.raises(ValueError, match="metrics_port.*must differ"):
            _make(port=3000, metrics_port=3000)


# ---- session_ttl > 0 ----


class TestSessionTtlValidation:
    def test_valid_session_ttl(self):
        s = _make(session_ttl=1800, absolute_session_ttl=7200)
        assert s.session_ttl == 1800

    def test_zero_session_ttl_raises(self):
        with pytest.raises(ValueError, match="session_ttl must be > 0"):
            _make(session_ttl=0)

    def test_negative_session_ttl_raises(self):
        with pytest.raises(ValueError, match="session_ttl must be > 0"):
            _make(session_ttl=-100)


# ---- absolute_session_ttl > session_ttl ----


class TestAbsoluteSessionTtlValidation:
    def test_valid_absolute_greater_than_session(self):
        s = _make(session_ttl=3600, absolute_session_ttl=7200)
        assert s.absolute_session_ttl > s.session_ttl

    def test_equal_raises(self):
        with pytest.raises(ValueError, match="absolute_session_ttl.*must be.*> session_ttl"):
            _make(session_ttl=3600, absolute_session_ttl=3600)

    def test_absolute_less_than_session_raises(self):
        with pytest.raises(ValueError, match="absolute_session_ttl.*must be.*> session_ttl"):
            _make(session_ttl=7200, absolute_session_ttl=3600)


# ---- circuit_failure_threshold > 0 ----


class TestCircuitBreakerValidation:
    def test_valid_threshold(self):
        s = _make(circuit_failure_threshold=10)
        assert s.circuit_failure_threshold == 10

    def test_zero_threshold_raises(self):
        with pytest.raises(ValueError, match="circuit_failure_threshold must be > 0"):
            _make(circuit_failure_threshold=0)

    def test_negative_threshold_raises(self):
        with pytest.raises(ValueError, match="circuit_failure_threshold must be > 0"):
            _make(circuit_failure_threshold=-1)

    def test_valid_recovery_timeout(self):
        s = _make(circuit_recovery_timeout=30.0)
        assert s.circuit_recovery_timeout == 30.0

    def test_zero_recovery_timeout_raises(self):
        with pytest.raises(ValueError, match="circuit_recovery_timeout must be > 0"):
            _make(circuit_recovery_timeout=0)

    def test_negative_recovery_timeout_raises(self):
        with pytest.raises(ValueError, match="circuit_recovery_timeout must be > 0"):
            _make(circuit_recovery_timeout=-5.0)


# ---- max_retries >= 0 ----


class TestRetryValidation:
    def test_valid_retries(self):
        s = _make(max_retries=5)
        assert s.max_retries == 5

    def test_zero_retries_valid(self):
        """Zero retries = no retries, which is a valid choice."""
        s = _make(max_retries=0)
        assert s.max_retries == 0

    def test_negative_retries_raises(self):
        with pytest.raises(ValueError, match="max_retries must be >= 0"):
            _make(max_retries=-1)


# ---- audit_retention_days > 0 ----


class TestAuditRetentionValidation:
    def test_valid_retention(self):
        s = _make(audit_retention_days=90)
        assert s.audit_retention_days == 90

    def test_zero_retention_raises(self):
        with pytest.raises(ValueError, match="audit_retention_days must be > 0"):
            _make(audit_retention_days=0)

    def test_negative_retention_raises(self):
        with pytest.raises(ValueError, match="audit_retention_days must be > 0"):
            _make(audit_retention_days=-30)


# ---- KMS provider cross-checks ----


class TestKmsValidation:
    def test_empty_kms_provider_valid(self):
        s = _make(kms_provider="")
        assert s.kms_provider == ""

    def test_vault_with_addr_valid(self):
        s = _make(kms_provider="vault", vault_addr="https://vault.example.com")
        assert s.kms_provider == "vault"

    def test_vault_without_addr_raises(self):
        with pytest.raises(ValueError, match="vault_addr is not set"):
            _make(kms_provider="vault", vault_addr="")

    def test_aws_with_key_id_valid(self):
        s = _make(kms_provider="aws", kms_key_id="arn:aws:kms:us-east-1:123456:key/abc")
        assert s.kms_provider == "aws"

    def test_aws_without_key_id_raises(self):
        with pytest.raises(ValueError, match="kms_key_id is not set"):
            _make(kms_provider="aws", kms_key_id="")

    def test_invalid_kms_provider(self):
        with pytest.raises(ValueError, match="kms_provider must be one of"):
            _make(kms_provider="gcp")


# ---- Multiple errors reported together ----


class TestMultipleErrors:
    def test_multiple_errors_in_one_message(self):
        """When multiple rules fail, all are reported together."""
        with pytest.raises(ValueError) as exc_info:
            _make(
                pg_pool_min_size=100,
                pg_pool_max_size=10,
                session_ttl=0,
                audit_retention_days=-1,
            )
        msg = str(exc_info.value)
        assert "pg_pool_min_size" in msg
        assert "session_ttl" in msg
        assert "audit_retention_days" in msg


# ---- Production Warnings (validate_for_production) ----


class TestProductionWarnings:
    def test_no_warnings_for_secure_config(self):
        s = _make(
            allow_insecure_http=False,
            dashboard_enabled=True,
            dashboard_bearer_token="secret-token",
            metrics_bearer_token="metrics-secret",
            transport="streamable-http",
            pg_pool_max_size=100,
        )
        warns = validate_for_production(s)
        assert warns == []

    def test_allow_insecure_http_not_in_warnings(self):
        """allow_insecure_http is a hard error (production_errors), not a warning."""
        s = _make(allow_insecure_http=True)
        warns = validate_for_production(s)
        assert not any("allow_insecure_http" in w for w in warns)

    def test_dashboard_without_token_warning(self):
        s = _make(dashboard_enabled=True, dashboard_bearer_token="")
        warns = validate_for_production(s)
        assert any("dashboard_bearer_token" in w for w in warns)

    def test_dashboard_disabled_no_warning(self):
        s = _make(dashboard_enabled=False, dashboard_bearer_token="")
        warns = validate_for_production(s)
        assert not any("dashboard" in w for w in warns)

    def test_dashboard_with_token_no_warning(self):
        s = _make(dashboard_enabled=True, dashboard_bearer_token="tok")
        warns = validate_for_production(s)
        assert not any("dashboard" in w for w in warns)

    def test_empty_metrics_bearer_token_warning(self):
        s = _make(metrics_bearer_token="")
        warns = validate_for_production(s)
        assert any("metrics_bearer_token" in w for w in warns)

    def test_metrics_bearer_token_set_no_warning(self):
        s = _make(metrics_bearer_token="secret")
        warns = validate_for_production(s)
        assert not any("metrics_bearer_token" in w for w in warns)

    def test_low_pool_size_http_warning(self):
        s = _make(transport="streamable-http", pg_pool_max_size=20)
        warns = validate_for_production(s)
        assert any("pg_pool_max_size" in w for w in warns)

    def test_adequate_pool_size_http_no_warning(self):
        s = _make(transport="streamable-http", pg_pool_max_size=100)
        warns = validate_for_production(s)
        assert not any("pg_pool_max_size" in w for w in warns)

    def test_low_pool_size_stdio_no_warning(self):
        """Stdio transport doesn't need high pool sizes."""
        s = _make(transport="stdio", pg_pool_max_size=5)
        warns = validate_for_production(s)
        assert not any("pg_pool_max_size" in w for w in warns)

    def test_all_warnings_together(self):
        s = _make(
            allow_insecure_http=True,
            dashboard_enabled=True,
            dashboard_bearer_token="",
            metrics_bearer_token="",
            transport="streamable-http",
            pg_pool_max_size=10,
        )
        warns = validate_for_production(s)
        # allow_insecure_http is now a hard error (production_errors), not a warning
        assert len(warns) == 3


# ---- Edge cases ----


class TestEdgeCases:
    def test_min_valid_session_ttl(self):
        """session_ttl=1 is the minimum valid value."""
        s = _make(session_ttl=1, absolute_session_ttl=2)
        assert s.session_ttl == 1

    def test_one_higher_absolute_ttl(self):
        s = _make(session_ttl=100, absolute_session_ttl=101)
        assert s.absolute_session_ttl == 101

    def test_very_large_values_valid(self):
        s = _make(
            pg_pool_min_size=1000,
            pg_pool_max_size=10000,
            session_ttl=86400,
            absolute_session_ttl=604800,
        )
        assert s.pg_pool_max_size == 10000

    def test_circuit_breaker_threshold_one(self):
        s = _make(circuit_failure_threshold=1)
        assert s.circuit_failure_threshold == 1

    def test_fractional_recovery_timeout(self):
        s = _make(circuit_recovery_timeout=0.5)
        assert s.circuit_recovery_timeout == 0.5

    def test_audit_retention_one_day(self):
        s = _make(audit_retention_days=1)
        assert s.audit_retention_days == 1


# ---- Production Errors (production_errors — hard failures) ----


class TestProductionErrors:
    def test_insecure_http_with_http_transport_is_fatal(self):
        s = _make(allow_insecure_http=True, transport="streamable-http")
        errs = production_errors(s)
        assert len(errs) == 1
        assert "FATAL" in errs[0]
        assert "allow_insecure_http" in errs[0]

    def test_insecure_http_with_stdio_not_fatal(self):
        """stdio transport doesn't transmit over HTTP, so not dangerous."""
        s = _make(allow_insecure_http=True, transport="stdio")
        errs = production_errors(s)
        assert errs == []

    def test_secure_http_transport_no_error(self):
        s = _make(allow_insecure_http=False, transport="streamable-http")
        errs = production_errors(s)
        assert errs == []

    def test_session_backend_memory_not_a_hard_error(self):
        """session_backend=memory is handled elsewhere, not in production_errors."""
        s = _make(session_backend="memory", transport="streamable-http")
        errs = production_errors(s)
        # Should not contain any session_backend errors
        assert not any("session_backend" in e for e in errs)
