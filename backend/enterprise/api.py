"""Enterprise assessment API v2 router."""

from __future__ import annotations

import base64
import json
import os
import time
from typing import Any, Callable, Protocol

from azure.core.credentials import AccessToken
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from enterprise.adapters.base import TokenCredential
from agent.entra_sso import COOKIE_ACCESS_TOKEN, UserOboCredential, parse_authorization_bearer
from enterprise.service import EnterpriseAssessmentService
from enterprise.service import EnterpriseServiceError


class EnterpriseAssessmentRequest(BaseModel):
    resource_ids: list[str] = Field(default_factory=list)
    control_keys: list[str] = Field(default_factory=list)


class EnterpriseAssessmentAccepted(BaseModel):
    run_id: str
    status: str = "running"


class AsyncStaticTokenCredential(TokenCredential):
    """Request-scoped async credential wrapper over UserOboCredential."""

    __enterprise_delegated_request__ = True

    def __init__(self, raw_token: str) -> None:
        self._raw_token = (raw_token or "").strip()
        self._obo = UserOboCredential(self._raw_token)

    @staticmethod
    def _normalize_scope(scope: str) -> str:
        return (scope or "").strip().lower()

    @staticmethod
    def _normalize_audience(audience: str) -> str:
        return (audience or "").strip().lower().rstrip("/")

    @staticmethod
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

    @classmethod
    def _token_audiences(cls, token: str) -> frozenset[str]:
        aud = cls._jwt_payload_dict(token).get("aud")
        if isinstance(aud, list):
            return frozenset(cls._normalize_audience(str(item)) for item in aud if str(item).strip())
        if aud:
            return frozenset({cls._normalize_audience(str(aud))})
        return frozenset()

    @staticmethod
    def _token_expires_on(token: str) -> int | None:
        payload = AsyncStaticTokenCredential._jwt_payload_dict(token)
        exp = payload.get("exp")
        if isinstance(exp, (int, float)):
            return int(exp)
        return None

    async def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        if not self._raw_token:
            raise ValueError("사용자 액세스 토큰이 없습니다.")

        normalized_scopes = {
            self._normalize_scope(scope)
            for scope in scopes
            if isinstance(scope, str) and scope.strip()
        }
        if not normalized_scopes:
            normalized_scopes = {"https://management.azure.com/.default"}

        allowed_scopes = {
            "https://management.azure.com/.default",
            "https://management.core.windows.net/.default",
        }
        if any(scope not in allowed_scopes for scope in normalized_scopes):
            raise PermissionError("non-ARM scope is not allowed")

        audiences = self._token_audiences(self._raw_token)
        allowed_audiences = {
            "https://management.azure.com",
            "https://management.core.windows.net",
        }
        if not audiences or any(audience not in allowed_audiences for audience in audiences):
            raise PermissionError("non-ARM audience is not allowed")

        expires_on = self._token_expires_on(self._raw_token)
        if expires_on is not None and expires_on <= int(time.time()):
            raise PermissionError("token has expired")

        requested_scopes = scopes or ("https://management.azure.com/.default",)
        return self._obo.get_token(*requested_scopes, **kwargs)


class EnterpriseServiceProvider(Protocol):
    def __call__(self, credential: TokenCredential) -> EnterpriseAssessmentService: ...


def enterprise_assessment_enabled() -> bool:
    return (os.environ.get("ENTERPRISE_ASSESSMENT_ENABLED") or "").strip().lower() in {
        "true",
        "1",
        "yes",
    }


