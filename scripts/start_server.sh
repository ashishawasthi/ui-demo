#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${PROJECT_ROOT}"

# 1. Load environment variables (.env including GEMINI_API_KEY / GOOGLE_API_KEY)
if [ -f "${PROJECT_ROOT}/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "${PROJECT_ROOT}/.env"
  set +a
fi

export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-elevate-data-508005}"
export PORT="${PORT:-8080}"

# 2. Ensure PostgreSQL 18.6 cluster / CloudSQL failover is running
if [ -f "${PROJECT_ROOT}/scripts/ensure_postgres.sh" ]; then
  bash "${PROJECT_ROOT}/scripts/ensure_postgres.sh"
fi

# 3. Ensure PostgreSQL schema and 5+ corporate customer profiles are seeded
if [ -f "${PROJECT_ROOT}/synthetic_data/seed.py" ]; then
  python3 "${PROJECT_ROOT}/synthetic_data/seed.py"
fi

# 4. Start FastAPI + Gemini Live WebSocket server
exec python3 -m uvicorn backend.main:app --host 0.0.0.0 --port "${PORT}"
