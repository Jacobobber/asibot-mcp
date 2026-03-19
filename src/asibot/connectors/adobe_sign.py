"""Adobe Sign connector: agreements via Adobe Sign REST API."""

import logging

from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store, validation
from asibot.connectors.base import Connector
from asibot.connectors.pagination import collect, paginate_cursor

logger = logging.getLogger(__name__)
API = "https://api.na1.adobesign.com/api/rest/v6"


class AdobeSignConnector(Connector):
    def __init__(self, config=None):
        super().__init__(name="adobe_sign", config=config)

    async def connect(self):
        logger.info("Adobe Sign: ready (per-user OAuth token)")

    async def disconnect(self):
        pass

    async def fetch_documents(self):
        return []

    def register_tools(self, mcp: FastMCP):

        @mcp.tool()
        async def adobe_sign_list_agreements(ctx: Context, limit: int = 20) -> str:
            """List Adobe Sign agreements.

            Args:
                limit: Max results (default: 20)
            """
            client, uid, err = await token_store.require_service(ctx, "adobe_sign", level="read")
            if err:
                return err
            pages = paginate_cursor(
                client, f"{API}/agreements",
                method="GET",
                service="Adobe Sign", action="list agreements",
                results_key="userAgreementList",
                cursor_response_key="page.nextCursor",
                cursor_request_key="cursor",
                cursor_in="params",
                page_size_param="pageSize",
                page_size=min(limit, 100),
            )
            agreements = await collect(pages, limit)
            if not agreements:
                return "No agreements found."
            return "\n\n".join(
                f"{a.get('name', 'Untitled')}\n  ID: {a.get('id', '?')} | Status: {a.get('status', '?')} | Modified: {(a.get('lastEventDate') or '?')[:10]}"
                for a in agreements
            )

        @mcp.tool()
        async def adobe_sign_get_agreement(agreement_id: str, ctx: Context) -> str:
            """Get full details of an Adobe Sign agreement.

            Args:
                agreement_id: The agreement ID
            """
            err = validation.validate_id(agreement_id, "agreement_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "adobe_sign", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(client, "GET", f"{API}/agreements/{agreement_id}", service="Adobe Sign", action="get agreement")
            if err:
                return err
            a = r.json()
            # Get participant info
            participants = []
            for p_set in a.get("participantSetsInfo", []):
                for p in p_set.get("memberInfos", []):
                    participants.append(f"{p.get('email', '?')} ({p_set.get('role', '?')})")
            part_str = ", ".join(participants) if participants else "none"
            return (
                f"{a.get('name', 'Untitled')}\n"
                f"ID: {a.get('id', '?')} | Status: {a.get('status', '?')}\n"
                f"Created: {a.get('createdDate', '?')}\n"
                f"Message: {a.get('message', 'None')}\n"
                f"Participants: {part_str}"
            )

        @mcp.tool()
        async def adobe_sign_get_signing_urls(agreement_id: str, ctx: Context) -> str:
            """Get signing URLs for an Adobe Sign agreement.

            Args:
                agreement_id: The agreement ID
            """
            err = validation.validate_id(agreement_id, "agreement_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "adobe_sign", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(client, "GET", f"{API}/agreements/{agreement_id}/signingUrls", service="Adobe Sign", action="get signing URLs")
            if err:
                return err
            data = r.json()
            signing_url_sets = data.get("signingUrlSetInfos", [])
            if not signing_url_sets:
                return "No signing URLs available for this agreement."
            lines = []
            for url_set in signing_url_sets:
                for url_info in url_set.get("signingUrls", []):
                    email = url_info.get("email", "?")
                    url = url_info.get("esignUrl", "?")
                    lines.append(f"{email}: {url}")
            return "\n".join(lines) if lines else "No signing URLs available for this agreement."

        @mcp.tool()
        async def adobe_sign_get_audit_trail(agreement_id: str, ctx: Context) -> str:
            """Get the audit trail for an Adobe Sign agreement.

            Args:
                agreement_id: The agreement ID
            """
            err = validation.validate_id(agreement_id, "agreement_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "adobe_sign", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(client, "GET", f"{API}/agreements/{agreement_id}/auditTrail", service="Adobe Sign", action="get audit trail")
            if err:
                return err
            data = r.json()
            events = data.get("events", [])
            if not events:
                return "No audit trail events found."
            lines = []
            for event in events:
                date = event.get("date", "?")
                description = event.get("description", "?")
                acting_user = event.get("actingUserEmail", event.get("participantEmail", "?"))
                event_type = event.get("type", "?")
                lines.append(f"{date} | {event_type} | {acting_user} | {description}")
            return "\n".join(lines)

        @mcp.tool()
        async def adobe_sign_list_templates(ctx: Context, limit: int = 20) -> str:
            """List library document templates from Adobe Sign.

            Args:
                limit: Max results (default: 20)
            """
            limit = validation.validate_limit(limit)
            client, uid, err = await token_store.require_service(ctx, "adobe_sign", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(client, "GET", f"{API}/libraryDocuments", service="Adobe Sign", action="list templates", params={"pageSize": limit})
            if err:
                return err
            templates = r.json().get("libraryDocumentList", [])
            if not templates:
                return "No templates found."
            lines = []
            for t in templates:
                name = t.get("name", "Untitled")
                tid = t.get("id", "?")
                modified = (t.get("modifiedDate") or "?")[:10]
                lines.append(f"{name} (ID: {tid}) | Modified: {modified}")
            return "\n".join(lines)

        @mcp.tool()
        async def adobe_sign_get_form_data(agreement_id: str, ctx: Context) -> str:
            """Get form field data from a completed Adobe Sign agreement.

            Args:
                agreement_id: The agreement ID
            """
            err = validation.validate_id(agreement_id, "agreement_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "adobe_sign", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(client, "GET", f"{API}/agreements/{agreement_id}/formData", service="Adobe Sign", action="get form data")
            if err:
                return err
            # formData endpoint returns CSV text
            text = r.text if hasattr(r, "text") and r.text else r.json() if callable(getattr(r, "json", None)) else str(r)
            if not text or text == "{}":
                return "No form data available for this agreement."
            return f"Form data for agreement {agreement_id}:\n{text}"

        @mcp.tool()
        async def adobe_sign_send_agreement(
            name: str, recipient_emails: list[str], template_id: str, ctx: Context, message: str = ""
        ) -> str:
            """Send an Adobe Sign agreement for signature.

            Args:
                name: Agreement name
                recipient_emails: List of signer email addresses
                template_id: Library template ID to use
                message: Optional message for signers
            """
            err = validation.validate_content(name, "name")
            if err:
                return err
            err = validation.validate_id(template_id, "template_id")
            if err:
                return err
            if not recipient_emails:
                return "recipient_emails is required."
            for email in recipient_emails:
                err = validation.validate_email_address(email)
                if err:
                    return err
            client, uid, err = await token_store.require_service(ctx, "adobe_sign", level="write")
            if err:
                return err
            participant_sets = [
                {
                    "memberInfos": [{"email": email}],
                    "order": idx + 1,
                    "role": "SIGNER",
                }
                for idx, email in enumerate(recipient_emails)
            ]
            payload = {
                "name": name,
                "participantSetsInfo": participant_sets,
                "signatureType": "ESIGN",
                "state": "IN_PROCESS",
                "fileInfos": [{"libraryDocumentId": template_id}],
            }
            if message:
                payload["message"] = message
            r, err = await token_store.safe_request(
                client, "POST", f"{API}/agreements",
                service="Adobe Sign", action="send agreement",
                json=payload,
            )
            if err:
                return err
            data = r.json()
            return f"Agreement sent.\nID: {data.get('id', '?')}\nName: {name}\nRecipients: {', '.join(recipient_emails)}"

        @mcp.tool()
        async def adobe_sign_send_reminder(agreement_id: str, ctx: Context, message: str = "") -> str:
            """Send a signing reminder for an Adobe Sign agreement.

            Args:
                agreement_id: The agreement ID
                message: Optional reminder message
            """
            err = validation.validate_id(agreement_id, "agreement_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "adobe_sign", level="write")
            if err:
                return err
            payload = {"agreementId": agreement_id}
            if message:
                payload["comment"] = message
            r, err = await token_store.safe_request(
                client, "POST", f"{API}/reminders",
                service="Adobe Sign", action="send reminder",
                json=payload,
            )
            if err:
                return err
            return f"Reminder sent for agreement {agreement_id}."

        @mcp.tool()
        async def adobe_sign_cancel_agreement(agreement_id: str, ctx: Context, comment: str = "") -> str:
            """Cancel an Adobe Sign agreement.

            Args:
                agreement_id: The agreement ID
                comment: Optional cancellation reason
            """
            err = validation.validate_id(agreement_id, "agreement_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "adobe_sign", level="write")
            if err:
                return err
            payload = {"value": "CANCEL"}
            if comment:
                payload["comment"] = comment
            r, err = await token_store.safe_request(
                client, "PUT", f"{API}/agreements/{agreement_id}/state",
                service="Adobe Sign", action="cancel agreement",
                json=payload,
            )
            if err:
                return err
            return f"Agreement {agreement_id} has been cancelled."

        @mcp.tool()
        async def adobe_sign_download_document(agreement_id: str, ctx: Context) -> str:
            """Get the download URL for a signed Adobe Sign document.

            Args:
                agreement_id: The agreement ID
            """
            err = validation.validate_id(agreement_id, "agreement_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "adobe_sign", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "GET", f"{API}/agreements/{agreement_id}/combinedDocument/url",
                service="Adobe Sign", action="download document",
            )
            if err:
                return err
            data = r.json()
            url = data.get("url", "?")
            return f"Download URL for agreement {agreement_id}:\n{url}"
