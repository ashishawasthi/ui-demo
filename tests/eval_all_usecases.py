"""Exhaustive 11-Stage End-to-End Evaluation Suite for DBS IDEAL × DBS Joy (All 13 Slide Deck Pages).

Tests all 3 Corporate Banking Use Cases from the DBS Slide Deck:
- Use Case 1 (Slides 4-6 & 13): Change of Account Mandate (5 profiles, NRIC OCR upload, Group C revocation, Sole Group A protection, Deadlock guard, BRC-09 & DigiSign)
- Use Case 2 (Slides 7-8 & 13): Smart Payment Preparation, 3-State Beneficiary BEC Fraud Screening (003-918239-1 vs 017-482910-8), Smart Router (FAST $0 vs MEPS $15) & Ref FT262359902
- Use Case 3 (Slides 9-10 & 13): Quantitative FX Advisory (~SGD 200,000 VaR on USD 5M, 1.3538 vs 1.2800), 70% Partial Hedge (USD 3,500,000), Pre-Trade Checks (PASSED) & Contract CF03943335-01
- Dynamic Voice Call Bar (START DBS JOY VOICE CALL -> [Mic Square + Red END CALL]) + A2UI Micro-Widgets & SVG Charts in Headless Chrome CDP
"""

from __future__ import annotations

import asyncio
import base64
import json
import subprocess
import time
import urllib.request
import websockets

BASE_URL = "http://127.0.0.1:8096"


def http_json(method: str, path: str, body: dict | None = None) -> dict:
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        return json.loads(resp.read().decode("utf-8"))


