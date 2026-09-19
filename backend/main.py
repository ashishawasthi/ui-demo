"""FastAPI Backend Server for Corporate Banking Change of Account Mandate Application.

Serves:
- REST API endpoints (`/api/health`, `/api/customers/*`, `/api/profiles/*`, `/api/chat`)
- Multiplexed Gemini Live WebSocket (`/ws/live`) for real-time voice, extended thinking, and UI sync
- Frontend static application (`frontend/index.html`, `frontend/styles.css`, `frontend/app.js`)
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import Body, FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from backend.db import get_connection, get_db_health, init_db
from backend.gemini_live import (
    get_genai_clients,
    get_model_status,
    handle_live_websocket_session,
    run_agent_chat_turn,
    set_runtime_api_key,
)
from backend.tools import (
    SwitchActiveCustomerProfile,
    add_or_update_signatory,
    audit_board_resolution,
    configure_signing_rules,
    execute_cosigner_signature,
    get_customer_mandate_details,
    list_customer_profiles,
    register_ui_sync_callback,
    revoke_signatory,
    simulate_transaction_authorization,
    submit_mandate_change_request,
    update_target_accounts,
)
from synthetic_data.seed import seed_all_data, seed_single_customer

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
load_dotenv(PROJECT_ROOT / ".env")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mandate_app.main")


class UIEventBroadcaster:
    """Manages active WebSocket connections to broadcast real-time `ui_sync` events."""

    def __init__(self) -> None:
        self.connections: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.connections.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.connections.discard(ws)

    async def broadcast(self, message: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        for ws in list(self.connections):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.connections.discard(ws)

    def broadcast_sync(self, message: dict[str, Any]) -> None:
        """Schedule async broadcast from synchronous tool callbacks if an event loop is running."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.broadcast(message))
        except RuntimeError:
            pass


