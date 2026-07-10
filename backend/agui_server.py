"""
AG-UI server for AIOps Resource Assessment.

FastAPI server exposing:
- AG-UI protocol endpoint for CopilotKit frontend (chat)
- REST API endpoints for dashboard boards (data)
- Static file serving for terraform downloads (`/api/downloads`)
"""

import logging
import os
import threading
import mimetypes
from pathlib import Path
from datetime import datetime
from typing import Any, Optional, Annotated
from urllib.parse import quote, unquote

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, UploadFile, File, Request, Query
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import PlainTextResponse, JSONResponse, Response, RedirectResponse
from starlette.concurrency import run_in_threadpool

load_dotenv()

from agent.subscription_scope import normalize_subscription_id
from agent.storage_paths import LEGACY_STORAGE_SUBSCRIPTION_KEY

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ── Azure chat session (tenant + subscription from UI headers) ──
CHAT_PATH = "/api/chat"
HDR_AZURE_TENANT = "X-Azure-Tenant-Id"
HDR_AZURE_SUBSCRIPTION = "X-Azure-Subscription-Id"
HDR_AZURE_SUBSCRIPTION_NAME = "X-Azure-Subscription-Name"


def _decode_header_value(value: str | None) -> str | None:
    raw = (value or "").strip()
    return unquote(raw).strip() or None


def _validate_azure_session_headers(subscription_id: str | None, tenant_id: str | None) -> tuple[str | None, str | None]:
    """헤더로 전달된 구독·테넌트를 검증하고, 컨텍스트에 넣을 (tenant_id, subscription_id) 반환."""
    if not subscription_id:
        return None, None
    from agent.azure_resource_reader import AzureResourceReader

    resolved_tenant = AzureResourceReader.resolve_subscription_tenant(subscription_id)
    if not resolved_tenant:
        raise HTTPException(
            status_code=400,
            detail="구독 ID를 확인할 수 없거나 이 자격 증명으로 접근할 수 없습니다.",
        )
    if tenant_id and normalize_subscription_id(tenant_id) != normalize_subscription_id(
        resolved_tenant
    ):
        raise HTTPException(
            status_code=400,
            detail="테넌트 ID가 선택한 구독과 일치하지 않습니다.",
        )
    return resolved_tenant, subscription_id


class DelegatedUserTokenMiddleware(BaseHTTPMiddleware):
    """
    Entra 설정 시: ARM 사용자 위임 경로(/api/chat, /api/azure/* 구독·리소스, /api/assessments*, /api/terraform*, /api/downloads*)에만
    HttpOnly ARM 쿠키(또는 Bearer)로 UserOboCredential 설정.
    Cognitive·Foundry 등은 항상 DefaultAzureCredential.
    """

    async def dispatch(self, request: Request, call_next):
        from agent.azure_credential import (
            path_requires_user_delegated_azure,
            reset_request_sso_credential,
            reset_request_user_delegation,
            set_request_sso_credential,
            set_request_user_delegation,
        )
        from agent.entra_sso import COOKIE_ACCESS_TOKEN, UserOboCredential, parse_authorization_bearer

        path = request.url.path
        need_user = path_requires_user_delegated_azure(path)

        cookie_arm = (request.cookies.get(COOKIE_ACCESS_TOKEN) or "").strip()
        bearer_t = parse_authorization_bearer(request.headers.get("Authorization")) or ""
        raw_arm = (cookie_arm or bearer_t).strip()

        cred = UserOboCredential(raw_arm) if (need_user and raw_arm) else None

        var_del = set_request_user_delegation(need_user)
        var_tok = set_request_sso_credential(cred)
        try:
            return await call_next(request)
        finally:
            reset_request_sso_credential(var_tok)
            reset_request_user_delegation(var_del)


class AzureSessionContextMiddleware(BaseHTTPMiddleware):
    """POST /api/chat 요청에 한해 X-Azure-* 헤더로 테넌트·구독 컨텍스트를 설정."""

    async def dispatch(self, request: Request, call_next):
        if request.method != "POST" or request.url.path.rstrip("/") != CHAT_PATH:
            return await call_next(request)

        from chat.tools.azure_session import set_azure_session, clear_azure_session

        sub = (request.headers.get(HDR_AZURE_SUBSCRIPTION) or "").strip() or None
        sub_name = _decode_header_value(request.headers.get(HDR_AZURE_SUBSCRIPTION_NAME))
        tenant = (request.headers.get(HDR_AZURE_TENANT) or "").strip() or None
        thread_id = (request.headers.get("X-Thread-Id") or "").strip() or None
        try:
            rt, rs = _validate_azure_session_headers(sub, tenant)
        except HTTPException as e:
            return JSONResponse({"detail": e.detail}, status_code=e.status_code)
        tokens = set_azure_session(
            tenant_id=rt,
            subscription_id=rs,
            subscription_name=sub_name,
            thread_id=thread_id,
        )
        try:
            return await call_next(request)
        finally:
            clear_azure_session(tokens)


# ── Configuration ─────────────────────────────────────────────────
PROJECT_DIR = Path(__file__).parent


def create_agent():
    """Create and configure the ChatAgent for AG-UI."""
    from chat.agent import create_agent as _create_agent
    return _create_agent()


_checklist_loader_singleton: Any = None
_checklist_loader_lock = threading.Lock()


def get_checklist_loader():
    """프로세스당 하나의 ChecklistLoader. /api/checklists 반복 호출 시 DB 재조회 방지."""
    global _checklist_loader_singleton
    if _checklist_loader_singleton is not None:
        return _checklist_loader_singleton
    with _checklist_loader_lock:
        if _checklist_loader_singleton is not None:
            return _checklist_loader_singleton
        from agent.checklist_loader import get_configured_checklist_loader

        _checklist_loader_singleton = get_configured_checklist_loader(PROJECT_DIR)
        return _checklist_loader_singleton


def invalidate_checklist_loader_cache() -> None:
    """체크리스트 변경 후 다음 API에서 다시 로드."""
    global _checklist_loader_singleton
    with _checklist_loader_lock:
        _checklist_loader_singleton = None


def _parse_subscription_filter_from_request(request: Request) -> Optional[str]:
    """X-Azure-* 헤더가 유효하면 구독 ID, 없거나 불일치면 None (필터 없음)."""
    sub = (request.headers.get(HDR_AZURE_SUBSCRIPTION) or "").strip()
    tenant = (request.headers.get(HDR_AZURE_TENANT) or "").strip()
    if not sub:
        return None
    from agent.azure_resource_reader import AzureResourceReader

    rt = AzureResourceReader.resolve_subscription_tenant(sub)
    if not rt:
        return None
    if tenant and normalize_subscription_id(tenant) != normalize_subscription_id(rt):
        return None
    return sub


