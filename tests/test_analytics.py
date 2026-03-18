"""Tests for analytics: metrics computation and helpers."""

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from asibot.analytics import (
    _estimate_time_saved,
    _percentile,
    compute_metrics,
    get_adoption_trend,
    get_summary,
)


# ---------------------------------------------------------------------------
# Test data helpers
# ---------------------------------------------------------------------------

def _make_tool_entry(user="user@test.com", tool="github_search_repos", service="github",
                     ts=None, latency_ms=200, success=True, error_type=None, args=None):
    entry = {"ts": ts or time.time(), "user": user, "tool": tool}
    if service:
        entry["service"] = service
    if latency_ms is not None:
        entry["latency_ms"] = latency_ms
    if success is not None:
        entry["success"] = success
    if error_type:
        entry["error_type"] = error_type
    if args:
        entry["args"] = args
    return entry


def _make_event_entry(user="user@test.com", event="service_connected", service=None, ts=None):
    entry = {"ts": ts or time.time(), "user": user, "event": event}
    if service:
        entry["service"] = service
    return entry


# ---------------------------------------------------------------------------
# Metrics Computation Tests
# ---------------------------------------------------------------------------

def _time_range():
    """Return a (start, end) time range covering a known window."""
    # 2025-01-15 00:00 UTC to 2025-01-17 00:00 UTC
    start = datetime(2025, 1, 15, tzinfo=timezone.utc).timestamp()
    end = datetime(2025, 1, 17, tzinfo=timezone.utc).timestamp()
    return start, end


def test_compute_metrics_basic():
    """Feed in tool call events, verify total_calls, unique_users, active_services."""
    start, end = _time_range()
    mid = (start + end) / 2
    events = [
        _make_tool_entry(user="alice@test.com", service="github", ts=mid),
        _make_tool_entry(user="bob@test.com", service="jira", ts=mid + 1),
        _make_tool_entry(user="alice@test.com", service="github", ts=mid + 2),
    ]

    m = compute_metrics(events, start, end)
    assert m["total_calls"] == 3
    assert m["unique_users"] == 2
    assert m["active_services"] == 2


def test_compute_metrics_calls_by_service():
    """Verify calls_by_service dict is sorted desc by count."""
    start, end = _time_range()
    mid = (start + end) / 2
    events = [
        _make_tool_entry(service="github", ts=mid),
        _make_tool_entry(service="github", ts=mid + 1),
        _make_tool_entry(service="github", ts=mid + 2),
        _make_tool_entry(service="jira", ts=mid + 3),
        _make_tool_entry(service="jira", ts=mid + 4),
        _make_tool_entry(service="slack", ts=mid + 5),
    ]

    m = compute_metrics(events, start, end)
    services = list(m["calls_by_service"].items())
    assert services[0] == ("github", 3)
    assert services[1] == ("jira", 2)
    assert services[2] == ("slack", 1)
    # Verify descending order
    counts = list(m["calls_by_service"].values())
    assert counts == sorted(counts, reverse=True)


def test_compute_metrics_calls_by_day():
    """Verify all days in range are present (including zero-count days)."""
    start = datetime(2025, 1, 15, tzinfo=timezone.utc).timestamp()
    end = datetime(2025, 1, 18, tzinfo=timezone.utc).timestamp()

    # Only create events on day 1 and day 3
    day1_ts = datetime(2025, 1, 15, 12, tzinfo=timezone.utc).timestamp()
    day3_ts = datetime(2025, 1, 17, 12, tzinfo=timezone.utc).timestamp()
    events = [
        _make_tool_entry(ts=day1_ts),
        _make_tool_entry(ts=day3_ts),
    ]

    m = compute_metrics(events, start, end)
    days = m["calls_by_day"]
    dates = [d["date"] for d in days]
    # Should include all 4 days: Jan 15, 16, 17, 18
    assert "2025-01-15" in dates
    assert "2025-01-16" in dates
    assert "2025-01-17" in dates
    assert "2025-01-18" in dates
    # Jan 16 should have zero count
    day16 = next(d for d in days if d["date"] == "2025-01-16")
    assert day16["count"] == 0
    # Jan 15 and 17 should each have 1
    day15 = next(d for d in days if d["date"] == "2025-01-15")
    assert day15["count"] == 1


def test_compute_metrics_calls_by_hour():
    """Verify 24-element list with correct hour counts."""
    start, end = _time_range()
    # Create events at hour 3 and hour 15 UTC
    h3 = datetime(2025, 1, 16, 3, 0, tzinfo=timezone.utc).timestamp()
    h15 = datetime(2025, 1, 16, 15, 0, tzinfo=timezone.utc).timestamp()
    events = [
        _make_tool_entry(ts=h3),
        _make_tool_entry(ts=h3 + 1),
        _make_tool_entry(ts=h15),
    ]

    m = compute_metrics(events, start, end)
    assert len(m["calls_by_hour"]) == 24
    assert m["calls_by_hour"][3] == 2
    assert m["calls_by_hour"][15] == 1
    assert m["calls_by_hour"][0] == 0


