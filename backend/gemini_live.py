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
import time
from typing import Any, Awaitable, Callable

from dotenv import load_dotenv
from google import genai
from google.genai import types

from backend.session import (
    DEFAULT_WORKSPACE_ID,
    get_workspace_id,
    normalize_workspace_id,
    set_ui_sync_origin,
    set_workspace_id,
)
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

# ---------------------------------------------------------------------------
# Joy's spoken persona (Singaporean English, soft + professional)
# ---------------------------------------------------------------------------
# Every live-audio path (the conversational session, the verbatim greeting narrator and the
# reply narrator) must sound like the SAME person. Previously each LiveConnectConfig carried
# its own ad-hoc "warm, natural, professional" phrasing and no voice was pinned at all, so the
# service picked a different default voice per session. This constant is the single source of
# truth for accent, tonality and delivery; the prebuilt voice is pinned alongside it.
#
# `JOY_VOICE_NAME` is env-overridable so the demo voice can be re-cast without a code change.
# "Aoede" is the softest, breathiest female preset in the Live API voice set, which is the
# closest match to the requested soft Singaporean corporate-banker delivery.
JOY_VOICE_NAME = os.environ.get("JOY_VOICE_NAME", "Aoede")

JOY_VOICE_PERSONA = (
    "VOICE, ACCENT & TONALITY (applies to everything you say aloud):\n"
    "- You are Joy, a Singaporean Chinese woman in her mid-thirties, a senior DBS corporate "
    "relationship manager based in Marina Bay Financial Centre.\n"
    "- Speak in polished Singaporean English with a light, natural Singaporean-Asian accent — "
    "the neutral, well-spoken register a DBS private-banking RM uses with a corporate director, "
    "NOT broad colloquial Singlish.\n"
    "- Tonality: soft, calm and unhurried. Low volume, gentle, breathy warmth. Never loud, "
    "never bubbly, never salesy, never over-enthusiastic. No exclamations.\n"
    "- Pace: measured and relaxed, with small natural pauses at commas and before figures so "
    "amounts and reference numbers are easy to follow. Do not rush.\n"
    "- Intonation: even and reassuring, with a slight downward inflection at the end of "
    "sentences. Avoid the rising American 'upspeak' lilt.\n"
    "- Diction: crisp, lightly clipped Singaporean consonants; clear final consonants; "
    "unrounded, neutral vowels. Say 'S-G-D' and 'U-S-D' as letters, and read figures naturally "
    "(e.g. 'five million US dollars', 'one point three five three eight').\n"
    "- Register: understated professional courtesy. Prefer 'Certainly', 'Of course', "
    "'May I confirm', 'Noted' over casual Americanisms like 'Sure thing', 'Awesome', 'No worries'.\n"
    "- Do NOT use Singlish particles (lah, lor, leh, meh, sia), and do NOT caricature the accent. "
    "It should read as authentically Singaporean and quietly premium.\n"
)


def build_joy_speech_config() -> types.SpeechConfig:
    """Pin the same prebuilt voice across every live-audio session.

    Without an explicit `speech_config` the Live API falls back to its own default voice, which
    differs between the AI Studio and Vertex backends — so Joy's greeting and Joy's answers could
    come back in two different voices within one call.
    """
    return types.SpeechConfig(
        voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=JOY_VOICE_NAME)
        )
    )

_CLIENT_API_KEY: genai.Client | None = None
_CLIENT_API_KEY_LIVE_ALPHA: genai.Client | None = None
_CLIENT_VERTEX_GLOBAL: genai.Client | None = None
_CLIENT_VERTEX_USC1: genai.Client | None = None
_API_KEY_VALID: bool | None = None
_RUNTIME_API_KEY: str | None = None
_SECRET_MANAGER_SECRET_ID = os.environ.get("GEMINI_API_KEY_SECRET_ID", "gemini-live-api-key")
_EXPIRED_KEY_PREFIX = "AIzaSyCE7i"
_SECRET_CACHE_VALUE: str | None = None
_SECRET_CACHE_FETCHED_AT: float = 0.0
_SECRET_CACHE_TTL_SECONDS: float = 60.0

# The API key path used to be disabled permanently on the first failure of any kind, including a
# single transient 503. Instead, trip a circuit breaker that automatically re-arms after a cooldown.
_API_KEY_COOLDOWN_SECONDS = float(os.environ.get("API_KEY_COOLDOWN_SECONDS", "120"))
_API_KEY_DISABLED_UNTIL: float = 0.0


def _is_expired_key(key_str: str) -> bool:
    """Return True if the key string is empty or matches a revoked key prefix."""
    cleaned = (key_str or "").strip()
    return not cleaned or cleaned.startswith(_EXPIRED_KEY_PREFIX)


def fetch_api_key_from_secret_manager(
    secret_id: str = _SECRET_MANAGER_SECRET_ID,
    force_refresh: bool = False,
) -> str:
    """Fetch the latest Gemini Live API key in real time from Google Cloud Secret Manager.

    Reads `projects/{GCP_PROJECT}/secrets/{secret_id}/versions/latest:access` via the Secret
    Manager REST API using Application Default Credentials (Cloud Run service account or local
    ADC), caching the value for 60 seconds unless `force_refresh=True` is requested after an
    auth/key error.
    """
    global _SECRET_CACHE_VALUE, _SECRET_CACHE_FETCHED_AT
    now = time.monotonic()
    if (
        not force_refresh
        and _SECRET_CACHE_VALUE
        and (now - _SECRET_CACHE_FETCHED_AT) < _SECRET_CACHE_TTL_SECONDS
    ):
        return _SECRET_CACHE_VALUE

    try:
        import google.auth
        from google.auth.transport.requests import Request as GoogleAuthRequest
        import httpx

        creds, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        if not creds.valid:
            creds.refresh(GoogleAuthRequest())
        url = (
            f"https://secretmanager.googleapis.com/v1/projects/{GCP_PROJECT}"
            f"/secrets/{secret_id}/versions/latest:access"
        )
        resp = httpx.get(
            url,
            headers={"Authorization": f"Bearer {creds.token}"},
            timeout=3.5,
        )
        if resp.status_code == 200:
            data_b64 = (resp.json().get("payload") or {}).get("data", "")
            if data_b64:
                decoded = base64.b64decode(data_b64).decode("utf-8").strip()
                if not _is_expired_key(decoded):
                    _SECRET_CACHE_VALUE = decoded
                    _SECRET_CACHE_FETCHED_AT = now
                    logger.info(
                        "Fetched live Gemini API key from Secret Manager (%s, prefix=%s...)",
                        secret_id,
                        decoded[:6],
                    )
                    return decoded
    except Exception as sm_exc:
        logger.debug("Secret Manager real-time lookup note: %s", sm_exc)

    return _SECRET_CACHE_VALUE or ""


