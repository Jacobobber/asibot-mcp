"""Jira connector: issues, projects, search via Jira REST API v3."""

import logging

from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store, validation
from asibot.connectors.base import Connector

logger = logging.getLogger(__name__)


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
            client, uid, err = token_store.require_service(ctx, "atlassian", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "GET", "/search",
                service="Jira", action="search",
                params={"jql": jql, "maxResults": limit, "fields": "summary,status,assignee,priority,updated"},
            )
            if err:
                return err
            issues = r.json().get("issues", [])
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
            client, uid, err = token_store.require_service(ctx, "atlassian", level="read")
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
            client, uid, err = token_store.require_service(ctx, "atlassian", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "GET", "/project/search",
                service="Jira", action="list projects",
                params={"maxResults": limit},
            )
            if err:
                return err
            projects = r.json().get("values", [])
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
            client, uid, err = token_store.require_service(ctx, "atlassian", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "GET", "/search",
                service="Jira", action="my issues",
                params={"jql": "assignee=currentUser() ORDER BY updated DESC", "maxResults": limit, "fields": "summary,status,priority,updated"},
            )
            if err:
                return err
            issues = r.json().get("issues", [])
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
            client, uid, err = token_store.require_service(ctx, "atlassian", level="write")
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
            client, uid, err = token_store.require_service(ctx, "atlassian", level="write")
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
