"""합성 fixture로 coverage spike의 감사 가능한 gate report를 생성한다."""

from __future__ import annotations

import errno
import hashlib
import json
import math
import os
import re
import stat
import tempfile
import time
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from filelock import FileLock

from enterprise.domain import (
    ControlDefinition,
    EvaluatorKind,
    EvidenceRecord,
    EvidenceStatus,
    SourceRole,
    VerdictState,
)
from enterprise.evaluator import DeterministicEvaluator


MANAGED_SOURCE_KINDS = frozenset({"aprl", "advisor", "defender", "azure_policy"})
CUSTOM_ASSERTION_SOURCE_KINDS = frozenset({"arm", "arg", "storage_service"})
MACHINE_VERIFIABLE_KINDS = frozenset({EvaluatorKind.MANAGED, EvaluatorKind.CUSTOM})
MANAGED_SOURCE_CONFLICT_REASON_CODE = "managed_source_conflict"
ALL_CONFLICT_REASON_CODES = frozenset(
    {"evidence_conflict", "evidence_scope_conflict", MANAGED_SOURCE_CONFLICT_REASON_CODE}
)
VALIDATION_MODE = "synthetic_fixture"
REPORT_MANIFEST_SCHEMA_VERSION = 1
REPORT_JSON_FILENAME = "coverage-summary.json"
REPORT_MARKDOWN_FILENAME = "coverage-summary.md"
CURRENT_MANIFEST_FILENAME = "current.json"
STAGING_DIRECTORY_NAME = ".staging"
PUBLISH_LOCK_FILENAME = ".publish.lock"
PUBLISH_LOCK_TIMEOUT_SECONDS = 10.0
GENERATION_RENAME_MAX_ATTEMPTS = 8
GENERATION_RENAME_RETRY_DELAY_SECONDS = 0.01
MANIFEST_REPLACE_MAX_ATTEMPTS = 8
MANIFEST_REPLACE_RETRY_DELAY_SECONDS = 0.01
MANIFEST_STAGING_PREFIX = "current."
MANIFEST_STAGING_SUFFIX = ".tmp"
MANIFEST_STAGING_NAME_PATTERN = re.compile(
    r"\Acurrent\.[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.tmp\Z"
)


class DirectoryFsyncUnsupportedError(OSError):
    """현재 platform/filesystem이 directory durability sync를 지원하지 않는다."""


class ReportPublicationDurabilityError(OSError):
    """Manifest 교체 후 destination directory durability sync가 실패했다."""


@dataclass(frozen=True)
class ReportBundle:
    generation_id: str
    current_manifest_path: Path
    json_path: Path
    markdown_path: Path
    json_sha256: str
    markdown_sha256: str
    json_content: str
    markdown_content: str
    json_data: Mapping[str, Any]


@dataclass(frozen=True)
class GateError:
    code: str
    message: str
    details: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "details": dict(sorted(self.details.items())),
            "message": self.message,
        }


@dataclass(frozen=True)
class ImplementationGate:
    status: str
    conditions: Mapping[str, bool]

    def to_dict(self) -> dict[str, Any]:
        return {
            "conditions": dict(sorted(self.conditions.items())),
            "status": self.status,
        }


@dataclass(frozen=True)
class UnmetCondition:
    code: str
    description: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "description": self.description}


@dataclass(frozen=True)
class DeploymentReadiness:
    status: str
    unmet_conditions: tuple[UnmetCondition, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "unmet_conditions": [condition.to_dict() for condition in self.unmet_conditions],
        }


DEPLOYMENT_UNMET_CONDITIONS = (
    UnmetCondition(
        code="live_azure_adapter_api_validation",
        description="Live Azure adapters and API behavior have not been validated.",
    ),
    UnmetCondition(
        code="rbac_api_limitations_validated",
        description="Required RBAC and Azure API limitations are not yet documented and validated.",
    ),
    UnmetCondition(
        code="human_mapping_verdict_review",
        description="Human review of source mappings and verdicts is pending.",
    ),
    UnmetCondition(
        code="ui_contract_approval",
        description="The UI contract has not been approved.",
    ),
)


@dataclass(frozen=True)
class CoverageMetric:
    count: int
    denominator: int

    def __post_init__(self) -> None:
        if self.count < 0 or self.denominator < 0 or self.count > self.denominator:
            raise ValueError("coverage metric requires 0 <= count <= denominator")

    @property
    def ratio(self) -> float:
        return self.count / self.denominator if self.denominator else 0.0

    def to_dict(self) -> dict[str, int | float]:
        return {
            "count": self.count,
            "denominator": self.denominator,
            "ratio": self.ratio,
        }


@dataclass(frozen=True)
class SourceCoverage:
    source_kind: str
    reference: str
    version: str
    role: str
    required: bool

    def to_dict(self) -> dict[str, str | bool]:
        return {
            "reference": self.reference,
            "required": self.required,
            "role": self.role,
            "source_kind": self.source_kind,
            "version": self.version,
        }


@dataclass(frozen=True)
class ControlCoverage:
    key: str
    evaluator_kind: str
    machine_verifiable: bool
    managed_source_covered: bool
    custom_assertion_covered: bool
    sources: tuple[SourceCoverage, ...]
    unmapped_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluator_kind": self.evaluator_kind,
            "key": self.key,
            "machine_verifiable": self.machine_verifiable,
            "managed_source_covered": self.managed_source_covered,
            "custom_assertion_covered": self.custom_assertion_covered,
            "sources": [source.to_dict() for source in self.sources],
            "unmapped_reason": self.unmapped_reason,
        }


@dataclass(frozen=True)
class FixtureVerdictCoverage:
    state: str
    reason_code: str
    expected_state: str | None = None
    expected_reason_code: str | None = None
    matches_expected: bool | None = None

    def to_dict(self) -> dict[str, str | bool | None]:
        return {
            "expected_reason_code": self.expected_reason_code,
            "expected_state": self.expected_state,
            "matches_expected": self.matches_expected,
            "reason_code": self.reason_code,
            "state": self.state,
        }


