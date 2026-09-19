#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PG_BASE="${PROJECT_ROOT}/.pgdata"
PG_ROOT="${PG_BASE}/root"
PG_DATA="${PG_BASE}/data"
PG_RUN="${PG_BASE}/run"
PG_LOG="${PG_BASE}/postgres.log"
PG_PORT="${PG_PORT:-5433}"
PG_DB="${PG_DB:-corporate_mandate_db}"

mkdir -p "${PG_BASE}/debs" "${PG_ROOT}" "${PG_RUN}"

# 1. Download & unpack PostgreSQL 18.6 binaries into user-space if not present
if [ ! -x "${PG_ROOT}/usr/lib/postgresql/18/bin/postgres" ]; then
  echo "[pg-bootstrap] Downloading postgresql-18, postgresql-client-18, libpq5 from Rapture..."
  (
    cd "${PG_BASE}/debs"
    apt-get download postgresql-18 postgresql-client-18 libpq5 >/dev/null
    # Without nullglob, an empty match leaves the literal string "*.deb" and the loop below
    # runs `dpkg -x '*.deb'`, which fails with a confusing "cannot access archive" error
    # instead of reporting that the download produced nothing.
    shopt -s nullglob
    debs=( *.deb )
    if [ ${#debs[@]} -eq 0 ]; then
      echo "[pg-bootstrap] ERROR: apt-get download produced no .deb files in ${PG_BASE}/debs." >&2
      exit 1
    fi
    for deb in "${debs[@]}"; do
      dpkg -x "$deb" "${PG_ROOT}"
    done
  )
fi

export LD_LIBRARY_PATH="${PG_ROOT}/usr/lib/x86_64-linux-gnu:${LD_LIBRARY_PATH:-}"
export PATH="${PG_ROOT}/usr/lib/postgresql/18/bin:${PG_ROOT}/usr/bin:${PATH}"

# 2. Initialize cluster if data directory is not yet initialized
if [ ! -f "${PG_DATA}/PG_VERSION" ]; then
  echo "[pg-bootstrap] Initializing PostgreSQL 18.6 cluster at ${PG_DATA}..."
  initdb -L "${PG_ROOT}/usr/share/postgresql/18" \
         -D "${PG_DATA}" \
         -U postgres \
         --encoding=UTF8 \
         --locale=en_US.UTF-8 \
         --auth=trust \
         --no-instructions
fi

# 3. Start PostgreSQL 18.6 server if not already running on PG_PORT
if ! pg_isready -h 127.0.0.1 -p "${PG_PORT}" -U postgres >/dev/null 2>&1; then
  echo "[pg-bootstrap] Starting PostgreSQL 18.6 on 127.0.0.1:${PG_PORT}..."
  pg_ctl -D "${PG_DATA}" \
         -l "${PG_LOG}" \
         -o "-p ${PG_PORT} -k ${PG_RUN} -c listen_addresses=127.0.0.1 -c dynamic_shared_memory_type=posix" \
         start
fi

# 4. Ensure database exists
psql -h 127.0.0.1 -p "${PG_PORT}" -U postgres -d postgres -tc \
  "SELECT 1 FROM pg_database WHERE datname = '${PG_DB}'" | grep -q 1 || \
  psql -h 127.0.0.1 -p "${PG_PORT}" -U postgres -d postgres -c "CREATE DATABASE ${PG_DB};"

echo "[pg-bootstrap] PostgreSQL 18.6 ready at postgresql://postgres@127.0.0.1:${PG_PORT}/${PG_DB}"
