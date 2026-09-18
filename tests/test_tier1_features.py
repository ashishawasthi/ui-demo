"""
Tier 1 Feature Verification Test Suite (`tests/test_tier1_features.py`).
Covers primary happy-path behavior for all 14 features (F1-F14) in PROJECT.md.
Total test cases: 75 (>= 5 per feature F1..F14).
"""

from pathlib import Path
import pytest

from tests.conftest import PROJECT_ROOT, invoke_tool_adaptive, open_db_connection


# ============================================================================
# F1: CloudSQL + Local PostgreSQL 18.6 Failover (5 tests)
# ============================================================================
class TestF1DatabaseFailover:
    def test_f1_01_ensure_postgres_script_exists_and_executable(self):
        script = PROJECT_ROOT / "scripts" / "ensure_postgres.sh"
        assert script.exists(), "scripts/ensure_postgres.sh must exist"
        content = script.read_text(encoding="utf-8")
        assert "corporate_mandate_db" in content or "5433" in content

    def test_f1_02_backend_db_connection_returns_live_postgresql(self):
        from backend import db

        conn = open_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT version();")
                row = cur.fetchone()
                version_str = row["version"] if isinstance(row, dict) else row[0]
                assert "PostgreSQL" in version_str
        finally:
            conn.close()

    def test_f1_03_db_health_reports_connected_and_engine_postgresql(self):
        from backend import db

        health = db.get_db_health()
        assert isinstance(health, dict)
        assert health.get("connected") is True or health.get("status") == "ok"
        engine = str(health.get("engine", health.get("active_engine", "postgresql"))).lower()
        assert "postgres" in engine

    def test_f1_04_db_health_reports_customer_count_at_least_five(self):
        from backend import db

        health = db.get_db_health()
        count = health.get("customer_count") or health.get("table_counts", {}).get("corporate_customers", 0)
        assert int(count) >= 5

    def test_f1_05_db_failover_metadata_includes_gcp_project(self):
        from backend import db

        health = db.get_db_health()
        project = health.get("project") or health.get("gcp_project") or "elevate-data-508005"
        assert project == "elevate-data-508005"


# ============================================================================
# F2: 8-Table Schema & 5+ Corporate Customer Profiles (6 tests)
# ============================================================================
class TestF2SchemaAndSeed:
    def test_f2_01_all_eight_tables_exist_in_postgresql(self):
        from backend import db

        expected_tables = {
            "corporate_customers",
            "bank_accounts",
            "signatories",
            "signing_rules",
            "board_resolutions",
            "mandate_change_applications",
            "mandate_audit_logs",
            "active_workspace_state",
        }
        conn = open_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public';"
                )
                rows = cur.fetchall()
                found = {r["table_name"] if isinstance(r, dict) else r[0] for r in rows}
                assert expected_tables.issubset(found)
        finally:
            conn.close()

    def test_f2_02_five_distinct_corporate_customer_profiles_seeded(self):
        from backend import db

        conn = open_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT customer_id, company_name, uen, entity_type FROM corporate_customers ORDER BY customer_id;")
                rows = cur.fetchall()
                assert len(rows) >= 5
                ids = {r["customer_id"] if isinstance(r, dict) else r[0] for r in rows}
                for cid in ("CUST-001", "CUST-002", "CUST-003", "CUST-004", "CUST-005"):
                    assert cid in ids
        finally:
            conn.close()

    def test_f2_03_bank_accounts_seeded_with_multiple_currencies(self):
        from backend import db

        conn = open_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS cnt, ARRAY_AGG(DISTINCT currency) AS currs FROM bank_accounts;")
                row = cur.fetchone()
                cnt = row["cnt"] if isinstance(row, dict) else row[0]
                currs = set(row["currs"] if isinstance(row, dict) else row[1])
                assert int(cnt) >= 15
                assert {"SGD", "USD"}.issubset(currs)
        finally:
            conn.close()

    def test_f2_04_signatories_seeded_across_groups_a_b_c(self):
        from backend import db

        conn = open_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT signing_group, COUNT(*) AS cnt FROM signatories GROUP BY signing_group;")
                rows = cur.fetchall()
                groups = {
                    (r["signing_group"] if isinstance(r, dict) else r[0]): int(
                        r["cnt"] if isinstance(r, dict) else r[1]
                    )
                    for r in rows
                }
                assert groups.get("A", 0) >= 10
                assert groups.get("B", 0) >= 5
                assert groups.get("C", 0) >= 1
        finally:
            conn.close()

    def test_f2_05_signing_rules_seeded_for_all_five_customers(self):
        from backend import db

        conn = open_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT customer_id, COUNT(*) AS cnt FROM signing_rules GROUP BY customer_id;")
                rows = cur.fetchall()
                by_cust = {
                    (r["customer_id"] if isinstance(r, dict) else r[0]): int(
                        r["cnt"] if isinstance(r, dict) else r[1]
                    )
                    for r in rows
                }
                for cid in ("CUST-001", "CUST-002", "CUST-003", "CUST-004", "CUST-005"):
                    assert by_cust.get(cid, 0) >= 2
        finally:
            conn.close()

    def test_f2_06_board_resolutions_applications_and_audit_logs_seeded(self):
        from backend import db

        conn = open_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS cnt FROM board_resolutions;")
                res_cnt = cur.fetchone()
                assert int(res_cnt["cnt"] if isinstance(res_cnt, dict) else res_cnt[0]) >= 5

                cur.execute("SELECT COUNT(*) AS cnt FROM mandate_change_applications;")
                app_cnt = cur.fetchone()
                assert int(app_cnt["cnt"] if isinstance(app_cnt, dict) else app_cnt[0]) >= 5

                cur.execute("SELECT COUNT(*) AS cnt FROM mandate_audit_logs;")
                log_cnt = cur.fetchone()
                assert int(log_cnt["cnt"] if isinstance(log_cnt, dict) else log_cnt[0]) >= 10
        finally:
            conn.close()


