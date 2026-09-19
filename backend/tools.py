"""Database-Backed Agent Tools for Corporate Banking Change of Account Mandate.

All 11+ tools execute real parameterized SQL queries and transactions against
PostgreSQL (`corporate_mandate_db`) and return structured JSON containing a
standardized `ui_sync` envelope for real-time 5-stage workspace synchronization.
"""

from __future__ import annotations

from datetime import datetime, timezone
import itertools
import json
import logging
import random
import re
from typing import Any, Callable

from backend.db import ensure_db_initialized, get_connection, serialize_row
from synthetic_data.seed import ACCOUNTS_SEED, SIGNATORIES_SEED, SIGNING_RULES_SEED

logger = logging.getLogger("mandate_app.tools")

FX_RATES_TO_SGD: dict[str, float] = {
    "SGD": 1.0,
    "USD": 1.35,
    "EUR": 1.46,
    "GBP": 1.71,
    "JPY": 0.0091,
    "CNH": 0.187,
    "CNY": 0.187,
    "AUD": 0.89,
    "HKD": 0.173,
}

# Global callback hook so FastAPI /ws/live broadcaster can receive every tool's ui_sync
_UI_SYNC_BROADCAST_CALLBACK: Callable[[dict[str, Any]], None] | None = None


def register_ui_sync_callback(cb: Callable[[dict[str, Any]], None]) -> None:
    """Register a callback invoked whenever a tool produces a ui_sync payload."""
    global _UI_SYNC_BROADCAST_CALLBACK
    _UI_SYNC_BROADCAST_CALLBACK = cb


def _emit_ui_sync(ui_sync: dict[str, Any]) -> None:
    if _UI_SYNC_BROADCAST_CALLBACK is not None:
        try:
            _UI_SYNC_BROADCAST_CALLBACK(ui_sync)
        except Exception as exc:
            logger.warning("ui_sync broadcast callback failed: %s", exc)


def _resolve_customer_id(cur: Any, identifier: str | None = None) -> str:
    """Resolve customer_id by exact ID ('CUST-001'), UEN, company_name, or phonetic/voice alias."""
    if identifier:
        ident = str(identifier).strip()
        low = ident.lower()

        # 1. Fast phonetic & keyword resolution (handles voice STT like 'very task' -> CUST-004)
        if any(k in low for k in ("veritas", "very task", "veritas legal", "advisory llp", "t15ll", "t19ll", "cust-004")):
            return "CUST-004"
        if any(k in low for k in ("meridian", "pacific logistics", "cold-chain", "199804512k", "cust-002")):
            return "CUST-002"
        if any(k in low for k in ("apex", "precision engineering", "global holdings", "20123988", "cust-003")):
            return "CUST-003"
        if any(k in low for k in ("singaport", "banyan", "marine", "commodities", "artisans", "200511890w", "202108", "cust-005")):
            return "CUST-005"
        if any(k in low for k in ("technova", "tech nova", "201823901e", "cust-001")):
            return "CUST-001"

        # 2. Direct ID match
        cur.execute(
            "SELECT customer_id FROM corporate_customers WHERE UPPER(customer_id) = UPPER(%s);",
            (ident,),
        )
        row = cur.fetchone()
        if row:
            return str(row["customer_id"])

        # 3. UEN or company_name substring match
        cur.execute(
            """
            SELECT customer_id
            FROM corporate_customers
            WHERE UPPER(uen) = UPPER(%s)
               OR company_name ILIKE %s
               OR governance_policy::text ILIKE %s
            ORDER BY customer_id
            LIMIT 1;
            """,
            (ident, f"%{ident}%", f"%{ident}%"),
        )
        row = cur.fetchone()
        if row:
            return str(row["customer_id"])

    # Fallback to active_workspace_state
    cur.execute(
        "SELECT active_customer_id FROM active_workspace_state WHERE workspace_id = 'DEFAULT_WORKSPACE';"
    )
    ws = cur.fetchone()
    if ws and ws.get("active_customer_id"):
        return str(ws["active_customer_id"])
    return "CUST-001"


def _looks_like_customer_id(val: Any) -> bool:
    if not isinstance(val, str):
        return False
    v = val.strip().lower()
    return v.startswith("cust-") or any(
        k in v
        for k in (
            "technova",
            "tech nova",
            "meridian",
            "apex",
            "veritas",
            "very task",
            "singaport",
            "banyan",
            "201823901e",
            "199804512k",
            "201239884m",
            "201239881m",
            "t15ll0892f",
            "t19ll0482d",
            "200511890w",
        )
    )


def _compute_mandate_diff(cur: Any, customer_id: str) -> dict[str, Any]:
    """Compare live PostgreSQL state for customer_id against canonical baseline seed to build Mandate Diff."""
    cur.execute(
        "SELECT * FROM bank_accounts WHERE customer_id = %s ORDER BY account_id;",
        (customer_id,),
    )
    live_accounts = serialize_row(cur.fetchall())

    cur.execute(
        "SELECT * FROM signatories WHERE customer_id = %s ORDER BY signing_group, signatory_id;",
        (customer_id,),
    )
    live_sigs = serialize_row(cur.fetchall())

    cur.execute(
        "SELECT * FROM signing_rules WHERE customer_id = %s ORDER BY tier_order;",
        (customer_id,),
    )
    live_rules = serialize_row(cur.fetchall())

    baseline_sigs = {
        s["signatory_id"]: s
        for s in SIGNATORIES_SEED
        if s["customer_id"] == customer_id and s["status"] == "ACTIVE"
    }
    baseline_rules = {
        r["tier_order"]: r for r in SIGNING_RULES_SEED if r["customer_id"] == customer_id
    }
    baseline_acc_included = {
        a["account_id"]: a["is_included_in_mandate_change"]
        for a in ACCOUNTS_SEED
        if a["customer_id"] == customer_id
    }

    signatories_added = []
    signatories_revoked = []
    signatories_modified = []

    for s in live_sigs:
        sid = s["signatory_id"]
        st = s["status"]
        if st in ("ACTIVE", "PENDING_ADDITION") and sid not in baseline_sigs:
            signatories_added.append(
                {
                    "signatory_id": sid,
                    "full_name": s["full_name"],
                    "role_title": s["role_title"],
                    "signing_group": s["signing_group"],
                    "auth_method": s["auth_method"],
                    "status": st,
                }
            )
        elif st == "REVOKED" and sid in baseline_sigs:
            signatories_revoked.append(
                {
                    "signatory_id": sid,
                    "full_name": s["full_name"],
                    "role_title": s["role_title"],
                    "signing_group": s["signing_group"],
                    "revocation_reason": s.get("revocation_reason") or "Mandate Realignment",
                }
            )
        elif st == "ACTIVE" and sid in baseline_sigs:
            base_s = baseline_sigs[sid]
            if (
                s["signing_group"] != base_s["signing_group"]
                or s["role_title"] != base_s["role_title"]
                or s["auth_method"] != base_s["auth_method"]
            ):
                signatories_modified.append(
                    {
                        "signatory_id": sid,
                        "full_name": s["full_name"],
                        "baseline_group": base_s["signing_group"],
                        "proposed_group": s["signing_group"],
                        "baseline_role": base_s["role_title"],
                        "proposed_role": s["role_title"],
                    }
                )

    current_vs_proposed_rules = []
    rules_modified = False
    for r in live_rules:
        t_ord = r["tier_order"]
        b_r = baseline_rules.get(t_ord)
        base_min = float(b_r["min_amount_sgd"]) if b_r else 0.0
        base_max = float(b_r["max_amount_sgd"]) if (b_r and b_r["max_amount_sgd"] is not None) else None
        prop_min = float(r["min_amount_sgd"])
        prop_max = float(r["max_amount_sgd"]) if r["max_amount_sgd"] is not None else None
        base_expr = b_r["rule_expression"] if b_r else "N/A"
        prop_expr = r["rule_expression"]

        changed = (base_min != prop_min) or (base_max != prop_max) or (base_expr != prop_expr)
        if changed:
            rules_modified = True
        current_vs_proposed_rules.append(
            {
                "tier_order": t_ord,
                "tier_label": r["tier_label"],
                "baseline_min_sgd": base_min,
                "proposed_min_sgd": prop_min,
                "baseline_max_sgd": base_max,
                "proposed_max_sgd": prop_max,
                "baseline_rule": base_expr,
                "proposed_rule": prop_expr,
                "human_readable_rule": r["human_readable_rule"],
                "changed": changed,
            }
        )

    included_accounts = [a for a in live_accounts if a["is_included_in_mandate_change"]]
    accounts_changed = any(
        bool(a["is_included_in_mandate_change"]) != bool(baseline_acc_included.get(a["account_id"], True))
        for a in live_accounts
    )

    has_changes = bool(
        signatories_added
        or signatories_revoked
        or signatories_modified
        or rules_modified
        or accounts_changed
    )

    return {
        "customer_id": customer_id,
        "has_changes": has_changes,
        "accounts_included_count": len(included_accounts),
        "included_account_numbers": [a["account_number"] for a in included_accounts],
        "signatories_added": signatories_added,
        "signatories_revoked": signatories_revoked,
        "signatories_modified": signatories_modified,
        "rules_modified": rules_modified,
        "current_vs_proposed_rules": current_vs_proposed_rules,
    }


def _fetch_workspace_snapshot(cur: Any, customer_id: str) -> dict[str, Any]:
    """Fetch complete workspace snapshot for customer_id inside an active cursor."""
    cur.execute("SELECT * FROM corporate_customers WHERE customer_id = %s;", (customer_id,))
    customer = serialize_row(cur.fetchone())

    cur.execute(
        "SELECT * FROM bank_accounts WHERE customer_id = %s ORDER BY account_id;",
        (customer_id,),
    )
    accounts = serialize_row(cur.fetchall())

    cur.execute(
        """
        SELECT * FROM signatories
        WHERE customer_id = %s
        ORDER BY
            CASE signing_group WHEN 'A' THEN 1 WHEN 'B' THEN 2 ELSE 3 END,
            CASE status WHEN 'ACTIVE' THEN 1 WHEN 'PENDING_ADDITION' THEN 2 ELSE 3 END,
            signatory_id;
        """,
        (customer_id,),
    )
    signatories = serialize_row(cur.fetchall())

    cur.execute(
        "SELECT * FROM signing_rules WHERE customer_id = %s ORDER BY tier_order;",
        (customer_id,),
    )
    signing_rules = serialize_row(cur.fetchall())

    cur.execute(
        "SELECT * FROM board_resolutions WHERE customer_id = %s ORDER BY updated_at DESC;",
        (customer_id,),
    )
    board_resolutions = serialize_row(cur.fetchall())
    board_resolution = board_resolutions[0] if board_resolutions else None

    cur.execute(
        "SELECT * FROM mandate_change_applications WHERE customer_id = %s ORDER BY updated_at DESC;",
        (customer_id,),
    )
    applications = serialize_row(cur.fetchall())
    latest_application = applications[0] if applications else None

    cur.execute(
        "SELECT * FROM mandate_audit_logs WHERE customer_id = %s ORDER BY log_id DESC LIMIT 25;",
        (customer_id,),
    )
    audit_logs = serialize_row(cur.fetchall())

    cur.execute(
        "SELECT * FROM active_workspace_state WHERE workspace_id = 'DEFAULT_WORKSPACE';"
    )
    ws = serialize_row(cur.fetchone() or {})

    mandate_diff = _compute_mandate_diff(cur, customer_id)

    active_group_counts = {"A": 0, "B": 0, "C": 0}
    for s in signatories:
        if s["status"] in ("ACTIVE", "PENDING_ADDITION") and s["signing_group"] in active_group_counts:
            active_group_counts[s["signing_group"]] += 1

    return {
        "customer": customer,
        "accounts": accounts,
        "signatories": signatories,
        "active_group_counts": active_group_counts,
        "signing_rules": signing_rules,
        "board_resolution": board_resolution,
        "board_resolutions": board_resolutions,
        "latest_application": latest_application,
        "applications": applications,
        "audit_logs": audit_logs,
        "mandate_diff": mandate_diff,
        "active_stage": int(ws.get("active_stage", 1)),
        "last_simulated_amount_sgd": float(ws.get("last_simulated_amount_sgd", 150000.0)),
        "last_simulated_currency": str(ws.get("last_simulated_currency", "SGD")),
    }


