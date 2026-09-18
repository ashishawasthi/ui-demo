#!/usr/bin/env bash
set -euo pipefail

export PG_HOST="${PG_HOST:-127.0.0.1}"
export PG_PORT="${PG_PORT:-5433}"
export PG_USER="${PG_USER:-postgres}"
export PG_DB="${PG_DB:-corporate_mandate_db}"
export PORT="${PORT:-8080}"

echo "[docker-entrypoint] Starting DBS IDEAL Corporate Banking Mandate Copilot container..."

# 1. Initialize and start local PostgreSQL cluster inside container if CloudSQL is not configured
if [ -z "${CLOUDSQL_INSTANCE_CONNECTION_NAME:-}" ]; then
  PG_INITDB="$(find /usr/lib/postgresql -name initdb | head -n 1)"
  if [ -z "${PG_INITDB}" ]; then
    echo "[docker-entrypoint] ERROR: PostgreSQL initdb binary not found under /usr/lib/postgresql" >&2
    exit 1
  fi
  PG_BIN_DIR="$(dirname "${PG_INITDB}")"
  export PATH="${PG_BIN_DIR}:${PATH}"

  mkdir -p /tmp/pgdata /tmp/pgrun
  chown -R postgres:postgres /tmp/pgdata /tmp/pgrun
  chmod 700 /tmp/pgdata

  if [ ! -f /tmp/pgdata/PG_VERSION ]; then
    echo "[docker-entrypoint] Initializing local PostgreSQL cluster at /tmp/pgdata..."
    su - postgres -c "${PG_BIN_DIR}/initdb -D /tmp/pgdata -U postgres --encoding=UTF8 --auth=trust --no-instructions"
  fi

  if ! pg_isready -h "${PG_HOST}" -p "${PG_PORT}" -U "${PG_USER}" >/dev/null 2>&1; then
    echo "[docker-entrypoint] Starting PostgreSQL server on ${PG_HOST}:${PG_PORT}..."
    su - postgres -c "${PG_BIN_DIR}/pg_ctl -D /tmp/pgdata -l /tmp/pg.log -o '-p ${PG_PORT} -k /tmp/pgrun -c listen_addresses=${PG_HOST} -c dynamic_shared_memory_type=posix' start"
  fi

  echo "[docker-entrypoint] Ensuring database ${PG_DB} exists..."
  psql -h "${PG_HOST}" -p "${PG_PORT}" -U "${PG_USER}" -d postgres -tc \
    "SELECT 1 FROM pg_database WHERE datname = '${PG_DB}'" | grep -q 1 || \
    psql -h "${PG_HOST}" -p "${PG_PORT}" -U "${PG_USER}" -d postgres -c "CREATE DATABASE ${PG_DB};"
fi

# 2. Initialize 8-table schema and seed all 5 corporate customer profiles (CUST-001..CUST-005)
cd /app
echo "[docker-entrypoint] Initializing schema/schema.sql and seeding 5 corporate customer profiles..."
python3 synthetic_data/seed.py

# 3. Launch FastAPI + WebSocket + Static UI server via Uvicorn on $PORT
echo "[docker-entrypoint] Launching Uvicorn server on 0.0.0.0:${PORT}..."
exec uvicorn backend.main:app --host 0.0.0.0 --port "${PORT}"
