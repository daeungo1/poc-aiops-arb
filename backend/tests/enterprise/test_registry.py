from pathlib import Path

import pytest
import yaml

from agent.checklist_loader import ChecklistLoader
from enterprise.domain import EvaluatorKind, SourceRole
from enterprise.registry import ControlRegistry


ROOT = Path(__file__).resolve().parents[3]
CHECKLIST_PATH = ROOT / "experiments/coverage_spike/checklists/azure_storage_production_readiness.yaml"
MAPPING_PATH = ROOT / "experiments/coverage_spike/mappings/azure_storage_production_readiness.yaml"
CONTROL_KEYS = {
    "storage.secure_transfer",
    "storage.minimum_tls",
    "storage.public_network",
    "storage.blob_soft_delete",
    "storage.redundancy",
    "storage.private_endpoint",
}


def _load_mapping_document():
    with open(MAPPING_PATH, encoding="utf-8") as file:
        return yaml.safe_load(file)


def _write_mapping(tmp_path, document):
    path = tmp_path / "mapping.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def _operators(assertion):
    if "all" in assertion:
        return {operator for child in assertion["all"] for operator in _operators(child)}
    return {assertion["operator"]}


def test_sample_checklist_parses_with_existing_loader_and_declares_six_unique_controls():
    checklist = ChecklistLoader().load_file(CHECKLIST_PATH)
    checklist_control_keys = [
        check.azure_check["control_key"]
        for _, _, check in checklist.get_all_checks()
    ]

    assert checklist.name == "Azure Storage Production Readiness"
    assert checklist.applicable_resource_types == ["microsoft.storage/storageaccounts"]
    assert len(checklist_control_keys) == 6
    assert set(checklist_control_keys) == CONTROL_KEYS


def test_registry_maps_every_checklist_control_one_to_one_with_required_metadata():
    checklist = ChecklistLoader().load_file(CHECKLIST_PATH)
    registry = ControlRegistry.load(CHECKLIST_PATH, MAPPING_PATH)
    checklist_control_keys = {
        check.azure_check["control_key"]
        for _, _, check in checklist.get_all_checks()
    }

    assert set(registry.controls) == checklist_control_keys == CONTROL_KEYS
    assert {control.evaluator_kind for control in registry.controls.values()} <= set(EvaluatorKind)
    assert set().union(*(_operators(control.assertion) for control in registry.controls.values())) == {
        "equals",
        "in",
        "not_in",
        "greater_than_or_equal",
        "exists",
    }

    for control in registry.controls.values():
        assert control.key
        assert control.version
        assert control.resource_type == "Microsoft.Storage/storageAccounts"
        assert control.sources
        assert sum(source.role is SourceRole.PRIMARY for source in control.sources) == 1
        assert control.primary_source.required is True
        assert control.assertion
        assert control.state_rules
        assert control.remediation
        assert control.verification


def test_registry_get_returns_control_by_stable_key():
    registry = ControlRegistry.load(CHECKLIST_PATH, MAPPING_PATH)

    control = registry.get("storage.secure_transfer")

    assert control.key == "storage.secure_transfer"
    assert control.assertion == {
        "selector": "properties.supportsHttpsTrafficOnly",
        "operator": "equals",
        "expected": True,
    }


def test_mapping_uses_canonical_sources_array_without_singular_source_fields():
    document = _load_mapping_document()

    for control in document["controls"]:
        assert isinstance(control["sources"], list) and control["sources"]
        assert {"source_kind", "reference", "version", "role", "required"} <= set(control["sources"][0])
        assert {"source_kind", "source_reference", "source_version"}.isdisjoint(control)


def test_every_source_declares_non_empty_adapter_config():
    registry = ControlRegistry.load(CHECKLIST_PATH, MAPPING_PATH)

    assert all(source.adapter_config for control in registry.controls.values() for source in control.sources)


def test_registry_rejects_source_without_adapter_config(tmp_path):
    document = _load_mapping_document()
    document["controls"][0]["sources"][0].pop("adapter_config", None)

    with pytest.raises(ValueError, match="adapter_config"):
        ControlRegistry.load(CHECKLIST_PATH, _write_mapping(tmp_path, document))


def test_registry_rejects_source_adapter_config_unknown_keys(tmp_path):
    document = _load_mapping_document()
    document["controls"][0]["sources"][0]["adapter_config"]["unexpected"] = "value"

    with pytest.raises(ValueError, match="adapter_config"):
        ControlRegistry.load(CHECKLIST_PATH, _write_mapping(tmp_path, document))


