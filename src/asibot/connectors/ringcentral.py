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
            client, uid, err = token_store.require_service(ctx, "ringcentral", level="read")
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
            client, uid, err = token_store.require_service(ctx, "ringcentral", level="read")
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
