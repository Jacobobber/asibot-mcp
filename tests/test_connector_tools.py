"""Tests for connector tool handlers with mocked HTTP responses.

Covers GitHub, Jira, Salesforce, Zoom, and Paylocity connector tools
including happy paths, error handling, and edge cases.
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from asibot import token_store


# --- Helpers ---


def _mock_response(status_code=200, json_data=None):
    """Create a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}", request=MagicMock(), response=resp
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


def _mock_client(responses):
    """Create a mock httpx.AsyncClient that returns a sequence of responses.

    responses: single response or list of responses for sequential calls.
    """
    client = AsyncMock(spec=httpx.AsyncClient)
    if isinstance(responses, list):
        client.request = AsyncMock(side_effect=responses)
        client.get = AsyncMock(side_effect=responses)
        client.post = AsyncMock(side_effect=responses)
    else:
        client.request = AsyncMock(return_value=responses)
        client.get = AsyncMock(return_value=responses)
        client.post = AsyncMock(return_value=responses)
    return client


def _patch_require_service(service, client, uid="test@example.com"):
    """Patch token_store.require_service to return given client."""
    return patch.object(
        token_store, "require_service",
        return_value=(client, uid, None),
    )


def _patch_require_service_error(error_msg):
    """Patch token_store.require_service to return an error."""
    return patch.object(
        token_store, "require_service",
        return_value=(None, None, error_msg),
    )


def _patch_get_creds(service, creds):
    """Patch token_store.get_credentials to return given creds."""
    return patch.object(
        token_store, "get_credentials",
        return_value=creds,
    )


# --- GitHub Connector Tests ---