def create_enterprise_router(
    service_provider: EnterpriseServiceProvider,
    scope_validator: Callable[[str | None, str | None], tuple[str | None, str | None]],
) -> APIRouter:
    router = APIRouter(prefix="/api/v2", tags=["enterprise"])

    async def _service_for_request(request: Request, *, require_auth: bool = False):
        raw_token = ((request.cookies.get(COOKIE_ACCESS_TOKEN) or "").strip() or parse_authorization_bearer(request.headers.get("Authorization")) or "").strip()
        if require_auth and not raw_token:
            raise HTTPException(status_code=401, detail="authentication required")
        credential = AsyncStaticTokenCredential(raw_token)
        if require_auth:
            try:
                await credential.get_token("https://management.azure.com/.default")
            except (PermissionError, ValueError) as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
        return service_provider(credential)

    def _require_scope(
        subscription_id: str | None,
        tenant_id: str | None,
    ) -> tuple[str, str]:
        if not subscription_id:
            raise HTTPException(status_code=400, detail="X-Azure-Subscription-Id header is required")
        resolved_tenant, resolved_subscription = scope_validator(subscription_id, tenant_id)
        return (resolved_tenant or "", resolved_subscription or "")

    @router.get("/controls")
    async def list_controls(request: Request):
        service = await _service_for_request(request)
        controls = await service.list_controls()
        def _value(item: Any, key: str) -> Any:
            if isinstance(item, dict):
                return item.get(key)
            return getattr(item, key)
        return [
            {
                "control_key": _value(item, "control_key"),
                "version": _value(item, "version"),
                "resource_type": _value(item, "resource_type"),
                "evaluator_kind": _value(item, "evaluator_kind"),
            }
            for item in controls
        ]

    @router.post("/assessments", status_code=202, response_model=EnterpriseAssessmentAccepted)
    async def create_assessment(
        body: EnterpriseAssessmentRequest,
        request: Request,
        x_subscription_id: str | None = Header(default=None, alias="X-Azure-Subscription-Id"),
        x_tenant_id: str | None = Header(default=None, alias="X-Azure-Tenant-Id"),
    ):
        tenant_id, subscription_id = _require_scope(x_subscription_id, x_tenant_id)
        service = await _service_for_request(request, require_auth=True)
        try:
            run_id = await service.run_assessment(
                tenant_id=tenant_id,
                subscription_id=subscription_id,
                resource_ids=body.resource_ids,
                control_keys=body.control_keys,
            )
            return EnterpriseAssessmentAccepted(run_id=run_id)
        except EnterpriseServiceError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    @router.get("/assessments/{run_id}")
    async def get_assessment_run(
        run_id: str,
        request: Request,
        x_subscription_id: str | None = Header(default=None, alias="X-Azure-Subscription-Id"),
        x_tenant_id: str | None = Header(default=None, alias="X-Azure-Tenant-Id"),
    ):
        _tenant_id, subscription_id = _require_scope(x_subscription_id, x_tenant_id)
        service = await _service_for_request(request)
        run = await service.get_run(run_id, subscription_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        return {
            "run_id": run.run_id,
            "tenant_id": run.tenant_id,
            "subscription_id": run.subscription_id,
            "state": run.state,
            "requested_resource_ids": list(run.requested_resource_ids),
            "control_keys": list(run.control_keys),
            "started_at": run.started_at,
            "completed_at": run.completed_at,
            "reason_code": run.reason_code,
            "verdict_counts": dict(run.verdict_counts),
            "evidence_provenance": [
                {
                    "source_kind": item.source_kind,
                    "source_reference": item.source_reference,
                    "source_version": item.source_version,
                    "observed_at": item.observed_at,
                    "content_hash": item.content_hash,
                }
                for item in run.evidence_provenance
            ],
            "findings": [
                {
                    "finding_id": item.finding_id,
                    "resource_id": item.resource_id,
                    "control_key": item.control_key,
                    "verdict_state": item.verdict_state,
                    "reason_code": item.reason_code,
                    "evidence_hashes": list(item.evidence_hashes),
                }
                for item in run.findings
            ],
            "collection_failures": [
                {
                    "reason_code": item.reason_code,
                    "source_kind": item.source_kind,
                    "source_reference": item.source_reference,
                    "status_code": item.status_code,
                    "retry_after": item.retry_after,
                    "detail": item.detail,
                }
                for item in run.collection_failures
            ],
        }

    @router.get("/findings/{finding_id}")
    async def get_finding(
        finding_id: str,
        request: Request,
        x_subscription_id: str | None = Header(default=None, alias="X-Azure-Subscription-Id"),
        x_tenant_id: str | None = Header(default=None, alias="X-Azure-Tenant-Id"),
    ):
        _tenant_id, subscription_id = _require_scope(x_subscription_id, x_tenant_id)
        service = await _service_for_request(request)
        finding = await service.get_finding(finding_id, subscription_id)
        if finding is None:
            raise HTTPException(status_code=404, detail="finding not found")
        return {
            "finding_id": finding.finding_id,
            "run_id": finding.run_id,
            "subscription_id": finding.subscription_id,
            "resource_id": finding.resource_id,
            "control_key": finding.control_key,
            "verdict_state": finding.verdict_state,
            "reason_code": finding.reason_code,
            "evidence_hashes": list(finding.evidence_hashes),
            "provenance": [
                {
                    "source_kind": item.source_kind,
                    "source_reference": item.source_reference,
                    "source_version": item.source_version,
                    "observed_at": item.observed_at,
                    "content_hash": item.content_hash,
                }
                for item in finding.provenance
            ],
        }

    return router
