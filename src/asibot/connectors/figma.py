"""Figma connector: projects, files, and comments via Figma REST API."""

import logging

from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store, validation
from asibot.connectors.base import Connector
from asibot.connectors.pagination import collect, paginate_cursor

logger = logging.getLogger(__name__)
API = "https://api.figma.com"


class FigmaConnector(Connector):
    def __init__(self, config=None):
        super().__init__(name="figma", config=config)

    async def connect(self):
        logger.info("Figma: ready (per-user token)")

    async def disconnect(self):
        pass

    async def fetch_documents(self):
        return []

    def register_tools(self, mcp: FastMCP):

        @mcp.tool()
        async def figma_list_projects(team_id: str, ctx: Context) -> str:
            """List projects in a Figma team.

            Args:
                team_id: The Figma team ID
            """
            err = validation.validate_id(team_id, "team_id")
            if err:
                return err
            client, uid, err = token_store.require_service(ctx, "figma", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(client, "GET", f"{API}/v1/teams/{team_id}/projects", service="Figma", action="list projects")
            if err:
                return err
            projects = r.json().get("projects", [])
            if not projects:
                return "No projects found."
            return "\n".join(f"{p.get('name', 'Untitled')}  (ID: {p.get('id', '?')})" for p in projects)

        @mcp.tool()
        async def figma_list_files(project_id: str, ctx: Context) -> str:
            """List files in a Figma project.

            Args:
                project_id: The Figma project ID
            """
            err = validation.validate_id(project_id, "project_id")
            if err:
                return err
            client, uid, err = token_store.require_service(ctx, "figma", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(client, "GET", f"{API}/v1/projects/{project_id}/files", service="Figma", action="list files")
            if err:
                return err
            files = r.json().get("files", [])
            if not files:
                return "No files found."
            lines = []
            for f in files:
                modified = f.get("last_modified", "?")
                lines.append(f"{f.get('name', 'Untitled')}\n  Key: {f.get('key', '?')} | Modified: {modified[:10] if modified and modified != '?' else modified}")
            return "\n\n".join(lines)

        @mcp.tool()
        async def figma_get_file(file_key: str, ctx: Context) -> str:
            """Get metadata and structure of a Figma file.

            Args:
                file_key: The Figma file key
            """
            err = validation.validate_id(file_key, "file_key")
            if err:
                return err
            client, uid, err = token_store.require_service(ctx, "figma", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(client, "GET", f"{API}/v1/files/{file_key}", service="Figma", action="get file", params={"depth": 1})
            if err:
                return err
            data = r.json()
            name = data.get("name", "Untitled")
            modified = data.get("lastModified", "?")
            version = data.get("version", "?")
            pages = data.get("document", {}).get("children", [])
            output = f"{name}\n  Last modified: {modified}\n  Version: {version}\n  Pages ({len(pages)}):\n"
            for page in pages:
                child_count = len(page.get("children", []))
                output += f"    - {page.get('name', 'Untitled')} ({child_count} top-level layers)\n"
            return output

        @mcp.tool()
        async def figma_get_comments(file_key: str, ctx: Context) -> str:
            """Get comments on a Figma file.

            Args:
                file_key: The Figma file key
            """
            err = validation.validate_id(file_key, "file_key")
            if err:
                return err
            client, uid, err = token_store.require_service(ctx, "figma", level="read")
            if err:
                return err
            pages = paginate_cursor(
                client, f"{API}/v1/files/{file_key}/comments",
                method="GET",
                service="Figma", action="get comments",
                results_key="comments",
                cursor_response_key="pagination.cursor",
                cursor_request_key="cursor",
                cursor_in="params",
                page_size_param="page_size",
                page_size=100,
            )
            comments = await collect(pages, 200)
            if not comments:
                return "No comments on this file."
            lines = []
            for c in comments:
                user = c.get("user", {}).get("handle", "?")
                created = c.get("created_at", "?")
                message = c.get("message", "")
                resolved = " [RESOLVED]" if c.get("resolved_at") else ""
                lines.append(f"[{created[:16] if created and created != '?' else created}] {user}{resolved}:\n  {message}")
            return "\n\n".join(lines)
