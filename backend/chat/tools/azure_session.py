"""Per-request Azure tenant/subscription context (set from AG-UI HTTP headers)."""

from __future__ import annotations

from contextvars import ContextVar

_azure_tenant_id: ContextVar[str | None] = ContextVar("azure_tenant_id", default=None)
_azure_subscription_id: ContextVar[str | None] = ContextVar("azure_subscription_id", default=None)
_azure_subscription_name: ContextVar[str | None] = ContextVar("azure_subscription_name", default=None)
_chat_thread_id: ContextVar[str | None] = ContextVar("chat_thread_id", default=None)


def set_azure_session(
    *,
    tenant_id: str | None,
    subscription_id: str | None,
    subscription_name: str | None = None,
    thread_id: str | None = None,
) -> tuple:
    """Set session context; returns reset tokens for clear_azure_session."""
    return (
        _azure_tenant_id.set(tenant_id),
        _azure_subscription_id.set(subscription_id),
        _azure_subscription_name.set(subscription_name),
        _chat_thread_id.set(thread_id),
    )


def clear_azure_session(tokens: tuple) -> None:
    _azure_tenant_id.reset(tokens[0])
    _azure_subscription_id.reset(tokens[1])
    if len(tokens) > 2:
        _azure_subscription_name.reset(tokens[2])
    if len(tokens) > 3:
        _chat_thread_id.reset(tokens[3])


def get_session_tenant_id() -> str | None:
    return _azure_tenant_id.get()


def get_session_subscription_id() -> str | None:
    return _azure_subscription_id.get()


def get_session_subscription_name() -> str | None:
    return _azure_subscription_name.get()


def get_chat_thread_id() -> str | None:
    return _chat_thread_id.get()


def effective_subscription_ids(tool_subscription_arg: str) -> list[str] | None:
    """UI 세션 구독이 있으면 우선, 없으면 도구 인자, 둘 다 없으면 None(CLI 기본)."""
    sid = get_session_subscription_id()
    if sid:
        return [sid]
    if tool_subscription_arg:
        return [tool_subscription_arg]
    return None


def resolve_assessment_subscription_id() -> str | None:
    """스토리지/AI Search 스냅샷 범위: UI 세션 구독 우선, 없으면 get_session_bootstrap 기본 구독."""
    s = (get_session_subscription_id() or "").strip()
    if s:
        return s
    try:
        from agent.azure_resource_reader import AzureResourceReader

        info = AzureResourceReader.get_session_bootstrap()
        return (info.get("subscription_id") or "").strip() or None
    except Exception:
        return None
