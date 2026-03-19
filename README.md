# Asibot

Connect Claude to the tools your team already uses. Asibot is an MCP server that gives Claude access to 26 SaaS platforms — search files, read emails, query databases, create issues, and more, all from a single conversation.

## Quick Start

### Claude Desktop (individual)

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "asibot": {
      "command": "asibot-stdio"
    }
  }
}
```

Restart Claude Desktop and say: **"Set up Asibot and connect me to [service name]."**

### Claude Code (CLI)

```
claude mcp add asibot asibot-stdio
```

Then ask Claude: **"Set up Asibot and connect me to [service name]."**

### Remote Server (Enterprise)

For shared multi-user deployments, add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "asibot": {
      "command": "npx",
      "args": [
        "mcp-remote",
        "https://your-server.com/mcp",
        "--header",
        "Authorization:Bearer YOUR_API_KEY"
      ]
    }
  }
}
```

Ask your admin for the server URL, or say: **"Set up my Asibot account."** Claude will guide you through Microsoft SSO sign-in.

---

## Server Deployment

This section is for admins deploying Asibot as a shared server for their organization.

### Prerequisites

- Docker and Docker Compose
- A Microsoft Entra ID (Azure AD) app registration
- TLS certificates (for production)

### 1. Azure AD App Registration

Create an app registration in the Azure portal:

1. Go to **Azure Portal → Entra ID → App registrations → New registration**
2. Name: `Asibot`
3. Supported account types: **Single tenant**
4. Under **Authentication → Advanced settings**, enable **Allow public client flows** (required for device code flow)
5. Under **API permissions**, add these **Microsoft Graph** delegated permissions:
   - `User.Read` — sign-in and profile
   - `GroupMember.Read.All` — role sync from security groups
   - `Sites.Read.All` — SharePoint
   - `Files.Read.All` — OneDrive/SharePoint files
   - `Mail.Read` — Outlook
   - `Calendars.Read` — Calendar
   - `Team.ReadBasic.All` — Teams
   - `ChannelMessage.Read.All` — Teams messages
   - `Chat.Read` — Teams chat
   - `Notes.Read.All` — OneNote
   - `Tasks.Read` — To Do / Planner
   - `offline_access` — refresh tokens
6. Click **Grant admin consent** for all permissions
7. Note your **Tenant ID** and **Application (client) ID**

### 2. Admin Role Sync (Optional)

To automatically assign admin roles based on Azure AD group membership:

1. Create a security group in Azure AD (e.g., "Asibot Admins")
2. Add the employees who should be admins
3. Copy the **Object ID** of the group
4. Set `ASIBOT_ADMIN_GROUP_ID` to that Object ID

When users sign in via `asibot_setup`, the server checks their group membership and assigns `admin` or `user` role automatically. No manual role assignment needed.

### 3. Configuration

Copy `.env.example` to `.env` and configure:

```bash
cp .env.example .env
```

#### Required Settings

```env
# Server
ASIBOT_TRANSPORT=streamable-http
ASIBOT_PORT=8080
ASIBOT_ALLOW_INSECURE_HTTP=true  # Only if behind a TLS-terminating reverse proxy

# Microsoft 365 SSO (from step 1)
ASIBOT_MS365_TENANT_ID=your-azure-tenant-id
ASIBOT_MS365_CLIENT_ID=your-app-client-id

# PostgreSQL (required for production)
ASIBOT_DATABASE_URL=postgresql://asibot:YOUR_PASSWORD@pgbouncer:6432/asibot
POSTGRES_PASSWORD=YOUR_PASSWORD
```

#### Recommended Settings

```env
# Azure AD admin group (from step 2)
ASIBOT_ADMIN_GROUP_ID=your-security-group-object-id

# Redis (required for multi-replica deployments)
ASIBOT_SESSION_BACKEND=redis
ASIBOT_REDIS_URL=redis://redis:6379/0

# Dashboard access control
ASIBOT_DASHBOARD_TOKEN_TTL=86400        # Per-user token TTL in seconds (default: 24h)
ASIBOT_DASHBOARD_MIN_ROLE=user          # "user" = everyone, "admin" = admins only

# Metrics and dashboard auth (for direct access without MCP)
ASIBOT_METRICS_BEARER_TOKEN=your-secret-token
ASIBOT_DASHBOARD_BEARER_TOKEN=your-secret-token

# SharePoint site URL
ASIBOT_SHAREPOINT_SITE_URL=yourcompany.sharepoint.com
```

#### Optional: OAuth for GitHub and Google