def _resolve_active_api_key(force_secret_refresh: bool = False) -> str:
    """Resolve the best available Gemini API key from Secret Manager or environment."""
    if _RUNTIME_API_KEY and not _is_expired_key(_RUNTIME_API_KEY) and not force_secret_refresh:
        return _RUNTIME_API_KEY

    env_key = (
        os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_API_KEY")
        or ""
    ).strip()

    if env_key and not _is_expired_key(env_key) and not force_secret_refresh:
        return env_key

    sm_key = fetch_api_key_from_secret_manager(force_refresh=force_secret_refresh)
    if sm_key:
        os.environ["GEMINI_API_KEY"] = sm_key
        os.environ["GOOGLE_API_KEY"] = sm_key
        return sm_key
    return env_key


def _api_key_path_available() -> bool:
    """True when the AI Studio API key path is not currently in its failure cooldown."""
    return time.monotonic() >= _API_KEY_DISABLED_UNTIL


def _trip_api_key_breaker() -> None:
    """Refresh key from Secret Manager on failure; trip cooldown only if no backup key is available."""
    global _API_KEY_DISABLED_UNTIL, _API_KEY_VALID, _RUNTIME_API_KEY
    global _CLIENT_API_KEY, _CLIENT_API_KEY_LIVE_ALPHA
    current = (
        _RUNTIME_API_KEY
        or os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_API_KEY")
        or ""
    ).strip()
    fresh = fetch_api_key_from_secret_manager(force_refresh=True)
    if fresh and fresh != current:
        logger.info("Hot-swapping failed Gemini API key with fresh Secret Manager key (prefix=%s...)", fresh[:6])
        _RUNTIME_API_KEY = fresh
        os.environ["GEMINI_API_KEY"] = fresh
        os.environ["GOOGLE_API_KEY"] = fresh
        _CLIENT_API_KEY = None
        _CLIENT_API_KEY_LIVE_ALPHA = None
        _API_KEY_VALID = True
        _API_KEY_DISABLED_UNTIL = 0.0
        return

    _API_KEY_DISABLED_UNTIL = time.monotonic() + _API_KEY_COOLDOWN_SECONDS
    _API_KEY_VALID = False


# Per-session text chat memory (keyed by browser workspace id — see backend/session.py). Only
# user text and the final assistant reply are kept, so follow-up questions ("now do the same for
# Meridian") have context without replaying tool traffic. Bounded so a long demo session never
# grows the prompt unboundedly.
_CHAT_HISTORY: dict[str, list[types.Content]] = {}
_CHAT_HISTORY_MAX_CONTENTS = 16


def get_chat_history(history_key: str | None) -> list[types.Content]:
    return list(_CHAT_HISTORY.get(history_key or DEFAULT_WORKSPACE_ID, []))


def append_chat_history(history_key: str | None, user_text: str, reply_text: str) -> None:
    key = history_key or DEFAULT_WORKSPACE_ID
    history = _CHAT_HISTORY.setdefault(key, [])
    history.append(types.Content(role="user", parts=[types.Part.from_text(text=user_text)]))
    if reply_text:
        history.append(types.Content(role="model", parts=[types.Part.from_text(text=reply_text)]))
    if len(history) > _CHAT_HISTORY_MAX_CONTENTS:
        del history[: len(history) - _CHAT_HISTORY_MAX_CONTENTS]


def clear_chat_history(history_key: str | None) -> None:
    _CHAT_HISTORY.pop(history_key or DEFAULT_WORKSPACE_ID, None)


def set_runtime_api_key(api_key: str) -> dict[str, Any]:
    """Update the Google AI Studio API key at runtime and reset client caches."""
    global _RUNTIME_API_KEY, _CLIENT_API_KEY, _CLIENT_API_KEY_LIVE_ALPHA, _API_KEY_VALID
    global _API_KEY_DISABLED_UNTIL
    cleaned = (api_key or "").strip()
    if cleaned:
        _RUNTIME_API_KEY = cleaned
        os.environ["GEMINI_API_KEY"] = cleaned
        os.environ["GOOGLE_API_KEY"] = cleaned
        _CLIENT_API_KEY = None
        _CLIENT_API_KEY_LIVE_ALPHA = None
        _API_KEY_VALID = None
        # A freshly supplied key deserves an immediate retry.
        _API_KEY_DISABLED_UNTIL = 0.0
    return get_model_status()


