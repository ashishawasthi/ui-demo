"""Per-browser-session workspace context for the login-less demo.

Each browser tab generates its own short workspace id (kept in ``sessionStorage``)
and sends it as the ``X-Workspace-Id`` header on REST calls and inside the
``init`` frame on ``/ws/live``. The id selects the row in ``active_workspace_state``
that holds that tab's active customer and stage, so two people (or two tabs) no
longer overwrite each other's context. Requests without an id fall back to the
seeded ``DEFAULT_WORKSPACE`` row, which keeps direct tool calls and the existing
test-suite behaviour unchanged.

``ui_sync_origin`` records whether a tool call was triggered by a plain REST
request ("rest") or by the copilot over the live WebSocket ("live"). The frontend
only follows stage navigation for copilot-originated events.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
import re

DEFAULT_WORKSPACE_ID = "DEFAULT_WORKSPACE"
WORKSPACE_HEADER = "X-Workspace-Id"
_WORKSPACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{4,64}$")

_current_workspace_id: ContextVar[str] = ContextVar("workspace_id", default=DEFAULT_WORKSPACE_ID)
_current_ui_sync_origin: ContextVar[str] = ContextVar("ui_sync_origin", default="rest")


def normalize_workspace_id(raw: str | None) -> str:
    """Return a safe workspace id, falling back to the default for missing/invalid values."""
    candidate = (raw or "").strip()
    if candidate and _WORKSPACE_ID_PATTERN.match(candidate):
        return candidate
    return DEFAULT_WORKSPACE_ID


def get_workspace_id() -> str:
    return _current_workspace_id.get()


def set_workspace_id(raw: str | None) -> Token[str]:
    return _current_workspace_id.set(normalize_workspace_id(raw))


def reset_workspace_id(token: Token[str]) -> None:
    _current_workspace_id.reset(token)


def get_ui_sync_origin() -> str:
    return _current_ui_sync_origin.get()


def set_ui_sync_origin(origin: str) -> Token[str]:
    return _current_ui_sync_origin.set("live" if origin == "live" else "rest")


def reset_ui_sync_origin(token: Token[str]) -> None:
    _current_ui_sync_origin.reset(token)
