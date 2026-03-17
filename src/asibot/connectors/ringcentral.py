"""RingCentral connector: call logs and messages via RingCentral REST API."""

import logging

import httpx
from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store
from asibot.connectors.base import Connector

logger = logging.getLogger(__name__)
API = "https://platform.ringcentral.com/restapi/v1.0"


def _make_client(creds):
    if not creds.get("token"):
        return None
    return httpx.AsyncClient(
        headers={"Authorization": f"Bearer {creds['token']}", "Accept": "application/json"},
        timeout=30.0,
    )


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
            client, uid, err = token_store.require_service(ctx, "ringcentral", _make_client, "read")
            if err:
                return err
            r = await client.get(f"{API}/account/~/call-log", params={"perPage": limit})
            r.raise_for_status()
            records = r.json().get("records", [])
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
            client, uid, err = token_store.require_service(ctx, "ringcentral", _make_client, "read")
            if err:
                return err
            r = await client.get(f"{API}/account/~/extension/~/message-store", params={"perPage": limit})
            r.raise_for_status()
            records = r.json().get("records", [])
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
