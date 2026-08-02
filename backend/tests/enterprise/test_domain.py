import hashlib
import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from enterprise.domain import (
    ControlDefinition,
    EvaluationRun,
    EvaluatorKind,
    EvidenceRecord,
    Verdict,
    VerdictState,
)


def test_evidence_hash_is_stable_for_key_order():
    first = EvidenceRecord.create("arm", "resource", "2024-01-01", {"a": 1, "b": 2})
    second = EvidenceRecord.create("arm", "resource", "2024-01-01", {"b": 2, "a": 1})

    assert first.content_hash == second.content_hash


def test_domain_contracts_are_immutable():
    evidence = EvidenceRecord.create("arm", "resource", "2024-01-01", {"enabled": True})

    with pytest.raises(FrozenInstanceError):
        evidence.source_kind = "arg"


def test_evidence_payload_is_recursively_immutable_through_tuples_lists_and_mappings():
    evidence = EvidenceRecord.create(
        "arm",
        "resource",
        "2024-01-01",
        {"outer": ({"items": [{"enabled": True}]},)},
    )

    with pytest.raises(TypeError):
        evidence.payload["outer"][0]["items"][0]["enabled"] = False


def _content_hash(payload):
    canonical_payload = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()


def _direct_evidence(**overrides):
    payload = {"b": 2, "a": 1}
    values = {
        "source_kind": "arm",
        "source_reference": "resource",
        "source_version": "2024-01-01",
        "observed_at": datetime(2026, 8, 2, tzinfo=UTC),
        "payload": payload,
        "content_hash": _content_hash(payload),
    }
    values.update(overrides)
    return EvidenceRecord(**values)


def test_direct_evidence_construction_canonicalizes_and_freezes_payload():
    evidence = _direct_evidence()

    assert evidence.content_hash == _content_hash({"a": 1, "b": 2})
    with pytest.raises(TypeError):
        evidence.payload["a"] = 3


@pytest.mark.parametrize("field", ["source_kind", "source_reference", "source_version"])
def test_direct_evidence_construction_rejects_empty_provenance(field):
    with pytest.raises(ValueError, match=field):
        _direct_evidence(**{field: " "})


@pytest.mark.parametrize("content_hash", ["", "not-a-sha256", "0" * 64])
def test_direct_evidence_construction_rejects_empty_invalid_or_forged_hash(content_hash):
    with pytest.raises(ValueError, match="content_hash"):
        _direct_evidence(content_hash=content_hash)


def test_direct_evidence_construction_rejects_timezone_naive_observed_at():
    with pytest.raises(ValueError, match="observed_at"):
        _direct_evidence(observed_at=datetime(2026, 8, 2))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("key", ""),
        ("version", ""),
        ("resource_type", ""),
        ("selector", ""),
        ("assertion", {}),
    ],
)
def test_control_definition_rejects_empty_required_values(field, value):
    values = {
        "key": "storage.secure_transfer",
        "version": "1.0.0",
        "resource_type": "Microsoft.Storage/storageAccounts",
        "evaluator_kind": EvaluatorKind.CUSTOM,
        "source_kind": "arm",
        "source_reference": "properties.supportsHttpsTrafficOnly",
        "source_version": "2024-01-01",
        "selector": "properties.supportsHttpsTrafficOnly",
        "assertion": {"equals": True},
        "scope_conditions": {"resource_groups": ("production",)},
        "state_rules": {"pass": {"equals": True}},
        "remediation": {"description": "Enable secure transfer"},
        "verification": {"selector": "properties.supportsHttpsTrafficOnly"},
    }
    values[field] = value

    with pytest.raises(ValueError, match=field):
        ControlDefinition(**values)


@pytest.mark.parametrize("field", ["state_rules", "remediation", "verification"])
def test_control_definition_rejects_empty_design_mappings(field):
    values = {
        "key": "storage.secure_transfer",
        "version": "1.0.0",
        "resource_type": "Microsoft.Storage/storageAccounts",
        "evaluator_kind": EvaluatorKind.CUSTOM,
        "source_kind": "arm",
        "source_reference": "properties.supportsHttpsTrafficOnly",
        "source_version": "2024-01-01",
        "selector": "properties.supportsHttpsTrafficOnly",
        "assertion": {"equals": True},
        "scope_conditions": {},
        "state_rules": {"pass": {"equals": True}},
        "remediation": {"description": "Enable secure transfer"},
        "verification": {"selector": "properties.supportsHttpsTrafficOnly"},
    }
    values[field] = {}

    with pytest.raises(ValueError, match=field):
        ControlDefinition(**values)


