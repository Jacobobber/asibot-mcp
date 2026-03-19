"""Tests for pagination helpers."""

import time
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from asibot.connectors.pagination import (
    _deep_get,
    collect,
    paginate_cursor,
    paginate_odata,
    paginate_offset,
    paginate_salesforce,
)


# --- Helpers ---


def _mock_response(json_data):
    """Create a mock httpx.Response with the given JSON payload."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.json.return_value = json_data
    resp.raise_for_status.return_value = None
    return resp


def _mock_error_response():
    """Create a mock httpx.Response that raises an HTTP error."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 500
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "HTTP 500", request=MagicMock(), response=resp
    )
    return resp


# --- _deep_get tests ---


class TestDeepGet:
    def test_simple_key(self):
        assert _deep_get({"a": 1}, "a") == 1

    def test_nested_key(self):
        assert _deep_get({"a": {"b": {"c": 42}}}, "a.b.c") == 42

    def test_missing_key(self):
        assert _deep_get({"a": 1}, "b") is None

    def test_missing_nested_key(self):
        assert _deep_get({"a": {"b": 1}}, "a.c") is None

    def test_non_dict_intermediate(self):
        assert _deep_get({"a": [1, 2]}, "a.b") is None

    def test_empty_dict(self):
        assert _deep_get({}, "a.b") is None

    def test_none_value(self):
        assert _deep_get({"a": None}, "a") is None

    def test_sap_style_nested(self):
        data = {"d": {"results": [{"id": 1}], "__next": "http://next"}}
        assert _deep_get(data, "d.results") == [{"id": 1}]
        assert _deep_get(data, "d.__next") == "http://next"

    def test_hubspot_style_nested(self):
        data = {"paging": {"next": {"after": "abc123"}}}
        assert _deep_get(data, "paging.next.after") == "abc123"

    def test_odata_dotted_key(self):
        data = {"@odata.nextLink": "https://graph.microsoft.com/next", "value": []}
        assert _deep_get(data, "@odata.nextLink") == "https://graph.microsoft.com/next"

    def test_exact_key_takes_precedence(self):
        """Exact key match takes precedence over dot-splitting."""
        data = {"a.b": "exact", "a": {"b": "nested"}}
        assert _deep_get(data, "a.b") == "exact"


# --- collect tests ---


class TestCollect:
    @pytest.mark.asyncio
    async def test_collect_single_page(self):
        async def gen():
            yield [{"id": 1}, {"id": 2}]

        result = await collect(gen(), limit=10)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_collect_truncates_at_limit(self):
        async def gen():
            yield [{"id": i} for i in range(50)]
            yield [{"id": i} for i in range(50, 100)]

        result = await collect(gen(), limit=30)
        assert len(result) == 30
        assert result[0]["id"] == 0
        assert result[29]["id"] == 29

    @pytest.mark.asyncio
    async def test_collect_empty(self):
        async def gen():
            return
            yield  # make it an async generator

        result = await collect(gen(), limit=10)
        assert result == []

    @pytest.mark.asyncio
    async def test_collect_multi_page(self):
        async def gen():
            yield [{"id": 1}]
            yield [{"id": 2}]
            yield [{"id": 3}]

        result = await collect(gen(), limit=100)
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_collect_stops_after_limit_reached(self):
        call_count = 0

        async def gen():
            nonlocal call_count
            call_count += 1
            yield [{"id": i} for i in range(10)]
            call_count += 1
            yield [{"id": i} for i in range(10, 20)]
            call_count += 1
            yield [{"id": i} for i in range(20, 30)]

        result = await collect(gen(), limit=15)
        assert len(result) == 15
        # Generator should stop after yielding enough
        assert call_count == 2


# --- paginate_odata tests ---


