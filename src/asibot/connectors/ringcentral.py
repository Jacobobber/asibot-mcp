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
