"""Tests for niche connectors: Adobe Sign, Citrix ShareFile, Concur,
LinkSquares, Paylocity, RingCentral, Roboflow, Zapier, SAP, Zoom.

These connectors use token_store.require_service() for auth.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from asibot import token_store


# --- Helpers ---


def _mock_response(status_code=200, json_data=None, text="", headers=None):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text
    resp.headers = headers or {}
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}", request=MagicMock(), response=resp
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


def _mock_client(responses):
    client = AsyncMock(spec=httpx.AsyncClient)
    if isinstance(responses, list):
        client.request = AsyncMock(side_effect=responses)
        client.get = AsyncMock(side_effect=responses)
        client.post = AsyncMock(side_effect=responses)
    else:
        client.request = AsyncMock(return_value=responses)
        client.get = AsyncMock(return_value=responses)
        client.post = AsyncMock(return_value=responses)
    return client


def _patch_require_service(service, client, uid="test@example.com"):
    return patch.object(
        token_store, "require_service",
        new_callable=AsyncMock,
        return_value=(client, uid, None),
    )


def _patch_get_creds(service, creds):
    return patch.object(
        token_store, "get_credentials",
        return_value=creds,
    )


def _register_tools(connector_cls):
    mcp = MagicMock()
    tools = {}
    mcp.tool = lambda: lambda f: tools.setdefault(f.__name__, f) or f
    connector_cls().register_tools(mcp)
    return tools


# --- Adobe Sign Connector Tests ---


class TestAdobeSignListAgreements:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.adobe_sign import AdobeSignConnector
        self.tools = _register_tools(AdobeSignConnector)

    @pytest.mark.asyncio
    async def test_list_success(self):
        resp = _mock_response(200, {
            "userAgreementList": [
                {"name": "NDA with Acme", "id": "agr-001", "status": "SIGNED", "lastEventDate": "2024-05-15T00:00:00Z"},
                {"name": "SOW Q3", "id": "agr-002", "status": "OUT_FOR_SIGNATURE", "lastEventDate": "2024-06-01T00:00:00Z"},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("adobe_sign", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["adobe_sign_list_agreements"](ctx)
        assert "NDA with Acme" in result
        assert "SIGNED" in result
        assert "SOW Q3" in result

    @pytest.mark.asyncio
    async def test_list_empty(self):
        resp = _mock_response(200, {"userAgreementList": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("adobe_sign", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["adobe_sign_list_agreements"](ctx)
        assert "No agreements found" in result


class TestAdobeSignGetAgreement:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.adobe_sign import AdobeSignConnector
        self.tools = _register_tools(AdobeSignConnector)

    @pytest.mark.asyncio
    async def test_get_success(self):
        resp = _mock_response(200, {
            "name": "Master Service Agreement",
            "id": "agr-010",
            "status": "SIGNED",
            "createdDate": "2024-01-10T00:00:00Z",
            "message": "Please review and sign",
            "participantSetsInfo": [
                {
                    "role": "SIGNER",
                    "memberInfos": [{"email": "signer@acme.com"}],
                },
            ],
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("adobe_sign", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["adobe_sign_get_agreement"]("agr-010", ctx)
        assert "Master Service Agreement" in result
        assert "signer@acme.com" in result
        assert "SIGNER" in result

    @pytest.mark.asyncio
    async def test_get_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["adobe_sign_get_agreement"]("", ctx)
        assert "required" in result.lower()


class TestAdobeSignGetSigningUrls:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.adobe_sign import AdobeSignConnector
        self.tools = _register_tools(AdobeSignConnector)

    @pytest.mark.asyncio
    async def test_get_signing_urls_success(self):
        resp = _mock_response(200, {
            "signingUrlSetInfos": [
                {
                    "signingUrls": [
                        {"email": "signer@acme.com", "esignUrl": "https://sign.example.com/sign/abc"},
                    ]
                }
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("adobe_sign", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["adobe_sign_get_signing_urls"]("agr-001", ctx)
        assert "signer@acme.com" in result
        assert "https://sign.example.com/sign/abc" in result

    @pytest.mark.asyncio
    async def test_get_signing_urls_empty(self):
        resp = _mock_response(200, {"signingUrlSetInfos": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("adobe_sign", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["adobe_sign_get_signing_urls"]("agr-001", ctx)
        assert "No signing URLs" in result

    @pytest.mark.asyncio
    async def test_get_signing_urls_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["adobe_sign_get_signing_urls"]("", ctx)
        assert "required" in result.lower()


class TestAdobeSignGetAuditTrail:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.adobe_sign import AdobeSignConnector
        self.tools = _register_tools(AdobeSignConnector)

    @pytest.mark.asyncio
    async def test_audit_trail_success(self):
        resp = _mock_response(200, {
            "events": [
                {"date": "2024-06-01T10:00:00Z", "type": "CREATED", "actingUserEmail": "admin@acme.com", "description": "Agreement created"},
                {"date": "2024-06-02T14:00:00Z", "type": "SIGNED", "actingUserEmail": "signer@acme.com", "description": "Agreement signed"},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("adobe_sign", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["adobe_sign_get_audit_trail"]("agr-001", ctx)
        assert "CREATED" in result
        assert "admin@acme.com" in result
        assert "SIGNED" in result
        assert "Agreement signed" in result

    @pytest.mark.asyncio
    async def test_audit_trail_empty(self):
        resp = _mock_response(200, {"events": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("adobe_sign", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["adobe_sign_get_audit_trail"]("agr-001", ctx)
        assert "No audit trail events" in result

    @pytest.mark.asyncio
    async def test_audit_trail_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["adobe_sign_get_audit_trail"]("", ctx)
        assert "required" in result.lower()


class TestAdobeSignListTemplates:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.adobe_sign import AdobeSignConnector
        self.tools = _register_tools(AdobeSignConnector)

    @pytest.mark.asyncio
    async def test_list_templates_success(self):
        resp = _mock_response(200, {
            "libraryDocumentList": [
                {"name": "NDA Template", "id": "tpl-001", "modifiedDate": "2024-03-15T00:00:00Z"},
                {"name": "SOW Template", "id": "tpl-002", "modifiedDate": "2024-04-01T00:00:00Z"},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("adobe_sign", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["adobe_sign_list_templates"](ctx)
        assert "NDA Template" in result
        assert "tpl-001" in result
        assert "SOW Template" in result

    @pytest.mark.asyncio
    async def test_list_templates_empty(self):
        resp = _mock_response(200, {"libraryDocumentList": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("adobe_sign", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["adobe_sign_list_templates"](ctx)
        assert "No templates found" in result


class TestAdobeSignGetFormData:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.adobe_sign import AdobeSignConnector
        self.tools = _register_tools(AdobeSignConnector)

    @pytest.mark.asyncio
    async def test_get_form_data_success(self):
        resp = _mock_response(200, json_data={"formData": "field1,field2\nval1,val2"}, text="field1,field2\nval1,val2")
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("adobe_sign", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["adobe_sign_get_form_data"]("agr-001", ctx)
        assert "Form data" in result
        assert "agr-001" in result

    @pytest.mark.asyncio
    async def test_get_form_data_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["adobe_sign_get_form_data"]("", ctx)
        assert "required" in result.lower()


# --- Citrix ShareFile Connector Tests ---


class TestShareFileListItems:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.citrix_sharefile import ShareFileConnector
        self.tools = _register_tools(ShareFileConnector)

    @pytest.mark.asyncio
    async def test_list_success(self):
        resp = _mock_response(200, {
            "value": [
                {"FileName": "Report.pdf", "odata.type": "ShareFile.Api.Models.File", "FileSizeBytes": 204800, "CreationDate": "2024-03-01T00:00:00Z"},
                {"FileName": "Archives", "odata.type": "ShareFile.Api.Models.Folder", "FileSizeBytes": 0, "CreationDate": "2024-01-15T00:00:00Z"},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("sharefile", client), \
             _patch_get_creds("sharefile", {"subdomain": "mycompany", "token": "tok"}), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["sharefile_list_items"](ctx)
        assert "Report.pdf" in result
        assert "Archives" in result
        assert "204800" in result

    @pytest.mark.asyncio
    async def test_list_empty(self):
        resp = _mock_response(200, {"value": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("sharefile", client), \
             _patch_get_creds("sharefile", {"subdomain": "mycompany", "token": "tok"}), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["sharefile_list_items"](ctx)
        assert "No items found" in result


class TestShareFileSearch:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.citrix_sharefile import ShareFileConnector
        self.tools = _register_tools(ShareFileConnector)

    @pytest.mark.asyncio
    async def test_search_success(self):
        resp = _mock_response(200, {
            "value": [
                {"FileName": "Budget2024.xlsx", "ParentName": "Finance", "FileSizeBytes": 51200},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("sharefile", client), \
             _patch_get_creds("sharefile", {"subdomain": "mycompany", "token": "tok"}), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["sharefile_search"]("budget", ctx)
        assert "Budget2024.xlsx" in result
        assert "Finance" in result

    @pytest.mark.asyncio
    async def test_search_empty(self):
        resp = _mock_response(200, {"value": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("sharefile", client), \
             _patch_get_creds("sharefile", {"subdomain": "mycompany", "token": "tok"}), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["sharefile_search"]("nothing", ctx)
        assert "No results found" in result


class TestShareFileGetItem:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.citrix_sharefile import ShareFileConnector
        self.tools = _register_tools(ShareFileConnector)

    @pytest.mark.asyncio
    async def test_get_item_success(self):
        resp = _mock_response(200, {
            "FileName": "Report.pdf",
            "odata.type": "ShareFile.Api.Models.File",
            "FileSizeBytes": 204800,
            "CreationDate": "2024-03-01T00:00:00Z",
            "CreatorNameShort": "Alice",
            "ParentName": "Documents",
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("sharefile", client), \
             _patch_get_creds("sharefile", {"subdomain": "mycompany", "token": "tok"}), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["sharefile_get_item"]("item-001", ctx)
        assert "Report.pdf" in result
        assert "File" in result
        assert "204800" in result
        assert "Alice" in result

    @pytest.mark.asyncio
    async def test_get_item_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["sharefile_get_item"]("", ctx)
        assert "required" in result.lower()


class TestShareFileDownloadText:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.citrix_sharefile import ShareFileConnector
        self.tools = _register_tools(ShareFileConnector)

    @pytest.mark.asyncio
    async def test_download_text_success(self):
        resp = _mock_response(200, text="Hello, World!\nLine 2", headers={"Content-Type": "text/plain"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("sharefile", client), \
             _patch_get_creds("sharefile", {"subdomain": "mycompany", "token": "tok"}), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["sharefile_download_text"]("item-001", ctx)
        assert "Hello, World!" in result
        assert "Line 2" in result

    @pytest.mark.asyncio
    async def test_download_binary_rejected(self):
        resp = _mock_response(200, text="binary data", headers={"Content-Type": "application/octet-stream"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("sharefile", client), \
             _patch_get_creds("sharefile", {"subdomain": "mycompany", "token": "tok"}), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["sharefile_download_text"]("item-001", ctx)
        assert "binary" in result.lower() or "Cannot display" in result

    @pytest.mark.asyncio
    async def test_download_text_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["sharefile_download_text"]("", ctx)
        assert "required" in result.lower()


class TestShareFileListShared:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.citrix_sharefile import ShareFileConnector
        self.tools = _register_tools(ShareFileConnector)

    @pytest.mark.asyncio
    async def test_list_shared_success(self):
        resp = _mock_response(200, {
            "value": [
                {"Name": "Shared Report", "Id": "share-001", "CreationDate": "2024-05-10T00:00:00Z"},
                {"Name": "Shared Budget", "Id": "share-002", "CreationDate": "2024-06-01T00:00:00Z"},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("sharefile", client), \
             _patch_get_creds("sharefile", {"subdomain": "mycompany", "token": "tok"}), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["sharefile_list_shared"](ctx)
        assert "Shared Report" in result
        assert "Shared Budget" in result

    @pytest.mark.asyncio
    async def test_list_shared_empty(self):
        resp = _mock_response(200, {"value": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("sharefile", client), \
             _patch_get_creds("sharefile", {"subdomain": "mycompany", "token": "tok"}), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["sharefile_list_shared"](ctx)
        assert "No shared items found" in result


# --- Concur Connector Tests ---


class TestConcurListReports:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.concur import ConcurConnector
        self.tools = _register_tools(ConcurConnector)

    @pytest.mark.asyncio
    async def test_list_success(self):
        resp = _mock_response(200, {
            "Items": [
                {"Name": "Trip to NYC", "ID": "rpt-001", "Status": "Approved", "Total": 1250.00, "CurrencyCode": "USD"},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("concur", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["concur_list_reports"](ctx)
        assert "Trip to NYC" in result
        assert "Approved" in result
        assert "USD" in result

    @pytest.mark.asyncio
    async def test_list_empty(self):
        resp = _mock_response(200, {"Items": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("concur", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["concur_list_reports"](ctx)
        assert "No expense reports found" in result


class TestConcurGetReport:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.concur import ConcurConnector
        self.tools = _register_tools(ConcurConnector)

    @pytest.mark.asyncio
    async def test_get_success(self):
        resp = _mock_response(200, {
            "Name": "Q1 Travel",
            "Status": "Submitted",
            "Total": 3000.00,
            "CurrencyCode": "USD",
            "CreateDate": "2024-03-15T00:00:00Z",
            "OwnerName": "Alice Smith",
            "Entries": [
                {"Description": "Flight to SFO", "TransactionAmount": 800},
                {"Description": "Hotel 3 nights", "TransactionAmount": 1500},
            ],
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("concur", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["concur_get_report"]("rpt-001", ctx)
        assert "Q1 Travel" in result
        assert "Alice Smith" in result
        assert "Flight to SFO" in result
        assert "Hotel 3 nights" in result

    @pytest.mark.asyncio
    async def test_get_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["concur_get_report"]("", ctx)
        assert "required" in result.lower()


class TestConcurListExpenses:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.concur import ConcurConnector
        self.tools = _register_tools(ConcurConnector)

    @pytest.mark.asyncio
    async def test_list_expenses_success(self):
        resp = _mock_response(200, {
            "Items": [
                {"Description": "Taxi to airport", "ID": "exp-001", "TransactionAmount": 45.00, "TransactionCurrencyCode": "USD", "TransactionDate": "2024-03-15T00:00:00Z"},
                {"Description": "Business lunch", "ID": "exp-002", "TransactionAmount": 85.00, "TransactionCurrencyCode": "USD", "TransactionDate": "2024-03-16T00:00:00Z"},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("concur", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["concur_list_expenses"]("rpt-001", ctx)
        assert "Taxi to airport" in result
        assert "Business lunch" in result
        assert "USD" in result

    @pytest.mark.asyncio
    async def test_list_expenses_empty(self):
        resp = _mock_response(200, {"Items": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("concur", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["concur_list_expenses"]("rpt-001", ctx)
        assert "No expense entries found" in result

    @pytest.mark.asyncio
    async def test_list_expenses_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["concur_list_expenses"]("", ctx)
        assert "required" in result.lower()


class TestConcurGetExpense:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.concur import ConcurConnector
        self.tools = _register_tools(ConcurConnector)

    @pytest.mark.asyncio
    async def test_get_expense_success(self):
        resp = _mock_response(200, {
            "Description": "Flight to NYC",
            "TransactionAmount": 450.00,
            "TransactionCurrencyCode": "USD",
            "TransactionDate": "2024-03-15T00:00:00Z",
            "VendorDescription": "Delta Airlines",
            "ExpenseTypeName": "Airfare",
            "ReportID": "rpt-001",
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("concur", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["concur_get_expense"]("exp-001", ctx)
        assert "Flight to NYC" in result
        assert "Delta Airlines" in result
        assert "Airfare" in result
        assert "USD" in result

    @pytest.mark.asyncio
    async def test_get_expense_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["concur_get_expense"]("", ctx)
        assert "required" in result.lower()


class TestConcurListApprovals:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.concur import ConcurConnector
        self.tools = _register_tools(ConcurConnector)

    @pytest.mark.asyncio
    async def test_list_approvals_success(self):
        resp = _mock_response(200, {
            "Items": [
                {"Name": "March Travel", "ID": "rpt-010", "OwnerName": "Bob Jones", "Total": 2500.00, "CurrencyCode": "USD"},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("concur", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["concur_list_approvals"](ctx)
        assert "March Travel" in result
        assert "Bob Jones" in result
        assert "USD" in result

    @pytest.mark.asyncio
    async def test_list_approvals_empty(self):
        resp = _mock_response(200, {"Items": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("concur", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["concur_list_approvals"](ctx)
        assert "No reports pending approval" in result


# --- LinkSquares Connector Tests ---


class TestLinkSquaresListContracts:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.linksquares import LinkSquaresConnector
        self.tools = _register_tools(LinkSquaresConnector)

    @pytest.mark.asyncio
    async def test_list_success(self):
        resp = _mock_response(200, {
            "contracts": [
                {"title": "Vendor Agreement", "id": "c-001", "status": "Active", "counterparty": "Vendor Co", "effective_date": "2024-01-01"},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("linksquares", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["linksquares_list_contracts"](ctx)
        assert "Vendor Agreement" in result
        assert "Vendor Co" in result
        assert "Active" in result

    @pytest.mark.asyncio
    async def test_list_empty(self):
        resp = _mock_response(200, {"contracts": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("linksquares", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["linksquares_list_contracts"](ctx)
        assert "No contracts found" in result


class TestLinkSquaresSearch:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.linksquares import LinkSquaresConnector
        self.tools = _register_tools(LinkSquaresConnector)

    @pytest.mark.asyncio
    async def test_search_success(self):
        resp = _mock_response(200, {
            "contracts": [
                {"title": "NDA - Partner X", "id": "c-010", "counterparty": "Partner X", "status": "Executed"},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("linksquares", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["linksquares_search"]("NDA", ctx)
        assert "NDA - Partner X" in result
        assert "Partner X" in result

    @pytest.mark.asyncio
    async def test_search_empty(self):
        resp = _mock_response(200, {"contracts": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("linksquares", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["linksquares_search"]("nothing", ctx)
        assert "No matching contracts found" in result


class TestLinkSquaresGetContract:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.linksquares import LinkSquaresConnector
        self.tools = _register_tools(LinkSquaresConnector)

    @pytest.mark.asyncio
    async def test_get_contract_success(self):
        resp = _mock_response(200, {
            "title": "Master Agreement",
            "status": "Active",
            "counterparty": "Acme Inc",
            "effective_date": "2024-01-01",
            "expiration_date": "2025-01-01",
            "type": "MSA",
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("linksquares", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["linksquares_get_contract"]("c-001", ctx)
        assert "Master Agreement" in result
        assert "Acme Inc" in result
        assert "MSA" in result
        assert "2025-01-01" in result

    @pytest.mark.asyncio
    async def test_get_contract_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["linksquares_get_contract"]("", ctx)
        assert "required" in result.lower()


class TestLinkSquaresListSmartValues:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.linksquares import LinkSquaresConnector
        self.tools = _register_tools(LinkSquaresConnector)

    @pytest.mark.asyncio
    async def test_list_smart_values_success(self):
        resp = _mock_response(200, {
            "smart_values": [
                {"name": "Payment Terms", "value": "Net 30"},
                {"name": "Governing Law", "value": "Delaware"},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("linksquares", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["linksquares_list_smart_values"]("c-001", ctx)
        assert "Payment Terms" in result
        assert "Net 30" in result
        assert "Governing Law" in result
        assert "Delaware" in result

    @pytest.mark.asyncio
    async def test_list_smart_values_empty(self):
        resp = _mock_response(200, {"smart_values": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("linksquares", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["linksquares_list_smart_values"]("c-001", ctx)
        assert "No smart values found" in result

    @pytest.mark.asyncio
    async def test_list_smart_values_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["linksquares_list_smart_values"]("", ctx)
        assert "required" in result.lower()


class TestLinkSquaresListAmendments:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.linksquares import LinkSquaresConnector
        self.tools = _register_tools(LinkSquaresConnector)

    @pytest.mark.asyncio
    async def test_list_amendments_success(self):
        resp = _mock_response(200, {
            "amendments": [
                {"id": "amd-001", "title": "Rate Increase", "effective_date": "2024-07-01", "status": "Active"},
                {"id": "amd-002", "title": "Term Extension", "effective_date": "2024-10-01", "status": "Pending"},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("linksquares", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["linksquares_list_amendments"]("c-001", ctx)
        assert "Rate Increase" in result
        assert "Term Extension" in result
        assert "2024-07-01" in result

    @pytest.mark.asyncio
    async def test_list_amendments_empty(self):
        resp = _mock_response(200, {"amendments": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("linksquares", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["linksquares_list_amendments"]("c-001", ctx)
        assert "No amendments found" in result

    @pytest.mark.asyncio
    async def test_list_amendments_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["linksquares_list_amendments"]("", ctx)
        assert "required" in result.lower()


# --- RingCentral Connector Tests ---


class TestRingCentralCallLog:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.ringcentral import RingCentralConnector
        self.tools = _register_tools(RingCentralConnector)

    @pytest.mark.asyncio
    async def test_call_log_success(self):
        resp = _mock_response(200, {
            "records": [
                {
                    "direction": "Inbound",
                    "result": "Accepted",
                    "startTime": "2024-06-01T10:30:00Z",
                    "from": {"name": "John Caller"},
                },
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("ringcentral", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["ringcentral_call_log"](ctx)
        assert "Inbound" in result
        assert "John Caller" in result
        assert "Accepted" in result

    @pytest.mark.asyncio
    async def test_call_log_empty(self):
        resp = _mock_response(200, {"records": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("ringcentral", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["ringcentral_call_log"](ctx)
        assert "No call log entries found" in result


class TestRingCentralMessages:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.ringcentral import RingCentralConnector
        self.tools = _register_tools(RingCentralConnector)

    @pytest.mark.asyncio
    async def test_messages_success(self):
        resp = _mock_response(200, {
            "records": [
                {
                    "direction": "Outbound",
                    "type": "SMS",
                    "creationTime": "2024-06-02T14:00:00Z",
                    "subject": "Meeting reminder",
                },
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("ringcentral", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["ringcentral_messages"](ctx)
        assert "Outbound" in result
        assert "SMS" in result
        assert "Meeting reminder" in result

    @pytest.mark.asyncio
    async def test_messages_empty(self):
        resp = _mock_response(200, {"records": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("ringcentral", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["ringcentral_messages"](ctx)
        assert "No messages found" in result


class TestRingCentralGetCallRecording:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.ringcentral import RingCentralConnector
        self.tools = _register_tools(RingCentralConnector)

    @pytest.mark.asyncio
    async def test_get_recording_success(self):
        resp = _mock_response(200, {
            "id": "rec-001",
            "contentUri": "https://platform.ringcentral.com/recording/rec-001/content",
            "duration": 120,
            "type": "Automatic",
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("ringcentral", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["ringcentral_get_call_recording"]("rec-001", ctx)
        assert "rec-001" in result
        assert "120" in result
        assert "Automatic" in result

    @pytest.mark.asyncio
    async def test_get_recording_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["ringcentral_get_call_recording"]("", ctx)
        assert "required" in result.lower()


class TestRingCentralPresence:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.ringcentral import RingCentralConnector
        self.tools = _register_tools(RingCentralConnector)

    @pytest.mark.asyncio
    async def test_presence_success(self):
        resp = _mock_response(200, {
            "presenceStatus": "Available",
            "dndStatus": "TakeAllCalls",
            "userStatus": "Available",
            "telephonyStatus": "NoCall",
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("ringcentral", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["ringcentral_presence"](ctx)
        assert "Available" in result
        assert "TakeAllCalls" in result
        assert "NoCall" in result


class TestRingCentralListExtensions:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.ringcentral import RingCentralConnector
        self.tools = _register_tools(RingCentralConnector)

    @pytest.mark.asyncio
    async def test_list_extensions_success(self):
        resp = _mock_response(200, {
            "records": [
                {"name": "Alice Smith", "extensionNumber": "101", "status": "Enabled", "type": "User"},
                {"name": "Sales Queue", "extensionNumber": "200", "status": "Enabled", "type": "Department"},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("ringcentral", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["ringcentral_list_extensions"](ctx)
        assert "Alice Smith" in result
        assert "101" in result
        assert "Sales Queue" in result

    @pytest.mark.asyncio
    async def test_list_extensions_empty(self):
        resp = _mock_response(200, {"records": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("ringcentral", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["ringcentral_list_extensions"](ctx)
        assert "No extensions found" in result


class TestRingCentralGetVoicemail:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.ringcentral import RingCentralConnector
        self.tools = _register_tools(RingCentralConnector)

    @pytest.mark.asyncio
    async def test_get_voicemail_success(self):
        resp = _mock_response(200, {
            "records": [
                {"creationTime": "2024-06-01T10:00:00Z", "from": {"name": "John Caller"}, "readStatus": "Unread"},
                {"creationTime": "2024-06-02T14:00:00Z", "from": {"name": "Jane Caller"}, "readStatus": "Read"},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("ringcentral", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["ringcentral_get_voicemail"](ctx)
        assert "John Caller" in result
        assert "Unread" in result
        assert "Jane Caller" in result

    @pytest.mark.asyncio
    async def test_get_voicemail_empty(self):
        resp = _mock_response(200, {"records": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("ringcentral", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["ringcentral_get_voicemail"](ctx)
        assert "No voicemail messages found" in result


class TestRingCentralSendSms:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.ringcentral import RingCentralConnector
        self.tools = _register_tools(RingCentralConnector)

    @pytest.mark.asyncio
    async def test_send_sms_success(self):
        resp = _mock_response(200, {
            "id": "msg-001",
            "messageStatus": "Delivered",
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("ringcentral", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["ringcentral_send_sms"]("+15551234567", "Hello there", ctx)
        assert "SMS sent successfully" in result
        assert "msg-001" in result
        assert "+15551234567" in result

    @pytest.mark.asyncio
    async def test_send_sms_empty_to(self):
        ctx = MagicMock()
        result = await self.tools["ringcentral_send_sms"]("", "Hello", ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_send_sms_empty_text(self):
        ctx = MagicMock()
        result = await self.tools["ringcentral_send_sms"]("+15551234567", "", ctx)
        assert "required" in result.lower()


class TestRingCentralSendMessage:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.ringcentral import RingCentralConnector
        self.tools = _register_tools(RingCentralConnector)

    @pytest.mark.asyncio
    async def test_send_message_success(self):
        resp = _mock_response(200, {
            "id": "pager-001",
            "subject": "Meeting Update",
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("ringcentral", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["ringcentral_send_message"]("101", "Meeting Update", "Please join at 3pm", ctx)
        assert "Pager message sent successfully" in result
        assert "pager-001" in result
        assert "101" in result

    @pytest.mark.asyncio
    async def test_send_message_empty_to(self):
        ctx = MagicMock()
        result = await self.tools["ringcentral_send_message"]("", "Subject", "Text", ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_send_message_empty_text(self):
        ctx = MagicMock()
        result = await self.tools["ringcentral_send_message"]("101", "Subject", "", ctx)
        assert "required" in result.lower()


class TestRingCentralGetCallDetails:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.ringcentral import RingCentralConnector
        self.tools = _register_tools(RingCentralConnector)

    @pytest.mark.asyncio
    async def test_get_call_details_success(self):
        resp = _mock_response(200, {
            "id": "call-001",
            "direction": "Inbound",
            "result": "Accepted",
            "from": {"name": "John Caller", "phoneNumber": "+15551111111"},
            "to": {"name": "Jane Receiver", "phoneNumber": "+15552222222"},
            "startTime": "2024-06-01T10:30:00Z",
            "duration": 300,
            "type": "Voice",
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("ringcentral", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["ringcentral_get_call_details"]("call-001", ctx)
        assert "call-001" in result
        assert "Inbound" in result
        assert "John Caller" in result
        assert "Jane Receiver" in result
        assert "300" in result

    @pytest.mark.asyncio
    async def test_get_call_details_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["ringcentral_get_call_details"]("", ctx)
        assert "required" in result.lower()


class TestRingCentralDownloadRecording:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.ringcentral import RingCentralConnector
        self.tools = _register_tools(RingCentralConnector)

    @pytest.mark.asyncio
    async def test_download_recording_success(self):
        resp = _mock_response(200, {
            "id": "rec-100",
            "duration": 60,
            "contentUri": "https://platform.ringcentral.com/recording/rec-100/content",
            "contentType": "audio/mpeg",
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("ringcentral", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["ringcentral_download_recording"]("rec-100", ctx)
        assert "rec-100" in result
        assert "60" in result
        assert "audio/mpeg" in result
        assert "Content URI" in result

    @pytest.mark.asyncio
    async def test_download_recording_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["ringcentral_download_recording"]("", ctx)
        assert "required" in result.lower()


class TestRingCentralListContacts:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.ringcentral import RingCentralConnector
        self.tools = _register_tools(RingCentralConnector)

    @pytest.mark.asyncio
    async def test_list_contacts_success(self):
        resp = _mock_response(200, {
            "records": [
                {"firstName": "Alice", "lastName": "Smith", "extensionNumber": "101", "email": "alice@example.com"},
                {"firstName": "Bob", "lastName": "Jones", "extensionNumber": "102", "email": "bob@example.com"},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("ringcentral", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["ringcentral_list_contacts"](ctx)
        assert "Alice Smith" in result
        assert "101" in result
        assert "Bob Jones" in result
        assert "bob@example.com" in result

    @pytest.mark.asyncio
    async def test_list_contacts_empty(self):
        resp = _mock_response(200, {"records": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("ringcentral", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["ringcentral_list_contacts"](ctx)
        assert "No contacts found" in result

    @pytest.mark.asyncio
    async def test_list_contacts_with_search(self):
        resp = _mock_response(200, {
            "records": [
                {"firstName": "Alice", "lastName": "Smith", "extensionNumber": "101", "email": "alice@example.com"},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("ringcentral", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["ringcentral_list_contacts"](ctx, search="Alice")
        assert "Alice Smith" in result


class TestRingCentralListActiveCalls:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.ringcentral import RingCentralConnector
        self.tools = _register_tools(RingCentralConnector)

    @pytest.mark.asyncio
    async def test_list_active_calls_success(self):
        resp = _mock_response(200, {
            "records": [
                {
                    "direction": "Outbound",
                    "from": {"name": "Alice Smith"},
                    "to": {"name": "Bob Jones"},
                    "result": "InProgress",
                },
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("ringcentral", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["ringcentral_list_active_calls"](ctx)
        assert "Outbound" in result
        assert "Alice Smith" in result
        assert "Bob Jones" in result
        assert "InProgress" in result

    @pytest.mark.asyncio
    async def test_list_active_calls_empty(self):
        resp = _mock_response(200, {"records": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("ringcentral", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["ringcentral_list_active_calls"](ctx)
        assert "No active calls" in result


# --- Zoom Connector Tests ---


class TestZoomCreateMeeting:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.zoom import ZoomConnector
        self.tools = _register_tools(ZoomConnector)

    @pytest.mark.asyncio
    async def test_create_meeting_success(self):
        resp = _mock_response(200, {
            "id": 789,
            "topic": "Planning Session",
            "start_time": "2024-07-01T10:00:00Z",
            "duration": 45,
            "join_url": "https://zoom.us/j/789",
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        creds = {"account_id": "acc1", "client_id": "cid", "client_secret": "csec"}
        with (
            _patch_require_service("zoom", client),
            _patch_get_creds("zoom", creds),
            patch("asibot.connectors.zoom._get_access_token", new_callable=AsyncMock, return_value="zoom_tok"),
            patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)),
        ):
            result = await self.tools["zoom_create_meeting"](ctx, topic="Planning Session", start_time="2024-07-01T10:00:00Z", duration=45)
        assert "Meeting created successfully" in result
        assert "Planning Session" in result
        assert "zoom.us" in result

    @pytest.mark.asyncio
    async def test_create_meeting_empty_topic(self):
        ctx = MagicMock()
        result = await self.tools["zoom_create_meeting"](ctx, topic="", start_time="2024-07-01T10:00:00Z")
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_create_meeting_empty_start_time(self):
        ctx = MagicMock()
        result = await self.tools["zoom_create_meeting"](ctx, topic="Test", start_time="")
        assert "required" in result.lower()


class TestZoomUpdateMeeting:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.zoom import ZoomConnector
        self.tools = _register_tools(ZoomConnector)

    @pytest.mark.asyncio
    async def test_update_meeting_success(self):
        resp = _mock_response(204)
        client = _mock_client(resp)
        ctx = MagicMock()
        creds = {"account_id": "acc1", "client_id": "cid", "client_secret": "csec"}
        with (
            _patch_require_service("zoom", client),
            _patch_get_creds("zoom", creds),
            patch("asibot.connectors.zoom._get_access_token", new_callable=AsyncMock, return_value="zoom_tok"),
            patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)),
        ):
            result = await self.tools["zoom_update_meeting"](123, ctx, topic="Updated Topic")
        assert "updated successfully" in result
        assert "123" in result

    @pytest.mark.asyncio
    async def test_update_meeting_no_fields(self):
        client = _mock_client(_mock_response(200))
        ctx = MagicMock()
        creds = {"account_id": "acc1", "client_id": "cid", "client_secret": "csec"}
        with (
            _patch_require_service("zoom", client),
            _patch_get_creds("zoom", creds),
            patch("asibot.connectors.zoom._get_access_token", new_callable=AsyncMock, return_value="zoom_tok"),
        ):
            result = await self.tools["zoom_update_meeting"](123, ctx)
        assert "No fields to update" in result


class TestZoomDeleteMeeting:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.zoom import ZoomConnector
        self.tools = _register_tools(ZoomConnector)

    @pytest.mark.asyncio
    async def test_delete_meeting_success(self):
        resp = _mock_response(204)
        client = _mock_client(resp)
        ctx = MagicMock()
        creds = {"account_id": "acc1", "client_id": "cid", "client_secret": "csec"}
        with (
            _patch_require_service("zoom", client),
            _patch_get_creds("zoom", creds),
            patch("asibot.connectors.zoom._get_access_token", new_callable=AsyncMock, return_value="zoom_tok"),
            patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)),
        ):
            result = await self.tools["zoom_delete_meeting"](999, ctx)
        assert "deleted successfully" in result
        assert "999" in result


class TestZoomGetMeetingRegistrants:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.zoom import ZoomConnector
        self.tools = _register_tools(ZoomConnector)

    @pytest.mark.asyncio
    async def test_list_registrants_success(self):
        resp = _mock_response(200, {
            "registrants": [
                {"first_name": "Alice", "last_name": "Smith", "email": "alice@example.com", "status": "approved"},
                {"first_name": "Bob", "last_name": "Jones", "email": "bob@example.com", "status": "pending"},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        creds = {"account_id": "acc1", "client_id": "cid", "client_secret": "csec"}
        with (
            _patch_require_service("zoom", client),
            _patch_get_creds("zoom", creds),
            patch("asibot.connectors.zoom._get_access_token", new_callable=AsyncMock, return_value="zoom_tok"),
            patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)),
        ):
            result = await self.tools["zoom_get_meeting_registrants"](123, ctx)
        assert "Alice Smith" in result
        assert "alice@example.com" in result
        assert "Bob Jones" in result
        assert "pending" in result

    @pytest.mark.asyncio
    async def test_list_registrants_empty(self):
        resp = _mock_response(200, {"registrants": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        creds = {"account_id": "acc1", "client_id": "cid", "client_secret": "csec"}
        with (
            _patch_require_service("zoom", client),
            _patch_get_creds("zoom", creds),
            patch("asibot.connectors.zoom._get_access_token", new_callable=AsyncMock, return_value="zoom_tok"),
            patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)),
        ):
            result = await self.tools["zoom_get_meeting_registrants"](123, ctx)
        assert "No registrants found" in result


class TestZoomListUsers:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.zoom import ZoomConnector
        self.tools = _register_tools(ZoomConnector)

    @pytest.mark.asyncio
    async def test_list_users_success(self):
        resp = _mock_response(200, {
            "users": [
                {"first_name": "Alice", "last_name": "Smith", "email": "alice@example.com", "type": 2, "status": "active"},
                {"first_name": "Bob", "last_name": "Jones", "email": "bob@example.com", "type": 1, "status": "active"},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        creds = {"account_id": "acc1", "client_id": "cid", "client_secret": "csec"}
        with (
            _patch_require_service("zoom", client),
            _patch_get_creds("zoom", creds),
            patch("asibot.connectors.zoom._get_access_token", new_callable=AsyncMock, return_value="zoom_tok"),
            patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)),
        ):
            result = await self.tools["zoom_list_users"](ctx)
        assert "Alice Smith" in result
        assert "alice@example.com" in result
        assert "Bob Jones" in result

    @pytest.mark.asyncio
    async def test_list_users_empty(self):
        resp = _mock_response(200, {"users": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        creds = {"account_id": "acc1", "client_id": "cid", "client_secret": "csec"}
        with (
            _patch_require_service("zoom", client),
            _patch_get_creds("zoom", creds),
            patch("asibot.connectors.zoom._get_access_token", new_callable=AsyncMock, return_value="zoom_tok"),
            patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)),
        ):
            result = await self.tools["zoom_list_users"](ctx)
        assert "No users found" in result


class TestZoomGetUser:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.zoom import ZoomConnector
        self.tools = _register_tools(ZoomConnector)

    @pytest.mark.asyncio
    async def test_get_user_success(self):
        resp = _mock_response(200, {
            "first_name": "Alice",
            "last_name": "Smith",
            "email": "alice@example.com",
            "type": 2,
            "status": "active",
            "pmi": 1234567890,
            "timezone": "America/New_York",
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        creds = {"account_id": "acc1", "client_id": "cid", "client_secret": "csec"}
        with (
            _patch_require_service("zoom", client),
            _patch_get_creds("zoom", creds),
            patch("asibot.connectors.zoom._get_access_token", new_callable=AsyncMock, return_value="zoom_tok"),
            patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)),
        ):
            result = await self.tools["zoom_get_user"]("user-abc123", ctx)
        assert "Alice Smith" in result
        assert "alice@example.com" in result
        assert "America/New_York" in result
        assert "1234567890" in result

    @pytest.mark.asyncio
    async def test_get_user_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["zoom_get_user"]("", ctx)
        assert "required" in result.lower()


# --- Roboflow Connector Tests ---


class TestRoboflowListProjects:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.roboflow import RoboflowConnector
        self.tools = _register_tools(RoboflowConnector)

    @pytest.mark.asyncio
    async def test_list_success(self):
        resp = _mock_response(200, {
            "workspace": {
                "projects": [
                    {"name": "Object Detection", "id": "od-proj", "images": 1500},
                    {"name": "Segmentation", "id": "seg-proj", "images": 800},
                ]
            }
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("roboflow", client), \
             _patch_get_creds("roboflow", {"api_key": "key", "workspace": "myws"}), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["roboflow_list_projects"](ctx)
        assert "Object Detection" in result
        assert "1500" in result
        assert "Segmentation" in result

    @pytest.mark.asyncio
    async def test_list_empty(self):
        resp = _mock_response(200, {"workspace": {"projects": []}})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("roboflow", client), \
             _patch_get_creds("roboflow", {"api_key": "key", "workspace": ""}), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["roboflow_list_projects"](ctx)
        assert "No projects found" in result


class TestRoboflowGetProject:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.roboflow import RoboflowConnector
        self.tools = _register_tools(RoboflowConnector)

    @pytest.mark.asyncio
    async def test_get_success(self):
        resp = _mock_response(200, {
            "name": "Car Detection",
            "type": "object-detection",
            "created": "2024-01-15",
            "versions": [
                {"id": "3", "images": 2000},
            ],
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("roboflow", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["roboflow_get_project"]("car-detection", ctx)
        assert "Car Detection" in result
        assert "object-detection" in result
        assert "v3" in result

    @pytest.mark.asyncio
    async def test_get_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["roboflow_get_project"]("", ctx)
        assert "required" in result.lower()


class TestRoboflowListVersions:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.roboflow import RoboflowConnector
        self.tools = _register_tools(RoboflowConnector)

    @pytest.mark.asyncio
    async def test_list_versions_success(self):
        resp = _mock_response(200, {
            "versions": [
                {"id": "1", "images": 500, "created": "2024-01-01"},
                {"id": "2", "images": 1000, "created": "2024-02-01"},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("roboflow", client), \
             _patch_get_creds("roboflow", {"api_key": "key", "workspace": "myws"}), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["roboflow_list_versions"]("my-project", ctx)
        assert "v1" in result
        assert "v2" in result
        assert "500" in result
        assert "1000" in result

    @pytest.mark.asyncio
    async def test_list_versions_empty(self):
        resp = _mock_response(200, {"versions": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("roboflow", client), \
             _patch_get_creds("roboflow", {"api_key": "key", "workspace": "myws"}), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["roboflow_list_versions"]("my-project", ctx)
        assert "No versions found" in result

    @pytest.mark.asyncio
    async def test_list_versions_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["roboflow_list_versions"]("", ctx)
        assert "required" in result.lower()


class TestRoboflowGetVersion:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.roboflow import RoboflowConnector
        self.tools = _register_tools(RoboflowConnector)

    @pytest.mark.asyncio
    async def test_get_version_success(self):
        resp = _mock_response(200, {
            "id": "2",
            "images": 1200,
            "created": "2024-03-01",
            "augmented": True,
            "preprocessing": "auto-orient",
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("roboflow", client), \
             _patch_get_creds("roboflow", {"api_key": "key", "workspace": "myws"}), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["roboflow_get_version"]("my-project", "2", ctx)
        assert "v2" in result
        assert "1200" in result
        assert "auto-orient" in result

    @pytest.mark.asyncio
    async def test_get_version_empty_project_id(self):
        ctx = MagicMock()
        result = await self.tools["roboflow_get_version"]("", "2", ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_get_version_empty_version_id(self):
        ctx = MagicMock()
        result = await self.tools["roboflow_get_version"]("my-project", "", ctx)
        assert "required" in result.lower()


class TestRoboflowGetModel:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.roboflow import RoboflowConnector
        self.tools = _register_tools(RoboflowConnector)

    @pytest.mark.asyncio
    async def test_get_model_success(self):
        resp = _mock_response(200, {
            "model": {
                "map": 0.85,
                "precision": 0.90,
                "recall": 0.80,
                "type": "yolov8",
            }
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("roboflow", client), \
             _patch_get_creds("roboflow", {"api_key": "key", "workspace": "myws"}), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["roboflow_get_model"]("my-project", "2", ctx)
        assert "0.85" in result
        assert "0.9" in result
        assert "0.8" in result
        assert "yolov8" in result

    @pytest.mark.asyncio
    async def test_get_model_empty_project_id(self):
        ctx = MagicMock()
        result = await self.tools["roboflow_get_model"]("", "2", ctx)
        assert "required" in result.lower()


# --- Zapier Connector Tests ---


class TestZapierListActions:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.zapier import ZapierConnector
        self.tools = _register_tools(ZapierConnector)

    @pytest.mark.asyncio
    async def test_list_success(self):
        resp = _mock_response(200, {
            "results": [
                {"description": "Send Slack message", "id": "act-001", "params": {"app": "Slack"}},
                {"description": "Create Trello card", "id": "act-002", "params": {"app": "Trello"}},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("zapier", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["zapier_list_actions"](ctx)
        assert "Send Slack message" in result
        assert "Slack" in result
        assert "Create Trello card" in result

    @pytest.mark.asyncio
    async def test_list_empty(self):
        resp = _mock_response(200, {"results": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("zapier", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["zapier_list_actions"](ctx)
        assert "No Zapier actions configured" in result


class TestZapierRunAction:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.zapier import ZapierConnector
        self.tools = _register_tools(ZapierConnector)

    @pytest.mark.asyncio
    async def test_run_success(self):
        resp = _mock_response(200, {
            "status": "success",
            "result": {"message_sent": True},
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("zapier", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["zapier_run_action"]("act-001", "Send hello to general channel", ctx)
        assert "successfully" in result

    @pytest.mark.asyncio
    async def test_run_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["zapier_run_action"]("", "Do something", ctx)
        assert "required" in result.lower()


class TestZapierPreviewAction:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.zapier import ZapierConnector
        self.tools = _register_tools(ZapierConnector)

    @pytest.mark.asyncio
    async def test_preview_success(self):
        resp = _mock_response(200, {
            "status": "success",
            "result": {"message": "Hello #general", "channel": "general"},
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("zapier", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["zapier_preview_action"]("act-001", "Send hello to general", ctx)
        assert "Preview" in result
        assert "success" in result

    @pytest.mark.asyncio
    async def test_preview_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["zapier_preview_action"]("", "Do something", ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_preview_empty_instructions(self):
        ctx = MagicMock()
        result = await self.tools["zapier_preview_action"]("act-001", "", ctx)
        assert "required" in result.lower()


class TestZapierGetAction:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.zapier import ZapierConnector
        self.tools = _register_tools(ZapierConnector)

    @pytest.mark.asyncio
    async def test_get_action_success(self):
        resp = _mock_response(200, {
            "description": "Send Slack message",
            "id": "act-001",
            "params": {"app": "Slack", "channel": "", "message": ""},
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("zapier", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["zapier_get_action"]("act-001", ctx)
        assert "Send Slack message" in result
        assert "Slack" in result
        assert "channel" in result

    @pytest.mark.asyncio
    async def test_get_action_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["zapier_get_action"]("", ctx)
        assert "required" in result.lower()


# --- SAP Connector Tests ---


class TestSAPListOrders:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.sap import SAPConnector
        self.tools = _register_tools(SAPConnector)

    @pytest.mark.asyncio
    async def test_list_success(self):
        resp = _mock_response(200, {
            "d": {
                "results": [
                    {
                        "SalesOrder": "0000012345",
                        "SalesOrderType": "OR",
                        "SalesOrganization": "1000",
                        "SoldToParty": "ACME Corp",
                        "CreationDate": "2024-06-01",
                    },
                ]
            }
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("sap", client), \
             _patch_get_creds("sap", {"token": "tok", "base_url": "https://sap.example.com"}), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["sap_list_orders"](ctx)
        assert "0000012345" in result
        assert "ACME Corp" in result

    @pytest.mark.asyncio
    async def test_list_empty(self):
        resp = _mock_response(200, {"d": {"results": []}})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("sap", client), \
             _patch_get_creds("sap", {"token": "tok", "base_url": "https://sap.example.com"}), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["sap_list_orders"](ctx)
        assert "No sales orders found" in result


class TestSAPGetOrder:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.sap import SAPConnector
        self.tools = _register_tools(SAPConnector)

    @pytest.mark.asyncio
    async def test_get_success(self):
        resp = _mock_response(200, {
            "d": {
                "SalesOrderType": "OR",
                "SalesOrganization": "1000",
                "SoldToParty": "Globex Inc",
                "CreationDate": "2024-03-15",
                "TotalNetAmount": "75000.00",
                "TransactionCurrency": "EUR",
                "OverallSDProcessStatus": "C",
            }
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("sap", client), \
             _patch_get_creds("sap", {"token": "tok", "base_url": "https://sap.example.com"}), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["sap_get_order"]("0000099999", ctx)
        assert "Globex Inc" in result
        assert "75000.00" in result
        assert "EUR" in result

    @pytest.mark.asyncio
    async def test_get_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["sap_get_order"]("", ctx)
        assert "required" in result.lower()


class TestSAPSearch:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.sap import SAPConnector
        self.tools = _register_tools(SAPConnector)

    @pytest.mark.asyncio
    async def test_search_success(self):
        resp = _mock_response(200, {
            "d": {
                "results": [
                    {"SalesOrder": "0000054321", "SoldToParty": "Initech", "CreationDate": "2024-04-10"},
                ]
            }
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("sap", client), \
             _patch_get_creds("sap", {"token": "tok", "base_url": "https://sap.example.com"}), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["sap_search"]("Initech", ctx)
        assert "0000054321" in result
        assert "Initech" in result

    @pytest.mark.asyncio
    async def test_search_empty(self):
        resp = _mock_response(200, {"d": {"results": []}})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("sap", client), \
             _patch_get_creds("sap", {"token": "tok", "base_url": "https://sap.example.com"}), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["sap_search"]("nothing", ctx)
        assert "No matching orders found" in result


class TestSAPListOrderItems:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.sap import SAPConnector
        self.tools = _register_tools(SAPConnector)

    @pytest.mark.asyncio
    async def test_list_order_items_success(self):
        resp = _mock_response(200, {
            "d": {
                "results": [
                    {"SalesOrderItem": "10", "Material": "MAT-001", "OrderQuantity": "100", "NetAmount": "5000.00", "TransactionCurrency": "EUR"},
                    {"SalesOrderItem": "20", "Material": "MAT-002", "OrderQuantity": "50", "NetAmount": "2500.00", "TransactionCurrency": "EUR"},
                ]
            }
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("sap", client), \
             _patch_get_creds("sap", {"token": "tok", "base_url": "https://sap.example.com"}), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["sap_list_order_items"]("0000012345", ctx)
        assert "MAT-001" in result
        assert "MAT-002" in result
        assert "5000.00" in result

    @pytest.mark.asyncio
    async def test_list_order_items_empty(self):
        resp = _mock_response(200, {"d": {"results": []}})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("sap", client), \
             _patch_get_creds("sap", {"token": "tok", "base_url": "https://sap.example.com"}), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["sap_list_order_items"]("0000012345", ctx)
        assert "No line items found" in result

    @pytest.mark.asyncio
    async def test_list_order_items_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["sap_list_order_items"]("", ctx)
        assert "required" in result.lower()


class TestSAPGetCustomer:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.sap import SAPConnector
        self.tools = _register_tools(SAPConnector)

    @pytest.mark.asyncio
    async def test_get_customer_success(self):
        resp = _mock_response(200, {
            "d": {
                "BusinessPartnerFullName": "Acme Corporation",
                "BusinessPartnerCategory": "1",
                "CreationDate": "2020-01-15",
                "Industry": "Manufacturing",
                "Country": "US",
            }
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("sap", client), \
             _patch_get_creds("sap", {"token": "tok", "base_url": "https://sap.example.com"}), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["sap_get_customer"]("BP-001", ctx)
        assert "Acme Corporation" in result
        assert "Manufacturing" in result
        assert "US" in result

    @pytest.mark.asyncio
    async def test_get_customer_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["sap_get_customer"]("", ctx)
        assert "required" in result.lower()


class TestSAPListDeliveries:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.sap import SAPConnector
        self.tools = _register_tools(SAPConnector)

    @pytest.mark.asyncio
    async def test_list_deliveries_success(self):
        resp = _mock_response(200, {
            "d": {
                "results": [
                    {"SalesOrderItem": "10", "ScheduleLine": "1", "ScheduleLineDeliveryDate": "2024-07-01", "OrderQuantity": "100"},
                    {"SalesOrderItem": "20", "ScheduleLine": "1", "ScheduleLineDeliveryDate": "2024-08-01", "OrderQuantity": "50"},
                ]
            }
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("sap", client), \
             _patch_get_creds("sap", {"token": "tok", "base_url": "https://sap.example.com"}), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["sap_list_deliveries"]("0000012345", ctx)
        assert "2024-07-01" in result
        assert "2024-08-01" in result
        assert "100" in result

    @pytest.mark.asyncio
    async def test_list_deliveries_empty(self):
        resp = _mock_response(200, {"d": {"results": []}})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("sap", client), \
             _patch_get_creds("sap", {"token": "tok", "base_url": "https://sap.example.com"}), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["sap_list_deliveries"]("0000012345", ctx)
        assert "No schedule lines found" in result

    @pytest.mark.asyncio
    async def test_list_deliveries_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["sap_list_deliveries"]("", ctx)
        assert "required" in result.lower()


# --- SAP OData Quote Escaping Test ---


class TestSAPODataEscaping:
    """Tests that SAP connector properly escapes single quotes in OData queries."""

    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.sap import SAPConnector
        self.tools = _register_tools(SAPConnector)

    def test_order_id_escaping_logic(self):
        """The replace("'", "''") logic should double single quotes."""
        order_id = "ORDER'INJ"
        safe_order_id = order_id.replace("'", "''")
        assert safe_order_id == "ORDER''INJ"
        assert "'" not in safe_order_id.replace("''", "")

    @pytest.mark.asyncio
    async def test_single_quote_escaped_in_search(self):
        """query containing a single quote should be doubled in OData $filter."""
        resp = _mock_response(200, {"d": {"results": []}})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("sap", client), \
             _patch_get_creds("sap", {"token": "tok", "base_url": "https://sap.example.com"}), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)) as mock_req:
            await self.tools["sap_search"]("O'Brien", ctx)
            call_args = mock_req.call_args
            kwargs = call_args[1]
            filter_expr = kwargs["params"]["$filter"]
            # The single quote should be doubled
            assert "O''Brien" in filter_expr

    def test_search_query_escaping_logic(self):
        """The replace("'", "''") logic used in sap_search should double quotes."""
        query = "O'Brien"
        safe_query = query.replace("'", "''")
        assert safe_query == "O''Brien"
        filter_expr = f"substringof('{safe_query}', SoldToParty)"
        assert "O''Brien" in filter_expr


# --- Paylocity Connector Tests ---


class TestPaylocitySearchEmployees:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.paylocity import PaylocityConnector
        self.tools = _register_tools(PaylocityConnector)

    @pytest.mark.asyncio
    async def test_search_success(self):
        resp = _mock_response(200, json_data=None)
        resp.json.return_value = [
            {"employeeId": "E001", "firstName": "Alice", "lastName": "Smith", "statusType": "Active"},
        ]
        client = _mock_client(resp)
        ctx = MagicMock()
        creds = {"client_id": "cid", "client_secret": "csec", "company_id": "comp1"}
        with (
            _patch_require_service("paylocity", client),
            _patch_get_creds("paylocity", creds),
            patch("asibot.connectors.paylocity._get_access_token", new_callable=AsyncMock, return_value="pay_tok"),
        ):
            result = await self.tools["paylocity_search_employees"]("Alice", ctx)
        assert "Alice Smith" in result

    @pytest.mark.asyncio
    async def test_search_empty(self):
        resp = _mock_response(200, json_data=None)
        resp.json.return_value = []
        client = _mock_client(resp)
        ctx = MagicMock()
        creds = {"client_id": "cid", "client_secret": "csec", "company_id": "comp1"}
        with (
            _patch_require_service("paylocity", client),
            _patch_get_creds("paylocity", creds),
            patch("asibot.connectors.paylocity._get_access_token", new_callable=AsyncMock, return_value="tok"),
        ):
            result = await self.tools["paylocity_search_employees"]("nonexistent", ctx)
        assert "No employees found" in result

    @pytest.mark.asyncio
    async def test_search_empty_query(self):
        ctx = MagicMock()
        result = await self.tools["paylocity_search_employees"]("", ctx)
        assert "required" in result.lower()


class TestPaylocityGetPayStatement:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.paylocity import PaylocityConnector
        self.tools = _register_tools(PaylocityConnector)

    @pytest.mark.asyncio
    async def test_get_pay_statement_success(self):
        resp = _mock_response(200, {
            "payStatement": [
                {"checkDate": "2024-01-15", "grossPay": 5000.00, "netPay": 3500.00},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        creds = {"client_id": "cid", "client_secret": "csec", "company_id": "comp1"}
        with (
            _patch_require_service("paylocity", client),
            _patch_get_creds("paylocity", creds),
            patch("asibot.connectors.paylocity._get_access_token", new_callable=AsyncMock, return_value="tok"),
        ):
            result = await self.tools["paylocity_get_pay_statement"]("E001", ctx, year="2024", check_date="2024-01-15")
        assert "2024-01-15" in result
        assert "5000" in result
        assert "3500" in result

    @pytest.mark.asyncio
    async def test_get_pay_statement_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["paylocity_get_pay_statement"]("", ctx, year="2024", check_date="2024-01-15")
        assert "required" in result.lower()


class TestPaylocityListDepartments:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.paylocity import PaylocityConnector
        self.tools = _register_tools(PaylocityConnector)

    @pytest.mark.asyncio
    async def test_list_departments_success(self):
        resp = _mock_response(200, json_data=None)
        resp.json.return_value = [
            {"code": "ENG", "description": "Engineering"},
            {"code": "MKT", "description": "Marketing"},
        ]
        client = _mock_client(resp)
        ctx = MagicMock()
        creds = {"client_id": "cid", "client_secret": "csec", "company_id": "comp1"}
        with (
            _patch_require_service("paylocity", client),
            _patch_get_creds("paylocity", creds),
            patch("asibot.connectors.paylocity._get_access_token", new_callable=AsyncMock, return_value="tok"),
        ):
            result = await self.tools["paylocity_list_departments"](ctx)
        assert "ENG" in result
        assert "Engineering" in result
        assert "MKT" in result
        assert "Marketing" in result

    @pytest.mark.asyncio
    async def test_list_departments_empty(self):
        resp = _mock_response(200, json_data=None)
        resp.json.return_value = []
        client = _mock_client(resp)
        ctx = MagicMock()
        creds = {"client_id": "cid", "client_secret": "csec", "company_id": "comp1"}
        with (
            _patch_require_service("paylocity", client),
            _patch_get_creds("paylocity", creds),
            patch("asibot.connectors.paylocity._get_access_token", new_callable=AsyncMock, return_value="tok"),
        ):
            result = await self.tools["paylocity_list_departments"](ctx)
        assert "No departments found" in result


# --- New ShareFile Write Tools ---


class TestShareFileUploadFile:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.citrix_sharefile import ShareFileConnector
        self.tools = _register_tools(ShareFileConnector)

    @pytest.mark.asyncio
    async def test_upload_success(self):
        resp = _mock_response(200, {"Id": "file-new-001"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("sharefile", client), \
             _patch_get_creds("sharefile", {"subdomain": "mycompany", "token": "tok"}), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["sharefile_upload_file"]("folder-001", "notes.txt", "Hello world", ctx)
        assert "Uploaded" in result
        assert "notes.txt" in result
        assert "file-new-001" in result

    @pytest.mark.asyncio
    async def test_upload_empty_parent(self):
        ctx = MagicMock()
        result = await self.tools["sharefile_upload_file"]("", "notes.txt", "content", ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_upload_empty_filename(self):
        ctx = MagicMock()
        result = await self.tools["sharefile_upload_file"]("folder-001", "", "content", ctx)
        assert "required" in result.lower()


class TestShareFileCreateFolder:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.citrix_sharefile import ShareFileConnector
        self.tools = _register_tools(ShareFileConnector)

    @pytest.mark.asyncio
    async def test_create_folder_success(self):
        resp = _mock_response(200, {"Id": "folder-new-001"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("sharefile", client), \
             _patch_get_creds("sharefile", {"subdomain": "mycompany", "token": "tok"}), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["sharefile_create_folder"]("parent-001", "New Folder", ctx)
        assert "Created folder" in result
        assert "New Folder" in result
        assert "folder-new-001" in result

    @pytest.mark.asyncio
    async def test_create_folder_empty_parent(self):
        ctx = MagicMock()
        result = await self.tools["sharefile_create_folder"]("", "New Folder", ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_create_folder_empty_name(self):
        ctx = MagicMock()
        result = await self.tools["sharefile_create_folder"]("parent-001", "", ctx)
        assert "required" in result.lower()


class TestShareFileDeleteItem:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.citrix_sharefile import ShareFileConnector
        self.tools = _register_tools(ShareFileConnector)

    @pytest.mark.asyncio
    async def test_delete_success(self):
        resp = _mock_response(200, {})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("sharefile", client), \
             _patch_get_creds("sharefile", {"subdomain": "mycompany", "token": "tok"}), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["sharefile_delete_item"]("item-del-001", ctx)
        assert "Deleted" in result
        assert "item-del-001" in result

    @pytest.mark.asyncio
    async def test_delete_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["sharefile_delete_item"]("", ctx)
        assert "required" in result.lower()


class TestShareFileCreateShare:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.citrix_sharefile import ShareFileConnector
        self.tools = _register_tools(ShareFileConnector)

    @pytest.mark.asyncio
    async def test_create_share_success(self):
        resp = _mock_response(200, {"Id": "share-new-001", "Uri": "https://mycompany.sharefile.com/s/share-new-001"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("sharefile", client), \
             _patch_get_creds("sharefile", {"subdomain": "mycompany", "token": "tok"}), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["sharefile_create_share"]("item-001", "alice@example.com", ctx)
        assert "Share created" in result
        assert "alice@example.com" in result
        assert "share-new-001" in result

    @pytest.mark.asyncio
    async def test_create_share_multiple_emails(self):
        resp = _mock_response(200, {"Id": "share-new-002"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("sharefile", client), \
             _patch_get_creds("sharefile", {"subdomain": "mycompany", "token": "tok"}), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["sharefile_create_share"]("item-001", "alice@example.com, bob@example.com", ctx)
        assert "alice@example.com" in result
        assert "bob@example.com" in result

    @pytest.mark.asyncio
    async def test_create_share_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["sharefile_create_share"]("", "alice@example.com", ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_create_share_invalid_email(self):
        ctx = MagicMock()
        result = await self.tools["sharefile_create_share"]("item-001", "not-an-email", ctx)
        assert "email" in result.lower() or "invalid" in result.lower()


# --- New LinkSquares Tools ---


class TestLinkSquaresListTags:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.linksquares import LinkSquaresConnector
        self.tools = _register_tools(LinkSquaresConnector)

    @pytest.mark.asyncio
    async def test_list_tags_success(self):
        resp = _mock_response(200, {
            "tags": [
                {"name": "Confidential", "id": "tag-001"},
                {"name": "Reviewed", "id": "tag-002"},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("linksquares", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["linksquares_list_tags"](ctx)
        assert "Confidential" in result
        assert "Reviewed" in result
        assert "tag-001" in result

    @pytest.mark.asyncio
    async def test_list_tags_empty(self):
        resp = _mock_response(200, {"tags": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("linksquares", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["linksquares_list_tags"](ctx)
        assert "No tags found" in result


class TestLinkSquaresAddTag:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.linksquares import LinkSquaresConnector
        self.tools = _register_tools(LinkSquaresConnector)

    @pytest.mark.asyncio
    async def test_add_tag_success(self):
        resp = _mock_response(200, {})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("linksquares", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["linksquares_add_tag"]("c-001", "Urgent", ctx)
        assert "Tag" in result
        assert "Urgent" in result
        assert "c-001" in result

    @pytest.mark.asyncio
    async def test_add_tag_empty_contract_id(self):
        ctx = MagicMock()
        result = await self.tools["linksquares_add_tag"]("", "Urgent", ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_add_tag_empty_tag(self):
        ctx = MagicMock()
        result = await self.tools["linksquares_add_tag"]("c-001", "", ctx)
        assert "required" in result.lower()


class TestLinkSquaresListParties:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.linksquares import LinkSquaresConnector
        self.tools = _register_tools(LinkSquaresConnector)

    @pytest.mark.asyncio
    async def test_list_parties_success(self):
        resp = _mock_response(200, {
            "parties": [
                {"name": "Acme Corp", "role": "Counterparty"},
                {"name": "Our Company", "role": "Owner"},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("linksquares", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["linksquares_list_parties"]("c-001", ctx)
        assert "Acme Corp" in result
        assert "Counterparty" in result
        assert "Our Company" in result
        assert "Owner" in result

    @pytest.mark.asyncio
    async def test_list_parties_empty(self):
        resp = _mock_response(200, {"parties": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("linksquares", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["linksquares_list_parties"]("c-001", ctx)
        assert "No parties found" in result

    @pytest.mark.asyncio
    async def test_list_parties_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["linksquares_list_parties"]("", ctx)
        assert "required" in result.lower()


class TestLinkSquaresGetSummary:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.linksquares import LinkSquaresConnector
        self.tools = _register_tools(LinkSquaresConnector)

    @pytest.mark.asyncio
    async def test_get_summary_success(self):
        resp = _mock_response(200, {
            "summary": "This contract establishes a 3-year service agreement with Acme Corp for IT consulting."
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("linksquares", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["linksquares_get_summary"]("c-001", ctx)
        assert "3-year service agreement" in result
        assert "c-001" in result

    @pytest.mark.asyncio
    async def test_get_summary_empty(self):
        resp = _mock_response(200, {"summary": ""})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("linksquares", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["linksquares_get_summary"]("c-001", ctx)
        assert "No summary available" in result

    @pytest.mark.asyncio
    async def test_get_summary_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["linksquares_get_summary"]("", ctx)
        assert "required" in result.lower()


# --- New Roboflow Tools ---


class TestRoboflowUploadImage:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.roboflow import RoboflowConnector
        self.tools = _register_tools(RoboflowConnector)

    @pytest.mark.asyncio
    async def test_upload_success(self):
        resp = _mock_response(200, {"id": "img-001"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("roboflow", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["roboflow_upload_image"]("my-project", "https://example.com/img.jpg", ctx)
        assert "uploaded" in result.lower()
        assert "img-001" in result
        assert "train" in result

    @pytest.mark.asyncio
    async def test_upload_custom_split(self):
        resp = _mock_response(200, {"id": "img-002"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("roboflow", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["roboflow_upload_image"]("my-project", "https://example.com/img.jpg", ctx, split="valid")
        assert "valid" in result

    @pytest.mark.asyncio
    async def test_upload_empty_project_id(self):
        ctx = MagicMock()
        result = await self.tools["roboflow_upload_image"]("", "https://example.com/img.jpg", ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_upload_empty_image_url(self):
        ctx = MagicMock()
        result = await self.tools["roboflow_upload_image"]("my-project", "", ctx)
        assert "required" in result.lower()


class TestRoboflowListAnnotations:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.roboflow import RoboflowConnector
        self.tools = _register_tools(RoboflowConnector)

    @pytest.mark.asyncio
    async def test_list_annotations_success(self):
        resp = _mock_response(200, {
            "annotations": [
                {"label": "car", "bbox": {"x": 10, "y": 20, "w": 100, "h": 50}},
                {"label": "person", "bbox": {"x": 200, "y": 300, "w": 60, "h": 120}},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("roboflow", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["roboflow_list_annotations"]("my-project", "img-001", ctx)
        assert "car" in result
        assert "person" in result

    @pytest.mark.asyncio
    async def test_list_annotations_empty(self):
        resp = _mock_response(200, {"annotations": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("roboflow", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["roboflow_list_annotations"]("my-project", "img-001", ctx)
        assert "No annotations found" in result

    @pytest.mark.asyncio
    async def test_list_annotations_empty_project_id(self):
        ctx = MagicMock()
        result = await self.tools["roboflow_list_annotations"]("", "img-001", ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_list_annotations_empty_image_id(self):
        ctx = MagicMock()
        result = await self.tools["roboflow_list_annotations"]("my-project", "", ctx)
        assert "required" in result.lower()


class TestRoboflowStartTraining:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.roboflow import RoboflowConnector
        self.tools = _register_tools(RoboflowConnector)

    @pytest.mark.asyncio
    async def test_start_training_success(self):
        resp = _mock_response(200, {"status": "training"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("roboflow", client), \
             _patch_get_creds("roboflow", {"api_key": "key", "workspace": "myws"}), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["roboflow_start_training"]("my-project", "3", ctx)
        assert "Training started" in result
        assert "my-project" in result
        assert "v3" in result

    @pytest.mark.asyncio
    async def test_start_training_empty_project_id(self):
        ctx = MagicMock()
        result = await self.tools["roboflow_start_training"]("", "3", ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_start_training_empty_version_id(self):
        ctx = MagicMock()
        result = await self.tools["roboflow_start_training"]("my-project", "", ctx)
        assert "required" in result.lower()


class TestRoboflowGetTrainingStatus:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.roboflow import RoboflowConnector
        self.tools = _register_tools(RoboflowConnector)

    @pytest.mark.asyncio
    async def test_get_training_status_success(self):
        resp = _mock_response(200, {
            "model": {"status": "training", "progress": "75%"},
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("roboflow", client), \
             _patch_get_creds("roboflow", {"api_key": "key", "workspace": "myws"}), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["roboflow_get_training_status"]("my-project", "3", ctx)
        assert "training" in result.lower()
        assert "75%" in result

    @pytest.mark.asyncio
    async def test_get_training_status_empty_project_id(self):
        ctx = MagicMock()
        result = await self.tools["roboflow_get_training_status"]("", "3", ctx)
        assert "required" in result.lower()


class TestRoboflowPredict:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.roboflow import RoboflowConnector
        self.tools = _register_tools(RoboflowConnector)

    @pytest.mark.asyncio
    async def test_predict_success(self):
        resp = _mock_response(200, {
            "predictions": [
                {"class": "car", "confidence": 0.95, "x": 150, "y": 200},
                {"class": "truck", "confidence": 0.82, "x": 400, "y": 300},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("roboflow", client), \
             _patch_get_creds("roboflow", {"api_key": "key", "workspace": "myws"}), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["roboflow_predict"]("my-project", "3", "https://example.com/img.jpg", ctx)
        assert "car" in result
        assert "0.95" in result
        assert "truck" in result

    @pytest.mark.asyncio
    async def test_predict_no_predictions(self):
        resp = _mock_response(200, {"predictions": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("roboflow", client), \
             _patch_get_creds("roboflow", {"api_key": "key", "workspace": "myws"}), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["roboflow_predict"]("my-project", "3", "https://example.com/img.jpg", ctx)
        assert "No predictions" in result

    @pytest.mark.asyncio
    async def test_predict_empty_project_id(self):
        ctx = MagicMock()
        result = await self.tools["roboflow_predict"]("", "3", "https://example.com/img.jpg", ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_predict_empty_image_url(self):
        ctx = MagicMock()
        result = await self.tools["roboflow_predict"]("my-project", "3", "", ctx)
        assert "required" in result.lower()


# --- New Paylocity Tools ---


class TestPaylocityGetPayHistory:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.paylocity import PaylocityConnector
        self.tools = _register_tools(PaylocityConnector)

    @pytest.mark.asyncio
    async def test_get_pay_history_success(self):
        resp = _mock_response(200, {
            "payStatement": [
                {"checkDate": "2024-01-15", "grossPay": 5000.00, "netPay": 3500.00},
                {"checkDate": "2024-02-15", "grossPay": 5000.00, "netPay": 3500.00},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        creds = {"client_id": "cid", "client_secret": "csec", "company_id": "comp1"}
        with (
            _patch_require_service("paylocity", client),
            _patch_get_creds("paylocity", creds),
            patch("asibot.connectors.paylocity._get_access_token", new_callable=AsyncMock, return_value="tok"),
        ):
            result = await self.tools["paylocity_get_pay_history"]("E001", "2024", ctx)
        assert "Pay history" in result
        assert "2024-01-15" in result
        assert "2024-02-15" in result
        assert "5000" in result

    @pytest.mark.asyncio
    async def test_get_pay_history_empty(self):
        resp = _mock_response(200, {"payStatement": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        creds = {"client_id": "cid", "client_secret": "csec", "company_id": "comp1"}
        with (
            _patch_require_service("paylocity", client),
            _patch_get_creds("paylocity", creds),
            patch("asibot.connectors.paylocity._get_access_token", new_callable=AsyncMock, return_value="tok"),
        ):
            result = await self.tools["paylocity_get_pay_history"]("E001", "2024", ctx)
        assert "No pay history found" in result

    @pytest.mark.asyncio
    async def test_get_pay_history_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["paylocity_get_pay_history"]("", "2024", ctx)
        assert "required" in result.lower()


class TestPaylocityListEarnings:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.paylocity import PaylocityConnector
        self.tools = _register_tools(PaylocityConnector)

    @pytest.mark.asyncio
    async def test_list_earnings_success(self):
        resp = _mock_response(200, json_data=None)
        resp.json.return_value = [
            {"earningCode": "REG", "description": "Regular Pay", "amount": 5000.00},
            {"earningCode": "OT", "description": "Overtime", "amount": 750.00},
        ]
        client = _mock_client(resp)
        ctx = MagicMock()
        creds = {"client_id": "cid", "client_secret": "csec", "company_id": "comp1"}
        with (
            _patch_require_service("paylocity", client),
            _patch_get_creds("paylocity", creds),
            patch("asibot.connectors.paylocity._get_access_token", new_callable=AsyncMock, return_value="tok"),
        ):
            result = await self.tools["paylocity_list_earnings"]("E001", ctx)
        assert "REG" in result
        assert "Regular Pay" in result
        assert "OT" in result
        assert "Overtime" in result

    @pytest.mark.asyncio
    async def test_list_earnings_empty(self):
        resp = _mock_response(200, json_data=None)
        resp.json.return_value = []
        client = _mock_client(resp)
        ctx = MagicMock()
        creds = {"client_id": "cid", "client_secret": "csec", "company_id": "comp1"}
        with (
            _patch_require_service("paylocity", client),
            _patch_get_creds("paylocity", creds),
            patch("asibot.connectors.paylocity._get_access_token", new_callable=AsyncMock, return_value="tok"),
        ):
            result = await self.tools["paylocity_list_earnings"]("E001", ctx)
        assert "No earnings found" in result

    @pytest.mark.asyncio
    async def test_list_earnings_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["paylocity_list_earnings"]("", ctx)
        assert "required" in result.lower()


class TestPaylocityGetBenefits:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.paylocity import PaylocityConnector
        self.tools = _register_tools(PaylocityConnector)

    @pytest.mark.asyncio
    async def test_get_benefits_success(self):
        resp = _mock_response(200, json_data=None)
        resp.json.return_value = [
            {"planDescription": "Health PPO", "coverageLevel": "Family", "effectiveDate": "2024-01-01"},
            {"planDescription": "Dental", "coverageLevel": "Employee Only", "effectiveDate": "2024-01-01"},
        ]
        client = _mock_client(resp)
        ctx = MagicMock()
        creds = {"client_id": "cid", "client_secret": "csec", "company_id": "comp1"}
        with (
            _patch_require_service("paylocity", client),
            _patch_get_creds("paylocity", creds),
            patch("asibot.connectors.paylocity._get_access_token", new_callable=AsyncMock, return_value="tok"),
        ):
            result = await self.tools["paylocity_get_benefits"]("E001", ctx)
        assert "Health PPO" in result
        assert "Family" in result
        assert "Dental" in result
        assert "Employee Only" in result

    @pytest.mark.asyncio
    async def test_get_benefits_empty(self):
        resp = _mock_response(200, json_data=None)
        resp.json.return_value = []
        client = _mock_client(resp)
        ctx = MagicMock()
        creds = {"client_id": "cid", "client_secret": "csec", "company_id": "comp1"}
        with (
            _patch_require_service("paylocity", client),
            _patch_get_creds("paylocity", creds),
            patch("asibot.connectors.paylocity._get_access_token", new_callable=AsyncMock, return_value="tok"),
        ):
            result = await self.tools["paylocity_get_benefits"]("E001", ctx)
        assert "No benefits found" in result

    @pytest.mark.asyncio
    async def test_get_benefits_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["paylocity_get_benefits"]("", ctx)
        assert "required" in result.lower()


# --- New Zapier Tools ---


class TestZapierListZaps:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.zapier import ZapierConnector
        self.tools = _register_tools(ZapierConnector)

    @pytest.mark.asyncio
    async def test_list_zaps_success(self):
        resp = _mock_response(200, {
            "results": [
                {"title": "New lead to Slack", "id": "zap-001", "status": "on"},
                {"title": "Email to sheet", "id": "zap-002", "status": "off"},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("zapier", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["zapier_list_zaps"](ctx)
        assert "New lead to Slack" in result
        assert "zap-001" in result
        assert "Email to sheet" in result

    @pytest.mark.asyncio
    async def test_list_zaps_with_status_filter(self):
        resp = _mock_response(200, {
            "results": [
                {"title": "Active Zap", "id": "zap-010", "status": "on"},
            ]
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("zapier", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["zapier_list_zaps"](ctx, status="on")
        assert "Active Zap" in result

    @pytest.mark.asyncio
    async def test_list_zaps_empty(self):
        resp = _mock_response(200, {"results": []})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("zapier", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["zapier_list_zaps"](ctx)
        assert "No Zaps found" in result


class TestZapierEnableZap:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.zapier import ZapierConnector
        self.tools = _register_tools(ZapierConnector)

    @pytest.mark.asyncio
    async def test_enable_success(self):
        resp = _mock_response(200, {})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("zapier", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["zapier_enable_zap"]("zap-001", ctx)
        assert "enabled" in result.lower()
        assert "zap-001" in result

    @pytest.mark.asyncio
    async def test_enable_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["zapier_enable_zap"]("", ctx)
        assert "required" in result.lower()


class TestZapierDisableZap:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.zapier import ZapierConnector
        self.tools = _register_tools(ZapierConnector)

    @pytest.mark.asyncio
    async def test_disable_success(self):
        resp = _mock_response(200, {})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("zapier", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["zapier_disable_zap"]("zap-001", ctx)
        assert "disabled" in result.lower()
        assert "zap-001" in result

    @pytest.mark.asyncio
    async def test_disable_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["zapier_disable_zap"]("", ctx)
        assert "required" in result.lower()


class TestAdobeSignSendAgreement:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.adobe_sign import AdobeSignConnector
        self.tools = _register_tools(AdobeSignConnector)

    @pytest.mark.asyncio
    async def test_send_success(self):
        resp = _mock_response(200, {"id": "agr-new-001"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("adobe_sign", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["adobe_sign_send_agreement"](
                "NDA for Acme", ["signer@acme.com"], "tpl-001", ctx, message="Please sign"
            )
        assert "Agreement sent" in result
        assert "agr-new-001" in result
        assert "signer@acme.com" in result

    @pytest.mark.asyncio
    async def test_send_empty_name(self):
        ctx = MagicMock()
        result = await self.tools["adobe_sign_send_agreement"](
            "", ["signer@acme.com"], "tpl-001", ctx
        )
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_send_empty_recipients(self):
        ctx = MagicMock()
        result = await self.tools["adobe_sign_send_agreement"](
            "NDA", [], "tpl-001", ctx
        )
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_send_invalid_email(self):
        ctx = MagicMock()
        result = await self.tools["adobe_sign_send_agreement"](
            "NDA", ["not-an-email"], "tpl-001", ctx
        )
        assert "email" in result.lower() or "invalid" in result.lower()



class TestAdobeSignSendReminder:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.adobe_sign import AdobeSignConnector
        self.tools = _register_tools(AdobeSignConnector)

    @pytest.mark.asyncio
    async def test_send_reminder_success(self):
        resp = _mock_response(200, {"id": "rem-001"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("adobe_sign", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["adobe_sign_send_reminder"]("agr-001", ctx, message="Please sign soon")
        assert "Reminder sent" in result
        assert "agr-001" in result

    @pytest.mark.asyncio
    async def test_send_reminder_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["adobe_sign_send_reminder"]("", ctx)
        assert "required" in result.lower()



class TestAdobeSignCancelAgreement:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.adobe_sign import AdobeSignConnector
        self.tools = _register_tools(AdobeSignConnector)

    @pytest.mark.asyncio
    async def test_cancel_success(self):
        resp = _mock_response(200, {})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("adobe_sign", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["adobe_sign_cancel_agreement"]("agr-001", ctx, comment="No longer needed")
        assert "cancelled" in result.lower()
        assert "agr-001" in result

    @pytest.mark.asyncio
    async def test_cancel_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["adobe_sign_cancel_agreement"]("", ctx)
        assert "required" in result.lower()



class TestAdobeSignDownloadDocument:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.adobe_sign import AdobeSignConnector
        self.tools = _register_tools(AdobeSignConnector)

    @pytest.mark.asyncio
    async def test_download_success(self):
        resp = _mock_response(200, {"url": "https://sign.example.com/download/agr-001.pdf"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("adobe_sign", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["adobe_sign_download_document"]("agr-001", ctx)
        assert "Download URL" in result
        assert "https://sign.example.com/download/agr-001.pdf" in result

    @pytest.mark.asyncio
    async def test_download_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["adobe_sign_download_document"]("", ctx)
        assert "required" in result.lower()



class TestConcurCreateReport:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.concur import ConcurConnector
        self.tools = _register_tools(ConcurConnector)

    @pytest.mark.asyncio
    async def test_create_success(self):
        resp = _mock_response(200, {"ID": "rpt-new-001"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("concur", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["concur_create_report"]("Q2 Travel", ctx, policy_id="pol-001")
        assert "Report created" in result
        assert "rpt-new-001" in result
        assert "Q2 Travel" in result

    @pytest.mark.asyncio
    async def test_create_empty_name(self):
        ctx = MagicMock()
        result = await self.tools["concur_create_report"]("", ctx)
        assert "required" in result.lower()



class TestConcurCreateExpense:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.concur import ConcurConnector
        self.tools = _register_tools(ConcurConnector)

    @pytest.mark.asyncio
    async def test_create_expense_success(self):
        resp = _mock_response(200, {"ID": "exp-new-001"})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("concur", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["concur_create_expense"](
                "rpt-001", "Airfare", 450.00, "USD", "2024-03-15", ctx, description="Flight to SFO"
            )
        assert "Expense created" in result
        assert "exp-new-001" in result
        assert "Airfare" in result
        assert "USD" in result

    @pytest.mark.asyncio
    async def test_create_expense_empty_report_id(self):
        ctx = MagicMock()
        result = await self.tools["concur_create_expense"](
            "", "Airfare", 100.0, "USD", "2024-01-01", ctx
        )
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_create_expense_invalid_date(self):
        ctx = MagicMock()
        result = await self.tools["concur_create_expense"](
            "rpt-001", "Airfare", 100.0, "USD", "not-a-date", ctx
        )
        assert "invalid" in result.lower() or "YYYY-MM-DD" in result



class TestConcurSubmitReport:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.concur import ConcurConnector
        self.tools = _register_tools(ConcurConnector)

    @pytest.mark.asyncio
    async def test_submit_success(self):
        resp = _mock_response(200, {})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("concur", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["concur_submit_report"]("rpt-001", ctx)
        assert "submitted" in result.lower()
        assert "rpt-001" in result

    @pytest.mark.asyncio
    async def test_submit_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["concur_submit_report"]("", ctx)
        assert "required" in result.lower()



class TestConcurApproveReport:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.concur import ConcurConnector
        self.tools = _register_tools(ConcurConnector)

    @pytest.mark.asyncio
    async def test_approve_success(self):
        resp = _mock_response(200, {})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("concur", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["concur_approve_report"]("rpt-001", ctx, comment="Looks good")
        assert "approved" in result.lower()
        assert "rpt-001" in result

    @pytest.mark.asyncio
    async def test_approve_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["concur_approve_report"]("", ctx)
        assert "required" in result.lower()



class TestConcurRejectReport:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.concur import ConcurConnector
        self.tools = _register_tools(ConcurConnector)

    @pytest.mark.asyncio
    async def test_reject_success(self):
        resp = _mock_response(200, {})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("concur", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["concur_reject_report"]("rpt-001", ctx, comment="Missing receipts")
        assert "sent back" in result.lower()
        assert "rpt-001" in result

    @pytest.mark.asyncio
    async def test_reject_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["concur_reject_report"]("", ctx)
        assert "required" in result.lower()



class TestConcurAddReceipt:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.concur import ConcurConnector
        self.tools = _register_tools(ConcurConnector)

    @pytest.mark.asyncio
    async def test_add_receipt_success(self):
        resp = _mock_response(200, {})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("concur", client), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["concur_add_receipt"]("exp-001", "receipt.jpg", "image/jpeg", ctx)
        assert "receipt.jpg" in result
        assert "exp-001" in result

    @pytest.mark.asyncio
    async def test_add_receipt_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["concur_add_receipt"]("", "receipt.jpg", "image/jpeg", ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_add_receipt_empty_filename(self):
        ctx = MagicMock()
        result = await self.tools["concur_add_receipt"]("exp-001", "", "image/jpeg", ctx)
        assert "required" in result.lower()



class TestSAPCreateOrder:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.sap import SAPConnector
        self.tools = _register_tools(SAPConnector)

    @pytest.mark.asyncio
    async def test_create_success(self):
        resp = _mock_response(200, {
            "d": {"SalesOrder": "0000099001"}
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("sap", client), \
             _patch_get_creds("sap", {"token": "tok", "base_url": "https://sap.example.com"}), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["sap_create_order"](
                "BP-001", [{"material": "MAT-001", "quantity": 10}], ctx, requested_date="2024-07-01"
            )
        assert "Sales order created" in result
        assert "0000099001" in result
        assert "BP-001" in result

    @pytest.mark.asyncio
    async def test_create_empty_customer(self):
        ctx = MagicMock()
        result = await self.tools["sap_create_order"](
            "", [{"material": "MAT-001", "quantity": 10}], ctx
        )
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_create_empty_items(self):
        ctx = MagicMock()
        result = await self.tools["sap_create_order"]("BP-001", [], ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_create_invalid_date(self):
        ctx = MagicMock()
        result = await self.tools["sap_create_order"](
            "BP-001", [{"material": "MAT-001", "quantity": 10}], ctx, requested_date="bad-date"
        )
        assert "invalid" in result.lower() or "YYYY-MM-DD" in result



class TestSAPUpdateOrder:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.sap import SAPConnector
        self.tools = _register_tools(SAPConnector)

    @pytest.mark.asyncio
    async def test_update_success(self):
        resp = _mock_response(200, {})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("sap", client), \
             _patch_get_creds("sap", {"token": "tok", "base_url": "https://sap.example.com"}), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["sap_update_order"](
                "0000012345", {"RequestedDeliveryDate": "2024-08-01"}, ctx
            )
        assert "updated" in result.lower()
        assert "0000012345" in result
        assert "RequestedDeliveryDate" in result

    @pytest.mark.asyncio
    async def test_update_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["sap_update_order"]("", {"field": "val"}, ctx)
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_update_empty_fields(self):
        ctx = MagicMock()
        result = await self.tools["sap_update_order"]("0000012345", {}, ctx)
        assert "required" in result.lower()



class TestSAPCancelOrder:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.sap import SAPConnector
        self.tools = _register_tools(SAPConnector)

    @pytest.mark.asyncio
    async def test_cancel_success(self):
        resp = _mock_response(200, {})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("sap", client), \
             _patch_get_creds("sap", {"token": "tok", "base_url": "https://sap.example.com"}), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["sap_cancel_order"]("0000012345", ctx, reason="Customer request")
        assert "cancelled" in result.lower()
        assert "0000012345" in result

    @pytest.mark.asyncio
    async def test_cancel_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["sap_cancel_order"]("", ctx)
        assert "required" in result.lower()



class TestSAPListMaterials:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.sap import SAPConnector
        self.tools = _register_tools(SAPConnector)

    @pytest.mark.asyncio
    async def test_list_success(self):
        resp = _mock_response(200, {
            "d": {
                "results": [
                    {"Material": "MAT-001", "MaterialDescription": "Steel Pipe", "MaterialType": "HAWA", "MaterialGroup": "001"},
                    {"Material": "MAT-002", "MaterialDescription": "Copper Wire", "MaterialType": "HAWA", "MaterialGroup": "002"},
                ]
            }
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("sap", client), \
             _patch_get_creds("sap", {"token": "tok", "base_url": "https://sap.example.com"}), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["sap_list_materials"](ctx, search="Steel")
        assert "MAT-001" in result
        assert "Steel Pipe" in result
        assert "MAT-002" in result

    @pytest.mark.asyncio
    async def test_list_empty(self):
        resp = _mock_response(200, {"d": {"results": []}})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("sap", client), \
             _patch_get_creds("sap", {"token": "tok", "base_url": "https://sap.example.com"}), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["sap_list_materials"](ctx)
        assert "No materials found" in result



class TestSAPGetMaterial:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.sap import SAPConnector
        self.tools = _register_tools(SAPConnector)

    @pytest.mark.asyncio
    async def test_get_success(self):
        resp = _mock_response(200, {
            "d": {
                "MaterialDescription": "Steel Pipe 50mm",
                "MaterialType": "HAWA",
                "MaterialGroup": "001",
                "BaseUnit": "EA",
                "GrossWeight": "2.5",
            }
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("sap", client), \
             _patch_get_creds("sap", {"token": "tok", "base_url": "https://sap.example.com"}), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["sap_get_material"]("MAT-001", ctx)
        assert "Steel Pipe 50mm" in result
        assert "MAT-001" in result
        assert "HAWA" in result
        assert "EA" in result

    @pytest.mark.asyncio
    async def test_get_empty_id(self):
        ctx = MagicMock()
        result = await self.tools["sap_get_material"]("", ctx)
        assert "required" in result.lower()



class TestSAPListInvoices:
    @pytest.fixture(autouse=True)
    def setup(self):
        from asibot.connectors.sap import SAPConnector
        self.tools = _register_tools(SAPConnector)

    @pytest.mark.asyncio
    async def test_list_success(self):
        resp = _mock_response(200, {
            "d": {
                "results": [
                    {
                        "BillingDocument": "INV-001",
                        "BillingDocumentType": "F2",
                        "SoldToParty": "BP-001",
                        "TotalNetAmount": "50000.00",
                        "TransactionCurrency": "EUR",
                        "BillingDocumentDate": "2024-06-15",
                    },
                ]
            }
        })
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("sap", client), \
             _patch_get_creds("sap", {"token": "tok", "base_url": "https://sap.example.com"}), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["sap_list_invoices"](ctx, customer_id="BP-001")
        assert "INV-001" in result
        assert "BP-001" in result
        assert "50000.00" in result
        assert "EUR" in result

    @pytest.mark.asyncio
    async def test_list_empty(self):
        resp = _mock_response(200, {"d": {"results": []}})
        client = _mock_client(resp)
        ctx = MagicMock()
        with _patch_require_service("sap", client), \
             _patch_get_creds("sap", {"token": "tok", "base_url": "https://sap.example.com"}), \
             patch.object(token_store, "safe_request", new_callable=AsyncMock, return_value=(resp, None)):
            result = await self.tools["sap_list_invoices"](ctx)
        assert "No invoices found" in result

