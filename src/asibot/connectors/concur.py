"""SAP Concur connector: expense reports via Concur REST API."""

import logging

from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store, validation
from asibot.connectors.base import Connector
from asibot.connectors.pagination import collect, paginate_odata

logger = logging.getLogger(__name__)
API = "https://us.api.concursolutions.com/api/v3.0"


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
            client, uid, err = await token_store.require_service(ctx, "concur", level="read")
            if err:
                return err
            pages = paginate_odata(
                client, f"{API}/expense/reports",
                service="Concur", action="list reports",
                params={"limit": min(limit, 100)},
                results_key="Items",
                next_link_key="NextPage",
            )
            items = await collect(pages, limit)
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
            err = validation.validate_id(report_id, "report_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "concur", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(client, "GET", f"{API}/expense/reports/{report_id}", service="Concur", action="get report")
            if err:
                return err
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

        @mcp.tool()
        async def concur_list_expenses(report_id: str, ctx: Context, limit: int = 25) -> str:
            """List expense entries for a specific report from SAP Concur.

            Args:
                report_id: The expense report ID
                limit: Max results (default: 25)
            """
            err = validation.validate_id(report_id, "report_id")
            if err:
                return err
            limit = validation.validate_limit(limit)
            client, uid, err = await token_store.require_service(ctx, "concur", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(client, "GET", f"{API}/expense/entries", service="Concur", action="list expenses", params={"reportID": report_id, "limit": limit})
            if err:
                return err
            items = r.json().get("Items", [])
            if not items:
                return "No expense entries found for this report."
            lines = []
            for e in items:
                desc = e.get("Description", e.get("ExpenseTypeName", "No description"))
                amount = e.get("TransactionAmount", "?")
                currency = e.get("TransactionCurrencyCode", "")
                date = e.get("TransactionDate", "?")[:10] if e.get("TransactionDate") else "?"
                eid = e.get("ID", "?")
                lines.append(f"{desc} (ID: {eid}) | {currency} {amount} | Date: {date}")
            return "\n".join(lines)

        @mcp.tool()
        async def concur_get_expense(expense_id: str, ctx: Context) -> str:
            """Get details of a specific expense entry from SAP Concur.

            Args:
                expense_id: The expense entry ID
            """
            err = validation.validate_id(expense_id, "expense_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "concur", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(client, "GET", f"{API}/expense/entries/{expense_id}", service="Concur", action="get expense")
            if err:
                return err
            e = r.json()
            desc = e.get("Description", e.get("ExpenseTypeName", "No description"))
            amount = e.get("TransactionAmount", "?")
            currency = e.get("TransactionCurrencyCode", "")
            date = e.get("TransactionDate", "?")[:10] if e.get("TransactionDate") else "?"
            vendor = e.get("VendorDescription", "?")
            category = e.get("ExpenseTypeName", "?")
            report_id = e.get("ReportID", "?")
            return f"{desc}\nID: {expense_id}\nAmount: {currency} {amount}\nDate: {date}\nVendor: {vendor}\nCategory: {category}\nReport: {report_id}"

        @mcp.tool()
        async def concur_list_approvals(ctx: Context, limit: int = 25) -> str:
            """List expense reports pending approval from SAP Concur.

            Args:
                limit: Max results (default: 25)
            """
            limit = validation.validate_limit(limit)
            client, uid, err = await token_store.require_service(ctx, "concur", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(client, "GET", f"{API}/expense/reports", service="Concur", action="list approvals", params={"approvalStatusCode": "A_PEND", "limit": limit})
            if err:
                return err
            items = r.json().get("Items", [])
            if not items:
                return "No reports pending approval."
            lines = []
            for rpt in items:
                name = rpt.get("Name", "Untitled")
                rid = rpt.get("ID", "?")
                owner = rpt.get("OwnerName", "?")
                total = rpt.get("Total", "?")
                currency = rpt.get("CurrencyCode", "")
                lines.append(f"{name} (ID: {rid}) | Owner: {owner} | Total: {currency} {total}")
            return "\n".join(lines)
