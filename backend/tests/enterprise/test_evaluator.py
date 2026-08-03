import json
from dataclasses import replace
from pathlib import Path

import pytest

from enterprise.domain import EvidenceRecord, EvidenceStatus, SourceRole, VerdictState
from enterprise.evaluator import DeterministicEvaluator
from enterprise.registry import ControlRegistry


ROOT = Path(__file__).resolve().parents[3]
SPIKE_ROOT = ROOT / "experiments/coverage_spike"
CHECKLIST_PATH = SPIKE_ROOT / "checklists/azure_storage_production_readiness.yaml"
MAPPING_PATH = SPIKE_ROOT / "mappings/azure_storage_production_readiness.yaml"


def _load_json(relative_path: str):
    with open(SPIKE_ROOT / relative_path, encoding="utf-8") as file:
        return json.load(file)


def _load_evidence(fixture_name: str) -> list[EvidenceRecord]:
    fixture = _load_json(f"fixtures/{fixture_name}.json")
    return [
        EvidenceRecord.create(**{**item, "status": EvidenceStatus(item["status"])})
        for item in fixture["evidence"]
    ]


def _primary_evidence(
    control,
    payload,
    *,
    resource_id="synthetic-storage-account",
    status=EvidenceStatus.COMPLETE,
):
    source = control.primary_source
    return EvidenceRecord.create(
        source_kind=source.source_kind,
        source_reference=source.reference,
        source_version=source.version,
        resource_id=resource_id,
        status=status,
        payload=payload,
    )


def _corroborating_evidence(
    control,
    signal,
    *,
    resource_id="synthetic-storage-account",
    status=EvidenceStatus.COMPLETE,
):
    source = next(source for source in control.sources if source.role is SourceRole.CORROBORATING)
    return EvidenceRecord.create(
        source_kind=source.source_kind,
        source_reference=source.reference,
        source_version=source.version,
        resource_id=resource_id,
        status=status,
        payload={
            "resource_type": control.resource_type,
            "verdict": {"status": signal},
        },
    )


@pytest.fixture
def registry():
    return ControlRegistry.load(CHECKLIST_PATH, MAPPING_PATH)


@pytest.mark.parametrize(
    "fixture_name",
    ["storage_account_compliant", "storage_account_noncompliant"],
)
def test_expected_fixture_results_cover_all_six_controls(registry, fixture_name):
    evidence = _load_evidence(fixture_name)
    expected = _load_json(f"expected/{fixture_name}.json")

    actual = {}
    for key, control in registry.controls.items():
        verdict = DeterministicEvaluator().evaluate(control, evidence)
        actual[key] = {
            "state": verdict.state.value,
            "reason_code": verdict.reason_code,
        }

    assert set(expected["verdicts"]) == set(registry.controls)
    assert actual == expected["verdicts"]


def test_missing_evidence_is_unknown(registry):
    control = registry.get("storage.secure_transfer")

    verdict = DeterministicEvaluator().evaluate(control, [])

    assert verdict.state is VerdictState.UNKNOWN
    assert verdict.reason_code == "evidence_missing"


def test_partial_required_evidence_is_unknown(registry):
    control = registry.get("storage.secure_transfer")
    partial_evidence = _load_evidence("storage_account_partial")[0]

    verdict = DeterministicEvaluator().evaluate(control, [partial_evidence])

    assert verdict.state is VerdictState.UNKNOWN
    assert verdict.reason_code == "evidence_partial"


def test_missing_required_payload_field_is_scope_incomplete(registry):
    control = registry.get("storage.secure_transfer")
    evidence = _primary_evidence(
        control,
        {"resource_type": control.resource_type, "properties": {}},
    )

    verdict = DeterministicEvaluator().evaluate(control, [evidence])

    assert verdict.state is VerdictState.UNKNOWN
    assert verdict.reason_code == "evidence_scope_incomplete"


def test_null_resource_type_is_unknown(registry):
    control = registry.get("storage.minimum_tls")
    evidence = _primary_evidence(
        control,
        {
            "resource_type": None,
            "properties": {"minimumTlsVersion": "TLS1_2"},
        },
    )

    verdict = DeterministicEvaluator().evaluate(control, [evidence])

    assert verdict.state is VerdictState.UNKNOWN
    assert verdict.reason_code == "evidence_resource_type_missing"


