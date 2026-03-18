# Asibot MCP Server -- Operations Runbook

## 1. Service Overview

Asibot is an MCP (Model Context Protocol) server that connects AI assistants to enterprise tools (GitHub, Jira, Salesforce, etc.) via a unified authentication and credential management layer.

```
                        +-----------+
         Users          |  Nginx    |  TLS termination, rate limiting
  (Claude Desktop) ---> |  :443     |
                        +-----+-----+
                              |
                        +-----v-----+
                        |  Asibot   |  MCP server (:8080) + Dashboard (:8081)
                        |  Python   |  Metrics (:9090, localhost only)
                        +-----+-----+
                              |
                  +-----------+-----------+
                  |                       |
            +-----v-----+         +------v------+
            | PgBouncer  |         | External    |
            | :6432      |         | APIs        |
            +-----+------+         | (GitHub,    |
                  |                | Salesforce, |
            +-----v-----+         | Jira, ...)  |
            | PostgreSQL |         +-------------+
            | :5432      |
            +------------+
```

**Components:**
- **Nginx** -- TLS reverse proxy, rate limiting (30 req/s API, 2 req/min setup)
- **Asibot** -- FastMCP Python server, session management, credential encryption
- **PgBouncer** -- Connection pooler (transaction mode, max 200 client conns)
- **PostgreSQL 16** -- Users, credentials (encrypted), sessions, audit log
- **Prometheus metrics** -- Exposed on :9090 (localhost only, optional bearer auth)

**Key config env vars:** `ASIBOT_DATABASE_URL`, `ASIBOT_TRANSPORT`, `ASIBOT_METRICS_ENABLED`, `ASIBOT_PG_POOL_MIN_SIZE`, `ASIBOT_PG_POOL_MAX_SIZE`, `ASIBOT_SESSION_CACHE_SIZE`.

---

## 2. Health Checks

### Asibot application

```bash
# Via Nginx (external)
curl -sk https://localhost:443/health

# Direct (internal, from same host or container network)
curl http://localhost:8080/health
```

Expected: HTTP 200. The `asibot_health` tool returns JSON:
```json
{
  "status": "ok",
  "data_dir_exists": true,
  "connectors_loaded": 20,
  "pending_setups": 0,
  "transport": "streamable-http",
  "http_pool": {"pool_size": 5, "max_pool_size": 200},
  "background_tasks": 6
}
```

### PostgreSQL

```bash
docker compose exec postgres pg_isready -U asibot
```

### PgBouncer

```bash
docker compose exec pgbouncer pg_isready -h 127.0.0.1 -p 6432 -U asibot
```

### Metrics endpoint

```bash
# Without auth
curl http://127.0.0.1:9090/metrics

# With bearer token (if ASIBOT_METRICS_BEARER_TOKEN is set)
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:9090/metrics
```

### Docker health

```bash
docker compose ps          # All services should show "healthy"
docker compose logs --tail=50 asibot
```

---

## 3. Common Alerts and Responses

### 3.1 High Error Rate (>5%)

**Detection:**
```promql
sum(rate(asibot_requests_total{status!="ok"}[5m])) / sum(rate(asibot_requests_total[5m])) > 0.05
```

**Diagnosis:**
1. Check which service is generating errors:
   ```promql
   sum(rate(asibot_requests_total{status="error"}[5m])) by (service)
   ```
2. Check application logs:
   ```bash
   docker compose logs --since=15m asibot | grep -i error
   ```
3. Check if upstream APIs are down:
   ```bash
   curl -s https://www.githubstatus.com/api/v2/status.json | jq .status
   curl -s https://status.atlassian.com/api/v2/status.json | jq .status
   ```
4. Check DB connectivity:
   ```bash
   docker compose exec postgres psql -U asibot -c "SELECT 1"
   ```

**Mitigation:**
- If a single upstream service, the circuit breaker should auto-trip. Verify with `asibot_circuit_state` metric.
- If DB related, check pool exhaustion (section 5).
- If widespread, check Nginx error log: `docker compose logs nginx | tail -100`.

