"""GitHub connector: repos, issues, code search via GitHub REST API."""

import logging

from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store, validation
from asibot.connectors.base import Connector
from asibot.connectors.pagination import collect, paginate_offset

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
            client, uid, err = await token_store.require_service(ctx, "github", level="read")
            if err:
                return err
            org = token_store.get_credentials(uid, "github").get("org", "")
            q = f"{query} org:{org}" if org else query
            pages = paginate_offset(
                client, f"{API}/search/repositories",
                service="GitHub", action="search repos",
                params={"q": q},
                results_key="items",
                page_size_param="per_page",
                offset_param="page",
                offset_start=1,
                offset_step=1,
                page_size=min(limit, 100),
            )
            items = await collect(pages, limit)
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
            client, uid, err = await token_store.require_service(ctx, "github", level="read")
            if err:
                return err
            org = token_store.get_credentials(uid, "github").get("org", "")
            q = f"{query} org:{org}" if org else query
            pages = paginate_offset(
                client, f"{API}/search/code",
                service="GitHub", action="search code",
                params={"q": q},
                results_key="items",
                page_size_param="per_page",
                offset_param="page",
                offset_start=1,
                offset_step=1,
                page_size=min(limit, 100),
            )
            items = await collect(pages, limit)
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
            client, uid, err = await token_store.require_service(ctx, "github", level="read")
            if err:
                return err
            org = token_store.get_credentials(uid, "github").get("org", "")
            if not org:
                return "No GitHub org configured. Reconnect with an org name."
            pages = paginate_offset(
                client, f"{API}/orgs/{org}/repos",
                service="GitHub", action="list repos",
                params={"sort": "updated"},
                results_key=None,
                page_size_param="per_page",
                offset_param="page",
                offset_start=1,
                offset_step=1,
                page_size=min(limit, 100),
            )
            repos = await collect(pages, limit)
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
            client, uid, err = await token_store.require_service(ctx, "github", level="read")
            if err:
                return err
            org = token_store.get_credentials(uid, "github").get("org", "")
            full = repo if "/" in repo else f"{org}/{repo}"
            pages = paginate_offset(
                client, f"{API}/repos/{full}/issues",
                service="GitHub", action="list issues",
                params={"state": state},
                results_key=None,
                page_size_param="per_page",
                offset_param="page",
                offset_start=1,
                offset_step=1,
                page_size=min(limit, 100),
            )
            issues = await collect(pages, limit)
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
            client, uid, err = await token_store.require_service(ctx, "github", level="read")
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
            client, uid, err = await token_store.require_service(ctx, "github", level="write")
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

        @mcp.tool()
        async def github_get_pull_request(repo: str, pr_number: int, ctx: Context) -> str:
            """Get full details of a GitHub pull request.

            Args:
                repo: Repo name (e.g., "my-repo" or "org/my-repo")
                pr_number: Pull request number
            """
            err = validation.validate_repo(repo)
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "github", level="read")
            if err:
                return err
            org = token_store.get_credentials(uid, "github").get("org", "")
            full = repo if "/" in repo else f"{org}/{repo}"
            r, err = await token_store.safe_request(
                client, "GET", f"{API}/repos/{full}/pulls/{pr_number}",
                service="GitHub", action="get pull request",
            )
            if err:
                return err
            pr = r.json()
            return (
                f"#{pr['number']}: {pr.get('title', '?')}\n"
                f"State: {pr.get('state', '?')} | Author: {pr.get('user', {}).get('login', '?')}\n"
                f"Head: {pr.get('head', {}).get('ref', '?')} -> Base: {pr.get('base', {}).get('ref', '?')}\n"
                f"Additions: {pr.get('additions', 0)} | Deletions: {pr.get('deletions', 0)} | Changed files: {pr.get('changed_files', 0)}\n"
                f"Mergeable: {pr.get('mergeable', '?')}\n\n"
                f"{pr.get('body', 'No description')}"
            )

        @mcp.tool()
        async def github_list_commits(repo: str, ctx: Context, limit: int = 20) -> str:
            """List recent commits for a repository.

            Args:
                repo: Repo name (e.g., "my-repo" or "org/my-repo")
                limit: Max results (default: 20)
            """
            err = validation.validate_repo(repo)
            if err:
                return err
            limit = validation.validate_limit(limit)
            client, uid, err = await token_store.require_service(ctx, "github", level="read")
            if err:
                return err
            org = token_store.get_credentials(uid, "github").get("org", "")
            full = repo if "/" in repo else f"{org}/{repo}"
            r, err = await token_store.safe_request(
                client, "GET", f"{API}/repos/{full}/commits",
                service="GitHub", action="list commits",
                params={"per_page": min(limit, 100)},
            )
            if err:
                return err
            commits = r.json()
            if not commits:
                return "No commits found."
            lines = []
            for c in commits:
                sha = c.get("sha", "?")[:7]
                msg = c.get("commit", {}).get("message", "?").split("\n")[0]
                author = c.get("commit", {}).get("author", {}).get("name", "?")
                date = c.get("commit", {}).get("author", {}).get("date", "?")
                lines.append(f"{sha} {msg}\n  Author: {author} | Date: {date}")
            return "\n\n".join(lines)

        @mcp.tool()
        async def github_list_releases(repo: str, ctx: Context, limit: int = 10) -> str:
            """List releases for a repository.

            Args:
                repo: Repo name (e.g., "my-repo" or "org/my-repo")
                limit: Max results (default: 10)
            """
            err = validation.validate_repo(repo)
            if err:
                return err
            limit = validation.validate_limit(limit)
            client, uid, err = await token_store.require_service(ctx, "github", level="read")
            if err:
                return err
            org = token_store.get_credentials(uid, "github").get("org", "")
            full = repo if "/" in repo else f"{org}/{repo}"
            r, err = await token_store.safe_request(
                client, "GET", f"{API}/repos/{full}/releases",
                service="GitHub", action="list releases",
                params={"per_page": min(limit, 100)},
            )
            if err:
                return err
            releases = r.json()
            if not releases:
                return "No releases found."
            lines = []
            for rel in releases:
                body = rel.get("body", "") or ""
                truncated = (body[:200] + "...") if len(body) > 200 else body
                lines.append(
                    f"{rel.get('tag_name', '?')}: {rel.get('name', 'Untitled')}\n"
                    f"  Published: {rel.get('published_at', '?')}\n"
                    f"  {truncated}"
                )
            return "\n\n".join(lines)

        @mcp.tool()
        async def github_list_branches(repo: str, ctx: Context) -> str:
            """List branches for a repository.

            Args:
                repo: Repo name (e.g., "my-repo" or "org/my-repo")
            """
            err = validation.validate_repo(repo)
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "github", level="read")
            if err:
                return err
            org = token_store.get_credentials(uid, "github").get("org", "")
            full = repo if "/" in repo else f"{org}/{repo}"
            r, err = await token_store.safe_request(
                client, "GET", f"{API}/repos/{full}/branches",
                service="GitHub", action="list branches",
            )
            if err:
                return err
            branches = r.json()
            if not branches:
                return "No branches found."
            return "\n".join(
                f"{b.get('name', '?')} | Protected: {b.get('protected', False)}"
                for b in branches
            )

        @mcp.tool()
        async def github_get_workflow_runs(repo: str, ctx: Context, limit: int = 10) -> str:
            """List recent GitHub Actions workflow runs.

            Args:
                repo: Repo name (e.g., "my-repo" or "org/my-repo")
                limit: Max results (default: 10)
            """
            err = validation.validate_repo(repo)
            if err:
                return err
            limit = validation.validate_limit(limit)
            client, uid, err = await token_store.require_service(ctx, "github", level="read")
            if err:
                return err
            org = token_store.get_credentials(uid, "github").get("org", "")
            full = repo if "/" in repo else f"{org}/{repo}"
            r, err = await token_store.safe_request(
                client, "GET", f"{API}/repos/{full}/actions/runs",
                service="GitHub", action="get workflow runs",
                params={"per_page": min(limit, 100)},
            )
            if err:
                return err
            runs = r.json().get("workflow_runs", [])
            if not runs:
                return "No workflow runs found."
            lines = []
            for run in runs:
                lines.append(
                    f"{run.get('name', '?')} | Status: {run.get('status', '?')} | Conclusion: {run.get('conclusion', '?')}\n"
                    f"  Branch: {run.get('head_branch', '?')} | Created: {run.get('created_at', '?')}"
                )
            return "\n\n".join(lines)

        # --- Issue Management ---

        @mcp.tool()
        async def github_update_issue(
            owner: str, repo: str, issue_number: int, ctx: Context,
            title: str = "", body: str = "", state: str = "",
            labels: str = "", assignees: str = "",
        ) -> str:
            """Update fields on a GitHub issue.

            Args:
                owner: Repository owner
                repo: Repository name
                issue_number: Issue number
                title: New title (optional)
                body: New body (optional)
                state: New state: open or closed (optional)
                labels: Comma-separated label names to set (optional)
                assignees: Comma-separated usernames to set (optional)
            """
            err = validation.validate_id(owner, "owner")
            if err:
                return err
            err = validation.validate_repo(repo)
            if err:
                return err
            payload: dict = {}
            if title:
                err = validation.validate_content(title, "title")
                if err:
                    return err
                payload["title"] = title
            if body:
                payload["body"] = body
            if state:
                if state not in ("open", "closed"):
                    return "Invalid state. Must be 'open' or 'closed'."
                payload["state"] = state
            if labels:
                payload["labels"] = [l.strip() for l in labels.split(",") if l.strip()]
            if assignees:
                payload["assignees"] = [a.strip() for a in assignees.split(",") if a.strip()]
            if not payload:
                return "No fields to update. Provide at least one of: title, body, state, labels, assignees."
            client, uid, err = await token_store.require_service(ctx, "github", level="write")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "PATCH", f"{API}/repos/{owner}/{repo}/issues/{issue_number}",
                service="GitHub", action="update issue",
                json=payload,
            )
            if err:
                return err
            i = r.json()
            return f"Updated issue #{i['number']}: {i.get('title', '?')}\nURL: {i.get('html_url', '?')}"

        @mcp.tool()
        async def github_close_issue(owner: str, repo: str, issue_number: int, ctx: Context) -> str:
            """Close a GitHub issue.

            Args:
                owner: Repository owner
                repo: Repository name
                issue_number: Issue number
            """
            err = validation.validate_id(owner, "owner")
            if err:
                return err
            err = validation.validate_repo(repo)
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "github", level="write")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "PATCH", f"{API}/repos/{owner}/{repo}/issues/{issue_number}",
                service="GitHub", action="close issue",
                json={"state": "closed"},
            )
            if err:
                return err
            i = r.json()
            return f"Closed issue #{i['number']}: {i.get('title', '?')}\nURL: {i.get('html_url', '?')}"

        @mcp.tool()
        async def github_add_labels(owner: str, repo: str, issue_number: int, labels: str, ctx: Context) -> str:
            """Add labels to a GitHub issue.

            Args:
                owner: Repository owner
                repo: Repository name
                issue_number: Issue number
                labels: Comma-separated label names to add
            """
            err = validation.validate_id(owner, "owner")
            if err:
                return err
            err = validation.validate_repo(repo)
            if err:
                return err
            err = validation.validate_content(labels, "labels")
            if err:
                return err
            label_list = [l.strip() for l in labels.split(",") if l.strip()]
            if not label_list:
                return "labels is required."
            client, uid, err = await token_store.require_service(ctx, "github", level="write")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "POST", f"{API}/repos/{owner}/{repo}/issues/{issue_number}/labels",
                service="GitHub", action="add labels",
                json={"labels": label_list},
            )
            if err:
                return err
            result_labels = r.json()
            names = ", ".join(l.get("name", "?") for l in result_labels)
            return f"Labels on issue #{issue_number}: {names}"

        @mcp.tool()
        async def github_remove_label(owner: str, repo: str, issue_number: int, label: str, ctx: Context) -> str:
            """Remove a label from a GitHub issue.

            Args:
                owner: Repository owner
                repo: Repository name
                issue_number: Issue number
                label: Label name to remove
            """
            err = validation.validate_id(owner, "owner")
            if err:
                return err
            err = validation.validate_repo(repo)
            if err:
                return err
            err = validation.validate_content(label, "label")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "github", level="write")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "DELETE", f"{API}/repos/{owner}/{repo}/issues/{issue_number}/labels/{label}",
                service="GitHub", action="remove label",
            )
            if err:
                return err
            return f"Removed label '{label}' from issue #{issue_number}."

        @mcp.tool()
        async def github_add_assignees(owner: str, repo: str, issue_number: int, assignees: str, ctx: Context) -> str:
            """Add assignees to a GitHub issue.

            Args:
                owner: Repository owner
                repo: Repository name
                issue_number: Issue number
                assignees: Comma-separated usernames to assign
            """
            err = validation.validate_id(owner, "owner")
            if err:
                return err
            err = validation.validate_repo(repo)
            if err:
                return err
            err = validation.validate_content(assignees, "assignees")
            if err:
                return err
            assignee_list = [a.strip() for a in assignees.split(",") if a.strip()]
            if not assignee_list:
                return "assignees is required."
            client, uid, err = await token_store.require_service(ctx, "github", level="write")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "POST", f"{API}/repos/{owner}/{repo}/issues/{issue_number}/assignees",
                service="GitHub", action="add assignees",
                json={"assignees": assignee_list},
            )
            if err:
                return err
            i = r.json()
            current = ", ".join(a.get("login", "?") for a in i.get("assignees", []))
            return f"Assignees on issue #{issue_number}: {current or 'none'}"

        @mcp.tool()
        async def github_create_comment(owner: str, repo: str, issue_number: int, body: str, ctx: Context) -> str:
            """Add a comment to a GitHub issue or pull request.

            Args:
                owner: Repository owner
                repo: Repository name
                issue_number: Issue or PR number
                body: Comment body
            """
            err = validation.validate_id(owner, "owner")
            if err:
                return err
            err = validation.validate_repo(repo)
            if err:
                return err
            err = validation.validate_content(body, "body")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "github", level="write")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "POST", f"{API}/repos/{owner}/{repo}/issues/{issue_number}/comments",
                service="GitHub", action="create comment",
                json={"body": body},
            )
            if err:
                return err
            c = r.json()
            return f"Comment added to issue #{issue_number}.\nURL: {c.get('html_url', '?')}"

        # --- Pull Request Management ---

        @mcp.tool()
        async def github_create_pr_review(
            owner: str, repo: str, pr_number: int, ctx: Context,
            body: str = "", event: str = "COMMENT",
        ) -> str:
            """Submit a review on a GitHub pull request.

            Args:
                owner: Repository owner
                repo: Repository name
                pr_number: Pull request number
                body: Review body text (optional for APPROVE)
                event: Review action: APPROVE, REQUEST_CHANGES, or COMMENT (default: COMMENT)
            """
            err = validation.validate_id(owner, "owner")
            if err:
                return err
            err = validation.validate_repo(repo)
            if err:
                return err
            valid_events = ("APPROVE", "REQUEST_CHANGES", "COMMENT")
            if event not in valid_events:
                return f"Invalid event. Must be one of: {', '.join(valid_events)}."
            if event == "REQUEST_CHANGES" and not body.strip():
                return "body is required when requesting changes."
            client, uid, err = await token_store.require_service(ctx, "github", level="write")
            if err:
                return err
            payload: dict = {"event": event}
            if body:
                payload["body"] = body
            r, err = await token_store.safe_request(
                client, "POST", f"{API}/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
                service="GitHub", action="create PR review",
                json=payload,
            )
            if err:
                return err
            review = r.json()
            return f"Review submitted on PR #{pr_number}: {event}\nReview ID: {review.get('id', '?')}"

        @mcp.tool()
        async def github_merge_pr(
            owner: str, repo: str, pr_number: int, ctx: Context,
            merge_method: str = "merge",
        ) -> str:
            """Merge a GitHub pull request.

            Args:
                owner: Repository owner
                repo: Repository name
                pr_number: Pull request number
                merge_method: Merge method: merge, squash, or rebase (default: merge)
            """
            err = validation.validate_id(owner, "owner")
            if err:
                return err
            err = validation.validate_repo(repo)
            if err:
                return err
            valid_methods = ("merge", "squash", "rebase")
            if merge_method not in valid_methods:
                return f"Invalid merge_method. Must be one of: {', '.join(valid_methods)}."
            client, uid, err = await token_store.require_service(ctx, "github", level="write")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "PUT", f"{API}/repos/{owner}/{repo}/pulls/{pr_number}/merge",
                service="GitHub", action="merge PR",
                json={"merge_method": merge_method},
            )
            if err:
                return err
            m = r.json()
            return f"PR #{pr_number} merged via {merge_method}.\nSHA: {m.get('sha', '?')}\nMessage: {m.get('message', '?')}"

        @mcp.tool()
        async def github_list_pr_files(owner: str, repo: str, pr_number: int, ctx: Context) -> str:
            """List files changed in a GitHub pull request.

            Args:
                owner: Repository owner
                repo: Repository name
                pr_number: Pull request number
            """
            err = validation.validate_id(owner, "owner")
            if err:
                return err
            err = validation.validate_repo(repo)
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "github", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "GET", f"{API}/repos/{owner}/{repo}/pulls/{pr_number}/files",
                service="GitHub", action="list PR files",
            )
            if err:
                return err
            files = r.json()
            if not files:
                return "No files changed in this PR."
            lines = []
            for f in files:
                lines.append(
                    f"{f.get('filename', '?')} | {f.get('status', '?')} | "
                    f"+{f.get('additions', 0)} -{f.get('deletions', 0)}"
                )
            return "\n".join(lines)

        @mcp.tool()
        async def github_list_pr_reviews(owner: str, repo: str, pr_number: int, ctx: Context) -> str:
            """List reviews on a GitHub pull request.

            Args:
                owner: Repository owner
                repo: Repository name
                pr_number: Pull request number
            """
            err = validation.validate_id(owner, "owner")
            if err:
                return err
            err = validation.validate_repo(repo)
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "github", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "GET", f"{API}/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
                service="GitHub", action="list PR reviews",
            )
            if err:
                return err
            reviews = r.json()
            if not reviews:
                return "No reviews on this PR."
            lines = []
            for rv in reviews:
                lines.append(
                    f"Review #{rv.get('id', '?')} by {rv.get('user', {}).get('login', '?')}\n"
                    f"  State: {rv.get('state', '?')} | Submitted: {rv.get('submitted_at', '?')}"
                )
            return "\n\n".join(lines)

        # --- Repository ---

        @mcp.tool()
        async def github_get_file_content(
            owner: str, repo: str, path: str, ctx: Context,
            ref: str = "",
        ) -> str:
            """Get file content from a GitHub repository.

            Args:
                owner: Repository owner
                repo: Repository name
                path: File path within the repository
                ref: Branch, tag, or commit SHA (optional, defaults to default branch)
            """
            err = validation.validate_id(owner, "owner")
            if err:
                return err
            err = validation.validate_repo(repo)
            if err:
                return err
            err = validation.validate_content(path, "path")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "github", level="read")
            if err:
                return err
            params = {}
            if ref:
                params["ref"] = ref
            r, err = await token_store.safe_request(
                client, "GET", f"{API}/repos/{owner}/{repo}/contents/{path}",
                service="GitHub", action="get file content",
                params=params if params else None,
            )
            if err:
                return err
            data = r.json()
            import base64
            if data.get("encoding") == "base64" and data.get("content"):
                try:
                    content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
                except Exception:
                    content = "(binary file — cannot display)"
            else:
                content = data.get("content", "(no content)")
            return (
                f"File: {data.get('path', path)}\n"
                f"Size: {data.get('size', '?')} bytes | SHA: {data.get('sha', '?')[:7]}\n\n"
                f"{content}"
            )

        @mcp.tool()
        async def github_list_tags(owner: str, repo: str, ctx: Context) -> str:
            """List tags for a GitHub repository.

            Args:
                owner: Repository owner
                repo: Repository name
            """
            err = validation.validate_id(owner, "owner")
            if err:
                return err
            err = validation.validate_repo(repo)
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "github", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "GET", f"{API}/repos/{owner}/{repo}/tags",
                service="GitHub", action="list tags",
            )
            if err:
                return err
            tags = r.json()
            if not tags:
                return "No tags found."
            return "\n".join(
                f"{t.get('name', '?')} | SHA: {t.get('commit', {}).get('sha', '?')[:7]}"
                for t in tags
            )