```env
# GitHub OAuth App (enables zero-input device code flow)
ASIBOT_GITHUB_CLIENT_ID=your-github-oauth-app-client-id

# Google OAuth (enables Google Workspace connectors)
ASIBOT_GOOGLE_CLIENT_ID=your-google-client-id
ASIBOT_GOOGLE_CLIENT_SECRET=your-google-client-secret
```

#### Optional: Business Defaults

Pre-fill per-service settings so users only need to provide their personal tokens:

```env
ASIBOT_GITHUB_ORG=mycompany
ASIBOT_ATLASSIAN_DOMAIN=mycompany.atlassian.net
ASIBOT_ZENDESK_SUBDOMAIN=mycompany
ASIBOT_SALESFORCE_INSTANCE_URL=https://mycompany.my.salesforce.com
ASIBOT_SHAREFILE_SUBDOMAIN=mycompany
ASIBOT_SAP_BASE_URL=https://api.sap.mycompany.com
ASIBOT_ROBOFLOW_WORKSPACE=mycompany
```

### 4. Deploy with Docker Compose

#### Development

```bash
docker compose -f docker-compose.dev.yml up --build -d
```

This starts PostgreSQL + Asibot on ports 8080 (MCP) and 8081 (dashboard).

#### Production

```bash
docker compose up --build -d
```

The production stack includes:
- **PostgreSQL 16** with health checks
- **PgBouncer** connection pooler (transaction mode, up to 200 connections)
- **Redis 7** for distributed cache (256MB, LRU eviction)
- **Nginx** reverse proxy with TLS termination and rate limiting
- **Prometheus + AlertManager** for monitoring
- **postgres-exporter** for database metrics

TLS certificates must be provided at `deploy/certs/cert.pem` and `deploy/certs/key.pem`, or set `TLS_CERT_PATH` and `TLS_KEY_PATH` in your `.env`.

#### High Availability

For PostgreSQL streaming replication:

```bash
docker compose -f docker-compose.yml -f docker-compose.ha.yml up --build -d
```

### 5. Verify

```bash
# Health check
curl http://localhost:8080/health

# Dashboard (uses auto-generated token from logs)
docker compose logs asibot | grep "dashboard"
# Open the URL shown in the logs
```

---

## Analytics Dashboard

The dashboard shows usage analytics: tool calls, active users, error rates, latency, and per-service metrics.

### Access Methods

**Via Claude (recommended):** Ask Claude: **"Get me a dashboard link."** Claude calls `asibot_dashboard_login` and returns a personal, time-limited URL.

- **Admins** see full org-wide analytics
- **Regular users** see only their own activity
- Links expire after 24 hours (configurable via `ASIBOT_DASHBOARD_TOKEN_TTL`)

**Via static token (admin fallback):** The server logs a dashboard URL with a static admin token on startup. This token gives full access and doesn't expire.

**Via bearer token (API/scripts):** Set `ASIBOT_DASHBOARD_BEARER_TOKEN` for a fixed token usable in `Authorization: Bearer <token>` headers.

---

## Configuration Reference

All settings use the `ASIBOT_` prefix and can be set via environment variables or `.env` file.

### Server

| Variable | Default | Description |
|----------|---------|-------------|
| `ASIBOT_TRANSPORT` | `stdio` | `stdio` (single-user) or `streamable-http` (multi-user server) |
| `ASIBOT_HOST` | `0.0.0.0` | Listen address |
| `ASIBOT_PORT` | `8080` | Listen port |
| `ASIBOT_ALLOW_INSECURE_HTTP` | `false` | Allow HTTP without TLS (set `true` when behind reverse proxy) |

### Authentication & Roles

| Variable | Default | Description |
|----------|---------|-------------|
| `ASIBOT_MS365_TENANT_ID` | | Azure AD tenant ID |
| `ASIBOT_MS365_CLIENT_ID` | | Azure AD app client ID |
| `ASIBOT_ADMIN_GROUP_ID` | | Azure AD security group Object ID — members get `admin` role |
| `ASIBOT_GITHUB_CLIENT_ID` | | GitHub OAuth App client ID |
| `ASIBOT_GOOGLE_CLIENT_ID` | | Google OAuth client ID |
| `ASIBOT_GOOGLE_CLIENT_SECRET` | | Google OAuth client secret |

### Dashboard

