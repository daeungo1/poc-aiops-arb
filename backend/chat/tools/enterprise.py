"""Enterprise assessment chat tools (deterministic, non-LLM)."""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from datetime import datetime
from typing import Annotated, Any, Callable

from agent_framework._tools import tool

from chat.tools.azure_session import get_session_subscription_id, get_session_tenant_id
from enterprise.runtime import AsyncDelegatedRequestCredential, get_enterprise_service
from enterprise.service import EnterpriseServiceError

logger = logging.getLogger(__name__)

_SIX_STATE_KEYS = (
    "pass",
    "fail",
    "unknown",
    "not_applicable",
    "exempted",
    "manual_pending",
)

ServiceProvider = Callable[[Any], Any]
_service_provider: ServiceProvider = get_enterprise_service


def set_enterprise_service_provider(provider: ServiceProvider) -> None:
    """Test hook: override enterprise service provider."""

    global _service_provider
    _service_provider = provider


def reset_enterprise_service_provider() -> None:
    """Restore default enterprise service provider."""

    global _service_provider
    _service_provider = get_enterprise_service


@contextmanager
def use_enterprise_service_provider(provider: ServiceProvider):
    """Context helper for tests overriding service provider."""

    original = _service_provider
    set_enterprise_service_provider(provider)
    try:
        yield
    finally:
        set_enterprise_service_provider(original)


def _enterprise_enabled() -> bool:
    return (os.environ.get("ENTERPRISE_ASSESSMENT_ENABLED") or "").strip().lower() in {
        "true",
        "1",
        "yes",
    }


def _scope_or_error() -> tuple[str, str] | None:
    tenant_id = (get_session_tenant_id() or "").strip()
    subscription_id = (get_session_subscription_id() or "").strip()
    if not tenant_id or not subscription_id:
        return None
    return tenant_id, subscription_id


def _service() -> Any:
    return _service_provider(AsyncDelegatedRequestCredential())


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()


def _normalize_counts(counts: dict[str, int] | Any) -> dict[str, int]:
    source = dict(counts or {})
    return {key: int(source.get(key, 0) or 0) for key in _SIX_STATE_KEYS}


def _serialize_provenance(items: Any) -> list[dict[str, Any]]:
    rows = []
    for item in items or ():
        rows.append(
            {
                "source_kind": item.source_kind,
                "source_reference": item.source_reference,
                "source_version": item.source_version,
                "observed_at": _iso(item.observed_at),
                "content_hash": item.content_hash,
            }
        )
    rows.sort(key=lambda item: item["content_hash"])
    return rows


