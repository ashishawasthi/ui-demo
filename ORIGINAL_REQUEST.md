# Original User Request

## Initial Request — 2026-09-18T04:38:45Z

Build a full-featured, production-grade Corporate Banking Change of Account Mandate application powered by Gemini Live (`models/gemini-3.8-live-extended-thinking`) with real-time bidirectional voice and chat interfaces, backed by a real CloudSQL PostgreSQL database (`elevate-data-508005`) containing at least 5 distinct corporate customer profiles, equipped with real database-backed tools (zero mocks or hardcoded simulations), and featuring a modern, super user-friendly UI inspired by—and substantially elevated beyond—the reference prototype at https://sabrinalim87-commits.github.io/changeofmandate/.

Working directory: /usr/local/google/home/ramneekkhurana/teamwork_projects/gemini_live_mandate_app
Integrity mode: development

Reference UI direction (to elevate significantly): https://sabrinalim87-commits.github.io/changeofmandate/

## Requirements

### R1. Gemini Live (`models/gemini-3.8-live-extended-thinking`) Multimodal Voice & Chat Interface
Integrate `models/gemini-3.8-live-extended-thinking` via the official `google-genai` SDK supporting both Vertex AI (Application Default Credentials in GCP project `elevate-data-508005`) and `GEMINI_API_KEY`. Provide real-time bidirectional voice streaming (microphone PCM audio capture, WebSocket streaming, live audio playback, interruption/barge-in handling, and live audio waveform visualization) alongside a synchronized rich text/document chat interface that surfaces extended thinking traces and live tool execution cards.

### R2. CloudSQL Backend & 5+ Distinct Corporate Customer Profiles (Zero Mocks)
Provision or connect to a CloudSQL PostgreSQL instance in GCP project `elevate-data-508005` using Application Default Credentials (with automated schema initialization and seeding, plus automatic local PostgreSQL failover only if CloudSQL network/IAM provisioning is blocked by environment policies). Persist all domain entities in the database with zero hardcoded UI mocks:
- At least **5 distinct corporate customer profiles** (e.g., SME, multi-entity MNC subsidiary, partnership, high-growth tech startup, and regulated import/export enterprise) with UENs, KYC/IDEAL authentication states, and multiple bank accounts (SGD Operating, USD Multi-Currency, Trade/FX, Escrow, etc.) with live balances.
- **Signatory matrices** per customer profile (Group A / Group B / Group C designations, NRIC/Passport identifiers, IDEAL Token / DigiSign status, specimen signature metadata, and active/revoked/pending states).
- **Tiered signing rules & mandate configurations** (e.g., single-signatory limits, joint signing combinations such as Any 1 Group A or Any 2 Group B up to threshold, Any 2 Group A above threshold).
- **Board Resolutions (BRC-09 / custom minutes) & immutable Audit / Application Tracking logs** recording every mandate modification and approval step.

### R3. Real Database-Backed Agent Tools & Live UI Synchronization
Provide a comprehensive suite of real backend tools callable by the Gemini Live agent during both voice and chat sessions that query and mutate the database directly and push real-time state updates to the active UI:
1. `list_customer_profiles` / `get_customer_mandate_details`: Query customer profiles, accounts, active signatories, and current signing rules from the database.
2. `SwitchActiveCustomerProfile`: Switch the active customer context in both the conversation and the live UI dashboard.
3. `add_or_update_signatory` & `revoke_signatory`: Add new authorized signatories (including OCR/document extraction metadata) or revoke existing signatories in the database with validation against minimum governance rules.
4. `configure_signing_rules` & `simulate_transaction_authorization`: Update tiered signing limit rules in the database and evaluate which signatory combinations are required to authorize a given transaction amount and currency.
5. `audit_board_resolution` & `submit_mandate_change_request`: Validate standard BRC-09 or custom board resolution clauses, generate the mandate diff (before vs. after), persist the submitted Change of Mandate application, and record digital execution (Maker-Checker / DigiSign) audit logs.

### R4. Super User-Friendly, Interactive Multi-Stage UI
Build a responsive, polished web interface that significantly improves upon `https://sabrinalim87-commits.github.io/changeofmandate/`:
- **Profile Switcher & Live Entity Header**: Instant switching across all 5+ database-backed customer profiles with live account balances, UEN, and mandate status badges.
- **Interactive 5-Stage Change of Mandate Workspace**:
  1. *Entity & Target Accounts* (interactive selection and account mandate inspection)
  2. *Signatory Matrix & Document Ingestion* (visual Group A/B/C hierarchy cards, add/revoke actions, document/NRIC upload & extraction preview)
  3. *Interactive Signing Rule Builder & Simulator* (visual tier configuration + live transaction slider/tester backed by database rules)
  4. *Board Resolution Pre-Flight Audit & Live Mandate Diff* (side-by-side "Current vs. Proposed Mandate" comparison and BRC-09 / clause validation)
  5. *Digital Execution & Audit Trail* (DigiSign / IDEAL Token sign-off status, application tracker, and database audit history)
