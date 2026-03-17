"""Citrix ShareFile connector: files and folders via ShareFile REST API."""

import logging

import httpx
from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store
from asibot.connectors.base import Connector

logger = logging.getLogger(__name__)


def _api(creds):
    return f"https://{creds['subdomain']}.sf-api.com/sf/v3"


def _make_client(creds):
    if not creds.get("token") or not creds.get("subdomain"):
        return None
    return httpx.AsyncClient(
        headers={"Authorization": f"Bearer {creds['token']}", "Accept": "application/json"},
        timeout=30.0,
    )


class ShareFileConnector(Connector):
    def __init__(self, config=None):
        super().__init__(name="sharefile", config=config)

    async def connect(self):
        logger.info("ShareFile: ready (per-user OAuth token)")

    async def disconnect(self):
        pass

    async def fetch_documents(self):
        return []

    def register_tools(self, mcp: FastMCP):

        @mcp.tool()
        async def sharefile_list_items(ctx: Context, folder_id: str = "home", limit: int = 25) -> str:
            """List items in a ShareFile folder.

            Args:
                folder_id: Folder ID to list (default: 'home' for root)
                limit: Max results (default: 25)
            """
            client, uid, err = token_store.require_service(ctx, "sharefile", _make_client, "read")
            if err:
                return err
            creds = token_store.get_credentials(uid, "sharefile")
            base = _api(creds)
            r = await client.get(f"{base}/Items({folder_id})/Children", params={"$top": limit})
            r.raise_for_status()
            items = r.json().get("value", [])
            if not items:
                return "No items found in this folder."
            lines = []
            for item in items:
                name = item.get("FileName", item.get("Name", "?"))
                itype = "Folder" if item.get("odata.type", "").endswith("Folder") else "File"
                size = item.get("FileSizeBytes", 0)
                created = item.get("CreationDate", "?")[:10] if item.get("CreationDate") else "?"
                lines.append(f"{name} | {itype} | {size} bytes | Created: {created}")
            return "\n".join(lines)

        @mcp.tool()
        async def sharefile_search(query: str, ctx: Context, limit: int = 25) -> str:
            """Search for files and folders in ShareFile.

            Args:
                query: Search query
                limit: Max results (default: 25)
            """
            client, uid, err = token_store.require_service(ctx, "sharefile", _make_client, "read")
            if err:
                return err
            creds = token_store.get_credentials(uid, "sharefile")
            base = _api(creds)
            r = await client.get(f"{base}/Items/Search", params={"query": query, "$top": limit})
            r.raise_for_status()
            results = r.json().get("value", r.json().get("Results", []))
            if not results:
                return "No results found."
            lines = []
            for item in results:
                name = item.get("FileName", item.get("Name", "?"))
                parent = item.get("ParentName", "?")
                size = item.get("FileSizeBytes", 0)
                lines.append(f"{name} | In: {parent} | {size} bytes")
            return "\n".join(lines)