### 3.2 Circuit Breaker Open

**Detection:**
```promql
asibot_circuit_state == 2
```

**Diagnosis:**
1. Identify the tripped service from the `service` label.
2. Check recent error logs for that service:
   ```bash
   docker compose logs --since=30m asibot | grep -i "<service_name>"
   ```
3. Verify the upstream API status.
4. Check if credentials have expired (token refresh failure).

**Recovery:**
- Circuit breaker auto-recovers after `ASIBOT_CIRCUIT_RECOVERY_TIMEOUT` seconds (default 60s).
- It transitions to half-open, allows a probe request, and closes on success.
- To force immediate reset, restart the Asibot container:
  ```bash
  docker compose restart asibot
  ```
- If the upstream is genuinely down, let the circuit stay open -- it protects the system.

### 3.3 Auth Failure Spike

**Detection:**
```promql
sum(rate(asibot_auth_failures_total[5m])) > 10
```

**Diagnosis -- Brute force vs misconfigured client:**
1. Check failure reasons:
   ```promql
   sum(rate(asibot_auth_failures_total[5m])) by (reason)
   ```
2. Review Nginx access logs for IP patterns:
   ```bash
   docker compose logs nginx | grep " 401 " | awk '{print $1}' | sort | uniq -c | sort -rn | head -20
   ```
3. Check audit log for repeated failures from one user:
   ```bash
   docker compose exec postgres psql -U asibot -c \
     "SELECT user_id, count(*) FROM audit_log WHERE event='auth_failure' AND ts > extract(epoch from now()) - 900 GROUP BY user_id ORDER BY count DESC LIMIT 10"
   ```

**Mitigation:**
- **Brute force:** Block the IP at the Nginx level or firewall. Add to `deny` list in nginx.conf.
- **Misconfigured client:** Contact the user. They likely have a stale API key -- advise `asibot_rotate_key`.

### 3.4 Audit Write Failure

**Detection:** `system` service errors in metrics, or exceptions in logs:
```bash
docker compose logs asibot | grep -i "audit" | grep -i "error\|fail"
```

**Diagnosis:**
1. Check disk space:
   ```bash
   df -h /data                       # inside container
   docker compose exec asibot df -h  # from host
   ```
2. Check DB write capability:
   ```bash
   docker compose exec postgres psql -U asibot -c "INSERT INTO audit_log (ts, event) VALUES (extract(epoch from now()), 'health_check') RETURNING id"
   ```
3. Check PostgreSQL logs:
   ```bash
   docker compose logs --since=15m postgres
   ```

**Mitigation:**
- **Disk full:** Prune old audit records:
  ```bash
  docker compose exec postgres psql -U asibot -c \
    "DELETE FROM audit_log WHERE ts < extract(epoch from now()) - (86400 * 90)"
  ```
- **Permission issue:** Check volume mounts in docker-compose.yml. Ensure `asibot-data` volume is writable.
- Audit writes are best-effort (tool calls still succeed) -- the JSONL file audit is the backup record.

### 3.5 High Latency (p95 > 2s)

**Detection:**
```promql
histogram_quantile(0.95, sum(rate(asibot_request_duration_seconds_bucket[5m])) by (le)) > 2
```

**Diagnosis:**
1. Identify slow services:
   ```promql
   histogram_quantile(0.95, sum(rate(asibot_request_duration_seconds_bucket[5m])) by (le, service)) > 2
   ```
2. Check DB pool saturation:
   ```bash
   docker compose exec postgres psql -U asibot -c \
     "SELECT state, count(*) FROM pg_stat_activity WHERE datname='asibot' GROUP BY state"
   ```
3. Check PgBouncer stats:
   ```bash
   docker compose exec pgbouncer psql -U asibot -p 6432 pgbouncer -c "SHOW POOLS"
   ```