def test_registry_rejects_arg_source_when_query_hash_and_source_version_do_not_match(tmp_path):
    document = _load_mapping_document()
    arg_source = document["controls"][-1]["sources"][0]
    arg_source["adapter_config"]["query"] = "Resources | project id, type"

    with pytest.raises(ValueError, match="query"):
        ControlRegistry.load(CHECKLIST_PATH, _write_mapping(tmp_path, document))


def test_sample_maps_four_synthetic_managed_corroborating_source_kinds():
    registry = ControlRegistry.load(CHECKLIST_PATH, MAPPING_PATH)
    managed_sources = {
        source.source_kind: source
        for control in registry.controls.values()
        for source in control.sources
        if source.role is SourceRole.CORROBORATING
    }

    assert {"azure_policy", "defender", "advisor", "aprl"} <= set(managed_sources)
    assert all("synthetic" in source.reference for source in managed_sources.values())
    assert managed_sources["azure_policy"].version == "2019-10-01"
    assert managed_sources["defender"].version == "2020-01-01"
    assert managed_sources["advisor"].version == "2025-01-01"
    assert managed_sources["aprl"].version.startswith("api-version:2022-10-01;query-sha256:")
    managed_controls = [control for control in registry.controls.values() if len(control.sources) > 1]
    assert len(managed_controls) >= 4
    assert all(control.evaluator_kind is EvaluatorKind.MANAGED for control in managed_controls)
    assert all(
        source.verdict_selector == "verdict.status"
        for control in managed_controls
        for source in control.sources
        if source.role is SourceRole.CORROBORATING
    )


def test_blob_soft_delete_uses_compound_all_assertion():
    control = ControlRegistry.load(CHECKLIST_PATH, MAPPING_PATH).get("storage.blob_soft_delete")

    assert control.assertion == {
        "all": (
            {
                "selector": "properties.deleteRetentionPolicy.enabled",
                "operator": "equals",
                "expected": True,
            },
            {
                "selector": "properties.deleteRetentionPolicy.days",
                "operator": "greater_than_or_equal",
                "expected": 7,
            },
        )
    }
    assert control.remediation["desired_changes"] == (
        {
            "property": "properties.deleteRetentionPolicy.enabled",
            "operator": "equals",
            "desired_value": True,
        },
        {
            "property": "properties.deleteRetentionPolicy.days",
            "operator": "greater_than_or_equal",
            "desired_value": 7,
        },
    )


def test_redundancy_assertion_and_verification_use_identical_allowed_values():
    control = ControlRegistry.load(CHECKLIST_PATH, MAPPING_PATH).get("storage.redundancy")

    assert control.assertion["expected"] == control.verification["assertion"]["expected"]
    assert control.assertion["expected"] == control.remediation["allowed_values"]
    assert set(control.assertion["expected"]) == {
        "Standard_ZRS",
        "Standard_GRS",
        "Standard_RAGRS",
        "Standard_GZRS",
        "Standard_RAGZRS",
    }


def test_registry_rejects_custom_or_unsupported_state_rules(tmp_path):
    document = _load_mapping_document()
    document["controls"][0]["state_rules"]["custom_reason"] = "pass"

    with pytest.raises(ValueError, match="state_rules"):
        ControlRegistry.load(CHECKLIST_PATH, _write_mapping(tmp_path, document))


def test_registry_rejects_non_executable_scope_conditions(tmp_path):
    document = _load_mapping_document()
    document["controls"][0]["scope_conditions"]["ignored_metadata"] = "production"

    with pytest.raises(ValueError, match="scope_conditions"):
        ControlRegistry.load(CHECKLIST_PATH, _write_mapping(tmp_path, document))


@pytest.mark.parametrize(
    "assertion",
    [
        {"selector": "properties.flag", "operator": "matches_regex", "expected": "true"},
        {"selector": "properties.flag", "operator": "in", "expected": "Enabled"},
        {"selector": "properties.flag", "operator": "not_in", "expected": "Enabled"},
        {"selector": "properties.days", "operator": "greater_than_or_equal", "expected": True},
        {"selector": "properties.days", "operator": "greater_than_or_equal", "expected": "7"},
        {"selector": "properties.flag", "operator": "exists", "expected": "true"},
        {"all": "not-a-sequence"},
        {"all": []},
        {"all": [{"selector": "", "operator": "equals", "expected": True}]},
    ],
)
def test_registry_rejects_malformed_assertion_schemas_and_operands(tmp_path, assertion):
    document = _load_mapping_document()
    document["controls"][0]["assertion"] = assertion

    with pytest.raises(ValueError, match="assertion"):
        ControlRegistry.load(CHECKLIST_PATH, _write_mapping(tmp_path, document))