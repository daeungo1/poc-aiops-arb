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
    EvidenceSource,
    EvidenceStatus,
    SourceRole,
    Verdict,
    VerdictState,
)


CANONICAL_STATE_RULES = {
    "assertion_matched": "pass",
    "assertion_not_matched": "fail",
    "evidence_missing": "unknown",
    "evidence_partial": "unknown",
    "evidence_resource_type_missing": "unknown",
    "evidence_scope_conflict": "unknown",
    "evidence_scope_incomplete": "unknown",
    "evidence_selector_missing": "unknown",
    "evidence_value_unknown": "unknown",
    "evidence_conflict": "unknown",
    "managed_source_conflict": "unknown",
    "corroborating_signal_invalid": "unknown",
    "assertion_operator_unsupported": "unknown",
    "assertion_value_invalid": "unknown",
    "resource_type_unsupported": "not_applicable",
    "valid_exemption": "exempted",
    "manual_evidence_required": "manual_pending",
}


def _create_evidence(payload=None, **overrides):
    values = {
        "source_kind": "arm",
        "source_reference": "resource",
        "source_version": "2024-01-01",
        "payload": payload if payload is not None else {"enabled": True},
        "resource_id": "/subscriptions/synthetic/resourceGroups/rg/providers/Microsoft.Storage/storageAccounts/sa",
        "status": EvidenceStatus.COMPLETE,
    }
    values.update(overrides)
    return EvidenceRecord.create(**values)


def test_evidence_hash_is_stable_for_key_order():
    first = _create_evidence({"a": 1, "b": 2})
    second = _create_evidence({"b": 2, "a": 1})

    assert first.content_hash == second.content_hash


def test_domain_contracts_are_immutable():
    evidence = _create_evidence()

    with pytest.raises(FrozenInstanceError):
        evidence.source_kind = "arg"


def test_evidence_payload_is_recursively_immutable_through_tuples_lists_and_mappings():
    evidence = _create_evidence({"outer": ({"items": [{"enabled": True}]},)})

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
        "resource_id": "/subscriptions/synthetic/resourceGroups/rg/providers/Microsoft.Storage/storageAccounts/sa",
        "status": EvidenceStatus.COMPLETE,
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


@pytest.mark.parametrize("field", ["source_kind", "source_reference", "source_version", "resource_id"])
def test_direct_evidence_construction_rejects_empty_provenance(field):
    with pytest.raises(ValueError, match=field):
        _direct_evidence(**{field: " "})


def test_evidence_create_rejects_empty_resource_id():
    with pytest.raises(ValueError, match="resource_id"):
        _create_evidence(resource_id=" ")


def test_direct_evidence_construction_rejects_non_enum_status():
    with pytest.raises(ValueError, match="status"):
        _direct_evidence(status="complete")


def test_evidence_create_rejects_non_enum_status():
    with pytest.raises(ValueError, match="status"):
        _create_evidence(status="partial")


@pytest.mark.parametrize("content_hash", ["", "not-a-sha256", "0" * 64])
def test_direct_evidence_construction_rejects_empty_invalid_or_forged_hash(content_hash):
    with pytest.raises(ValueError, match="content_hash"):
        _direct_evidence(content_hash=content_hash)


def test_direct_evidence_construction_rejects_timezone_naive_observed_at():
    with pytest.raises(ValueError, match="observed_at"):
        _direct_evidence(observed_at=datetime(2026, 8, 2))


def _primary_source(**overrides):
    values = {
        "source_kind": "arm",
        "reference": "arm.storage_account.resource",
        "version": "2023-05-01",
        "role": SourceRole.PRIMARY,
        "required": True,
    }
    values.update(overrides)
    return EvidenceSource(**values)