class TestPaginateOdata:
    @pytest.mark.asyncio
    async def test_single_page(self):
        """Single page with no next link."""
        resp = _mock_response({"value": [{"id": 1}, {"id": 2}]})
        from unittest.mock import patch
        with patch("asibot.connectors.pagination.token_store.safe_request", AsyncMock(return_value=(resp, None))):
            pages = paginate_odata(
                MagicMock(), "https://api.example.com/items",
                service="Test", action="list",
            )
            result = await collect(pages, 100)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_multi_page(self):
        """Three pages with next links."""
        page1 = _mock_response({
            "value": [{"id": 1}],
            "@odata.nextLink": "https://api.example.com/items?skip=1",
        })
        page2 = _mock_response({
            "value": [{"id": 2}],
            "@odata.nextLink": "https://api.example.com/items?skip=2",
        })
        page3 = _mock_response({"value": [{"id": 3}]})
        mock_req = AsyncMock(side_effect=[(page1, None), (page2, None), (page3, None)])
        from unittest.mock import patch
        with patch("asibot.connectors.pagination.token_store.safe_request", mock_req):
            pages = paginate_odata(
                MagicMock(), "https://api.example.com/items",
                service="Test", action="list",
            )
            result = await collect(pages, 100)
        assert len(result) == 3
        assert mock_req.call_count == 3

    @pytest.mark.asyncio
    async def test_error_on_page_2(self):
        """Error on second page returns partial results from first page."""
        page1 = _mock_response({
            "value": [{"id": 1}, {"id": 2}],
            "@odata.nextLink": "https://api.example.com/items?skip=2",
        })
        mock_req = AsyncMock(side_effect=[(page1, None), (None, "HTTP 500 error")])
        from unittest.mock import patch
        with patch("asibot.connectors.pagination.token_store.safe_request", mock_req):
            pages = paginate_odata(
                MagicMock(), "https://api.example.com/items",
                service="Test", action="list",
            )
            result = await collect(pages, 100)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_max_pages_hit(self):
        """Stop after max_pages even if next link exists."""
        resp = _mock_response({
            "value": [{"id": 1}],
            "@odata.nextLink": "https://api.example.com/items?skip=1",
        })
        mock_req = AsyncMock(return_value=(resp, None))
        from unittest.mock import patch
        with patch("asibot.connectors.pagination.token_store.safe_request", mock_req):
            pages = paginate_odata(
                MagicMock(), "https://api.example.com/items",
                service="Test", action="list",
                max_pages=3,
            )
            result = await collect(pages, 1000)
        assert len(result) == 3
        assert mock_req.call_count == 3

    @pytest.mark.asyncio
    async def test_empty_first_page(self):
        """Empty first page returns no results."""
        resp = _mock_response({"value": []})
        mock_req = AsyncMock(return_value=(resp, None))
        from unittest.mock import patch
        with patch("asibot.connectors.pagination.token_store.safe_request", mock_req):
            pages = paginate_odata(
                MagicMock(), "https://api.example.com/items",
                service="Test", action="list",
            )
            result = await collect(pages, 100)
        assert result == []

    @pytest.mark.asyncio
    async def test_custom_results_key(self):
        """SAP-style nested results key."""
        resp = _mock_response({
            "d": {"results": [{"SalesOrder": "001"}], "__next": None},
        })
        mock_req = AsyncMock(return_value=(resp, None))
        from unittest.mock import patch
        with patch("asibot.connectors.pagination.token_store.safe_request", mock_req):
            pages = paginate_odata(
                MagicMock(), "https://api.example.com/orders",
                service="SAP", action="list",
                results_key="d.results",
                next_link_key="d.__next",
            )
            result = await collect(pages, 100)
        assert len(result) == 1
        assert result[0]["SalesOrder"] == "001"


# --- paginate_offset tests ---


