import json
import multiprocessing
import os
import shutil
import subprocess
import sys
import io
import threading
import tomllib
from dataclasses import replace
from pathlib import Path
from contextlib import redirect_stderr, redirect_stdout

import pytest
from filelock import FileLock, Timeout

import enterprise.coverage as coverage_module
from enterprise.coverage import (
    build_coverage_report,
    read_current_report_bundle,
    write_coverage_reports,
)
from enterprise.registry import ControlRegistry
from scripts.run_coverage_spike import main as run_coverage_spike_main
from tests.enterprise import coverage_process_helper


ROOT = Path(__file__).resolve().parents[3]
SPIKE_ROOT = ROOT / "experiments/coverage_spike"
CHECKLIST_PATH = SPIKE_ROOT / "checklists/azure_storage_production_readiness.yaml"
MAPPING_PATH = SPIKE_ROOT / "mappings/azure_storage_production_readiness.yaml"
FIXTURES_PATH = SPIKE_ROOT / "fixtures"
EXPECTED_PATH = SPIKE_ROOT / "expected"
SCRIPT_PATH = ROOT / "backend/scripts/run_coverage_spike.py"
README_PATH = SPIKE_ROOT / "README.md"
PUBLISH_LOCK_PATH_NAME = ".publish.lock"
STALE_MANIFEST_NAME = "current.123e4567-e89b-12d3-a456-426614174000.tmp"
PROCESS_START_TIMEOUT_SECONDS = 30
PROCESS_JOIN_TIMEOUT_SECONDS = 30
PROCESS_NOT_DONE_TIMEOUT_SECONDS = 1
CLI_SUBPROCESS_TIMEOUT_SECONDS = 120


def _join_process(process):
    process.join(timeout=PROCESS_JOIN_TIMEOUT_SECONDS)
    if process.is_alive():
        process.terminate()
        process.join(timeout=5)
    if process.is_alive():
        process.kill()
        process.join(timeout=5)
    if process.is_alive():
        pytest.fail(f"child process did not exit: pid={process.pid}")
    exitcode = process.exitcode
    process.close()
    assert exitcode == 0


def test_filelock_is_a_base_product_dependency():
    pyproject = tomllib.loads((ROOT / "backend/pyproject.toml").read_text(encoding="utf-8"))

    assert "filelock>=3.16.0" in pyproject["project"]["dependencies"]


def test_cross_process_publishers_serialize_without_scavenging_active_staging(
    report,
    tmp_path,
):
    context = multiprocessing.get_context("spawn")
    first_staged = context.Event()
    release_first = context.Event()
    second_done = context.Event()
    second_lock_attempted = context.Event()
    first_result = tmp_path / "first-result.json"
    second_result = tmp_path / "second-result.json"
    first_report = replace(report, validation_mode="first_process")
    second_report = replace(report, validation_mode="second_process")
    first = context.Process(
        target=coverage_process_helper._publish_in_process,
        args=(
            first_report,
            tmp_path,
            first_result,
            first_staged,
            release_first,
            None,
        ),
    )
    second = context.Process(
        target=coverage_process_helper._publish_in_process,
        args=(
            second_report,
            tmp_path,
            second_result,
            None,
            None,
            second_done,
            second_lock_attempted,
        ),
    )

    first.start()
    assert first_staged.wait(timeout=PROCESS_START_TIMEOUT_SECONDS)
    second.start()
    try:
        assert second_lock_attempted.wait(timeout=PROCESS_START_TIMEOUT_SECONDS)
        assert second_done.wait(timeout=PROCESS_NOT_DONE_TIMEOUT_SECONDS) is False
    finally:
        release_first.set()
        _join_process(first)
        _join_process(second)

    assert json.loads(first_result.read_text(encoding="utf-8"))["status"] == "ok"
    assert json.loads(second_result.read_text(encoding="utf-8"))["status"] == "ok"
    current = read_current_report_bundle(tmp_path)
    assert current.json_data["validation_mode"] == "second_process"
    assert not list((tmp_path / ".staging").iterdir())


def test_publish_lock_timeout_fails_closed_without_cleanup_or_replacement(
    report,
    tmp_path,
):
    previous = write_coverage_reports(report, tmp_path)
    previous_manifest = previous.current_manifest_path.read_bytes()
    stale_path = tmp_path / ".staging" / STALE_MANIFEST_NAME
    stale_path.write_text("stale", encoding="utf-8")

    with FileLock(str(tmp_path / PUBLISH_LOCK_PATH_NAME), timeout=1):
        with pytest.raises(Timeout):
            write_coverage_reports(
                replace(report, validation_mode="lock_timeout"),
                tmp_path,
                lock_timeout_seconds=0.05,
            )

    assert previous.current_manifest_path.read_bytes() == previous_manifest
    assert stale_path.read_text(encoding="utf-8") == "stale"


def test_publish_lock_releases_after_publication_exception(report, tmp_path, monkeypatch):
    previous = write_coverage_reports(report, tmp_path)
    updated_report = replace(report, validation_mode="after_exception")

    with monkeypatch.context() as patch:
        patch.setattr(
            coverage_module,
            "_replace_current_manifest",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError("synthetic publication failure")
            ),
        )
        with pytest.raises(OSError, match="synthetic publication failure"):
            write_coverage_reports(
                updated_report,
                tmp_path,
                lock_timeout_seconds=0.1,
            )

    current = write_coverage_reports(
        updated_report,
        tmp_path,
        lock_timeout_seconds=0.1,
    )

    assert current.generation_id != previous.generation_id
    assert current.json_data["validation_mode"] == "after_exception"


@pytest.mark.parametrize("lock_timeout_seconds", [-1, float("inf"), float("nan")])
def test_publish_rejects_unbounded_lock_timeout(
    report,
    tmp_path,
    lock_timeout_seconds,
):
    with pytest.raises(ValueError, match="finite non-negative"):
        write_coverage_reports(
            report,
            tmp_path,
            lock_timeout_seconds=lock_timeout_seconds,
        )

    assert not (tmp_path / "current.json").exists()


@pytest.fixture
def registry():
    return ControlRegistry.load(CHECKLIST_PATH, MAPPING_PATH)


