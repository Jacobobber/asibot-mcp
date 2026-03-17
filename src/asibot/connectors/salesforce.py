"""Salesforce connector: records and SOQL queries via Salesforce REST API."""

import logging

import httpx
from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store
from asibot.connectors.base import Connector

logger = logging.getLogger(__name__)


def _make_client(creds):
    if not creds.get("token") or not creds.get("instance_url"):
        return None
    base = f"{creds['instance_url']}/services/data/v59.0"
    return httpx.AsyncClient(
        base_url=base,
        headers={"Authorization": f"Bearer {creds['token']}"},
        timeout=30.0,
    )


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
            client, uid, err = token_store.require_service(ctx, "salesforce", _make_client, "read")
            if err:
                return err
            sosl = f"FIND {{{query}}} IN ALL FIELDS RETURNING Account(Name, Id), Contact(Name, Email, Id), Opportunity(Name, StageName, Amount, Id) LIMIT {limit}"
            r = await client.get("/search", params={"q": sosl})
            r.raise_for_status()
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
            client, uid, err = token_store.require_service(ctx, "salesforce", _make_client, "read")
            if err:
                return err
            r = await client.get("/query", params={"q": soql})
            r.raise_for_status()
            data = r.json()
            records = data.get("records", [])
            total = data.get("totalSize", 0)
            if not records:
                return "Query returned no records."
            lines = [f"Total: {total} record(s)\n"]
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
            client, uid, err = token_store.require_service(ctx, "salesforce", _make_client, "read")
            if err:
                return err
            r = await client.get(f"/sobjects/{object_type}/{record_id}")
            r.raise_for_status()
            rec = r.json()
            fields = {k: v for k, v in rec.items() if k != "attributes" and v is not None}
            lines = [f"[{object_type}] {rec.get('Name', rec.get('Id', '?'))}\n"]
            for k, v in fields.items():
                lines.append(f"  {k}: {v}")
            return "\n".join(lines)
