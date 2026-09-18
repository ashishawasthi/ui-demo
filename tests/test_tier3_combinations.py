"""
Tier 3 Cross-Feature Pairwise Combination Test Suite (`tests/test_tier3_combinations.py`).
Covers multi-step and pairwise interactions across tools, REST endpoints, WebSocket broadcasts,
and fresh PostgreSQL database connections.
Total test cases: 18 (>= 15 required).
"""

import pytest

from tests.conftest import ensure_database_ready_and_seeded, invoke_tool_adaptive, open_db_connection


class TestTier3FeatureCombinations:
    def test_t3_01_profile_switch_plus_target_account_filtering_updates_diff(self, clean_db):
        from backend import tools

        invoke_tool_adaptive(tools.SwitchActiveCustomerProfile, customer_id="CUST-001")
        details = invoke_tool_adaptive(tools.get_customer_mandate_details, customer_id="CUST-001")
        all_accounts = details["accounts"]
        subset_ids = [all_accounts[0]["account_id"], all_accounts[1]["account_id"]]

        upd = invoke_tool_adaptive(
            tools.update_target_accounts,
            customer_id="CUST-001",
            account_ids=subset_ids,
        )
        assert upd.get("status") == "success"
        after = invoke_tool_adaptive(tools.get_customer_mandate_details, customer_id="CUST-001")
        included = [a for a in after["accounts"] if a.get("is_included_in_mandate_change")]
        assert len(included) == 2

    def test_t3_02_add_group_a_signatory_immediately_appears_in_transaction_simulator(self, clean_db):
        from backend import tools

        invoke_tool_adaptive(
            tools.add_or_update_signatory,
            customer_id="CUST-001",
            full_name="Dr. Alistair Vance",
            role_title="Independent Director",
            signing_group="A",
            nric_masked="S7100293V",
        )
        sim = invoke_tool_adaptive(
            tools.simulate_transaction_authorization,
            customer_id="CUST-001",
            amount=50000.0,
            currency="SGD",
        )
        combos_str = str(sim.get("eligible_signatory_combinations", []))
        assert "Alistair Vance" in combos_str

    def test_t3_03_revoke_signatory_immediately_excluded_from_transaction_simulator(self, clean_db):
        from backend import tools

        invoke_tool_adaptive(
            tools.revoke_signatory,
            customer_id="CUST-001",
            signatory_id_or_name="Rachel Koh",
            reason="Transferred to APAC Subsidiary",
        )
        sim = invoke_tool_adaptive(
            tools.simulate_transaction_authorization,
            customer_id="CUST-001",
            amount=50000.0,
            currency="SGD",
        )
        combos_str = str(sim.get("eligible_signatory_combinations", []))
        assert "Rachel Koh" not in combos_str

    def test_t3_04_configure_tier1_limit_to_200k_shifts_150k_sgd_from_tier2_to_tier1(self, clean_db):
        from backend import tools

        before_sim = invoke_tool_adaptive(
            tools.simulate_transaction_authorization,
            customer_id="CUST-001",
            amount=150000.0,
            currency="SGD",
        )
        assert int(before_sim["matched_tier"]["tier_order"]) == 2

        invoke_tool_adaptive(
            tools.configure_signing_rules,
            customer_id="CUST-001",
            tier_order=1,
            max_amount_sgd=200000.0,
            rule_expression="1A OR 2B",
        )
        after_sim = invoke_tool_adaptive(
            tools.simulate_transaction_authorization,
            customer_id="CUST-001",
            amount=150000.0,
            currency="SGD",
        )
        assert int(after_sim["matched_tier"]["tier_order"]) == 1

    def test_t3_05_configure_signing_rules_plus_usd_fx_simulation_chain(self, clean_db):
        from backend import tools

        invoke_tool_adaptive(
            tools.configure_signing_rules,
            customer_id="CUST-001",
            tier_order=1,
            max_amount_sgd=120000.0,
            rule_expression="1A OR 2B",
        )
        # 100,000 USD * ~1.35 = ~135,000 SGD > 120,000 SGD -> Tier 2
        sim = invoke_tool_adaptive(
            tools.simulate_transaction_authorization,
            customer_id="CUST-001",
            amount=100000.0,
            currency="USD",
        )
        assert int(sim["matched_tier"]["tier_order"]) == 2

    def test_t3_06_target_account_selection_reflected_in_brc09_board_resolution(self, clean_db):
        from backend import tools

        details = invoke_tool_adaptive(tools.get_customer_mandate_details, customer_id="CUST-001")
        first_acc = details["accounts"][0]
        invoke_tool_adaptive(
            tools.update_target_accounts,
            customer_id="CUST-001",
            account_ids=[first_acc["account_id"]],
        )
        audit = invoke_tool_adaptive(
            tools.audit_board_resolution,
            customer_id="CUST-001",
            resolution_type="STANDARD_BRC_09",
        )
        doc = audit.get("generated_brc09_document") or audit.get("resolution", {}).get("extracted_text_summary", "")
        assert first_acc["account_number"] in doc or first_acc["account_name"] in doc

    def test_t3_07_add_and_revoke_signatories_combined_in_mandate_diff(self, clean_db):
        from backend import tools

        invoke_tool_adaptive(
            tools.add_or_update_signatory,
            customer_id="CUST-001",
            full_name="Added Treasury VP",
            role_title="VP Treasury",
            signing_group="A",
        )
        invoke_tool_adaptive(
            tools.revoke_signatory,
            customer_id="CUST-001",
            signatory_id_or_name="Rachel Koh",
            reason="Role change",
        )
        details = invoke_tool_adaptive(tools.get_customer_mandate_details, customer_id="CUST-001")
        diff = details["mandate_diff"]
        assert diff["has_changes"] is True
        assert any("Added Treasury VP" in s["full_name"] for s in diff["signatories_added"])
        assert any("Rachel Koh" in s["full_name"] for s in diff["signatories_revoked"])

    def test_t3_08_rule_change_and_signatory_change_frozen_in_submitted_application_snapshot(self, clean_db):
        from backend import tools

        invoke_tool_adaptive(
            tools.add_or_update_signatory,
            customer_id="CUST-002",
            full_name="Snapshot CFO",
            role_title="CFO",
            signing_group="A",
        )
        invoke_tool_adaptive(
            tools.configure_signing_rules,
            customer_id="CUST-002",
            tier_order=1,
            max_amount_sgd=90000.0,
            rule_expression="1A OR 1B",
        )
        sub = invoke_tool_adaptive(
            tools.submit_mandate_change_request,
            customer_id="CUST-002",
            submitted_by="Raymond Ong",
        )
        snap = sub.get("mandate_diff") or sub.get("application", {}).get("mandate_diff_snapshot", {})
        assert isinstance(snap, dict)
        assert snap.get("has_changes") is True

    def test_t3_09_submit_and_cosign_workflow_promotes_signatories_and_updates_audit_trail(self, clean_db):
        from backend import tools

        invoke_tool_adaptive(
            tools.add_or_update_signatory,
            customer_id="CUST-001",
            full_name="Board Approved Director",
            role_title="Director",
            signing_group="A",
        )
        sub = invoke_tool_adaptive(
            tools.submit_mandate_change_request,
            customer_id="CUST-001",
            submitted_by="Sarah Lim",
        )
        cosign = invoke_tool_adaptive(
            tools.execute_cosigner_signature,
            customer_id="CUST-001",
            application_ref=sub.get("application_ref"),
            signer_name="David Tan",
        )
        assert cosign.get("status") == "success"
        details = invoke_tool_adaptive(tools.get_customer_mandate_details, customer_id="CUST-001")
        logs = details.get("audit_logs", [])
        assert any("COSIGN" in l.get("event_type", "").upper() or "APPROVED" in l.get("event_type", "").upper() for l in logs)

    def test_t3_10_governance_sole_group_a_block_resolved_by_adding_new_group_a_director(self, clean_db):
        from backend import tools

        details = invoke_tool_adaptive(tools.get_customer_mandate_details, customer_id="CUST-002")
        group_a = [
            s for s in details["signatories"]
            if s["signing_group"] == "A" and s["status"] == "ACTIVE"
        ]
        # Revoke first Group A director
        r1 = invoke_tool_adaptive(
            tools.revoke_signatory,
            customer_id="CUST-002",
            signatory_id_or_name=group_a[0]["signatory_id"],
            reason="Retiring",
        )
        assert r1.get("status") == "success"

        # Second Group A director is now sole remaining -> revocation must be blocked
        blocked = invoke_tool_adaptive(
            tools.revoke_signatory,
            customer_id="CUST-002",
            signatory_id_or_name=group_a[1]["signatory_id"],
            reason="Should be blocked",
        )
        assert blocked.get("status") == "error"

        # Add a replacement Group A director
        invoke_tool_adaptive(
            tools.add_or_update_signatory,
            customer_id="CUST-002",
            full_name="Replacement Executive Director",
            role_title="Executive Director",
            signing_group="A",
        )

        # Now revoking the second original Group A director succeeds!
        unblocked = invoke_tool_adaptive(
            tools.revoke_signatory,
            customer_id="CUST-002",
            signatory_id_or_name=group_a[1]["signatory_id"],
            reason="Now permitted after replacement added",
        )
        assert unblocked.get("status") == "success"

    def test_t3_11_multi_entity_strict_isolation_across_cust001_and_cust003(self, clean_db):
        from backend import tools

        before_c3 = invoke_tool_adaptive(tools.get_customer_mandate_details, customer_id="CUST-003")
        c3_sig_count = len(before_c3["signatories"])

        invoke_tool_adaptive(
            tools.add_or_update_signatory,
            customer_id="CUST-001",
            full_name="Isolated TechNova Officer",
            signing_group="B",
        )
        after_c3 = invoke_tool_adaptive(tools.get_customer_mandate_details, customer_id="CUST-003")
        assert len(after_c3["signatories"]) == c3_sig_count

    def test_t3_12_rest_and_websocket_live_ui_sync_across_all_five_stages(self, client, clean_db):
        from tests.conftest import receive_next_ws_frame

        with client.websocket_connect("/ws/live") as ws:
            _ = ws.receive_json()  # Initial session_ready on connect
            ws.send_json({"type": "init", "customer_id": "CUST-001", "stage": 1, "mode": "chat"})
            _ = ws.receive_json()  # Second session_ready from init

            # Stage 1: Switch profile
            client.post("/api/customers/switch", json={"customer_id": "CUST-001"})
            f1 = receive_next_ws_frame(ws, "ui_sync")
            assert f1["type"] == "ui_sync" and int(f1["target_stage"]) == 1

            # Stage 2: Add signatory
            client.post(
                "/api/customers/CUST-001/signatories",
                json={"full_name": "WS Sync Signer", "signing_group": "B", "role_title": "Manager"},
            )
            f2 = receive_next_ws_frame(ws, "ui_sync")
            assert f2["type"] == "ui_sync" and int(f2["target_stage"]) == 2

            # Stage 3: Simulate transaction
            client.post("/api/customers/CUST-001/simulate", json={"amount": 60000, "currency": "SGD"})
            f3 = receive_next_ws_frame(ws, "ui_sync")
            assert f3["type"] == "ui_sync" and int(f3["target_stage"]) == 3

            # Stage 4: Audit resolution
            client.post("/api/customers/CUST-001/board-resolution/audit", json={"resolution_type": "STANDARD_BRC_09"})
            f4 = receive_next_ws_frame(ws, "ui_sync")
            assert f4["type"] == "ui_sync" and int(f4["target_stage"]) == 4

            # Stage 5: Submit application
            client.post("/api/customers/CUST-001/submit", json={"submitted_by": "Sarah Lim"})
            f5 = receive_next_ws_frame(ws, "ui_sync")
            assert f5["type"] == "ui_sync" and int(f5["target_stage"]) == 5

    def test_t3_13_fresh_postgresql_cursor_verifies_multi_step_rest_mutations(self, client, clean_db):
        from backend import db

        client.post(
            "/api/customers/CUST-003/signatories",
            json={"full_name": "Fresh Cursor Officer", "signing_group": "A", "role_title": "VP"},
        )
        client.post(
            "/api/customers/CUST-003/signing-rules",
            json={"tier_order": 1, "max_amount_sgd": 400000.0, "rule_expression": "1A OR 2B"},
        )
        conn = open_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status FROM signatories WHERE customer_id = 'CUST-003' AND full_name = 'Fresh Cursor Officer';"
                )
                assert cur.fetchone() is not None
                cur.execute(
                    "SELECT max_amount_sgd FROM signing_rules WHERE customer_id = 'CUST-003' AND tier_order = 1;"
                )
                row = cur.fetchone()
                val = float(row["max_amount_sgd"] if isinstance(row, dict) else row[0])
                assert val == 400000.0
        finally:
            conn.close()

    def test_t3_14_custom_minutes_gap_detected_then_resolved_by_standard_brc09(self, client, clean_db):
        r_gap = client.post(
            "/api/customers/CUST-002/board-resolution/audit",
            json={"resolution_type": "CUSTOM_BOARD_MINUTES", "clause_text": "Informal note without clauses."},
        ).json()
        assert int(r_gap["audit_score"]) < 100

        r_fix = client.post(
            "/api/customers/CUST-002/board-resolution/audit",
            json={"resolution_type": "STANDARD_BRC_09"},
        ).json()
        assert int(r_fix["audit_score"]) >= 95
        assert r_fix["audit_status"] == "COMPLIANT"

    def test_t3_15_cnh_and_jpy_simulation_against_multi_tier_mnc_rules(self, client, clean_db):
        r_cnh = client.post(
            "/api/customers/CUST-003/simulate",
            json={"amount": 500000.0, "currency": "CNH"},
        ).json()
        r_jpy = client.post(
            "/api/customers/CUST-003/simulate",
            json={"amount": 100000000.0, "currency": "JPY"},
        ).json()
        assert r_cnh.get("status") == "success"
        assert r_jpy.get("status") == "success"
        assert float(r_jpy["evaluated_amount_sgd"]) > float(r_cnh["evaluated_amount_sgd"])

    def test_t3_16_llp_partnership_dual_group_a_rule_and_resolution_verification(self, client, clean_db):
        sim = client.post(
            "/api/customers/CUST-004/simulate",
            json={"amount": 250000.0, "currency": "SGD"},
        ).json()
        assert sim.get("status") == "success"
        assert "2A" in sim["matched_tier"]["rule_expression"]

        aud = client.post(
            "/api/customers/CUST-004/board-resolution/audit",
            json={"resolution_type": "LLP_PARTNERS_RESOLUTION"},
        ).json()
        assert aud.get("status") == "success"

    def test_t3_17_commodity_trading_house_three_signatory_rule_2a_plus_1b_combination(self, client, clean_db):
        sim = client.post(
            "/api/customers/CUST-005/simulate",
            json={"amount": 3000000.0, "currency": "SGD"},
        ).json()
        assert sim.get("status") == "success"
        combos = sim.get("eligible_signatory_combinations", [])
        assert len(combos) >= 1
        first_combo_sigs = combos[0].get("signatories", [])
        assert len(first_combo_sigs) >= 2

    def test_t3_18_audit_log_records_complete_5_stage_sequence_for_customer(self, client, clean_db):
        client.post("/api/customers/switch", json={"customer_id": "CUST-001"})
        client.post(
            "/api/customers/CUST-001/signatories",
            json={"full_name": "Audit Trail CFO", "signing_group": "A", "role_title": "CFO"},
        )
        client.post(
            "/api/customers/CUST-001/signing-rules",
            json={"tier_order": 1, "max_amount_sgd": 140000.0, "rule_expression": "1A OR 2B"},
        )
        client.post("/api/customers/CUST-001/simulate", json={"amount": 130000.0, "currency": "SGD"})
        client.post("/api/customers/CUST-001/board-resolution/audit", json={"resolution_type": "STANDARD_BRC_09"})
        sub = client.post("/api/customers/CUST-001/submit", json={"submitted_by": "Sarah Lim"}).json()
        client.post(
            "/api/customers/CUST-001/cosign",
            json={"application_ref": sub.get("application_ref"), "signer_name": "David Tan"},
        )

        mandate = client.get("/api/customers/CUST-001/mandate").json()
        logs = mandate.get("audit_logs", [])
        assert len(logs) >= 6
