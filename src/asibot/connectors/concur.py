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

        @mcp.tool()
        async def concur_create_report(name: str, ctx: Context, policy_id: str = "") -> str:
            """Create a new expense report in SAP Concur.

            Args:
                name: Report name
                policy_id: Optional expense policy ID
            """
            err = validation.validate_content(name, "name")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "concur", level="write")
            if err:
                return err
            payload = {"Name": name}
            if policy_id:
                payload["PolicyID"] = policy_id
            r, err = await token_store.safe_request(
                client, "POST", f"{API}/expense/reports",
                service="Concur", action="create report",
                json=payload,
            )
            if err:
                return err
            data = r.json()
            rid = data.get("ID", "?")
            return f"Report created.\nID: {rid}\nName: {name}"

        @mcp.tool()
        async def concur_create_expense(
            report_id: str, expense_type: str, amount: float, currency: str, date: str, ctx: Context, description: str = ""
        ) -> str:
            """Create an expense entry in a SAP Concur report.

            Args:
                report_id: The expense report ID
                expense_type: Expense type (e.g., "Airfare", "Hotel")
                amount: Transaction amount
                currency: Currency code (e.g., "USD")
                date: Transaction date (YYYY-MM-DD)
                description: Optional description
            """
            err = validation.validate_id(report_id, "report_id")
            if err:
                return err
            err = validation.validate_content(expense_type, "expense_type")
            if err:
                return err
            err = validation.validate_date(date, "date")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "concur", level="write")
            if err:
                return err
            payload = {
                "ReportID": report_id,
                "ExpenseTypeName": expense_type,
                "TransactionAmount": amount,
                "TransactionCurrencyCode": currency,
                "TransactionDate": date,
            }
            if description:
                payload["Description"] = description
            r, err = await token_store.safe_request(
                client, "POST", f"{API}/expense/entries",
                service="Concur", action="create expense",
                json=payload,
            )
            if err:
                return err
            data = r.json()
            eid = data.get("ID", "?")
            return f"Expense created.\nID: {eid}\nType: {expense_type}\nAmount: {currency} {amount}\nDate: {date}"

        @mcp.tool()
        async def concur_submit_report(report_id: str, ctx: Context) -> str:
            """Submit an expense report for approval in SAP Concur.

            Args:
                report_id: The expense report ID
            """
            err = validation.validate_id(report_id, "report_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "concur", level="write")
            if err:
                return err
            payload = {"Status": "Submitted"}
            r, err = await token_store.safe_request(
                client, "PATCH", f"{API}/expense/reports/{report_id}",
                service="Concur", action="submit report",
                json=payload,
            )
            if err:
                return err
            return f"Report {report_id} submitted for approval."

        @mcp.tool()
        async def concur_approve_report(report_id: str, ctx: Context, comment: str = "") -> str:
            """Approve a pending expense report in SAP Concur.

            Args:
                report_id: The expense report ID
                comment: Optional approval comment
            """
            err = validation.validate_id(report_id, "report_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "concur", level="write")
            if err:
                return err
            payload = {"Status": "Approved"}
            if comment:
                payload["Comment"] = comment
            r, err = await token_store.safe_request(
                client, "PATCH", f"{API}/expense/reports/{report_id}",
                service="Concur", action="approve report",
                json=payload,
            )
            if err:
                return err
            return f"Report {report_id} approved."

        @mcp.tool()
        async def concur_reject_report(report_id: str, ctx: Context, comment: str = "") -> str:
            """Reject (send back) an expense report in SAP Concur.

            Args:
                report_id: The expense report ID
                comment: Optional rejection comment
            """
            err = validation.validate_id(report_id, "report_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "concur", level="write")
            if err:
                return err
            payload = {"Status": "SendBack"}
            if comment:
                payload["Comment"] = comment
            r, err = await token_store.safe_request(
                client, "PATCH", f"{API}/expense/reports/{report_id}",
                service="Concur", action="reject report",
                json=payload,
            )
            if err:
                return err
            return f"Report {report_id} sent back for revision."

        @mcp.tool()
        async def concur_add_receipt(expense_id: str, filename: str, content_type: str, ctx: Context) -> str:
            """Attach a receipt image to an expense entry in SAP Concur.

            Args:
                expense_id: The expense entry ID
                filename: Receipt filename
                content_type: MIME type (e.g., "image/jpeg", "application/pdf")
            """
            err = validation.validate_id(expense_id, "expense_id")
            if err:
                return err
            err = validation.validate_content(filename, "filename")
            if err:
                return err
            err = validation.validate_content(content_type, "content_type")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "concur", level="write")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "POST", f"{API}/expense/entries/{expense_id}/receipts",
                service="Concur", action="add receipt",
                headers={"Content-Type": content_type, "Content-Disposition": f'attachment; filename="{filename}"'},
            )
            if err:
                return err
            return f"Receipt '{filename}' attached to expense {expense_id}."
