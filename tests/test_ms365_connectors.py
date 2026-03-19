"""Tests for MS365 connector tools (SharePoint, Outlook, Teams).

Covers SharePoint, Outlook (email, contacts, calendar), and Teams tools.
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


def _sp_tools():
    """Helper to register SharePoint tools with mocked settings."""
    from asibot.connectors.sharepoint import SharePointConnector
    with patch("asibot.connectors.sharepoint.settings") as mock_settings:
        mock_settings.ms365_tenant_id = "tid"
        mock_settings.ms365_client_id = "cid"
        mock_settings.sharepoint_site_url = "company.sharepoint.com"
        return _register_tools(SharePointConnector)


def _outlook_tools():
    """Helper to register Outlook tools with mocked settings."""
    from asibot.connectors.outlook import OutlookConnector
    with patch("asibot.connectors.outlook.settings") as mock_settings:
        mock_settings.ms365_tenant_id = "tid"
        mock_settings.ms365_client_id = "cid"
        return _register_tools(OutlookConnector)


def _teams_tools():
    """Helper to register Teams tools with mocked settings."""
    from asibot.connectors.teams import TeamsConnector
    with patch("asibot.connectors.teams.settings") as mock_settings:
        mock_settings.ms365_tenant_id = "tid"
        mock_settings.ms365_client_id = "cid"
        return _register_tools(TeamsConnector)


# --- SharePoint: sharepoint_get_file_info ---


class TestSharePointGetFileInfo:
    @pytest.mark.asyncio
    async def test_get_file_info_success(self):
        tools = _sp_tools()
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
        tools = _sp_tools()
        ctx = MagicMock()
        with _patch_graph_client_error("Microsoft 365 not authenticated"):
            result = await tools["sharepoint_get_file_info"]("test.txt", ctx, site="site-id")
        assert "not authenticated" in result


# --- SharePoint: sharepoint_list_versions ---


class TestSharePointListVersions:
    @pytest.mark.asyncio
    async def test_list_versions_success(self):
        tools = _sp_tools()
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
        tools = _sp_tools()
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
        tools = _outlook_tools()
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
        tools = _outlook_tools()
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
        tools = _outlook_tools()
        ctx = MagicMock()
        result = await tools["outlook_get_attachments"]("", ctx)
        assert "required" in result.lower()


# --- Outlook: calendar_create_event ---


class TestCalendarCreateEvent:
    @pytest.mark.asyncio
    async def test_create_event_success(self):
        tools = _outlook_tools()
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
        tools = _outlook_tools()
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
        tools = _outlook_tools()
        ctx = MagicMock()
        result = await tools["calendar_create_event"](
            "", "2024-03-15T10:00:00", "2024-03-15T11:00:00", ctx,
        )
        assert "required" in result.lower()


# --- Teams: teams_list_members ---


class TestTeamsListMembers:
    @pytest.mark.asyncio
    async def test_list_members_success(self):
        tools = _teams_tools()
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
        tools = _teams_tools()
        ctx = MagicMock()
        result = await tools["teams_list_members"]("", ctx)
        assert "required" in result.lower()


# --- Teams: teams_send_message ---


class TestTeamsSendMessage:
    @pytest.mark.asyncio
    async def test_send_message_success(self):
        tools = _teams_tools()
        resp = _mock_response(200, {"id": "msg-123"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_graph_client(client):
            result = await tools["teams_send_message"]("team-abc", "channel-xyz", "Hello team!", ctx)
        assert "Message sent" in result

    @pytest.mark.asyncio
    async def test_send_message_empty_message(self):
        tools = _teams_tools()
        ctx = MagicMock()
        result = await tools["teams_send_message"]("team-abc", "channel-xyz", "", ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_send_message_write_blocked(self):
        tools = _teams_tools()
        ctx = MagicMock()
        with _patch_graph_client_error("teams is in read-only mode"):
            result = await tools["teams_send_message"]("team-abc", "channel-xyz", "Hello!", ctx)
        assert "read-only" in result


# --- Outlook: outlook_move_email ---


class TestOutlookMoveEmail:
    @pytest.mark.asyncio
    async def test_move_email_success(self):
        tools = _outlook_tools()
        resp = _mock_response(200, {"id": "msg-moved"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_graph_client(client):
            result = await tools["outlook_move_email"]("msg-abc123", "Archive", ctx)
        assert "moved" in result.lower()
        assert "Archive" in result

    @pytest.mark.asyncio
    async def test_move_email_invalid_id(self):
        tools = _outlook_tools()
        ctx = MagicMock()
        result = await tools["outlook_move_email"]("", "Archive", ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_move_email_empty_folder(self):
        tools = _outlook_tools()
        ctx = MagicMock()
        result = await tools["outlook_move_email"]("msg-abc123", "", ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_move_email_auth_error(self):
        tools = _outlook_tools()
        ctx = MagicMock()
        with _patch_graph_client_error("outlook is in read-only mode"):
            result = await tools["outlook_move_email"]("msg-abc123", "Archive", ctx)
        assert "read-only" in result


# --- Outlook: outlook_delete_email ---


class TestOutlookDeleteEmail:
    @pytest.mark.asyncio
    async def test_delete_email_success(self):
        tools = _outlook_tools()
        resp = _mock_response(204)
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_graph_client(client):
            result = await tools["outlook_delete_email"]("msg-abc123", ctx)
        assert "deleted" in result.lower()

    @pytest.mark.asyncio
    async def test_delete_email_invalid_id(self):
        tools = _outlook_tools()
        ctx = MagicMock()
        result = await tools["outlook_delete_email"]("", ctx)
        assert "required" in result.lower()


# --- Outlook: outlook_mark_read ---


class TestOutlookMarkRead:
    @pytest.mark.asyncio
    async def test_mark_read_success(self):
        tools = _outlook_tools()
        resp = _mock_response(200, {"id": "msg-abc123", "isRead": True})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_graph_client(client):
            result = await tools["outlook_mark_read"]("msg-abc123", ctx)
        assert "read" in result.lower()

    @pytest.mark.asyncio
    async def test_mark_unread_success(self):
        tools = _outlook_tools()
        resp = _mock_response(200, {"id": "msg-abc123", "isRead": False})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_graph_client(client):
            result = await tools["outlook_mark_read"]("msg-abc123", ctx, is_read=False)
        assert "unread" in result.lower()

    @pytest.mark.asyncio
    async def test_mark_read_invalid_id(self):
        tools = _outlook_tools()
        ctx = MagicMock()
        result = await tools["outlook_mark_read"]("", ctx)
        assert "required" in result.lower()


# --- Outlook: outlook_reply_email ---


class TestOutlookReplyEmail:
    @pytest.mark.asyncio
    async def test_reply_success(self):
        tools = _outlook_tools()
        resp = _mock_response(202)
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_graph_client(client):
            result = await tools["outlook_reply_email"]("msg-abc123", "Thanks!", ctx)
        assert "Reply sent" in result

    @pytest.mark.asyncio
    async def test_reply_all_success(self):
        tools = _outlook_tools()
        resp = _mock_response(202)
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_graph_client(client):
            result = await tools["outlook_reply_email"]("msg-abc123", "Thanks everyone!", ctx, reply_all=True)
        assert "Reply-all sent" in result

    @pytest.mark.asyncio
    async def test_reply_empty_body(self):
        tools = _outlook_tools()
        ctx = MagicMock()
        result = await tools["outlook_reply_email"]("msg-abc123", "", ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_reply_invalid_id(self):
        tools = _outlook_tools()
        ctx = MagicMock()
        result = await tools["outlook_reply_email"]("", "Hello", ctx)
        assert "required" in result.lower()


# --- Outlook: outlook_forward_email ---


class TestOutlookForwardEmail:
    @pytest.mark.asyncio
    async def test_forward_success(self):
        tools = _outlook_tools()
        resp = _mock_response(202)
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_graph_client(client):
            result = await tools["outlook_forward_email"]("msg-abc123", "alice@example.com", ctx, body="FYI")
        assert "forwarded" in result.lower()
        assert "alice@example.com" in result

    @pytest.mark.asyncio
    async def test_forward_multiple_recipients(self):
        tools = _outlook_tools()
        resp = _mock_response(202)
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_graph_client(client):
            result = await tools["outlook_forward_email"]("msg-abc123", "alice@example.com,bob@example.com", ctx)
        assert "alice@example.com" in result
        assert "bob@example.com" in result

    @pytest.mark.asyncio
    async def test_forward_invalid_recipient(self):
        tools = _outlook_tools()
        client = _mock_client(_mock_response(200))
        ctx = MagicMock()
        with _patch_graph_client(client):
            result = await tools["outlook_forward_email"]("msg-abc123", "not-an-email", ctx)
        assert "Invalid email" in result

    @pytest.mark.asyncio
    async def test_forward_empty_recipients(self):
        tools = _outlook_tools()
        ctx = MagicMock()
        result = await tools["outlook_forward_email"]("msg-abc123", "", ctx)
        assert "required" in result.lower()


# --- Outlook: outlook_list_contacts ---


class TestOutlookListContacts:
    @pytest.mark.asyncio
    async def test_list_contacts_success(self):
        tools = _outlook_tools()
        resp = _mock_response(200, {
            "value": [
                {
                    "id": "contact-1",
                    "displayName": "Alice Smith",
                    "givenName": "Alice",
                    "surname": "Smith",
                    "emailAddresses": [{"address": "alice@example.com"}],
                    "mobilePhone": "+1234567890",
                    "businessPhones": [],
                },
                {
                    "id": "contact-2",
                    "displayName": "Bob Jones",
                    "givenName": "Bob",
                    "surname": "Jones",
                    "emailAddresses": [{"address": "bob@example.com"}],
                    "mobilePhone": None,
                    "businessPhones": ["+0987654321"],
                },
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_graph_client(client):
            result = await tools["outlook_list_contacts"](ctx)
        assert "Alice Smith" in result
        assert "alice@example.com" in result
        assert "Bob Jones" in result

    @pytest.mark.asyncio
    async def test_list_contacts_empty(self):
        tools = _outlook_tools()
        resp = _mock_response(200, {"value": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_graph_client(client):
            result = await tools["outlook_list_contacts"](ctx)
        assert "No contacts found" in result


# --- Outlook: outlook_get_contact ---


class TestOutlookGetContact:
    @pytest.mark.asyncio
    async def test_get_contact_success(self):
        tools = _outlook_tools()
        resp = _mock_response(200, {
            "id": "contact-1",
            "displayName": "Alice Smith",
            "givenName": "Alice",
            "surname": "Smith",
            "emailAddresses": [{"address": "alice@example.com"}],
            "mobilePhone": "+1234567890",
            "businessPhones": ["+0987654321"],
            "companyName": "Contoso",
            "jobTitle": "Engineer",
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_graph_client(client):
            result = await tools["outlook_get_contact"]("contact-1", ctx)
        assert "Alice Smith" in result
        assert "alice@example.com" in result
        assert "+1234567890" in result
        assert "Contoso" in result
        assert "Engineer" in result

    @pytest.mark.asyncio
    async def test_get_contact_invalid_id(self):
        tools = _outlook_tools()
        ctx = MagicMock()
        result = await tools["outlook_get_contact"]("", ctx)
        assert "required" in result.lower()


# --- Outlook: outlook_create_contact ---


class TestOutlookCreateContact:
    @pytest.mark.asyncio
    async def test_create_contact_success(self):
        tools = _outlook_tools()
        resp = _mock_response(201, {"id": "contact-new", "displayName": "Jane Doe"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_graph_client(client):
            result = await tools["outlook_create_contact"]("Jane", "Doe", ctx, email="jane@example.com", phone="+1111111111")
        assert "Contact created" in result
        assert "Jane Doe" in result

    @pytest.mark.asyncio
    async def test_create_contact_minimal(self):
        tools = _outlook_tools()
        resp = _mock_response(201, {"id": "contact-new", "displayName": "Jane Doe"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_graph_client(client):
            result = await tools["outlook_create_contact"]("Jane", "Doe", ctx)
        assert "Contact created" in result

    @pytest.mark.asyncio
    async def test_create_contact_empty_name(self):
        tools = _outlook_tools()
        ctx = MagicMock()
        result = await tools["outlook_create_contact"]("", "Doe", ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_create_contact_invalid_email(self):
        tools = _outlook_tools()
        client = _mock_client(_mock_response(200))
        ctx = MagicMock()
        with _patch_graph_client(client):
            result = await tools["outlook_create_contact"]("Jane", "Doe", ctx, email="not-an-email")
        assert "Invalid email" in result


# --- Outlook: calendar_update_event ---


class TestCalendarUpdateEvent:
    @pytest.mark.asyncio
    async def test_update_event_success(self):
        tools = _outlook_tools()
        resp = _mock_response(200, {"id": "event-123", "subject": "Updated Meeting"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_graph_client(client):
            result = await tools["calendar_update_event"]("event-123", ctx, subject="Updated Meeting", location="Room 42")
        assert "updated" in result.lower()
        assert "Updated Meeting" in result

    @pytest.mark.asyncio
    async def test_update_event_no_changes(self):
        tools = _outlook_tools()
        ctx = MagicMock()
        result = await tools["calendar_update_event"]("event-123", ctx)
        assert "No updates provided" in result

    @pytest.mark.asyncio
    async def test_update_event_invalid_id(self):
        tools = _outlook_tools()
        ctx = MagicMock()
        result = await tools["calendar_update_event"]("", ctx, subject="Test")
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_update_event_time_only(self):
        tools = _outlook_tools()
        resp = _mock_response(200, {"id": "event-123", "subject": "Meeting"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_graph_client(client):
            result = await tools["calendar_update_event"](
                "event-123", ctx,
                start="2024-03-15T14:00:00",
                end="2024-03-15T15:00:00",
            )
        assert "updated" in result.lower()


# --- Outlook: calendar_delete_event ---


class TestCalendarDeleteEvent:
    @pytest.mark.asyncio
    async def test_delete_event_success(self):
        tools = _outlook_tools()
        resp = _mock_response(204)
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_graph_client(client):
            result = await tools["calendar_delete_event"]("event-123", ctx)
        assert "deleted" in result.lower()

    @pytest.mark.asyncio
    async def test_delete_event_invalid_id(self):
        tools = _outlook_tools()
        ctx = MagicMock()
        result = await tools["calendar_delete_event"]("", ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_delete_event_auth_error(self):
        tools = _outlook_tools()
        ctx = MagicMock()
        with _patch_graph_client_error("calendar is in read-only mode"):
            result = await tools["calendar_delete_event"]("event-123", ctx)
        assert "read-only" in result


# --- Teams: teams_reply_message ---


class TestTeamsReplyMessage:
    @pytest.mark.asyncio
    async def test_reply_message_success(self):
        tools = _teams_tools()
        resp = _mock_response(201, {"id": "reply-123"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_graph_client(client):
            result = await tools["teams_reply_message"]("team-abc", "channel-xyz", "msg-123", "Great idea!", ctx)
        assert "Reply sent" in result

    @pytest.mark.asyncio
    async def test_reply_message_empty_body(self):
        tools = _teams_tools()
        ctx = MagicMock()
        result = await tools["teams_reply_message"]("team-abc", "channel-xyz", "msg-123", "", ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_reply_message_invalid_ids(self):
        tools = _teams_tools()
        ctx = MagicMock()
        result = await tools["teams_reply_message"]("", "channel-xyz", "msg-123", "Hello", ctx)
        assert "required" in result.lower()


# --- Teams: teams_create_channel ---


class TestTeamsCreateChannel:
    @pytest.mark.asyncio
    async def test_create_channel_success(self):
        tools = _teams_tools()
        resp = _mock_response(201, {"id": "ch-new", "displayName": "Design"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_graph_client(client):
            result = await tools["teams_create_channel"]("team-abc", "Design", ctx, description="Design discussions")
        assert "Channel created" in result
        assert "Design" in result

    @pytest.mark.asyncio
    async def test_create_channel_no_description(self):
        tools = _teams_tools()
        resp = _mock_response(201, {"id": "ch-new", "displayName": "General"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_graph_client(client):
            result = await tools["teams_create_channel"]("team-abc", "General", ctx)
        assert "Channel created" in result

    @pytest.mark.asyncio
    async def test_create_channel_empty_name(self):
        tools = _teams_tools()
        ctx = MagicMock()
        result = await tools["teams_create_channel"]("team-abc", "", ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_create_channel_invalid_team_id(self):
        tools = _teams_tools()
        ctx = MagicMock()
        result = await tools["teams_create_channel"]("", "Design", ctx)
        assert "required" in result.lower()


# --- Teams: teams_delete_channel ---


class TestTeamsDeleteChannel:
    @pytest.mark.asyncio
    async def test_delete_channel_success(self):
        tools = _teams_tools()
        resp = _mock_response(204)
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_graph_client(client):
            result = await tools["teams_delete_channel"]("team-abc", "channel-xyz", ctx)
        assert "deleted" in result.lower()

    @pytest.mark.asyncio
    async def test_delete_channel_invalid_ids(self):
        tools = _teams_tools()
        ctx = MagicMock()
        result = await tools["teams_delete_channel"]("team-abc", "", ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_delete_channel_write_blocked(self):
        tools = _teams_tools()
        ctx = MagicMock()
        with _patch_graph_client_error("teams is in read-only mode"):
            result = await tools["teams_delete_channel"]("team-abc", "channel-xyz", ctx)
        assert "read-only" in result


# --- Teams: teams_send_chat_message ---


class TestTeamsSendChatMessage:
    @pytest.mark.asyncio
    async def test_send_chat_message_success(self):
        tools = _teams_tools()
        resp = _mock_response(201, {"id": "chatmsg-123"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_graph_client(client):
            result = await tools["teams_send_chat_message"]("chat-abc", "Hey there!", ctx)
        assert "Chat message sent" in result

    @pytest.mark.asyncio
    async def test_send_chat_message_empty_body(self):
        tools = _teams_tools()
        ctx = MagicMock()
        result = await tools["teams_send_chat_message"]("chat-abc", "", ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_send_chat_message_invalid_chat_id(self):
        tools = _teams_tools()
        ctx = MagicMock()
        result = await tools["teams_send_chat_message"]("", "Hello", ctx)
        assert "required" in result.lower()


# --- Teams: teams_list_channel_files ---


class TestTeamsListChannelFiles:
    @pytest.mark.asyncio
    async def test_list_channel_files_success(self):
        tools = _teams_tools()
        folder_resp = _mock_response(200, {
            "id": "folder-123",
            "parentReference": {"driveId": "drive-abc"},
        })
        files_resp = _mock_response(200, {
            "value": [
                {
                    "name": "design.pptx",
                    "size": 1024000,
                    "lastModifiedDateTime": "2024-06-15T10:30:00Z",
                    "webUrl": "https://teams.sharepoint.com/design.pptx",
                    "file": {"mimeType": "application/vnd.ms-powerpoint"},
                },
                {
                    "name": "Assets",
                    "size": 0,
                    "lastModifiedDateTime": "2024-06-10T08:00:00Z",
                    "webUrl": "https://teams.sharepoint.com/Assets",
                    "folder": {"childCount": 5},
                },
            ]
        })
        client = _mock_client([folder_resp, files_resp])
        ctx = MagicMock()
        with _patch_graph_client(client):
            result = await tools["teams_list_channel_files"]("team-abc", "channel-xyz", ctx)
        assert "design.pptx" in result
        assert "File" in result
        assert "Assets" in result
        assert "Folder" in result

    @pytest.mark.asyncio
    async def test_list_channel_files_empty(self):
        tools = _teams_tools()
        folder_resp = _mock_response(200, {
            "id": "folder-123",
            "parentReference": {"driveId": "drive-abc"},
        })
        files_resp = _mock_response(200, {"value": []})
        client = _mock_client([folder_resp, files_resp])
        ctx = MagicMock()
        with _patch_graph_client(client):
            result = await tools["teams_list_channel_files"]("team-abc", "channel-xyz", ctx)
        assert "No files" in result

    @pytest.mark.asyncio
    async def test_list_channel_files_invalid_ids(self):
        tools = _teams_tools()
        ctx = MagicMock()
        result = await tools["teams_list_channel_files"]("", "channel-xyz", ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_list_channel_files_no_folder(self):
        tools = _teams_tools()
        folder_resp = _mock_response(200, {"id": "", "parentReference": {}})
        client = _mock_client(folder_resp)
        ctx = MagicMock()
        with _patch_graph_client(client):
            result = await tools["teams_list_channel_files"]("team-abc", "channel-xyz", ctx)
        assert "Could not locate" in result
