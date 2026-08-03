"""정규화된 evidence에 대해 결정론 verdict를 계산한다."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from enterprise.domain import (
    ControlDefinition,
    EvidenceRecord,
    EvidenceSource,
    EvidenceStatus,
    SourceRole,
    Verdict,
    VerdictState,
)


_MISSING = object()


class _AssertionInvalid(ValueError):
    pass


class _SelectorMissing(ValueError):
    pass


class _ValueUnknown(ValueError):
    pass


class _UnsupportedOperator(ValueError):
    pass


class DeterministicEvaluator:
    SUPPORTED_OPERATORS = {
        "equals",
        "in",
        "not_in",
        "greater_than_or_equal",
        "exists",
    }

    def evaluate(
        self,
        control: ControlDefinition,
        evidence: Iterable[EvidenceRecord],
    ) -> Verdict:
        records = tuple(evidence)
        if not records:
            return self._verdict(control, "evidence_missing")

        by_source = {
            source: tuple(record for record in records if self._matches_source(record, source))
            for source in control.sources
        }
        primary_source = control.primary_source
        if not by_source[primary_source]:
            return self._verdict(control, "evidence_missing")

        expected_resource_type = control.resource_type.casefold()
        supported_resource_ids = {
            record.resource_id
            for record in by_source[primary_source]
            if self._resource_type(record) == expected_resource_type
        }
        if not supported_resource_ids:
            if any(self._resource_type(record) is None for record in by_source[primary_source]):
                return self._verdict(
                    control,
                    "evidence_resource_type_missing",
                    by_source[primary_source],
                )
            return self._verdict(control, "resource_type_unsupported")

        target_records = tuple(
            record
            for source in control.sources
            for record in by_source[source]
            if record.resource_id in supported_resource_ids
        )
        target_resource_types = tuple(self._resource_type(record) for record in target_records)
        if any(resource_type is None for resource_type in target_resource_types):
            return self._verdict(
                control,
                "evidence_resource_type_missing",
                target_records,
            )
        if any(resource_type != expected_resource_type for resource_type in target_resource_types):
            return self._verdict(control, "evidence_scope_conflict", target_records)

        applicable_by_source = {
            source: tuple(
                record
                for record in source_records
                if self._resource_type(record) == expected_resource_type
            )
            for source, source_records in by_source.items()
        }
        for source, source_records in applicable_by_source.items():
            if source.required and not source_records:
                return self._verdict(control, "evidence_missing")

        applicable = tuple(
            record
            for source in control.sources
            for record in applicable_by_source[source]
        )
        if any(record.status is EvidenceStatus.PARTIAL for record in applicable):
            return self._verdict(control, "evidence_partial", applicable)

        resource_ids = {record.resource_id for record in applicable}
        if len(resource_ids) != 1:
            return self._verdict(control, "evidence_scope_conflict", applicable)

        if not self._scope_is_complete(control, applicable_by_source):
            return self._verdict(control, "evidence_scope_incomplete", applicable)

        try:
            primary_signals = tuple(
                self._evaluate_assertion(control.assertion, record.payload)
                for record in applicable_by_source[primary_source]
            )
        except _UnsupportedOperator:
            return self._verdict(control, "assertion_operator_unsupported", applicable)
        except _SelectorMissing:
            return self._verdict(control, "evidence_selector_missing", applicable)
        except _ValueUnknown:
            return self._verdict(control, "evidence_value_unknown", applicable)
        except (TypeError, ValueError, KeyError, AttributeError):
            return self._verdict(control, "assertion_value_invalid", applicable)

        if len(set(primary_signals)) != 1:
            return self._verdict(control, "evidence_conflict", applicable)
        primary_signal = primary_signals[0]

        try:
            corroborating_signals = tuple(
                self._corroborating_signal(source, record)
                for source in control.sources
                if source.role is SourceRole.CORROBORATING
                for record in applicable_by_source[source]
            )
        except (TypeError, ValueError):
            return self._verdict(control, "corroborating_signal_invalid", applicable)
        if any(signal is not primary_signal for signal in corroborating_signals):
            return self._verdict(control, "managed_source_conflict", applicable)

        return self._verdict(
            control,
            "assertion_matched" if primary_signal else "assertion_not_matched",
            applicable,
        )

    @staticmethod
    def resolve_selector(payload: Mapping[str, Any], selector: str) -> Any:
        return DeterministicEvaluator._resolve(payload, selector)

    @staticmethod
    def _resolve(payload: Mapping[str, Any], selector: str) -> Any:
        value: Any = payload
        for segment in selector.split("."):
            if not isinstance(value, Mapping) or segment not in value:
                return _MISSING
            value = value[segment]
        return value

    @staticmethod
    def _resource_type(record: EvidenceRecord) -> str | None:
        resource_type = record.payload.get("resource_type")
        if not isinstance(resource_type, str) or not resource_type.strip():
            return None
        return resource_type.casefold()

    @staticmethod
    def _matches_source(record: EvidenceRecord, source: EvidenceSource) -> bool:
        return (
            record.source_kind == source.source_kind
            and record.source_reference == source.reference
            and record.source_version == source.version
        )

    @staticmethod
    def _is_sequence(value: Any) -> bool:
        return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))

    @classmethod
    def _evaluate_assertion(cls, assertion: Mapping[str, Any], payload: Mapping[str, Any]) -> bool:
        if not isinstance(assertion, Mapping) or not assertion:
            raise _AssertionInvalid("assertion must be a non-empty mapping")
        if "all" in assertion:
            if set(assertion) != {"all"}:
                raise _AssertionInvalid("all assertion contains unsupported fields")
            children = assertion["all"]
            if not cls._is_sequence(children) or not children:
                raise _AssertionInvalid("all assertion must contain a non-empty sequence")
            results = tuple(cls._evaluate_assertion(child, payload) for child in children)
            return all(results)
        if set(assertion) != {"selector", "operator", "expected"}:
            raise _AssertionInvalid("assertion leaf is malformed")
        selector = assertion["selector"]
        if not isinstance(selector, str) or not selector.strip():
            raise _AssertionInvalid("assertion selector is invalid")
        operator = assertion["operator"]
        if operator not in cls.SUPPORTED_OPERATORS:
            raise _UnsupportedOperator(operator)
        actual = cls._resolve(payload, selector)
        if actual is _MISSING:
            raise _SelectorMissing(selector)
        if actual is None and operator != "exists":
            raise _ValueUnknown(selector)
        return cls._matches(operator, actual, assertion["expected"])

    @classmethod
    def _matches(cls, operator: str, actual: Any, expected: Any) -> bool:
        if operator == "equals":
            return type(actual) is type(expected) and actual == expected
        if operator == "in":
            if not cls._is_sequence(expected) or not expected:
                raise _AssertionInvalid("in expected must be a non-string sequence")
            return any(type(actual) is type(candidate) and actual == candidate for candidate in expected)
        if operator == "not_in":
            if not cls._is_sequence(expected) or not expected:
                raise _AssertionInvalid("not_in expected must be a non-string sequence")
            return not any(type(actual) is type(candidate) and actual == candidate for candidate in expected)
        if operator == "greater_than_or_equal":
            if (
                isinstance(actual, bool)
                or not isinstance(actual, (int, float))
                or isinstance(expected, bool)
                or not isinstance(expected, (int, float))
            ):
                raise _AssertionInvalid("greater_than_or_equal values must be numeric")
            return actual >= expected
        if operator == "exists":
            if not isinstance(expected, bool):
                raise ValueError("exists assertion must be boolean")
            return (actual is not None) is expected
        raise _UnsupportedOperator(operator)

    @classmethod
    def _scope_is_complete(
        cls,
        control: ControlDefinition,
        applicable_by_source: Mapping[EvidenceSource, tuple[EvidenceRecord, ...]],
    ) -> bool:
        try:
            required_fields = control.scope_conditions["required_payload_fields"]
            for source, records in applicable_by_source.items():
                selectors = required_fields.get(source.role.value, ())
                if not cls._is_sequence(selectors):
                    return False
                for record in records:
                    if any(cls._resolve(record.payload, selector) is _MISSING for selector in selectors):
                        return False
            return True
        except (TypeError, KeyError, AttributeError):
            return False

    @classmethod
    def _corroborating_signal(cls, source: EvidenceSource, record: EvidenceRecord) -> bool:
        signal = cls._resolve(record.payload, source.verdict_selector or "")
        if signal == VerdictState.PASS.value:
            return True
        if signal == VerdictState.FAIL.value:
            return False
        raise ValueError("corroborating verdict status must be pass or fail")

    @staticmethod
    def _verdict(
        control: ControlDefinition,
        reason_code: str,
        evidence: Iterable[EvidenceRecord] = (),
    ) -> Verdict:
        try:
            state = VerdictState(control.state_rules[reason_code])
        except (KeyError, TypeError, ValueError):
            state = VerdictState.UNKNOWN
        return Verdict(
            control_key=control.key,
            state=state,
            reason_code=reason_code,
            evidence_hashes=tuple(dict.fromkeys(record.content_hash for record in evidence)),
        )