- **Synchronized Voice & Chat Copilot Dock**: Always-accessible Gemini Live 3.8 voice orb/waveform and chat stream where every tool call made by voice or text dynamically navigates and updates the workspace in real time.

## Acceptance Criteria

### Database & Zero-Mock Integrity
- [ ] A programmatic verification script confirms that the relational database schema is created and populated with at least 5 distinct corporate customer profiles, each having multiple bank accounts, existing signatories across signing groups, and tiered mandate rules.
- [ ] Every API endpoint and agent tool reads from and writes to the database; modifying a signatory, updating a signing threshold, or submitting a mandate change persists across server restarts and immediately alters subsequent query results.
- [ ] Zero static/mock data dictionaries are used in the frontend components—all rendered profiles, signatories, accounts, rules, and audit logs originate from backend database queries.

### Gemini Live (`models/gemini-3.8-live-extended-thinking`) & Tool Execution
- [ ] The backend configures and connects to `models/gemini-3.8-live-extended-thinking` via the `google-genai` SDK (supporting Vertex AI ADC in `elevate-data-508005` and `GEMINI_API_KEY`) with all mandate management tools registered in the session declaration.
- [ ] Automated integration tests verify each tool (`list_customer_profiles`, `get_customer_mandate_details`, `add_or_update_signatory`, `revoke_signatory`, `configure_signing_rules`, `simulate_transaction_authorization`, `audit_board_resolution`, `submit_mandate_change_request`) against the live database, including edge-case governance validation (e.g., preventing removal of the sole remaining Group A signatory).
- [ ] Both WebSocket bidirectional voice/audio streaming and chat streaming endpoints pass automated health and end-to-end message/tool-call round-trip verification.

### User Interface & End-to-End Verification
- [ ] The web application starts cleanly, serves both the frontend UI and backend API/WebSocket server, and renders all 5+ customer profiles and the 5-stage Change of Mandate workspace without console or runtime errors.
- [ ] UI actions and AI Copilot tool calls stay synchronized in real time (e.g., asking the voice/chat agent to add a CFO to Group A or change Tier 1 limit to $150,000 updates the visual Signatory Matrix, Signing Rule Simulator, and Mandate Diff view automatically).

## Follow-up — 2026-09-18T04:53:52Z

USER UPDATE — GEMINI LIVE API KEY PROVIDED:
Please pass this immediately to the orchestrator and all implementation/verification agents:
Use the following API key for Gemini Live (`models/gemini-3.8-live-extended-thinking`):
GEMINI_API_KEY="<REDACTED_GEMINI_API_KEY>"
GOOGLE_API_KEY="<REDACTED_GEMINI_API_KEY>"

This key has also been written to `/usr/local/google/home/ramneekkhurana/teamwork_projects/gemini_live_mandate_app/.env`. Ensure the backend server, Gemini Live WebSocket/chat integration, and end-to-end verification tests automatically load and use this key.

## Follow-up — 2026-09-18T05:24:32Z

USER REQUIREMENT ADDITION — DEPLOY TO GOOGLE CLOUD RUN:
The user requested: "deploy frontend on cloudrun".
Please forward this immediately to the Project Orchestrator (`a2a186cc-c672-40e8-8b54-7e67f00024f3`):
1. Add a production `Dockerfile` (and `.dockerignore`) in `/usr/local/google/home/ramneekkhurana/teamwork_projects/gemini_live_mandate_app` that packages the frontend (`frontend/`) and backend (`backend/`, `schema/`, `synthetic_data/`) so the Cloud Run service serves the frontend UI and all `/api/*` + `/ws/live` endpoints seamlessly on `$PORT` (default 8080), seeded and connected to PostgreSQL / CloudSQL and configured with `GEMINI_API_KEY` and `models/gemini-3.8-live-extended-thinking`.
2. Deploy the service to Google Cloud Run in GCP project `elevate-data-508005` (region `us-central1`, `--allow-unauthenticated`) using `gcloud run deploy`, verify that the live `https://...run.app` URL responds 200 OK and serves the full UI + API, and include the live Cloud Run URL in the final handoff report.