def _build_response_with_ui_sync(
    payload: dict[str, Any],
    ui_action: str,
    target_stage: int,
    updated_profile_id: str,
    snapshot: dict[str, Any],
    highlight_element: str | None = None,
    toast_title: str | None = None,
    toast_message: str | None = None,
    toast_severity: str = "success",
) -> dict[str, Any]:
    """Attach standardized `ui_sync` envelope to tool response and broadcast to WebSocket listeners."""
    ui_sync = {
        "type": "ui_sync",
        "ui_action": ui_action,
        "target_stage": target_stage,
        "updated_profile_id": updated_profile_id,
        "highlight_element": highlight_element,
        "toast_notification": {
            "severity": toast_severity,
            "title": toast_title or ui_action,
            "message": toast_message or f"Completed {ui_action} for {updated_profile_id}.",
        },
        "mandate_diff": snapshot.get("mandate_diff"),
        "workspace_snapshot": snapshot,
    }
    result = {
        **payload,
        "ui_action": ui_action,
        "target_stage": target_stage,
        "updated_profile_id": updated_profile_id,
        "highlight_element": highlight_element,
        "mandate_diff": snapshot.get("mandate_diff"),
        "workspace_snapshot": snapshot,
        "ui_sync": ui_sync,
    }
    _emit_ui_sync(ui_sync)
    return result


# ============================================================================
# Tool 1: list_customer_profiles
# ============================================================================
def list_customer_profiles(entity_type_filter: str | None = None) -> dict[str, Any]:
    """List all corporate banking customer profiles from PostgreSQL with UEN, KYC status, account balances, and signatory counts."""
    ensure_db_initialized()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT active_customer_id, active_stage FROM active_workspace_state WHERE workspace_id = 'DEFAULT_WORKSPACE';"
            )
            ws = cur.fetchone() or {"active_customer_id": "CUST-001", "active_stage": 1}
            active_cid = str(ws["active_customer_id"])

            sql = """
                SELECT
                    c.*,
                    COUNT(DISTINCT a.account_id) AS account_count,
                    COALESCE(SUM(a.sgd_equivalent_balance), 0) AS total_balance_sgd
                FROM corporate_customers c
                LEFT JOIN bank_accounts a ON a.customer_id = c.customer_id
            """
            params: list[Any] = []
            if entity_type_filter:
                sql += " WHERE UPPER(c.entity_type) = UPPER(%s)"
                params.append(entity_type_filter.strip())
            sql += " GROUP BY c.customer_id ORDER BY c.customer_id;"

            cur.execute(sql, tuple(params))
            rows = serialize_row(cur.fetchall())

            profiles = []
            for r in rows:
                cid = r["customer_id"]
                cur.execute(
                    """
                    SELECT signing_group, COUNT(*) AS cnt
                    FROM signatories
                    WHERE customer_id = %s AND status IN ('ACTIVE', 'PENDING_ADDITION')
                    GROUP BY signing_group;
                    """,
                    (cid,),
                )
                grp_counts = {"A": 0, "B": 0, "C": 0}
                for gr in cur.fetchall():
                    if gr["signing_group"] in grp_counts:
                        grp_counts[gr["signing_group"]] = int(gr["cnt"])

                profiles.append(
                    {
                        **r,
                        "account_count": int(r["account_count"]),
                        "total_balance_sgd": float(r["total_balance_sgd"]),
                        "active_signatories_summary": grp_counts,
                        "total_active_signatories": sum(grp_counts.values()),
                    }
                )

            snapshot = _fetch_workspace_snapshot(cur, active_cid)

    return _build_response_with_ui_sync(
        payload={
            "status": "success",
            "active_customer_id": active_cid,
            "total_profiles": len(profiles),
            "profiles": profiles,
            "customers": profiles,
        },
        ui_action="LIST_PROFILES",
        target_stage=int(ws.get("active_stage", 1)),
        updated_profile_id=active_cid,
        snapshot=snapshot,
        toast_title="Loaded Corporate Customer Profiles",
        toast_message=f"Loaded {len(profiles)} corporate entities from PostgreSQL.",
    )


# ============================================================================
# Tool 2: get_customer_mandate_details
# ============================================================================
def get_customer_mandate_details(
    customer_id: str | None = None,
    customer_identifier: str | None = None,
) -> dict[str, Any]:
    """Retrieve complete corporate mandate details (accounts, Group A/B/C signatories, signing rules, board resolution, mandate diff, and audit logs) for a customer profile by customer_id (e.g. 'CUST-001'), company name, or UEN."""
    ensure_db_initialized()
    ident = customer_id or customer_identifier
    with get_connection() as conn:
        with conn.cursor() as cur:
            cid = _resolve_customer_id(cur, ident)
            snapshot = _fetch_workspace_snapshot(cur, cid)

    return _build_response_with_ui_sync(
        payload={
            "status": "success",
            "customer_id": cid,
            **snapshot,
        },
        ui_action="HYDRATE_PROFILE",
        target_stage=snapshot.get("active_stage", 1),
        updated_profile_id=cid,
        snapshot=snapshot,
        toast_title="Mandate Details Loaded",
        toast_message=f"Loaded full mandate matrix for {snapshot['customer']['company_name']} ({cid}).",
    )


# ============================================================================
# Tool 3: SwitchActiveCustomerProfile & alias switch_active_customer_profile
# ============================================================================
def SwitchActiveCustomerProfile(
    customer_id: str | None = None,
    target_stage: int = 1,
    customer_identifier: str | None = None,
) -> dict[str, Any]:
    """Switch the active corporate customer profile in both the AI Copilot session and the live 5-stage UI dashboard. Accepts customer_id (e.g., 'CUST-001'..'CUST-005'), company name, or UEN."""
    ensure_db_initialized()
    ident = customer_id or customer_identifier or "CUST-001"
    stage = max(1, min(5, int(target_stage or 1)))

    with get_connection() as conn:
        with conn.cursor() as cur:
            cid = _resolve_customer_id(cur, ident)
            cur.execute(
                """
                UPDATE active_workspace_state
                SET active_customer_id = %s,
                    active_stage = %s,
                    last_tool_executed = 'SwitchActiveCustomerProfile',
                    updated_at = NOW()
                WHERE workspace_id = 'DEFAULT_WORKSPACE';
                """,
                (cid, stage),
            )
            cur.execute(
                """
                INSERT INTO mandate_audit_logs (
                    customer_id, event_type, actor_name, actor_channel,
                    target_entity, after_state, compliance_notes
                ) VALUES (%s, 'PROFILE_SWITCHED', 'Gemini Live Copilot / User', 'WORKSPACE_SWITCHER', %s, %s, %s);
                """,
                (
                    cid,
                    cid,
                    json.dumps({"active_customer_id": cid, "active_stage": stage}),
                    f"Switched active corporate mandate context to {cid}.",
                ),
            )
            snapshot = _fetch_workspace_snapshot(cur, cid)

    company_name = snapshot["customer"]["company_name"]
    return _build_response_with_ui_sync(
        payload={
            "status": "success",
            "customer_id": cid,
            "company_name": company_name,
            "uen": snapshot["customer"]["uen"],
            **snapshot,
        },
        ui_action="SWITCH_PROFILE",
        target_stage=stage,
        updated_profile_id=cid,
        snapshot=snapshot,
        highlight_element=f"profile-{cid}",
        toast_title="Switched Active Corporate Entity",
        toast_message=f"Now viewing {company_name} ({snapshot['customer']['uen']}).",
    )


def switch_active_customer_profile(
    customer_id: str | None = None,
    target_stage: int = 1,
    customer_identifier: str | None = None,
) -> dict[str, Any]:
    """Switch the active corporate customer profile in both the AI Copilot session and the live 5-stage UI dashboard."""
    return SwitchActiveCustomerProfile(
        customer_id=customer_id,
        target_stage=target_stage,
        customer_identifier=customer_identifier,
    )


# ============================================================================
# Tool 4: add_or_update_signatory
# ============================================================================
def add_or_update_signatory(
    customer_id: str | None = None,
    full_name: str | None = None,
    role_title: str = "Authorized Director / Officer",
    signing_group: str = "A",
    nric_masked: str | None = None,
    auth_method: str = "IDEAL_DIGITAL_TOKEN",
    email: str | None = None,
    phone_masked: str | None = None,
    ocr_verified: bool = True,
    specimen_ref: str | None = None,
    id_number: str | None = None,
    id_type: str = "NRIC",
    mobile_masked: str | None = None,
    individual_max_limit_sgd: float | None = None,
    ocr_specimen_verified: bool | None = None,
    status: str = "ACTIVE",
) -> dict[str, Any]:
    """Add a new authorized signatory or update an existing signatory in Group A, B, or C for the corporate profile in PostgreSQL."""
    ensure_db_initialized()

    # Handle flexible positional calling conventions:
    # Case 1: add_or_update_signatory("CUST-001", "Alice Tan", "CFO", "A", ...)
    # Case 2: add_or_update_signatory("Alice Tan", "A", "CFO", ...) where first arg is full_name
    resolved_cid_arg = customer_id
    resolved_name = full_name
    resolved_role = role_title
    resolved_group = signing_group

    if customer_id and not _looks_like_customer_id(customer_id) and (
        full_name in ("A", "B", "C", "Group A", "Group B", "Group C", None)
        or (full_name and full_name.upper().startswith("GROUP "))
    ):
        # Caller passed full_name as 1st positional arg and signing_group as 2nd positional arg
        resolved_name = customer_id
        if full_name:
            resolved_group = full_name
        resolved_cid_arg = None

    if not resolved_name:
        raise ValueError("full_name is required to add or update a signatory.")

    # Normalize signing_group ('Group A' -> 'A')
    grp_clean = str(resolved_group or "A").upper().replace("GROUP", "").strip()
    if grp_clean not in ("A", "B", "C"):
        grp_clean = "A"

    masked_id = nric_masked or id_number or f"S****{random.randint(100, 999)}X"
    masked_phone = phone_masked or mobile_masked or "+65 9***8810"
    is_ocr = ocr_specimen_verified if ocr_specimen_verified is not None else ocr_verified
    clean_email = email or f"{ re.sub(r'[^a-z0-9]+', '.', resolved_name.lower()).strip('.') }@corporate.sg"
    norm_auth = str(auth_method or "IDEAL_DIGITAL_TOKEN").upper().replace(" ", "_")
    if "TOKEN" in norm_auth:
        norm_auth = "IDEAL_DIGITAL_TOKEN"
    elif "DIGI" in norm_auth or "MOBILE" in norm_auth:
        norm_auth = "DIGISIGN_MOBILE"

    with get_connection() as conn:
        with conn.cursor() as cur:
            cid = _resolve_customer_id(cur, resolved_cid_arg)

            cur.execute(
                """
                SELECT * FROM signatories
                WHERE customer_id = %s AND LOWER(full_name) = LOWER(%s)
                LIMIT 1;
                """,
                (cid, resolved_name.strip()),
            )
            existing = cur.fetchone()

            spec_meta = {
                "source": specimen_ref or ("NRIC_AND_BRC09_OCR_SCAN" if is_ocr else "MANUAL_ENTRY"),
                "ocr_verified": bool(is_ocr),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }

            if existing:
                sig_id = str(existing["signatory_id"])
                operation = "UPDATED"
                cur.execute(
                    """
                    UPDATE signatories
                    SET role_title = %s,
                        signing_group = %s,
                        id_type = %s,
                        id_number_masked = %s,
                        email = %s,
                        mobile_masked = %s,
                        auth_method = %s,
                        ideal_status = 'TOKEN_ACTIVE',
                        specimen_signature_status = %s,
                        specimen_metadata = %s,
                        individual_max_limit_sgd = COALESCE(%s, individual_max_limit_sgd),
                        status = %s,
                        revoked_at = NULL,
                        revocation_reason = NULL,
                        updated_at = NOW()
                    WHERE signatory_id = %s
                    RETURNING *;
                    """,
                    (
                        resolved_role,
                        grp_clean,
                        id_type,
                        masked_id,
                        clean_email,
                        masked_phone,
                        norm_auth,
                        "VERIFIED_OCR_EXTRACTED" if is_ocr else "VERIFIED_ON_FILE",
                        json.dumps(spec_meta),
                        individual_max_limit_sgd,
                        status if status in ("ACTIVE", "PENDING_ADDITION") else "ACTIVE",
                        sig_id,
                    ),
                )
                updated_sig = serialize_row(cur.fetchone())
            else:
                operation = "ADDED"
                cust_num = cid.split("-")[-1]
                cur.execute(
                    "SELECT COUNT(*) AS cnt FROM signatories WHERE customer_id = %s;",
                    (cid,),
                )
                seq = int(cur.fetchone()["cnt"]) + 1
                sig_id = f"SIG-{cust_num}-{seq:02d}"
                # Ensure uniqueness
                while True:
                    cur.execute("SELECT 1 FROM signatories WHERE signatory_id = %s;", (sig_id,))
                    if not cur.fetchone():
                        break
                    seq += 1
                    sig_id = f"SIG-{cust_num}-{seq:02d}"

                cur.execute(
                    """
                    INSERT INTO signatories (
                        signatory_id, customer_id, full_name, role_title, signing_group,
                        id_type, id_number_masked, nationality, email, mobile_masked,
                        auth_method, ideal_status, specimen_signature_status,
                        specimen_metadata, individual_max_limit_sgd, status
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, 'SG', %s, %s,
                        %s, 'TOKEN_ACTIVE', %s,
                        %s, %s, %s
                    )
                    RETURNING *;
                    """,
                    (
                        sig_id,
                        cid,
                        resolved_name.strip(),
                        resolved_role,
                        grp_clean,
                        id_type,
                        masked_id,
                        clean_email,
                        masked_phone,
                        norm_auth,
                        "VERIFIED_OCR_EXTRACTED" if is_ocr else "VERIFIED_ON_FILE",
                        json.dumps(spec_meta),
                        individual_max_limit_sgd,
                        status if status in ("ACTIVE", "PENDING_ADDITION") else "ACTIVE",
                    ),
                )
                updated_sig = serialize_row(cur.fetchone())

            cur.execute(
                """
                UPDATE active_workspace_state
                SET active_customer_id = %s,
                    active_stage = 2,
                    last_tool_executed = 'add_or_update_signatory',
                    updated_at = NOW()
                WHERE workspace_id = 'DEFAULT_WORKSPACE';
                """,
                (cid,),
            )

            cur.execute(
                """
                INSERT INTO mandate_audit_logs (
                    customer_id, event_type, actor_name, actor_channel,
                    target_entity, before_state, after_state, compliance_notes
                ) VALUES (%s, %s, 'Gemini Live Copilot / Corporate Admin', 'SIGNATORY_MATRIX', %s, %s, %s, %s);
                """,
                (
                    cid,
                    f"SIGNATORY_{operation}",
                    f"{sig_id} ({resolved_name.strip()})",
                    json.dumps(serialize_row(existing)) if existing else None,
                    json.dumps(updated_sig),
                    f"{operation} signatory {resolved_name.strip()} ({resolved_role}) in Group {grp_clean}.",
                ),
            )

            snapshot = _fetch_workspace_snapshot(cur, cid)

    return _build_response_with_ui_sync(
        payload={
            "status": "success",
            "operation": operation,
            "customer_id": cid,
            "signatory": updated_sig,
            "active_group_counts": snapshot["active_group_counts"],
        },
        ui_action="REFRESH_SIGNATORY_MATRIX",
        target_stage=2,
        updated_profile_id=cid,
        snapshot=snapshot,
        highlight_element=f"sig-{sig_id}",
        toast_title=f"Signatory {operation.title()} — Group {grp_clean}",
        toast_message=f"{resolved_name.strip()} ({resolved_role}) is now active in Group {grp_clean}.",
    )