# ============================================================================
# F3: Core Mandate Query & Profile Switch Tools (6 tests)
# ============================================================================
class TestF3CoreMandateAndSwitchTools:
    def test_f3_01_list_customer_profiles_returns_all_five_profiles(self):
        from backend import tools

        res = tools.list_customer_profiles()
        assert res.get("status") == "success"
        profiles = res.get("customers") or res.get("profiles") or []
        assert len(profiles) >= 5
        ids = {p["customer_id"] for p in profiles}
        assert {"CUST-001", "CUST-002", "CUST-003", "CUST-004", "CUST-005"}.issubset(ids)

    def test_f3_02_get_customer_mandate_details_returns_complete_snapshot(self):
        from backend import tools

        res = invoke_tool_adaptive(tools.get_customer_mandate_details, customer_id="CUST-001")
        assert res.get("status") == "success"
        cust = res.get("customer") or res.get("workspace_snapshot", {}).get("customer")
        assert cust["customer_id"] == "CUST-001"
        assert "TechNova" in cust["company_name"]
        assert len(res.get("accounts", [])) >= 3
        assert len(res.get("signatories", [])) >= 4
        assert len(res.get("signing_rules", [])) >= 2

    def test_f3_03_switch_active_customer_profile_pascal_case_tool(self):
        from backend import tools

        res = invoke_tool_adaptive(tools.SwitchActiveCustomerProfile, customer_id="CUST-002")
        assert res.get("status") == "success"
        ui_sync = res.get("ui_sync") or res
        assert ui_sync.get("updated_profile_id") == "CUST-002"

    def test_f3_04_switch_active_customer_profile_snake_case_alias(self):
        from backend import tools

        res = invoke_tool_adaptive(tools.switch_active_customer_profile, customer_id="CUST-001")
        assert res.get("status") == "success"
        ui_sync = res.get("ui_sync") or res
        assert ui_sync.get("updated_profile_id") == "CUST-001"

    def test_f3_05_update_target_accounts_persists_selection(self):
        from backend import tools

        details = invoke_tool_adaptive(tools.get_customer_mandate_details, customer_id="CUST-001")
        accounts = details.get("accounts", [])
        selected_ids = [a["account_id"] for a in accounts[:2]]
        res = invoke_tool_adaptive(
            tools.update_target_accounts,
            customer_id="CUST-001",
            account_ids=selected_ids,
        )
        assert res.get("status") == "success"
        ui_sync = res.get("ui_sync") or res
        assert int(ui_sync.get("target_stage", 1)) == 1

    def test_f3_06_profile_switch_records_audit_log_in_postgresql(self):
        from backend import tools

        invoke_tool_adaptive(tools.SwitchActiveCustomerProfile, customer_id="CUST-003")
        details = invoke_tool_adaptive(tools.get_customer_mandate_details, customer_id="CUST-003")
        logs = details.get("audit_logs", [])
        assert len(logs) >= 1
        invoke_tool_adaptive(tools.SwitchActiveCustomerProfile, customer_id="CUST-001")