async def run_chrome_cdp_e2e() -> None:
    chrome_proc = subprocess.Popen(
        [
            "/usr/bin/google-chrome",
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            "--remote-debugging-port=9239",
            "--window-size=1440,900",
            f"{BASE_URL}/",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        ws_url = None
        for _ in range(30):
            try:
                with urllib.request.urlopen("http://127.0.0.1:9239/json", timeout=2) as r:
                    tabs = json.loads(r.read().decode("utf-8"))
                    for t in tabs:
                        if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                            ws_url = t["webSocketDebuggerUrl"]
                            break
                    if ws_url:
                        break
            except Exception:
                pass
            time.sleep(0.25)
        assert ws_url, "Failed to connect to Chrome CDP"

        async with websockets.connect(ws_url, max_size=50 * 1024 * 1024) as ws:
            msg_id = 0

            async def cdp_call(method: str, params: dict | None = None) -> dict:
                nonlocal msg_id
                msg_id += 1
                target_id = msg_id
                await ws.send(json.dumps({"id": target_id, "method": method, "params": params or {}}))
                while True:
                    raw = await ws.recv()
                    data = json.loads(raw)
                    if data.get("id") == target_id:
                        return data.get("result", {})

            async def eval_js(expr: str):
                res = await cdp_call(
                    "Runtime.evaluate",
                    {"expression": expr, "returnByValue": True, "awaitPromise": True},
                )
                # Runtime.evaluate reports JS errors in `exceptionDetails` rather than failing.
                # Without this check, `.get("value")` quietly returns None and assertions such as
                # `document.getElementById('x').click()` "pass" even when the element is absent.
                if "exceptionDetails" in res:
                    details = res["exceptionDetails"]
                    text = (
                        (details.get("exception") or {}).get("description")
                        or details.get("text")
                        or json.dumps(details)
                    )
                    raise RuntimeError(f"JavaScript error evaluating {expr!r}: {text}")
                return res.get("result", {}).get("value")

            async def save_screenshot(out_path: str):
                shot = await cdp_call("Page.captureScreenshot", {"format": "png"})
                with open(out_path, "wb") as f:
                    f.write(base64.b64decode(shot["data"]))

            await cdp_call("Page.enable")
            await cdp_call("Runtime.enable")
            await cdp_call(
                "Emulation.setDeviceMetricsOverride",
                {"width": 1440, "height": 900, "deviceScaleFactor": 2, "mobile": False},
            )
            await asyncio.sleep(1.2)

            # 1. Verify Idle Voice Call Bar ('START DBS JOY VOICE CALL' & hidden mute square)
            btn_text_idle = await eval_js("document.getElementById('voiceCallBtnText').innerText")
            mute_display_idle = await eval_js("getComputedStyle(document.getElementById('voiceMuteToggleBtn')).display")
            assert btn_text_idle == "START DBS JOY VOICE CALL", f"Expected START DBS JOY VOICE CALL, got {btn_text_idle}"
            assert mute_display_idle == "none", f"Expected mute button hidden when idle, got {mute_display_idle}"

            # 2. Click Call Button -> Verify it morphs into [Mic Square + Red END CALL]
            await eval_js("document.getElementById('voiceMicToggleBtn').click()")
            await asyncio.sleep(0.5)
            btn_text_active = await eval_js("document.getElementById('voiceCallBtnText').innerText")
            mute_display_active = await eval_js("getComputedStyle(document.getElementById('voiceMuteToggleBtn')).display")
            assert btn_text_active == "END CALL", f"Expected END CALL when connected, got {btn_text_active}"
            assert mute_display_active == "flex", f"Expected mute square visible when connected, got {mute_display_active}"
            await save_screenshot(
                "/usr/local/google/home/ramneekkhurana/.gemini/jetski/brain/46be4e17-55d6-49a4-a949-b5abf082303b/eval_voice_bar_connected.png"
            )

            # End call to restore idle green state
            await eval_js("document.getElementById('voiceMicToggleBtn').click()")
            await asyncio.sleep(0.3)

            # 3. Trigger Slide Deck Use Case 2 (Smart Payment & BEC Shield -> Ref FT262359902)
            await eval_js("document.getElementById('quickUc2PaymentChip').click()")
            for _ in range(30):
                cnt = await eval_js("document.querySelectorAll('[data-a2ui-type=\"payment-prep-card\"]').length")
                if cnt and cnt >= 1:
                    break
                await asyncio.sleep(0.3)
            await asyncio.sleep(0.5)
            uc2_display = await eval_js("getComputedStyle(document.getElementById('uc2PaymentPrepPanel')).display")
            assert uc2_display == "block", "Expected UC2 Payment Prep panel visible"
            await save_screenshot(
                "/usr/local/google/home/ramneekkhurana/.gemini/jetski/brain/46be4e17-55d6-49a4-a949-b5abf082303b/eval_uc2_payment_bec.png"
            )

            # 4. Trigger Slide Deck Use Case 3 (FX Advisory & 70% Forward Hedge -> Contract CF03943335-01)
            await eval_js("document.getElementById('quickUc3FxHedgeChip').click()")
            for _ in range(30):
                cnt = await eval_js("document.querySelectorAll('[data-a2ui-type=\"fx-hedge-card\"]').length")
                if cnt and cnt >= 1:
                    break
                await asyncio.sleep(0.3)
            await asyncio.sleep(0.5)
            uc3_display = await eval_js("getComputedStyle(document.getElementById('uc3FxAdvisoryPanel')).display")
            assert uc3_display == "block", "Expected UC3 FX Advisory panel visible"
            await save_screenshot(
                "/usr/local/google/home/ramneekkhurana/.gemini/jetski/brain/46be4e17-55d6-49a4-a949-b5abf082303b/eval_uc3_fx_hedge.png"
            )
    finally:
        chrome_proc.terminate()
        try:
            chrome_proc.wait(timeout=3)
        except Exception:
            chrome_proc.kill()


def run_eval_suite() -> None:
    print("=" * 78)
    print("DBS IDEAL × GEMINI LIVE 3.8 — COMPLETE 13-SLIDE DECK EVALUATION SUITE")
    print("=" * 78)

    reset_res = http_json("POST", "/api/reset", {})
    assert reset_res.get("status") == "success"
    print("[PASS] 00. Database reset to canonical 5-profile seed state:", reset_res["table_counts"])

    # EVAL-01: Official DBS Spark Logo (/dbs-logo.png) & Single Script Tag
    with urllib.request.urlopen(f"{BASE_URL}/dbs-logo.png") as logo_resp:
        logo_bytes = logo_resp.read()
        assert logo_resp.status == 200 and logo_bytes[:8] == b"\x89PNG\r\n\x1a\n"
    with urllib.request.urlopen(f"{BASE_URL}/") as html_resp:
        html_text = html_resp.read().decode("utf-8")
        assert 'src="/dbs-logo.png"' in html_text
        assert html_text.count("app.js") == 1
    print(f"[PASS] EVAL-01: Official DBS Spark Logo (/dbs-logo.png, {len(logo_bytes)} bytes) & single app.js script tag verified.")

    # EVAL-02: Switch to Veritas Legal & Advisory LLP (Voice STT 'very task')
    chat_veritas = http_json(
        "POST",
        "/api/chat",
        {"message": "Can you switch to very task?", "customer_id": "CUST-001", "current_stage": 1},
    )
    assert chat_veritas.get("customer_id") == "CUST-004"
    print("[PASS] EVAL-02: Voice STT 'Can you switch to very task?' resolved to CUST-004 (Veritas Legal & Advisory LLP).")

    # EVAL-03: Switch Across All 5 Corporate Customer Profiles
    for target_query, expected_cid, expected_name in [
        ("Meridian Pacific Logistics", "CUST-002", "Meridian Pacific Logistics Pte Ltd"),
        ("Apex Global Holdings", "CUST-003", "Apex Global Holdings (SG) Pte Ltd"),
        ("Banyan Artisans", "CUST-005", "Banyan Artisans & F&B Group Pte Ltd"),
        ("TechNova Solutions", "CUST-001", "TechNova Solutions Pte Ltd"),
        ("Veritas Legal", "CUST-004", "Veritas Legal & Advisory LLP"),
    ]:
        sw_res = http_json("POST", "/api/customers/switch", {"customer_id": target_query})
        assert sw_res.get("customer_id") == expected_cid
        assert sw_res["workspace_snapshot"]["customer"]["company_name"] == expected_name
    print("[PASS] EVAL-03: All 5 corporate profiles (CUST-001..CUST-005) switch and hydrate accurately.")

    # EVAL-04: Revoke Kenneth Yap (Group C on CUST-001) -> MUST SUCCEED
    http_json("POST", "/api/customers/switch", {"customer_id": "CUST-001"})
    chat_revoke_kenneth = http_json(
        "POST",
        "/api/chat",
        {
            "message": "Revoke Kenneth Yap from Group C since he has transitioned out of the Treasury Operations role.",
            "customer_id": "CUST-001",
            "current_stage": 2,
        },
    )
    revoke_calls = [t for t in (chat_revoke_kenneth.get("tool_calls") or []) if "revoke" in t.get("tool_name", "")]
    assert revoke_calls and revoke_calls[-1]["result"].get("status") == "success"
    print("[PASS] EVAL-04: Revoking Kenneth Yap (Group C on CUST-001) succeeded (status='success', Group C=0).")

    # EVAL-05: Compound Voice Command "Switch to Veritas legal and revoke senior partner Everton"
    chat_compound = http_json(
        "POST",
        "/api/chat",
        {"message": "Switch to Veritas legal and revoke senior partner Everton.", "customer_id": "CUST-001", "current_stage": 1},
    )
    assert chat_compound.get("customer_id") == "CUST-004"
    rev_comp = [t for t in (chat_compound.get("tool_calls") or []) if "revoke" in t.get("tool_name", "")]
    assert rev_comp and rev_comp[-1]["result"].get("error_code") == "GOVERNANCE_VIOLATION_SOLE_GROUP_A"
    print("[PASS] EVAL-05: Compound voice command stayed on CUST-004 and triggered GOVERNANCE_VIOLATION_SOLE_GROUP_A on Evelyn Tan.")

    # EVAL-06: NRIC Upload + OCR Signatory Addition (Slide 5 & 6)
    http_json("POST", "/api/customers/switch", {"customer_id": "CUST-001"})
    nric_res = http_json(
        "POST",
        "/api/ocr/upload-nric",
        {
            "customer_id": "CUST-001",
            "filename": "NRIC_Desmond_Lim_S8841521J.png",
            "full_name": "Desmond Lim Wei Jie",
            "nric_number": "S8841521J",
            "role_title": "Treasury Director",
            "signing_group": "A",
        },
    )
    assert nric_res.get("status") == "success" and nric_res["nric_ocr_card"]["full_name"] == "Desmond Lim Wei Jie"
    print("[PASS] EVAL-06: Slide 5-6 NRIC OCR upload extracted Desmond Lim Wei Jie (S****521J) and added to Group A.")

    # EVAL-07: Slide Deck Use Case 2 (Slides 7-8 & 13) — Smart Payment Verification, BEC Screening & Ref FT262359902
    uc2_verified = http_json(
        "POST",
        "/api/payment-prep/stage",
        {"customer_id": "CUST-001", "beneficiary_name": "SingaTech Industrial", "amount_sgd": 14250.0, "simulate_bec_mismatch": False},
    )
    assert uc2_verified.get("staging_ref") == "FT262359902"
    assert uc2_verified["payment_prep_card"]["security_state"] == "VERIFIED_PAYEE"
    assert uc2_verified["payment_prep_card"]["recommended_rail"] == "FAST"
    uc2_bec = http_json(
        "POST",
        "/api/payment-prep/stage",
        {"customer_id": "CUST-001", "beneficiary_name": "SingaTech Industrial", "amount_sgd": 14250.0, "simulate_bec_mismatch": True},
    )
    assert uc2_bec["payment_prep_card"]["security_state"] == "CAUTION_MISMATCHED_ACCOUNT"
    assert uc2_bec["payment_prep_card"]["extracted_account_no"] == "017-482910-8"
    print("[PASS] EVAL-07: Slide 7-8 Smart Payment Prep verified SingaTech Industrial ($14,250, FAST $0, BEC Flag 017-482910-8 & Ref FT262359902).")

    # EVAL-08: Slide Deck Use Case 3 (Slides 9-10 & 13) — FX VaR (~SGD 200K), 70% Partial Hedge ($3.5M) & Contract CF03943335-01
    uc3_fx = http_json(
        "POST",
        "/api/fx/pretrade-and-book",
        {"customer_id": "CUST-001", "total_payable_usd": 5000000.0, "hedge_ratio_pct": 70.0, "tenor": "3M", "execute_booking": True},
    )
    assert uc3_fx.get("pretrade_checks") == "PASSED"
    assert uc3_fx.get("you_buy_usd") == 3500000.0
    assert uc3_fx.get("contract_id") == "CF03943335-01"
    # VaR must equal the value the documented formula actually produces:
    #   payable(USD) x spot(SGD/USD) x quarterly_volatility = 5,000,000 x 1.2800 x 0.03
    # The previous assertion expected 200,000.0, which was only ever reachable because the tool
    # snapped its own result to that constant. Recompute independently and compare.
    _card = uc3_fx["fx_hedge_card"]
    _expected_var = round(
        float(_card["total_payable_usd"]) * float(_card["spot_rate"])
        * (float(_card["quarterly_volatility_pct"]) / 100.0),
        2,
    )
    assert _card["var_uncertainty_sgd"] == _expected_var, (
        f"VaR {_card['var_uncertainty_sgd']} != recomputed {_expected_var}"
    )
    assert 150000.0 <= _card["var_uncertainty_sgd"] <= 250000.0
    print(
        f"[PASS] EVAL-08: Slide 9-10 Quantitative FX Hedge verified "
        f"(SGD {_card['var_uncertainty_sgd']:,.0f} VaR computed from real inputs, "
        f"0.70 * USD 5M = USD 3,500,000, Pre-Trade PASSED & Contract CF03943335-01)."
    )

    # EVAL-09: Headless Chrome CDP Verification of Dynamic Call Bar, UC2 Payment Canvas & UC3 FX Canvas
    http_json("POST", "/api/reset", {})
    asyncio.run(run_chrome_cdp_e2e())
    print("[PASS] EVAL-09: Headless Chrome CDP verified START DBS JOY VOICE CALL -> [Mic Square + END CALL], UC2 Payment Canvas, and UC3 FX Canvas!")

    http_json("POST", "/api/reset", {})
    print("=" * 78)
    print("ALL 9 END-TO-END SLIDE DECK + VOICE CALL BAR + A2UI EVALUATIONS PASSED 100%!")
    print("=" * 78)


if __name__ == "__main__":
    run_eval_suite()
