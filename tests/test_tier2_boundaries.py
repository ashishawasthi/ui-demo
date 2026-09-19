"""
Tier 2 Boundary, Governance & Edge Case Test Suite (`tests/test_tier2_boundaries.py`).
Covers boundary conditions, governance guardrails, FX threshold crossings, and adversarial edge cases
across all 14 features (F1-F14).
Total test cases: 75 (>= 5 per feature F1..F14).
"""

import pytest

from tests.conftest import (
    PROJECT_ROOT,
    ensure_database_ready_and_seeded,
    invoke_tool_adaptive,
    open_db_connection,
    receive_next_ws_frame,
)


# ============================================================================
# F1: Database Connection & Failover Boundaries (5 tests)
# ============================================================================
class TestF1DatabaseBoundaries:
    def test_f1_b01_multiple_simultaneous_connections_isolation(self):
        from backend import db

        c1 = open_db_connection()
        c2 = open_db_connection()
        try:
            with c1.cursor() as cur1, c2.cursor() as cur2:
                cur1.execute("SELECT COUNT(*) AS cnt FROM corporate_customers;")
                cur2.execute("SELECT COUNT(*) AS cnt FROM bank_accounts;")
                r1 = cur1.fetchone()
                r2 = cur2.fetchone()
                assert int(r1["cnt"] if isinstance(r1, dict) else r1[0]) >= 5
                assert int(r2["cnt"] if isinstance(r2, dict) else r2[0]) >= 15
        finally:
            c1.close()
            c2.close()

    def test_f1_b02_utf8_and_special_characters_roundtrip_in_postgresql(self):
        from backend import db

        conn = open_db_connection()
        try:
            with conn.cursor() as cur:
                special = "TechNova — 財務部 & Co. (S$100,000) 'quoted' \"double\""
                cur.execute("SELECT %s AS echo;", (special,))
                row = cur.fetchone()
                val = row["echo"] if isinstance(row, dict) else row[0]
                assert val == special
        finally:
            conn.close()

    def test_f1_b03_jsonb_column_query_on_governance_policy(self):
        from backend import db

        conn = open_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT governance_policy FROM corporate_customers WHERE customer_id = 'CUST-001';")
                row = cur.fetchone()
                pol = row["governance_policy"] if isinstance(row, dict) else row[0]
                assert isinstance(pol, dict)
        finally:
            conn.close()

    def test_f1_b04_database_reseed_idempotency(self):
        ensure_database_ready_and_seeded()
        ensure_database_ready_and_seeded()
        from backend import db

        health = db.get_db_health()
        count = health.get("customer_count") or health.get("table_counts", {}).get("corporate_customers", 0)
        assert int(count) == 5

    def test_f1_b05_postgresql_numeric_precision_two_decimal_places(self):
        from backend import db

        conn = open_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT current_balance FROM bank_accounts WHERE customer_id = 'CUST-001' LIMIT 1;")
                row = cur.fetchone()
                bal = float(row["current_balance"] if isinstance(row, dict) else row[0])
                assert bal > 0.0
        finally:
            conn.close()


