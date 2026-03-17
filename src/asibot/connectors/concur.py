"""SAP Concur connector: expense reports via Concur REST API."""

import logging

import httpx
from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store
from asibot.connectors.base import Connector

logger = logging.getLogger(__name__)
API = "https://us.api.concursolutions.com/api/v3.0"


def _make_client(creds):
    if not creds.get("token"):
        return None
    return httpx.AsyncClient(
        headers={"Authorization": f"Bearer {creds['token']}", "Accept": "application/json"},
        timeout=30.0,
    )


class ConcurConnector(Connector):
    def __init__(self, config=None):
        super().__init__(name="concur", config=config)

    async def connect(self):
        logger.info("Concur: ready (per-user OAuth token)")

    async def disconnect(self):
        pass

    async def fetch_documents(self):
        return []

    def register_tools(self, mcp: FastMCP):

        @mcp.tool()
        async def concur_list_reports(ctx: Context, limit: int = 25) -> str:
            """List expense reports from SAP Concur.

            Args:
                limit: Max results (default: 25)
            """
            client, uid, err = token_store.require_service(ctx, "concur", _make_client, "read")
            if err:
                return err
            r = await client.get(f"{API}/expense/reports", params={"limit": limit})
            r.raise_for_status()
            items = r.json().get("Items", [])
            if not items:
                return "No expense reports found."
            lines = []
            for rpt in items:
                name = rpt.get("Name", "Untitled")
                rid = rpt.get("ID", "?")
                status = rpt.get("Status", "?")
                total = rpt.get("Total", "?")
                currency = rpt.get("CurrencyCode", "")
                lines.append(f"{name} (ID: {rid})\n  Status: {status} | Total: {currency} {total}")
            return "\n\n".join(lines)

        @mcp.tool()
        async def concur_get_report(report_id: str, ctx: Context) -> str:
            """Get details of a specific expense report.

            Args:
                report_id: The expense report ID
            """
            client, uid, err = token_store.require_service(ctx, "concur", _make_client, "read")
            if err:
                return err
            r = await client.get(f"{API}/expense/reports/{report_id}")
            r.raise_for_status()
            rpt = r.json()
            name = rpt.get("Name", "Untitled")
            status = rpt.get("Status", "?")
            total = rpt.get("Total", "?")
            currency = rpt.get("CurrencyCode", "")
            created = rpt.get("CreateDate", "?")[:10] if rpt.get("CreateDate") else "?"
            owner = rpt.get("OwnerName", "?")
            output = f"Report: {name}\nID: {report_id}\nOwner: {owner}\nStatus: {status}\nTotal: {currency} {total}\nCreated: {created}"
            entries = rpt.get("Entries", [])
            if entries:
                output += f"\n\n--- {len(entries)} Entries ---"
                for e in entries:
                    desc = e.get("Description", "No description")
                    amount = e.get("TransactionAmount", "?")
                    output += f"\n  {desc} | {currency} {amount}"
            return output
