"""Zendesk connector: tickets, articles, search via Zendesk REST API v2."""

import logging

from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store, validation
from asibot.connectors.base import Connector
from asibot.connectors.pagination import collect, paginate_odata

logger = logging.getLogger(__name__)


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
            err = validation.validate_query(query, "query")
            if err:
                return err
            limit = validation.validate_limit(limit)
            client, uid, err = token_store.require_service(ctx, "zendesk", level="read")
            if err:
                return err
            q = f"type:ticket {query}"
            if status:
                q += f" status:{status}"
            pages = paginate_odata(
                client, "/search.json",
                service="Zendesk", action="search tickets",
                params={"query": q, "per_page": min(limit, 100)},
                results_key="results",
                next_link_key="next_page",
            )
            results = await collect(pages, limit)
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
            client, uid, err = token_store.require_service(ctx, "zendesk", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "GET", f"/tickets/{ticket_id}.json",
                service="Zendesk", action="get ticket",
            )
            if err:
                return err
            t = r.json().get("ticket", {})
            output = (
                f"#{t.get('id', '?')}: {t.get('subject', '?')}\n"
                f"Status: {t.get('status', '?')} | Priority: {t.get('priority', '?')} | Type: {t.get('type', '?')}\n"
                f"Requester ID: {t.get('requester_id', '?')} | Assignee ID: {t.get('assignee_id', '?')}\n"
                f"Created: {str(t.get('created_at', '?'))[:10]} | Updated: {str(t.get('updated_at', '?'))[:10]}\n"
                f"\n{t.get('description', 'No description')}\n"
            )
            # Fetch comments
            cr, _ = await token_store.safe_request(
                client, "GET", f"/tickets/{ticket_id}/comments.json",
                service="Zendesk", action="get comments",
            )
            if cr is None:
                return output
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
            err = validation.validate_query(query, "query")
            if err:
                return err
            limit = validation.validate_limit(limit)
            client, uid, err = token_store.require_service(ctx, "zendesk", level="read")
            if err:
                return err
            pages = paginate_odata(
                client, "/help_center/articles/search.json",
                service="Zendesk", action="search articles",
                params={"query": query, "per_page": min(limit, 100)},
                results_key="results",
                next_link_key="next_page",
            )
            results = await collect(pages, limit)
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