def get_genai_clients() -> dict[str, Any]:
    """Initialize and return Google AI Studio (`v1alpha` & `v1beta`) and Vertex AI clients."""
    global _CLIENT_API_KEY, _CLIENT_API_KEY_LIVE_ALPHA, _CLIENT_VERTEX_GLOBAL, _CLIENT_VERTEX_USC1

    api_key = _resolve_active_api_key(force_secret_refresh=False)
    if api_key and _CLIENT_API_KEY is None:
        try:
            _CLIENT_API_KEY = genai.Client(vertexai=False, api_key=api_key)
            _CLIENT_API_KEY_LIVE_ALPHA = genai.Client(
                vertexai=False, api_key=api_key, http_options={"api_version": "v1alpha"}
            )
        except Exception as exc:
            logger.warning("Failed to initialize API key client: %s", exc)

    # Vertex client construction resolves Application Default Credentials and can raise when ADC is
    # missing or expired (a daily occurrence on Cloudtop). It was previously unguarded, so a bad ADC
    # took down /api/health, /api/config/api-key and every API-key-only code path with it.
    if _CLIENT_VERTEX_GLOBAL is None:
        try:
            _CLIENT_VERTEX_GLOBAL = genai.Client(
                vertexai=True, project=GCP_PROJECT, location="global"
            )
        except Exception as exc:
            logger.warning("Vertex AI 'global' client unavailable (ADC problem?): %s", exc)

    if _CLIENT_VERTEX_USC1 is None:
        try:
            _CLIENT_VERTEX_USC1 = genai.Client(
                vertexai=True, project=GCP_PROJECT, location="us-central1"
            )
        except Exception as exc:
            logger.warning("Vertex AI 'us-central1' client unavailable (ADC problem?): %s", exc)

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
        "ROLE LOCK (HIGHEST PRIORITY — NEVER VIOLATE):\n"
        "- You are ALWAYS the DBS banker. The human on the call is the corporate director/customer.\n"
        "- NEVER speak the customer's lines. NEVER say things like 'Hi Joy', 'I need to change my supplier', "
        "'I want to book a forward', or any other request as if you were the customer.\n"
        "- NEVER narrate, script, simulate, or act out both sides of the conversation.\n"
        "- The example phrases listed in the use cases below are things the CUSTOMER may say to YOU. "
        "They are triggers for you to listen for — never sentences for you to speak.\n"
        "- If you have nothing to respond to, stay silent and wait for the customer to speak.\n\n"
        "MANDATORY OPERATIONAL RULES ACROSS ALL 3 DBS SLIDE DECK USE CASES:\n"
        "1. USE CASE 1 — CHANGE OF MANDATE (Slides 4–6 & 13): Call `get_ideal_entity_profile`, "
        "`SwitchActiveCustomerProfile`, `add_or_update_signatory`, `upload_nric_and_add_signatory`, "
        "`revoke_signatory`, `configure_signing_rules`, `validate_mandate_rules`, `audit_board_resolution`, "
        "`submit_mandate_change_request`, or `execute_cosigner_signature`.\n"
        "2. USE CASE 2 — PAYMENT PREPARATION & BEC SECURITY SCREENING (Slides 7–8 & 13): When the CUSTOMER "
        "asks you to prepare/verify an invoice payment (e.g., they mention 'SingaTech Industrial', 'SGD 14,250', "
        "'BEC check', or 'FAST vs MEPS'), call `stage_payment_to_ideal` (`Ref FT262359902`).\n"
        "3. USE CASE 3 — FX ADVISORY, VOLATILITY VaR & PARTIAL HEDGE BOOKING (Slides 9–10 & 13): When the CUSTOMER "
        "asks about FX exposure/hedging (e.g., they mention 'USD 5M payable in 90 days', 'spot vs forward', "
        "'200k uncertainty', or 'book a forward for 70% of my total payable'), call `run_fx_pretrade_checks` or "
        "`book_fx_forward_contract` "
        "(`0.70 * USD 5M = USD 3,500,000`, `Spot 1.2800`, `90D Forward 1.3538`, `SGD 200,000 VaR`, `Contract CF03943335-01`).\n"
        "4. TAB & VIEW SWITCHING (`switch_workspace_tab`): Whenever the user asks to switch tabs, screens, or views "
        "(e.g. 'switch to FX', 'go to the payment tab', 'show me BEC shield', 'go back to change of mandate', 'open stage 3'), "
        "ALWAYS call `switch_workspace_tab(tab_name=...)` with `'UC1_MANDATE'`, `'UC2_PAYMENT'`, or `'UC3_FX'`.\n"
        "5. GOVERNANCE QUORUM RULE: ONLY Group A requires a minimum of 1 active signatory (`GOVERNANCE_VIOLATION_SOLE_GROUP_A`). "
        "Group B and Group C signatories (such as Kenneth Yap in Group C) can ALWAYS be revoked.\n"
        "6. CONCISE iCHAT + A2UI STYLE: Keep your text/voice reply to 1–2 short, natural sentences because the UI "
        "renders interactive A2UI visual cards and SVG charts for every tool call."
    )


def _detect_tab_switch_intent(text: str, customer_id: str = "CUST-001") -> dict[str, Any] | None:
    """Detect explicit user requests to switch workspace tabs using strict word-boundary regexes."""
    import re
    from backend.tools import switch_workspace_tab

    cleaned = (text or "").strip()
    if not cleaned:
        return None

    # Require an explicit navigation verb + target tab noun so general questions aren't hijacked
    has_nav_verb = bool(
        re.search(
            r"\b(switch|go|open|navigate|move|return|back|show|take\s+me|view|change\s+tab|select\s+tab)\b",
            cleaned,
            re.IGNORECASE,
        )
    )
    if not has_nav_verb:
        return None

    # Do not intercept action requests that already have dedicated action tools (e.g. "prepare an invoice payment", "book a 70% forward")
    if re.search(r"\b(prepare|book|execute|revoke|remove|delete|add|upload|simulate|audit|submit)\b", cleaned, re.IGNORECASE):
        return None

    if re.search(r"\b(fx|foreign\s+exchange|hedg(?:e|ing)|forward\s+tab|pricing\s+tab|tab\s*3|use\s*case\s*3)\b", cleaned, re.IGNORECASE):
        return switch_workspace_tab(tab_name="UC3_FX", customer_id=customer_id)

    if re.search(r"\b(payment(?:s)?|bec\b|fraud\s+shield|invoice\s+tab|rail\s+tab|tab\s*2|use\s*case\s*2)\b", cleaned, re.IGNORECASE):
        return switch_workspace_tab(tab_name="UC2_PAYMENT", customer_id=customer_id)

    if re.search(r"\b(mandate|signator(?:y|ies)|signing\s+rules|board\s+resolution|digisign|tab\s*1|use\s*case\s*1|stage\s*[1-5])\b", cleaned, re.IGNORECASE):
        m_stage = re.search(r"\bstage\s*([1-5])\b", cleaned, re.IGNORECASE)
        st = int(m_stage.group(1)) if m_stage else 1
        return switch_workspace_tab(tab_name=cleaned, stage=st, customer_id=customer_id)

    return None