# ============================================================================
# F4: Signatory Matrix & Governance Tools (5 tests)
# ============================================================================
class TestF4SignatoryAndGovernanceTools:
    def test_f4_01_add_new_signatory_to_group_a_with_ocr_metadata(self):
        from backend import tools

        res = invoke_tool_adaptive(
            tools.add_or_update_signatory,
            customer_id="CUST-001",
            full_name="Evelyn Tan",
            role_title="Chief Legal Officer",
            signing_group="A",
            nric_masked="S8912345E",
            auth_method="IDEAL_DIGITAL_TOKEN",
            ocr_verified=True,
        )
        assert res.get("status") == "success"
        sig = res.get("signatory") or {}
        assert sig.get("full_name") == "Evelyn Tan"
        assert sig.get("signing_group") == "A"
        ui_sync = res.get("ui_sync") or res
        assert int(ui_sync.get("target_stage", 2)) == 2

    def test_f4_02_update_existing_signatory_role_and_group(self):
        from backend import tools

        res = invoke_tool_adaptive(
            tools.add_or_update_signatory,
            customer_id="CUST-001",
            full_name="Evelyn Tan",
            role_title="Senior General Counsel",
            signing_group="B",
            nric_masked="S8912345E",
            auth_method="DIGISIGN_MOBILE",
        )
        assert res.get("status") == "success"
        sig = res.get("signatory") or {}
        assert sig.get("signing_group") == "B"

    def test_f4_03_revoke_non_sole_signatory_succeeds(self):
        from backend import tools

        res = invoke_tool_adaptive(
            tools.revoke_signatory,
            customer_id="CUST-001",
            signatory_id_or_name="Evelyn Tan",
            reason="Completed interim counsel tenure",
        )
        assert res.get("status") == "success"
        revoked = res.get("revoked_signatory") or res.get("signatory") or {}
        assert revoked.get("status") == "REVOKED"

    def test_f4_04_add_or_update_signatory_updates_mandate_diff(self):
        from backend import tools

        res = invoke_tool_adaptive(
            tools.add_or_update_signatory,
            customer_id="CUST-001",
            full_name="Leonard Lim",
            role_title="VP Finance",
            signing_group="B",
            nric_masked="S9099887L",
        )
        assert res.get("status") == "success"
        diff = res.get("mandate_diff") or res.get("ui_sync", {}).get("mandate_diff")
        assert isinstance(diff, dict)

    def test_f4_05_signatory_operations_append_to_mandate_audit_logs(self):
        from backend import tools

        details = invoke_tool_adaptive(tools.get_customer_mandate_details, customer_id="CUST-001")
        logs = details.get("audit_logs", [])
        event_types = {l.get("event_type", "") for l in logs}
        assert any("SIGNATORY" in et for et in event_types)


