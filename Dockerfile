FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    PG_HOST=127.0.0.1 \
    PG_PORT=5433 \
    PG_USER=postgres \
    PG_DB=corporate_mandate_db \
    GOOGLE_CLOUD_PROJECT=elevate-data-508005 \
    GEMINI_LIVE_MODEL=models/gemini-3.8-live-extended-thinking

WORKDIR /app

# Install PostgreSQL server, client utilities, and system libraries
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      postgresql \
      postgresql-client \
      libpq-dev \
      curl \
      ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies (with fallback if internal mirror version pins exceed public PyPI)
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt || \
    pip install --no-cache-dir \
      fastapi \
      "uvicorn[standard]" \
      websockets \
      google-genai \
      google-auth \
      psycopg2-binary \
      cloud-sql-python-connector \
      pg8000 \
      python-dotenv \
      pydantic \
      httpx \
      pytest

# Copy application source code, schema, synthetic seed data, frontend UI, and scripts
COPY schema/ /app/schema/
COPY synthetic_data/ /app/synthetic_data/
COPY scripts/ /app/scripts/
COPY backend/ /app/backend/
COPY frontend/ /app/frontend/

RUN chmod +x /app/scripts/*.sh

EXPOSE 8080

# Without a healthcheck, a wedged app (e.g. a Postgres deadlock hanging every request) still
# looks "running" to Docker/orchestrators because the process is alive.
# Note: Cloud Run ignores HEALTHCHECK and uses its own startup/liveness probes, but this makes
# local `docker run` and any Compose/Swarm/K8s usage behave correctly.
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/api/health" || exit 1

ENTRYPOINT ["/app/scripts/docker_entrypoint.sh"]