| Variable | Default | Description |
|----------|---------|-------------|
| `ASIBOT_DASHBOARD_ENABLED` | `true` | Enable analytics dashboard |
| `ASIBOT_DASHBOARD_PORT` | `8081` | Dashboard listen port |
| `ASIBOT_DASHBOARD_BEARER_TOKEN` | | Fixed admin token for dashboard (auto-generated if empty) |
| `ASIBOT_DASHBOARD_TOKEN_TTL` | `86400` | Per-user dashboard token TTL in seconds (24h) |
| `ASIBOT_DASHBOARD_MIN_ROLE` | `user` | Minimum role to access dashboard (`user` or `admin`) |

### Database & Sessions

| Variable | Default | Description |
|----------|---------|-------------|
| `ASIBOT_DATABASE_URL` | | PostgreSQL connection string (falls back to SQLite) |
| `ASIBOT_DATABASE_READ_URL` | | Read replica URL (optional) |
| `ASIBOT_PG_POOL_MIN_SIZE` | `10` | Minimum async connection pool size |
| `ASIBOT_PG_POOL_MAX_SIZE` | `100` | Maximum async connection pool size |
| `ASIBOT_SESSION_BACKEND` | `memory` | `memory` or `redis` |
| `ASIBOT_REDIS_URL` | | Redis connection string |
| `ASIBOT_SESSION_TTL` | `3600` | Session inactivity timeout (seconds) |
| `ASIBOT_ABSOLUTE_SESSION_TTL` | `28800` | Hard session lifetime cap (seconds, 8h) |

### Rate Limiting

| Variable | Default | Description |
|----------|---------|-------------|
| `ASIBOT_GLOBAL_RATE_LIMIT_DEFAULT` | `200` | Requests/minute per external service (all users combined) |
| `ASIBOT_GLOBAL_RATE_LIMITS` | `{}` | Per-service overrides, JSON (e.g., `{"github": 80}`) |
| `ASIBOT_PER_USER_RATE_LIMIT_DEFAULT` | `30` | Requests/minute per user per service |

### Concurrency

| Variable | Default | Description |
|----------|---------|-------------|
| `ASIBOT_MAX_CONCURRENT_REQUESTS` | `2000` | Global cap on simultaneous tool calls |
| `ASIBOT_MAX_CONCURRENT_PER_USER` | `10` | Per-user concurrent tool call limit |
| `ASIBOT_MAX_CONCURRENT_PER_SERVICE` | `200` | Per-external-service concurrent call limit |
| `ASIBOT_MAX_CONCURRENT_SETUPS` | `100` | Max concurrent device-code polling tasks |

### Resilience

| Variable | Default | Description |
|----------|---------|-------------|
| `ASIBOT_CIRCUIT_FAILURE_THRESHOLD` | `5` | Failures before circuit opens |
| `ASIBOT_CIRCUIT_RECOVERY_TIMEOUT` | `60` | Seconds before circuit tries half-open |
| `ASIBOT_MAX_RETRIES` | `3` | Retry attempts for transient API failures |

### Monitoring

| Variable | Default | Description |
|----------|---------|-------------|
| `ASIBOT_METRICS_ENABLED` | `true` | Enable Prometheus metrics |
| `ASIBOT_METRICS_HOST` | `127.0.0.1` | Metrics listen address (localhost-only by default) |
| `ASIBOT_METRICS_PORT` | `9090` | Metrics listen port |
| `ASIBOT_METRICS_BEARER_TOKEN` | | Bearer token for metrics endpoint auth |
| `ASIBOT_AUDIT_RETENTION_DAYS` | `365` | Days to keep audit log entries |

### Secrets Management

| Variable | Default | Description |
|----------|---------|-------------|
| `ASIBOT_KMS_PROVIDER` | | `aws`, `vault`, or empty (local file) |
| `ASIBOT_KMS_KEY_ID` | | AWS KMS key ARN or Vault path |
| `ASIBOT_VAULT_ADDR` | | HashiCorp Vault address |
| `ASIBOT_VAULT_TOKEN` | | Vault auth token |

---

## Architecture

```
┌─────────────────┐     ┌──────────────┐     ┌──────────────┐
│  Claude Desktop  │────▶│    Nginx     │────▶│    Asibot    │
│  Claude Code     │     │  (TLS, rate  │     │  (FastMCP)   │
│  Claude API      │     │   limiting)  │     │  port 8080   │
└─────────────────┘     └──────────────┘     └──────┬───────┘
                                                     │
                              ┌───────────────┬──────┴──────────┐
                              │               │                 │
                        ┌─────▼─────┐  ┌──────▼──────┐  ┌──────▼──────┐
                        │ PgBouncer │  │    Redis    │  │  External   │
                        │  (pool)   │  │  (cache)   │  │  SaaS APIs  │
                        └─────┬─────┘  └─────────────┘  │  (26 svcs) │
                        ┌─────▼─────┐                    └─────────────┘
                        │ PostgreSQL│
                        │    16     │
                        └───────────┘
```

