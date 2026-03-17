"""Smartsheet connector: sheets and search via Smartsheet REST API."""

import logging

import httpx
from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store
from asibot.connectors.base import Connector

logger = logging.getLogger(__name__)
API = "https://api.smartsheet.com/2.0"


def _make_client(creds):
    if not creds.get("token"):
        return None
    return httpx.AsyncClient(
        headers={"Authorization": f"Bearer {creds['token']}"},
        timeout=30.0,
    )


class SmartsheetConnector(Connector):
    def __init__(self, config=None):
        super().__init__(name="smartsheet", config=config)

    async def connect(self):
        logger.info("Smartsheet: ready (per-user token)")

    async def disconnect(self):
        pass

    async def fetch_documents(self):
        return []

    def register_tools(self, mcp: FastMCP):

        @mcp.tool()
        async def smartsheet_list_sheets(ctx: Context, limit: int = 50) -> str:
            """List Smartsheet sheets accessible to you.

            Args:
                limit: Max results (default: 50)
            """
            client, uid, err = token_store.require_service(ctx, "smartsheet", _make_client, "read")
            if err:
                return err
            r = await client.get(f"{API}/sheets", params={"pageSize": limit})
            r.raise_for_status()
            sheets = r.json().get("data", [])
            if not sheets:
                return "No sheets found."
            lines = []
            for s in sheets:
                modified = s.get("modifiedAt", "?")
                lines.append(f"{s.get('name', 'Untitled')}\n  ID: {s.get('id', '?')} | Modified: {modified[:10] if modified and modified != '?' else modified}")
            return "\n\n".join(lines)

        @mcp.tool()
        async def smartsheet_get_sheet(sheet_id: str, ctx: Context) -> str:
            """Get a Smartsheet sheet with columns and rows.

            Args:
                sheet_id: The sheet ID
            """
            client, uid, err = token_store.require_service(ctx, "smartsheet", _make_client, "read")
            if err:
                return err
            r = await client.get(f"{API}/sheets/{sheet_id}")
            r.raise_for_status()
            data = r.json()
            name = data.get("name", "Untitled")
            columns = data.get("columns", [])
            rows = data.get("rows", [])
            col_names = [c.get("title", "?") for c in columns]
            col_ids = [c.get("id") for c in columns]
            output = f"{name}  ({len(rows)} rows, {len(columns)} columns)\n"
            output += "Columns: " + " | ".join(col_names) + "\n\n"
            for row in rows[:50]:
                cells = row.get("cells", [])
                cell_map = {c.get("columnId"): c.get("displayValue", c.get("value", "")) for c in cells}
                values = [str(cell_map.get(cid, "")) for cid in col_ids]
                output += " | ".join(values) + "\n"
            if len(rows) > 50:
                output += f"\n... and {len(rows) - 50} more rows"
            return output

        @mcp.tool()
        async def smartsheet_search(query: str, ctx: Context) -> str:
            """Search across all Smartsheet sheets.

            Args:
                query: Search query text
            """
            client, uid, err = token_store.require_service(ctx, "smartsheet", _make_client, "read")
            if err:
                return err
            r = await client.get(f"{API}/search", params={"query": query})
            r.raise_for_status()
            results = r.json().get("results", [])
            if not results:
                return "No results found."
            lines = []
            for item in results:
                obj_type = item.get("objectType", "?")
                text = item.get("text", "?")
                context_data = item.get("contextData", [])
                parent = item.get("parentObjectName", "?")
                lines.append(f"[{obj_type}] {text}\n  In: {parent}")
                if context_data:
                    for cd in context_data[:3]:
                        lines.append(f"    {cd}")
            return "\n\n".join(lines)
