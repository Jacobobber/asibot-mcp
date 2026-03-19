# Asibot

Asibot gives Claude direct access to 26 enterprise platforms your team already uses — from Microsoft 365 and Salesforce to GitHub and Jira — so employees can search, read, create, and act across all of them in a single conversation, with per-user authentication and encrypted credentials.

## Get Started

Your Claude Enterprise admin configures the Asibot MCP integration once — after that, it's available to every employee across Claude Desktop, Claude Code, and claude.ai automatically. No per-user setup needed.

The first time you use Asibot, tell Claude:

> **Set up my Asibot account.**

Claude walks you through Microsoft SSO sign-in. After that, just talk:

> **Connect me to Jira.**

> **Search SharePoint for the Q4 budget deck.**

---

## What You Can Do

**Work across platforms without switching tabs.** Ask Claude to pull context from multiple tools in one conversation — find a Jira ticket, check the related GitHub PR, look up the customer in Salesforce, and draft a summary email in Outlook.

**Read and write everywhere.** Search SharePoint files, read email threads, create Jira issues, update Salesforce records, send Teams messages, add Notion pages, run Zapier actions — 150+ operations across 26 services.

**Stay in control.** Every connection is per-user with individual credentials. Set services to read-only or read-write. Disconnect anytime. Admins control who can access what.

### Supported Platforms

| Category | Services |
|----------|----------|
| **Microsoft 365** | SharePoint, Outlook, Teams, Calendar |
| **Google Workspace** | Drive, Calendar |
| **Dev & Engineering** | GitHub, Jira, Confluence |
| **CRM & Sales** | Salesforce, HubSpot |
| **Support** | Zendesk |
| **Productivity** | Notion, Smartsheet, Figma |
| **Communication** | Zoom, RingCentral |
| **Finance & HR** | SAP, SAP Concur, Paylocity |
| **Documents & Legal** | Adobe Sign, LinkSquares, Citrix ShareFile |
| **Automation & ML** | Zapier, Roboflow |

<details>
<summary><strong>Full connector capabilities</strong></summary>

**Microsoft 365** — Sign in once to unlock all four:
- **SharePoint**: Search files, browse folders, read documents (Word, PDF, Excel, CSV, Markdown), list sites, view version history
- **Outlook**: Search email, read threads, send emails, browse folders, view attachments
- **Teams**: List teams/channels, read conversations, search messages, send messages, list members
- **Calendar**: View events, create events with attendees

**Google Workspace**
- **Drive**: Search files, browse folders, read documents (Docs, Sheets, text, PDF)
- **Calendar**: View events, create events

**GitHub** — Search repos/code, browse issues/PRs, create issues, view diffs, check CI status, list releases/branches

**Jira** — Search with JQL, view issues with comments, create issues, add comments, list sprints, move issues between statuses

**Confluence** — Search pages, read content, list spaces, view history, create pages

**Salesforce** — Search records, run SOQL, describe objects, create/update records

**HubSpot** — Search contacts/deals/companies, view profiles and pipelines, create contacts and deals

**Zendesk** — Search tickets, read threads, search Help Center, create tickets, add comments

**Notion** — Search pages/databases, query with filters, create pages, update properties, add database entries

**Figma** — List projects/files, view structure, read comments, list components and styles

**Smartsheet** — List sheets, read data, search, add rows

**Zoom** — List meetings, view details, browse recordings, get transcripts

**Adobe Sign** — List agreements, view status, get signing URLs, view audit trail

**SAP** — List/search sales orders, view details, look up customers

**SAP Concur** — List expense reports, view details with line items, view pending approvals

**Citrix ShareFile** — Browse files, search documents, list shared links

**LinkSquares** — List contracts, search, view AI-extracted values, list amendments

**Paylocity** — List employees, search, view pay statements, list departments

**RingCentral** — View call log, browse messages, check presence, view voicemail

**Roboflow** — List projects, view dataset versions, view model metrics

