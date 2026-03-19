"""RingCentral connector: call logs and messages via RingCentral REST API."""

import logging

from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store, validation
from asibot.connectors.base import Connector
from asibot.connectors.pagination import collect, paginate_odata

logger = logging.getLogger(__name__)
API = "https://platform.ringcentral.com/restapi/v1.0"


class RingCentralConnector(Connector):
    def __init__(self, config=None):
        super().__init__(name="ringcentral", config=config)

    async def connect(self):
        logger.info("RingCentral: ready (per-user OAuth token)")

    async def disconnect(self):
        pass

    async def fetch_documents(self):
        return []

    def register_tools(self, mcp: FastMCP):

        @mcp.tool()
        async def ringcentral_call_log(ctx: Context, limit: int = 25) -> str:
            """List recent call log entries from RingCentral.

            Args:
                limit: Max results (default: 25)
            """
            limit = validation.validate_limit(limit)
            client, uid, err = await token_store.require_service(ctx, "ringcentral", level="read")
            if err:
                return err
            pages = paginate_odata(
                client, f"{API}/account/~/call-log",
                service="RingCentral", action="call log",
                params={"perPage": min(limit, 100)},
                results_key="records",
                next_link_key="navigation.nextPage.uri",
            )
            records = await collect(pages, limit)
            if not records:
                return "No call log entries found."
            lines = []
            for c in records:
                direction = c.get("direction", "?")
                result = c.get("result", "?")
                start = c.get("startTime", "?")[:16] if c.get("startTime") else "?"
                name = c.get("from", {}).get("name") or c.get("to", {}).get("name") or "Unknown"
                lines.append(f"{start} | {direction} | {result} | {name}")
            return "\n".join(lines)

        @mcp.tool()
        async def ringcentral_messages(ctx: Context, limit: int = 25) -> str:
            """List recent messages from RingCentral.

            Args:
                limit: Max results (default: 25)
            """
            limit = validation.validate_limit(limit)
            client, uid, err = await token_store.require_service(ctx, "ringcentral", level="read")
            if err:
                return err
            pages = paginate_odata(
                client, f"{API}/account/~/extension/~/message-store",
                service="RingCentral", action="messages",
                params={"perPage": min(limit, 100)},
                results_key="records",
                next_link_key="navigation.nextPage.uri",
            )
            records = await collect(pages, limit)
            if not records:
                return "No messages found."
            lines = []
            for m in records:
                direction = m.get("direction", "?")
                msg_type = m.get("type", "?")
                created = m.get("creationTime", "?")[:16] if m.get("creationTime") else "?"
                subject = m.get("subject", "No subject")
                lines.append(f"{created} | {direction} | {msg_type} | {subject}")
            return "\n".join(lines)

        @mcp.tool()
        async def ringcentral_get_call_recording(recording_id: str, ctx: Context) -> str:
            """Get metadata for a call recording from RingCentral.

            Args:
                recording_id: The recording ID
            """
            err = validation.validate_id(recording_id, "recording_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "ringcentral", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(client, "GET", f"{API}/account/~/recording/{recording_id}", service="RingCentral", action="get recording")
            if err:
                return err
            rec = r.json()
            rid = rec.get("id", "?")
            content_uri = rec.get("contentUri", "?")
            duration = rec.get("duration", "?")
            rec_type = rec.get("type", "?")
            return f"Recording ID: {rid}\nType: {rec_type}\nDuration: {duration}s\nContent URI: {content_uri}"

        @mcp.tool()
        async def ringcentral_presence(ctx: Context) -> str:
            """Get current presence/availability status from RingCentral."""
            client, uid, err = await token_store.require_service(ctx, "ringcentral", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(client, "GET", f"{API}/account/~/extension/~/presence", service="RingCentral", action="get presence")
            if err:
                return err
            p = r.json()
            status = p.get("presenceStatus", "?")
            dnd = p.get("dndStatus", "?")
            message = p.get("userStatus", "?")
            telephony = p.get("telephonyStatus", "?")
            return f"Status: {status}\nDND: {dnd}\nUser Status: {message}\nTelephony: {telephony}"

        @mcp.tool()
        async def ringcentral_list_extensions(ctx: Context, limit: int = 25) -> str:
            """List extensions in the RingCentral account.

            Args:
                limit: Max results (default: 25)
            """
            limit = validation.validate_limit(limit)
            client, uid, err = await token_store.require_service(ctx, "ringcentral", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(client, "GET", f"{API}/account/~/extension", service="RingCentral", action="list extensions", params={"perPage": limit})
            if err:
                return err
            records = r.json().get("records", [])
            if not records:
                return "No extensions found."
            lines = []
            for ext in records:
                name = ext.get("name", "?")
                ext_num = ext.get("extensionNumber", "?")
                status = ext.get("status", "?")
                ext_type = ext.get("type", "?")
                lines.append(f"{name} | Ext: {ext_num} | Type: {ext_type} | Status: {status}")
            return "\n".join(lines)

        @mcp.tool()
        async def ringcentral_get_voicemail(ctx: Context, limit: int = 10) -> str:
            """Get recent voicemail messages from RingCentral.

            Args:
                limit: Max results (default: 10)
            """
            limit = validation.validate_limit(limit)
            client, uid, err = await token_store.require_service(ctx, "ringcentral", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(client, "GET", f"{API}/account/~/extension/~/message-store", service="RingCentral", action="get voicemail", params={"messageType": "VoiceMail", "perPage": limit})
            if err:
                return err
            records = r.json().get("records", [])
            if not records:
                return "No voicemail messages found."
            lines = []
            for m in records:
                created = m.get("creationTime", "?")[:16] if m.get("creationTime") else "?"
                caller = m.get("from", {}).get("name", m.get("from", {}).get("phoneNumber", "Unknown"))
                read_status = m.get("readStatus", "?")
                lines.append(f"{created} | From: {caller} | Read: {read_status}")
            return "\n".join(lines)

        @mcp.tool()
        async def ringcentral_send_sms(to: str, text: str, ctx: Context) -> str:
            """Send an SMS message via RingCentral.

            Args:
                to: Recipient phone number
                text: Message text
            """
            err = validation.validate_content(to, "to")
            if err:
                return err
            err = validation.validate_content(text, "text")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "ringcentral", level="write")
            if err:
                return err
            body = {
                "from": {"phoneNumber": "~"},
                "to": [{"phoneNumber": to}],
                "text": text,
            }
            r, err = await token_store.safe_request(
                client, "POST", f"{API}/account/~/extension/~/sms",
                service="RingCentral", action="send SMS",
                json=body,
            )
            if err:
                return err
            m = r.json()
            return f"SMS sent successfully.\nID: {m.get('id', '?')}\nTo: {to}\nStatus: {m.get('messageStatus', '?')}"

        @mcp.tool()
        async def ringcentral_send_message(to: str, subject: str, text: str, ctx: Context) -> str:
            """Send an internal pager message via RingCentral.

            Args:
                to: Recipient extension number
                subject: Message subject
                text: Message text
            """
            err = validation.validate_content(to, "to")
            if err:
                return err
            err = validation.validate_content(text, "text")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "ringcentral", level="write")
            if err:
                return err
            body = {
                "from": {"extensionNumber": "~"},
                "to": [{"extensionNumber": to}],
                "text": text,
            }
            if subject:
                body["subject"] = subject
            r, err = await token_store.safe_request(
                client, "POST", f"{API}/account/~/extension/~/company-pager",
                service="RingCentral", action="send pager message",
                json=body,
            )
            if err:
                return err
            m = r.json()
            return f"Pager message sent successfully.\nID: {m.get('id', '?')}\nTo: {to}\nSubject: {m.get('subject', '?')}"

        @mcp.tool()
        async def ringcentral_get_call_details(call_id: str, ctx: Context) -> str:
            """Get detailed information about a specific call.

            Args:
                call_id: The call log record ID
            """
            err = validation.validate_id(call_id, "call_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "ringcentral", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "GET", f"{API}/account/~/call-log/{call_id}",
                service="RingCentral", action="get call details",
            )
            if err:
                return err
            c = r.json()
            from_info = c.get("from", {})
            to_info = c.get("to", {})
            return (
                f"Call ID: {c.get('id', '?')}\n"
                f"Direction: {c.get('direction', '?')}\n"
                f"Result: {c.get('result', '?')}\n"
                f"From: {from_info.get('name', from_info.get('phoneNumber', '?'))}\n"
                f"To: {to_info.get('name', to_info.get('phoneNumber', '?'))}\n"
                f"Start: {c.get('startTime', '?')}\n"
                f"Duration: {c.get('duration', '?')}s\n"
                f"Type: {c.get('type', '?')}"
            )

        @mcp.tool()
        async def ringcentral_download_recording(recording_id: str, ctx: Context) -> str:
            """Get the download URL for a call recording.

            Args:
                recording_id: The recording ID
            """
            err = validation.validate_id(recording_id, "recording_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "ringcentral", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "GET", f"{API}/account/~/recording/{recording_id}",
                service="RingCentral", action="get recording",
            )
            if err:
                return err
            rec = r.json()
            return (
                f"Recording ID: {rec.get('id', '?')}\n"
                f"Duration: {rec.get('duration', '?')}s\n"
                f"Content URI: {rec.get('contentUri', '?')}\n"
                f"Content Type: {rec.get('contentType', '?')}"
            )

        @mcp.tool()
        async def ringcentral_list_contacts(ctx: Context, search: str = "", limit: int = 25) -> str:
            """List company contacts from the RingCentral directory.

            Args:
                search: Search query to filter contacts (optional)
                limit: Max results (default: 25)
            """
            limit = validation.validate_limit(limit)
            client, uid, err = await token_store.require_service(ctx, "ringcentral", level="read")
            if err:
                return err
            params: dict = {"perPage": limit}
            if search:
                params["searchString"] = search
            r, err = await token_store.safe_request(
                client, "GET", f"{API}/account/~/directory/contacts",
                service="RingCentral", action="list contacts",
                params=params,
            )
            if err:
                return err
            records = r.json().get("records", [])
            if not records:
                return "No contacts found."
            lines = []
            for c in records:
                name = f"{c.get('firstName', '')} {c.get('lastName', '')}".strip() or "?"
                ext = c.get("extensionNumber", "?")
                email = c.get("email", "?")
                lines.append(f"{name} | Ext: {ext} | Email: {email}")
            return "\n".join(lines)

        @mcp.tool()
        async def ringcentral_list_active_calls(ctx: Context) -> str:
            """List currently active calls in the RingCentral account."""
            client, uid, err = await token_store.require_service(ctx, "ringcentral", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "GET", f"{API}/account/~/active-calls",
                service="RingCentral", action="list active calls",
            )
            if err:
                return err
            records = r.json().get("records", [])
            if not records:
                return "No active calls."
            lines = []
            for c in records:
                direction = c.get("direction", "?")
                from_info = c.get("from", {})
                to_info = c.get("to", {})
                from_name = from_info.get("name", from_info.get("phoneNumber", "?"))
                to_name = to_info.get("name", to_info.get("phoneNumber", "?"))
                result = c.get("result", "?")
                lines.append(f"{direction} | From: {from_name} | To: {to_name} | {result}")
            return "\n".join(lines)