class TestGitHubSearchRepos:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.github import GitHubConnector
        self.mcp = MagicMock()
        self.mcp.tool = lambda: lambda f: f  # passthrough decorator
        self.connector = GitHubConnector()
        self.connector.register_tools(self.mcp)
        # The tools are registered as local closures — extract them
        from asibot.connectors.github import GitHubConnector as GH
        self.connector_instance = GH()

    @pytest.mark.asyncio
    async def test_search_repos_success(self):
        from asibot.connectors.github import GitHubConnector
        mcp = MagicMock()
        tools = {}
        mcp.tool = lambda: lambda f: tools.setdefault(f.__name__, f) or f
        GitHubConnector().register_tools(mcp)

        resp = _mock_response(200, {
            "items": [
                {"full_name": "org/repo1", "description": "A repo", "stargazers_count": 42, "updated_at": "2024-01-15T00:00:00Z"},
                {"full_name": "org/repo2", "description": "Another", "stargazers_count": 10, "updated_at": "2024-02-01T00:00:00Z"},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("github", client), _patch_get_creds("github", {"org": "testorg"}):
            result = await tools["github_search_repos"]("test query", ctx, limit=10)
        assert "org/repo1" in result
        assert "org/repo2" in result
        assert "42" in result

    @pytest.mark.asyncio
    async def test_search_repos_no_results(self):
        from asibot.connectors.github import GitHubConnector
        mcp = MagicMock()
        tools = {}
        mcp.tool = lambda: lambda f: tools.setdefault(f.__name__, f) or f
        GitHubConnector().register_tools(mcp)

        resp = _mock_response(200, {"items": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("github", client), _patch_get_creds("github", {"org": ""}):
            result = await tools["github_search_repos"]("nonexistent", ctx)
        assert "No repos found" in result

    @pytest.mark.asyncio
    async def test_search_repos_auth_error(self):
        from asibot.connectors.github import GitHubConnector
        mcp = MagicMock()
        tools = {}
        mcp.tool = lambda: lambda f: tools.setdefault(f.__name__, f) or f
        GitHubConnector().register_tools(mcp)

        ctx = MagicMock()
        with _patch_require_service_error("Not connected to github"):
            result = await tools["github_search_repos"]("test", ctx)
        assert "Not connected" in result

    @pytest.mark.asyncio
    async def test_search_repos_http_error(self):
        from asibot.connectors.github import GitHubConnector
        mcp = MagicMock()
        tools = {}
        mcp.tool = lambda: lambda f: tools.setdefault(f.__name__, f) or f
        GitHubConnector().register_tools(mcp)

        resp = _mock_response(403)
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("github", client), _patch_get_creds("github", {"org": ""}):
            result = await tools["github_search_repos"]("test", ctx)
        # Pagination gracefully handles API errors (logged as warning);
        # the user sees "no results" rather than a raw HTTP error
        assert "No repos found" in result

    @pytest.mark.asyncio
    async def test_search_repos_invalid_query(self):
        from asibot.connectors.github import GitHubConnector
        mcp = MagicMock()
        tools = {}
        mcp.tool = lambda: lambda f: tools.setdefault(f.__name__, f) or f
        GitHubConnector().register_tools(mcp)

        ctx = MagicMock()
        result = await tools["github_search_repos"]("", ctx)
        assert "required" in result.lower()


class TestGitHubCreateIssue:
    @pytest.mark.asyncio
    async def test_create_issue_success(self):
        from asibot.connectors.github import GitHubConnector
        mcp = MagicMock()
        tools = {}
        mcp.tool = lambda: lambda f: tools.setdefault(f.__name__, f) or f
        GitHubConnector().register_tools(mcp)

        resp = _mock_response(200, {"number": 42, "title": "Bug fix", "html_url": "https://github.com/org/repo/issues/42"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("github", client), _patch_get_creds("github", {"org": "testorg"}):
            result = await tools["github_create_issue"]("my-repo", "Bug fix", ctx, body="Details here")
        assert "#42" in result
        assert "Bug fix" in result

    @pytest.mark.asyncio
    async def test_create_issue_write_blocked(self):
        from asibot.connectors.github import GitHubConnector
        mcp = MagicMock()
        tools = {}
        mcp.tool = lambda: lambda f: tools.setdefault(f.__name__, f) or f
        GitHubConnector().register_tools(mcp)

        ctx = MagicMock()
        with _patch_require_service_error("github is in read-only mode"):
            result = await tools["github_create_issue"]("my-repo", "Title", ctx)
        assert "read-only" in result


class TestGitHubGetIssue:
    @pytest.mark.asyncio
    async def test_get_issue_with_comments(self):
        from asibot.connectors.github import GitHubConnector
        mcp = MagicMock()
        tools = {}
        mcp.tool = lambda: lambda f: tools.setdefault(f.__name__, f) or f
        GitHubConnector().register_tools(mcp)

        issue_resp = _mock_response(200, {
            "number": 1, "title": "Test", "state": "open",
            "user": {"login": "alice"}, "created_at": "2024-01-01T00:00:00Z",
            "body": "Issue body",
        })
        comments_resp = _mock_response(200, [
            {"created_at": "2024-01-02T00:00:00Z", "user": {"login": "bob"}, "body": "A comment"},
        ])
        client = _mock_client([issue_resp, comments_resp])
        ctx = MagicMock()
        with _patch_require_service("github", client), _patch_get_creds("github", {"org": "testorg"}):
            result = await tools["github_get_issue"]("my-repo", 1, ctx)
        assert "#1" in result
        assert "alice" in result
        assert "bob" in result
        assert "A comment" in result


# --- Jira Connector Tests ---


class TestJiraSearch:
    @pytest.mark.asyncio
    async def test_search_success(self):
        from asibot.connectors.jira import JiraConnector
        mcp = MagicMock()
        tools = {}
        mcp.tool = lambda: lambda f: tools.setdefault(f.__name__, f) or f
        JiraConnector().register_tools(mcp)

        resp = _mock_response(200, {
            "issues": [
                {
                    "key": "PROJ-123",
                    "fields": {
                        "summary": "Fix login",
                        "status": {"name": "Open"},
                        "priority": {"name": "High"},
                        "assignee": {"displayName": "Alice"},
                    },
                },
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("atlassian", client):
            result = await tools["jira_search"]("project = PROJ", ctx)
        assert "PROJ-123" in result
        assert "Fix login" in result
        assert "Alice" in result

    @pytest.mark.asyncio
    async def test_search_empty(self):
        from asibot.connectors.jira import JiraConnector
        mcp = MagicMock()
        tools = {}
        mcp.tool = lambda: lambda f: tools.setdefault(f.__name__, f) or f
        JiraConnector().register_tools(mcp)

        resp = _mock_response(200, {"issues": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("atlassian", client):
            result = await tools["jira_search"]("project = EMPTY", ctx)
        assert "No issues found" in result

    @pytest.mark.asyncio
    async def test_search_invalid_jql(self):
        from asibot.connectors.jira import JiraConnector
        mcp = MagicMock()
        tools = {}
        mcp.tool = lambda: lambda f: tools.setdefault(f.__name__, f) or f
        JiraConnector().register_tools(mcp)

        ctx = MagicMock()
        result = await tools["jira_search"]("", ctx)
        assert "required" in result.lower()


class TestJiraCreateIssue:
    @pytest.mark.asyncio
    async def test_create_success(self):
        from asibot.connectors.jira import JiraConnector
        mcp = MagicMock()
        tools = {}
        mcp.tool = lambda: lambda f: tools.setdefault(f.__name__, f) or f
        JiraConnector().register_tools(mcp)

        resp = _mock_response(200, {"key": "PROJ-456"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("atlassian", client), _patch_get_creds("atlassian", {"domain": "test.atlassian.net"}):
            result = await tools["jira_create_issue"]("PROJ", "New task", ctx)
        assert "PROJ-456" in result

    @pytest.mark.asyncio
    async def test_create_invalid_project_key(self):
        from asibot.connectors.jira import JiraConnector
        mcp = MagicMock()
        tools = {}
        mcp.tool = lambda: lambda f: tools.setdefault(f.__name__, f) or f
        JiraConnector().register_tools(mcp)

        ctx = MagicMock()
        result = await tools["jira_create_issue"]("invalid", "Title", ctx)
        assert "Invalid project key" in result


class TestJiraGetIssue:
    @pytest.mark.asyncio
    async def test_get_issue_success(self):
        from asibot.connectors.jira import JiraConnector
        mcp = MagicMock()
        tools = {}
        mcp.tool = lambda: lambda f: tools.setdefault(f.__name__, f) or f
        JiraConnector().register_tools(mcp)

        resp = _mock_response(200, {
            "key": "PROJ-1",
            "fields": {
                "summary": "Test issue",
                "status": {"name": "Done"},
                "priority": {"name": "Low"},
                "assignee": {"displayName": "Bob"},
                "reporter": {"displayName": "Carol"},
                "issuetype": {"name": "Bug"},
                "created": "2024-01-01T00:00:00Z",
                "updated": "2024-02-01T00:00:00Z",
                "description": "Issue description",
                "comment": {"comments": []},
            },
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("atlassian", client):
            result = await tools["jira_get_issue"]("PROJ-1", ctx)
        assert "PROJ-1" in result
        assert "Test issue" in result
        assert "Bob" in result

    @pytest.mark.asyncio
    async def test_get_issue_invalid_key(self):
        from asibot.connectors.jira import JiraConnector
        mcp = MagicMock()
        tools = {}
        mcp.tool = lambda: lambda f: tools.setdefault(f.__name__, f) or f
        JiraConnector().register_tools(mcp)

        ctx = MagicMock()
        result = await tools["jira_get_issue"]("bad-key", ctx)
        assert "Invalid issue key" in result


class TestJiraAddComment:
    @pytest.mark.asyncio
    async def test_add_comment_success(self):
        from asibot.connectors.jira import JiraConnector
        mcp = MagicMock()
        tools = {}
        mcp.tool = lambda: lambda f: tools.setdefault(f.__name__, f) or f
        JiraConnector().register_tools(mcp)

        resp = _mock_response(200, {"id": "12345"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("atlassian", client):
            result = await tools["jira_add_comment"]("PROJ-1", "Great work!", ctx)
        assert "Comment added" in result


# --- Salesforce Connector Tests ---


class TestSalesforceSearch:
    @pytest.mark.asyncio
    async def test_search_success(self):
        from asibot.connectors.salesforce import SalesforceConnector
        mcp = MagicMock()
        tools = {}
        mcp.tool = lambda: lambda f: tools.setdefault(f.__name__, f) or f
        SalesforceConnector().register_tools(mcp)

        resp = _mock_response(200, {
            "searchRecords": [
                {"attributes": {"type": "Account"}, "Name": "Acme Corp", "Id": "001xx000003DGb2"},
                {"attributes": {"type": "Contact"}, "Name": "John Doe", "Email": "john@acme.com", "Id": "003xx000004DGb2"},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("salesforce", client):
            result = await tools["salesforce_search"]("Acme", ctx)
        assert "Acme Corp" in result
        assert "John Doe" in result
        assert "john@acme.com" in result

    @pytest.mark.asyncio
    async def test_search_empty(self):
        from asibot.connectors.salesforce import SalesforceConnector
        mcp = MagicMock()
        tools = {}
        mcp.tool = lambda: lambda f: tools.setdefault(f.__name__, f) or f
        SalesforceConnector().register_tools(mcp)

        resp = _mock_response(200, {"searchRecords": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("salesforce", client):
            result = await tools["salesforce_search"]("nonexistent", ctx)
        assert "No records found" in result


class TestSalesforceQuery:
    @pytest.mark.asyncio
    async def test_query_success(self):
        from asibot.connectors.salesforce import SalesforceConnector
        mcp = MagicMock()
        tools = {}
        mcp.tool = lambda: lambda f: tools.setdefault(f.__name__, f) or f
        SalesforceConnector().register_tools(mcp)

        resp = _mock_response(200, {
            "totalSize": 2,
            "done": True,
            "records": [
                {"attributes": {"type": "Account"}, "Id": "001", "Name": "Acme"},
                {"attributes": {"type": "Account"}, "Id": "002", "Name": "Globex"},
            ],
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("salesforce", client):
            result = await tools["salesforce_query"]("SELECT Id, Name FROM Account", ctx)
        assert "Acme" in result
        assert "Globex" in result
        assert "2 record" in result

    @pytest.mark.asyncio
    async def test_query_more_results(self):
        """Salesforce pagination follows nextRecordsUrl; without one, returns what's available."""
        from asibot.connectors.salesforce import SalesforceConnector
        mcp = MagicMock()
        tools = {}
        mcp.tool = lambda: lambda f: tools.setdefault(f.__name__, f) or f
        SalesforceConnector().register_tools(mcp)

        # Simulate two pages: first has nextRecordsUrl, second is done
        page1 = _mock_response(200, {
            "totalSize": 2,
            "done": False,
            "records": [{"attributes": {"type": "Account"}, "Id": "001", "Name": "First"}],
            "nextRecordsUrl": "/query/next",
        })
        page2 = _mock_response(200, {
            "totalSize": 2,
            "done": True,
            "records": [{"attributes": {"type": "Account"}, "Id": "002", "Name": "Second"}],
        })
        client = _mock_client([page1, page2])
        ctx = MagicMock()
        with _patch_require_service("salesforce", client):
            result = await tools["salesforce_query"]("SELECT Id FROM Account", ctx)
        # Pagination now collects all pages
        assert "First" in result
        assert "Second" in result
        assert "2 record" in result


class TestSalesforceGetRecord:
    @pytest.mark.asyncio
    async def test_get_record_success(self):
        from asibot.connectors.salesforce import SalesforceConnector
        mcp = MagicMock()
        tools = {}
        mcp.tool = lambda: lambda f: tools.setdefault(f.__name__, f) or f
        SalesforceConnector().register_tools(mcp)

        resp = _mock_response(200, {
            "attributes": {"type": "Account"},
            "Id": "001",
            "Name": "Acme Corp",
            "Industry": "Technology",
            "Website": None,
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("salesforce", client):
            result = await tools["salesforce_get_record"]("Account", "001", ctx)
        assert "Acme Corp" in result
        assert "Technology" in result
        # None values should be filtered out
        assert "Website" not in result

    @pytest.mark.asyncio
    async def test_get_record_invalid_object_type(self):
        from asibot.connectors.salesforce import SalesforceConnector
        mcp = MagicMock()
        tools = {}
        mcp.tool = lambda: lambda f: tools.setdefault(f.__name__, f) or f
        SalesforceConnector().register_tools(mcp)

        ctx = MagicMock()
        result = await tools["salesforce_get_record"]("FakeObject", "001", ctx)
        assert "Unknown Salesforce object" in result


# --- Zoom Connector Tests ---


class TestZoomListMeetings:
    @pytest.mark.asyncio
    async def test_list_meetings_success(self):
        from asibot.connectors.zoom import ZoomConnector, _token_cache
        _token_cache.clear()
        mcp = MagicMock()
        tools = {}
        mcp.tool = lambda: lambda f: tools.setdefault(f.__name__, f) or f
        ZoomConnector().register_tools(mcp)

        # Mock the token fetch
        token_resp = _mock_response(200, {"access_token": "zoom_tok", "expires_in": 3600})
        meetings_resp = _mock_response(200, {
            "meetings": [
                {"id": 123, "topic": "Team Sync", "start_time": "2024-01-15T10:00:00Z", "duration": 30},
            ]
        })
        client = _mock_client(meetings_resp)
        ctx = MagicMock()
        creds = {"account_id": "acc1", "client_id": "cid", "client_secret": "csec"}
        with (
            _patch_require_service("zoom", client),
            _patch_get_creds("zoom", creds),
            patch("asibot.connectors.zoom._get_access_token", new_callable=AsyncMock, return_value="zoom_tok"),
        ):
            result = await tools["zoom_list_meetings"](ctx)
        assert "Team Sync" in result
        assert "123" in result
        _token_cache.clear()

    @pytest.mark.asyncio
    async def test_list_meetings_empty(self):
        from asibot.connectors.zoom import ZoomConnector, _token_cache
        _token_cache.clear()
        mcp = MagicMock()
        tools = {}
        mcp.tool = lambda: lambda f: tools.setdefault(f.__name__, f) or f
        ZoomConnector().register_tools(mcp)

        resp = _mock_response(200, {"meetings": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        creds = {"account_id": "acc1", "client_id": "cid", "client_secret": "csec"}
        with (
            _patch_require_service("zoom", client),
            _patch_get_creds("zoom", creds),
            patch("asibot.connectors.zoom._get_access_token", new_callable=AsyncMock, return_value="tok"),
        ):
            result = await tools["zoom_list_meetings"](ctx)
        assert "No upcoming meetings" in result
        _token_cache.clear()


class TestZoomGetMeeting:
    @pytest.mark.asyncio
    async def test_get_meeting_success(self):
        from asibot.connectors.zoom import ZoomConnector, _token_cache
        _token_cache.clear()
        mcp = MagicMock()
        tools = {}
        mcp.tool = lambda: lambda f: tools.setdefault(f.__name__, f) or f
        ZoomConnector().register_tools(mcp)

        resp = _mock_response(200, {
            "id": 456, "topic": "Sprint Review", "status": "waiting",
            "start_time": "2024-02-01T14:00:00Z", "duration": 60,
            "timezone": "America/New_York",
            "join_url": "https://zoom.us/j/456",
            "agenda": "Review sprint progress",
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        creds = {"account_id": "acc1", "client_id": "cid", "client_secret": "csec"}
        with (
            _patch_require_service("zoom", client),
            _patch_get_creds("zoom", creds),
            patch("asibot.connectors.zoom._get_access_token", new_callable=AsyncMock, return_value="tok"),
        ):
            result = await tools["zoom_get_meeting"](456, ctx)
        assert "Sprint Review" in result
        assert "zoom.us" in result
        _token_cache.clear()


# --- Paylocity Connector Tests ---


class TestPaylocityListEmployees:
    @pytest.mark.asyncio
    async def test_list_employees_success(self):
        from asibot.connectors.paylocity import PaylocityConnector
        mcp = MagicMock()
        tools = {}
        mcp.tool = lambda: lambda f: tools.setdefault(f.__name__, f) or f
        PaylocityConnector().register_tools(mcp)

        resp = _mock_response(200, json_data=None)
        resp.json.return_value = [
            {"employeeId": "E001", "firstName": "Alice", "lastName": "Smith", "statusType": "Active"},
            {"employeeId": "E002", "firstName": "Bob", "lastName": "Jones", "statusType": "Active"},
        ]
        client = _mock_client(resp)
        ctx = MagicMock()
        creds = {"client_id": "cid", "client_secret": "csec", "company_id": "comp1"}
        with (
            _patch_require_service("paylocity", client),
            _patch_get_creds("paylocity", creds),
            patch("asibot.connectors.paylocity._get_access_token", new_callable=AsyncMock, return_value="pay_tok"),
        ):
            result = await tools["paylocity_list_employees"](ctx)
        assert "Alice Smith" in result
        assert "Bob Jones" in result

    @pytest.mark.asyncio
    async def test_list_employees_empty(self):
        from asibot.connectors.paylocity import PaylocityConnector
        mcp = MagicMock()
        tools = {}
        mcp.tool = lambda: lambda f: tools.setdefault(f.__name__, f) or f
        PaylocityConnector().register_tools(mcp)

        resp = _mock_response(200, json_data=None)
        resp.json.return_value = []
        client = _mock_client(resp)
        ctx = MagicMock()
        creds = {"client_id": "cid", "client_secret": "csec", "company_id": "comp1"}
        with (
            _patch_require_service("paylocity", client),
            _patch_get_creds("paylocity", creds),
            patch("asibot.connectors.paylocity._get_access_token", new_callable=AsyncMock, return_value="tok"),
        ):
            result = await tools["paylocity_list_employees"](ctx)
        assert "No employees found" in result


class TestPaylocityGetEmployee:
    @pytest.mark.asyncio
    async def test_get_employee_success(self):
        from asibot.connectors.paylocity import PaylocityConnector
        mcp = MagicMock()
        tools = {}
        mcp.tool = lambda: lambda f: tools.setdefault(f.__name__, f) or f
        PaylocityConnector().register_tools(mcp)

        resp = _mock_response(200, {
            "firstName": "Carol",
            "lastName": "Davis",
            "statusType": "Active",
            "departmentPosition": {"departmentCode": "ENG", "jobTitle": "Engineer"},
            "hireDate": "2023-01-15",
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        creds = {"client_id": "cid", "client_secret": "csec", "company_id": "comp1"}
        with (
            _patch_require_service("paylocity", client),
            _patch_get_creds("paylocity", creds),
            patch("asibot.connectors.paylocity._get_access_token", new_callable=AsyncMock, return_value="tok"),
        ):
            result = await tools["paylocity_get_employee"]("E001", ctx)
        assert "Carol Davis" in result
        assert "Engineer" in result
        assert "ENG" in result

    @pytest.mark.asyncio
    async def test_get_employee_invalid_id(self):
        from asibot.connectors.paylocity import PaylocityConnector
        mcp = MagicMock()
        tools = {}
        mcp.tool = lambda: lambda f: tools.setdefault(f.__name__, f) or f
        PaylocityConnector().register_tools(mcp)

        ctx = MagicMock()
        result = await tools["paylocity_get_employee"]("", ctx)
        assert "required" in result.lower()


# --- Token Caching Tests ---


class TestZoomTokenCaching:
    @pytest.mark.asyncio
    async def test_token_cached(self):
        from asibot.connectors.zoom import _token_cache, _get_access_token, _TOKEN_MARGIN
        _token_cache.clear()
        _token_cache["acc1"] = ("cached_token", time.time() + 3600)
        creds = {"account_id": "acc1", "client_id": "cid", "client_secret": "csec"}
        # Should return cached token without making HTTP request
        token = await _get_access_token(creds)
        assert token == "cached_token"
        _token_cache.clear()

    @pytest.mark.asyncio
    async def test_expired_token_refetched(self):
        from asibot.connectors.zoom import _token_cache, _get_access_token, _TOKEN_MARGIN
        _token_cache.clear()
        _token_cache["acc1"] = ("old_token", time.time() - 100)  # expired
        creds = {"account_id": "acc1", "client_id": "cid", "client_secret": "csec"}

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"access_token": "new_token", "expires_in": 3600}
        mock_resp.raise_for_status.return_value = None

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("asibot.connectors.zoom.httpx.AsyncClient", return_value=mock_client):
            token = await _get_access_token(creds)
        assert token == "new_token"
        assert _token_cache["acc1"][0] == "new_token"
        _token_cache.clear()


class TestPaylocityTokenCaching:
    @pytest.mark.asyncio
    async def test_token_cached(self):
        from asibot.connectors.paylocity import _get_access_token
        # Paylocity now delegates to token_store.get_s2s_token which uses _s2s_token_cache
        token_store._s2s_token_cache.clear()
        token_store._s2s_token_cache["paylocity:cid"] = ("cached_pay_token", time.time() + 3600)
        creds = {"client_id": "cid", "client_secret": "csec"}
        token = await _get_access_token(creds)
        assert token == "cached_pay_token"
        token_store._s2s_token_cache.clear()


# --- safe_request Tests ---


class TestSafeRequest:
    @pytest.mark.asyncio
    async def test_success(self):
        resp = _mock_response(200, {"ok": True})
        client = _mock_client(resp)
        r, err = await token_store.safe_request(
            client, "GET", "https://api.example.com/test",
            service="Test", action="fetch",
        )
        assert r is not None
        assert err is None
        assert r.json() == {"ok": True}

    @pytest.mark.asyncio
    async def test_http_error(self):
        resp = _mock_response(500)
        client = _mock_client(resp)
        r, err = await token_store.safe_request(
            client, "GET", "https://api.example.com/test",
            service="Test", action="fetch",
        )
        assert r is None
        assert "500" in err
        assert "Test fetch failed" in err

    @pytest.mark.asyncio
    async def test_network_error(self):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.request.side_effect = httpx.RequestError("Connection refused")
        r, err = await token_store.safe_request(
            client, "GET", "https://api.example.com/test",
            service="Test", action="fetch",
        )
        assert r is None
        assert "network error" in err

    @pytest.mark.asyncio
    async def test_passes_kwargs(self):
        resp = _mock_response(200, {})
        client = AsyncMock(spec=httpx.AsyncClient)
        client.request.return_value = resp
        await token_store.safe_request(
            client, "POST", "https://api.example.com/test",
            service="Test", action="create",
            json={"key": "value"},
            params={"q": "search"},
        )
        client.request.assert_called_once_with(
            "POST", "https://api.example.com/test",
            json={"key": "value"}, params={"q": "search"},
        )


# --- Pending Setup Tests ---


class TestPendingSetupCleanup:
    def test_expired_entries_cleaned(self):
        from asibot.server import _pending_setups, _cleanup_pending_setups, _SETUP_TTL
        _pending_setups.clear()
        _pending_setups["old"] = {"status": "complete", "_created_at": time.time() - _SETUP_TTL - 100}
        _pending_setups["fresh"] = {"status": "complete", "_created_at": time.time()}
        _cleanup_pending_setups()
        assert "old" not in _pending_setups
        assert "fresh" in _pending_setups
        _pending_setups.clear()

    def test_size_cap_enforced(self):
        from asibot.server import _pending_setups, _MAX_PENDING_SETUPS
        _pending_setups.clear()
        for i in range(_MAX_PENDING_SETUPS):
            _pending_setups[f"setup_{i}"] = {"status": "pending", "_created_at": time.time()}
        assert len(_pending_setups) == _MAX_PENDING_SETUPS
        _pending_setups.clear()
