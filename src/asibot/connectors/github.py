"""GitHub connector: repos, issues, code search via GitHub REST API."""

import logging

from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store, validation
from asibot.connectors.base import Connector

logger = logging.getLogger(__name__)
API = "https://api.github.com"


class GitHubConnector(Connector):
    def __init__(self, config=None):
        super().__init__(name="github", config=config)

    async def connect(self):
        logger.info("GitHub: ready (per-user PAT)")

    async def disconnect(self):
        pass

    async def fetch_documents(self):
        return []

    def register_tools(self, mcp: FastMCP):

        @mcp.tool()
        async def github_search_repos(query: str, ctx: Context, limit: int = 10) -> str:
            """Search GitHub repositories.

            Args:
                query: Search query
                limit: Max results (default: 10)
            """
            err = validation.validate_query(query, "query")
            if err:
                return err
            limit = validation.validate_limit(limit)
            client, uid, err = token_store.require_service(ctx, "github", level="read")
            if err:
                return err
            org = token_store.get_credentials(uid, "github").get("org", "")
            q = f"{query} org:{org}" if org else query
            r, err = await token_store.safe_request(
                client, "GET", f"{API}/search/repositories",
                service="GitHub", action="search repos",
                params={"q": q, "per_page": min(limit, 100)},
            )
            if err:
                return err
            items = r.json().get("items", [])
            if not items:
                return "No repos found."
            return "\n\n".join(f"{i['full_name']}\n  {i.get('description', 'No description')}\n  Stars: {i.get('stargazers_count', 0)} | Updated: {i.get('updated_at', '?')[:10]}" for i in items)

        @mcp.tool()
        async def github_search_code(query: str, ctx: Context, limit: int = 10) -> str:
            """Search code across GitHub repos.

            Args:
                query: Code search query
                limit: Max results (default: 10)
            """
            err = validation.validate_query(query, "query")
            if err:
                return err
            limit = validation.validate_limit(limit)
            client, uid, err = token_store.require_service(ctx, "github", level="read")
            if err:
                return err
            org = token_store.get_credentials(uid, "github").get("org", "")
            q = f"{query} org:{org}" if org else query
            r, err = await token_store.safe_request(
                client, "GET", f"{API}/search/code",
                service="GitHub", action="search code",
                params={"q": q, "per_page": min(limit, 100)},
            )
            if err:
                return err
            items = r.json().get("items", [])
            if not items:
                return "No code matches found."
            return "\n\n".join(f"{i['repository']['full_name']}/{i['path']}\n  URL: {i.get('html_url', '?')}" for i in items)

        @mcp.tool()
        async def github_list_repos(ctx: Context, limit: int = 30) -> str:
            """List repos in your GitHub organization.

            Args:
                limit: Max results (default: 30)
            """
            limit = validation.validate_limit(limit)
            client, uid, err = token_store.require_service(ctx, "github", level="read")
            if err:
                return err
            org = token_store.get_credentials(uid, "github").get("org", "")
            if not org:
                return "No GitHub org configured. Reconnect with an org name."
            r, err = await token_store.safe_request(
                client, "GET", f"{API}/orgs/{org}/repos",
                service="GitHub", action="list repos",
                params={"per_page": min(limit, 100), "sort": "updated"},
            )
            if err:
                return err
            repos = r.json()
            if not repos:
                return "No repos found."
            return "\n".join(f"{repo['name']}  ({repo.get('language', '?')}, updated {repo.get('updated_at', '?')[:10]})" for repo in repos)

        @mcp.tool()
        async def github_list_issues(repo: str, ctx: Context, state: str = "open", limit: int = 20) -> str:
            """List issues and PRs for a repository.

            Args:
                repo: Repo name (e.g., "my-repo" or "org/my-repo")
                state: open, closed, or all (default: open)
                limit: Max results (default: 20)
            """
            err = validation.validate_repo(repo)
            if err:
                return err
            limit = validation.validate_limit(limit)
            client, uid, err = token_store.require_service(ctx, "github", level="read")
            if err:
                return err
            org = token_store.get_credentials(uid, "github").get("org", "")
            full = repo if "/" in repo else f"{org}/{repo}"
            r, err = await token_store.safe_request(
                client, "GET", f"{API}/repos/{full}/issues",
                service="GitHub", action="list issues",
                params={"state": state, "per_page": min(limit, 100)},
            )
            if err:
                return err
            issues = r.json()
            if not issues:
                return f"No {state} issues found."
            lines = []
            for i in issues:
                pr = " [PR]" if i.get("pull_request") else ""
                labels = ", ".join(lbl["name"] for lbl in i.get("labels", []))
                lines.append(f"#{i['number']}{pr} {i.get('title', '?')}\n  State: {i.get('state', '?')} | Labels: {labels or 'none'}")
            return "\n\n".join(lines)

        @mcp.tool()
        async def github_get_issue(repo: str, issue_number: int, ctx: Context) -> str:
            """Get full details of a GitHub issue or PR with comments.

            Args:
                repo: Repo name
                issue_number: Issue/PR number
            """
            err = validation.validate_repo(repo)
            if err:
                return err
            client, uid, err = token_store.require_service(ctx, "github", level="read")
            if err:
                return err
            org = token_store.get_credentials(uid, "github").get("org", "")
            full = repo if "/" in repo else f"{org}/{repo}"
            r, err = await token_store.safe_request(
                client, "GET", f"{API}/repos/{full}/issues/{issue_number}",
                service="GitHub", action="get issue",
            )
            if err:
                return err
            i = r.json()
            output = f"#{i['number']}: {i.get('title', '?')}\nState: {i.get('state', '?')} | Author: {i.get('user', {}).get('login', '?')}\nCreated: {i.get('created_at', '?')}\n\n{i.get('body', 'No description')}\n"
            r2, _ = await token_store.safe_request(
                client, "GET", f"{API}/repos/{full}/issues/{issue_number}/comments",
                service="GitHub", action="get comments",
            )
            comments = r2.json() if r2 else []
            if comments:
                output += f"\n--- {len(comments)} Comments ---\n"
                for c in comments:
                    output += f"\n[{c.get('created_at', '?')[:16]}] {c.get('user', {}).get('login', '?')}:\n{c.get('body', '')}\n"
            return output

        @mcp.tool()
        async def github_create_issue(repo: str, title: str, ctx: Context, body: str = "") -> str:
            """Create a new GitHub issue.

            Args:
                repo: Repo name
                title: Issue title
                body: Issue body (optional)
            """
            err = validation.validate_repo(repo)
            if err:
                return err
            err = validation.validate_content(title, "title")
            if err:
                return err
            client, uid, err = token_store.require_service(ctx, "github", level="write")
            if err:
                return err
            org = token_store.get_credentials(uid, "github").get("org", "")
            full = repo if "/" in repo else f"{org}/{repo}"
            r, err = await token_store.safe_request(
                client, "POST", f"{API}/repos/{full}/issues",
                service="GitHub", action="create issue",
                json={"title": title, "body": body},
            )
            if err:
                return err
            i = r.json()
            return f"Created issue #{i['number']}: {i.get('title', '?')}\nURL: {i.get('html_url', '?')}"
