"""Analytics: query audit database and compute aggregated metrics.

Reads from the SQLite audit_log table and produces usage metrics,
latency percentiles, error breakdowns, and adoption trends.
"""

import logging
from datetime import datetime, timedelta, timezone

from asibot import db

logger = logging.getLogger(__name__)

# Configurable multipliers — minutes saved per tool call
TIME_SAVED_MULTIPLIERS: dict[str, float] = {
    "search": 3.0,      # search tools save ~3 min each
    "list": 2.0,         # listing tools save ~2 min
    "get": 2.0,          # retrieval tools save ~2 min
    "query": 10.0,       # complex query tools save ~10 min
    "create": 5.0,       # creation tools save ~5 min
    "update": 5.0,       # update tools save ~5 min
    "send": 3.0,         # messaging tools save ~3 min
    "_default": 2.0,     # fallback
}


def _estimate_time_saved(tool_name: str) -> float:
    """Estimate minutes saved by a single tool call based on tool name keywords."""
    name_lower = tool_name.lower()
    for keyword, minutes in TIME_SAVED_MULTIPLIERS.items():
        if keyword.startswith("_"):
            continue
        if keyword in name_lower:
            return minutes
    return TIME_SAVED_MULTIPLIERS["_default"]


def _percentile(sorted_values: list[float], p: float) -> float:
    """Compute the p-th percentile (0-100) from a sorted list.

    Uses linear interpolation between closest ranks.
    """
    if not sorted_values:
        raise ValueError("Cannot compute percentile of empty list")
    if p < 0 or p > 100:
        raise ValueError(f"Percentile must be between 0 and 100, got {p}")
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    # Rank (0-based index) corresponding to the percentile
    rank = (p / 100) * (n - 1)
    lower = int(rank)
    upper = lower + 1
    if upper >= n:
        return sorted_values[-1]
    fraction = rank - lower
    return sorted_values[lower] + fraction * (sorted_values[upper] - sorted_values[lower])


def compute_metrics(events: list[dict], start: float, end: float) -> dict:
    """Compute aggregated metrics for events within [start, end] time range.

    Returns dict with usage counts, error rates, latency stats, and lifecycle counts.
    Only tool call entries (those with "tool" key) are included in call metrics.
    Lifecycle events (those with "event" key) go into the lifecycle dict.
    """
    # Filter events within the time range
    in_range = [e for e in events if start <= e.get("ts", 0.0) <= end]

    # Separate tool calls from lifecycle events
    tool_calls = [e for e in in_range if "tool" in e]
    lifecycle_events = [e for e in in_range if "event" in e]

    # --- Basic counts ---
    total_calls = len(tool_calls)
    users: set[str] = set()
    services: set[str] = set()
    calls_by_service: dict[str, int] = {}
    calls_by_tool: dict[str, int] = {}
    calls_by_user: dict[str, int] = {}
    calls_by_day_map: dict[str, int] = {}
    calls_by_hour: list[int] = [0] * 24

    error_count = 0
    errors_by_service: dict[str, int] = {}
    errors_by_type: dict[str, int] = {}

    latencies: list[float] = []
    latency_by_service_raw: dict[str, list[float]] = {}

    time_saved = 0.0

    for call in tool_calls:
        user = call.get("user", "unknown")
        tool = call.get("tool", "unknown")
        service = call.get("service", "unknown")
        ts = call.get("ts", 0.0)
        success = call.get("success", True)  # default True for older entries
        latency = call.get("latency_ms")
        error_type = call.get("error_type")

        users.add(user)
        services.add(service)

        calls_by_service[service] = calls_by_service.get(service, 0) + 1
        calls_by_tool[tool] = calls_by_tool.get(tool, 0) + 1
        calls_by_user[user] = calls_by_user.get(user, 0) + 1

        # Day and hour grouping
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        day_str = dt.strftime("%Y-%m-%d")
        calls_by_day_map[day_str] = calls_by_day_map.get(day_str, 0) + 1
        calls_by_hour[dt.hour] += 1

        # Errors
        if not success:
            error_count += 1
            errors_by_service[service] = errors_by_service.get(service, 0) + 1
            if error_type:
                errors_by_type[error_type] = errors_by_type.get(error_type, 0) + 1

        # Latency
        if latency is not None:
            latencies.append(float(latency))
            if service not in latency_by_service_raw:
                latency_by_service_raw[service] = []
            latency_by_service_raw[service].append(float(latency))

        # Time saved
        time_saved += _estimate_time_saved(tool)

    # --- Sort dicts by count descending ---
    calls_by_service = dict(sorted(calls_by_service.items(), key=lambda x: x[1], reverse=True))
    calls_by_tool = dict(sorted(calls_by_tool.items(), key=lambda x: x[1], reverse=True))
    calls_by_user = dict(sorted(calls_by_user.items(), key=lambda x: x[1], reverse=True))
    errors_by_service = dict(sorted(errors_by_service.items(), key=lambda x: x[1], reverse=True))
    errors_by_type = dict(sorted(errors_by_type.items(), key=lambda x: x[1], reverse=True))

    # --- calls_by_day: include ALL days in range (even zero-count) ---
    start_date = datetime.fromtimestamp(start, tz=timezone.utc).date()
    end_date = datetime.fromtimestamp(end, tz=timezone.utc).date()
    calls_by_day: list[dict] = []
    current_date = start_date
    while current_date <= end_date:
        day_str = current_date.strftime("%Y-%m-%d")
        calls_by_day.append({"date": day_str, "count": calls_by_day_map.get(day_str, 0)})
        current_date += timedelta(days=1)

    # --- Latency percentiles ---
    latencies.sort()
    avg_latency: float | None = None
    p50_latency: float | None = None
    p95_latency: float | None = None
    if latencies:
        avg_latency = sum(latencies) / len(latencies)
        p50_latency = _percentile(latencies, 50)
        p95_latency = _percentile(latencies, 95)

    # --- Latency by service ---
    latency_by_service: dict[str, dict] = {}
    for svc, lats in latency_by_service_raw.items():
        lats_sorted = sorted(lats)
        latency_by_service[svc] = {
            "avg": sum(lats_sorted) / len(lats_sorted),
            "p95": _percentile(lats_sorted, 95),
            "count": len(lats_sorted),
        }

    # --- Top tools and users ---
    top_tools = [{"tool": t, "count": c} for t, c in list(calls_by_tool.items())[:10]]
    top_users = [{"user": u, "count": c} for u, c in list(calls_by_user.items())[:10]]

    # --- Error rate ---
    error_rate = (error_count / total_calls) if total_calls > 0 else 0.0

    # --- Lifecycle ---
    lifecycle: dict[str, int] = {
        "user_created": 0,
        "service_connected": 0,
        "service_disconnected": 0,
        "key_rotated": 0,
        "session_start": 0,
    }
    for evt in lifecycle_events:
        event_name = evt.get("event", "")
        if event_name in lifecycle:
            lifecycle[event_name] += 1

    return {
        "total_calls": total_calls,
        "unique_users": len(users),
        "active_services": len(services),
        "calls_by_service": calls_by_service,
        "calls_by_tool": calls_by_tool,
        "calls_by_user": calls_by_user,
        "calls_by_day": calls_by_day,
        "calls_by_hour": calls_by_hour,
        "error_count": error_count,
        "error_rate": error_rate,
        "errors_by_service": errors_by_service,
        "errors_by_type": errors_by_type,
        "avg_latency_ms": avg_latency,
        "p50_latency_ms": p50_latency,
        "p95_latency_ms": p95_latency,
        "latency_by_service": latency_by_service,
        "top_tools": top_tools,
        "top_users": top_users,
        "time_saved_minutes": time_saved,
        "lifecycle": lifecycle,
    }


