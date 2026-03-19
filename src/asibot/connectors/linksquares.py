"""LinkSquares connector: contracts and search via LinkSquares REST API."""

import logging

from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store, validation
from asibot.connectors.base import Connector
from asibot.connectors.pagination import collect, paginate_offset

logger = logging.getLogger(__name__)
API = "https://api.linksquares.com/v1"


class LinkSquaresConnector(Connector):
    def __init__(self, config=None):
        super().__init__(name="linksquares", config=config)

    async def connect(self):
        logger.info("LinkSquares: ready (per-user Bearer token)")

    async def disconnect(self):
        pass

    async def fetch_documents(self):
        return []

    def register_tools(self, mcp: FastMCP):

        @mcp.tool()
        async def linksquares_list_contracts(ctx: Context, limit: int = 25) -> str:
            """List contracts from LinkSquares.

            Args:
                limit: Max results (default: 25)
            """
            client, uid, err = await token_store.require_service(ctx, "linksquares", level="read")
            if err:
                return err
            pages = paginate_offset(
                client, f"{API}/contracts",
                service="LinkSquares", action="list contracts",
                params={},
                results_key="contracts",
                page_size_param="limit",
                offset_param="offset",
                offset_start=0,
                page_size=min(limit, 100),
            )
            contracts = await collect(pages, limit)
            if not contracts:
                return "No contracts found."
            lines = []
            for c in contracts:
                title = c.get("title", c.get("name", "Untitled"))
                cid = c.get("id", "?")
                status = c.get("status", "?")
                counterparty = c.get("counterparty", c.get("counter_party", "?"))
                effective = c.get("effective_date", "?")
                lines.append(f"{title} (ID: {cid})\n  Counterparty: {counterparty} | Status: {status} | Effective: {effective}")
            return "\n\n".join(lines)

        @mcp.tool()
        async def linksquares_search(query: str, ctx: Context, limit: int = 25) -> str:
            """Search contracts in LinkSquares.

            Args:
                query: Search query (contract title, counterparty, or keyword)
                limit: Max results (default: 25)
            """
            err = validation.validate_query(query, "query")
            if err:
                return err
            limit = validation.validate_limit(limit)
            client, uid, err = await token_store.require_service(ctx, "linksquares", level="read")
            if err:
                return err
            pages = paginate_offset(
                client, f"{API}/contracts/search",
                service="LinkSquares", action="search",
                params={"q": query},
                results_key="contracts",
                page_size_param="limit",
                offset_param="offset",
                offset_start=0,
                page_size=min(limit, 100),
            )
            results = await collect(pages, limit)
            if not results:
                return "No matching contracts found."
            lines = []
            for c in results:
                title = c.get("title", c.get("name", "Untitled"))
                cid = c.get("id", "?")
                counterparty = c.get("counterparty", c.get("counter_party", "?"))
                status = c.get("status", "?")
                lines.append(f"{title} (ID: {cid}) | Counterparty: {counterparty} | Status: {status}")
            return "\n".join(lines)

        @mcp.tool()
        async def linksquares_get_contract(contract_id: str, ctx: Context) -> str:
            """Get full details of a specific contract from LinkSquares.

            Args:
                contract_id: The contract ID
            """
            err = validation.validate_id(contract_id, "contract_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "linksquares", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(client, "GET", f"{API}/contracts/{contract_id}", service="LinkSquares", action="get contract")
            if err:
                return err
            c = r.json()
            title = c.get("title", c.get("name", "Untitled"))
            status = c.get("status", "?")
            counterparty = c.get("counterparty", c.get("counter_party", "?"))
            effective = c.get("effective_date", "?")
            expiration = c.get("expiration_date", "?")
            doc_type = c.get("type", c.get("document_type", "?"))
            return (
                f"{title}\n"
                f"ID: {contract_id}\n"
                f"Status: {status}\n"
                f"Counterparty: {counterparty}\n"
                f"Type: {doc_type}\n"
                f"Effective: {effective}\n"
                f"Expiration: {expiration}"
            )

        @mcp.tool()
        async def linksquares_list_smart_values(contract_id: str, ctx: Context) -> str:
            """List smart values (extracted data) for a contract in LinkSquares.

            Args:
                contract_id: The contract ID
            """
            err = validation.validate_id(contract_id, "contract_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "linksquares", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(client, "GET", f"{API}/contracts/{contract_id}/smart-values", service="LinkSquares", action="list smart values")
            if err:
                return err
            data = r.json()
            values = data.get("smart_values", data.get("smartValues", data.get("data", [])))
            if not values:
                return "No smart values found for this contract."
            lines = []
            for sv in values:
                name = sv.get("name", sv.get("field", "?"))
                val = sv.get("value", "?")
                lines.append(f"{name}: {val}")
            return "\n".join(lines)

        @mcp.tool()
        async def linksquares_list_amendments(contract_id: str, ctx: Context) -> str:
            """List amendments for a contract in LinkSquares.

            Args:
                contract_id: The contract ID
            """
            err = validation.validate_id(contract_id, "contract_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "linksquares", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(client, "GET", f"{API}/contracts/{contract_id}/amendments", service="LinkSquares", action="list amendments")
            if err:
                return err
            data = r.json()
            amendments = data.get("amendments", data.get("data", []))
            if not amendments:
                return "No amendments found for this contract."
            lines = []
            for a in amendments:
                aid = a.get("id", "?")
                title = a.get("title", a.get("name", "Untitled"))
                date = a.get("effective_date", a.get("date", "?"))
                status = a.get("status", "?")
                lines.append(f"{title} (ID: {aid}) | Effective: {date} | Status: {status}")
            return "\n".join(lines)
