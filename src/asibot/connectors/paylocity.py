"""Paylocity connector: employee data via Paylocity REST API."""

import logging
import time

import httpx
from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store, validation
from asibot.connectors.base import Connector

logger = logging.getLogger(__name__)
API = "https://api.paylocity.com/api/v2"
TOKEN_URL = "https://api.paylocity.com/IdentityServer/connect/token"

# Token cache: client_id -> (token, expires_at)
_token_cache: dict[str, tuple[str, float]] = {}
_TOKEN_MARGIN = 300  # refresh 5 min before expiry


async def _get_access_token(creds) -> str:
    """Exchange client credentials for a bearer token (cached)."""
    client_id = creds["client_id"]
    cached = _token_cache.get(client_id)
    if cached:
        token, expires_at = cached
        if time.time() < expires_at - _TOKEN_MARGIN:
            return token

    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.post(
            TOKEN_URL,
            data={"grant_type": "client_credentials", "scope": "WebLinkAPI"},
            auth=(client_id, creds["client_secret"]),
        )
        r.raise_for_status()
        data = r.json()
        token = data.get("access_token")
        if not token:
            raise ValueError("Paylocity OAuth response missing access_token")
        expires_in = data.get("expires_in", 3600)
        _token_cache[client_id] = (token, time.time() + expires_in)
        return token


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
            client, uid, err = token_store.require_service(ctx, "paylocity", level="read")
            if err:
                return err
            creds = token_store.get_credentials(uid, "paylocity")
            try:
                token = await _get_access_token(creds)
            except (httpx.HTTPStatusError, httpx.RequestError, ValueError) as e:
                return token_store.format_api_error("Paylocity", "authenticate", e)
            company_id = creds["company_id"]
            r, err = await token_store.safe_request(
                client, "GET", f"{API}/companies/{company_id}/employees",
                service="Paylocity", action="list employees",
                headers={"Authorization": f"Bearer {token}"},
                params={"pagesize": limit},
            )
            if err:
                return err
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
            err = validation.validate_id(employee_id, "employee_id")
            if err:
                return err
            client, uid, err = token_store.require_service(ctx, "paylocity", level="read")
            if err:
                return err
            creds = token_store.get_credentials(uid, "paylocity")
            try:
                token = await _get_access_token(creds)
            except (httpx.HTTPStatusError, httpx.RequestError, ValueError) as e:
                return token_store.format_api_error("Paylocity", "authenticate", e)
            company_id = creds["company_id"]
            r, err = await token_store.safe_request(
                client, "GET", f"{API}/companies/{company_id}/employees/{employee_id}",
                service="Paylocity", action="get employee",
                headers={"Authorization": f"Bearer {token}"},
            )
            if err:
                return err
            emp = r.json()
            first = emp.get("firstName", "")
            last = emp.get("lastName", "")
            status = emp.get("statusType", "?")
            dept = emp.get("departmentPosition", {}).get("departmentCode", "?")
            title = emp.get("departmentPosition", {}).get("jobTitle", "?")
            hire = emp.get("hireDate", "?")
            return f"{first} {last}\nID: {employee_id}\nStatus: {status}\nDepartment: {dept}\nTitle: {title}\nHire Date: {hire}"
