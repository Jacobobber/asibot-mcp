"""Tests for standard connectors: Notion, Google Workspace, HubSpot, Zendesk,
Figma, Smartsheet, Confluence.

These connectors use token_store.require_service() for auth.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from asibot import token_store


# --- Helpers ---


def _mock_response(status_code=200, json_data=None):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.headers = {}  # Required for retry logic (Retry-After header check)
    resp.json.return_value = json_data or {}
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}", request=MagicMock(), response=resp
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


def _mock_client(responses):
    client = AsyncMock(spec=httpx.AsyncClient)
    if isinstance(responses, list):
        client.request = AsyncMock(side_effect=responses)
        client.get = AsyncMock(side_effect=responses)
        client.post = AsyncMock(side_effect=responses)
        client.put = AsyncMock(side_effect=responses)
        client.delete = AsyncMock(side_effect=responses)
        client.patch = AsyncMock(side_effect=responses)
    else:
        client.request = AsyncMock(return_value=responses)
        client.get = AsyncMock(return_value=responses)
        client.post = AsyncMock(return_value=responses)
        client.put = AsyncMock(return_value=responses)
        client.delete = AsyncMock(return_value=responses)
        client.patch = AsyncMock(return_value=responses)
    return client


def _patch_require_service(service, client, uid="test@example.com"):
    return patch.object(
        token_store, "require_service",
        return_value=(client, uid, None),
    )


def _register_tools(connector_cls):
    mcp = MagicMock()
    tools = {}
    mcp.tool = lambda: lambda f: tools.setdefault(f.__name__, f) or f
    connector_cls().register_tools(mcp)
    return tools


# --- Notion Connector Tests ---


class TestNotionSearch:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.notion import NotionConnector
        self.tools = _register_tools(NotionConnector)

    @pytest.mark.asyncio
    async def test_search_success(self):
        resp = _mock_response(200, {
            "results": [
                {
                    "object": "page",
                    "id": "page-001",
                    "properties": {
                        "title": {"title": [{"plain_text": "Project Plan"}]},
                    },
                },
                {
                    "object": "database",
                    "id": "db-001",
                    "title": [{"plain_text": "Tasks DB"}],
                },
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("notion", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["notion_search"]("project", ctx)
        assert "Project Plan" in result
        assert "Tasks DB" in result
        assert "page-001" in result

    @pytest.mark.asyncio
    async def test_search_empty(self):
        resp = _mock_response(200, {"results": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("notion", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["notion_search"]("nothing", ctx)
        assert "No results found" in result


class TestNotionReadPage:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.notion import NotionConnector
        self.tools = _register_tools(NotionConnector)

    @pytest.mark.asyncio
    async def test_read_page_success(self):
        page_resp = _mock_response(200, {
            "properties": {"title": {"title": [{"plain_text": "Design Doc"}]}},
        })
        blocks_resp = _mock_response(200, {
            "results": [
                {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Introduction section."}]}},
                {"type": "heading_1", "heading_1": {"rich_text": [{"plain_text": "Overview"}]}},
            ]
        })
        client = _mock_client([page_resp, blocks_resp])
        ctx = MagicMock()
        with _patch_require_service("notion", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, side_effect=[(page_resp, None), (blocks_resp, None)]):
            result = await self.tools["notion_read_page"]("page-001", ctx)
        assert "Design Doc" in result
        assert "Introduction section." in result
        assert "# Overview" in result

    @pytest.mark.asyncio
    async def test_read_page_empty(self):
        page_resp = _mock_response(200, {
            "properties": {"title": {"title": [{"plain_text": "Empty Page"}]}},
        })
        blocks_resp = _mock_response(200, {"results": []})
        client = _mock_client([page_resp, blocks_resp])
        ctx = MagicMock()
        with _patch_require_service("notion", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, side_effect=[(page_resp, None), (blocks_resp, None)]):
            result = await self.tools["notion_read_page"]("page-002", ctx)
        assert "Empty Page" in result
        assert "empty page" in result.lower()


class TestNotionListDatabases:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.notion import NotionConnector
        self.tools = _register_tools(NotionConnector)

    @pytest.mark.asyncio
    async def test_list_databases_success(self):
        resp = _mock_response(200, {
            "results": [
                {"title": [{"plain_text": "Bugs Tracker"}], "id": "db-100"},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("notion", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["notion_list_databases"](ctx)
        assert "Bugs Tracker" in result
        assert "db-100" in result

    @pytest.mark.asyncio
    async def test_list_databases_empty(self):
        resp = _mock_response(200, {"results": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("notion", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["notion_list_databases"](ctx)
        assert "No databases found" in result


class TestNotionQueryDatabase:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.notion import NotionConnector
        self.tools = _register_tools(NotionConnector)

    @pytest.mark.asyncio
    async def test_query_success(self):
        resp = _mock_response(200, {
            "results": [
                {
                    "id": "row-001",
                    "properties": {
                        "Name": {"type": "title", "title": [{"plain_text": "Task Alpha"}]},
                        "Status": {"type": "status", "status": {"name": "In Progress"}},
                    },
                },
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("notion", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["notion_query_database"]("db-100", ctx)
        assert "Task Alpha" in result
        assert "In Progress" in result

    @pytest.mark.asyncio
    async def test_query_empty(self):
        resp = _mock_response(200, {"results": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("notion", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["notion_query_database"]("db-100", ctx)
        assert "No entries found" in result


# --- Google Workspace Connector Tests ---


class TestGDriveSearch:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.google_workspace import GoogleWorkspaceConnector
        self.tools = _register_tools(GoogleWorkspaceConnector)

    @pytest.mark.asyncio
    async def test_search_success(self):
        resp = _mock_response(200, {
            "files": [
                {
                    "id": "file-001",
                    "name": "Q1 Budget.xlsx",
                    "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "modifiedTime": "2024-03-01T00:00:00Z",
                    "webViewLink": "https://drive.google.com/file/d/file-001",
                },
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("google", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["gdrive_search"]("budget", ctx)
        assert "Q1 Budget.xlsx" in result
        assert "file-001" in result

    @pytest.mark.asyncio
    async def test_search_empty(self):
        resp = _mock_response(200, {"files": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("google", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["gdrive_search"]("nonexistent", ctx)
        assert "No files found" in result


class TestGDriveListFiles:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.google_workspace import GoogleWorkspaceConnector
        self.tools = _register_tools(GoogleWorkspaceConnector)

    @pytest.mark.asyncio
    async def test_list_success(self):
        resp = _mock_response(200, {
            "files": [
                {"id": "f1", "name": "Notes.doc", "mimeType": "application/vnd.google-apps.document", "modifiedTime": "2024-01-01T00:00:00Z"},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("google", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["gdrive_list_files"](ctx)
        assert "Notes.doc" in result

    @pytest.mark.asyncio
    async def test_list_empty(self):
        resp = _mock_response(200, {"files": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("google", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["gdrive_list_files"](ctx)
        assert "No files found" in result


class TestGCalendarEvents:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.google_workspace import GoogleWorkspaceConnector
        self.tools = _register_tools(GoogleWorkspaceConnector)

    @pytest.mark.asyncio
    async def test_events_success(self):
        resp = _mock_response(200, {
            "items": [
                {
                    "summary": "Team Standup",
                    "start": {"dateTime": "2024-06-01T09:00:00-07:00"},
                    "end": {"dateTime": "2024-06-01T09:30:00-07:00"},
                    "attendees": [{"email": "a@co.com"}, {"email": "b@co.com"}],
                },
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("google", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["gcalendar_events"](ctx)
        assert "Team Standup" in result
        assert "2 attendees" in result

    @pytest.mark.asyncio
    async def test_events_empty(self):
        resp = _mock_response(200, {"items": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("google", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["gcalendar_events"](ctx)
        assert "No events" in result


# --- HubSpot Connector Tests ---


class TestHubSpotSearchContacts:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.hubspot import HubSpotConnector
        self.tools = _register_tools(HubSpotConnector)

    @pytest.mark.asyncio
    async def test_search_success(self):
        resp = _mock_response(200, {
            "results": [
                {
                    "id": "101",
                    "properties": {
                        "firstname": "Jane",
                        "lastname": "Doe",
                        "email": "jane@acme.com",
                        "company": "Acme Inc",
                    },
                },
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("hubspot", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["hubspot_search_contacts"]("jane", ctx)
        assert "Jane Doe" in result
        assert "jane@acme.com" in result
        assert "Acme Inc" in result

    @pytest.mark.asyncio
    async def test_search_empty(self):
        resp = _mock_response(200, {"results": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("hubspot", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["hubspot_search_contacts"]("nobody", ctx)
        assert "No contacts found" in result


class TestHubSpotSearchDeals:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.hubspot import HubSpotConnector
        self.tools = _register_tools(HubSpotConnector)

    @pytest.mark.asyncio
    async def test_search_success(self):
        resp = _mock_response(200, {
            "results": [
                {
                    "id": "d-001",
                    "properties": {
                        "dealname": "Enterprise License",
                        "dealstage": "contractsent",
                        "amount": "50000",
                        "closedate": "2024-12-31T00:00:00Z",
                    },
                },
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("hubspot", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["hubspot_search_deals"]("enterprise", ctx)
        assert "Enterprise License" in result
        assert "50000" in result

    @pytest.mark.asyncio
    async def test_search_empty(self):
        resp = _mock_response(200, {"results": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("hubspot", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["hubspot_search_deals"]("nothing", ctx)
        assert "No deals found" in result


class TestHubSpotGetContact:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.hubspot import HubSpotConnector
        self.tools = _register_tools(HubSpotConnector)

    @pytest.mark.asyncio
    async def test_get_contact_success(self):
        resp = _mock_response(200, {
            "properties": {
                "firstname": "John",
                "lastname": "Smith",
                "email": "john@globex.com",
                "phone": "555-1234",
                "company": "Globex",
                "jobtitle": "CTO",
                "lifecyclestage": "customer",
            },
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("hubspot", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["hubspot_get_contact"]("101", ctx)
        assert "John Smith" in result
        assert "john@globex.com" in result
        assert "CTO" in result

    @pytest.mark.asyncio
    async def test_get_contact_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["hubspot_get_contact"]("", ctx)
        assert "required" in result.lower()


class TestHubSpotGetDeal:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.hubspot import HubSpotConnector
        self.tools = _register_tools(HubSpotConnector)

    @pytest.mark.asyncio
    async def test_get_deal_success(self):
        resp = _mock_response(200, {
            "properties": {
                "dealname": "Big Deal",
                "dealstage": "closedwon",
                "amount": "100000",
                "closedate": "2024-06-15",
                "pipeline": "default",
                "hubspot_owner_id": "owner-1",
                "description": "Major enterprise deal",
            },
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("hubspot", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["hubspot_get_deal"]("d-001", ctx)
        assert "Big Deal" in result
        assert "100000" in result
        assert "Major enterprise deal" in result

    @pytest.mark.asyncio
    async def test_get_deal_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["hubspot_get_deal"]("", ctx)
        assert "required" in result.lower()


# --- Zendesk Connector Tests ---


class TestZendeskSearchTickets:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.zendesk import ZendeskConnector
        self.tools = _register_tools(ZendeskConnector)

    @pytest.mark.asyncio
    async def test_search_success(self):
        resp = _mock_response(200, {
            "results": [
                {
                    "id": 42,
                    "subject": "Login issue",
                    "status": "open",
                    "priority": "high",
                    "updated_at": "2024-05-10T00:00:00Z",
                },
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("zendesk", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["zendesk_search_tickets"]("login", ctx)
        assert "#42" in result
        assert "Login issue" in result
        assert "open" in result

    @pytest.mark.asyncio
    async def test_search_empty(self):
        resp = _mock_response(200, {"results": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("zendesk", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["zendesk_search_tickets"]("nothing", ctx)
        assert "No tickets found" in result


class TestZendeskGetTicket:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.zendesk import ZendeskConnector
        self.tools = _register_tools(ZendeskConnector)

    @pytest.mark.asyncio
    async def test_get_ticket_success(self):
        ticket_resp = _mock_response(200, {
            "ticket": {
                "id": 99,
                "subject": "Cannot export",
                "status": "pending",
                "priority": "normal",
                "type": "problem",
                "requester_id": 1001,
                "assignee_id": 2002,
                "created_at": "2024-04-01T00:00:00Z",
                "updated_at": "2024-04-05T00:00:00Z",
                "description": "When I try to export, I get an error.",
            },
        })
        comments_resp = _mock_response(200, {
            "comments": [
                {"created_at": "2024-04-01T00:00:00Z", "author_id": 1001, "body": "Original description"},
                {"created_at": "2024-04-02T10:00:00Z", "author_id": 2002, "body": "Looking into it."},
            ]
        })
        client = _mock_client([ticket_resp, comments_resp])
        ctx = MagicMock()
        with _patch_require_service("zendesk", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, side_effect=[(ticket_resp, None), (comments_resp, None)]):
            result = await self.tools["zendesk_get_ticket"](99, ctx)
        assert "#99" in result
        assert "Cannot export" in result
        assert "Looking into it." in result

    @pytest.mark.asyncio
    async def test_get_ticket_no_comments(self):
        ticket_resp = _mock_response(200, {
            "ticket": {
                "id": 100,
                "subject": "Simple ticket",
                "status": "solved",
                "priority": "low",
                "type": "question",
                "requester_id": 1001,
                "assignee_id": 2002,
                "created_at": "2024-04-01T00:00:00Z",
                "updated_at": "2024-04-01T00:00:00Z",
                "description": "A simple question.",
            },
        })
        comments_resp = _mock_response(200, {"comments": []})
        client = _mock_client([ticket_resp, comments_resp])
        ctx = MagicMock()
        with _patch_require_service("zendesk", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, side_effect=[(ticket_resp, None), (comments_resp, None)]):
            result = await self.tools["zendesk_get_ticket"](100, ctx)
        assert "#100" in result
        assert "Follow-up" not in result


class TestZendeskSearchArticles:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.zendesk import ZendeskConnector
        self.tools = _register_tools(ZendeskConnector)

    @pytest.mark.asyncio
    async def test_search_success(self):
        resp = _mock_response(200, {
            "results": [
                {
                    "title": "How to reset password",
                    "id": 5001,
                    "html_url": "https://help.company.com/articles/5001",
                    "snippet": "Click on forgot password and follow the steps...",
                },
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("zendesk", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["zendesk_search_articles"]("password reset", ctx)
        assert "How to reset password" in result
        assert "5001" in result

    @pytest.mark.asyncio
    async def test_search_empty(self):
        resp = _mock_response(200, {"results": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("zendesk", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["zendesk_search_articles"]("nothing", ctx)
        assert "No articles found" in result


# --- Figma Connector Tests ---


class TestFigmaListProjects:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.figma import FigmaConnector
        self.tools = _register_tools(FigmaConnector)

    @pytest.mark.asyncio
    async def test_list_success(self):
        resp = _mock_response(200, {
            "projects": [
                {"name": "Web Redesign", "id": "proj-001"},
                {"name": "Mobile App", "id": "proj-002"},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("figma", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["figma_list_projects"]("team-001", ctx)
        assert "Web Redesign" in result
        assert "Mobile App" in result

    @pytest.mark.asyncio
    async def test_list_empty(self):
        resp = _mock_response(200, {"projects": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("figma", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["figma_list_projects"]("team-001", ctx)
        assert "No projects found" in result


class TestFigmaListFiles:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.figma import FigmaConnector
        self.tools = _register_tools(FigmaConnector)

    @pytest.mark.asyncio
    async def test_list_files_success(self):
        resp = _mock_response(200, {
            "files": [
                {"name": "Homepage", "key": "abc123", "last_modified": "2024-06-01T00:00:00Z"},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("figma", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["figma_list_files"]("proj-001", ctx)
        assert "Homepage" in result
        assert "abc123" in result

    @pytest.mark.asyncio
    async def test_list_files_empty(self):
        resp = _mock_response(200, {"files": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("figma", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["figma_list_files"]("proj-001", ctx)
        assert "No files found" in result


class TestFigmaGetFile:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.figma import FigmaConnector
        self.tools = _register_tools(FigmaConnector)

    @pytest.mark.asyncio
    async def test_get_file_success(self):
        resp = _mock_response(200, {
            "name": "Dashboard",
            "lastModified": "2024-07-01T00:00:00Z",
            "version": "v42",
            "document": {
                "children": [
                    {"name": "Page 1", "children": [{"id": "1"}, {"id": "2"}]},
                ]
            },
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("figma", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["figma_get_file"]("abc123", ctx)
        assert "Dashboard" in result
        assert "v42" in result
        assert "Page 1" in result

    @pytest.mark.asyncio
    async def test_get_file_empty_key(self):
        ctx = MagicMock()
        result = await self.tools["figma_get_file"]("", ctx)
        assert "required" in result.lower()


class TestFigmaGetComments:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.figma import FigmaConnector
        self.tools = _register_tools(FigmaConnector)

    @pytest.mark.asyncio
    async def test_comments_success(self):
        resp = _mock_response(200, {
            "comments": [
                {
                    "user": {"handle": "alice_design"},
                    "created_at": "2024-06-15T10:00:00Z",
                    "message": "Looks great!",
                    "resolved_at": None,
                },
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("figma", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["figma_get_comments"]("abc123", ctx)
        assert "alice_design" in result
        assert "Looks great!" in result

    @pytest.mark.asyncio
    async def test_comments_empty(self):
        resp = _mock_response(200, {"comments": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("figma", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["figma_get_comments"]("abc123", ctx)
        assert "No comments" in result


# --- Smartsheet Connector Tests ---


class TestSmartsheetListSheets:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.smartsheet import SmartsheetConnector
        self.tools = _register_tools(SmartsheetConnector)

    @pytest.mark.asyncio
    async def test_list_success(self):
        resp = _mock_response(200, {
            "data": [
                {"name": "Project Tracker", "id": "sheet-001", "modifiedAt": "2024-05-01T00:00:00Z"},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("smartsheet", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["smartsheet_list_sheets"](ctx)
        assert "Project Tracker" in result
        assert "sheet-001" in result

    @pytest.mark.asyncio
    async def test_list_empty(self):
        resp = _mock_response(200, {"data": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("smartsheet", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["smartsheet_list_sheets"](ctx)
        assert "No sheets found" in result


class TestSmartsheetGetSheet:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.smartsheet import SmartsheetConnector
        self.tools = _register_tools(SmartsheetConnector)

    @pytest.mark.asyncio
    async def test_get_sheet_success(self):
        resp = _mock_response(200, {
            "name": "Budget Sheet",
            "columns": [
                {"id": 1, "title": "Item"},
                {"id": 2, "title": "Amount"},
            ],
            "rows": [
                {"cells": [{"columnId": 1, "value": "Travel"}, {"columnId": 2, "value": 500}]},
            ],
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("smartsheet", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["smartsheet_get_sheet"]("sheet-001", ctx)
        assert "Budget Sheet" in result
        assert "Item" in result
        assert "Amount" in result

    @pytest.mark.asyncio
    async def test_get_sheet_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["smartsheet_get_sheet"]("", ctx)
        assert "required" in result.lower()


class TestSmartsheetSearch:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.smartsheet import SmartsheetConnector
        self.tools = _register_tools(SmartsheetConnector)

    @pytest.mark.asyncio
    async def test_search_success(self):
        resp = _mock_response(200, {
            "results": [
                {"objectType": "row", "text": "Budget item", "parentObjectName": "Finance Sheet"},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("smartsheet", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["smartsheet_search"]("budget", ctx)
        assert "Budget item" in result
        assert "Finance Sheet" in result

    @pytest.mark.asyncio
    async def test_search_empty(self):
        resp = _mock_response(200, {"results": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("smartsheet", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["smartsheet_search"]("nothing", ctx)
        assert "No results found" in result


# --- Confluence Connector Tests ---


class TestConfluenceSearch:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.confluence import ConfluenceConnector
        self.tools = _register_tools(ConfluenceConnector)

    @pytest.mark.asyncio
    async def test_search_success(self):
        resp = _mock_response(200, {
            "results": [
                {
                    "title": "Architecture Guide",
                    "space": {"name": "Engineering"},
                    "id": "page-200",
                    "type": "page",
                },
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("atlassian", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["confluence_search"]("architecture", ctx)
        assert "Architecture Guide" in result
        assert "Engineering" in result
        assert "page-200" in result

    @pytest.mark.asyncio
    async def test_search_empty(self):
        resp = _mock_response(200, {"results": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("atlassian", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["confluence_search"]("nothing", ctx)
        assert "No pages found" in result


class TestConfluenceReadPage:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.confluence import ConfluenceConnector
        self.tools = _register_tools(ConfluenceConnector)

    @pytest.mark.asyncio
    async def test_read_success(self):
        resp = _mock_response(200, {
            "title": "Onboarding",
            "space": {"name": "HR"},
            "version": {"number": 3},
            "body": {"storage": {"value": "<p>Welcome to the company!</p>"}},
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("atlassian", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["confluence_read_page"]("page-200", ctx)
        assert "Onboarding" in result
        assert "HR" in result
        assert "Welcome to the company!" in result

    @pytest.mark.asyncio
    async def test_read_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["confluence_read_page"]("", ctx)
        assert "required" in result.lower()


class TestConfluenceListSpaces:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.confluence import ConfluenceConnector
        self.tools = _register_tools(ConfluenceConnector)

    @pytest.mark.asyncio
    async def test_list_success(self):
        resp = _mock_response(200, {
            "results": [
                {"key": "ENG", "name": "Engineering", "type": "global"},
                {"key": "HR", "name": "Human Resources", "type": "global"},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("atlassian", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["confluence_list_spaces"](ctx)
        assert "Engineering" in result
        assert "Human Resources" in result

    @pytest.mark.asyncio
    async def test_list_empty(self):
        resp = _mock_response(200, {"results": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("atlassian", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["confluence_list_spaces"](ctx)
        assert "No spaces found" in result


# --- CQL Escaping Tests ---


class TestCQLEscaping:
    def test_escape_special_chars(self):
        from asibot.connectors.confluence import _escape_cql_value
        assert _escape_cql_value("test+query") == "test\\+query"
        assert _escape_cql_value("test&query") == "test\\&query"
        assert _escape_cql_value("test|query") == "test\\|query"
        assert _escape_cql_value('test"query') == 'test\\"query'
        assert _escape_cql_value("test*query") == "test\\*query"
        assert _escape_cql_value("test?query") == "test\\?query"

    def test_plain_text_unescaped(self):
        from asibot.connectors.confluence import _escape_cql_value
        assert _escape_cql_value("hello world") == "hello world"
        assert _escape_cql_value("simple") == "simple"

    def test_cql_auto_generated_for_plain_queries(self):
        from asibot.connectors.confluence import _escape_cql_value
        user_input = 'test+injection"attempt'
        escaped = _escape_cql_value(user_input)
        assert "\\" in escaped
        assert escaped == 'test\\+injection\\"attempt'


# --- Phase 2: HubSpot New Tools ---


class TestHubSpotSearchCompanies:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.hubspot import HubSpotConnector
        self.tools = _register_tools(HubSpotConnector)

    @pytest.mark.asyncio
    async def test_search_success(self):
        resp = _mock_response(200, {
            "results": [
                {
                    "id": "c-001",
                    "properties": {
                        "name": "Acme Corp",
                        "domain": "acme.com",
                        "industry": "Technology",
                        "city": "San Francisco",
                    },
                },
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("hubspot", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["hubspot_search_companies"]("acme", ctx)
        assert "Acme Corp" in result
        assert "acme.com" in result
        assert "Technology" in result

    @pytest.mark.asyncio
    async def test_search_empty(self):
        resp = _mock_response(200, {"results": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("hubspot", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["hubspot_search_companies"]("nothing", ctx)
        assert "No companies found" in result


class TestHubSpotGetCompany:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.hubspot import HubSpotConnector
        self.tools = _register_tools(HubSpotConnector)

    @pytest.mark.asyncio
    async def test_get_company_success(self):
        resp = _mock_response(200, {
            "properties": {
                "name": "Globex Inc",
                "domain": "globex.com",
                "industry": "Manufacturing",
                "city": "Springfield",
                "numberofemployees": "500",
                "annualrevenue": "10000000",
            },
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("hubspot", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["hubspot_get_company"]("c-001", ctx)
        assert "Globex Inc" in result
        assert "globex.com" in result
        assert "500" in result

    @pytest.mark.asyncio
    async def test_get_company_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["hubspot_get_company"]("", ctx)
        assert "required" in result.lower()


class TestHubSpotListPipelines:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.hubspot import HubSpotConnector
        self.tools = _register_tools(HubSpotConnector)

    @pytest.mark.asyncio
    async def test_list_success(self):
        resp = _mock_response(200, {
            "results": [
                {
                    "id": "pipe-001",
                    "label": "Sales Pipeline",
                    "stages": [
                        {"id": "s1", "label": "Qualification", "displayOrder": 0},
                        {"id": "s2", "label": "Proposal", "displayOrder": 1},
                    ],
                },
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("hubspot", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["hubspot_list_pipelines"](ctx)
        assert "Sales Pipeline" in result
        assert "Qualification" in result
        assert "Proposal" in result

    @pytest.mark.asyncio
    async def test_list_empty(self):
        resp = _mock_response(200, {"results": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("hubspot", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["hubspot_list_pipelines"](ctx)
        assert "No pipelines found" in result


class TestHubSpotGetActivities:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.hubspot import HubSpotConnector
        self.tools = _register_tools(HubSpotConnector)

    @pytest.mark.asyncio
    async def test_get_activities_success(self):
        resp = _mock_response(200, {
            "results": [
                {"id": "note-001", "type": "note"},
                {"id": "note-002", "type": "note"},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("hubspot", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["hubspot_get_activities"]("contacts", "101", ctx)
        assert "note-001" in result
        assert "note-002" in result

    @pytest.mark.asyncio
    async def test_get_activities_invalid_type(self):
        ctx = MagicMock()
        result = await self.tools["hubspot_get_activities"]("invalid_type", "101", ctx)
        assert "Invalid object_type" in result

    @pytest.mark.asyncio
    async def test_get_activities_empty(self):
        resp = _mock_response(200, {"results": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("hubspot", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["hubspot_get_activities"]("deals", "d-001", ctx)
        assert "No activities found" in result


class TestHubSpotCreateContact:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.hubspot import HubSpotConnector
        self.tools = _register_tools(HubSpotConnector)

    @pytest.mark.asyncio
    async def test_create_success(self):
        resp = _mock_response(200, {"id": "new-101"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("hubspot", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["hubspot_create_contact"]("jane@acme.com", ctx, firstname="Jane", lastname="Doe")
        assert "Contact created" in result
        assert "new-101" in result

    @pytest.mark.asyncio
    async def test_create_invalid_email(self):
        ctx = MagicMock()
        result = await self.tools["hubspot_create_contact"]("not-an-email", ctx)
        assert "email" in result.lower()


class TestHubSpotCreateDeal:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.hubspot import HubSpotConnector
        self.tools = _register_tools(HubSpotConnector)

    @pytest.mark.asyncio
    async def test_create_success(self):
        resp = _mock_response(200, {"id": "deal-new-001"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("hubspot", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["hubspot_create_deal"]("New Deal", "default", "qualifiedtobuy", ctx, amount="5000")
        assert "Deal created" in result
        assert "deal-new-001" in result

    @pytest.mark.asyncio
    async def test_create_empty_name(self):
        ctx = MagicMock()
        result = await self.tools["hubspot_create_deal"]("", "default", "stage", ctx)
        assert "required" in result.lower()


# --- Phase 2: Confluence New Tools ---


class TestConfluenceListPages:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.confluence import ConfluenceConnector
        self.tools = _register_tools(ConfluenceConnector)

    @pytest.mark.asyncio
    async def test_list_success(self):
        resp = _mock_response(200, {
            "results": [
                {"title": "Getting Started", "id": "pg-001", "version": {"number": 5}},
                {"title": "FAQ", "id": "pg-002", "version": {"number": 2}},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("atlassian", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["confluence_list_pages"]("ENG", ctx)
        assert "Getting Started" in result
        assert "FAQ" in result
        assert "pg-001" in result

    @pytest.mark.asyncio
    async def test_list_empty(self):
        resp = _mock_response(200, {"results": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("atlassian", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["confluence_list_pages"]("EMPTY", ctx)
        assert "No pages found" in result


class TestConfluenceGetPageHistory:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.confluence import ConfluenceConnector
        self.tools = _register_tools(ConfluenceConnector)

    @pytest.mark.asyncio
    async def test_history_success(self):
        resp = _mock_response(200, {
            "createdBy": {"displayName": "Alice"},
            "createdDate": "2024-01-15T10:00:00Z",
            "lastUpdated": {
                "by": {"displayName": "Bob"},
                "when": "2024-06-01T14:00:00Z",
                "number": 7,
            },
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("atlassian", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["confluence_get_page_history"]("pg-001", ctx)
        assert "Alice" in result
        assert "Bob" in result
        assert "7" in result

    @pytest.mark.asyncio
    async def test_history_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["confluence_get_page_history"]("", ctx)
        assert "required" in result.lower()


class TestConfluenceListAttachments:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.confluence import ConfluenceConnector
        self.tools = _register_tools(ConfluenceConnector)

    @pytest.mark.asyncio
    async def test_list_success(self):
        resp = _mock_response(200, {
            "results": [
                {
                    "title": "diagram.png",
                    "id": "att-001",
                    "extensions": {"fileSize": 102400, "mediaType": "image/png"},
                },
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("atlassian", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["confluence_list_attachments"]("pg-001", ctx)
        assert "diagram.png" in result
        assert "image/png" in result

    @pytest.mark.asyncio
    async def test_list_empty(self):
        resp = _mock_response(200, {"results": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("atlassian", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["confluence_list_attachments"]("pg-001", ctx)
        assert "No attachments found" in result


class TestConfluenceCreatePage:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.confluence import ConfluenceConnector
        self.tools = _register_tools(ConfluenceConnector)

    @pytest.mark.asyncio
    async def test_create_success(self):
        resp = _mock_response(200, {"id": "pg-new-001", "title": "New Page"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("atlassian", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["confluence_create_page"]("ENG", "New Page", "<p>Hello</p>", ctx)
        assert "Page created" in result
        assert "pg-new-001" in result

    @pytest.mark.asyncio
    async def test_create_empty_title(self):
        ctx = MagicMock()
        result = await self.tools["confluence_create_page"]("ENG", "", "<p>body</p>", ctx)
        assert "required" in result.lower()


# --- Phase 2: Google Workspace New Tools ---


class TestGDriveGetFileInfo:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.google_workspace import GoogleWorkspaceConnector
        self.tools = _register_tools(GoogleWorkspaceConnector)

    @pytest.mark.asyncio
    async def test_get_file_info_success(self):
        resp = _mock_response(200, {
            "id": "file-001",
            "name": "Roadmap.docx",
            "mimeType": "application/vnd.google-apps.document",
            "modifiedTime": "2024-05-15T00:00:00Z",
            "createdTime": "2024-01-10T00:00:00Z",
            "size": "12345",
            "shared": True,
            "owners": [{"displayName": "Alice"}],
            "webViewLink": "https://docs.google.com/document/d/file-001",
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("google", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["gdrive_get_file_info"]("file-001", ctx)
        assert "Roadmap.docx" in result
        assert "Alice" in result
        assert "12345" in result

    @pytest.mark.asyncio
    async def test_get_file_info_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["gdrive_get_file_info"]("", ctx)
        assert "required" in result.lower()


class TestGCalendarGetEvent:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.google_workspace import GoogleWorkspaceConnector
        self.tools = _register_tools(GoogleWorkspaceConnector)

    @pytest.mark.asyncio
    async def test_get_event_success(self):
        resp = _mock_response(200, {
            "summary": "Board Meeting",
            "start": {"dateTime": "2024-06-10T14:00:00-07:00"},
            "end": {"dateTime": "2024-06-10T15:00:00-07:00"},
            "location": "Conference Room A",
            "organizer": {"email": "ceo@company.com"},
            "description": "Quarterly board review",
            "attendees": [
                {"email": "a@co.com"},
                {"email": "b@co.com"},
            ],
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("google", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["gcalendar_get_event"]("evt-001", ctx)
        assert "Board Meeting" in result
        assert "Conference Room A" in result
        assert "ceo@company.com" in result
        assert "2 attendees" in result or "a@co.com" in result

    @pytest.mark.asyncio
    async def test_get_event_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["gcalendar_get_event"]("", ctx)
        assert "required" in result.lower()


class TestGCalendarCreateEvent:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.google_workspace import GoogleWorkspaceConnector
        self.tools = _register_tools(GoogleWorkspaceConnector)

    @pytest.mark.asyncio
    async def test_create_success(self):
        resp = _mock_response(200, {"id": "evt-new-001", "htmlLink": "https://calendar.google.com/event/evt-new-001"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("google", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["gcalendar_create_event"](
                "Team Lunch", "2024-06-15T12:00:00-07:00", "2024-06-15T13:00:00-07:00", ctx,
                attendees="alice@co.com, bob@co.com",
            )
        assert "Event created" in result
        assert "evt-new-001" in result

    @pytest.mark.asyncio
    async def test_create_invalid_attendee_email(self):
        ctx = MagicMock()
        with _patch_require_service("google", _mock_client(_mock_response())):
            result = await self.tools["gcalendar_create_event"](
                "Meeting", "2024-06-15T12:00:00", "2024-06-15T13:00:00", ctx,
                attendees="not-an-email",
            )
        assert "email" in result.lower()

    @pytest.mark.asyncio
    async def test_create_empty_summary(self):
        ctx = MagicMock()
        result = await self.tools["gcalendar_create_event"]("", "2024-06-15T12:00:00", "2024-06-15T13:00:00", ctx)
        assert "required" in result.lower()


# --- Phase 2: Notion New Tools ---


class TestNotionCreatePage:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.notion import NotionConnector
        self.tools = _register_tools(NotionConnector)

    @pytest.mark.asyncio
    async def test_create_success(self):
        resp = _mock_response(200, {"id": "page-new-001"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("notion", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["notion_create_page"]("parent-001", "My New Page", ctx, content="Hello world")
        assert "Page created" in result
        assert "page-new-001" in result

    @pytest.mark.asyncio
    async def test_create_empty_title(self):
        ctx = MagicMock()
        result = await self.tools["notion_create_page"]("parent-001", "", ctx)
        assert "required" in result.lower()


class TestNotionUpdatePage:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.notion import NotionConnector
        self.tools = _register_tools(NotionConnector)

    @pytest.mark.asyncio
    async def test_update_success(self):
        resp = _mock_response(200, {"id": "page-001"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("notion", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["notion_update_page"]("page-001", ctx, properties_json='{"Status": {"status": {"name": "Done"}}}')
        assert "Page updated" in result
        assert "page-001" in result

    @pytest.mark.asyncio
    async def test_update_invalid_json(self):
        ctx = MagicMock()
        with _patch_require_service("notion", _mock_client(_mock_response())):
            result = await self.tools["notion_update_page"]("page-001", ctx, properties_json="not valid json")
        assert "Invalid properties_json" in result


class TestNotionCreateDatabaseEntry:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.notion import NotionConnector
        self.tools = _register_tools(NotionConnector)

    @pytest.mark.asyncio
    async def test_create_success(self):
        resp = _mock_response(200, {"id": "entry-new-001"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("notion", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["notion_create_database_entry"](
                "db-100", ctx,
                properties_json='{"Name": {"title": [{"text": {"content": "New Task"}}]}}',
            )
        assert "Entry created" in result
        assert "entry-new-001" in result

    @pytest.mark.asyncio
    async def test_create_invalid_json(self):
        ctx = MagicMock()
        with _patch_require_service("notion", _mock_client(_mock_response())):
            result = await self.tools["notion_create_database_entry"]("db-100", ctx, properties_json="{bad json")
        assert "Invalid properties_json" in result


# --- Phase 2: Smartsheet New Tools ---


class TestSmartsheetGetRow:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.smartsheet import SmartsheetConnector
        self.tools = _register_tools(SmartsheetConnector)

    @pytest.mark.asyncio
    async def test_get_row_success(self):
        resp = _mock_response(200, {
            "id": "row-001",
            "rowNumber": 3,
            "cells": [
                {"columnId": 1, "value": "Task A", "displayValue": "Task A"},
                {"columnId": 2, "value": "Complete", "displayValue": "Complete"},
            ],
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("smartsheet", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["smartsheet_get_row"]("sheet-001", "row-001", ctx)
        assert "row-001" in result
        assert "Task A" in result
        assert "Complete" in result

    @pytest.mark.asyncio
    async def test_get_row_empty_ids(self):
        ctx = MagicMock()
        result = await self.tools["smartsheet_get_row"]("", "row-001", ctx)
        assert "required" in result.lower()
        result2 = await self.tools["smartsheet_get_row"]("sheet-001", "", ctx)
        assert "required" in result2.lower()


class TestSmartsheetListColumns:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.smartsheet import SmartsheetConnector
        self.tools = _register_tools(SmartsheetConnector)

    @pytest.mark.asyncio
    async def test_list_success(self):
        resp = _mock_response(200, {
            "data": [
                {"title": "Task Name", "type": "TEXT_NUMBER", "id": 1},
                {"title": "Status", "type": "PICKLIST", "id": 2},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("smartsheet", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["smartsheet_list_columns"]("sheet-001", ctx)
        assert "Task Name" in result
        assert "TEXT_NUMBER" in result
        assert "Status" in result

    @pytest.mark.asyncio
    async def test_list_empty(self):
        resp = _mock_response(200, {"data": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("smartsheet", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["smartsheet_list_columns"]("sheet-001", ctx)
        assert "No columns found" in result


class TestSmartsheetAddRow:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.smartsheet import SmartsheetConnector
        self.tools = _register_tools(SmartsheetConnector)

    @pytest.mark.asyncio
    async def test_add_success(self):
        resp = _mock_response(200, {"result": [{"id": "row-new-001"}]})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("smartsheet", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["smartsheet_add_row"](
                "sheet-001", ctx,
                cells_json='[{"columnId": 1, "value": "New Task"}]',
            )
        assert "Row added" in result
        assert "row-new-001" in result

    @pytest.mark.asyncio
    async def test_add_invalid_json(self):
        ctx = MagicMock()
        with _patch_require_service("smartsheet", _mock_client(_mock_response())):
            result = await self.tools["smartsheet_add_row"]("sheet-001", ctx, cells_json="not json")
        assert "Invalid cells_json" in result


# --- Phase 2: Figma New Tools ---


class TestFigmaGetFileVersions:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.figma import FigmaConnector
        self.tools = _register_tools(FigmaConnector)

    @pytest.mark.asyncio
    async def test_versions_success(self):
        resp = _mock_response(200, {
            "versions": [
                {
                    "id": "v-001",
                    "label": "Final Design",
                    "created_at": "2024-06-01T12:00:00Z",
                    "user": {"handle": "alice"},
                },
                {
                    "id": "v-002",
                    "label": "",
                    "created_at": "2024-05-28T10:00:00Z",
                    "user": {"handle": "bob"},
                },
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("figma", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["figma_get_file_versions"]("abc123", ctx)
        assert "v-001" in result
        assert "Final Design" in result
        assert "alice" in result

    @pytest.mark.asyncio
    async def test_versions_empty(self):
        resp = _mock_response(200, {"versions": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("figma", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["figma_get_file_versions"]("abc123", ctx)
        assert "No versions found" in result


class TestFigmaGetComponents:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.figma import FigmaConnector
        self.tools = _register_tools(FigmaConnector)

    @pytest.mark.asyncio
    async def test_components_success(self):
        resp = _mock_response(200, {
            "meta": {
                "components": [
                    {"name": "Button", "key": "comp-001", "description": "Primary button component"},
                    {"name": "Card", "key": "comp-002", "description": ""},
                ]
            }
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("figma", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["figma_get_components"]("abc123", ctx)
        assert "Button" in result
        assert "comp-001" in result
        assert "Primary button component" in result

    @pytest.mark.asyncio
    async def test_components_empty(self):
        resp = _mock_response(200, {"meta": {"components": []}})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("figma", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["figma_get_components"]("abc123", ctx)
        assert "No components found" in result


class TestFigmaGetStyles:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.figma import FigmaConnector
        self.tools = _register_tools(FigmaConnector)

    @pytest.mark.asyncio
    async def test_styles_success(self):
        resp = _mock_response(200, {
            "meta": {
                "styles": [
                    {"name": "Primary Blue", "key": "style-001", "style_type": "FILL", "description": "Main brand color"},
                    {"name": "Heading Font", "key": "style-002", "style_type": "TEXT", "description": ""},
                ]
            }
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("figma", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["figma_get_styles"]("abc123", ctx)
        assert "Primary Blue" in result
        assert "FILL" in result
        assert "Main brand color" in result
        assert "Heading Font" in result

    @pytest.mark.asyncio
    async def test_styles_empty(self):
        resp = _mock_response(200, {"meta": {"styles": []}})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("figma", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["figma_get_styles"]("abc123", ctx)
        assert "No styles found" in result


# --- Phase 2: Zendesk New Tools ---


class TestZendeskListUsers:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.zendesk import ZendeskConnector
        self.tools = _register_tools(ZendeskConnector)

    @pytest.mark.asyncio
    async def test_list_success(self):
        resp = _mock_response(200, {
            "users": [
                {"name": "Jane Agent", "email": "jane@support.com", "role": "agent", "active": True, "id": 1001},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("zendesk", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["zendesk_list_users"](ctx)
        assert "Jane Agent" in result
        assert "jane@support.com" in result
        assert "agent" in result

    @pytest.mark.asyncio
    async def test_search_users(self):
        resp = _mock_response(200, {
            "users": [
                {"name": "Bob Customer", "email": "bob@example.com", "role": "end-user", "active": True, "id": 2001},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("zendesk", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["zendesk_list_users"](ctx, query="bob")
        assert "Bob Customer" in result

    @pytest.mark.asyncio
    async def test_list_empty(self):
        resp = _mock_response(200, {"users": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("zendesk", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["zendesk_list_users"](ctx)
        assert "No users found" in result


class TestZendeskGetUser:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.zendesk import ZendeskConnector
        self.tools = _register_tools(ZendeskConnector)

    @pytest.mark.asyncio
    async def test_get_user_success(self):
        resp = _mock_response(200, {
            "user": {
                "name": "Alice Support",
                "email": "alice@support.com",
                "role": "admin",
                "active": True,
                "id": 3001,
            }
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("zendesk", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["zendesk_get_user"]("3001", ctx)
        assert "Alice Support" in result
        assert "alice@support.com" in result
        assert "admin" in result

    @pytest.mark.asyncio
    async def test_get_user_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["zendesk_get_user"]("", ctx)
        assert "required" in result.lower()


class TestZendeskCreateTicket:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.zendesk import ZendeskConnector
        self.tools = _register_tools(ZendeskConnector)

    @pytest.mark.asyncio
    async def test_create_success(self):
        resp = _mock_response(200, {"ticket": {"id": 500, "subject": "Printer broken"}})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("zendesk", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["zendesk_create_ticket"]("Printer broken", "The office printer is not working", ctx, priority="high")
        assert "Ticket created" in result
        assert "#500" in result

    @pytest.mark.asyncio
    async def test_create_invalid_priority(self):
        ctx = MagicMock()
        result = await self.tools["zendesk_create_ticket"]("Test", "Body", ctx, priority="critical")
        assert "Invalid priority" in result

    @pytest.mark.asyncio
    async def test_create_empty_subject(self):
        ctx = MagicMock()
        result = await self.tools["zendesk_create_ticket"]("", "Body", ctx)
        assert "required" in result.lower()


class TestZendeskAddComment:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.zendesk import ZendeskConnector
        self.tools = _register_tools(ZendeskConnector)

    @pytest.mark.asyncio
    async def test_add_comment_success(self):
        resp = _mock_response(200, {"ticket": {"id": 42}})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("zendesk", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["zendesk_add_comment"]("42", "We are looking into this.", ctx)
        assert "Comment added" in result
        assert "42" in result

    @pytest.mark.asyncio
    async def test_add_comment_empty_body(self):
        ctx = MagicMock()
        result = await self.tools["zendesk_add_comment"]("42", "", ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_add_comment_empty_ticket_id(self):
        ctx = MagicMock()
        result = await self.tools["zendesk_add_comment"]("", "Some comment", ctx)
        assert "required" in result.lower()


class TestJiraUpdateIssue:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.jira import JiraConnector
        self.tools = _register_tools(JiraConnector)

    @pytest.mark.asyncio
    async def test_update_success(self):
        resp = _mock_response(204)
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("atlassian", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["jira_update_issue"]("PROJ-123", '{"summary": "New title"}', ctx)
        assert "Updated PROJ-123" in result

    @pytest.mark.asyncio
    async def test_update_invalid_json(self):
        ctx = MagicMock()
        with _patch_require_service("atlassian", _mock_client(_mock_response())):
            result = await self.tools["jira_update_issue"]("PROJ-123", "not valid json", ctx)
        assert "Invalid fields" in result

    @pytest.mark.asyncio
    async def test_update_invalid_key(self):
        ctx = MagicMock()
        result = await self.tools["jira_update_issue"]("bad", '{"summary": "x"}', ctx)
        assert "Invalid issue key" in result



class TestJiraCreateSubtask:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.jira import JiraConnector
        self.tools = _register_tools(JiraConnector)

    @pytest.mark.asyncio
    async def test_create_success(self):
        resp = _mock_response(200, {"key": "PROJ-456"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("atlassian", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["jira_create_subtask"]("PROJ-123", "PROJ", "Sub task", ctx)
        assert "PROJ-456" in result
        assert "PROJ-123" in result

    @pytest.mark.asyncio
    async def test_create_with_description(self):
        resp = _mock_response(200, {"key": "PROJ-457"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("atlassian", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["jira_create_subtask"]("PROJ-123", "PROJ", "Sub task", ctx, description="Details here")
        assert "PROJ-457" in result

    @pytest.mark.asyncio
    async def test_create_invalid_parent(self):
        ctx = MagicMock()
        result = await self.tools["jira_create_subtask"]("bad", "PROJ", "Sub task", ctx)
        assert "Invalid issue key" in result

    @pytest.mark.asyncio
    async def test_create_empty_summary(self):
        ctx = MagicMock()
        result = await self.tools["jira_create_subtask"]("PROJ-123", "PROJ", "", ctx)
        assert "required" in result.lower()



class TestJiraLinkIssues:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.jira import JiraConnector
        self.tools = _register_tools(JiraConnector)

    @pytest.mark.asyncio
    async def test_link_success(self):
        resp = _mock_response(201)
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("atlassian", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["jira_link_issues"]("PROJ-123", "PROJ-456", "Blocks", ctx)
        assert "Linked" in result
        assert "PROJ-123" in result
        assert "PROJ-456" in result
        assert "Blocks" in result

    @pytest.mark.asyncio
    async def test_link_invalid_inward(self):
        ctx = MagicMock()
        result = await self.tools["jira_link_issues"]("bad", "PROJ-456", "Blocks", ctx)
        assert "Invalid issue key" in result

    @pytest.mark.asyncio
    async def test_link_empty_type(self):
        ctx = MagicMock()
        result = await self.tools["jira_link_issues"]("PROJ-123", "PROJ-456", "", ctx)
        assert "required" in result.lower()



class TestJiraAddLabel:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.jira import JiraConnector
        self.tools = _register_tools(JiraConnector)

    @pytest.mark.asyncio
    async def test_add_label_success(self):
        resp = _mock_response(204)
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("atlassian", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["jira_add_label"]("PROJ-123", "urgent", ctx)
        assert "urgent" in result
        assert "PROJ-123" in result

    @pytest.mark.asyncio
    async def test_add_label_empty(self):
        ctx = MagicMock()
        result = await self.tools["jira_add_label"]("PROJ-123", "", ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_add_label_invalid_key(self):
        ctx = MagicMock()
        result = await self.tools["jira_add_label"]("bad", "urgent", ctx)
        assert "Invalid issue key" in result



class TestJiraRemoveLabel:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.jira import JiraConnector
        self.tools = _register_tools(JiraConnector)

    @pytest.mark.asyncio
    async def test_remove_label_success(self):
        resp = _mock_response(204)
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("atlassian", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["jira_remove_label"]("PROJ-123", "urgent", ctx)
        assert "urgent" in result
        assert "removed" in result.lower()
        assert "PROJ-123" in result

    @pytest.mark.asyncio
    async def test_remove_label_empty(self):
        ctx = MagicMock()
        result = await self.tools["jira_remove_label"]("PROJ-123", "", ctx)
        assert "required" in result.lower()



class TestJiraAssignIssue:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.jira import JiraConnector
        self.tools = _register_tools(JiraConnector)

    @pytest.mark.asyncio
    async def test_assign_by_email(self):
        resp = _mock_response(204)
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("atlassian", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["jira_assign_issue"]("PROJ-123", "alice@example.com", ctx)
        assert "Assigned" in result
        assert "PROJ-123" in result
        assert "alice@example.com" in result

    @pytest.mark.asyncio
    async def test_assign_by_account_id(self):
        resp = _mock_response(204)
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("atlassian", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["jira_assign_issue"]("PROJ-123", "5b10ac8d82e05b22cc7d4ef5", ctx)
        assert "Assigned" in result
        assert "PROJ-123" in result

    @pytest.mark.asyncio
    async def test_assign_empty_assignee(self):
        ctx = MagicMock()
        result = await self.tools["jira_assign_issue"]("PROJ-123", "", ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_assign_invalid_key(self):
        ctx = MagicMock()
        result = await self.tools["jira_assign_issue"]("bad", "alice@example.com", ctx)
        assert "Invalid issue key" in result



class TestJiraListBoards:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.jira import JiraConnector
        self.tools = _register_tools(JiraConnector)

    @pytest.mark.asyncio
    async def test_list_boards_success(self):
        resp = _mock_response(200, {
            "values": [
                {"name": "Sprint Board", "id": 1, "type": "scrum"},
                {"name": "Kanban Board", "id": 2, "type": "kanban"},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("atlassian", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)), \
             patch.object(token_store, "get_credentials", return_value={"domain": "test.atlassian.net"}):
            result = await self.tools["jira_list_boards"](ctx)
        assert "Sprint Board" in result
        assert "Kanban Board" in result
        assert "scrum" in result

    @pytest.mark.asyncio
    async def test_list_boards_with_project(self):
        resp = _mock_response(200, {
            "values": [
                {"name": "PROJ Board", "id": 10, "type": "scrum"},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("atlassian", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)), \
             patch.object(token_store, "get_credentials", return_value={"domain": "test.atlassian.net"}):
            result = await self.tools["jira_list_boards"](ctx, project_key="PROJ")
        assert "PROJ Board" in result

    @pytest.mark.asyncio
    async def test_list_boards_empty(self):
        resp = _mock_response(200, {"values": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("atlassian", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)), \
             patch.object(token_store, "get_credentials", return_value={"domain": "test.atlassian.net"}):
            result = await self.tools["jira_list_boards"](ctx)
        assert "No boards found" in result



class TestJiraGetSprintIssues:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.jira import JiraConnector
        self.tools = _register_tools(JiraConnector)

    @pytest.mark.asyncio
    async def test_get_sprint_issues_success(self):
        resp = _mock_response(200, {
            "issues": [
                {
                    "key": "PROJ-100",
                    "fields": {
                        "summary": "Implement login",
                        "status": {"name": "In Progress"},
                        "assignee": {"displayName": "Alice"},
                        "priority": {"name": "High"},
                    },
                },
                {
                    "key": "PROJ-101",
                    "fields": {
                        "summary": "Fix bug",
                        "status": {"name": "Done"},
                        "assignee": None,
                        "priority": {"name": "Medium"},
                    },
                },
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("atlassian", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)), \
             patch.object(token_store, "get_credentials", return_value={"domain": "test.atlassian.net"}):
            result = await self.tools["jira_get_sprint_issues"]("42", ctx)
        assert "PROJ-100" in result
        assert "Implement login" in result
        assert "Alice" in result
        assert "PROJ-101" in result
        assert "Unassigned" in result

    @pytest.mark.asyncio
    async def test_get_sprint_issues_empty(self):
        resp = _mock_response(200, {"issues": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("atlassian", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)), \
             patch.object(token_store, "get_credentials", return_value={"domain": "test.atlassian.net"}):
            result = await self.tools["jira_get_sprint_issues"]("42", ctx)
        assert "No issues found" in result

    @pytest.mark.asyncio
    async def test_get_sprint_issues_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["jira_get_sprint_issues"]("", ctx)
        assert "required" in result.lower()



class TestConfluenceUpdatePage:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.confluence import ConfluenceConnector
        self.tools = _register_tools(ConfluenceConnector)

    @pytest.mark.asyncio
    async def test_update_success(self):
        resp = _mock_response(200, {"id": "pg-001", "title": "Updated Page", "version": {"number": 4}})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("atlassian", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["confluence_update_page"]("pg-001", "Updated Page", "<p>New content</p>", 3, ctx)
        assert "Page updated" in result
        assert "pg-001" in result
        assert "Updated Page" in result

    @pytest.mark.asyncio
    async def test_update_empty_title(self):
        ctx = MagicMock()
        result = await self.tools["confluence_update_page"]("pg-001", "", "<p>body</p>", 1, ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_update_empty_body(self):
        ctx = MagicMock()
        result = await self.tools["confluence_update_page"]("pg-001", "Title", "", 1, ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_update_empty_page_id(self):
        ctx = MagicMock()
        result = await self.tools["confluence_update_page"]("", "Title", "<p>body</p>", 1, ctx)
        assert "required" in result.lower()



class TestConfluenceAddCommentTool:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.confluence import ConfluenceConnector
        self.tools = _register_tools(ConfluenceConnector)

    @pytest.mark.asyncio
    async def test_add_comment_success(self):
        resp = _mock_response(200, {"id": "comment-001"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("atlassian", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["confluence_add_comment"]("pg-001", "<p>Great page!</p>", ctx)
        assert "Comment added" in result
        assert "pg-001" in result
        assert "comment-001" in result

    @pytest.mark.asyncio
    async def test_add_comment_empty_body(self):
        ctx = MagicMock()
        result = await self.tools["confluence_add_comment"]("pg-001", "", ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_add_comment_empty_page_id(self):
        ctx = MagicMock()
        result = await self.tools["confluence_add_comment"]("", "<p>body</p>", ctx)
        assert "required" in result.lower()



class TestConfluenceAddLabelTool:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.confluence import ConfluenceConnector
        self.tools = _register_tools(ConfluenceConnector)

    @pytest.mark.asyncio
    async def test_add_label_success(self):
        resp = _mock_response(200, {})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("atlassian", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["confluence_add_label"]("pg-001", "important", ctx)
        assert "important" in result
        assert "pg-001" in result

    @pytest.mark.asyncio
    async def test_add_label_empty_label(self):
        ctx = MagicMock()
        result = await self.tools["confluence_add_label"]("pg-001", "", ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_add_label_empty_page_id(self):
        ctx = MagicMock()
        result = await self.tools["confluence_add_label"]("", "label", ctx)
        assert "required" in result.lower()



class TestConfluenceRemoveLabelTool:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.confluence import ConfluenceConnector
        self.tools = _register_tools(ConfluenceConnector)

    @pytest.mark.asyncio
    async def test_remove_label_success(self):
        resp = _mock_response(204)
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("atlassian", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["confluence_remove_label"]("pg-001", "old-label", ctx)
        assert "old-label" in result
        assert "removed" in result.lower()
        assert "pg-001" in result

    @pytest.mark.asyncio
    async def test_remove_label_empty_label(self):
        ctx = MagicMock()
        result = await self.tools["confluence_remove_label"]("pg-001", "", ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_remove_label_empty_page_id(self):
        ctx = MagicMock()
        result = await self.tools["confluence_remove_label"]("", "label", ctx)
        assert "required" in result.lower()



class TestConfluenceDeletePage:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.confluence import ConfluenceConnector
        self.tools = _register_tools(ConfluenceConnector)

    @pytest.mark.asyncio
    async def test_delete_success(self):
        resp = _mock_response(204)
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("atlassian", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["confluence_delete_page"]("pg-001", ctx)
        assert "pg-001" in result
        assert "deleted" in result.lower()

    @pytest.mark.asyncio
    async def test_delete_empty_page_id(self):
        ctx = MagicMock()
        result = await self.tools["confluence_delete_page"]("", ctx)
        assert "required" in result.lower()



class TestConfluenceMovePage:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.confluence import ConfluenceConnector
        self.tools = _register_tools(ConfluenceConnector)

    @pytest.mark.asyncio
    async def test_move_success(self):
        get_resp = _mock_response(200, {
            "title": "My Page",
            "version": {"number": 3},
            "ancestors": [{"id": "old-parent"}],
        })
        put_resp = _mock_response(200, {"id": "pg-001", "title": "My Page"})
        client = _mock_client([get_resp, put_resp])
        ctx = MagicMock()
        with _patch_require_service("atlassian", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, side_effect=[(get_resp, None), (put_resp, None)]):
            result = await self.tools["confluence_move_page"]("pg-001", "new-parent", ctx)
        assert "pg-001" in result
        assert "new-parent" in result
        assert "moved" in result.lower()

    @pytest.mark.asyncio
    async def test_move_empty_page_id(self):
        ctx = MagicMock()
        result = await self.tools["confluence_move_page"]("", "new-parent", ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_move_empty_target(self):
        ctx = MagicMock()
        result = await self.tools["confluence_move_page"]("pg-001", "", ctx)
        assert "required" in result.lower()



class TestConfluenceUploadAttachment:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.confluence import ConfluenceConnector
        self.tools = _register_tools(ConfluenceConnector)

    @pytest.mark.asyncio
    async def test_upload_success(self):
        import base64
        content_b64 = base64.b64encode(b"file content here").decode()
        resp = _mock_response(200, {
            "results": [{"id": "att-001", "title": "test.txt"}]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("atlassian", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["confluence_upload_attachment"]("pg-001", "test.txt", content_b64, ctx)
        assert "att-001" in result
        assert "test.txt" in result

    @pytest.mark.asyncio
    async def test_upload_invalid_base64(self):
        ctx = MagicMock()
        with _patch_require_service("atlassian", _mock_client(_mock_response())):
            result = await self.tools["confluence_upload_attachment"]("pg-001", "test.txt", "!!!not-base64!!!", ctx)
        assert "Invalid content" in result

    @pytest.mark.asyncio
    async def test_upload_empty_page_id(self):
        ctx = MagicMock()
        result = await self.tools["confluence_upload_attachment"]("", "test.txt", "dGVzdA==", ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_upload_empty_filename(self):
        ctx = MagicMock()
        result = await self.tools["confluence_upload_attachment"]("pg-001", "", "dGVzdA==", ctx)
        assert "required" in result.lower()



class TestHubSpotUpdateContact:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.hubspot import HubSpotConnector
        self.tools = _register_tools(HubSpotConnector)

    @pytest.mark.asyncio
    async def test_update_success(self):
        resp = _mock_response(200, {"id": "101"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("hubspot", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["hubspot_update_contact"]("101", ctx, properties_json='{"email": "new@example.com"}')
        assert "Contact 101 updated" in result

    @pytest.mark.asyncio
    async def test_update_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["hubspot_update_contact"]("", ctx, properties_json='{"email": "x@y.com"}')
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_update_invalid_json(self):
        ctx = MagicMock()
        with _patch_require_service("hubspot", _mock_client(_mock_response())):
            result = await self.tools["hubspot_update_contact"]("101", ctx, properties_json="not json")
        assert "Invalid properties_json" in result

    @pytest.mark.asyncio
    async def test_update_missing_properties(self):
        ctx = MagicMock()
        result = await self.tools["hubspot_update_contact"]("101", ctx)
        assert "properties_json is required" in result



class TestHubSpotUpdateDeal:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.hubspot import HubSpotConnector
        self.tools = _register_tools(HubSpotConnector)

    @pytest.mark.asyncio
    async def test_update_success(self):
        resp = _mock_response(200, {"id": "d-001"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("hubspot", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["hubspot_update_deal"]("d-001", ctx, properties_json='{"dealstage": "closedwon"}')
        assert "Deal d-001 updated" in result

    @pytest.mark.asyncio
    async def test_update_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["hubspot_update_deal"]("", ctx, properties_json='{"amount": "100"}')
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_update_invalid_json(self):
        ctx = MagicMock()
        with _patch_require_service("hubspot", _mock_client(_mock_response())):
            result = await self.tools["hubspot_update_deal"]("d-001", ctx, properties_json="{bad}")
        assert "Invalid properties_json" in result



class TestHubSpotUpdateCompany:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.hubspot import HubSpotConnector
        self.tools = _register_tools(HubSpotConnector)

    @pytest.mark.asyncio
    async def test_update_success(self):
        resp = _mock_response(200, {"id": "c-001"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("hubspot", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["hubspot_update_company"]("c-001", ctx, properties_json='{"name": "Updated Corp"}')
        assert "Company c-001 updated" in result

    @pytest.mark.asyncio
    async def test_update_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["hubspot_update_company"]("", ctx, properties_json='{"name": "X"}')
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_update_invalid_json(self):
        ctx = MagicMock()
        with _patch_require_service("hubspot", _mock_client(_mock_response())):
            result = await self.tools["hubspot_update_company"]("c-001", ctx, properties_json="not valid")
        assert "Invalid properties_json" in result



class TestHubSpotCreateCompany:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.hubspot import HubSpotConnector
        self.tools = _register_tools(HubSpotConnector)

    @pytest.mark.asyncio
    async def test_create_success(self):
        resp = _mock_response(200, {"id": "c-new-001"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("hubspot", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["hubspot_create_company"]("Acme Corp", ctx, properties_json='{"domain": "acme.com"}')
        assert "Company created" in result
        assert "c-new-001" in result

    @pytest.mark.asyncio
    async def test_create_no_extra_props(self):
        resp = _mock_response(200, {"id": "c-new-002"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("hubspot", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["hubspot_create_company"]("Simple Corp", ctx)
        assert "Company created" in result
        assert "c-new-002" in result

    @pytest.mark.asyncio
    async def test_create_empty_name(self):
        ctx = MagicMock()
        result = await self.tools["hubspot_create_company"]("", ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_create_invalid_json(self):
        ctx = MagicMock()
        with _patch_require_service("hubspot", _mock_client(_mock_response())):
            result = await self.tools["hubspot_create_company"]("Test", ctx, properties_json="bad json")
        assert "Invalid properties_json" in result



class TestHubSpotCreateAssociation:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.hubspot import HubSpotConnector
        self.tools = _register_tools(HubSpotConnector)

    @pytest.mark.asyncio
    async def test_create_success(self):
        resp = _mock_response(200, {})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("hubspot", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["hubspot_create_association"]("contacts", "101", "deals", "d-001", "contact_to_deal", ctx)
        assert "Association created" in result
        assert "contacts/101" in result
        assert "deals/d-001" in result

    @pytest.mark.asyncio
    async def test_invalid_from_type(self):
        ctx = MagicMock()
        result = await self.tools["hubspot_create_association"]("invalid", "101", "deals", "d-001", "type", ctx)
        assert "Invalid from_type" in result

    @pytest.mark.asyncio
    async def test_invalid_to_type(self):
        ctx = MagicMock()
        result = await self.tools["hubspot_create_association"]("contacts", "101", "invalid", "d-001", "type", ctx)
        assert "Invalid to_type" in result

    @pytest.mark.asyncio
    async def test_empty_ids(self):
        ctx = MagicMock()
        result = await self.tools["hubspot_create_association"]("contacts", "", "deals", "d-001", "type", ctx)
        assert "required" in result.lower()



class TestHubSpotListAssociations:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.hubspot import HubSpotConnector
        self.tools = _register_tools(HubSpotConnector)

    @pytest.mark.asyncio
    async def test_list_success(self):
        resp = _mock_response(200, {
            "results": [
                {"id": "d-001", "type": "contact_to_deal"},
                {"id": "d-002", "type": "contact_to_deal"},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("hubspot", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["hubspot_list_associations"]("contacts", "101", "deals", ctx)
        assert "d-001" in result
        assert "d-002" in result

    @pytest.mark.asyncio
    async def test_list_empty(self):
        resp = _mock_response(200, {"results": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("hubspot", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["hubspot_list_associations"]("contacts", "101", "deals", ctx)
        assert "No associations found" in result

    @pytest.mark.asyncio
    async def test_invalid_object_type(self):
        ctx = MagicMock()
        result = await self.tools["hubspot_list_associations"]("invalid", "101", "deals", ctx)
        assert "Invalid object_type" in result

    @pytest.mark.asyncio
    async def test_invalid_to_type(self):
        ctx = MagicMock()
        result = await self.tools["hubspot_list_associations"]("contacts", "101", "invalid", ctx)
        assert "Invalid to_type" in result



class TestHubSpotCreateNote:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.hubspot import HubSpotConnector
        self.tools = _register_tools(HubSpotConnector)

    @pytest.mark.asyncio
    async def test_create_success(self):
        resp = _mock_response(200, {"id": "note-new-001"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("hubspot", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["hubspot_create_note"]("Meeting went well", ctx)
        assert "Note created" in result
        assert "note-new-001" in result

    @pytest.mark.asyncio
    async def test_create_with_associations(self):
        resp = _mock_response(200, {"id": "note-new-002"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("hubspot", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["hubspot_create_note"](
                "Follow up needed", ctx,
                associations_json='{"contactIds": ["101"]}',
            )
        assert "Note created" in result

    @pytest.mark.asyncio
    async def test_create_empty_body(self):
        ctx = MagicMock()
        result = await self.tools["hubspot_create_note"]("", ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_create_invalid_associations(self):
        ctx = MagicMock()
        with _patch_require_service("hubspot", _mock_client(_mock_response())):
            result = await self.tools["hubspot_create_note"]("Note text", ctx, associations_json="bad json")
        assert "Invalid associations_json" in result



class TestHubSpotCreateTask:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.hubspot import HubSpotConnector
        self.tools = _register_tools(HubSpotConnector)

    @pytest.mark.asyncio
    async def test_create_success(self):
        resp = _mock_response(200, {"id": "task-new-001"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("hubspot", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["hubspot_create_task"]("Follow up call", ctx, body="Call the client", due_date="2024-12-31")
        assert "Task created" in result
        assert "task-new-001" in result

    @pytest.mark.asyncio
    async def test_create_minimal(self):
        resp = _mock_response(200, {"id": "task-new-002"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("hubspot", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["hubspot_create_task"]("Quick task", ctx)
        assert "Task created" in result

    @pytest.mark.asyncio
    async def test_create_empty_subject(self):
        ctx = MagicMock()
        result = await self.tools["hubspot_create_task"]("", ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_create_invalid_due_date(self):
        ctx = MagicMock()
        result = await self.tools["hubspot_create_task"]("Task", ctx, due_date="not-a-date")
        assert "Invalid due_date" in result

    @pytest.mark.asyncio
    async def test_create_invalid_associations(self):
        ctx = MagicMock()
        with _patch_require_service("hubspot", _mock_client(_mock_response())):
            result = await self.tools["hubspot_create_task"]("Task", ctx, associations_json="bad")
        assert "Invalid associations_json" in result



class TestHubSpotDeleteContact:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.hubspot import HubSpotConnector
        self.tools = _register_tools(HubSpotConnector)

    @pytest.mark.asyncio
    async def test_delete_success(self):
        resp = _mock_response(204)
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("hubspot", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["hubspot_delete_contact"]("101", ctx)
        assert "Contact 101 deleted" in result

    @pytest.mark.asyncio
    async def test_delete_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["hubspot_delete_contact"]("", ctx)
        assert "required" in result.lower()



class TestHubSpotDeleteDeal:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.hubspot import HubSpotConnector
        self.tools = _register_tools(HubSpotConnector)

    @pytest.mark.asyncio
    async def test_delete_success(self):
        resp = _mock_response(204)
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("hubspot", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["hubspot_delete_deal"]("d-001", ctx)
        assert "Deal d-001 deleted" in result

    @pytest.mark.asyncio
    async def test_delete_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["hubspot_delete_deal"]("", ctx)
        assert "required" in result.lower()



class TestNotionUpdateBlock:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.notion import NotionConnector
        self.tools = _register_tools(NotionConnector)

    @pytest.mark.asyncio
    async def test_update_block_success(self):
        get_resp = _mock_response(200, {"type": "paragraph", "paragraph": {"rich_text": []}})
        patch_resp = _mock_response(200, {"id": "block-001"})
        client = _mock_client([get_resp, patch_resp])
        ctx = MagicMock()
        with _patch_require_service("notion", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, side_effect=[(get_resp, None), (patch_resp, None)]):
            result = await self.tools["notion_update_block"]("block-001", "Updated text", ctx)
        assert "Block updated" in result
        assert "block-001" in result

    @pytest.mark.asyncio
    async def test_update_block_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["notion_update_block"]("", "content", ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_update_block_empty_content(self):
        ctx = MagicMock()
        result = await self.tools["notion_update_block"]("block-001", "", ctx)
        assert "required" in result.lower()



class TestNotionDeleteBlock:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.notion import NotionConnector
        self.tools = _register_tools(NotionConnector)

    @pytest.mark.asyncio
    async def test_delete_block_success(self):
        resp = _mock_response(200, {"id": "block-001"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("notion", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["notion_delete_block"]("block-001", ctx)
        assert "Block deleted" in result
        assert "block-001" in result

    @pytest.mark.asyncio
    async def test_delete_block_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["notion_delete_block"]("", ctx)
        assert "required" in result.lower()



class TestNotionAppendBlocks:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.notion import NotionConnector
        self.tools = _register_tools(NotionConnector)

    @pytest.mark.asyncio
    async def test_append_blocks_success(self):
        resp = _mock_response(200, {"results": [{"id": "new-block-001"}]})
        client = _mock_client(resp)
        ctx = MagicMock()
        blocks_json = '[{"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": "Hello"}}]}}]'
        with _patch_require_service("notion", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["notion_append_blocks"]("page-001", blocks_json, ctx)
        assert "Blocks appended" in result
        assert "page-001" in result

    @pytest.mark.asyncio
    async def test_append_blocks_invalid_json(self):
        ctx = MagicMock()
        with _patch_require_service("notion", _mock_client(_mock_response())):
            result = await self.tools["notion_append_blocks"]("page-001", "not json", ctx)
        assert "Invalid blocks_json" in result

    @pytest.mark.asyncio
    async def test_append_blocks_not_array(self):
        ctx = MagicMock()
        with _patch_require_service("notion", _mock_client(_mock_response())):
            result = await self.tools["notion_append_blocks"]("page-001", '{"key": "value"}', ctx)
        assert "must be a JSON array" in result

    @pytest.mark.asyncio
    async def test_append_blocks_empty_page_id(self):
        ctx = MagicMock()
        result = await self.tools["notion_append_blocks"]("", "[]", ctx)
        assert "required" in result.lower()



class TestNotionUpdateDatabase:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.notion import NotionConnector
        self.tools = _register_tools(NotionConnector)

    @pytest.mark.asyncio
    async def test_update_title_success(self):
        resp = _mock_response(200, {"id": "db-100"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("notion", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["notion_update_database"]("db-100", ctx, title="New DB Title")
        assert "Database updated" in result
        assert "db-100" in result

    @pytest.mark.asyncio
    async def test_update_properties_success(self):
        resp = _mock_response(200, {"id": "db-100"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("notion", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["notion_update_database"]("db-100", ctx, properties_json='{"Status": {"select": {}}}')
        assert "Database updated" in result

    @pytest.mark.asyncio
    async def test_update_no_args(self):
        ctx = MagicMock()
        result = await self.tools["notion_update_database"]("db-100", ctx)
        assert "At least one" in result

    @pytest.mark.asyncio
    async def test_update_invalid_json(self):
        ctx = MagicMock()
        with _patch_require_service("notion", _mock_client(_mock_response())):
            result = await self.tools["notion_update_database"]("db-100", ctx, properties_json="bad json")
        assert "Invalid properties_json" in result



class TestNotionDeletePage:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.notion import NotionConnector
        self.tools = _register_tools(NotionConnector)

    @pytest.mark.asyncio
    async def test_delete_page_success(self):
        resp = _mock_response(200, {"id": "page-001", "archived": True})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("notion", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["notion_delete_page"]("page-001", ctx)
        assert "Page archived" in result
        assert "page-001" in result

    @pytest.mark.asyncio
    async def test_delete_page_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["notion_delete_page"]("", ctx)
        assert "required" in result.lower()



class TestNotionAddComment:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.notion import NotionConnector
        self.tools = _register_tools(NotionConnector)

    @pytest.mark.asyncio
    async def test_add_comment_success(self):
        resp = _mock_response(200, {"id": "comment-001"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("notion", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["notion_add_comment"]("page-001", "Great work!", ctx)
        assert "Comment added" in result
        assert "comment-001" in result

    @pytest.mark.asyncio
    async def test_add_comment_empty_body(self):
        ctx = MagicMock()
        result = await self.tools["notion_add_comment"]("page-001", "", ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_add_comment_empty_page_id(self):
        ctx = MagicMock()
        result = await self.tools["notion_add_comment"]("", "Hello", ctx)
        assert "required" in result.lower()



class TestNotionListComments:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.notion import NotionConnector
        self.tools = _register_tools(NotionConnector)

    @pytest.mark.asyncio
    async def test_list_comments_success(self):
        resp = _mock_response(200, {
            "results": [
                {
                    "id": "comment-001",
                    "created_time": "2024-06-01T10:00:00.000Z",
                    "rich_text": [{"plain_text": "Looks good!"}],
                },
                {
                    "id": "comment-002",
                    "created_time": "2024-06-02T14:00:00.000Z",
                    "rich_text": [{"plain_text": "Please revise."}],
                },
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("notion", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["notion_list_comments"]("page-001", ctx)
        assert "Looks good!" in result
        assert "Please revise." in result
        assert "comment-001" in result

    @pytest.mark.asyncio
    async def test_list_comments_empty(self):
        resp = _mock_response(200, {"results": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("notion", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["notion_list_comments"]("page-001", ctx)
        assert "No comments found" in result

    @pytest.mark.asyncio
    async def test_list_comments_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["notion_list_comments"]("", ctx)
        assert "required" in result.lower()



class TestNotionListUsers:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.notion import NotionConnector
        self.tools = _register_tools(NotionConnector)

    @pytest.mark.asyncio
    async def test_list_users_success(self):
        resp = _mock_response(200, {
            "results": [
                {
                    "id": "user-001",
                    "name": "Alice Johnson",
                    "type": "person",
                    "person": {"email": "alice@example.com"},
                },
                {
                    "id": "bot-001",
                    "name": "My Integration",
                    "type": "bot",
                },
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("notion", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["notion_list_users"](ctx)
        assert "Alice Johnson" in result
        assert "alice@example.com" in result
        assert "My Integration" in result
        assert "(bot)" in result

    @pytest.mark.asyncio
    async def test_list_users_empty(self):
        resp = _mock_response(200, {"results": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("notion", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["notion_list_users"](ctx)
        assert "No users found" in result



class TestFigmaPostComment:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.figma import FigmaConnector
        self.tools = _register_tools(FigmaConnector)

    @pytest.mark.asyncio
    async def test_post_comment_success(self):
        resp = _mock_response(200, {"id": "comment-001"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("figma", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["figma_post_comment"]("abc123", "Nice design!", ctx)
        assert "Comment posted" in result
        assert "comment-001" in result

    @pytest.mark.asyncio
    async def test_post_comment_with_coords(self):
        resp = _mock_response(200, {"id": "comment-002"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("figma", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["figma_post_comment"]("abc123", "Fix this", ctx, x=100.0, y=200.0)
        assert "Comment posted" in result

    @pytest.mark.asyncio
    async def test_post_comment_empty_message(self):
        ctx = MagicMock()
        result = await self.tools["figma_post_comment"]("abc123", "", ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_post_comment_empty_file_key(self):
        ctx = MagicMock()
        result = await self.tools["figma_post_comment"]("", "Hello", ctx)
        assert "required" in result.lower()



class TestFigmaResolveComment:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.figma import FigmaConnector
        self.tools = _register_tools(FigmaConnector)

    @pytest.mark.asyncio
    async def test_resolve_comment_success(self):
        resp = _mock_response(200, {"id": "comment-001"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("figma", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["figma_resolve_comment"]("abc123", "comment-001", ctx)
        assert "resolved" in result.lower()
        assert "comment-001" in result

    @pytest.mark.asyncio
    async def test_resolve_comment_empty_ids(self):
        ctx = MagicMock()
        result = await self.tools["figma_resolve_comment"]("", "comment-001", ctx)
        assert "required" in result.lower()
        result2 = await self.tools["figma_resolve_comment"]("abc123", "", ctx)
        assert "required" in result2.lower()



class TestFigmaDeleteComment:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.figma import FigmaConnector
        self.tools = _register_tools(FigmaConnector)

    @pytest.mark.asyncio
    async def test_delete_comment_success(self):
        resp = _mock_response(200, {})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("figma", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["figma_delete_comment"]("abc123", "comment-001", ctx)
        assert "deleted" in result.lower()
        assert "comment-001" in result

    @pytest.mark.asyncio
    async def test_delete_comment_empty_ids(self):
        ctx = MagicMock()
        result = await self.tools["figma_delete_comment"]("", "comment-001", ctx)
        assert "required" in result.lower()
        result2 = await self.tools["figma_delete_comment"]("abc123", "", ctx)
        assert "required" in result2.lower()



class TestFigmaGetImages:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.figma import FigmaConnector
        self.tools = _register_tools(FigmaConnector)

    @pytest.mark.asyncio
    async def test_get_images_success(self):
        resp = _mock_response(200, {
            "images": {
                "1:2": "https://figma-alpha-api.s3.us-west-2.amazonaws.com/img/abc.png",
                "3:4": "https://figma-alpha-api.s3.us-west-2.amazonaws.com/img/def.png",
            }
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("figma", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["figma_get_images"]("abc123", "1:2,3:4", ctx)
        assert "1:2" in result
        assert "3:4" in result
        assert "https://" in result

    @pytest.mark.asyncio
    async def test_get_images_invalid_format(self):
        ctx = MagicMock()
        with _patch_require_service("figma", _mock_client(_mock_response())):
            result = await self.tools["figma_get_images"]("abc123", "1:2", ctx, format="bmp")
        assert "Invalid format" in result

    @pytest.mark.asyncio
    async def test_get_images_empty(self):
        resp = _mock_response(200, {"images": {}})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("figma", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["figma_get_images"]("abc123", "1:2", ctx)
        assert "No images generated" in result

    @pytest.mark.asyncio
    async def test_get_images_empty_file_key(self):
        ctx = MagicMock()
        result = await self.tools["figma_get_images"]("", "1:2", ctx)
        assert "required" in result.lower()



class TestFigmaGetComponentSets:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.figma import FigmaConnector
        self.tools = _register_tools(FigmaConnector)

    @pytest.mark.asyncio
    async def test_component_sets_success(self):
        resp = _mock_response(200, {
            "meta": {
                "component_sets": [
                    {"name": "Button Variants", "key": "cs-001", "description": "All button variants"},
                    {"name": "Icon Set", "key": "cs-002", "description": ""},
                ]
            }
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("figma", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["figma_get_component_sets"]("abc123", ctx)
        assert "Button Variants" in result
        assert "cs-001" in result
        assert "All button variants" in result

    @pytest.mark.asyncio
    async def test_component_sets_empty(self):
        resp = _mock_response(200, {"meta": {"component_sets": []}})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("figma", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["figma_get_component_sets"]("abc123", ctx)
        assert "No component sets found" in result

    @pytest.mark.asyncio
    async def test_component_sets_empty_key(self):
        ctx = MagicMock()
        result = await self.tools["figma_get_component_sets"]("", ctx)
        assert "required" in result.lower()



class TestFigmaGetFileNodes:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.figma import FigmaConnector
        self.tools = _register_tools(FigmaConnector)

    @pytest.mark.asyncio
    async def test_get_file_nodes_success(self):
        resp = _mock_response(200, {
            "nodes": {
                "1:2": {
                    "document": {
                        "name": "Header Frame",
                        "type": "FRAME",
                        "children": [{"id": "a"}, {"id": "b"}],
                    },
                },
            }
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("figma", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["figma_get_file_nodes"]("abc123", "1:2", ctx)
        assert "Header Frame" in result
        assert "FRAME" in result
        assert "Children: 2" in result

    @pytest.mark.asyncio
    async def test_get_file_nodes_empty(self):
        resp = _mock_response(200, {"nodes": {}})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("figma", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["figma_get_file_nodes"]("abc123", "1:2", ctx)
        assert "No nodes found" in result

    @pytest.mark.asyncio
    async def test_get_file_nodes_empty_ids(self):
        ctx = MagicMock()
        result = await self.tools["figma_get_file_nodes"]("abc123", "", ctx)
        assert "required" in result.lower()
        result2 = await self.tools["figma_get_file_nodes"]("", "1:2", ctx)
        assert "required" in result2.lower()



class TestFigmaListTeamProjects:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.figma import FigmaConnector
        self.tools = _register_tools(FigmaConnector)

    @pytest.mark.asyncio
    async def test_list_team_projects_success(self):
        resp = _mock_response(200, {
            "projects": [
                {"name": "Brand Assets", "id": "proj-010"},
                {"name": "App Design", "id": "proj-020"},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("figma", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["figma_list_team_projects"]("team-001", ctx)
        assert "Brand Assets" in result
        assert "App Design" in result
        assert "proj-010" in result

    @pytest.mark.asyncio
    async def test_list_team_projects_empty(self):
        resp = _mock_response(200, {"projects": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("figma", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["figma_list_team_projects"]("team-001", ctx)
        assert "No projects found" in result

    @pytest.mark.asyncio
    async def test_list_team_projects_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["figma_list_team_projects"]("", ctx)
        assert "required" in result.lower()



class TestZendeskUpdateTicket:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.zendesk import ZendeskConnector
        self.tools = _register_tools(ZendeskConnector)

    @pytest.mark.asyncio
    async def test_update_success(self):
        resp = _mock_response(200, {"ticket": {"id": 42}})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("zendesk", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["zendesk_update_ticket"]("42", ctx, status="pending", priority="high")
        assert "updated" in result
        assert "42" in result

    @pytest.mark.asyncio
    async def test_update_invalid_status(self):
        ctx = MagicMock()
        result = await self.tools["zendesk_update_ticket"]("42", ctx, status="invalid")
        assert "Invalid status" in result

    @pytest.mark.asyncio
    async def test_update_invalid_priority(self):
        ctx = MagicMock()
        result = await self.tools["zendesk_update_ticket"]("42", ctx, priority="critical")
        assert "Invalid priority" in result

    @pytest.mark.asyncio
    async def test_update_no_fields(self):
        ctx = MagicMock()
        result = await self.tools["zendesk_update_ticket"]("42", ctx)
        assert "No fields to update" in result

    @pytest.mark.asyncio
    async def test_update_empty_ticket_id(self):
        ctx = MagicMock()
        result = await self.tools["zendesk_update_ticket"]("", ctx, status="open")
        assert "required" in result.lower()



class TestZendeskCloseTicket:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.zendesk import ZendeskConnector
        self.tools = _register_tools(ZendeskConnector)

    @pytest.mark.asyncio
    async def test_close_success(self):
        resp = _mock_response(200, {"ticket": {"id": 42}})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("zendesk", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["zendesk_close_ticket"]("42", ctx)
        assert "closed" in result
        assert "42" in result

    @pytest.mark.asyncio
    async def test_close_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["zendesk_close_ticket"]("", ctx)
        assert "required" in result.lower()



class TestZendeskAddTags:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.zendesk import ZendeskConnector
        self.tools = _register_tools(ZendeskConnector)

    @pytest.mark.asyncio
    async def test_add_tags_success(self):
        resp = _mock_response(200, {"tags": ["urgent", "vip"]})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("zendesk", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["zendesk_add_tags"]("42", "urgent, vip", ctx)
        assert "Tags added" in result
        assert "42" in result
        assert "urgent" in result

    @pytest.mark.asyncio
    async def test_add_tags_empty_tags(self):
        ctx = MagicMock()
        result = await self.tools["zendesk_add_tags"]("42", "", ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_add_tags_empty_ticket_id(self):
        ctx = MagicMock()
        result = await self.tools["zendesk_add_tags"]("", "urgent", ctx)
        assert "required" in result.lower()



class TestZendeskRemoveTags:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.zendesk import ZendeskConnector
        self.tools = _register_tools(ZendeskConnector)

    @pytest.mark.asyncio
    async def test_remove_tags_success(self):
        resp = _mock_response(200, {"tags": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("zendesk", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["zendesk_remove_tags"]("42", "old_tag", ctx)
        assert "Tags removed" in result
        assert "42" in result

    @pytest.mark.asyncio
    async def test_remove_tags_empty_tags(self):
        ctx = MagicMock()
        result = await self.tools["zendesk_remove_tags"]("42", "", ctx)
        assert "required" in result.lower()



class TestZendeskListViews:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.zendesk import ZendeskConnector
        self.tools = _register_tools(ZendeskConnector)

    @pytest.mark.asyncio
    async def test_list_views_success(self):
        resp = _mock_response(200, {
            "views": [
                {"title": "Unsolved tickets", "id": 101, "active": True},
                {"title": "My open tickets", "id": 102, "active": True},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("zendesk", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["zendesk_list_views"](ctx)
        assert "Unsolved tickets" in result
        assert "My open tickets" in result
        assert "101" in result

    @pytest.mark.asyncio
    async def test_list_views_empty(self):
        resp = _mock_response(200, {"views": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("zendesk", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["zendesk_list_views"](ctx)
        assert "No views found" in result



class TestZendeskGetViewTickets:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.zendesk import ZendeskConnector
        self.tools = _register_tools(ZendeskConnector)

    @pytest.mark.asyncio
    async def test_get_view_tickets_success(self):
        resp = _mock_response(200, {
            "tickets": [
                {"id": 10, "subject": "Bug report", "status": "open", "priority": "high"},
                {"id": 11, "subject": "Feature request", "status": "pending", "priority": "normal"},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("zendesk", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["zendesk_get_view_tickets"]("101", ctx)
        assert "#10" in result
        assert "Bug report" in result
        assert "#11" in result

    @pytest.mark.asyncio
    async def test_get_view_tickets_empty(self):
        resp = _mock_response(200, {"tickets": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("zendesk", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["zendesk_get_view_tickets"]("101", ctx)
        assert "No tickets in this view" in result

    @pytest.mark.asyncio
    async def test_get_view_tickets_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["zendesk_get_view_tickets"]("", ctx)
        assert "required" in result.lower()



class TestZendeskListGroups:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.zendesk import ZendeskConnector
        self.tools = _register_tools(ZendeskConnector)

    @pytest.mark.asyncio
    async def test_list_groups_success(self):
        resp = _mock_response(200, {
            "groups": [
                {"name": "Support", "id": 201, "default": True},
                {"name": "Sales", "id": 202, "default": False},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("zendesk", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["zendesk_list_groups"](ctx)
        assert "Support" in result
        assert "Sales" in result
        assert "201" in result

    @pytest.mark.asyncio
    async def test_list_groups_empty(self):
        resp = _mock_response(200, {"groups": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("zendesk", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["zendesk_list_groups"](ctx)
        assert "No groups found" in result



class TestZendeskAssignTicket:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.zendesk import ZendeskConnector
        self.tools = _register_tools(ZendeskConnector)

    @pytest.mark.asyncio
    async def test_assign_to_agent(self):
        resp = _mock_response(200, {"ticket": {"id": 42}})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("zendesk", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["zendesk_assign_ticket"]("42", ctx, assignee_id="3001")
        assert "assigned" in result
        assert "42" in result
        assert "3001" in result

    @pytest.mark.asyncio
    async def test_assign_to_group(self):
        resp = _mock_response(200, {"ticket": {"id": 42}})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("zendesk", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["zendesk_assign_ticket"]("42", ctx, group_id="201")
        assert "assigned" in result
        assert "group 201" in result

    @pytest.mark.asyncio
    async def test_assign_no_target(self):
        ctx = MagicMock()
        result = await self.tools["zendesk_assign_ticket"]("42", ctx)
        assert "Provide at least one" in result

    @pytest.mark.asyncio
    async def test_assign_empty_ticket_id(self):
        ctx = MagicMock()
        result = await self.tools["zendesk_assign_ticket"]("", ctx, assignee_id="3001")
        assert "required" in result.lower()



class TestSmartsheetUpdateRow:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.smartsheet import SmartsheetConnector
        self.tools = _register_tools(SmartsheetConnector)

    @pytest.mark.asyncio
    async def test_update_success(self):
        resp = _mock_response(200, {"result": [{"id": "row-001"}]})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("smartsheet", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["smartsheet_update_row"](
                "sheet-001", "row-001",
                '[{"columnId": 1, "value": "Updated"}]', ctx,
            )
        assert "row-001" in result
        assert "updated" in result.lower()

    @pytest.mark.asyncio
    async def test_update_invalid_json(self):
        ctx = MagicMock()
        with _patch_require_service("smartsheet", _mock_client(_mock_response())):
            result = await self.tools["smartsheet_update_row"]("sheet-001", "row-001", "not json", ctx)
        assert "Invalid cells_json" in result

    @pytest.mark.asyncio
    async def test_update_empty_ids(self):
        ctx = MagicMock()
        result = await self.tools["smartsheet_update_row"]("", "row-001", "[]", ctx)
        assert "required" in result.lower()
        result2 = await self.tools["smartsheet_update_row"]("sheet-001", "", "[]", ctx)
        assert "required" in result2.lower()



class TestSmartsheetDeleteRow:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.smartsheet import SmartsheetConnector
        self.tools = _register_tools(SmartsheetConnector)

    @pytest.mark.asyncio
    async def test_delete_success(self):
        resp = _mock_response(200, {})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("smartsheet", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["smartsheet_delete_row"]("sheet-001", "row-001", ctx)
        assert "row-001" in result
        assert "deleted" in result.lower()

    @pytest.mark.asyncio
    async def test_delete_empty_ids(self):
        ctx = MagicMock()
        result = await self.tools["smartsheet_delete_row"]("", "row-001", ctx)
        assert "required" in result.lower()
        result2 = await self.tools["smartsheet_delete_row"]("sheet-001", "", ctx)
        assert "required" in result2.lower()



class TestSmartsheetCreateSheet:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.smartsheet import SmartsheetConnector
        self.tools = _register_tools(SmartsheetConnector)

    @pytest.mark.asyncio
    async def test_create_success(self):
        resp = _mock_response(200, {"result": {"id": "sheet-new-001", "name": "New Sheet"}})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("smartsheet", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["smartsheet_create_sheet"](
                "New Sheet",
                '[{"title": "Name", "type": "TEXT_NUMBER", "primary": true}]',
                ctx,
            )
        assert "Sheet created" in result
        assert "sheet-new-001" in result

    @pytest.mark.asyncio
    async def test_create_invalid_json(self):
        ctx = MagicMock()
        with _patch_require_service("smartsheet", _mock_client(_mock_response())):
            result = await self.tools["smartsheet_create_sheet"]("Test", "bad json", ctx)
        assert "Invalid columns_json" in result

    @pytest.mark.asyncio
    async def test_create_empty_name(self):
        ctx = MagicMock()
        result = await self.tools["smartsheet_create_sheet"]("", "[]", ctx)
        assert "required" in result.lower()



class TestSmartsheetAddColumn:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.smartsheet import SmartsheetConnector
        self.tools = _register_tools(SmartsheetConnector)

    @pytest.mark.asyncio
    async def test_add_column_success(self):
        resp = _mock_response(200, {"result": {"id": "col-new-001", "title": "Priority"}})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("smartsheet", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["smartsheet_add_column"]("sheet-001", "Priority", "PICKLIST", ctx, options="High,Medium,Low")
        assert "Column added" in result
        assert "col-new-001" in result

    @pytest.mark.asyncio
    async def test_add_column_empty_title(self):
        ctx = MagicMock()
        result = await self.tools["smartsheet_add_column"]("sheet-001", "", "TEXT_NUMBER", ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_add_column_empty_sheet_id(self):
        ctx = MagicMock()
        result = await self.tools["smartsheet_add_column"]("", "Name", "TEXT_NUMBER", ctx)
        assert "required" in result.lower()



class TestSmartsheetUpdateColumn:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.smartsheet import SmartsheetConnector
        self.tools = _register_tools(SmartsheetConnector)

    @pytest.mark.asyncio
    async def test_update_column_success(self):
        resp = _mock_response(200, {"result": {"id": "col-001", "title": "Renamed Column"}})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("smartsheet", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["smartsheet_update_column"]("sheet-001", "col-001", ctx, title="Renamed Column")
        assert "col-001" in result
        assert "updated" in result.lower()

    @pytest.mark.asyncio
    async def test_update_column_no_fields(self):
        ctx = MagicMock()
        result = await self.tools["smartsheet_update_column"]("sheet-001", "col-001", ctx)
        assert "No fields to update" in result

    @pytest.mark.asyncio
    async def test_update_column_empty_ids(self):
        ctx = MagicMock()
        result = await self.tools["smartsheet_update_column"]("", "col-001", ctx, title="X")
        assert "required" in result.lower()
        result2 = await self.tools["smartsheet_update_column"]("sheet-001", "", ctx, title="X")
        assert "required" in result2.lower()



class TestSmartsheetAddComment:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.smartsheet import SmartsheetConnector
        self.tools = _register_tools(SmartsheetConnector)

    @pytest.mark.asyncio
    async def test_add_comment_success(self):
        resp = _mock_response(200, {"result": {"id": "disc-001"}})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("smartsheet", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["smartsheet_add_comment"]("sheet-001", "row-001", "This looks good", ctx)
        assert "Comment added" in result
        assert "row-001" in result

    @pytest.mark.asyncio
    async def test_add_comment_empty_text(self):
        ctx = MagicMock()
        result = await self.tools["smartsheet_add_comment"]("sheet-001", "row-001", "", ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_add_comment_empty_ids(self):
        ctx = MagicMock()
        result = await self.tools["smartsheet_add_comment"]("", "row-001", "text", ctx)
        assert "required" in result.lower()
        result2 = await self.tools["smartsheet_add_comment"]("sheet-001", "", "text", ctx)
        assert "required" in result2.lower()



class TestSmartsheetListAttachments:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.smartsheet import SmartsheetConnector
        self.tools = _register_tools(SmartsheetConnector)

    @pytest.mark.asyncio
    async def test_list_attachments_success(self):
        resp = _mock_response(200, {
            "data": [
                {"name": "report.pdf", "id": "att-001", "mimeType": "application/pdf", "sizeInKb": 512},
                {"name": "photo.png", "id": "att-002", "mimeType": "image/png", "sizeInKb": 128},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("smartsheet", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["smartsheet_list_attachments"]("sheet-001", "row-001", ctx)
        assert "report.pdf" in result
        assert "photo.png" in result
        assert "att-001" in result

    @pytest.mark.asyncio
    async def test_list_attachments_empty(self):
        resp = _mock_response(200, {"data": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("smartsheet", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["smartsheet_list_attachments"]("sheet-001", "row-001", ctx)
        assert "No attachments found" in result

    @pytest.mark.asyncio
    async def test_list_attachments_empty_ids(self):
        ctx = MagicMock()
        result = await self.tools["smartsheet_list_attachments"]("", "row-001", ctx)
        assert "required" in result.lower()
        result2 = await self.tools["smartsheet_list_attachments"]("sheet-001", "", ctx)
        assert "required" in result2.lower()



class TestSmartsheetShareSheet:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.smartsheet import SmartsheetConnector
        self.tools = _register_tools(SmartsheetConnector)

    @pytest.mark.asyncio
    async def test_share_success(self):
        resp = _mock_response(200, {"result": [{"email": "bob@example.com"}]})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("smartsheet", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["smartsheet_share_sheet"]("sheet-001", "bob@example.com", "EDITOR", ctx)
        assert "shared" in result.lower()
        assert "bob@example.com" in result
        assert "EDITOR" in result

    @pytest.mark.asyncio
    async def test_share_invalid_email(self):
        ctx = MagicMock()
        result = await self.tools["smartsheet_share_sheet"]("sheet-001", "not-an-email", "VIEWER", ctx)
        assert "email" in result.lower()

    @pytest.mark.asyncio
    async def test_share_invalid_access_level(self):
        ctx = MagicMock()
        result = await self.tools["smartsheet_share_sheet"]("sheet-001", "bob@example.com", "OWNER", ctx)
        assert "Invalid access_level" in result

    @pytest.mark.asyncio
    async def test_share_empty_sheet_id(self):
        ctx = MagicMock()
        result = await self.tools["smartsheet_share_sheet"]("", "bob@example.com", "VIEWER", ctx)
        assert "required" in result.lower()

