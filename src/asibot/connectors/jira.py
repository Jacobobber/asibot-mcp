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
        async def jira_update_issue(issue_key: str, fields: str, ctx: Context) -> str:
            """Update fields on a Jira issue.

            Args:
                issue_key: Issue key (e.g., "PROJ-123")
                fields: JSON string of fields to update (e.g., '{"summary": "New title", "priority": {"name": "High"}}')
            """
            import json as _json
            err = validation.validate_issue_key(issue_key)
            if err:
                return err
            err = validation.validate_content(fields, "fields")
            if err:
                return err
            try:
                fields_dict = _json.loads(fields)
            except (ValueError, TypeError):
                return "Invalid fields: must be a valid JSON string."
            client, uid, err = await token_store.require_service(ctx, "atlassian", level="write")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "PUT", f"/issue/{issue_key}",
                service="Jira", action="update issue",
                json={"fields": fields_dict},
            )
            if err:
                return err
            return f"Updated {issue_key}."

        @mcp.tool()
        async def jira_create_subtask(parent_key: str, project_key: str, summary: str, ctx: Context, description: str = "") -> str:
            """Create a subtask under a parent Jira issue.

            Args:
                parent_key: Parent issue key (e.g., "PROJ-123")
                project_key: Project key (e.g., "PROJ")
                summary: Subtask summary/title
                description: Subtask description (optional)
            """
            err = validation.validate_issue_key(parent_key)
            if err:
                return err
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
                    "parent": {"key": parent_key},
                    "summary": summary,
                    "issuetype": {"name": "Sub-task"},
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
                service="Jira", action="create subtask",
                json=payload,
            )
            if err:
                return err
            i = r.json()
            return f"Created subtask {i['key']}: {summary} (parent: {parent_key})"

        @mcp.tool()
        async def jira_link_issues(inward_key: str, outward_key: str, link_type: str, ctx: Context) -> str:
            """Link two Jira issues.

            Args:
                inward_key: Inward issue key (e.g., "PROJ-123")
                outward_key: Outward issue key (e.g., "PROJ-456")
                link_type: Link type name (e.g., "Blocks", "Duplicate", "Relates")
            """
            err = validation.validate_issue_key(inward_key)
            if err:
                return err
            err = validation.validate_issue_key(outward_key)
            if err:
                return err
            err = validation.validate_content(link_type, "link_type")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "atlassian", level="write")
            if err:
                return err
            payload = {
                "type": {"name": link_type},
                "inwardIssue": {"key": inward_key},
                "outwardIssue": {"key": outward_key},
            }
            r, err = await token_store.safe_request(
                client, "POST", "/issueLink",
                service="Jira", action="link issues",
                json=payload,
            )
            if err:
                return err
            return f"Linked {inward_key} -> {outward_key} ({link_type})."

        @mcp.tool()
        async def jira_add_label(issue_key: str, label: str, ctx: Context) -> str:
            """Add a label to a Jira issue.

            Args:
                issue_key: Issue key (e.g., "PROJ-123")
                label: Label to add
            """
            err = validation.validate_issue_key(issue_key)
            if err:
                return err
            err = validation.validate_content(label, "label")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "atlassian", level="write")
            if err:
                return err
            payload = {
                "update": {
                    "labels": [{"add": label}],
                }
            }
            r, err = await token_store.safe_request(
                client, "PUT", f"/issue/{issue_key}",
                service="Jira", action="add label",
                json=payload,
            )
            if err:
                return err
            return f"Label '{label}' added to {issue_key}."

        @mcp.tool()
        async def jira_remove_label(issue_key: str, label: str, ctx: Context) -> str:
            """Remove a label from a Jira issue.

            Args:
                issue_key: Issue key (e.g., "PROJ-123")
                label: Label to remove
            """
            err = validation.validate_issue_key(issue_key)
            if err:
                return err
            err = validation.validate_content(label, "label")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "atlassian", level="write")
            if err:
                return err
            payload = {
                "update": {
                    "labels": [{"remove": label}],
                }
            }
            r, err = await token_store.safe_request(
                client, "PUT", f"/issue/{issue_key}",
                service="Jira", action="remove label",
                json=payload,
            )
            if err:
                return err
            return f"Label '{label}' removed from {issue_key}."

        @mcp.tool()
        async def jira_assign_issue(issue_key: str, assignee: str, ctx: Context) -> str:
            """Assign a Jira issue to a user.

            Args:
                issue_key: Issue key (e.g., "PROJ-123")
                assignee: Assignee email address or account ID
            """
            err = validation.validate_issue_key(issue_key)
            if err:
                return err
            err = validation.validate_content(assignee, "assignee")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "atlassian", level="write")
            if err:
                return err
            # If it looks like an email, use emailAddress; otherwise treat as accountId
            if "@" in assignee:
                payload = {"fields": {"assignee": {"emailAddress": assignee}}}
            else:
                payload = {"fields": {"assignee": {"accountId": assignee}}}
            r, err = await token_store.safe_request(
                client, "PUT", f"/issue/{issue_key}",
                service="Jira", action="assign issue",
                json=payload,
            )
            if err:
                return err
            return f"Assigned {issue_key} to {assignee}."

        @mcp.tool()
        async def jira_list_boards(ctx: Context, project_key: str = "") -> str:
            """List Scrum/Kanban boards, optionally filtered by project.

            Args:
                project_key: Project key to filter boards (optional)
            """
            if project_key:
                err = validation.validate_project_key(project_key)
                if err:
                    return err
            client, uid, err = await token_store.require_service(ctx, "atlassian", level="read")
            if err:
                return err
            domain = token_store.get_credentials(uid, "atlassian").get("domain", "")
            url = f"https://{domain}/rest/agile/1.0/board"
            params = {}
            if project_key:
                params["projectKeyOrId"] = project_key
            r, err = await token_store.safe_request(
                client, "GET", url,
                service="Jira", action="list boards",
                params=params,
            )
            if err:
                return err
            boards = r.json().get("values", [])
            if not boards:
                return "No boards found."
            lines = []
            for b in boards:
                btype = b.get("type", "?")
                lines.append(f"{b.get('name', '?')} (ID: {b.get('id', '?')})\n  Type: {btype}")
            return "\n\n".join(lines)

        @mcp.tool()
        async def jira_get_sprint_issues(sprint_id: str, ctx: Context, limit: int = 50) -> str:
            """Get issues in a Jira sprint.

            Args:
                sprint_id: The sprint ID
                limit: Max results (default: 50)
            """
            err = validation.validate_id(sprint_id, "sprint_id")
            if err:
                return err
            limit = validation.validate_limit(limit)
            client, uid, err = await token_store.require_service(ctx, "atlassian", level="read")
            if err:
                return err
            domain = token_store.get_credentials(uid, "atlassian").get("domain", "")
            url = f"https://{domain}/rest/agile/1.0/sprint/{sprint_id}/issue"
            r, err = await token_store.safe_request(
                client, "GET", url,
                service="Jira", action="get sprint issues",
                params={"maxResults": min(limit, 100), "fields": "summary,status,assignee,priority"},
            )
            if err:
                return err
            issues = r.json().get("issues", [])
            if not issues:
                return "No issues found in this sprint."
            lines = []
            for i in issues:
                f = i.get("fields", {})
                assignee = (f.get("assignee") or {}).get("displayName", "Unassigned")
                status = (f.get("status") or {}).get("name", "?")
                priority = (f.get("priority") or {}).get("name", "?")
                lines.append(f"{i['key']}: {f.get('summary', '?')}\n  Status: {status} | Priority: {priority} | Assignee: {assignee}")
            return "\n\n".join(lines)