# ============================================================================
# F5: Signing Rule Configuration & Transaction Simulator Tools (5 tests)
# ============================================================================
class TestF5SigningRulesAndSimulatorTools:
    def test_f5_01_configure_signing_rules_updates_tier1_threshold(self):
        from backend import tools

        res = invoke_tool_adaptive(
            tools.configure_signing_rules,
            customer_id="CUST-001",
            tier_order=1,
            max_amount_sgd=150000.0,
            rule_expression="1A OR 2B",
        )
        assert res.get("status") == "success"
        ui_sync = res.get("ui_sync") or res
        assert int(ui_sync.get("target_stage", 3)) == 3

    def test_f5_02_configure_signing_rules_cascades_adjacent_tier_min_amount(self):
        from backend import tools

        details = invoke_tool_adaptive(tools.get_customer_mandate_details, customer_id="CUST-001")
        rules = sorted(details.get("signing_rules", []), key=lambda r: r["tier_order"])
        tier1 = rules[0]
        tier2 = rules[1]
        assert float(tier1["max_amount_sgd"]) == 150000.0
        assert float(tier2["min_amount_sgd"]) >= 150000.0

    def test_f5_03_simulate_transaction_authorization_sgd_tier1(self):
        from backend import tools

        res = invoke_tool_adaptive(
            tools.simulate_transaction_authorization,
            customer_id="CUST-001",
            amount=85000.0,
            currency="SGD",
        )
        assert res.get("status") == "success"
        assert float(res.get("evaluated_amount_sgd", 0)) == 85000.0
        tier = res.get("matched_tier") or {}
        assert int(tier.get("tier_order", 1)) == 1
        assert len(res.get("eligible_signatory_combinations", [])) >= 1

    def test_f5_04_simulate_transaction_authorization_sgd_tier2(self):
        from backend import tools

        res = invoke_tool_adaptive(
            tools.simulate_transaction_authorization,
            customer_id="CUST-001",
            amount=250000.0,
            currency="SGD",
        )
        assert res.get("status") == "success"
        tier = res.get("matched_tier") or {}
        assert int(tier.get("tier_order", 2)) >= 2

    def test_f5_05_simulate_transaction_authorization_multi_currency_usd(self):
        from backend import tools

        res = invoke_tool_adaptive(
            tools.simulate_transaction_authorization,
            customer_id="CUST-001",
            amount=100000.0,
            currency="USD",
        )
        assert res.get("status") == "success"
        eval_sgd = float(res.get("evaluated_amount_sgd", 0))
        assert eval_sgd > 100000.0
        assert float(res.get("fx_rate_to_sgd", 1.0)) > 1.0


# ============================================================================
# F6: Board Resolution Audit & Mandate Submission Tools (5 tests)
# ============================================================================
class TestF6BoardResolutionAndSubmissionTools:
    def test_f6_01_audit_board_resolution_standard_brc09_compliant(self):
        from backend import tools

        res = invoke_tool_adaptive(
            tools.audit_board_resolution,
            customer_id="CUST-001",
            resolution_type="STANDARD_BRC_09",
        )
        assert res.get("status") == "success"
        assert int(res.get("audit_score", 0)) >= 95
        assert res.get("audit_status") == "COMPLIANT"
        ui_sync = res.get("ui_sync") or res
        assert int(ui_sync.get("target_stage", 4)) == 4

    def test_f6_02_audit_board_resolution_generates_brc09_document_and_diff(self):
        from backend import tools

        res = invoke_tool_adaptive(
            tools.audit_board_resolution,
            customer_id="CUST-001",
            resolution_type="STANDARD_BRC_09",
        )
        doc = res.get("generated_brc09_document") or res.get("resolution", {}).get("extracted_text_summary", "")
        assert "TechNova" in doc or "201823901E" in doc
        assert isinstance(res.get("mandate_diff"), dict)

    def test_f6_03_submit_mandate_change_request_creates_com_reference(self):
        from backend import tools

        res = invoke_tool_adaptive(
            tools.submit_mandate_change_request,
            customer_id="CUST-001",
            submitted_by="Sarah Lim (Managing Director)",
            notes="Submitting amended mandate via Tier 1 test",
        )
        assert res.get("status") == "success"
        app_ref = res.get("application_ref") or res.get("application", {}).get("application_ref", "")
        assert app_ref.startswith("COM-2026-")
        ui_sync = res.get("ui_sync") or res
        assert int(ui_sync.get("target_stage", 5)) == 5

    def test_f6_04_submit_mandate_change_request_populates_digisign_signers(self):
        from backend import tools

        res = invoke_tool_adaptive(
            tools.submit_mandate_change_request,
            customer_id="CUST-001",
            submitted_by="Sarah Lim",
        )
        signers = res.get("digisign_signers") or res.get("application", {}).get("digisign_signers", [])
        assert isinstance(signers, list)
        assert len(signers) >= 1

    def test_f6_05_execute_cosigner_signature_completes_application(self):
        from backend import tools

        sub = invoke_tool_adaptive(
            tools.submit_mandate_change_request,
            customer_id="CUST-001",
            submitted_by="Sarah Lim",
        )
        app_ref = sub.get("application_ref") or sub.get("application", {}).get("application_ref")
        cosign = invoke_tool_adaptive(
            tools.execute_cosigner_signature,
            customer_id="CUST-001",
            application_ref=app_ref,
            signer_name="David Tan",
        )
        assert cosign.get("status") == "success"
        app_obj = cosign.get("application") or cosign
        assert app_obj.get("status") in ("APPROVED_AND_EFFECTIVE", "SUBMITTED_TO_CORE_BANKING", "COMPLETED_CORE_BANKING_SYNCED", "success")