- **Transport**: `stdio` for single-user desktop, `streamable-http` for multi-user server
- **Auth**: Microsoft SSO device code flow → per-user API keys (`asb_...`)
- **Credentials**: Encrypted at rest with Fernet (master key at `~/.asibot/master.key`)
- **Roles**: Synced from Azure AD group membership — `admin` or `user`
- **Rate limiting**: 3 layers — global per-service, per-user, and Nginx
- **Circuit breaker**: Per-service, opens after 5 failures, recovers after 60s
- **Caching**: Redis distributed cache for S2S tokens + in-memory session cache (10K cap)
- **Monitoring**: Prometheus metrics + AlertManager rules for error rate, latency, circuit state

---

## Connectors

### Microsoft 365

Sign in once with your Microsoft account to unlock SharePoint, Outlook, Teams, and Calendar.

**SharePoint** — Search files and documents, browse folders, read files (Word, PDF, Excel, CSV, text, Markdown), list sites, view file details and version history

**Outlook** — Search email, read threads, send emails, browse by folder, list folders, view attachments

**Teams** — List teams and channels, read conversations, search messages, view chats, list members, send messages

**Calendar** — View upcoming events, create events with attendees

### Google Workspace

**Google Drive** — Search files, browse folders, read documents (Docs, Sheets, text, PDF), view file details

**Google Calendar** — View events, get details with attendees, create events

### GitHub

Search repos and code, list org repos, browse issues/PRs, read comments, create issues, view PR diffs, browse commits, list releases/branches, check CI status

### Jira

Search with JQL, view issue details with comments, list projects, see assigned issues, create issues, add comments, list sprints, view transitions, move issues

### Confluence

Search pages, read content, list spaces, browse pages, view history, list attachments, create pages

### Salesforce

Search records, run SOQL, get record details, describe objects, create and update records

### HubSpot

Search contacts/deals/companies, view profiles, view pipelines, view activity timelines, create contacts and deals

### Zendesk

Search tickets, read ticket threads, search Help Center, look up users, create tickets, add comments

### Notion

Search pages/databases, read pages, list databases, query with filters, create pages, update properties, add database entries

### Figma

List projects and files, view structure, read comments, browse versions, list components and styles

### Smartsheet

List sheets, read data, search, view rows, list columns, add rows

### Zoom

List meetings, view details, browse recordings, view participants, list past meetings, get transcripts

### Adobe Sign

List agreements, view details/status, get signing URLs, view audit trail, list templates, get form data

### SAP

List/search sales orders, view details and line items, look up customers, view delivery schedules

### SAP Concur

List expense reports, view details with line items, list entries, view pending approvals

### Citrix ShareFile

Browse files/folders, search documents, view details, download text files, list shared links

### LinkSquares

List contracts, search contract data, view details, view AI-extracted values, list amendments

### Paylocity

List employees, view details, search by name, view pay statements, list departments

### RingCentral

View call log, browse messages, get recording details, check presence, list extensions, view voicemail

### Roboflow

List projects, view details, list dataset versions, view preprocessing, view model metrics

### Zapier

List configured actions, run actions with natural language, preview with dry run, view action details

---

## Managing Connections

Once Asibot is installed, manage everything through conversation:

- **"Connect me to Jira"** — Claude walks you through authentication
- **"What services am I connected to?"** — see all active connections
- **"Disconnect Salesforce"** — remove stored credentials
- **"Set GitHub to read-only"** — control access levels per service
- **"Get me a dashboard link"** — personal analytics dashboard (admins see org-wide, users see their own)
- **"Rotate my API key"** — generate a new key, old one stops working immediately

---

## Operations

- **Runbook**: [`deploy/runbook.md`](deploy/runbook.md) — health checks, alert triage, scaling, incident response
- **Backup & Restore**: [`deploy/backup-restore.md`](deploy/backup-restore.md) — PostgreSQL dumps, master key backup, restore procedures
- **Load Testing**: [`tests/load/`](tests/load/) — Locust-based load tests for capacity validation

### Key Backup Warning

The master encryption key at `~/.asibot/master.key` (or `/data/master.key` in Docker) encrypts all stored credentials. **If this key is lost, all user credentials are unrecoverable.** Back it up to a separate, secure location. See [`deploy/backup-restore.md`](deploy/backup-restore.md) for procedures.