4. Check for long-running queries:
   ```bash
   docker compose exec postgres psql -U asibot -c \
     "SELECT pid, now() - pg_stat_activity.query_start AS duration, query FROM pg_stat_activity WHERE datname='asibot' AND state != 'idle' ORDER BY duration DESC LIMIT 5"
   ```

**Mitigation:**
- **DB pool exhaustion:** Increase `ASIBOT_PG_POOL_MAX_SIZE` (and PgBouncer `default_pool_size`).
- **Slow upstream:** Check circuit breaker state. If one service is slow, the circuit breaker will eventually trip.
- **Lock contention:** Check for idle-in-transaction connections:
  ```bash
  docker compose exec postgres psql -U asibot -c \
    "SELECT count(*) FROM pg_stat_activity WHERE state='idle in transaction' AND datname='asibot'"
  ```

### 3.6 Session Cache Eviction Rate High

**Detection:**
```promql
rate(asibot_session_cache_misses_total[5m]) / (rate(asibot_session_cache_hits_total[5m]) + rate(asibot_session_cache_misses_total[5m])) > 0.5
```

**Diagnosis:**
- Cache size is `ASIBOT_SESSION_CACHE_SIZE` (default 1000). If active sessions exceed this, the LRU cache evicts entries and falls back to DB lookups.
- Check active sessions: `asibot_active_sessions` metric.

**Mitigation:**
- Increase `ASIBOT_SESSION_CACHE_SIZE` (e.g., 2000 or 5000). Each entry is small (~200 bytes).
- Reduce `ASIBOT_SESSION_TTL` to expire idle sessions faster.
- If user count genuinely exceeds single-instance capacity, scale horizontally (section 4).

---

## 4. Scaling Operations

### Vertical scaling

Adjust resource limits in `docker-compose.yml`:
```yaml
deploy:
  resources:
    limits:
      cpus: '4'     # was 2
      memory: 8G    # was 4G
```

Tune pool sizes accordingly:
```bash
# .env
ASIBOT_PG_POOL_MAX_SIZE=200
ASIBOT_SESSION_CACHE_SIZE=5000
ASIBOT_MAX_CONCURRENT_REQUESTS=4000
```

### Horizontal scaling (multiple replicas)

Asibot stores session state in PostgreSQL, so replicas are stateless. To add replicas:

1. Scale the Asibot service:
   ```bash
   docker compose up -d --scale asibot=3
   ```
2. Update Nginx upstream to round-robin:
   ```nginx
   upstream asibot_backend {
       server asibot:8080;
       # Docker DNS resolves to all replicas
   }
   ```
3. Increase PgBouncer limits proportionally:
   ```ini
   max_client_conn = 600   # 200 per replica * 3
   default_pool_size = 40
   ```

### When to scale

| Signal | Threshold | Action |
|--------|-----------|--------|
| CPU sustained >80% | 5+ minutes | Add CPU or replica |
| Memory >75% | Sustained | Increase memory limit |
| p95 latency >2s | 5+ minutes | Check DB pool first, then scale |
| Active sessions > cache size | Sustained | Increase cache or add replica |
| PgBouncer `cl_waiting` > 0 | Sustained | Increase pool sizes |

---

## 5. Database Operations

### Connection pool tuning

```bash
# Check current pool usage
docker compose exec pgbouncer psql -U asibot -p 6432 pgbouncer -c "SHOW POOLS"
docker compose exec pgbouncer psql -U asibot -p 6432 pgbouncer -c "SHOW STATS"

# Check PostgreSQL connections
docker compose exec postgres psql -U asibot -c \
  "SELECT count(*), state FROM pg_stat_activity WHERE datname='asibot' GROUP BY state"

# Check for connection limit pressure
docker compose exec postgres psql -U asibot -c \
  "SELECT max_conn, used, max_conn - used AS available FROM (SELECT count(*) AS used FROM pg_stat_activity) u, (SELECT setting::int AS max_conn FROM pg_settings WHERE name='max_connections') m"
```

### Useful pg_stat queries

