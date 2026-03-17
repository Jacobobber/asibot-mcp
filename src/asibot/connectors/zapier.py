"""Zapier NLA connector: list and run actions via Zapier Natural Language API."""

import logging

import httpx
from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store
from asibot.connectors.base import Connector

logger = logging.getLogger(__name__)
API = "https://nla.zapier.com/api/v1"


def _make_client(creds):
    if not creds.get("api_key"):
        return None
    return httpx.AsyncClient(
        headers={"X-API-Key": creds["api_key"]},
        timeout=30.0,
    )


class ZapierConnector(Connector):
    def __init__(self, config=None):
        super().__init__(name="zapier", config=config)

    async def connect(self):
        logger.info("Zapier: ready (per-user NLA API key)")

    async def disconnect(self):
        pass

    async def fetch_documents(self):
        return []

    def register_tools(self, mcp: FastMCP):

        @mcp.tool()
        async def zapier_list_actions(ctx: Context) -> str:
            """List all available Zapier NLA actions configured for your account."""
            client, uid, err = token_store.require_service(ctx, "zapier", _make_client, "read")
            if err:
                return err
            r = await client.get(f"{API}/exposed/")
            r.raise_for_status()
            results = r.json().get("results", [])
            if not results:
                return "No Zapier actions configured. Set up actions at https://nla.zapier.com/."
            return "\n\n".join(
                f"{a.get('description', 'No description')}\n  ID: {a['id']} | App: {a.get('params', {}).get('app', '?')}"
                for a in results
            )

        @mcp.tool()
        async def zapier_run_action(action_id: str, instructions: str, ctx: Context) -> str:
            """Run a Zapier NLA action with natural language instructions.

            Args:
                action_id: The action ID from zapier_list_actions
                instructions: Natural language instructions for the action
            """
            client, uid, err = token_store.require_service(ctx, "zapier", _make_client, "write")
            if err:
                return err
            r = await client.post(
                f"{API}/exposed/{action_id}/execute/",
                json={"instructions": instructions},
            )
            r.raise_for_status()
            data = r.json()
            status = data.get("status", "unknown")
            result = data.get("result", {})
            if status == "success":
                return f"Action executed successfully.\n\nResult: {result}"
            return f"Action status: {status}\n\nDetails: {data}"
