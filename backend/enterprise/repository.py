"""Enterprise assessment repository contracts and in-memory implementation."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Mapping, Protocol, Sequence

from enterprise.adapters.base import CollectionFailure
from enterprise.domain import ControlDefinition, EvidenceRecord, Verdict, VerdictState


RUN_STATES = ("running", "completed", "partial", "failed")


@dataclass(frozen=True)
class ControlRecord:
    control_key: str
    version: str
    resource_type: str
    evaluator_kind: str
    definition: Mapping[str, Any]
    definition_hash: str


@dataclass(frozen=True)
class CollectionFailureInput:
    reason_code: str
    source_kind: str
    source_reference: str
    status_code: int | None = None
    retry_after: float | None = None
    detail: str = ""


@dataclass(frozen=True)
class EvidenceProvenance:
    source_kind: str
    source_reference: str
    source_version: str
    observed_at: datetime
    content_hash: str


@dataclass(frozen=True)
class EvaluationVerdictInput:
    resource_id: str
    verdict: Verdict


@dataclass(frozen=True)
class FindingRecord:
    finding_id: str
    run_id: str
    subscription_id: str
    resource_id: str
    control_key: str
    verdict_state: str
    reason_code: str
    evidence_hashes: tuple[str, ...]
    provenance: tuple[EvidenceProvenance, ...]


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    tenant_id: str
    subscription_id: str
    state: str
    requested_resource_ids: tuple[str, ...]
    control_keys: tuple[str, ...]
    started_at: datetime
    completed_at: datetime | None
    reason_code: str | None
    verdict_counts: Mapping[str, int]
    evidence_provenance: tuple[EvidenceProvenance, ...]
    findings: tuple[FindingRecord, ...]
    collection_failures: tuple[CollectionFailureInput, ...]


class EnterpriseRepository(Protocol):
    async def register_controls(self, registry: Mapping[str, ControlDefinition]) -> None: ...

    async def create_run(
        self,
        tenant_id: str,
        subscription_id: str,
        requested_resource_ids: Sequence[str],
        control_keys: Sequence[str],
    ) -> str: ...

    async def complete_run(
        self,
        run_id: str,
        evidence: Sequence[EvidenceRecord],
        verdicts: Sequence[EvaluationVerdictInput],
        collection_failures: Sequence[CollectionFailureInput | CollectionFailure],
    ) -> None: ...

    async def fail_run(self, run_id: str, reason_code: str) -> None: ...

    async def get_run(self, run_id: str, subscription_id: str) -> RunRecord | None: ...

    async def get_finding(self, finding_id: str, subscription_id: str) -> FindingRecord | None: ...

    async def list_controls(self) -> tuple[ControlRecord, ...]: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _freeze_mapping(data: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(data))


def _normalize_collection_failures(
    failures: Sequence[CollectionFailureInput | CollectionFailure],
) -> tuple[CollectionFailureInput, ...]:
    normalized: list[CollectionFailureInput] = []
    for failure in failures:
        if isinstance(failure, CollectionFailureInput):
            normalized.append(failure)
            continue
        normalized.append(
            CollectionFailureInput(
                reason_code=failure.reason_code,
                source_kind=failure.source_kind,
                source_reference=failure.source_reference,
                status_code=failure.status_code,
                retry_after=failure.retry_after,
                detail=failure.detail,
            )
        )
    return tuple(normalized)


class InMemoryEnterpriseRepository(EnterpriseRepository):
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._runs: dict[str, RunRecord] = {}
        self._findings: dict[str, FindingRecord] = {}
        self._controls: dict[tuple[str, str], ControlRecord] = {}

    async def register_controls(self, registry: Mapping[str, ControlDefinition]) -> None:
        with self._lock:
            for control in registry.values():
                record = ControlRecord(
                    control_key=control.key,
                    version=control.version,
                    resource_type=control.resource_type,
                    evaluator_kind=control.evaluator_kind.value,
                    definition=_freeze_mapping(
                        {
                            "key": control.key,
                            "version": control.version,
                            "resource_type": control.resource_type,
                            "evaluator_kind": control.evaluator_kind.value,
                        }
                    ),
                    definition_hash=f"{control.key}:{control.version}",
                )
                self._controls[(record.control_key, record.version)] = record

    async def create_run(
        self,
        tenant_id: str,
        subscription_id: str,
        requested_resource_ids: Sequence[str],
        control_keys: Sequence[str],
    ) -> str:
        with self._lock:
            run_id = str(uuid.uuid4())
            now = _utc_now()
            self._runs[run_id] = RunRecord(
                run_id=run_id,
                tenant_id=tenant_id,
                subscription_id=subscription_id,
                state="running",
                requested_resource_ids=tuple(requested_resource_ids),
                control_keys=tuple(control_keys),
                started_at=now,
                completed_at=None,
                reason_code=None,
                verdict_counts=_freeze_mapping(_empty_verdict_counts()),
                evidence_provenance=(),
                findings=(),
                collection_failures=(),
            )
            return run_id

    async def complete_run(
        self,
        run_id: str,
        evidence: Sequence[EvidenceRecord],
        verdicts: Sequence[EvaluationVerdictInput],
        collection_failures: Sequence[CollectionFailureInput | CollectionFailure],
    ) -> None:
        with self._lock:
            run = self._require_run(run_id)
            self._require_running(run)

            evidence_by_hash = {
                record.content_hash: EvidenceProvenance(
                    source_kind=record.source_kind,
                    source_reference=record.source_reference,
                    source_version=record.source_version,
                    observed_at=record.observed_at,
                    content_hash=record.content_hash,
                )
                for record in evidence
            }
            findings: list[FindingRecord] = []
            counts = _empty_verdict_counts()

            for verdict_input in verdicts:
                verdict_state = verdict_input.verdict.state.value
                counts[verdict_state] += 1
                provenance = tuple(
                    evidence_by_hash[content_hash]
                    for content_hash in verdict_input.verdict.evidence_hashes
                    if content_hash in evidence_by_hash
                )
                finding = FindingRecord(
                    finding_id=str(uuid.uuid4()),
                    run_id=run_id,
                    subscription_id=run.subscription_id,
                    resource_id=verdict_input.resource_id,
                    control_key=verdict_input.verdict.control_key,
                    verdict_state=verdict_state,
                    reason_code=verdict_input.verdict.reason_code,
                    evidence_hashes=tuple(verdict_input.verdict.evidence_hashes),
                    provenance=provenance,
                )
                findings.append(finding)
                self._findings[finding.finding_id] = finding

            normalized_failures = _normalize_collection_failures(collection_failures)
            next_state = "partial" if normalized_failures else "completed"
            self._runs[run_id] = RunRecord(
                run_id=run.run_id,
                tenant_id=run.tenant_id,
                subscription_id=run.subscription_id,
                state=next_state,
                requested_resource_ids=run.requested_resource_ids,
                control_keys=run.control_keys,
                started_at=run.started_at,
                completed_at=_utc_now(),
                reason_code=None,
                verdict_counts=_freeze_mapping(counts),
                evidence_provenance=tuple(evidence_by_hash.values()),
                findings=tuple(findings),
                collection_failures=normalized_failures,
            )

    async def fail_run(self, run_id: str, reason_code: str) -> None:
        with self._lock:
            run = self._require_run(run_id)
            self._require_running(run)
            self._runs[run_id] = RunRecord(
                run_id=run.run_id,
                tenant_id=run.tenant_id,
                subscription_id=run.subscription_id,
                state="failed",
                requested_resource_ids=run.requested_resource_ids,
                control_keys=run.control_keys,
                started_at=run.started_at,
                completed_at=_utc_now(),
                reason_code=reason_code,
                verdict_counts=run.verdict_counts,
                evidence_provenance=run.evidence_provenance,
                findings=run.findings,
                collection_failures=run.collection_failures,
            )

    async def get_run(self, run_id: str, subscription_id: str) -> RunRecord | None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None or run.subscription_id.casefold() != subscription_id.casefold():
                return None
            return run

    async def get_finding(self, finding_id: str, subscription_id: str) -> FindingRecord | None:
        with self._lock:
            finding = self._findings.get(finding_id)
            if finding is None or finding.subscription_id.casefold() != subscription_id.casefold():
                return None
            return finding

    async def list_controls(self) -> tuple[ControlRecord, ...]:
        with self._lock:
            ordered = sorted(self._controls.values(), key=lambda item: (item.control_key, item.version))
            return tuple(ordered)

    def _require_run(self, run_id: str) -> RunRecord:
        run = self._runs.get(run_id)
        if run is None:
            raise KeyError(f"run not found: {run_id}")
        return run

    @staticmethod
    def _require_running(run: RunRecord) -> None:
        if run.state != "running":
            raise ValueError("run is immutable once finalized")

    def _list_runs_for_testing(self) -> list[RunRecord]:
        with self._lock:
            return list(self._runs.values())


def _empty_verdict_counts() -> dict[str, int]:
    return {
        VerdictState.PASS.value: 0,
        VerdictState.FAIL.value: 0,
        VerdictState.UNKNOWN.value: 0,
        VerdictState.NOT_APPLICABLE.value: 0,
        VerdictState.EXEMPTED.value: 0,
        VerdictState.MANUAL_PENDING.value: 0,
    }