def _detect_voice_entity_switch(text: str, current_cid: str = "CUST-001") -> dict[str, Any] | None:
    """Detect voice STT requests to switch the active corporate customer profile (including phonetic STT like 'Stuve Veritas Legal' or 'very task')."""
    import re
    from backend.tools import SwitchActiveCustomerProfile

    cleaned = (text or "").strip()
    if not cleaned:
        return None

    # Do not trigger on compound action turns (e.g. "remove Evelyn Tan", "add Desmond") — let the model's tool chain run
    if re.search(r"\b(revoke|remove|delete|add|promote|book|prepare|simulate|audit|submit)\b", cleaned, re.IGNORECASE):
        return None

    entity_map = [
        (r"\b(veritas|very\s+task|veritask|stuve\s+veritas|cust-004)\b", "CUST-004"),
        (r"\b(meridian|meridian\s+pacific|cust-002)\b", "CUST-002"),
        (r"\b(apex|apex\s+global|cust-003)\b", "CUST-003"),
        (r"\b(banyan|banyan\s+hospitality|cust-005)\b", "CUST-005"),
        (r"\b(technova|tech\s+nova|cust-001)\b", "CUST-001"),
    ]
    for pattern, target_cid in entity_map:
        if re.search(pattern, cleaned, re.IGNORECASE):
            if target_cid != current_cid or re.search(r"\b(switch|stuve|go\s+to|select|change|load|open)\b", cleaned, re.IGNORECASE):
                return SwitchActiveCustomerProfile(customer_id=target_cid)
    return None


def _is_bridgeable_credential_error(exc: BaseException) -> bool:
    """Return True only for credential/transport failures worth retrying via the Cloud Run bridge.

    Previously the bridge was triggered by a bare `except Exception` around the entire tool
    loop, which meant a plain KeyError or a tool bug was misreported as an auth problem and
    quietly turned into an outbound HTTP call. Restricting it here keeps real bugs visible.
    """
    try:
        from google.auth.exceptions import GoogleAuthError, RefreshError, TransportError

        if isinstance(exc, (GoogleAuthError, RefreshError, TransportError)):
            return True
    except Exception:  # pragma: no cover - google.auth always present in practice
        pass

    text = f"{type(exc).__name__}: {exc}".lower()
    markers = (
        "reauthentication is needed",
        "could not automatically determine credentials",
        "default credentials",
        "invalid_grant",
        "invalid_rapt",
        "unauthenticated",
        "permission_denied",
        "403",
        "401",
        "access token",
        "credential",
    )
    return any(m in text for m in markers)


