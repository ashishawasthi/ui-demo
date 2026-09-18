# E2E Test Infrastructure & 4-Tier Verification Architecture (`TEST_INFRA.md`)

## 1. Overview & Zero-Mock Mandate
The `gemini_live_mandate_app` test suite verifies all **14 features (`F1`–`F14`)** of the **Corporate Banking Change of Account Mandate** application powered by **Gemini Live (`models/gemini-3.8-live-extended-thinking`)** and backed by a real **PostgreSQL 18.6 / CloudSQL** database (`elevate-data-508005`).

In strict compliance with the **Zero Mock Policy** (`ORIGINAL_REQUEST.md` & `production_agent_integrity.md`):
- **Real Relational Database**: All tests execute against the live PostgreSQL 18.6 cluster (`127.0.0.1:5433/corporate_mandate_db`) or CloudSQL PostgreSQL instance (`elevate-data-508005`). Zero SQLite or in-memory mock dictionaries are used.
- **Real Gemini Live (`models/gemini-3.8-live-extended-thinking`) Integration**: All LLM & Live Audio/Chat tests load `GEMINI_API_KEY` / `GOOGLE_API_KEY` from `/usr/local/google/home/ramneekkhurana/teamwork_projects/gemini_live_mandate_app/.env` alongside Vertex AI Application Default Credentials (`elevate-data-508005`).
- **Real FastAPI REST & WebSocket `/ws/live` Execution**: All HTTP and WebSocket tests run through `fastapi.testclient.TestClient` against `backend.main:app`, verifying real database persistence, `ui_sync` payloads, and WebSocket frame broadcasts.
- **Automatic Database Re-Seeding (`tests/conftest.py`)**: Before and after destructive test classes/modules (and at the conclusion of the entire test session), `conftest.py` invokes the deterministic seed pipeline (`synthetic_data.seed`) so the live PostgreSQL database always remains in a clean, ready-to-serve baseline state with all 5 corporate customer profiles (`CUST-001`..`CUST-005`).

---

## 2. 4-Tier Test Suite Structure (`>= 165` Test Cases)

| Tier | File | Minimum Count | Actual Coverage | Scope |
|------|------|---------------|-----------------|-------|
| **Tier 1** | `tests/test_tier1_features.py` | `>= 70` (`>= 5` per feature `F1`–`F14`) | **75+ tests** | Happy-path verification of all 14 features (`F1`–`F14`): PostgreSQL connection & failover (`backend.db`), 8 tables & 5 corporate profiles (`CUST-001`..`CUST-005`), all 11+ database-backed tools (`backend.tools`) & `ui_sync` contracts, all FastAPI REST endpoints & `/ws/live` WebSocket, and static/runtime verification of `frontend/index.html`, `frontend/styles.css`, and `frontend/app.js`. |
| **Tier 2** | `tests/test_tier2_boundaries.py` | `>= 70` (`>= 5` per feature `F1`–`F14`) | **75+ tests** | Boundary conditions, governance guardrails (sole active Group A signatory protection `GOVERNANCE_VIOLATION_SOLE_GROUP_A`), exact SGD threshold boundaries (`0`, `1`, `100000`, `100000.01`, `500000`, `500000.01`, `10000000`), multi-currency FX boundaries (`SGD`, `USD`, `EUR`, `CNH`, `JPY`), idempotent signatory upserts, missing BRC-09/custom resolution clauses, invalid customer/account IDs, and WebSocket `barge_in`/`ping` edge cases. |
| **Tier 3** | `tests/test_tier3_combinations.py` | `>= 15` | **18+ tests** | Pairwise and multi-step cross-feature interactions: Profile Switch + Target Account Filtering + Signatory Addition + Rule Modification + Multi-Currency Simulation + Board Resolution Audit + Mandate Diff Verification + Application Submission + Co-Signer Token Execution + Immutable Audit Log verification across fresh PostgreSQL connections. |
| **Tier 4** | `tests/test_tier4_scenarios.py` | `>= 10` | **12+ tests** | Realistic end-to-end corporate banking journeys across all 5 corporate customer profiles (`TechNova Solutions Pte Ltd`, `Meridian Pacific Logistics Pte Ltd`, `Apex Global Holdings (SG) Pte Ltd`, `Veritas Legal & Advisory LLP`, `Banyan Artisans & F&B Group Pte Ltd`), including live `models/gemini-3.8-live-extended-thinking` `/api/chat` and `/ws/live` WebSocket multi-tool turns and automatic post-scenario database restoration. |

