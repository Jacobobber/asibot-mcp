"""HubSpot connector: contacts and deals via HubSpot REST API."""

import logging

from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store, validation
from asibot.connectors.base import Connector
from asibot.connectors.pagination import collect, paginate_cursor

logger = logging.getLogger(__name__)
API = "https://api.hubapi.com"

_HUBSPOT_OBJECT_TYPES = frozenset({"contacts", "deals", "companies"})


class HubSpotConnector(Connector):
    def __init__(self, config=None):
        super().__init__(name="hubspot", config=config)

    async def connect(self):
        logger.info("HubSpot: ready (per-user token)")

    async def disconnect(self):
        pass

    async def fetch_documents(self):
        return []

    def register_tools(self, mcp: FastMCP):

        @mcp.tool()
        async def hubspot_search_contacts(query: str, ctx: Context, limit: int = 10) -> str:
            """Search HubSpot contacts.

            Args:
                query: Search query
                limit: Max results (default: 10)
            """
            err = validation.validate_query(query, "query")
            if err:
                return err
            limit = validation.validate_limit(limit)
            client, uid, err = await token_store.require_service(ctx, "hubspot", level="read")
            if err:
                return err
            pages = paginate_cursor(
                client, f"{API}/crm/v3/objects/contacts/search",
                service="HubSpot", action="search contacts",
                json_body={
                    "query": query,
                    "properties": ["firstname", "lastname", "email", "company", "phone"],
                },
                results_key="results",
                cursor_response_key="paging.next.after",
                cursor_request_key="after",
                cursor_in="json",
                page_size_param="limit",
                page_size=min(limit, 100),
            )
            results = await collect(pages, limit)
            if not results:
                return "No contacts found."
            lines = []
            for c in results:
                props = c.get("properties", {})
                name = f"{props.get('firstname', '')} {props.get('lastname', '')}".strip() or "Unknown"
                email = props.get("email", "no email")
                company = props.get("company", "no company")
                lines.append(f"{name} ({email})\n  Company: {company} | ID: {c.get('id', '?')}")
            return "\n\n".join(lines)

        @mcp.tool()
        async def hubspot_search_deals(query: str, ctx: Context, limit: int = 10) -> str:
            """Search HubSpot deals.

            Args:
                query: Search query
                limit: Max results (default: 10)
            """
            err = validation.validate_query(query, "query")
            if err:
                return err
            limit = validation.validate_limit(limit)
            client, uid, err = await token_store.require_service(ctx, "hubspot", level="read")
            if err:
                return err
            pages = paginate_cursor(
                client, f"{API}/crm/v3/objects/deals/search",
                service="HubSpot", action="search deals",
                json_body={
                    "query": query,
                    "properties": ["dealname", "dealstage", "amount", "closedate", "pipeline"],
                },
                results_key="results",
                cursor_response_key="paging.next.after",
                cursor_request_key="after",
                cursor_in="json",
                page_size_param="limit",
                page_size=min(limit, 100),
            )
            results = await collect(pages, limit)
            if not results:
                return "No deals found."
            lines = []
            for d in results:
                props = d.get("properties", {})
                name = props.get("dealname", "Untitled")
                stage = props.get("dealstage", "?")
                amount = props.get("amount", "?")
                close = props.get("closedate", "?")
                lines.append(f"{name}\n  Stage: {stage} | Amount: {amount} | Close: {close[:10] if close and close != '?' else close} | ID: {d.get('id', '?')}")
            return "\n\n".join(lines)

        @mcp.tool()
        async def hubspot_get_contact(contact_id: str, ctx: Context) -> str:
            """Get full details of a HubSpot contact.

            Args:
                contact_id: The contact ID
            """
            err = validation.validate_id(contact_id, "contact_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "hubspot", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "GET", f"{API}/crm/v3/objects/contacts/{contact_id}",
                service="HubSpot", action="get contact",
                params={"properties": "firstname,lastname,email,company,phone,jobtitle,lifecyclestage"},
            )
            if err:
                return err
            props = r.json().get("properties", {})
            name = f"{props.get('firstname', '')} {props.get('lastname', '')}".strip() or "Unknown"
            return (
                f"{name}\n"
                f"  Email: {props.get('email', '?')}\n"
                f"  Phone: {props.get('phone', '?')}\n"
                f"  Company: {props.get('company', '?')}\n"
                f"  Title: {props.get('jobtitle', '?')}\n"
                f"  Lifecycle: {props.get('lifecyclestage', '?')}"
            )

        @mcp.tool()
        async def hubspot_get_deal(deal_id: str, ctx: Context) -> str:
            """Get full details of a HubSpot deal.

            Args:
                deal_id: The deal ID
            """
            err = validation.validate_id(deal_id, "deal_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "hubspot", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "GET", f"{API}/crm/v3/objects/deals/{deal_id}",
                service="HubSpot", action="get deal",
                params={"properties": "dealname,dealstage,amount,closedate,pipeline,hubspot_owner_id,description"},
            )
            if err:
                return err
            props = r.json().get("properties", {})
            return (
                f"{props.get('dealname', 'Untitled')}\n"
                f"  Stage: {props.get('dealstage', '?')}\n"
                f"  Amount: {props.get('amount', '?')}\n"
                f"  Close date: {props.get('closedate', '?')}\n"
                f"  Pipeline: {props.get('pipeline', '?')}\n"
                f"  Owner ID: {props.get('hubspot_owner_id', '?')}\n"
                f"  Description: {props.get('description', 'No description')}"
            )

        @mcp.tool()
        async def hubspot_search_companies(query: str, ctx: Context, limit: int = 10) -> str:
            """Search HubSpot companies.

            Args:
                query: Search query
                limit: Max results (default: 10)
            """
            err = validation.validate_query(query, "query")
            if err:
                return err
            limit = validation.validate_limit(limit)
            client, uid, err = await token_store.require_service(ctx, "hubspot", level="read")
            if err:
                return err
            body = {
                "query": query,
                "limit": limit,
                "properties": ["name", "domain", "industry", "city"],
            }
            r, err = await token_store.safe_request(client, "POST", f"{API}/crm/v3/objects/companies/search", service="HubSpot", action="search companies", json=body)
            if err:
                return err
            results = r.json().get("results", [])
            if not results:
                return "No companies found."
            lines = []
            for c in results:
                props = c.get("properties", {})
                name = props.get("name", "Unknown")
                domain = props.get("domain", "no domain")
                industry = props.get("industry", "?")
                city = props.get("city", "?")
                lines.append(f"{name} ({domain})\n  Industry: {industry} | City: {city} | ID: {c.get('id', '?')}")
            return "\n\n".join(lines)

        @mcp.tool()
        async def hubspot_get_company(company_id: str, ctx: Context) -> str:
            """Get full details of a HubSpot company.

            Args:
                company_id: The company ID
            """
            err = validation.validate_id(company_id, "company_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "hubspot", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "GET", f"{API}/crm/v3/objects/companies/{company_id}",
                service="HubSpot", action="get company",
                params={"properties": "name,domain,industry,city,numberofemployees,annualrevenue"},
            )
            if err:
                return err
            props = r.json().get("properties", {})
            return (
                f"{props.get('name', 'Unknown')}\n"
                f"  Domain: {props.get('domain', '?')}\n"
                f"  Industry: {props.get('industry', '?')}\n"
                f"  City: {props.get('city', '?')}\n"
                f"  Employees: {props.get('numberofemployees', '?')}\n"
                f"  Annual Revenue: {props.get('annualrevenue', '?')}"
            )

        @mcp.tool()
        async def hubspot_list_pipelines(ctx: Context) -> str:
            """List HubSpot deal pipelines and their stages."""
            client, uid, err = await token_store.require_service(ctx, "hubspot", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "GET", f"{API}/crm/v3/pipelines/deals",
                service="HubSpot", action="list pipelines",
            )
            if err:
                return err
            pipelines = r.json().get("results", [])
            if not pipelines:
                return "No pipelines found."
            lines = []
            for p in pipelines:
                label = p.get("label", "Untitled")
                lines.append(f"{label} (ID: {p.get('id', '?')})")
                stages = sorted(p.get("stages", []), key=lambda s: s.get("displayOrder", 0))
                for s in stages:
                    lines.append(f"  {s.get('displayOrder', '?')}. {s.get('label', '?')} (ID: {s.get('id', '?')})")
            return "\n".join(lines)

        @mcp.tool()
        async def hubspot_get_activities(object_type: str, object_id: str, ctx: Context, limit: int = 10) -> str:
            """Get activity notes associated with a HubSpot object.

            Args:
                object_type: Object type (contacts, deals, companies)
                object_id: The object ID
                limit: Max results (default: 10)
            """
            if object_type not in _HUBSPOT_OBJECT_TYPES:
                return f"Invalid object_type: '{object_type}'. Allowed: {', '.join(sorted(_HUBSPOT_OBJECT_TYPES))}"
            err = validation.validate_id(object_id, "object_id")
            if err:
                return err
            limit = validation.validate_limit(limit)
            client, uid, err = await token_store.require_service(ctx, "hubspot", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "GET", f"{API}/crm/v3/objects/{object_type}/{object_id}/associations/notes",
                service="HubSpot", action="get activities",
                params={"limit": limit},
            )
            if err:
                return err
            results = r.json().get("results", [])
            if not results:
                return "No activities found."
            lines = []
            for a in results:
                lines.append(f"Note ID: {a.get('id', '?')} | Type: {a.get('type', '?')}")
            return "\n".join(lines)

        @mcp.tool()
        async def hubspot_create_contact(email: str, ctx: Context, firstname: str = "", lastname: str = "", company: str = "") -> str:
            """Create a new HubSpot contact.

            Args:
                email: Contact email address
                firstname: First name (optional)
                lastname: Last name (optional)
                company: Company name (optional)
            """
            err = validation.validate_email_address(email)
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "hubspot", level="write")
            if err:
                return err
            properties = {"email": email}
            if firstname:
                properties["firstname"] = firstname
            if lastname:
                properties["lastname"] = lastname
            if company:
                properties["company"] = company
            r, err = await token_store.safe_request(
                client, "POST", f"{API}/crm/v3/objects/contacts",
                service="HubSpot", action="create contact",
                json={"properties": properties},
            )
            if err:
                return err
            data = r.json()
            return f"Contact created. ID: {data.get('id', '?')}"

        @mcp.tool()
        async def hubspot_create_deal(dealname: str, pipeline: str, dealstage: str, ctx: Context, amount: str = "") -> str:
            """Create a new HubSpot deal.

            Args:
                dealname: Deal name
                pipeline: Pipeline ID
                dealstage: Deal stage ID
                amount: Deal amount (optional)
            """
            err = validation.validate_content(dealname, "dealname")
            if err:
                return err
            err = validation.validate_content(pipeline, "pipeline")
            if err:
                return err
            err = validation.validate_content(dealstage, "dealstage")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "hubspot", level="write")
            if err:
                return err
            properties = {
                "dealname": dealname,
                "pipeline": pipeline,
                "dealstage": dealstage,
            }
            if amount:
                properties["amount"] = amount
            r, err = await token_store.safe_request(
                client, "POST", f"{API}/crm/v3/objects/deals",
                service="HubSpot", action="create deal",
                json={"properties": properties},
            )
            if err:
                return err
            data = r.json()
            return f"Deal created. ID: {data.get('id', '?')}"
