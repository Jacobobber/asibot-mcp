"""Tests for the Asibot analytics dashboard (Starlette app)."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from starlette.testclient import TestClient

# ---------------------------------------------------------------------------
# Mock data matching what analytics.get_summary() would return
# ---------------------------------------------------------------------------

MOCK_SUMMARY = {
    "period_start": "2026-02-16",
    "period_end": "2026-03-18",
    "days": 30,
    "total_calls": 1234,
    "unique_users": 8,
    "active_services": 5,
    "calls_by_service": {
        "github": 500,
        "jira": 300,
        "salesforce": 200,
        "notion": 134,
        "zendesk": 100,
    },
    "calls_by_tool": {
        "github_search_repos": 200,
        "jira_search": 150,
        "github_get_issue": 100,
    },
    "calls_by_user": {
        "alice_at_test.com": 400,
        "bob_at_test.com": 300,
        "carol_at_test.com": 200,
    },
    "calls_by_day": [
        {"date": "2026-03-17", "count": 42},
        {"date": "2026-03-18", "count": 50},
    ],
    "calls_by_hour": [
        0, 0, 0, 0, 0, 5, 10, 30, 50, 60, 55, 40,
        35, 45, 50, 40, 30, 20, 10, 5, 2, 1, 0, 0,
    ],
    "error_count": 15,
    "error_rate": 0.012,
    "errors_by_service": {"jira": 8, "salesforce": 7},
    "errors_by_type": {"timeout": 10, "HTTPStatusError": 5},
    "avg_latency_ms": 450.5,
    "p50_latency_ms": 350.0,
    "p95_latency_ms": 1200.0,
    "latency_by_service": {
        "github": {"avg": 300.0, "p95": 800.0, "count": 500},
        "jira": {"avg": 600.0, "p95": 1500.0, "count": 300},
    },
    "top_tools": [
        {"tool": "github_search_repos", "count": 200},
        {"tool": "jira_search", "count": 150},
    ],
    "top_users": [
        {"user": "alice_at_test.com", "count": 400},
        {"user": "bob_at_test.com", "count": 300},
    ],
    "time_saved_minutes": 3500.0,
    "lifecycle": {
        "user_created": 3,
        "service_connected": 12,
        "service_disconnected": 2,
        "key_rotated": 1,
    },
    "adoption_trend": [
        {"date": "2026-03-17", "cumulative_users": 7, "active_users": 5, "total_calls": 42},
        {"date": "2026-03-18", "cumulative_users": 8, "active_users": 6, "total_calls": 50},
    ],
}

TOKEN = "test-secret-token"
DASHBOARD_HTML = "<html><head><title>Asibot Analytics</title></head><body>Dashboard</body></html>"


@pytest.fixture()
def client():
    """Create a TestClient with mocked token, analytics, and HTML."""
    import asibot.dashboard as mod

    # Inject mock token and bypass file I/O
    with (
        patch.object(mod, "_TOKEN", TOKEN),
        patch.object(mod, "_get_cached_summary", new_callable=AsyncMock, return_value=MOCK_SUMMARY),
        patch.object(mod, "_read_html", return_value=DASHBOARD_HTML),
    ):
        yield TestClient(mod.app)


# ---------------------------------------------------------------------------
# Authentication tests
# ---------------------------------------------------------------------------


def test_health_no_auth(client: TestClient):
    """GET /health returns 200 without any auth token."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_api_requires_auth(client: TestClient):
    """GET /api/summary without token returns 401."""
    resp = client.get("/api/summary")
    assert resp.status_code == 401
    assert resp.json()["error"] == "Unauthorized"


