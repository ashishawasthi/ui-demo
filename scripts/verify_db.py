#!/usr/bin/env python3
"""Programmatic Verification Script for Corporate Banking Mandate Database, Tools & Gemini Live Backend.

Verifies:
1. PostgreSQL 18.6 connection & 8-table relational schema (`corporate_mandate_db`)
2. Seed dataset completeness (5 corporate profiles, 17 bank accounts, 23 signatories, 13 signing rules, 5 board resolutions, 5 mandate applications, 15+ audit logs)
3. All 12 database-backed tools and `ui_sync` envelopes
4. Sole Group A revocation governance protection (`GOVERNANCE_VIOLATION_SOLE_GROUP_A`)
5. Persistence across fresh PostgreSQL connections
6. FastAPI REST endpoints (`/api/health`, `/api/customers/*`, `/api/chat`) & WebSocket (`/ws/live`)
7. Live `models/gemini-3.8-live-extended-thinking` multi-turn tool calling & extended thinking traces
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient

from backend.db import get_connection, get_db_health, init_db
from backend.gemini_live import run_agent_chat_turn
from backend.main import app
from backend.tools import (
    SwitchActiveCustomerProfile,
    add_or_update_signatory,
    audit_board_resolution,
    configure_signing_rules,
    execute_cosigner_signature,
    get_customer_mandate_details,
    list_customer_profiles,
    revoke_signatory,
    simulate_transaction_authorization,
    submit_mandate_change_request,
    switch_active_customer_profile,
    update_target_accounts,
)
from synthetic_data.seed import seed_all_data


def verify_all() -> None:
    print("=" * 80)
    print("1. INITIALIZING & VERIFYING POSTGRESQL 18.6 SCHEMA & SEED DATA")
    print("=" * 80)
    seed_counts = seed_all_data(reset_existing=True)
    health = get_db_health()
    print("DB Health:", json.dumps(health, indent=2))

    assert health["connected"] is True, "PostgreSQL must be connected"
    assert health["engine"] == "postgresql"
    tc = health["table_counts"]
    assert tc["corporate_customers"] == 5, f"Expected 5 customers, got {tc['corporate_customers']}"
    assert tc["bank_accounts"] == 17, f"Expected 17 accounts, got {tc['bank_accounts']}"
    assert tc["signatories"] == 23, f"Expected 23 signatories, got {tc['signatories']}"
    assert tc["signing_rules"] == 13, f"Expected 13 signing rules, got {tc['signing_rules']}"
    assert tc["board_resolutions"] == 5, f"Expected 5 resolutions, got {tc['board_resolutions']}"
    assert tc["mandate_change_applications"] == 5, f"Expected 5 applications, got {tc['mandate_change_applications']}"
    assert tc["mandate_audit_logs"] >= 15, f"Expected >=15 audit logs, got {tc['mandate_audit_logs']}"
    assert tc["active_workspace_state"] == 1

    print("\n" + "=" * 80)
    print("2. VERIFYING ALL 12 DATABASE-BACKED MANDATE TOOLS & UI_SYNC ENVELOPES")
    print("=" * 80)

    # Tool 1: list_customer_profiles
    res_list = list_customer_profiles()
    assert res_list["status"] == "success"
    assert len(res_list["profiles"]) == 5
    assert "ui_sync" in res_list and res_list["ui_sync"]["target_stage"] == 1
    print("[PASS] list_customer_profiles -> 5 profiles returned")

    # Tool 2: get_customer_mandate_details
    for cid in ["CUST-001", "CUST-002", "CUST-003", "CUST-004", "CUST-005"]:
        det = get_customer_mandate_details(cid)
        assert det["status"] == "success"
        assert det["customer"]["customer_id"] == cid
        assert len(det["accounts"]) >= 3
        assert len(det["signatories"]) >= 4
        assert len(det["signing_rules"]) >= 2
    print("[PASS] get_customer_mandate_details -> verified all 5 customer profiles")

    # Tool 3: SwitchActiveCustomerProfile & switch_active_customer_profile
    sw1 = SwitchActiveCustomerProfile("CUST-002", target_stage=2)
    assert sw1["status"] == "success" and sw1["updated_profile_id"] == "CUST-002"
    sw2 = switch_active_customer_profile("CUST-001", target_stage=1)
    assert sw2["status"] == "success" and sw2["updated_profile_id"] == "CUST-001"
    print("[PASS] SwitchActiveCustomerProfile & switch_active_customer_profile -> verified")

    # Tool 4: add_or_update_signatory
    add_res = add_or_update_signatory(
        customer_id="CUST-001",
        full_name="Evelyn Tan",
        role_title="Deputy CFO",
        signing_group="B",
        nric_masked="S****654E",
        auth_method="IDEAL_DIGITAL_TOKEN",
    )
    assert add_res["status"] == "success"
    assert add_res["operation"] == "ADDED"
    assert add_res["ui_sync"]["target_stage"] == 2

    # Verify persistence across a brand new PostgreSQL connection
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM signatories WHERE customer_id = 'CUST-001' AND full_name = 'Evelyn Tan';"
            )
            persisted_sig = cur.fetchone()
            assert persisted_sig is not None and persisted_sig["signing_group"] == "B"
    print("[PASS] add_or_update_signatory + cross-connection PostgreSQL persistence -> verified")

    # Tool 5: revoke_signatory + SOLE GROUP A PROTECTION
    # In CUST-002, Raymond Ong (SIG-002-01) and Helen Ong-Teo (SIG-002-02) are the 2 active Group A signatories.
    rev_first = revoke_signatory("CUST-002", "SIG-002-02", reason="Retirement")
    assert rev_first["status"] == "success"
    # Now Raymond Ong (SIG-002-01) is the SOLE remaining active Group A signatory for CUST-002!
    rev_sole = revoke_signatory("CUST-002", "SIG-002-01", reason="Attempt sole Group A removal")
    assert rev_sole["status"] == "error", "Sole Group A revocation must fail!"
    assert rev_sole["error_code"] == "GOVERNANCE_VIOLATION_SOLE_GROUP_A"
    print("[PASS] revoke_signatory & GOVERNANCE_VIOLATION_SOLE_GROUP_A protection -> verified")

    # Tool 6: configure_signing_rules
    rule_res = configure_signing_rules(
        customer_id="CUST-001",
        tier_order=1,
        max_amount_sgd=150000.0,
        rule_expression="1A OR 2B",
    )
    assert rule_res["status"] == "success"
    assert float(rule_res["updated_tier"]["max_amount_sgd"]) == 150000.0
    # Verify Tier 2 min_amount_sgd cascaded to 150000.01
    t2 = next(r for r in rule_res["all_signing_rules"] if r["tier_order"] == 2)
    assert float(t2["min_amount_sgd"]) == 150000.01
    print("[PASS] configure_signing_rules + contiguous tier cascade -> verified")

    # Tool 7: simulate_transaction_authorization
    sim_res = simulate_transaction_authorization("CUST-001", amount=100000.0, currency="USD")
    assert sim_res["status"] == "success"
    assert sim_res["evaluated_amount_sgd"] == 135000.0
    assert sim_res["matched_tier"]["tier_order"] == 1
    assert len(sim_res["eligible_signatory_combinations"]) > 0
    print("[PASS] simulate_transaction_authorization (FX USD->SGD + combinations) -> verified")

    # Tool 8: audit_board_resolution
    aud_res = audit_board_resolution(
        customer_id="CUST-001",
        resolution_type="BRC-09",
        resolution_ref="DBS-BRC-09-2026-TEST",
    )
    assert aud_res["status"] == "success"
    assert aud_res["audit_score"] >= 95
    assert aud_res["ui_sync"]["target_stage"] == 4
    print("[PASS] audit_board_resolution -> verified")

    # Tool 9: submit_mandate_change_request
    sub_res = submit_mandate_change_request(
        customer_id="CUST-001",
        submitted_by="Sarah Lim (Managing Director)",
        notes="Verification test submission",
    )
    assert sub_res["status"] == "success"
    assert sub_res["application_ref"].startswith("COM-2026-")
    assert sub_res["ui_sync"]["target_stage"] == 5
    print("[PASS] submit_mandate_change_request -> verified")

    # Tool 10: update_target_accounts
    acc_res = update_target_accounts(
        customer_id="CUST-001",
        account_ids=["ACC-001-01", "ACC-001-02", "ACC-001-03"],
    )
    assert acc_res["status"] == "success"
    assert acc_res["mandate_diff"]["accounts_included_count"] == 3
    print("[PASS] update_target_accounts -> verified")

    # Tool 11: execute_cosigner_signature
    cos_res = execute_cosigner_signature(
        customer_id="CUST-001",
        signer_name="David Tan",
        auth_method="IDEAL Token",
    )
    assert cos_res["status"] == "success"
    print("[PASS] execute_cosigner_signature -> verified")

    print("\n" + "=" * 80)
    print("3. VERIFYING FASTAPI REST ENDPOINTS & WEBSOCKET (/ws/live) & GEMINI 3.8 LIVE")
    print("=" * 80)
    with TestClient(app) as client:
        h_resp = client.get("/api/health")
        assert h_resp.status_code == 200
        h_json = h_resp.json()
        assert h_json["status"] == "ok"
        assert h_json["gemini_live"]["model"] == "models/gemini-3.8-live-extended-thinking"
        print("[PASS] GET /api/health ->", json.dumps(h_json["gemini_live"]))

        # Test WebSocket /ws/live
        with client.websocket_connect("/ws/live") as ws:
            ready = ws.receive_json()
            assert ready["type"] == "session_ready"
            assert ready["model"] == "models/gemini-3.8-live-extended-thinking"
            ws.send_json({"type": "ping"})
            pong = ws.receive_json()
            assert pong["type"] == "pong"
        print("[PASS] WebSocket /ws/live handshake & ping/pong -> verified")

        # Test live Gemini 3.8 Extended Thinking + Tool Execution via /api/chat
        chat_resp = client.post(
            "/api/chat",
            json={
                "message": "Call list_customer_profiles and tell me how many corporate customer profiles exist.",
                "customer_id": "CUST-001",
                "current_stage": 1,
            },
        )
        assert chat_resp.status_code == 200
        chat_json = chat_resp.json()
        assert chat_json["status"] == "success"
        assert len(chat_json["tool_calls"]) >= 1
        print(
            f"[PASS] POST /api/chat (Gemini 3.8 Extended Thinking) -> "
            f"tools={[t['tool_name'] for t in chat_json['tool_calls']]}, "
            f"thoughts={len(chat_json['thinking_traces'])}, "
            f"reply={chat_json['reply'][:100]}..."
        )

    # Restore clean canonical seed state for downstream milestones
    seed_all_data(reset_existing=True)
    print("\n[SUCCESS] All Milestone M1 Database, Tools & Gemini Live verifications passed 100%!")


if __name__ == "__main__":
    verify_all()