def _iter_local_terraform_runs(tf_dir: Path) -> list[tuple[str, str, list[str]]]:
    import re

    legacy_ts = re.compile(r"^\d{8}_\d{6}$")
    out: list[tuple[str, str, list[str]]] = []
    if not tf_dir.exists():
        return out
    for child in sorted(tf_dir.iterdir(), reverse=True):
        if not child.is_dir():
            continue
        if legacy_ts.match(child.name):
            files = [f.name for f in child.iterdir() if f.is_file()]
            if files:
                out.append((LEGACY_STORAGE_SUBSCRIPTION_KEY, child.name, sorted(files)))
            continue
        sub_key = normalize_subscription_id(child.name)
        for ts_dir in sorted(child.iterdir(), reverse=True):
            if not ts_dir.is_dir():
                continue
            files = [f.name for f in ts_dir.iterdir() if f.is_file()]
            if files:
                out.append((sub_key, ts_dir.name, sorted(files)))
    return out


def _norm_sub_filter(request: Request) -> Optional[str]:
    sub = _parse_subscription_filter_from_request(request)
    return normalize_subscription_id(sub) if sub else None


def _norm_sub_filter_db_only(request: Request) -> Optional[str]:
    """DB 조회 필터용 구독 ID. ARM 검증 없이 헤더 값을 정규화만 한다."""
    sub = (request.headers.get(HDR_AZURE_SUBSCRIPTION) or "").strip()
    return normalize_subscription_id(sub) if sub else None


class AssessmentRunRequest(BaseModel):
    """진단 평가 실행: 리소스 그룹명·리소스 id 목록. 둘 다 비면 구독 전체."""
    resource_group_names: list[str] = []
    resource_ids: list[str] = []
    checklist_ids: list[str] = []


class TerraformGenerateRequest(BaseModel):
    """테라폼 코드 생성 요청: 리소스 이름 목록, 그룹, 타입 필터, 특정 평가 결과."""
    resource_names: list[str] = []
    resource_group: str = ""
    resource_type: str = ""
    assessment_filename: str = ""
    assessment_report_id: int = 0
    assessment_target: str = ""


def _list_azure_resources_session_sync(tenant_id: str | None, subscription_id: str) -> list[dict]:
    from chat.tools.azure_session import set_azure_session, clear_azure_session
    from chat.tools.assessment import _reader_for_session

    tok = set_azure_session(tenant_id=tenant_id, subscription_id=subscription_id)
    try:
        reader = _reader_for_session("")
        return [r.to_dict() for r in reader.get_all_resources()]
    finally:
        clear_azure_session(tok)


def _run_assessment_selection_sync(
    tenant_id: str | None,
    subscription_id: str,
    subscription_name: str | None,
    body: AssessmentRunRequest,
) -> dict:
    from chat.tools.azure_session import set_azure_session, clear_azure_session
    from chat.tools.assessment import _reader_for_session, run_assessment_for_resources

    tok = set_azure_session(
        tenant_id=tenant_id,
        subscription_id=subscription_id,
        subscription_name=subscription_name,
    )
    try:
        reader = _reader_for_session("")
        rgs = [str(x).strip() for x in body.resource_group_names if str(x).strip()]
        rids = [str(x).strip() for x in body.resource_ids if str(x).strip()]
        if not rgs and not rids:
            resources = reader.get_all_resources()
        else:
            by_id: dict[str, Any] = {}
            for rg in rgs:
                for r in reader.get_resources_by_resource_group(rg):
                    by_id[r.id] = r
            for rid in rids:
                r = reader.get_resource_details(rid)
                if r:
                    by_id[r.id] = r
            resources = list(by_id.values())
        if not resources:
            return {
                "success": False,
                "detail": "평가할 리소스가 없습니다. 구독 범위·선택 항목을 확인하세요.",
            }
        checklist_keys = [str(x).strip() for x in body.checklist_ids if str(x).strip()]
        if not checklist_keys:
            return {
                "success": False,
                "detail": "평가에 사용할 체크리스트를 하나 이상 선택해 주세요.",
            }
        summary = run_assessment_for_resources(
            resources,
            reader,
            subscription_id_tool_arg=subscription_id,
            output_format="all",
            checklist_ids=checklist_keys,
        )
        if summary.startswith("Error:"):
            return {"success": False, "detail": summary}
        return {"success": True, "summary": summary}
    except Exception as e:
        logger.exception("assessment run (selection)")
        return {"success": False, "detail": str(e)}
    finally:
        clear_azure_session(tok)


def _list_assessments_sync(sub_norm: Optional[str]) -> list[dict]:
    """동기 DB 리포트 목록 나열 — run_in_threadpool에서 호출.

    각 리포트마다 .json / .md / .html 세 항목을 반환한다.
    대시보드는 .json 파일을 파싱해 차트 데이터를 구성하고,
    평가결과 탭은 stem 기준으로 그룹화해 세 파일을 모두 표시한다.
    """
    try:
        from agent.db.assessment import is_db_configured, list_reports
        if is_db_configured():
            r_rows = list_reports(subscription_id=sub_norm, limit=50)
            items = []
            for rr in r_rows:
                rid = rr["report_id"]
                dt = rr["generated_at"]
                dt_clean = dt.replace(":", "").replace("-", "").replace("T", "_")[:15]
                base_name = f"Report_{rid}_{dt_clean}"
                common = {
                    "report_id": rid,
                    "date": dt,
                    "size": 0,
                    "source": "db",
                    "total_resources": rr.get("total_resources", 0),
                    "summary_average_score": rr.get("summary_average_score", 0),
                }
                for ext in ("json", "md", "html"):
                    items.append({**common, "filename": f"db/{base_name}.{ext}"})
            return items
    except Exception as _e:
        logger.warning(f"DB assessment list failed: {_e}")

    return []


def _list_terraform_sync(sub_norm: Optional[str]) -> list[dict]:
    """동기 Terraform 실행 목록 — run_in_threadpool에서 호출."""
    try:
        from agent.db.terraform import is_db_configured, list_runs
        if is_db_configured():
            return list_runs(subscription_id=sub_norm, limit=30)
    except Exception as _e:
        logger.warning(f"DB terraform list failed: {_e}")
    return []


