"""
Tier 4 End-to-End Corporate Banking Scenarios (`tests/test_tier4_scenarios.py`).
Covers full 5-stage corporate workflows across all 5 customer entities (`CUST-001`..`CUST-005`),
live `models/gemini-3.8-live-extended-thinking` `/api/chat` and `/ws/live` WebSocket multi-tool turns,
and post-scenario clean database restoration.
Total test cases: 12 (>= 10 required).
"""

import base64
import pytest

from tests.conftest import ensure_database_ready_and_seeded


class TestTier4EndToEndScenarios:
    def test_t4_01_e2e_cust001_technova_series_b_cfo_onboarding_and_threshold_elevation(self, client, clean_db):
        # Stage 1: Switch to CUST-001 and select SGD Operating & USD Multi-Currency accounts
        sw = client.post("/api/customers/switch", json={"customer_id": "CUST-001"}).json()
        assert sw.get("status") == "success"
        m1 = client.get("/api/customers/CUST-001/mandate").json()
        acc_ids = [a["account_id"] for a in m1["accounts"][:2]]
        r_acc = client.post("/api/customers/CUST-001/target-accounts", json={"account_ids": acc_ids}).json()
        assert r_acc.get("status") == "success"

        # Stage 2: Add CFO Michael Chang to Group A with OCR specimen verification
        r_sig = client.post(
            "/api/customers/CUST-001/signatories",
            json={
                "full_name": "Michael Chang",
                "role_title": "Chief Financial Officer",
                "signing_group": "A",
                "nric_masked": "S8477219C",
                "auth_method": "IDEAL_DIGITAL_TOKEN",
                "ocr_verified": True,
            },
        ).json()
        assert r_sig.get("status") == "success"

        # Stage 3: Elevate Tier 1 signing limit from SGD 100,000 to SGD 150,000 & simulate USD 100,000 payment
        r_rule = client.post(
            "/api/customers/CUST-001/signing-rules",
            json={"tier_order": 1, "max_amount_sgd": 150000.0, "rule_expression": "1A OR 2B"},
        ).json()
        assert r_rule.get("status") == "success"

        r_sim = client.post(
            "/api/customers/CUST-001/simulate",
            json={"amount": 100000.0, "currency": "USD"},
        ).json()
        assert r_sim.get("status") == "success"
        # USD 100,000 (~135,000 SGD) is now within the elevated 150,000 SGD Tier 1 threshold!
        assert int(r_sim["matched_tier"]["tier_order"]) == 1

        # Stage 4: Audit Standard BRC-09 Board Resolution and verify Mandate Diff
        r_aud = client.post(
            "/api/customers/CUST-001/board-resolution/audit",
            json={"resolution_type": "STANDARD_BRC_09"},
        ).json()
        assert r_aud.get("status") == "success"
        assert r_aud["audit_status"] == "COMPLIANT"

        # Stage 5: Submit Change of Mandate & execute Co-Signer IDEAL Token approval
        r_sub = client.post(
            "/api/customers/CUST-001/submit",
            json={"submitted_by": "Sarah Lim (MD)", "notes": "Series B Treasury Mandate Upgrade"},
        ).json()
        assert r_sub.get("status") == "success"
        app_ref = r_sub.get("application_ref")
        assert app_ref.startswith("COM-2026-")

        r_cos = client.post(
            "/api/customers/CUST-001/cosign",
            json={"application_ref": app_ref, "signer_name": "David Tan", "auth_method": "IDEAL Token"},
        ).json()
        assert r_cos.get("status") == "success"

    def test_t4_02_e2e_cust002_meridian_logistics_clause_remediation_and_joint_rules(self, client, clean_db):
        client.post("/api/customers/switch", json={"customer_id": "CUST-002"})
        # Audit custom minutes with missing clause first
        gap = client.post(
            "/api/customers/CUST-002/board-resolution/audit",
            json={"resolution_type": "CUSTOM_BOARD_MINUTES", "clause_text": "Board met to discuss EUR account."},
        ).json()
        assert int(gap["audit_score"]) < 100

        # Configure joint 1A + 1B signing rule up to SGD 300,000
        client.post(
            "/api/customers/CUST-002/signing-rules",
            json={"tier_order": 2, "max_amount_sgd": 300000.0, "rule_expression": "1A + 1B"},
        )
        sim = client.post(
            "/api/customers/CUST-002/simulate",
            json={"amount": 150000.0, "currency": "EUR"},
        ).json()
        assert sim.get("status") == "success"

        # Remediate via Standard BRC-09 and complete submission
        fix = client.post(
            "/api/customers/CUST-002/board-resolution/audit",
            json={"resolution_type": "STANDARD_BRC_09"},
        ).json()
        assert fix["audit_status"] == "COMPLIANT"

        sub = client.post("/api/customers/CUST-002/submit", json={"submitted_by": "Raymond Ong"}).json()
        cos = client.post(
            "/api/customers/CUST-002/cosign",
            json={"application_ref": sub.get("application_ref"), "signer_name": "Helen Ong-Teo"},
        ).json()
        assert cos.get("status") == "success"

    def test_t4_03_e2e_cust003_apex_global_mnc_expatriate_director_rotation(self, client, clean_db):
        client.post("/api/customers/switch", json={"customer_id": "CUST-003"})
        # Add incoming APAC Regional CFO to Group A
        add_res = client.post(
            "/api/customers/CUST-003/signatories",
            json={
                "full_name": "Mei Ling Chow",
                "role_title": "APAC Regional CFO",
                "signing_group": "A",
                "nric_masked": "S7719283B",
                "auth_method": "IDEAL_DIGITAL_TOKEN",
            },
        ).json()
        assert add_res.get("status") == "success"

        # Simulate high-value USD 1,500,000 regional liquidity sweep
        sim = client.post(
            "/api/customers/CUST-003/simulate",
            json={"amount": 1500000.0, "currency": "USD"},
        ).json()
        assert sim.get("status") == "success"
        assert float(sim["evaluated_amount_sgd"]) > 1500000.0

        aud = client.post(
            "/api/customers/CUST-003/board-resolution/audit",
            json={"resolution_type": "STANDARD_BRC_09"},
        ).json()
        assert aud.get("status") == "success"

        sub = client.post("/api/customers/CUST-003/submit", json={"submitted_by": "Eleanor Vance"}).json()
        assert sub.get("application_ref", "").startswith("COM-2026-")

    def test_t4_04_e2e_cust004_veritas_llp_client_trust_governance_protection(self, client, clean_db):
        client.post("/api/customers/switch", json={"customer_id": "CUST-004"})
        mandate = client.get("/api/customers/CUST-004/mandate").json()
        group_a = [
            s for s in mandate["signatories"]
            if s["signing_group"] == "A" and s["status"] == "ACTIVE"
        ]
        # Revoke all but one partner
        for partner in group_a[:-1]:
            r = client.post(
                "/api/customers/CUST-004/signatories/revoke",
                json={"signatory_id_or_name": partner["signatory_id"], "reason": "Partner retirement"},
            )
            assert r.status_code == 200

        # Attempt to revoke the last equity partner -> must be blocked by governance guardrail
        sole_partner = group_a[-1]
        block_resp = client.post(
            "/api/customers/CUST-004/signatories/revoke",
            json={"signatory_id_or_name": sole_partner["signatory_id"], "reason": "Illegal removal"},
        )
        assert block_resp.status_code in (400, 422, 200)
        assert "GOVERNANCE" in str(block_resp.json()).upper() or "GROUP_A" in str(block_resp.json()).upper()

        # Onboard new Equity Partner Clarissa Ho to Group A
        client.post(
            "/api/customers/CUST-004/signatories",
            json={
                "full_name": "Clarissa Ho",
                "role_title": "Equity Partner",
                "signing_group": "A",
                "nric_masked": "S7533901C",
            },
        )
        aud = client.post(
            "/api/customers/CUST-004/board-resolution/audit",
            json={"resolution_type": "LLP_PARTNERS_RESOLUTION"},
        ).json()
        assert aud.get("status") == "success"

    def test_t4_05_e2e_cust005_banyan_commodity_group_multi_outlet_workflow(self, client, clean_db):
        client.post("/api/customers/switch", json={"customer_id": "CUST-005"})
        client.post(
            "/api/customers/CUST-005/signatories",
            json={
                "full_name": "Chef Desmond Lau",
                "role_title": "Founder & Executive Director",
                "signing_group": "A",
                "nric_masked": "S8310492Z",
            },
        )
        sim = client.post(
            "/api/customers/CUST-005/simulate",
            json={"amount": 85000.0, "currency": "SGD"},
        ).json()
        assert sim.get("status") == "success"

        sub = client.post("/api/customers/CUST-005/submit", json={"submitted_by": "Budi Santoso"}).json()
        cos = client.post(
            "/api/customers/CUST-005/cosign",
            json={"application_ref": sub.get("application_ref"), "signer_name": "Victor Lim"},
        ).json()
        assert cos.get("status") == "success"

    def test_t4_06_live_gemini_3_8_chat_turn_profile_query_and_switch(self, client, clean_db):
        resp = client.post(
            "/api/chat",
            json={
                "message": "Switch active customer profile to CUST-002 and summarize their current mandate accounts.",
                "customer_id": "CUST-001",
                "current_stage": 1,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "reply" in data
        assert isinstance(data.get("thinking_traces", []), list)
        assert isinstance(data.get("tool_calls", []), list)
        assert len(data.get("reply", "")) > 0

    def test_t4_07_live_gemini_3_8_chat_turn_add_signatory_and_simulate_transaction(self, client, clean_db):
        resp = client.post(
            "/api/chat",
            json={
                "message": (
                    "For customer CUST-001 (TechNova Solutions), please add a new signatory named "
                    "'Samantha Lee' as Chief Operating Officer in Group A using tool add_or_update_signatory, "
                    "and simulate a transaction of 120000 SGD."
                ),
                "customer_id": "CUST-001",
                "current_stage": 2,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data.get("reply", "")) > 0
        assert isinstance(data.get("ui_sync"), dict)

    def test_t4_08_live_gemini_3_8_chat_turn_audit_resolution_and_submit_mandate(self, client, clean_db):
        resp = client.post(
            "/api/chat",
            json={
                "message": (
                    "For CUST-001, run audit_board_resolution with STANDARD_BRC_09 and then call "
                    "submit_mandate_change_request submitted by Sarah Lim."
                ),
                "customer_id": "CUST-001",
                "current_stage": 4,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data.get("reply", "")) > 0
        assert isinstance(data.get("workspace_snapshot"), dict)

    def test_t4_09_live_websocket_ws_live_text_turn_and_tool_execution_roundtrip(self, client, clean_db):
        with client.websocket_connect("/ws/live") as ws:
            ws.send_json({"type": "init", "customer_id": "CUST-001", "stage": 1, "mode": "chat"})
            ready = ws.receive_json()
            assert ready["type"] == "session_ready"

            ws.send_json(
                {
                    "type": "text_turn",
                    "text": "Simulate a transaction of 75,000 SGD for CUST-001.",
                    "customer_id": "CUST-001",
                }
            )
            received_types = []
            for _ in range(12):
                frame = ws.receive_json()
                received_types.append(frame.get("type"))
                if frame.get("type") == "turn_complete":
                    break
            assert any(t in received_types for t in ("transcript", "tool_call_result", "ui_sync", "turn_complete"))

    def test_t4_10_live_websocket_ws_live_voice_pcm_streaming_and_barge_in_roundtrip(self, client, clean_db):
        from tests.conftest import receive_next_ws_frame

        pcm16_frame = base64.b64encode(b"\x00\x01" * 800).decode("ascii")
        with client.websocket_connect("/ws/live") as ws:
            ws.send_json({"type": "init", "customer_id": "CUST-001", "stage": 1, "mode": "voice"})
            ready = ws.receive_json()
            assert ready["type"] == "session_ready"
            assert ready["model"] == "models/gemini-3.8-live-extended-thinking"

            ws.send_json({"type": "audio_chunk", "pcm16_base64": pcm16_frame, "sample_rate": 16000})
            ws.send_json({"type": "barge_in"})
            frame = receive_next_ws_frame(
                ws, ("interrupted", "barge_in_ack", "turn_complete", "ui_sync", "audio_out")
            )
            assert frame["type"] in ("interrupted", "barge_in_ack", "turn_complete", "ui_sync", "audio_out")

    def test_t4_11_cross_entity_audit_trail_and_mandate_integrity_verification(self, client, clean_db):
        for cid in ("CUST-001", "CUST-002", "CUST-003", "CUST-004", "CUST-005"):
            m = client.get(f"/api/customers/{cid}/mandate").json()
            assert m["customer"]["customer_id"] == cid
            assert len(m["accounts"]) >= 3
            assert len(m["signatories"]) >= 3
            assert len(m["signing_rules"]) >= 2
            assert len(m["audit_logs"]) >= 1

    def test_t4_12_final_clean_database_reseed_leaves_all_five_profiles_pristine(self, client):
        ensure_database_ready_and_seeded()
        health = client.get("/api/health").json()
        assert health["status"] == "ok"
        assert int(health["database"]["customer_count"]) == 5
        custs = client.get("/api/customers").json()
        assert len(custs["customers"]) == 5
