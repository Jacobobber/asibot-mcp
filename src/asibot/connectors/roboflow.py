"""Roboflow connector: projects and datasets via Roboflow REST API."""

import logging

import httpx
from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store
from asibot.connectors.base import Connector

logger = logging.getLogger(__name__)
API = "https://api.roboflow.com"


def _make_client(creds):
    if not creds.get("api_key"):
        return None
    return httpx.AsyncClient(
        headers={"Accept": "application/json"},
        timeout=30.0,
    )


class RoboflowConnector(Connector):
    def __init__(self, config=None):
        super().__init__(name="roboflow", config=config)

    async def connect(self):
        logger.info("Roboflow: ready (per-user API key)")

    async def disconnect(self):
        pass

    async def fetch_documents(self):
        return []

    def register_tools(self, mcp: FastMCP):

        @mcp.tool()
        async def roboflow_list_projects(ctx: Context) -> str:
            """List all projects in your Roboflow workspace."""
            client, uid, err = token_store.require_service(ctx, "roboflow", _make_client, "read")
            if err:
                return err
            creds = token_store.get_credentials(uid, "roboflow")
            workspace = creds.get("workspace", "")
            url = f"{API}/{workspace}" if workspace else API
            r = await client.get(url, params={"api_key": creds["api_key"]})
            r.raise_for_status()
            data = r.json()
            projects = data.get("workspace", {}).get("projects", data.get("projects", []))
            if not projects:
                return "No projects found."
            lines = []
            for p in projects:
                name = p.get("name", "Untitled")
                pid = p.get("id", "?")
                img_count = p.get("images", p.get("image_count", "?"))
                lines.append(f"{name} (id: {pid}) | Images: {img_count}")
            return "\n".join(lines)

        @mcp.tool()
        async def roboflow_get_project(project_id: str, ctx: Context) -> str:
            """Get details about a specific Roboflow project.

            Args:
                project_id: The project ID or URL slug
            """
            client, uid, err = token_store.require_service(ctx, "roboflow", _make_client, "read")
            if err:
                return err
            creds = token_store.get_credentials(uid, "roboflow")
            r = await client.get(f"{API}/{project_id}", params={"api_key": creds["api_key"]})
            r.raise_for_status()
            p = r.json()
            name = p.get("name", "Untitled")
            proj_type = p.get("type", "?")
            created = p.get("created", "?")
            versions = p.get("versions", [])
            output = f"Project: {name}\nType: {proj_type}\nCreated: {created}\nVersions: {len(versions)}"
            if versions:
                latest = versions[-1]
                output += f"\nLatest version: v{latest.get('id', '?')} | Images: {latest.get('images', '?')}"
            return output