class TestPaginateOffset:
    @pytest.mark.asyncio
    async def test_single_page(self):
        """Single page when result count < page_size."""
        resp = _mock_response({"items": [{"id": 1}]})
        mock_req = AsyncMock(return_value=(resp, None))
        from unittest.mock import patch
        with patch("asibot.connectors.pagination.token_store.safe_request", mock_req):
            pages = paginate_offset(
                MagicMock(), "https://api.example.com/search",
                service="Test", action="search",
                page_size=10,
            )
            result = await collect(pages, 100)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_multi_page_github_style(self):
        """GitHub-style page=1,2,3 pagination."""
        page1 = _mock_response({"items": [{"id": i} for i in range(10)]})
        page2 = _mock_response({"items": [{"id": i} for i in range(10, 20)]})
        page3 = _mock_response({"items": [{"id": i} for i in range(20, 25)]})
        mock_req = AsyncMock(side_effect=[(page1, None), (page2, None), (page3, None)])
        from unittest.mock import patch
        with patch("asibot.connectors.pagination.token_store.safe_request", mock_req):
            pages = paginate_offset(
                MagicMock(), "https://api.github.com/search/repos",
                service="GitHub", action="search repos",
                params={"q": "test"},
                results_key="items",
                page_size_param="per_page",
                offset_param="page",
                offset_start=1,
                offset_step=1,
                page_size=10,
            )
            result = await collect(pages, 100)
        assert len(result) == 25
        assert mock_req.call_count == 3

    @pytest.mark.asyncio
    async def test_bare_array_response(self):
        """results_key=None means the response is a bare JSON array."""
        resp = _mock_response([{"id": 1}, {"id": 2}])
        mock_req = AsyncMock(return_value=(resp, None))
        from unittest.mock import patch
        with patch("asibot.connectors.pagination.token_store.safe_request", mock_req):
            pages = paginate_offset(
                MagicMock(), "https://api.example.com/repos",
                service="Test", action="list",
                results_key=None,
                page_size=10,
            )
            result = await collect(pages, 100)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_total_key_stops_pagination(self):
        """Stop when total_seen >= total from response."""
        page1 = _mock_response({"issues": [{"id": 1}, {"id": 2}], "total": 2})
        mock_req = AsyncMock(return_value=(page1, None))
        from unittest.mock import patch
        with patch("asibot.connectors.pagination.token_store.safe_request", mock_req):
            pages = paginate_offset(
                MagicMock(), "https://jira.example.com/search",
                service="Jira", action="search",
                results_key="issues",
                page_size_param="maxResults",
                offset_param="startAt",
                page_size=2,
                total_key="total",
            )
            result = await collect(pages, 100)
        assert len(result) == 2
        assert mock_req.call_count == 1

    @pytest.mark.asyncio
    async def test_error_on_page_2(self):
        """Error on second page returns first page results."""
        page1 = _mock_response({"items": [{"id": i} for i in range(10)]})
        mock_req = AsyncMock(side_effect=[(page1, None), (None, "HTTP 500")])
        from unittest.mock import patch
        with patch("asibot.connectors.pagination.token_store.safe_request", mock_req):
            pages = paginate_offset(
                MagicMock(), "https://api.example.com/search",
                service="Test", action="search",
                page_size=10,
            )
            result = await collect(pages, 100)
        assert len(result) == 10

    @pytest.mark.asyncio
    async def test_max_pages_respected(self):
        """Respect max_pages limit."""
        resp = _mock_response({"items": [{"id": i} for i in range(10)]})
        mock_req = AsyncMock(return_value=(resp, None))
        from unittest.mock import patch
        with patch("asibot.connectors.pagination.token_store.safe_request", mock_req):
            pages = paginate_offset(
                MagicMock(), "https://api.example.com/search",
                service="Test", action="search",
                page_size=10,
                max_pages=2,
            )
            result = await collect(pages, 1000)
        assert len(result) == 20
        assert mock_req.call_count == 2


# --- paginate_cursor tests ---