def test_coverage_totals_equal_control_count(registry):
    report = build_coverage_report(registry.controls)

    assert report.total_controls == 6
    assert sum(report.evaluator_kind_counts.values()) == 6


@pytest.fixture
def report(registry):
    return build_coverage_report(
        registry.controls,
        fixtures_dir=FIXTURES_PATH,
        expected_dir=EXPECTED_PATH,
    )


def test_control_coverage_uses_explicit_denominators_and_multi_source_rules(report):
    assert report.evaluator_kind_counts == {
        "agent_assisted": 0,
        "custom": 2,
        "managed": 4,
        "manual": 0,
    }
    assert report.machine_verifiable.to_dict() == {
        "count": 6,
        "denominator": 6,
        "ratio": 1.0,
    }
    assert report.managed_source_coverage.to_dict() == {
        "count": 4,
        "denominator": 6,
        "ratio": pytest.approx(2 / 3),
    }
    assert report.custom_evaluator_coverage.to_dict() == {
        "count": 2,
        "denominator": 6,
        "ratio": pytest.approx(1 / 3),
    }
    assert report.custom_assertion_coverage.to_dict() == {
        "count": 6,
        "denominator": 6,
        "ratio": 1.0,
    }
    assert report.agent_assisted.to_dict() == {"count": 0, "denominator": 6, "ratio": 0.0}
    assert report.manual.to_dict() == {"count": 0, "denominator": 6, "ratio": 0.0}

    managed_source_controls = {
        control.key for control in report.controls if control.managed_source_covered
    }
    assert managed_source_controls == {
        "storage.secure_transfer",
        "storage.public_network",
        "storage.redundancy",
        "storage.private_endpoint",
    }

    serialized = report.to_dict()["control_coverage"]
    assert "custom" not in serialized
    assert serialized["custom_evaluator"] == report.custom_evaluator_coverage.to_dict()
    assert serialized["custom_assertion"] == report.custom_assertion_coverage.to_dict()

    markdown = report.to_markdown()
    assert "Custom evaluator" in markdown
    assert "Custom assertion" in markdown
    assert "overlap" in markdown.casefold()


def test_fixture_verdicts_and_gate_totals_are_auditable(report):
    assert set(report.fixtures) == {
        "storage_account_compliant",
        "storage_account_noncompliant",
        "storage_account_partial",
    }
    assert {verdict.state for verdict in report.fixtures["storage_account_compliant"].verdicts.values()} == {
        "pass"
    }
    assert {
        verdict.reason_code
        for verdict in report.fixtures["storage_account_compliant"].verdicts.values()
    } == {"assertion_matched"}
    assert {
        verdict.state for verdict in report.fixtures["storage_account_noncompliant"].verdicts.values()
    } == {"fail"}
    assert {
        verdict.reason_code
        for verdict in report.fixtures["storage_account_noncompliant"].verdicts.values()
    } == {"assertion_not_matched"}

    partial = report.fixtures["storage_account_partial"]
    assert partial.verdicts["storage.secure_transfer"].reason_code == "evidence_partial"
    assert partial.verdicts["storage.blob_soft_delete"].reason_code == "evidence_missing"
    assert {verdict.state for verdict in partial.verdicts.values()} == {"unknown"}

    assert report.verdict_state_counts == {
        "exempted": 0,
        "fail": 6,
        "manual_pending": 0,
        "not_applicable": 0,
        "pass": 6,
        "unknown": 6,
    }
    assert report.unknown.to_dict() == {"count": 6, "denominator": 18, "ratio": pytest.approx(1 / 3)}
    assert report.managed_source_conflicts.to_dict() == {
        "count": 0,
        "denominator": 18,
        "ratio": 0.0,
    }
    assert report.all_conflicts.to_dict() == {"count": 0, "denominator": 18, "ratio": 0.0}
    assert report.fixture_mismatches.to_dict() == {"count": 0, "denominator": 12, "ratio": 0.0}
    assert report.internal_totals_valid is True
    assert report.validation_errors == ()


def test_managed_source_conflicts_exclude_scope_conflicts(registry, tmp_path):
    fixtures_dir = tmp_path / "fixtures"
    shutil.copytree(FIXTURES_PATH, fixtures_dir)
    compliant = json.loads(
        (fixtures_dir / "storage_account_compliant.json").read_text(encoding="utf-8")
    )
    arm = next(item for item in compliant["evidence"] if item["source_kind"] == "arm")
    policy = next(
        item for item in compliant["evidence"] if item["source_kind"] == "azure_policy"
    )

    managed_conflict = json.loads(json.dumps(compliant))
    managed_conflict["fixture_id"] = "synthetic-managed-source-conflict"
    managed_conflict["metadata"] = {"classification": "exploratory", "comparable": False}
    managed_conflict["evidence"] = [json.loads(json.dumps(arm)), json.loads(json.dumps(policy))]
    for item in managed_conflict["evidence"]:
        item["resource_id"] = managed_conflict["fixture_id"]
    managed_conflict["evidence"][1]["payload"]["verdict"]["status"] = "fail"

    scope_conflict = json.loads(json.dumps(managed_conflict))
    scope_conflict["fixture_id"] = "synthetic-scope-conflict"
    scope_conflict["evidence"][0]["resource_id"] = "synthetic-scope-primary"
    scope_conflict["evidence"][1]["resource_id"] = "synthetic-scope-corroborating"
    scope_conflict["evidence"][1]["payload"]["verdict"]["status"] = "pass"

    (fixtures_dir / "managed_conflict.json").write_text(
        json.dumps(managed_conflict),
        encoding="utf-8",
    )
    (fixtures_dir / "scope_conflict.json").write_text(
        json.dumps(scope_conflict),
        encoding="utf-8",
    )

    conflict_report = build_coverage_report(
        registry.controls,
        fixtures_dir=fixtures_dir,
        expected_dir=EXPECTED_PATH,
    )

    assert conflict_report.managed_source_conflicts.count == 1
    assert conflict_report.all_conflicts.count == 2


