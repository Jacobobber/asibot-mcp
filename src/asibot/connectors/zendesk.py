"""Zendesk connector: tickets, articles, search via Zendesk REST API v2."""

import logging

import httpx
from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store
from asibot.connectors.base import Connector

logger = logging.getLogger(__name__)


def _make_client(creds):
    if not creds.get("email") or not creds.get("api_token") or not creds.get("subdomain"):
        return None
    return httpx.AsyncClient(
        auth=(f"{creds['email']}/token", creds["api_token"]),
        base_url=f"https://{creds['subdomain']}.zendesk.com/api/v2",
        headers={"Accept": "application/json"},
        timeout=30.0,
    )


class ZendeskConnector(Connector):
    def __init__(self, config=None):
        super().__init__(name="zendesk", config=config)

    async def connect(self):
        logger.info("Zendesk: ready (per-user credentials)")

    async def disconnect(self):
        pass

    async def fetch_documents(self):
        return []

    def register_tools(self, mcp: FastMCP):

        @mcp.tool()
        async def zendesk_search_tickets(query: str, ctx: Context, status: str = "", limit: int = 20) -> str:
            """Search Zendesk tickets.

            Args:
                query: Search query
                status: Filter by status (open, pending, solved, closed) — optional
                limit: Max results (default: 20)
            """
            client, uid, err = token_store.require_service(ctx, "zendesk", _make_client, "read")
            if err:
                return err
            q = f"type:ticket {query}"
            if status:
                q += f" status:{status}"
            r = await client.get("/search.json", params={"query": q, "per_page": limit})
            r.raise_for_status()
            results = r.json().get("results", [])
            if not results:
                return "No tickets found."
            lines = []
            for t in results:
                lines.append(
                    f"#{t.get('id', '?')}: {t.get('subject', '?')}\n"
                    f"  Status: {t.get('status', '?')} | Priority: {t.get('priority', '?')} | "
                    f"Updated: {str(t.get('updated_at', '?'))[:10]}"
                )
            return "\n\n".join(lines)

        @mcp.tool()
        async def zendesk_get_ticket(ticket_id: int, ctx: Context) -> str:
            """Get full details of a Zendesk ticket with comments.

            Args:
                ticket_id: Ticket ID
            """
            client, uid, err = token_store.require_service(ctx, "zendesk", _make_client, "read")
            if err:
                return err
            r = await client.get(f"/tickets/{ticket_id}.json")
            r.raise_for_status()
            t = r.json().get("ticket", {})
            output = (
                f"#{t.get('id', '?')}: {t.get('subject', '?')}\n"
                f"Status: {t.get('status', '?')} | Priority: {t.get('priority', '?')} | Type: {t.get('type', '?')}\n"
                f"Requester ID: {t.get('requester_id', '?')} | Assignee ID: {t.get('assignee_id', '?')}\n"
                f"Created: {str(t.get('created_at', '?'))[:10]} | Updated: {str(t.get('updated_at', '?'))[:10]}\n"
                f"\n{t.get('description', 'No description')}\n"
            )
            # Fetch comments
            cr = await client.get(f"/tickets/{ticket_id}/comments.json")
            cr.raise_for_status()
            comments = cr.json().get("comments", [])
            if len(comments) > 1:
                output += f"\n--- {len(comments) - 1} Follow-up Comments ---\n"
                for c in comments[1:]:
                    output += f"\n[{str(c.get('created_at', '?'))[:16]}] Author ID {c.get('author_id', '?')}:\n{c.get('body', '')}\n"
            return output

        @mcp.tool()
        async def zendesk_search_articles(query: str, ctx: Context, limit: int = 10) -> str:
            """Search Zendesk Help Center articles.

            Args:
                query: Search query
                limit: Max results (default: 10)
            """
            client, uid, err = token_store.require_service(ctx, "zendesk", _make_client, "read")
            if err:
                return err
            r = await client.get("/help_center/articles/search.json", params={"query": query, "per_page": limit})
            r.raise_for_status()
            results = r.json().get("results", [])
            if not results:
                return "No articles found."
            lines = []
            for a in results:
                lines.append(
                    f"{a.get('title', '?')}\n"
                    f"  ID: {a.get('id', '?')} | URL: {a.get('html_url', '?')}\n"
                    f"  Snippet: {a.get('snippet', '?')[:150]}"
                )
            return "\n\n".join(lines)
