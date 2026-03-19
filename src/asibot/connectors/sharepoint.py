"""SharePoint connector via Microsoft Graph API. Per-user SSO auth."""

import base64
import io
import logging
import re
from urllib.parse import quote

import httpx
from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store, validation
from asibot.config import settings
from asibot.connectors import microsoft
from asibot.connectors.base import Connector
from asibot.connectors.pagination import collect, paginate_odata

logger = logging.getLogger(__name__)
GRAPH = microsoft.GRAPH_BASE


class SharePointConnector(Connector):
    def __init__(self, config=None):
        super().__init__(name="sharepoint", config=config)
        self.site_url = (config or {}).get("site_url") or settings.sharepoint_site_url
        self._user_site_ids: dict[str, str] = {}

    async def connect(self):
        logger.info("SharePoint: ready (Microsoft SSO)")

    async def disconnect(self):
        self._user_site_ids.clear()

    async def fetch_documents(self):
        return []

    async def _resolve_site(self, uid, client):
        if uid in self._user_site_ids:
            return self._user_site_ids[uid]
        if not self.site_url:
            return None
        try:
            r = await client.get(f"{GRAPH}/sites/{self.site_url}")
            r.raise_for_status()
            data = r.json()
            sid = data.get("id")
            if not sid:
                logger.warning("SharePoint site response missing 'id' for %s", self.site_url)
                return None
            self._user_site_ids[uid] = sid
            return sid
        except httpx.HTTPStatusError:
            logger.warning("SharePoint: failed to resolve site %s", self.site_url)
            return None
        except (httpx.RequestError, KeyError):
            logger.warning("SharePoint: request error resolving site %s", self.site_url)
            return None

    async def _extract_text(self, client, item):
        mime = item.get("file", {}).get("mimeType", "")
        name = item.get("name", "")
        if item.get("size", 0) > 10_000_000:
            return None
        url = item.get("@microsoft.graph.downloadUrl")
        if not url:
            return None
        if mime in ("text/plain", "text/csv", "text/markdown") or name.endswith((".txt", ".md", ".csv")):
            try:
                r = await client.get(url)
                r.raise_for_status()
                return r.text
            except (httpx.HTTPStatusError, httpx.RequestError):
                logger.warning("SharePoint: failed to download text file %s", name)
                return None
        if mime == "application/pdf" or name.endswith(".pdf"):
            try:
                import pymupdf
                r = await client.get(url)
                r.raise_for_status()
                d = pymupdf.open(stream=r.content, filetype="pdf")
                t = "\n".join(p.get_text() for p in d)
                d.close()
                return t
            except (httpx.HTTPStatusError, httpx.RequestError):
                logger.warning("SharePoint: failed to download PDF %s", name)
                return None
            except (RuntimeError, ValueError) as e:
                logger.warning("SharePoint: failed to parse PDF %s: %s", name, e)
                return None
        if "wordprocessingml" in mime or name.endswith(".docx"):
            try:
                from docx import Document as DocxDoc
                r = await client.get(url)
                r.raise_for_status()
                d = DocxDoc(io.BytesIO(r.content))
                return "\n".join(p.text for p in d.paragraphs)
            except (httpx.HTTPStatusError, httpx.RequestError):
                logger.warning("SharePoint: failed to download DOCX %s", name)
                return None
            except (ValueError, KeyError) as e:
                logger.warning("SharePoint: failed to parse DOCX %s: %s", name, e)
                return None
        return None

    def register_tools(self, mcp: FastMCP):
        if not all([settings.ms365_tenant_id, settings.ms365_client_id]):
            return
        conn = self

        @mcp.tool()
        async def sharepoint_search(query: str, ctx: Context, limit: int = 10) -> str:
            """Search SharePoint for files and documents.

            Args:
                query: Search query
                limit: Max results (default: 10)
            """
            err = validation.validate_query(query, "query")
            if err:
                return err
            limit = validation.validate_limit(limit)
            client, uid, err = await microsoft.require_graph_client(ctx, "sharepoint", "read")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "POST", f"{GRAPH}/search/query",
                service="SharePoint", action="search",
                json={"requests": [{"entityTypes": ["driveItem"], "query": {"queryString": query}, "from": 0, "size": limit}]},
            )
            if err:
                return err
            hits = r.json().get("value", [{}])[0].get("hitsContainers", [{}])[0].get("hits", [])
            if not hits:
                return "No results found."
            lines = []
            for h in hits:
                res = h.get("resource", {})
                summary = re.sub(r"<[^>]+>", "", h.get("summary", "")).strip()
                lines.append(f"{res.get('name', '?')}\n  URL: {res.get('webUrl', '')}\n  Preview: {summary[:200]}")
            return "\n\n".join(lines)

        @mcp.tool()
        async def sharepoint_list_files(ctx: Context, folder_path: str = "", site: str = "") -> str:
            """List files in a SharePoint folder.

            Args:
                folder_path: Path within drive. Empty for root.
                site: Site ID. Leave empty for default.
            """
            client, uid, err = await microsoft.require_graph_client(ctx, "sharepoint", "read")
            if err:
                return err
            sid = site or await conn._resolve_site(uid, client)
            if not sid:
                return "No site configured."
            safe_path = quote(folder_path, safe="/")
            url = f"{GRAPH}/sites/{sid}/drive/root:/{safe_path}:/children" if folder_path else f"{GRAPH}/sites/{sid}/drive/root/children"
            pages = paginate_odata(
                client, url,
                service="SharePoint", action="list files",
            )
            items = await collect(pages, 200)
            if not items:
                return "No files found."
            return "\n".join(f"{'[folder]' if 'folder' in i else '[file]'} {i['name']}  ({i.get('size', 0):,} bytes)" for i in items)

        @mcp.tool()
        async def sharepoint_read_file(file_path: str, ctx: Context, site: str = "") -> str:
            """Read text content of a SharePoint file (.txt, .md, .csv, .pdf, .docx).

            Args:
                file_path: Path to file
                site: Site ID. Leave empty for default.
            """
            client, uid, err = await microsoft.require_graph_client(ctx, "sharepoint", "read")
            if err:
                return err
            sid = site or await conn._resolve_site(uid, client)
            if not sid:
                return "No site configured."
            safe_path = quote(file_path, safe="/")
            r, err = await token_store.safe_request(client, "GET", f"{GRAPH}/sites/{sid}/drive/root:/{safe_path}", service="SharePoint", action="read file")
            if err:
                return err
            item = r.json()
            text = await conn._extract_text(client, item)
            if not text:
                return f"Could not extract text from {file_path}."
            return f"--- {item['name']} ---\n\n{text}"

        @mcp.tool()
        async def sharepoint_get_file_info(file_path: str, ctx: Context, site: str = "") -> str:
            """Get metadata about a SharePoint file (size, author, modified date, sharing).

            Args:
                file_path: Path to file
                site: Site ID. Leave empty for default.
            """
            client, uid, err = await microsoft.require_graph_client(ctx, "sharepoint", "read")
            if err:
                return err
            sid = site or await conn._resolve_site(uid, client)
            if not sid:
                return "No site configured."
            safe_path = quote(file_path, safe="/")
            r, err = await token_store.safe_request(
                client, "GET",
                f"{GRAPH}/sites/{sid}/drive/root:/{safe_path}",
                service="SharePoint", action="get file info",
                params={"$select": "id,name,size,createdBy,lastModifiedBy,lastModifiedDateTime,webUrl,shared,file"},
            )
            if err:
                return err
            item = r.json()
            created_by = item.get("createdBy", {}).get("user", {}).get("displayName", "?")
            modified_by = item.get("lastModifiedBy", {}).get("user", {}).get("displayName", "?")
            lines = [
                f"Name: {item.get('name', '?')}",
                f"ID: {item.get('id', '?')}",
                f"Size: {item.get('size', 0):,} bytes",
                f"Created by: {created_by}",
                f"Modified by: {modified_by}",
                f"Modified: {item.get('lastModifiedDateTime', '?')}",
                f"URL: {item.get('webUrl', '?')}",
            ]
            mime = item.get("file", {}).get("mimeType", "")
            if mime:
                lines.append(f"Type: {mime}")
            if item.get("shared"):
                lines.append(f"Shared: {item['shared'].get('scope', 'unknown')}")
            return "\n".join(lines)

        @mcp.tool()
        async def sharepoint_list_versions(file_path: str, ctx: Context, site: str = "") -> str:
            """List version history of a SharePoint file.

            Args:
                file_path: Path to file
                site: Site ID. Leave empty for default.
            """
            client, uid, err = await microsoft.require_graph_client(ctx, "sharepoint", "read")
            if err:
                return err
            sid = site or await conn._resolve_site(uid, client)
            if not sid:
                return "No site configured."
            safe_path = quote(file_path, safe="/")
            r, err = await token_store.safe_request(
                client, "GET",
                f"{GRAPH}/sites/{sid}/drive/root:/{safe_path}:/versions",
                service="SharePoint", action="list versions",
            )
            if err:
                return err
            versions = r.json().get("value", [])
            if not versions:
                return "No versions found."
            lines = []
            for v in versions:
                modified_by = v.get("lastModifiedBy", {}).get("user", {}).get("displayName", "?")
                lines.append(f"Version {v.get('id', '?')}\n  Modified: {v.get('lastModifiedDateTime', '?')}\n  By: {modified_by}")
            return "\n\n".join(lines)

        @mcp.tool()
        async def sharepoint_list_sites(ctx: Context, query: str = "") -> str:
            """List or search SharePoint sites.

            Args:
                query: Search term. Empty to list all.
            """
            client, uid, err = await microsoft.require_graph_client(ctx, "sharepoint", "read")
            if err:
                return err
            pages = paginate_odata(
                client, f"{GRAPH}/sites",
                service="SharePoint", action="list sites",
                params={"search": query or "*"},
            )
            sites = await collect(pages, 200)
            if not sites:
                return "No sites found."
            return "\n\n".join(f"{s.get('displayName', '?')}\n  URL: {s.get('webUrl', '')}\n  ID: {s.get('id', '')}" for s in sites)

        @mcp.tool()
        async def sharepoint_upload_file(folder_path: str, filename: str, content: str, ctx: Context, site: str = "") -> str:
            """Upload a file to a SharePoint folder.

            Args:
                folder_path: Destination folder path within drive
                filename: Name for the uploaded file
                content: Base64-encoded file content
                site: Site ID. Leave empty for default.
            """
            err = validation.validate_content(folder_path, "folder_path")
            if err:
                return err
            err = validation.validate_content(filename, "filename")
            if err:
                return err
            err = validation.validate_content(content, "content")
            if err:
                return err
            client, uid, err = await microsoft.require_graph_client(ctx, "sharepoint", "write")
            if err:
                return err
            sid = site or await conn._resolve_site(uid, client)
            if not sid:
                return "No site configured."
            try:
                file_bytes = base64.b64decode(content)
            except Exception:
                file_bytes = content.encode("utf-8")
            safe_folder = quote(folder_path, safe="/")
            safe_name = quote(filename, safe="")
            r, err = await token_store.safe_request(
                client, "PUT",
                f"{GRAPH}/sites/{sid}/drive/root:/{safe_folder}/{safe_name}:/content",
                service="SharePoint", action="upload file",
                content=file_bytes,
                headers={"Content-Type": "application/octet-stream"},
            )
            if err:
                return err
            item = r.json()
            return f"File uploaded. ID: {item.get('id', '?')} | Name: {item.get('name', '?')} | URL: {item.get('webUrl', '?')}"

        @mcp.tool()
        async def sharepoint_create_folder(parent_path: str, folder_name: str, ctx: Context, site: str = "") -> str:
            """Create a folder in SharePoint.

            Args:
                parent_path: Parent folder path (empty for root)
                folder_name: Name of the new folder
                site: Site ID. Leave empty for default.
            """
            err = validation.validate_content(folder_name, "folder_name")
            if err:
                return err
            client, uid, err = await microsoft.require_graph_client(ctx, "sharepoint", "write")
            if err:
                return err
            sid = site or await conn._resolve_site(uid, client)
            if not sid:
                return "No site configured."
            safe_parent = quote(parent_path, safe="/")
            base_url = f"{GRAPH}/sites/{sid}/drive/root:/{safe_parent}:/children" if parent_path else f"{GRAPH}/sites/{sid}/drive/root/children"
            r, err = await token_store.safe_request(
                client, "POST", base_url,
                service="SharePoint", action="create folder",
                json={"name": folder_name, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"},
            )
            if err:
                return err
            item = r.json()
            return f"Folder created. ID: {item.get('id', '?')} | Name: {item.get('name', '?')}"

        @mcp.tool()
        async def sharepoint_delete_item(item_id: str, ctx: Context, site: str = "") -> str:
            """Delete a file or folder in SharePoint.

            Args:
                item_id: ID of the item to delete
                site: Site ID. Leave empty for default.
            """
            err = validation.validate_id(item_id, "item_id")
            if err:
                return err
            client, uid, err = await microsoft.require_graph_client(ctx, "sharepoint", "write")
            if err:
                return err
            sid = site or await conn._resolve_site(uid, client)
            if not sid:
                return "No site configured."
            r, err = await token_store.safe_request(
                client, "DELETE",
                f"{GRAPH}/sites/{sid}/drive/items/{item_id}",
                service="SharePoint", action="delete item",
            )
            if err:
                return err
            return f"Item {item_id} deleted."

        @mcp.tool()
        async def sharepoint_move_item(item_id: str, destination_path: str, ctx: Context, site: str = "") -> str:
            """Move a file or folder to a new location in SharePoint.

            Args:
                item_id: ID of the item to move
                destination_path: Destination folder path
                site: Site ID. Leave empty for default.
            """
            err = validation.validate_id(item_id, "item_id")
            if err:
                return err
            err = validation.validate_content(destination_path, "destination_path")
            if err:
                return err
            client, uid, err = await microsoft.require_graph_client(ctx, "sharepoint", "write")
            if err:
                return err
            sid = site or await conn._resolve_site(uid, client)
            if not sid:
                return "No site configured."
            safe_dest = quote(destination_path, safe="/")
            # Resolve destination folder to get its driveItem ID
            dest_r, dest_err = await token_store.safe_request(
                client, "GET",
                f"{GRAPH}/sites/{sid}/drive/root:/{safe_dest}",
                service="SharePoint", action="resolve destination",
            )
            if dest_err:
                return dest_err
            dest_id = dest_r.json().get("id")
            r, err = await token_store.safe_request(
                client, "PATCH",
                f"{GRAPH}/sites/{sid}/drive/items/{item_id}",
                service="SharePoint", action="move item",
                json={"parentReference": {"id": dest_id}},
            )
            if err:
                return err
            item = r.json()
            return f"Item moved. ID: {item.get('id', '?')} | Name: {item.get('name', '?')}"

        @mcp.tool()
        async def sharepoint_copy_item(item_id: str, destination_path: str, ctx: Context, site: str = "") -> str:
            """Copy a file or folder in SharePoint.

            Args:
                item_id: ID of the item to copy
                destination_path: Destination folder path
                site: Site ID. Leave empty for default.
            """
            err = validation.validate_id(item_id, "item_id")
            if err:
                return err
            err = validation.validate_content(destination_path, "destination_path")
            if err:
                return err
            client, uid, err = await microsoft.require_graph_client(ctx, "sharepoint", "write")
            if err:
                return err
            sid = site or await conn._resolve_site(uid, client)
            if not sid:
                return "No site configured."
            safe_dest = quote(destination_path, safe="/")
            # Resolve destination folder
            dest_r, dest_err = await token_store.safe_request(
                client, "GET",
                f"{GRAPH}/sites/{sid}/drive/root:/{safe_dest}",
                service="SharePoint", action="resolve destination",
            )
            if dest_err:
                return dest_err
            dest_id = dest_r.json().get("id")
            drive_id = dest_r.json().get("parentReference", {}).get("driveId")
            body = {"parentReference": {"id": dest_id}}
            if drive_id:
                body["parentReference"]["driveId"] = drive_id
            r, err = await token_store.safe_request(
                client, "POST",
                f"{GRAPH}/sites/{sid}/drive/items/{item_id}/copy",
                service="SharePoint", action="copy item",
                json=body,
            )
            if err:
                return err
            return f"Copy initiated for item {item_id}."

        @mcp.tool()
        async def sharepoint_list_lists(site_id: str, ctx: Context) -> str:
            """List SharePoint lists on a site.

            Args:
                site_id: Site ID
            """
            err = validation.validate_id(site_id, "site_id")
            if err:
                return err
            client, uid, err = await microsoft.require_graph_client(ctx, "sharepoint", "read")
            if err:
                return err
            pages = paginate_odata(
                client, f"{GRAPH}/sites/{site_id}/lists",
                service="SharePoint", action="list lists",
            )
            lists = await collect(pages, 200)
            if not lists:
                return "No lists found."
            return "\n\n".join(
                f"{l.get('displayName', '?')}\n  ID: {l.get('id', '?')} | Items: {l.get('list', {}).get('contentTypesEnabled', '?')}"
                for l in lists
            )

        @mcp.tool()
        async def sharepoint_get_list_items(site_id: str, list_id: str, ctx: Context, limit: int = 20) -> str:
            """Get items from a SharePoint list.

            Args:
                site_id: Site ID
                list_id: List ID
                limit: Max results (default: 20)
            """
            err = validation.validate_id(site_id, "site_id")
            if err:
                return err
            err = validation.validate_id(list_id, "list_id")
            if err:
                return err
            limit = validation.validate_limit(limit)
            client, uid, err = await microsoft.require_graph_client(ctx, "sharepoint", "read")
            if err:
                return err
            pages = paginate_odata(
                client, f"{GRAPH}/sites/{site_id}/lists/{list_id}/items",
                service="SharePoint", action="get list items",
                params={"expand": "fields", "$top": str(limit)},
            )
            items = await collect(pages, limit)
            if not items:
                return "No items found."
            lines = []
            for item in items:
                fields = item.get("fields", {})
                title = fields.get("Title", fields.get("Name", item.get("id", "?")))
                field_str = " | ".join(f"{k}: {v}" for k, v in list(fields.items())[:5])
                lines.append(f"{title}\n  ID: {item.get('id', '?')} | {field_str}")
            return "\n\n".join(lines)

        @mcp.tool()
        async def sharepoint_create_list_item(site_id: str, list_id: str, fields: str, ctx: Context) -> str:
            """Create a new item in a SharePoint list.

            Args:
                site_id: Site ID
                list_id: List ID
                fields: JSON string of field name-value pairs (e.g. '{"Title": "New Item"}')
            """
            err = validation.validate_id(site_id, "site_id")
            if err:
                return err
            err = validation.validate_id(list_id, "list_id")
            if err:
                return err
            err = validation.validate_content(fields, "fields")
            if err:
                return err
            import json
            try:
                fields_dict = json.loads(fields)
            except (json.JSONDecodeError, ValueError):
                return "Invalid fields: must be valid JSON."
            client, uid, err = await microsoft.require_graph_client(ctx, "sharepoint", "write")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "POST",
                f"{GRAPH}/sites/{site_id}/lists/{list_id}/items",
                service="SharePoint", action="create list item",
                json={"fields": fields_dict},
            )
            if err:
                return err
            item = r.json()
            return f"List item created. ID: {item.get('id', '?')}"

        @mcp.tool()
        async def sharepoint_share_link(item_id: str, ctx: Context, type: str = "view", scope: str = "anonymous", site: str = "") -> str:
            """Create a sharing link for a SharePoint item.

            Args:
                item_id: ID of the file or folder
                type: Link type: 'view', 'edit', or 'embed' (default: view)
                scope: Link scope: 'anonymous', 'organization', or 'users' (default: anonymous)
                site: Site ID. Leave empty for default.
            """
            err = validation.validate_id(item_id, "item_id")
            if err:
                return err
            valid_types = ("view", "edit", "embed")
            if type not in valid_types:
                return f"Invalid type. Must be one of: {', '.join(valid_types)}"
            valid_scopes = ("anonymous", "organization", "users")
            if scope not in valid_scopes:
                return f"Invalid scope. Must be one of: {', '.join(valid_scopes)}"
            client, uid, err = await microsoft.require_graph_client(ctx, "sharepoint", "write")
            if err:
                return err
            sid = site or await conn._resolve_site(uid, client)
            if not sid:
                return "No site configured."
            r, err = await token_store.safe_request(
                client, "POST",
                f"{GRAPH}/sites/{sid}/drive/items/{item_id}/createLink",
                service="SharePoint", action="create sharing link",
                json={"type": type, "scope": scope},
            )
            if err:
                return err
            link = r.json().get("link", {})
            return f"Sharing link created.\n  URL: {link.get('webUrl', '?')}\n  Type: {type} | Scope: {scope}"
