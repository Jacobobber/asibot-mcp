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


# CQL special characters that must be escaped with a backslash
_CQL_SPECIAL = set(r'+-&|!(){}[]^"~*?\/')


def _escape_cql_value(value: str) -> str:
    """Escape special characters for safe use in a CQL text search value."""
    return "".join(f"\\{ch}" if ch in _CQL_SPECIAL else ch for ch in value)


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
                query: Search query text
                limit: Max results (default: 10)
            """
            err = validation.validate_query(query, "query")
            if err:
                return err
            limit = validation.validate_limit(limit)
            client, uid, err = await token_store.require_service(ctx, "atlassian", _make_client, "read")
            if err:
                return err
            cql = f'text ~ "{_escape_cql_value(query)}"'
            r, err = await token_store.safe_request(
                client, "GET", "/content/search",
                service="Confluence", action="search",
                params={"cql": cql, "expand": "space", "limit": min(limit, 100)},
            )
            if err:
                return err
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
            err = validation.validate_id(page_id, "page_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "atlassian", _make_client, "read")
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
            client, uid, err = await token_store.require_service(ctx, "atlassian", _make_client, "read")
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

        @mcp.tool()
        async def confluence_list_pages(space_key: str, ctx: Context, limit: int = 25) -> str:
            """List pages in a Confluence space.

            Args:
                space_key: The space key
                limit: Max results (default: 25)
            """
            err = validation.validate_id(space_key, "space_key")
            if err:
                return err
            limit = validation.validate_limit(limit)
            client, uid, err = await token_store.require_service(ctx, "atlassian", _make_client, "read")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "GET", "/content",
                service="Confluence", action="list pages",
                params={"spaceKey": space_key, "type": "page", "limit": limit, "expand": "version"},
            )
            if err:
                return err
            results = r.json().get("results", [])
            if not results:
                return "No pages found."
            lines = []
            for p in results:
                version = (p.get("version") or {}).get("number", "?")
                lines.append(f"{p.get('title', '?')}\n  ID: {p.get('id', '?')} | Version: {version}")
            return "\n\n".join(lines)

        @mcp.tool()
        async def confluence_get_page_history(page_id: str, ctx: Context, limit: int = 10) -> str:
            """Get the version history of a Confluence page.

            Args:
                page_id: Page ID
                limit: Max history entries (default: 10)
            """
            err = validation.validate_id(page_id, "page_id")
            if err:
                return err
            limit = validation.validate_limit(limit)
            client, uid, err = await token_store.require_service(ctx, "atlassian", _make_client, "read")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "GET", f"/content/{page_id}/history",
                service="Confluence", action="get page history",
            )
            if err:
                return err
            data = r.json()
            latest = data.get("lastUpdated", {})
            created = data.get("createdBy", {})
            created_date = data.get("createdDate", "?")
            output = (
                f"Created by: {created.get('displayName', '?')} on {created_date[:10] if created_date and created_date != '?' else created_date}\n"
                f"Last updated by: {latest.get('by', {}).get('displayName', '?')} on {latest.get('when', '?')[:10] if latest.get('when') else '?'}\n"
                f"Current version: {latest.get('number', '?')}"
            )
            return output

        @mcp.tool()
        async def confluence_list_attachments(page_id: str, ctx: Context) -> str:
            """List attachments on a Confluence page.

            Args:
                page_id: Page ID
            """
            err = validation.validate_id(page_id, "page_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "atlassian", _make_client, "read")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "GET", f"/content/{page_id}/child/attachment",
                service="Confluence", action="list attachments",
            )
            if err:
                return err
            results = r.json().get("results", [])
            if not results:
                return "No attachments found."
            lines = []
            for a in results:
                title = a.get("title", "?")
                ext = a.get("extensions", {})
                size = ext.get("fileSize", "?")
                media_type = ext.get("mediaType", "?")
                lines.append(f"{title}\n  Size: {size} | Type: {media_type} | ID: {a.get('id', '?')}")
            return "\n\n".join(lines)

        @mcp.tool()
        async def confluence_create_page(space_key: str, title: str, body: str, ctx: Context) -> str:
            """Create a new Confluence page.

            Args:
                space_key: The space key to create the page in
                title: Page title
                body: Page body (Confluence storage format HTML)
            """
            err = validation.validate_id(space_key, "space_key")
            if err:
                return err
            err = validation.validate_content(title, "title")
            if err:
                return err
            err = validation.validate_content(body, "body")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "atlassian", _make_client, "write")
            if err:
                return err
            payload = {
                "type": "page",
                "title": title,
                "space": {"key": space_key},
                "body": {"storage": {"value": body, "representation": "storage"}},
            }
            r, err = await token_store.safe_request(
                client, "POST", "/content",
                service="Confluence", action="create page",
                json=payload,
            )
            if err:
                return err
            data = r.json()
            return f"Page created. ID: {data.get('id', '?')} | Title: {data.get('title', '?')}"
