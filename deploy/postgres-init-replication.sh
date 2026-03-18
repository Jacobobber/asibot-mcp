#!/bin/bash
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
  DO \$\$
  BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'replicator') THEN
      CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD '${POSTGRES_PASSWORD}';
    END IF;
  END
  \$\$;
EOSQL
echo "host replication replicator 0.0.0.0/0 md5" >> "$PGDATA/pg_hba.conf"
pg_ctl reload -D "$PGDATA"
