"""Confluence connector: pages, spaces, search via Confluence REST API."""

import logging
import re

import httpx
from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store, validation
from asibot.connectors.base import Connector
from asibot.connectors.pagination import collect, paginate_offset

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
            err = validation.validate_query(query, "query")
            if err:
                return err
            limit = validation.validate_limit(limit)
            client, uid, err = token_store.require_service(ctx, "atlassian", _make_client, "read")
            if err:
                return err
            cql = query if "=" in query else f'text ~ "{query}"'
            pages = paginate_offset(
                client, "/content/search",
                service="Confluence", action="search",
                params={"cql": cql, "expand": "space"},
                results_key="results",
                page_size_param="limit",
                offset_param="start",
                offset_start=0,
                page_size=min(limit, 100),
            )
            results = await collect(pages, limit)
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
            err = validation.validate_id(page_id, "page_id")
            if err:
                return err
            client, uid, err = token_store.require_service(ctx, "atlassian", _make_client, "read")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "GET", f"/content/{page_id}",
                service="Confluence", action="read page",
                params={"expand": "body.storage,space,version"},
            )
            if err:
                return err
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
            pages = paginate_offset(
                client, "/space",
                service="Confluence", action="list spaces",
                params={"type": "global"},
                results_key="results",
                page_size_param="limit",
                offset_param="start",
                offset_start=0,
                page_size=min(limit, 100),
            )
            spaces = await collect(pages, limit)
            if not spaces:
                return "No spaces found."
            return "\n".join(f"{s.get('key', '?')}: {s.get('name', '?')} ({s.get('type', '?')})" for s in spaces)