def test_api_with_bearer_token(client: TestClient):
    """GET /api/summary with valid Authorization: Bearer header returns 200."""
    resp = client.get("/api/summary", headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 200
    data = resp.json()
    assert "total_calls" in data


def test_api_with_query_token(client: TestClient):
    """GET /api/summary?token=VALID returns 200."""
    resp = client.get("/api/summary", params={"token": TOKEN})
    assert resp.status_code == 200
    data = resp.json()
    assert "total_calls" in data


def test_api_invalid_token(client: TestClient):
    """GET /api/summary with wrong token returns 401."""
    resp = client.get("/api/summary", headers={"Authorization": "Bearer wrong-token"})
    assert resp.status_code == 401


def test_html_requires_auth(client: TestClient):
    """GET / without token returns 401."""
    resp = client.get("/")
    assert resp.status_code == 401
    assert resp.json()["error"] == "Unauthorized"


# ---------------------------------------------------------------------------
# API endpoint tests (all with valid auth)
# ---------------------------------------------------------------------------

def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def test_summary_endpoint(client: TestClient):
    """GET /api/summary returns expected JSON structure."""
    resp = client.get("/api/summary", headers=_auth_headers())
    assert resp.status_code == 200
    data = resp.json()

    assert data["total_calls"] == 1234
    assert data["unique_users"] == 8
    assert data["active_services"] == 5
    assert data["error_rate"] == 0.012
    assert data["avg_latency_ms"] == 450.5
    assert data["p50_latency_ms"] == 350.0
    assert data["p95_latency_ms"] == 1200.0
    assert data["time_saved_minutes"] == 3500.0
    assert data["time_saved_hours"] == round(3500.0 / 60, 1)

    assert data["period"]["start"] == "2026-02-16"
    assert data["period"]["end"] == "2026-03-18"
    assert data["period"]["days"] == 30

    assert data["lifecycle"]["user_created"] == 3
    assert data["lifecycle"]["service_connected"] == 12


def test_summary_days_param(client: TestClient):
    """GET /api/summary?days=7 passes days=7 to _get_cached_summary."""
    import asibot.dashboard as mod

    with patch.object(mod, "_get_cached_summary", return_value=MOCK_SUMMARY) as mock_get:
        resp = client.get("/api/summary", params={"token": TOKEN, "days": "7"})
        assert resp.status_code == 200
        mock_get.assert_called_once_with(7)


def test_usage_endpoint(client: TestClient):
    """GET /api/usage returns calls_by_day, calls_by_hour, calls_by_service, top_tools."""
    resp = client.get("/api/usage", headers=_auth_headers())
    assert resp.status_code == 200
    data = resp.json()

    assert data["calls_by_day"] == MOCK_SUMMARY["calls_by_day"]
    assert data["calls_by_hour"] == MOCK_SUMMARY["calls_by_hour"]
    assert data["calls_by_service"] == MOCK_SUMMARY["calls_by_service"]
    assert data["top_tools"] == MOCK_SUMMARY["top_tools"]


def test_services_endpoint(client: TestClient):
    """GET /api/services returns services list with expected fields."""
    resp = client.get("/api/services", headers=_auth_headers())
    assert resp.status_code == 200
    data = resp.json()

    services = data["services"]
    assert isinstance(services, list)
    assert len(services) == 5  # github, jira, salesforce, notion, zendesk

    # Services are sorted by calls descending
    assert services[0]["name"] == "github"
    assert services[0]["calls"] == 500
    assert services[1]["name"] == "jira"
    assert services[1]["calls"] == 300

    # Check structure of each service entry
    for svc in services:
        assert "name" in svc
        assert "calls" in svc
        assert "errors" in svc
        assert "error_rate" in svc
        assert "avg_latency_ms" in svc
        assert "p95_latency_ms" in svc
        assert "users" in svc

    # Verify error data for services that have errors
    github_svc = next(s for s in services if s["name"] == "github")
    assert github_svc["errors"] == 0
    assert github_svc["error_rate"] == 0.0
    assert github_svc["avg_latency_ms"] == 300.0
    assert github_svc["p95_latency_ms"] == 800.0

    jira_svc = next(s for s in services if s["name"] == "jira")
    assert jira_svc["errors"] == 8
    assert jira_svc["error_rate"] == round(8 / 300, 4)
    assert jira_svc["avg_latency_ms"] == 600.0
    assert jira_svc["p95_latency_ms"] == 1500.0


def test_users_endpoint(client: TestClient):
    """GET /api/users returns users list from top_users."""
    resp = client.get("/api/users", headers=_auth_headers())
    assert resp.status_code == 200
    data = resp.json()

    users = data["users"]
    assert isinstance(users, list)
    assert len(users) == 2  # from top_users mock data

    assert users[0]["user"] == "alice_at_test.com"
    assert users[0]["calls"] == 400
    assert users[1]["user"] == "bob_at_test.com"
    assert users[1]["calls"] == 300

    # Each user entry should have these fields
    for u in users:
        assert "user" in u
        assert "calls" in u
        assert "services_used" in u
        assert "last_active" in u
        assert "top_tool" in u


def test_errors_endpoint(client: TestClient):
    """GET /api/errors returns error_count, error_rate, errors_by_service, errors_by_type."""
    resp = client.get("/api/errors", headers=_auth_headers())
    assert resp.status_code == 200
    data = resp.json()

    assert data["error_count"] == 15
    assert data["error_rate"] == 0.012
    assert data["errors_by_service"] == {"jira": 8, "salesforce": 7}
    assert data["errors_by_type"] == {"timeout": 10, "HTTPStatusError": 5}


# ---------------------------------------------------------------------------
# HTML page test
# ---------------------------------------------------------------------------


def test_dashboard_html(client: TestClient):
    """GET / with valid auth returns HTML containing 'Asibot Analytics'."""
    resp = client.get("/", headers=_auth_headers())
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Asibot Analytics" in resp.text


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_days_param_max(client: TestClient):
    """days=999 should be capped at 365."""
    import asibot.dashboard as mod

    with patch.object(mod, "_get_cached_summary", return_value=MOCK_SUMMARY) as mock_get:
        resp = client.get("/api/summary", params={"token": TOKEN, "days": "999"})
        assert resp.status_code == 200
        mock_get.assert_called_once_with(365)


def test_days_param_invalid(client: TestClient):
    """days=abc should default to 30."""
    import asibot.dashboard as mod

    with patch.object(mod, "_get_cached_summary", return_value=MOCK_SUMMARY) as mock_get:
        resp = client.get("/api/summary", params={"token": TOKEN, "days": "abc"})
        assert resp.status_code == 200
        mock_get.assert_called_once_with(30)
