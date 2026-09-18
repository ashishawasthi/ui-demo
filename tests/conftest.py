import inspect
import os
import subprocess
import sys
from pathlib import Path
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def load_env_file():
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and v:
                os.environ[k] = v


load_env_file()


def open_db_connection():
    """Return a raw psycopg2 connection to the active PostgreSQL database."""
    from backend import db

    if hasattr(db, "get_raw_connection"):
        return db.get_raw_connection()
    conn_or_cm = db.get_connection()
    if hasattr(conn_or_cm, "__enter__") and not hasattr(conn_or_cm, "cursor"):
        return conn_or_cm.__enter__()
    return conn_or_cm


def ensure_database_ready_and_seeded():
    """Ensure PostgreSQL 18.6 is running and seeded with baseline corporate profiles."""
    ensure_script = PROJECT_ROOT / "scripts" / "ensure_postgres.sh"
    if ensure_script.exists():
        subprocess.run(["bash", str(ensure_script)], check=True)

    from backend import db

    if hasattr(db, "init_db"):
        db.init_db(force_reseed=True)
        return

    from synthetic_data import seed as seed_mod

    if hasattr(seed_mod, "seed_all_data"):
        seed_mod.seed_all_data(reset_existing=True)
    elif hasattr(seed_mod, "seed_all"):
        seed_mod.seed_all(reset=True)



@pytest.fixture(scope="session", autouse=True)
def bootstrap_session_environment():
    """Session-wide fixture that loads .env, boots PostgreSQL 18.6, and seeds before & after all tests."""
    load_env_file()
    ensure_database_ready_and_seeded()
    yield
    # Restore clean baseline state after the entire test suite finishes
    ensure_database_ready_and_seeded()


@pytest.fixture
def clean_db():
    """Fixture to reset the PostgreSQL database to its clean seeded baseline before & after a test."""
    ensure_database_ready_and_seeded()
    yield
    ensure_database_ready_and_seeded()


@pytest.fixture(scope="module")
def client():
    """FastAPI TestClient connected to the real backend app and PostgreSQL database."""
    from fastapi.testclient import TestClient
    from backend.main import app

    with TestClient(app) as tc:
        yield tc


def invoke_tool_adaptive(func, *args, **kwargs):
    """Call a backend tool function, mapping common parameter aliases to its actual signature."""
    sig = inspect.signature(func)
    params = sig.parameters
    accepts_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
    if accepts_var_kw:
        return func(*args, **kwargs)

    alias_groups = [
        ("customer_id", "customer_identifier", "profile_id"),
        ("signatory_id_or_name", "signatory_identifier", "signatory_id", "full_name"),
        ("reason", "revocation_reason"),
        ("nric_masked", "id_number", "id_number_masked"),
        ("phone_masked", "mobile_masked", "phone_number"),
        ("ocr_verified", "ocr_specimen_verified", "ocr_extracted"),
        ("resolution_type", "format_type"),
        ("clause_text", "custom_resolution_text", "custom_text", "extracted_text_summary"),
        ("notes", "submission_notes", "summary_description"),
        ("account_ids", "account_number", "account_id"),
        ("included", "included_in_mandate", "is_included_in_mandate_change"),
    ]

    mapped_kwargs = {}
    used_input_keys = set()

    # 1. Direct matches
    for k, v in kwargs.items():
        if k in params:
            mapped_kwargs[k] = v
            used_input_keys.add(k)

    # 2. Alias matches for remaining kwargs
    for k, v in kwargs.items():
        if k in used_input_keys:
            continue
        for group in alias_groups:
            if k in group:
                for candidate in group:
                    if candidate in params and candidate not in mapped_kwargs:
                        mapped_kwargs[candidate] = v
                        used_input_keys.add(k)
                        break

    return func(*args, **mapped_kwargs)


def receive_next_ws_frame(ws, expected_types, max_frames: int = 8):
    """Read frames from a TestClient WebSocket until one matches expected_types."""
    if isinstance(expected_types, str):
        expected_types = (expected_types,)
    last_frame = None
    for _ in range(max_frames):
        last_frame = ws.receive_json()
        if last_frame.get("type") in expected_types:
            return last_frame
    return last_frame