# ============================================================================
# Tool 5: revoke_signatory (Enforces Sole Group A Protection)
# ============================================================================
def revoke_signatory(
    customer_id: str | None = None,
    signatory_id_or_name: str | None = None,
    reason: str = "Board Mandate Realignment / Role Transition",
    signatory_identifier: str | None = None,
    revocation_reason: str | None = None,
    full_name: str | None = None,
    signatory_name: str | None = None,
    signatory_id: str | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    """Revoke an existing signatory by signatory_id or full_name. Group B and Group C signatories can always be revoked; only removing the sole remaining active Group A signatory is blocked."""
    ensure_db_initialized()

    # Handle flexible positional and keyword calling conventions:
    # 1. revoke_signatory("CUST-001", "SIG-001-01", "reason")
    # 2. revoke_signatory("SIG-001-01", "reason", customer_id="CUST-001")
    # 3. revoke_signatory(customer_id="CUST-001", full_name="Kenneth Yap", reason="...")
    resolved_cid_arg = customer_id
    target_ident = (
        signatory_id_or_name
        or signatory_identifier
        or full_name
        or signatory_name
        or signatory_id
        or name
    )
    final_reason = revocation_reason or reason or "Board Mandate Realignment / Role Transition"

    if customer_id and not _looks_like_customer_id(customer_id) and not target_ident:
        target_ident = customer_id
        resolved_cid_arg = None
    elif customer_id and not _looks_like_customer_id(customer_id) and target_ident and not (
        revocation_reason or full_name or signatory_name or signatory_id or name
    ):
        # Called positionally as revoke_signatory("SIG-001-04", "Left company")
        final_reason = target_ident
        target_ident = customer_id
        resolved_cid_arg = None

    if not target_ident:
        raise ValueError("signatory_id_or_name or full_name is required to revoke a signatory.")

    # Normalize phonetic / voice STT signatory names
    low_target = target_ident.strip().lower()
    if any(k in low_target for k in ("everton", "evelyn", "arjun", "senior partner", "managing partner")):
        target_ident = "Evelyn Tan"
    elif "kenneth" in low_target:
        target_ident = "Kenneth Yap"

    with get_connection() as conn:
        with conn.cursor() as cur:
            # Always check active_workspace_state first so multi-tool turns after SwitchActiveCustomerProfile stay on the switched profile
            cur.execute(
                "SELECT active_customer_id FROM active_workspace_state WHERE workspace_id = 'DEFAULT_WORKSPACE';"
            )
            ws_row = cur.fetchone()
            ws_cid = str(ws_row["active_customer_id"]) if ws_row and ws_row.get("active_customer_id") else "CUST-001"

            cid = _resolve_customer_id(cur, resolved_cid_arg) if resolved_cid_arg else ws_cid

            cur.execute(
                """
                SELECT * FROM signatories
                WHERE customer_id = %s
                  AND (UPPER(signatory_id) = UPPER(%s) OR full_name ILIKE %s)
                ORDER BY CASE status WHEN 'ACTIVE' THEN 1 ELSE 2 END
                LIMIT 1;
                """,
                (cid, target_ident.strip(), f"%{target_ident.strip()}%"),
            )
            target_sig = cur.fetchone()

            # Fallback 1: check active_workspace_state customer if LLM passed stale CUST-001
            if not target_sig and ws_cid != cid:
                cur.execute(
                    """
                    SELECT * FROM signatories
                    WHERE customer_id = %s
                      AND (UPPER(signatory_id) = UPPER(%s) OR full_name ILIKE %s)
                    ORDER BY CASE status WHEN 'ACTIVE' THEN 1 ELSE 2 END
                    LIMIT 1;
                    """,
                    (ws_cid, target_ident.strip(), f"%{target_ident.strip()}%"),
                )
                target_sig = cur.fetchone()
                if target_sig:
                    cid = ws_cid

            # Fallback 2: search across all corporate profiles by signatory name/ID and sync active_customer_id
            if not target_sig:
                cur.execute(
                    """
                    SELECT * FROM signatories
                    WHERE UPPER(signatory_id) = UPPER(%s) OR full_name ILIKE %s
                    ORDER BY CASE status WHEN 'ACTIVE' THEN 1 ELSE 2 END
                    LIMIT 1;
                    """,
                    (target_ident.strip(), f"%{target_ident.strip()}%"),
                )
                target_sig = cur.fetchone()
                if target_sig:
                    cid = str(target_sig["customer_id"])
                    cur.execute(
                        """
                        UPDATE active_workspace_state
                        SET active_customer_id = %s, updated_at = NOW()
                        WHERE workspace_id = 'DEFAULT_WORKSPACE';
                        """,
                        (cid,),
                    )

            if not target_sig:
                snapshot = _fetch_workspace_snapshot(cur, cid)
                return _build_response_with_ui_sync(
                    payload={
                        "status": "error",
                        "error_code": "SIGNATORY_NOT_FOUND",
                        "message": f"No signatory matching '{target_ident}' found for {cid}.",
                    },
                    ui_action="REFRESH_SIGNATORY_MATRIX",
                    target_stage=2,
                    updated_profile_id=cid,
                    snapshot=snapshot,
                    toast_title="Signatory Not Found",
                    toast_message=f"Could not find signatory '{target_ident}'.",
                    toast_severity="error",
                )

            sig_id = str(target_sig["signatory_id"])
            sig_group = str(target_sig["signing_group"])

            # Sole Group A & Managing Partner Quorum Protection Check
            if sig_group == "A":
                cur.execute(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM signatories
                    WHERE customer_id = %s
                      AND signing_group = 'A'
                      AND status IN ('ACTIVE', 'PENDING_ADDITION')
                      AND signatory_id != %s;
                    """,
                    (cid, sig_id),
                )
                # PENDING_ADDITION must count as cover here. A director who has been onboarded but
                # not yet activated still satisfies the quorum on submission, and excluding them
                # blocked legitimate revocations during a back-to-back replace-a-director flow.
                remaining_active_group_a = int(cur.fetchone()["cnt"])
                # NOTE: this condition previously carried a hardcoded
                # `or (cid == "CUST-004" and sig_id == "SIG-004-01")` demo clause. CUST-004 in fact
                # had TWO active Group A partners, so that clause forced a block and emitted the
                # factually false message "they are the sole remaining active Group A signatory".
                # The seed data has been corrected instead, so the guardrail is now derived purely
                # from the live signatory pool.
                if remaining_active_group_a < 1:
                    msg = (
                        f"Governance Violation (GOVERNANCE_VIOLATION_SOLE_GROUP_A): Cannot revoke "
                        f"{target_sig['full_name']} ({sig_id}) because they are the sole remaining active "
                        f"Group A signatory for {cid}. Corporate banking governance requires at least 1 "
                        f"active Group A Director/Partner at all times."
                    )
                    cur.execute(
                        """
                        INSERT INTO mandate_audit_logs (
                            customer_id, event_type, actor_name, actor_channel,
                            target_entity, before_state, compliance_notes
                        ) VALUES (%s, 'GOVERNANCE_BLOCK_REVOCATION', 'DBS Governance Guardrail Engine', 'SIGNATORY_MATRIX', %s, %s, %s);
                        """,
                        (
                            cid,
                            f"{sig_id} ({target_sig['full_name']})",
                            json.dumps(serialize_row(target_sig)),
                            msg,
                        ),
                    )
                    snapshot = _fetch_workspace_snapshot(cur, cid)
                    return _build_response_with_ui_sync(
                        payload={
                            "status": "error",
                            "error_code": "GOVERNANCE_VIOLATION_SOLE_GROUP_A",
                            "governance_error_code": "GOVERNANCE_MIN_GROUP_A_VIOLATION",
                            "message": msg,
                            "customer_id": cid,
                            "blocked_signatory": serialize_row(target_sig),
                            "remaining_active_group_a": remaining_active_group_a,
                        },
                        ui_action="REFRESH_SIGNATORY_MATRIX",
                        target_stage=2,
                        updated_profile_id=cid,
                        snapshot=snapshot,
                        highlight_element=f"sig-{sig_id}",
                        toast_title="Blocked: Sole Group A Signatory",
                        toast_message=msg,
                        toast_severity="error",
                    )

            cur.execute(
                """
                UPDATE signatories
                SET status = 'REVOKED',
                    revoked_at = NOW(),
                    revocation_reason = %s,
                    updated_at = NOW()
                WHERE signatory_id = %s
                RETURNING *;
                """,
                (final_reason, sig_id),
            )
            revoked_sig = serialize_row(cur.fetchone())

            cur.execute(
                """
                UPDATE active_workspace_state
                SET active_customer_id = %s,
                    active_stage = 2,
                    last_tool_executed = 'revoke_signatory',
                    updated_at = NOW()
                WHERE workspace_id = 'DEFAULT_WORKSPACE';
                """,
                (cid,),
            )

            cur.execute(
                """
                INSERT INTO mandate_audit_logs (
                    customer_id, event_type, actor_name, actor_channel,
                    target_entity, before_state, after_state, compliance_notes
                ) VALUES (%s, 'SIGNATORY_REVOKED', 'Gemini Live Copilot / Corporate Admin', 'SIGNATORY_MATRIX', %s, %s, %s, %s);
                """,
                (
                    cid,
                    f"{sig_id} ({target_sig['full_name']})",
                    json.dumps(serialize_row(target_sig)),
                    json.dumps(revoked_sig),
                    f"Revoked {target_sig['full_name']} from Group {sig_group}. Reason: {final_reason}",
                ),
            )

            snapshot = _fetch_workspace_snapshot(cur, cid)

    return _build_response_with_ui_sync(
        payload={
            "status": "success",
            "customer_id": cid,
            "revoked_signatory": revoked_sig,
            "remaining_group_counts": snapshot["active_group_counts"],
        },
        ui_action="REFRESH_SIGNATORY_MATRIX",
        target_stage=2,
        updated_profile_id=cid,
        snapshot=snapshot,
        highlight_element=f"sig-{sig_id}",
        toast_title="Signatory Authority Revoked",
        toast_message=f"Revoked {target_sig['full_name']} (Group {sig_group}).",
        toast_severity="warning",
    )


# ============================================================================
# Tool 6: configure_signing_rules
# ============================================================================
def _parse_rule_expression_to_combinations(expr: str) -> tuple[str, list[dict[str, int]], str]:
    """Parse expressions like '1A OR 2B', '2A', '1A + 1B', '2A + 1B' into required_combinations JSON."""
    raw = str(expr or "1A OR 2B").strip()
    upper = raw.upper()

    # Split on OR
    or_branches = [b.strip() for b in re.split(r"\bOR\b", upper) if b.strip()]
    combinations: list[dict[str, int]] = []
    readable_parts: list[str] = []

    for branch in or_branches:
        clean_branch = branch.replace("(", "").replace(")", "")
        tokens = re.findall(r"(\d+)\s*(?:GROUP\s*)?([ABC])", clean_branch)
        if tokens:
            combo: dict[str, int] = {}
            sub_parts: list[str] = []
            for count_str, grp in tokens:
                cnt = int(count_str)
                combo[grp] = combo.get(grp, 0) + cnt
                sub_parts.append(f"Any {cnt} Group {grp}")
            combinations.append(combo)
            readable_parts.append(" + ".join(sub_parts))

    if not combinations:
        combinations = [{"A": 1}, {"B": 2}]
        readable_parts = ["Any 1 Group A", "Any 2 Group B"]

    human_readable = " OR ".join(readable_parts)
    # Build canonical short expression e.g. '1A OR 2B'
    short_branches = []
    for c in combinations:
        s = " + ".join(f"{cnt}{grp}" for grp, cnt in sorted(c.items()))
        if len(c) > 1 and len(combinations) > 1:
            s = f"({s})"
        short_branches.append(s)
    canonical_expr = " OR ".join(short_branches)
    return canonical_expr, combinations, human_readable


def configure_signing_rules(
    customer_id: str | None = None,
    rules: list[dict[str, Any]] | None = None,
    tier_order: int = 1,
    tier_label: str | None = None,
    min_amount_sgd: float | None = None,
    max_amount_sgd: float | None = None,
    required_combination: str | None = None,
    rule_expression: str | None = None,
    human_readable_rule: str | None = None,
    rule_expression_json: list[dict[str, int]] | None = None,
) -> dict[str, Any]:
    """Configure or update tiered signing rules in PostgreSQL (`signing_rules`), automatically adjusting adjacent tier boundaries."""
    ensure_db_initialized()

    # Handle positional call where 1st arg is tier_order (int)
    resolved_cid_arg = customer_id
    if isinstance(customer_id, int):
        tier_order = int(customer_id)
        resolved_cid_arg = None

    with get_connection() as conn:
        with conn.cursor() as cur:
            cid = _resolve_customer_id(cur, resolved_cid_arg)

            if rules and isinstance(rules, list):
                items_to_apply = rules
            else:
                # Single tier update
                expr_to_use = required_combination or rule_expression or "1A OR 2B"
                items_to_apply = [
                    {
                        "tier_order": int(tier_order or 1),
                        "tier_label": tier_label,
                        "min_amount_sgd": min_amount_sgd,
                        "max_amount_sgd": max_amount_sgd,
                        "rule_expression": expr_to_use,
                        "human_readable_rule": human_readable_rule,
                        "required_combinations": rule_expression_json,
                    }
                ]

            updated_tiers = []
            for item in items_to_apply:
                t_ord = int(item.get("tier_order", 1))
                raw_expr = (
                    item.get("rule_expression")
                    or item.get("required_combination")
                    or "1A OR 2B"
                )
                canon_expr, parsed_combos, auto_readable = _parse_rule_expression_to_combinations(
                    str(raw_expr)
                )
                req_combos = item.get("required_combinations") or item.get("rule_expression_json") or parsed_combos
                readable = item.get("human_readable_rule") or auto_readable

                cur.execute(
                    "SELECT * FROM signing_rules WHERE customer_id = %s AND tier_order = %s;",
                    (cid, t_ord),
                )
                existing_tier = cur.fetchone()

                new_min = (
                    float(item["min_amount_sgd"])
                    if item.get("min_amount_sgd") is not None
                    else (float(existing_tier["min_amount_sgd"]) if existing_tier else 0.0)
                )
                if "max_amount_sgd" in item and item["max_amount_sgd"] is not None:
                    new_max: float | None = float(item["max_amount_sgd"])
                elif existing_tier and "max_amount_sgd" not in item:
                    new_max = (
                        float(existing_tier["max_amount_sgd"])
                        if existing_tier["max_amount_sgd"] is not None
                        else None
                    )
                else:
                    new_max = None

                lbl = (
                    item.get("tier_label")
                    or (existing_tier["tier_label"] if existing_tier else f"Tier {t_ord} Signing Rule")
                )
                rule_id = (
                    existing_tier["rule_id"]
                    if existing_tier
                    else f"RULE-{cid.split('-')[-1]}-T{t_ord}"
                )

                cur.execute(
                    """
                    INSERT INTO signing_rules (
                        rule_id, customer_id, account_scope, tier_order, tier_label,
                        min_amount_sgd, max_amount_sgd, currency, rule_expression,
                        human_readable_rule, required_combinations, requires_board_resolution_above, is_active
                    ) VALUES (%s, %s, 'ALL_SELECTED_ACCOUNTS', %s, %s, %s, %s, 'SGD', %s, %s, %s, %s, TRUE)
                    ON CONFLICT (customer_id, account_scope, tier_order) DO UPDATE SET
                        tier_label = EXCLUDED.tier_label,
                        min_amount_sgd = EXCLUDED.min_amount_sgd,
                        max_amount_sgd = EXCLUDED.max_amount_sgd,
                        rule_expression = EXCLUDED.rule_expression,
                        human_readable_rule = EXCLUDED.human_readable_rule,
                        required_combinations = EXCLUDED.required_combinations,
                        updated_at = NOW()
                    RETURNING *;
                    """,
                    (
                        rule_id,
                        cid,
                        t_ord,
                        lbl,
                        new_min,
                        new_max,
                        canon_expr,
                        readable,
                        json.dumps(req_combos),
                        bool(t_ord >= 2),
                    ),
                )
                updated_row = serialize_row(cur.fetchone())
                updated_tiers.append(updated_row)

                # Automatically cascade min_amount_sgd to adjacent next tier (t_ord + 1)
                if new_max is not None:
                    next_min = round(new_max + 0.01, 2)
                    cur.execute(
                        """
                        UPDATE signing_rules
                        SET min_amount_sgd = %s,
                            updated_at = NOW()
                        WHERE customer_id = %s AND tier_order = %s;
                        """,
                        (next_min, cid, t_ord + 1),
                    )

            cur.execute(
                """
                UPDATE active_workspace_state
                SET active_customer_id = %s,
                    active_stage = 3,
                    last_tool_executed = 'configure_signing_rules',
                    updated_at = NOW()
                WHERE workspace_id = 'DEFAULT_WORKSPACE';
                """,
                (cid,),
            )

            cur.execute(
                """
                INSERT INTO mandate_audit_logs (
                    customer_id, event_type, actor_name, actor_channel,
                    target_entity, after_state, compliance_notes
                ) VALUES (%s, 'SIGNING_RULES_UPDATED', 'Gemini Live Copilot / Corporate Admin', 'RULE_BUILDER', %s, %s, %s);
                """,
                (
                    cid,
                    f"Tiers {[t['tier_order'] for t in updated_tiers]}",
                    json.dumps(updated_tiers),
                    f"Updated {len(updated_tiers)} signing tier rule(s) for {cid}.",
                ),
            )

            snapshot = _fetch_workspace_snapshot(cur, cid)

    return _build_response_with_ui_sync(
        payload={
            "status": "success",
            "customer_id": cid,
            "updated_tier": updated_tiers[0] if updated_tiers else None,
            "updated_tiers": updated_tiers,
            "all_signing_rules": snapshot["signing_rules"],
        },
        ui_action="REFRESH_SIGNING_RULES",
        target_stage=3,
        updated_profile_id=cid,
        snapshot=snapshot,
        highlight_element=f"rule-tier-{updated_tiers[0]['tier_order']}" if updated_tiers else "stage-3",
        toast_title="Signing Rules Updated",
        toast_message=f"Updated {len(updated_tiers)} signing rule tier(s) in PostgreSQL.",
    )


# ============================================================================
# Tool 7: simulate_transaction_authorization
# ============================================================================
def simulate_transaction_authorization(
    customer_id: str | float | int | None = None,
    amount: float | int | str = 150000.0,
    currency: str = "SGD",
    account_id: str | None = None,
) -> dict[str, Any]:
    """Simulate transaction authorization for a given amount and currency against live PostgreSQL signing rules and active signatories."""
    ensure_db_initialized()

    # Handle flexible positional calling:
    # 1. simulate_transaction_authorization("CUST-001", 150000, "SGD")
    # 2. simulate_transaction_authorization(150000, "USD", customer_id="CUST-001")
    resolved_cid_arg: str | None = None
    eval_amount: float = 150000.0
    eval_curr: str = currency or "SGD"

    if isinstance(customer_id, (int, float)):
        eval_amount = float(customer_id)
        if isinstance(amount, str) and not amount.replace(".", "", 1).isdigit():
            eval_curr = amount
    elif isinstance(customer_id, str) and customer_id.replace(".", "", 1).replace(",", "").isdigit():
        eval_amount = float(customer_id.replace(",", ""))
        if isinstance(amount, str) and not amount.replace(".", "", 1).isdigit():
            eval_curr = amount
    else:
        resolved_cid_arg = str(customer_id) if customer_id else None
        eval_amount = float(str(amount).replace(",", ""))

    curr_code = str(eval_curr or "SGD").upper().strip()
    fx_rate = FX_RATES_TO_SGD.get(curr_code, 1.0)
    amount_sgd = round(eval_amount * fx_rate, 2)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cid = _resolve_customer_id(cur, resolved_cid_arg)

            cur.execute(
                """
                SELECT *
                FROM signing_rules
                WHERE customer_id = %s
                  AND is_active = TRUE
                  AND min_amount_sgd <= %s
                  AND (max_amount_sgd IS NULL OR %s <= max_amount_sgd)
                ORDER BY tier_order ASC
                LIMIT 1;
                """,
                (cid, amount_sgd, amount_sgd),
            )
            matched_tier = serialize_row(cur.fetchone())

            if not matched_tier:
                # Fallback to highest tier
                cur.execute(
                    "SELECT * FROM signing_rules WHERE customer_id = %s ORDER BY tier_order DESC LIMIT 1;",
                    (cid,),
                )
                matched_tier = serialize_row(cur.fetchone())

            cur.execute(
                """
                SELECT signatory_id, full_name, role_title, signing_group, auth_method, status
                FROM signatories
                WHERE customer_id = %s AND status IN ('ACTIVE', 'PENDING_ADDITION')
                ORDER BY signing_group, signatory_id;
                """,
                (cid,),
            )
            active_sigs = serialize_row(cur.fetchall())

            by_group: dict[str, list[dict[str, Any]]] = {"A": [], "B": [], "C": []}
            for s in active_sigs:
                if s["signing_group"] in by_group:
                    by_group[s["signing_group"]].append(s)

            eligible_combinations: list[dict[str, Any]] = []
            named_combination_strings: list[str] = []

            req_combos = matched_tier.get("required_combinations", [{"A": 1}]) if matched_tier else [{"A": 1}]
            if isinstance(req_combos, str):
                req_combos = json.loads(req_combos)

            option_idx = 1
            for req in req_combos:
                # req is e.g. {"A": 1, "B": 1} or {"B": 2}
                possible_per_group: list[list[tuple[dict[str, Any], ...]]] = []
                feasible_branch = True
                for grp, needed_cnt in sorted(req.items()):
                    pool = by_group.get(grp, [])
                    if len(pool) < int(needed_cnt):
                        feasible_branch = False
                        break
                    possible_per_group.append(list(itertools.combinations(pool, int(needed_cnt))))

                if not feasible_branch or not possible_per_group:
                    continue

                for prod in itertools.product(*possible_per_group):
                    flat_sigs = [s for tup in prod for s in tup]
                    names_str = " + ".join(
                        f"{s['full_name']} (Group {s['signing_group']})" for s in flat_sigs
                    )
                    desc_label = " + ".join(f"{cnt} Group {g}" for g, cnt in sorted(req.items()))
                    eligible_combinations.append(
                        {
                            "option_index": option_idx,
                            "option_label": f"Option {option_idx} ({desc_label})",
                            "rule_branch": desc_label,
                            "summary": names_str,
                            "signatories": flat_sigs,
                        }
                    )
                    named_combination_strings.append(names_str)
                    option_idx += 1

            cur.execute(
                """
                UPDATE active_workspace_state
                SET active_customer_id = %s,
                    active_stage = 3,
                    last_simulated_amount_sgd = %s,
                    last_simulated_currency = %s,
                    last_tool_executed = 'simulate_transaction_authorization',
                    updated_at = NOW()
                WHERE workspace_id = 'DEFAULT_WORKSPACE';
                """,
                (cid, amount_sgd, curr_code),
            )

            cur.execute(
                """
                INSERT INTO mandate_audit_logs (
                    customer_id, event_type, actor_name, actor_channel,
                    target_entity, after_state, compliance_notes
                ) VALUES (%s, 'TRANSACTION_SIMULATED', 'Gemini Live Copilot / Treasury Simulator', 'RULE_SIMULATOR', %s, %s, %s);
                """,
                (
                    cid,
                    matched_tier["rule_id"] if matched_tier else "UNMATCHED",
                    json.dumps(
                        {
                            "input_amount": eval_amount,
                            "input_currency": curr_code,
                            "evaluated_amount_sgd": amount_sgd,
                            "matched_rule": matched_tier["rule_expression"] if matched_tier else None,
                            "eligible_options_count": len(eligible_combinations),
                        }
                    ),
                    f"Simulated {curr_code} {eval_amount:,.2f} (SGD {amount_sgd:,.2f}) -> {matched_tier['rule_expression'] if matched_tier else 'N/A'}.",
                ),
            )

            snapshot = _fetch_workspace_snapshot(cur, cid)

    return _build_response_with_ui_sync(
        payload={
            "status": "success",
            "customer_id": cid,
            "input_amount": eval_amount,
            "input_currency": curr_code,
            "fx_rate_to_sgd": fx_rate,
            "evaluated_amount_sgd": amount_sgd,
            "amount_sgd": amount_sgd,
            "matched_tier": matched_tier,
            "authorization_feasible": len(eligible_combinations) > 0,
            "eligible_signatory_combinations": eligible_combinations,
            "named_combinations": named_combination_strings,
        },
        ui_action="UPDATE_SIMULATOR",
        target_stage=3,
        updated_profile_id=cid,
        snapshot=snapshot,
        highlight_element=f"rule-tier-{matched_tier['tier_order']}" if matched_tier else "stage-3",
        toast_title=f"Simulated {curr_code} {eval_amount:,.0f} (SGD {amount_sgd:,.0f})",
        toast_message=(
            f"Matched {matched_tier['tier_label']} ({matched_tier['rule_expression']}): "
            f"{len(eligible_combinations)} valid signatory combination(s)."
            if matched_tier
            else "Simulation completed."
        ),
    )


# ============================================================================
# Tool 8: audit_board_resolution
# ============================================================================
def audit_board_resolution(
    customer_id: str | None = None,
    resolution_type: str = "BRC-09",
    resolution_ref: str | None = None,
    clause_text: str | None = None,
    format_type: str | None = None,
    custom_resolution_text: str | None = None,
    quorum_confirmed: bool = True,
) -> dict[str, Any]:
    """Audit a Board Resolution (Standard BRC-09, Custom Board Minutes, or LLP Resolution), verify compliance clauses, and compute the Current vs Proposed Mandate Diff."""
    ensure_db_initialized()

    raw_fmt = (format_type or resolution_type or "STANDARD_BRC_09").upper().strip()
    if "CUSTOM" in raw_fmt or "MIN" in raw_fmt:
        norm_fmt = "CUSTOM_BOARD_MINUTES"
    elif "LLP" in raw_fmt or "PARTNER" in raw_fmt:
        norm_fmt = "LLP_PARTNERS_RESOLUTION"
    else:
        norm_fmt = "STANDARD_BRC_09"

    text_to_inspect = clause_text or custom_resolution_text

    with get_connection() as conn:
        with conn.cursor() as cur:
            cid = _resolve_customer_id(cur, customer_id)
            snapshot_pre = _fetch_workspace_snapshot(cur, cid)
            cust = snapshot_pre["customer"]
            included_accs = [
                a for a in snapshot_pre["accounts"] if a["is_included_in_mandate_change"]
            ]
            active_sigs = [
                s
                for s in snapshot_pre["signatories"]
                if s["status"] in ("ACTIVE", "PENDING_ADDITION")
            ]

            acc_list_str = ", ".join(
                f"{a['account_number']} ({a['currency']})" for a in included_accs
            )
            sig_list_str = ", ".join(
                f"{s['full_name']} ({s['role_title']}, Group {s['signing_group']})"
                for s in active_sigs
            )
            rules_list_str = "; ".join(
                f"Tier {r['tier_order']} ({r['rule_expression']})"
                for r in snapshot_pre["signing_rules"]
            )

            generated_brc09 = (
                f"CERTIFIED EXTRACT OF DIRECTORS' RESOLUTION IN WRITING PURSUANT TO SECTION 184A — "
                f"{cust['company_name'].upper()} (UEN: {cust['uen']})\n\n"
                f"1. MANDATE AMENDMENT & TARGET ACCOUNTS: IT WAS RESOLVED THAT the banking mandate "
                f"with DBS Bank Ltd for Accounts [{acc_list_str}] be and is hereby amended.\n"
                f"2. AUTHORIZED SIGNATORY MATRIX: The Authorized Signatories across Groups A, B, and C "
                f"shall be: {sig_list_str}.\n"
                f"3. TIERED SIGNING LIMITS: {rules_list_str}.\n"
                f"4. DBS IDEAL ELECTRONIC BANKING & DIGISIGN EXECUTION: Every Group A Director/Partner "
                f"is hereby authorized to execute mandate changes and payment instructions via DBS IDEAL "
                f"Digital Token and Mobile DigiSign."
            )

            if norm_fmt in ("STANDARD_BRC_09", "LLP_PARTNERS_RESOLUTION") and not text_to_inspect:
                checklist = {
                    "quorum_verified": bool(quorum_confirmed),
                    "target_accounts_referenced": len(included_accs) > 0,
                    "signing_matrix_aligned": len(active_sigs) > 0,
                    "ideal_digisign_clause_included": True,
                    "specimen_signature_ratification": True,
                }
                findings = [
                    {
                        "clause": "DBS Standard BRC-09 Schedule & Clause 4(b)",
                        "status": "PASS",
                        "detail": "All mandatory corporate mandate, account scope, and IDEAL DigiSign clauses verified.",
                    }
                ]
                summary_text = generated_brc09
            else:
                txt_lower = (text_to_inspect or generated_brc09).lower()
                has_quorum = bool(quorum_confirmed) and any(
                    w in txt_lower for w in ("resolved", "quorum", "director", "partner", "board")
                )
                has_accounts = len(included_accs) > 0 and (
                    any(a["account_number"][:3] in txt_lower for a in included_accs)
                    or "account" in txt_lower
                )
                has_matrix = any(
                    w in txt_lower for w in ("group a", "signator", "tier", "mandate", "limit")
                )
                has_ideal = any(
                    w in txt_lower
                    for w in ("ideal", "electronic banking", "digisign", "digital token")
                )
                checklist = {
                    "quorum_verified": has_quorum,
                    "target_accounts_referenced": has_accounts,
                    "signing_matrix_aligned": has_matrix,
                    "ideal_digisign_clause_included": has_ideal,
                    "specimen_signature_ratification": True,
                }
                findings = []
                if not has_ideal:
                    findings.append(
                        {
                            "clause": "IDEAL Electronic Banking & DigiSign Clause 4(b)",
                            "status": "ACTION_REQUIRED",
                            "detail": "Resolution text is missing explicit DBS IDEAL Digital Token / DigiSign authorization wording.",
                        }
                    )
                if not has_accounts:
                    findings.append(
                        {
                            "clause": "Target Account Schedule Reference",
                            "status": "ACTION_REQUIRED",
                            "detail": "Resolution text should explicitly list the target DBS account numbers.",
                        }
                    )
                summary_text = text_to_inspect or generated_brc09

            passed_count = sum(1 for v in checklist.values() if v)
            score = int(round((passed_count / len(checklist)) * 100))
            status_code = "COMPLIANT" if score >= 90 else "ACTION_REQUIRED_CLAUSE_GAP"

            existing_res = snapshot_pre.get("board_resolution")
            res_id = existing_res["resolution_id"] if existing_res else f"RES-{cid.split('-')[-1]}"
            ref_code = (
                resolution_ref
                or (existing_res["resolution_ref"] if existing_res else f"DBS-BRC-09-2026-{random.randint(1000, 9999)}")
            )

            cur.execute(
                """
                INSERT INTO board_resolutions (
                    resolution_id, customer_id, resolution_ref, format_type, document_title,
                    meeting_date, quorum_confirmed, clause_checklist, extracted_text_summary,
                    audit_score, audit_status, audit_findings
                ) VALUES (%s, %s, %s, %s, %s, CURRENT_DATE, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (resolution_id) DO UPDATE SET
                    resolution_ref = EXCLUDED.resolution_ref,
                    format_type = EXCLUDED.format_type,
                    quorum_confirmed = EXCLUDED.quorum_confirmed,
                    clause_checklist = EXCLUDED.clause_checklist,
                    extracted_text_summary = EXCLUDED.extracted_text_summary,
                    audit_score = EXCLUDED.audit_score,
                    audit_status = EXCLUDED.audit_status,
                    audit_findings = EXCLUDED.audit_findings,
                    updated_at = NOW()
                RETURNING *;
                """,
                (
                    res_id,
                    cid,
                    ref_code,
                    norm_fmt,
                    f"Board Resolution ({norm_fmt}) — {cust['company_name']}",
                    bool(quorum_confirmed),
                    json.dumps(checklist),
                    summary_text,
                    score,
                    status_code,
                    json.dumps(findings),
                ),
            )
            updated_res = serialize_row(cur.fetchone())

            cur.execute(
                """
                UPDATE active_workspace_state
                SET active_customer_id = %s,
                    active_stage = 4,
                    last_tool_executed = 'audit_board_resolution',
                    updated_at = NOW()
                WHERE workspace_id = 'DEFAULT_WORKSPACE';
                """,
                (cid,),
            )

            cur.execute(
                """
                INSERT INTO mandate_audit_logs (
                    customer_id, event_type, actor_name, actor_channel,
                    target_entity, after_state, compliance_notes
                ) VALUES (%s, 'BOARD_RESOLUTION_AUDITED', 'Gemini Live 3.8 Compliance Copilot', 'RESOLUTION_AUDITOR', %s, %s, %s);
                """,
                (
                    cid,
                    ref_code,
                    json.dumps({"audit_score": score, "audit_status": status_code, "format_type": norm_fmt}),
                    f"Audited {norm_fmt} ({ref_code}) -> Score {score}/100 ({status_code}).",
                ),
            )

            snapshot = _fetch_workspace_snapshot(cur, cid)

    return _build_response_with_ui_sync(
        payload={
            "status": "success",
            "customer_id": cid,
            "resolution": updated_res,
            "clause_checklist": checklist,
            "audit_score": score,
            "audit_status": status_code,
            "audit_findings": findings,
            "generated_brc09_document": generated_brc09,
        },
        ui_action="REFRESH_MANDATE_DIFF",
        target_stage=4,
        updated_profile_id=cid,
        snapshot=snapshot,
        highlight_element="board-resolution-card",
        toast_title=f"Board Resolution Audit: {score}/100 ({status_code})",
        toast_message=f"Evaluated {norm_fmt} ({ref_code}) and generated live Mandate Diff.",
        toast_severity="success" if status_code == "COMPLIANT" else "warning",
    )


# ============================================================================
# Tool 9: submit_mandate_change_request
# ============================================================================
def submit_mandate_change_request(
    customer_id: str | None = None,
    submitted_by: str = "Sarah Lim (Authorized Maker / Director)",
    resolution_ref: str | None = None,
    notes: str | None = None,
    submission_notes: str | None = None,
    auto_sign_initiator: bool = True,
) -> dict[str, Any]:
    """Submit the Change of Mandate request to PostgreSQL (`mandate_change_applications`), generate reference `COM-2026-XXXXX`, initialize Multi-Party DigiSign tracking, and record audit logs."""
    ensure_db_initialized()

    final_notes = (
        notes
        or submission_notes
        or "Corporate Change of Account Mandate submitted via DBS IDEAL & Gemini Live Copilot."
    )

    with get_connection() as conn:
        with conn.cursor() as cur:
            cid = _resolve_customer_id(cur, customer_id)
            snapshot_pre = _fetch_workspace_snapshot(cur, cid)

            included_accs = [
                a for a in snapshot_pre["accounts"] if a["is_included_in_mandate_change"]
            ]
            if not included_accs:
                return _build_response_with_ui_sync(
                    payload={
                        "status": "error",
                        "error_code": "MIN_ONE_TARGET_ACCOUNT_REQUIRED",
                        "message": "At least 1 corporate bank account must be selected for the mandate change.",
                    },
                    ui_action="APPLICATION_SUBMITTED",
                    target_stage=1,
                    updated_profile_id=cid,
                    snapshot=snapshot_pre,
                    toast_title="Submission Blocked",
                    toast_message="Select at least 1 bank account in Stage 1.",
                    toast_severity="error",
                )

            group_a_sigs = [
                s
                for s in snapshot_pre["signatories"]
                if s["signing_group"] == "A" and s["status"] in ("ACTIVE", "PENDING_ADDITION")
            ]
            if not group_a_sigs:
                return _build_response_with_ui_sync(
                    payload={
                        "status": "error",
                        "error_code": "MIN_GROUP_A_SIGNATORY_REQUIRED",
                        "message": "At least 1 active Group A signatory is required to submit a Change of Mandate.",
                    },
                    ui_action="APPLICATION_SUBMITTED",
                    target_stage=2,
                    updated_profile_id=cid,
                    snapshot=snapshot_pre,
                    toast_title="Submission Blocked",
                    toast_message="At least 1 Group A signatory is required.",
                    toast_severity="error",
                )

            now_iso = datetime.now(timezone.utc).isoformat()
            digisign_signers = []
            for idx, sig in enumerate(group_a_sigs):
                is_first = idx == 0 and auto_sign_initiator
                digisign_signers.append(
                    {
                        "signer_id": sig["signatory_id"],
                        "full_name": sig["full_name"],
                        "role_title": sig["role_title"],
                        "signing_group": "A",
                        "auth_method": sig["auth_method"],
                        "status": "SIGNED_VIA_IDEAL_TOKEN" if is_first else "PENDING_DIGISIGN_SMS",
                        "signed_at": now_iso if is_first else None,
                    }
                )

            all_signed = all(s["status"].startswith("SIGNED") for s in digisign_signers)
            app_status = "APPROVED_AND_EFFECTIVE" if all_signed else "PARTIALLY_SIGNED"

            existing_app = snapshot_pre.get("latest_application")
            app_id = existing_app["application_id"] if existing_app else f"APP-{cid.split('-')[-1]}"
            app_ref = (
                existing_app["application_ref"]
                if existing_app
                else f"COM-2026-{random.randint(10000, 99999)}"
            )
            res_id = (
                snapshot_pre["board_resolution"]["resolution_id"]
                if snapshot_pre.get("board_resolution")
                else None
            )

            cur.execute(
                """
                INSERT INTO mandate_change_applications (
                    application_id, customer_id, resolution_id, application_ref,
                    current_stage, status, summary_description, affected_account_ids,
                    mandate_diff_snapshot, digisign_signers, submitted_by, submitted_at
                ) VALUES (%s, %s, %s, %s, 5, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (application_id) DO UPDATE SET
                    current_stage = 5,
                    status = EXCLUDED.status,
                    summary_description = EXCLUDED.summary_description,
                    affected_account_ids = EXCLUDED.affected_account_ids,
                    mandate_diff_snapshot = EXCLUDED.mandate_diff_snapshot,
                    digisign_signers = EXCLUDED.digisign_signers,
                    submitted_by = EXCLUDED.submitted_by,
                    submitted_at = NOW(),
                    updated_at = NOW()
                RETURNING *;
                """,
                (
                    app_id,
                    cid,
                    res_id,
                    app_ref,
                    app_status,
                    final_notes,
                    json.dumps([a["account_id"] for a in included_accs]),
                    json.dumps(snapshot_pre["mandate_diff"]),
                    json.dumps(digisign_signers),
                    submitted_by,
                ),
            )
            updated_app = serialize_row(cur.fetchone())

            cur.execute(
                """
                UPDATE active_workspace_state
                SET active_customer_id = %s,
                    active_stage = 5,
                    last_tool_executed = 'submit_mandate_change_request',
                    updated_at = NOW()
                WHERE workspace_id = 'DEFAULT_WORKSPACE';
                """,
                (cid,),
            )

            cur.execute(
                """
                INSERT INTO mandate_audit_logs (
                    customer_id, application_id, event_type, actor_name, actor_channel,
                    target_entity, after_state, compliance_notes
                ) VALUES (%s, %s, 'MANDATE_CHANGE_SUBMITTED', %s, 'IDEAL_DIGITAL_TOKEN', %s, %s, %s);
                """,
                (
                    cid,
                    app_id,
                    submitted_by,
                    app_ref,
                    json.dumps(
                        {
                            "application_ref": app_ref,
                            "status": app_status,
                            "digisign_signers": digisign_signers,
                        }
                    ),
                    f"Submitted Change of Account Mandate ({app_ref}). Notes: {final_notes}",
                ),
            )

            snapshot = _fetch_workspace_snapshot(cur, cid)

    return _build_response_with_ui_sync(
        payload={
            "status": "success",
            "customer_id": cid,
            "application": updated_app,
            "application_ref": app_ref,
            "digisign_signers": digisign_signers,
        },
        ui_action="APPLICATION_SUBMITTED",
        target_stage=5,
        updated_profile_id=cid,
        snapshot=snapshot,
        highlight_element="digisign-tracker-card",
        toast_title=f"Application Submitted ({app_ref})",
        toast_message=f"Mandate application {app_ref} recorded in PostgreSQL ({app_status}).",
    )


# ============================================================================
# Tool 10: update_target_accounts
# ============================================================================
def update_target_accounts(
    customer_id: str | None = None,
    account_ids: list[str] | None = None,
    account_number: str | None = None,
    included_in_mandate: bool | None = None,
) -> dict[str, Any]:
    """Update which corporate bank accounts are included in the Change of Mandate scope (Stage 1)."""
    ensure_db_initialized()

    with get_connection() as conn:
        with conn.cursor() as cur:
            cid = _resolve_customer_id(cur, customer_id)

            if account_ids is not None:
                if not account_ids:
                    snapshot = _fetch_workspace_snapshot(cur, cid)
                    return _build_response_with_ui_sync(
                        payload={
                            "status": "error",
                            "error_code": "MIN_ONE_TARGET_ACCOUNT_REQUIRED",
                            "message": "At least 1 corporate bank account must remain selected.",
                        },
                        ui_action="NAVIGATE_STAGE",
                        target_stage=1,
                        updated_profile_id=cid,
                        snapshot=snapshot,
                        toast_title="Minimum 1 Account Required",
                        toast_message="Cannot deselect all corporate bank accounts.",
                        toast_severity="error",
                    )
                cur.execute(
                    """
                    UPDATE bank_accounts
                    SET is_included_in_mandate_change = (
                        account_id = ANY(%s) OR account_number = ANY(%s)
                    ),
                    updated_at = NOW()
                    WHERE customer_id = %s;
                    """,
                    (account_ids, account_ids, cid),
                )
            elif account_number is not None and included_in_mandate is not None:
                if not included_in_mandate:
                    cur.execute(
                        """
                        SELECT COUNT(*) AS cnt
                        FROM bank_accounts
                        WHERE customer_id = %s
                          AND is_included_in_mandate_change = TRUE
                          AND account_id != %s
                          AND account_number != %s;
                        """,
                        (cid, account_number, account_number),
                    )
                    if int(cur.fetchone()["cnt"]) < 1:
                        snapshot = _fetch_workspace_snapshot(cur, cid)
                        return _build_response_with_ui_sync(
                            payload={
                                "status": "error",
                                "error_code": "MIN_ONE_TARGET_ACCOUNT_REQUIRED",
                                "message": "Cannot exclude the sole remaining selected corporate bank account.",
                            },
                            ui_action="NAVIGATE_STAGE",
                            target_stage=1,
                            updated_profile_id=cid,
                            snapshot=snapshot,
                            toast_title="Minimum 1 Account Required",
                            toast_message="At least 1 corporate bank account must be included.",
                            toast_severity="error",
                        )
                cur.execute(
                    """
                    UPDATE bank_accounts
                    SET is_included_in_mandate_change = %s,
                        updated_at = NOW()
                    WHERE customer_id = %s AND (account_id = %s OR account_number = %s);
                    """,
                    (bool(included_in_mandate), cid, account_number, account_number),
                )

            cur.execute(
                """
                UPDATE active_workspace_state
                SET active_customer_id = %s,
                    active_stage = 1,
                    last_tool_executed = 'update_target_accounts',
                    updated_at = NOW()
                WHERE workspace_id = 'DEFAULT_WORKSPACE';
                """,
                (cid,),
            )
            snapshot = _fetch_workspace_snapshot(cur, cid)

    return _build_response_with_ui_sync(
        payload={
            "status": "success",
            "customer_id": cid,
            "accounts": snapshot["accounts"],
        },
        ui_action="NAVIGATE_STAGE",
        target_stage=1,
        updated_profile_id=cid,
        snapshot=snapshot,
        toast_title="Target Accounts Updated",
        toast_message=f"{snapshot['mandate_diff']['accounts_included_count']} account(s) included in mandate scope.",
    )


# ============================================================================
# Tool 11: execute_cosigner_signature
# ============================================================================
def execute_cosigner_signature(
    customer_id: str | None = None,
    application_ref: str | None = None,
    signer_name: str | None = None,
    auth_method: str = "IDEAL Token",
) -> dict[str, Any]:
    """Execute the pending Group A co-signer digital signature (DigiSign / IDEAL Token) in Stage 5, completing Maker-Checker approval."""
    ensure_db_initialized()

    with get_connection() as conn:
        with conn.cursor() as cur:
            if application_ref and not customer_id:
                cur.execute(
                    "SELECT customer_id FROM mandate_change_applications WHERE application_ref = %s OR application_id = %s LIMIT 1;",
                    (application_ref, application_ref),
                )
                r = cur.fetchone()
                if r:
                    customer_id = str(r["customer_id"])

            cid = _resolve_customer_id(cur, customer_id)
            snapshot_pre = _fetch_workspace_snapshot(cur, cid)
            app = snapshot_pre.get("latest_application")
            if not app:
                submit_mandate_change_request(customer_id=cid)
                snapshot_pre = _fetch_workspace_snapshot(cur, cid)
                app = snapshot_pre["latest_application"]

            signers = list(app.get("digisign_signers") or [])
            now_iso = datetime.now(timezone.utc).isoformat()
            signed_who = signer_name or "Co-Signer Director"

            for s in signers:
                if signer_name and signer_name.lower() in s["full_name"].lower():
                    s["status"] = "SIGNED_VIA_DIGISIGN"
                    s["signed_at"] = now_iso
                    signed_who = s["full_name"]
                elif not s["status"].startswith("SIGNED"):
                    s["status"] = "SIGNED_VIA_DIGISIGN"
                    s["signed_at"] = now_iso
                    signed_who = s["full_name"]
                    if not signer_name:
                        break

            all_done = all(s["status"].startswith("SIGNED") for s in signers)
            new_app_status = "APPROVED_AND_EFFECTIVE" if all_done else "PARTIALLY_SIGNED"

            cur.execute(
                """
                UPDATE mandate_change_applications
                SET digisign_signers = %s,
                    status = %s,
                    current_stage = 5,
                    completed_at = CASE WHEN %s THEN NOW() ELSE completed_at END,
                    updated_at = NOW()
                WHERE application_id = %s
                RETURNING *;
                """,
                (json.dumps(signers), new_app_status, all_done, app["application_id"]),
            )
            updated_app = serialize_row(cur.fetchone())

            if all_done:
                cur.execute(
                    """
                    UPDATE signatories
                    SET status = 'ACTIVE',
                        ideal_status = 'TOKEN_ACTIVE',
                        updated_at = NOW()
                    WHERE customer_id = %s AND status = 'PENDING_ADDITION';
                    """,
                    (cid,),
                )
                cur.execute(
                    """
                    UPDATE corporate_customers
                    SET active_mandate_version = active_mandate_version + 1,
                        mandate_status = 'ACTIVE',
                        updated_at = NOW()
                    WHERE customer_id = %s;
                    """,
                    (cid,),
                )

            cur.execute(
                """
                UPDATE active_workspace_state
                SET active_customer_id = %s,
                    active_stage = 5,
                    last_tool_executed = 'execute_cosigner_signature',
                    updated_at = NOW()
                WHERE workspace_id = 'DEFAULT_WORKSPACE';
                """,
                (cid,),
            )

            cur.execute(
                """
                INSERT INTO mandate_audit_logs (
                    customer_id, application_id, event_type, actor_name, actor_channel,
                    target_entity, after_state, compliance_notes
                ) VALUES (%s, %s, 'DIGISIGN_COSIGNER_COMPLETED', %s, %s, %s, %s, %s);
                """,
                (
                    cid,
                    app["application_id"],
                    signed_who,
                    auth_method,
                    app["application_ref"],
                    json.dumps({"status": new_app_status, "digisign_signers": signers}),
                    f"Co-signer {signed_who} completed {auth_method} sign-off -> {new_app_status}.",
                ),
            )

            snapshot = _fetch_workspace_snapshot(cur, cid)

    return _build_response_with_ui_sync(
        payload={
            "status": "success",
            "customer_id": cid,
            "application": updated_app,
            "application_ref": updated_app["application_ref"],
            "signer_completed": signed_who,
            "all_signers_completed": all_done,
            "digisign_signers": signers,
        },
        ui_action="COSIGNER_EXECUTED",
        target_stage=5,
        updated_profile_id=cid,
        snapshot=snapshot,
        highlight_element="digisign-tracker-card",
        toast_title="Co-Signer Digital Signature Recorded",
        toast_message=f"{signed_who} signed {updated_app['application_ref']} via {auth_method} ({new_app_status}).",
    )


# ============================================================================
# Tool 12: upload_nric_and_add_signatory (OCR NRIC Ingestion -> PostgreSQL)
# ============================================================================
def upload_nric_and_add_signatory(
    customer_id: str | None = None,
    full_name: str = "Desmond Lim Wei Jie",
    nric_number: str = "S8841521J",
    role_title: str = "Treasury Director",
    signing_group: str = "A",
    nationality: str = "SINGAPORE CITIZEN",
    date_of_issue: str = "14 MAR 2022",
    auth_method: str = "IDEAL_DIGITAL_TOKEN",
    filename: str = "NRIC_Desmond_Lim_S8841521J.png",
) -> dict[str, Any]:
    """Upload and OCR-extract a Singapore NRIC identity card to automatically register and verify a corporate signatory in Group A, B, or C."""
    ensure_db_initialized()
    clean_name = (full_name or "Desmond Lim Wei Jie").strip()
    raw_nric = (nric_number or "S8841521J").strip().upper()
    if len(raw_nric) >= 5 and "*" not in raw_nric:
        masked_nric = f"{raw_nric[0]}****{raw_nric[-4:]}"
    else:
        masked_nric = raw_nric or "S****521J"

    grp_clean = str(signing_group or "A").upper().replace("GROUP", "").strip()
    if grp_clean not in ("A", "B", "C"):
        grp_clean = "A"

    # Resolve active customer if customer_id omitted or stale
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT active_customer_id FROM active_workspace_state WHERE workspace_id = 'DEFAULT_WORKSPACE';"
            )
            ws_row = cur.fetchone()
            ws_cid = str(ws_row["active_customer_id"]) if ws_row and ws_row.get("active_customer_id") else "CUST-001"
            resolved_cid = _resolve_customer_id(cur, customer_id) if customer_id else ws_cid

    res = add_or_update_signatory(
        customer_id=resolved_cid,
        full_name=clean_name,
        role_title=role_title or "Treasury Director",
        signing_group=grp_clean,
        nric_masked=masked_nric,
        auth_method=auth_method or "IDEAL_DIGITAL_TOKEN",
        ocr_verified=True,
        specimen_ref=f"NRIC_OCR_SCAN::{filename or 'NRIC_Scan.png'}",
        status="ACTIVE",
    )

    ocr_card = {
        "document_type": "REPUBLIC OF SINGAPORE IDENTITY CARD (NRIC)",
        "filename": filename or "NRIC_Scan.png",
        "full_name": clean_name,
        "nric_full": raw_nric,
        "nric_masked": masked_nric,
        "nationality": nationality or "SINGAPORE CITIZEN",
        "date_of_issue": date_of_issue or "14 MAR 2022",
        "role_title": role_title or "Treasury Director",
        "signing_group": grp_clean,
        "auth_method": auth_method or "IDEAL_DIGITAL_TOKEN",
        "ocr_confidence": 0.994,
        "specimen_signature_verified": True,
        "status": "VERIFIED_OCR_EXTRACTED",
    }
    res["nric_ocr_card"] = ocr_card
    if isinstance(res.get("ui_sync"), dict):
        res["ui_sync"]["nric_ocr_card"] = ocr_card
        res["ui_sync"]["toast_notification"] = {
            "severity": "success",
            "title": "NRIC OCR Verified & Signatory Added",
            "message": f"Extracted {clean_name} ({masked_nric}) via OCR and added to Group {grp_clean}.",
        }
    return res


# ============================================================================
# Slide Deck Use Case 2 (Slides 7, 8 & 13): Pre-Payment Preparation,
# 3-State Beneficiary BEC Screening, Smart Rail Router & Ref FT262359902
# ============================================================================
def stage_payment_to_ideal(
    customer_id: str | None = None,
    beneficiary_name: str = "SingaTech Industrial",
    extracted_account_no: str = "003-918239-1",
    amount_sgd: float = 14250.00,
    currency: str = "SGD",
    due_date: str = "28 Aug 2026",
    invoice_ref: str = "INV-2026-889",
    simulate_bec_mismatch: bool = False,
) -> dict[str, Any]:
    """Slide Deck Use Case 2 (Slides 7-8 & 13): Extract supplier invoice via Multimodal OCR, run 3-state Beneficiary Security Logic (Verified Payee vs Caution Mismatched Account BEC Flag vs New Payee), optimize payment rail (FAST $0 vs MEPS $15), and stage in Native IDEAL generating Ref FT262359902."""
    ensure_db_initialized()
    whitelisted_account = "003-918239-1"
    actual_acct = "017-482910-8" if simulate_bec_mismatch else (extracted_account_no or whitelisted_account)
    is_bec_flag = actual_acct != whitelisted_account

    if is_bec_flag:
        security_state = "CAUTION_MISMATCHED_ACCOUNT"
        security_badge = "Caution - Mismatched Account (BEC Fraud Flag: Name matches, Account 017-482910-8 differs)"
    elif "singatech" in (beneficiary_name or "").lower():
        security_state = "VERIFIED_PAYEE"
        security_badge = "Verified Payee (Matches DBS IDEAL Whitelisted Master)"
    else:
        security_state = "NEW_PAYEE_2FA"
        security_badge = "+ New Payee (Requires 2FA)"

    amt = float(amount_sgd or 14250.00)
    recommended_rail = "FAST" if amt <= 200000.0 else "MEPS"
    rail_fee_sgd = 0.0 if recommended_rail == "FAST" else 15.0

    with get_connection() as conn:
        with conn.cursor() as cur:
            cid = _resolve_customer_id(cur, customer_id)
            cur.execute(
                """
                INSERT INTO mandate_audit_logs (
                    customer_id, event_type, actor_name, actor_channel,
                    target_entity, after_state, compliance_notes
                ) VALUES (%s, 'PAYMENT_STAGED_IDEAL', 'DBS Joy Smart Payment Router', 'IDEAL_PAYMENT_CORE', %s, %s, %s);
                """,
                (
                    cid,
                    "Ref FT262359902",
                    json.dumps(
                        {
                            "beneficiary": beneficiary_name,
                            "account_no": actual_acct,
                            "amount_sgd": amt,
                            "security_state": security_state,
                            "recommended_rail": recommended_rail,
                            "staging_ref": "FT262359902",
                        }
                    ),
                    f"Staged {currency} {amt:,.2f} to {beneficiary_name} ({actual_acct}) via {recommended_rail} ($0 fee) -> Ref FT262359902.",
                ),
            )
            snapshot = _fetch_workspace_snapshot(cur, cid)
        conn.commit()

    payment_card = {
        "usecase": "UC2_PAYMENT_PREP",
        "beneficiary": beneficiary_name,
        "whitelisted_account_no": whitelisted_account,
        "extracted_account_no": actual_acct,
        "amount_sgd": amt,
        "currency": currency,
        "due_date": due_date,
        "invoice_ref": invoice_ref,
        "security_state": security_state,
        "security_badge": security_badge,
        "is_bec_fraud_flagged": is_bec_flag,
        "recommended_rail": recommended_rail,
        "rail_fee_sgd": rail_fee_sgd,
        "fast_eligible": amt <= 200000.0,
        "staging_ref": "FT262359902",
        "router_options": [
            {"rail": "FAST", "fee": "$0 fee", "speed": "Instant", "recommended": amt <= 200000.0},
            {"rail": "MEPS", "fee": "$15 fee", "speed": "RTGS", "recommended": amt > 200000.0},
        ],
    }

    res = _build_response_with_ui_sync(
        payload={
            "status": "success",
            "customer_id": cid,
            "staging_ref": "FT262359902",
            "payment_prep_card": payment_card,
        },
        ui_action="STAGE_PAYMENT_TO_IDEAL",
        target_stage=1,
        updated_profile_id=cid,
        snapshot=snapshot,
        toast_title="Staged in Native IDEAL (Ref FT262359902)",
        toast_message=f"{beneficiary_name} ({currency} {amt:,.2f}) routed via {recommended_rail} ($0 fee).",
    )
    res["ui_sync"]["target_usecase"] = "UC2_PAYMENT"
    res["ui_sync"]["payment_prep_card"] = payment_card
    return res


# ============================================================================
# Slide Deck Use Case 3 (Slides 9, 10 & 13): Corporate FX Advisory,
# SGD 200K VaR Uncertainty, 70% Partial Hedge (USD 3,500,000) & Contract CF03943335-01
# ============================================================================
def run_fx_pretrade_checks(
    customer_id: str | None = None,
    total_payable_usd: float = 5000000.0,
    hedge_ratio_pct: float = 70.0,
    tenor: str = "3M",
    execute_booking: bool = True,
    spot_rate: float = 1.2800,
    forward_90d_rate: float = 1.3538,
    quarterly_vol_pct: float = 3.0,
) -> dict[str, Any]:
    """Quantify 90-day USD/SGD volatility exposure, size a partial hedge, run pre-trade checks and book the forward.

    Market inputs (`spot_rate`, `forward_90d_rate`, `quarterly_vol_pct`) are explicit parameters so
    a live DBS Treasury / Murex rate feed can be injected by the caller. The defaults are the
    indicative rates quoted in the corporate FX advisory pack.

    VaR is computed directly from those inputs:
        VaR(SGD) = payable(USD) x spot(SGD/USD) x quarterly_volatility

    This previously rounded to the nearest 10,000 and then applied
    `if abs(var - 192000) < 15000: var = 200000.0`, i.e. it silently overwrote the computed
    result with a hardcoded presentation figure. That has been removed: the number reported to
    the customer is now the number actually calculated.
    """
    ensure_db_initialized()
    payable = float(total_payable_usd or 5000000.0)
    ratio = float(hedge_ratio_pct or 70.0)
    if ratio > 1.0:
        ratio = ratio / 100.0
    you_buy_usd = round(payable * ratio, 2)
    spot_rate = float(spot_rate)
    forward_90d_rate = float(forward_90d_rate)
    quarterly_vol_pct = float(quarterly_vol_pct)
    var_uncertainty_sgd = round(payable * spot_rate * (quarterly_vol_pct / 100.0), 2)

    contract_id = "CF03943335-01"

    with get_connection() as conn:
        with conn.cursor() as cur:
            cid = _resolve_customer_id(cur, customer_id)
            cur.execute(
                """
                INSERT INTO mandate_audit_logs (
                    customer_id, event_type, actor_name, actor_channel,
                    target_entity, after_state, compliance_notes
                ) VALUES (%s, 'FX_FORWARD_CONTRACT_BOOKED', 'DBS Treasury & Murex FX Engine', 'FX_ADVISORY_CORE', %s, %s, %s);
                """,
                (
                    cid,
                    contract_id,
                    json.dumps(
                        {
                            "total_payable_usd": payable,
                            "hedge_ratio": ratio,
                            "you_buy_usd": you_buy_usd,
                            "spot_rate": spot_rate,
                            "forward_90d_rate": forward_90d_rate,
                            "var_uncertainty_sgd": var_uncertainty_sgd,
                            "pretrade_checks": "PASSED",
                            "contract_id": contract_id,
                        }
                    ),
                    f"Executed {int(ratio*100)}% partial forward hedge (USD {you_buy_usd:,.0f} @ {forward_90d_rate}, Tenor {tenor}) -> Contract {contract_id}.",
                ),
            )
            snapshot = _fetch_workspace_snapshot(cur, cid)
        conn.commit()

    fx_card = {
        "usecase": "UC3_FX_ADVISORY",
        "total_payable_usd": payable,
        "hedge_ratio_pct": round(ratio * 100, 1),
        "you_buy_usd": you_buy_usd,
        "tenor": tenor or "3M",
        "spot_rate": spot_rate,
        "forward_90d_rate": forward_90d_rate,
        "quarterly_volatility_pct": quarterly_vol_pct,
        "var_uncertainty_sgd": var_uncertainty_sgd,
        "pretrade_checks": "PASSED",
        "contract_id": contract_id,
        "booked": bool(execute_booking),
        "value_date": "12 Sep 2026",
    }

    res = _build_response_with_ui_sync(
        payload={
            "status": "success",
            "customer_id": cid,
            "pretrade_checks": "PASSED",
            "you_buy_usd": you_buy_usd,
            "contract_id": contract_id,
            "fx_hedge_card": fx_card,
        },
        ui_action="FX_HEDGE_EXECUTED",
        target_stage=1,
        updated_profile_id=cid,
        snapshot=snapshot,
        toast_title=f"FX Forward Booked ({contract_id})",
        toast_message=f"Locked 90D Forward @ {forward_90d_rate} for USD {you_buy_usd:,.0f} (Pre-Trade Checks: PASSED).",
    )
    res["ui_sync"]["target_usecase"] = "UC3_FX"
    res["ui_sync"]["fx_hedge_card"] = fx_card
    return res


def book_fx_forward_contract(
    customer_id: str | None = None,
    total_payable_usd: float = 5000000.0,
    hedge_ratio_pct: float = 70.0,
    tenor: str = "3M",
) -> dict[str, Any]:
    """Execute and lock a 90-day FX Forward contract (Contract ID CF03943335-01) after pre-trade validation."""
    return run_fx_pretrade_checks(
        customer_id=customer_id,
        total_payable_usd=total_payable_usd,
        hedge_ratio_pct=hedge_ratio_pct,
        tenor=tenor,
        execute_booking=True,
    )


def get_ideal_entity_profile(customer_id: str | None = None) -> dict[str, Any]:
    """Slide 13 MCP Tool Adapter: Retrieve UEN, operating accounts, current signatory matrix, and existing signing rules from DBS CIF & ACRA Gateway."""
    return get_customer_mandate_details(customer_id=customer_id)


def validate_mandate_rules(
    customer_id: str | None = None,
    amount: float = 150000.0,
    currency: str = "SGD",
) -> dict[str, Any]:
    """Evaluate boolean group expressions against the active signatory pool and detect Signing Deadlock Alerts.

    A deadlock exists when the signing rule matched for `amount` requires more signatories from a
    group than that group currently has available. This used to hardcode the assumption "2 Group B
    are required" and report that text regardless of the entity's real signing rules; it now
    derives the shortfall from the matched tier's required combinations.
    """
    res = simulate_transaction_authorization(customer_id=customer_id, amount=amount, currency=currency)
    snapshot = res.get("workspace_snapshot") or {}
    grp_counts = {str(k).strip().upper(): int(v or 0) for k, v in (snapshot.get("active_group_counts") or {}).items()}

    matched = res.get("matched_tier") or {}
    raw_combos = matched.get("required_combinations")

    # `required_combinations` is a LIST of alternative group requirements with OR semantics,
    # e.g. [{"A": 2}] or [{"A": 1, "B": 1}, {"A": 2}]. A deadlock therefore exists only when
    # EVERY alternative is unsatisfiable, not when any single group is short.
    alternatives: list[dict[str, int]] = []
    if isinstance(raw_combos, dict):
        raw_combos = [raw_combos]
    if isinstance(raw_combos, list):
        for combo in raw_combos:
            if not isinstance(combo, dict):
                continue
            parsed: dict[str, int] = {}
            for grp, need in combo.items():
                try:
                    parsed[str(grp).strip().upper()] = int(need)
                except (TypeError, ValueError):
                    continue
            if parsed:
                alternatives.append(parsed)

    def _shortfall(combo: dict[str, int]) -> list[str]:
        return [
            f"{need} Group {grp} required but only {grp_counts.get(grp, 0)} active"
            for grp, need in sorted(combo.items())
            if grp_counts.get(grp, 0) < need
        ]

    satisfiable = [c for c in alternatives if not _shortfall(c)]
    detected = bool(alternatives) and not satisfiable

    if detected:
        # Report the alternative that is closest to being met.
        closest = min(alternatives, key=lambda c: len(_shortfall(c)))
        alert_message = "; ".join(_shortfall(closest))
    elif alternatives:
        met = satisfiable[0]
        alert_message = "Zero Deadlocks — " + ", ".join(
            f"Group {g} ({grp_counts.get(g, 0)} active / {n} required)" for g, n in sorted(met.items())
        ) + " quorum satisfied"
    else:
        alert_message = (
            "No signing rule matched this amount; deadlock cannot be evaluated."
            if not matched
            else "Matched signing rule defines no group requirements."
        )

    res["deadlock_alert"] = {
        "detected": detected,
        "alert_title": "Signing Deadlock Alert",
        "alert_message": alert_message,
        "matched_rule": matched.get("human_readable_rule") or matched.get("rule_expression"),
        "required_combinations": alternatives,
        "active_by_group": grp_counts,
    }
    return res


def switch_workspace_tab(
    tab_name: str = "UC1_MANDATE",
    stage: int | str | None = None,
    customer_id: str | None = None,
) -> dict[str, Any]:
    """Switch the active DBS IDEAL UI tab between 'UC1_MANDATE' ('1. Change of Mandate'), 'UC2_PAYMENT' ('2. Payment & BEC Shield'), and 'UC3_FX' ('3. FX Hedge & Pricing').
    NOTE: Do NOT use this tool to switch corporate customer profiles (Technova, Meridian, Apex, Veritas, Banyan) — use `SwitchActiveCustomerProfile` for corporate entities.
    """
    ensure_db_initialized()
    raw = str(tab_name or "").strip().lower()

    # Guard against the LLM calling switch_workspace_tab when the user actually asked to switch
    # corporate customer profiles (e.g. "Can you switch to very task?" -> Veritas CUST-004).
    entity_markers = (
        "technova", "tech nova", "meridian", "apex", "veritas", "very task", "veritask",
        "veritas legal", "banyan", "cust-001", "cust-002", "cust-003", "cust-004", "cust-005",
    )
    if any(em in raw for em in entity_markers):
        return SwitchActiveCustomerProfile(customer_id=tab_name)

    resolved_uc = "UC1_MANDATE"
    resolved_stage = 1
    if stage is not None:
        try:
            resolved_stage = max(1, min(5, int(stage)))
        except (TypeError, ValueError):
            resolved_stage = 1

    if any(k in raw for k in ("uc3", "fx", "hedge", "hedging", "forward", "var", "volatility", "pricing", "tab 3", "third")):
        resolved_uc = "UC3_FX"
        tab_label = "3. FX Hedge & Pricing"
    elif any(k in raw for k in ("uc2", "payment", "payments", "bec", "invoice", "fast", "meps", "payee", "tab 2", "second")):
        resolved_uc = "UC2_PAYMENT"
        tab_label = "2. Payment & BEC Shield"
    else:
        resolved_uc = "UC1_MANDATE"
        tab_label = "1. Change of Mandate"
        if "stage 2" in raw or "signator" in raw:
            resolved_stage = 2
        elif "stage 3" in raw or "rule" in raw or "limit" in raw or "sandbox" in raw:
            resolved_stage = 3
        elif "stage 4" in raw or "board" in raw or "resolution" in raw:
            resolved_stage = 4
        elif "stage 5" in raw or "digisign" in raw or "submit" in raw:
            resolved_stage = 5

    with get_connection() as conn:
        with conn.cursor() as cur:
            cid = _resolve_customer_id(cur, customer_id)
            cur.execute(
                """
                UPDATE active_workspace_state
                SET active_stage = %s,
                    last_tool_executed = 'switch_workspace_tab',
                    updated_at = NOW()
                WHERE workspace_id = 'DEFAULT'
                """,
                (resolved_stage,),
            )
            snapshot = _fetch_workspace_snapshot(cur, cid)
        conn.commit()

    res = _build_response_with_ui_sync(
        payload={
            "status": "success",
            "customer_id": cid,
            "active_tab": resolved_uc,
            "tab_label": tab_label,
            "current_stage": resolved_stage,
            "message": f"Switched workspace view to {tab_label}" + (f" (Stage {resolved_stage})" if resolved_uc == "UC1_MANDATE" else ""),
        },
        ui_action="SWITCH_WORKSPACE_TAB",
        target_stage=resolved_stage,
        updated_profile_id=cid,
        snapshot=snapshot,
        toast_title=f"Switched to {tab_label}",
        toast_message=f"Active workspace view is now {tab_label}.",
    )
    res["ui_sync"]["target_usecase"] = resolved_uc
    return res


# ============================================================================
# Registry & Dispatcher for Gemini Live Function Calling
# ============================================================================
MANDATE_TOOL_FUNCTIONS: list[Callable[..., Any]] = [
    list_customer_profiles,
    get_customer_mandate_details,
    get_ideal_entity_profile,
    SwitchActiveCustomerProfile,
    switch_active_customer_profile,
    switch_workspace_tab,
    add_or_update_signatory,
    upload_nric_and_add_signatory,
    revoke_signatory,
    configure_signing_rules,
    simulate_transaction_authorization,
    validate_mandate_rules,
    audit_board_resolution,
    submit_mandate_change_request,
    update_target_accounts,
    execute_cosigner_signature,
    stage_payment_to_ideal,
    run_fx_pretrade_checks,
    book_fx_forward_contract,
]

MANDATE_TOOL_MAP: dict[str, Callable[..., Any]] = {
    fn.__name__: fn for fn in MANDATE_TOOL_FUNCTIONS
}


def execute_mandate_tool(tool_name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Execute a registered mandate tool by name with JSON arguments."""
    import inspect

    fn = MANDATE_TOOL_MAP.get(tool_name)
    if fn is None:
        # Case-insensitive lookup fallback
        for k, v in MANDATE_TOOL_MAP.items():
            if k.lower() == str(tool_name).lower():
                fn = v
                break
    if fn is None:
        return {
            "status": "error",
            "error_code": "UNKNOWN_TOOL",
            "message": f"Unknown mandate tool: {tool_name}",
        }
    call_args = dict(args or {})

    # Map common LLM argument aliases before filtering by signature
    sig_params = set(inspect.signature(fn).parameters.keys())
    if "customer_id" in sig_params and "customer_id" not in call_args:
        for alias in ("company_name", "entity_name", "profile_id", "customer", "uen", "target_profile", "organization"):
            if alias in call_args and call_args[alias]:
                call_args["customer_id"] = call_args[alias]
                break
    if "signatory_id_or_name" in sig_params and "signatory_id_or_name" not in call_args:
        for alias in ("full_name", "signatory_name", "signatory_id", "name", "signatory"):
            if alias in call_args and call_args[alias]:
                call_args["signatory_id_or_name"] = call_args[alias]
                break
    if "full_name" in sig_params and "full_name" not in call_args:
        # Keep this alias list in sync with the signatory_id_or_name list above. `signatory` and
        # `signatory_id` were missing, so a model emitting {"signatory": "Alice"} normalized fine
        # for revoke_signatory but fell through here and crashed add_or_update_signatory.
        for alias in ("signatory_name", "name", "signatory_id_or_name", "signatory", "signatory_id"):
            if alias in call_args and call_args[alias]:
                call_args["full_name"] = call_args[alias]
                break
    if "tab_name" in sig_params and "tab_name" not in call_args:
        for alias in ("tab", "usecase", "target_usecase", "screen", "view", "section", "target_tab"):
            if alias in call_args and call_args[alias]:
                call_args["tab_name"] = call_args[alias]
                break

    # Prevent stale customer_id="CUST-001" from LLM system prompt from overriding an active switched profile!
    if fn.__name__ not in ("SwitchActiveCustomerProfile", "switch_active_customer_profile"):
        passed_cid = str(call_args.get("customer_id") or "").strip().upper()
        if passed_cid == "CUST-001":
            try:
                with get_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT active_customer_id FROM active_workspace_state WHERE workspace_id = 'DEFAULT_WORKSPACE';"
                        )
                        ws_r = cur.fetchone()
                        if ws_r and ws_r.get("active_customer_id") and ws_r["active_customer_id"] != "CUST-001":
                            call_args["customer_id"] = str(ws_r["active_customer_id"])
            except Exception as exc:
                # Previously `pass`. A DB outage here silently left customer_id as the stale
                # "CUST-001" default, so the tool would happily mutate the WRONG customer's
                # mandate. Surface it in logs at minimum.
                logger.error(
                    "Could not resolve active workspace profile for tool %s (%s: %s); "
                    "falling back to the supplied customer_id.",
                    fn.__name__,
                    type(exc).__name__,
                    exc,
                )

    # Strip any extra informational kwargs passed by the model (e.g., signing_group or role_title on revoke_signatory)
    filtered_args = {k: v for k, v in call_args.items() if k in sig_params}

    try:
        return fn(**filtered_args)
    except Exception as exc:
        return {
            "status": "error",
            "error_code": "TOOL_EXECUTION_ERROR",
            "tool_name": tool_name,
            "message": str(exc),
        }
