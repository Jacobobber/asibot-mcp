"""Pagination helpers for connector API calls."""

import logging
from collections.abc import AsyncIterator
from typing import Any

from asibot import token_store

logger = logging.getLogger(__name__)
DEFAULT_MAX_PAGES = 50


def _deep_get(data: dict, dotted_key: str) -> Any:
    """Traverse nested dict using dot notation. Returns None if path not found.

    Tries the exact key first (e.g. "@odata.nextLink") before splitting on dots.
    """
    if not isinstance(data, dict):
        return None
    # Try exact key first (handles keys with dots like "@odata.nextLink")
    if dotted_key in data:
        return data[dotted_key]
    # Fall back to dot-separated traversal
    current = data
    for key in dotted_key.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(key)
        if current is None:
            return None
    return current


async def collect(pages: AsyncIterator[list[dict]], limit: int) -> list[dict]:
    """Collect results from a page generator up to limit total items."""
    results: list[dict] = []
    async for page in pages:
        results.extend(page)
        if len(results) >= limit:
            break
    return results[:limit]


async def paginate_odata(
    client,
    url: str,
    *,
    service: str,
    action: str,
    params: dict | None = None,
    results_key: str = "value",
    next_link_key: str = "@odata.nextLink",
    page_size: int = 100,
    max_pages: int = DEFAULT_MAX_PAGES,
    **request_kwargs,
) -> AsyncIterator[list[dict]]:
    """Follow OData-style next-link URLs until exhausted.

    Used by SharePoint, Outlook, Teams, ShareFile, SAP, Zendesk, Concur, RingCentral.
    """
    current_url = url
    current_params = dict(params) if params else {}
    for page_num in range(max_pages):
        r, err = await token_store.safe_request(
            client, "GET", current_url,
            service=service, action=action,
            params=current_params if current_params else None,
            **request_kwargs,
        )
        if err:
            logger.warning("%s %s pagination error on page %d: %s", service, action, page_num + 1, err)
            return
        data = r.json()
        items = _deep_get(data, results_key)
        if items is None:
            items = []
        if not isinstance(items, list):
            items = []
        if items:
            yield items
        if not items:
            return
        next_link = _deep_get(data, next_link_key)
        if not next_link:
            return
        # Follow the next link URL directly; params are embedded
        current_url = next_link
        current_params = {}


async def paginate_offset(
    client,
    url: str,
    *,
    service: str,
    action: str,
    params: dict | None = None,
    results_key: str | None = "items",
    page_size_param: str = "per_page",
    offset_param: str = "offset",
    offset_start: int = 0,
    offset_step: int | None = None,
    page_size: int = 100,
    max_pages: int = DEFAULT_MAX_PAGES,
    total_key: str | None = None,
    **request_kwargs,
) -> AsyncIterator[list[dict]]:
    """Increment offset or page number each request.

    Used by GitHub, Jira, Confluence, Smartsheet, LinkSquares, Paylocity.
    """
    step = offset_step if offset_step is not None else page_size
    current_offset = offset_start
    base_params = dict(params) if params else {}
    total_seen = 0

    for page_num in range(max_pages):
        page_params = {**base_params, page_size_param: page_size, offset_param: current_offset}
        r, err = await token_store.safe_request(
            client, "GET", url,
            service=service, action=action,
            params=page_params,
            **request_kwargs,
        )
        if err:
            logger.warning("%s %s pagination error on page %d: %s", service, action, page_num + 1, err)
            return
        data = r.json()
        if results_key is None:
            # Bare JSON array expected
            items = data if isinstance(data, list) else []
        else:
            items = _deep_get(data, results_key)
            if items is None:
                items = []
            if not isinstance(items, list):
                items = []
        if items:
            yield items
        total_seen += len(items)
        if not items or len(items) < page_size:
            return
        # Check total if available
        if total_key:
            total = _deep_get(data, total_key)
            if total is not None and total_seen >= total:
                return
        current_offset += step


async def paginate_cursor(
    client,
    url: str,
    *,
    method: str = "POST",
    service: str,
    action: str,
    params: dict | None = None,
    json_body: dict | None = None,
    results_key: str = "results",
    cursor_response_key: str = "next_cursor",
    cursor_request_key: str = "start_cursor",
    cursor_in: str = "json",
    page_size_param: str = "page_size",
    page_size: int = 100,
    max_pages: int = DEFAULT_MAX_PAGES,
    has_more_key: str | None = None,
    **request_kwargs,
) -> AsyncIterator[list[dict]]:
    """Follow cursor tokens in params or JSON body.

    Used by Notion, HubSpot, Google Workspace, Zoom, Figma, Adobe Sign.
    """
    base_params = dict(params) if params else {}
    base_json = dict(json_body) if json_body else {}
    cursor: str | None = None

    for page_num in range(max_pages):
        if cursor_in == "json":
            req_json = {**base_json, page_size_param: page_size}
            if cursor:
                req_json[cursor_request_key] = cursor
            r, err = await token_store.safe_request(
                client, method, url,
                service=service, action=action,
                params=base_params if base_params else None,
                json=req_json,
                **request_kwargs,
            )
        else:  # cursor_in == "params"
            req_params = {**base_params, page_size_param: page_size}
            if cursor:
                req_params[cursor_request_key] = cursor
            r, err = await token_store.safe_request(
                client, method, url,
                service=service, action=action,
                params=req_params,
                **request_kwargs,
            )
        if err:
            logger.warning("%s %s pagination error on page %d: %s", service, action, page_num + 1, err)
            return
        data = r.json()
        items = _deep_get(data, results_key)
        if items is None:
            items = []
        if not isinstance(items, list):
            items = []
        if items:
            yield items
        if not items:
            return
        # Check has_more flag if provided
        if has_more_key:
            has_more = _deep_get(data, has_more_key)
            if not has_more:
                return
        cursor = _deep_get(data, cursor_response_key)
        if not cursor:
            return


async def paginate_salesforce(
    client,
    initial_url: str,
    *,
    service: str = "Salesforce",
    action: str = "query",
    params: dict | None = None,
    max_pages: int = DEFAULT_MAX_PAGES,
    **request_kwargs,
) -> AsyncIterator[list[dict]]:
    """Follow Salesforce nextRecordsUrl until done=true.

    Used by salesforce_query.
    """
    # First request uses the provided URL with params
    r, err = await token_store.safe_request(
        client, "GET", initial_url,
        service=service, action=action,
        params=params,
        **request_kwargs,
    )
    if err:
        logger.warning("%s %s pagination error on page 1: %s", service, action, err)
        return
    data = r.json()
    records = data.get("records", [])
    if records:
        yield records
    if not records or data.get("done", True):
        return

    # Follow subsequent pages
    for page_num in range(1, max_pages):
        next_url = data.get("nextRecordsUrl")
        if not next_url:
            return
        r, err = await token_store.safe_request(
            client, "GET", next_url,
            service=service, action=action,
            **request_kwargs,
        )
        if err:
            logger.warning("%s %s pagination error on page %d: %s", service, action, page_num + 1, err)
            return
        data = r.json()
        records = data.get("records", [])
        if records:
            yield records
        if not records or data.get("done", True):
            return