class TestPaginateCursor:
    @pytest.mark.asyncio
    async def test_single_page_no_cursor(self):
        """Single page with no next cursor."""
        resp = _mock_response({"results": [{"id": 1}], "has_more": False})
        mock_req = AsyncMock(return_value=(resp, None))
        from unittest.mock import patch
        with patch("asibot.connectors.pagination.token_store.safe_request", mock_req):
            pages = paginate_cursor(
                MagicMock(), "https://api.notion.com/v1/search",
                service="Notion", action="search",
                json_body={"query": "test"},
                has_more_key="has_more",
                page_size=10,
            )
            result = await collect(pages, 100)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_multi_page_notion_style(self):
        """Notion-style cursor in JSON body."""
        page1 = _mock_response({
            "results": [{"id": 1}],
            "has_more": True,
            "next_cursor": "cursor_abc",
        })
        page2 = _mock_response({
            "results": [{"id": 2}],
            "has_more": False,
            "next_cursor": None,
        })
        mock_req = AsyncMock(side_effect=[(page1, None), (page2, None)])
        from unittest.mock import patch
        with patch("asibot.connectors.pagination.token_store.safe_request", mock_req):
            pages = paginate_cursor(
                MagicMock(), "https://api.notion.com/v1/search",
                service="Notion", action="search",
                json_body={"query": "test"},
                has_more_key="has_more",
                page_size=10,
            )
            result = await collect(pages, 100)
        assert len(result) == 2
        # Verify cursor was passed in second request
        call_args = mock_req.call_args_list[1]
        assert call_args.kwargs["json"]["start_cursor"] == "cursor_abc"

    @pytest.mark.asyncio
    async def test_cursor_in_params(self):
        """Google/Zoom-style cursor in query params."""
        page1 = _mock_response({
            "files": [{"id": 1}],
            "nextPageToken": "token_xyz",
        })
        page2 = _mock_response({
            "files": [{"id": 2}],
        })
        mock_req = AsyncMock(side_effect=[(page1, None), (page2, None)])
        from unittest.mock import patch
        with patch("asibot.connectors.pagination.token_store.safe_request", mock_req):
            pages = paginate_cursor(
                MagicMock(), "https://www.googleapis.com/drive/v3/files",
                method="GET",
                service="Google Drive", action="list",
                results_key="files",
                cursor_response_key="nextPageToken",
                cursor_request_key="pageToken",
                cursor_in="params",
                page_size_param="pageSize",
                page_size=10,
            )
            result = await collect(pages, 100)
        assert len(result) == 2
        # Verify cursor was passed in second request params
        call_args = mock_req.call_args_list[1]
        assert call_args.kwargs["params"]["pageToken"] == "token_xyz"

    @pytest.mark.asyncio
    async def test_nested_cursor_key(self):
        """HubSpot-style nested cursor key: paging.next.after."""
        page1 = _mock_response({
            "results": [{"id": 1}],
            "paging": {"next": {"after": "abc123"}},
        })
        page2 = _mock_response({
            "results": [{"id": 2}],
        })
        mock_req = AsyncMock(side_effect=[(page1, None), (page2, None)])
        from unittest.mock import patch
        with patch("asibot.connectors.pagination.token_store.safe_request", mock_req):
            pages = paginate_cursor(
                MagicMock(), "https://api.hubapi.com/crm/v3/objects/contacts/search",
                service="HubSpot", action="search",
                json_body={"query": "test"},
                cursor_response_key="paging.next.after",
                cursor_request_key="after",
                cursor_in="json",
                page_size_param="limit",
                page_size=10,
            )
            result = await collect(pages, 100)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_error_on_page_2(self):
        """Error on second page returns partial results."""
        page1 = _mock_response({
            "results": [{"id": 1}],
            "has_more": True,
            "next_cursor": "cursor_abc",
        })
        mock_req = AsyncMock(side_effect=[(page1, None), (None, "HTTP 500")])
        from unittest.mock import patch
        with patch("asibot.connectors.pagination.token_store.safe_request", mock_req):
            pages = paginate_cursor(
                MagicMock(), "https://api.notion.com/v1/search",
                service="Notion", action="search",
                json_body={"query": "test"},
                has_more_key="has_more",
                page_size=10,
            )
            result = await collect(pages, 100)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_max_pages_hit(self):
        """Stop after max_pages."""
        resp = _mock_response({
            "results": [{"id": 1}],
            "has_more": True,
            "next_cursor": "cursor",
        })
        mock_req = AsyncMock(return_value=(resp, None))
        from unittest.mock import patch
        with patch("asibot.connectors.pagination.token_store.safe_request", mock_req):
            pages = paginate_cursor(
                MagicMock(), "https://api.notion.com/v1/search",
                service="Notion", action="search",
                json_body={},
                has_more_key="has_more",
                page_size=10,
                max_pages=3,
            )
            result = await collect(pages, 1000)
        assert len(result) == 3
        assert mock_req.call_count == 3

    @pytest.mark.asyncio
    async def test_empty_first_page(self):
        """Empty first page returns no results."""
        resp = _mock_response({"results": [], "has_more": False})
        mock_req = AsyncMock(return_value=(resp, None))
        from unittest.mock import patch
        with patch("asibot.connectors.pagination.token_store.safe_request", mock_req):
            pages = paginate_cursor(
                MagicMock(), "https://api.notion.com/v1/search",
                service="Notion", action="search",
                json_body={},
                has_more_key="has_more",
                page_size=10,
            )
            result = await collect(pages, 100)
        assert result == []


# --- paginate_salesforce tests ---


