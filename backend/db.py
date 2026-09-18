"""Database Connection & Failover Manager for Corporate Banking Mandate Application.

Connects to CloudSQL PostgreSQL in GCP project `elevate-data-508005` using Application
Default Credentials (`cloud-sql-python-connector` + `pg8000`) when an instance is
configured/reachable, and automatically provisions/connects to a real local user-space
PostgreSQL 18.6 cluster (`127.0.0.1:5433`, database `corporate_mandate_db`, via `psycopg2`)
when CloudSQL instances are absent or network/IAM blocked.
"""

from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
from datetime import date, datetime
import json
import logging
import os
from pathlib import Path
import socket
import subprocess
from typing import Any, Generator

from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

logger = logging.getLogger("mandate_app.db")

GCP_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "elevate-data-508005")
PG_HOST = os.environ.get("PG_HOST", "127.0.0.1")
PG_PORT = int(os.environ.get("PG_PORT", "5433"))
PG_USER = os.environ.get("PG_USER", "postgres")
PG_DB = os.environ.get("PG_DB", "corporate_mandate_db")
LOCAL_DATABASE_URL = os.environ.get(
    "DATABASE_URL", f"postgresql://{PG_USER}@{PG_HOST}:{PG_PORT}/{PG_DB}"
)

_ACTIVE_MODE: str = "uninitialized"
_ACTIVE_TIER: str = "uninitialized"
_CLOUD_SQL_CONNECTOR: Any = None
_DB_INITIALIZED: bool = False


