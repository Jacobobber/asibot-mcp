"""Salesforce connector: records and SOQL queries via Salesforce REST API."""

import json
import logging

from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store, validation
from asibot.connectors.base import Connector
from asibot.connectors.pagination import collect, paginate_salesforce

logger = logging.getLogger(__name__)

# SOSL reserved characters that must be escaped with a backslash
_SOSL_SPECIAL = set(r"""?&|!{}[]()^~*:\\"'+-""")


def _escape_sosl(value: str) -> str:
    """Escape special characters for safe use in a SOSL FIND clause."""
    return "".join(f"\\{ch}" if ch in _SOSL_SPECIAL else ch for ch in value)


class SalesforceConnector(Connector):
    def __init__(self, config=None):
        super().__init__(name="salesforce", config=config)

    async def connect(self):
        logger.info("Salesforce: ready (per-user token)")

    async def disconnect(self):
        pass

    async def fetch_documents(self):
        return []

    def register_tools(self, mcp: FastMCP):

        @mcp.tool()
        async def salesforce_search(query: str, ctx: Context, limit: int = 10) -> str:
            """Search Salesforce records using SOSL.

            Args:
                query: Search query text
                limit: Max results (default: 10)
            """
            err = validation.validate_query(query, "query")
            if err:
                return err
            limit = validation.validate_limit(limit)
            client, uid, err = await token_store.require_service(ctx, "salesforce", level="read")
            if err:
                return err
            sosl = f"FIND {{{_escape_sosl(query)}}} IN ALL FIELDS RETURNING Account(Name, Id), Contact(Name, Email, Id), Opportunity(Name, StageName, Amount, Id) LIMIT {limit}"
            r, err = await token_store.safe_request(
                client, "GET", "/search",
                service="Salesforce", action="search",
                params={"q": sosl},
            )
            if err:
                return err
            results = r.json().get("searchRecords", [])
            if not results:
                return "No records found."
            lines = []
            for rec in results:
                obj_type = rec.get("attributes", {}).get("type", "?")
                name = rec.get("Name", "?")
                rec_id = rec.get("Id", "?")
                extras = ""
                if obj_type == "Contact":
                    extras = f" | Email: {rec.get('Email', '?')}"
                elif obj_type == "Opportunity":
                    extras = f" | Stage: {rec.get('StageName', '?')} | Amount: {rec.get('Amount', '?')}"
                lines.append(f"[{obj_type}] {name}{extras}\n  ID: {rec_id}")
            return "\n\n".join(lines)

        @mcp.tool()
        async def salesforce_query(soql: str, ctx: Context) -> str:
            """Run a SOQL query against Salesforce.

            Args:
                soql: The SOQL query string (e.g., "SELECT Id, Name FROM Account LIMIT 10")
            """
            err = validation.validate_query(soql, "soql")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "salesforce", level="read")
            if err:
                return err
            pages = paginate_salesforce(
                client, "/query",
                service="Salesforce", action="query",
                params={"q": soql},
            )
            records = await collect(pages, 2000)
            if not records:
                return "Query returned no records."
            lines = [f"Total: {len(records)} record(s)\n"]
            for rec in records:
                obj_type = rec.get("attributes", {}).get("type", "?")
                fields = {k: v for k, v in rec.items() if k != "attributes"}
                field_str = " | ".join(f"{k}: {v}" for k, v in fields.items())
                lines.append(f"[{obj_type}] {field_str}")
            return "\n".join(lines)

        @mcp.tool()
        async def salesforce_get_record(object_type: str, record_id: str, ctx: Context) -> str:
            """Get a Salesforce record by type and ID.

            Args:
                object_type: Salesforce object type (e.g., "Account", "Contact", "Opportunity")
                record_id: The record ID
            """
            err = validation.validate_sf_object_type(object_type)
            if err:
                return err
            err = validation.validate_id(record_id, "record_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "salesforce", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "GET", f"/sobjects/{object_type}/{record_id}",
                service="Salesforce", action="get record",
            )
            if err:
                return err
            rec = r.json()
            fields = {k: v for k, v in rec.items() if k != "attributes" and v is not None}
            lines = [f"[{object_type}] {rec.get('Name', rec.get('Id', '?'))}\n"]
            for k, v in fields.items():
                lines.append(f"  {k}: {v}")
            return "\n".join(lines)

        @mcp.tool()
        async def salesforce_describe(object_type: str, ctx: Context) -> str:
            """Describe a Salesforce object's metadata (fields, labels, types).

            Args:
                object_type: Salesforce object type (e.g., "Account", "Contact")
            """
            err = validation.validate_sf_object_type(object_type)
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "salesforce", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "GET", f"/sobjects/{object_type}/describe",
                service="Salesforce", action="describe",
            )
            if err:
                return err
            data = r.json()
            obj_name = data.get("name", "?")
            obj_label = data.get("label", "?")
            all_fields = data.get("fields", [])
            fields = all_fields[:50]
            lines = [f"{obj_name} ({obj_label}) — {len(all_fields)} fields (showing first {len(fields)})\n"]
            for f in fields:
                line = f"  {f.get('name', '?')} ({f.get('label', '?')}): {f.get('type', '?')}"
                picklist = f.get("picklistValues")
                if picklist:
                    values = [pv.get("value", "?") for pv in picklist[:5]]
                    if len(picklist) > 5:
                        values.append("...")
                    line += f" | Values: {', '.join(values)}"
                lines.append(line)
            return "\n".join(lines)

        @mcp.tool()
        async def salesforce_create_record(object_type: str, ctx: Context, fields_json: str = "") -> str:
            """Create a new Salesforce record.

            Args:
                object_type: Salesforce object type (e.g., "Account", "Contact")
                fields_json: JSON string of field values (e.g., '{"Name": "Acme"}')
            """
            err = validation.validate_sf_object_type(object_type)
            if err:
                return err
            if not fields_json or not fields_json.strip():
                return "fields_json is required."
            try:
                body = json.loads(fields_json)
            except (json.JSONDecodeError, TypeError):
                return "Invalid fields_json: must be valid JSON."
            if not isinstance(body, dict):
                return "Invalid fields_json: must be a JSON object."
            client, uid, err = await token_store.require_service(ctx, "salesforce", level="write")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "POST", f"/sobjects/{object_type}",
                service="Salesforce", action="create record",
                json=body,
            )
            if err:
                return err
            data = r.json()
            return f"Created {object_type} record: {data.get('id', '?')}"

        @mcp.tool()
        async def salesforce_update_record(object_type: str, record_id: str, ctx: Context, fields_json: str = "") -> str:
            """Update an existing Salesforce record.

            Args:
                object_type: Salesforce object type (e.g., "Account", "Contact")
                record_id: The record ID
                fields_json: JSON string of field values to update
            """
            err = validation.validate_sf_object_type(object_type)
            if err:
                return err
            err = validation.validate_id(record_id, "record_id")
            if err:
                return err
            if not fields_json or not fields_json.strip():
                return "fields_json is required."
            try:
                body = json.loads(fields_json)
            except (json.JSONDecodeError, TypeError):
                return "Invalid fields_json: must be valid JSON."
            if not isinstance(body, dict):
                return "Invalid fields_json: must be a JSON object."
            client, uid, err = await token_store.require_service(ctx, "salesforce", level="write")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "PATCH", f"/sobjects/{object_type}/{record_id}",
                service="Salesforce", action="update record",
                json=body,
            )
            if err:
                return err
            return f"Updated {object_type} record {record_id}."
