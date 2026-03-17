"""Notion connector: pages, databases, search via Notion API."""

import logging

import httpx
from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store
from asibot.connectors.base import Connector

logger = logging.getLogger(__name__)
API = "https://api.notion.com"


def _make_client(creds):
    if not creds.get("token"):
        return None
    return httpx.AsyncClient(
        headers={
            "Authorization": f"Bearer {creds['token']}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        },
        base_url=API,
        timeout=30.0,
    )


def _rich_text_to_str(rich_text_list: list) -> str:
    """Extract plain text from Notion rich_text array."""
    return "".join(t.get("plain_text", "") for t in rich_text_list)


def _block_to_text(block: dict) -> str:
    """Convert a single Notion block to plain text."""
    btype = block.get("type", "")
    data = block.get(btype, {})

    if btype in ("paragraph", "bulleted_list_item", "numbered_list_item", "quote"):
        text = _rich_text_to_str(data.get("rich_text", []))
        prefix = "- " if btype == "bulleted_list_item" else "> " if btype == "quote" else ""
        return f"{prefix}{text}"

    if btype.startswith("heading_"):
        text = _rich_text_to_str(data.get("rich_text", []))
        level = btype[-1]
        return f"{'#' * int(level)} {text}"

    if btype == "to_do":
        text = _rich_text_to_str(data.get("rich_text", []))
        checked = "[x]" if data.get("checked") else "[ ]"
        return f"{checked} {text}"

    if btype == "code":
        text = _rich_text_to_str(data.get("rich_text", []))
        lang = data.get("language", "")
        return f"```{lang}\n{text}\n```"

    return ""


class NotionConnector(Connector):
    def __init__(self, config=None):
        super().__init__(name="notion", config=config)

    async def connect(self):
        logger.info("Notion: ready (per-user integration token)")

    async def disconnect(self):
        pass

    async def fetch_documents(self):
        return []

    def register_tools(self, mcp: FastMCP):

        @mcp.tool()
        async def notion_search(query: str, ctx: Context, limit: int = 10) -> str:
            """Search Notion pages and databases.

            Args:
                query: Search query
                limit: Max results (default: 10)
            """
            client, uid, err = token_store.require_service(ctx, "notion", _make_client, "read")
            if err:
                return err
            r = await client.post("/v1/search", json={"query": query, "page_size": limit})
            r.raise_for_status()
            results = r.json().get("results", [])
            if not results:
                return "No results found."
            lines = []
            for item in results:
                obj_type = item.get("object", "?")
                item_id = item.get("id", "?")
                if obj_type == "page":
                    props = item.get("properties", {})
                    title_prop = props.get("title") or props.get("Name") or {}
                    title_arr = title_prop.get("title", [])
                    title = _rich_text_to_str(title_arr) if title_arr else "Untitled"
                    lines.append(f"[page] {title}\n  ID: {item_id}")
                elif obj_type == "database":
                    title_arr = item.get("title", [])
                    title = _rich_text_to_str(title_arr) if title_arr else "Untitled DB"
                    lines.append(f"[database] {title}\n  ID: {item_id}")
                else:
                    lines.append(f"[{obj_type}] ID: {item_id}")
            return "\n\n".join(lines)

        @mcp.tool()
        async def notion_read_page(page_id: str, ctx: Context) -> str:
            """Read the content of a Notion page.

            Args:
                page_id: Page ID (UUID)
            """
            client, uid, err = token_store.require_service(ctx, "notion", _make_client, "read")
            if err:
                return err
            # Fetch page metadata
            pr = await client.get(f"/v1/pages/{page_id}")
            pr.raise_for_status()
            page = pr.json()
            props = page.get("properties", {})
            title_prop = props.get("title") or props.get("Name") or {}
            title_arr = title_prop.get("title", [])
            title = _rich_text_to_str(title_arr) if title_arr else "Untitled"

            # Fetch page blocks
            br = await client.get(f"/v1/blocks/{page_id}/children", params={"page_size": 100})
            br.raise_for_status()
            blocks = br.json().get("results", [])
            content_lines = [_block_to_text(b) for b in blocks]
            content = "\n".join(line for line in content_lines if line)
            return f"Title: {title}\n\n{content}" if content else f"Title: {title}\n\n(empty page)"

        @mcp.tool()
        async def notion_list_databases(ctx: Context, limit: int = 10) -> str:
            """List Notion databases the integration has access to.

            Args:
                limit: Max results (default: 10)
            """
            client, uid, err = token_store.require_service(ctx, "notion", _make_client, "read")
            if err:
                return err
            r = await client.post("/v1/search", json={"filter": {"property": "object", "value": "database"}, "page_size": limit})
            r.raise_for_status()
            results = r.json().get("results", [])
            if not results:
                return "No databases found."
            lines = []
            for db in results:
                title_arr = db.get("title", [])
                title = _rich_text_to_str(title_arr) if title_arr else "Untitled DB"
                lines.append(f"{title}\n  ID: {db.get('id', '?')}")
            return "\n\n".join(lines)

        @mcp.tool()
        async def notion_query_database(database_id: str, ctx: Context, limit: int = 20) -> str:
            """Query a Notion database and list its entries.

            Args:
                database_id: Database ID (UUID)
                limit: Max results (default: 20)
            """
            client, uid, err = token_store.require_service(ctx, "notion", _make_client, "read")
            if err:
                return err
            r = await client.post(f"/v1/databases/{database_id}/query", json={"page_size": limit})
            r.raise_for_status()
            results = r.json().get("results", [])
            if not results:
                return "No entries found."
            lines = []
            for row in results:
                props = row.get("properties", {})
                parts = []
                for key, val in props.items():
                    ptype = val.get("type", "")
                    if ptype == "title":
                        parts.append(f"{key}: {_rich_text_to_str(val.get('title', []))}")
                    elif ptype == "rich_text":
                        parts.append(f"{key}: {_rich_text_to_str(val.get('rich_text', []))}")
                    elif ptype == "number":
                        parts.append(f"{key}: {val.get('number', '?')}")
                    elif ptype == "select":
                        parts.append(f"{key}: {(val.get('select') or {}).get('name', '?')}")
                    elif ptype == "status":
                        parts.append(f"{key}: {(val.get('status') or {}).get('name', '?')}")
                    elif ptype == "date":
                        d = val.get("date") or {}
                        parts.append(f"{key}: {d.get('start', '?')}")
                    elif ptype == "checkbox":
                        parts.append(f"{key}: {'Yes' if val.get('checkbox') else 'No'}")
                lines.append(f"ID: {row.get('id', '?')}\n  " + " | ".join(parts))
            return "\n\n".join(lines)