def test_fixture_comparability_is_explicit_and_partial_is_exploratory(report):
    assert report.fixtures["storage_account_compliant"].comparable is True
    assert report.fixtures["storage_account_noncompliant"].comparable is True
    assert report.fixtures["storage_account_partial"].comparable is False
    assert report.fixtures["storage_account_partial"].classification == "exploratory"


def test_report_exposes_honest_synthetic_implementation_and_deployment_gates(report):
    assert report.validation_mode == "synthetic_fixture"
    assert report.implementation_gate.status == "passed"
    assert report.implementation_gate.conditions == {
        "internal_totals_valid": True,
        "oracle_complete": True,
        "oracle_has_comparisons": True,
        "zero_fixture_mismatches": True,
    }
    assert report.deployment_readiness.status == "blocked"
    assert {condition.code for condition in report.deployment_readiness.unmet_conditions} == {
        "human_mapping_verdict_review",
        "live_azure_adapter_api_validation",
        "rbac_api_limitations_validated",
        "ui_contract_approval",
    }

    serialized = report.to_dict()
    assert serialized["validation_mode"] == "synthetic_fixture"
    assert serialized["implementation_gate"]["status"] == "passed"
    assert serialized["deployment_readiness"]["status"] == "blocked"
    markdown = report.to_markdown()
    assert "Synthetic fixture" in markdown
    assert "Deployment readiness | blocked" in markdown
    assert "does not validate live Azure adapters or APIs" in markdown


def test_zero_oracle_comparisons_fail_the_implementation_gate(registry, tmp_path):
    fixtures_dir = tmp_path / "fixtures"
    expected_dir = tmp_path / "expected"
    fixtures_dir.mkdir()
    expected_dir.mkdir()
    shutil.copy2(FIXTURES_PATH / "storage_account_partial.json", fixtures_dir)

    zero_comparison_report = build_coverage_report(
        registry.controls,
        fixtures_dir=fixtures_dir,
        expected_dir=expected_dir,
    )

    assert zero_comparison_report.fixture_mismatches.denominator == 0
    assert zero_comparison_report.implementation_gate.status == "failed"
    assert zero_comparison_report.implementation_gate.conditions[
        "oracle_has_comparisons"
    ] is False


def test_report_publishes_one_deterministic_immutable_generation(report, tmp_path, monkeypatch):
    replacements = []
    original_replace = os.replace

    def recording_replace(source, destination):
        replacements.append((Path(source), Path(destination)))
        original_replace(source, destination)

    monkeypatch.setattr("enterprise.coverage.os.replace", recording_replace)
    first = write_coverage_reports(report, tmp_path)
    second = write_coverage_reports(report, tmp_path)
    current = read_current_report_bundle(tmp_path)

    assert first.generation_id == second.generation_id == current.generation_id
    assert first.json_path == current.json_path
    assert first.markdown_path == current.markdown_path
    assert first.json_path.parent == tmp_path / "generations" / first.generation_id
    assert first.json_path.read_text(encoding="utf-8") == (
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    assert "| storage.secure_transfer | managed |" in current.markdown_content
    assert (
        "| storage_account_partial | storage.secure_transfer | unknown | evidence_partial |"
        in current.markdown_content
    )
    assert [destination for _, destination in replacements] == [
        tmp_path / "current.json",
        tmp_path / "current.json",
    ]
    assert not [path for path in tmp_path.rglob("*") if path.name.endswith(".tmp")]


def test_existing_generation_with_conflicting_content_is_rejected(report, tmp_path):
    bundle = write_coverage_reports(report, tmp_path)
    bundle.markdown_path.write_text("conflicting content\n", encoding="utf-8")

    with pytest.raises(ValueError, match="conflicts with generation"):
        write_coverage_reports(report, tmp_path)


def test_reader_rejects_manifest_or_bundle_hash_mismatch(report, tmp_path):
    bundle = write_coverage_reports(report, tmp_path)
    manifest = json.loads(bundle.current_manifest_path.read_text(encoding="utf-8"))
    manifest["reports"]["json"]["sha256"] = "0" * 64
    bundle.current_manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="hash"):
        read_current_report_bundle(tmp_path)


def test_second_content_staging_failure_cleans_all_temporary_paths(
    report,
    tmp_path,
    monkeypatch,
):
    previous = write_coverage_reports(report, tmp_path)
    updated_report = replace(report, validation_mode="synthetic_fixture_updated")
    original_write = coverage_module._write_fsynced_content

    def fail_markdown_write(path, content):
        if Path(path).name == "coverage-summary.md":
            raise OSError("synthetic second content creation failure")
        original_write(path, content)

    monkeypatch.setattr("enterprise.coverage._write_fsynced_content", fail_markdown_write)

    with pytest.raises(OSError, match="synthetic second content creation failure"):
        write_coverage_reports(updated_report, tmp_path)

    assert read_current_report_bundle(tmp_path).generation_id == previous.generation_id
    assert not [path for path in tmp_path.rglob("*") if ".tmp" in path.name]


def test_generation_staging_failure_never_uses_recursive_cleanup(
    report,
    tmp_path,
    monkeypatch,
):
    original_write = coverage_module._write_fsynced_content

    def fail_markdown_write(path, content):
        if Path(path).name == "coverage-summary.md":
            raise OSError("synthetic generation staging failure")
        original_write(path, content)

    monkeypatch.setattr(coverage_module, "_write_fsynced_content", fail_markdown_write)
    monkeypatch.setattr(
        shutil,
        "rmtree",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("recursive cleanup must not be used")
        ),
    )

    with pytest.raises(OSError, match="synthetic generation staging failure"):
        write_coverage_reports(report, tmp_path)


def test_generation_cleanup_failure_does_not_mask_primary_write_failure(
    report,
    tmp_path,
    monkeypatch,
):
    original_write = coverage_module._write_fsynced_content

    def fail_markdown_write(path, content):
        if Path(path).name == "coverage-summary.md":
            raise OSError("synthetic primary write failure")
        original_write(path, content)

    monkeypatch.setattr(coverage_module, "_write_fsynced_content", fail_markdown_write)
    monkeypatch.setattr(
        coverage_module,
        "_remove_generation_staging_directory",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            PermissionError("synthetic cleanup failure")
        ),
    )

    with pytest.raises(OSError, match="synthetic primary write failure") as error:
        write_coverage_reports(report, tmp_path)

    assert any("synthetic cleanup failure" in note for note in error.value.__notes__)


