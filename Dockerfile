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
COPY .env /app/.env

RUN chmod +x /app/scripts/*.sh

EXPOSE 8080

ENTRYPOINT ["/app/scripts/docker_entrypoint.sh"]