def _serialize_run(run: Any) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "tenant_id": run.tenant_id,
        "subscription_id": run.subscription_id,
        "state": run.state,
        "requested_resource_ids": list(run.requested_resource_ids),
        "control_keys": list(run.control_keys),
        "started_at": _iso(run.started_at),
        "completed_at": _iso(run.completed_at),
        "reason_code": run.reason_code,
        "verdict_counts": _normalize_counts(run.verdict_counts),
        "evidence_provenance": _serialize_provenance(run.evidence_provenance),
        "findings": [
            {
                "finding_id": finding.finding_id,
                "resource_id": finding.resource_id,
                "control_key": finding.control_key,
                "verdict_state": finding.verdict_state,
                "reason_code": finding.reason_code,
                "evidence_hashes": list(finding.evidence_hashes),
            }
            for finding in run.findings
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


def _serialize_finding(finding: Any) -> dict[str, Any]:
    return {
        "finding_id": finding.finding_id,
        "run_id": finding.run_id,
        "subscription_id": finding.subscription_id,
        "resource_id": finding.resource_id,
        "control_key": finding.control_key,
        "verdict_state": finding.verdict_state,
        "reason_code": finding.reason_code,
        "evidence_hashes": list(finding.evidence_hashes),
        "provenance": _serialize_provenance(finding.provenance),
    }


def _error_disabled() -> dict[str, Any]:
    return {"ok": False, "code": "enterprise_disabled", "message": "Enterprise assessment tools are disabled"}


def _error_scope() -> dict[str, Any]:
    return {"ok": False, "code": "azure_scope_required", "message": "Azure session scope is required"}


def _handle_expected_errors(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, EnterpriseServiceError):
        return {
            "ok": False,
            "code": "enterprise_service_error",
            "message": str(exc),
            "status_code": exc.status_code,
        }
    if isinstance(exc, ValueError):
        return {
            "ok": False,
            "code": "enterprise_invalid_input",
            "message": str(exc),
        }
    logger.error("Unexpected enterprise tool error (%s)", type(exc).__name__)
    return {
        "ok": False,
        "code": "enterprise_internal_error",
        "message": "Unexpected enterprise tool error",
    }


def _abstain_for_state(state: str) -> bool:
    return state in {"unknown", "manual_pending"}


def _korean_summary(finding: dict[str, Any], abstain: bool) -> str:
    if abstain:
        return (
            "현재 판정은 deterministic_evaluator 기준으로 보류(abstain) 상태입니다. "
            "누락된 자동 증거 또는 수동 증거를 보강한 뒤 다시 평가해야 합니다."
        )
    return (
        "현재 판정은 deterministic_evaluator가 저장한 결과이며, 근거 해시와 원본 출처를 기준으로 재현 가능합니다."
    )


@tool
async def run_enterprise_assessment(
    resource_ids: Annotated[list[str] | None, "Optional ARM resource IDs to scope the run."] = None,
    control_keys: Annotated[list[str] | None, "Optional control keys for targeted deterministic evaluation."] = None,
) -> dict[str, Any]:
    """Run deterministic enterprise assessment in current Azure chat session scope."""

    if not _enterprise_enabled():
        return _error_disabled()

    scope = _scope_or_error()
    if scope is None:
        return _error_scope()
    tenant_id, subscription_id = scope

    try:
        service = _service()
        run_id = await service.run_assessment(
            tenant_id=tenant_id,
            subscription_id=subscription_id,
            resource_ids=resource_ids,
            control_keys=control_keys,
        )
        return {
            "ok": True,
            "run_id": run_id,
            "status": "running",
            "next_action": "get_enterprise_assessment",
        }
    except Exception as exc:
        return _handle_expected_errors(exc)


@tool
async def get_enterprise_assessment(
    run_id: Annotated[str, "Enterprise run identifier returned by run_enterprise_assessment."],
) -> dict[str, Any]:
    """Get deterministic enterprise assessment run for the current Azure session subscription."""

    if not _enterprise_enabled():
        return _error_disabled()

    scope = _scope_or_error()
    if scope is None:
        return _error_scope()
    _tenant_id, subscription_id = scope

    try:
        service = _service()
        run = await service.get_run(run_id, subscription_id)
        if run is None:
            return {"ok": False, "code": "not_found", "message": "assessment run not found"}
        return {"ok": True, "assessment": _serialize_run(run)}
    except Exception as exc:
        return _handle_expected_errors(exc)


@tool
async def get_enterprise_finding(
    finding_id: Annotated[str, "Enterprise finding identifier."],
) -> dict[str, Any]:
    """Get deterministic enterprise finding for the current Azure session subscription."""

    if not _enterprise_enabled():
        return _error_disabled()

    scope = _scope_or_error()
    if scope is None:
        return _error_scope()
    _tenant_id, subscription_id = scope

    try:
        service = _service()
        finding = await service.get_finding(finding_id, subscription_id)
        if finding is None:
            return {"ok": False, "code": "not_found", "message": "finding not found"}
        return {"ok": True, "finding": _serialize_finding(finding)}
    except Exception as exc:
        return _handle_expected_errors(exc)


@tool
async def explain_enterprise_evidence(
    finding_id: Annotated[str, "Enterprise finding identifier to explain deterministically from stored evidence."],
) -> dict[str, Any]:
    """Explain stored enterprise evidence deterministically without calling any model."""

    if not _enterprise_enabled():
        return _error_disabled()

    scope = _scope_or_error()
    if scope is None:
        return _error_scope()
    _tenant_id, subscription_id = scope

    try:
        service = _service()
        finding = await service.get_finding(finding_id, subscription_id)
        if finding is None:
            return {"ok": False, "code": "not_found", "message": "finding not found"}

        serialized = _serialize_finding(finding)
        abstain = _abstain_for_state(serialized["verdict_state"])

        return {
            "ok": True,
            "finding": serialized,
            "verdict_authority": "deterministic_evaluator",
            "verdict_override_allowed": False,
            "abstain": abstain,
            "evidence_citations": serialized["provenance"],
            "explanation_context": {
                "language": "ko",
                "summary_ko": _korean_summary(serialized, abstain),
                "reason_code": serialized["reason_code"],
                "resource_id": serialized["resource_id"],
                "control_key": serialized["control_key"],
            },
        }
    except Exception as exc:
        return _handle_expected_errors(exc)


__all__ = [
    "run_enterprise_assessment",
    "get_enterprise_assessment",
    "get_enterprise_finding",
    "explain_enterprise_evidence",
    "set_enterprise_service_provider",
    "reset_enterprise_service_provider",
    "use_enterprise_service_provider",
]
