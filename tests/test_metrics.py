"""Tests for Prometheus metrics module.

Validates that prometheus_client is a required (non-optional) dependency
and that all metric instruments are real prometheus_client objects, not no-op stubs.
"""

import time

import prometheus_client
import pytest

from asibot import metrics


class TestMetricsImport:
    """Verify that metrics.py imports prometheus_client unconditionally."""

    def test_no_has_prometheus_flag(self):
        """The _HAS_PROMETHEUS guard flag should not exist -- import is unconditional."""
        assert not hasattr(metrics, "_HAS_PROMETHEUS")

    def test_no_noop_metric_class(self):
        """The _NoOpMetric fallback class should not exist."""
        assert not hasattr(metrics, "_NoOpMetric")

    def test_no_noop_sentinel(self):
        """The _NOOP sentinel should not exist."""
        assert not hasattr(metrics, "_NOOP")


class TestMetricTypes:
    """Verify all exported metrics are real prometheus_client instruments."""

    def test_request_duration_is_histogram(self):
        assert isinstance(metrics.request_duration, prometheus_client.Histogram)

    def test_request_total_is_counter(self):
        assert isinstance(metrics.request_total, prometheus_client.Counter)

    def test_circuit_state_is_gauge(self):
        assert isinstance(metrics.circuit_state, prometheus_client.Gauge)

    def test_active_sessions_is_gauge(self):
        assert isinstance(metrics.active_sessions, prometheus_client.Gauge)

    def test_auth_failures_total_is_counter(self):
        assert isinstance(metrics.auth_failures_total, prometheus_client.Counter)

    def test_session_cache_hits_is_counter(self):
        assert isinstance(metrics.session_cache_hits, prometheus_client.Counter)

    def test_session_cache_misses_is_counter(self):
        assert isinstance(metrics.session_cache_misses, prometheus_client.Counter)

    def test_session_cache_evictions_is_counter(self):
        assert isinstance(metrics.session_cache_evictions, prometheus_client.Counter)

    def test_audit_write_failures_is_counter(self):
        assert isinstance(metrics.audit_write_failures, prometheus_client.Counter)


class TestMetricNames:
    """Verify the Prometheus metric names match what alert rules expect."""

    def test_request_duration_name(self):
        assert metrics.request_duration._name == "asibot_request_duration_seconds"

    def test_request_total_name(self):
        # prometheus_client strips _total suffix from Counter._name; it's added back in exposition
        assert metrics.request_total._name == "asibot_requests"

    def test_circuit_state_name(self):
        assert metrics.circuit_state._name == "asibot_circuit_state"

    def test_active_sessions_name(self):
        assert metrics.active_sessions._name == "asibot_active_sessions"

    def test_auth_failures_total_name(self):
        assert metrics.auth_failures_total._name == "asibot_auth_failures"

    def test_session_cache_hits_name(self):
        assert metrics.session_cache_hits._name == "asibot_session_cache_hits"

    def test_session_cache_misses_name(self):
        assert metrics.session_cache_misses._name == "asibot_session_cache_misses"

    def test_session_cache_evictions_name(self):
        assert metrics.session_cache_evictions._name == "asibot_session_cache_evictions"

    def test_audit_write_failures_name(self):
        assert metrics.audit_write_failures._name == "asibot_audit_write_failures"


class TestTrackRequest:
    """Verify the track_request context manager records metrics."""

    def test_track_request_records_duration(self):
        """track_request should observe a duration on the histogram."""
        before = prometheus_client.REGISTRY.get_sample_value(
            "asibot_request_duration_seconds_count",
            {"service": "TestSvc", "status": "ok"},
        ) or 0.0

        with metrics.track_request("TestSvc") as ctx:
            ctx["status"] = "ok"

        after = prometheus_client.REGISTRY.get_sample_value(
            "asibot_request_duration_seconds_count",
            {"service": "TestSvc", "status": "ok"},
        ) or 0.0

        assert after == before + 1

    def test_track_request_increments_total(self):
        """track_request should increment the request counter."""
        before = prometheus_client.REGISTRY.get_sample_value(
            "asibot_requests_total",
            {"service": "TestSvc2", "status": "ok"},
        ) or 0.0

        with metrics.track_request("TestSvc2") as ctx:
            ctx["status"] = "ok"

        after = prometheus_client.REGISTRY.get_sample_value(
            "asibot_requests_total",
            {"service": "TestSvc2", "status": "ok"},
        ) or 0.0

        assert after == before + 1

    def test_track_request_defaults_to_ok(self):
        """If status is not changed, it defaults to 'ok'."""
        before = prometheus_client.REGISTRY.get_sample_value(
            "asibot_requests_total",
            {"service": "TestSvcDefault", "status": "ok"},
        ) or 0.0

        with metrics.track_request("TestSvcDefault"):
            pass  # Don't set status

        after = prometheus_client.REGISTRY.get_sample_value(
            "asibot_requests_total",
            {"service": "TestSvcDefault", "status": "ok"},
        ) or 0.0

        assert after == before + 1

    def test_track_request_records_error_on_exception(self):
        """If an exception occurs, the status should be whatever was set."""
        before = prometheus_client.REGISTRY.get_sample_value(
            "asibot_requests_total",
            {"service": "TestSvcErr", "status": "error"},
        ) or 0.0

        with pytest.raises(ValueError):
            with metrics.track_request("TestSvcErr") as ctx:
                ctx["status"] = "error"
                raise ValueError("boom")

        after = prometheus_client.REGISTRY.get_sample_value(
            "asibot_requests_total",
            {"service": "TestSvcErr", "status": "error"},
        ) or 0.0

        assert after == before + 1


class TestNewRequestId:
    """Verify request ID generation."""

    def test_returns_hex_string(self):
        rid = metrics.new_request_id()
        assert isinstance(rid, str)
        assert len(rid) == 8
        int(rid, 16)  # Should not raise

    def test_unique_ids(self):
        ids = {metrics.new_request_id() for _ in range(100)}
        assert len(ids) == 100


class TestStartMetricsServer:
    """Verify the metrics server start function."""

    def test_start_metrics_server_is_callable(self):
        """start_metrics_server should be a callable function."""
        assert callable(metrics.start_metrics_server)

    def test_idempotent_start(self):
        """Calling start_metrics_server multiple times should not raise."""
        # Reset state so we can test
        original = metrics._metrics_started
        metrics._metrics_started = True
        try:
            # Should be a no-op since already started
            metrics.start_metrics_server(port=19999)
        finally:
            metrics._metrics_started = original
