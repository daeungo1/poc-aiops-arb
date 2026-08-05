"""체크리스트와 실행 가능한 control mapping을 결합하는 registry."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from agent.checklist_loader import ChecklistLoader
from enterprise.adapters.advisor import ADVISOR_API_VERSION
from enterprise.adapters.aprl import RESOURCE_GRAPH_API_VERSION, resource_graph_source_version
from enterprise.adapters.arm import ARM_STORAGE_API_VERSION
from enterprise.adapters.defender import DEFENDER_API_VERSION
from enterprise.adapters.policy import POLICY_STATES_API_VERSION
from enterprise.domain import (
    CANONICAL_STATE_RULES,
    ControlDefinition,
    EvaluatorKind,
    EvidenceSource,
    SourceRole,
)

_SUPPORTED_OPERATORS = {
    "equals",
    "in",
    "not_in",
    "greater_than_or_equal",
    "exists",
}


class ControlRegistry:
    def __init__(self, controls: Mapping[str, ControlDefinition]) -> None:
        self.controls = MappingProxyType(dict(controls))

    @classmethod
    def load(cls, checklist_path: Path | str, mapping_path: Path | str) -> ControlRegistry:
        checklist = ChecklistLoader().load_file(checklist_path)
        checklist_keys = cls._checklist_control_keys(checklist)

        with open(mapping_path, encoding="utf-8") as file:
            mapping_document = yaml.safe_load(file)
        if not isinstance(mapping_document, Mapping):
            raise ValueError("mapping document must be a mapping")
        if mapping_document.get("checklist_name") != checklist.name:
            raise ValueError("mapping checklist_name must match the checklist metadata name")

        raw_controls = mapping_document.get("controls")
        if not isinstance(raw_controls, list):
            raise ValueError("mapping controls must be a list")

        controls: dict[str, ControlDefinition] = {}
        for raw_control in raw_controls:
            control = cls._parse_control(raw_control)
            if control.key in controls:
                raise ValueError(f"duplicate mapping control key: {control.key}")
            controls[control.key] = control

        mapping_keys = set(controls)
        if mapping_keys != checklist_keys:
            missing = sorted(checklist_keys - mapping_keys)
            extra = sorted(mapping_keys - checklist_keys)
            raise ValueError(f"mapping keys must match checklist controls; missing={missing}, extra={extra}")
        return cls(controls)

    def get(self, key: str) -> ControlDefinition:
        return self.controls[key]

    @staticmethod
    def _checklist_control_keys(checklist: Any) -> set[str]:
        keys: list[str] = []
        for _, _, check in checklist.get_all_checks():
            key = check.azure_check.get("control_key")
            if not isinstance(key, str) or not key.strip():
                raise ValueError("every checklist check must declare azure_check.control_key")
            keys.append(key)
        if len(keys) != len(set(keys)):
            raise ValueError("checklist control keys must be unique")
        return set(keys)

    @staticmethod
    def _parse_control(raw_control: Any) -> ControlDefinition:
        if not isinstance(raw_control, Mapping):
            raise ValueError("each mapping control must be a mapping")
        try:
            evaluator_kind = EvaluatorKind(raw_control["evaluator_kind"])
            sources = ControlRegistry._parse_sources(raw_control["sources"])
            assertion = raw_control["assertion"]
            ControlRegistry._validate_assertion(assertion)
            scope_conditions = raw_control["scope_conditions"]
            ControlRegistry._validate_scope_conditions(scope_conditions, sources, assertion)
            state_rules = raw_control["state_rules"]
            if not isinstance(state_rules, Mapping) or dict(state_rules) != dict(CANONICAL_STATE_RULES):
                raise ValueError("state_rules must exactly match the canonical evaluator rules")
            verification = raw_control["verification"]
            if not isinstance(verification, Mapping):
                raise ValueError("verification must be a mapping")
            ControlRegistry._validate_assertion(verification.get("assertion"), field_name="verification.assertion")
            return ControlDefinition(
                key=raw_control["key"],
                version=raw_control["version"],
                resource_type=raw_control["resource_type"],
                evaluator_kind=evaluator_kind,
                sources=sources,
                assertion=assertion,
                scope_conditions=scope_conditions,
                state_rules=state_rules,
                remediation=raw_control["remediation"],
                verification=verification,
            )
        except KeyError as exc:
            raise ValueError(f"mapping control is missing required field: {exc.args[0]}") from exc

    @staticmethod
    def _parse_sources(raw_sources: Any) -> tuple[EvidenceSource, ...]:
        if not ControlRegistry._is_sequence(raw_sources) or not raw_sources:
            raise ValueError("sources must be a non-empty sequence")
        sources: list[EvidenceSource] = []
        for raw_source in raw_sources:
            if not isinstance(raw_source, Mapping):
                raise ValueError("each source must be a mapping")
            allowed_fields = {
                "source_kind",
                "reference",
                "version",
                "adapter_config",
                "role",
                "required",
                "verdict_selector",
            }
            if not set(raw_source) <= allowed_fields:
                raise ValueError("source contains unsupported fields")
            try:
                adapter_config = raw_source["adapter_config"]
                ControlRegistry._validate_adapter_config(
                    source_kind=raw_source["source_kind"],
                    version=raw_source["version"],
                    adapter_config=adapter_config,
                )
                sources.append(
                    EvidenceSource(
                        source_kind=raw_source["source_kind"],
                        reference=raw_source["reference"],
                        version=raw_source["version"],
                        adapter_config=adapter_config,
                        role=SourceRole(raw_source["role"]),
                        required=raw_source["required"],
                        verdict_selector=raw_source.get("verdict_selector"),
                    )
                )
            except KeyError as exc:
                raise ValueError(f"source is missing required field: {exc.args[0]}") from exc
        return tuple(sources)

    @staticmethod
    def _validate_assertion(assertion: Any, *, field_name: str = "assertion") -> None:
        if not isinstance(assertion, Mapping) or not assertion:
            raise ValueError(f"{field_name} must be a non-empty mapping")
        if "all" in assertion:
            if set(assertion) != {"all"}:
                raise ValueError(f"{field_name} all assertion contains unsupported fields")
            children = assertion["all"]
            if not ControlRegistry._is_sequence(children) or not children:
                raise ValueError(f"{field_name} all must be a non-empty sequence")
            for child in children:
                ControlRegistry._validate_assertion(child, field_name=field_name)
            return

        if set(assertion) != {"selector", "operator", "expected"}:
            raise ValueError(f"{field_name} leaf must contain selector, operator, and expected")
        selector = assertion["selector"]
        if not isinstance(selector, str) or not selector.strip():
            raise ValueError(f"{field_name} selector must not be empty")
        operator = assertion["operator"]
        if operator not in _SUPPORTED_OPERATORS:
            raise ValueError(f"{field_name} operator is unsupported")
        expected = assertion["expected"]
        if operator in {"in", "not_in"} and (
            not ControlRegistry._is_sequence(expected) or not expected
        ):
            raise ValueError(f"{field_name} {operator} expected must be a non-string sequence")
        if operator == "greater_than_or_equal" and (
            isinstance(expected, bool) or not isinstance(expected, (int, float))
        ):
            raise ValueError(f"{field_name} greater_than_or_equal expected must be numeric")
        if operator == "exists" and not isinstance(expected, bool):
            raise ValueError(f"{field_name} exists expected must be a bool")

    @staticmethod
    def _validate_scope_conditions(
        scope_conditions: Any,
        sources: tuple[EvidenceSource, ...],
        assertion: Mapping[str, Any],
    ) -> None:
        if not isinstance(scope_conditions, Mapping):
            raise ValueError("scope_conditions must be a mapping")
        if set(scope_conditions) != {"same_resource_id", "required_payload_fields"}:
            raise ValueError("scope_conditions contains unsupported or missing fields")
        if scope_conditions["same_resource_id"] is not True:
            raise ValueError("scope_conditions.same_resource_id must be true")
        required_fields = scope_conditions["required_payload_fields"]
        if not isinstance(required_fields, Mapping):
            raise ValueError("scope_conditions.required_payload_fields must be a mapping")
        allowed_roles = {role.value for role in SourceRole}
        if not set(required_fields) <= allowed_roles or "primary" not in required_fields:
            raise ValueError("scope_conditions.required_payload_fields contains unsupported or missing roles")
        for role, selectors in required_fields.items():
            if not ControlRegistry._is_sequence(selectors) or not selectors:
                raise ValueError(f"scope_conditions required fields for {role} must be a non-empty sequence")
            if any(not isinstance(selector, str) or not selector.strip() for selector in selectors):
                raise ValueError(f"scope_conditions required fields for {role} must not contain empty selectors")

        primary_selectors = ControlRegistry._assertion_selectors(assertion)
        if not primary_selectors <= set(required_fields["primary"]):
            raise ValueError("scope_conditions primary fields must include all assertion selectors")
        corroborating = tuple(source for source in sources if source.role is SourceRole.CORROBORATING)
        if corroborating:
            corroborating_fields = set(required_fields.get("corroborating", ()))
            if not corroborating_fields or any(
                source.verdict_selector not in corroborating_fields for source in corroborating
            ):
                raise ValueError("scope_conditions corroborating fields must include all verdict selectors")

    @staticmethod
    def _assertion_selectors(assertion: Mapping[str, Any]) -> set[str]:
        if "all" in assertion:
            return {
                selector
                for child in assertion["all"]
                for selector in ControlRegistry._assertion_selectors(child)
            }
        return {assertion["selector"]}

    @staticmethod
    def _is_sequence(value: Any) -> bool:
        return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))

    @staticmethod
    def _validate_adapter_config(*, source_kind: Any, version: Any, adapter_config: Any) -> None:
        if not isinstance(source_kind, str) or not source_kind.strip():
            raise ValueError("source_kind must not be empty")
        if not isinstance(version, str) or not version.strip():
            raise ValueError("source version must not be empty")
        if not isinstance(adapter_config, Mapping) or not adapter_config:
            raise ValueError("adapter_config must be a non-empty mapping")

        kind = source_kind
        if kind in {"arm", "storage_service"}:
            ControlRegistry._validate_arm_adapter_config(kind, version, adapter_config)
            return
        if kind == "arg":
            ControlRegistry._validate_arg_adapter_config(version, adapter_config)
            return
        if kind == "aprl":
            ControlRegistry._validate_aprl_adapter_config(version, adapter_config)
            return
        if kind == "azure_policy":
            ControlRegistry._validate_policy_adapter_config(version, adapter_config)
            return
        if kind == "defender":
            ControlRegistry._validate_defender_adapter_config(version, adapter_config)
            return
        if kind == "advisor":
            ControlRegistry._validate_advisor_adapter_config(version, adapter_config)
            return
        raise ValueError(f"unsupported source_kind for adapter_config validation: {kind}")

    @staticmethod
    def _validate_arm_adapter_config(source_kind: str, version: str, config: Mapping[str, Any]) -> None:
        if set(config) != {"adapter", "api_version", "resource_detail"}:
            raise ValueError("adapter_config for arm/storage_service must contain adapter, api_version, resource_detail")
        if config["adapter"] != "storage_account":
            raise ValueError("adapter_config adapter must be storage_account for arm/storage_service")
        if config["api_version"] != ARM_STORAGE_API_VERSION or version != ARM_STORAGE_API_VERSION:
            raise ValueError("adapter_config api_version must match ARM storage adapter API version")
        expected_detail = "account" if source_kind == "arm" else "blob_service"
        if config["resource_detail"] != expected_detail:
            raise ValueError("adapter_config resource_detail does not match source_kind")

    @staticmethod
    def _validate_projection(config: Mapping[str, Any]) -> None:
        projection = config.get("projection")
        if not isinstance(projection, Mapping):
            raise ValueError("adapter_config projection must be a mapping")
        if set(projection) != {"resource_id", "resource_type"}:
            raise ValueError("adapter_config projection must exactly contain resource_id and resource_type")
        if any(not isinstance(value, str) or not value.strip() for value in projection.values()):
            raise ValueError("adapter_config projection selectors must be non-empty strings")

    @staticmethod
    def _validate_arg_adapter_config(version: str, config: Mapping[str, Any]) -> None:
        if set(config) != {"adapter", "api_version", "query", "projection"}:
            raise ValueError("adapter_config for arg must contain adapter, api_version, query, projection")
        if config["adapter"] != "resource_graph":
            raise ValueError("adapter_config adapter must be resource_graph for arg")
        if config["api_version"] != RESOURCE_GRAPH_API_VERSION:
            raise ValueError("adapter_config api_version must match ARG API version")
        query = config["query"]
        if not isinstance(query, str) or not query.strip():
            raise ValueError("adapter_config query must be a non-empty string")
        ControlRegistry._validate_projection(config)
        expected_version = resource_graph_source_version(query)
        if version != expected_version:
            raise ValueError("source version query hash must match adapter_config query")

    @staticmethod
    def _validate_aprl_adapter_config(version: str, config: Mapping[str, Any]) -> None:
        if set(config) != {
            "adapter",
            "api_version",
            "query",
            "projection",
            "status_field",
            "pass_status_values",
            "fail_status_values",
        }:
            raise ValueError(
                "adapter_config for aprl must contain adapter, api_version, query, projection, status_field, pass_status_values, fail_status_values"
            )
        if config["adapter"] != "aprl":
            raise ValueError("adapter_config adapter must be aprl for aprl source")
        if config["api_version"] != RESOURCE_GRAPH_API_VERSION:
            raise ValueError("adapter_config api_version must match APRL Resource Graph API version")
        query = config["query"]
        if not isinstance(query, str) or not query.strip():
            raise ValueError("adapter_config query must be a non-empty string")
        ControlRegistry._validate_projection(config)
        if not isinstance(config["status_field"], str) or not config["status_field"].strip():
            raise ValueError("adapter_config status_field must be a non-empty string")
        for field_name in ("pass_status_values", "fail_status_values"):
            values = config[field_name]
            if (
                not ControlRegistry._is_sequence(values)
                or not values
                or any(not isinstance(value, str) or not value.strip() for value in values)
            ):
                raise ValueError(f"adapter_config {field_name} must be a non-empty sequence of strings")
        expected_version = resource_graph_source_version(query)
        if version != expected_version:
            raise ValueError("source version query hash must match adapter_config query")

    @staticmethod
    def _validate_policy_adapter_config(version: str, config: Mapping[str, Any]) -> None:
        if not set(config) <= {
            "adapter",
            "api_version",
            "policy_definition_id",
            "assignment_id",
            "definition_reference_id",
        }:
            raise ValueError("adapter_config for azure_policy contains unsupported keys")
        if set(config) < {"adapter", "api_version", "policy_definition_id"}:
            raise ValueError("adapter_config for azure_policy must contain adapter, api_version, policy_definition_id")
        if config["adapter"] != "azure_policy":
            raise ValueError("adapter_config adapter must be azure_policy for policy sources")
        if config["api_version"] != POLICY_STATES_API_VERSION or version != POLICY_STATES_API_VERSION:
            raise ValueError("adapter_config api_version must match Policy States API version")
        if not isinstance(config["policy_definition_id"], str) or not config["policy_definition_id"].strip():
            raise ValueError("adapter_config policy_definition_id must be a non-empty string")
        for optional_field in ("assignment_id", "definition_reference_id"):
            value = config.get(optional_field)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"adapter_config {optional_field} must be a non-empty string when provided")

    @staticmethod
    def _validate_defender_adapter_config(version: str, config: Mapping[str, Any]) -> None:
        if set(config) != {"adapter", "api_version", "assessment_names"}:
            raise ValueError("adapter_config for defender must contain adapter, api_version, assessment_names")
        if config["adapter"] != "defender":
            raise ValueError("adapter_config adapter must be defender for defender sources")
        if config["api_version"] != DEFENDER_API_VERSION or version != DEFENDER_API_VERSION:
            raise ValueError("adapter_config api_version must match Defender API version")
        names = config["assessment_names"]
        if (
            not ControlRegistry._is_sequence(names)
            or not names
            or any(not isinstance(name, str) or not name.strip() for name in names)
        ):
            raise ValueError("adapter_config assessment_names must be a non-empty sequence of strings")

    @staticmethod
    def _validate_advisor_adapter_config(version: str, config: Mapping[str, Any]) -> None:
        if set(config) != {"adapter", "api_version", "recommendation_type_id"}:
            raise ValueError("adapter_config for advisor must contain adapter, api_version, recommendation_type_id")
        if config["adapter"] != "advisor":
            raise ValueError("adapter_config adapter must be advisor for advisor sources")
        if config["api_version"] != ADVISOR_API_VERSION or version != ADVISOR_API_VERSION:
            raise ValueError("adapter_config api_version must match Advisor API version")
        if not isinstance(config["recommendation_type_id"], str) or not config["recommendation_type_id"].strip():
            raise ValueError("adapter_config recommendation_type_id must be a non-empty string")