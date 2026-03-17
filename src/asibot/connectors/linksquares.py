"""LinkSquares connector: contracts and search via LinkSquares REST API."""

import logging

import httpx
from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store
from asibot.connectors.base import Connector

logger = logging.getLogger(__name__)
API = "https://api.linksquares.com/v1"


def _make_client(creds):
    if not creds.get("token"):
        return None
    return httpx.AsyncClient(
        headers={"Authorization": f"Bearer {creds['token']}", "Accept": "application/json"},
        timeout=30.0,
    )


class LinkSquaresConnector(Connector):
    def __init__(self, config=None):
        super().__init__(name="linksquares", config=config)

    async def connect(self):
        logger.info("LinkSquares: ready (per-user Bearer token)")

    async def disconnect(self):
        pass

    async def fetch_documents(self):
        return []

    def register_tools(self, mcp: FastMCP):

        @mcp.tool()
        async def linksquares_list_contracts(ctx: Context, limit: int = 25) -> str:
            """List contracts from LinkSquares.

            Args:
                limit: Max results (default: 25)
            """
            client, uid, err = token_store.require_service(ctx, "linksquares", _make_client, "read")
            if err:
                return err
            r = await client.get(f"{API}/contracts", params={"limit": limit})
            r.raise_for_status()
            contracts = r.json().get("contracts", r.json().get("data", []))
            if not contracts:
                return "No contracts found."
            lines = []
            for c in contracts:
                title = c.get("title", c.get("name", "Untitled"))
                cid = c.get("id", "?")
                status = c.get("status", "?")
                counterparty = c.get("counterparty", c.get("counter_party", "?"))
                effective = c.get("effective_date", "?")
                lines.append(f"{title} (ID: {cid})\n  Counterparty: {counterparty} | Status: {status} | Effective: {effective}")
            return "\n\n".join(lines)

        @mcp.tool()
        async def linksquares_search(query: str, ctx: Context, limit: int = 25) -> str:
            """Search contracts in LinkSquares.

            Args:
                query: Search query (contract title, counterparty, or keyword)
                limit: Max results (default: 25)
            """
            client, uid, err = token_store.require_service(ctx, "linksquares", _make_client, "read")
            if err:
                return err
            r = await client.get(f"{API}/contracts/search", params={"q": query, "limit": limit})
            r.raise_for_status()
            results = r.json().get("contracts", r.json().get("data", []))
            if not results:
                return "No matching contracts found."
            lines = []
            for c in results:
                title = c.get("title", c.get("name", "Untitled"))
                cid = c.get("id", "?")
                counterparty = c.get("counterparty", c.get("counter_party", "?"))
                status = c.get("status", "?")
                lines.append(f"{title} (ID: {cid}) | Counterparty: {counterparty} | Status: {status}")
            return "\n".join(lines)