def test_compute_metrics_error_rate():
    """Mix of success=True/False entries, verify error_count and error_rate."""
    start, end = _time_range()
    mid = (start + end) / 2
    events = [
        _make_tool_entry(ts=mid, success=True),
        _make_tool_entry(ts=mid + 1, success=True),
        _make_tool_entry(ts=mid + 2, success=False),
        _make_tool_entry(ts=mid + 3, success=False),
    ]

    m = compute_metrics(events, start, end)
    assert m["error_count"] == 2
    assert m["error_rate"] == pytest.approx(0.5)


def test_compute_metrics_errors_by_service():
    """Verify errors grouped by service."""
    start, end = _time_range()
    mid = (start + end) / 2
    events = [
        _make_tool_entry(ts=mid, service="github", success=False),
        _make_tool_entry(ts=mid + 1, service="github", success=False),
        _make_tool_entry(ts=mid + 2, service="jira", success=False),
        _make_tool_entry(ts=mid + 3, service="jira", success=True),
    ]

    m = compute_metrics(events, start, end)
    assert m["errors_by_service"]["github"] == 2
    assert m["errors_by_service"]["jira"] == 1


def test_compute_metrics_errors_by_type():
    """Verify errors grouped by error_type."""
    start, end = _time_range()
    mid = (start + end) / 2
    events = [
        _make_tool_entry(ts=mid, success=False, error_type="timeout"),
        _make_tool_entry(ts=mid + 1, success=False, error_type="timeout"),
        _make_tool_entry(ts=mid + 2, success=False, error_type="auth_failure"),
        _make_tool_entry(ts=mid + 3, success=True),
    ]

    m = compute_metrics(events, start, end)
    assert m["errors_by_type"]["timeout"] == 2
    assert m["errors_by_type"]["auth_failure"] == 1


def test_compute_metrics_empty():
    """Empty events list returns zeros/empty collections (no crash)."""
    start, end = _time_range()
    m = compute_metrics([], start, end)

    assert m["total_calls"] == 0
    assert m["unique_users"] == 0
    assert m["active_services"] == 0
    assert m["calls_by_service"] == {}
    assert m["error_count"] == 0
    assert m["error_rate"] == 0.0
    assert m["avg_latency_ms"] is None
    assert m["p50_latency_ms"] is None
    assert m["p95_latency_ms"] is None
    assert m["time_saved_minutes"] == 0.0
    assert m["top_tools"] == []
    assert m["top_users"] == []
    assert len(m["calls_by_hour"]) == 24


def test_compute_metrics_time_range_filter():
    """Events outside [start, end] are excluded."""
    start, end = _time_range()
    before = start - 1000
    after = end + 1000
    mid = (start + end) / 2

    events = [
        _make_tool_entry(ts=before, tool="before_tool"),
        _make_tool_entry(ts=mid, tool="in_range_tool"),
        _make_tool_entry(ts=after, tool="after_tool"),
    ]

    m = compute_metrics(events, start, end)
    assert m["total_calls"] == 1
    assert m["top_tools"][0]["tool"] == "in_range_tool"


def test_compute_metrics_legacy_entries():
    """Entries without latency_ms/success/service fields handled gracefully."""
    start, end = _time_range()
    mid = (start + end) / 2
    # Legacy entry: only ts, user, tool
    legacy = {"ts": mid, "user": "legacy@test.com", "tool": "old_tool"}
    events = [legacy]

    m = compute_metrics(events, start, end)
    assert m["total_calls"] == 1
    assert m["unique_users"] == 1
    # Service should default to "unknown"
    assert "unknown" in m["calls_by_service"]
    # success defaults to True, so no errors
    assert m["error_count"] == 0
    # No latency data
    assert m["avg_latency_ms"] is None


# ---------------------------------------------------------------------------
# Latency Tests
# ---------------------------------------------------------------------------

def test_latency_stats():
    """Feed entries with known latency_ms values, verify avg, p50, p95."""
    start, end = _time_range()
    mid = (start + end) / 2
    latencies = [100, 200, 300, 400, 500]
    events = [
        _make_tool_entry(ts=mid + i, latency_ms=lat) for i, lat in enumerate(latencies)
    ]

    m = compute_metrics(events, start, end)
    assert m["avg_latency_ms"] == pytest.approx(300.0)
    assert m["p50_latency_ms"] == pytest.approx(300.0)
    # p95 of [100, 200, 300, 400, 500]: rank = 0.95 * 4 = 3.8 -> 400 + 0.8*(500-400) = 480
    assert m["p95_latency_ms"] == pytest.approx(480.0)


