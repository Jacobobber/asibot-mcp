# Asibot Backup & Disaster Recovery Runbook

## Overview

Asibot stores data in two locations that **must** be backed up together:

| Component | Location | Contents |
|-----------|----------|----------|
| PostgreSQL | `pg-data` Docker volume | Users, credentials (encrypted blobs), sessions, audit log |
| Master encryption key | `~/.asibot/master.key` | Fernet key that encrypts all credential blobs |

**Without `master.key`, all encrypted credentials in the database are permanently unrecoverable.** There is no backdoor. Back up the key separately from the database dump to limit blast radius if one backup store is compromised.

---

## 1. PostgreSQL Backup

### Manual pg_dump

```bash
# From Docker host — dumps the asibot database from the postgres container
docker compose exec postgres pg_dump -U asibot -Fc asibot > asibot_$(date +%Y%m%d_%H%M%S).dump
```

- `-Fc` = custom format (compressed, supports selective restore)
- Verify dump is non-empty: `ls -lh asibot_*.dump`

### Cron schedule (daily at 02:00)

```cron
0 2 * * * /home/jacob/asibot/deploy/backup.sh >> /var/log/asibot-backup.log 2>&1
```

### Retention

Keep 30 days of daily backups. The `backup.sh` script handles rotation automatically via `find -mtime +30 -delete`.

---

## 2. Encryption Key Backup

The master key lives at `~/.asibot/master.key` (or `$ASIBOT_DATA_DIR/master.key` in production — see `deploy/asibot.service`).

### Backup procedure

```bash
cp ~/.asibot/master.key /secure/offsite/master.key.$(date +%Y%m%d)
chmod 600 /secure/offsite/master.key.*
```

### Requirements

- Store the key backup **physically separate** from DB backups (different storage account, different machine, or encrypted USB in a safe).
- If using cloud storage for DB dumps, **do not** put the key in the same bucket/container.
- The key file is 44 bytes (base64-encoded Fernet key). It does not change unless you run key rotation (`crypto.rotate_key()`).
- After key rotation, immediately back up the new `master.key`. The old key is saved as `master.key.bak` — back that up too until you've verified the rotation succeeded.

### What happens if you lose the key

All credential blobs in the `credentials` table become undecryptable. All user OAuth tokens (Microsoft, Google, etc.) are lost. Users must re-authenticate every connector. There is **no recovery path**.

---

## 3. Restore Procedure

### Prerequisites

- Access to a recent `.dump` file (from pg_dump)
- The `master.key` file that was active when that dump was taken

### Steps

```bash
# 1. Stop asibot (prevents writes during restore)
docker compose stop asibot

# 2. Drop and recreate the database
docker compose exec postgres dropdb -U asibot asibot
docker compose exec postgres createdb -U asibot asibot

# 3. Restore the dump
docker compose exec -T postgres pg_restore -U asibot -d asibot < /backups/asibot_20260318_020000.dump

# 4. Restore the master key
cp /secure/offsite/master.key ~/.asibot/master.key
chmod 600 ~/.asibot/master.key

# 5. Start asibot
docker compose start asibot

# 6. Verify
docker compose exec asibot python -c "
import asyncio
from asibot.db import init_db, db_stats
async def check():
    await init_db()
    stats = await db_stats()
    print(stats)
asyncio.run(check())
"
```

### Verification checklist

- [ ] `db_stats()` returns expected user count
- [ ] Health endpoint returns 200: `curl -k https://localhost/health`
- [ ] A test user can authenticate and list connected services
- [ ] Audit log contains pre-restore entries

---

## 4. Disaster Recovery Targets

For a 1000-user deployment:

| Metric | Target | Notes |
|--------|--------|-------|
| **RPO** (Recovery Point Objective) | **24 hours** | Daily backups at 02:00. Acceptable: users re-do at most 1 day of credential changes. Audit log entries from the gap are lost. |
| **RTO** (Recovery Time Objective) | **1 hour** | Restore from dump (~5 min for typical DB size) + key restore + service restart + verification. Budget 30 min for troubleshooting. |
| **MTTR** (Mean Time to Repair) | **30 minutes** | Assuming backups are verified and the operator has this runbook. |

### To reduce RPO below 24h

- Enable PostgreSQL WAL archiving for point-in-time recovery (PITR)
- Use `pg_basebackup` + continuous WAL shipping to a secondary host
- Or switch to a managed PostgreSQL service with automated backups (e.g., RDS, Cloud SQL)

### To reduce RTO below 1h

- Maintain a warm standby with streaming replication
- Pre-stage the master key on the standby host
- Use container orchestration (k8s) with automated failover

---

## 5. Docker Volume Backup

The `pg-data` volume holds the raw PostgreSQL data directory. This is an alternative to pg_dump for full-volume snapshots.

### Snapshot (cold — requires stopping postgres)

```bash
docker compose stop postgres
docker run --rm -v asibot_pg-data:/data -v /backups:/backup alpine \
  tar czf /backup/pg-data_$(date +%Y%m%d).tar.gz -C /data .
docker compose start postgres
```

### Snapshot (hot — using pg_basebackup, no downtime)

```bash
docker compose exec postgres pg_basebackup -U asibot -D /tmp/backup -Ft -z
docker cp $(docker compose ps -q postgres):/tmp/backup/base.tar.gz /backups/pg-base_$(date +%Y%m%d).tar.gz
```

**Recommendation:** Prefer `pg_dump` (logical backup) for routine daily backups. Use volume snapshots only for migration or pre-upgrade safety nets. Logical backups are portable across PostgreSQL versions; volume snapshots are not.

---

## 6. Automated Backup Script

See [`deploy/backup.sh`](backup.sh). Install with:

```bash
chmod +x /home/jacob/asibot/deploy/backup.sh

# Add to crontab
crontab -e
# 0 2 * * * /home/jacob/asibot/deploy/backup.sh >> /var/log/asibot-backup.log 2>&1
```

The script:
1. Runs `pg_dump` via Docker with timestamped filename
2. Copies `master.key` alongside the dump
3. Compresses both into a single `.tar.gz` archive
4. Removes the uncompressed intermediaries
5. Deletes archives older than 30 days
6. Exits non-zero on any failure (`set -euo pipefail`)
