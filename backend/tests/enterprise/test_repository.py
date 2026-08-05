from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from enterprise.domain import EvidenceRecord, EvidenceStatus, Verdict, VerdictState
from enterprise.postgres_repository import PostgresEnterpriseRepository
from enterprise.repository import (
    EvaluationVerdictInput,
    InMemoryEnterpriseRepository,
)


ROOT = Path(__file__).resolve().parents[3]


def _evidence(resource_id: str = "/subscriptions/sub-a/resourceGroups/rg/providers/Microsoft.Storage/storageAccounts/sa") -> EvidenceRecord:
    return EvidenceRecord.create(
        source_kind="arm",
        source_reference="arm.storage_account.resource",
        source_version="2023-05-01",
        resource_id=resource_id,
        status=EvidenceStatus.COMPLETE,
        observed_at=datetime.now(UTC),
        payload={
            "resource_type": "Microsoft.Storage/storageAccounts",
            "properties": {"supportsHttpsTrafficOnly": True},
        },
    )


@pytest.mark.asyncio
async def test_inmemory_repository_complete_run_is_immutable_and_scope_guarded():
    repo = InMemoryEnterpriseRepository()
    run_id = await repo.create_run(
        tenant_id="tenant-a",
        subscription_id="sub-a",
        requested_resource_ids=("/subscriptions/sub-a/resourceGroups/rg/providers/Microsoft.Storage/storageAccounts/sa",),
        control_keys=("storage.secure_transfer",),
    )

    verdict_input = EvaluationVerdictInput(
        resource_id="/subscriptions/sub-a/resourceGroups/rg/providers/Microsoft.Storage/storageAccounts/sa",
        verdict=Verdict(
            control_key="storage.secure_transfer",
            state=VerdictState.PASS,
            reason_code="assertion_matched",
            evidence_hashes=(_evidence().content_hash,),
        ),
    )
    await repo.complete_run(run_id, (_evidence(),), (verdict_input,), ())

    run = await repo.get_run(run_id, "sub-a")
    assert run is not None
    assert run.state == "completed"
    assert run.verdict_counts[VerdictState.PASS.value] == 1
    assert run.verdict_counts[VerdictState.UNKNOWN.value] == 0
    assert len(run.findings) == 1

    finding = await repo.get_finding(run.findings[0].finding_id, "sub-a")
    assert finding is not None
    assert finding.control_key == "storage.secure_transfer"
    assert finding.verdict_state == VerdictState.PASS.value

    assert await repo.get_run(run_id, "sub-b") is None
    assert await repo.get_finding(run.findings[0].finding_id, "sub-b") is None

    with pytest.raises(ValueError, match="immutable"):
        await repo.complete_run(run_id, (), (), ())


@pytest.mark.asyncio
async def test_inmemory_repository_fail_run_sets_failed_state_once():
    repo = InMemoryEnterpriseRepository()
    run_id = await repo.create_run(
        tenant_id="tenant-a",
        subscription_id="sub-a",
        requested_resource_ids=(),
        control_keys=("storage.secure_transfer",),
    )

    await repo.fail_run(run_id, "internal_error")
    run = await repo.get_run(run_id, "sub-a")
    assert run is not None
    assert run.state == "failed"
    assert run.reason_code == "internal_error"

    with pytest.raises(ValueError, match="immutable"):
        await repo.fail_run(run_id, "another")


def test_postgres_repository_uses_parameterized_sql_and_statement_timeout():
    executed: list[tuple[str, object]] = []

    class FakeCursor:
        def execute(self, sql: str, params=None):
            executed.append((sql, params))

        def fetchone(self):
            return {"run_id": "run-id"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeConn:
        autocommit = False

        def cursor(self):
            return FakeCursor()

        def commit(self):
            return None

        def rollback(self):
            return None

        def close(self):
            return None

    def fake_connection_factory():
        return FakeConn()

    repo = PostgresEnterpriseRepository(connection_factory=fake_connection_factory, statement_timeout_ms=30000)

    run_id = repo._create_run_sync(
        tenant_id="tenant-a",
        subscription_id="sub-a",
        requested_resource_ids=(),
        control_keys=("storage.secure_transfer",),
    )
    assert run_id == "run-id"
    assert executed[0][0].startswith("SET LOCAL statement_timeout")
    assert "%s" in executed[1][0]
    assert executed[1][1] is not None


def test_postgres_repository_rejects_autocommit_connections():
    class FakeCursor:
        def execute(self, sql: str, params=None):
            raise AssertionError("cursor should not be opened for autocommit connections")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeConn:
        autocommit = True

        def cursor(self):
            return FakeCursor()

        def commit(self):
            raise AssertionError("commit should not be reached")

        def rollback(self):
            raise AssertionError("rollback should not be reached")

        def close(self):
            return None

    repo = PostgresEnterpriseRepository(connection_factory=lambda: FakeConn(), statement_timeout_ms=30000)

    with pytest.raises(ValueError, match="autocommit"):
        repo._create_run_sync(
            tenant_id="tenant-a",
            subscription_id="sub-a",
            requested_resource_ids=(),
            control_keys=("storage.secure_transfer",),
        )


def test_postgres_repository_json_serializer_rejects_unknown_object_type():
    with pytest.raises(TypeError):
        PostgresEnterpriseRepository._json_dumps({"unsupported": object()})


def test_schema_contains_required_enterprise_tables_constraints_and_indexes():
    sql = (ROOT / "backend" / "scripts" / "01_schema.sql").read_text(encoding="utf-8")

    required_tables = [
        "control_definitions",
        "snapshot_runs",
        "evidence_records",
        "enterprise_evaluation_runs",
        "enterprise_collection_failures",
        "enterprise_verdicts",
        "remediation_runs",
        "remediation_artifacts",
    ]
    for table_name in required_tables:
        assert f"CREATE TABLE IF NOT EXISTS {table_name}" in sql

    assert "chk_enterprise_verdict_state" in sql
    assert "chk_enterprise_run_state" in sql
    assert "idx_enterprise_runs_subscription_started" in sql
    assert "idx_enterprise_verdicts_run_resource_control" in sql
    assert "idx_evidence_records_hash_provenance" in sql


def test_schema_does_not_drop_or_alter_legacy_result_contracts():
    sql = (ROOT / "backend" / "scripts" / "01_schema.sql").read_text(encoding="utf-8").lower()
    assert "drop table result_reports" not in sql
    assert "drop table result_resource_assessments" not in sql
    assert "drop table result_check_results" not in sql
