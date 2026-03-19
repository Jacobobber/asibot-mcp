"""Tests for niche connectors: Adobe Sign, Citrix ShareFile, Concur,
LinkSquares, Paylocity, RingCentral, Roboflow, Zapier, SAP.

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
