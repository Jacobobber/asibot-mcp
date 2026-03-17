"""Paylocity connector: employee data via Paylocity REST API."""

import logging

import httpx
from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store
from asibot.connectors.base import Connector

logger = logging.getLogger(__name__)
API = "https://api.paylocity.com/api/v2"
TOKEN_URL = "https://api.paylocity.com/IdentityServer/connect/token"


def _make_client(creds):
    if not creds.get("client_id") or not creds.get("client_secret") or not creds.get("company_id"):
        return None
    # Paylocity uses client credentials; obtain a bearer token on the fly.
    # We store a cached token in creds to avoid re-auth every call.
    token = creds.get("_cached_token")
    if not token:
        r = httpx.post(
            TOKEN_URL,
            data={"grant_type": "client_credentials", "scope": "WebLinkAPI"},
            auth=(creds["client_id"], creds["client_secret"]),
            timeout=30.0,
        )
        r.raise_for_status()
        token = r.json().get("access_token")
        if not token:
            return None
    return httpx.AsyncClient(
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=30.0,
    )


class PaylocityConnector(Connector):
    def __init__(self, config=None):
        super().__init__(name="paylocity", config=config)

    async def connect(self):
        logger.info("Paylocity: ready (client credentials OAuth)")

    async def disconnect(self):
        pass

    async def fetch_documents(self):
        return []

    def register_tools(self, mcp: FastMCP):

        @mcp.tool()
        async def paylocity_list_employees(ctx: Context, limit: int = 25) -> str:
            """List employees from Paylocity.

            Args:
                limit: Max results (default: 25)
            """
            client, uid, err = token_store.require_service(ctx, "paylocity", _make_client, "read")
            if err:
                return err
            creds = token_store.get_credentials(uid, "paylocity")
            company_id = creds["company_id"]
            r = await client.get(f"{API}/companies/{company_id}/employees", params={"pagesize": limit})
            r.raise_for_status()
            employees = r.json()
            if not employees:
                return "No employees found."
            lines = []
            for emp in employees[:limit]:
                eid = emp.get("employeeId", "?")
                first = emp.get("firstName", "")
                last = emp.get("lastName", "")
                status = emp.get("statusType", "?")
                lines.append(f"{first} {last} (ID: {eid}) | Status: {status}")
            return "\n".join(lines)

        @mcp.tool()
        async def paylocity_get_employee(employee_id: str, ctx: Context) -> str:
            """Get details of a specific employee from Paylocity.

            Args:
                employee_id: The employee ID
            """
            client, uid, err = token_store.require_service(ctx, "paylocity", _make_client, "read")
            if err:
                return err
            creds = token_store.get_credentials(uid, "paylocity")
            company_id = creds["company_id"]
            r = await client.get(f"{API}/companies/{company_id}/employees/{employee_id}")
            r.raise_for_status()
            emp = r.json()
            first = emp.get("firstName", "")
            last = emp.get("lastName", "")
            status = emp.get("statusType", "?")
            dept = emp.get("departmentPosition", {}).get("departmentCode", "?")
            title = emp.get("departmentPosition", {}).get("jobTitle", "?")
            hire = emp.get("hireDate", "?")
            return f"{first} {last}\nID: {employee_id}\nStatus: {status}\nDepartment: {dept}\nTitle: {title}\nHire Date: {hire}"
