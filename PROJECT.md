# Project: Corporate Banking Change of Account Mandate (`gemini_live_mandate_app`)

## Architecture
- **Database Engine & Automatic Failover (`scripts/ensure_postgres.sh`, `backend/db.py`)**:
  - Connects to CloudSQL PostgreSQL in GCP project `elevate-data-508005` using Application Default Credentials (`cloud-sql-python-connector` + `pg8000`) when an instance is reachable, and automatically provisions/connects to a real local user-space **PostgreSQL 18.6** server (`127.0.0.1:5433`, database `corporate_mandate_db`, via `psycopg2`) when CloudSQL instances are absent or network/IAM blocked. Zero SQLite/in-memory or static mocks are permitted.
- **Relational Schema & Seed Data (`schema/schema.sql`, `synthetic_data/seed.py`)**:
  - 8 tables: `corporate_customers`, `bank_accounts`, `signatories`, `signing_rules`, `board_resolutions`, `mandate_change_applications`, `mandate_audit_logs`, and `active_workspace_state`.
  - Seeded with 5 distinct corporate customer profiles (`CUST-001` TechNova Solutions Pte Ltd, `CUST-002` Meridian Pacific Logistics Pte Ltd, `CUST-003` Apex Global Holdings (SG) Pte Ltd, `CUST-004` Veritas Legal & Advisory LLP, `CUST-005` Banyan Artisans & F&B Group Pte Ltd), 17 bank accounts across SGD/USD/EUR/CNH/JPY, 23 signatories across Groups A/B/C, 13 tiered signing rules, 5 board resolutions, 5 mandate change applications, and 15+ audit log entries.
- **Gemini Live (`models/gemini-3.8-live-extended-thinking`) & Tool Engine (`backend/gemini_live.py`, `backend/tools.py`, `backend/main.py`)**:
  - Configures `models/gemini-3.8-live-extended-thinking` via the `google-genai` SDK supporting both Vertex AI ADC in `elevate-data-508005` and `GEMINI_API_KEY`.
  - Uses dual-client Vertex AI routing in `elevate-data-508005`:
    - `client_global` (`location="global"`, `gemini-3.8-flash` with `ThinkingConfig(include_thoughts=True)`) for real Extended Thinking traces (`part.thought == True`) and structured tool execution.
    - `client_usc1` (`location="us-central1"`, `gemini-live-2.5-flash-native-audio` with `response_modalities=["AUDIO"]`, `output_audio_transcription`, `input_audio_transcription`) for real-time 24kHz PCM bidirectional voice streaming, barge-in handling, and tool execution.
  - Exposes 11 real database-backed tools (`list_customer_profiles`, `get_customer_mandate_details`, `SwitchActiveCustomerProfile`, `switch_active_customer_profile`, `add_or_update_signatory`, `revoke_signatory`, `configure_signing_rules`, `simulate_transaction_authorization`, `audit_board_resolution`, `submit_mandate_change_request`, `update_target_accounts`, `execute_cosigner_signature`). Every tool mutates/queries PostgreSQL and returns a `ui_sync` envelope broadcast over WebSocket (`/ws/live`) to synchronize the browser UI.
- **Elevated 5-Stage Web UI (`frontend/index.html`, `frontend/styles.css`, `frontend/app.js`)**:
  - Inspired by `https://sabrinalim87-commits.github.io/changeofmandate/` (DBS IDEAL brand palette `--dbs-red: #CC0000`, spark logo, dark executive header) and elevated into a split-screen workspace with a top Profile Switcher & Live Entity Header, a 5-stage interactive Change of Mandate main canvas, and an always-accessible Synchronized Voice & Chat Copilot Dock.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| F1 | CloudSQL + Local PostgreSQL 18.6 Failover | Automated PostgreSQL 18.6 cluster bootstrap (`127.0.0.1:5433`) and CloudSQL ADC failover manager (`elevate-data-508005`) | M1 | survey_2 |
