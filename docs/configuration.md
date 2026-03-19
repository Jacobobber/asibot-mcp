# Configuration Reference

All settings use the `ASIBOT_` prefix and can be set via environment variables or `.env` file.

## Server

| Variable | Default | Description |
|----------|---------|-------------|
| `ASIBOT_TRANSPORT` | `stdio` | Set to `streamable-http` for server deployment |
| `ASIBOT_HOST` | `0.0.0.0` | Listen address |
| `ASIBOT_PORT` | `8080` | Listen port |
| `ASIBOT_ALLOW_INSECURE_HTTP` | `false` | Allow HTTP without TLS (set `true` when behind a TLS-terminating reverse proxy) |

## Authentication & Roles

| Variable | Default | Description |
|----------|---------|-------------|
| `ASIBOT_MS365_TENANT_ID` | | Azure AD tenant ID |
| `ASIBOT_MS365_CLIENT_ID` | | Azure AD app client ID |
| `ASIBOT_ADMIN_GROUP_ID` | | Azure AD security group Object ID — members get `admin` role |
| `ASIBOT_GITHUB_CLIENT_ID` | | GitHub OAuth App client ID (enables device code flow) |
| `ASIBOT_GOOGLE_CLIENT_ID` | | Google OAuth client ID |
| `ASIBOT_GOOGLE_CLIENT_SECRET` | | Google OAuth client secret |

## Database & Sessions

| Variable | Default | Description |
|----------|---------|-------------|
| `ASIBOT_DATABASE_URL` | | PostgreSQL connection string (e.g., `postgresql://asibot:pass@pgbouncer:6432/asibot`) |
| `ASIBOT_DATABASE_READ_URL` | | Read replica URL (optional, falls back to `DATABASE_URL`) |
| `ASIBOT_PG_POOL_MIN_SIZE` | `10` | Minimum async connection pool size |
| `ASIBOT_PG_POOL_MAX_SIZE` | `100` | Maximum async connection pool size |
| `ASIBOT_SESSION_BACKEND` | `memory` | `memory` or `redis` (use `redis` for multi-replica) |
| `ASIBOT_REDIS_URL` | | Redis connection string (e.g., `redis://redis:6379/0`) |
| `ASIBOT_SESSION_TTL` | `3600` | Session inactivity timeout in seconds (1h) |
| `ASIBOT_ABSOLUTE_SESSION_TTL` | `28800` | Hard session lifetime cap in seconds (8h) |

## Dashboard

| Variable | Default | Description |
|----------|---------|-------------|
| `ASIBOT_DASHBOARD_ENABLED` | `true` | Enable the analytics dashboard |
| `ASIBOT_DASHBOARD_PORT` | `8081` | Dashboard listen port |
| `ASIBOT_DASHBOARD_BEARER_TOKEN` | | Fixed admin token for dashboard (auto-generated if empty) |
| `ASIBOT_DASHBOARD_TOKEN_TTL` | `86400` | Per-user dashboard token TTL in seconds (24h) |
| `ASIBOT_DASHBOARD_MIN_ROLE` | `user` | Minimum role to access dashboard (`user` or `admin`) |

## Business Defaults

Pre-fill org-level settings so users only need to provide their personal tokens:

| Variable | Default | Description |
|----------|---------|-------------|
| `ASIBOT_GITHUB_ORG` | | GitHub organization name |
| `ASIBOT_ATLASSIAN_DOMAIN` | | Atlassian domain (e.g., `mycompany.atlassian.net`) |
| `ASIBOT_ZENDESK_SUBDOMAIN` | | Zendesk subdomain (e.g., `mycompany`) |
| `ASIBOT_SALESFORCE_INSTANCE_URL` | | Salesforce instance URL |
| `ASIBOT_SHAREFILE_SUBDOMAIN` | | Citrix ShareFile subdomain |
| `ASIBOT_SAP_BASE_URL` | | SAP API base URL |
| `ASIBOT_ROBOFLOW_WORKSPACE` | | Roboflow workspace name |
| `ASIBOT_SHAREPOINT_SITE_URL` | | SharePoint site URL (e.g., `yourcompany.sharepoint.com`) |

## Rate Limiting & Concurrency

| Variable | Default | Description |
|----------|---------|-------------|
| `ASIBOT_GLOBAL_RATE_LIMIT_DEFAULT` | `200` | Requests/minute per external service (all users combined) |
| `ASIBOT_GLOBAL_RATE_LIMITS` | `{}` | Per-service overrides as JSON (e.g., `{"github": 80, "salesforce": 100}`) |
| `ASIBOT_PER_USER_RATE_LIMIT_DEFAULT` | `30` | Requests/minute per user per service |
| `ASIBOT_MAX_CONCURRENT_REQUESTS` | `2000` | Global cap on simultaneous tool calls |
| `ASIBOT_MAX_CONCURRENT_PER_USER` | `10` | Per-user concurrent tool call limit |
| `ASIBOT_MAX_CONCURRENT_PER_SERVICE` | `200` | Per-external-service concurrent call limit |
| `ASIBOT_MAX_CONCURRENT_SETUPS` | `100` | Max concurrent device-code polling tasks |

## Resilience

| Variable | Default | Description |
|----------|---------|-------------|
| `ASIBOT_CIRCUIT_FAILURE_THRESHOLD` | `5` | Consecutive failures before circuit breaker opens |
| `ASIBOT_CIRCUIT_RECOVERY_TIMEOUT` | `60` | Seconds in open state before half-open probe |
| `ASIBOT_MAX_RETRIES` | `3` | Retry attempts for transient API failures |
| `ASIBOT_RETRY_BASE_DELAY` | `1.0` | Initial retry backoff delay in seconds |

## Monitoring

| Variable | Default | Description |
|----------|---------|-------------|
| `ASIBOT_METRICS_ENABLED` | `true` | Enable Prometheus metrics endpoint |
| `ASIBOT_METRICS_HOST` | `127.0.0.1` | Metrics listen address (localhost-only by default) |
| `ASIBOT_METRICS_PORT` | `9090` | Metrics listen port |
| `ASIBOT_METRICS_BEARER_TOKEN` | | Bearer token for metrics endpoint authentication |
| `ASIBOT_AUDIT_RETENTION_DAYS` | `365` | Days to retain audit log entries |

## Secrets Management

| Variable | Default | Description |
|----------|---------|-------------|
| `ASIBOT_KMS_PROVIDER` | | External KMS: `aws`, `vault`, or empty for local file encryption |
| `ASIBOT_KMS_KEY_ID` | | AWS KMS key ARN or HashiCorp Vault path |
| `ASIBOT_VAULT_ADDR` | | HashiCorp Vault server address |
| `ASIBOT_VAULT_TOKEN` | | Vault authentication token |

## Docker Compose Variables

These are used by `docker-compose.yml` directly (no `ASIBOT_` prefix):

| Variable | Default | Description |
|----------|---------|-------------|
| `POSTGRES_PASSWORD` | *(required)* | PostgreSQL password |
| `TLS_CERT_PATH` | `./deploy/certs/cert.pem` | Path to TLS certificate |
| `TLS_KEY_PATH` | `./deploy/certs/key.pem` | Path to TLS private key |