def _control_values():
    return {
        "key": "storage.secure_transfer",
        "version": "1.0.0",
        "resource_type": "Microsoft.Storage/storageAccounts",
        "evaluator_kind": EvaluatorKind.CUSTOM,
        "sources": (_primary_source(),),
        "assertion": {
            "selector": "properties.supportsHttpsTrafficOnly",
            "operator": "equals",
            "expected": True,
        },
        "scope_conditions": {
            "same_resource_id": True,
            "required_payload_fields": {
                "primary": ("resource_type", "properties.supportsHttpsTrafficOnly"),
            },
        },
        "state_rules": CANONICAL_STATE_RULES,
        "remediation": {"description": "Enable secure transfer"},
        "verification": {"selector": "properties.supportsHttpsTrafficOnly"},
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("key", ""),
        ("version", ""),
        ("resource_type", ""),
        ("sources", ()),
        ("assertion", {}),
    ],
)
def test_control_definition_rejects_empty_required_values(field, value):
    values = _control_values()
    values[field] = value

    with pytest.raises(ValueError, match=field):
        ControlDefinition(**values)


@pytest.mark.parametrize("field", ["state_rules", "remediation", "verification"])
def test_control_definition_rejects_empty_design_mappings(field):
    values = _control_values()
    values[field] = {}

    with pytest.raises(ValueError, match=field):
        ControlDefinition(**values)


@pytest.mark.parametrize("field", ["source_kind", "reference", "version"])
def test_evidence_source_rejects_empty_provenance(field):
    with pytest.raises(ValueError, match=field):
        _primary_source(**{field: " "})


def test_evidence_source_rejects_invalid_role_and_required_flag():
    with pytest.raises(ValueError, match="role"):
        _primary_source(role="primary")

    with pytest.raises(ValueError, match="required"):
        _primary_source(required=1)


def test_corroborating_source_requires_normalized_verdict_selector():
    with pytest.raises(ValueError, match="verdict_selector"):
        _primary_source(role=SourceRole.CORROBORATING, required=False)


def test_corroborating_source_rejects_noncanonical_verdict_selector():
    with pytest.raises(ValueError, match="verdict_selector"):
        _primary_source(
            role=SourceRole.CORROBORATING,
            required=False,
            verdict_selector="recommendation.compliance",
        )


def test_primary_source_rejects_corroborating_verdict_selector():
    with pytest.raises(ValueError, match="verdict_selector"):
        _primary_source(verdict_selector="verdict.status")


def test_control_definition_requires_exactly_one_required_primary_source():
    values = _control_values()
    values["sources"] = (_primary_source(required=False),)

    with pytest.raises(ValueError, match="required primary"):
        ControlDefinition(**values)


def test_control_definition_rejects_noncanonical_state_rules():
    values = _control_values()
    values["state_rules"] = {**CANONICAL_STATE_RULES, "assertion_matched": "fail"}

    with pytest.raises(ValueError, match="state_rules"):
        ControlDefinition(**values)


def test_control_definition_stores_sources_as_immutable_tuple():
    sources = [
        _primary_source(),
        EvidenceSource(
            source_kind="azure_policy",
            reference="synthetic-policy-definition-storage-secure-transfer",
            version="synthetic-v1",
            role=SourceRole.CORROBORATING,
            required=False,
            verdict_selector="verdict.status",
        ),
    ]
    values = _control_values()
    values["sources"] = sources

    control = ControlDefinition(**values)

    assert control.sources == tuple(sources)
    assert control.source_kind == "arm"
    assert control.source_reference == "arm.storage_account.resource"
    assert control.source_version == "2023-05-01"


def test_control_definition_carries_and_recursively_freezes_design_metadata():
    values = _control_values()
    values.update(
        scope_conditions={"same_resource_id": True, "required_payload_fields": {"primary": ("resource_type",)}},
        state_rules=CANONICAL_STATE_RULES,
        remediation={"steps": [{"set": {"supportsHttpsTrafficOnly": True}}]},
        verification={"checks": ({"selector": "properties.supportsHttpsTrafficOnly"},)},
    )
    control = ControlDefinition(**values)

    assert control.source_kind == "arm"
    assert control.source_reference == "arm.storage_account.resource"
    assert control.source_version == "2023-05-01"
    assert control.scope_conditions["required_payload_fields"]["primary"] == ("resource_type",)
    with pytest.raises(TypeError):
        control.state_rules["assertion_matched"] = "fail"
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