class TestPaginateSalesforce:
    @pytest.mark.asyncio
    async def test_single_page_done(self):
        """Single page with done=true."""
        resp = _mock_response({
            "records": [{"Id": "001", "Name": "Acme"}],
            "done": True,
            "totalSize": 1,
        })
        mock_req = AsyncMock(return_value=(resp, None))
        from unittest.mock import patch
        with patch("asibot.connectors.pagination.token_store.safe_request", mock_req):
            pages = paginate_salesforce(
                MagicMock(), "/query",
                params={"q": "SELECT Id FROM Account"},
            )
            result = await collect(pages, 100)
        assert len(result) == 1
        assert result[0]["Name"] == "Acme"

    @pytest.mark.asyncio
    async def test_multi_page_follow_next(self):
        """Follow nextRecordsUrl across pages."""
        page1 = _mock_response({
            "records": [{"Id": "001"}],
            "done": False,
            "nextRecordsUrl": "/query/01gD00000-next",
        })
        page2 = _mock_response({
            "records": [{"Id": "002"}],
            "done": True,
        })
        mock_req = AsyncMock(side_effect=[(page1, None), (page2, None)])
        from unittest.mock import patch
        with patch("asibot.connectors.pagination.token_store.safe_request", mock_req):
            pages = paginate_salesforce(
                MagicMock(), "/query",
                params={"q": "SELECT Id FROM Account"},
            )
            result = await collect(pages, 100)
        assert len(result) == 2
        # Verify second request used the nextRecordsUrl
        call_args = mock_req.call_args_list[1]
        assert call_args[0][2] == "/query/01gD00000-next"

    @pytest.mark.asyncio
    async def test_error_on_first_page(self):
        """Error on first page returns empty."""
        mock_req = AsyncMock(return_value=(None, "HTTP 500"))
        from unittest.mock import patch
        with patch("asibot.connectors.pagination.token_store.safe_request", mock_req):
            pages = paginate_salesforce(
                MagicMock(), "/query",
                params={"q": "SELECT Id FROM Account"},
            )
            result = await collect(pages, 100)
        assert result == []

    @pytest.mark.asyncio
    async def test_error_on_page_2(self):
        """Error on second page returns first page."""
        page1 = _mock_response({
            "records": [{"Id": "001"}],
            "done": False,
            "nextRecordsUrl": "/query/next",
        })
        mock_req = AsyncMock(side_effect=[(page1, None), (None, "HTTP 500")])
        from unittest.mock import patch
        with patch("asibot.connectors.pagination.token_store.safe_request", mock_req):
            pages = paginate_salesforce(
                MagicMock(), "/query",
                params={"q": "SELECT Id FROM Account"},
            )
            result = await collect(pages, 100)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_empty_records(self):
        """Empty records returns nothing."""
        resp = _mock_response({"records": [], "done": True, "totalSize": 0})
        mock_req = AsyncMock(return_value=(resp, None))
        from unittest.mock import patch
        with patch("asibot.connectors.pagination.token_store.safe_request", mock_req):
            pages = paginate_salesforce(
                MagicMock(), "/query",
                params={"q": "SELECT Id FROM Account WHERE Name = 'Nobody'"},
            )
            result = await collect(pages, 100)
        assert result == []


# --- S2S Token Cache tests ---


class TestGetS2SToken:
    @pytest.mark.asyncio
    async def test_cached_token_returned(self):
        """Cached token is returned without making HTTP request."""
        from asibot.token_store import _s2s_token_cache, _S2S_TOKEN_MARGIN

        cache_key = "test:cid"
        _s2s_token_cache[cache_key] = ("cached_token", time.time() + 3600 + _S2S_TOKEN_MARGIN)
        try:
            from asibot import token_store
            token = await token_store.get_s2s_token(
                cache_key="test:cid",
                token_url="https://auth.example.com/token",
                grant_data={"grant_type": "client_credentials"},
                auth=("cid", "secret"),
                service_name="Test",
            )
            assert token == "cached_token"
        finally:
            _s2s_token_cache.pop(cache_key, None)

    @pytest.mark.asyncio
    async def test_expired_token_refetched(self):
        """Expired token triggers a new fetch."""
        from unittest.mock import patch
        from asibot.token_store import _s2s_token_cache

        cache_key = "test:cid"
        _s2s_token_cache[cache_key] = ("old_token", time.time() - 100)
        try:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"access_token": "new_token", "expires_in": 3600}
            mock_resp.raise_for_status.return_value = None

            mock_client = AsyncMock()
            mock_client.post.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)

            from asibot import token_store
            with patch("asibot.token_store.httpx.AsyncClient", return_value=mock_client):
                token = await token_store.get_s2s_token(
                    cache_key="test:cid",
                    token_url="https://auth.example.com/token",
                    grant_data={"grant_type": "client_credentials"},
                    auth=("cid", "secret"),
                    service_name="Test",
                )
            assert token == "new_token"
            # Verify it's cached in _s2s_token_cache
            assert cache_key in _s2s_token_cache
            assert _s2s_token_cache[cache_key][0] == "new_token"
        finally:
            _s2s_token_cache.pop(cache_key, None)