def test_latency_by_service():
    """Verify per-service latency aggregation."""
    start, end = _time_range()
    mid = (start + end) / 2
    events = [
        _make_tool_entry(ts=mid, service="github", latency_ms=100),
        _make_tool_entry(ts=mid + 1, service="github", latency_ms=300),
        _make_tool_entry(ts=mid + 2, service="jira", latency_ms=500),
    ]

    m = compute_metrics(events, start, end)
    assert "github" in m["latency_by_service"]
    assert m["latency_by_service"]["github"]["avg"] == pytest.approx(200.0)
    assert m["latency_by_service"]["github"]["count"] == 2
    assert m["latency_by_service"]["jira"]["avg"] == pytest.approx(500.0)
    assert m["latency_by_service"]["jira"]["count"] == 1


def test_latency_no_data():
    """No latency data returns None for avg/p50/p95."""
    start, end = _time_range()
    mid = (start + end) / 2
    events = [_make_tool_entry(ts=mid, latency_ms=None)]

    m = compute_metrics(events, start, end)
    assert m["avg_latency_ms"] is None
    assert m["p50_latency_ms"] is None
    assert m["p95_latency_ms"] is None


# ---------------------------------------------------------------------------
# Time Saved Tests
# ---------------------------------------------------------------------------

def test_estimate_time_saved_search():
    """'github_search_repos' matches 'search' -> 3.0 min."""
    assert _estimate_time_saved("github_search_repos") == 3.0


def test_estimate_time_saved_get():
    """'github_get_issue' matches 'get' -> 2.0 min."""
    assert _estimate_time_saved("github_get_issue") == 2.0


def test_estimate_time_saved_default():
    """'unknown_tool' -> default 2.0 min."""
    assert _estimate_time_saved("unknown_tool") == 2.0


def test_time_saved_total():
    """Feed N tool calls, verify time_saved_minutes matches expected sum."""
    start, end = _time_range()
    mid = (start + end) / 2
    events = [
        _make_tool_entry(ts=mid, tool="github_search_repos"),       # 3.0
        _make_tool_entry(ts=mid + 1, tool="jira_list_issues"),      # 2.0 (list)
        _make_tool_entry(ts=mid + 2, tool="salesforce_query_leads"),# 10.0 (query)
        _make_tool_entry(ts=mid + 3, tool="slack_send_message"),    # 3.0 (send)
        _make_tool_entry(ts=mid + 4, tool="github_create_issue"),   # 5.0 (create)
    ]

    m = compute_metrics(events, start, end)
    expected = 3.0 + 2.0 + 10.0 + 3.0 + 5.0
    assert m["time_saved_minutes"] == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Adoption Trend Tests
# ---------------------------------------------------------------------------

def test_adoption_trend_basic():
    """Verify cumulative_users increases, active_users reflects daily counts."""
    now = datetime.now(tz=timezone.utc)
    today = now.replace(hour=12, minute=0, second=0, microsecond=0)
    yesterday = today - timedelta(days=1)

    events = [
        _make_tool_entry(user="alice@test.com", ts=yesterday.timestamp()),
        _make_tool_entry(user="bob@test.com", ts=today.timestamp()),
        _make_tool_entry(user="alice@test.com", ts=today.timestamp()),
    ]

    trend = get_adoption_trend(events, days=3)
    assert len(trend) == 3

    # Find the yesterday and today entries
    yesterday_str = yesterday.strftime("%Y-%m-%d")
    today_str = today.strftime("%Y-%m-%d")

    yesterday_entry = next(t for t in trend if t["date"] == yesterday_str)
    today_entry = next(t for t in trend if t["date"] == today_str)

    # Yesterday: alice active
    assert yesterday_entry["active_users"] == 1
    assert yesterday_entry["total_calls"] == 1

    # Today: alice + bob active
    assert today_entry["active_users"] == 2
    assert today_entry["total_calls"] == 2

    # Cumulative should increase
    assert today_entry["cumulative_users"] >= yesterday_entry["cumulative_users"]
    assert today_entry["cumulative_users"] == 2


def test_adoption_trend_empty():
    """No events returns empty list? Actually returns list of days with zeros."""
    trend = get_adoption_trend([], days=3)
    assert len(trend) == 3
    for entry in trend:
        assert entry["cumulative_users"] == 0
        assert entry["active_users"] == 0
        assert entry["total_calls"] == 0


# ---------------------------------------------------------------------------
# Top N Tests
# ---------------------------------------------------------------------------

