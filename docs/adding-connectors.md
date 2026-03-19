# Adding a Connector

This guide walks you through adding a new connector to Asibot — for an internal tool, a third-party API, or anything with an HTTP API.

## What you'll create

1. A connector file at `src/asibot/connectors/yourservice.py` (~50-150 lines)
2. A client spec entry in `src/asibot/token_store.py` (3-5 lines)
3. A credential schema entry in `src/asibot/token_store.py` (2-3 lines)

That's it. The server auto-discovers connectors on startup — no registration code, no config changes.

## Step 1: Create the connector file

Create `src/asibot/connectors/yourservice.py`. Here's a complete working template:

```python
"""Your Service connector: describe what it does."""

import logging

from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store, validation
from asibot.connectors.base import Connector

logger = logging.getLogger(__name__)

# Base URL for the API
API = "https://api.yourservice.com/v1"


class YourServiceConnector(Connector):
    def __init__(self, config=None):
        super().__init__(name="yourservice", config=config)

    async def connect(self):
        logger.info("YourService: ready (per-user token)")

    async def disconnect(self):
        pass

    async def fetch_documents(self):
        return []

    def register_tools(self, mcp: FastMCP):

        @mcp.tool()
        async def yourservice_list_items(ctx: Context, limit: int = 25) -> str:
            """List items from Your Service.

            Args:
                limit: Max results (default: 25)
            """
            # Auth check — gets the user's HTTP client or returns an error
            client, uid, err = token_store.require_service(
                ctx, "yourservice", level="read",
            )
            if err:
                return err

            # Make the API call
            r, err = await token_store.safe_request(
                client, "GET", f"{API}/items",
                service="YourService", action="list items",
                params={"limit": limit},
            )
            if err:
                return err

            # Format the response for Claude
            items = r.json().get("items", [])
            if not items:
                return "No items found."
            lines = []
            for item in items:
                lines.append(f"{item.get('name', 'Untitled')} (ID: {item.get('id')})")
            return "\n".join(lines)

        @mcp.tool()
        async def yourservice_get_item(item_id: str, ctx: Context) -> str:
            """Get details for a specific item.

            Args:
                item_id: The item ID
            """
            err = validation.validate_id(item_id, "item_id")
            if err:
                return err
            client, uid, err = token_store.require_service(
                ctx, "yourservice", level="read",
            )
            if err:
                return err

            r, err = await token_store.safe_request(
                client, "GET", f"{API}/items/{item_id}",
                service="YourService", action="get item",
            )
            if err:
                return err

            data = r.json()
            return (
                f"Name: {data.get('name')}\n"
                f"Status: {data.get('status')}\n"
                f"Created: {data.get('created_at')}\n"
                f"Description: {data.get('description', 'None')}"
            )

        @mcp.tool()
        async def yourservice_create_item(
            name: str, description: str, ctx: Context,
        ) -> str:
            """Create a new item in Your Service.

            Args:
                name: Item name
                description: Item description
            """
            # Write operations use level="write"
            client, uid, err = token_store.require_service(
                ctx, "yourservice", level="write",
            )
            if err:
                return err

            r, err = await token_store.safe_request(
                client, "POST", f"{API}/items",
                service="YourService", action="create item",
                json={"name": name, "description": description},
            )
            if err:
                return err

            data = r.json()
            return f"Created item: {data.get('name')} (ID: {data.get('id')})"
```

### Key patterns

- **`token_store.require_service(ctx, "yourservice", level="read")`** — checks auth, loads the user's credentials, builds an HTTP client. Returns `(client, user_id, error)`.
- **`token_store.safe_request(client, method, url, service=..., action=...)`** — makes the HTTP call with rate limiting, error formatting, and circuit breaker integration. Returns `(response, error)`.
- **`level="read"` vs `level="write"`** — read tools work in read-only mode (default). Write tools require the user to have explicitly enabled write access for the service.
- **`validation.validate_id()`** / **`validation.validate_query()`** — input validation helpers. Use them to reject empty or malformed inputs before making API calls.
- **Tool docstrings matter** — Claude reads them to decide when to call each tool. Be specific about what the tool does and what each argument means.

## Step 2: Register the client spec

In `src/asibot/token_store.py`, add an entry to `CLIENT_SPECS`:

```python
CLIENT_SPECS: dict[str, ClientSpec] = {
    # ... existing entries ...
    "yourservice": ClientSpec(
        required_fields=("token",),
        headers={"Accept": "application/json"},
    ),
}
```

The `ClientSpec` handles HTTP client construction from the user's stored credentials. Common auth patterns:

```python
# Bearer token (most common)
ClientSpec(required_fields=("token",))

# API key in a custom header
ClientSpec(
    required_fields=("api_key",),
    auth_type="api_key",
    api_key_header="X-API-Key",
    api_key_field="api_key",
)

# Basic auth (email + token)
ClientSpec(
    required_fields=("email", "api_token", "domain"),
    auth_type="basic",
    base_url="https://{domain}/api/v2",
)

# No auth in client (token fetched at runtime, like Zoom/Paylocity)
ClientSpec(
    required_fields=("client_id", "client_secret"),
    auth_type="none",
)
```

## Step 3: Register the credential schema

In the same file, add an entry to `SERVICE_SCHEMAS`:

```python
SERVICE_SCHEMAS: dict[str, dict] = {
    # ... existing entries ...
    "yourservice": {
        "fields": ["token"],          # What the user needs to provide
        "labels": ["API Token"],      # Human-readable labels Claude shows
    },
}
```

If some fields are configured server-wide by the admin (like a base URL or org name), add them as `server_fields`:

```python
"yourservice": {
    "fields": ["token"],
    "labels": ["API Token"],
    "server_fields": ["base_url"],  # Admin sets ASIBOT_YOURSERVICE_BASE_URL
},
```

## Step 4: Test

```bash
# Restart the server (or rebuild Docker)
docker compose up --build -d

# Check the logs — your connector should appear
docker compose logs asibot | grep "yourservice"
# Expected: "Registered tools for connector: yourservice"
```

Then tell Claude: **"Connect me to Your Service"** — it will ask for the credentials defined in your schema, store them encrypted, and your tools are live.

## Tips

- **Start with read-only tools.** Get listing and detail views working first, add write operations later.
- **Return plain text, not JSON.** Claude reads your tool output as text — format it for readability, not machine parsing.
- **Keep tool count small.** 3-5 focused tools are better than 20 granular ones. Claude is good at composing a few flexible tools.
- **Use the `safe_request` wrapper.** It handles rate limiting, error formatting, and circuit breaker integration automatically. Don't call `client.get()` directly.
- **Test with `"What services am I connected to?"`** to verify your connector appears after connecting.
