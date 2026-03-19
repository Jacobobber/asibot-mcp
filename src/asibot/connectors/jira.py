"""Jira connector: issues, projects, search via Jira REST API v3."""

import logging

from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store, validation
from asibot.connectors.base import Connector
from asibot.connectors.pagination import collect, paginate_offset

logger = logging.getLogger(__name__)

_JIRA_SPRINT_STATES = frozenset({"active", "closed", "future"})


class JiraConnector(Connector):
    def __init__(self, config=None):
        super().__init__(name="jira", config=config)

    async def connect(self):
        logger.info("Jira: ready (per-user credentials)")

    async def disconnect(self):
        pass

    async def fetch_documents(self):
        return []

    def register_tools(self, mcp: FastMCP):

        @mcp.tool()
        async def jira_search(jql: str, ctx: Context, limit: int = 20) -> str:
            """Search Jira issues using JQL.

            Args:
                jql: JQL query string
                limit: Max results (default: 20)
            """
            err = validation.validate_query(jql, "jql")
            if err:
                return err
            limit = validation.validate_limit(limit)
            client, uid, err = await token_store.require_service(ctx, "atlassian", level="read")
            if err:
                return err
            pages = paginate_offset(
                client, "/search",
                service="Jira", action="search",
                params={"jql": jql, "fields": "summary,status,assignee,priority,updated"},
                results_key="issues",
                page_size_param="maxResults",
                offset_param="startAt",
                offset_start=0,
                page_size=min(limit, 100),
                total_key="total",
            )
            issues = await collect(pages, limit)
            if not issues:
                return "No issues found."
            lines = []
            for i in issues:
                f = i.get("fields", {})
                assignee = (f.get("assignee") or {}).get("displayName", "Unassigned")
                status = (f.get("status") or {}).get("name", "?")
                priority = (f.get("priority") or {}).get("name", "?")
                lines.append(f"{i['key']}: {f.get('summary', '?')}\n  Status: {status} | Priority: {priority} | Assignee: {assignee}")
            return "\n\n".join(lines)

        @mcp.tool()
        async def jira_get_issue(issue_key: str, ctx: Context) -> str:
            """Get full details of a Jira issue.

            Args:
                issue_key: Issue key (e.g., "PROJ-123")
            """
            err = validation.validate_issue_key(issue_key)
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "atlassian", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "GET", f"/issue/{issue_key}",
                service="Jira", action="get issue",
            )
            if err:
                return err
            i = r.json()
            f = i.get("fields", {})
            assignee = (f.get("assignee") or {}).get("displayName", "Unassigned")
            reporter = (f.get("reporter") or {}).get("displayName", "?")
            status = (f.get("status") or {}).get("name", "?")
            priority = (f.get("priority") or {}).get("name", "?")
            issue_type = (f.get("issuetype") or {}).get("name", "?")
            desc = f.get("description")
            desc_text = desc if isinstance(desc, str) else "(Atlassian Document Format — view in Jira)"
            output = (
                f"{i['key']}: {f.get('summary', '?')}\n"
                f"Type: {issue_type} | Status: {status} | Priority: {priority}\n"
                f"Assignee: {assignee} | Reporter: {reporter}\n"
                f"Created: {f.get('created', '?')[:10]} | Updated: {f.get('updated', '?')[:10]}\n"
                f"\n{desc_text}\n"
            )
            comments = (f.get("comment") or {}).get("comments", [])
            if comments:
                output += f"\n--- {len(comments)} Comments ---\n"
                for c in comments:
                    author = (c.get("author") or {}).get("displayName", "?")
                    output += f"\n[{c.get('created', '?')[:16]}] {author}:\n{c.get('body', '')}\n"
            return output

        @mcp.tool()
        async def jira_list_projects(ctx: Context, limit: int = 50) -> str:
            """List Jira projects.

            Args:
                limit: Max results (default: 50)
            """
            limit = validation.validate_limit(limit)
            client, uid, err = await token_store.require_service(ctx, "atlassian", level="read")
            if err:
                return err
            pages = paginate_offset(
                client, "/project/search",
                service="Jira", action="list projects",
                params={},
                results_key="values",
                page_size_param="maxResults",
                offset_param="startAt",
                offset_start=0,
                page_size=min(limit, 100),
                total_key="total",
            )
            projects = await collect(pages, limit)
            if not projects:
                return "No projects found."
            return "\n".join(f"{p['key']}: {p.get('name', '?')} ({p.get('projectTypeKey', '?')})" for p in projects)

        @mcp.tool()
        async def jira_my_issues(ctx: Context, limit: int = 20) -> str:
            """List Jira issues assigned to me.

            Args:
                limit: Max results (default: 20)
            """
            limit = validation.validate_limit(limit)
            client, uid, err = await token_store.require_service(ctx, "atlassian", level="read")
            if err:
                return err
            pages = paginate_offset(
                client, "/search",
                service="Jira", action="my issues",
                params={"jql": "assignee=currentUser() ORDER BY updated DESC", "fields": "summary,status,priority,updated"},
                results_key="issues",
                page_size_param="maxResults",
                offset_param="startAt",
                offset_start=0,
                page_size=min(limit, 100),
                total_key="total",
            )
            issues = await collect(pages, limit)
            if not issues:
                return "No issues assigned to you."
            lines = []
            for i in issues:
                f = i.get("fields", {})
                status = (f.get("status") or {}).get("name", "?")
                priority = (f.get("priority") or {}).get("name", "?")
                lines.append(f"{i['key']}: {f.get('summary', '?')}\n  Status: {status} | Priority: {priority} | Updated: {f.get('updated', '?')[:10]}")
            return "\n\n".join(lines)

        @mcp.tool()
        async def jira_create_issue(project_key: str, summary: str, ctx: Context, description: str = "") -> str:
            """Create a new Jira issue.

            Args:
                project_key: Project key (e.g., "PROJ")
                summary: Issue summary/title
                description: Issue description (optional)
            """
            err = validation.validate_project_key(project_key)
            if err:
                return err
            err = validation.validate_content(summary, "summary")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "atlassian", level="write")
            if err:
                return err
            payload = {
                "fields": {
                    "project": {"key": project_key},
                    "summary": summary,
                    "issuetype": {"name": "Task"},
                }
            }
            if description:
                payload["fields"]["description"] = {
                    "type": "doc",
                    "version": 1,
                    "content": [{"type": "paragraph", "content": [{"type": "text", "text": description}]}],
                }
            r, err = await token_store.safe_request(
                client, "POST", "/issue",
                service="Jira", action="create issue",
                json=payload,
            )
            if err:
                return err
            i = r.json()
            return f"Created {i['key']}: {summary}\nURL: https://{token_store.get_credentials(uid, 'atlassian').get('domain', '')}/browse/{i['key']}"

        @mcp.tool()
        async def jira_add_comment(issue_key: str, comment: str, ctx: Context) -> str:
            """Add a comment to a Jira issue.

            Args:
                issue_key: Issue key (e.g., "PROJ-123")
                comment: Comment text
            """
            err = validation.validate_issue_key(issue_key)
            if err:
                return err
            err = validation.validate_content(comment, "comment")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "atlassian", level="write")
            if err:
                return err
            payload = {
                "body": {
                    "type": "doc",
                    "version": 1,
                    "content": [{"type": "paragraph", "content": [{"type": "text", "text": comment}]}],
                }
            }
            r, err = await token_store.safe_request(
                client, "POST", f"/issue/{issue_key}/comment",
                service="Jira", action="add comment",
                json=payload,
            )
            if err:
                return err
            return f"Comment added to {issue_key}."

        @mcp.tool()
        async def jira_list_sprints(board_id: str, ctx: Context, state: str = "active") -> str:
            """List sprints for a Jira board.

            Args:
                board_id: The Jira board ID
                state: Sprint state filter: active, closed, or future (default: active)
            """
            err = validation.validate_id(board_id, "board_id")
            if err:
                return err
            if state not in _JIRA_SPRINT_STATES:
                return f"Invalid state: '{state}'. Allowed: {', '.join(sorted(_JIRA_SPRINT_STATES))}"
            client, uid, err = await token_store.require_service(ctx, "atlassian", level="read")
            if err:
                return err
            domain = token_store.get_credentials(uid, "atlassian").get("domain", "")
            url = f"https://{domain}/rest/agile/1.0/board/{board_id}/sprint"
            r, err = await token_store.safe_request(
                client, "GET", url,
                service="Jira", action="list sprints",
                params={"state": state},
            )
            if err:
                return err
            sprints = r.json().get("values", [])
            if not sprints:
                return f"No {state} sprints found."
            lines = []
            for s in sprints:
                lines.append(
                    f"{s.get('name', '?')} (ID: {s.get('id', '?')})\n"
                    f"  State: {s.get('state', '?')} | Start: {s.get('startDate', '?')} | End: {s.get('endDate', '?')}"
                )
            return "\n\n".join(lines)

        @mcp.tool()
        async def jira_list_transitions(issue_key: str, ctx: Context) -> str:
            """List available transitions for a Jira issue.

            Args:
                issue_key: Issue key (e.g., "PROJ-123")
            """
            err = validation.validate_issue_key(issue_key)
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "atlassian", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "GET", f"/issue/{issue_key}/transitions",
                service="Jira", action="list transitions",
            )
            if err:
                return err
            transitions = r.json().get("transitions", [])
            if not transitions:
                return f"No transitions available for {issue_key}."
            return "\n".join(
                f"ID: {t.get('id', '?')} | {t.get('name', '?')}"
                for t in transitions
            )

        @mcp.tool()
        async def jira_transition_issue(issue_key: str, transition_id: str, ctx: Context) -> str:
            """Transition a Jira issue to a new status.

            Args:
                issue_key: Issue key (e.g., "PROJ-123")
                transition_id: The transition ID (from jira_list_transitions)
            """
            err = validation.validate_issue_key(issue_key)
            if err:
                return err
            err = validation.validate_id(transition_id, "transition_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "atlassian", level="write")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "POST", f"/issue/{issue_key}/transitions",
                service="Jira", action="transition issue",
                json={"transition": {"id": transition_id}},
            )
            if err:
                return err
            return f"Transitioned {issue_key} with transition {transition_id}."