@pytest.mark.parametrize("resource_type", [_MISSING := object(), None])
def test_target_record_with_missing_resource_type_cannot_be_hidden_by_passing_evidence(
    registry,
    resource_type,
):
    control = registry.get("storage.secure_transfer")
    passing = _primary_evidence(
        control,
        {
            "resource_type": control.resource_type,
            "properties": {"supportsHttpsTrafficOnly": True},
        },
    )
    malformed_payload = {"properties": {"supportsHttpsTrafficOnly": True}}
    if resource_type is not _MISSING:
        malformed_payload["resource_type"] = resource_type
    malformed = _primary_evidence(control, malformed_payload)

    verdict = DeterministicEvaluator().evaluate(control, [passing, malformed])

    assert verdict.state is VerdictState.UNKNOWN
    assert verdict.reason_code == "evidence_resource_type_missing"
    assert set(verdict.evidence_hashes) == {passing.content_hash, malformed.content_hash}


def test_target_record_with_contradictory_resource_type_cannot_be_hidden_by_passing_evidence(
    registry,
):
    control = registry.get("storage.secure_transfer")
    passing = _primary_evidence(
        control,
        {
            "resource_type": control.resource_type,
            "properties": {"supportsHttpsTrafficOnly": True},
        },
    )
    contradictory = _primary_evidence(
        control,
        {
            "resource_type": "Microsoft.Compute/virtualMachines",
            "properties": {"supportsHttpsTrafficOnly": True},
        },
    )

    verdict = DeterministicEvaluator().evaluate(control, [passing, contradictory])

    assert verdict.state is VerdictState.UNKNOWN
    assert verdict.reason_code == "evidence_scope_conflict"
    assert set(verdict.evidence_hashes) == {passing.content_hash, contradictory.content_hash}


@pytest.mark.parametrize("resource_type", [_MISSING, None, "Microsoft.Compute/virtualMachines"])
def test_malformed_resource_type_for_unrelated_resource_id_is_ignored(
    registry,
    resource_type,
):
    control = registry.get("storage.secure_transfer")
    passing = _primary_evidence(
        control,
        {
            "resource_type": control.resource_type,
            "properties": {"supportsHttpsTrafficOnly": True},
        },
    )
    unrelated_payload = {"properties": {"supportsHttpsTrafficOnly": False}}
    if resource_type is not _MISSING:
        unrelated_payload["resource_type"] = resource_type
    unrelated = _primary_evidence(
        control,
        unrelated_payload,
        resource_id="unrelated-storage-account",
    )

    verdict = DeterministicEvaluator().evaluate(control, [passing, unrelated])

    assert verdict.state is VerdictState.PASS
    assert verdict.reason_code == "assertion_matched"
    assert verdict.evidence_hashes == (passing.content_hash,)


def test_mixed_resource_ids_are_scope_conflict(registry):
    control = registry.get("storage.secure_transfer")
    primary = _primary_evidence(
        control,
        {
            "resource_type": control.resource_type,
            "properties": {"supportsHttpsTrafficOnly": True},
        },
        resource_id="synthetic-storage-account-one",
    )
    corroborating = _corroborating_evidence(
        control,
        "pass",
        resource_id="synthetic-storage-account-two",
    )

    verdict = DeterministicEvaluator().evaluate(control, [primary, corroborating])

    assert verdict.state is VerdictState.UNKNOWN
    assert verdict.reason_code == "evidence_scope_conflict"


def test_conflicting_applicable_source_values_are_unknown(registry):
    control = registry.get("storage.secure_transfer")
    compliant_evidence = _load_evidence("storage_account_compliant")[0]
    conflicting_evidence = EvidenceRecord.create(
        source_kind=compliant_evidence.source_kind,
        source_reference=compliant_evidence.source_reference,
        source_version=compliant_evidence.source_version,
        resource_id=compliant_evidence.resource_id,
        status=EvidenceStatus.COMPLETE,
        payload={
            "resource_type": "Microsoft.Storage/storageAccounts",
            "properties": {"supportsHttpsTrafficOnly": False},
        },
    )

    verdict = DeterministicEvaluator().evaluate(
        control,
        [compliant_evidence, conflicting_evidence],
    )

    assert verdict.state is VerdictState.UNKNOWN
    assert verdict.reason_code == "evidence_conflict"
    assert set(verdict.evidence_hashes) == {
        compliant_evidence.content_hash,
        conflicting_evidence.content_hash,
    }