| F2 | 8-Table Schema & 5+ Corporate Customer Profiles | PostgreSQL DDL (`schema/schema.sql`) and seed script (`synthetic_data/seed.py`) with 5 distinct corporate entities, 17 accounts, 23 signatories, 13 rules, 5 resolutions, and audit logs | M1 | survey_1, survey_2 |
| F3 | Core Mandate Query & Profile Switch Tools | `list_customer_profiles`, `get_customer_mandate_details`, `SwitchActiveCustomerProfile` / `switch_active_customer_profile`, `update_target_accounts` backed by PostgreSQL | M1 | survey_2, survey_3 |
| F4 | Signatory Matrix & Governance Tools | `add_or_update_signatory` (with OCR metadata) and `revoke_signatory` (enforcing sole Group A protection and minimum governance validation) | M1 | survey_1, survey_3 |
| F5 | Signing Rule Configuration & Transaction Simulator Tools | `configure_signing_rules` and `simulate_transaction_authorization` (multi-currency FX conversion to SGD base + Tier 1/2/3 evaluation) | M1 | survey_1, survey_3 |
| F6 | Board Resolution Audit & Mandate Submission Tools | `audit_board_resolution` (BRC-09 & custom clause check + live diff generation), `submit_mandate_change_request` (`COM-2026-...` + Maker-Checker DigiSign), and `execute_cosigner_signature` | M1 | survey_1, survey_3 |
| F7 | Gemini Live (`models/gemini-3.8-live-extended-thinking`) Voice & Chat Backend | FastAPI REST + `/ws/live` WebSocket server with `google-genai` SDK (`elevate-data-508005` ADC & `GEMINI_API_KEY`), bidirectional PCM audio, extended thinking traces, tool execution cards, and `ui_sync` broadcasting | M1 | survey_3 |
| F8 | Profile Switcher & Live Entity Header + Stage 1 UI | Top corporate entity selector across all 5+ profiles, UEN/KYC/IDEAL badges, aggregate balances, and Stage 1 Target Account selection | M2 | survey_1 |
| F9 | Stage 2 Signatory Matrix & OCR Ingestion UI | Group A/B/C visual cards, Add/Edit signatory modal, interactive NRIC/Board Resolution OCR dropzone & extraction preview, and Revoke action with governance error banner | M2 | survey_1 |
| F10 | Stage 3 Rule Builder & Live Simulator UI | Visual Tier 1/2/3 rule editor and interactive transaction slider + currency selector backed by `simulate_transaction_authorization` | M2 | survey_1 |
| F11 | Stage 4 Board Resolution Audit & Live Mandate Diff UI | Side-by-side Current vs. Proposed Mandate diff viewer and BRC-09 / custom resolution clause verification checklist | M2 | survey_1 |
| F12 | Stage 5 Digital Execution, DigiSign Tracker & Audit Trail UI | Multi-party DigiSign co-signer status tracker (`COM-2026-...`), one-click co-signer token signing, and live PostgreSQL `mandate_audit_logs` feed | M2 | survey_1 |
| F13 | Synchronized Voice & Chat Copilot Dock UI | WebAudio PCM mic capture & playback, live canvas waveform orb, barge-in control, extended thinking trace accordion, live tool execution cards, and automatic `ui_sync` stage navigation | M2 | survey_1, survey_3 |
| F14 | 4-Tier E2E Test Suite & Verification Harness | Programmatic DB verification (`scripts/verify_db.py`), Tier 1-4 test suite (`tests/`), and end-to-end UI + WebSocket synchronization verification | M3 | survey_1, survey_2, survey_3 |
| F15 | Production Dockerfile & Google Cloud Run Deployment | Production `Dockerfile`, `.dockerignore`, `scripts/docker_entrypoint.sh`, and live deployment to Google Cloud Run in `elevate-data-508005` (`us-central1`, `--allow-unauthenticated`) recorded in `CLOUD_RUN_URL.md` | M4 | user_request |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Database, Tools & Gemini Live Backend | `scripts/ensure_postgres.sh`, `schema/schema.sql`, `synthetic_data/seed.py`, `backend/db.py`, `backend/tools.py`, `backend/gemini_live.py`, `backend/main.py`, `scripts/verify_db.py` | none | DONE |
| M2 | Elevated 5-Stage Web UI & Voice/Chat Copilot Dock | `frontend/index.html`, `frontend/styles.css`, `frontend/app.js`, `scripts/start_server.sh` | M1 interface contract | DONE |
| M3 | E2E Testing Track & Full Integration Verification | `TEST_INFRA.md`, `tests/test_tier1_features.py`, `tests/test_tier2_boundaries.py`, `tests/test_tier3_combinations.py`, `tests/test_tier4_scenarios.py`, `TEST_READY.md` | M1, M2 | DONE |
| M4 | Production Containerization & Google Cloud Run Deployment | `Dockerfile`, `.dockerignore`, `scripts/docker_entrypoint.sh`, `CLOUD_RUN_URL.md` | M1, M2 | DONE |

