"""체크리스트와 실행 가능한 control mapping을 결합하는 registry."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from agent.checklist_loader import ChecklistLoader
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
            allowed_fields = {"source_kind", "reference", "version", "role", "required", "verdict_selector"}
            if not set(raw_source) <= allowed_fields:
                raise ValueError("source contains unsupported fields")
            try:
                sources.append(
                    EvidenceSource(
                        source_kind=raw_source["source_kind"],
                        reference=raw_source["reference"],
                        version=raw_source["version"],
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