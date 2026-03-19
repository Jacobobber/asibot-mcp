"""Notion connector: pages, databases, search via Notion API."""

import json
import logging

from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store, validation
from asibot.connectors.base import Connector
from asibot.connectors.pagination import collect, paginate_cursor

logger = logging.getLogger(__name__)
API = "https://api.notion.com"


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
        hashes = "#" * int(level) if level.isdigit() else "#"
        return f"{hashes} {text}"

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
            err = validation.validate_query(query, "query")
            if err:
                return err
            limit = validation.validate_limit(limit)
            client, uid, err = await token_store.require_service(ctx, "notion", level="read")
            if err:
                return err
            pages = paginate_cursor(
                client, "/v1/search",
                service="Notion", action="search",
                json_body={"query": query},
                results_key="results",
                cursor_response_key="next_cursor",
                cursor_request_key="start_cursor",
                cursor_in="json",
                page_size_param="page_size",
                page_size=min(limit, 100),
                has_more_key="has_more",
            )
            results = await collect(pages, limit)
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
            err = validation.validate_id(page_id, "page_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "notion", level="read")
            if err:
                return err
            # Fetch page metadata
            pr, err = await token_store.safe_request(client, "GET", f"/v1/pages/{page_id}", service="Notion", action="read page")
            if err:
                return err
            page = pr.json()
            props = page.get("properties", {})
            title_prop = props.get("title") or props.get("Name") or {}
            title_arr = title_prop.get("title", [])
            title = _rich_text_to_str(title_arr) if title_arr else "Untitled"

            # Fetch page blocks
            br, block_err = await token_store.safe_request(client, "GET", f"/v1/blocks/{page_id}/children", service="Notion", action="read blocks", params={"page_size": 100})
            if block_err:
                return f"Title: {title}\n\n(could not load page blocks)"
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
            client, uid, err = await token_store.require_service(ctx, "notion", level="read")
            if err:
                return err
            pages = paginate_cursor(
                client, "/v1/search",
                service="Notion", action="list databases",
                json_body={"filter": {"property": "object", "value": "database"}},
                results_key="results",
                cursor_response_key="next_cursor",
                cursor_request_key="start_cursor",
                cursor_in="json",
                page_size_param="page_size",
                page_size=min(limit, 100),
                has_more_key="has_more",
            )
            results = await collect(pages, limit)
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
            err = validation.validate_id(database_id, "database_id")
            if err:
                return err
            limit = validation.validate_limit(limit)
            client, uid, err = await token_store.require_service(ctx, "notion", level="read")
            if err:
                return err
            pages = paginate_cursor(
                client, f"/v1/databases/{database_id}/query",
                service="Notion", action="query database",
                json_body={},
                results_key="results",
                cursor_response_key="next_cursor",
                cursor_request_key="start_cursor",
                cursor_in="json",
                page_size_param="page_size",
                page_size=min(limit, 100),
                has_more_key="has_more",
            )
            results = await collect(pages, limit)
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

        @mcp.tool()
        async def notion_create_page(parent_id: str, title: str, ctx: Context, content: str = "") -> str:
            """Create a new Notion page.

            Args:
                parent_id: Parent page ID
                title: Page title
                content: Optional paragraph text content
            """
            err = validation.validate_id(parent_id, "parent_id")
            if err:
                return err
            err = validation.validate_content(title, "title")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "notion", level="write")
            if err:
                return err
            body = {
                "parent": {"page_id": parent_id},
                "properties": {"title": [{"text": {"content": title}}]},
            }
            if content:
                body["children"] = [
                    {
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {"rich_text": [{"type": "text", "text": {"content": content}}]},
                    }
                ]
            r, err = await token_store.safe_request(client, "POST", f"{API}/v1/pages", service="Notion", action="create page", json=body)
            if err:
                return err
            data = r.json()
            return f"Page created. ID: {data.get('id', '?')}"

        @mcp.tool()
        async def notion_update_page(page_id: str, ctx: Context, properties_json: str = "") -> str:
            """Update properties of a Notion page.

            Args:
                page_id: Page ID (UUID)
                properties_json: JSON string of properties to update
            """
            err = validation.validate_id(page_id, "page_id")
            if err:
                return err
            err = validation.validate_content(properties_json, "properties_json")
            if err:
                return err
            try:
                properties = json.loads(properties_json)
            except (json.JSONDecodeError, TypeError):
                return "Invalid properties_json: must be valid JSON."
            client, uid, err = await token_store.require_service(ctx, "notion", level="write")
            if err:
                return err
            r, err = await token_store.safe_request(client, "PATCH", f"{API}/v1/pages/{page_id}", service="Notion", action="update page", json={"properties": properties})
            if err:
                return err
            data = r.json()
            return f"Page updated. ID: {data.get('id', '?')}"

        @mcp.tool()
        async def notion_create_database_entry(database_id: str, ctx: Context, properties_json: str = "") -> str:
            """Create a new entry in a Notion database.

            Args:
                database_id: Database ID (UUID)
                properties_json: JSON string of properties for the new entry
            """
            err = validation.validate_id(database_id, "database_id")
            if err:
                return err
            err = validation.validate_content(properties_json, "properties_json")
            if err:
                return err
            try:
                properties = json.loads(properties_json)
            except (json.JSONDecodeError, TypeError):
                return "Invalid properties_json: must be valid JSON."
            client, uid, err = await token_store.require_service(ctx, "notion", level="write")
            if err:
                return err
            body = {
                "parent": {"database_id": database_id},
                "properties": properties,
            }
            r, err = await token_store.safe_request(client, "POST", f"{API}/v1/pages", service="Notion", action="create database entry", json=body)
            if err:
                return err
            data = r.json()
            return f"Entry created. ID: {data.get('id', '?')}"

        @mcp.tool()
        async def notion_update_block(block_id: str, content: str, ctx: Context) -> str:
            """Update the content of a Notion block (paragraph, heading, etc.).

            Args:
                block_id: Block ID (UUID)
                content: New text content for the block
            """
            err = validation.validate_id(block_id, "block_id")
            if err:
                return err
            err = validation.validate_content(content, "content")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "notion", level="write")
            if err:
                return err
            # Fetch existing block to determine its type
            br, fetch_err = await token_store.safe_request(client, "GET", f"{API}/v1/blocks/{block_id}", service="Notion", action="get block")
            if fetch_err:
                return fetch_err
            block = br.json()
            btype = block.get("type", "paragraph")
            body = {
                btype: {
                    "rich_text": [{"type": "text", "text": {"content": content}}],
                },
            }
            r, err = await token_store.safe_request(client, "PATCH", f"{API}/v1/blocks/{block_id}", service="Notion", action="update block", json=body)
            if err:
                return err
            return f"Block updated. ID: {block_id}"

        @mcp.tool()
        async def notion_delete_block(block_id: str, ctx: Context) -> str:
            """Delete a Notion block.

            Args:
                block_id: Block ID (UUID)
            """
            err = validation.validate_id(block_id, "block_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "notion", level="write")
            if err:
                return err
            r, err = await token_store.safe_request(client, "DELETE", f"{API}/v1/blocks/{block_id}", service="Notion", action="delete block")
            if err:
                return err
            return f"Block deleted. ID: {block_id}"

        @mcp.tool()
        async def notion_append_blocks(page_id: str, blocks_json: str, ctx: Context) -> str:
            """Append blocks to a Notion page.

            Args:
                page_id: Page ID (UUID)
                blocks_json: JSON array of block objects to append
            """
            err = validation.validate_id(page_id, "page_id")
            if err:
                return err
            err = validation.validate_content(blocks_json, "blocks_json")
            if err:
                return err
            try:
                blocks = json.loads(blocks_json)
            except (json.JSONDecodeError, TypeError):
                return "Invalid blocks_json: must be a valid JSON array."
            if not isinstance(blocks, list):
                return "Invalid blocks_json: must be a JSON array."
            client, uid, err = await token_store.require_service(ctx, "notion", level="write")
            if err:
                return err
            body = {"children": blocks}
            r, err = await token_store.safe_request(client, "PATCH", f"{API}/v1/blocks/{page_id}/children", service="Notion", action="append blocks", json=body)
            if err:
                return err
            return f"Blocks appended to page {page_id}."

        @mcp.tool()
        async def notion_update_database(database_id: str, ctx: Context, title: str = "", properties_json: str = "") -> str:
            """Update a Notion database title and/or properties.

            Args:
                database_id: Database ID (UUID)
                title: New title for the database (optional)
                properties_json: JSON string of properties to update (optional)
            """
            err = validation.validate_id(database_id, "database_id")
            if err:
                return err
            if not title and not properties_json:
                return "At least one of title or properties_json is required."
            body = {}
            if title:
                body["title"] = [{"type": "text", "text": {"content": title}}]
            if properties_json:
                try:
                    properties = json.loads(properties_json)
                except (json.JSONDecodeError, TypeError):
                    return "Invalid properties_json: must be valid JSON."
                body["properties"] = properties
            client, uid, err = await token_store.require_service(ctx, "notion", level="write")
            if err:
                return err
            r, err = await token_store.safe_request(client, "PATCH", f"{API}/v1/databases/{database_id}", service="Notion", action="update database", json=body)
            if err:
                return err
            data = r.json()
            return f"Database updated. ID: {data.get('id', '?')}"

        @mcp.tool()
        async def notion_delete_page(page_id: str, ctx: Context) -> str:
            """Archive (delete) a Notion page.

            Args:
                page_id: Page ID (UUID)
            """
            err = validation.validate_id(page_id, "page_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "notion", level="write")
            if err:
                return err
            r, err = await token_store.safe_request(client, "PATCH", f"{API}/v1/pages/{page_id}", service="Notion", action="archive page", json={"archived": True})
            if err:
                return err
            return f"Page archived. ID: {page_id}"

        @mcp.tool()
        async def notion_add_comment(page_id: str, body: str, ctx: Context) -> str:
            """Add a comment to a Notion page.

            Args:
                page_id: Page ID (UUID)
                body: Comment text
            """
            err = validation.validate_id(page_id, "page_id")
            if err:
                return err
            err = validation.validate_content(body, "body")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "notion", level="write")
            if err:
                return err
            payload = {
                "parent": {"page_id": page_id},
                "rich_text": [{"type": "text", "text": {"content": body}}],
            }
            r, err = await token_store.safe_request(client, "POST", f"{API}/v1/comments", service="Notion", action="add comment", json=payload)
            if err:
                return err
            data = r.json()
            return f"Comment added. ID: {data.get('id', '?')}"

        @mcp.tool()
        async def notion_list_comments(page_id: str, ctx: Context) -> str:
            """List comments on a Notion page.

            Args:
                page_id: Page ID (UUID)
            """
            err = validation.validate_id(page_id, "page_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "notion", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(client, "GET", f"{API}/v1/comments", service="Notion", action="list comments", params={"block_id": page_id})
            if err:
                return err
            data = r.json()
            comments = data.get("results", [])
            if not comments:
                return "No comments found."
            lines = []
            for c in comments:
                rich_text = c.get("rich_text", [])
                text = _rich_text_to_str(rich_text)
                created = c.get("created_time", "?")
                comment_id = c.get("id", "?")
                lines.append(f"[{created[:16] if created and created != '?' else created}] (ID: {comment_id})\n  {text}")
            return "\n\n".join(lines)

        @mcp.tool()
        async def notion_list_users(ctx: Context) -> str:
            """List users in the Notion workspace."""
            client, uid, err = await token_store.require_service(ctx, "notion", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(client, "GET", f"{API}/v1/users", service="Notion", action="list users")
            if err:
                return err
            data = r.json()
            users = data.get("results", [])
            if not users:
                return "No users found."
            lines = []
            for u in users:
                name = u.get("name", "?")
                utype = u.get("type", "?")
                uid_val = u.get("id", "?")
                email = ""
                if utype == "person":
                    email = u.get("person", {}).get("email", "")
                elif utype == "bot":
                    email = "(bot)"
                email_str = f" | {email}" if email else ""
                lines.append(f"{name} ({utype}){email_str}\n  ID: {uid_val}")
            return "\n\n".join(lines)
