# Asibot

Enterprise connector agent exposed as an MCP server. Connects to 23+ SaaS platforms and lets you search, query, and manage data through Claude Desktop or any MCP client.

## Quick Start

```bash
# Clone and install
git clone <repo-url> && cd asibot
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Configure
cp .env.example .env
# Edit .env with your Microsoft 365 app registration (required for SSO)

# Run (stdio mode for Claude Desktop)
asibot-stdio

# Or run as HTTP server
ASIBOT_TRANSPORT=streamable-http asibot
```

## Architecture

```
src/asibot/
├── server.py          # MCP server entry point, 13 core tools
├── stdio_server.py    # Claude Desktop stdio transport entry point
├── config.py          # Settings via pydantic-settings (ASIBOT_ env prefix)
├── auth.py            # User creation/lookup, API key store (encrypted)
├── user_session.py    # Per-request auth, session cache (LRU), rate limiting
├── token_store.py     # Per-user credential/preference storage, ClientSpec factory
├── crypto.py          # Fernet encryption at rest, key rotation
├── audit.py           # Append-only audit log with secret redaction (rotating)
├── validation.py      # Input validation (IDs, queries, URLs, injection prevention)
└── connectors/
    ├── base.py        # Abstract Connector class
    ├── registry.py    # Auto-discovery and registration
    ├── microsoft.py   # Shared MS Graph auth (SSO device code flow)
    └── ...            # 23 service connectors
```

**Data directory:** `~/.asibot/` — contains encrypted user profiles, per-user credentials, and audit logs.

## Security

- **Encryption at rest** — All credentials and tokens encrypted with Fernet (AES-128-CBC). Master key stored with `0600` permissions.
- **Key rotation** — `crypto.rotate_key()` re-encrypts all user data with a new key and backs up the old one.
- **Input validation** — All user-supplied parameters validated before reaching external APIs: ID format, query length, path traversal prevention, SOQL object allowlisting, URL scheme enforcement.
- **Rate limiting** — Failed auth attempts rate-limited per key prefix (10 failures / 5 minutes).
- **Session management** — LRU session cache with 1-hour TTL, 10k cap, and per-user invalidation on key rotation.
- **Audit logging** — Append-only JSON log with automatic secret redaction and log rotation (10 MB, 5 backups).
- **Per-user isolation** — Each user's credentials stored in separate encrypted files.
- **Permission control** — Per-service enabled/disabled and read/readwrite modes.
- **TLS warning** — Server warns when running HTTP transport without TLS.

## Configuration

All settings use the `ASIBOT_` env prefix. Copy `.env.example` to `.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `ASIBOT_TRANSPORT` | `stdio` | `stdio` (Claude Desktop) or `streamable-http` (web) |
| `ASIBOT_HOST` | `0.0.0.0` | HTTP server bind address |
| `ASIBOT_PORT` | `8080` | HTTP server port |
| `ASIBOT_SHAREPOINT_TENANT_ID` | | Azure AD tenant ID (required for SSO) |
| `ASIBOT_SHAREPOINT_CLIENT_ID` | | Azure AD app client ID (required for SSO) |
| `ASIBOT_SHAREPOINT_SITE_URL` | | Default SharePoint site (e.g., `company.sharepoint.com`) |

## User Setup

1. Start the server
2. In Claude Desktop, call `asibot_setup` — this starts a Microsoft SSO device code flow
3. Sign in via browser with the provided code
4. Call `asibot_setup_status` to get your API key and Claude Desktop config snippet
5. Add the config to `claude_desktop_config.json` and restart Claude Desktop

For local dev with a single user, the API key header is optional — the server auto-resolves the only registered user.

## MCP Tools

### Setup & Identity
- `asibot_setup` — One-time SSO account creation
- `asibot_setup_status` — Get API key after SSO sign-in
- `asibot_whoami` — Check current authenticated user
- `asibot_rotate_key` — Generate a new API key (invalidates old one)

### Credential Management
- `asibot_connect <service>` — Get instructions to connect a service
- `asibot_set_credentials <service> <json>` — Store service credentials
- `asibot_disconnect <service>` — Remove service credentials
- `asibot_services` — List all services with connection status

### Permission Control
- `asibot_enable <service>` — Enable a connector
- `asibot_disable <service>` — Disable a connector
- `asibot_set_mode <service> <read|readwrite>` — Set access level

## Connectors

Each connector is auto-discovered at startup. Users connect services individually via `asibot_connect`.

| Connector | Auth Type | What You Need |
|-----------|-----------|---------------|
| **SharePoint** | Microsoft SSO | Handled by `asibot_setup` |
| **Outlook** | Microsoft SSO | Handled by `asibot_setup` |
| **Teams** | Microsoft SSO | Handled by `asibot_setup` |
| **Calendar** | Microsoft SSO | Handled by `asibot_setup` |
| **GitHub** | Personal Access Token | Token + org name |
| **Jira** | Email + API Token | Email, token, domain (e.g., `company.atlassian.net`) |
| **Confluence** | Email + API Token | Same as Jira |
| **Notion** | Integration Token | Internal integration token |
| **Zendesk** | Email + API Token | Subdomain, email, token |
| **HubSpot** | Private App Token | Access token |
| **Salesforce** | OAuth Token | Instance URL + token |
| **Google Workspace** | OAuth Token | OAuth access token |
| **Figma** | Personal Access Token | Token |
| **Zoom** | Server-to-Server OAuth | Account ID, client ID, client secret |
| **Zapier NLA** | API Key | NLA API key |
| **Adobe Sign** | OAuth Token | Token |
| **RingCentral** | OAuth Token | Token |
| **Roboflow** | API Key | API key + workspace |
| **Smartsheet** | Bearer Token | API token |
| **Paylocity** | Client Credentials OAuth | Client ID, client secret, company ID |
| **SAP Concur** | OAuth Token | Token |
| **Citrix ShareFile** | OAuth Token | Token + subdomain |
| **SAP** | Bearer Token | Base URL (HTTPS) + token |
| **LinkSquares** | Bearer Token | API token |

### Adding a New Connector

1. Create `src/asibot/connectors/yourservice.py`
2. Subclass `Connector` and implement `connect()`, `disconnect()`, `fetch_documents()`, `register_tools()`
3. Add credential schema to `SERVICE_SCHEMAS` and a `ClientSpec` to `CLIENT_SPECS` in `token_store.py`
4. Use `token_store.safe_request()` for all API calls (standardized error handling)
5. The connector is auto-discovered at startup — no registration code needed

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Lint
ruff check src/

# Run with hot reload (HTTP mode)
ASIBOT_TRANSPORT=streamable-http python -m asibot.server
```

## Claude Desktop Config

For **stdio** mode (local, direct):
```json
{
  "mcpServers": {
    "asibot": {
      "command": "asibot-stdio"
    }
  }
}
```

For **HTTP** mode (remote server):
```json
{
  "mcpServers": {
    "asibot": {
      "command": "npx",
      "args": [
        "mcp-remote",
        "https://your-server.com:8080/mcp",
        "--header",
        "Authorization:Bearer YOUR_API_KEY"
      ]
    }
  }
}
```
