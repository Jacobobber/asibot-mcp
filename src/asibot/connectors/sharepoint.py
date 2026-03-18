"""SharePoint connector via Microsoft Graph API. Per-user SSO auth."""

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
        if not all([settings.sharepoint_tenant_id, settings.sharepoint_client_id]):
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
            r, err = await token_store.safe_request(client, "GET", url, service="SharePoint", action="list files")
            if err:
                return err
            items = r.json().get("value", [])
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
        async def sharepoint_list_sites(ctx: Context, query: str = "") -> str:
            """List or search SharePoint sites.

            Args:
                query: Search term. Empty to list all.
            """
            client, uid, err = await microsoft.require_graph_client(ctx, "sharepoint", "read")
            if err:
                return err
            r, err = await token_store.safe_request(client, "GET", f"{GRAPH}/sites", service="SharePoint", action="list sites", params={"search": query or "*"})
            if err:
                return err
            sites = r.json().get("value", [])
            if not sites:
                return "No sites found."
            return "\n\n".join(f"{s.get('displayName', '?')}\n  URL: {s.get('webUrl', '')}\n  ID: {s.get('id', '')}" for s in sites)