**Zapier** — List actions, run with natural language, preview with dry run

</details>

---

## Managing Connections

Everything is managed through conversation:

- **"Connect me to Jira"** — Claude walks you through authentication
- **"What services am I connected to?"** — see all active connections
- **"Disconnect Salesforce"** — remove stored credentials
- **"Set GitHub to read-only"** — control access levels per service
- **"Rotate my API key"** — generate a new key, old one stops immediately

---

## Server Deployment (Azure)

This section is for admins deploying the Asibot server on an Azure Linux VM.

### Prerequisites

- An Azure Linux VM (Standard D4as v5 recommended for 1000+ users)
- Docker and Docker Compose installed on the VM
- A Microsoft Entra ID app registration
- TLS certificates (or use Azure Application Gateway for termination)

### 1. Azure AD App Registration

1. **Azure Portal → Entra ID → App registrations → New registration**
2. Name: `Asibot`, account type: **Single tenant**
3. **Authentication → Advanced settings** → enable **Allow public client flows**
4. **API permissions** → add **Microsoft Graph** delegated permissions:
   - `User.Read`, `GroupMember.Read.All`, `Sites.Read.All`, `Files.Read.All`, `Mail.Read`, `Calendars.Read`, `Team.ReadBasic.All`, `ChannelMessage.Read.All`, `Chat.Read`, `Notes.Read.All`, `Tasks.Read`, `offline_access`
5. **Grant admin consent** for all permissions
6. Note your **Tenant ID** and **Application (client) ID**

### 2. Admin Role Sync (Optional)

Automatically assign admin roles from Azure AD group membership:

1. Create a security group in Azure AD (e.g., "Asibot Admins")
2. Add the employees who should be admins
3. Set `ASIBOT_ADMIN_GROUP_ID` to the group's **Object ID**

When users sign in, the server checks group membership and assigns `admin` or `user` role automatically.

### 3. Configure and Deploy

Docker Compose handles everything — PostgreSQL, PgBouncer, Redis, Nginx, Prometheus, and Asibot itself. The database schema is created automatically on first start. You just set a few environment variables:

```bash
cp .env.example .env
# Edit .env with your values, then:
docker compose up --build -d
```

**Required `.env` settings:**

```env
POSTGRES_PASSWORD=your-secure-password          # Used by PostgreSQL and Asibot
ASIBOT_MS365_TENANT_ID=your-tenant-id           # From step 1
ASIBOT_MS365_CLIENT_ID=your-client-id           # From step 1
```

That's it for a working deployment. Everything else has sensible defaults. The compose stack brings up PostgreSQL 16, PgBouncer (connection pooling), Redis (distributed cache), Nginx (TLS + rate limiting), Prometheus, and AlertManager automatically.

**Recommended additions:**

```env
ASIBOT_ADMIN_GROUP_ID=your-security-group-id    # Azure AD role sync (step 2)
ASIBOT_METRICS_BEARER_TOKEN=your-secret         # Secure the metrics endpoint
ASIBOT_SHAREPOINT_SITE_URL=yourcompany.sharepoint.com
```

**Optional — OAuth for GitHub/Google:**

```env
ASIBOT_GITHUB_CLIENT_ID=your-github-client-id
ASIBOT_GOOGLE_CLIENT_ID=your-google-client-id
ASIBOT_GOOGLE_CLIENT_SECRET=your-google-secret
```

**Optional — business defaults** (pre-fill org-level settings so users only provide personal tokens):

```env
ASIBOT_GITHUB_ORG=mycompany
ASIBOT_ATLASSIAN_DOMAIN=mycompany.atlassian.net
ASIBOT_ZENDESK_SUBDOMAIN=mycompany
ASIBOT_SALESFORCE_INSTANCE_URL=https://mycompany.my.salesforce.com
```

**TLS:** Place certificates at `deploy/certs/cert.pem` and `deploy/certs/key.pem`, or set `TLS_CERT_PATH`/`TLS_KEY_PATH` in `.env`. Alternatively, terminate TLS at Azure Application Gateway and set `ASIBOT_ALLOW_INSECURE_HTTP=true`.