def test_generation_parent_fsync_failure_stops_before_manifest_publication(
    report,
    tmp_path,
    monkeypatch,
):
    previous = write_coverage_reports(report, tmp_path)
    updated_report = replace(report, validation_mode="synthetic_fixture_updated")
    original_fsync_directory = coverage_module._fsync_directory

    def fail_generation_parent_fsync(path):
        if Path(path) == tmp_path / "generations":
            raise OSError("synthetic generation parent fsync failure")
        original_fsync_directory(path)

    monkeypatch.setattr(
        "enterprise.coverage._fsync_directory",
        fail_generation_parent_fsync,
    )

    with pytest.raises(OSError, match="synthetic generation parent fsync failure"):
        write_coverage_reports(updated_report, tmp_path)

    current = read_current_report_bundle(tmp_path)
    assert current.generation_id == previous.generation_id
    assert current.json_data["validation_mode"] == "synthetic_fixture"


def test_generation_directory_rename_retries_transient_permission_error(
    report,
    tmp_path,
    monkeypatch,
):
    original_rename = os.rename
    rename_attempts = 0

    def transiently_failing_rename(source, destination):
        nonlocal rename_attempts
        rename_attempts += 1
        if rename_attempts == 1:
            raise PermissionError("synthetic transient rename failure")
        original_rename(source, destination)

    monkeypatch.setattr("enterprise.coverage.os.rename", transiently_failing_rename)

    bundle = write_coverage_reports(report, tmp_path)

    assert rename_attempts == 2
    assert bundle.json_path.is_file()


def test_interruption_before_manifest_replacement_leaves_previous_generation_readable(
    report,
    tmp_path,
    monkeypatch,
):
    previous = write_coverage_reports(report, tmp_path)
    updated_report = replace(report, validation_mode="synthetic_fixture_updated")

    def interrupt_before_publish(*_args, **_kwargs):
        raise KeyboardInterrupt("synthetic interruption")

    monkeypatch.setattr("enterprise.coverage._replace_current_manifest", interrupt_before_publish)

    with pytest.raises(KeyboardInterrupt, match="synthetic interruption"):
        write_coverage_reports(updated_report, tmp_path)

    current = read_current_report_bundle(tmp_path)
    assert current.generation_id == previous.generation_id
    assert current.json_data["validation_mode"] == "synthetic_fixture"


def test_manifest_replace_failure_leaves_previous_generation_current(
    report,
    tmp_path,
    monkeypatch,
):
    previous = write_coverage_reports(report, tmp_path)
    updated_report = replace(report, validation_mode="synthetic_fixture_updated")
    original_replace = os.replace

    def fail_current_replace(source, destination):
        if Path(destination) == tmp_path / "current.json":
            raise OSError("synthetic manifest replacement failure")
        original_replace(source, destination)

    monkeypatch.setattr("enterprise.coverage.os.replace", fail_current_replace)

    with pytest.raises(OSError, match="synthetic manifest replacement failure"):
        write_coverage_reports(updated_report, tmp_path)

    assert read_current_report_bundle(tmp_path).generation_id == previous.generation_id
    assert not [path for path in tmp_path.glob(".*") if path.name.endswith(".tmp")]


def test_manifest_replace_retries_transient_permission_error(
    report,
    tmp_path,
    monkeypatch,
):
    previous = write_coverage_reports(report, tmp_path)
    updated_report = replace(report, validation_mode="manifest_retry")
    original_replace = os.replace
    replace_attempts = 0

    def transiently_failing_replace(source, destination):
        nonlocal replace_attempts
        if Path(destination) == tmp_path / "current.json":
            replace_attempts += 1
            if replace_attempts == 1:
                raise PermissionError("synthetic transient manifest replacement failure")
        original_replace(source, destination)

    monkeypatch.setattr(coverage_module.os, "replace", transiently_failing_replace)

    current = write_coverage_reports(updated_report, tmp_path)

    assert replace_attempts == 2
    assert current.generation_id != previous.generation_id
    assert read_current_report_bundle(tmp_path).json_data["validation_mode"] == "manifest_retry"


def test_destination_directory_fsync_failure_after_manifest_replace_is_surfaced(
    report,
    tmp_path,
    monkeypatch,
):
    previous = write_coverage_reports(report, tmp_path)
    updated_report = replace(report, validation_mode="destination_fsync_failure")
    original_replace = coverage_module.os.replace
    original_fsync_directory = coverage_module._fsync_directory
    publication_events = []

    def recording_replace(source, destination):
        original_replace(source, destination)
        if Path(destination) == tmp_path / "current.json":
            publication_events.append("replace")

    def fail_destination_directory_fsync(path):
        if Path(path) == tmp_path and publication_events == ["replace"]:
            publication_events.append("destination_directory_fsync")
            raise OSError("synthetic destination directory fsync failure")
        return original_fsync_directory(path)

    with monkeypatch.context() as patch:
        patch.setattr(coverage_module.os, "replace", recording_replace)
        patch.setattr(
            coverage_module,
            "_fsync_directory",
            fail_destination_directory_fsync,
        )

        with pytest.raises(
            coverage_module.ReportPublicationDurabilityError,
            match="retry publication",
        ) as error:
            write_coverage_reports(updated_report, tmp_path)

    assert publication_events == ["replace", "destination_directory_fsync"]
    assert "synthetic destination directory fsync failure" in str(error.value.__cause__)
    current = read_current_report_bundle(tmp_path)
    assert current.json_data["validation_mode"] in {
        "synthetic_fixture",
        "destination_fsync_failure",
    }
    assert (current.generation_id == previous.generation_id) == (
        current.json_data["validation_mode"] == "synthetic_fixture"
    )
    assert not list((tmp_path / ".staging").iterdir())

    retried = write_coverage_reports(
        updated_report,
        tmp_path,
        lock_timeout_seconds=0.1,
    )

    assert retried.json_data["validation_mode"] == "destination_fsync_failure"