def get_adoption_trend(events: list[dict], days: int = 90) -> list[dict]:
    """Compute daily adoption metrics over the last N days.

    Returns list of {date, cumulative_users, active_users, total_calls}
    - cumulative_users: total unique users seen up to and including that day
    - active_users: unique users active on that day
    - total_calls: tool calls on that day
    """
    now = datetime.now(tz=timezone.utc)
    start_date = (now - timedelta(days=days - 1)).date()
    end_date = now.date()

    # Build per-day user sets and call counts from tool-call events
    day_users: dict[str, set[str]] = {}
    day_calls: dict[str, int] = {}

    for evt in events:
        if "tool" not in evt:
            continue
        ts = evt.get("ts", 0.0)
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).date()
        day_str = dt.strftime("%Y-%m-%d")
        user = evt.get("user", "unknown")
        if day_str not in day_users:
            day_users[day_str] = set()
        day_users[day_str].add(user)
        day_calls[day_str] = day_calls.get(day_str, 0) + 1

    # Also gather users from events before the window for cumulative count
    all_users_before: set[str] = set()
    for evt in events:
        if "tool" not in evt:
            continue
        ts = evt.get("ts", 0.0)
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).date()
        if dt < start_date:
            all_users_before.add(evt.get("user", "unknown"))

    trend: list[dict] = []
    cumulative_users: set[str] = set(all_users_before)
    current_date = start_date
    while current_date <= end_date:
        day_str = current_date.strftime("%Y-%m-%d")
        active = day_users.get(day_str, set())
        cumulative_users |= active
        trend.append({
            "date": day_str,
            "cumulative_users": len(cumulative_users),
            "active_users": len(active),
            "total_calls": day_calls.get(day_str, 0),
        })
        current_date += timedelta(days=1)

    return trend


async def get_summary(days: int = 30, user_id: str | None = None) -> dict:
    """Query audit DB and compute metrics for last N days.

    If user_id is provided, results are scoped to that user only
    (for non-admin dashboard views).

    Returns the compute_metrics() result plus:
    - period_start: ISO date string
    - period_end: ISO date string
    - days: int
    - adoption_trend: list from get_adoption_trend()
    """
    now = datetime.now(tz=timezone.utc)
    period_end = now
    period_start = now - timedelta(days=days)

    start_ts = period_start.timestamp()
    end_ts = period_end.timestamp()

    # For adoption trend we need all events (including before the window)
    # to compute cumulative users correctly
    all_events = await db.query_audit_range(0, end_ts, user_id=user_id)

    metrics = compute_metrics(all_events, start_ts, end_ts)
    adoption = get_adoption_trend(all_events, days=days)

    return {
        **metrics,
        "period_start": period_start.strftime("%Y-%m-%d"),
        "period_end": period_end.strftime("%Y-%m-%d"),
        "days": days,
        "adoption_trend": adoption,
    }