async def run_agent_chat_turn(
    message: str,
    customer_id: str | None = None,
    current_stage: int = 1,
    event_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    allow_bridge: bool = True,
    history_key: str | None = None,
) -> dict[str, Any]:
    """Execute a multi-step Extended Thinking + Database Tool-Calling turn using `models/gemini-3.8-live-extended-thinking`.

    ``history_key`` (the caller's browser workspace id) selects the per-session chat memory that
    is prepended to the turn so follow-up questions keep their context.
    """
    global _API_KEY_VALID
    clients = get_genai_clients()
    active_cid = customer_id or "CUST-001"

    # Fast-path for explicit workspace tab switching ('switch to FX', 'go to payments', 'back to change of mandate')
    fast_tab_res = await asyncio.to_thread(_detect_tab_switch_intent, message, active_cid)
    if fast_tab_res and fast_tab_res.get("ui_sync"):
        ui_sync_obj = fast_tab_res["ui_sync"]
        tab_lbl = fast_tab_res.get("tab_label", "the requested tab")
        reply_str = f"Certainly. I have switched the workspace view to {tab_lbl}."
        tc_entry = {
            "call_id": "call_tab_switch_fast",
            "tool_name": "switch_workspace_tab",
            "args": {"tab_name": message, "customer_id": active_cid},
            "result": {k: v for k, v in fast_tab_res.items() if k not in ("workspace_snapshot", "ui_sync")},
            "ui_sync": ui_sync_obj,
        }
        if event_callback:
            await event_callback({"type": "tool_call_result", **tc_entry})
            await event_callback(ui_sync_obj)
        return {
            "status": "success",
            "model": LOGICAL_MODEL_ID,
            "resolved_thinking_model": THINKING_MODEL_ID,
            "resolved_live_audio_model": LIVE_AUDIO_MODEL_ID,
            "customer_id": str(ui_sync_obj.get("updated_profile_id") or active_cid),
            "reply": reply_str,
            "assistant_reply": reply_str,
            "thinking_traces": [],
            "thought_traces": [],
            "tool_calls": [tc_entry],
            "tool_executions": [tc_entry],
            "ui_sync": ui_sync_obj,
            "ui_sync_events": [ui_sync_obj],
            "workspace_snapshot": fast_tab_res.get("workspace_snapshot", {}),
        }

    # build_system_instruction() issues a synchronous psycopg2 query; run it off the event loop
    # so it cannot stall concurrent /ws/live audio streaming.
    sys_instruction = await asyncio.to_thread(
        build_system_instruction, customer_id=active_cid, current_stage=current_stage
    )

    contents: list[types.Content] = get_chat_history(history_key) + [
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
        # Try the API key client first using THINKING_MODEL_ID (`gemini-3.8-flash`).
        # Note: LOGICAL_MODEL_ID (`models/gemini-3.8-live-extended-thinking`) only supports
        # WebSocket BidiGenerateContent and fails unary REST generateContent with 400.
        if clients["api_key_client"] is not None and _api_key_path_available():
            try:
                resp = await clients["api_key_client"].aio.models.generate_content(
                    model=THINKING_MODEL_ID,
                    contents=turn_contents,
                    config=config,
                )
                _API_KEY_VALID = True
                return resp
            except Exception as api_err:
                _trip_api_key_breaker()
                logger.info(
                    "API key client unavailable (%s); routing %s to Vertex AI global (%s). "
                    "Retrying the API key path in %.0fs.",
                    type(api_err).__name__,
                    LOGICAL_MODEL_ID,
                    THINKING_MODEL_ID,
                    _API_KEY_COOLDOWN_SECONDS,
                )

        vertex_global = clients.get("vertex_global")
        if vertex_global is None:
            raise RuntimeError(
                "No usable Gemini backend: the AI Studio API key path failed and the Vertex AI "
                "client could not be constructed (check Application Default Credentials)."
            )

        # Primary Vertex AI Extended Thinking model (`gemini-3.8-flash` on `global`)
        try:
            return await vertex_global.aio.models.generate_content(
                model=THINKING_MODEL_ID,
                contents=turn_contents,
                config=config,
            )
        except Exception as exc:
            logger.warning("Primary model %s error (%s); trying %s", THINKING_MODEL_ID, exc, FALLBACK_TEXT_MODEL_ID)
            return await vertex_global.aio.models.generate_content(
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

                tool_res = await asyncio.to_thread(execute_mandate_tool, t_name, t_args)
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
        # This fallback exists for one narrow situation: running locally on Cloudtop after the
        # 24h ADC/RAPT expiry, where Vertex AI refuses the call. In that case we borrow the
        # deployed Cloud Run service account for the *reasoning* step and still execute tools
        # against the local PostgreSQL instance.
        #
        # Guard rails (each of these was previously missing and actively harmful):
        #   1. Only bridge on credential/transport failures. The old bare `except Exception`
        #      wrapped the whole 100-line tool loop, so an ordinary KeyError in tool handling
        #      silently turned into a network call and the real traceback was lost.
        #   2. Never bridge when we *are* Cloud Run (K_SERVICE is set), otherwise the service
        #      POSTs to its own /api/chat on every Vertex hiccup and fans out recursively.
        #   3. Never bridge a request that was itself bridged (allow_bridge=False).
        if not _is_bridgeable_credential_error(auth_exc):
            logger.exception("Agent chat turn failed (not a credential error; not bridging).")
            raise

        running_on_cloud_run = bool(os.environ.get("K_SERVICE"))
        if running_on_cloud_run or not allow_bridge:
            logger.error(
                "Vertex AI credential failure and bridging is disabled "
                "(on_cloud_run=%s, allow_bridge=%s): %s",
                running_on_cloud_run,
                allow_bridge,
                auth_exc,
            )
            raise

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
                # Tell the remote end never to bridge onwards, so a misconfigured
                # CLOUD_RUN_BRIDGE_URL can never produce an unbounded call chain.
                headers={"X-No-Bridge": "1"},
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
                tool_res = await asyncio.to_thread(execute_mandate_tool, t_name, t_args)
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

    if latest_ui_sync is None:
        tab_switch_res = await asyncio.to_thread(_detect_tab_switch_intent, message, active_cid)
        if tab_switch_res and tab_switch_res.get("ui_sync"):
            latest_ui_sync = tab_switch_res["ui_sync"]
            ui_sync_events.append(latest_ui_sync)
            tc_entry = {
                "call_id": "call_tab_switch_auto",
                "tool_name": "switch_workspace_tab",
                "args": {"tab_name": message, "customer_id": active_cid},
                "result": {k: v for k, v in tab_switch_res.items() if k not in ("workspace_snapshot", "ui_sync")},
                "ui_sync": latest_ui_sync,
            }
            tool_calls_log.append(tc_entry)
            if event_callback:
                await event_callback({"type": "tool_call_result", **tc_entry})
                await event_callback(latest_ui_sync)
            if not reply_text:
                reply_text = f"Certainly. I have switched the workspace view to {tab_switch_res.get('tab_label', 'the requested tab')}."

    if latest_ui_sync and latest_ui_sync.get("workspace_snapshot"):
        workspace_snapshot = latest_ui_sync["workspace_snapshot"]
    else:
        details = await asyncio.to_thread(get_customer_mandate_details, customer_id=active_cid)
        workspace_snapshot = details.get("workspace_snapshot", details)
        if latest_ui_sync is None:
            latest_ui_sync = details.get("ui_sync")

    append_chat_history(history_key, message, reply_text)

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
    """Read `prompt_text` aloud verbatim as 24kHz PCM via `gemini-live-2.5-flash-native-audio`."""
    del customer_id  # Narration is context-free by design.

    await speak_verbatim_as_joy(prompt_text, send_json)


JOY_GREETING_TEXT = (
    "Hello, this is Joy from DBS IDEAL Corporate Banking. "
    "How may I assist you today, with your account mandate, a supplier payment, or your F X hedging?"
)


async def speak_verbatim_as_joy(
    text_to_speak: str,
    send_json: Callable[[dict[str, Any]], Awaitable[None]],
    interrupt_event: asyncio.Event | None = None,
    turn_id: str | None = None,
) -> bool:
    """Read `text_to_speak` aloud VERBATIM as Joy (aborting immediately if `interrupt_event` is set)."""
    clients = get_genai_clients()
    narration_sys = types.Content(
        parts=[
            types.Part.from_text(
                text=(
                    "You are the speaking voice of Joy, a female DBS corporate banker. "
                    "Read the user's message aloud EXACTLY as written. Do NOT add, remove, "
                    "rephrase, translate, or answer anything. Do NOT role-play as a customer. "
                    "Do NOT ask follow-up questions. Speak only the given sentence, once, "
                    "then stop.\n\n" + JOY_VOICE_PERSONA
                )
            )
        ]
    )

    candidates = [
        ("ai_studio_38_live", clients.get("api_key_client"), "gemini-3.8-live", False),
        ("ai_studio", clients.get("api_key_live_alpha"), LOGICAL_MODEL_ID, True),
        ("vertex_usc1", clients.get("vertex_usc1"), LIVE_AUDIO_MODEL_ID, False),
    ]
    for label, client, model_id, needs_thinking in candidates:
        if client is None:
            continue
        cfg_kwargs: dict[str, Any] = {
            "response_modalities": ["AUDIO"],
            "speech_config": build_joy_speech_config(),
            "system_instruction": narration_sys,
        }
        if needs_thinking:
            cfg_kwargs["thinking_config"] = types.ThinkingConfig(thinking_level="LOW")
        narration_config = types.LiveConnectConfig(**cfg_kwargs)

        seq = 0
        try:
            async with client.aio.live.connect(model=model_id, config=narration_config) as session:
                await session.send_client_content(
                    turns=types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=text_to_speak)],
                    ),
                    turn_complete=True,
                )
                async for msg in session.receive():
                    if interrupt_event is not None and interrupt_event.is_set():
                        logger.info("Verbatim narration interrupted by user barge-in after %d chunks.", seq)
                        await send_json({"type": "interrupted", "reason": "user_barge_in"})
                        return True
                    sc = getattr(msg, "server_content", None)
                    if not sc:
                        continue
                    mt = getattr(sc, "model_turn", None)
                    if mt and mt.parts:
                        for part in mt.parts:
                            if interrupt_event is not None and interrupt_event.is_set():
                                break
                            inline = getattr(part, "inline_data", None)
                            if inline and inline.data:
                                seq += 1
                                b64_pcm = base64.b64encode(inline.data).decode("ascii")
                                await send_json(
                                    {
                                        "type": "audio_out",
                                        "seq": seq,
                                        "turn_id": turn_id or "joy_verbatim",
                                        "pcm24_base64": b64_pcm,
                                        "data": b64_pcm,
                                        "sample_rate": 24000,
                                    }
                                )
                    if getattr(sc, "turn_complete", False):
                        break
            if seq > 0:
                logger.info("Spoke verbatim Joy audio via %s (%d audio chunks).", label, seq)
                return True
        except Exception as exc:
            logger.info("Verbatim audio via %s unavailable: %s", label, exc)
            if label == "ai_studio":
                _trip_api_key_breaker()
    return False


