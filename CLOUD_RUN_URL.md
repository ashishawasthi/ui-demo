# Google Cloud Run Live Deployment — `gemini-live-mandate-app`

## Live Cloud Run Service URLs
- **Primary Cloud Run URL**: https://gemini-live-mandate-app-327571158527.us-central1.run.app
- **Alternate Cloud Run URL (`a.run.app`)**: https://gemini-live-mandate-app-5p6ecehe6q-uc.a.run.app
- **GCP Project**: `elevate-data-508005` (`327571158527`)
- **Region**: `us-central1`
- **Service Name**: `gemini-live-mandate-app`
- **Active Revision**: `gemini-live-mandate-app-00001-z4f` (100% traffic)
- **Authentication**: Publicly accessible (`--allow-unauthenticated`, `allUsers` granted `roles/run.invoker`)
- **Resources**: 2 vCPU, 2 GiB Memory, 300s Request Timeout

---

## Container & Runtime Architecture
- **`Dockerfile`**: Built on `python:3.12-slim` with `postgresql`, `postgresql-client`, `libpq-dev`, and all Python dependencies (`fastapi`, `uvicorn[standard]`, `websockets`, `google-genai`, `google-auth`, `psycopg2-binary`, `cloud-sql-python-connector`, `pg8000`, `python-dotenv`, `pydantic`, `httpx`).
- **`scripts/docker_entrypoint.sh`**:
  1. Initializes and starts a container-local PostgreSQL server (`PostgreSQL 17.11 (Debian 17.11-0+deb13u1)`) at `127.0.0.1:5433` (`dynamic_shared_memory_type=posix`) and creates `corporate_mandate_db` when `CLOUDSQL_INSTANCE_CONNECTION_NAME` is not set.
  2. Executes `python3 synthetic_data/seed.py` to initialize `schema/schema.sql` (8 relational tables) and seed all 5 corporate customer profiles (`CUST-001`..`CUST-005`, 17 bank accounts, 23 signatories, 13 signing rules, 5 board resolutions, 5 mandate change applications, and 16 audit logs).
  3. Starts `uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8080}` serving the frontend (`frontend/index.html`, `/styles.css`, `/app.js`), REST API (`/api/*`), and Gemini Live WebSocket (`/ws/live`).

---

## Live Endpoint Verification Results

### 1. Frontend Web Application (`GET /`, `/styles.css`, `/app.js`)
```text
GET https://gemini-live-mandate-app-327571158527.us-central1.run.app/
  <title>DBS IDEAL Corporate Banking — Change of Account Mandate | Gemini Live 3.8</title>
  HTTP_STATUS: 200 | SIZE_BYTES: 35985

GET https://gemini-live-mandate-app-327571158527.us-central1.run.app/styles.css
  HTTP_STATUS: 200 | SIZE_BYTES: 28275

GET https://gemini-live-mandate-app-327571158527.us-central1.run.app/app.js
  HTTP_STATUS: 200 | SIZE_BYTES: 75094
```

### 2. Health & PostgreSQL + Gemini Live Diagnostics (`GET /api/health`)
```json
{
  "status": "ok",
  "database": {
    "engine": "postgresql",
    "mode": "local_pg18",
    "tier": "local_postgresql_18",
    "connected": true,
    "project": "elevate-data-508005",
    "database": "corporate_mandate_db",
    "host": "127.0.0.1:5433",
    "version": "PostgreSQL 17.11 (Debian 17.11-0+deb13u1) on x86_64-pc-linux-gnu, compiled by gcc (Debian 14.2.0-19) 14.2.0, 64-bit",
    "customer_count": 5,
    "active_customer_id": "CUST-001",
    "table_counts": {
      "corporate_customers": 5,
      "bank_accounts": 17,
      "signatories": 23,
      "signing_rules": 13,
      "board_resolutions": 5,
      "mandate_change_applications": 5,
      "mandate_audit_logs": 16,
      "active_workspace_state": 1
    }
  },
  "gemini_live": {
    "model": "models/gemini-3.8-live-extended-thinking",
    "resolved_thinking_model": "gemini-3.8-flash",
    "resolved_live_audio_model": "gemini-live-2.5-flash-native-audio",
    "vertexai": true,
    "project": "elevate-data-508005",
    "locations": {
      "thinking_and_tools": "global",
      "live_native_audio": "us-central1"
    },
    "api_key_configured": true,
    "api_key_prefix": "AIzaSyCE7i...",
    "tools_registered": [
      "list_customer_profiles",
      "get_customer_mandate_details",
      "SwitchActiveCustomerProfile",
      "switch_active_customer_profile",
      "add_or_update_signatory",
      "revoke_signatory",
      "configure_signing_rules",
      "simulate_transaction_authorization",
      "audit_board_resolution",
      "submit_mandate_change_request",
      "update_target_accounts",
      "execute_cosigner_signature"
    ],
    "tools_count": 12
  }
}
```

### 3. Corporate Customer Profiles (`GET /api/customers`)
```text
GET https://gemini-live-mandate-app-327571158527.us-central1.run.app/api/customers
  HTTP_STATUS: 200
  count: 5
  ids: ['CUST-001', 'CUST-002', 'CUST-003', 'CUST-004', 'CUST-005']
```

### 4. Gemini Live (`models/gemini-3.8-live-extended-thinking`) Tool Execution (`POST /api/chat`)
```text
POST https://gemini-live-mandate-app-327571158527.us-central1.run.app/api/chat
  Payload: {"message": "List our corporate customer profiles.", "customer_id": "CUST-001", "current_stage": 1}
  Result:
    status: success
    model: models/gemini-3.8-live-extended-thinking
    tool_calls: ['list_customer_profiles']
    thinking_traces_count: 2
    reply_preview: "Here are the corporate banking customer profiles available in DBS IDEAL: | Customer ID | Entity Name | UEN | Entity Type | KYC Status | Active Signatories | Total Balance (SGD) | Mandate Status | ..."
```
