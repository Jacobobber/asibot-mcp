"""Citrix ShareFile connector: files and folders via ShareFile REST API."""

import logging
import re

from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store, validation
from asibot.connectors.base import Connector
from asibot.connectors.pagination import collect, paginate_odata

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
            client, uid, err = await token_store.require_service(ctx, "sharefile", level="read")
            if err:
                return err
            creds = token_store.get_credentials(uid, "sharefile")
            try:
                base = _api(creds)
            except ValueError as e:
                return str(e)
            pages = paginate_odata(
                client, f"{base}/Items({folder_id})/Children",
                service="ShareFile", action="list items",
                params={"$top": min(limit, 100)},
            )
            items = await collect(pages, limit)
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
            client, uid, err = await token_store.require_service(ctx, "sharefile", level="read")
            if err:
                return err
            creds = token_store.get_credentials(uid, "sharefile")
            try:
                base = _api(creds)
            except ValueError as e:
                return str(e)
            pages = paginate_odata(
                client, f"{base}/Items/Search",
                service="ShareFile", action="search",
                params={"query": query, "$top": min(limit, 100)},
            )
            results = await collect(pages, limit)
            if not results:
                return "No results found."
            lines = []
            for item in results:
                name = item.get("FileName", item.get("Name", "?"))
                parent = item.get("ParentName", "?")
                size = item.get("FileSizeBytes", 0)
                lines.append(f"{name} | In: {parent} | {size} bytes")
            return "\n".join(lines)

        @mcp.tool()
        async def sharefile_get_item(item_id: str, ctx: Context) -> str:
            """Get details of a specific ShareFile item (file or folder).

            Args:
                item_id: The item ID
            """
            err = validation.validate_id(item_id, "item_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "sharefile", level="read")
            if err:
                return err
            creds = token_store.get_credentials(uid, "sharefile")
            try:
                base = _api(creds)
            except ValueError as e:
                return str(e)
            r, err = await token_store.safe_request(client, "GET", f"{base}/Items({item_id})", service="ShareFile", action="get item")
            if err:
                return err
            item = r.json()
            name = item.get("FileName", item.get("Name", "?"))
            itype = "Folder" if item.get("odata.type", "").endswith("Folder") else "File"
            size = item.get("FileSizeBytes", 0)
            created = item.get("CreationDate", "?")[:10] if item.get("CreationDate") else "?"
            creator = item.get("CreatorNameShort", item.get("CreatedBy", "?"))
            parent = item.get("ParentName", "?")
            return f"{name}\nType: {itype}\nSize: {size} bytes\nCreated: {created}\nCreator: {creator}\nParent: {parent}"

        @mcp.tool()
        async def sharefile_download_text(item_id: str, ctx: Context) -> str:
            """Download and return the text content of a ShareFile item. Only works for text files under 1MB.

            Args:
                item_id: The item ID
            """
            err = validation.validate_id(item_id, "item_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "sharefile", level="read")
            if err:
                return err
            creds = token_store.get_credentials(uid, "sharefile")
            try:
                base = _api(creds)
            except ValueError as e:
                return str(e)
            r, err = await token_store.safe_request(client, "GET", f"{base}/Items({item_id})/Download", service="ShareFile", action="download text")
            if err:
                return err
            content_type = r.headers.get("Content-Type", "") if hasattr(r, "headers") else ""
            if content_type and "text" not in content_type and "json" not in content_type and "xml" not in content_type:
                return f"Cannot display binary file (Content-Type: {content_type}). Only text files are supported."
            text = r.text if hasattr(r, "text") else str(r.content if hasattr(r, "content") else r)
            if len(text) > 1_048_576:
                return "File is too large to display (exceeds 1MB limit)."
            return text

        @mcp.tool()
        async def sharefile_list_shared(ctx: Context, limit: int = 25) -> str:
            """List shared items/links from ShareFile.

            Args:
                limit: Max results (default: 25)
            """
            limit = validation.validate_limit(limit)
            client, uid, err = await token_store.require_service(ctx, "sharefile", level="read")
            if err:
                return err
            creds = token_store.get_credentials(uid, "sharefile")
            try:
                base = _api(creds)
            except ValueError as e:
                return str(e)
            r, err = await token_store.safe_request(client, "GET", f"{base}/Shares", service="ShareFile", action="list shared", params={"$top": limit})
            if err:
                return err
            shares = r.json().get("value", [])
            if not shares:
                return "No shared items found."
            lines = []
            for s in shares:
                name = s.get("FileName", s.get("Name", s.get("Title", "?")))
                share_id = s.get("Id", s.get("id", "?"))
                created = s.get("CreationDate", "?")[:10] if s.get("CreationDate") else "?"
                lines.append(f"{name} (ID: {share_id}) | Created: {created}")
            return "\n".join(lines)

        @mcp.tool()
        async def sharefile_upload_file(parent_id: str, filename: str, content: str, ctx: Context) -> str:
            """Upload a file to a ShareFile folder.

            Args:
                parent_id: The parent folder ID to upload into
                filename: Name of the file to create
                content: Text content for the file
            """
            err = validation.validate_id(parent_id, "parent_id")
            if err:
                return err
            err = validation.validate_content(filename, "filename")
            if err:
                return err
            err = validation.validate_content(content, "content")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "sharefile", level="write")
            if err:
                return err
            creds = token_store.get_credentials(uid, "sharefile")
            try:
                base = _api(creds)
            except ValueError as e:
                return str(e)
            r, err = await token_store.safe_request(
                client, "POST", f"{base}/Items({parent_id})/Upload",
                service="ShareFile", action="upload file",
                json={"FileName": filename, "Content": content},
            )
            if err:
                return err
            data = r.json()
            fid = data.get("Id", data.get("id", "?"))
            return f"Uploaded '{filename}' (ID: {fid}) to folder {parent_id}."

        @mcp.tool()
        async def sharefile_create_folder(parent_id: str, name: str, ctx: Context, description: str = "") -> str:
            """Create a new folder in ShareFile.

            Args:
                parent_id: The parent folder ID
                name: Name for the new folder
                description: Optional folder description
            """
            err = validation.validate_id(parent_id, "parent_id")
            if err:
                return err
            err = validation.validate_content(name, "name")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "sharefile", level="write")
            if err:
                return err
            creds = token_store.get_credentials(uid, "sharefile")
            try:
                base = _api(creds)
            except ValueError as e:
                return str(e)
            payload = {"Name": name, "Description": description}
            r, err = await token_store.safe_request(
                client, "POST", f"{base}/Items({parent_id})/Folder",
                service="ShareFile", action="create folder",
                json=payload,
            )
            if err:
                return err
            data = r.json()
            fid = data.get("Id", data.get("id", "?"))
            return f"Created folder '{name}' (ID: {fid}) under {parent_id}."

        @mcp.tool()
        async def sharefile_delete_item(item_id: str, ctx: Context) -> str:
            """Delete a file or folder from ShareFile.

            Args:
                item_id: The item ID to delete
            """
            err = validation.validate_id(item_id, "item_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "sharefile", level="write")
            if err:
                return err
            creds = token_store.get_credentials(uid, "sharefile")
            try:
                base = _api(creds)
            except ValueError as e:
                return str(e)
            r, err = await token_store.safe_request(
                client, "DELETE", f"{base}/Items({item_id})",
                service="ShareFile", action="delete item",
            )
            if err:
                return err
            return f"Deleted item {item_id}."

        @mcp.tool()
        async def sharefile_create_share(item_id: str, emails: str, ctx: Context, expiration_days: int = 30) -> str:
            """Create a share link for a ShareFile item and send to specified emails.

            Args:
                item_id: The item ID to share
                emails: Comma-separated email addresses
                expiration_days: Number of days until link expires (default: 30)
            """
            err = validation.validate_id(item_id, "item_id")
            if err:
                return err
            err = validation.validate_content(emails, "emails")
            if err:
                return err
            email_list = [e.strip() for e in emails.split(",") if e.strip()]
            for email in email_list:
                err = validation.validate_email_address(email)
                if err:
                    return err
            client, uid, err = await token_store.require_service(ctx, "sharefile", level="write")
            if err:
                return err
            creds = token_store.get_credentials(uid, "sharefile")
            try:
                base = _api(creds)
            except ValueError as e:
                return str(e)
            payload = {
                "Items": [{"Id": item_id}],
                "Recipients": [{"User": {"Email": e}} for e in email_list],
                "ExpirationDays": expiration_days,
            }
            r, err = await token_store.safe_request(
                client, "POST", f"{base}/Shares",
                service="ShareFile", action="create share",
                json=payload,
            )
            if err:
                return err
            data = r.json()
            share_id = data.get("Id", data.get("id", "?"))
            uri = data.get("Uri", data.get("uri", ""))
            return f"Share created (ID: {share_id}) for {', '.join(email_list)}. Expires in {expiration_days} days.{f' Link: {uri}' if uri else ''}"
