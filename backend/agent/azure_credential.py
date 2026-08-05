"""요청 경로·Entra 설정에 따라 DefaultAzureCredential 또는 SSO UserOboCredential(ARM 전용)."""

from __future__ import annotations

import os
import threading
from contextvars import ContextVar
from typing import Any

_request_sso_credential: ContextVar[Any | None] = ContextVar("request_sso_credential", default=None)
_request_user_delegation: ContextVar[bool] = ContextVar("request_user_delegation", default=False)

# main.py CLI 전용: DefaultAzureCredential 등
_cli_fallback_credential: ContextVar[Any | None] = ContextVar("_cli_fallback_credential", default=None)

_default_credential: Any | None = None
_default_cred_lock = threading.Lock()
_resource_reader_credential: Any | None = None
_resource_reader_cred_lock = threading.Lock()

RESOURCE_READER_UAMI_CLIENT_ID_ENV = "AZURE_RESOURCE_READER_UAMI_CLIENT_ID"
RESOURCE_READER_UAMI_OBJECT_ID_ENV = "AZURE_RESOURCE_READER_UAMI_OBJECT_ID"
RESOURCE_READER_UAMI_RESOURCE_ID_ENV = "AZURE_RESOURCE_READER_UAMI_RESOURCE_ID"
RESOURCE_READER_UAMI_NAME_ENV = "AZURE_RESOURCE_READER_UAMI_NAME"


def path_requires_user_delegated_azure(http_path: str) -> bool:
    """
    Azure Resource Manager(ARM) 호출에 사용자 위임 토큰이 필요한 API만 True.

    Cognitive·Foundry·AI Search는 항상 DefaultAzureCredential(MI/환경 등)만 사용하며
    이 플래그와 무관합니다.
    """
    p = (http_path or "").rstrip("/") or "/"
    if p == "/api/chat":
        return True
    if p == "/api/azure" or p.startswith("/api/azure/"):
        return True
    if p == "/api/v2" or p.startswith("/api/v2/"):
        return True
    for prefix in ("/api/assessments", "/api/terraform", "/api/downloads"):
        if p == prefix or p.startswith(prefix + "/"):
            return True
    return False


def _get_default_azure_credential() -> Any:
    global _default_credential
    if _default_credential is None:
        with _default_cred_lock:
            if _default_credential is None:
                from azure.identity import DefaultAzureCredential

                # App Service에 UAMI+시스템 할당 MI가 같이 있어도 AZURE_CLIENT_ID 없이 시스템 할당만 사용
                _default_credential = DefaultAzureCredential(
                    exclude_interactive_browser_credential=True,
                    managed_identity_client_id=None,
                    workload_identity_client_id=None,
                )
    return _default_credential


def get_default_azure_credential() -> Any:
    """Cognitive·Foundry·Search 등: 항상 DefaultAzureCredential만 (OBO·사용자 토큰 없음)."""
    return _get_default_azure_credential()


def get_resource_reader_azure_credential() -> Any:
    """리소스 조회·평가·구독 교집합 확인 전용 credential.

    ``AZURE_RESOURCE_READER_UAMI_CLIENT_ID``가 있으면 해당 User-Assigned MI를 사용하고,
    로컬처럼 IMDS가 없는 환경에서는 기존 기본 credential로 폴백합니다.
    값이 없으면 기존 기본 credential(System-Assigned MI 등)을 그대로 사용합니다.
    """
    global _resource_reader_credential
    client_id = (os.environ.get(RESOURCE_READER_UAMI_CLIENT_ID_ENV) or "").strip()
    if not client_id:
        return _get_default_azure_credential()

    if _resource_reader_credential is None:
        with _resource_reader_cred_lock:
            if _resource_reader_credential is None:
                from azure.identity import ChainedTokenCredential, ManagedIdentityCredential

                _resource_reader_credential = ChainedTokenCredential(
                    ManagedIdentityCredential(client_id=client_id),
                    _get_default_azure_credential(),
                )
    return _resource_reader_credential


def set_request_sso_credential(credential: Any | None) -> Any:
    """미들웨어용: ContextVar set 토큰 반환."""
    return _request_sso_credential.set(credential)


def reset_request_sso_credential(token: Any) -> None:
    _request_sso_credential.reset(token)


def set_request_user_delegation(enabled: bool) -> Any:
    """미들웨어용: True면 get_effective가 SSO 자격 증명을 요구(Entra 설정 시)."""
    return _request_user_delegation.set(enabled)


def reset_request_user_delegation(token: Any) -> None:
    _request_user_delegation.reset(token)


def push_cli_credential(credential: Any) -> Any:
    """CLI 진입 시에만 사용. 반환 토큰으로 pop_cli_credential 호출."""
    return _cli_fallback_credential.set(credential)


def pop_cli_credential(token: Any) -> None:
    _cli_fallback_credential.reset(token)


class LazyDelegatedCredential:
    """get_token 시점에 get_effective_azure_credential() 사용(ARM 사용자 위임 경로 전용)."""

    def get_token(self, *scopes: str, **kwargs: Any) -> Any:
        return get_effective_azure_credential().get_token(*scopes, **kwargs)


class LazyDefaultAzureCredential:
    """Storage·Cognitive·Foundry·AI Search 등: 항상 DefaultAzureCredential만 (OBO 없음)."""

    def get_token(self, *scopes: str, **kwargs: Any) -> Any:
        return _get_default_azure_credential().get_token(*scopes, **kwargs)


def get_effective_azure_credential() -> Any:
    """
    1) CLI 모드 push_cli_credential
    2) Entra 미설정 → DefaultAzureCredential
    3) Entra 설정 + 사용자 위임 경로 → HttpOnly SSO UserOboCredential(ARM 전용)
    4) 그 외 → DefaultAzureCredential
    """
    cli = _cli_fallback_credential.get()
    if cli is not None:
        return cli

    from agent.entra_sso import is_sso_configured

    if not is_sso_configured():
        return _get_default_azure_credential()

    if _request_user_delegation.get():
        c = _request_sso_credential.get()
        if c is None:
            raise RuntimeError(
                "이 작업에는 Microsoft 계정 로그인이 필요합니다. "
                "/login 에서 로그인한 뒤 다시 시도하세요."
            )
        return c

    return _get_default_azure_credential()