@pytest.mark.parametrize("operation", ["write", "flush", "fsync", "close"])
def test_manifest_staging_failure_cleans_temporary_file(
    report,
    tmp_path,
    monkeypatch,
    operation,
):
    previous = write_coverage_reports(report, tmp_path)
    original_open_staged_manifest = coverage_module._open_staged_manifest

    class FaultingTemporaryFile:
        def __init__(self, wrapped):
            self._wrapped = wrapped
            self.name = wrapped.name

        def __enter__(self):
            self._wrapped.__enter__()
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            if operation == "close":
                raise OSError("synthetic manifest close failure")
            return self._wrapped.__exit__(exc_type, exc_value, traceback)

        def close(self):
            return self._wrapped.close()

        def write(self, content):
            if operation == "write":
                raise OSError("synthetic manifest write failure")
            return self._wrapped.write(content)

        def flush(self):
            if operation == "flush":
                raise OSError("synthetic manifest flush failure")
            return self._wrapped.flush()

        def fileno(self):
            return self._wrapped.fileno()

    def faulting_open_staged_manifest(path):
        return FaultingTemporaryFile(original_open_staged_manifest(path))

    if operation == "fsync":
        monkeypatch.setattr(
            "enterprise.coverage.os.fsync",
            lambda _descriptor: (_ for _ in ()).throw(
                OSError("synthetic manifest fsync failure")
            ),
        )
    else:
        monkeypatch.setattr(
            "enterprise.coverage._open_staged_manifest",
            faulting_open_staged_manifest,
        )

    with pytest.raises(OSError, match=f"synthetic manifest {operation} failure"):
        write_coverage_reports(report, tmp_path)

    assert read_current_report_bundle(tmp_path).generation_id == previous.generation_id
    assert not list((tmp_path / ".staging").iterdir())


def test_persistent_manifest_close_failure_is_recovered_on_next_publication(
    report,
    tmp_path,
    monkeypatch,
):
    previous = write_coverage_reports(report, tmp_path)
    previous_manifest = previous.current_manifest_path.read_bytes()
    updated_report = replace(report, validation_mode="synthetic_fixture_updated")
    original_open_staged_manifest = coverage_module._open_staged_manifest
    close_fault_active = True
    open_files = []
    staged_paths = []

    class PersistentlyFaultingTemporaryFile:
        def __init__(self, path, wrapped):
            self._path = Path(path)
            self._wrapped = wrapped

        def __enter__(self):
            self._wrapped.__enter__()
            open_files.append(self._wrapped)
            staged_paths.append(self._path)
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            if close_fault_active:
                raise OSError("synthetic persistent manifest close failure")
            return self._wrapped.__exit__(exc_type, exc_value, traceback)

        def close(self):
            if close_fault_active:
                raise OSError("synthetic persistent manifest close failure")
            return self._wrapped.close()

        def write(self, content):
            return self._wrapped.write(content)

        def flush(self):
            return self._wrapped.flush()

        def fileno(self):
            return self._wrapped.fileno()

    def persistently_faulting_open_staged_manifest(path):
        return PersistentlyFaultingTemporaryFile(
            path,
            original_open_staged_manifest(path),
        )

    monkeypatch.setattr(
        "enterprise.coverage._open_staged_manifest",
        persistently_faulting_open_staged_manifest,
    )

    with pytest.raises(OSError, match="synthetic persistent manifest close failure"):
        write_coverage_reports(updated_report, tmp_path)

    assert previous.current_manifest_path.read_bytes() == previous_manifest
    assert staged_paths
    assert all(path.parent == tmp_path / ".staging" for path in staged_paths)
    assert all(path.name.startswith("current.") for path in staged_paths)
    assert all(path.name.endswith(".tmp") for path in staged_paths)
    assert not list(tmp_path.glob("current.*.tmp"))

    with pytest.raises(OSError):
        write_coverage_reports(updated_report, tmp_path)

    assert previous.current_manifest_path.read_bytes() == previous_manifest

    close_fault_active = False
    for open_file in open_files:
        open_file.close()

    current = write_coverage_reports(updated_report, tmp_path)

    assert current.generation_id != previous.generation_id
    assert current.json_data["validation_mode"] == "synthetic_fixture_updated"
    assert not list((tmp_path / ".staging").iterdir())


def test_reader_does_not_lock_or_delete_staging_entries(
    report,
    tmp_path,
):
    previous = write_coverage_reports(report, tmp_path)
    staging_dir = tmp_path / ".staging"
    stale_path = staging_dir / STALE_MANIFEST_NAME
    stale_path.write_text("stale", encoding="utf-8")
    unexpected_path = staging_dir / "unexpected.txt"
    unexpected_path.write_text("unexpected", encoding="utf-8")
    lock_path = tmp_path / PUBLISH_LOCK_PATH_NAME
    lock_path.unlink(missing_ok=True)

    current = read_current_report_bundle(tmp_path)

    assert current.generation_id == previous.generation_id
    assert stale_path.read_text(encoding="utf-8") == "stale"
    assert unexpected_path.read_text(encoding="utf-8") == "unexpected"
    assert not lock_path.exists()


def test_publisher_fails_closed_when_stale_staging_cleanup_still_fails(
    report,
    tmp_path,
    monkeypatch,
):
    previous = write_coverage_reports(report, tmp_path)
    previous_manifest = previous.current_manifest_path.read_bytes()
    stale_path = tmp_path / ".staging" / STALE_MANIFEST_NAME
    stale_path.write_text("stale", encoding="utf-8")
    original_remove_staging_entry = coverage_module._remove_staging_entry

    def fail_stale_cleanup(path, *, expected_status=None):
        if Path(path) == stale_path:
            raise PermissionError("synthetic stale staging cleanup failure")
        original_remove_staging_entry(path, expected_status=expected_status)

    monkeypatch.setattr(
        "enterprise.coverage._remove_staging_entry",
        fail_stale_cleanup,
    )

    with pytest.raises(PermissionError, match="synthetic stale staging cleanup failure"):
        write_coverage_reports(
            replace(report, validation_mode="cleanup_failure"),
            tmp_path,
        )

    assert previous.current_manifest_path.read_bytes() == previous_manifest
    assert stale_path.read_text(encoding="utf-8") == "stale"


