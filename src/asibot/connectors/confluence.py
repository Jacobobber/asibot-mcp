"""Confluence connector: pages, spaces, search via Confluence REST API."""

import logging
import re

import httpx
from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store
from asibot.connectors.base import Connector

logger = logging.getLogger(__name__)


def _make_client(creds):
    if not creds.get("email") or not creds.get("api_token") or not creds.get("domain"):
        return None
    return httpx.AsyncClient(
        auth=(creds["email"], creds["api_token"]),
        base_url=f"https://{creds['domain']}/wiki/rest/api",
        headers={"Accept": "application/json"},
        timeout=30.0,
    )


def _strip_html(html: str) -> str:
    """Strip HTML tags from content."""
    return re.sub(r"<[^>]+>", " ", html).strip()


class ConfluenceConnector(Connector):
    def __init__(self, config=None):
        super().__init__(name="confluence", config=config)

    async def connect(self):
        logger.info("Confluence: ready (per-user credentials)")

    async def disconnect(self):
        pass

    async def fetch_documents(self):
        return []

    def register_tools(self, mcp: FastMCP):

        @mcp.tool()
        async def confluence_search(query: str, ctx: Context, limit: int = 10) -> str:
            """Search Confluence pages and content.

            Args:
                query: Search query (CQL or text)
                limit: Max results (default: 10)
            """
            client, uid, err = token_store.require_service(ctx, "atlassian", _make_client, "read")
            if err:
                return err
            cql = query if "=" in query else f'text ~ "{query}"'
            r = await client.get("/content/search", params={"cql": cql, "limit": limit, "expand": "space"})
            r.raise_for_status()
            results = r.json().get("results", [])
            if not results:
                return "No pages found."
            lines = []
            for p in results:
                space = (p.get("space") or {}).get("name", "?")
                lines.append(f"{p.get('title', '?')}\n  Space: {space} | ID: {p.get('id', '?')} | Type: {p.get('type', '?')}")
            return "\n\n".join(lines)

        @mcp.tool()
        async def confluence_read_page(page_id: str, ctx: Context) -> str:
            """Read the content of a Confluence page.

            Args:
                page_id: Page ID
            """
            client, uid, err = token_store.require_service(ctx, "atlassian", _make_client, "read")
            if err:
                return err
            r = await client.get(f"/content/{page_id}", params={"expand": "body.storage,space,version"})
            r.raise_for_status()
            page = r.json()
            title = page.get("title", "?")
            space = (page.get("space") or {}).get("name", "?")
            version = (page.get("version") or {}).get("number", "?")
            html = (page.get("body", {}).get("storage", {}).get("value", ""))
            text = _strip_html(html)
            return (
                f"Title: {title}\n"
                f"Space: {space} | Version: {version}\n"
                f"\n{text}\n"
            )

        @mcp.tool()
        async def confluence_list_spaces(ctx: Context, limit: int = 25) -> str:
            """List Confluence spaces.

            Args:
                limit: Max results (default: 25)
            """
            client, uid, err = token_store.require_service(ctx, "atlassian", _make_client, "read")
            if err:
                return err
            r = await client.get("/space", params={"limit": limit, "type": "global"})
            r.raise_for_status()
            spaces = r.json().get("results", [])
            if not spaces:
                return "No spaces found."
            return "\n".join(f"{s.get('key', '?')}: {s.get('name', '?')} ({s.get('type', '?')})" for s in spaces)
