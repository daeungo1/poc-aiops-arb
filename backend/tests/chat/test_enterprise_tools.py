from __future__ import annotations

import importlib
import inspect
import logging
import sys
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest

from enterprise.repository import EvidenceProvenance, FindingRecord, RunRecord
from enterprise.service import EnterpriseServiceError


class FakeEnterpriseService:
    def __init__(self) -> None:
        self.run_calls: list[tuple[str, str, tuple[str, ...], tuple[str, ...]]] = []
        self.get_run_calls: list[tuple[str, str]] = []
        self.get_finding_calls: list[tuple[str, str]] = []
        self.raise_on: str | None = None

    async def run_assessment(self, tenant_id, subscription_id, resource_ids=None, control_keys=None):
        if self.raise_on == "service_error":
            raise EnterpriseServiceError("downstream unavailable", status_code=503)
        if self.raise_on == "value_error":
            raise ValueError("bad input")
        if self.raise_on == "unexpected":
            raise RuntimeError("secret access_token=leak")
        self.run_calls.append(
            (
                tenant_id,
                subscription_id,
                tuple(resource_ids or ()),
                tuple(control_keys or ()),
            )
        )
        return "run-001"

    async def get_run(self, run_id, subscription_id):
        self.get_run_calls.append((run_id, subscription_id))
        if run_id == "missing" or subscription_id != "sub-a":
            return None
        return RunRecord(
            run_id=run_id,
            tenant_id="tenant-a",
            subscription_id="sub-a",
            state="completed",
            requested_resource_ids=(
                "/subscriptions/sub-a/resourceGroups/rg/providers/Microsoft.Storage/storageAccounts/sa",
            ),
            control_keys=("storage.secure_transfer",),
            started_at=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
            completed_at=datetime(2026, 8, 5, 12, 1, tzinfo=UTC),
            reason_code=None,
            verdict_counts={
                "pass": 1,
                "fail": 0,
                "unknown": 0,
                "not_applicable": 0,
                "exempted": 0,
                "manual_pending": 0,
            },
            evidence_provenance=(),
            findings=(),
            collection_failures=(),
        )

    async def get_finding(self, finding_id, subscription_id):
        self.get_finding_calls.append((finding_id, subscription_id))
        if finding_id == "missing" or subscription_id != "sub-a":
            return None
        state = "unknown" if finding_id == "f-unknown" else "pass"
        reason = "evidence_missing" if state == "unknown" else "assertion_matched"
        return FindingRecord(
            finding_id=finding_id,
            run_id="run-001",
            subscription_id="sub-a",
            resource_id="/subscriptions/sub-a/resourceGroups/rg/providers/Microsoft.Storage/storageAccounts/sa",
            control_key="storage.secure_transfer",
            verdict_state=state,
            reason_code=reason,
            evidence_hashes=("b" * 64, "a" * 64) if state != "unknown" else (),
            provenance=(
                EvidenceProvenance(
                    source_kind="arm",
                    source_reference="Microsoft.Storage/storageAccounts",
                    source_version="2024-01-01",
                    observed_at=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
                    content_hash="b" * 64,
                ),
                EvidenceProvenance(
                    source_kind="arm",
                    source_reference="Microsoft.Storage/storageAccounts",
                    source_version="2024-01-01",
                    observed_at=datetime(2026, 8, 5, 11, 59, tzinfo=UTC),
                    content_hash="a" * 64,
                ),
            ),
        )


def _import_tools_module():
    sys.modules.pop("chat.tools.enterprise", None)
    return importlib.import_module("chat.tools.enterprise")


@contextmanager
def _session_scope(tenant_id: str | None = "tenant-a", subscription_id: str | None = "sub-a"):
    from chat.tools.azure_session import clear_azure_session, set_azure_session

    token = set_azure_session(tenant_id=tenant_id, subscription_id=subscription_id)
    try:
        yield
    finally:
        clear_azure_session(token)


@pytest.mark.asyncio
async def test_tools_return_enterprise_disabled_when_feature_flag_off(monkeypatch):
    mod = _import_tools_module()
    monkeypatch.setenv("ENTERPRISE_ASSESSMENT_ENABLED", "false")

    with _session_scope():
        run_payload = await mod.run_enterprise_assessment()
        get_payload = await mod.get_enterprise_assessment("run-001")
        finding_payload = await mod.get_enterprise_finding("f-001")
        explain_payload = await mod.explain_enterprise_evidence("f-001")

    for payload in (run_payload, get_payload, finding_payload, explain_payload):
        assert payload["ok"] is False
        assert payload["code"] == "enterprise_disabled"


@pytest.mark.asyncio
async def test_tools_require_session_scope_without_service_call(monkeypatch):
    mod = _import_tools_module()
    monkeypatch.setenv("ENTERPRISE_ASSESSMENT_ENABLED", "true")
    service = FakeEnterpriseService()
    mod.set_enterprise_service_provider(lambda _credential: service)

    try:
        payload = await mod.run_enterprise_assessment()
    finally:
        mod.reset_enterprise_service_provider()

    assert payload["ok"] is False
    assert payload["code"] == "azure_scope_required"
    assert service.run_calls == []


