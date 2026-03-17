"""SAP connector: sales orders and search via SAP API."""

import logging

import httpx
from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store
from asibot.connectors.base import Connector

logger = logging.getLogger(__name__)


def _make_client(creds):
    if not creds.get("token") or not creds.get("base_url"):
        return None
    return httpx.AsyncClient(
        headers={"Authorization": f"Bearer {creds['token']}", "Accept": "application/json"},
        timeout=30.0,
    )


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
            client, uid, err = token_store.require_service(ctx, "sap", _make_client, "read")
            if err:
                return err
            creds = token_store.get_credentials(uid, "sap")
            base = creds["base_url"].rstrip("/")
            r = await client.get(f"{base}/sap/opu/odata/sap/API_SALES_ORDER_SRV/A_SalesOrder", params={"$top": limit, "$format": "json"})
            r.raise_for_status()
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
            client, uid, err = token_store.require_service(ctx, "sap", _make_client, "read")
            if err:
                return err
            creds = token_store.get_credentials(uid, "sap")
            base = creds["base_url"].rstrip("/")
            r = await client.get(f"{base}/sap/opu/odata/sap/API_SALES_ORDER_SRV/A_SalesOrder('{order_id}')", params={"$format": "json"})
            r.raise_for_status()
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
            client, uid, err = token_store.require_service(ctx, "sap", _make_client, "read")
            if err:
                return err
            creds = token_store.get_credentials(uid, "sap")
            base = creds["base_url"].rstrip("/")
            filter_expr = f"substringof('{query}', SoldToParty) or substringof('{query}', SalesOrder)"
            r = await client.get(
                f"{base}/sap/opu/odata/sap/API_SALES_ORDER_SRV/A_SalesOrder",
                params={"$filter": filter_expr, "$top": limit, "$format": "json"},
            )
            r.raise_for_status()
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
