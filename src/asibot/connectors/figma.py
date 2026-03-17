"""Figma connector: projects, files, and comments via Figma REST API."""

import logging

import httpx
from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store
from asibot.connectors.base import Connector

logger = logging.getLogger(__name__)
API = "https://api.figma.com"


def _make_client(creds):
    if not creds.get("token"):
        return None
    return httpx.AsyncClient(
        headers={"X-Figma-Token": creds["token"]},
        timeout=30.0,
    )


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
            client, uid, err = token_store.require_service(ctx, "figma", _make_client, "read")
            if err:
                return err
            r = await client.get(f"{API}/v1/teams/{team_id}/projects")
            r.raise_for_status()
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
            client, uid, err = token_store.require_service(ctx, "figma", _make_client, "read")
            if err:
                return err
            r = await client.get(f"{API}/v1/projects/{project_id}/files")
            r.raise_for_status()
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
            client, uid, err = token_store.require_service(ctx, "figma", _make_client, "read")
            if err:
                return err
            r = await client.get(f"{API}/v1/files/{file_key}", params={"depth": 1})
            r.raise_for_status()
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
            client, uid, err = token_store.require_service(ctx, "figma", _make_client, "read")
            if err:
                return err
            r = await client.get(f"{API}/v1/files/{file_key}/comments")
            r.raise_for_status()
            comments = r.json().get("comments", [])
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