def _create_directory_link(link_path, target_path):
    try:
        link_path.symlink_to(target_path, target_is_directory=True)
        return
    except OSError as exc:
        if os.name != "nt":
            pytest.skip(f"directory symlinks are unavailable: {exc}")
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link_path), str(target_path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if result.returncode != 0:
        pytest.skip(f"directory links are unavailable: {result.stderr or result.stdout}")


def test_publisher_rejects_staged_symlink_without_unlinking_it(report, tmp_path):
    previous = write_coverage_reports(report, tmp_path)
    previous_manifest = previous.current_manifest_path.read_bytes()
    outside_directory = tmp_path / "outside-staging"
    outside_directory.mkdir()
    marker = outside_directory / "must-survive.txt"
    marker.write_text("keep", encoding="utf-8")
    staged_link = tmp_path / ".staging" / STALE_MANIFEST_NAME
    _create_directory_link(staged_link, outside_directory)

    with pytest.raises(OSError, match="staging"):
        write_coverage_reports(
            replace(report, validation_mode="symlink_entry"),
            tmp_path,
        )

    assert previous.current_manifest_path.read_bytes() == previous_manifest
    assert marker.read_text(encoding="utf-8") == "keep"
    assert staged_link.lstat()


def test_publisher_rejects_unexpected_staging_entry_without_deleting_anything(
    report,
    tmp_path,
):
    previous = write_coverage_reports(report, tmp_path)
    previous_manifest = previous.current_manifest_path.read_bytes()
    staging_dir = tmp_path / ".staging"
    stale_path = staging_dir / STALE_MANIFEST_NAME
    stale_path.write_text("stale", encoding="utf-8")
    unexpected_path = staging_dir / "unexpected.txt"
    unexpected_path.write_text("unexpected", encoding="utf-8")

    with pytest.raises(OSError, match="unexpected staging entry"):
        write_coverage_reports(
            replace(report, validation_mode="unexpected_entry"),
            tmp_path,
        )

    assert previous.current_manifest_path.read_bytes() == previous_manifest
    assert stale_path.read_text(encoding="utf-8") == "stale"
    assert unexpected_path.read_text(encoding="utf-8") == "unexpected"


def test_publisher_rejects_exact_pattern_staging_directory_without_deleting_it(
    report,
    tmp_path,
):
    previous = write_coverage_reports(report, tmp_path)
    previous_manifest = previous.current_manifest_path.read_bytes()
    staged_directory = tmp_path / ".staging" / STALE_MANIFEST_NAME
    staged_directory.mkdir()
    marker = staged_directory / "must-survive.txt"
    marker.write_text("keep", encoding="utf-8")

    with pytest.raises(OSError, match="regular file"):
        write_coverage_reports(
            replace(report, validation_mode="directory_entry"),
            tmp_path,
        )

    assert previous.current_manifest_path.read_bytes() == previous_manifest
    assert marker.read_text(encoding="utf-8") == "keep"


def test_publisher_rejects_hardlinked_staging_file_without_unlinking_it(
    report,
    tmp_path,
):
    previous = write_coverage_reports(report, tmp_path)
    previous_manifest = previous.current_manifest_path.read_bytes()
    outside_path = tmp_path / "outside.txt"
    outside_path.write_text("keep", encoding="utf-8")
    staged_path = tmp_path / ".staging" / STALE_MANIFEST_NAME
    os.link(outside_path, staged_path)

    with pytest.raises(OSError, match="hardlink"):
        write_coverage_reports(
            replace(report, validation_mode="hardlink_entry"),
            tmp_path,
        )

    assert previous.current_manifest_path.read_bytes() == previous_manifest
    assert staged_path.read_text(encoding="utf-8") == "keep"
    assert outside_path.read_text(encoding="utf-8") == "keep"


def test_locked_publisher_removes_exact_pattern_stale_file(report, tmp_path):
    write_coverage_reports(report, tmp_path)
    stale_path = tmp_path / ".staging" / STALE_MANIFEST_NAME
    stale_path.write_text("stale", encoding="utf-8")

    write_coverage_reports(
        replace(report, validation_mode="stale_cleanup"),
        tmp_path,
    )

    assert not stale_path.exists()


def test_process_local_active_staging_registry_is_removed():
    assert not hasattr(coverage_module, "_ACTIVE_STAGING_PATHS")
    assert not hasattr(coverage_module, "_ACTIVE_STAGING_PATHS_LOCK")


def test_publish_rejects_reparse_point_staging_root_without_following_target(
    report,
    tmp_path,
):
    previous = write_coverage_reports(report, tmp_path)
    previous_manifest = previous.current_manifest_path.read_bytes()
    staging_dir = tmp_path / ".staging"
    staging_dir.rmdir()
    outside_directory = tmp_path / "outside-staging"
    outside_directory.mkdir()
    marker = outside_directory / "must-survive.txt"
    marker.write_text("keep", encoding="utf-8")
    _create_directory_link(staging_dir, outside_directory)

    with pytest.raises(OSError, match="regular directory"):
        write_coverage_reports(
            replace(report, validation_mode="synthetic_fixture_updated"),
            tmp_path,
        )

    assert previous.current_manifest_path.read_bytes() == previous_manifest
    assert marker.read_text(encoding="utf-8") == "keep"


def test_reader_during_manifest_replacement_observes_old_or_new_bundle_only(
    report,
    tmp_path,
    monkeypatch,
):
    previous = write_coverage_reports(report, tmp_path)
    updated_report = replace(report, validation_mode="synthetic_fixture_updated")
    reader_has_manifest_open = threading.Event()
    allow_reader_close = threading.Event()
    replace_attempted = threading.Event()
    reader_done = threading.Event()
    writer_errors = []
    reader_errors = []
    reader_results = []
    original_replace = os.replace
    original_load_json_mapping = coverage_module._load_json_mapping

    def recording_current_replace(source, destination):
        if Path(destination) == tmp_path / "current.json":
            try:
                original_replace(source, destination)
            finally:
                replace_attempted.set()
            return
        original_replace(source, destination)

    def blocking_current_manifest_read(path):
        if Path(path) != tmp_path / "current.json":
            return original_load_json_mapping(path)
        with open(path, encoding="utf-8") as manifest_file:
            document = json.load(manifest_file)
            reader_has_manifest_open.set()
            assert allow_reader_close.wait(timeout=5)
            return document

    def publish_updated_report():
        try:
            write_coverage_reports(updated_report, tmp_path)
        except Exception as exc:  # pragma: no cover - asserted below
            writer_errors.append(exc)

    def read_during_publish():
        try:
            reader_results.append(read_current_report_bundle(tmp_path))
        except Exception as exc:  # pragma: no cover - asserted below
            reader_errors.append(exc)
        finally:
            reader_done.set()

    monkeypatch.setattr(coverage_module.os, "replace", recording_current_replace)
    monkeypatch.setattr(
        coverage_module,
        "_load_json_mapping",
        blocking_current_manifest_read,
    )
    reader = threading.Thread(target=read_during_publish)
    reader.start()
    assert reader_has_manifest_open.wait(timeout=5)
    writer = threading.Thread(target=publish_updated_report)
    writer.start()
    try:
        assert replace_attempted.wait(timeout=5)
    finally:
        allow_reader_close.set()
    writer.join(timeout=5)
    reader.join(timeout=5)
    after = read_current_report_bundle(tmp_path)

    assert not writer.is_alive()
    assert not reader.is_alive()
    assert writer_errors == []
    assert reader_errors == []
    during = reader_results[0]
    assert during.generation_id == previous.generation_id
    assert after.generation_id != previous.generation_id
    assert {during.json_data["validation_mode"], after.json_data["validation_mode"]} == {
        "synthetic_fixture",
        "synthetic_fixture_updated",
    }


def test_duplicate_fixture_ids_fail_the_implementation_gate_with_structured_error(
    registry,
    tmp_path,
):
    fixtures_dir = tmp_path / "fixtures"
    shutil.copytree(FIXTURES_PATH, fixtures_dir)
    duplicate = json.loads(
        (fixtures_dir / "storage_account_partial.json").read_text(encoding="utf-8")
    )
    (fixtures_dir / "duplicate_partial.json").write_text(
        json.dumps(duplicate),
        encoding="utf-8",
    )

    duplicate_report = build_coverage_report(
        registry.controls,
        fixtures_dir=fixtures_dir,
        expected_dir=EXPECTED_PATH,
    )

    assert duplicate_report.implementation_gate.status == "failed"
    duplicate_error = next(
        error for error in duplicate_report.gate_errors if error.code == "duplicate_fixture_id"
    )
    assert duplicate_error.details == {
        "fixture_id": duplicate["fixture_id"],
        "fixtures": ["duplicate_partial", "storage_account_partial"],
    }


def test_cli_returns_one_and_publishes_structured_duplicate_fixture_id_error(tmp_path):
    fixtures_dir = tmp_path / "fixtures"
    reports_dir = tmp_path / "reports"
    shutil.copytree(FIXTURES_PATH, fixtures_dir)
    duplicate = json.loads(
        (fixtures_dir / "storage_account_partial.json").read_text(encoding="utf-8")
    )
    (fixtures_dir / "duplicate_partial.json").write_text(
        json.dumps(duplicate),
        encoding="utf-8",
    )

    result = _run_cli_in_process(
        "--fixtures-dir",
        str(fixtures_dir),
        "--reports-dir",
        str(reports_dir),
    )

    assert result.returncode == 1
    generated = read_current_report_bundle(reports_dir).json_data
    duplicate_error = next(
        error for error in generated["gate_errors"] if error["code"] == "duplicate_fixture_id"
    )
    assert duplicate_error["details"] == {
        "fixture_id": duplicate["fixture_id"],
        "fixtures": ["duplicate_partial", "storage_account_partial"],
    }


def test_cli_returns_structured_failure_when_oracle_has_no_comparisons(tmp_path):
    fixtures_dir = tmp_path / "fixtures"
    expected_dir = tmp_path / "expected"
    reports_dir = tmp_path / "reports"
    fixtures_dir.mkdir()
    expected_dir.mkdir()
    shutil.copy2(FIXTURES_PATH / "storage_account_partial.json", fixtures_dir)

    result = _run_cli_in_process(
        "--fixtures-dir",
        str(fixtures_dir),
        "--expected-dir",
        str(expected_dir),
        "--reports-dir",
        str(reports_dir),
    )

    assert result.returncode == 1
    failure = json.loads(result.stderr)
    assert failure["error"]["code"] == "coverage_spike_gate_failed"
    assert failure["error"]["details"]["failed_conditions"] == [
        "oracle_has_comparisons"
    ]
    generated = read_current_report_bundle(reports_dir).json_data
    assert generated["implementation_gate"]["conditions"][
        "oracle_has_comparisons"
    ] is False


def _run_cli_subprocess(cwd, *arguments):
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=CLI_SUBPROCESS_TIMEOUT_SECONDS,
    )