def _checklist_summary_sync() -> dict:
    from agent.db.checklist import get_summary as _db_summary, is_db_configured
    if is_db_configured():
        try:
            return _db_summary()
        except Exception as e:
            logger.warning("DB 체크리스트 목록 조회 실패, 파일 기반으로 전환: %s", e)
    return get_checklist_loader().get_summary()


def _user_info_from_request(request: Request) -> dict[str, str]:
    """현재 요청의 SSO 쿠키/Bearer 토큰에서 login_id, user_name, sso_no 추출."""
    try:
        from agent.entra_sso import (
            COOKIE_ACCESS_TOKEN,
            _jwt_payload_dict,
            is_sso_configured,
            parse_authorization_bearer,
        )
        if not is_sso_configured():
            return {"login_id": "", "user_name": "", "sso_no": ""}
        token = (request.cookies.get(COOKIE_ACCESS_TOKEN) or "").strip()
        if not token:
            token = parse_authorization_bearer(request.headers.get("Authorization")) or ""
        if not token:
            return {"login_id": "", "user_name": "", "sso_no": ""}
        claims = _jwt_payload_dict(token)
        return {
            "login_id": str(
                claims.get("preferred_username")
                or claims.get("unique_name")
                or claims.get("upn")
                or claims.get("email")
                or ""
            ),
            "user_name": str(claims.get("name") or ""),
            "sso_no": str(claims.get("oid") or ""),
        }
    except Exception:
        return {"login_id": "", "user_name": "", "sso_no": ""}


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    # DB 테이블 자동 생성 (없을 경우)
    try:
        from agent.db_init import ensure_tables_exist
        ensure_tables_exist()
    except Exception as e:
        logger.error(f"Failed to ensure database tables: {e}")

    from agent_framework.ag_ui import add_agent_framework_fastapi_endpoint

    app = FastAPI(
        title="AIOps Resource Assessment",
        description="AG-UI server for AIOps Assessment Chatbot with CopilotKit frontend",
        version="1.0.0",
    )

    # nginx가 FE 정적 파일과 /api/* 프록시를 동일 오리진으로 제공하므로 CORS 미들웨어는 불필요.
    app.add_middleware(AzureSessionContextMiddleware)
    app.add_middleware(DelegatedUserTokenMiddleware)

    # ── Entra ID SSO (docs/plan/sso.md) ──────────────────────────
    def _request_scheme(request: Request) -> str:
        """nginx/App Gateway 뒤에서 원 스킴 판별 — X-Forwarded-Proto 우선."""
        fwd = (request.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip()
        return fwd or (request.url.scheme or "http")

    @app.get("/api/auth/login")
    async def auth_login():
        """Entra 로그인 페이지로 302 redirect (state는 서명되어 URL에 포함)."""
        from agent.entra_sso import build_login_authorization_url, is_sso_configured

        if not is_sso_configured():
            raise HTTPException(
                status_code=503,
                detail="Entra SSO가 설정되지 않았습니다. AZURE_AUTH_CLIENT_ID, TENANT_ID, REDIRECT_URI, STATE_SECRET 및 클라이언트 인증(AZURE_AUTH_CLIENT_SECRET 또는 UAMI 페더레이션)을 확인하세요.",
            )
        try:
            url, _ = build_login_authorization_url()
        except Exception as e:
            logger.exception("GET /api/auth/login")
            raise HTTPException(status_code=500, detail=str(e)) from e
        return RedirectResponse(url, status_code=302)

    @app.get("/api/getAToken")
    async def auth_callback(
        request: Request,
        code: str = "",
        state: str = "",
        error: str = "",
        error_description: str = "",
    ):
        """Entra OAuth redirect_uri 콜백 — BE가 직접 수신, HttpOnly 쿠키 설정 후 FE(/)로 redirect."""
        from agent.entra_sso import (
            COOKIE_ACCESS_TOKEN,
            all_auth_cookie_names,
            cookie_secure_for_request,
            exchange_code_for_result,
            is_sso_configured,
            verify_signed_oauth_state,
        )

        if not is_sso_configured():
            return RedirectResponse("/login?error=sso_not_configured", status_code=302)
        if error:
            logger.warning("Entra auth error: %s — %s", error, error_description)
            return RedirectResponse(f"/login?error={quote(error)}", status_code=302)
        if not code or not verify_signed_oauth_state(state):
            logger.warning("Entra callback: code 없음 또는 state 검증 실패")
            return RedirectResponse("/login?error=invalid_state", status_code=302)

        try:
            result = await run_in_threadpool(exchange_code_for_result, code.strip())
        except Exception:
            logger.exception("GET /api/getAToken exchange")
            return RedirectResponse("/login?error=auth_failed", status_code=302)

        if result.get("error") or not result.get("access_token"):
            logger.warning(
                "MSAL token exchange error: %s",
                result.get("error_description") or result.get("error") or "no access_token",
            )
            return RedirectResponse("/login?error=auth_failed", status_code=302)

        access = result["access_token"]
        max_age = int(result.get("expires_in") or 3600)
        secure = cookie_secure_for_request(_request_scheme(request))
        opts = {"path": "/", "secure": secure, "httponly": True, "samesite": "lax"}

        resp = RedirectResponse("/", status_code=302)
        resp.set_cookie(key=COOKIE_ACCESS_TOKEN, value=access, max_age=max_age, **opts)
        for name in all_auth_cookie_names():
            if name == COOKIE_ACCESS_TOKEN:
                continue
            resp.delete_cookie(key=name, **opts)
        logger.info("Entra SSO: 쿠키 설정 완료, / 로 redirect")
        return resp

    @app.post("/api/auth/logout")
    async def auth_logout(request: Request):
        """HttpOnly 인증 쿠키 전부 제거. 클라이언트 sessionStorage 정리는 FE에서."""
        from agent.entra_sso import all_auth_cookie_names, cookie_secure_for_request

        resp = JSONResponse(content={"ok": True})
        secure = cookie_secure_for_request(_request_scheme(request))
        opts = {"path": "/", "secure": secure, "httponly": True, "samesite": "lax"}
        for key in all_auth_cookie_names():
            resp.delete_cookie(key=key, **opts)
        return resp

    @app.get("/api/auth/session")
    async def auth_session(request: Request):
        """SSO가 설정된 경우 HttpOnly 액세스 토큰 쿠키 유무·만료로 인증 여부 판단. 미설정 시 메인 앱 진입 허용."""
        import time

        from agent.entra_sso import (
            COOKIE_ACCESS_TOKEN,
            is_sso_configured,
            jwt_expires_on,
            parse_authorization_bearer,
        )

        if not is_sso_configured():
            return {"authenticated": True, "sso_enforced": False}

        token = (request.cookies.get(COOKIE_ACCESS_TOKEN) or "").strip()
        if not token:
            token = parse_authorization_bearer(request.headers.get("Authorization")) or ""
        if not token:
            return {"authenticated": False, "sso_enforced": True}

        now = int(time.time())
        if jwt_expires_on(token) > now:
            return {"authenticated": True, "sso_enforced": True}

        return {"authenticated": False, "sso_enforced": True}

    # ── Health Check ──────────────────────────────────────────────
    @app.get("/api/health")
    async def health_check():
        return {"status": "healthy"}

    @app.get("/api/system/mi-info")
    async def api_system_mi_info():
        """백엔드 리소스 조회용 MI 정보.

        UAMI 환경변수가 설정된 경우 AZURE_RESOURCE_READER_UAMI_* 값을 반환한다.
        미설정 시 Web App의 System-Assigned MI 정보를 반환한다.
        """
        from agent.azure_resource_reader import AzureResourceReader

        try:
            return await run_in_threadpool(AzureResourceReader.get_self_mi_info)
        except Exception as e:
            logger.exception("GET /api/system/mi-info")
            raise HTTPException(status_code=500, detail=str(e)) from e

    @app.get("/api/azure/subscriptions")
    async def api_azure_subscriptions():
        """OBO(사용자 토큰) ∩ MI(Reader) 교집합만 반환.

        HYBRID_AUTH_PLAN.md Step 2: 사용자가 Azure에서 접근 가능한 구독 중에서도
        백엔드 MI에 Reader 권한이 부여된 구독만 표시한다.
        """
        from agent.azure_resource_reader import AzureResourceReader

        try:
            current = AzureResourceReader.get_session_bootstrap()
        except RuntimeError as e:
            logger.warning(
                "GET /api/azure/subscriptions → 503: 기본 구독 결정 실패 (구독 목록 비어 있음·ARM 오류·인증 메시지 등). detail=%s",
                e,
            )
            raise HTTPException(status_code=503, detail=str(e)) from e
        tenant_id = current.get("tenant_id") or ""
        if not tenant_id:
            logger.warning(
                "GET /api/azure/subscriptions → 503: tenant_id 없음. current=%s",
                {k: current.get(k) for k in ("subscription_id", "name", "state", "tenant_id")},
            )
            raise HTTPException(status_code=503, detail="테넌트 ID를 확인할 수 없습니다.")
        try:
            subs = AzureResourceReader.list_subscriptions_for_tenant(tenant_id)
        except RuntimeError as e:
            logger.warning(
                "GET /api/azure/subscriptions → 503: 테넌트별 구독 목록 실패. tenant_id=%s detail=%s",
                tenant_id,
                e,
            )
            raise HTTPException(status_code=503, detail=str(e)) from e

        try:
            mi_ids = AzureResourceReader.list_mi_accessible_subscription_ids()
        except RuntimeError as e:
            logger.warning(
                "GET /api/azure/subscriptions → 503: MI 구독 목록 실패. detail=%s", e
            )
            raise HTTPException(status_code=503, detail=str(e)) from e

        subs = [
            s for s in subs
            if normalize_subscription_id(str(s.get("subscription_id") or "")) in mi_ids
        ]

        default_sub = current.get("subscription_id") or ""
        # session-bootstrap이 이미 OBO∩MI 교집합 기준이나, 폴백·경계 대비로 한 번 더 교정
        if default_sub and normalize_subscription_id(default_sub) not in mi_ids:
            default_sub = subs[0]["subscription_id"] if subs else ""

        return {
            "tenant_id": tenant_id,
            "default_subscription_id": default_sub,
            "subscriptions": subs,
        }

    @app.get("/api/azure/session-bootstrap")
    async def api_azure_session_bootstrap(request: Request):
        """앱 로드 시 기본 구독·테넌트·표시 이름·사용자 힌트(Entra 시 HttpOnly ARM 토큰 기준)."""
        from agent.azure_resource_reader import AzureResourceReader

        try:
            info = await run_in_threadpool(AzureResourceReader.get_session_bootstrap)
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e
        # SSO 쿠키(ARM access token) 클레임에서 프로필(name/email/oid) 추출 — 콜백이 BE로 옮겨지며
        # FE가 직접 프로필 응답을 받지 못하므로 여기서 내려준다.
        ui = _user_info_from_request(request)
        return {
            "subscription_id": info.get("subscription_id", ""),
            "tenant_id": info.get("tenant_id", ""),
            "name": info.get("name", ""),
            "state": info.get("state", ""),
            "user": info.get("user", "") or ui["login_id"] or ui["user_name"],
            "profile": {
                "name": ui["user_name"],
                "email": ui["login_id"],
                "oid": ui["sso_no"],
            },
        }

    @app.get("/api/azure/resources")
    async def api_azure_resources(request: Request):
        """현재 세션 구독(X-Azure-*) 기준 지원 리소스 타입 목록(Resource Graph)."""
        sub = (request.headers.get(HDR_AZURE_SUBSCRIPTION) or "").strip()
        tenant = (request.headers.get(HDR_AZURE_TENANT) or "").strip()
        if not sub:
            raise HTTPException(
                status_code=400,
                detail="X-Azure-Subscription-Id 헤더가 필요합니다.",
            )
        try:
            rt, rs = _validate_azure_session_headers(sub, tenant or None)
        except HTTPException:
            raise
        try:
            return await run_in_threadpool(_list_azure_resources_session_sync, rt, rs)
        except Exception as e:
            logger.exception("GET /api/azure/resources")
            raise HTTPException(status_code=500, detail=str(e)) from e

    @app.post("/api/assessments/run")
    async def api_assessments_run(request: Request, body: AssessmentRunRequest):
        """리소스 그룹·리소스 ID 선택(또는 비우면 구독 전체)으로 평가 파이프라인 실행."""
        sub = (request.headers.get(HDR_AZURE_SUBSCRIPTION) or "").strip()
        sub_name = _decode_header_value(request.headers.get(HDR_AZURE_SUBSCRIPTION_NAME))
        tenant = (request.headers.get(HDR_AZURE_TENANT) or "").strip()
        if not sub:
            raise HTTPException(
                status_code=400,
                detail="X-Azure-Subscription-Id 헤더가 필요합니다.",
            )
        try:
            rt, rs = _validate_azure_session_headers(sub, tenant or None)
        except HTTPException:
            raise
        return await run_in_threadpool(_run_assessment_selection_sync, rt, rs, sub_name, body)

    @app.post("/api/terraform/generate")
    async def api_terraform_generate(request: Request, body: TerraformGenerateRequest):
        """선택한 리소스 또는 필터 조건으로 테라폼 코드 생성 실행."""
        sub = (request.headers.get(HDR_AZURE_SUBSCRIPTION) or "").strip()
        tenant = (request.headers.get(HDR_AZURE_TENANT) or "").strip()
        if not sub:
            raise HTTPException(status_code=400, detail="X-Azure-Subscription-Id 헤더가 필요합니다.")
        
        try:
            rt, rs = _validate_azure_session_headers(sub, tenant or None)
        except HTTPException:
            raise

        from chat.tools.azure_session import set_azure_session, clear_azure_session
        from chat.tools.terraform import generate_terraform_code

        # 툴 내부에서 사용하는 세션 컨텍스트 설정
        tok = set_azure_session(tenant_id=rt, subscription_id=rs)
        try:
            # 기존 툴 함수 재사용 (문자열 요약 + 다운로드 링크 반환)
            # MODIFIED: async 함수이므로 직접 await 호출 (run_in_threadpool 사용 금지)
            summary = await generate_terraform_code(
                resource_type=body.resource_type,
                resource_group=body.resource_group,
                resource_names=body.resource_names,
                assessment_filename=body.assessment_filename,
                assessment_report_id=body.assessment_report_id,
                assessment_target=body.assessment_target,
            )
            import re as _re
            _m = _re.search(r'run_id=(\d+)', summary or "")
            run_id = int(_m.group(1)) if _m else None
            return {"success": True, "summary": summary, "run_id": run_id}
        except Exception as e:
            logger.exception("terraform generate api error")
            return {"success": False, "detail": str(e)}
        finally:
            clear_azure_session(tok)

    # ── AG-UI endpoint (for CopilotKit) ──────────────────────────
    agent = create_agent()
    
    # 챗봇 요청 처리가 메인 루프를 블로킹하지 않도록 비동기 핸들러 최적화
    # 툴 실행 등 무거운 작업은 내부적으로 run_in_threadpool을 통해 처리됨
    add_agent_framework_fastapi_endpoint(app, agent, "/api/chat")

    # ── REST API: Dashboard Stats ────────────────────────────────
    @app.get("/api/dashboard/stats")
    async def get_dashboard_stats_endpoint(
        request: Request,
        period_days: int = Query(15, ge=1, le=365),
        resource_group: str = Query(""),
        resource_type: str = Query(""),
        start_date: str = Query(""),
        end_date: str = Query(""),
    ):
        """대시보드용 통계 (DB 직접 집계). trend / score_distribution / worst_resources 등 반환."""
        sub_norm = _norm_sub_filter_db_only(request)

        def _query():
            from agent.db.assessment import is_db_configured, get_dashboard_stats
            if not is_db_configured():
                return {
                    "kpi": {"total_reports": 0, "avg_score": 0, "total_resources": 0, "total_checks": 0},
                    "trend": [],
                    "score_distribution": [
                        {"range": "0-20", "count": 0}, {"range": "21-40", "count": 0},
                        {"range": "41-60", "count": 0}, {"range": "61-80", "count": 0},
                        {"range": "81-100", "count": 0},
                    ],
                    "worst_resources": [],
                    "auto_manual": {"total_checks": 0, "automated": 0, "manual": 0},
                    "pass_fail": {
                        "total_checks": 0,
                        "passed": 0,
                        "failed": 0,
                        "warnings": 0,
                        "type_mismatch_count": 0,
                    },
                    "filters": {"resource_groups": [], "resource_types": []},
                    "avg_score_resources": [],
                    "subscriptions": [],
                }
            return get_dashboard_stats(
                subscription_id=sub_norm or None,
                period_days=period_days,
                resource_group=resource_group or None,
                resource_type=resource_type or None,
                start_date=start_date or None,
                end_date=end_date or None,
            )

        try:
            return await run_in_threadpool(_query)
        except Exception as e:
            logger.exception("GET /api/dashboard/stats")
            raise HTTPException(status_code=500, detail=str(e)) from e

    @app.get("/api/dashboard/kpi")
    async def get_global_kpi_endpoint(request: Request):
        sub_norm = _norm_sub_filter_db_only(request)

        def _query():
            from agent.db.assessment import is_db_configured, get_global_kpi
            if not is_db_configured():
                return {
                    "total_reports": 0,
                    "avg_score": 0,
                    "pass_fail": {"total_checks": 0, "passed": 0, "failed": 0, "warnings": 0},
                    "resources": [],
                }
            return get_global_kpi(subscription_id=sub_norm or None)

        try:
            return await run_in_threadpool(_query)
        except Exception as e:
            logger.exception("GET /api/dashboard/kpi")
            raise HTTPException(status_code=500, detail=str(e)) from e

    # ── REST API: Trend Detail (특정 일자 리소스 목록) ──────────────
    @app.get("/api/dashboard/trend-detail")
    async def get_trend_detail_endpoint(
        request: Request,
        date: str = Query(...),
        subscription_id: str = Query(""),
        resource_group: str = Query(""),
        resource_type: str = Query(""),
    ):
        sub_norm = _norm_sub_filter_db_only(request)
        sub_filter = subscription_id.strip() or sub_norm

        def _query():
            from agent.db.assessment import is_db_configured, get_trend_date_resources
            if not is_db_configured():
                return []
            return get_trend_date_resources(
                date=date,
                subscription_id=sub_filter or None,
                resource_group=resource_group or None,
                resource_type=resource_type or None,
            )

        try:
            return await run_in_threadpool(_query)
        except Exception as e:
            logger.exception("GET /api/dashboard/trend-detail")
            raise HTTPException(status_code=500, detail=str(e)) from e

    # ── REST API: Score Range Resources (팝업 lazy load) ────────────
    @app.get("/api/dashboard/score-range-resources")
    async def get_score_range_resources_endpoint(
        request: Request,
        score_min: float = Query(0, ge=0, le=100),
        score_max: float = Query(100, ge=0, le=100),
        period_days: int = Query(15, ge=1, le=365),
        resource_group: str = Query(""),
        resource_type: str = Query(""),
        start_date: str = Query(""),
        end_date: str = Query(""),
    ):
        """점수 구간 리소스 목록 (점수 분포 바 클릭 시 팝업 lazy load용)."""
        sub_norm = _norm_sub_filter_db_only(request)

        def _query():
            from agent.db.assessment import is_db_configured, get_score_range_resources
            if not is_db_configured():
                return []
            return get_score_range_resources(
                score_min=score_min,
                score_max=score_max,
                subscription_id=sub_norm or None,
                period_days=period_days,
                resource_group=resource_group or None,
                resource_type=resource_type or None,
                start_date=start_date or None,
                end_date=end_date or None,
            )

        try:
            return await run_in_threadpool(_query)
        except Exception as e:
            logger.exception("GET /api/dashboard/score-range-resources")
            raise HTTPException(status_code=500, detail=str(e)) from e

    @app.get("/api/assessments/subscription-charts-summary")
    async def get_subscription_charts_summary_endpoint(
        period_days: int = Query(15, ge=1, le=365),
        trend_subscription: str = Query(""),
        start_date: str = Query(""),
        end_date: str = Query(""),
    ):
        """구독별 평균 점수 바 + 일별 추이. 전체 구독 집계(구독 헤더 필터 없음)."""
        def _query():
            from agent.db.assessment import get_subscription_charts_summary
            return get_subscription_charts_summary(
                period_days=period_days,
                trend_subscription=trend_subscription or None,
                start_date=start_date or None,
                end_date=end_date or None,
            )

        try:
            return await run_in_threadpool(_query)
        except Exception as e:
            logger.exception("GET /api/assessments/subscription-charts-summary")
            raise HTTPException(status_code=500, detail=str(e)) from e

    @app.get("/api/assessments/charts-summary")
    async def get_assessment_charts_summary_endpoint(
        request: Request,
        period_days: int = Query(15, ge=1, le=365),
        trend_resource_group: str = Query(""),
        trend_resource_type: str = Query(""),
        start_date: str = Query(""),
        end_date: str = Query(""),
        rg_subscription_id: str = Query(""),
    ):
        """진단 요약 차트: RG/유형 막대 + 일별 추이(DB). 헤더 구독 기준."""
        sub_norm = _norm_sub_filter_db_only(request)

        def _query():
            from agent.db.assessment import is_db_configured, get_assessment_charts_summary
            return get_assessment_charts_summary(
                subscription_id=sub_norm or None,
                period_days=period_days,
                trend_resource_group=trend_resource_group or None,
                trend_resource_type=trend_resource_type or None,
                start_date=start_date or None,
                end_date=end_date or None,
                rg_subscription_id=rg_subscription_id or None,
            )

        try:
            return await run_in_threadpool(_query)
        except Exception as e:
            logger.exception("GET /api/assessments/charts-summary")
            raise HTTPException(status_code=500, detail=str(e)) from e

    # ── REST API: Resource Check Results (팝업용) ────────────────
    @app.get("/api/assessments/resource-check-results")
    async def get_resource_check_results_endpoint(
        report_id: int = Query(...),
        resource_name: str = Query(...),
    ):
        """특정 리소스의 체크 결과 상세 조회 (취약 리소스 팝업용)."""
        def _query():
            from agent.db.assessment import is_db_configured, get_resource_check_results
            if not is_db_configured():
                return {}
            return get_resource_check_results(report_id=report_id, resource_name=resource_name)

        try:
            return await run_in_threadpool(_query)
        except Exception as e:
            logger.exception("GET /api/assessments/resource-check-results")
            raise HTTPException(status_code=500, detail=str(e)) from e

    # ── REST API: Assessments ────────────────────────────────────
    @app.get("/api/assessments")
    async def list_assessments(request: Request):
        """List assessment report files from Azure Storage or local directory."""
        sub_norm = _norm_sub_filter(request)
        return await run_in_threadpool(_list_assessments_sync, sub_norm)


    def _assessment_download_media_type(norm_path: str) -> str:
        base = norm_path.split("/")[-1].lower()
        if base.endswith(".md"):
            return "text/markdown; charset=utf-8"
        if base.endswith(".html") or base.endswith(".htm"):
            return "text/html; charset=utf-8"
        if base.endswith(".js") or base.endswith(".mjs") or base.endswith(".cjs"):
            return "text/javascript; charset=utf-8"
        return "application/octet-stream; charset=utf-8"

    def _content_disposition_attachment(basename: str) -> str:
        safe_ascii = basename.replace('"', "_").encode("ascii", "replace").decode("ascii")
        utf8_star = quote(basename, safe="")
        return f"attachment; filename=\"{safe_ascii}\"; filename*=UTF-8''{utf8_star}"

    @app.get("/api/assessments/{filename:path}")
    async def get_assessment(
        filename: str,
        request: Request,
        download: Annotated[bool, Query(description="If true, return raw file bytes for download")] = False,
    ):
        """Get assessment report content from Azure or local (JSON), or raw file when download=1."""
        import re as _re
        sub_norm = _norm_sub_filter(request)
        norm_path = filename.replace("\\", "/")
        if ".." in norm_path.split("/"):
            raise HTTPException(status_code=404, detail="Assessment not found")

        # ── DB 리포트 경로 처리 (db/Report_{rid}_{date}.(json|md|html) 또는 db/report_{id}) ───────────
        _db_match = _re.match(r"^db/[Rr]eport_(\d+)(?:_.*?)?(?:\.(json|md|html))?$", norm_path)
        if not _db_match:
            # db/(\d+) 형태도 지원 (구형 get_assessment_db 호환)
            _db_match = _re.match(r"^db/(\d+)$", norm_path)
        
        if _db_match:
            report_id = int(_db_match.group(1))
            try:
                ext = _db_match.group(2)
            except IndexError:
                ext = "json"  # 기본값
            
            def _fetch_db():
                import json as _json
                from agent.db.assessment import is_db_configured, get_report_detail
                if not is_db_configured():
                    raise HTTPException(status_code=503, detail="DB not configured")
                data = get_report_detail(report_id)
                if data is None:
                    raise HTTPException(status_code=404, detail="Report not found")
                
                if ext == "md":
                    content = data.get("report_md") or ""
                elif ext == "html":
                    content = data.get("report_html") or ""
                elif ext == "json" or ext is None:
                    # 확장자가 없거나 json이면 전체 데이터 또는 변환 데이터 반환
                    # 여기서는 기존 get_assessment_db처럼 원본 data를 반환할지, 
                    # 아니면 FE용 AssessmentReport 형식을 반환할지 결정해야 함.
                    # 기존 db/report_{id}.json 요청은 FE용 형식을 기대함.
                    # 경로에 'report_'가 포함되어 있거나 확장자가 명시되어 있으면 FE용 형식으로 변환.
                    if "report_" in norm_path.lower() or ext == "json":
                        report_json = {
                            "report_metadata": {
                                "generated_at": data.get("generated_at", ""),
                                "total_resources": data.get("total_resources", 0),
                            },
                            "summary": {
                                "average_score": float(data.get("summary_average_score", 0)),
                                "total_passed": data.get("summary_total_passed", 0),
                                "total_failed": data.get("summary_total_failed", 0),
                                "total_warnings": data.get("summary_total_warnings", 0),
                                "total_manual": data.get("summary_total_manual", 0),
                                "total_checks": data.get("summary_total_checks", 0),
                            },
                            "assessments": [
                                {
                                    "resource_name": a.get("resource_name", ""),
                                    "resource_type": a.get("resource_type", ""),
                                    "resource_group": a.get("resource_group", ""),
                                    "assessment_time": a.get("assessment_time", ""),
                                    "overall_score": float(a.get("overall_score", 0)),
                                    "summary": {
                                        "total_checks": a.get("summary_total_checks", 0),
                                        "passed": a.get("summary_passed", 0),
                                        "failed": a.get("summary_failed", 0),
                                        "warnings": a.get("summary_warnings", 0),
                                    },
                                }
                                for a in data.get("assessments", [])
                            ],
                            "resource_files": [
                                {
                                    "id": r.get("id"),
                                    "scope_id": r.get("scope_id", ""),
                                    "resource_id": r.get("resource_id", ""),
                                    "resource_name": r.get("resource_name", ""),
                                    "resource_type": r.get("resource_type", ""),
                                    "resource_group": r.get("resource_group", ""),
                                    "result_status": r.get("result_status", ""),
                                    "overall_score": float(r.get("overall_score") or 0),
                                    "trace_id": r.get("trace_id", ""),
                                    "created_at": r.get("created_at", ""),
                                }
                                for r in data.get("resource_files", [])
                            ],
                        }
                        content = _json.dumps(report_json, ensure_ascii=False)
                    else:
                        content = _json.dumps(data, ensure_ascii=False)
                
                if download:
                    basename = norm_path.split("/")[-1]
                    return Response(
                        content=content if isinstance(content, bytes) else content.encode("utf-8"),
                        media_type=_assessment_download_media_type(norm_path),
                        headers={"Content-Disposition": _content_disposition_attachment(basename)},
                    )
                return {"filename": norm_path, "content": content, "source": "db"}
            return await run_in_threadpool(_fetch_db)

        # ── DB 개별 파일 경로 처리 (db/Resource_..._{fid}_... 또는 db/file_{id}.(json|md|html)) ────
        _file_match = _re.match(r"^db/(?:[Ff]ile|Resource)_.*?(\d+)(?:_.*?)?\.(json|md|html)$", norm_path)
        if _file_match:
            file_id = int(_file_match.group(1))
            ext = _file_match.group(2)
            def _fetch_file():
                import json as _json
                from agent.db.assessment import is_db_configured, get_file_detail
                if not is_db_configured():
                    raise HTTPException(status_code=503, detail="DB not configured")
                data = get_file_detail(file_id)
                if data is None:
                    raise HTTPException(status_code=404, detail="Resource assessment not found")
                
                if ext == "md":
                    content = data.get("report_md") or ""
                elif ext == "html":
                    content = data.get("report_html") or ""
                else:
                    # 'details' (JSONB) 필드 원본 반환
                    details = data.get("details")
                    if isinstance(details, (dict, list)):
                        content = _json.dumps(details, ensure_ascii=False)
                    else:
                        content = str(details or "{}")
                
                if download:
                    basename = norm_path.split("/")[-1]
                    return Response(
                        content=content if isinstance(content, bytes) else content.encode("utf-8"),
                        media_type=_assessment_download_media_type(norm_path),
                        headers={"Content-Disposition": _content_disposition_attachment(basename)},
                    )
                return {"filename": norm_path, "content": content, "source": "db"}
            return await run_in_threadpool(_fetch_file)

        if sub_norm and not norm_path.startswith(f"{sub_norm}/"):
            raise HTTPException(status_code=404, detail="Assessment not found")

        filepath = PROJECT_DIR / "results" / norm_path
        if not filepath.exists():
            raise HTTPException(status_code=404, detail="Assessment not found")

        content = filepath.read_text(encoding="utf-8")

        if download:
            basename = norm_path.split("/")[-1] or "assessment"
            return Response(
                content=content.encode("utf-8"),
                media_type=_assessment_download_media_type(norm_path),
                headers={"Content-Disposition": _content_disposition_attachment(basename)},
            )

        return {"filename": norm_path, "content": content, "source": "local"}

    # ── REST API: Checklists ─────────────────────────────────────
    @app.get("/api/checklists")
    async def api_list_checklists():
        return await run_in_threadpool(_checklist_summary_sync)

    @app.get("/api/checklists/{name}")
    async def api_get_checklist(name: str):
        from agent.db.checklist import get_detail as _db_detail, is_db_configured
        file_key = name.replace(".yaml", "").replace(".yml", "")
        if is_db_configured():
            try:
                detail = _db_detail(file_key)
                if detail:
                    return detail
            except Exception as e:
                logger.warning("DB 체크리스트 상세 조회 실패, 파일 기반으로 전환: %s", e)

        loader = get_checklist_loader()
        for cl_name, checklist in loader.checklists.items():
            if name.lower() in cl_name.lower() or name.lower() in checklist.name.lower():
                return {
                    "name": checklist.name,
                    "version": checklist.version,
                    "description": checklist.description,
                    "applicable_resource_types": checklist.applicable_resource_types,
                    "categories": [c.to_dict() if hasattr(c, "to_dict") else vars(c) for c in checklist.categories],
                }
        raise HTTPException(status_code=404, detail="Checklist not found")

    @app.post("/api/checklists/upload")
    async def upload_checklist(request: Request, file: UploadFile = File(...)):
        if not file.filename.endswith(".yaml") and not file.filename.endswith(".yml"):
            raise HTTPException(status_code=400, detail="Only YAML files are allowed")

        content = await file.read()
        file_key = Path(file.filename).stem
        message = ""

        from agent.db.checklist import upsert_from_yaml_content
        try:
            user_info = _user_info_from_request(request)
            upsert_from_yaml_content(
                file_key, content,
                login_id=user_info["login_id"],
                user_name=user_info["user_name"],
                sso_no=user_info["sso_no"],
            )
            message = f"Saved {file.filename} to DB"
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"DB 저장 실패: {e}") from e

        invalidate_checklist_loader_cache()
        return {"message": message}

    class ChecklistUpdate(BaseModel):
        content: str

    @app.get("/api/checklists/{name}/raw")
    async def get_checklist_raw(name: str):
        file_key = name.replace(".yaml", "").replace(".yml", "")
        filename = f"{file_key}.yaml"

        from agent.db.checklist import get_raw_yaml as _db_raw
        try:
            raw = _db_raw(file_key)
            if raw:
                return {"filename": filename, "content": raw}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"DB 조회 실패: {e}") from e
        raise HTTPException(status_code=404, detail="Not found")

    @app.put("/api/checklists/{name}")
    async def update_checklist(request: Request, name: str, update: ChecklistUpdate):
        file_key = name.replace(".yaml", "").replace(".yml", "")
        filename = f"{file_key}.yaml"

        from agent.db.checklist import upsert_from_yaml_content
        try:
            user_info = _user_info_from_request(request)
            upsert_from_yaml_content(
                file_key, update.content,
                login_id=user_info["login_id"],
                user_name=user_info["user_name"],
                sso_no=user_info["sso_no"],
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"DB 업데이트 실패: {e}") from e

        invalidate_checklist_loader_cache()
        return {"message": "Update successful"}

    @app.delete("/api/checklists/{name}")
    async def delete_checklist_endpoint(name: str):
        file_key = name.replace(".yaml", "").replace(".yml", "")
        filename = f"{file_key}.yaml"

        from agent.db.checklist import delete_checklist as _db_delete
        try:
            _db_delete(file_key)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"DB 삭제 실패: {e}") from e

        invalidate_checklist_loader_cache()
        return {"message": "Delete successful"}

    # ── REST API: Terraform Outputs ──────────────────────────────
    @app.get("/api/terraform")
    async def list_terraform_outputs(request: Request):
        """List terraform runs from DB."""
        sub_norm = _norm_sub_filter(request)
        return await run_in_threadpool(_list_terraform_sync, sub_norm)

    @app.delete("/api/terraform/{subscription_id}/{timestamp}")
    async def delete_terraform_output(subscription_id: str, timestamp: str):
        """Delete one terraform run."""
        # ── DB 삭제 ───────────────────────────────────────────────────────
        def _db_delete():
            from agent.db.terraform import is_db_configured, delete_run
            if not is_db_configured():
                return 0
            scope = (
                None if subscription_id.strip().lower() == LEGACY_STORAGE_SUBSCRIPTION_KEY
                else normalize_subscription_id(subscription_id)
            )
            return delete_run(scope, timestamp)

        deleted = await run_in_threadpool(_db_delete)
        if deleted > 0:
            return {"message": f"Deleted {subscription_id}/{timestamp} from DB"}

        raise HTTPException(status_code=404, detail="Not found")

    @app.get("/api/terraform/{subscription_id}/{timestamp}/{filename}")
    async def get_terraform_file(subscription_id: str, timestamp: str, filename: str, request: Request):
        sub_norm = _norm_sub_filter(request)
        if sub_norm:
            cmp_sub = (
                LEGACY_STORAGE_SUBSCRIPTION_KEY
                if subscription_id.strip().lower() == LEGACY_STORAGE_SUBSCRIPTION_KEY
                else normalize_subscription_id(subscription_id)
            )
            if cmp_sub != sub_norm:
                raise HTTPException(status_code=404, detail="File not found")

        # ── DB 우선 조회 ──────────────────────────────────────────────────
        def _fetch_db_file():
            from agent.db.terraform import is_db_configured, get_run_file
            if not is_db_configured():
                return None
            scope = (
                None if subscription_id.strip().lower() == LEGACY_STORAGE_SUBSCRIPTION_KEY
                else normalize_subscription_id(subscription_id)
            )
            return get_run_file(scope, timestamp, filename)

        db_row = await run_in_threadpool(_fetch_db_file)
        if db_row:
            out_sub = (
                LEGACY_STORAGE_SUBSCRIPTION_KEY
                if subscription_id.strip().lower() == LEGACY_STORAGE_SUBSCRIPTION_KEY
                else normalize_subscription_id(subscription_id)
            )
            return {
                "filename": filename,
                "timestamp": timestamp,
                "subscription_id": out_sub,
                "content": db_row["content"],
                "source": "db",
            }

        raise HTTPException(status_code=404, detail="File not found")

    @app.get("/api/terraform/{subscription_id}/{timestamp}/{filename}/raw")
    async def get_terraform_file_raw(subscription_id: str, timestamp: str, filename: str, request: Request):
        """Get raw terraform file content as downloadable attachment."""
        sub_norm = _norm_sub_filter(request)
        if sub_norm:
            cmp_sub = (
                LEGACY_STORAGE_SUBSCRIPTION_KEY
                if subscription_id.strip().lower() == LEGACY_STORAGE_SUBSCRIPTION_KEY
                else normalize_subscription_id(subscription_id)
            )
            if cmp_sub != sub_norm:
                raise HTTPException(status_code=404, detail="File not found")

        # ── DB 우선 조회 ──────────────────────────────────────────────────
        def _fetch_db_raw():
            from agent.db.terraform import is_db_configured, get_run_file
            if not is_db_configured():
                return None
            scope = (
                None if subscription_id.strip().lower() == LEGACY_STORAGE_SUBSCRIPTION_KEY
                else normalize_subscription_id(subscription_id)
            )
            return get_run_file(scope, timestamp, filename)

        db_row = await run_in_threadpool(_fetch_db_raw)
        if db_row:
            media_type, _ = mimetypes.guess_type(filename)
            return Response(
                content=db_row["content"],
                media_type=media_type or "text/plain; charset=utf-8",
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"',
                },
            )

        raise HTTPException(status_code=404, detail="File not found")

    # ── Static/Config ───────────────────────────────────────────
    terraform_output_dir = PROJECT_DIR / "terraform_output"
    terraform_output_dir.mkdir(exist_ok=True)
    app.mount(
        "/api/downloads",
        StaticFiles(directory=str(terraform_output_dir)),
        name="terraform_downloads",
    )

    import chat.tools.terraform as _terraform_mod
    port = int(os.environ.get("PORT", "5100"))
    _terraform_mod.TERRAFORM_DOWNLOAD_BASE_URL = f"http://localhost:{port}/api/terraform"

    logger.info("FastAPI app created successfully")
    return app


# ── App instance ────────────────────────────────────────────────
app: FastAPI | None = None

def get_app() -> FastAPI:
    global app
    if app is None:
        app = create_app()
    return app

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "5100"))
    logger.info(f"Starting AG-UI server on http://0.0.0.0:{port}")
    uvicorn.run("agui_server:get_app", host="0.0.0.0", port=port, factory=True)
