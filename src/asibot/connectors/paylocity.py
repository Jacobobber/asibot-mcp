"""Paylocity connector: employee data via Paylocity REST API."""

import logging

import httpx
from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store, validation
from asibot.connectors.base import Connector
from asibot.connectors.pagination import collect, paginate_offset

logger = logging.getLogger(__name__)
API = "https://api.paylocity.com/api/v2"
TOKEN_URL = "https://api.paylocity.com/IdentityServer/connect/token"


async def _get_access_token(creds: dict) -> str:
    """Exchange client credentials for a bearer token (cached, locked)."""
    return await token_store.get_s2s_token(
        cache_key=f"paylocity:{creds['client_id']}",
        token_url=TOKEN_URL,
        grant_data={"grant_type": "client_credentials", "scope": "WebLinkAPI"},
        auth=(creds["client_id"], creds["client_secret"]),
        service_name="Paylocity",
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
            client, uid, err = token_store.require_service(ctx, "paylocity", level="read")
            if err:
                return err
            creds = token_store.get_credentials(uid, "paylocity")
            try:
                token = await _get_access_token(creds)
            except (httpx.HTTPStatusError, httpx.RequestError, ValueError) as e:
                return token_store.format_api_error("Paylocity", "authenticate", e)
            company_id = creds["company_id"]
            pages = paginate_offset(
                client, f"{API}/companies/{company_id}/employees",
                service="Paylocity", action="list employees",
                params={},
                results_key=None,
                page_size_param="pagesize",
                offset_param="pagenumber",
                offset_start=1,
                offset_step=1,
                page_size=min(limit, 100),
                headers={"Authorization": f"Bearer {token}"},
            )
            employees = await collect(pages, limit)
            if not employees:
                return "No employees found."
            lines = []
            for emp in employees:
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
