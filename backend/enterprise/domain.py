"""결정론 평가에 사용하는 불변 도메인 계약."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping


class VerdictState(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"
    EXEMPTED = "exempted"
    MANUAL_PENDING = "manual_pending"


class EvaluatorKind(str, Enum):
    MANAGED = "managed"
    CUSTOM = "custom"
    AGENT_ASSISTED = "agent_assisted"
    MANUAL = "manual"


class EvidenceStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"


class SourceRole(str, Enum):
    PRIMARY = "primary"
    CORROBORATING = "corroborating"


CANONICAL_STATE_RULES = MappingProxyType(
    {
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
        "corroborating_signal_invalid": "unknown",
        "assertion_operator_unsupported": "unknown",
        "assertion_value_invalid": "unknown",
        "resource_type_unsupported": "not_applicable",
        "valid_exemption": "exempted",
        "manual_evidence_required": "manual_pending",
    }
)


def _require_text(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be empty")


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _json_compatible(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_compatible(item) for item in value]
    return value


def _payload_hash(payload: Mapping[str, Any]) -> str:
    if not isinstance(payload, Mapping):
        raise ValueError("payload must be a mapping")
    try:
        canonical_payload = json.dumps(
            _json_compatible(payload),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("payload must be JSON serializable") from exc
    return hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()


def _is_sha256_hash(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


@dataclass(frozen=True)
class EvidenceSource:
    source_kind: str
    reference: str
    version: str
    role: SourceRole
    required: bool
    verdict_selector: str | None = None

    def __post_init__(self) -> None:
        _require_text("source_kind", self.source_kind)
        _require_text("reference", self.reference)
        _require_text("version", self.version)
        if not isinstance(self.role, SourceRole):
            raise ValueError("role must be a SourceRole")
        if not isinstance(self.required, bool):
            raise ValueError("required must be a bool")
        if self.role is SourceRole.CORROBORATING:
            if self.verdict_selector != "verdict.status":
                raise ValueError("verdict_selector must be 'verdict.status' for corroborating sources")
        elif self.verdict_selector is not None:
            raise ValueError("verdict_selector is only valid for corroborating sources")


@dataclass(frozen=True)
class EvidenceRecord:
    source_kind: str
    source_reference: str
    source_version: str
    resource_id: str
    status: EvidenceStatus
    observed_at: datetime
    payload: Mapping[str, Any]
    content_hash: str

    def __post_init__(self) -> None:
        _require_text("source_kind", self.source_kind)
        _require_text("source_reference", self.source_reference)
        _require_text("source_version", self.source_version)
        _require_text("resource_id", self.resource_id)
        if not isinstance(self.status, EvidenceStatus):
            raise ValueError("status must be an EvidenceStatus")
        if (
            not isinstance(self.observed_at, datetime)
            or self.observed_at.tzinfo is None
            or self.observed_at.utcoffset() is None
        ):
            raise ValueError("observed_at must be timezone-aware")

        expected_hash = _payload_hash(self.payload)
        if not _is_sha256_hash(self.content_hash) or self.content_hash != expected_hash:
            raise ValueError("content_hash must match the canonical payload SHA-256 hash")
        object.__setattr__(self, "payload", _freeze(self.payload))

    @classmethod
    def create(
        cls,
        source_kind: str,
        source_reference: str,
        source_version: str,
        payload: Mapping[str, Any],
        *,
        resource_id: str,
        status: EvidenceStatus,
        observed_at: datetime | None = None,
    ) -> EvidenceRecord:
        return cls(
            source_kind=source_kind,
            source_reference=source_reference,
            source_version=source_version,
            resource_id=resource_id,
            status=status,
            observed_at=observed_at if observed_at is not None else datetime.now(UTC),
            payload=payload,
            content_hash=_payload_hash(payload),
        )


@dataclass(frozen=True)
class ControlDefinition:
    key: str
    version: str
    resource_type: str
    evaluator_kind: EvaluatorKind
    sources: tuple[EvidenceSource, ...]
    assertion: Mapping[str, Any]
    scope_conditions: Mapping[str, Any]
    state_rules: Mapping[str, Any]
    remediation: Mapping[str, Any]
    verification: Mapping[str, Any]

    def __post_init__(self) -> None:
        _require_text("key", self.key)
        _require_text("version", self.version)
        _require_text("resource_type", self.resource_type)
        if not isinstance(self.evaluator_kind, EvaluatorKind):
            raise ValueError("evaluator_kind must be an EvaluatorKind")
        if isinstance(self.sources, (str, bytes)):
            raise ValueError("sources must be a non-empty collection of EvidenceSource values")
        try:
            sources = tuple(self.sources)
        except TypeError as exc:
            raise ValueError("sources must be a non-empty collection of EvidenceSource values") from exc
        if not sources or any(not isinstance(source, EvidenceSource) for source in sources):
            raise ValueError("sources must be a non-empty collection of EvidenceSource values")
        primary_sources = tuple(source for source in sources if source.role is SourceRole.PRIMARY)
        if len(primary_sources) != 1 or not primary_sources[0].required:
            raise ValueError("sources must contain exactly one required primary source")
        object.__setattr__(self, "sources", sources)
        if not isinstance(self.assertion, Mapping) or not self.assertion:
            raise ValueError("assertion must not be empty")
        for field_name in ("assertion", "scope_conditions", "state_rules", "remediation", "verification"):
            value = getattr(self, field_name)
            if not isinstance(value, Mapping):
                raise ValueError(f"{field_name} must be a mapping")
            if field_name in {"state_rules", "remediation", "verification"} and not value:
                raise ValueError(f"{field_name} must not be empty")
            object.__setattr__(self, field_name, _freeze(value))
        if dict(self.state_rules) != dict(CANONICAL_STATE_RULES):
            raise ValueError("state_rules must exactly match the canonical evaluator rules")

    @property
    def primary_source(self) -> EvidenceSource:
        return next(source for source in self.sources if source.role is SourceRole.PRIMARY)

    @property
    def source_kind(self) -> str:
        return self.primary_source.source_kind

    @property
    def source_reference(self) -> str:
        return self.primary_source.reference

    @property
    def source_version(self) -> str:
        return self.primary_source.version


@dataclass(frozen=True)
class Verdict:
    control_key: str
    state: VerdictState
    reason_code: str
    evidence_hashes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text("control_key", self.control_key)
        _require_text("reason_code", self.reason_code)
        if not isinstance(self.state, VerdictState):
            raise ValueError("state must be a VerdictState")
        if isinstance(self.evidence_hashes, str):
            raise ValueError("evidence_hashes must be a collection of SHA-256 hashes")
        try:
            evidence_hashes = tuple(self.evidence_hashes)
        except TypeError as exc:
            raise ValueError("evidence_hashes must be a collection of SHA-256 hashes") from exc
        if any(not _is_sha256_hash(content_hash) for content_hash in evidence_hashes):
            raise ValueError("evidence_hashes must contain only SHA-256 hashes")
        object.__setattr__(self, "evidence_hashes", evidence_hashes)
        if self.state in {VerdictState.PASS, VerdictState.FAIL} and not self.evidence_hashes:
            raise ValueError("evidence_hashes are required for pass and fail verdicts")


@dataclass(frozen=True)
class EvaluationRun:
    run_id: str
    started_at: datetime
    verdicts: tuple[Verdict, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _require_text("run_id", self.run_id)
        object.__setattr__(self, "verdicts", tuple(self.verdicts))