@pytest.mark.parametrize("field", ["source_kind", "source_reference", "source_version"])
def test_control_definition_rejects_empty_source_provenance(field):
    values = {
        "key": "storage.secure_transfer",
        "version": "1.0.0",
        "resource_type": "Microsoft.Storage/storageAccounts",
        "evaluator_kind": EvaluatorKind.CUSTOM,
        "source_kind": "arm",
        "source_reference": "properties.supportsHttpsTrafficOnly",
        "source_version": "2024-01-01",
        "selector": "properties.supportsHttpsTrafficOnly",
        "assertion": {"equals": True},
        "scope_conditions": {},
        "state_rules": {"pass": {"equals": True}},
        "remediation": {"description": "Enable secure transfer"},
        "verification": {"selector": "properties.supportsHttpsTrafficOnly"},
    }
    values[field] = " "

    with pytest.raises(ValueError, match=field):
        ControlDefinition(**values)


def test_control_definition_carries_and_recursively_freezes_design_metadata():
    control = ControlDefinition(
        key="storage.secure_transfer",
        version="1.0.0",
        resource_type="Microsoft.Storage/storageAccounts",
        evaluator_kind=EvaluatorKind.CUSTOM,
        source_kind="arm",
        source_reference="properties.supportsHttpsTrafficOnly",
        source_version="2024-01-01",
        selector="properties.supportsHttpsTrafficOnly",
        assertion={"equals": True},
        scope_conditions={"resource_groups": ("production",)},
        state_rules={"pass": {"equals": True}},
        remediation={"steps": [{"set": {"supportsHttpsTrafficOnly": True}}]},
        verification={"checks": ({"selector": "properties.supportsHttpsTrafficOnly"},)},
    )

    assert control.source_kind == "arm"
    assert control.source_reference == "properties.supportsHttpsTrafficOnly"
    assert control.source_version == "2024-01-01"
    assert control.scope_conditions["resource_groups"] == ("production",)
    with pytest.raises(TypeError):
        control.state_rules["pass"]["equals"] = False
    with pytest.raises(TypeError):
        control.remediation["steps"][0]["set"]["supportsHttpsTrafficOnly"] = False
    with pytest.raises(TypeError):
        control.verification["checks"][0]["selector"] = "changed"


@pytest.mark.parametrize("state", [VerdictState.PASS, VerdictState.FAIL])
def test_pass_and_fail_verdicts_require_evidence_hashes(state):
    with pytest.raises(ValueError, match="evidence_hashes"):
        Verdict(
            control_key="storage.secure_transfer",
            state=state,
            reason_code="assertion_matched",
        )


def test_verdict_rejects_string_evidence_hashes():
    with pytest.raises(ValueError, match="evidence_hashes"):
        Verdict(
            control_key="storage.secure_transfer",
            state=VerdictState.PASS,
            reason_code="assertion_matched",
            evidence_hashes="0" * 64,
        )


@pytest.mark.parametrize("content_hash", ["", "not-a-sha256", "g" * 64, "0" * 63])
def test_verdict_rejects_invalid_or_non_sha256_evidence_hash_entries(content_hash):
    with pytest.raises(ValueError, match="evidence_hashes"):
        Verdict(
            control_key="storage.secure_transfer",
            state=VerdictState.PASS,
            reason_code="assertion_matched",
            evidence_hashes=(content_hash,),
        )


def test_evaluation_run_keeps_verdicts_as_an_immutable_tuple():
    verdict = Verdict(
        control_key="storage.secure_transfer",
        state=VerdictState.UNKNOWN,
        reason_code="evidence_missing",
    )
    run = EvaluationRun(
        run_id="run-1",
        started_at=datetime(2026, 8, 2, tzinfo=UTC),
        verdicts=(verdict,),
    )

    assert run.verdicts == (verdict,)
    with pytest.raises(FrozenInstanceError):
        run.run_id = "run-2"