async def handle_live_websocket_session(websocket: Any, broadcaster: Any) -> None:
    """Multiplexed `/ws/live` WebSocket handler for bidirectional PCM voice, Extended Thinking, and UI Sync."""
    active_cid = "CUST-001"
    active_stage = 1
    active_mode = "chat"
    # The browser tab's session id (see backend/session.py). ASGI-scope headers cannot carry it on
    # a raw WebSocket upgrade (browsers don't let JS set custom headers there), so the real value
    # arrives inside the first `init` frame and is applied via bind_workspace() below.
    workspace_id = get_workspace_id()
    set_ui_sync_origin("live")
    clients = get_genai_clients()

    # Persistent native audio session for streaming microphone PCM chunks
    live_session_ctx: Any = None
    live_session: Any = None
    live_receive_task: asyncio.Task[Any] | None = None
    turn_counter = 0
    interrupt_event = asyncio.Event()
    barge_in_discard_until = 0.0
    last_voice_tab_switched = ""
    # Exponential backoff guarding live-session reconnection (see the audio_chunk handler).
    live_session_backoff = 1.0
    live_session_retry_after = 0.0

    def bind_workspace(raw_id: Any) -> None:
        nonlocal workspace_id
        workspace_id = normalize_workspace_id(str(raw_id or ""))
        set_workspace_id(workspace_id)
        if broadcaster is not None and hasattr(broadcaster, "bind"):
            broadcaster.bind(websocket, workspace_id)

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
            + "\n\nVOICE MODE RULES: Keep each spoken reply to 1-3 short sentences. Never repeat "
            "yourself or state the same information twice. Brevity must not make you sound "
            "clipped or rushed — stay soft, unhurried and courteous.\n\n"
            + JOY_VOICE_PERSONA
        )

        # Primary: Google AI Studio v1alpha Live API (`models/gemini-3.8-live-extended-thinking`)
        compression_cfg = types.ContextWindowCompressionConfig(
            trigger_tokens=25600,
            sliding_window=types.SlidingWindow(target_tokens=12800),
        )

        # Primary: Google AI Studio Live API (`gemini-3.8-live` / `models/gemini-3.8-live-extended-thinking`)
        # following https://github.com/google-gemini/cookbook/blob/main/quickstarts/Get_started_LiveAPI.py
        if (
            clients.get("api_key_live_alpha") is not None
            or clients.get("api_key_client") is not None
        ):
            for live_client, candidate_model, use_thinking in [
                (clients.get("api_key_client"), "gemini-3.8-live", False),
                (clients.get("api_key_live_alpha"), LOGICAL_MODEL_ID, True),
            ]:
                if live_client is None:
                    continue
                try:
                    cfg_kwargs: dict[str, Any] = {
                        "response_modalities": ["AUDIO"],
                        "speech_config": build_joy_speech_config(),
                        "output_audio_transcription": types.AudioTranscriptionConfig(),
                        "input_audio_transcription": types.AudioTranscriptionConfig(),
                        "context_window_compression": compression_cfg,
                        "system_instruction": types.Content(
                            parts=[types.Part.from_text(text=voice_instruction)]
                        ),
                        "tools": MANDATE_TOOL_FUNCTIONS,
                    }
                    if use_thinking:
                        cfg_kwargs["thinking_config"] = types.ThinkingConfig(thinking_level="LOW")
                    ai_studio_config = types.LiveConnectConfig(**cfg_kwargs)
                    live_session_ctx = live_client.aio.live.connect(
                        model=candidate_model, config=ai_studio_config
                    )
                    live_session = await live_session_ctx.__aenter__()
                    logger.info("Connected to Gemini Live API (%s)", candidate_model)
                    break
                except Exception as ais_err:
                    logger.info(
                        "Gemini Live API (%s) handshake note (%s); trying next candidate",
                        candidate_model,
                        ais_err,
                    )
                    _trip_api_key_breaker()
                    live_session_ctx = None
                    live_session = None

        if live_session is None and clients.get("vertex_usc1") is not None:
            live_config = types.LiveConnectConfig(
                response_modalities=["AUDIO"],
                speech_config=build_joy_speech_config(),
                output_audio_transcription=types.AudioTranscriptionConfig(),
                input_audio_transcription=types.AudioTranscriptionConfig(),
                context_window_compression=compression_cfg,
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
            # `active_cid` / `active_stage` / `last_voice_tab_switched` / `live_session` MUST be
            # declared nonlocal. Without `last_voice_tab_switched` in `nonlocal`, Python treats it
            # as a function-local variable because it is assigned on `turn_complete`, raising
            # `UnboundLocalError` on the very first `input_transcription` chunk!
            nonlocal turn_counter, user_has_spoken_meaningfully, active_cid, active_stage
            nonlocal last_voice_tab_switched, live_session, live_session_ctx
            audio_seq = 0
            current_voice_turn_id = f"voice_turn_{turn_counter}"
            accumulated_out_text = ""
            accumulated_in_text = ""
            last_voice_tab_switched = ""
            last_voice_entity_switched = ""
            try:
                # Per `Get_started_LiveAPI.py` and `google.genai.live.AsyncSession.receive`:
                # `session.receive()` yields responses for ONE model turn and breaks when
                # `server_content.turn_complete` is True. The outer `while` re-enters `receive()`
                # for every subsequent turn, while `if not got_any: break` exits cleanly without
                # spinning when the underlying WebSocket actually closes (EOF).
                while live_session is not None:
                    got_any = False
                    async for msg in live_session.receive():
                        got_any = True
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
                                tool_res = await asyncio.to_thread(execute_mandate_tool, t_name, t_args)
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
                                    # If Joy was currently speaking, flush her output queue on the client immediately
                                    if accumulated_out_text:
                                        accumulated_out_text = ""
                                        await send_safe({"type": "interrupted", "reason": "user_voice_barge_in"})
                                    await send_safe(
                                        {
                                            "type": "input_transcript",
                                            "turn_id": f"{current_voice_turn_id}_user",
                                            "text": cleaned_in,
                                            "delta": chunk_in,
                                            "finished": bool(getattr(in_tr, "finished", False)),
                                        }
                                    )
                                    # Real-time voice tab switching ("switch to FX", "go to payments", "back to change of mandate")
                                    if cleaned_in != last_voice_tab_switched:
                                        tab_res = await asyncio.to_thread(
                                            _detect_tab_switch_intent, cleaned_in, active_cid
                                        )
                                        if tab_res and tab_res.get("ui_sync"):
                                            last_voice_tab_switched = cleaned_in
                                            ui_s = tab_res["ui_sync"]
                                            await send_safe(
                                                {
                                                    "type": "tool_call_result",
                                                    "call_id": f"voice_tab_{audio_seq}",
                                                    "tool_name": "switch_workspace_tab",
                                                    "args": {"tab_name": cleaned_in, "customer_id": active_cid},
                                                    "result": {
                                                        k: v
                                                        for k, v in tab_res.items()
                                                        if k not in ("workspace_snapshot", "ui_sync")
                                                    },
                                                    "ui_sync": ui_s,
                                                }
                                            )
                                            await broadcaster.broadcast(ui_s)
                                    # Real-time phonetic corporate entity switch (e.g. "Stuve Veritas Legal" -> CUST-004)
                                    if cleaned_in != last_voice_entity_switched:
                                        ent_res = await asyncio.to_thread(
                                            _detect_voice_entity_switch, cleaned_in, active_cid
                                        )
                                        if ent_res and ent_res.get("ui_sync"):
                                            last_voice_entity_switched = cleaned_in
                                            ui_e = ent_res["ui_sync"]
                                            if ui_e.get("updated_profile_id"):
                                                active_cid = str(ui_e["updated_profile_id"])
                                            await send_safe(
                                                {
                                                    "type": "tool_call_result",
                                                    "call_id": f"voice_entity_{audio_seq}",
                                                    "tool_name": "SwitchActiveCustomerProfile",
                                                    "args": {"customer_id": active_cid},
                                                    "result": {
                                                        k: v
                                                        for k, v in ent_res.items()
                                                        if k not in ("workspace_snapshot", "ui_sync")
                                                    },
                                                    "ui_sync": ui_e,
                                                }
                                            )
                                            await broadcaster.broadcast(ui_e)

                            mt = getattr(sc, "model_turn", None)
                            if mt and mt.parts:
                                for part in mt.parts:
                                    inline = getattr(part, "inline_data", None)
                                    if inline and inline.data:
                                        if time.monotonic() < barge_in_discard_until:
                                            continue
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
                            if out_tr and out_tr.text and time.monotonic() >= barge_in_discard_until:
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
                                last_voice_tab_switched = ""
                                last_voice_entity_switched = ""
                                await send_safe({"type": "turn_complete"})
                    if not got_any:
                        logger.info(
                            "Live reader loop: receive() returned no messages (EOF); "
                            "closing reader for this session."
                        )
                        break
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                # This loop is the ONLY path that delivers model audio to the browser. Logging its
                # death at debug level made a dead voice session look identical to an idle one:
                # the greeting played, then every later turn silently returned nothing.
                logger.warning("Live reader loop crashed: %s", exc, exc_info=True)
            finally:
                logger.info("Live reader loop exited after %d audio chunks.", audio_seq)
                # Mark the session closed so the next audio_chunk transparently reconnects
                live_session = None

        logger.info("Starting live reader loop for the conversational session.")
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
                if frame.get("workspace_id"):
                    bind_workspace(frame["workspace_id"])
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
                        "workspace_id": workspace_id,
                        "tools_registered": list(MANDATE_TOOL_MAP.keys()),
                    }
                )

            elif msg_type in ("barge_in", "interrupt"):
                # Abort any active verbatim greeting narration and discard stale in-flight chunks
                # from the old turn while preserving the conversational session history.
                interrupt_event.set()
                barge_in_discard_until = time.monotonic() + 0.85
                await send_safe({"type": "interrupted", "reason": "user_barge_in"})
                await send_safe({"type": "barge_in_ack"})

            elif msg_type == "audio_chunk":
                b64_audio = frame.get("pcm16_base64") or frame.get("data") or ""
                if b64_audio:
                    if live_session is None and time.monotonic() < live_session_retry_after:
                        pass
                    else:
                        try:
                            raw_pcm = base64.b64decode(b64_audio)
                            sess = await ensure_live_audio_session()
                            await sess.send_realtime_input(
                                audio=types.Blob(
                                    data=raw_pcm,
                                    mime_type=frame.get("mime_type") or "audio/pcm;rate=16000",
                                )
                            )
                            live_session_backoff = 0.5
                        except Exception as audio_err:
                            # CRITICAL: Tear down the dead live_session so the next chunk after
                            # the short backoff reconnects instead of looping on a dead socket!
                            await close_live_audio_session()
                            live_session_backoff = min(live_session_backoff * 1.5, 2.0)
                            live_session_retry_after = time.monotonic() + live_session_backoff
                            logger.warning(
                                "Live audio chunk failed (%s); reconnecting in %.1fs.",
                                audio_err,
                                live_session_backoff,
                            )

            elif msg_type == "audio_stream_end":
                if live_session is not None:
                    try:
                        await live_session.send_realtime_input(audio_stream_end=True)
                    except Exception:
                        await live_session.send(input="", end_of_turn=True)

            elif msg_type == "voice_greeting":
                if frame.get("workspace_id"):
                    bind_workspace(frame["workspace_id"])
                if frame.get("customer_id"):
                    active_cid = str(frame["customer_id"])

                turn_counter += 1
                g_tid = f"voice_greeting_{turn_counter}"
                interrupt_event.clear()

                # 1. Publish Joy's greeting transcript immediately so the ringer stops and the
                #    iChat bubble shows the banker greeting (never a customer line).
                await send_safe(
                    {
                        "type": "output_transcript",
                        "turn_id": g_tid,
                        "role": "assistant",
                        "text": JOY_GREETING_TEXT.replace("F X", "FX"),
                        "is_streaming": False,
                        "final": True,
                    }
                )

                # 2. Speak the EXACT same sentence aloud via a dedicated verbatim narration session.
                spoke = await speak_verbatim_as_joy(
                    JOY_GREETING_TEXT, send_safe, interrupt_event=interrupt_event, turn_id=g_tid
                )

                if not spoke:
                    # Cloudtop ADC expired and no local AI Studio audio: stream the greeting audio
                    # through the deployed Cloud Run service account instead.
                    try:
                        import websockets

                        ws_bridge_url = "wss://gemini-live-mandate-app-327571158527.us-central1.run.app/ws/live"
                        async with websockets.connect(ws_bridge_url, open_timeout=8) as r_ws:
                            await r_ws.send(
                                json.dumps({"type": "voice_greeting", "customer_id": active_cid})
                            )
                            async for r_raw in r_ws:
                                r_msg = json.loads(r_raw)
                                if r_msg.get("type") == "audio_out":
                                    await send_safe(r_msg)
                                elif r_msg.get("type") == "turn_complete":
                                    break
                    except Exception as bridge_exc:
                        logger.info("Cloud Run greeting bridge unavailable: %s", bridge_exc)

                await send_safe({"type": "turn_complete", "turn_id": g_tid})

                # 3. Warm up the conversational session in the background so the very next thing the
                #    user says is handled instantly (and it never speaks first).
                try:
                    await ensure_live_audio_session()
                except Exception as warm_exc:
                    logger.info("Conversational session warm-up deferred: %s", warm_exc)

            elif msg_type in ("text_turn", "user_message", "chat"):
                user_text = str(frame.get("text") or frame.get("message") or "").strip()
                if frame.get("workspace_id"):
                    bind_workspace(frame["workspace_id"])
                if frame.get("customer_id"):
                    active_cid = str(frame["customer_id"])
                if frame.get("stage") or frame.get("current_stage"):
                    active_stage = int(frame.get("stage") or frame.get("current_stage") or active_stage)
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

                try:
                    turn_res = await run_agent_chat_turn(
                        message=user_text,
                        customer_id=active_cid,
                        current_stage=active_stage,
                        event_callback=send_safe,
                        history_key=workspace_id,
                    )
                except Exception as exc:
                    # A model/credential error must not drop the socket: answer in-chat and
                    # release the composer so the user can retry.
                    logger.warning("Chat turn failed (%s): %s", type(exc).__name__, exc)
                    turn_res = {
                        "customer_id": active_cid,
                        "reply": (
                            "I couldn't reach the Gemini model just now, so nothing was changed. "
                            "Please try again in a moment or use the workspace controls directly."
                        ),
                        "thinking_traces": [],
                        "tool_calls": [],
                        "ui_sync": None,
                    }
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