@dataclass(frozen=True)
class FixtureCoverage:
    fixture_id: str
    classification: str
    comparable: bool
    verdicts: Mapping[str, FixtureVerdictCoverage]
    mismatch_count: int
    expected_comparisons: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification,
            "comparable": self.comparable,
            "expected_comparisons": self.expected_comparisons,
            "fixture_id": self.fixture_id,
            "mismatch_count": self.mismatch_count,
            "verdicts": {
                key: verdict.to_dict() for key, verdict in sorted(self.verdicts.items())
            },
        }


@dataclass(frozen=True)
class CoverageReport:
    validation_mode: str
    total_controls: int
    evaluator_kind_counts: Mapping[str, int]
    machine_verifiable: CoverageMetric
    managed_source_coverage: CoverageMetric
    custom_evaluator_coverage: CoverageMetric
    custom_assertion_coverage: CoverageMetric
    agent_assisted: CoverageMetric
    manual: CoverageMetric
    controls: tuple[ControlCoverage, ...]
    fixtures: Mapping[str, FixtureCoverage]
    verdict_state_counts: Mapping[str, int]
    unknown: CoverageMetric
    managed_source_conflicts: CoverageMetric
    all_conflicts: CoverageMetric
    fixture_mismatches: CoverageMetric
    validation_errors: tuple[str, ...]
    gate_errors: tuple[GateError, ...]
    implementation_gate: ImplementationGate
    deployment_readiness: DeploymentReadiness

    @property
    def internal_totals_valid(self) -> bool:
        return not self.validation_errors

    def to_dict(self) -> dict[str, Any]:
        verdicts_evaluated = sum(self.verdict_state_counts.values())
        return {
            "control_coverage": {
                "agent_assisted": self.agent_assisted.to_dict(),
                "custom_assertion": self.custom_assertion_coverage.to_dict(),
                "custom_evaluator": self.custom_evaluator_coverage.to_dict(),
                "machine_verifiable": self.machine_verifiable.to_dict(),
                "managed_source": self.managed_source_coverage.to_dict(),
                "manual": self.manual.to_dict(),
            },
            "controls": [control.to_dict() for control in self.controls],
            "evaluator_kind_counts": dict(sorted(self.evaluator_kind_counts.items())),
            "fixture_summary": {
                "all_conflicts": self.all_conflicts.to_dict(),
                "fixtures_evaluated": len(self.fixtures),
                "mismatches": self.fixture_mismatches.to_dict(),
                "managed_source_conflicts": self.managed_source_conflicts.to_dict(),
                "unknown": self.unknown.to_dict(),
                "verdicts_evaluated": verdicts_evaluated,
            },
            "fixtures": {
                key: fixture.to_dict() for key, fixture in sorted(self.fixtures.items())
            },
            "internal_totals": {
                "errors": list(self.validation_errors),
                "valid": self.internal_totals_valid,
            },
            "deployment_readiness": self.deployment_readiness.to_dict(),
            "gate_errors": [error.to_dict() for error in self.gate_errors],
            "implementation_gate": self.implementation_gate.to_dict(),
            "total_controls": self.total_controls,
            "validation_mode": self.validation_mode,
            "verdict_state_counts": dict(sorted(self.verdict_state_counts.items())),
        }

    def to_markdown(self) -> str:
        lines = [
            "# Coverage Spike Summary",
            "",
            "## Gate",
            "",
            "| Check | Result |",
            "| --- | --- |",
            f"| Validation mode | {self.validation_mode} |",
            f"| Implementation gate | {self.implementation_gate.status} |",
            f"| Deployment readiness | {self.deployment_readiness.status} |",
            f"| Internal totals | {'valid' if self.internal_totals_valid else 'invalid'} |",
            (
                "| Fixture mismatches | "
                f"{self.fixture_mismatches.count} / {self.fixture_mismatches.denominator} "
                f"({_format_ratio(self.fixture_mismatches.ratio)}) |"
            ),
            "",
            (
                "Synthetic fixture evidence validates the implementation gate only; "
                "it does not validate live Azure adapters or APIs."
            ),
            "",
            "### Deployment Readiness Unmet Conditions",
            "",
        ]
        lines.extend(
            f"- `{condition.code}`: {condition.description}"
            for condition in self.deployment_readiness.unmet_conditions
        )
        lines.extend(
            [
            "",
            "## Control Coverage",
            "",
            "| Category | Count | Denominator | Ratio |",
            "| --- | ---: | ---: | ---: |",
            ]
        )
        control_metrics = (
            ("Machine-verifiable", self.machine_verifiable),
            ("Managed source", self.managed_source_coverage),
            ("Custom evaluator", self.custom_evaluator_coverage),
            ("Custom assertion", self.custom_assertion_coverage),
            ("Agent-assisted", self.agent_assisted),
            ("Manual", self.manual),
        )
        lines.extend(
            f"| {name} | {metric.count} | {metric.denominator} | {_format_ratio(metric.ratio)} |"
            for name, metric in control_metrics
        )
        lines.extend(
            [
                "",
                (
                    "Custom assertion coverage and managed source coverage overlap: "
                    "a control can execute a local ARM/ARG/storage-service assertion and also "
                    "carry a corroborating managed-source mapping."
                ),
            ]
        )
        lines.extend(
            [
                "",
                "## Evaluator Kinds",
                "",
                "| Evaluator kind | Count |",
                "| --- | ---: |",
            ]
        )
        lines.extend(
            f"| {kind} | {count} |" for kind, count in sorted(self.evaluator_kind_counts.items())
        )
        lines.extend(
            [
                "",
                "## Controls",
                "",
                "| Control | Evaluator | Machine-verifiable | Custom assertion | Managed source | Sources | Unmapped reason |",
                "| --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for control in self.controls:
            sources = "<br>".join(
                _escape_markdown(
                    f"{source.source_kind}:{source.reference}@{source.version} "
                    f"({source.role}, {'required' if source.required else 'optional'})"
                )
                for source in control.sources
            )
            lines.append(
                "| "
                f"{_escape_markdown(control.key)} | {control.evaluator_kind} | "
                f"{'yes' if control.machine_verifiable else 'no'} | "
                f"{'yes' if control.custom_assertion_covered else 'no'} | "
                f"{'yes' if control.managed_source_covered else 'no'} | {sources} | "
                f"{_escape_markdown(control.unmapped_reason or '-')} |"
            )
        lines.extend(
            [
                "",
                "## Fixture Verdicts",
                "",
                "| Fixture | Control | State | Reason | Expected | Match |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        for fixture_name, fixture in sorted(self.fixtures.items()):
            for control_key, verdict in sorted(fixture.verdicts.items()):
                expected = (
                    f"{verdict.expected_state} / {verdict.expected_reason_code}"
                    if verdict.matches_expected is not None
                    else "not compared"
                )
                match = (
                    "yes"
                    if verdict.matches_expected is True
                    else "no"
                    if verdict.matches_expected is False
                    else "-"
                )
                lines.append(
                    "| "
                    f"{_escape_markdown(fixture_name)} | {_escape_markdown(control_key)} | "
                    f"{verdict.state} | {_escape_markdown(verdict.reason_code)} | "
                    f"{_escape_markdown(expected)} | {match} |"
                )
        lines.extend(
            [
                "",
                "## Verdict Outcomes",
                "",
                "| State | Count |",
                "| --- | ---: |",
            ]
        )
        lines.extend(
            f"| {state} | {count} |" for state, count in sorted(self.verdict_state_counts.items())
        )
        lines.extend(
            [
                "",
                (
                    "Conflict reasons are an orthogonal subset of verdict outcomes: "
                    f"managed-source conflicts {self.managed_source_conflicts.count} / "
                    f"{self.managed_source_conflicts.denominator} "
                    f"({_format_ratio(self.managed_source_conflicts.ratio)}); all conflicts "
                    f"{self.all_conflicts.count} / {self.all_conflicts.denominator} "
                    f"({_format_ratio(self.all_conflicts.ratio)})."
                ),
            ]
        )
        if self.gate_errors:
            lines.extend(["", "## Gate Errors", ""])
            lines.extend(
                f"- `{error.code}`: {_escape_markdown(error.message)}"
                for error in self.gate_errors
            )
        if self.validation_errors:
            lines.extend(["", "## Validation Errors", ""])
            lines.extend(f"- {_escape_markdown(error)}" for error in self.validation_errors)
        return "\n".join(lines) + "\n"


def build_coverage_report(
    controls: Mapping[str, ControlDefinition],
    *,
    fixtures_dir: Path | str | None = None,
    expected_dir: Path | str | None = None,
) -> CoverageReport:
    ordered_controls = tuple(sorted(controls.values(), key=lambda control: control.key))
    evaluator_kind_counts = {kind.value: 0 for kind in EvaluatorKind}
    evaluator_kind_counts.update(
        Counter(control.evaluator_kind.value for control in ordered_controls)
    )
    total_controls = len(ordered_controls)
    control_rows = tuple(_control_coverage(control) for control in ordered_controls)
    machine_count = sum(control.machine_verifiable for control in control_rows)
    managed_source_count = sum(control.managed_source_covered for control in control_rows)
    custom_assertion_count = sum(control.custom_assertion_covered for control in control_rows)

    fixtures: dict[str, FixtureCoverage] = {}
    gate_errors: tuple[GateError, ...]
    if fixtures_dir is not None:
        fixtures, gate_errors = _evaluate_fixtures(
            ordered_controls,
            Path(fixtures_dir),
            Path(expected_dir) if expected_dir is not None else None,
        )
    else:
        gate_errors = (
            GateError(
                code="oracle_not_evaluated",
                message="Fixture oracle completeness was not evaluated.",
                details={},
            ),
        )

    verdict_state_counts = {state.value: 0 for state in VerdictState}
    all_verdicts = [
        verdict
        for fixture in fixtures.values()
        for verdict in fixture.verdicts.values()
    ]
    verdict_state_counts.update(Counter(verdict.state for verdict in all_verdicts))
    verdict_count = len(all_verdicts)
    mismatch_count = sum(fixture.mismatch_count for fixture in fixtures.values())
    expected_comparisons = sum(fixture.expected_comparisons for fixture in fixtures.values())
    validation_errors = _validate_totals(
        total_controls=total_controls,
        evaluator_kind_counts=evaluator_kind_counts,
        machine_count=machine_count,
        custom_count=evaluator_kind_counts[EvaluatorKind.CUSTOM.value],
        managed_count=evaluator_kind_counts[EvaluatorKind.MANAGED.value],
        custom_assertion_count=custom_assertion_count,
        control_rows=control_rows,
        fixtures=fixtures,
        verdict_state_counts=verdict_state_counts,
        verdict_count=verdict_count,
        mismatch_count=mismatch_count,
        expected_comparisons=expected_comparisons,
    )
    implementation_conditions = {
        "internal_totals_valid": not validation_errors,
        "oracle_complete": not gate_errors,
        "oracle_has_comparisons": expected_comparisons > 0,
        "zero_fixture_mismatches": mismatch_count == 0,
    }
    implementation_gate = ImplementationGate(
        status="passed" if all(implementation_conditions.values()) else "failed",
        conditions=implementation_conditions,
    )
    return CoverageReport(
        validation_mode=VALIDATION_MODE,
        total_controls=total_controls,
        evaluator_kind_counts=dict(sorted(evaluator_kind_counts.items())),
        machine_verifiable=CoverageMetric(machine_count, total_controls),
        managed_source_coverage=CoverageMetric(managed_source_count, total_controls),
        custom_evaluator_coverage=CoverageMetric(
            evaluator_kind_counts[EvaluatorKind.CUSTOM.value], total_controls
        ),
        custom_assertion_coverage=CoverageMetric(custom_assertion_count, total_controls),
        agent_assisted=CoverageMetric(
            evaluator_kind_counts[EvaluatorKind.AGENT_ASSISTED.value], total_controls
        ),
        manual=CoverageMetric(evaluator_kind_counts[EvaluatorKind.MANUAL.value], total_controls),
        controls=control_rows,
        fixtures=fixtures,
        verdict_state_counts=dict(sorted(verdict_state_counts.items())),
        unknown=CoverageMetric(verdict_state_counts[VerdictState.UNKNOWN.value], verdict_count),
        managed_source_conflicts=CoverageMetric(
            sum(
                verdict.reason_code == MANAGED_SOURCE_CONFLICT_REASON_CODE
                for verdict in all_verdicts
            ),
            verdict_count,
        ),
        all_conflicts=CoverageMetric(
            sum(verdict.reason_code in ALL_CONFLICT_REASON_CODES for verdict in all_verdicts),
            verdict_count,
        ),
        fixture_mismatches=CoverageMetric(mismatch_count, expected_comparisons),
        validation_errors=validation_errors,
        gate_errors=gate_errors,
        implementation_gate=implementation_gate,
        deployment_readiness=DeploymentReadiness(
            status="blocked",
            unmet_conditions=DEPLOYMENT_UNMET_CONDITIONS,
        ),
    )


def write_coverage_reports(
    report: CoverageReport,
    reports_dir: Path | str,
    *,
    lock_timeout_seconds: float = PUBLISH_LOCK_TIMEOUT_SECONDS,
) -> ReportBundle:
    if not math.isfinite(lock_timeout_seconds) or lock_timeout_seconds < 0:
        raise ValueError("publication lock timeout must be finite non-negative seconds")
    output_dir = Path(reports_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    with FileLock(
        str(output_dir / PUBLISH_LOCK_FILENAME),
        timeout=lock_timeout_seconds,
    ):
        return _write_coverage_reports_locked(report, output_dir)


def _write_coverage_reports_locked(
    report: CoverageReport,
    output_dir: Path,
) -> ReportBundle:
    _scavenge_staging(output_dir)
    json_content = json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    markdown_content = report.to_markdown()
    json_sha256 = _sha256(json_content.encode("utf-8"))
    markdown_sha256 = _sha256(markdown_content.encode("utf-8"))
    generation_id = _generation_id(json_sha256, markdown_sha256)
    generation_dir = output_dir / "generations" / generation_id
    contents = {
        REPORT_JSON_FILENAME: (json_content, json_sha256),
        REPORT_MARKDOWN_FILENAME: (markdown_content, markdown_sha256),
    }
    _ensure_generation(generation_dir, contents)

    manifest = {
        "generation_id": generation_id,
        "reports": {
            "json": {
                "path": f"generations/{generation_id}/{REPORT_JSON_FILENAME}",
                "sha256": json_sha256,
            },
            "markdown": {
                "path": f"generations/{generation_id}/{REPORT_MARKDOWN_FILENAME}",
                "sha256": markdown_sha256,
            },
        },
        "schema_version": REPORT_MANIFEST_SCHEMA_VERSION,
    }
    current_manifest_path = output_dir / CURRENT_MANIFEST_FILENAME
    _replace_current_manifest(
        current_manifest_path,
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return ReportBundle(
        generation_id=generation_id,
        current_manifest_path=current_manifest_path,
        json_path=generation_dir / REPORT_JSON_FILENAME,
        markdown_path=generation_dir / REPORT_MARKDOWN_FILENAME,
        json_sha256=json_sha256,
        markdown_sha256=markdown_sha256,
        json_content=json_content,
        markdown_content=markdown_content,
        json_data=report.to_dict(),
    )


def read_current_report_bundle(reports_dir: Path | str) -> ReportBundle:
    output_dir = Path(reports_dir)
    current_manifest_path = output_dir / CURRENT_MANIFEST_FILENAME
    manifest = _load_json_mapping(current_manifest_path)
    if manifest.get("schema_version") != REPORT_MANIFEST_SCHEMA_VERSION:
        raise ValueError("current report manifest schema_version is invalid")

    generation_id = manifest.get("generation_id")
    if not _is_sha256(generation_id):
        raise ValueError("current report manifest generation_id is invalid")
    reports = manifest.get("reports")
    if not isinstance(reports, Mapping) or set(reports) != {"json", "markdown"}:
        raise ValueError("current report manifest reports are invalid")

    report_entries: dict[str, tuple[Path, str]] = {}
    expected_filenames = {
        "json": REPORT_JSON_FILENAME,
        "markdown": REPORT_MARKDOWN_FILENAME,
    }
    for report_kind, filename in expected_filenames.items():
        entry = reports.get(report_kind)
        if not isinstance(entry, Mapping):
            raise ValueError(f"current report manifest {report_kind} entry is invalid")
        expected_relative_path = f"generations/{generation_id}/{filename}"
        if entry.get("path") != expected_relative_path:
            raise ValueError(
                f"current report manifest {report_kind} path does not match generation_id"
            )
        content_hash = entry.get("sha256")
        if not _is_sha256(content_hash):
            raise ValueError(f"current report manifest {report_kind} hash is invalid")
        report_entries[report_kind] = (output_dir / Path(expected_relative_path), content_hash)

    json_path, json_sha256 = report_entries["json"]
    markdown_path, markdown_sha256 = report_entries["markdown"]
    try:
        json_bytes = json_path.read_bytes()
        markdown_bytes = markdown_path.read_bytes()
    except OSError as exc:
        raise ValueError("current report bundle content could not be read") from exc
    if _sha256(json_bytes) != json_sha256:
        raise ValueError("current report bundle JSON hash does not match manifest")
    if _sha256(markdown_bytes) != markdown_sha256:
        raise ValueError("current report bundle Markdown hash does not match manifest")
    if _generation_id(json_sha256, markdown_sha256) != generation_id:
        raise ValueError("current report bundle generation_id does not match content hashes")

    try:
        json_content = json_bytes.decode("utf-8")
        markdown_content = markdown_bytes.decode("utf-8")
        json_data = json.loads(json_content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("current report bundle content is malformed") from exc
    if not isinstance(json_data, dict):
        raise ValueError("current report bundle JSON must be an object")
    return ReportBundle(
        generation_id=generation_id,
        current_manifest_path=current_manifest_path,
        json_path=json_path,
        markdown_path=markdown_path,
        json_sha256=json_sha256,
        markdown_sha256=markdown_sha256,
        json_content=json_content,
        markdown_content=markdown_content,
        json_data=json_data,
    )


def _control_coverage(control: ControlDefinition) -> ControlCoverage:
    machine_verifiable = control.evaluator_kind in MACHINE_VERIFIABLE_KINDS
    managed_source_covered = any(
        source.source_kind.casefold() in MANAGED_SOURCE_KINDS for source in control.sources
    )
    custom_assertion_covered = (
        control.evaluator_kind in MACHINE_VERIFIABLE_KINDS
        and control.primary_source.required
        and control.primary_source.source_kind.casefold() in CUSTOM_ASSERTION_SOURCE_KINDS
    )
    unmapped_reason = None
    if control.evaluator_kind is EvaluatorKind.AGENT_ASSISTED:
        unmapped_reason = "agent_assisted_evaluator_not_machine_verifiable"
    elif control.evaluator_kind is EvaluatorKind.MANUAL:
        unmapped_reason = "manual_evaluator_not_machine_verifiable"
    sources = tuple(
        SourceCoverage(
            source_kind=source.source_kind,
            reference=source.reference,
            version=source.version,
            role=source.role.value,
            required=source.required,
        )
        for source in sorted(
            control.sources,
            key=lambda source: (
                0 if source.role is SourceRole.PRIMARY else 1,
                source.source_kind,
                source.reference,
                source.version,
            ),
        )
    )
    return ControlCoverage(
        key=control.key,
        evaluator_kind=control.evaluator_kind.value,
        machine_verifiable=machine_verifiable,
        managed_source_covered=managed_source_covered,
        custom_assertion_covered=custom_assertion_covered,
        sources=sources,
        unmapped_reason=unmapped_reason,
    )


def _evaluate_fixtures(
    controls: Sequence[ControlDefinition],
    fixtures_dir: Path,
    expected_dir: Path | None,
) -> tuple[dict[str, FixtureCoverage], tuple[GateError, ...]]:
    if not fixtures_dir.is_dir():
        raise ValueError(f"fixtures directory does not exist: {fixtures_dir}")
    expected_by_name = _load_expected(expected_dir) if expected_dir is not None else {}
    fixture_paths = sorted(fixtures_dir.glob("*.json"), key=lambda path: path.name)
    if not fixture_paths:
        raise ValueError(f"fixtures directory contains no JSON fixtures: {fixtures_dir}")

    loaded_fixtures: list[
        tuple[Path, str, str, bool, tuple[EvidenceRecord, ...]]
    ] = []
    for fixture_path in fixture_paths:
        document = _load_json_mapping(fixture_path)
        fixture_id = document.get("fixture_id")
        metadata = document.get("metadata")
        raw_evidence = document.get("evidence")
        if not isinstance(fixture_id, str) or not fixture_id.strip():
            raise ValueError(f"fixture_id must not be empty: {fixture_path}")
        if not isinstance(metadata, Mapping):
            raise ValueError(f"fixture metadata must be a mapping: {fixture_path}")
        classification = metadata.get("classification")
        comparable = metadata.get("comparable")
        if classification not in {"oracle", "exploratory"} or not isinstance(comparable, bool):
            raise ValueError(
                f"fixture metadata requires classification and comparable: {fixture_path}"
            )
        if comparable != (classification == "oracle"):
            raise ValueError(f"fixture classification and comparable disagree: {fixture_path}")
        if not isinstance(raw_evidence, list):
            raise ValueError(f"fixture evidence must be a list: {fixture_path}")
        evidence = tuple(_load_evidence(item, fixture_path) for item in raw_evidence)
        loaded_fixtures.append(
            (fixture_path, fixture_id, classification, comparable, evidence)
        )

    fixture_names_by_id: dict[str, list[str]] = {}
    for fixture_path, fixture_id, _, _, _ in loaded_fixtures:
        fixture_names_by_id.setdefault(fixture_id, []).append(fixture_path.stem)
    gate_errors: list[GateError] = [
        GateError(
            code="duplicate_fixture_id",
            message=f"fixture_id is duplicated across fixture files: {fixture_id}",
            details={
                "fixture_id": fixture_id,
                "fixtures": sorted(fixture_names),
            },
        )
        for fixture_id, fixture_names in sorted(fixture_names_by_id.items())
        if len(fixture_names) > 1
    ]

    comparable_fixture_names = {
        fixture_path.stem
        for fixture_path, _, _, comparable, _ in loaded_fixtures
        if comparable
    }
    expected_fixture_names = set(expected_by_name)
    if comparable_fixture_names != expected_fixture_names:
        gate_errors.append(
            GateError(
                code="expected_fixture_set_mismatch",
                message="Expected fixtures must exactly match comparable fixtures.",
                details={
                    "extra_expected_fixtures": sorted(
                        expected_fixture_names - comparable_fixture_names
                    ),
                    "missing_expected_fixtures": sorted(
                        comparable_fixture_names - expected_fixture_names
                    ),
                },
            )
        )

    evaluator = DeterministicEvaluator()
    fixtures: dict[str, FixtureCoverage] = {}
    control_keys = {control.key for control in controls}
    for fixture_path, fixture_id, classification, comparable, evidence in loaded_fixtures:
        expected = expected_by_name.get(fixture_path.stem) if comparable else None
        expected_verdicts = expected["verdicts"] if expected is not None else {}
        if expected is not None and expected["fixture_id"] != fixture_id:
            gate_errors.append(
                GateError(
                    code="expected_fixture_id_mismatch",
                    message=f"Expected fixture_id does not match fixture {fixture_path.stem}.",
                    details={
                        "actual_fixture_id": fixture_id,
                        "expected_fixture_id": expected["fixture_id"],
                        "fixture": fixture_path.stem,
                    },
                )
            )
        expected_control_keys = set(expected_verdicts)
        if expected is not None and expected_control_keys != control_keys:
            gate_errors.append(
                GateError(
                    code="expected_control_set_mismatch",
                    message=f"Expected controls do not match registry controls for {fixture_path.stem}.",
                    details={
                        "extra_controls": sorted(expected_control_keys - control_keys),
                        "fixture": fixture_path.stem,
                        "missing_controls": sorted(control_keys - expected_control_keys),
                    },
                )
            )
        verdicts: dict[str, FixtureVerdictCoverage] = {}
        mismatch_count = 0
        for control in controls:
            verdict = evaluator.evaluate(control, evidence)
            expected_verdict = expected_verdicts.get(control.key)
            matches_expected = None
            expected_state = None
            expected_reason_code = None
            if expected_verdict is not None:
                expected_state = expected_verdict["state"]
                expected_reason_code = expected_verdict["reason_code"]
                matches_expected = (
                    verdict.state.value == expected_state
                    and verdict.reason_code == expected_reason_code
                )
                mismatch_count += not matches_expected
            verdicts[control.key] = FixtureVerdictCoverage(
                state=verdict.state.value,
                reason_code=verdict.reason_code,
                expected_state=expected_state,
                expected_reason_code=expected_reason_code,
                matches_expected=matches_expected,
            )
        fixtures[fixture_path.stem] = FixtureCoverage(
            fixture_id=fixture_id,
            classification=classification,
            comparable=comparable,
            verdicts=dict(sorted(verdicts.items())),
            mismatch_count=mismatch_count,
            expected_comparisons=sum(
                control.key in expected_verdicts for control in controls
            ),
        )

    return dict(sorted(fixtures.items())), tuple(gate_errors)


def _load_expected(expected_dir: Path) -> dict[str, dict[str, Any]]:
    if not expected_dir.is_dir():
        raise ValueError(f"expected directory does not exist: {expected_dir}")
    expected_by_name: dict[str, dict[str, Any]] = {}
    for expected_path in sorted(expected_dir.glob("*.json"), key=lambda path: path.name):
        document = _load_json_mapping(expected_path)
        fixture_id = document.get("fixture_id")
        verdicts = document.get("verdicts")
        if not isinstance(fixture_id, str) or not fixture_id.strip():
            raise ValueError(f"expected fixture_id must not be empty: {expected_path}")
        if not isinstance(verdicts, Mapping):
            raise ValueError(f"expected verdicts must be a mapping: {expected_path}")
        normalized_verdicts: dict[str, dict[str, str]] = {}
        for control_key, verdict in verdicts.items():
            if not isinstance(control_key, str) or not isinstance(verdict, Mapping):
                raise ValueError(f"expected verdict entry is malformed: {expected_path}")
            state = verdict.get("state")
            reason_code = verdict.get("reason_code")
            if not isinstance(state, str) or not isinstance(reason_code, str):
                raise ValueError(f"expected verdict state and reason_code must be strings: {expected_path}")
            normalized_verdicts[control_key] = {
                "state": state,
                "reason_code": reason_code,
            }
        expected_by_name[expected_path.stem] = {
            "fixture_id": fixture_id,
            "verdicts": normalized_verdicts,
        }
    return expected_by_name


def _load_evidence(raw_evidence: Any, fixture_path: Path) -> EvidenceRecord:
    if not isinstance(raw_evidence, Mapping):
        raise ValueError(f"fixture evidence entry must be a mapping: {fixture_path}")
    try:
        observed_at = raw_evidence.get("observed_at")
        if isinstance(observed_at, str):
            observed_at = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        return EvidenceRecord.create(
            source_kind=raw_evidence["source_kind"],
            source_reference=raw_evidence["source_reference"],
            source_version=raw_evidence["source_version"],
            resource_id=raw_evidence["resource_id"],
            status=EvidenceStatus(raw_evidence["status"]),
            payload=raw_evidence["payload"],
            observed_at=observed_at,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"fixture evidence entry is malformed: {fixture_path}") from exc


def _load_json_mapping(path: Path) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as file:
            document = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not load JSON document: {path}") from exc
    if not isinstance(document, dict):
        raise ValueError(f"JSON document must be an object: {path}")
    return document


def _validate_totals(
    *,
    total_controls: int,
    evaluator_kind_counts: Mapping[str, int],
    machine_count: int,
    custom_count: int,
    managed_count: int,
    custom_assertion_count: int,
    control_rows: Sequence[ControlCoverage],
    fixtures: Mapping[str, FixtureCoverage],
    verdict_state_counts: Mapping[str, int],
    verdict_count: int,
    mismatch_count: int,
    expected_comparisons: int,
) -> tuple[str, ...]:
    errors: list[str] = []
    if sum(evaluator_kind_counts.values()) != total_controls:
        errors.append("evaluator_kind_counts must sum to total_controls")
    if machine_count != managed_count + custom_count:
        errors.append("machine_verifiable must equal managed plus custom evaluators")
    if custom_assertion_count != sum(
        control.custom_assertion_covered for control in control_rows
    ):
        errors.append("custom_assertion coverage must equal covered control rows")
    if len(control_rows) != total_controls:
        errors.append("control rows must equal total_controls")
    if sum(verdict_state_counts.values()) != verdict_count:
        errors.append("verdict_state_counts must sum to evaluated verdicts")
    if any(len(fixture.verdicts) != total_controls for fixture in fixtures.values()):
        errors.append("every fixture must produce one verdict per control")
    if sum(fixture.mismatch_count for fixture in fixtures.values()) != mismatch_count:
        errors.append("fixture mismatch counts must sum to the report mismatch count")
    if sum(fixture.expected_comparisons for fixture in fixtures.values()) != expected_comparisons:
        errors.append("fixture expected comparisons must sum to the mismatch denominator")
    return tuple(errors)


def _ensure_generation(
    generation_dir: Path,
    contents: Mapping[str, tuple[str, str]],
) -> None:
    generations_dir = generation_dir.parent
    generations_dir.mkdir(parents=True, exist_ok=True)
    if generation_dir.exists():
        _validate_existing_generation(generation_dir, contents)
        return

    staging_dir = Path(
        tempfile.mkdtemp(
            dir=generations_dir,
            prefix=f".{generation_dir.name}.",
            suffix=".tmp",
        )
    )
    publication_error: BaseException | None = None
    try:
        for filename, (content, _) in contents.items():
            _write_fsynced_content(staging_dir / filename, content)
        _fsync_directory(staging_dir)
        try:
            _rename_generation_directory(staging_dir, generation_dir)
        except FileExistsError:
            if not generation_dir.exists():
                raise
            _validate_existing_generation(generation_dir, contents)
        else:
            _fsync_directory(generations_dir)
    except BaseException as exc:
        publication_error = exc
        raise
    finally:
        if staging_dir.exists():
            try:
                _remove_generation_staging_directory(
                    staging_dir,
                    allowed_names=set(contents),
                )
            except BaseException as cleanup_error:
                if publication_error is None:
                    raise
                publication_error.add_note(
                    f"generation staging cleanup also failed: {cleanup_error}"
                )


def _rename_generation_directory(source: Path, destination: Path) -> None:
    for attempt in range(1, GENERATION_RENAME_MAX_ATTEMPTS + 1):
        try:
            os.rename(source, destination)
            return
        except PermissionError:
            if attempt == GENERATION_RENAME_MAX_ATTEMPTS:
                raise
            time.sleep(GENERATION_RENAME_RETRY_DELAY_SECONDS * attempt)


def _remove_generation_staging_directory(
    staging_dir: Path,
    *,
    allowed_names: set[str],
) -> None:
    try:
        directory_status = staging_dir.lstat()
    except FileNotFoundError:
        return
    if _is_link_or_reparse_point(directory_status) or not stat.S_ISDIR(
        directory_status.st_mode
    ):
        raise OSError("generation staging path must be a regular directory")

    entries: dict[str, os.stat_result] = {}
    with os.scandir(staging_dir) as directory_entries:
        for entry in directory_entries:
            entry_status = entry.stat(follow_symlinks=False)
            if entry.name not in allowed_names:
                raise OSError(f"unexpected generation staging entry: {entry.name}")
            if _is_link_or_reparse_point(entry_status) or not stat.S_ISREG(
                entry_status.st_mode
            ):
                raise OSError(
                    f"generation staging entry must be a regular file: {entry.name}"
                )
            if getattr(entry_status, "st_nlink", 1) > 1:
                raise OSError(
                    f"generation staging entry must not be a hardlink: {entry.name}"
                )
            entries[entry.name] = entry_status

    current_directory_status = staging_dir.lstat()
    if not _same_file_identity(directory_status, current_directory_status):
        raise OSError("generation staging directory identity changed during cleanup")
    for name, expected_status in entries.items():
        entry_path = staging_dir / name
        current_status = entry_path.lstat()
        if not _same_file_identity(expected_status, current_status):
            raise OSError(
                f"generation staging entry identity changed before unlink: {name}"
            )
        os.unlink(entry_path)
    staging_dir.rmdir()


def _validate_existing_generation(
    generation_dir: Path,
    contents: Mapping[str, tuple[str, str]],
) -> None:
    if not generation_dir.is_dir():
        raise ValueError(f"existing path conflicts with generation {generation_dir.name}")
    if {path.name for path in generation_dir.iterdir()} != set(contents):
        raise ValueError(f"existing content conflicts with generation {generation_dir.name}")
    for filename, (_, expected_hash) in contents.items():
        path = generation_dir / filename
        try:
            actual_hash = _sha256(path.read_bytes())
        except OSError as exc:
            raise ValueError(
                f"existing content conflicts with generation {generation_dir.name}"
            ) from exc
        if actual_hash != expected_hash:
            raise ValueError(f"existing content conflicts with generation {generation_dir.name}")


def _write_fsynced_content(path: Path, content: str) -> None:
    with open(path, "x", encoding="utf-8", newline="\n") as output_file:
        output_file.write(content)
        output_file.flush()
        os.fsync(output_file.fileno())


def _replace_current_manifest(path: Path, content: str) -> None:
    temporary_path = _stage_content(path, content)
    try:
        _fsync_directory(temporary_path.parent)
        _replace_manifest_file(temporary_path, path)
        try:
            _fsync_directory(path.parent)
        except OSError as exc:
            raise ReportPublicationDurabilityError(
                "current report manifest was atomically replaced, but destination reports "
                "directory durability sync failed; after recovery current.json may reference "
                "the previous or new complete immutable bundle. Validate it with "
                f"read_current_report_bundle() and retry publication: {exc}"
            ) from exc
    except BaseException:
        _best_effort_remove_staging_entry(temporary_path)
        raise


def _replace_manifest_file(source: Path, destination: Path) -> None:
    for attempt in range(1, MANIFEST_REPLACE_MAX_ATTEMPTS + 1):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if attempt == MANIFEST_REPLACE_MAX_ATTEMPTS:
                raise
            time.sleep(MANIFEST_REPLACE_RETRY_DELAY_SECONDS * attempt)


def _stage_content(path: Path, content: str) -> Path:
    staging_dir = _validated_staging_directory(path.parent, create=True)
    if staging_dir is None:  # pragma: no cover - create=True always returns a path
        raise OSError("report staging directory could not be created")
    temporary_path = staging_dir / f"{MANIFEST_STAGING_PREFIX}{uuid.uuid4()}{MANIFEST_STAGING_SUFFIX}"
    temporary_file = None
    staged = False
    try:
        with _open_staged_manifest(temporary_path) as temporary_file:
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        staged = True
        return temporary_path
    finally:
        if not staged:
            if temporary_file is not None:
                try:
                    temporary_file.close()
                except BaseException:
                    pass
            _best_effort_remove_staging_entry(temporary_path)


def _open_staged_manifest(path: Path):
    return open(path, "x", encoding="utf-8", newline="\n")


def _scavenge_staging(reports_dir: Path) -> None:
    staging_dir = _validated_staging_directory(reports_dir, create=False)
    if staging_dir is None:
        return
    first_directory_identity, first_entries = _scan_staging_directory(staging_dir)
    second_directory_identity, second_entries = _scan_staging_directory(staging_dir)
    if (
        first_directory_identity is not None
        and second_directory_identity is not None
        and first_directory_identity != second_directory_identity
    ):
        raise OSError("report staging directory identity changed during cleanup")
    if set(first_entries) != set(second_entries):
        raise OSError("report staging entries changed during cleanup")
    for name, first_status in first_entries.items():
        second_status = second_entries[name]
        if not _same_file_identity(first_status, second_status):
            raise OSError(f"report staging entry identity changed during cleanup: {name}")
    for name in sorted(second_entries):
        _remove_staging_entry(staging_dir / name, expected_status=second_entries[name])


def _scan_staging_directory(
    staging_dir: Path,
) -> tuple[tuple[int, int] | None, dict[str, os.stat_result]]:
    directory_status = _validate_staging_directory_status(staging_dir)
    entries: dict[str, os.stat_result] = {}
    with os.scandir(staging_dir) as directory_entries:
        for entry in directory_entries:
            entry_status = entry.stat(follow_symlinks=False)
            _validate_staging_entry(entry.name, entry_status)
            entries[entry.name] = entry_status
    current_status = _validate_staging_directory_status(staging_dir)
    if not _same_file_identity(directory_status, current_status):
        raise OSError("report staging directory identity changed during scan")
    return _file_identity(current_status), entries


def _validated_staging_directory(reports_dir: Path, *, create: bool) -> Path | None:
    staging_dir = reports_dir / STAGING_DIRECTORY_NAME
    try:
        staging_status = staging_dir.lstat()
    except FileNotFoundError:
        if not create:
            return None
        staging_dir.mkdir()
        staging_status = staging_dir.lstat()
    _validate_staging_directory_status(staging_dir, staging_status)
    return staging_dir


def _validate_staging_directory_status(
    staging_dir: Path,
    staging_status: os.stat_result | None = None,
) -> os.stat_result:
    if staging_status is None:
        try:
            staging_status = staging_dir.lstat()
        except FileNotFoundError as exc:
            raise OSError("report staging directory disappeared during cleanup") from exc
    if _is_link_or_reparse_point(staging_status) or not stat.S_ISDIR(
        staging_status.st_mode
    ):
        raise OSError("report staging path must be a regular directory")
    return staging_status


def _validate_staging_entry(name: str, path_status: os.stat_result) -> None:
    if MANIFEST_STAGING_NAME_PATTERN.fullmatch(name) is None:
        raise OSError(f"unexpected staging entry: {name}")
    if _is_link_or_reparse_point(path_status):
        raise OSError(f"report staging entry must not be a symlink or reparse point: {name}")
    if not stat.S_ISREG(path_status.st_mode):
        raise OSError(f"report staging entry must be a regular file: {name}")
    link_count = getattr(path_status, "st_nlink", 1)
    if link_count > 1:
        raise OSError(f"report staging entry must not be a hardlink: {name}")


def _remove_staging_entry(
    path: Path,
    *,
    expected_status: os.stat_result | None = None,
) -> None:
    try:
        path_status = path.lstat()
    except FileNotFoundError:
        return
    _validate_staging_entry(path.name, path_status)
    if expected_status is not None and not _same_file_identity(
        expected_status,
        path_status,
    ):
        raise OSError(f"report staging entry identity changed before unlink: {path.name}")
    os.unlink(path)


def _best_effort_remove_staging_entry(path: Path) -> None:
    try:
        _remove_staging_entry(path)
    except BaseException:
        pass


def _file_identity(path_status: os.stat_result) -> tuple[int, int] | None:
    identity = (path_status.st_dev, path_status.st_ino)
    return identity if identity != (0, 0) else None


def _same_file_identity(
    first_status: os.stat_result,
    second_status: os.stat_result,
) -> bool:
    first_identity = _file_identity(first_status)
    second_identity = _file_identity(second_status)
    if first_identity is not None and second_identity is not None:
        return first_identity == second_identity
    return (
        stat.S_IFMT(first_status.st_mode) == stat.S_IFMT(second_status.st_mode)
        and first_status.st_size == second_status.st_size
        and first_status.st_mtime_ns == second_status.st_mtime_ns
    )


def _is_link_or_reparse_point(path_status: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    file_attributes = getattr(path_status, "st_file_attributes", 0)
    return stat.S_ISLNK(path_status.st_mode) or bool(file_attributes & reparse_flag)


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        _fsync_directory_windows(path)
        return

    open_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, open_flags)
    except OSError as exc:
        _raise_if_directory_fsync_unsupported(path, exc)
        raise
    sync_error: BaseException | None = None
    try:
        os.fsync(descriptor)
    except BaseException as exc:
        sync_error = exc
        if isinstance(exc, OSError):
            _raise_if_directory_fsync_unsupported(path, exc)
        raise
    finally:
        try:
            os.close(descriptor)
        except OSError as close_error:
            if sync_error is None:
                raise
            sync_error.add_note(f"directory descriptor close also failed: {close_error}")


def _fsync_directory_windows(path: Path) -> None:
    import ctypes
    from ctypes import wintypes

    generic_write = 0x40000000
    file_share_read = 0x00000001
    file_share_write = 0x00000002
    file_share_delete = 0x00000004
    open_existing = 3
    file_flag_backup_semantics = 0x02000000
    unsupported_errors = {1, 50}  # ERROR_INVALID_FUNCTION, ERROR_NOT_SUPPORTED

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    flush_file_buffers = kernel32.FlushFileBuffers
    flush_file_buffers.argtypes = [wintypes.HANDLE]
    flush_file_buffers.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    handle = create_file(
        str(path),
        generic_write,
        file_share_read | file_share_write | file_share_delete,
        None,
        open_existing,
        file_flag_backup_semantics,
        None,
    )
    invalid_handle_value = ctypes.c_void_p(-1).value
    if handle == invalid_handle_value:
        error = ctypes.get_last_error()
        if error in unsupported_errors:
            raise DirectoryFsyncUnsupportedError(
                f"directory durability sync is unsupported for {path}: {ctypes.WinError(error)}"
            )
        raise ctypes.WinError(error)

    sync_error: BaseException | None = None
    try:
        if not flush_file_buffers(handle):
            error = ctypes.get_last_error()
            if error in unsupported_errors:
                raise DirectoryFsyncUnsupportedError(
                    f"directory durability sync is unsupported for {path}: {ctypes.WinError(error)}"
                )
            raise ctypes.WinError(error)
    except BaseException as exc:
        sync_error = exc
        raise
    finally:
        if not close_handle(handle):
            close_error = ctypes.WinError(ctypes.get_last_error())
            if sync_error is None:
                raise close_error
            sync_error.add_note(f"directory handle close also failed: {close_error}")


def _raise_if_directory_fsync_unsupported(path: Path, error: OSError) -> None:
    unsupported_errnos = {
        errno.EINVAL,
        errno.ENOSYS,
        getattr(errno, "ENOTSUP", errno.EINVAL),
        getattr(errno, "EOPNOTSUPP", errno.EINVAL),
    }
    if error.errno in unsupported_errnos:
        raise DirectoryFsyncUnsupportedError(
            f"directory durability sync is unsupported for {path}: {error}"
        ) from error


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _generation_id(json_sha256: str, markdown_sha256: str) -> str:
    identity = json.dumps(
        {
            "json": {"filename": REPORT_JSON_FILENAME, "sha256": json_sha256},
            "markdown": {
                "filename": REPORT_MARKDOWN_FILENAME,
                "sha256": markdown_sha256,
            },
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return _sha256(identity)


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _format_ratio(ratio: float) -> str:
    return f"{ratio:.2%}"


def _escape_markdown(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")