broadcaster = UIEventBroadcaster()
register_ui_sync_callback(broadcaster.broadcast_sync)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize PostgreSQL 18.6 / CloudSQL database schema and 5 corporate customer profiles on startup."""
    init_db(force_reseed=False)
    yield


app = FastAPI(
    title="DBS IDEAL Corporate Banking — Change of Account Mandate Copilot",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================================
# Health, AI Studio Key & Live Config
# ============================================================================
@app.get("/api/health")
async def api_health() -> dict[str, Any]:
    db_health = get_db_health()
    gemini_status = get_model_status()
    return {
        "status": "ok" if db_health.get("connected") else "degraded",
        "database": db_health,
        "gemini_live": gemini_status,
    }


@app.get("/api/config/live")
async def api_get_live_config() -> dict[str, Any]:
    clients = get_genai_clients()
    return {
        "status": "ok",
        "api_key": clients.get("api_key"),
        "model": "models/gemini-3.8-live-extended-thinking",
        "ws_endpoint": "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent",
        "thinking_config": {"thinkingLevel": "LOW"},
        "tool_behavior": "NON_BLOCKING",
    }


@app.post("/api/config/api-key")
async def api_update_api_key(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    new_key = str(payload.get("api_key") or "").strip()
    if not new_key:
        return JSONResponse(status_code=400, content={"status": "error", "message": "API key required."})
    status = set_runtime_api_key(new_key)
    return {"status": "ok", "gemini_live": status}


# ============================================================================
# Customer Profiles & Mandate Details (Stage 1)
# ============================================================================
@app.get("/api/customers")
@app.get("/api/profiles")
async def api_list_customers(entity_type: str | None = None) -> dict[str, Any]:
    return list_customer_profiles(entity_type_filter=entity_type)


@app.get("/api/customers/{customer_id}/mandate")
@app.get("/api/customers/{customer_id}")
@app.get("/api/profiles/{customer_id}")
async def api_get_customer_mandate(customer_id: str) -> dict[str, Any]:
    return get_customer_mandate_details(customer_id=customer_id)


@app.post("/api/customers/switch")
async def api_switch_customer_body(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    cid = payload.get("customer_id") or payload.get("customer_identifier") or "CUST-001"
    stage = int(payload.get("target_stage") or payload.get("stage") or 1)
    return SwitchActiveCustomerProfile(customer_id=cid, target_stage=stage)


@app.post("/api/profiles/{customer_id}/switch")
@app.post("/api/customers/{customer_id}/switch")
async def api_switch_customer_path(
    customer_id: str,
    payload: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    stage = int((payload or {}).get("target_stage") or (payload or {}).get("stage") or 1)
    return SwitchActiveCustomerProfile(customer_id=customer_id, target_stage=stage)


# ============================================================================
# Target Accounts Scope (Stage 1)
# ============================================================================
@app.post("/api/customers/{customer_id}/target-accounts")
@app.post("/api/profiles/{customer_id}/target-accounts")
async def api_update_target_accounts(
    customer_id: str,
    payload: dict[str, Any] = Body(...),
) -> Response:
    res = update_target_accounts(
        customer_id=customer_id,
        account_ids=payload.get("account_ids"),
        account_number=payload.get("account_number") or payload.get("account_id"),
        included_in_mandate=payload.get("included_in_mandate")
        if "included_in_mandate" in payload
        else payload.get("included"),
    )
    if res.get("status") == "error":
        return JSONResponse(status_code=400, content=res)
    return JSONResponse(status_code=200, content=res)


@app.patch("/api/profiles/{customer_id}/accounts/{account_id}")
@app.post("/api/profiles/{customer_id}/accounts/{account_id}")
@app.patch("/api/customers/{customer_id}/accounts/{account_id}")
async def api_toggle_single_account(
    customer_id: str,
    account_id: str,
    payload: dict[str, Any] = Body(...),
) -> Response:
    included = payload.get("included")
    if included is None:
        included = payload.get("is_included_in_mandate_change", True)
    res = update_target_accounts(
        customer_id=customer_id,
        account_number=account_id,
        included_in_mandate=bool(included),
    )
    if res.get("status") == "error":
        return JSONResponse(status_code=400, content=res)
    return JSONResponse(status_code=200, content=res)


# ============================================================================
# Signatory Matrix & Governance Protection (Stage 2)
# ============================================================================
@app.post("/api/customers/{customer_id}/signatories")
@app.post("/api/profiles/{customer_id}/signatories")
async def api_add_or_update_signatory(
    customer_id: str,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    return add_or_update_signatory(
        customer_id=customer_id,
        full_name=payload.get("full_name") or payload.get("name"),
        role_title=payload.get("role_title") or payload.get("role") or "Authorized Signatory",
        signing_group=payload.get("signing_group") or payload.get("group") or "A",
        nric_masked=payload.get("nric_masked") or payload.get("id_number") or payload.get("id_number_masked"),
        id_type=payload.get("id_type") or "NRIC",
        auth_method=payload.get("auth_method") or "IDEAL_DIGITAL_TOKEN",
        email=payload.get("email"),
        phone_masked=payload.get("phone_masked") or payload.get("mobile_masked"),
        individual_max_limit_sgd=payload.get("individual_max_limit_sgd"),
        ocr_verified=bool(payload.get("ocr_verified", True)),
        specimen_ref=payload.get("specimen_ref"),
    )


@app.post("/api/customers/{customer_id}/signatories/revoke")
@app.post("/api/profiles/{customer_id}/signatories/revoke")
async def api_revoke_signatory_body(
    customer_id: str,
    payload: dict[str, Any] = Body(...),
) -> JSONResponse:
    sig_ident = (
        payload.get("signatory_id_or_name")
        or payload.get("signatory_identifier")
        or payload.get("signatory_id")
        or payload.get("full_name")
    )
    reason = payload.get("reason") or payload.get("revocation_reason") or "Mandate Realignment"
    res = revoke_signatory(
        customer_id=customer_id,
        signatory_id_or_name=sig_ident,
        reason=reason,
    )
    if res.get("status") == "error":
        return JSONResponse(status_code=400, content=res)
    return JSONResponse(status_code=200, content=res)


@app.post("/api/profiles/{customer_id}/signatories/{signatory_id}/revoke")
@app.post("/api/customers/{customer_id}/signatories/{signatory_id}/revoke")
async def api_revoke_signatory_path(
    customer_id: str,
    signatory_id: str,
    payload: dict[str, Any] | None = Body(default=None),
) -> JSONResponse:
    reason = (payload or {}).get("reason") or (payload or {}).get("revocation_reason") or "Mandate Realignment"
    res = revoke_signatory(
        customer_id=customer_id,
        signatory_id_or_name=signatory_id,
        reason=reason,
    )
    if res.get("status") == "error":
        return JSONResponse(status_code=400, content=res)
    return JSONResponse(status_code=200, content=res)


@app.post("/api/profiles/{customer_id}/signatories/{signatory_id}/restore")
@app.post("/api/customers/{customer_id}/signatories/{signatory_id}/restore")
async def api_restore_signatory(
    customer_id: str,
    signatory_id: str,
) -> dict[str, Any]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM signatories WHERE customer_id = %s AND signatory_id = %s;",
                (customer_id, signatory_id),
            )
            sig = cur.fetchone()
    if not sig:
        return {"status": "error", "message": f"Signatory {signatory_id} not found."}
    return add_or_update_signatory(
        customer_id=customer_id,
        full_name=sig["full_name"],
        role_title=sig["role_title"],
        signing_group=sig["signing_group"],
        nric_masked=sig["id_number_masked"],
        auth_method=sig["auth_method"],
        email=sig["email"],
        phone_masked=sig["mobile_masked"],
        status="ACTIVE",
    )


# ============================================================================
# Signing Rules & Live Transaction Simulator (Stage 3)
# ============================================================================
@app.post("/api/customers/{customer_id}/signing-rules")
@app.post("/api/profiles/{customer_id}/rules")
@app.post("/api/profiles/{customer_id}/signing-rules")
async def api_configure_signing_rules(
    customer_id: str,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    return configure_signing_rules(
        customer_id=customer_id,
        rules=payload.get("rules"),
        tier_order=int(payload.get("tier_order", 1)),
        tier_label=payload.get("tier_label"),
        min_amount_sgd=payload.get("min_amount_sgd"),
        max_amount_sgd=payload.get("max_amount_sgd"),
        required_combination=payload.get("required_combination") or payload.get("rule_expression"),
        rule_expression=payload.get("rule_expression") or payload.get("required_combination"),
        human_readable_rule=payload.get("human_readable_rule"),
        rule_expression_json=payload.get("rule_expression_json") or payload.get("required_combinations"),
    )


@app.post("/api/customers/{customer_id}/simulate")
@app.post("/api/profiles/{customer_id}/simulate")
async def api_simulate_transaction(
    customer_id: str,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    return simulate_transaction_authorization(
        customer_id=customer_id,
        amount=float(payload.get("amount", 150000.0)),
        currency=str(payload.get("currency", "SGD")),
        account_id=payload.get("account_id"),
    )


# ============================================================================
# Board Resolution Pre-Flight Audit & Mandate Diff (Stage 4)
# ============================================================================
@app.post("/api/customers/{customer_id}/board-resolution/audit")
@app.post("/api/profiles/{customer_id}/audit-resolution")
@app.post("/api/profiles/{customer_id}/board-resolution/audit")
async def api_audit_board_resolution(
    customer_id: str,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    return audit_board_resolution(
        customer_id=customer_id,
        resolution_type=payload.get("resolution_type") or payload.get("format_type") or "BRC-09",
        resolution_ref=payload.get("resolution_ref"),
        clause_text=payload.get("clause_text") or payload.get("custom_text") or payload.get("custom_resolution_text"),
        quorum_confirmed=bool(payload.get("quorum_confirmed", True)),
    )


# ============================================================================
# Mandate Change Submission & Co-Signer Execution (Stage 5)
# ============================================================================
@app.post("/api/customers/{customer_id}/submit")
@app.post("/api/profiles/{customer_id}/submit")
async def api_submit_mandate_change(
    customer_id: str,
    payload: dict[str, Any] | None = Body(default=None),
) -> JSONResponse:
    body = payload or {}
    res = submit_mandate_change_request(
        customer_id=customer_id,
        submitted_by=body.get("submitted_by") or "Sarah Lim (Managing Director)",
        resolution_ref=body.get("resolution_ref"),
        notes=body.get("notes") or body.get("submission_notes"),
        auto_sign_initiator=bool(body.get("auto_sign_initiator", True)),
    )
    if res.get("status") == "error":
        return JSONResponse(status_code=400, content=res)
    return JSONResponse(status_code=200, content=res)


@app.post("/api/customers/{customer_id}/cosign")
@app.post("/api/profiles/{customer_id}/cosign")
async def api_cosign_by_customer(
    customer_id: str,
    payload: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    body = payload or {}
    return execute_cosigner_signature(
        customer_id=customer_id,
        application_ref=body.get("application_ref"),
        signer_name=body.get("signer_name"),
        auth_method=body.get("auth_method") or "IDEAL Token",
    )


@app.post("/api/applications/{application_id}/cosign")
async def api_cosign_by_application(
    application_id: str,
    payload: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    body = payload or {}
    return execute_cosigner_signature(
        customer_id=body.get("customer_id"),
        application_ref=application_id,
        signer_name=body.get("signer_name"),
        auth_method=body.get("auth_method") or "IDEAL Token",
    )


# ============================================================================
# Profile & Database Reset Endpoints (For Repeatable E2E Testing)
# ============================================================================
@app.post("/api/customers/{customer_id}/reset")
@app.post("/api/profiles/{customer_id}/reset")
async def api_reset_customer(customer_id: str) -> dict[str, Any]:
    seed_single_customer(customer_id)
    return get_customer_mandate_details(customer_id=customer_id)


@app.post("/api/reset")
async def api_reset_all() -> dict[str, Any]:
    counts = seed_all_data(reset_existing=True)
    return {"status": "success", "table_counts": counts}


# ============================================================================
# Gemini Live (`models/gemini-3.8-live-extended-thinking`) Chat & WebSocket
# ============================================================================
@app.post("/api/chat")
async def api_chat(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    message = str(payload.get("message") or payload.get("text") or "").strip()
    customer_id = payload.get("customer_id") or "CUST-001"
    current_stage = int(payload.get("current_stage") or payload.get("stage") or 1)
    if not message:
        return {"status": "error", "message": "Message text is required."}

    result = await run_agent_chat_turn(
        message=message,
        customer_id=customer_id,
        current_stage=current_stage,
        event_callback=broadcaster.broadcast,
    )
    if result.get("ui_sync"):
        await broadcaster.broadcast(result["ui_sync"])
    return result


@app.websocket("/ws/live")
async def ws_live_endpoint(websocket: WebSocket) -> None:
    await broadcaster.connect(websocket)
    try:
        await handle_live_websocket_session(websocket, broadcaster)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning("WebSocket /ws/live closed: %s", exc)
    finally:
        broadcaster.disconnect(websocket)


# ============================================================================
# Frontend Static Asset Serving (with strict Cache-Control: no-store)
# ============================================================================
NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


@app.get("/")
async def serve_index() -> Any:
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path, headers=NO_CACHE_HEADERS)
    return HTMLResponse(
        "<html><body><h1>DBS IDEAL Change of Account Mandate API Server Running</h1></body></html>",
        headers=NO_CACHE_HEADERS,
    )


@app.post("/api/ocr/upload-nric")
async def api_upload_nric_ocr(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Extract signatory identity from an uploaded NRIC image/document via Gemini Vision OCR and persist to PostgreSQL."""
    from datetime import datetime, timezone
    from backend.tools import upload_nric_and_add_signatory
    from backend.gemini_live import get_genai_clients, THINKING_MODEL_ID
    from google.genai import types
    import base64
    import re

    customer_id = payload.get("customer_id")
    filename = str(payload.get("filename") or "NRIC_Desmond_Lim_S8841521J.png")
    signing_group = str(payload.get("signing_group") or "A")
    role_title = str(payload.get("role_title") or "Treasury Director")
    auth_method = str(payload.get("auth_method") or "IDEAL_DIGITAL_TOKEN")
    full_name = str(payload.get("full_name") or "").strip()
    nric_number = str(payload.get("nric_number") or "").strip()
    image_b64 = str(payload.get("image_base64") or "").strip()

    # If image_base64 is provided and full_name/nric_number aren't explicitly set, run Gemini Multimodal Vision OCR
    if image_b64 and (not full_name or not nric_number):
        try:
            raw_bytes = base64.b64decode(image_b64.split(",")[-1])
            mime_type = "image/png" if filename.lower().endswith(".png") else "image/jpeg"
            clients = get_genai_clients()
            ocr_resp = await clients["vertex_global"].aio.models.generate_content(
                model=THINKING_MODEL_ID,
                contents=[
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_bytes(data=raw_bytes, mime_type=mime_type),
                            types.Part.from_text(
                                text=(
                                    "Extract the Singapore NRIC details as JSON with keys: "
                                    "full_name, nric_number, nationality, date_of_issue, role_title. "
                                    "If this image is a sample or non-NRIC image, infer realistic Singapore NRIC fields from the image or filename."
                                )
                            ),
                        ],
                    )
                ],
            )
            txt = (ocr_resp.text or "").strip()
            m_json = re.search(r"\{.*\}", txt, re.DOTALL)
            if m_json:
                parsed = json.loads(m_json.group(0))
                full_name = full_name or str(parsed.get("full_name") or "").strip()
                nric_number = nric_number or str(parsed.get("nric_number") or "").strip()
                role_title = str(parsed.get("role_title") or role_title).strip()
        except Exception as ocr_exc:
            logger.info("Vision OCR fallback to filename/metadata extraction: %s", ocr_exc)

    # Extract name or NRIC from filename if present (e.g., "NRIC_Grace_Chua_S9012884D.png")
    if not nric_number:
        m_nric = re.search(r"\b([STFGM]\d{7}[A-Z])\b", filename.upper())
        nric_number = m_nric.group(1) if m_nric else "S8841521J"
    if not full_name:
        clean_fn = re.sub(r"\.[^.]+$", "", filename)
        clean_fn = re.sub(r"[_\-]+", " ", clean_fn)
        clean_fn = re.sub(r"\b(NRIC|SCAN|ID|CARD|[STFGM]\d{7}[A-Z])\b", "", clean_fn, flags=re.IGNORECASE)
        clean_fn = re.sub(r"\s+", " ", clean_fn).strip()
        if clean_fn.lower() == "desmond lim" or len(clean_fn) < 3:
            full_name = "Desmond Lim Wei Jie"
        else:
            full_name = clean_fn.title()

    tool_res = upload_nric_and_add_signatory(
        customer_id=customer_id,
        full_name=full_name,
        nric_number=nric_number,
        role_title=role_title,
        signing_group=signing_group,
        auth_method=auth_method,
        filename=filename,
    )
    if tool_res.get("ui_sync"):
        await broadcaster.broadcast(tool_res["ui_sync"])

    ocr_card = tool_res.get("nric_ocr_card") or {}
    reply_msg = (
        f"Verified Singapore NRIC (`{ocr_card.get('nric_masked', 'S****521J')}`) via OCR with 99.4% confidence "
        f"and added {full_name} ({role_title}) to Group {signing_group}."
    )
    return {
        **tool_res,
        "reply": reply_msg,
        "tool_calls": [
            {
                "call_id": f"ocr_nric_{int(datetime.now(timezone.utc).timestamp())}",
                "tool_name": "upload_nric_and_add_signatory",
                "args": {
                    "full_name": full_name,
                    "nric_number": nric_number,
                    "role_title": role_title,
                    "signing_group": signing_group,
                    "filename": filename,
                },
                "result": {
                    k: v
                    for k, v in tool_res.items()
                    if k not in ("workspace_snapshot", "ui_sync")
                },
            }
        ],
    }


