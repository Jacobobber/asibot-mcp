"""Adobe Sign connector: agreements via Adobe Sign REST API."""

import logging

import httpx
from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store
from asibot.connectors.base import Connector

logger = logging.getLogger(__name__)
API = "https://api.na1.adobesign.com/api/rest/v6"


def _make_client(creds):
    if not creds.get("token"):
        return None
    return httpx.AsyncClient(
        headers={"Authorization": f"Bearer {creds['token']}"},
        timeout=30.0,
    )


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
            client, uid, err = token_store.require_service(ctx, "adobe_sign", _make_client, "read")
            if err:
                return err
            r = await client.get(
                f"{API}/agreements",
                params={"pageSize": limit},
            )
            r.raise_for_status()
            agreements = r.json().get("userAgreementList", [])
            if not agreements:
                return "No agreements found."
            return "\n\n".join(
                f"{a.get('name', 'Untitled')}\n  ID: {a['id']} | Status: {a.get('status', '?')} | Modified: {a.get('lastEventDate', '?')[:10]}"
                for a in agreements
            )

        @mcp.tool()
        async def adobe_sign_get_agreement(agreement_id: str, ctx: Context) -> str:
            """Get full details of an Adobe Sign agreement.

            Args:
                agreement_id: The agreement ID
            """
            client, uid, err = token_store.require_service(ctx, "adobe_sign", _make_client, "read")
            if err:
                return err
            r = await client.get(f"{API}/agreements/{agreement_id}")
            r.raise_for_status()
            a = r.json()
            # Get participant info
            participants = []
            for p_set in a.get("participantSetsInfo", []):
                for p in p_set.get("memberInfos", []):
                    participants.append(f"{p.get('email', '?')} ({p_set.get('role', '?')})")
            part_str = ", ".join(participants) if participants else "none"
            return (
                f"{a.get('name', 'Untitled')}\n"
                f"ID: {a['id']} | Status: {a.get('status', '?')}\n"
                f"Created: {a.get('createdDate', '?')}\n"
                f"Message: {a.get('message', 'None')}\n"
                f"Participants: {part_str}"
            )