def test_missing_required_corroborating_source_is_unknown(registry):
    control = registry.get("storage.secure_transfer")
    required_sources = tuple(
        replace(source, required=True) if source.role is SourceRole.CORROBORATING else source
        for source in control.sources
    )
    control = replace(control, sources=required_sources)
    primary = _primary_evidence(
        control,
        {
            "resource_type": control.resource_type,
            "properties": {"supportsHttpsTrafficOnly": True},
        },
    )

    verdict = DeterministicEvaluator().evaluate(control, [primary])

    assert verdict.state is VerdictState.UNKNOWN
    assert verdict.reason_code == "evidence_missing"


def test_partial_required_corroborating_source_is_unknown(registry):
    control = registry.get("storage.secure_transfer")
    required_sources = tuple(
        replace(source, required=True) if source.role is SourceRole.CORROBORATING else source
        for source in control.sources
    )
    control = replace(control, sources=required_sources)
    primary = _primary_evidence(
        control,
        {
            "resource_type": control.resource_type,
            "properties": {"supportsHttpsTrafficOnly": True},
        },
    )
    corroborating = _corroborating_evidence(control, "pass", status=EvidenceStatus.PARTIAL)

    verdict = DeterministicEvaluator().evaluate(control, [primary, corroborating])

    assert verdict.state is VerdictState.UNKNOWN
    assert verdict.reason_code == "evidence_partial"


def test_conflicting_corroborating_verdict_signal_is_unknown(registry):
    control = registry.get("storage.secure_transfer")
    primary = _primary_evidence(
        control,
        {
            "resource_type": control.resource_type,
            "properties": {"supportsHttpsTrafficOnly": True},
        },
    )
    corroborating = _corroborating_evidence(control, "fail")

    verdict = DeterministicEvaluator().evaluate(control, [primary, corroborating])

    assert verdict.state is VerdictState.UNKNOWN
    assert verdict.reason_code == "managed_source_conflict"
    assert set(verdict.evidence_hashes) == {primary.content_hash, corroborating.content_hash}


@pytest.mark.parametrize(
    "control_key",
    [
        "storage.secure_transfer",
        "storage.public_network",
        "storage.redundancy",
        "storage.private_endpoint",
    ],
)
def test_each_managed_source_kind_conflict_is_unknown(registry, control_key):
    control = registry.get(control_key)
    evidence = _load_evidence("storage_account_compliant")
    evidence.append(
        _corroborating_evidence(
            control,
            "fail",
            resource_id="synthetic-storage-account-compliant",
        )
    )

    verdict = DeterministicEvaluator().evaluate(control, evidence)

    assert verdict.state is VerdictState.UNKNOWN
    assert verdict.reason_code == "managed_source_conflict"


def test_matching_corroborating_verdict_signal_preserves_primary_verdict(registry):
    control = registry.get("storage.secure_transfer")
    primary = _primary_evidence(
        control,
        {
            "resource_type": control.resource_type,
            "properties": {"supportsHttpsTrafficOnly": True},
        },
    )
    corroborating = _corroborating_evidence(control, "pass")

    verdict = DeterministicEvaluator().evaluate(control, [primary, corroborating])

    assert verdict.state is VerdictState.PASS
    assert verdict.reason_code == "assertion_matched"


@pytest.mark.parametrize(
    ("assertion", "actual"),
    [
        ({"selector": "properties.supportsHttpsTrafficOnly", "operator": "equals", "expected": True}, None),
        ({"selector": "properties.supportsHttpsTrafficOnly", "operator": "in", "expected": (True,)}, None),
        ({"selector": "properties.supportsHttpsTrafficOnly", "operator": "not_in", "expected": (False,)}, None),
        ({"selector": "properties.supportsHttpsTrafficOnly", "operator": "greater_than_or_equal", "expected": 1}, None),
    ],
)
def test_null_actual_values_are_unknown_except_for_exists(registry, assertion, actual):
    control = replace(registry.get("storage.secure_transfer"), assertion=assertion)
    evidence = _primary_evidence(
        control,
        {
            "resource_type": control.resource_type,
            "properties": {"supportsHttpsTrafficOnly": actual},
        },
    )

    verdict = DeterministicEvaluator().evaluate(control, [evidence])

    assert verdict.state is VerdictState.UNKNOWN
    assert verdict.reason_code == "evidence_value_unknown"


