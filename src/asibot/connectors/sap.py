"""SAP connector: sales orders and search via SAP API."""

import logging

from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store, validation
from asibot.connectors.base import Connector
from asibot.connectors.pagination import collect, paginate_odata

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
            client, uid, err = await token_store.require_service(ctx, "sap", level="read")
            if err:
                return err
            creds = token_store.get_credentials(uid, "sap")
            base = creds.get("base_url", "")
            url_err = validation.validate_base_url(base, "base_url")
            if url_err:
                return url_err
            base = base.rstrip("/")
            pages = paginate_odata(
                client, f"{base}/sap/opu/odata/sap/API_SALES_ORDER_SRV/A_SalesOrder",
                service="SAP", action="list orders",
                params={"$top": min(limit, 100), "$format": "json"},
                results_key="d.results",
                next_link_key="d.__next",
            )
            results = await collect(pages, limit)
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
            client, uid, err = await token_store.require_service(ctx, "sap", level="read")
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
            client, uid, err = await token_store.require_service(ctx, "sap", level="read")
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
            pages = paginate_odata(
                client, f"{base}/sap/opu/odata/sap/API_SALES_ORDER_SRV/A_SalesOrder",
                service="SAP", action="search",
                params={"$filter": filter_expr, "$top": min(limit, 100), "$format": "json"},
                results_key="d.results",
                next_link_key="d.__next",
            )
            results = await collect(pages, limit)
            if not results:
                return "No matching orders found."
            lines = []
            for o in results:
                oid = o.get("SalesOrder", "?")
                customer = o.get("SoldToParty", "?")
                date = o.get("CreationDate", "?")
                lines.append(f"Order {oid} | Customer: {customer} | Created: {date}")
            return "\n".join(lines)

        @mcp.tool()
        async def sap_list_order_items(order_id: str, ctx: Context) -> str:
            """List line items for a SAP sales order.

            Args:
                order_id: The sales order number
            """
            err = validation.validate_id(order_id, "order_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "sap", level="read")
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
                client, "GET", f"{base}/sap/opu/odata/sap/API_SALES_ORDER_SRV/A_SalesOrder('{safe_order_id}')/to_Item",
                service="SAP", action="list order items",
                params={"$format": "json"},
            )
            if err:
                return err
            results = r.json().get("d", {}).get("results", [])
            if not results:
                return "No line items found for this order."
            lines = []
            for item in results:
                item_num = item.get("SalesOrderItem", "?")
                material = item.get("Material", "?")
                quantity = item.get("OrderQuantity", "?")
                net_amount = item.get("NetAmount", "?")
                currency = item.get("TransactionCurrency", "")
                lines.append(f"Item {item_num} | Material: {material} | Qty: {quantity} | Net: {currency} {net_amount}")
            return "\n".join(lines)

        @mcp.tool()
        async def sap_get_customer(customer_id: str, ctx: Context) -> str:
            """Get business partner (customer) details from SAP.

            Args:
                customer_id: The business partner number
            """
            err = validation.validate_id(customer_id, "customer_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "sap", level="read")
            if err:
                return err
            creds = token_store.get_credentials(uid, "sap")
            base = creds.get("base_url", "")
            url_err = validation.validate_base_url(base, "base_url")
            if url_err:
                return url_err
            base = base.rstrip("/")
            safe_customer_id = customer_id.replace("'", "''")
            r, err = await token_store.safe_request(
                client, "GET", f"{base}/sap/opu/odata/sap/API_BUSINESS_PARTNER/A_BusinessPartner('{safe_customer_id}')",
                service="SAP", action="get customer",
                params={"$format": "json"},
            )
            if err:
                return err
            bp = r.json().get("d", r.json())
            name = bp.get("BusinessPartnerFullName", bp.get("BusinessPartnerName", "?"))
            category = bp.get("BusinessPartnerCategory", "?")
            created = bp.get("CreationDate", "?")
            industry = bp.get("Industry", "?")
            country = bp.get("Country", "?")
            return f"Customer: {name}\nID: {customer_id}\nCategory: {category}\nIndustry: {industry}\nCountry: {country}\nCreated: {created}"

        @mcp.tool()
        async def sap_list_deliveries(order_id: str, ctx: Context) -> str:
            """List schedule lines (deliveries) for a SAP sales order.

            Args:
                order_id: The sales order number
            """
            err = validation.validate_id(order_id, "order_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "sap", level="read")
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
                client, "GET", f"{base}/sap/opu/odata/sap/API_SALES_ORDER_SRV/A_SalesOrder('{safe_order_id}')/to_ScheduleLine",
                service="SAP", action="list deliveries",
                params={"$format": "json"},
            )
            if err:
                return err
            results = r.json().get("d", {}).get("results", [])
            if not results:
                return "No schedule lines found for this order."
            lines = []
            for sl in results:
                item = sl.get("SalesOrderItem", "?")
                schedule = sl.get("ScheduleLine", "?")
                delivery_date = sl.get("ScheduleLineDeliveryDate", sl.get("RequestedDeliveryDate", "?"))
                quantity = sl.get("OrderQuantity", sl.get("ScheduleLineOrderQuantity", "?"))
                lines.append(f"Item {item} / Line {schedule} | Delivery: {delivery_date} | Qty: {quantity}")
            return "\n".join(lines)

        @mcp.tool()
        async def sap_create_order(customer_id: str, items: list[dict], ctx: Context, requested_date: str = "") -> str:
            """Create a sales order in SAP.

            Args:
                customer_id: Customer (business partner) ID
                items: List of item dicts with keys: material, quantity
                requested_date: Requested delivery date (YYYY-MM-DD, optional)
            """
            err = validation.validate_id(customer_id, "customer_id")
            if err:
                return err
            if not items:
                return "items is required."
            if requested_date:
                err = validation.validate_date(requested_date, "requested_date")
                if err:
                    return err
            client, uid, err = await token_store.require_service(ctx, "sap", level="write")
            if err:
                return err
            creds = token_store.get_credentials(uid, "sap")
            base = creds.get("base_url", "")
            url_err = validation.validate_base_url(base, "base_url")
            if url_err:
                return url_err
            base = base.rstrip("/")
            order_items = []
            for idx, item in enumerate(items):
                order_item = {
                    "SalesOrderItem": str((idx + 1) * 10),
                    "Material": item.get("material", ""),
                    "OrderQuantity": str(item.get("quantity", 1)),
                }
                order_items.append(order_item)
            payload = {
                "SalesOrderType": "OR",
                "SoldToParty": customer_id,
                "to_Item": {"results": order_items},
            }
            if requested_date:
                payload["RequestedDeliveryDate"] = requested_date
            r, err = await token_store.safe_request(
                client, "POST", f"{base}/sap/opu/odata/sap/API_SALES_ORDER_SRV/A_SalesOrder",
                service="SAP", action="create order",
                params={"$format": "json"},
                json=payload,
            )
            if err:
                return err
            data = r.json().get("d", r.json())
            oid = data.get("SalesOrder", "?")
            return f"Sales order created.\nOrder: {oid}\nCustomer: {customer_id}\nItems: {len(items)}"

        @mcp.tool()
        async def sap_update_order(order_id: str, fields: dict, ctx: Context) -> str:
            """Update fields on an existing SAP sales order.

            Args:
                order_id: The sales order number
                fields: Dict of field names and values to update
            """
            err = validation.validate_id(order_id, "order_id")
            if err:
                return err
            if not fields:
                return "fields is required."
            client, uid, err = await token_store.require_service(ctx, "sap", level="write")
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
                client, "PATCH", f"{base}/sap/opu/odata/sap/API_SALES_ORDER_SRV/A_SalesOrder('{safe_order_id}')",
                service="SAP", action="update order",
                params={"$format": "json"},
                json=fields,
            )
            if err:
                return err
            updated = ", ".join(fields.keys())
            return f"Order {order_id} updated.\nFields: {updated}"

        @mcp.tool()
        async def sap_cancel_order(order_id: str, ctx: Context, reason: str = "") -> str:
            """Cancel a SAP sales order.

            Args:
                order_id: The sales order number
                reason: Optional cancellation reason
            """
            err = validation.validate_id(order_id, "order_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "sap", level="write")
            if err:
                return err
            creds = token_store.get_credentials(uid, "sap")
            base = creds.get("base_url", "")
            url_err = validation.validate_base_url(base, "base_url")
            if url_err:
                return url_err
            base = base.rstrip("/")
            safe_order_id = order_id.replace("'", "''")
            payload = {"OverallSDProcessStatus": "C"}
            if reason:
                payload["RejectionReason"] = reason
            r, err = await token_store.safe_request(
                client, "PATCH", f"{base}/sap/opu/odata/sap/API_SALES_ORDER_SRV/A_SalesOrder('{safe_order_id}')",
                service="SAP", action="cancel order",
                params={"$format": "json"},
                json=payload,
            )
            if err:
                return err
            return f"Order {order_id} cancelled."

        @mcp.tool()
        async def sap_list_materials(ctx: Context, search: str = "", limit: int = 25) -> str:
            """Search SAP material master records.

            Args:
                search: Optional search term to filter materials
                limit: Max results (default: 25)
            """
            limit = validation.validate_limit(limit)
            client, uid, err = await token_store.require_service(ctx, "sap", level="read")
            if err:
                return err
            creds = token_store.get_credentials(uid, "sap")
            base = creds.get("base_url", "")
            url_err = validation.validate_base_url(base, "base_url")
            if url_err:
                return url_err
            base = base.rstrip("/")
            params = {"$top": min(limit, 100), "$format": "json"}
            if search:
                safe_search = search.replace("'", "''")
                params["$filter"] = f"substringof('{safe_search}', MaterialDescription) or substringof('{safe_search}', Material)"
            r, err = await token_store.safe_request(
                client, "GET", f"{base}/sap/opu/odata/sap/API_MATERIAL_SRV/A_Material",
                service="SAP", action="list materials",
                params=params,
            )
            if err:
                return err
            results = r.json().get("d", {}).get("results", [])
            if not results:
                return "No materials found."
            lines = []
            for m in results:
                mid = m.get("Material", "?")
                desc = m.get("MaterialDescription", m.get("MaterialName", "?"))
                mtype = m.get("MaterialType", "?")
                group = m.get("MaterialGroup", "?")
                lines.append(f"{mid} | {desc} | Type: {mtype} | Group: {group}")
            return "\n".join(lines)

        @mcp.tool()
        async def sap_get_material(material_id: str, ctx: Context) -> str:
            """Get details of a SAP material master record.

            Args:
                material_id: The material number
            """
            err = validation.validate_id(material_id, "material_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "sap", level="read")
            if err:
                return err
            creds = token_store.get_credentials(uid, "sap")
            base = creds.get("base_url", "")
            url_err = validation.validate_base_url(base, "base_url")
            if url_err:
                return url_err
            base = base.rstrip("/")
            safe_material_id = material_id.replace("'", "''")
            r, err = await token_store.safe_request(
                client, "GET", f"{base}/sap/opu/odata/sap/API_MATERIAL_SRV/A_Material('{safe_material_id}')",
                service="SAP", action="get material",
                params={"$format": "json"},
            )
            if err:
                return err
            m = r.json().get("d", r.json())
            desc = m.get("MaterialDescription", m.get("MaterialName", "?"))
            mtype = m.get("MaterialType", "?")
            group = m.get("MaterialGroup", "?")
            uom = m.get("BaseUnit", "?")
            weight = m.get("GrossWeight", "?")
            return f"Material: {material_id}\nDescription: {desc}\nType: {mtype}\nGroup: {group}\nBase Unit: {uom}\nGross Weight: {weight}"

        @mcp.tool()
        async def sap_list_invoices(ctx: Context, customer_id: str = "", limit: int = 25) -> str:
            """List billing documents (invoices) from SAP.

            Args:
                customer_id: Optional customer ID to filter invoices
                limit: Max results (default: 25)
            """
            limit = validation.validate_limit(limit)
            client, uid, err = await token_store.require_service(ctx, "sap", level="read")
            if err:
                return err
            creds = token_store.get_credentials(uid, "sap")
            base = creds.get("base_url", "")
            url_err = validation.validate_base_url(base, "base_url")
            if url_err:
                return url_err
            base = base.rstrip("/")
            params = {"$top": min(limit, 100), "$format": "json"}
            if customer_id:
                safe_cid = customer_id.replace("'", "''")
                params["$filter"] = f"SoldToParty eq '{safe_cid}'"
            r, err = await token_store.safe_request(
                client, "GET", f"{base}/sap/opu/odata/sap/API_BILLING_DOCUMENT_SRV/A_BillingDocument",
                service="SAP", action="list invoices",
                params=params,
            )
            if err:
                return err
            results = r.json().get("d", {}).get("results", [])
            if not results:
                return "No invoices found."
            lines = []
            for inv in results:
                doc_id = inv.get("BillingDocument", "?")
                doc_type = inv.get("BillingDocumentType", "?")
                customer = inv.get("SoldToParty", "?")
                amount = inv.get("TotalNetAmount", "?")
                currency = inv.get("TransactionCurrency", "")
                date = inv.get("BillingDocumentDate", "?")
                lines.append(f"Invoice {doc_id} | Type: {doc_type} | Customer: {customer} | {currency} {amount} | Date: {date}")
            return "\n".join(lines)