def _run_cli_in_process(*arguments):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        returncode = run_coverage_spike_main(list(arguments))
    return subprocess.CompletedProcess(
        args=[str(SCRIPT_PATH), *arguments],
        returncode=returncode,
        stdout=stdout.getvalue(),
        stderr=stderr.getvalue(),
    )


def test_cli_resolves_default_input_paths_independent_of_cwd(tmp_path):
    reports_dir = tmp_path / "reports"
    result = _run_cli_subprocess(tmp_path, "--reports-dir", str(reports_dir))

    assert result.returncode == 0, result.stderr
    assert "fixture_mismatches=0/12" in result.stdout
    assert "implementation_gate=passed" in result.stdout
    assert "deployment_readiness=blocked" in result.stdout
    current = read_current_report_bundle(reports_dir)
    assert f"generation_id={current.generation_id}" in result.stdout
    assert f"json_report={current.json_path}" in result.stdout
    assert f"markdown_report={current.markdown_path}" in result.stdout
    assert f"current_manifest={current.current_manifest_path}" in result.stdout


def test_cli_returns_one_and_reports_expected_verdict_mismatch(tmp_path):
    expected_dir = tmp_path / "expected"
    shutil.copytree(EXPECTED_PATH, expected_dir)
    expected_file = expected_dir / "storage_account_compliant.json"
    expected = json.loads(expected_file.read_text(encoding="utf-8"))
    expected["verdicts"]["storage.secure_transfer"]["state"] = "fail"
    expected_file.write_text(json.dumps(expected), encoding="utf-8")
    reports_dir = tmp_path / "reports"

    result = _run_cli_in_process(
        "--expected-dir",
        str(expected_dir),
        "--reports-dir",
        str(reports_dir),
    )

    assert result.returncode == 1
    generated = read_current_report_bundle(reports_dir).json_data
    assert generated["fixture_summary"]["mismatches"] == {
        "count": 1,
        "denominator": 12,
        "ratio": pytest.approx(1 / 12),
    }