**High availability** (adds PostgreSQL streaming replication):

```bash
docker compose -f docker-compose.yml -f docker-compose.ha.yml up --build -d
```

### 4. Verify

```bash
curl https://your-server/health
```

---

## Architecture & Design

```
┌─────────────────┐     ┌──────────────┐     ┌──────────────┐
│  Claude Desktop  │────▶│    Nginx     │────▶│    Asibot    │
│  (employees)     │     │  (TLS, rate  │     │  (FastMCP)   │
│                  │     │   limiting)  │     │  port 8080   │
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

### Key Design Choices

**Authentication** — Microsoft SSO via device code flow. Users sign in once in the browser, the server polls for a token, creates an API key (`asb_...`), and returns a config snippet for Claude Desktop. Subsequent requests use the API key in the `Authorization` header, resolved to a user via a 3-level lookup: in-memory LRU cache (10K sessions) → PostgreSQL → API key table.

**Credential isolation** — Every user's credentials are stored separately, encrypted at rest with Fernet symmetric encryption. The master key lives at `~/.asibot/master.key` with 600 permissions. Optional external KMS (AWS KMS, HashiCorp Vault) for production.

**Role sync** — During SSO setup, the server calls Microsoft Graph `/me/memberOf` to check Azure AD group membership. Members of a configured security group get `admin`; everyone else gets `user`. Roles update automatically on re-setup.

**Rate limiting (3 layers)** — Global per-service sliding window (200 req/min default, configurable per service), per-user limits (30 req/min), and Nginx rate limiting (30 req/s per IP, 2 req/min for setup endpoint).

**Circuit breaker** — Per-service circuit breakers prevent cascade failures. 5 failures → circuit opens → fast-fail for 60 seconds → half-open probe → close on success. All state tracked in Prometheus gauges.

**Connection pooling** — Shared `httpx.AsyncClient` pool (200 clients, LRU eviction) avoids TCP/TLS connection churn. PgBouncer (transaction mode, 200 max connections) pools database connections. Both designed for 1000+ concurrent users.

**Caching** — Three layers: Redis distributed cache for S2S OAuth tokens and rate limit counters, in-memory session store (10K LRU cap), and HTTP connection pool. Falls back gracefully — Redis unavailable → in-memory, no data loss.

**Observability** — Prometheus metrics (request latency histograms, error counters, circuit state gauges, session cache hit rates), AlertManager rules (error rate, circuit open, auth spikes, latency).

**Concurrency** — asyncio event loop with semaphore-based limiting at three levels: 2000 global, 10 per-user, 200 per-service. Background tasks for cleanup (sessions, tokens, rate limits, audit pruning) run on 60-300 second intervals with error isolation.

---

## Configuration Reference

All settings use the `ASIBOT_` prefix. Set via environment variables or `.env` file.

<details>
<summary><strong>Full environment variable reference</strong></summary>

### Server

| Variable | Default | Description |
|----------|---------|-------------|
| `ASIBOT_TRANSPORT` | `stdio` | Set to `streamable-http` for server deployment |
| `ASIBOT_HOST` | `0.0.0.0` | Listen address |
| `ASIBOT_PORT` | `8080` | Listen port |
| `ASIBOT_ALLOW_INSECURE_HTTP` | `false` | Allow HTTP without TLS (behind reverse proxy) |

### Authentication & Roles

| Variable | Default | Description |
|----------|---------|-------------|
| `ASIBOT_MS365_TENANT_ID` | | Azure AD tenant ID |
| `ASIBOT_MS365_CLIENT_ID` | | Azure AD app client ID |
| `ASIBOT_ADMIN_GROUP_ID` | | Azure AD security group ID — members get `admin` role |
| `ASIBOT_GITHUB_CLIENT_ID` | | GitHub OAuth App client ID |
| `ASIBOT_GOOGLE_CLIENT_ID` | | Google OAuth client ID |
| `ASIBOT_GOOGLE_CLIENT_SECRET` | | Google OAuth client secret |

### Database & Sessions

| Variable | Default | Description |
|----------|---------|-------------|
| `ASIBOT_DATABASE_URL` | | PostgreSQL connection string |
| `ASIBOT_DATABASE_READ_URL` | | Read replica URL (optional) |
| `ASIBOT_PG_POOL_MIN_SIZE` | `10` | Minimum connection pool size |
| `ASIBOT_PG_POOL_MAX_SIZE` | `100` | Maximum connection pool size |
| `ASIBOT_SESSION_BACKEND` | `memory` | `memory` or `redis` |
| `ASIBOT_REDIS_URL` | | Redis connection string |
| `ASIBOT_SESSION_TTL` | `3600` | Session inactivity timeout (seconds) |
| `ASIBOT_ABSOLUTE_SESSION_TTL` | `28800` | Hard session lifetime (8h) |

### Rate Limiting & Concurrency

| Variable | Default | Description |
|----------|---------|-------------|
| `ASIBOT_GLOBAL_RATE_LIMIT_DEFAULT` | `200` | Requests/min per service (all users) |
| `ASIBOT_GLOBAL_RATE_LIMITS` | `{}` | Per-service overrides (JSON) |
| `ASIBOT_PER_USER_RATE_LIMIT_DEFAULT` | `30` | Requests/min per user per service |
| `ASIBOT_MAX_CONCURRENT_REQUESTS` | `2000` | Global concurrent tool call cap |
| `ASIBOT_MAX_CONCURRENT_PER_USER` | `10` | Per-user concurrent cap |
| `ASIBOT_MAX_CONCURRENT_PER_SERVICE` | `200` | Per-service concurrent cap |

### Resilience

| Variable | Default | Description |
|----------|---------|-------------|
| `ASIBOT_CIRCUIT_FAILURE_THRESHOLD` | `5` | Failures before circuit opens |
| `ASIBOT_CIRCUIT_RECOVERY_TIMEOUT` | `60` | Seconds before half-open probe |
| `ASIBOT_MAX_RETRIES` | `3` | Retry attempts for transient failures |

### Monitoring

| Variable | Default | Description |
|----------|---------|-------------|
| `ASIBOT_METRICS_ENABLED` | `true` | Enable Prometheus metrics |
| `ASIBOT_METRICS_HOST` | `127.0.0.1` | Metrics listen address |
| `ASIBOT_METRICS_PORT` | `9090` | Metrics listen port |
| `ASIBOT_METRICS_BEARER_TOKEN` | | Auth token for metrics endpoint |
| `ASIBOT_AUDIT_RETENTION_DAYS` | `365` | Days to keep audit entries |

### Secrets Management

| Variable | Default | Description |
|----------|---------|-------------|
| `ASIBOT_KMS_PROVIDER` | | `aws`, `vault`, or empty (local file) |
| `ASIBOT_KMS_KEY_ID` | | AWS KMS key ARN or Vault path |
| `ASIBOT_VAULT_ADDR` | | HashiCorp Vault address |
| `ASIBOT_VAULT_TOKEN` | | Vault auth token |

</details>

---

## Operations

- **Runbook**: [`deploy/runbook.md`](deploy/runbook.md) — health checks, alert triage, scaling, incident response
- **Backup & Restore**: [`deploy/backup-restore.md`](deploy/backup-restore.md) — PostgreSQL dumps, master key backup, restore procedures
- **Load Testing**: [`tests/load/`](tests/load/) — Locust-based load tests for capacity validation

**Key backup warning:** The master encryption key at `~/.asibot/master.key` (or `/data/master.key` in Docker) encrypts all stored credentials. **If lost, all user credentials are unrecoverable.** Back it up separately. See [`deploy/backup-restore.md`](deploy/backup-restore.md).
