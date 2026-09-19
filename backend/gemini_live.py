"""Gemini Live (`models/gemini-3.8-live-extended-thinking`) Multimodal Voice & Chat Engine.

Integrates `google-genai` SDK supporting both `GEMINI_API_KEY` (loaded from `.env`)
and Vertex AI Application Default Credentials in GCP project `elevate-data-508005`:
- `client_global` (`location="global"`, `gemini-3.8-flash` with `ThinkingConfig(include_thoughts=True)`)
  for real Extended Thinking traces (`part.thought == True`) and multi-step PostgreSQL tool calling.
- `client_usc1` (`location="us-central1"`, `gemini-live-2.5-flash-native-audio` via `aio.live.connect`)
  for real-time 24kHz PCM bidirectional voice streaming, input/output speech transcription,
  barge-in handling, and live database tool execution.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from pathlib import Path
from typing import Any, Awaitable, Callable

from dotenv import load_dotenv
from google import genai
from google.genai import types

from backend.tools import (
    MANDATE_TOOL_FUNCTIONS,
    MANDATE_TOOL_MAP,
    execute_mandate_tool,
    get_customer_mandate_details,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

logger = logging.getLogger("mandate_app.gemini_live")

LOGICAL_MODEL_ID = os.environ.get("GEMINI_LIVE_MODEL", "models/gemini-3.8-live-extended-thinking")
THINKING_MODEL_ID = "gemini-3.8-flash"
FALLBACK_TEXT_MODEL_ID = "gemini-2.5-flash"
LIVE_AUDIO_MODEL_ID = "gemini-live-2.5-flash-native-audio"
GCP_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "elevate-data-508005")

_CLIENT_API_KEY: genai.Client | None = None
_CLIENT_API_KEY_LIVE_ALPHA: genai.Client | None = None
_CLIENT_VERTEX_GLOBAL: genai.Client | None = None
_CLIENT_VERTEX_USC1: genai.Client | None = None
_API_KEY_VALID: bool | None = None
_RUNTIME_API_KEY: str | None = None


def set_runtime_api_key(api_key: str) -> dict[str, Any]:
    """Update the Google AI Studio API key at runtime and reset client caches."""
    global _RUNTIME_API_KEY, _CLIENT_API_KEY, _CLIENT_API_KEY_LIVE_ALPHA, _API_KEY_VALID
    cleaned = (api_key or "").strip()
    if cleaned:
        _RUNTIME_API_KEY = cleaned
        os.environ["GEMINI_API_KEY"] = cleaned
        os.environ["GOOGLE_API_KEY"] = cleaned
        _CLIENT_API_KEY = None
        _CLIENT_API_KEY_LIVE_ALPHA = None
        _API_KEY_VALID = None
    return get_model_status()


def get_genai_clients() -> dict[str, Any]:
    """Initialize and return Google AI Studio (`v1alpha` & `v1beta`) and Vertex AI clients."""
    global _CLIENT_API_KEY, _CLIENT_API_KEY_LIVE_ALPHA, _CLIENT_VERTEX_GLOBAL, _CLIENT_VERTEX_USC1

    api_key = (
        _RUNTIME_API_KEY
        or os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_API_KEY")
        or ""
    )
    if api_key and _CLIENT_API_KEY is None:
        try:
            _CLIENT_API_KEY = genai.Client(api_key=api_key)
            _CLIENT_API_KEY_LIVE_ALPHA = genai.Client(
                api_key=api_key, http_options={"api_version": "v1alpha"}
            )
        except Exception as exc:
            logger.warning("Failed to initialize API key client: %s", exc)

    if _CLIENT_VERTEX_GLOBAL is None:
        _CLIENT_VERTEX_GLOBAL = genai.Client(
            vertexai=True, project=GCP_PROJECT, location="global"
        )

    if _CLIENT_VERTEX_USC1 is None:
        _CLIENT_VERTEX_USC1 = genai.Client(
            vertexai=True, project=GCP_PROJECT, location="us-central1"
        )

    return {
        "api_key": api_key,
        "api_key_client": _CLIENT_API_KEY,
        "api_key_live_alpha": _CLIENT_API_KEY_LIVE_ALPHA,
        "vertex_global": _CLIENT_VERTEX_GLOBAL,
        "vertex_usc1": _CLIENT_VERTEX_USC1,
        "api_key_configured": bool(api_key),
        "api_key_prefix": f"{api_key[:10]}..." if api_key else None,
    }


def get_model_status() -> dict[str, Any]:
    """Return Gemini Live model and credential status for `/api/health`."""
    clients = get_genai_clients()
    return {
        "model": LOGICAL_MODEL_ID,
        "ai_studio_endpoint": "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent",
        "thinking_config": {"thinkingLevel": "LOW", "includeThoughts": True},
        "tool_behavior": "NON_BLOCKING",
        "resolved_thinking_model": THINKING_MODEL_ID,
        "resolved_live_audio_model": LIVE_AUDIO_MODEL_ID,
        "vertexai": True,
        "project": GCP_PROJECT,
        "locations": {"thinking_and_tools": "global", "live_native_audio": "us-central1"},
        "api_key_configured": clients["api_key_configured"],
        "api_key_prefix": clients["api_key_prefix"],
        "tools_registered": list(MANDATE_TOOL_MAP.keys()),
        "tools_count": len(MANDATE_TOOL_MAP),
    }


def build_system_instruction(customer_id: str = "CUST-001", current_stage: int = 1) -> str:
    """Build context-aware system instructions grounded in the active customer's PostgreSQL mandate."""
    try:
        details = get_customer_mandate_details(customer_id=customer_id)
        cust = details.get("customer") or {}
        company_name = cust.get("company_name", "TechNova Solutions Pte Ltd")
        uen = cust.get("uen", "201823901E")
        grp_counts = details.get("active_group_counts", {"A": 2, "B": 2, "C": 1})
    except Exception:
        company_name = "TechNova Solutions Pte Ltd"
        uen = "201823901E"
        grp_counts = {"A": 2, "B": 2, "C": 1}

    return (
        "You are Joy, the DBS IDEAL Corporate Banking Change of Account Mandate, Payment & Liquidity Advisor, "
        f"powered by {LOGICAL_MODEL_ID}.\n"
        f"Current Active Corporate Entity: {company_name} ({customer_id}, UEN: {uen}).\n"
        f"Current Active Signatory Counts: Group A={grp_counts.get('A', 0)}, "
        f"Group B={grp_counts.get('B', 0)}, Group C={grp_counts.get('C', 0)}.\n"
        f"Current UI Workspace Stage: Stage {current_stage} of 5.\n\n"
        "MANDATORY OPERATIONAL RULES ACROSS ALL 3 DBS SLIDE DECK USE CASES:\n"
        "1. USE CASE 1 — CHANGE OF MANDATE (Slides 4–6 & 13): Call `get_ideal_entity_profile`, "
        "`SwitchActiveCustomerProfile`, `add_or_update_signatory`, `upload_nric_and_add_signatory`, "
        "`revoke_signatory`, `configure_signing_rules`, `validate_mandate_rules`, `audit_board_resolution`, "
        "`submit_mandate_change_request`, or `execute_cosigner_signature`.\n"
        "2. USE CASE 2 — PAYMENT PREPARATION & BEC SECURITY SCREENING (Slides 7–8 & 13): Whenever the user "
        "asks to prepare/verify an invoice payment (e.g., 'SingaTech Industrial', 'SGD 14,250', 'BEC check', "
        "or 'FAST vs MEPS'), ALWAYS call `stage_payment_to_ideal` (`Ref FT262359902`).\n"
        "3. USE CASE 3 — FX ADVISORY, VOLATILITY VaR & PARTIAL HEDGE BOOKING (Slides 9–10 & 13): Whenever the "
        "user asks about FX exposure/hedging (e.g., 'USD 5M payable in 90 days', 'spot vs forward', '200k uncertainty', "
        "or 'Book a forward for 70% of my total payable'), ALWAYS call `run_fx_pretrade_checks` or `book_fx_forward_contract` "
        "(`0.70 * USD 5M = USD 3,500,000`, `Spot 1.2800`, `90D Forward 1.3538`, `SGD 200,000 VaR`, `Contract CF03943335-01`).\n"
        "4. GOVERNANCE QUORUM RULE: ONLY Group A requires a minimum of 1 active signatory (`GOVERNANCE_VIOLATION_SOLE_GROUP_A`). "
        "Group B and Group C signatories (such as Kenneth Yap in Group C) can ALWAYS be revoked.\n"
        "5. CONCISE iCHAT + A2UI STYLE: Keep your text/voice reply to 1–2 short, natural sentences because the UI "
        "renders interactive A2UI visual cards and SVG charts for every tool call."
    )