@pytest.mark.parametrize(
    ("scenario", "error_code"),
    [
        ("missing_fixture", "expected_fixture_set_mismatch"),
        ("extra_fixture", "expected_fixture_set_mismatch"),
        ("missing_control", "expected_control_set_mismatch"),
        ("extra_control", "expected_control_set_mismatch"),
        ("mismatched_fixture_id", "expected_fixture_id_mismatch"),
    ],
)
def test_cli_returns_one_with_structured_oracle_gate_errors(tmp_path, scenario, error_code):
    fixtures_dir = tmp_path / "fixtures"
    expected_dir = tmp_path / "expected"
    reports_dir = tmp_path / "reports"
    shutil.copytree(FIXTURES_PATH, fixtures_dir)
    shutil.copytree(EXPECTED_PATH, expected_dir)

    compliant_path = expected_dir / "storage_account_compliant.json"
    compliant = json.loads(compliant_path.read_text(encoding="utf-8"))
    if scenario == "missing_fixture":
        (expected_dir / "storage_account_noncompliant.json").unlink()
    elif scenario == "extra_fixture":
        partial_fixture = json.loads(
            (fixtures_dir / "storage_account_partial.json").read_text(encoding="utf-8")
        )
        compliant["fixture_id"] = partial_fixture["fixture_id"]
        (expected_dir / "storage_account_partial.json").write_text(
            json.dumps(compliant),
            encoding="utf-8",
        )
    elif scenario == "missing_control":
        compliant["verdicts"].pop("storage.secure_transfer")
        compliant_path.write_text(json.dumps(compliant), encoding="utf-8")
    elif scenario == "extra_control":
        compliant["verdicts"]["storage.unexpected"] = {
            "state": "pass",
            "reason_code": "assertion_matched",
        }
        compliant_path.write_text(json.dumps(compliant), encoding="utf-8")
    else:
        compliant["fixture_id"] = "different-fixture-id"
        compliant_path.write_text(json.dumps(compliant), encoding="utf-8")

    result = _run_cli_in_process(
        "--fixtures-dir",
        str(fixtures_dir),
        "--expected-dir",
        str(expected_dir),
        "--reports-dir",
        str(reports_dir),
    )

    assert result.returncode == 1, result.stdout
    generated = read_current_report_bundle(reports_dir).json_data
    assert generated["implementation_gate"]["status"] == "failed"
    assert error_code in {error["code"] for error in generated["gate_errors"]}


@pytest.mark.parametrize(
    "checklist_content",
    [
        "null\n",
        "metadata: []\ncategories: []\n",
    ],
)
def test_cli_reports_malformed_checklist_as_concise_structured_error(tmp_path, checklist_content):
    checklist_path = tmp_path / "malformed-checklist.yaml"
    checklist_path.write_text(checklist_content, encoding="utf-8")

    result = _run_cli_in_process(
        "--checklist",
        str(checklist_path),
        "--reports-dir",
        str(tmp_path / "reports"),
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert "Traceback" not in result.stderr
    assert result.stderr.count("\n") == 1
    error = json.loads(result.stderr)
    assert error["error"]["code"] == "coverage_spike_input_error"
    assert error["error"]["message"]


def test_cli_returns_one_for_malformed_mapping(tmp_path):
    mapping_path = tmp_path / "malformed.yaml"
    mapping_path.write_text("controls: [", encoding="utf-8")

    result = _run_cli_in_process(
        "--mapping",
        str(mapping_path),
        "--reports-dir",
        str(tmp_path / "reports"),
    )

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    error = json.loads(result.stderr)
    assert error["error"]["code"] == "coverage_spike_input_error"


def test_readme_commands_declare_exact_working_directories_and_manifest_publication():
    readme = README_PATH.read_text(encoding="utf-8")

    assert "어느 경로에서든" not in readme
    assert "현재 작업 디렉터리: 저장소 루트" in readme
    assert "uv run --project backend python backend/scripts/run_coverage_spike.py" in readme
    assert "현재 작업 디렉터리: `backend/`" in readme
    assert "uv run python scripts/run_coverage_spike.py" in readme
    assert "reports/current.json" in readme
    assert "current manifest" in readme
    assert "유일한 canonical publication boundary" in readme
    assert "두 report가 atomic" not in readme
    assert "reports/.staging" in readme
    assert "reports/.publish.lock" in readme
    assert "filelock" in readme
    assert "best effort" in readme
    assert "publisher만 stale staging을 정리" in readme
    assert "reader는 `.staging`을 검사하거나 정리하지 않는다" in readme
    assert "trusted filesystem/ACL boundary" in readme
    assert "same-privilege malicious replacement" in readme
    assert "재귀 삭제하지 않는다" in readme
    assert "`CreateFileW`" in readme
    assert "`FILE_FLAG_BACKUP_SEMANTICS`" in readme
    assert "`FlushFileBuffers`" in readme
    assert "`DirectoryFsyncUnsupportedError`" in readme
    assert "`ReportPublicationDurabilityError`" in readme
    assert "old 또는 new complete immutable bundle" in readme
    assert "publication을 재시도" in readme