```bash
# Table bloat / dead tuples
docker compose exec postgres psql -U asibot -c \
  "SELECT relname, n_dead_tup, n_live_tup, last_vacuum, last_autovacuum FROM pg_stat_user_tables ORDER BY n_dead_tup DESC"

# Index usage
docker compose exec postgres psql -U asibot -c \
  "SELECT indexrelname, idx_scan, idx_tup_read FROM pg_stat_user_indexes ORDER BY idx_scan DESC"

# Slow queries (if pg_stat_statements is enabled)
docker compose exec postgres psql -U asibot -c \
  "SELECT query, calls, mean_exec_time, total_exec_time FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 10"
```

### Manual vacuum

```bash
# Regular vacuum (non-blocking)
docker compose exec postgres psql -U asibot -c "VACUUM ANALYZE audit_log"

# Full vacuum (blocks writes -- use during maintenance window only)
docker compose exec postgres psql -U asibot -c "VACUUM FULL audit_log"
```

### Audit log pruning

Automatic pruning runs hourly (background task). To prune manually:
```bash
docker compose exec postgres psql -U asibot -c \
  "DELETE FROM audit_log WHERE ts < extract(epoch from now()) - (86400 * $ASIBOT_AUDIT_RETENTION_DAYS)"
```

Default retention: 365 days. Adjust via `ASIBOT_AUDIT_RETENTION_DAYS`.

---

## 6. PostgreSQL HA (Read Replicas)

### Overview

PostgreSQL HA uses native streaming replication to create read replicas. The primary
handles all writes; replicas serve read-only queries (user lookups, credential reads,
audit queries, session validation, statistics). This reduces primary load and improves
read latency.

**Production recommendation:** For 1000+ employee deployments, use a managed PostgreSQL
service (AWS RDS, Google Cloud SQL, Azure Database for PostgreSQL) instead of
self-managed HA. Managed services provide automated failover, backups, monitoring, and
patching with significantly less operational burden.

### Enabling HA

#### Docker Compose (local/dev)

Use the HA override file:
```bash
docker compose -f docker-compose.yml -f docker-compose.ha.yml up
```

This starts:
- **postgres** -- primary with WAL-level replication enabled
- **postgres-replica** -- streaming replica (hot standby)
- **asibot** -- configured with `ASIBOT_DATABASE_READ_URL` pointing to the replica

#### Kubernetes (Helm)

```bash
helm upgrade asibot deploy/helm/asibot \
  --set postgresql.ha.enabled=true \
  --set postgresql.ha.replicas=2 \
  --set postgresql.ha.replicationPassword=<secret>
```

This deploys a replica StatefulSet and configures Asibot to route read-only queries
to the `<release>-postgres-replicas` headless service.

#### Read replica URL configuration

Set `ASIBOT_DATABASE_READ_URL` to point to your replica:
```bash
# Docker Compose HA override sets this automatically:
ASIBOT_DATABASE_READ_URL=postgresql://asibot:<password>@postgres-replica:5432/asibot

# For managed services (e.g., AWS RDS):
ASIBOT_DATABASE_READ_URL=postgresql://asibot:<password>@my-db-reader.cluster-xyz.us-east-1.rds.amazonaws.com:5432/asibot
```

If `ASIBOT_DATABASE_READ_URL` is empty or identical to `ASIBOT_DATABASE_URL`, all queries
go to the primary (no read/write splitting).

### Monitoring replication lag

#### On the primary

```sql
SELECT client_addr,
       state,
       pg_wal_lsn_diff(pg_current_wal_lsn(), sent_lsn) AS send_lag_bytes,
       pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn) AS replay_lag_bytes
FROM pg_stat_replication;
```

#### Via Prometheus

The `postgres-exporter` service exposes `pg_stat_replication_pg_wal_lsn_diff` as a gauge.
Alerts fire when:

| Alert | Threshold | Severity |
|-------|-----------|----------|
| `PostgreSQL_ReplicationLag` | > 10MB for 5 min | SEV2 |
| `PostgreSQL_ReplicaDown` | replica unreachable for 1 min | SEV1 |
| `PostgreSQL_ConnectionSaturation` | > 80% connections used for 5 min | SEV2 |

