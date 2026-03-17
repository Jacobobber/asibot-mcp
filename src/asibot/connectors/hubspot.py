"""HubSpot connector: contacts and deals via HubSpot REST API."""

import logging

import httpx
from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store
from asibot.connectors.base import Connector

logger = logging.getLogger(__name__)
API = "https://api.hubapi.com"


def _make_client(creds):
    if not creds.get("token"):
        return None
    return httpx.AsyncClient(
        headers={"Authorization": f"Bearer {creds['token']}"},
        timeout=30.0,
    )


class HubSpotConnector(Connector):
    def __init__(self, config=None):
        super().__init__(name="hubspot", config=config)

    async def connect(self):
        logger.info("HubSpot: ready (per-user token)")

    async def disconnect(self):
        pass

    async def fetch_documents(self):
        return []

    def register_tools(self, mcp: FastMCP):

        @mcp.tool()
        async def hubspot_search_contacts(query: str, ctx: Context, limit: int = 10) -> str:
            """Search HubSpot contacts.

            Args:
                query: Search query
                limit: Max results (default: 10)
            """
            client, uid, err = token_store.require_service(ctx, "hubspot", _make_client, "read")
            if err:
                return err
            body = {
                "query": query,
                "limit": limit,
                "properties": ["firstname", "lastname", "email", "company", "phone"],
            }
            r = await client.post(f"{API}/crm/v3/objects/contacts/search", json=body)
            r.raise_for_status()
            results = r.json().get("results", [])
            if not results:
                return "No contacts found."
            lines = []
            for c in results:
                props = c.get("properties", {})
                name = f"{props.get('firstname', '')} {props.get('lastname', '')}".strip() or "Unknown"
                email = props.get("email", "no email")
                company = props.get("company", "no company")
                lines.append(f"{name} ({email})\n  Company: {company} | ID: {c.get('id', '?')}")
            return "\n\n".join(lines)

        @mcp.tool()
        async def hubspot_search_deals(query: str, ctx: Context, limit: int = 10) -> str:
            """Search HubSpot deals.

            Args:
                query: Search query
                limit: Max results (default: 10)
            """
            client, uid, err = token_store.require_service(ctx, "hubspot", _make_client, "read")
            if err:
                return err
            body = {
                "query": query,
                "limit": limit,
                "properties": ["dealname", "dealstage", "amount", "closedate", "pipeline"],
            }
            r = await client.post(f"{API}/crm/v3/objects/deals/search", json=body)
            r.raise_for_status()
            results = r.json().get("results", [])
            if not results:
                return "No deals found."
            lines = []
            for d in results:
                props = d.get("properties", {})
                name = props.get("dealname", "Untitled")
                stage = props.get("dealstage", "?")
                amount = props.get("amount", "?")
                close = props.get("closedate", "?")
                lines.append(f"{name}\n  Stage: {stage} | Amount: {amount} | Close: {close[:10] if close and close != '?' else close} | ID: {d.get('id', '?')}")
            return "\n\n".join(lines)

        @mcp.tool()
        async def hubspot_get_contact(contact_id: str, ctx: Context) -> str:
            """Get full details of a HubSpot contact.

            Args:
                contact_id: The contact ID
            """
            client, uid, err = token_store.require_service(ctx, "hubspot", _make_client, "read")
            if err:
                return err
            r = await client.get(
                f"{API}/crm/v3/objects/contacts/{contact_id}",
                params={"properties": "firstname,lastname,email,company,phone,jobtitle,lifecyclestage"},
            )
            r.raise_for_status()
            props = r.json().get("properties", {})
            name = f"{props.get('firstname', '')} {props.get('lastname', '')}".strip() or "Unknown"
            return (
                f"{name}\n"
                f"  Email: {props.get('email', '?')}\n"
                f"  Phone: {props.get('phone', '?')}\n"
                f"  Company: {props.get('company', '?')}\n"
                f"  Title: {props.get('jobtitle', '?')}\n"
                f"  Lifecycle: {props.get('lifecyclestage', '?')}"
            )

        @mcp.tool()
        async def hubspot_get_deal(deal_id: str, ctx: Context) -> str:
            """Get full details of a HubSpot deal.

            Args:
                deal_id: The deal ID
            """
            client, uid, err = token_store.require_service(ctx, "hubspot", _make_client, "read")
            if err:
                return err
            r = await client.get(
                f"{API}/crm/v3/objects/deals/{deal_id}",
                params={"properties": "dealname,dealstage,amount,closedate,pipeline,hubspot_owner_id,description"},
            )
            r.raise_for_status()
            props = r.json().get("properties", {})
            return (
                f"{props.get('dealname', 'Untitled')}\n"
                f"  Stage: {props.get('dealstage', '?')}\n"
                f"  Amount: {props.get('amount', '?')}\n"
                f"  Close date: {props.get('closedate', '?')}\n"
                f"  Pipeline: {props.get('pipeline', '?')}\n"
                f"  Owner ID: {props.get('hubspot_owner_id', '?')}\n"
                f"  Description: {props.get('description', 'No description')}"
            )