## Interface Contracts
### Backend REST API (`backend/main.py` on port `8080`) ↔ Frontend (`frontend/app.js`)
- `GET /api/health` → `{"status": "ok", "database": {"engine": "postgresql", "mode": "local_pg18|cloudsql", "connected": true, "customer_count": 5}, "gemini_live": {"model": "models/gemini-3.8-live-extended-thinking", "vertexai": true, "project": "elevate-data-508005"}}`
- `GET /api/customers` → calls `list_customer_profiles()` and returns `{"customers": [...], "active_customer_id": "..."}`
- `GET /api/customers/{customer_id}/mandate` → calls `get_customer_mandate_details(customer_id)` and returns full workspace snapshot (`customer`, `accounts`, `signatories`, `signing_rules`, `board_resolutions`, `applications`, `audit_logs`, `mandate_diff`, `active_stage`)
- `POST /api/customers/switch` → body `{"customer_id": "..."}` → calls `SwitchActiveCustomerProfile(customer_id)` and broadcasts `ui_sync`
- `POST /api/customers/{customer_id}/target-accounts` → body `{"account_ids": [...]}` → calls `update_target_accounts(customer_id, account_ids)`
- `POST /api/customers/{customer_id}/signatories` → body `{full_name, role_title, signing_group, nric_masked, auth_method, email, phone_masked, ocr_verified, specimen_ref}` → calls `add_or_update_signatory(...)`
- `POST /api/customers/{customer_id}/signatories/revoke` → body `{"signatory_id_or_name": "...", "reason": "..."}` → calls `revoke_signatory(...)` (returns HTTP 400 / `{status: "error", error_code: "GOVERNANCE_VIOLATION_SOLE_GROUP_A"}` if revoking sole active Group A signatory)
- `POST /api/customers/{customer_id}/signing-rules` → body `{"rules": [...]}` → calls `configure_signing_rules(...)`
- `POST /api/customers/{customer_id}/simulate` → body `{"amount": 150000, "currency": "SGD"}` → calls `simulate_transaction_authorization(...)`
- `POST /api/customers/{customer_id}/board-resolution/audit` → body `{"resolution_type": "BRC-09|CUSTOM", "resolution_ref": "...", "clause_text": "..."}` → calls `audit_board_resolution(...)`
- `POST /api/customers/{customer_id}/submit` → body `{"submitted_by": "...", "resolution_ref": "...", "notes": "..."}` → calls `submit_mandate_change_request(...)`
- `POST /api/customers/{customer_id}/cosign` → body `{"application_ref": "...", "signer_name": "...", "auth_method": "IDEAL Token"}` → calls `execute_cosigner_signature(...)`
- `POST /api/chat` → body `{"message": "...", "customer_id": "...", "current_stage": 1}` → executes `models/gemini-3.8-live-extended-thinking` (`gemini-3.8-flash` on `global` with `ThinkingConfig(include_thoughts=True)` and all mandate tools), returning `{"reply": "...", "thinking_traces": [...], "tool_calls": [...], "ui_sync": {...}, "workspace_snapshot": {...}}` and broadcasting `ui_sync` to `/ws/live`.

### WebSocket `/ws/live` (`backend/main.py`) ↔ Frontend Voice/Chat Copilot (`frontend/app.js`)
- Client → Server JSON frames:
  - `{"type": "init", "customer_id": "CUST-001", "stage": 1, "mode": "voice|chat"}`
  - `{"type": "audio_chunk", "pcm16_base64": "...", "sample_rate": 16000}`
  - `{"type": "text_turn", "text": "...", "customer_id": "CUST-001"}`
  - `{"type": "barge_in"}`
  - `{"type": "ping"}`
- Server → Client JSON frames:
  - `{"type": "session_ready", "model": "models/gemini-3.8-live-extended-thinking", "active_customer_id": "..."}`
  - `{"type": "thinking_trace", "text": "..."}`
  - `{"type": "tool_call_start", "tool_name": "...", "args": {...}}`
  - `{"type": "tool_call_result", "tool_name": "...", "result": {...}, "ui_sync": {...}}`
  - `{"type": "ui_sync", "ui_action": "...", "target_stage": 2, "updated_profile_id": "CUST-001", "mandate_diff": {...}, "workspace_snapshot": {...}}`
  - `{"type": "audio_out", "pcm24_base64": "...", "sample_rate": 24000}`
  - `{"type": "transcript", "role": "user|assistant", "text": "...", "final": true}`
  - `{"type": "turn_complete"}`

## Code Layout
```
/usr/local/google/home/ramneekkhurana/teamwork_projects/gemini_live_mandate_app/
├── ORIGINAL_REQUEST.md
├── PROJECT.md
├── TEST_INFRA.md
├── TEST_READY.md
├── schema/
│   └── schema.sql
├── synthetic_data/
│   └── seed.py
├── scripts/
│   ├── ensure_postgres.sh
│   ├── verify_db.py
│   └── start_server.sh
├── backend/
│   ├── __init__.py
│   ├── db.py
│   ├── tools.py
│   ├── gemini_live.py
│   └── main.py
├── frontend/
│   ├── index.html
│   ├── styles.css
│   └── app.js
└── tests/
    ├── conftest.py
    ├── test_tier1_features.py
    ├── test_tier2_boundaries.py
    ├── test_tier3_combinations.py
    └── test_tier4_scenarios.py
```