@pytest.mark.asyncio
async def test_run_tool_passes_exact_session_scope(monkeypatch):
    mod = _import_tools_module()
    monkeypatch.setenv("ENTERPRISE_ASSESSMENT_ENABLED", "1")
    service = FakeEnterpriseService()
    mod.set_enterprise_service_provider(lambda _credential: service)

    try:
        with _session_scope("tenant-a", "sub-a"):
            payload = await mod.run_enterprise_assessment(
                resource_ids=["/subscriptions/sub-a/resourceGroups/rg/providers/Microsoft.Storage/storageAccounts/sa"],
                control_keys=["storage.secure_transfer"],
            )
    finally:
        mod.reset_enterprise_service_provider()

    assert payload["ok"] is True
    assert payload["run_id"] == "run-001"
    assert payload["status"] == "running"
    assert payload["next_action"] == "get_enterprise_assessment"
    assert service.run_calls == [
        (
            "tenant-a",
            "sub-a",
            ("/subscriptions/sub-a/resourceGroups/rg/providers/Microsoft.Storage/storageAccounts/sa",),
            ("storage.secure_transfer",),
        )
    ]


@pytest.mark.asyncio
async def test_get_and_finding_serialization_and_cross_sub_not_found(monkeypatch):
    mod = _import_tools_module()
    monkeypatch.setenv("ENTERPRISE_ASSESSMENT_ENABLED", "yes")
    service = FakeEnterpriseService()
    mod.set_enterprise_service_provider(lambda _credential: service)

    try:
        with _session_scope("tenant-a", "sub-a"):
            run_payload = await mod.get_enterprise_assessment("run-001")
            finding_payload = await mod.get_enterprise_finding("f-001")
        with _session_scope("tenant-a", "sub-b"):
            cross_sub_payload = await mod.get_enterprise_assessment("run-001")
    finally:
        mod.reset_enterprise_service_provider()

    assert run_payload["ok"] is True
    assert run_payload["assessment"]["verdict_counts"]["manual_pending"] == 0
    assert set(run_payload["assessment"]["verdict_counts"].keys()) == {
        "pass",
        "fail",
        "unknown",
        "not_applicable",
        "exempted",
        "manual_pending",
    }
    assert finding_payload["ok"] is True
    assert finding_payload["finding"]["verdict_state"] == "pass"
    assert cross_sub_payload == {"ok": False, "code": "not_found", "message": "assessment run not found"}


@pytest.mark.asyncio
async def test_service_errors_and_unexpected_errors_are_mapped_safely(monkeypatch, caplog):
    mod = _import_tools_module()
    monkeypatch.setenv("ENTERPRISE_ASSESSMENT_ENABLED", "true")

    service = FakeEnterpriseService()
    mod.set_enterprise_service_provider(lambda _credential: service)
    try:
        service.raise_on = "service_error"
        with _session_scope():
            service_error = await mod.run_enterprise_assessment()

        service.raise_on = "value_error"
        with _session_scope():
            value_error = await mod.run_enterprise_assessment()

        service.raise_on = "unexpected"
        with _session_scope(), caplog.at_level(logging.ERROR):
            unexpected = await mod.run_enterprise_assessment()
    finally:
        mod.reset_enterprise_service_provider()

    assert service_error == {
        "ok": False,
        "code": "enterprise_service_error",
        "message": "downstream unavailable",
        "status_code": 503,
    }
    assert value_error == {
        "ok": False,
        "code": "enterprise_invalid_input",
        "message": "bad input",
    }
    assert unexpected == {
        "ok": False,
        "code": "enterprise_internal_error",
        "message": "Unexpected enterprise tool error",
    }
    assert "access_token=leak" not in caplog.text


@pytest.mark.asyncio
async def test_explain_is_authoritative_and_unknown_abstains(monkeypatch):
    mod = _import_tools_module()
    monkeypatch.setenv("ENTERPRISE_ASSESSMENT_ENABLED", "true")
    service = FakeEnterpriseService()
    mod.set_enterprise_service_provider(lambda _credential: service)

    try:
        with _session_scope("tenant-a", "sub-a"):
            explain_pass = await mod.explain_enterprise_evidence("f-001")
            explain_unknown = await mod.explain_enterprise_evidence("f-unknown")
            explain_missing = await mod.explain_enterprise_evidence("missing")
    finally:
        mod.reset_enterprise_service_provider()

    assert explain_pass["ok"] is True
    assert explain_pass["verdict_authority"] == "deterministic_evaluator"
    assert explain_pass["verdict_override_allowed"] is False
    assert explain_pass["abstain"] is False
    hashes = [item["content_hash"] for item in explain_pass["evidence_citations"]]
    assert hashes == sorted(hashes)
    assert explain_pass["explanation_context"]["summary_ko"]

    assert explain_unknown["ok"] is True
    assert explain_unknown["finding"]["verdict_state"] == "unknown"
    assert explain_unknown["abstain"] is True

    assert explain_missing == {"ok": False, "code": "not_found", "message": "finding not found"}


def test_tool_signatures_exclude_scope_and_override_params():
    mod = _import_tools_module()
    forbidden = {"subscription_id", "tenant_id", "override", "text", "explanation_text"}
    for fn_name in (
        "run_enterprise_assessment",
        "get_enterprise_assessment",
        "get_enterprise_finding",
        "explain_enterprise_evidence",
    ):
        fn = getattr(mod, fn_name).func
        names = set(inspect.signature(fn).parameters)
        assert not (names & forbidden)


def test_all_tools_and_system_instructions_include_enterprise_rules():
    tools_mod = importlib.import_module("chat.tools")
    names = [tool.name for tool in tools_mod.ALL_TOOLS]
    assert names.count("run_enterprise_assessment") == 1
    assert names.count("get_enterprise_assessment") == 1
    assert names.count("get_enterprise_finding") == 1
    assert names.count("explain_enterprise_evidence") == 1

    agent_mod = importlib.import_module("chat.agent")
    text = agent_mod.SYSTEM_INSTRUCTIONS
    assert "deterministic verdict and evidence are authoritative" in text
    assert "must not alter or relabel pass/fail/unknown" in text
    assert "unknown/manual_pending => explicitly abstain" in text
    assert "legacy fallback" in text