---

## 3. Feature-to-Test Mapping (`F1`–`F14`)

1. **F1 — CloudSQL + Local PostgreSQL 18.6 Failover**: Verified in Tier 1 (`TestF1DatabaseFailover`) & Tier 2 (`TestF1DatabaseBoundaries`).
2. **F2 — 8-Table Schema & 5+ Corporate Customer Profiles**: Verified in Tier 1 (`TestF2SchemaAndSeed`) & Tier 2 (`TestF2SchemaConstraintsAndIntegrity`).
3. **F3 — Core Mandate Query & Profile Switch Tools**: Verified in Tier 1 (`TestF3CoreMandateAndSwitchTools`) & Tier 2 (`TestF3QueryAndSwitchBoundaries`).
4. **F4 — Signatory Matrix & Governance Tools**: Verified in Tier 1 (`TestF4SignatoryAndGovernanceTools`) & Tier 2 (`TestF4GovernanceAndSignatoryBoundaries`).
5. **F5 — Signing Rule Configuration & Transaction Simulator Tools**: Verified in Tier 1 (`TestF5SigningRulesAndSimulatorTools`) & Tier 2 (`TestF5ThresholdAndFxBoundaries`).
6. **F6 — Board Resolution Audit & Mandate Submission Tools**: Verified in Tier 1 (`TestF6BoardResolutionAndSubmissionTools`) & Tier 2 (`TestF6ResolutionClauseAndSubmissionBoundaries`).
7. **F7 — Gemini Live (`models/gemini-3.8-live-extended-thinking`) Voice & Chat Backend**: Verified in Tier 1 (`TestF7GeminiLiveAndFastAPIBackend`) & Tier 2 (`TestF7ApiAndWebSocketEdgeCases`).
8. **F8 — Profile Switcher & Live Entity Header + Stage 1 UI**: Verified in Tier 1 (`TestF8Stage1AndHeaderUI`) & Tier 2 (`TestF8Stage1AccountScopeBoundaries`).
9. **F9 — Stage 2 Signatory Matrix & OCR Ingestion UI**: Verified in Tier 1 (`TestF9Stage2SignatoryMatrixUI`) & Tier 2 (`TestF9Stage2OcrAndGovernanceUI`).
10. **F10 — Stage 3 Rule Builder & Live Simulator UI**: Verified in Tier 1 (`TestF10Stage3RuleBuilderSimulatorUI`) & Tier 2 (`TestF10Stage3SimulatorBoundariesUI`).
11. **F11 — Stage 4 Board Resolution Audit & Live Mandate Diff UI**: Verified in Tier 1 (`TestF11Stage4DiffAndResolutionUI`) & Tier 2 (`TestF11Stage4DiffEdgeCasesUI`).
12. **F12 — Stage 5 Digital Execution, DigiSign Tracker & Audit Trail UI**: Verified in Tier 1 (`TestF12Stage5ExecutionAndAuditUI`) & Tier 2 (`TestF12Stage5CosignAndAuditBoundariesUI`).
13. **F13 — Synchronized Voice & Chat Copilot Dock UI**: Verified in Tier 1 (`TestF13CopilotDockUI`) & Tier 2 (`TestF13CopilotWebSocketSyncBoundaries`).
14. **F14 — 4-Tier E2E Test Suite & Verification Harness**: Verified in Tier 1 (`TestF14VerificationHarness`) & Tier 2 (`TestF14ZeroMockAndPersistenceAudit`).

---

## 4. Execution Commands

```bash
# 1. Ensure PostgreSQL 18.6 is running and seeded
bash scripts/ensure_postgres.sh
python3 scripts/verify_db.py

# 2. Run the entire 4-Tier E2E Test Suite (>= 165 test cases)
pytest tests/ -v

# 3. Run individual tiers
pytest tests/test_tier1_features.py -v
pytest tests/test_tier2_boundaries.py -v
pytest tests/test_tier3_combinations.py -v
pytest tests/test_tier4_scenarios.py -v
```
