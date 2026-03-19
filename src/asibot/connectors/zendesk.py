"""Zendesk connector: tickets, articles, search via Zendesk REST API v2."""

import logging

from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store, validation
from asibot.connectors.base import Connector
from asibot.connectors.pagination import collect, paginate_odata

logger = logging.getLogger(__name__)

_ZENDESK_PRIORITIES = frozenset({"low", "normal", "high", "urgent"})


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
            client, uid, err = await token_store.require_service(ctx, "zendesk", level="read")
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
            client, uid, err = await token_store.require_service(ctx, "zendesk", level="read")
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
            client, uid, err = await token_store.require_service(ctx, "zendesk", level="read")
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

        @mcp.tool()
        async def zendesk_list_users(ctx: Context, query: str = "", limit: int = 10) -> str:
            """List or search Zendesk users.

            Args:
                query: Search query (optional, lists all if empty)
                limit: Max results (default: 10)
            """
            limit = validation.validate_limit(limit)
            client, uid, err = await token_store.require_service(ctx, "zendesk", level="read")
            if err:
                return err
            if query:
                err = validation.validate_query(query, "query")
                if err:
                    return err
                r, err = await token_store.safe_request(
                    client, "GET", "/users/search.json",
                    service="Zendesk", action="search users",
                    params={"query": query},
                )
            else:
                r, err = await token_store.safe_request(
                    client, "GET", "/users.json",
                    service="Zendesk", action="list users",
                    params={"per_page": limit},
                )
            if err:
                return err
            users = r.json().get("users", [])
            if not users:
                return "No users found."
            lines = []
            for u in users:
                lines.append(
                    f"{u.get('name', '?')} ({u.get('email', '?')})\n"
                    f"  Role: {u.get('role', '?')} | Active: {u.get('active', '?')} | ID: {u.get('id', '?')}"
                )
            return "\n\n".join(lines)

        @mcp.tool()
        async def zendesk_get_user(user_id: str, ctx: Context) -> str:
            """Get details of a Zendesk user.

            Args:
                user_id: User ID
            """
            err = validation.validate_id(user_id, "user_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "zendesk", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "GET", f"/users/{user_id}.json",
                service="Zendesk", action="get user",
            )
            if err:
                return err
            u = r.json().get("user", {})
            return (
                f"{u.get('name', '?')}\n"
                f"  Email: {u.get('email', '?')}\n"
                f"  Role: {u.get('role', '?')}\n"
                f"  Active: {u.get('active', '?')}\n"
                f"  ID: {u.get('id', '?')}"
            )

        @mcp.tool()
        async def zendesk_create_ticket(subject: str, description: str, ctx: Context, priority: str = "normal") -> str:
            """Create a new Zendesk ticket.

            Args:
                subject: Ticket subject
                description: Ticket description
                priority: Priority (low, normal, high, urgent). Default: normal
            """
            err = validation.validate_content(subject, "subject")
            if err:
                return err
            err = validation.validate_content(description, "description")
            if err:
                return err
            if priority not in _ZENDESK_PRIORITIES:
                return f"Invalid priority: '{priority}'. Allowed: {', '.join(sorted(_ZENDESK_PRIORITIES))}"
            client, uid, err = await token_store.require_service(ctx, "zendesk", level="write")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "POST", "/tickets.json",
                service="Zendesk", action="create ticket",
                json={"ticket": {"subject": subject, "comment": {"body": description}, "priority": priority}},
            )
            if err:
                return err
            ticket = r.json().get("ticket", {})
            return f"Ticket created. ID: #{ticket.get('id', '?')} | Subject: {ticket.get('subject', '?')}"

        @mcp.tool()
        async def zendesk_add_comment(ticket_id: str, comment: str, ctx: Context, public: bool = True) -> str:
            """Add a comment to a Zendesk ticket.

            Args:
                ticket_id: Ticket ID
                comment: Comment body text
                public: Whether the comment is public (default: True)
            """
            err = validation.validate_id(ticket_id, "ticket_id")
            if err:
                return err
            err = validation.validate_content(comment, "comment")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "zendesk", level="write")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "PUT", f"/tickets/{ticket_id}.json",
                service="Zendesk", action="add comment",
                json={"ticket": {"comment": {"body": comment, "public": public}}},
            )
            if err:
                return err
            return f"Comment added to ticket #{ticket_id}."

        @mcp.tool()
        async def zendesk_update_ticket(
            ticket_id: str, ctx: Context,
            status: str = "", priority: str = "", assignee_id: str = "",
            tags: str = "", subject: str = "",
        ) -> str:
            """Update fields on a Zendesk ticket.

            Args:
                ticket_id: Ticket ID
                status: New status (open, pending, solved, closed) — optional
                priority: New priority (low, normal, high, urgent) — optional
                assignee_id: New assignee user ID — optional
                tags: Comma-separated tags to set — optional
                subject: New subject — optional
            """
            err = validation.validate_id(ticket_id, "ticket_id")
            if err:
                return err
            update: dict = {}
            if status:
                valid_statuses = {"open", "pending", "solved", "closed"}
                if status not in valid_statuses:
                    return f"Invalid status: '{status}'. Allowed: {', '.join(sorted(valid_statuses))}"
                update["status"] = status
            if priority:
                if priority not in _ZENDESK_PRIORITIES:
                    return f"Invalid priority: '{priority}'. Allowed: {', '.join(sorted(_ZENDESK_PRIORITIES))}"
                update["priority"] = priority
            if assignee_id:
                update["assignee_id"] = assignee_id
            if tags:
                update["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
            if subject:
                update["subject"] = subject
            if not update:
                return "No fields to update. Provide at least one field."
            client, uid, err = await token_store.require_service(ctx, "zendesk", level="write")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "PUT", f"/tickets/{ticket_id}.json",
                service="Zendesk", action="update ticket",
                json={"ticket": update},
            )
            if err:
                return err
            return f"Ticket #{ticket_id} updated."

        @mcp.tool()
        async def zendesk_close_ticket(ticket_id: str, ctx: Context) -> str:
            """Close a Zendesk ticket (set status to closed).

            Args:
                ticket_id: Ticket ID
            """
            err = validation.validate_id(ticket_id, "ticket_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "zendesk", level="write")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "PUT", f"/tickets/{ticket_id}.json",
                service="Zendesk", action="close ticket",
                json={"ticket": {"status": "closed"}},
            )
            if err:
                return err
            return f"Ticket #{ticket_id} closed."

        @mcp.tool()
        async def zendesk_add_tags(ticket_id: str, tags: str, ctx: Context) -> str:
            """Add tags to a Zendesk ticket.

            Args:
                ticket_id: Ticket ID
                tags: Comma-separated tags to add
            """
            err = validation.validate_id(ticket_id, "ticket_id")
            if err:
                return err
            err = validation.validate_content(tags, "tags")
            if err:
                return err
            tag_list = [t.strip() for t in tags.split(",") if t.strip()]
            if not tag_list:
                return "tags is required."
            client, uid, err = await token_store.require_service(ctx, "zendesk", level="write")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "PUT", f"/tickets/{ticket_id}/tags.json",
                service="Zendesk", action="add tags",
                json={"tags": tag_list},
            )
            if err:
                return err
            return f"Tags added to ticket #{ticket_id}: {', '.join(tag_list)}"

        @mcp.tool()
        async def zendesk_remove_tags(ticket_id: str, tags: str, ctx: Context) -> str:
            """Remove tags from a Zendesk ticket.

            Args:
                ticket_id: Ticket ID
                tags: Comma-separated tags to remove
            """
            err = validation.validate_id(ticket_id, "ticket_id")
            if err:
                return err
            err = validation.validate_content(tags, "tags")
            if err:
                return err
            tag_list = [t.strip() for t in tags.split(",") if t.strip()]
            if not tag_list:
                return "tags is required."
            client, uid, err = await token_store.require_service(ctx, "zendesk", level="write")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "DELETE", f"/tickets/{ticket_id}/tags.json",
                service="Zendesk", action="remove tags",
                json={"tags": tag_list},
            )
            if err:
                return err
            return f"Tags removed from ticket #{ticket_id}: {', '.join(tag_list)}"

        @mcp.tool()
        async def zendesk_list_views(ctx: Context) -> str:
            """List saved views in Zendesk."""
            client, uid, err = await token_store.require_service(ctx, "zendesk", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "GET", "/views.json",
                service="Zendesk", action="list views",
            )
            if err:
                return err
            views = r.json().get("views", [])
            if not views:
                return "No views found."
            lines = []
            for v in views:
                lines.append(
                    f"{v.get('title', '?')}\n"
                    f"  ID: {v.get('id', '?')} | Active: {v.get('active', '?')}"
                )
            return "\n\n".join(lines)

        @mcp.tool()
        async def zendesk_get_view_tickets(view_id: str, ctx: Context, limit: int = 20) -> str:
            """Get tickets from a Zendesk saved view.

            Args:
                view_id: View ID
                limit: Max results (default: 20)
            """
            err = validation.validate_id(view_id, "view_id")
            if err:
                return err
            limit = validation.validate_limit(limit)
            client, uid, err = await token_store.require_service(ctx, "zendesk", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "GET", f"/views/{view_id}/tickets.json",
                service="Zendesk", action="get view tickets",
                params={"per_page": min(limit, 100)},
            )
            if err:
                return err
            tickets = r.json().get("tickets", [])
            if not tickets:
                return "No tickets in this view."
            lines = []
            for t in tickets[:limit]:
                lines.append(
                    f"#{t.get('id', '?')}: {t.get('subject', '?')}\n"
                    f"  Status: {t.get('status', '?')} | Priority: {t.get('priority', '?')}"
                )
            return "\n\n".join(lines)

        @mcp.tool()
        async def zendesk_list_groups(ctx: Context) -> str:
            """List agent groups in Zendesk."""
            client, uid, err = await token_store.require_service(ctx, "zendesk", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "GET", "/groups.json",
                service="Zendesk", action="list groups",
            )
            if err:
                return err
            groups = r.json().get("groups", [])
            if not groups:
                return "No groups found."
            lines = []
            for g in groups:
                lines.append(
                    f"{g.get('name', '?')}\n"
                    f"  ID: {g.get('id', '?')} | Default: {g.get('default', '?')}"
                )
            return "\n\n".join(lines)

        @mcp.tool()
        async def zendesk_assign_ticket(ticket_id: str, ctx: Context, assignee_id: str = "", group_id: str = "") -> str:
            """Assign a Zendesk ticket to an agent and/or group.

            Args:
                ticket_id: Ticket ID
                assignee_id: Agent user ID — optional
                group_id: Group ID — optional
            """
            err = validation.validate_id(ticket_id, "ticket_id")
            if err:
                return err
            update: dict = {}
            if assignee_id:
                update["assignee_id"] = assignee_id
            if group_id:
                update["group_id"] = group_id
            if not update:
                return "Provide at least one of assignee_id or group_id."
            client, uid, err = await token_store.require_service(ctx, "zendesk", level="write")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "PUT", f"/tickets/{ticket_id}.json",
                service="Zendesk", action="assign ticket",
                json={"ticket": update},
            )
            if err:
                return err
            parts = []
            if assignee_id:
                parts.append(f"assignee {assignee_id}")
            if group_id:
                parts.append(f"group {group_id}")
            return f"Ticket #{ticket_id} assigned to {' and '.join(parts)}."
