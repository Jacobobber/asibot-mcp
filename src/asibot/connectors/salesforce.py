"""Salesforce connector: records and SOQL queries via Salesforce REST API."""

import logging

from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store, validation
from asibot.connectors.base import Connector
from asibot.connectors.pagination import collect, paginate_salesforce

logger = logging.getLogger(__name__)


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
            client, uid, err = token_store.require_service(ctx, "salesforce", level="read")
            if err:
                return err
            sosl = f"FIND {{{query}}} IN ALL FIELDS RETURNING Account(Name, Id), Contact(Name, Email, Id), Opportunity(Name, StageName, Amount, Id) LIMIT {limit}"
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
            client, uid, err = token_store.require_service(ctx, "salesforce", level="read")
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
            client, uid, err = token_store.require_service(ctx, "salesforce", level="read")
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
