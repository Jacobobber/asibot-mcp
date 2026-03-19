"""Zapier NLA connector: list and run actions via Zapier Natural Language API."""

import logging

from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store, validation
from asibot.connectors.base import Connector

logger = logging.getLogger(__name__)
API = "https://nla.zapier.com/api/v1"
PLATFORM_API = "https://api.zapier.com/v1"


class ZapierConnector(Connector):
    def __init__(self, config=None):
        super().__init__(name="zapier", config=config)

    async def connect(self):
        logger.info("Zapier: ready (per-user NLA API key)")

    async def disconnect(self):
        pass

    async def fetch_documents(self):
        return []

    def register_tools(self, mcp: FastMCP):

        @mcp.tool()
        async def zapier_list_actions(ctx: Context) -> str:
            """List all available Zapier NLA actions configured for your account."""
            client, uid, err = await token_store.require_service(ctx, "zapier", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(client, "GET", f"{API}/exposed/", service="Zapier", action="list actions")
            if err:
                return err
            results = r.json().get("results", [])
            if not results:
                return "No Zapier actions configured. Set up actions at https://nla.zapier.com/."
            return "\n\n".join(
                f"{a.get('description', 'No description')}\n  ID: {a.get('id', '?')} | App: {a.get('params', {}).get('app', '?')}"
                for a in results
            )

        @mcp.tool()
        async def zapier_run_action(action_id: str, instructions: str, ctx: Context) -> str:
            """Run a Zapier NLA action with natural language instructions.

            Args:
                action_id: The action ID from zapier_list_actions
                instructions: Natural language instructions for the action
            """
            err = validation.validate_id(action_id, "action_id")
            if err:
                return err
            err = validation.validate_content(instructions, "instructions")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "zapier", level="write")
            if err:
                return err
            r, err = await token_store.safe_request(client, "POST", f"{API}/exposed/{action_id}/execute/", service="Zapier", action="run action", json={"instructions": instructions})
            if err:
                return err
            data = r.json()
            status = data.get("status", "unknown")
            result = data.get("result", {})
            if status == "success":
                return f"Action executed successfully.\n\nResult: {result}"
            return f"Action status: {status}\n\nDetails: {data}"

        @mcp.tool()
        async def zapier_preview_action(action_id: str, instructions: str, ctx: Context) -> str:
            """Preview a Zapier NLA action (dry run) without executing it.

            Args:
                action_id: The action ID from zapier_list_actions
                instructions: Natural language instructions for the action
            """
            err = validation.validate_id(action_id, "action_id")
            if err:
                return err
            err = validation.validate_content(instructions, "instructions")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "zapier", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(client, "POST", f"{API}/exposed/{action_id}/preview/", service="Zapier", action="preview action", json={"instructions": instructions})
            if err:
                return err
            data = r.json()
            status = data.get("status", "unknown")
            preview = data.get("result", data.get("preview", {}))
            return f"Preview status: {status}\n\nPreview result: {preview}"

        @mcp.tool()
        async def zapier_get_action(action_id: str, ctx: Context) -> str:
            """Get details of a specific Zapier NLA action.

            Args:
                action_id: The action ID
            """
            err = validation.validate_id(action_id, "action_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "zapier", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(client, "GET", f"{API}/exposed/{action_id}/", service="Zapier", action="get action")
            if err:
                return err
            a = r.json()
            description = a.get("description", "No description")
            aid = a.get("id", action_id)
            app = a.get("params", {}).get("app", "?")
            params = a.get("params", {})
            param_keys = [k for k in params if k != "app"]
            param_str = ", ".join(param_keys) if param_keys else "none"
            return f"{description}\nID: {aid}\nApp: {app}\nParameters: {param_str}"

        @mcp.tool()
        async def zapier_list_zaps(ctx: Context, status: str = "") -> str:
            """List Zaps from your Zapier account.

            Args:
                status: Filter by status: 'on', 'off', or 'draft' (optional, leave empty for all)
            """
            client, uid, err = await token_store.require_service(ctx, "zapier", level="read")
            if err:
                return err
            params = {}
            if status and status.strip():
                params["status"] = status.strip()
            r, err = await token_store.safe_request(client, "GET", f"{PLATFORM_API}/zaps", service="Zapier", action="list zaps", params=params)
            if err:
                return err
            data = r.json()
            zaps = data.get("results", data.get("zaps", data.get("data", [])))
            if not zaps:
                filter_msg = f" with status '{status}'" if status else ""
                return f"No Zaps found{filter_msg}."
            lines = []
            for z in zaps:
                name = z.get("title", z.get("name", "Untitled"))
                zid = z.get("id", "?")
                zstatus = z.get("status", z.get("state", "?"))
                lines.append(f"{name} (ID: {zid}) | Status: {zstatus}")
            return "\n".join(lines)

        @mcp.tool()
        async def zapier_enable_zap(zap_id: str, ctx: Context) -> str:
            """Enable (turn on) a Zap.

            Args:
                zap_id: The Zap ID to enable
            """
            err = validation.validate_id(zap_id, "zap_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "zapier", level="write")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "PUT", f"{PLATFORM_API}/zaps/{zap_id}/enable",
                service="Zapier", action="enable zap",
            )
            if err:
                return err
            return f"Zap {zap_id} enabled."

        @mcp.tool()
        async def zapier_disable_zap(zap_id: str, ctx: Context) -> str:
            """Disable (turn off) a Zap.

            Args:
                zap_id: The Zap ID to disable
            """
            err = validation.validate_id(zap_id, "zap_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "zapier", level="write")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "PUT", f"{PLATFORM_API}/zaps/{zap_id}/disable",
                service="Zapier", action="disable zap",
            )
            if err:
                return err
            return f"Zap {zap_id} disabled."