def serialize_row(obj: Any) -> Any:
    """Recursively serialize PostgreSQL row values (Decimal, datetime, date) to JSON-safe primitives."""
    if isinstance(obj, dict):
        return {k: serialize_row(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [serialize_row(v) for v in obj]
    if isinstance(obj, tuple):
        return [serialize_row(v) for v in obj]
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return obj


def _is_port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def ensure_local_postgres_running() -> None:
    """Ensure the user-space PostgreSQL 18.6 cluster on 127.0.0.1:5433 is running."""
    if _is_port_open(PG_HOST, PG_PORT):
        return
    script_path = PROJECT_ROOT / "scripts" / "ensure_postgres.sh"
    if not script_path.exists():
        raise RuntimeError(f"PostgreSQL bootstrap script not found at {script_path}")
    logger.info("Starting local PostgreSQL 18.6 cluster via %s...", script_path)
    subprocess.run(["bash", str(script_path)], check=True, cwd=str(PROJECT_ROOT))


def _try_cloudsql_connection() -> Any | None:
    """Attempt Tier 1 / Tier 2 CloudSQL PostgreSQL connection in elevate-data-508005 via ADC."""
    global _ACTIVE_MODE, _ACTIVE_TIER, _CLOUD_SQL_CONNECTOR
    instance_conn_name = os.environ.get("CLOUDSQL_INSTANCE_CONNECTION_NAME", "").strip()
    if not instance_conn_name and os.environ.get("CLOUDSQL_AUTO_DISCOVER") == "1":
        # Check if a CloudSQL instance connection name was dynamically discovered
        instance_conn_name = os.environ.get("DISCOVERED_CLOUDSQL_INSTANCE", "").strip()

    if not instance_conn_name:
        return None

    try:
        from google.cloud.sql.connector import Connector, IPTypes

        if _CLOUD_SQL_CONNECTOR is None:
            _CLOUD_SQL_CONNECTOR = Connector()

        db_user = os.environ.get("CLOUDSQL_USER", "postgres")
        db_pass = os.environ.get("CLOUDSQL_PASS", "")
        enable_iam = os.environ.get("CLOUDSQL_IAM_AUTH", "true").lower() == "true"

        conn = _CLOUD_SQL_CONNECTOR.connect(
            instance_conn_name,
            "pg8000",
            user=db_user,
            password=db_pass,
            db=PG_DB,
            enable_iam_auth=enable_iam,
            ip_type=IPTypes.PUBLIC,
            timeout=3,
        )
        _ACTIVE_MODE = "cloudsql"
        _ACTIVE_TIER = "cloudsql_postgresql"
        return conn
    except Exception as exc:
        logger.warning(
            "CloudSQL connection (%s) unavailable (%s); failing over to local PostgreSQL 18.6.",
            instance_conn_name,
            exc,
        )
        return None


def get_raw_connection() -> psycopg2.extensions.connection:
    """Return an active PostgreSQL connection (CloudSQL Tier 1/2 or Local PostgreSQL 18.6 Tier 3)."""
    global _ACTIVE_MODE, _ACTIVE_TIER

    cloud_conn = _try_cloudsql_connection()
    if cloud_conn is not None:
        return cloud_conn

    ensure_local_postgres_running()
    conn = psycopg2.connect(LOCAL_DATABASE_URL, cursor_factory=RealDictCursor)
    _ACTIVE_MODE = "local_pg18"
    _ACTIVE_TIER = "local_postgresql_18"
    return conn


@contextmanager
def get_connection(autocommit: bool = False) -> Generator[Any, None, None]:
    """Context manager yielding an active PostgreSQL connection with transaction commit/rollback."""
    conn = get_raw_connection()
    if autocommit:
        conn.autocommit = True
    try:
        yield conn
        if not autocommit:
            conn.commit()
    except Exception:
        if not autocommit:
            conn.rollback()
        raise
    finally:
        conn.close()


def execute_sql_file(sql_path: Path) -> None:
    """Execute a DDL/SQL file against the active PostgreSQL database."""
    sql_text = sql_path.read_text(encoding="utf-8")
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql_text)


def init_db(force_reseed: bool = False) -> dict[str, Any]:
    """Ensure the 8-table schema is created and all 5 corporate customer profiles are seeded."""
    global _DB_INITIALIZED
    schema_path = PROJECT_ROOT / "schema" / "schema.sql"
    if not schema_path.exists():
        raise RuntimeError(f"Schema file missing at {schema_path}")

    execute_sql_file(schema_path)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS cnt FROM corporate_customers;")
            row = cur.fetchone()
            customer_count = int(row["cnt"]) if row else 0

    if force_reseed or customer_count < 5:
        from synthetic_data.seed import seed_all_data

        seed_all_data(reset_existing=force_reseed or (customer_count < 5))

    _DB_INITIALIZED = True
    return get_db_health()


def ensure_db_initialized() -> None:
    """Idempotently initialize schema and seed data on first tool or API call."""
    global _DB_INITIALIZED
    if not _DB_INITIALIZED:
        init_db(force_reseed=False)


def get_db_health() -> dict[str, Any]:
    """Query live PostgreSQL version, connection tier, and row counts across all 8 tables."""
    tables = [
        "corporate_customers",
        "bank_accounts",
        "signatories",
        "signing_rules",
        "board_resolutions",
        "mandate_change_applications",
        "mandate_audit_logs",
        "active_workspace_state",
    ]
    table_counts: dict[str, int] = {}
    pg_version = "unknown"
    active_customer_id = "CUST-001"

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT version() AS ver;")
                ver_row = cur.fetchone()
                if ver_row:
                    pg_version = str(ver_row["ver"])
                for tbl in tables:
                    cur.execute(f"SELECT COUNT(*) AS cnt FROM {tbl};")
                    r = cur.fetchone()
                    table_counts[tbl] = int(r["cnt"]) if r else 0
                cur.execute(
                    "SELECT active_customer_id FROM active_workspace_state WHERE workspace_id = 'DEFAULT_WORKSPACE';"
                )
                ws_row = cur.fetchone()
                if ws_row and ws_row.get("active_customer_id"):
                    active_customer_id = str(ws_row["active_customer_id"])
        connected = True
    except Exception as exc:
        logger.error("Database health check failed: %s", exc)
        connected = False

    return {
        "engine": "postgresql",
        "mode": _ACTIVE_MODE if _ACTIVE_MODE != "uninitialized" else "local_pg18",
        "tier": _ACTIVE_TIER if _ACTIVE_TIER != "uninitialized" else "local_postgresql_18",
        "connected": connected,
        "project": GCP_PROJECT,
        "database": PG_DB,
        "host": f"{PG_HOST}:{PG_PORT}",
        "version": pg_version,
        "customer_count": table_counts.get("corporate_customers", 0),
        "active_customer_id": active_customer_id,
        "table_counts": table_counts,
    }
