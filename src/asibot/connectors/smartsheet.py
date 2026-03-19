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

        @mcp.tool()
        async def smartsheet_update_row(sheet_id: str, row_id: str, cells_json: str, ctx: Context) -> str:
            """Update cell values in a Smartsheet row.

            Args:
                sheet_id: The sheet ID
                row_id: The row ID
                cells_json: JSON string of cells array, e.g. [{"columnId": 123, "value": "new text"}]
            """
            err = validation.validate_id(sheet_id, "sheet_id")
            if err:
                return err
            err = validation.validate_id(row_id, "row_id")
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
                client, "PUT", f"{API}/sheets/{sheet_id}/rows",
                service="Smartsheet", action="update row",
                json=[{"id": row_id, "cells": cells}],
            )
            if err:
                return err
            return f"Row {row_id} updated."

        @mcp.tool()
        async def smartsheet_delete_row(sheet_id: str, row_id: str, ctx: Context) -> str:
            """Delete a row from a Smartsheet sheet.

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
            client, uid, err = await token_store.require_service(ctx, "smartsheet", level="write")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "DELETE", f"{API}/sheets/{sheet_id}/rows",
                service="Smartsheet", action="delete row",
                params={"ids": row_id},
            )
            if err:
                return err
            return f"Row {row_id} deleted."

        @mcp.tool()
        async def smartsheet_create_sheet(name: str, columns_json: str, ctx: Context) -> str:
            """Create a new Smartsheet sheet with column definitions.

            Args:
                name: Sheet name
                columns_json: JSON string of columns array, e.g. [{"title": "Name", "type": "TEXT_NUMBER", "primary": true}]
            """
            err = validation.validate_content(name, "name")
            if err:
                return err
            err = validation.validate_content(columns_json, "columns_json")
            if err:
                return err
            try:
                columns = json.loads(columns_json)
            except (json.JSONDecodeError, TypeError):
                return "Invalid columns_json: must be valid JSON."
            client, uid, err = await token_store.require_service(ctx, "smartsheet", level="write")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "POST", f"{API}/sheets",
                service="Smartsheet", action="create sheet",
                json={"name": name, "columns": columns},
            )
            if err:
                return err
            data = r.json()
            result = data.get("result", {})
            return f"Sheet created. ID: {result.get('id', '?')} | Name: {result.get('name', name)}"

        @mcp.tool()
        async def smartsheet_add_column(sheet_id: str, title: str, column_type: str, ctx: Context, options: str = "") -> str:
            """Add a column to a Smartsheet sheet.

            Args:
                sheet_id: The sheet ID
                title: Column title
                column_type: Column type (TEXT_NUMBER, PICKLIST, DATE, CONTACT_LIST, CHECKBOX)
                options: Comma-separated options for PICKLIST type — optional
            """
            err = validation.validate_id(sheet_id, "sheet_id")
            if err:
                return err
            err = validation.validate_content(title, "title")
            if err:
                return err
            err = validation.validate_content(column_type, "column_type")
            if err:
                return err
            col_def: dict = {"title": title, "type": column_type}
            if options:
                col_def["options"] = [o.strip() for o in options.split(",") if o.strip()]
            client, uid, err = await token_store.require_service(ctx, "smartsheet", level="write")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "POST", f"{API}/sheets/{sheet_id}/columns",
                service="Smartsheet", action="add column",
                json=col_def,
            )
            if err:
                return err
            data = r.json()
            result = data.get("result", {})
            return f"Column added. ID: {result.get('id', '?')} | Title: {result.get('title', title)}"

        @mcp.tool()
        async def smartsheet_update_column(sheet_id: str, column_id: str, ctx: Context, title: str = "", column_type: str = "") -> str:
            """Update a column in a Smartsheet sheet.

            Args:
                sheet_id: The sheet ID
                column_id: The column ID
                title: New column title — optional
                column_type: New column type — optional
            """
            err = validation.validate_id(sheet_id, "sheet_id")
            if err:
                return err
            err = validation.validate_id(column_id, "column_id")
            if err:
                return err
            update: dict = {}
            if title:
                update["title"] = title
            if column_type:
                update["type"] = column_type
            if not update:
                return "No fields to update. Provide at least title or column_type."
            client, uid, err = await token_store.require_service(ctx, "smartsheet", level="write")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "PUT", f"{API}/sheets/{sheet_id}/columns/{column_id}",
                service="Smartsheet", action="update column",
                json=update,
            )
            if err:
                return err
            data = r.json()
            result = data.get("result", {})
            return f"Column {column_id} updated. Title: {result.get('title', '?')}"

        @mcp.tool()
        async def smartsheet_add_comment(sheet_id: str, row_id: str, text: str, ctx: Context) -> str:
            """Add a comment to a Smartsheet row.

            Args:
                sheet_id: The sheet ID
                row_id: The row ID
                text: Comment text
            """
            err = validation.validate_id(sheet_id, "sheet_id")
            if err:
                return err
            err = validation.validate_id(row_id, "row_id")
            if err:
                return err
            err = validation.validate_content(text, "text")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "smartsheet", level="write")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "POST", f"{API}/sheets/{sheet_id}/rows/{row_id}/discussions",
                service="Smartsheet", action="add comment",
                json={"comment": {"text": text}},
            )
            if err:
                return err
            return f"Comment added to row {row_id}."

        @mcp.tool()
        async def smartsheet_list_attachments(sheet_id: str, row_id: str, ctx: Context) -> str:
            """List attachments on a Smartsheet row.

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
            r, err = await token_store.safe_request(
                client, "GET", f"{API}/sheets/{sheet_id}/rows/{row_id}/attachments",
                service="Smartsheet", action="list attachments",
            )
            if err:
                return err
            attachments = r.json().get("data", [])
            if not attachments:
                return "No attachments found."
            lines = []
            for a in attachments:
                lines.append(
                    f"{a.get('name', '?')}\n"
                    f"  ID: {a.get('id', '?')} | Type: {a.get('mimeType', '?')} | Size: {a.get('sizeInKb', '?')} KB"
                )
            return "\n\n".join(lines)

        @mcp.tool()
        async def smartsheet_share_sheet(sheet_id: str, email: str, access_level: str, ctx: Context) -> str:
            """Share a Smartsheet sheet with a user.

            Args:
                sheet_id: The sheet ID
                email: Email address to share with
                access_level: Access level (VIEWER, EDITOR, EDITOR_SHARE, ADMIN)
            """
            err = validation.validate_id(sheet_id, "sheet_id")
            if err:
                return err
            err = validation.validate_email_address(email)
            if err:
                return err
            valid_levels = {"VIEWER", "EDITOR", "EDITOR_SHARE", "ADMIN"}
            if access_level not in valid_levels:
                return f"Invalid access_level: '{access_level}'. Allowed: {', '.join(sorted(valid_levels))}"
            client, uid, err = await token_store.require_service(ctx, "smartsheet", level="write")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "POST", f"{API}/sheets/{sheet_id}/shares",
                service="Smartsheet", action="share sheet",
                json={"email": email, "accessLevel": access_level},
            )
            if err:
                return err
            return f"Sheet {sheet_id} shared with {email} as {access_level}."
