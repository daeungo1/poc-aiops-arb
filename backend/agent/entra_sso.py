"""
Microsoft Entra ID SSO (OIDC) + MSAL Confidential Client with **UAMI 페더레이션** (client_assertion).

- **필수 env:** `AZURE_AUTH_CLIENT_ID`, `AZURE_AUTH_TENANT_ID`, `AZURE_AUTH_REDIRECT_URI`, `AZURE_AUTH_STATE_SECRET`
- **`AZURE_AUTH_REDIRECT_URI`:** Entra가 리다이렉트할 **백엔드 콜백** 주소. 경로는 `/api/getAToken`(BE가 직접 수신·쿠키 설정 후 FE `/`로 redirect).
  예) 로컬 docker(nginx :80) `http://localhost/api/getAToken`, 배포 `https://<도메인>/api/getAToken`.
- **클라이언트 인증(아래 중 하나):**
  - **로컬:** `AZURE_AUTH_CLIENT_SECRET` — Entra 앱의 client secret. UAMI 불필요.
  - **배포:** `AZURE_AUTH_SSO_UAMI_CLIENT_ID` 또는 `AZURE_RESOURCE_READER_UAMI_CLIENT_ID`(동일 UAMI 재사용).
    Entra 앱 등록에 해당 관리 ID용 **페더레이션 자격 증명**이 있어야 하며, MSAL은 `ManagedIdentityCredential` 로
    기본 `api://AzureADTokenExchange/.default` 토큰을 **client_assertion** 으로 사용합니다.
  - 둘 다 설정 시 `AZURE_AUTH_CLIENT_SECRET` 이 우선합니다.
- **선택:** `AZURE_AUTH_AUTHORITY`, `AZURE_AUTH_FEDERATION_TOKEN_SCOPE`, `AZURE_SCOPES`

- **`AZURE_AUTH_STATE_SECRET`:** OAuth CSRF/state HMAC 서명용(Entropy 높은 임의 문자열). Entra에 등록 불필요.
- `AZURE_SCOPES`에 `openid`/`offline_access`/`profile` 넣지 마세요(MSAL 예약어).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time
from typing import Any

from azure.core.credentials import AccessToken
from azure.core.exceptions import ClientAuthenticationError

COOKIE_ACCESS_TOKEN = "aiops_azure_access_token"
# 구버전에서 발급됐을 수 있는 쿠키(로그아웃·새 로그인 시 삭제)
LEGACY_AUTH_COOKIE_NAMES: tuple[str, ...] = (
    "aiops_azure_storage_token",
    "aiops_azure_cognitive_token",
    "aiops_azure_refresh_token",
)

STATE_TTL_SEC = 600.0

_msal_app: Any = None
_msal_lock = threading.Lock()
_sso_federation_mi_credential: Any = None
_sso_federation_mi_lock = threading.Lock()

_SCALAR_ENV_COMMON: tuple[str, ...] = (
    "AZURE_AUTH_CLIENT_ID",
    "AZURE_AUTH_TENANT_ID",
    "AZURE_AUTH_REDIRECT_URI",
    "AZURE_AUTH_STATE_SECRET",
)


def _sso_federation_mi_client_id() -> str:
    """MSAL SSO 페더레이션용 UAMI 클라이언트 ID."""
    a = (os.environ.get("AZURE_AUTH_SSO_UAMI_CLIENT_ID") or "").strip()
    if a:
        return a
    return (os.environ.get("AZURE_RESOURCE_READER_UAMI_CLIENT_ID") or "").strip()


def _sso_client_secret() -> str:
    """로컬 개발용 client secret(설정 시 UAMI 페더레이션 대신 사용)."""
    return (os.environ.get("AZURE_AUTH_CLIENT_SECRET") or "").strip()


def missing_sso_env_keys() -> list[str]:
    """값이 비어 있거나 불충분한 필수 SSO 환경 변수(명칭) 목록."""
    missing = [k for k in _SCALAR_ENV_COMMON if not (os.environ.get(k) or "").strip()]
    # 클라이언트 인증: client secret(로컬) 또는 UAMI 페더레이션(배포) 중 하나만 있으면 됨.
    if not _sso_client_secret() and not _sso_federation_mi_client_id():
        missing.append(
            "(AZURE_AUTH_CLIENT_SECRET 또는 AZURE_AUTH_SSO_UAMI_CLIENT_ID/AZURE_RESOURCE_READER_UAMI_CLIENT_ID)"
        )
    return missing


def is_sso_configured() -> bool:
    return not missing_sso_env_keys()


def require_sso_env() -> None:
    """MSAL·state 서명 등 SSO 동작 전 호출. 누락 또는 상충 시 RuntimeError."""
    m = missing_sso_env_keys()
    if m:
        raise RuntimeError(
            "Entra SSO 필수 환경 변수가 없습니다: "
            + ", ".join(m)
            + ". 공통: AZURE_AUTH_CLIENT_ID, TENANT_ID, REDIRECT_URI, STATE_SECRET. "
            + "클라이언트 인증: AZURE_AUTH_CLIENT_SECRET(로컬) 또는 "
            + "AZURE_AUTH_SSO_UAMI_CLIENT_ID/AZURE_RESOURCE_READER_UAMI_CLIENT_ID(UAMI 페더레이션)."
        )


def _sso_client_id() -> str:
    require_sso_env()
    return (os.environ.get("AZURE_AUTH_CLIENT_ID") or "").strip()


def _sso_tenant_id() -> str:
    require_sso_env()
    return (os.environ.get("AZURE_AUTH_TENANT_ID") or "").strip()


def _authority() -> str:
    require_sso_env()
    a = (os.environ.get("AZURE_AUTH_AUTHORITY") or "").strip()
    if a:
        return a.rstrip("/")
    tid = _sso_tenant_id()
    return f"https://login.microsoftonline.com/{tid}"


def get_configured_redirect_uri() -> str:
    require_sso_env()
    return (os.environ.get("AZURE_AUTH_REDIRECT_URI") or "").strip().rstrip("/")


def _redirect_uri() -> str:
    return get_configured_redirect_uri()


# 로그인·코드 교환: ARM만. refresh는 MSAL/Entra가 confidential client에 대해 흔히 함께 발급(storage/cognitive URL은 authorize에 넣지 않음).
_DEFAULT_LOGIN_SCOPES: tuple[str, ...] = (
    "https://management.azure.com/user_impersonation",
)

# MSAL get_authorization_request_url 에 넣으면 안 되는 예약 scope(자동 추가됨).
_MSAL_RESERVED_SCOPES: frozenset[str] = frozenset({"openid", "offline_access", "profile"})


def _scopes() -> list[str]:
    # User.Read(Graph)는 넣지 않음 — access_token aud가 Graph가 되어 ARM이 거부되는 경우가 많음. 프로필은 id_token.
    raw = (os.environ.get("AZURE_SCOPES") or "").strip()
    if raw:
        parts = [s for s in raw.split() if s and s not in _MSAL_RESERVED_SCOPES]
        return parts if parts else list(_DEFAULT_LOGIN_SCOPES)
    return list(_DEFAULT_LOGIN_SCOPES)


def _get_sso_federation_managed_identity_credential() -> Any:
    global _sso_federation_mi_credential
    require_sso_env()
    if _sso_federation_mi_credential is not None:
        return _sso_federation_mi_credential
    with _sso_federation_mi_lock:
        if _sso_federation_mi_credential is not None:
            return _sso_federation_mi_credential
        from azure.identity import ManagedIdentityCredential

        cid = _sso_federation_mi_client_id()
        if not cid:
            raise RuntimeError(
                "UAMI 클라이언트 ID가 없습니다. "
                "AZURE_AUTH_SSO_UAMI_CLIENT_ID 또는 AZURE_RESOURCE_READER_UAMI_CLIENT_ID 를 설정하세요."
            )
        _sso_federation_mi_credential = ManagedIdentityCredential(client_id=cid)
        return _sso_federation_mi_credential


def _federated_client_assertion_string() -> str:
    """Entra 페더레이션 교환용 액세스 토큰(MSAL client_assertion)."""
    cred = _get_sso_federation_managed_identity_credential()
    scope = (os.environ.get("AZURE_AUTH_FEDERATION_TOKEN_SCOPE") or "api://AzureADTokenExchange/.default").strip()
    tok = cred.get_token(scope)
    return tok.token


def _get_msal_client_credential() -> str | dict[str, Any]:
    """MSAL용 클라이언트 자격 증명.

    - `AZURE_AUTH_CLIENT_SECRET` 설정 시(로컬 개발) client secret 문자열 사용.
    - 미설정 시 UAMI 페더레이션 client_assertion(매 호출 시 MI 토큰 재발급).
    """
    secret = _sso_client_secret()
    if secret:
        return secret
    return {"client_assertion": lambda: _federated_client_assertion_string()}


def _get_msal_app() -> Any:
    global _msal_app
    require_sso_env()
    if _msal_app is not None:
        return _msal_app
    with _msal_lock:
        if _msal_app is not None:
            return _msal_app
        from msal import ConfidentialClientApplication

        cred = _get_msal_client_credential()
        _msal_app = ConfidentialClientApplication(
            _sso_client_id(),
            authority=_authority(),
            client_credential=cred,
        )
        return _msal_app


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(s: str) -> bytes:
    pad = (4 - len(s) % 4) % 4
    return base64.urlsafe_b64decode(s + "=" * pad)


def _state_hmac_key() -> bytes:
    """클라이언트 인증 시크릿과 분리된 앱 로컬 비밀(Entra 미등록)."""
    require_sso_env()
    s = (os.environ.get("AZURE_AUTH_STATE_SECRET") or "").strip()
    if len(s) < 16:
        raise RuntimeError("AZURE_AUTH_STATE_SECRET 은 안전한 HMAC용으로 16바이트 이상이어야 합니다.")
    return s.encode("utf-8")


def create_signed_oauth_state() -> str:
    import secrets

    body = {
        "exp": int(time.time()) + int(STATE_TTL_SEC),
        "n": secrets.token_hex(16),
    }
    payload = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_b64 = _b64url_encode(payload)
    sig = hmac.new(_state_hmac_key(), payload_b64.encode("ascii"), hashlib.sha256).digest()
    sig_b64 = _b64url_encode(sig)
    return f"{payload_b64}.{sig_b64}"


def verify_signed_oauth_state(state: str) -> bool:
    if not state or "." not in state:
        return False
    dot = state.rfind(".")
    payload_b64, sig_b64 = state[:dot], state[dot + 1 :]
    if not payload_b64 or not sig_b64:
        return False
    try:
        sig_expected = hmac.new(
            _state_hmac_key(),
            payload_b64.encode("ascii"),
            hashlib.sha256,
        ).digest()
        sig_actual = _b64url_decode(sig_b64)
        if not hmac.compare_digest(sig_expected, sig_actual):
            return False
        body = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
        exp = int(body.get("exp", 0))
        if int(time.time()) > exp:
            return False
    except Exception:
        return False
    return True


def parse_authorization_bearer(authorization_header: str | None) -> str | None:
    if not authorization_header:
        return None
    h = authorization_header.strip()
    if not h.lower().startswith("bearer "):
        return None
    t = h[7:].strip()
    return t if t else None


def cookie_secure_for_request(scheme: str) -> bool:
    if (os.environ.get("COOKIE_SECURE") or "").strip().lower() in ("1", "true", "yes"):
        return True
    return scheme.lower() == "https"


def jwt_expires_on(token: str) -> int:
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return int(time.time()) + 3600
        payload_b64 = parts[1]
        pad = (4 - len(payload_b64) % 4) % 4
        raw = base64.urlsafe_b64decode(payload_b64 + "=" * pad)
        payload = json.loads(raw)
        exp = payload.get("exp")
        if isinstance(exp, (int, float)):
            return int(exp)
    except Exception:
        pass
    return int(time.time()) + 3600


_GRAPH_APP_ID = "00000003-0000-0000-c000-000000000000"


def _jwt_payload_dict(token: str) -> dict[str, Any]:
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        payload_b64 = parts[1]
        pad = (4 - len(payload_b64) % 4) % 4
        raw = base64.urlsafe_b64decode(payload_b64 + "=" * pad)
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _token_audiences(token: str) -> frozenset[str]:
    aud = _jwt_payload_dict(token).get("aud")
    if isinstance(aud, list):
        return frozenset(str(x) for x in aud)
    if aud:
        return frozenset({str(aud)})
    return frozenset()


def _is_graph_audience(auds: frozenset[str]) -> bool:
    if _GRAPH_APP_ID in auds:
        return True
    for a in auds:
        al = a.lower().rstrip("/")
        if "graph.microsoft.com" in al:
            return True
    return False


def _scope_list_targets_management(scope_list: list[str]) -> bool:
    for s in scope_list:
        sl = s.lower()
        if "management.azure.com" in sl or "management.core.windows.net" in sl:
            return True
    return False


def _is_arm_resource_audience(auds: frozenset[str]) -> bool:
    """로그인 access_token aud가 ARM(관리 평면)인지. 이 경우 OBO 대신 토큰 그대로 쓴다."""
    for a in auds:
        al = str(a).lower().rstrip("/")
        if "management.azure.com" in al or "management.core.windows.net" in al:
            return True
    return False


class UserOboCredential:
    """
    로그인 시 받은 사용자 ARM 액세스 토큰만 처리한다.

    ARM(`management.azure.com`) scope이고 토큰 aud가 이미 ARM이면 그대로 반환.
    그 외 scope(Storage·Cognitive·Foundry 등)는 OBO 없이 거부 — 해당 API는 DefaultAzureCredential만 사용.
    """

    def __init__(self, user_access_token: str) -> None:
        t = (user_access_token or "").strip()
        self._assertion = t
        self._assertion_expires_on = jwt_expires_on(t) if t else int(time.time())

    def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        if not self._assertion:
            raise ValueError("사용자 액세스 토큰이 없습니다.")
        scope_list = [s for s in scopes if s]
        if not scope_list:
            scope_list = ["https://management.azure.com/.default"]
        auds = _token_audiences(self._assertion)
        if _scope_list_targets_management(scope_list) and _is_arm_resource_audience(auds):
            return AccessToken(self._assertion, self._assertion_expires_on)
        if _scope_list_targets_management(scope_list) and _is_graph_audience(auds):
            raise ClientAuthenticationError(
                message=(
                    "액세스 토큰 audience가 Microsoft Graph입니다. ARM(구독·리소스) API에는 "
                    "management.azure.com용 토큰이 필요합니다. AZURE_SCOPES에서 User.Read 등 Graph 스코프를 빼고 "
                    "https://management.azure.com/user_impersonation 만 남긴 뒤 로그아웃하고 다시 로그인하세요."
                )
            )
        raise ClientAuthenticationError(
            message=(
                "이 자격 증명은 Azure Resource Manager(management.azure.com) 전용입니다. "
                "Storage·Cognitive·Foundry·AI Search 등은 서버의 DefaultAzureCredential(예: MI, AZURE_CLIENT_*)을 "
                "사용하도록 구성하세요."
            )
        )


def all_auth_cookie_names() -> tuple[str, ...]:
    """로그아웃·콜백 시 제거할 HttpOnly 인증 쿠키(구버전 storage/cognitive/refresh 포함)."""
    return (COOKIE_ACCESS_TOKEN,) + LEGACY_AUTH_COOKIE_NAMES


def build_login_authorization_url() -> tuple[str, str]:
    app = _get_msal_app()
    state = create_signed_oauth_state()
    url = app.get_authorization_request_url(
        _scopes(),
        state=state,
        redirect_uri=_redirect_uri(),
    )
    return url, state


def exchange_code_for_result(code: str) -> dict[str, Any]:
    app = _get_msal_app()
    return app.acquire_token_by_authorization_code(
        code,
        scopes=_scopes(),
        redirect_uri=_redirect_uri(),
    )
