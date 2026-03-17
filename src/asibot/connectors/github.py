"""GitHub connector: repos, issues, code search via GitHub REST API."""

import logging

import httpx
from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store
from asibot.connectors.base import Connector

logger = logging.getLogger(__name__)
API = "https://api.github.com"


def _make_client(creds):
    if not creds.get("token"):
        return None
    return httpx.AsyncClient(
        headers={"Authorization": f"Bearer {creds['token']}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
        timeout=30.0,
    )


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
            client, uid, err = token_store.require_service(ctx, "github", _make_client, "read")
            if err:
                return err
            org = token_store.get_credentials(uid, "github").get("org", "")
            q = f"{query} org:{org}" if org else query
            r = await client.get(f"{API}/search/repositories", params={"q": q, "per_page": limit})
            r.raise_for_status()
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
            client, uid, err = token_store.require_service(ctx, "github", _make_client, "read")
            if err:
                return err
            org = token_store.get_credentials(uid, "github").get("org", "")
            q = f"{query} org:{org}" if org else query
            r = await client.get(f"{API}/search/code", params={"q": q, "per_page": limit})
            r.raise_for_status()
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
            client, uid, err = token_store.require_service(ctx, "github", _make_client, "read")
            if err:
                return err
            org = token_store.get_credentials(uid, "github").get("org", "")
            if not org:
                return "No GitHub org configured. Reconnect with an org name."
            r = await client.get(f"{API}/orgs/{org}/repos", params={"per_page": limit, "sort": "updated"})
            r.raise_for_status()
            repos = r.json()
            if not repos:
                return "No repos found."
            return "\n".join(f"{r['name']}  ({r.get('language', '?')}, updated {r.get('updated_at', '?')[:10]})" for r in repos)

        @mcp.tool()
        async def github_list_issues(repo: str, ctx: Context, state: str = "open", limit: int = 20) -> str:
            """List issues and PRs for a repository.

            Args:
                repo: Repo name (e.g., "my-repo" or "org/my-repo")
                state: open, closed, or all (default: open)
                limit: Max results (default: 20)
            """
            client, uid, err = token_store.require_service(ctx, "github", _make_client, "read")
            if err:
                return err
            org = token_store.get_credentials(uid, "github").get("org", "")
            full = repo if "/" in repo else f"{org}/{repo}"
            r = await client.get(f"{API}/repos/{full}/issues", params={"state": state, "per_page": limit})
            r.raise_for_status()
            issues = r.json()
            if not issues:
                return f"No {state} issues found."
            lines = []
            for i in issues:
                pr = " [PR]" if i.get("pull_request") else ""
                labels = ", ".join(l["name"] for l in i.get("labels", []))
                lines.append(f"#{i['number']}{pr} {i.get('title', '?')}\n  State: {i.get('state', '?')} | Labels: {labels or 'none'}")
            return "\n\n".join(lines)

        @mcp.tool()
        async def github_get_issue(repo: str, issue_number: int, ctx: Context) -> str:
            """Get full details of a GitHub issue or PR with comments.

            Args:
                repo: Repo name
                issue_number: Issue/PR number
            """
            client, uid, err = token_store.require_service(ctx, "github", _make_client, "read")
            if err:
                return err
            org = token_store.get_credentials(uid, "github").get("org", "")
            full = repo if "/" in repo else f"{org}/{repo}"
            r = await client.get(f"{API}/repos/{full}/issues/{issue_number}")
            r.raise_for_status()
            i = r.json()
            output = f"#{i['number']}: {i.get('title', '?')}\nState: {i.get('state', '?')} | Author: {i.get('user', {}).get('login', '?')}\nCreated: {i.get('created_at', '?')}\n\n{i.get('body', 'No description')}\n"
            r2 = await client.get(f"{API}/repos/{full}/issues/{issue_number}/comments")
            r2.raise_for_status()
            comments = r2.json()
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
            client, uid, err = token_store.require_service(ctx, "github", _make_client, "write")
            if err:
                return err
            org = token_store.get_credentials(uid, "github").get("org", "")
            full = repo if "/" in repo else f"{org}/{repo}"
            r = await client.post(f"{API}/repos/{full}/issues", json={"title": title, "body": body})
            r.raise_for_status()
            i = r.json()
            return f"Created issue #{i['number']}: {i.get('title', '?')}\nURL: {i.get('html_url', '?')}"
