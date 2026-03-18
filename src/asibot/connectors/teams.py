"""Microsoft Teams connector via Graph API."""

import logging
import re

from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store, validation
from asibot.config import settings
from asibot.connectors import microsoft
from asibot.connectors.base import Connector

logger = logging.getLogger(__name__)
GRAPH = microsoft.GRAPH_BASE


class TeamsConnector(Connector):
    def __init__(self, config=None):
        super().__init__(name="teams", config=config)

    async def connect(self):
        logger.info("Teams: ready (Microsoft SSO)")

    async def disconnect(self):
        pass

    async def fetch_documents(self):
        return []

    def register_tools(self, mcp: FastMCP):
        if not all([settings.sharepoint_tenant_id, settings.sharepoint_client_id]):
            return

        @mcp.tool()
        async def teams_list_teams(ctx: Context) -> str:
            """List the Microsoft Teams you belong to."""
            client, uid, err = await microsoft.require_graph_client(ctx, "teams", "read")
            if err:
                return err
            r, err = await token_store.safe_request(client, "GET", f"{GRAPH}/me/joinedTeams", service="Teams", action="list teams")
            if err:
                return err
            teams = r.json().get("value", [])
            if not teams:
                return "No teams found."
            return "\n\n".join(f"{t.get('displayName', '?')}\n  ID: {t.get('id', '')}\n  Description: {t.get('description', 'None')}" for t in teams)

        @mcp.tool()
        async def teams_list_channels(team_id: str, ctx: Context) -> str:
            """List channels in a Team.

            Args:
                team_id: The team ID
            """
            err = validation.validate_id(team_id, "team_id")
            if err:
                return err
            client, uid, err = await microsoft.require_graph_client(ctx, "teams", "read")
            if err:
                return err
            r, err = await token_store.safe_request(client, "GET", f"{GRAPH}/teams/{team_id}/channels", service="Teams", action="list channels")
            if err:
                return err
            channels = r.json().get("value", [])
            if not channels:
                return "No channels found."
            return "\n".join(f"#{ch.get('displayName', '?')}  ID: {ch.get('id', '')}" for ch in channels)

        @mcp.tool()
        async def teams_read_messages(team_id: str, channel_id: str, ctx: Context, limit: int = 20) -> str:
            """Read recent messages from a Teams channel.

            Args:
                team_id: The team ID
                channel_id: The channel ID
                limit: Number of messages (default: 20)
            """
            err = validation.validate_id(team_id, "team_id")
            if err:
                return err
            err = validation.validate_id(channel_id, "channel_id")
            if err:
                return err
            limit = validation.validate_limit(limit)
            client, uid, err = await microsoft.require_graph_client(ctx, "teams", "read")
            if err:
                return err
            r, err = await token_store.safe_request(client, "GET", f"{GRAPH}/teams/{team_id}/channels/{channel_id}/messages", service="Teams", action="read messages", params={"$top": limit})
            if err:
                return err
            msgs = r.json().get("value", [])
            if not msgs:
                return "No messages."
            lines = []
            for m in msgs:
                sender = m.get("from", {}).get("user", {}).get("displayName", "?")
                body = m.get("body", {}).get("content", "")
                if m.get("body", {}).get("contentType") == "html":
                    body = re.sub(r"<[^>]+>", " ", body).strip()
                if body.strip():
                    lines.append(f"[{m.get('createdDateTime', '?')[:16]}] {sender}: {body[:500]}")
            return "\n\n".join(lines) if lines else "No text messages."

        @mcp.tool()
        async def teams_search_messages(query: str, ctx: Context, limit: int = 10) -> str:
            """Search across all your Teams messages.

            Args:
                query: Search query
                limit: Max results (default: 10)
            """
            err = validation.validate_query(query, "query")
            if err:
                return err
            limit = validation.validate_limit(limit)
            client, uid, err = await microsoft.require_graph_client(ctx, "teams", "read")
            if err:
                return err
            r, err = await token_store.safe_request(client, "POST", f"{GRAPH}/search/query", service="Teams", action="search messages", json={"requests": [{"entityTypes": ["chatMessage"], "query": {"queryString": query}, "from": 0, "size": limit}]})
            if err:
                return err
            hits = r.json().get("value", [{}])[0].get("hitsContainers", [{}])[0].get("hits", [])
            if not hits:
                return "No messages found."
            lines = []
            for h in hits:
                summary = re.sub(r"<[^>]+>", "", h.get("summary", "")).strip()
                sender = h.get("resource", {}).get("from", {}).get("user", {}).get("displayName", "?")
                lines.append(f"[{h.get('resource', {}).get('createdDateTime', '?')[:16]}] {sender}: {summary[:300]}")
            return "\n\n".join(lines)

        @mcp.tool()
        async def teams_recent_chats(ctx: Context, limit: int = 10) -> str:
            """List your recent Teams chats.

            Args:
                limit: Number of chats (default: 10)
            """
            client, uid, err = await microsoft.require_graph_client(ctx, "teams", "read")
            if err:
                return err
            r, err = await token_store.safe_request(client, "GET", f"{GRAPH}/me/chats", service="Teams", action="recent chats", params={"$top": limit, "$orderby": "lastUpdatedDateTime desc", "$expand": "members"})
            if err:
                return err
            chats = r.json().get("value", [])
            if not chats:
                return "No recent chats."
            lines = []
            for c in chats:
                topic = c.get("topic") or "Direct message"
                members = [m.get("displayName", "?") for m in c.get("members", [])][:5]
                lines.append(f"{topic}\n  With: {', '.join(members)}\n  Updated: {c.get('lastUpdatedDateTime', '?')[:16]}\n  ID: {c.get('id', '')}")
            return "\n\n".join(lines)
