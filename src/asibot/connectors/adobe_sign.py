"""Adobe Sign connector: agreements via Adobe Sign REST API."""

import logging

from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store, validation
from asibot.connectors.base import Connector
from asibot.connectors.pagination import collect, paginate_cursor

logger = logging.getLogger(__name__)
API = "https://api.na1.adobesign.com/api/rest/v6"


class AdobeSignConnector(Connector):
    def __init__(self, config=None):
        super().__init__(name="adobe_sign", config=config)

    async def connect(self):
        logger.info("Adobe Sign: ready (per-user OAuth token)")

    async def disconnect(self):
        pass

    async def fetch_documents(self):
        return []

    def register_tools(self, mcp: FastMCP):

        @mcp.tool()
        async def adobe_sign_list_agreements(ctx: Context, limit: int = 20) -> str:
            """List Adobe Sign agreements.

            Args:
                limit: Max results (default: 20)
            """
            client, uid, err = token_store.require_service(ctx, "adobe_sign", level="read")
            if err:
                return err
            pages = paginate_cursor(
                client, f"{API}/agreements",
                method="GET",
                service="Adobe Sign", action="list agreements",
                results_key="userAgreementList",
                cursor_response_key="page.nextCursor",
                cursor_request_key="cursor",
                cursor_in="params",
                page_size_param="pageSize",
                page_size=min(limit, 100),
            )
            agreements = await collect(pages, limit)
            if not agreements:
                return "No agreements found."
            return "\n\n".join(
                f"{a.get('name', 'Untitled')}\n  ID: {a.get('id', '?')} | Status: {a.get('status', '?')} | Modified: {(a.get('lastEventDate') or '?')[:10]}"
                for a in agreements
            )

        @mcp.tool()
        async def adobe_sign_get_agreement(agreement_id: str, ctx: Context) -> str:
            """Get full details of an Adobe Sign agreement.

            Args:
                agreement_id: The agreement ID
            """
            err = validation.validate_id(agreement_id, "agreement_id")
            if err:
                return err
            client, uid, err = token_store.require_service(ctx, "adobe_sign", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(client, "GET", f"{API}/agreements/{agreement_id}", service="Adobe Sign", action="get agreement")
            if err:
                return err
            a = r.json()
            # Get participant info
            participants = []
            for p_set in a.get("participantSetsInfo", []):
                for p in p_set.get("memberInfos", []):
                    participants.append(f"{p.get('email', '?')} ({p_set.get('role', '?')})")
            part_str = ", ".join(participants) if participants else "none"
            return (
                f"{a.get('name', 'Untitled')}\n"
                f"ID: {a.get('id', '?')} | Status: {a.get('status', '?')}\n"
                f"Created: {a.get('createdDate', '?')}\n"
                f"Message: {a.get('message', 'None')}\n"
                f"Participants: {part_str}"
            )