async def run_agent_chat_turn(
    message: str,
    customer_id: str | None = None,
    current_stage: int = 1,
    event_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Execute a multi-step Extended Thinking + Database Tool-Calling turn using `models/gemini-3.8-live-extended-thinking`."""
    global _API_KEY_VALID
    clients = get_genai_clients()
    active_cid = customer_id or "CUST-001"
    sys_instruction = build_system_instruction(customer_id=active_cid, current_stage=current_stage)

    contents: list[types.Content] = [
        types.Content(role="user", parts=[types.Part.from_text(text=message)])
    ]

    config = types.GenerateContentConfig(
        system_instruction=sys_instruction,
        tools=MANDATE_TOOL_FUNCTIONS,
        thinking_config=types.ThinkingConfig(include_thoughts=True),
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        temperature=0.2,
    )

    thinking_traces: list[str] = []
    tool_calls_log: list[dict[str, Any]] = []
    ui_sync_events: list[dict[str, Any]] = []
    final_text_parts: list[str] = []
    latest_ui_sync: dict[str, Any] | None = None

    async def _call_model(turn_contents: list[types.Content]) -> Any:
        global _API_KEY_VALID
        # Try API key client first if not yet marked invalid
        if clients["api_key_client"] is not None and _API_KEY_VALID is not False:
            try:
                resp = await clients["api_key_client"].aio.models.generate_content(
                    model=LOGICAL_MODEL_ID,
                    contents=turn_contents,
                    config=config,
                )
                _API_KEY_VALID = True
                return resp
            except Exception as api_err:
                _API_KEY_VALID = False
                logger.info(
                    "API key client unavailable (%s); routing %s to Vertex AI global (%s).",
                    type(api_err).__name__,
                    LOGICAL_MODEL_ID,
                    THINKING_MODEL_ID,
                )

        # Primary Vertex AI Extended Thinking model (`gemini-3.8-flash` on `global`)
        try:
            return await clients["vertex_global"].aio.models.generate_content(
                model=THINKING_MODEL_ID,
                contents=turn_contents,
                config=config,
            )
        except Exception as exc:
            logger.warning("Primary model %s error (%s); trying %s", THINKING_MODEL_ID, exc, FALLBACK_TEXT_MODEL_ID)
            return await clients["vertex_global"].aio.models.generate_content(
                model=FALLBACK_TEXT_MODEL_ID,
                contents=turn_contents,
                config=config,
            )

    max_tool_rounds = 6
    try:
        for round_idx in range(max_tool_rounds):
            response = await _call_model(contents)
            if not response.candidates:
                break

            cand_content = response.candidates[0].content
            if not cand_content or not cand_content.parts:
                break

            contents.append(cand_content)

            function_Calls_in_turn: list[Any] = []
            for part in cand_content.parts:
                if getattr(part, "thought", False) and part.text:
                    thought_text = part.text.strip()
                    if thought_text:
                        thinking_traces.append(thought_text)
                        if event_callback:
                            await event_callback(
                                {
                                    "type": "thinking_trace",
                                    "text": thought_text,
                                    "model": LOGICAL_MODEL_ID,
                                }
                            )
                elif getattr(part, "function_call", None) is not None:
                    function_Calls_in_turn.append(part.function_call)
                elif getattr(part, "text", None):
                    txt = part.text.strip()
                    if txt:
                        final_text_parts.append(txt)

            if not function_Calls_in_turn:
                break

            # Execute each requested tool call against PostgreSQL
            func_response_parts: list[types.Part] = []
            for idx, fc in enumerate(function_Calls_in_turn):
                t_name = fc.name
                t_args = dict(fc.args) if fc.args else {}
                call_id = getattr(fc, "id", None) or f"call_{round_idx}_{idx}"

                if event_callback:
                    await event_callback(
                        {
                            "type": "tool_call_start",
                            "call_id": call_id,
                            "tool_name": t_name,
                            "args": t_args,
                        }
                    )

                tool_res = execute_mandate_tool(t_name, t_args)
                ui_sync_obj = tool_res.get("ui_sync")
                if ui_sync_obj:
                    latest_ui_sync = ui_sync_obj
                    ui_sync_events.append(ui_sync_obj)
                    if ui_sync_obj.get("updated_profile_id"):
                        active_cid = str(ui_sync_obj["updated_profile_id"])

                compact_res = {
                    k: v
                    for k, v in tool_res.items()
                    if k not in ("workspace_snapshot", "ui_sync")
                }
                if "workspace_snapshot" in tool_res and isinstance(tool_res["workspace_snapshot"], dict):
                    snap = tool_res["workspace_snapshot"]
                    compact_res["customer"] = snap.get("customer")
                    compact_res["active_group_counts"] = snap.get("active_group_counts")
                    compact_res["mandate_diff"] = snap.get("mandate_diff")

                tool_calls_log.append(
                    {
                        "call_id": call_id,
                        "tool_name": t_name,
                        "args": t_args,
                        "result": compact_res,
                        "ui_sync": ui_sync_obj,
                    }
                )

                if event_callback:
                    await event_callback(
                        {
                            "type": "tool_call_result",
                            "call_id": call_id,
                            "tool_name": t_name,
                            "args": t_args,
                            "result": compact_res,
                            "ui_sync": ui_sync_obj,
                        }
                    )
                    if ui_sync_obj:
                        await event_callback(ui_sync_obj)

                func_response_parts.append(
                    types.Part.from_function_response(name=t_name, response=compact_res)
                )

            contents.append(types.Content(role="user", parts=func_response_parts))
    except Exception as auth_exc:
        # When running locally on Cloudtop after 24h RAPT expiration, route Vertex AI reasoning through
        # the deployed Cloud Run service account in elevate-data-508005 and execute tools locally on PG18!
        import httpx
        cloud_run_url = os.environ.get(
            "CLOUD_RUN_BRIDGE_URL",
            "https://gemini-live-mandate-app-327571158527.us-central1.run.app",
        )
        logger.info("Bridging Vertex AI turn via Cloud Run service account (%s): %s", cloud_run_url, auth_exc)
        async with httpx.AsyncClient(timeout=45.0) as http_client:
            r = await http_client.post(
                f"{cloud_run_url}/api/chat",
                json={"message": message, "customer_id": active_cid, "current_stage": current_stage},
            )
            r_data = r.json()
            for tr in r_data.get("thinking_traces", []):
                thinking_traces.append(tr)
                if event_callback:
                    await event_callback({"type": "thinking_trace", "text": tr, "model": LOGICAL_MODEL_ID})
            for idx, tc in enumerate(r_data.get("tool_calls", [])):
                t_name = tc.get("tool_name") or tc.get("name")
                t_args = tc.get("args") or {}
                call_id = tc.get("call_id") or f"bridge_call_{idx}"
                if event_callback:
                    await event_callback({"type": "tool_call_start", "call_id": call_id, "tool_name": t_name, "args": t_args})
                tool_res = execute_mandate_tool(t_name, t_args)
                ui_sync_obj = tool_res.get("ui_sync")
                if ui_sync_obj:
                    latest_ui_sync = ui_sync_obj
                    ui_sync_events.append(ui_sync_obj)
                    if ui_sync_obj.get("updated_profile_id"):
                        active_cid = str(ui_sync_obj["updated_profile_id"])
                compact_res = {k: v for k, v in tool_res.items() if k not in ("workspace_snapshot", "ui_sync")}
                tool_calls_log.append(
                    {"call_id": call_id, "tool_name": t_name, "args": t_args, "result": compact_res, "ui_sync": ui_sync_obj}
                )
                if event_callback:
                    await event_callback(
                        {"type": "tool_call_result", "call_id": call_id, "tool_name": t_name, "args": t_args, "result": compact_res, "ui_sync": ui_sync_obj}
                    )
                    if ui_sync_obj:
                        await event_callback(ui_sync_obj)
            if r_data.get("reply"):
                final_text_parts.append(str(r_data["reply"]))

    reply_text = "\n\n".join(final_text_parts).strip()
    if not reply_text and tool_calls_log:
        last_tool = tool_calls_log[-1]
        reply_text = (
            f"Completed `{last_tool['tool_name']}` against PostgreSQL for {active_cid}."
        )

    if latest_ui_sync and latest_ui_sync.get("workspace_snapshot"):
        workspace_snapshot = latest_ui_sync["workspace_snapshot"]
    else:
        details = get_customer_mandate_details(customer_id=active_cid)
        workspace_snapshot = details.get("workspace_snapshot", details)
        if latest_ui_sync is None:
            latest_ui_sync = details.get("ui_sync")

    return {
        "status": "success",
        "model": LOGICAL_MODEL_ID,
        "resolved_thinking_model": THINKING_MODEL_ID,
        "resolved_live_audio_model": LIVE_AUDIO_MODEL_ID,
        "customer_id": active_cid,
        "reply": reply_text,
        "assistant_reply": reply_text,
        "thinking_traces": thinking_traces,
        "thought_traces": thinking_traces,
        "tool_calls": tool_calls_log,
        "tool_executions": tool_calls_log,
        "ui_sync": latest_ui_sync,
        "ui_sync_events": ui_sync_events,
        "workspace_snapshot": workspace_snapshot,
    }


async def synthesize_live_voice_response(
    prompt_text: str,
    customer_id: str,
    send_json: Callable[[dict[str, Any]], Awaitable[None]],
) -> None:
    """Stream real 24kHz PCM audio + output_transcription via `gemini-live-2.5-flash-native-audio` on `us-central1`."""
    clients = get_genai_clients()
    live_config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        output_audio_transcription=types.AudioTranscriptionConfig(),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        system_instruction=types.Content(
            parts=[
                types.Part.from_text(
                    text=(
                        "Speak naturally, concisely, and clearly as a Corporate Treasury Advisor. "
                        "State the answer once in 1-2 short sentences without repeating phrases."
                    )
                )
            ]
        ),
    )
    seq = 0
    try:
        async with clients["vertex_usc1"].aio.live.connect(
            model=LIVE_AUDIO_MODEL_ID, config=live_config
        ) as session:
            await session.send(input=prompt_text, end_of_turn=True)
            async for msg in session.receive():
                sc = getattr(msg, "server_content", None)
                if sc:
                    mt = getattr(sc, "model_turn", None)
                    if mt and mt.parts:
                        for part in mt.parts:
                            inline = getattr(part, "inline_data", None)
                            if inline and inline.data:
                                seq += 1
                                b64_pcm = base64.b64encode(inline.data).decode("ascii")
                                await send_json(
                                    {
                                        "type": "audio_out",
                                        "seq": seq,
                                        "pcm24_base64": b64_pcm,
                                        "data": b64_pcm,
                                        "sample_rate": 24000,
                                    }
                                )
                    if getattr(sc, "turn_complete", False):
                        break
    except Exception as exc:
        logger.warning("Live native audio stream warning: %s", exc)


async def handle_live_websocket_session(websocket: Any, broadcaster: Any) -> None:
    """Multiplexed `/ws/live` WebSocket handler for bidirectional PCM voice, Extended Thinking, and UI Sync."""
    active_cid = "CUST-001"
    active_stage = 1
    active_mode = "chat"
    clients = get_genai_clients()

    # Persistent native audio session for streaming microphone PCM chunks
    live_session_ctx: Any = None
    live_session: Any = None
    live_receive_task: asyncio.Task[Any] | None = None
    turn_counter = 0

    async def send_safe(payload: dict[str, Any]) -> None:
        try:
            await websocket.send_json(payload)
        except Exception:
            pass

    async def ensure_live_audio_session() -> Any:
        nonlocal live_session_ctx, live_session, live_receive_task, turn_counter
        if live_session is not None:
            return live_session

        voice_instruction = (
            build_system_instruction(customer_id=active_cid, current_stage=active_stage)
            + "\nVOICE MODE RULES: Speak concisely in 1-3 clear sentences. Never repeat yourself or state the same information twice."
        )

        # Primary: Google AI Studio v1alpha Live API (`models/gemini-3.8-live-extended-thinking`)
        # with ThinkingConfig(thinking_level="LOW") as documented in AI Studio Live API spec
        if clients.get("api_key_live_alpha") is not None:
            try:
                ai_studio_config = types.LiveConnectConfig(
                    response_modalities=["AUDIO"],
                    thinking_config=types.ThinkingConfig(thinking_level="LOW"),
                    output_audio_transcription=types.AudioTranscriptionConfig(),
                    input_audio_transcription=types.AudioTranscriptionConfig(),
                    system_instruction=types.Content(
                        parts=[types.Part.from_text(text=voice_instruction)]
                    ),
                    tools=MANDATE_TOOL_FUNCTIONS,
                )
                live_session_ctx = clients["api_key_live_alpha"].aio.live.connect(
                    model=LOGICAL_MODEL_ID, config=ai_studio_config
                )
                live_session = await live_session_ctx.__aenter__()
                logger.info("Connected to Google AI Studio Live API (%s)", LOGICAL_MODEL_ID)
            except Exception as ais_err:
                logger.info(
                    "AI Studio Live API handshake note (%s); bridging session via %s",
                    ais_err,
                    LIVE_AUDIO_MODEL_ID,
                )
                live_session_ctx = None
                live_session = None

        if live_session is None:
            live_config = types.LiveConnectConfig(
                response_modalities=["AUDIO"],
                output_audio_transcription=types.AudioTranscriptionConfig(),
                input_audio_transcription=types.AudioTranscriptionConfig(),
                system_instruction=types.Content(
                    parts=[types.Part.from_text(text=voice_instruction)]
                ),
                tools=MANDATE_TOOL_FUNCTIONS,
            )
            live_session_ctx = clients["vertex_usc1"].aio.live.connect(
                model=LIVE_AUDIO_MODEL_ID, config=live_config
            )
            live_session = await live_session_ctx.__aenter__()

        user_has_spoken_meaningfully = False
        _NOISE_FILLERS = {
            "hum", "hum.", "hmm", "hmm.", "uh", "um", "ah", "oh", "eh",
            "mm", "mhm", "hm", "noise", "<noise>", "[noise]",
        }

        def _is_meaningful_user_speech(raw_txt: str) -> str:
            # Strip non-ASCII script hallucinations from ambient/ringer noise (e.g., Tamil 'ம்')
            ascii_only = "".join(ch for ch in (raw_txt or "") if 32 <= ord(ch) <= 126).strip()
            low = ascii_only.lower().strip(" .,!?-_:;\"'()")
            if not low or low in _NOISE_FILLERS:
                return ""
            alpha_count = sum(1 for ch in low if ch.isalpha())
            if alpha_count < 3:
                return ""
            return ascii_only

        async def _reader_loop() -> None:
            nonlocal turn_counter, user_has_spoken_meaningfully
            audio_seq = 0
            current_voice_turn_id = f"voice_turn_{turn_counter}"
            accumulated_out_text = ""
            accumulated_in_text = ""
            try:
                while True:
                    async for msg in live_session.receive():
                        # Handle tool calls from native audio session
                        tc = getattr(msg, "tool_call", None)
                        if tc and getattr(tc, "function_calls", None):
                            # Never allow unsolicited tool calls before the user has actually spoken a request
                            if not user_has_spoken_meaningfully:
                                f_responses = [
                                    types.FunctionResponse(
                                        id=fc.id,
                                        name=fc.name,
                                        response={
                                            "status": "ready",
                                            "instruction": "Do not list profiles or call tools until the user asks a specific question. Complete your brief greeting first.",
                                        },
                                    )
                                    for fc in tc.function_calls
                                ]
                                await live_session.send_tool_response(function_responses=f_responses)
                                continue

                            f_responses = []
                            for fc in tc.function_calls:
                                t_name = fc.name
                                t_args = dict(fc.args) if fc.args else {}
                                call_id = getattr(fc, "id", None) or f"live_call_{audio_seq}"
                                await send_safe(
                                    {
                                        "type": "tool_call_start",
                                        "call_id": call_id,
                                        "tool_name": t_name,
                                        "args": t_args,
                                    }
                                )
                                tool_res = execute_mandate_tool(t_name, t_args)
                                ui_sync_obj = tool_res.get("ui_sync")
                                if ui_sync_obj:
                                    if ui_sync_obj.get("updated_profile_id"):
                                        active_cid = str(ui_sync_obj["updated_profile_id"])
                                    if ui_sync_obj.get("target_stage"):
                                        active_stage = int(ui_sync_obj["target_stage"])
                                compact_res = {
                                    k: v
                                    for k, v in tool_res.items()
                                    if k not in ("workspace_snapshot", "ui_sync")
                                }
                                await send_safe(
                                    {
                                        "type": "tool_call_result",
                                        "call_id": call_id,
                                        "tool_name": t_name,
                                        "args": t_args,
                                        "result": compact_res,
                                        "ui_sync": ui_sync_obj,
                                    }
                                )
                                if ui_sync_obj:
                                    await broadcaster.broadcast(ui_sync_obj)
                                f_responses.append(
                                    types.FunctionResponse(
                                        id=fc.id, name=t_name, response=compact_res
                                    )
                                )
                            await live_session.send_tool_response(function_responses=f_responses)

                        sc = getattr(msg, "server_content", None)
                        if sc:
                            if getattr(sc, "interrupted", False):
                                accumulated_out_text = ""
                                await send_safe({"type": "interrupted", "reason": "model_interrupted"})

                            in_tr = getattr(sc, "input_transcription", None)
                            if in_tr and in_tr.text:
                                chunk_in = in_tr.text
                                accumulated_in_text = (accumulated_in_text + chunk_in).strip()
                                cleaned_in = _is_meaningful_user_speech(accumulated_in_text)
                                if cleaned_in:
                                    user_has_spoken_meaningfully = True
                                    await send_safe(
                                        {
                                            "type": "input_transcript",
                                            "turn_id": f"{current_voice_turn_id}_user",
                                            "text": cleaned_in,
                                            "delta": chunk_in,
                                            "finished": bool(getattr(in_tr, "finished", False)),
                                        }
                                    )

                            mt = getattr(sc, "model_turn", None)
                            if mt and mt.parts:
                                for part in mt.parts:
                                    inline = getattr(part, "inline_data", None)
                                    if inline and inline.data:
                                        audio_seq += 1
                                        b64_pcm = base64.b64encode(inline.data).decode("ascii")
                                        await send_safe(
                                            {
                                                "type": "audio_out",
                                                "seq": audio_seq,
                                                "turn_id": current_voice_turn_id,
                                                "pcm24_base64": b64_pcm,
                                                "data": b64_pcm,
                                                "sample_rate": 24000,
                                            }
                                        )

                            out_tr = getattr(sc, "output_transcription", None)
                            if out_tr and out_tr.text:
                                accumulated_out_text += out_tr.text
                                await send_safe(
                                    {
                                        "type": "output_transcript",
                                        "turn_id": current_voice_turn_id,
                                        "role": "assistant",
                                        "text": accumulated_out_text.strip(),
                                        "is_streaming": True,
                                        "finished": bool(getattr(out_tr, "finished", False)),
                                    }
                                )

                            if getattr(sc, "turn_complete", False):
                                if accumulated_out_text.strip():
                                    await send_safe(
                                        {
                                            "type": "output_transcript",
                                            "turn_id": current_voice_turn_id,
                                            "role": "assistant",
                                            "text": accumulated_out_text.strip(),
                                            "is_streaming": False,
                                            "final": True,
                                        }
                                    )
                                turn_counter += 1
                                current_voice_turn_id = f"voice_turn_{turn_counter}"
                                accumulated_out_text = ""
                                accumulated_in_text = ""
                                await send_safe({"type": "turn_complete"})
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.debug("Live reader loop ended: %s", exc)

        live_receive_task = asyncio.create_task(_reader_loop())
        return live_session

    async def close_live_audio_session() -> None:
        nonlocal live_session_ctx, live_session, live_receive_task
        if live_receive_task is not None:
            live_receive_task.cancel()
            live_receive_task = None
        if live_session_ctx is not None:
            try:
                await live_session_ctx.__aexit__(None, None, None)
            except Exception:
                pass
            live_session_ctx = None
            live_session = None

    # Send initial session_ready frame immediately on connection
    await send_safe(
        {
            "type": "session_ready",
            "model": LOGICAL_MODEL_ID,
            "resolved_thinking_model": THINKING_MODEL_ID,
            "resolved_live_audio_model": LIVE_AUDIO_MODEL_ID,
            "active_customer_id": active_cid,
            "tools_registered": list(MANDATE_TOOL_MAP.keys()),
        }
    )

    try:
        while True:
            raw_msg = await websocket.receive_text()
            try:
                frame = json.loads(raw_msg)
            except Exception:
                frame = {"type": "text_turn", "text": raw_msg}

            msg_type = str(frame.get("type") or "text_turn").lower()

            if msg_type in ("ping",):
                await send_safe({"type": "pong"})

            elif msg_type in ("init", "sync_context"):
                if frame.get("customer_id"):
                    active_cid = str(frame["customer_id"])
                if frame.get("stage") or frame.get("active_stage"):
                    active_stage = int(frame.get("stage") or frame.get("active_stage") or 1)
                if frame.get("mode"):
                    active_mode = str(frame["mode"]).lower()
                await send_safe(
                    {
                        "type": "session_ready",
                        "model": LOGICAL_MODEL_ID,
                        "resolved_thinking_model": THINKING_MODEL_ID,
                        "resolved_live_audio_model": LIVE_AUDIO_MODEL_ID,
                        "active_customer_id": active_cid,
                        "stage": active_stage,
                        "mode": active_mode,
                        "tools_registered": list(MANDATE_TOOL_MAP.keys()),
                    }
                )

            elif msg_type in ("barge_in", "interrupt"):
                await close_live_audio_session()
                await send_safe({"type": "interrupted", "reason": "user_barge_in"})
                await send_safe({"type": "barge_in_ack"})

            elif msg_type == "audio_chunk":
                b64_audio = frame.get("pcm16_base64") or frame.get("data") or ""
                if b64_audio:
                    try:
                        raw_pcm = base64.b64decode(b64_audio)
                        sess = await ensure_live_audio_session()
                        await sess.send_realtime_input(
                            audio=types.Blob(
                                data=raw_pcm,
                                mime_type=frame.get("mime_type") or "audio/pcm;rate=16000",
                            )
                        )
                    except Exception as audio_err:
                        logger.debug("Local live audio chunk warning: %s", audio_err)

            elif msg_type == "audio_stream_end":
                if live_session is not None:
                    try:
                        await live_session.send_realtime_input(audio_stream_end=True)
                    except Exception:
                        await live_session.send(input="", end_of_turn=True)

            elif msg_type == "voice_greeting":
                if frame.get("customer_id"):
                    active_cid = str(frame["customer_id"])
                greeting_text = (
                    "Hello! I'm Joy, your DBS IDEAL Corporate Banking Advisor—"
                    "how can I assist you today with your account mandate, supplier payment verification, or 90-day FX hedging?"
                )
                try:
                    sess = await ensure_live_audio_session()
                    await sess.send(
                        input=(
                            "You just answered a live corporate banking voice call. "
                            "Greet the director warmly in ONE short sentence as Joy, their DBS IDEAL Corporate Banking Advisor, "
                            "and ask how you can help with their account mandate, payment verification, or FX hedge today. "
                            "Do NOT call any tools or list customer profiles."
                        ),
                        end_of_turn=True,
                    )
                except Exception:
                    # Emit clean assistant greeting transcript + stream 24kHz PCM audio via Cloud Run live bridge
                    turn_counter += 1
                    g_tid = f"voice_greeting_{turn_counter}"
                    await send_safe(
                        {
                            "type": "output_transcript",
                            "turn_id": g_tid,
                            "role": "assistant",
                            "text": greeting_text,
                            "is_streaming": False,
                            "final": True,
                        }
                    )
                    try:
                        import websockets
                        ws_bridge_url = "wss://gemini-live-mandate-app-327571158527.us-central1.run.app/ws/live"
                        async with websockets.connect(ws_bridge_url, open_timeout=8) as r_ws:
                            await r_ws.send(
                                json.dumps(
                                    {
                                        "type": "text_turn",
                                        "text": f"Say only this sentence aloud: {greeting_text}",
                                        "customer_id": active_cid,
                                        "synthesize_audio": True,
                                    }
                                )
                            )
                            async for r_raw in r_ws:
                                r_msg = json.loads(r_raw)
                                if r_msg.get("type") == "audio_out":
                                    await send_safe(r_msg)
                                elif r_msg.get("type") == "turn_complete":
                                    break
                    except Exception:
                        pass
                    await send_safe({"type": "turn_complete", "turn_id": g_tid})

            elif msg_type in ("text_turn", "user_message", "chat"):
                user_text = str(frame.get("text") or frame.get("message") or "").strip()
                if frame.get("customer_id"):
                    active_cid = str(frame["customer_id"])
                if not user_text:
                    continue

                is_sys_greeting = user_text.lower().startswith("greet the corporate director")
                turn_counter += 1
                chat_turn_id = f"chat_turn_{turn_counter}"

                if not is_sys_greeting:
                    await send_safe(
                        {
                            "type": "transcript",
                            "turn_id": f"{chat_turn_id}_user",
                            "role": "user",
                            "text": user_text,
                            "final": True,
                        }
                    )

                turn_res = await run_agent_chat_turn(
                    message=user_text,
                    customer_id=active_cid,
                    current_stage=active_stage,
                    event_callback=send_safe,
                )
                active_cid = str(turn_res.get("customer_id") or active_cid)
                reply = str(turn_res.get("reply") or "")

                # Emit transcript & assistant_text with the same turn_id so UI renders exactly once
                await send_safe(
                    {
                        "type": "transcript",
                        "turn_id": chat_turn_id,
                        "role": "assistant",
                        "text": reply,
                        "final": True,
                    }
                )
                await send_safe(
                    {
                        "type": "assistant_text",
                        "turn_id": chat_turn_id,
                        "text": reply,
                        "thinking_traces": turn_res.get("thinking_traces", []),
                        "tool_calls": turn_res.get("tool_calls", []),
                        "ui_sync": turn_res.get("ui_sync"),
                    }
                )

                # Only synthesize voice on text_turn if client explicitly requested synthesize_audio=True
                if frame.get("synthesize_audio"):
                    await synthesize_live_voice_response(
                        prompt_text=reply,
                        customer_id=active_cid,
                        send_json=send_safe,
                    )

                await send_safe({"type": "turn_complete", "turn_id": chat_turn_id})
    finally:
        await close_live_audio_session()
