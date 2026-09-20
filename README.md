# DBS IDEAL Corporate Mandate Hub — Gemini Live 3.8 Copilot

A full-stack **Corporate Banking Change of Account Mandate** web application powered by **Gemini Live 3.8 (`models/gemini-3.8-live-extended-thinking`)**, featuring real-time bidirectional voice and chat interfaces, a PostgreSQL / CloudSQL backend with 5 distinct corporate customer profiles, 12 database-backed agent tools (zero mocks), and an interactive 5-stage mandate workspace.

## Live Google Cloud Run Deployment
- **Primary URL**: https://gemini-live-mandate-app-327571158527.us-central1.run.app
- **Secondary URL**: https://gemini-live-mandate-app-5p6ecehe6q-uc.a.run.app
- **Architecture & DBS IDEAL Production POV**: [ARCHITECTURE_AND_DBS_IDEAL_POV.md](./ARCHITECTURE_AND_DBS_IDEAL_POV.md)

## Architecture & Highlights
1. **Gemini Live 3.8 (`models/gemini-3.8-live-extended-thinking`) Voice & Chat Interface (`backend/gemini_live.py`, `backend/main.py`)**:
   - Real-time bidirectional WebAudio PCM streaming (16kHz mic input / 24kHz native audio playback) over `/ws/live` with barge-in support and live waveform visualization.
   - Extended Thinking traces (`ThinkingConfig(include_thoughts=True)`) and real-time `ui_sync` WebSocket workspace synchronization.
2. **PostgreSQL / CloudSQL Backend (`schema/schema.sql`, `synthetic_data/seed.py`, `backend/db.py`)**:
   - 8 relational tables (`corporate_customers`, `bank_accounts`, `signatories`, `signing_rules`, `board_resolutions`, `mandate_change_applications`, `mandate_audit_logs`, `active_workspace_state`).
   - 5 pre-seeded corporate profiles (`CUST-001` through `CUST-005`), 17 multi-currency accounts (`SGD`, `USD`, `EUR`, `CNH`, `JPY`), 23 signatories across Groups A/B/C, 13 tiered signing rules, and 5 board resolutions (`BRC-09` & `CUSTOM`).
3. **12 Real Database-Backed Agent Tools (`backend/tools.py`)**:
   - `list_customer_profiles`, `get_customer_mandate_details`
   - `SwitchActiveCustomerProfile` / `switch_active_customer_profile`
   - `add_or_update_signatory`, `revoke_signatory` (with `GOVERNANCE_VIOLATION_SOLE_GROUP_A` protection)
   - `configure_signing_rules`, `simulate_transaction_authorization`
   - `audit_board_resolution`, `submit_mandate_change_request`
   - `update_target_accounts`, `execute_cosigner_signature`
4. **5-Stage Interactive Mandate Workspace (`frontend/index.html`, `frontend/styles.css`, `frontend/app.js`)**:
   - **Stage 1**: Entity & Target Accounts
   - **Stage 2**: Signatory Matrix & Document / OCR Ingestion
   - **Stage 3**: Interactive Signing Rule Builder & Multi-Currency Transaction Simulator
   - **Stage 4**: Board Resolution Pre-Flight Audit & Live Mandate Diff
   - **Stage 5**: Digital Execution (Maker-Checker / DigiSign) & Immutable Audit Trail

## Quick Start
```bash
cp .env.example .env
# Add your GEMINI_API_KEY in .env
bash scripts/start_server.sh
```

## Running Automated Verification (`178 passed`)
```bash
python3 scripts/verify_db.py
pytest tests/ -v
```
