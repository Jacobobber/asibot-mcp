"""Citrix ShareFile connector: files and folders via ShareFile REST API."""

import logging
import re

from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store, validation
from asibot.connectors.base import Connector

logger = logging.getLogger(__name__)

_VALID_SUBDOMAIN = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?$")


def _api(creds):
    subdomain = creds.get("subdomain", "").strip()
    if not subdomain or not _VALID_SUBDOMAIN.match(subdomain):
        raise ValueError(f"Invalid ShareFile subdomain: {subdomain!r}")
    return f"https://{subdomain}.sf-api.com/sf/v3"


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
            if folder_id and folder_id != "home":
                err = validation.validate_id(folder_id, "folder_id")
                if err:
                    return err
            limit = validation.validate_limit(limit)
            client, uid, err = token_store.require_service(ctx, "sharefile", level="read")
            if err:
                return err
            creds = token_store.get_credentials(uid, "sharefile")
            try:
                base = _api(creds)
            except ValueError as e:
                return str(e)
            r, err = await token_store.safe_request(client, "GET", f"{base}/Items({folder_id})/Children", service="ShareFile", action="list items", params={"$top": limit})
            if err:
                return err
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
            err = validation.validate_query(query, "query")
            if err:
                return err
            limit = validation.validate_limit(limit)
            client, uid, err = token_store.require_service(ctx, "sharefile", level="read")
            if err:
                return err
            creds = token_store.get_credentials(uid, "sharefile")
            try:
                base = _api(creds)
            except ValueError as e:
                return str(e)
            r, err = await token_store.safe_request(client, "GET", f"{base}/Items/Search", service="ShareFile", action="search", params={"query": query, "$top": limit})
            if err:
                return err
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