```bash
# Quick check via docker
docker compose exec postgres psql -U asibot -c \
  "SELECT client_addr, state, pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn) AS lag_bytes FROM pg_stat_replication"
```

### Failover procedure

**Self-managed HA (Docker Compose / bare Kubernetes):**

1. Verify the primary is truly down:
   ```bash
   docker compose exec postgres pg_isready -U asibot
   ```

2. Promote the replica to primary:
   ```bash
   docker compose exec postgres-replica pg_ctl promote -D /var/lib/postgresql/data/pgdata
   ```

3. Update `ASIBOT_DATABASE_URL` to point to the promoted replica:
   ```bash
   # In .env:
   ASIBOT_DATABASE_URL=postgresql://asibot:<password>@postgres-replica:5432/asibot
   ```

4. Restart Asibot to pick up the new primary:
   ```bash
   docker compose restart asibot
   ```

5. Clear `ASIBOT_DATABASE_READ_URL` (no replica available until a new one is provisioned):
   ```bash
   # In .env: remove or blank out ASIBOT_DATABASE_READ_URL
   ```

6. Provision a new replica from the promoted primary when ready.

**Managed services (RDS, Cloud SQL):** Failover is automatic. The reader endpoint
continues to work after failover. No manual intervention is needed for Asibot -- the
DNS-based endpoints resolve to the new primary/replicas automatically.

### Troubleshooting

**Replica not starting (pg_basebackup fails):**
```bash
# Check that the replication user exists on the primary
docker compose exec postgres psql -U asibot -c "SELECT rolname FROM pg_roles WHERE rolname='replicator'"

# Check pg_hba.conf allows replication connections
docker compose exec postgres cat /var/lib/postgresql/data/pg_hba.conf | grep replication
```

**Replica connected but lag growing:**
```bash
# Check if replica is replaying WAL
docker compose exec postgres-replica psql -U asibot -c "SELECT pg_last_wal_replay_lsn(), pg_last_wal_receive_lsn()"

# Check for long-running queries on the replica blocking replay
docker compose exec postgres-replica psql -U asibot -c \
  "SELECT pid, now() - query_start AS duration, query FROM pg_stat_activity WHERE state != 'idle' ORDER BY duration DESC LIMIT 5"
```

**Asibot not using read replica:**
- Verify `ASIBOT_DATABASE_READ_URL` is set and non-empty
- Check Asibot logs for "PostgreSQL read replica pool initialized"
- If you see "Failed to connect to read replica, falling back to primary" -- the replica URL is unreachable

---

## 7. Key Rotation

The master encryption key protects all stored credentials. Rotation steps:

### 7.1 Pre-rotation backup

```bash
# Back up current master key
cp ~/.asibot/master.key ~/.asibot/master.key.bak.$(date +%Y%m%d)

# Back up database
./deploy/backup.sh
```

### 7.2 Rotate the key

```bash
# 1. Generate new master key
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" > /tmp/new_master.key

# 2. Stop the Asibot service (prevent writes during rotation)
docker compose stop asibot

# 3. Re-encrypt all credentials with the new key
#    This requires a migration script that:
#    a) Reads all rows from `credentials` and `microsoft_tokens`
#    b) Decrypts with the old key
#    c) Re-encrypts with the new key
#    d) Writes back
python3 -c "
import asyncio
from asibot.config import settings
from asibot.token_store import _get_fernet
# Load old fernet
old_fernet = _get_fernet()
# Swap key file
import shutil
shutil.copy('/tmp/new_master.key', str(settings.data_dir / 'master.key'))
# Load new fernet (will pick up new key)
from importlib import reload
import asibot.token_store as ts
reload(ts)
new_fernet = ts._get_fernet()
print('Re-encryption requires a custom migration script. See deploy/backup-restore.md.')
"

# 4. Replace the master key
cp /tmp/new_master.key ~/.asibot/master.key
chmod 600 ~/.asibot/master.key
rm /tmp/new_master.key

# 5. Restart Asibot
docker compose start asibot

# 6. Verify: test an authenticated tool call
curl -sk https://localhost/health
```

