"""PostgreSQL repository for enterprise assessment runs."""

from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from dataclasses import is_dataclass
from datetime import date, datetime
from enum import Enum
from threading import Lock
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence

from psycopg2.extras import Json

from agent.db.connection import get_conn
from enterprise.adapters.base import CollectionFailure
from enterprise.domain import ControlDefinition, EvidenceRecord
from enterprise.repository import (
    CollectionFailureInput,
    ControlRecord,
    EnterpriseRepository,
    EvaluationVerdictInput,
    EvidenceProvenance,
    FindingRecord,
    RunRecord,
)


def _empty_counts() -> dict[str, int]:
    return {
        "pass": 0,
        "fail": 0,
        "unknown": 0,
        "not_applicable": 0,
        "exempted": 0,
        "manual_pending": 0,
    }


class PostgresEnterpriseRepository(EnterpriseRepository):
    def __init__(
        self,
        *,
        connection_factory: Callable[[], Any] | None = None,
        statement_timeout_ms: int = 30000,
        max_workers: int = 8,
    ) -> None:
        if statement_timeout_ms <= 0:
            raise ValueError("statement_timeout_ms must be positive")
        if max_workers <= 0:
            raise ValueError("max_workers must be positive")
        self._connection_factory = connection_factory or get_conn
        self._statement_timeout_ms = int(statement_timeout_ms)
        self._semaphore = asyncio.Semaphore(max_workers)
        self._lock = Lock()

    async def register_controls(self, registry: Mapping[str, ControlDefinition]) -> None:
        await self._run_db(self._register_controls_sync, registry)

    async def create_run(
        self,
        tenant_id: str,
        subscription_id: str,
        requested_resource_ids: Sequence[str],
        control_keys: Sequence[str],
    ) -> str:
        return await self._run_db(
            self._create_run_sync,
            tenant_id,
            subscription_id,
            tuple(requested_resource_ids),
            tuple(control_keys),
        )

    async def complete_run(
        self,
        run_id: str,
        evidence: Sequence[EvidenceRecord],
        verdicts: Sequence[EvaluationVerdictInput],
        collection_failures: Sequence[CollectionFailureInput | CollectionFailure],
    ) -> None:
        await self._run_db(
            self._complete_run_sync,
            run_id,
            tuple(evidence),
            tuple(verdicts),
            tuple(collection_failures),
        )

    async def fail_run(self, run_id: str, reason_code: str) -> None:
        await self._run_db(self._fail_run_sync, run_id, reason_code)

    async def get_run(self, run_id: str, subscription_id: str) -> RunRecord | None:
        return await self._run_db(self._get_run_sync, run_id, subscription_id)

    async def get_finding(self, finding_id: str, subscription_id: str) -> FindingRecord | None:
        return await self._run_db(self._get_finding_sync, finding_id, subscription_id)

    async def list_controls(self) -> tuple[ControlRecord, ...]:
        return await self._run_db(self._list_controls_sync)

    async def _run_db(self, fn: Callable[..., Any], *args: Any) -> Any:
        async with self._worker_slot():
            return await asyncio.to_thread(fn, *args)

    def _transactional_connection(self):
        conn = self._connection_factory()
        if getattr(conn, "autocommit", False):
            try:
                close = getattr(conn, "close", None)
                if callable(close):
                    close()
            finally:
                raise ValueError("connection_factory must return a connection with autocommit disabled")
        if hasattr(conn, "autocommit"):
            conn.autocommit = False
        return conn

    @asynccontextmanager
    async def _worker_slot(self):
        await self._semaphore.acquire()
        try:
            yield
        finally:
            self._semaphore.release()

    def _register_controls_sync(self, registry: Mapping[str, ControlDefinition]) -> None:
        conn = self._transactional_connection()
        try:
            with conn.cursor() as cur:
                # SET LOCAL only has effect inside a transaction.
                cur.execute("SET LOCAL statement_timeout = %s", (self._statement_timeout_ms,))
                for control in registry.values():
                    definition_doc = {
                        "key": control.key,
                        "version": control.version,
                        "resource_type": control.resource_type,
                        "evaluator_kind": control.evaluator_kind.value,
                    }
                    definition_json = self._json_dumps(definition_doc)
                    cur.execute(
                        """
                        INSERT INTO control_definitions
                            (control_key, control_version, definition, definition_hash)
                        VALUES (%s, %s, %s::jsonb, %s)
                        ON CONFLICT (control_key, control_version) DO NOTHING
                        """,
                        (
                            control.key,
                            control.version,
                            definition_json,
                            hashlib.sha256(definition_json.encode("utf-8")).hexdigest(),
                        ),
                    )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _create_run_sync(
        self,
        tenant_id: str,
        subscription_id: str,
        requested_resource_ids: Sequence[str],
        control_keys: Sequence[str],
    ) -> str:
        conn = self._transactional_connection()
        try:
            with conn.cursor() as cur:
                # SET LOCAL only has effect inside a transaction.
                cur.execute("SET LOCAL statement_timeout = %s", (self._statement_timeout_ms,))
                cur.execute(
                    """
                    INSERT INTO enterprise_evaluation_runs
                        (tenant_id, subscription_id, requested_resource_ids, control_keys, run_state)
                    VALUES (%s, %s, %s::jsonb, %s::jsonb, 'running')
                    RETURNING run_id
                    """,
                    (
                        tenant_id,
                        subscription_id,
                        self._json_dumps(list(requested_resource_ids)),
                        self._json_dumps(list(control_keys)),
                    ),
                )
                row = cur.fetchone()
            conn.commit()
            if isinstance(row, Mapping):
                return str(row["run_id"])
            return str(row[0])
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _complete_run_sync(
        self,
        run_id: str,
        evidence: Sequence[EvidenceRecord],
        verdicts: Sequence[EvaluationVerdictInput],
        collection_failures: Sequence[CollectionFailureInput | CollectionFailure],
    ) -> None:
        conn = self._transactional_connection()
        try:
            with conn.cursor() as cur:
                # SET LOCAL only has effect inside a transaction.
                cur.execute("SET LOCAL statement_timeout = %s", (self._statement_timeout_ms,))
                cur.execute(
                    "SELECT subscription_id, run_state FROM enterprise_evaluation_runs WHERE run_id = %s FOR UPDATE",
                    (run_id,),
                )
                row = cur.fetchone()
                if row is None:
                    raise KeyError(f"run not found: {run_id}")
                current_state = row["run_state"] if isinstance(row, Mapping) else row[1]
                subscription_id = row["subscription_id"] if isinstance(row, Mapping) else row[0]
                if current_state != "running":
                    raise ValueError("run is immutable once finalized")

                provenance: dict[str, EvidenceProvenance] = {}
                for item in evidence:
                    provenance[item.content_hash] = EvidenceProvenance(
                        source_kind=item.source_kind,
                        source_reference=item.source_reference,
                        source_version=item.source_version,
                        observed_at=item.observed_at,
                        content_hash=item.content_hash,
                    )
                    cur.execute(
                        """
                        INSERT INTO evidence_records
                            (run_id, resource_id, source_kind, source_reference, source_version, observed_at, content_hash, payload)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT DO NOTHING
                        """,
                        (
                            run_id,
                            item.resource_id,
                            item.source_kind,
                            item.source_reference,
                            item.source_version,
                            item.observed_at,
                            item.content_hash,
                            Json(self._json_safe(item.payload)),
                        ),
                    )

                counts = _empty_counts()
                finding_rows: list[tuple[str, str, str, str, str, list[str], str]] = []
                for verdict_input in verdicts:
                    verdict = verdict_input.verdict
                    counts[verdict.state.value] += 1
                    cur.execute(
                        """
                        INSERT INTO enterprise_verdicts
                            (run_id, resource_id, control_key, verdict_state, reason_code, evidence_hashes)
                        VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                        RETURNING finding_id
                        """,
                        (
                            run_id,
                            verdict_input.resource_id,
                            verdict.control_key,
                            verdict.state.value,
                            verdict.reason_code,
                            self._json_dumps(list(verdict.evidence_hashes)),
                        ),
                    )
                    finding_row = cur.fetchone()
                    finding_id = str(finding_row["finding_id"] if isinstance(finding_row, Mapping) else finding_row[0])
                    finding_rows.append(
                        (
                            finding_id,
                            verdict_input.resource_id,
                            verdict.control_key,
                            verdict.state.value,
                            verdict.reason_code,
                            list(verdict.evidence_hashes),
                            subscription_id,
                        )
                    )

                normalized_failures = []
                for failure in collection_failures:
                    if isinstance(failure, CollectionFailureInput):
                        normalized_failures.append(failure)
                    else:
                        normalized_failures.append(
                            CollectionFailureInput(
                                reason_code=failure.reason_code,
                                source_kind=failure.source_kind,
                                source_reference=failure.source_reference,
                                status_code=failure.status_code,
                                retry_after=failure.retry_after,
                                detail=failure.detail,
                            )
                        )

                for failure in normalized_failures:
                    cur.execute(
                        """
                        INSERT INTO enterprise_collection_failures
                            (run_id, source_kind, source_reference, reason_code, status_code, retry_after, detail)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            run_id,
                            failure.source_kind,
                            failure.source_reference,
                            failure.reason_code,
                            failure.status_code,
                            failure.retry_after,
                            failure.detail,
                        ),
                    )

                next_state = "partial" if normalized_failures else "completed"
                cur.execute(
                    """
                    UPDATE enterprise_evaluation_runs
                    SET run_state = %s,
                        completed_at = NOW(),
                        reason_code = NULL,
                        verdict_counts = %s::jsonb
                    WHERE run_id = %s
                    """,
                    (next_state, self._json_dumps(counts), run_id),
                )

            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _fail_run_sync(self, run_id: str, reason_code: str) -> None:
        conn = self._transactional_connection()
        try:
            with conn.cursor() as cur:
                # SET LOCAL only has effect inside a transaction.
                cur.execute("SET LOCAL statement_timeout = %s", (self._statement_timeout_ms,))
                cur.execute(
                    "SELECT run_state FROM enterprise_evaluation_runs WHERE run_id = %s FOR UPDATE",
                    (run_id,),
                )
                row = cur.fetchone()
                if row is None:
                    raise KeyError(f"run not found: {run_id}")
                state = row["run_state"] if isinstance(row, Mapping) else row[0]
                if state != "running":
                    raise ValueError("run is immutable once finalized")
                cur.execute(
                    """
                    UPDATE enterprise_evaluation_runs
                    SET run_state = 'failed', completed_at = NOW(), reason_code = %s
                    WHERE run_id = %s
                    """,
                    (reason_code, run_id),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _get_run_sync(self, run_id: str, subscription_id: str) -> RunRecord | None:
        conn = self._transactional_connection()
        try:
            with conn.cursor() as cur:
                # SET LOCAL only has effect inside a transaction.
                cur.execute("SET LOCAL statement_timeout = %s", (self._statement_timeout_ms,))
                cur.execute(
                    """
                    SELECT run_id, tenant_id, subscription_id, run_state, requested_resource_ids,
                           control_keys, started_at, completed_at, reason_code, verdict_counts
                    FROM enterprise_evaluation_runs
                    WHERE run_id = %s AND LOWER(subscription_id) = LOWER(%s)
                    """,
                    (run_id, subscription_id),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                run = self._row_to_run(cur, row)
                return run
        finally:
            conn.close()

    def _get_finding_sync(self, finding_id: str, subscription_id: str) -> FindingRecord | None:
        conn = self._transactional_connection()
        try:
            with conn.cursor() as cur:
                # SET LOCAL only has effect inside a transaction.
                cur.execute("SET LOCAL statement_timeout = %s", (self._statement_timeout_ms,))
                cur.execute(
                    """
                    SELECT v.finding_id, v.run_id, r.subscription_id, v.resource_id, v.control_key,
                           v.verdict_state, v.reason_code, v.evidence_hashes
                    FROM enterprise_verdicts v
                    JOIN enterprise_evaluation_runs r ON r.run_id = v.run_id
                    WHERE v.finding_id = %s AND LOWER(r.subscription_id) = LOWER(%s)
                    """,
                    (finding_id, subscription_id),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                values = self._row_as_mapping(row)
                return FindingRecord(
                    finding_id=str(values["finding_id"]),
                    run_id=str(values["run_id"]),
                    subscription_id=str(values["subscription_id"]),
                    resource_id=str(values["resource_id"]),
                    control_key=str(values["control_key"]),
                    verdict_state=str(values["verdict_state"]),
                    reason_code=str(values["reason_code"]),
                    evidence_hashes=tuple(values.get("evidence_hashes") or ()),
                    provenance=(),
                )
        finally:
            conn.close()

    def _list_controls_sync(self) -> tuple[ControlRecord, ...]:
        conn = self._transactional_connection()
        try:
            with conn.cursor() as cur:
                # SET LOCAL only has effect inside a transaction.
                cur.execute("SET LOCAL statement_timeout = %s", (self._statement_timeout_ms,))
                cur.execute(
                    """
                    SELECT control_key, control_version, definition, definition_hash
                    FROM control_definitions
                    ORDER BY control_key, control_version
                    """
                )
                rows = cur.fetchall()
                controls: list[ControlRecord] = []
                for row in rows:
                    values = self._row_as_mapping(row)
                    definition = values.get("definition") or {}
                    controls.append(
                        ControlRecord(
                            control_key=str(values["control_key"]),
                            version=str(values["control_version"]),
                            resource_type=str(definition.get("resource_type") or ""),
                            evaluator_kind=str(definition.get("evaluator_kind") or ""),
                            definition=MappingProxyType(dict(definition)),
                            definition_hash=str(values["definition_hash"]),
                        )
                    )
                return tuple(controls)
        finally:
            conn.close()

    def _row_to_run(self, cursor: Any, row: Any) -> RunRecord:
        values = self._row_as_mapping(row)
        run_id = str(values["run_id"])
        cursor.execute(
            """
            SELECT finding_id, run_id, resource_id, control_key, verdict_state, reason_code, evidence_hashes
            FROM enterprise_verdicts
            WHERE run_id = %s
            ORDER BY created_at ASC
            """,
            (run_id,),
        )
        findings_rows = cursor.fetchall()
        findings = tuple(
            FindingRecord(
                finding_id=str(self._row_as_mapping(item)["finding_id"]),
                run_id=str(self._row_as_mapping(item)["run_id"]),
                subscription_id=str(values["subscription_id"]),
                resource_id=str(self._row_as_mapping(item)["resource_id"]),
                control_key=str(self._row_as_mapping(item)["control_key"]),
                verdict_state=str(self._row_as_mapping(item)["verdict_state"]),
                reason_code=str(self._row_as_mapping(item)["reason_code"]),
                evidence_hashes=tuple(self._row_as_mapping(item).get("evidence_hashes") or ()),
                provenance=(),
            )
            for item in findings_rows
        )

        cursor.execute(
            """
            SELECT source_kind, source_reference, reason_code, status_code, retry_after, detail
            FROM enterprise_collection_failures
            WHERE run_id = %s
            ORDER BY created_at ASC
            """,
            (run_id,),
        )
        failure_rows = cursor.fetchall()
        failures = tuple(
            CollectionFailureInput(
                reason_code=str(self._row_as_mapping(item)["reason_code"]),
                source_kind=str(self._row_as_mapping(item)["source_kind"]),
                source_reference=str(self._row_as_mapping(item)["source_reference"]),
                status_code=self._row_as_mapping(item).get("status_code"),
                retry_after=self._row_as_mapping(item).get("retry_after"),
                detail=str(self._row_as_mapping(item).get("detail") or ""),
            )
            for item in failure_rows
        )

        verdict_counts = values.get("verdict_counts") or _empty_counts()
        started_at = values["started_at"]
        completed_at = values.get("completed_at")
        return RunRecord(
            run_id=run_id,
            tenant_id=str(values["tenant_id"]),
            subscription_id=str(values["subscription_id"]),
            state=str(values["run_state"]),
            requested_resource_ids=tuple(values.get("requested_resource_ids") or ()),
            control_keys=tuple(values.get("control_keys") or ()),
            started_at=started_at,
            completed_at=completed_at,
            reason_code=values.get("reason_code"),
            verdict_counts=MappingProxyType(dict(verdict_counts)),
            evidence_provenance=(),
            findings=findings,
            collection_failures=failures,
        )

    @staticmethod
    def _row_as_mapping(row: Any) -> Mapping[str, Any]:
        if isinstance(row, Mapping):
            return row
        if hasattr(row, "keys"):
            keys = list(row.keys())
            return {key: row[key] for key in keys}
        raise TypeError("cursor row must provide mapping access")

    @staticmethod
    def _json_safe(value: Any) -> Any:
        if isinstance(value, MappingProxyType):
            return {key: PostgresEnterpriseRepository._json_safe(item) for key, item in value.items()}
        if isinstance(value, Mapping):
            return {str(key): PostgresEnterpriseRepository._json_safe(item) for key, item in value.items()}
        if isinstance(value, tuple):
            return [PostgresEnterpriseRepository._json_safe(item) for item in value]
        if isinstance(value, list):
            return [PostgresEnterpriseRepository._json_safe(item) for item in value]
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if is_dataclass(value):
            return PostgresEnterpriseRepository._json_safe(value.__dict__)
        raise TypeError(f"unsupported JSON value type: {type(value).__name__}")

    @staticmethod
    def _json_dumps(value: Any) -> str:
        safe_value = PostgresEnterpriseRepository._json_safe(value)
        return json.dumps(safe_value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