def test_top_tools():
    """Verify top_tools returns top 10 sorted by count."""
    start, end = _time_range()
    mid = (start + end) / 2
    # Create 12 different tools, each with different counts
    events = []
    for i in range(12):
        tool_name = f"tool_{i:02d}"
        count = 12 - i  # tool_00 has 12 calls, tool_01 has 11, etc.
        for j in range(count):
            events.append(_make_tool_entry(ts=mid + i * 100 + j, tool=tool_name))

    m = compute_metrics(events, start, end)
    top = m["top_tools"]
    assert len(top) == 10  # Only top 10
    assert top[0]["tool"] == "tool_00"
    assert top[0]["count"] == 12
    assert top[9]["tool"] == "tool_09"
    assert top[9]["count"] == 3
    # Verify descending order
    counts = [t["count"] for t in top]
    assert counts == sorted(counts, reverse=True)


def test_top_users():
    """Verify top_users returns top 10 sorted by count."""
    start, end = _time_range()
    mid = (start + end) / 2
    events = []
    for i in range(12):
        user_name = f"user{i:02d}@test.com"
        count = 12 - i
        for j in range(count):
            events.append(_make_tool_entry(ts=mid + i * 100 + j, user=user_name))

    m = compute_metrics(events, start, end)
    top = m["top_users"]
    assert len(top) == 10
    assert top[0]["user"] == "user00@test.com"
    assert top[0]["count"] == 12
    counts = [t["count"] for t in top]
    assert counts == sorted(counts, reverse=True)


# ---------------------------------------------------------------------------
# Lifecycle Events
# ---------------------------------------------------------------------------

def test_lifecycle_events():
    """Mix of user_created, service_connected events, verify lifecycle dict counts."""
    start, end = _time_range()
    mid = (start + end) / 2
    events = [
        _make_event_entry(event="user_created", ts=mid),
        _make_event_entry(event="user_created", ts=mid + 1),
        _make_event_entry(event="service_connected", service="github", ts=mid + 2),
        _make_event_entry(event="service_connected", service="jira", ts=mid + 3),
        _make_event_entry(event="service_connected", service="slack", ts=mid + 4),
        _make_event_entry(event="service_disconnected", service="slack", ts=mid + 5),
        _make_event_entry(event="key_rotated", ts=mid + 6),
        # Also include a tool call to make sure it doesn't affect lifecycle
        _make_tool_entry(ts=mid + 7),
    ]

    m = compute_metrics(events, start, end)
    assert m["lifecycle"]["user_created"] == 2
    assert m["lifecycle"]["service_connected"] == 3
    assert m["lifecycle"]["service_disconnected"] == 1
    assert m["lifecycle"]["key_rotated"] == 1


# ---------------------------------------------------------------------------
# Summary Helper (DB-backed)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_summary():
    """get_summary queries DB and returns expected structure."""
    now = datetime.now(tz=timezone.utc)
    ts = now.timestamp()
    mock_events = [
        _make_tool_entry(ts=ts - 100, user="alice@test.com", tool="github_search_repos"),
        _make_tool_entry(ts=ts - 50, user="bob@test.com", tool="jira_get_issue"),
        _make_event_entry(event="user_created", ts=ts - 200),
    ]

    with patch("asibot.analytics.db.query_audit_range", new_callable=AsyncMock, return_value=mock_events):
        summary = await get_summary(days=30)

    # Verify structure
    assert "total_calls" in summary
    assert "unique_users" in summary
    assert "active_services" in summary
    assert "calls_by_service" in summary
    assert "calls_by_day" in summary
    assert "calls_by_hour" in summary
    assert "error_count" in summary
    assert "error_rate" in summary
    assert "avg_latency_ms" in summary
    assert "p50_latency_ms" in summary
    assert "p95_latency_ms" in summary
    assert "top_tools" in summary
    assert "top_users" in summary
    assert "time_saved_minutes" in summary
    assert "lifecycle" in summary
    assert "period_start" in summary
    assert "period_end" in summary
    assert "days" in summary
    assert "adoption_trend" in summary

    # Verify content
    assert summary["total_calls"] == 2
    assert summary["unique_users"] == 2
    assert summary["days"] == 30
    assert isinstance(summary["adoption_trend"], list)


# ---------------------------------------------------------------------------
# Percentile Helper
# ---------------------------------------------------------------------------

def test_percentile_basic():
    """Known values, verify p50 and p95."""
    values = sorted([10, 20, 30, 40, 50, 60, 70, 80, 90, 100])
    p50 = _percentile(values, 50)
    p95 = _percentile(values, 95)

    # p50: rank = 0.5 * 9 = 4.5 -> 50 + 0.5*(60-50) = 55
    assert p50 == pytest.approx(55.0)
    # p95: rank = 0.95 * 9 = 8.55 -> 90 + 0.55*(100-90) = 95.5
    assert p95 == pytest.approx(95.5)


def test_percentile_single_value():
    """Single element list returns that element for any percentile."""
    assert _percentile([42.0], 0) == 42.0
    assert _percentile([42.0], 50) == 42.0
    assert _percentile([42.0], 100) == 42.0
