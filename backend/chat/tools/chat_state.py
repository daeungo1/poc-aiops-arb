"""In-memory per-chat-thread state for tool handoff."""

from __future__ import annotations

from threading import Lock
from typing import Any

from chat.tools.azure_session import get_chat_thread_id

_lock = Lock()
_state_by_thread: dict[str, dict[str, Any]] = {}


def _current_key() -> str | None:
    thread_id = (get_chat_thread_id() or "").strip()
    return thread_id or None


def set_last_assessment_report_id(report_id: int, subscription_id: str | None = None) -> None:
    key = _current_key()
    if not key or report_id <= 0:
        return
    with _lock:
        state = _state_by_thread.setdefault(key, {})
        state["last_assessment_report_id"] = int(report_id)
        state["last_assessment_subscription_id"] = subscription_id


def get_last_assessment_report_id(subscription_id: str | None = None) -> int | None:
    key = _current_key()
    if not key:
        return None
    with _lock:
        state = dict(_state_by_thread.get(key) or {})
    report_id = state.get("last_assessment_report_id")
    stored_sub = state.get("last_assessment_subscription_id")
    if subscription_id and stored_sub and stored_sub != subscription_id:
        return None
    try:
        rid = int(report_id)
    except (TypeError, ValueError):
        return None
    return rid if rid > 0 else None
