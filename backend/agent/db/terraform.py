"""
PostgreSQL 기반 Terraform 실행 이력 저장 헬퍼.

terraform_runs + terraform_run_files 테이블을 대상으로 저장 / 조회 / 삭제를 제공한다.
"""
from __future__ import annotations

from typing import Optional

from .connection import get_conn, is_db_configured

__all__ = [
    "is_db_configured",
    "save_terraform_run",
    "list_runs",
    "get_run_file",
    "delete_run",
]


def _infer_file_type(file_name: str) -> str:
    name = file_name.lower()
    if name.endswith(".tf"):
        return "tf"
    if name.endswith(".md"):
        return "md"
    return "other"


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def save_terraform_run(
    scope_id: Optional[str],
    run_timestamp: str,
    files: list[dict],
    resources_count: int = 0,
    recommendations_count: int = 0,
    source_report_ids: Optional[list] = None,
    source_resource_names: Optional[list] = None,
    source_diagnosis_ids: Optional[list] = None,
) -> int:
    """
    terraform_runs + terraform_run_files 에 저장하고 run_id 반환.

    Parameters
    ----------
    scope_id : str | None
        Azure 구독 ID
    run_timestamp : str
        실행 타임스탬프 (예: 20260421_123456)
    files : list[dict]
        [{"file_name": str, "content": str, "file_type": str(optional)}, ...]
    resources_count : int
        대상 리소스 수
    recommendations_count : int
        적용된 권고 건수
    source_report_ids : list | None
        사용된 result_reports.report_id 목록
    source_resource_names : list | None
        대상 리소스명 목록
    source_diagnosis_ids : list | None
        연관 trace_id 목록 (레거시)

    Returns
    -------
    int  run_id
    """
    import json
    conn = get_conn()
    try:
        with conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO terraform_runs (
                    scope_id, run_timestamp, source_diagnosis_ids,
                    resources_count, recommendations_count,
                    source_report_ids, source_resource_names
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    scope_id,
                    run_timestamp,
                    json.dumps(source_diagnosis_ids or [], ensure_ascii=False),
                    resources_count,
                    recommendations_count,
                    json.dumps(source_report_ids or [], ensure_ascii=False),
                    json.dumps(source_resource_names or [], ensure_ascii=False),
                ),
            )
            run_id: int = cur.fetchone()[0]

            for f in files:
                file_name = f["file_name"]
                content = f.get("content", "")
                file_type = f.get("file_type") or _infer_file_type(file_name)
                cur.execute(
                    """
                    INSERT INTO terraform_run_files (run_id, file_name, file_type, content)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (run_id, file_name, file_type, content),
                )
            cur.close()
        return run_id
    finally:
        conn.close()


def list_runs(
    subscription_id: Optional[str] = None,
    limit: int = 30,
) -> list[dict]:
    """
    terraform_runs 목록 반환.

    Returns
    -------
    list[dict]
        [{run_id, scope_id, run_timestamp, created_at, files: [file_name, ...]}, ...]
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        if subscription_id:
            cur.execute(
                """
                SELECT id, scope_id, run_timestamp, created_at,
                       resources_count, recommendations_count,
                       source_report_ids, source_resource_names, source_diagnosis_ids
                FROM terraform_runs
                WHERE scope_id = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (subscription_id, limit),
            )
        else:
            cur.execute(
                """
                SELECT id, scope_id, run_timestamp, created_at,
                       resources_count, recommendations_count,
                       source_report_ids, source_resource_names, source_diagnosis_ids
                FROM terraform_runs
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (limit,),
            )
        rows = cur.fetchall()
        result = []
        for (run_id, scope_id, run_ts, created_at,
             resources_count, recommendations_count,
             source_report_ids, source_resource_names, source_diagnosis_ids) in rows:
            import json as _json
            cur.execute(
                "SELECT file_name FROM terraform_run_files WHERE run_id = %s ORDER BY id",
                (run_id,),
            )
            file_names = [r[0] for r in cur.fetchall()]
            parsed_report_ids = (
                source_report_ids if isinstance(source_report_ids, list)
                else _json.loads(source_report_ids) if isinstance(source_report_ids, str)
                else []
            )
            parsed_diagnosis_ids = (
                source_diagnosis_ids if isinstance(source_diagnosis_ids, list)
                else _json.loads(source_diagnosis_ids) if isinstance(source_diagnosis_ids, str)
                else []
            )
            result.append({
                "run_id": run_id,
                "subscription_id": scope_id or "legacy",
                "timestamp": run_ts or "",
                "files": file_names,
                "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at),
                "source": "db",
                "resources_count": resources_count or 0,
                "recommendations_count": recommendations_count or 0,
                # 최신 컬럼(source_report_ids) 우선, 비어 있으면 레거시(source_diagnosis_ids) 폴백
                "source_report_ids": parsed_report_ids or parsed_diagnosis_ids,
                "source_resource_names": (
                    source_resource_names if isinstance(source_resource_names, list)
                    else _json.loads(source_resource_names) if isinstance(source_resource_names, str)
                    else []
                ),
                "source_diagnosis_ids": parsed_diagnosis_ids,
            })
        cur.close()
        return result
    finally:
        conn.close()


def get_run_file(
    scope_id: Optional[str],
    run_timestamp: str,
    file_name: str,
) -> Optional[dict]:
    """
    특정 run의 파일 내용 반환.

    Returns
    -------
    dict | None
        {"run_id": int, "file_name": str, "file_type": str, "content": str}
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        if scope_id and scope_id.lower() != "legacy":
            cur.execute(
                """
                SELECT rf.run_id, rf.file_name, rf.file_type, rf.content
                FROM terraform_run_files rf
                JOIN terraform_runs r ON r.id = rf.run_id
                WHERE r.scope_id = %s AND r.run_timestamp = %s AND rf.file_name = %s
                ORDER BY r.created_at DESC
                LIMIT 1
                """,
                (scope_id, run_timestamp, file_name),
            )
        else:
            cur.execute(
                """
                SELECT rf.run_id, rf.file_name, rf.file_type, rf.content
                FROM terraform_run_files rf
                JOIN terraform_runs r ON r.id = rf.run_id
                WHERE r.run_timestamp = %s AND rf.file_name = %s
                ORDER BY r.created_at DESC
                LIMIT 1
                """,
                (run_timestamp, file_name),
            )
        row = cur.fetchone()
        cur.close()
        if not row:
            return None
        return {
            "run_id": row[0],
            "file_name": row[1],
            "file_type": row[2],
            "content": row[3],
        }
    finally:
        conn.close()


def delete_run(
    scope_id: Optional[str],
    run_timestamp: str,
) -> int:
    """
    특정 run 삭제 (CASCADE로 run_files도 삭제).

    Returns
    -------
    int  삭제된 run 수
    """
    conn = get_conn()
    try:
        with conn:
            cur = conn.cursor()
            if scope_id and scope_id.lower() != "legacy":
                cur.execute(
                    "DELETE FROM terraform_runs WHERE scope_id = %s AND run_timestamp = %s",
                    (scope_id, run_timestamp),
                )
            else:
                cur.execute(
                    "DELETE FROM terraform_runs WHERE run_timestamp = %s",
                    (run_timestamp,),
                )
            count = cur.rowcount
            cur.close()
        return count
    finally:
        conn.close()