### 7.3 Post-rotation

- Verify all connector tools work for at least one test user.
- Delete the backup key after confirming (keep for 24h as rollback).
- Update any external backups that reference the old key.

---

## 8. Incident Response

### Severity levels

| Level | Definition | Response time |
|-------|-----------|---------------|
| SEV1 | Total service outage, all users affected | 15 min |
| SEV2 | Partial outage, single service or degraded performance | 30 min |
| SEV3 | Minor issue, workaround available | 4 hours |

### Escalation path

1. **On-call engineer** -- initial triage, check runbook, apply mitigations
2. **Backend lead** -- if Asibot application code issue
3. **Infra/DBA** -- if PostgreSQL, PgBouncer, or Nginx issue
4. **Security** -- if auth failure spike, credential exposure, or breach suspected

### Incident communication template

```
INCIDENT: [SEV level] [Brief description]
TIME: [When detected, UTC]
IMPACT: [Number of users affected, which services]
STATUS: [Investigating | Identified | Mitigating | Resolved]
CURRENT ACTION: [What is being done right now]
NEXT UPDATE: [Time of next update]
```

### Quick triage checklist

```bash
# 1. Is the service running?
docker compose ps

# 2. Health check
curl -sk https://localhost/health | jq .

# 3. Recent errors
docker compose logs --since=10m asibot | grep -c ERROR

# 4. Database reachable?
docker compose exec postgres pg_isready -U asibot

# 5. Metrics available?
curl -s http://127.0.0.1:9090/metrics | head -5

# 6. Disk space
df -h

# 7. Memory/CPU
docker stats --no-stream
```

---

## 9. Maintenance Windows

### Pre-maintenance

1. Announce maintenance window (min 1 hour notice for SEV3, 24h for planned).
2. Verify backup is current:
   ```bash
   ls -la /backups/asibot/ | tail -3
   ./deploy/backup.sh
   ```

### Drain connections

```bash
# 1. Stop accepting new connections at Nginx
#    Replace nginx.conf with a maintenance page or return 503
docker compose exec nginx nginx -s reload

# 2. Wait for in-flight requests to complete (session TTL is 1 hour max)
#    For a quick drain, wait 30-60 seconds for active tool calls to finish
sleep 60

# 3. Stop Asibot gracefully (runs _async_shutdown: cancels tasks, closes pools)
docker compose stop asibot
```

### Deploy new version

```bash
# 1. Pull / build new image
docker compose build asibot

# 2. Run database migrations (if any)
#    Asibot runs schema migration on startup (CREATE IF NOT EXISTS), so
#    typically just restarting is sufficient.

# 3. Start with new image
docker compose up -d asibot

# 4. Wait for health check to pass
for i in $(seq 1 30); do
  if curl -sf http://localhost:8080/health > /dev/null 2>&1; then
    echo "Healthy after ${i}s"
    break
  fi
  sleep 1
done

# 5. Re-enable Nginx traffic
docker compose exec nginx nginx -s reload
```

### Post-deploy verification

```bash
# 1. Health check returns OK
curl -sk https://localhost/health | jq .status

# 2. Metrics are being emitted
curl -s http://127.0.0.1:9090/metrics | grep asibot_requests_total

# 3. Background tasks are running
docker compose logs --since=2m asibot | grep "Background\|cleanup\|tracking"

# 4. Test an authenticated tool call (use a test API key)
# This depends on your test setup

# 5. Check error rate is not elevated
docker compose logs --since=5m asibot | grep -c ERROR
```

### Rollback

```bash
# If deployment fails, roll back to previous image
docker compose stop asibot
docker compose up -d asibot  # uses cached previous image if build not re-run

# Or restore from backup
./deploy/backup.sh  # ensure current state is saved first
# Then restore per deploy/backup-restore.md
```