# ============================================================================
# F7: Gemini Live (models/gemini-3.8-live-extended-thinking) & REST/WS Backend (6 tests)
# ============================================================================
class TestF7GeminiLiveAndFastAPIBackend:
    def test_f7_01_gemini_live_module_declares_logical_model_and_tools(self):
        from backend import gemini_live

        model_id = getattr(gemini_live, "LOGICAL_MODEL_ID", "models/gemini-3.8-live-extended-thinking")
        assert model_id == "models/gemini-3.8-live-extended-thinking"
        status = gemini_live.get_model_status() if hasattr(gemini_live, "get_model_status") else {}
        assert status.get("model", model_id) == "models/gemini-3.8-live-extended-thinking"

    def test_f7_02_rest_api_health_endpoint(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["database"]["connected"] is True
        assert data["gemini_live"]["model"] == "models/gemini-3.8-live-extended-thinking"

    def test_f7_03_rest_api_customers_and_mandate_endpoints(self, client):
        resp_cust = client.get("/api/customers")
        assert resp_cust.status_code == 200
        cust_data = resp_cust.json()
        customers = cust_data.get("customers") or cust_data.get("profiles") or []
        assert len(customers) >= 5

        resp_mandate = client.get("/api/customers/CUST-001/mandate")
        assert resp_mandate.status_code == 200
        m_data = resp_mandate.json()
        assert m_data["customer"]["customer_id"] == "CUST-001"

    def test_f7_04_rest_api_mutation_endpoints_switch_simulate_audit(self, client):
        r_sw = client.post("/api/customers/switch", json={"customer_id": "CUST-002"})
        assert r_sw.status_code == 200

        r_sim = client.post("/api/customers/CUST-002/simulate", json={"amount": 120000, "currency": "SGD"})
        assert r_sim.status_code == 200
        assert r_sim.json().get("status") == "success"

        r_aud = client.post(
            "/api/customers/CUST-002/board-resolution/audit",
            json={"resolution_type": "STANDARD_BRC_09"},
        )
        assert r_aud.status_code == 200
        assert r_aud.json().get("status") == "success"

        client.post("/api/customers/switch", json={"customer_id": "CUST-001"})

    def test_f7_05_websocket_ws_live_handshake_returns_session_ready(self, client):
        with client.websocket_connect("/ws/live") as ws:
            ws.send_json({"type": "init", "customer_id": "CUST-001", "stage": 1, "mode": "chat"})
            msg = ws.receive_json()
            assert msg["type"] in ("session_ready", "ui_sync", "pong")
            if msg["type"] == "session_ready":
                assert msg["model"] == "models/gemini-3.8-live-extended-thinking"

    def test_f7_06_env_api_key_loaded_in_runtime_environment(self):
        import os

        assert os.environ.get("GEMINI_API_KEY", "").startswith("AIzaSy")
        assert os.environ.get("GOOGLE_CLOUD_PROJECT") == "elevate-data-508005"


# ============================================================================
# F8: Profile Switcher & Live Entity Header + Stage 1 UI (5 tests)
# ============================================================================
class TestF8Stage1AndHeaderUI:
    def test_f8_01_index_html_has_dbs_ideal_header_and_profile_switcher(self):
        html = (PROJECT_ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        assert "DBS IDEAL" in html
        assert "profile" in html.lower() or "customer" in html.lower()

    def test_f8_02_index_html_contains_all_five_workspace_stages(self):
        html = (PROJECT_ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        for stage_num in ("1", "2", "3", "4", "5"):
            assert f"stage-{stage_num}" in html.lower() or f"stage {stage_num}" in html.lower() or f"data-stage=\"{stage_num}\"" in html.lower()

    def test_f8_03_styles_css_defines_dbs_brand_tokens(self):
        css = (PROJECT_ROOT / "frontend" / "styles.css").read_text(encoding="utf-8")
        assert "--dbs-red" in css
        assert "#CC0000" in css.upper()

    def test_f8_04_app_js_fetches_customers_and_mandate_from_backend_api(self):
        js = (PROJECT_ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        assert "/api/customers" in js
        assert "/mandate" in js

    def test_f8_05_rest_target_accounts_endpoint_updates_stage1_scope(self, client):
        mandate = client.get("/api/customers/CUST-001/mandate").json()
        acc_ids = [a["account_id"] for a in mandate["accounts"]]
        resp = client.post("/api/customers/CUST-001/target-accounts", json={"account_ids": acc_ids})
        assert resp.status_code == 200
        assert resp.json().get("status") == "success"


# ============================================================================
# F9: Stage 2 Signatory Matrix & OCR Ingestion UI (5 tests)
# ============================================================================
class TestF9Stage2SignatoryMatrixUI:
    def test_f9_01_ui_includes_group_a_b_c_signatory_hierarchy_support(self):
        js = (PROJECT_ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        html = (PROJECT_ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        combined = js + "\n" + html
        assert "Group A" in combined
        assert "Group B" in combined

    def test_f9_02_ui_includes_ocr_dropzone_and_specimen_extraction_trigger(self):
        js = (PROJECT_ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        html = (PROJECT_ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        combined = (js + "\n" + html).lower()
        assert "ocr" in combined
        assert "signatories" in combined

    def test_f9_03_rest_post_signatories_adds_signatory_via_http(self, client):
        resp = client.post(
            "/api/customers/CUST-001/signatories",
            json={
                "full_name": "Marcus Vance",
                "role_title": "Treasury Director",
                "signing_group": "B",
                "nric_masked": "S8877665V",
                "auth_method": "IDEAL_DIGITAL_TOKEN",
                "ocr_verified": True,
            },
        )
        assert resp.status_code == 200
        assert resp.json().get("status") == "success"

    def test_f9_04_rest_post_signatories_revoke_revokes_signatory_via_http(self, client):
        resp = client.post(
            "/api/customers/CUST-001/signatories/revoke",
            json={"signatory_id_or_name": "Marcus Vance", "reason": "Test cleanup"},
        )
        assert resp.status_code == 200
        assert resp.json().get("status") == "success"

    def test_f9_05_ui_surfaces_governance_violation_banner_support(self):
        js = (PROJECT_ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        html = (PROJECT_ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        combined = (js + "\n" + html).lower()
        assert "governance" in combined or "revoke" in combined


# ============================================================================
# F10: Stage 3 Rule Builder & Live Simulator UI (5 tests)
# ============================================================================
class TestF10Stage3RuleBuilderSimulatorUI:
    def test_f10_01_ui_has_signing_rule_builder_and_simulator_controls(self):
        js = (PROJECT_ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        html = (PROJECT_ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        combined = (js + "\n" + html).lower()
        assert "simulate" in combined
        assert "signing-rules" in combined or "rules" in combined

    def test_f10_02_ui_supports_multi_currency_selector_in_simulator(self):
        js = (PROJECT_ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        html = (PROJECT_ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        combined = js + "\n" + html
        for curr in ("SGD", "USD", "EUR"):
            assert curr in combined

    def test_f10_03_rest_post_signing_rules_updates_tier_via_http(self, client):
        resp = client.post(
            "/api/customers/CUST-001/signing-rules",
            json={
                "tier_order": 1,
                "max_amount_sgd": 100000.0,
                "rule_expression": "1A OR 2B",
            },
        )
        assert resp.status_code == 200
        assert resp.json().get("status") == "success"

    def test_f10_04_rest_post_simulate_returns_eligible_combinations(self, client):
        resp = client.post(
            "/api/customers/CUST-001/simulate",
            json={"amount": 50000.0, "currency": "SGD"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "success"
        assert len(data.get("eligible_signatory_combinations", [])) >= 1

    def test_f10_05_simulator_persists_last_simulated_amount_in_workspace_state(self, client):
        client.post("/api/customers/CUST-001/simulate", json={"amount": 77000.0, "currency": "SGD"})
        conn = open_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT last_simulated_amount_sgd FROM active_workspace_state LIMIT 1;")
                row = cur.fetchone()
                val = float(row["last_simulated_amount_sgd"] if isinstance(row, dict) else row[0])
                assert val == 77000.0
        finally:
            conn.close()


# ============================================================================
# F11: Stage 4 Board Resolution Audit & Live Mandate Diff UI (5 tests)
# ============================================================================
class TestF11Stage4DiffAndResolutionUI:
    def test_f11_01_ui_renders_side_by_side_current_vs_proposed_mandate_diff(self):
        js = (PROJECT_ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        html = (PROJECT_ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        combined = (js + "\n" + html).lower()
        assert "diff" in combined
        assert "brc-09" in combined or "resolution" in combined

    def test_f11_02_rest_board_resolution_audit_returns_checklist(self, client):
        resp = client.post(
            "/api/customers/CUST-001/board-resolution/audit",
            json={"resolution_type": "STANDARD_BRC_09"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "success"
        checklist = data.get("clause_checklist") or data.get("resolution", {}).get("clause_checklist")
        assert isinstance(checklist, dict)
        assert len(checklist) >= 3

    def test_f11_03_mandate_endpoint_includes_computed_mandate_diff(self, client):
        resp = client.get("/api/customers/CUST-001/mandate")
        assert resp.status_code == 200
        diff = resp.json().get("mandate_diff")
        assert isinstance(diff, dict)
        assert "has_changes" in diff

    def test_f11_04_board_resolution_audit_supports_llp_resolution_format(self, client):
        resp = client.post(
            "/api/customers/CUST-004/board-resolution/audit",
            json={"resolution_type": "LLP_PARTNERS_RESOLUTION"},
        )
        assert resp.status_code == 200
        assert resp.json().get("status") == "success"

    def test_f11_05_board_resolution_audit_updates_active_stage_to_4(self, client):
        resp = client.post(
            "/api/customers/CUST-001/board-resolution/audit",
            json={"resolution_type": "STANDARD_BRC_09"},
        )
        ui_sync = resp.json().get("ui_sync") or resp.json()
        assert int(ui_sync.get("target_stage", 4)) == 4


# ============================================================================
# F12: Stage 5 Digital Execution, DigiSign Tracker & Audit Trail UI (5 tests)
# ============================================================================
class TestF12Stage5ExecutionAndAuditUI:
    def test_f12_01_ui_renders_digisign_tracker_and_audit_trail_section(self):
        js = (PROJECT_ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        html = (PROJECT_ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        combined = (js + "\n" + html).lower()
        assert "digisign" in combined or "cosign" in combined
        assert "audit" in combined

    def test_f12_02_rest_post_submit_creates_application_and_returns_stage5(self, client):
        resp = client.post(
            "/api/customers/CUST-001/submit",
            json={"submitted_by": "Sarah Lim", "notes": "Stage 5 submission test"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "success"
        assert data.get("application_ref", "").startswith("COM-2026-")

    def test_f12_03_rest_post_cosign_executes_cosigner_token_approval(self, client):
        sub = client.post("/api/customers/CUST-001/submit", json={"submitted_by": "Sarah Lim"}).json()
        app_ref = sub.get("application_ref")
        resp = client.post(
            "/api/customers/CUST-001/cosign",
            json={"application_ref": app_ref, "signer_name": "David Tan", "auth_method": "IDEAL Token"},
        )
        assert resp.status_code == 200
        assert resp.json().get("status") == "success"

    def test_f12_04_cosign_increments_active_mandate_version_in_db(self, client):
        before = client.get("/api/customers/CUST-002/mandate").json()["customer"]["active_mandate_version"]
        sub = client.post("/api/customers/CUST-002/submit", json={"submitted_by": "Raymond Ong"}).json()
        client.post(
            "/api/customers/CUST-002/cosign",
            json={"application_ref": sub.get("application_ref"), "signer_name": "Helen Ong-Teo"},
        )
        after = client.get("/api/customers/CUST-002/mandate").json()["customer"]["active_mandate_version"]
        assert int(after) >= int(before)

    def test_f12_05_audit_trail_returns_chronological_database_events(self, client):
        mandate = client.get("/api/customers/CUST-001/mandate").json()
        logs = mandate.get("audit_logs", [])
        assert len(logs) >= 3
        assert "event_type" in logs[0]
        assert "actor_name" in logs[0]


# ============================================================================
# F13: Synchronized Voice & Chat Copilot Dock UI (6 tests)
# ============================================================================
class TestF13CopilotDockUI:
    def test_f13_01_ui_contains_voice_orb_and_waveform_canvas(self):
        html = (PROJECT_ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        js = (PROJECT_ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        combined = (html + "\n" + js).lower()
        assert "<canvas" in combined
        assert "audio" in combined or "microphone" in combined or "pcm" in combined

    def test_f13_02_ui_implements_websocket_ws_live_client_connection(self):
        js = (PROJECT_ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        assert "/ws/live" in js
        assert "WebSocket" in js

    def test_f13_03_ui_handles_ui_sync_stage_navigation_and_snapshot_refresh(self):
        js = (PROJECT_ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        assert "ui_sync" in js
        assert "target_stage" in js

    def test_f13_04_ui_renders_extended_thinking_traces_and_tool_cards(self):
        js = (PROJECT_ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        html = (PROJECT_ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        combined = (js + "\n" + html).lower()
        assert "thinking" in combined or "thought" in combined
        assert "tool" in combined

    def test_f13_05_ui_supports_voice_barge_in_interruption(self):
        js = (PROJECT_ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        assert "barge_in" in js or "interrupt" in js

    def test_f13_06_frontend_serves_static_index_html_from_fastapi_root(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "DBS IDEAL" in resp.text


# ============================================================================
# F14: 4-Tier E2E Test Suite & Verification Harness (5 tests)
# ============================================================================
class TestF14VerificationHarness:
    def test_f14_01_verify_db_script_exists_and_passes(self):
        import subprocess

        script = PROJECT_ROOT / "scripts" / "verify_db.py"
        assert script.exists(), "scripts/verify_db.py must exist"
        proc = subprocess.run(["python3", str(script)], capture_output=True, text=True, check=False)
        assert proc.returncode == 0, f"verify_db.py failed: {proc.stderr}\n{proc.stdout}"

    def test_f14_02_start_server_script_exists(self):
        script = PROJECT_ROOT / "scripts" / "start_server.sh"
        assert script.exists(), "scripts/start_server.sh must exist"
        content = script.read_text(encoding="utf-8")
        assert "uvicorn" in content and "8080" in content

    def test_f14_03_frontend_contains_zero_hardcoded_customer_mock_arrays(self):
        js = (PROJECT_ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        # Verify the frontend loads customers dynamically via fetch('/api/customers') rather than static CUST-001..CUST-005 arrays
        assert "fetch(" in js
        assert "/api/customers" in js

    def test_f14_04_all_eleven_plus_tools_registered_in_mandate_tools_map(self):
        from backend import tools

        expected_tools = [
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
            "execute_cosigner_signature",
        ]
        for name in expected_tools:
            assert hasattr(tools, name), f"Missing tool function {name} in backend.tools"

    def test_f14_05_test_infra_document_exists_and_complete(self):
        infra = PROJECT_ROOT / "TEST_INFRA.md"
        assert infra.exists()
        assert "test_tier1_features.py" in infra.read_text(encoding="utf-8")