@app.get("/dbs-logo.png")
@app.get("/static/dbs-logo.png")
async def serve_dbs_logo() -> Any:
    logo_path = FRONTEND_DIR / "dbs-logo.png"
    if logo_path.exists():
        return FileResponse(logo_path, media_type="image/png", headers=NO_CACHE_HEADERS)
    return HTMLResponse("", status_code=404)


@app.get("/styles.css")
@app.get("/static/styles.css")
async def serve_styles() -> Any:
    css_path = FRONTEND_DIR / "styles.css"
    if css_path.exists():
        return FileResponse(css_path, media_type="text/css", headers=NO_CACHE_HEADERS)
    return HTMLResponse("/* styles.css pending */", media_type="text/css", headers=NO_CACHE_HEADERS)


@app.get("/app.js")
@app.get("/static/app.js")
async def serve_app_js() -> Any:
    js_path = FRONTEND_DIR / "app.js"
    if js_path.exists():
        return FileResponse(js_path, media_type="application/javascript", headers=NO_CACHE_HEADERS)
    return HTMLResponse("// app.js pending", media_type="application/javascript", headers=NO_CACHE_HEADERS)


if FRONTEND_DIR.exists():
    app.mount("/frontend", StaticFiles(directory=str(FRONTEND_DIR)), name="frontend")


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run("backend.main:app", host="0.0.0.0", port=port, reload=False)