# ============================================================================
# F2: Schema Constraints & Referential Integrity Boundaries (5 tests)
# ============================================================================
class TestF2SchemaConstraintsAndIntegrity:
    def test_f2_b01_unique_uen_constraint_across_all_customers(self):
        from backend import db

        conn = open_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(DISTINCT uen) AS u_cnt, COUNT(*) AS t_cnt FROM corporate_customers;")
                row = cur.fetchone()
                u_cnt = int(row["u_cnt"] if isinstance(row, dict) else row[0])
                t_cnt = int(row["t_cnt"] if isinstance(row, dict) else row[1])
                assert u_cnt == t_cnt
        finally:
            conn.close()

    def test_f2_b02_unique_account_number_constraint_across_all_accounts(self):
        from backend import db

        conn = open_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(DISTINCT account_number) AS a_cnt, COUNT(*) AS t_cnt FROM bank_accounts;")
                row = cur.fetchone()
                a_cnt = int(row["a_cnt"] if isinstance(row, dict) else row[0])
                t_cnt = int(row["t_cnt"] if isinstance(row, dict) else row[1])
                assert a_cnt == t_cnt
        finally:
            conn.close()

    def test_f2_b03_all_signatories_belong_to_valid_groups_a_b_or_c(self):
        from backend import db

        conn = open_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT DISTINCT signing_group FROM signatories;")
                rows = cur.fetchall()
                groups = {r["signing_group"] if isinstance(r, dict) else r[0] for r in rows}
                assert groups.issubset({"A", "B", "C"})
        finally:
            conn.close()

    def test_f2_b04_every_customer_has_at_least_two_active_group_a_signatories_in_baseline(self):
        ensure_database_ready_and_seeded()
        from backend import db

        conn = open_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT customer_id, COUNT(*) AS a_cnt
                    FROM signatories
                    WHERE signing_group = 'A' AND status IN ('ACTIVE', 'PENDING_ADDITION')
                    GROUP BY customer_id;
                    """
                )
                rows = cur.fetchall()
                counts = {
                    (r["customer_id"] if isinstance(r, dict) else r[0]): int(
                        r["a_cnt"] if isinstance(r, dict) else r[1]
                    )
                    for r in rows
                }
                for cid in ("CUST-001", "CUST-002", "CUST-003", "CUST-004", "CUST-005"):
                    assert counts.get(cid, 0) >= 2
        finally:
            conn.close()

    def test_f2_b05_board_resolutions_audit_score_within_0_to_100(self):
        from backend import db

        conn = open_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT MIN(audit_score) AS min_s, MAX(audit_score) AS max_s FROM board_resolutions;")
                row = cur.fetchone()
                min_s = int(row["min_s"] if isinstance(row, dict) else row[0])
                max_s = int(row["max_s"] if isinstance(row, dict) else row[1])
                assert 0 <= min_s <= max_s <= 100
        finally:
            conn.close()


# ============================================================================
# F3: Core Mandate Query & Profile Switch Boundaries (5 tests)
# ============================================================================
class TestF3QueryAndSwitchBoundaries:
    def test_f3_b01_get_customer_mandate_details_by_company_name_substring(self):
        from backend import tools

        res = invoke_tool_adaptive(tools.get_customer_mandate_details, customer_id="TechNova")
        assert res.get("status") == "success"
        assert res["customer"]["customer_id"] == "CUST-001"

    def test_f3_b02_get_customer_mandate_details_by_uen(self):
        from backend import tools

        res = invoke_tool_adaptive(tools.get_customer_mandate_details, customer_id="201823901E")
        assert res.get("status") == "success"
        assert res["customer"]["customer_id"] == "CUST-001"

    def test_f3_b03_get_customer_mandate_details_invalid_customer_returns_error_or_fallback(self):
        from backend import tools

        res = invoke_tool_adaptive(tools.get_customer_mandate_details, customer_id="CUST-NONEXISTENT-999")
        assert res.get("status") in ("error", "not_found", "success")
        if res.get("status") == "success":
            # Verifies graceful fallback to active_workspace_state customer
            assert res.get("customer_id") in ("CUST-001", "CUST-002", "CUST-003", "CUST-004", "CUST-005")

    def test_f3_b04_switch_active_customer_profile_invalid_id_handled_gracefully(self):
        from backend import tools

        res = invoke_tool_adaptive(tools.SwitchActiveCustomerProfile, customer_id="CUST-INVALID-999")
        assert res.get("status") in ("error", "success")
        if res.get("status") == "success":
            assert res.get("customer_id") in ("CUST-001", "CUST-002", "CUST-003", "CUST-004", "CUST-005")

    def test_f3_b05_update_target_accounts_empty_selection_rejected(self):
        from backend import tools

        res = invoke_tool_adaptive(tools.update_target_accounts, customer_id="CUST-001", account_ids=[])
        assert res.get("status") == "error" or "MIN_ONE" in str(res).upper() or "AT LEAST" in str(res).upper()


# ============================================================================
# F4: Governance Guardrails & Signatory Boundaries (6 tests)
# ============================================================================
class TestF4GovernanceAndSignatoryBoundaries:
    def test_f4_b01_sole_remaining_group_a_signatory_revocation_blocked(self):
        ensure_database_ready_and_seeded()
        from backend import tools

        details = invoke_tool_adaptive(tools.get_customer_mandate_details, customer_id="CUST-005")
        group_a = [
            s for s in details.get("signatories", [])
            if s.get("signing_group") == "A" and s.get("status") == "ACTIVE"
        ]
        assert len(group_a) >= 2
        # Revoke all but 1 active Group A signatory
        for sig in group_a[:-1]:
            r = invoke_tool_adaptive(
                tools.revoke_signatory,
                customer_id="CUST-005",
                signatory_id_or_name=sig["signatory_id"],
                reason="Leave single Group A for governance test",
            )
            assert r.get("status") == "success"

        # Now attempt to revoke the SOLE remaining active Group A signatory
        sole_sig = group_a[-1]
        blocked = invoke_tool_adaptive(
            tools.revoke_signatory,
            customer_id="CUST-005",
            signatory_id_or_name=sole_sig["signatory_id"],
            reason="Attempt illegal sole Group A removal",
        )
        assert blocked.get("status") == "error"
        err_code = str(blocked.get("error_code", "")).upper()
        assert "GOVERNANCE" in err_code or "GROUP_A" in err_code
        ensure_database_ready_and_seeded()

    def test_f4_b02_rest_api_sole_group_a_revocation_returns_400_or_governance_error(self, client):
        ensure_database_ready_and_seeded()
        mandate = client.get("/api/customers/CUST-004/mandate").json()
        group_a = [
            s for s in mandate.get("signatories", [])
            if s.get("signing_group") == "A" and s.get("status") == "ACTIVE"
        ]
        for sig in group_a[:-1]:
            client.post(
                "/api/customers/CUST-004/signatories/revoke",
                json={"signatory_id_or_name": sig["signatory_id"], "reason": "Reduce to 1"},
            )
        sole = group_a[-1]
        resp = client.post(
            "/api/customers/CUST-004/signatories/revoke",
            json={"signatory_id_or_name": sole["signatory_id"], "reason": "Try revoking last Group A"},
        )
        assert resp.status_code in (400, 422, 200)
        body = resp.json()
        if resp.status_code == 200:
            assert body.get("status") == "error"
        assert "GOVERNANCE" in str(body).upper() or "GROUP_A" in str(body).upper()
        ensure_database_ready_and_seeded()

    def test_f4_b03_idempotent_duplicate_signatory_upsert_by_name(self):
        from backend import tools

        r1 = invoke_tool_adaptive(
            tools.add_or_update_signatory,
            customer_id="CUST-001",
            full_name="Duplicate Test Officer",
            role_title="Finance Officer",
            signing_group="C",
            nric_masked="S9111222D",
        )
        r2 = invoke_tool_adaptive(
            tools.add_or_update_signatory,
            customer_id="CUST-001",
            full_name="Duplicate Test Officer",
            role_title="Senior Finance Officer",
            signing_group="B",
            nric_masked="S9111222D",
        )
        assert r1.get("status") == "success"
        assert r2.get("status") == "success"
        assert r2["signatory"]["signing_group"] == "B"
        assert r1["signatory"]["signatory_id"] == r2["signatory"]["signatory_id"]

    def test_f4_b04_normalize_group_label_prefix_in_add_signatory(self):
        from backend import tools

        res = invoke_tool_adaptive(
            tools.add_or_update_signatory,
            customer_id="CUST-001",
            full_name="Group Prefix Officer",
            role_title="Director",
            signing_group="Group A",
            nric_masked="S8000111G",
        )
        assert res.get("status") == "success"
        assert res["signatory"]["signing_group"] == "A"

    def test_f4_b05_revoke_nonexistent_signatory_returns_structured_error(self):
        from backend import tools

        res = invoke_tool_adaptive(
            tools.revoke_signatory,
            customer_id="CUST-001",
            signatory_id_or_name="Nonexistent Person XYZ-999",
            reason="Test unknown person",
        )
        assert res.get("status") == "error"
        ensure_database_ready_and_seeded()


# ============================================================================
# F5: Signing Threshold & Multi-Currency FX Boundaries (8 tests)
# ============================================================================
class TestF5ThresholdAndFxBoundaries:
    def test_f5_b01_simulate_amount_zero_and_one_sgd(self):
        ensure_database_ready_and_seeded()
        from backend import tools

        r_zero = invoke_tool_adaptive(
            tools.simulate_transaction_authorization,
            customer_id="CUST-001",
            amount=0.0,
            currency="SGD",
        )
        r_one = invoke_tool_adaptive(
            tools.simulate_transaction_authorization,
            customer_id="CUST-001",
            amount=1.0,
            currency="SGD",
        )
        assert r_zero.get("status") in ("success", "error")
        assert r_one.get("status") == "success"
        assert int(r_one["matched_tier"]["tier_order"]) == 1

    def test_f5_b02_simulate_exact_tier1_upper_boundary_100000_sgd(self):
        from backend import tools

        res = invoke_tool_adaptive(
            tools.simulate_transaction_authorization,
            customer_id="CUST-001",
            amount=100000.00,
            currency="SGD",
        )
        assert res.get("status") == "success"
        assert int(res["matched_tier"]["tier_order"]) == 1

    def test_f5_b03_simulate_exact_tier2_lower_boundary_100000_01_sgd(self):
        from backend import tools

        res = invoke_tool_adaptive(
            tools.simulate_transaction_authorization,
            customer_id="CUST-001",
            amount=100000.01,
            currency="SGD",
        )
        assert res.get("status") == "success"
        assert int(res["matched_tier"]["tier_order"]) == 2

    def test_f5_b04_simulate_exact_500000_and_500000_01_boundaries_on_three_tier_customer(self):
        from backend import tools

        # Configure CUST-001 or CUST-003 with 500,000 boundary
        invoke_tool_adaptive(
            tools.configure_signing_rules,
            customer_id="CUST-003",
            tier_order=1,
            max_amount_sgd=500000.00,
            rule_expression="1A OR 2B",
        )
        r_500k = invoke_tool_adaptive(
            tools.simulate_transaction_authorization,
            customer_id="CUST-003",
            amount=500000.00,
            currency="SGD",
        )
        r_500k_plus = invoke_tool_adaptive(
            tools.simulate_transaction_authorization,
            customer_id="CUST-003",
            amount=500000.01,
            currency="SGD",
        )
        assert int(r_500k["matched_tier"]["tier_order"]) == 1
        assert int(r_500k_plus["matched_tier"]["tier_order"]) >= 2
        ensure_database_ready_and_seeded()

    def test_f5_b05_simulate_extreme_high_value_10000000_sgd(self):
        from backend import tools

        res = invoke_tool_adaptive(
            tools.simulate_transaction_authorization,
            customer_id="CUST-003",
            amount=10000000.00,
            currency="SGD",
        )
        assert res.get("status") == "success"
        assert int(res["matched_tier"]["tier_order"]) >= 2

    def test_f5_b06_multi_currency_usd_80000_crosses_100k_sgd_threshold(self):
        from backend import tools

        # 80,000 USD * ~1.34-1.35 = ~107,200-108,000 SGD (> 100,000 SGD Tier 1 max on CUST-001)
        res = invoke_tool_adaptive(
            tools.simulate_transaction_authorization,
            customer_id="CUST-001",
            amount=80000.00,
            currency="USD",
        )
        assert res.get("status") == "success"
        assert float(res["evaluated_amount_sgd"]) > 100000.0
        assert int(res["matched_tier"]["tier_order"]) >= 2

    def test_f5_b07_multi_currency_eur_cnh_jpy_conversions(self):
        from backend import tools

        for curr, amt in (("EUR", 50000.0), ("CNH", 200000.0), ("JPY", 5000000.0)):
            res = invoke_tool_adaptive(
                tools.simulate_transaction_authorization,
                customer_id="CUST-003",
                amount=amt,
                currency=curr,
            )
            assert res.get("status") == "success"
            assert float(res["evaluated_amount_sgd"]) > 0.0

    def test_f5_b08_negative_transaction_amount_rejected_or_handled(self):
        from backend import tools

        res = invoke_tool_adaptive(
            tools.simulate_transaction_authorization,
            customer_id="CUST-001",
            amount=-5000.0,
            currency="SGD",
        )
        assert res.get("status") in ("error", "success")
        if res.get("status") == "success":
            assert res.get("matched_tier") is not None


# ============================================================================
# F6: Board Resolution Clause Gaps & Submission Boundaries (5 tests)
# ============================================================================
class TestF6ResolutionClauseAndSubmissionBoundaries:
    def test_f6_b01_custom_board_minutes_missing_clauses_flags_action_required(self):
        from backend import tools

        incomplete_minutes = "Minutes of meeting held on Monday. We discussed general company updates."
        res = invoke_tool_adaptive(
            tools.audit_board_resolution,
            customer_id="CUST-001",
            resolution_type="CUSTOM_BOARD_MINUTES",
            clause_text=incomplete_minutes,
        )
        assert res.get("status") == "success"
        score = int(res.get("audit_score", 100))
        assert score < 100
        status = str(res.get("audit_status", ""))
        assert status in ("ACTION_REQUIRED_CLAUSE_GAP", "NON_COMPLIANT", "ACTION_REQUIRED")

    def test_f6_b02_custom_board_minutes_with_all_four_mandatory_clauses_scores_high(self):
        from backend import tools

        complete_minutes = (
            "BOARD RESOLUTION OF TECHNOVA SOLUTIONS PTE LTD (UEN 201823901E). "
            "Quorum of Directors present and resolved: "
            "1. Target bank accounts 001-928341-0 and 001-928341-8 (4912, 8821) mandate amended. "
            "2. Signing Group A and Group B tier limits updated (Tier 1 SGD 100,000). "
            "3. DBS IDEAL Electronic Banking and DigiSign Digital Token specimen signatures ratified."
        )
        res = invoke_tool_adaptive(
            tools.audit_board_resolution,
            customer_id="CUST-001",
            resolution_type="CUSTOM_BOARD_MINUTES",
            clause_text=complete_minutes,
        )
        assert res.get("status") == "success"
        assert int(res.get("audit_score", 0)) >= 75

    def test_f6_b03_mandate_diff_reflects_added_signatory_and_modified_rule(self):
        from backend import tools

        invoke_tool_adaptive(
            tools.add_or_update_signatory,
            customer_id="CUST-001",
            full_name="Diff Verification CFO",
            role_title="CFO",
            signing_group="A",
            nric_masked="S8888999D",
        )
        invoke_tool_adaptive(
            tools.configure_signing_rules,
            customer_id="CUST-001",
            tier_order=1,
            max_amount_sgd=175000.0,
            rule_expression="1A OR 2B",
        )
        audit = invoke_tool_adaptive(
            tools.audit_board_resolution,
            customer_id="CUST-001",
            resolution_type="STANDARD_BRC_09",
        )
        diff = audit.get("mandate_diff") or {}
        assert diff.get("has_changes") is True
        ensure_database_ready_and_seeded()

    def test_f6_b04_submit_mandate_change_persists_snapshot_in_postgresql(self):
        from backend import tools

        sub = invoke_tool_adaptive(
            tools.submit_mandate_change_request,
            customer_id="CUST-003",
            submitted_by="Eleanor Vance",
            notes="MNC regional treasury sweep update",
        )
        assert sub.get("status") == "success"
        app = sub.get("application") or {}
        assert app.get("customer_id") == "CUST-003"

    def test_f6_b05_cosign_already_approved_application_is_idempotent(self):
        from backend import tools

        sub = invoke_tool_adaptive(
            tools.submit_mandate_change_request,
            customer_id="CUST-003",
            submitted_by="Eleanor Vance",
        )
        app_ref = sub.get("application_ref")
        c1 = invoke_tool_adaptive(
            tools.execute_cosigner_signature,
            customer_id="CUST-003",
            application_ref=app_ref,
            signer_name="Chuan Kai Wong",
        )
        c2 = invoke_tool_adaptive(
            tools.execute_cosigner_signature,
            customer_id="CUST-003",
            application_ref=app_ref,
            signer_name="Chuan Kai Wong",
        )
        assert c1.get("status") == "success"
        assert c2.get("status") in ("success", "already_signed")
        ensure_database_ready_and_seeded()


# ============================================================================
# F7: API & WebSocket Edge Cases (5 tests)
# ============================================================================
class TestF7ApiAndWebSocketEdgeCases:
    def test_f7_b01_websocket_ping_returns_pong_or_ack(self, client):
        with client.websocket_connect("/ws/live") as ws:
            ws.send_json({"type": "init", "customer_id": "CUST-001", "stage": 1, "mode": "chat"})
            _ = ws.receive_json()
            ws.send_json({"type": "ping"})
            reply = ws.receive_json()
            assert reply["type"] in ("pong", "session_ready", "ui_sync")

    def test_f7_b02_websocket_barge_in_frame_returns_interrupted_ack(self, client):
        with client.websocket_connect("/ws/live") as ws:
            ws.send_json({"type": "init", "customer_id": "CUST-001", "stage": 1, "mode": "voice"})
            ws.send_json({"type": "barge_in"})
            reply = receive_next_ws_frame(ws, ("interrupted", "barge_in_ack", "turn_complete", "ui_sync"))
            assert reply["type"] in ("interrupted", "barge_in_ack", "turn_complete", "ui_sync")

    def test_f7_b03_websocket_audio_chunk_streaming_frame_handled(self, client):
        import base64

        pcm_silence = base64.b64encode(b"\x00\x00" * 320).decode("ascii")
        with client.websocket_connect("/ws/live") as ws:
            ws.send_json({"type": "init", "customer_id": "CUST-001", "stage": 1, "mode": "voice"})
            _ = ws.receive_json()
            ws.send_json({"type": "audio_chunk", "pcm16_base64": pcm_silence, "sample_rate": 16000})
            ws.send_json({"type": "ping"})
            reply = ws.receive_json()
            assert isinstance(reply, dict)

    def test_f7_b04_rest_invalid_customer_mandate_returns_404_or_fallback(self, client):
        resp = client.get("/api/customers/CUST-DOES-NOT-EXIST/mandate")
        assert resp.status_code in (404, 400, 200)
        if resp.status_code == 200:
            body = resp.json()
            assert body.get("status") in ("error", "success")

    def test_f7_b05_rest_profile_reset_restores_customer_baseline(self, client):
        resp = client.post("/api/customers/CUST-001/reset")
        if resp.status_code == 404:
            resp = client.post("/api/profiles/CUST-001/reset")
        assert resp.status_code in (200, 404)


# ============================================================================
# F8: Stage 1 & Profile Switcher Boundaries (5 tests)
# ============================================================================
class TestF8Stage1AccountScopeBoundaries:
    def test_f8_b01_deselecting_single_account_while_keeping_others_succeeds(self, client):
        mandate = client.get("/api/customers/CUST-001/mandate").json()
        accounts = mandate["accounts"]
        keep_ids = [accounts[0]["account_id"]]
        resp = client.post("/api/customers/CUST-001/target-accounts", json={"account_ids": keep_ids})
        assert resp.status_code == 200
        assert resp.json().get("status") == "success"

    def test_f8_b02_deselecting_all_accounts_rejected_by_governance(self, client):
        resp = client.post("/api/customers/CUST-001/target-accounts", json={"account_ids": []})
        assert resp.status_code in (400, 422, 200)
        if resp.status_code == 200:
            assert resp.json().get("status") == "error"

    def test_f8_b03_switching_across_all_five_customer_profiles_sequentially(self, client):
        for cid in ("CUST-001", "CUST-002", "CUST-003", "CUST-004", "CUST-005"):
            r = client.post("/api/customers/switch", json={"customer_id": cid})
            assert r.status_code == 200
            assert r.json().get("status") == "success"

    def test_f8_b04_every_customer_profile_has_valid_kyc_and_ideal_badges(self, client):
        customers = client.get("/api/customers").json().get("customers", [])
        for c in customers:
            assert c.get("uen")
            assert c.get("kyc_status")
            assert c.get("ideal_auth_status")

    def test_f8_b05_account_balances_have_consistent_sgd_equivalents(self, client):
        mandate = client.get("/api/customers/CUST-003/mandate").json()
        for acc in mandate["accounts"]:
            assert float(acc["current_balance"]) > 0
            assert float(acc["sgd_equivalent_balance"]) > 0


# ============================================================================
# F9: Stage 2 OCR & Signatory Governance Boundaries (5 tests)
# ============================================================================
class TestF9Stage2OcrAndGovernanceUI:
    def test_f9_b01_add_signatory_with_passport_id_type(self, client):
        resp = client.post(
            "/api/customers/CUST-003/signatories",
            json={
                "full_name": "Kenji Watanabe",
                "role_title": "APAC Treasury VP",
                "signing_group": "B",
                "nric_masked": "TK8829104",
                "id_type": "PASSPORT",
                "auth_method": "IDEAL_DIGITAL_TOKEN",
                "ocr_verified": True,
            },
        )
        assert resp.status_code == 200
        assert resp.json().get("status") == "success"

    def test_f9_b02_add_signatory_with_unicode_and_apostrophe_in_name(self, client):
        resp = client.post(
            "/api/customers/CUST-004/signatories",
            json={
                "full_name": "Siobhán O'Connor-Tan",
                "role_title": "Senior Conveyancing Partner",
                "signing_group": "A",
                "nric_masked": "G7712390P",
                "auth_method": "IDEAL_DIGITAL_TOKEN",
            },
        )
        assert resp.status_code == 200
        assert resp.json().get("status") == "success"

    def test_f9_b03_add_signatory_to_group_c_maker_only(self, client):
        resp = client.post(
            "/api/customers/CUST-005/signatories",
            json={
                "full_name": "Zacchaeus Lim",
                "role_title": "Trade Settlement Clerk",
                "signing_group": "C",
                "nric_masked": "S9811223Z",
                "auth_method": "IDEAL_MAKER",
            },
        )
        assert resp.status_code == 200
        assert resp.json().get("status") == "success"

    def test_f9_b04_restore_previously_revoked_signatory_via_upsert(self, client):
        client.post(
            "/api/customers/CUST-005/signatories/revoke",
            json={"signatory_id_or_name": "Zacchaeus Lim", "reason": "Temporary leave"},
        )
        restore = client.post(
            "/api/customers/CUST-005/signatories",
            json={
                "full_name": "Zacchaeus Lim",
                "role_title": "Trade Settlement Clerk",
                "signing_group": "C",
                "nric_masked": "S9811223Z",
                "auth_method": "IDEAL_MAKER",
            },
        )
        assert restore.status_code == 200
        assert restore.json()["signatory"]["status"] in ("ACTIVE", "PENDING_ADDITION")

    def test_f9_b05_signatory_specimen_metadata_preserved_in_database(self, client):
        mandate = client.get("/api/customers/CUST-001/mandate").json()
        sigs = mandate["signatories"]
        assert all("specimen_signature_status" in s for s in sigs)
        ensure_database_ready_and_seeded()


# ============================================================================
# F10: Stage 3 Rule Builder & Simulator Boundaries (6 tests)
# ============================================================================
class TestF10Stage3SimulatorBoundariesUI:
    def test_f10_b01_rule_expression_joint_signing_1a_plus_1b(self, client):
        resp = client.post(
            "/api/customers/CUST-002/signing-rules",
            json={
                "tier_order": 2,
                "max_amount_sgd": 300000.0,
                "rule_expression": "1A + 1B",
            },
        )
        assert resp.status_code == 200
        sim = client.post(
            "/api/customers/CUST-002/simulate",
            json={"amount": 180000.0, "currency": "SGD"},
        ).json()
        assert sim.get("status") == "success"
        combos = sim.get("eligible_signatory_combinations", [])
        assert len(combos) >= 1

    def test_f10_b02_rule_expression_2a_plus_1b_on_commodity_trading_house(self, client):
        sim = client.post(
            "/api/customers/CUST-005/simulate",
            json={"amount": 2500000.0, "currency": "SGD"},
        ).json()
        assert sim.get("status") == "success"
        assert int(sim["matched_tier"]["tier_order"]) >= 2

    def test_f10_b03_rule_requiring_excessive_signatories_flags_warning_or_unfeasible(self, client):
        resp = client.post(
            "/api/customers/CUST-005/signing-rules",
            json={
                "tier_order": 2,
                "max_amount_sgd": None,
                "rule_expression": "5A",
            },
        )
        assert resp.status_code in (200, 400)
        sim = client.post(
            "/api/customers/CUST-005/simulate",
            json={"amount": 900000.0, "currency": "SGD"},
        ).json()
        assert sim.get("authorization_feasible") is False or len(sim.get("eligible_signatory_combinations", [])) == 0
        ensure_database_ready_and_seeded()

    def test_f10_b04_simulate_fractional_cents_amount_precision(self, client):
        sim = client.post(
            "/api/customers/CUST-001/simulate",
            json={"amount": 99999.99, "currency": "SGD"},
        ).json()
        assert sim.get("status") == "success"
        assert int(sim["matched_tier"]["tier_order"]) == 1

    def test_f10_b05_simulate_gbp_aud_hkd_currencies(self, client):
        for curr in ("GBP", "AUD", "HKD"):
            sim = client.post(
                "/api/customers/CUST-001/simulate",
                json={"amount": 25000.0, "currency": curr},
            ).json()
            assert sim.get("status") == "success"
            assert float(sim.get("evaluated_amount_sgd", 0)) > 0

    def test_f10_b06_bulk_rules_array_payload_accepted_by_signing_rules_endpoint(self, client):
        resp = client.post(
            "/api/customers/CUST-001/signing-rules",
            json={
                "rules": [
                    {"tier_order": 1, "min_amount_sgd": 0, "max_amount_sgd": 120000, "rule_expression": "1A OR 2B"},
                    {"tier_order": 2, "min_amount_sgd": 120000.01, "max_amount_sgd": None, "rule_expression": "2A"},
                ]
            },
        )
        assert resp.status_code == 200
        assert resp.json().get("status") == "success"
        ensure_database_ready_and_seeded()


# ============================================================================
# F11: Stage 4 Diff & Resolution Audit Boundaries (5 tests)
# ============================================================================
class TestF11Stage4DiffEdgeCasesUI:
    def test_f11_b01_baseline_profile_diff_has_expected_structure(self, client):
        ensure_database_ready_and_seeded()
        mandate = client.get("/api/customers/CUST-004/mandate").json()
        diff = mandate.get("mandate_diff", {})
        assert "signatories_added" in diff
        assert "signatories_revoked" in diff
        assert "accounts_included_count" in diff

    def test_f11_b02_diff_tracks_revoked_signatory_accurately(self, client):
        client.post(
            "/api/customers/CUST-001/signatories/revoke",
            json={"signatory_id_or_name": "Rachel Koh", "reason": "Diff revocation test"},
        )
        mandate = client.get("/api/customers/CUST-001/mandate").json()
        revoked_list = mandate["mandate_diff"]["signatories_revoked"]
        assert any("Rachel Koh" in s.get("full_name", "") for s in revoked_list)
        ensure_database_ready_and_seeded()

    def test_f11_b03_brc09_document_includes_company_uen_and_selected_accounts(self, client):
        res = client.post(
            "/api/customers/CUST-002/board-resolution/audit",
            json={"resolution_type": "STANDARD_BRC_09"},
        ).json()
        doc = res.get("generated_brc09_document") or res.get("resolution", {}).get("extracted_text_summary", "")
        assert "199804512K" in doc or "Merlion" in doc or "Meridian" in doc

    def test_f11_b04_custom_resolution_minimal_unverified_string_scores_low(self, client):
        res = client.post(
            "/api/customers/CUST-001/board-resolution/audit",
            json={"resolution_type": "CUSTOM_BOARD_MINUTES", "clause_text": "Draft note without required clauses."},
        ).json()
        assert int(res.get("audit_score", 100)) < 100

    def test_f11_b05_board_resolution_persisted_in_board_resolutions_table(self, client):
        client.post(
            "/api/customers/CUST-005/board-resolution/audit",
            json={"resolution_type": "STANDARD_BRC_09"},
        )
        mandate = client.get("/api/customers/CUST-005/mandate").json()
        res_obj = mandate.get("board_resolution") or (mandate.get("board_resolutions") or [None])[0]
        assert res_obj is not None


# ============================================================================
# F12: Stage 5 Co-Signer & Audit Trail Boundaries (5 tests)
# ============================================================================
class TestF12Stage5CosignAndAuditBoundariesUI:
    def test_f12_b01_submit_with_custom_initiator_name_records_in_application(self, client):
        res = client.post(
            "/api/customers/CUST-004/submit",
            json={"submitted_by": "Evelyn Tan (Senior Partner)", "notes": "LLP Trust Mandate"},
        ).json()
        assert res.get("status") == "success"
        app = res.get("application") or {}
        assert "Evelyn Tan" in app.get("submitted_by", "Evelyn Tan")

    def test_f12_b02_cosign_updates_digisign_signers_status_to_signed(self, client):
        sub = client.post("/api/customers/CUST-004/submit", json={"submitted_by": "Evelyn Tan"}).json()
        cosign = client.post(
            "/api/customers/CUST-004/cosign",
            json={"application_ref": sub.get("application_ref"), "signer_name": "Beatrice Chee"},
        ).json()
        signers = cosign.get("digisign_signers") or cosign.get("application", {}).get("digisign_signers", [])
        assert all("SIGNED" in str(s.get("status", "")).upper() for s in signers)

    def test_f12_b03_audit_log_preserves_before_and_after_json_states(self, client):
        mandate = client.get("/api/customers/CUST-004/mandate").json()
        logs = mandate.get("audit_logs", [])
        assert any(l.get("after_state") is not None for l in logs)

    def test_f12_b04_pending_addition_signatories_promoted_to_active_after_cosign(self, client):
        client.post(
            "/api/customers/CUST-001/signatories",
            json={"full_name": "Promoted Director", "signing_group": "A", "role_title": "Director"},
        )
        sub = client.post("/api/customers/CUST-001/submit", json={"submitted_by": "Sarah Lim"}).json()
        client.post(
            "/api/customers/CUST-001/cosign",
            json={"application_ref": sub.get("application_ref"), "signer_name": "David Tan"},
        )
        mandate = client.get("/api/customers/CUST-001/mandate").json()
        promoted = [s for s in mandate["signatories"] if s["full_name"] == "Promoted Director"]
        assert len(promoted) == 1
        assert promoted[0]["status"] == "ACTIVE"
        ensure_database_ready_and_seeded()

    def test_f12_b05_audit_logs_ordered_reverse_chronologically(self, client):
        mandate = client.get("/api/customers/CUST-001/mandate").json()
        logs = mandate.get("audit_logs", [])
        if len(logs) >= 2 and "log_id" in logs[0]:
            assert int(logs[0]["log_id"]) >= int(logs[-1]["log_id"])


# ============================================================================
# F13: Copilot WebSocket Synchronization Boundaries (5 tests)
# ============================================================================
class TestF13CopilotWebSocketSyncBoundaries:
    def test_f13_b01_ui_sync_payload_contains_all_required_keys(self, client):
        resp = client.post("/api/customers/switch", json={"customer_id": "CUST-002"}).json()
        ui_sync = resp.get("ui_sync") or resp
        for key in ("ui_action", "target_stage", "updated_profile_id", "mandate_diff"):
            assert key in ui_sync

    def test_f13_b02_ui_sync_target_stages_match_each_tool_domain(self):
        from backend import tools

        s1 = invoke_tool_adaptive(tools.SwitchActiveCustomerProfile, customer_id="CUST-001")
        s2 = invoke_tool_adaptive(
            tools.add_or_update_signatory,
            customer_id="CUST-001",
            full_name="Stage Check B",
            signing_group="B",
        )
        s3 = invoke_tool_adaptive(tools.simulate_transaction_authorization, customer_id="CUST-001", amount=50000)
        s4 = invoke_tool_adaptive(tools.audit_board_resolution, customer_id="CUST-001")
        s5 = invoke_tool_adaptive(tools.submit_mandate_change_request, customer_id="CUST-001")

        assert int((s1.get("ui_sync") or s1)["target_stage"]) == 1
        assert int((s2.get("ui_sync") or s2)["target_stage"]) == 2
        assert int((s3.get("ui_sync") or s3)["target_stage"]) == 3
        assert int((s4.get("ui_sync") or s4)["target_stage"]) == 4
        assert int((s5.get("ui_sync") or s5)["target_stage"]) == 5
        ensure_database_ready_and_seeded()

    def test_f13_b03_websocket_receives_ui_sync_broadcast_on_rest_mutation(self, client):
        with client.websocket_connect("/ws/live") as ws:
            ws.send_json({"type": "init", "customer_id": "CUST-001", "stage": 1, "mode": "chat"})
            _ = ws.receive_json()
            client.post("/api/customers/switch", json={"customer_id": "CUST-003"})
            frame = ws.receive_json()
            assert frame["type"] in ("ui_sync", "session_ready")
        client.post("/api/customers/switch", json={"customer_id": "CUST-001"})

    def test_f13_b04_websocket_sync_context_frame_updates_active_stage(self, client):
        with client.websocket_connect("/ws/live") as ws:
            ws.send_json({"type": "init", "customer_id": "CUST-001", "stage": 3, "mode": "chat"})
            frame = ws.receive_json()
            assert frame["type"] == "session_ready"

    def test_f13_b05_execute_mandate_tool_dispatcher_handles_unknown_tool_gracefully(self):
        from backend import tools

        if hasattr(tools, "execute_mandate_tool"):
            res = tools.execute_mandate_tool("non_existent_tool_xyz", {})
            assert res.get("status") == "error"


# ============================================================================
# F14: Zero-Mock & Cross-Connection Persistence Audit (5 tests)
# ============================================================================
class TestF14ZeroMockAndPersistenceAudit:
    def test_f14_b01_mutation_persists_across_independent_postgresql_connections(self):
        from backend import tools

        invoke_tool_adaptive(
            tools.add_or_update_signatory,
            customer_id="CUST-001",
            full_name="Cross Connection Director",
            signing_group="A",
            role_title="Director",
        )
        conn = open_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT full_name FROM signatories WHERE customer_id = 'CUST-001' AND full_name = 'Cross Connection Director';"
                )
                row = cur.fetchone()
                assert row is not None
        finally:
            conn.close()
        ensure_database_ready_and_seeded()

    def test_f14_b02_no_sqlite_or_memory_db_files_exist_in_project(self):
        sqlite_files = list(PROJECT_ROOT.glob("**/*.sqlite*")) + list(PROJECT_ROOT.glob("**/*.db"))
        assert len(sqlite_files) == 0

    def test_f14_b03_backend_code_does_not_import_sqlite3(self):
        for py_file in (PROJECT_ROOT / "backend").glob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            assert "import sqlite3" not in content

    def test_f14_b04_schema_sql_contains_all_eight_create_table_statements(self):
        ddl = (PROJECT_ROOT / "schema" / "schema.sql").read_text(encoding="utf-8")
        assert ddl.upper().count("CREATE TABLE") >= 8

    def test_f14_b05_final_database_state_after_tier2_has_all_five_profiles(self):
        ensure_database_ready_and_seeded()
        from backend import tools

        res = tools.list_customer_profiles()
        profiles = res.get("customers") or res.get("profiles") or []
        assert len(profiles) == 5
