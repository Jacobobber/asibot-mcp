"""Smartsheet connector: sheets and search via Smartsheet REST API."""

import json
import logging

from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store, validation
from asibot.connectors.base import Connector
from asibot.connectors.pagination import collect, paginate_offset

logger = logging.getLogger(__name__)
API = "https://api.smartsheet.com/2.0"


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
            client, uid, err = await token_store.require_service(ctx, "smartsheet", level="read")
            if err:
                return err
            pages = paginate_offset(
                client, f"{API}/sheets",
                service="Smartsheet", action="list sheets",
                params={},
                results_key="data",
                page_size_param="pageSize",
                offset_param="page",
                offset_start=1,
                offset_step=1,
                page_size=min(limit, 100),
            )
            sheets = await collect(pages, limit)
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
            err = validation.validate_id(sheet_id, "sheet_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "smartsheet", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(client, "GET", f"{API}/sheets/{sheet_id}", service="Smartsheet", action="get sheet")
            if err:
                return err
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
            err = validation.validate_query(query, "query")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "smartsheet", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(client, "GET", f"{API}/search", service="Smartsheet", action="search", params={"query": query})
            if err:
                return err
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

        @mcp.tool()
        async def smartsheet_get_row(sheet_id: str, row_id: str, ctx: Context) -> str:
            """Get a specific row from a Smartsheet sheet.

            Args:
                sheet_id: The sheet ID
                row_id: The row ID
            """
            err = validation.validate_id(sheet_id, "sheet_id")
            if err:
                return err
            err = validation.validate_id(row_id, "row_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "smartsheet", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(client, "GET", f"{API}/sheets/{sheet_id}/rows/{row_id}", service="Smartsheet", action="get row")
            if err:
                return err
            row = r.json()
            cells = row.get("cells", [])
            if not cells:
                return f"Row {row_id}: no cells found."
            lines = [f"Row ID: {row.get('id', '?')} | Row Number: {row.get('rowNumber', '?')}"]
            for cell in cells:
                col_id = cell.get("columnId", "?")
                value = cell.get("displayValue", cell.get("value", ""))
                lines.append(f"  Column {col_id}: {value}")
            return "\n".join(lines)

        @mcp.tool()
        async def smartsheet_list_columns(sheet_id: str, ctx: Context) -> str:
            """List columns of a Smartsheet sheet.

            Args:
                sheet_id: The sheet ID
            """
            err = validation.validate_id(sheet_id, "sheet_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "smartsheet", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(client, "GET", f"{API}/sheets/{sheet_id}/columns", service="Smartsheet", action="list columns")
            if err:
                return err
            columns = r.json().get("data", [])
            if not columns:
                return "No columns found."
            lines = []
            for c in columns:
                lines.append(f"{c.get('title', '?')} (type: {c.get('type', '?')}, ID: {c.get('id', '?')})")
            return "\n".join(lines)

        @mcp.tool()
        async def smartsheet_add_row(sheet_id: str, ctx: Context, cells_json: str = "") -> str:
            """Add a row to a Smartsheet sheet.

            Args:
                sheet_id: The sheet ID
                cells_json: JSON string of cells array, e.g. [{"columnId": 123, "value": "text"}]
            """
            err = validation.validate_id(sheet_id, "sheet_id")
            if err:
                return err
            err = validation.validate_content(cells_json, "cells_json")
            if err:
                return err
            try:
                cells = json.loads(cells_json)
            except (json.JSONDecodeError, TypeError):
                return "Invalid cells_json: must be valid JSON."
            client, uid, err = await token_store.require_service(ctx, "smartsheet", level="write")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "POST", f"{API}/sheets/{sheet_id}/rows",
                service="Smartsheet", action="add row",
                json=[{"toBottom": True, "cells": cells}],
            )
            if err:
                return err
            data = r.json()
            result_rows = data.get("result", [])
            if result_rows:
                return f"Row added. ID: {result_rows[0].get('id', '?')}"
            return "Row added."
