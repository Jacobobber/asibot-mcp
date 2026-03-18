"""SAP connector: sales orders and search via SAP API."""

import logging

from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store, validation
from asibot.connectors.base import Connector

logger = logging.getLogger(__name__)


class SAPConnector(Connector):
    def __init__(self, config=None):
        super().__init__(name="sap", config=config)

    async def connect(self):
        logger.info("SAP: ready (per-user Bearer token)")

    async def disconnect(self):
        pass

    async def fetch_documents(self):
        return []

    def register_tools(self, mcp: FastMCP):

        @mcp.tool()
        async def sap_list_orders(ctx: Context, limit: int = 25) -> str:
            """List sales orders from SAP.

            Args:
                limit: Max results (default: 25)
            """
            limit = validation.validate_limit(limit)
            client, uid, err = token_store.require_service(ctx, "sap", level="read")
            if err:
                return err
            creds = token_store.get_credentials(uid, "sap")
            base = creds.get("base_url", "")
            url_err = validation.validate_base_url(base, "base_url")
            if url_err:
                return url_err
            base = base.rstrip("/")
            r, err = await token_store.safe_request(
                client, "GET", f"{base}/sap/opu/odata/sap/API_SALES_ORDER_SRV/A_SalesOrder",
                service="SAP", action="list orders",
                params={"$top": limit, "$format": "json"},
            )
            if err:
                return err
            results = r.json().get("d", {}).get("results", [])
            if not results:
                return "No sales orders found."
            lines = []
            for o in results:
                oid = o.get("SalesOrder", "?")
                otype = o.get("SalesOrderType", "?")
                org = o.get("SalesOrganization", "?")
                customer = o.get("SoldToParty", "?")
                date = o.get("CreationDate", "?")
                lines.append(f"Order {oid} | Type: {otype} | Org: {org} | Customer: {customer} | Created: {date}")
            return "\n".join(lines)

        @mcp.tool()
        async def sap_get_order(order_id: str, ctx: Context) -> str:
            """Get details of a specific SAP sales order.

            Args:
                order_id: The sales order number
            """
            err = validation.validate_id(order_id, "order_id")
            if err:
                return err
            client, uid, err = token_store.require_service(ctx, "sap", level="read")
            if err:
                return err
            creds = token_store.get_credentials(uid, "sap")
            base = creds.get("base_url", "")
            url_err = validation.validate_base_url(base, "base_url")
            if url_err:
                return url_err
            base = base.rstrip("/")
            safe_order_id = order_id.replace("'", "''")
            r, err = await token_store.safe_request(
                client, "GET", f"{base}/sap/opu/odata/sap/API_SALES_ORDER_SRV/A_SalesOrder('{safe_order_id}')",
                service="SAP", action="get order",
                params={"$format": "json"},
            )
            if err:
                return err
            o = r.json().get("d", r.json())
            otype = o.get("SalesOrderType", "?")
            org = o.get("SalesOrganization", "?")
            customer = o.get("SoldToParty", "?")
            date = o.get("CreationDate", "?")
            net = o.get("TotalNetAmount", "?")
            currency = o.get("TransactionCurrency", "")
            status = o.get("OverallSDProcessStatus", "?")
            return f"Order: {order_id}\nType: {otype}\nOrg: {org}\nCustomer: {customer}\nCreated: {date}\nNet Amount: {currency} {net}\nStatus: {status}"

        @mcp.tool()
        async def sap_search(query: str, ctx: Context, limit: int = 25) -> str:
            """Search SAP sales orders by customer or keyword.

            Args:
                query: Search query (customer name or order number)
                limit: Max results (default: 25)
            """
            err = validation.validate_query(query, "query")
            if err:
                return err
            limit = validation.validate_limit(limit)
            client, uid, err = token_store.require_service(ctx, "sap", level="read")
            if err:
                return err
            creds = token_store.get_credentials(uid, "sap")
            base = creds.get("base_url", "")
            url_err = validation.validate_base_url(base, "base_url")
            if url_err:
                return url_err
            base = base.rstrip("/")
            safe_query = query.replace("'", "''")
            filter_expr = f"substringof('{safe_query}', SoldToParty) or substringof('{safe_query}', SalesOrder)"
            r, err = await token_store.safe_request(
                client, "GET", f"{base}/sap/opu/odata/sap/API_SALES_ORDER_SRV/A_SalesOrder",
                service="SAP", action="search",
                params={"$filter": filter_expr, "$top": limit, "$format": "json"},
            )
            if err:
                return err
            results = r.json().get("d", {}).get("results", [])
            if not results:
                return "No matching orders found."
            lines = []
            for o in results:
                oid = o.get("SalesOrder", "?")
                customer = o.get("SoldToParty", "?")
                date = o.get("CreationDate", "?")
                lines.append(f"Order {oid} | Customer: {customer} | Created: {date}")
            return "\n".join(lines)
