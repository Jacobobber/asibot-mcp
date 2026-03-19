"""Tests for MS365 connector tool expansions (SharePoint, Outlook, Teams).

Covers the 7 new tools: sharepoint_get_file_info, sharepoint_list_versions,
outlook_list_folders, outlook_get_attachments, calendar_create_event,
teams_list_members, teams_send_message.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


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
    """Create a mock httpx.AsyncClient that returns a sequence of responses."""
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


def _patch_graph_client(client, uid="test@example.com"):
    """Patch microsoft.require_graph_client to return given client."""
    return patch(
        "asibot.connectors.microsoft.require_graph_client",
        new_callable=AsyncMock,
        return_value=(client, uid, None),
    )


def _patch_graph_client_error(error_msg):
    """Patch microsoft.require_graph_client to return an error."""
    return patch(
        "asibot.connectors.microsoft.require_graph_client",
        new_callable=AsyncMock,
        return_value=(None, None, error_msg),
    )


def _register_tools(connector_class):
    """Register tools and return a dict of tool functions by name."""
    mcp = MagicMock()
    tools = {}
    mcp.tool = lambda: lambda f: tools.setdefault(f.__name__, f) or f
    connector_class().register_tools(mcp)
    return tools


# --- SharePoint: sharepoint_get_file_info ---


class TestSharePointGetFileInfo:
    @pytest.mark.asyncio
    async def test_get_file_info_success(self):
        from asibot.connectors.sharepoint import SharePointConnector
        with patch("asibot.connectors.sharepoint.settings") as mock_settings:
            mock_settings.ms365_tenant_id = "tid"
            mock_settings.ms365_client_id = "cid"
            mock_settings.sharepoint_site_url = "company.sharepoint.com"
            tools = _register_tools(SharePointConnector)

        resp = _mock_response(200, {
            "id": "file-123",
            "name": "report.docx",
            "size": 45678,
            "createdBy": {"user": {"displayName": "Alice"}},
            "lastModifiedBy": {"user": {"displayName": "Bob"}},
            "lastModifiedDateTime": "2024-06-15T10:30:00Z",
            "webUrl": "https://company.sharepoint.com/docs/report.docx",
            "file": {"mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_graph_client(client):
            result = await tools["sharepoint_get_file_info"]("docs/report.docx", ctx, site="site-id")
        assert "report.docx" in result
        assert "45,678 bytes" in result
        assert "Alice" in result
        assert "Bob" in result
        assert "2024-06-15" in result

    @pytest.mark.asyncio
    async def test_get_file_info_auth_error(self):
        from asibot.connectors.sharepoint import SharePointConnector
        with patch("asibot.connectors.sharepoint.settings") as mock_settings:
            mock_settings.ms365_tenant_id = "tid"
            mock_settings.ms365_client_id = "cid"
            mock_settings.sharepoint_site_url = ""
            tools = _register_tools(SharePointConnector)

        ctx = MagicMock()
        with _patch_graph_client_error("Microsoft 365 not authenticated"):
            result = await tools["sharepoint_get_file_info"]("test.txt", ctx, site="site-id")
        assert "not authenticated" in result


# --- SharePoint: sharepoint_list_versions ---


class TestSharePointListVersions:
    @pytest.mark.asyncio
    async def test_list_versions_success(self):
        from asibot.connectors.sharepoint import SharePointConnector
        with patch("asibot.connectors.sharepoint.settings") as mock_settings:
            mock_settings.ms365_tenant_id = "tid"
            mock_settings.ms365_client_id = "cid"
            mock_settings.sharepoint_site_url = "company.sharepoint.com"
            tools = _register_tools(SharePointConnector)

        resp = _mock_response(200, {
            "value": [
                {
                    "id": "1.0",
                    "lastModifiedDateTime": "2024-05-01T09:00:00Z",
                    "lastModifiedBy": {"user": {"displayName": "Alice"}},
                },
                {
                    "id": "2.0",
                    "lastModifiedDateTime": "2024-06-15T10:30:00Z",
                    "lastModifiedBy": {"user": {"displayName": "Bob"}},
                },
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_graph_client(client):
            result = await tools["sharepoint_list_versions"]("docs/report.docx", ctx, site="site-id")
        assert "1.0" in result
        assert "2.0" in result
        assert "Alice" in result
        assert "Bob" in result

    @pytest.mark.asyncio
    async def test_list_versions_empty(self):
        from asibot.connectors.sharepoint import SharePointConnector
        with patch("asibot.connectors.sharepoint.settings") as mock_settings:
            mock_settings.ms365_tenant_id = "tid"
            mock_settings.ms365_client_id = "cid"
            mock_settings.sharepoint_site_url = ""
            tools = _register_tools(SharePointConnector)

        resp = _mock_response(200, {"value": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_graph_client(client):
            result = await tools["sharepoint_list_versions"]("docs/old.txt", ctx, site="site-id")
        assert "No versions found" in result


# --- Outlook: outlook_list_folders ---


class TestOutlookListFolders:
    @pytest.mark.asyncio
    async def test_list_folders_success(self):
        from asibot.connectors.outlook import OutlookConnector
        with patch("asibot.connectors.outlook.settings") as mock_settings:
            mock_settings.ms365_tenant_id = "tid"
            mock_settings.ms365_client_id = "cid"
            tools = _register_tools(OutlookConnector)

        resp = _mock_response(200, {
            "value": [
                {"displayName": "Inbox", "id": "inbox-id", "totalItemCount": 150, "unreadItemCount": 5},
                {"displayName": "Sent Items", "id": "sent-id", "totalItemCount": 80, "unreadItemCount": 0},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_graph_client(client):
            result = await tools["outlook_list_folders"](ctx)
        assert "Inbox" in result
        assert "Sent Items" in result
        assert "150" in result
        assert "5" in result


# --- Outlook: outlook_get_attachments ---


class TestOutlookGetAttachments:
    @pytest.mark.asyncio
    async def test_get_attachments_success(self):
        from asibot.connectors.outlook import OutlookConnector
        with patch("asibot.connectors.outlook.settings") as mock_settings:
            mock_settings.ms365_tenant_id = "tid"
            mock_settings.ms365_client_id = "cid"
            tools = _register_tools(OutlookConnector)

        resp = _mock_response(200, {
            "value": [
                {"name": "report.pdf", "contentType": "application/pdf", "size": 102400},
                {"name": "photo.jpg", "contentType": "image/jpeg", "size": 2048000},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_graph_client(client):
            result = await tools["outlook_get_attachments"]("msg-abc123", ctx)
        assert "report.pdf" in result
        assert "application/pdf" in result
        assert "photo.jpg" in result

    @pytest.mark.asyncio
    async def test_get_attachments_invalid_id(self):
        from asibot.connectors.outlook import OutlookConnector
        with patch("asibot.connectors.outlook.settings") as mock_settings:
            mock_settings.ms365_tenant_id = "tid"
            mock_settings.ms365_client_id = "cid"
            tools = _register_tools(OutlookConnector)

        ctx = MagicMock()
        result = await tools["outlook_get_attachments"]("", ctx)
        assert "required" in result.lower()


# --- Outlook: calendar_create_event ---


class TestCalendarCreateEvent:
    @pytest.mark.asyncio
    async def test_create_event_success(self):
        from asibot.connectors.outlook import OutlookConnector
        with patch("asibot.connectors.outlook.settings") as mock_settings:
            mock_settings.ms365_tenant_id = "tid"
            mock_settings.ms365_client_id = "cid"
            tools = _register_tools(OutlookConnector)

        resp = _mock_response(200, {"id": "event-123", "subject": "Sprint Planning"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_graph_client(client):
            result = await tools["calendar_create_event"](
                "Sprint Planning",
                "2024-03-15T10:00:00",
                "2024-03-15T11:00:00",
                ctx,
                attendees="alice@example.com,bob@example.com",
            )
        assert "Sprint Planning" in result
        assert "event-123" in result

    @pytest.mark.asyncio
    async def test_create_event_invalid_attendee(self):
        from asibot.connectors.outlook import OutlookConnector
        with patch("asibot.connectors.outlook.settings") as mock_settings:
            mock_settings.ms365_tenant_id = "tid"
            mock_settings.ms365_client_id = "cid"
            tools = _register_tools(OutlookConnector)

        client = _mock_client(_mock_response(200))
        ctx = MagicMock()
        with _patch_graph_client(client):
            result = await tools["calendar_create_event"](
                "Meeting",
                "2024-03-15T10:00:00",
                "2024-03-15T11:00:00",
                ctx,
                attendees="not-an-email",
            )
        assert "Invalid email" in result

    @pytest.mark.asyncio
    async def test_create_event_empty_subject(self):
        from asibot.connectors.outlook import OutlookConnector
        with patch("asibot.connectors.outlook.settings") as mock_settings:
            mock_settings.ms365_tenant_id = "tid"
            mock_settings.ms365_client_id = "cid"
            tools = _register_tools(OutlookConnector)

        ctx = MagicMock()
        result = await tools["calendar_create_event"](
            "", "2024-03-15T10:00:00", "2024-03-15T11:00:00", ctx,
        )
        assert "required" in result.lower()


# --- Teams: teams_list_members ---


class TestTeamsListMembers:
    @pytest.mark.asyncio
    async def test_list_members_success(self):
        from asibot.connectors.teams import TeamsConnector
        with patch("asibot.connectors.teams.settings") as mock_settings:
            mock_settings.ms365_tenant_id = "tid"
            mock_settings.ms365_client_id = "cid"
            tools = _register_tools(TeamsConnector)

        resp = _mock_response(200, {
            "value": [
                {"displayName": "Alice", "roles": ["owner"], "email": "alice@example.com"},
                {"displayName": "Bob", "roles": [], "email": "bob@example.com"},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_graph_client(client):
            result = await tools["teams_list_members"]("team-abc", ctx)
        assert "Alice" in result
        assert "owner" in result
        assert "Bob" in result
        assert "member" in result

    @pytest.mark.asyncio
    async def test_list_members_invalid_id(self):
        from asibot.connectors.teams import TeamsConnector
        with patch("asibot.connectors.teams.settings") as mock_settings:
            mock_settings.ms365_tenant_id = "tid"
            mock_settings.ms365_client_id = "cid"
            tools = _register_tools(TeamsConnector)

        ctx = MagicMock()
        result = await tools["teams_list_members"]("", ctx)
        assert "required" in result.lower()


# --- Teams: teams_send_message ---


class TestTeamsSendMessage:
    @pytest.mark.asyncio
    async def test_send_message_success(self):
        from asibot.connectors.teams import TeamsConnector
        with patch("asibot.connectors.teams.settings") as mock_settings:
            mock_settings.ms365_tenant_id = "tid"
            mock_settings.ms365_client_id = "cid"
            tools = _register_tools(TeamsConnector)

        resp = _mock_response(200, {"id": "msg-123"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_graph_client(client):
            result = await tools["teams_send_message"]("team-abc", "channel-xyz", "Hello team!", ctx)
        assert "Message sent" in result

    @pytest.mark.asyncio
    async def test_send_message_empty_message(self):
        from asibot.connectors.teams import TeamsConnector
        with patch("asibot.connectors.teams.settings") as mock_settings:
            mock_settings.ms365_tenant_id = "tid"
            mock_settings.ms365_client_id = "cid"
            tools = _register_tools(TeamsConnector)

        ctx = MagicMock()
        result = await tools["teams_send_message"]("team-abc", "channel-xyz", "", ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_send_message_write_blocked(self):
        from asibot.connectors.teams import TeamsConnector
        with patch("asibot.connectors.teams.settings") as mock_settings:
            mock_settings.ms365_tenant_id = "tid"
            mock_settings.ms365_client_id = "cid"
            tools = _register_tools(TeamsConnector)

        ctx = MagicMock()
        with _patch_graph_client_error("teams is in read-only mode"):
            result = await tools["teams_send_message"]("team-abc", "channel-xyz", "Hello!", ctx)
        assert "read-only" in result