def test_exists_treats_null_as_a_known_nonexistent_value(registry):
    control = replace(
        registry.get("storage.secure_transfer"),
        assertion={
            "selector": "properties.supportsHttpsTrafficOnly",
            "operator": "exists",
            "expected": True,
        },
    )
    evidence = _primary_evidence(
        control,
        {
            "resource_type": control.resource_type,
            "properties": {"supportsHttpsTrafficOnly": None},
        },
    )

    verdict = DeterministicEvaluator().evaluate(control, [evidence])

    assert verdict.state is VerdictState.FAIL
    assert verdict.reason_code == "assertion_not_matched"


def test_equals_uses_exact_types_so_bool_does_not_equal_int(registry):
    control = registry.get("storage.secure_transfer")
    evidence = _primary_evidence(
        control,
        {
            "resource_type": control.resource_type,
            "properties": {"supportsHttpsTrafficOnly": 1},
        },
    )

    verdict = DeterministicEvaluator().evaluate(control, [evidence])

    assert verdict.state is VerdictState.FAIL
    assert verdict.reason_code == "assertion_not_matched"


@pytest.mark.parametrize(
    ("enabled", "days"),
    [(False, 14), (True, 6)],
)
def test_blob_soft_delete_requires_enabled_and_minimum_days(registry, enabled, days):
    control = registry.get("storage.blob_soft_delete")
    evidence = _primary_evidence(
        control,
        {
            "resource_type": control.resource_type,
            "properties": {"deleteRetentionPolicy": {"enabled": enabled, "days": days}},
        },
    )

    verdict = DeterministicEvaluator().evaluate(control, [evidence])

    assert verdict.state is VerdictState.FAIL
    assert verdict.reason_code == "assertion_not_matched"


def test_unsupported_operator_is_unknown(registry):
    control = replace(
        registry.get("storage.secure_transfer"),
        assertion={
            "selector": "properties.supportsHttpsTrafficOnly",
            "operator": "matches_regex",
            "expected": "true",
        },
    )
    evidence = _load_evidence("storage_account_compliant")

    verdict = DeterministicEvaluator().evaluate(control, evidence)

    assert verdict.state is VerdictState.UNKNOWN
    assert verdict.reason_code == "assertion_operator_unsupported"


@pytest.mark.parametrize(
    "assertion",
    [
        {"selector": "properties.supportsHttpsTrafficOnly", "operator": "in", "expected": "true"},
        {"selector": "properties.supportsHttpsTrafficOnly", "operator": "greater_than_or_equal", "expected": True},
        {"all": "not-a-sequence"},
    ],
)
def test_runtime_malformed_replacement_controls_return_unknown(registry, assertion):
    control = replace(registry.get("storage.secure_transfer"), assertion=assertion)
    evidence = _load_evidence("storage_account_compliant")

    verdict = DeterministicEvaluator().evaluate(control, evidence)

    assert verdict.state is VerdictState.UNKNOWN
    assert verdict.reason_code == "assertion_value_invalid"


def test_unsupported_resource_type_is_not_applicable(registry):
    control = registry.get("storage.secure_transfer")
    evidence = EvidenceRecord.create(
        source_kind=control.source_kind,
        source_reference=control.source_reference,
        source_version=control.source_version,
        resource_id="synthetic-virtual-machine",
        status=EvidenceStatus.COMPLETE,
        payload={
            "resource_type": "Microsoft.Compute/virtualMachines",
            "properties": {"supportsHttpsTrafficOnly": True},
        },
    )

    verdict = DeterministicEvaluator().evaluate(control, [evidence])

    assert verdict.state is VerdictState.NOT_APPLICABLE
    assert verdict.reason_code == "resource_type_unsupported"


@pytest.mark.parametrize(
    ("selector", "payload", "expected"),
    [
        ("properties.flag", {"properties": {"flag": True}}, True),
        ("properties.mode", {"properties": {"mode": "TLS1_2"}}, "TLS1_2"),
        ("properties.access", {"properties": {"access": "Disabled"}}, "Disabled"),
        ("properties.days", {"properties": {"days": 14}}, 14),
        ("properties.endpoint", {"properties": {"endpoint": {"id": "pe-synthetic"}}}, {"id": "pe-synthetic"}),
    ],
)
def test_dot_selectors_resolve_nested_dictionary_values(selector, payload, expected):
    assert DeterministicEvaluator.resolve_selector(payload, selector) == expected