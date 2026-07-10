"""
PostgreSQL 기반 평가 결과 저장/조회 헬퍼.

result_reports, result_resource_assessments, result_check_results, result_file
테이블에 ResourceAssessment 목록을 삽입한다.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
from typing import Any, Optional

from agent.subscription_scope import normalize_subscription_id

from .connection import get_conn, is_db_configured

__all__ = [
    "is_db_configured",
    "save_assessment_report",
    "list_reports",
    "get_dashboard_stats",
    "get_score_range_resources",
    "get_assessment_charts_summary",
    "get_global_kpi",
    "get_trend_date_resources",
    "get_report_detail",
    "get_file_detail",
    "list_individual_files",
]


# ──────────────────────────────────────────────────────────────────────────────
# status 정규화
# ──────────────────────────────────────────────────────────────────────────────
_STATUS_MAP = {
    "pass": "pass",
    "fail": "fail",
    "warning": "warning",
    "manual_review": "manual_review",
    "n/a": "n_a",
    "n_a": "n_a",
}
_SEVERITY_MAP = {"low": "low", "medium": "medium", "high": "high", "critical": "critical"}


def _norm_status(val: str) -> str:
    return _STATUS_MAP.get((val or "").lower().strip(), "fail")


def _norm_severity(val: str) -> Optional[str]:
    return _SEVERITY_MAP.get((val or "").lower().strip())


def _resolve_subscription_name(subscription_id: Optional[str]) -> Optional[str]:
    """평가 시점의 구독 표시명을 세션 값 우선, ARM 구독 목록 폴백으로 해석."""
    sub_norm = normalize_subscription_id(subscription_id or "")
    if not sub_norm:
        return None

    try:
        from chat.tools.azure_session import (
            get_session_subscription_id,
            get_session_subscription_name,
        )

        session_sub = normalize_subscription_id(get_session_subscription_id() or "")
        session_name = (get_session_subscription_name() or "").strip()
        if session_sub == sub_norm and session_name:
            return session_name
    except Exception:
        pass

    try:
        from agent.azure_resource_reader import AzureResourceReader

        for entry in AzureResourceReader.list_account_entries():
            entry_sub = normalize_subscription_id(str(entry.get("id") or ""))
            if entry_sub == sub_norm:
                return (str(entry.get("name") or "").strip() or None)
    except Exception:
        pass

    return None


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def save_assessment_report(
    assessments: list,
    subscription_id: Optional[str] = None,
    report_md: Optional[str] = None,
    report_html: Optional[str] = None,
) -> int:
    """
    ResourceAssessment 목록을 DB에 저장하고 report_id를 반환한다.

    Parameters
    ----------
    assessments : list[ResourceAssessment]
        평가 결과 목록
    subscription_id : str | None
        구독 ID (result_resource_assessments.subscription_id)

    Returns
    -------
    int
        생성된 result_reports.report_id
    """
    if not assessments:
        raise ValueError("assessments is empty")

    # ── 구독 이름 조회 (세션 값 우선, ARM 실패 시 None으로 무시) ────────────────
    subscription_name = _resolve_subscription_name(subscription_id)

    now = datetime.now(tz=KST)

    # ── 집계 ──────────────────────────────────────────────────────────────────
    total_resources = len(assessments)
    total_checks = sum(a.total_checks for a in assessments)
    total_passed = sum(a.passed_checks for a in assessments)
    total_failed = sum(a.failed_checks for a in assessments)
    # warning_checks 속성이 있을 때만 합산
    total_warnings = sum(getattr(a, "warning_checks", 0) for a in assessments)
    total_manual = sum(
        sum(1 for r in a.results if _norm_status(r.status.value if hasattr(r.status, "value") else r.status) == "manual_review")
        for a in assessments
    )
    scored = [a for a in assessments if a.total_checks > 0]
    avg_score = sum(a.overall_score for a in scored) / len(scored) if scored else 0.0
    pass_rate = (total_passed / total_checks * 100) if total_checks else 0.0
    fail_rate = (total_failed / total_checks * 100) if total_checks else 0.0
    manual_rate = (total_manual / total_checks * 100) if total_checks else 0.0

    conn = get_conn()
    try:
        with conn:
            cur = conn.cursor()

            # ── result_reports ────────────────────────────────────────────────
            cur.execute(
                """
                INSERT INTO result_reports (
                    generated_at, report_version,
                    total_resources,
                    summary_total_checks, summary_total_passed,
                    summary_total_failed, summary_total_warnings,
                    summary_total_manual,
                    summary_average_score, summary_pass_rate,
                    summary_fail_rate, summary_manual_rate,
                    report_md, report_html
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                ) RETURNING report_id
                """,
                (
                    now, "1.0",
                    total_resources,
                    total_checks, total_passed,
                    total_failed, total_warnings,
                    total_manual,
                    round(avg_score, 2), round(pass_rate, 2),
                    round(fail_rate, 2), round(manual_rate, 2),
                    report_md, report_html,
                ),
            )
            report_id: int = cur.fetchone()[0]

            for a in assessments:
                sub_id = subscription_id
                # resource_id 에서 구독 ID 추출 (ARM ID 형식)
                if not sub_id and a.resource_id and a.resource_id.startswith("/subscriptions/"):
                    parts = a.resource_id.split("/")
                    if len(parts) > 2:
                        sub_id = parts[2]

                a_time_str = getattr(a, "assessment_time", None)
                a_time = None
                if a_time_str:
                    try:
                        a_time = datetime.fromisoformat(a_time_str)
                    except (ValueError, TypeError):
                        pass

                a_warnings = getattr(a, "warning_checks", 0)

                # 타입 불일치 판단: 체크 결과 중 '선택 체크리스트 적용 여부' 항목 존재 여부
                a_type_mismatch = any(
                    (getattr(r, "check_question", "") == "선택 체크리스트 적용 여부")
                    for r in a.results
                )

                # ── result_resource_assessments ──────────────────────────────
                cur.execute(
                    """
                    INSERT INTO result_resource_assessments (
                        report_id, subscription_id, subscription_name,
                        resource_id, resource_name, resource_type,
                        resource_group, location, assessment_time,
                        overall_score,
                        summary_total_checks, summary_passed,
                        summary_failed, summary_warnings,
                        is_type_mismatch
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    ) RETURNING assessment_id
                    """,
                    (
                        report_id, sub_id, subscription_name,
                        a.resource_id, a.resource_name, a.resource_type,
                        a.resource_group, a.location, a_time,
                        round(a.overall_score, 2),
                        a.total_checks, a.passed_checks,
                        a.failed_checks, a_warnings,
                        a_type_mismatch,
                    ),
                )
                assessment_id: int = cur.fetchone()[0]

                # ── result_check_results ─────────────────────────────────────
                for r in a.results:
                    raw_status = r.status.value if hasattr(r.status, "value") else str(r.status)
                    status = _norm_status(raw_status)
                    severity = _norm_severity(r.severity)

                    ev = r.evidence if isinstance(r.evidence, dict) else {}
                    evidence_property = str(ev.get("property_checked") or ev.get("property") or "")[:300] or None
                    evidence_actual = str(ev.get("actual_value") or ev.get("actual") or "")[:2000] or None
                    evidence_expected = str(ev.get("expected_value") or ev.get("expected") or "")[:2000] or None

                    checklist_name = str(getattr(r, "checklist_name", "") or "")[:200] or None

                    cur.execute(
                        """
                        INSERT INTO result_check_results (
                            assessment_id, status, severity,
                            question, finding, recommendation,
                            evidence_property, evidence_actual, evidence_expected,
                            checklist_name
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                        )
                        """,
                        (
                            assessment_id, status, severity,
                            r.check_question, r.finding, r.recommendation,
                            evidence_property, evidence_actual, evidence_expected,
                            checklist_name,
                        ),
                    )

                # ── result_file (raw JSONB) ──────────────────────────────────
                details = a.to_dict() if hasattr(a, "to_dict") else {}
                result_status_raw = "pass"
                if a.failed_checks > 0:
                    result_status_raw = "fail"
                elif a_warnings > 0:
                    result_status_raw = "warning"

                cur.execute(
                    """
                    INSERT INTO result_file (
                        report_id, scope_id, resource_id, resource_name,
                        resource_type, resource_group,
                        result_status, overall_score, details,
                        report_md, report_html
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        report_id, sub_id, a.resource_id, a.resource_name,
                        a.resource_type, a.resource_group,
                        result_status_raw, round(a.overall_score, 2),
                        json.dumps(details, ensure_ascii=False, default=str),
                        report_md, report_html,
                    ),
                )

            cur.close()
        return report_id
    finally:
        conn.close()


def list_reports(
    subscription_id: Optional[str] = None,
    limit: int = 30,
) -> list[dict]:
    """result_file 기준으로 리포트 목록을 반환한다. (result_reports JOIN)"""
    conn = get_conn()
    try:
        cur = conn.cursor()
        if subscription_id:
            cur.execute(
                """
                SELECT
                    f.report_id,
                    r.generated_at,
                    COUNT(f.id)             AS total_resources,
                    r.summary_average_score,
                    r.summary_total_checks,
                    r.summary_total_passed,
                    r.summary_total_failed,
                    r.summary_total_warnings
                FROM result_file f
                JOIN result_reports r ON r.report_id = f.report_id
                WHERE f.scope_id = %s
                GROUP BY f.report_id, r.generated_at,
                    r.summary_average_score, r.summary_total_checks,
                    r.summary_total_passed, r.summary_total_failed,
                    r.summary_total_warnings
                ORDER BY r.generated_at DESC
                LIMIT %s
                """,
                (subscription_id, limit),
            )
        else:
            cur.execute(
                """
                SELECT
                    f.report_id,
                    r.generated_at,
                    COUNT(f.id)             AS total_resources,
                    r.summary_average_score,
                    r.summary_total_checks,
                    r.summary_total_passed,
                    r.summary_total_failed,
                    r.summary_total_warnings
                FROM result_file f
                JOIN result_reports r ON r.report_id = f.report_id
                GROUP BY f.report_id, r.generated_at,
                    r.summary_average_score, r.summary_total_checks,
                    r.summary_total_passed, r.summary_total_failed,
                    r.summary_total_warnings
                ORDER BY r.generated_at DESC
                LIMIT %s
                """,
                (limit,),
            )
        rows = cur.fetchall()
        cur.close()
        result = []
        for row in rows:
            rid, gen_at, total_res, avg_score, total_chk, passed, failed, warnings = row
            if hasattr(gen_at, "isoformat"):
                gen_at_kst = gen_at.astimezone(KST) if gen_at.tzinfo else gen_at.replace(tzinfo=timezone.utc).astimezone(KST)
                gen_at_str = gen_at_kst.isoformat()
            else:
                gen_at_str = str(gen_at)
            result.append({
                "report_id": rid,
                "generated_at": gen_at_str,
                "total_resources": int(total_res),
                "summary_average_score": float(avg_score or 0),
                "summary_total_checks": total_chk,
                "summary_total_passed": passed,
                "summary_total_failed": failed,
                "summary_total_warnings": warnings,
            })
        return result
    finally:
        conn.close()


def get_dashboard_stats(
    subscription_id: Optional[str] = None,
    period_days: int = 15,
    resource_group: Optional[str] = None,
    resource_type: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> dict:
    """대시보드용 통계를 DB에서 직접 집계해 반환한다.

    ``subscriptions`` 필드: 동일 요청 기간(start_date / end_date / period_days) 안에
    평가 행이 있는 구독의 distinct 목록(구독·RG·유형 헤더 필터와 무관하게 기간 전체).

    7개 쿼리를 ThreadPoolExecutor로 병렬 실행해 응답 속도를 개선한다.
    - _q_trend              : 일자별 평균 점수 추이
    - _q_kpi_agg            : KPI / auto_manual / pass_fail 집계 (필터·기간 일치)
    - _q_filters            : RG·유형 필터 선택지
    - _q_resources          : 리소스별 최신 점수 (score_distribution / worst 계산용)
    - _q_distribution       : 점수 구간 분포
    - _q_avg_score_resources: 평균 점수 팝업용 평가 행 목록
    - _q_subscription_dropdown: 기간 내 구독 필터 목록(distinct)
    """
    from datetime import timedelta

    if start_date:
        try:
            period_start = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=KST)
        except ValueError:
            period_start = datetime.now(tz=KST) - timedelta(days=period_days)
    else:
        period_start = datetime.now(tz=KST) - timedelta(days=period_days)

    if end_date:
        try:
            period_end: Optional[datetime] = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=KST) + timedelta(days=1)
        except ValueError:
            period_end = None
    else:
        period_end = None

    end_params: list = [period_end] if period_end else []
    end_clause = "AND rr.generated_at < %s" if period_end else ""
    rr_end_clause = "AND generated_at < %s" if period_end else ""
    sub_clause = "AND rra.subscription_id = %s" if subscription_id else ""
    base_params_sub: list = [period_start] + end_params + ([subscription_id] if subscription_id else [])

    # ── Q1: 일별 추이 ─────────────────────────────────────────────────────────
    def _q_trend():
        rg_cl = "AND rra.resource_group = %s" if resource_group else ""
        rt_cl = "AND LOWER(rra.resource_type) LIKE %s" if resource_type else ""
        params: list = [period_start] + end_params + ([subscription_id] if subscription_id else [])
        if resource_group:
            params.append(resource_group)
        if resource_type:
            params.append(f"%{resource_type.lower()}%")
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT
                        to_char(rr.generated_at AT TIME ZONE 'Asia/Seoul', 'YYYY-MM-DD') AS dt,
                        ROUND(AVG(rra.overall_score)::numeric, 1)
                    FROM result_resource_assessments rra
                    JOIN result_reports rr ON rr.report_id = rra.report_id
                    WHERE rr.generated_at >= %s
                      {end_clause}
                      AND rra.summary_total_checks > 0
                      AND NOT rra.is_type_mismatch
                      {sub_clause}
                      {rg_cl}
                      {rt_cl}
                    GROUP BY dt
                    ORDER BY dt ASC
                    """,
                    params,
                )
                return [{"date": r[0], "score": float(r[1])} for r in cur.fetchall()]
        finally:
            conn.close()

    # ── Q2: KPI 집계 (구독·기간·RG·유형 필터 일치) ────────────────────────────
    def _q_kpi_agg():
        rg_cl = "AND rra.resource_group = %s" if resource_group else ""
        rt_cl = "AND LOWER(rra.resource_type) LIKE %s" if resource_type else ""
        join_params: list = [period_start] + end_params + ([subscription_id] if subscription_id else [])
        if resource_group:
            join_params.append(resource_group)
        if resource_type:
            join_params.append(f"%{resource_type.lower()}%")
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                # 리포트 수·리소스 수: 필터에 해당하는 평가 행이 있는 범위만
                cur.execute(
                    f"""
                    WITH base AS (
                        SELECT
                            rr.report_id AS report_id,
                            rra.resource_name AS resource_name,
                            COALESCE(rra.resource_group, '') AS rg
                        FROM result_resource_assessments rra
                        INNER JOIN result_reports rr ON rr.report_id = rra.report_id
                        WHERE rr.generated_at >= %s
                          {end_clause}
                          {sub_clause}
                          {rg_cl}
                          {rt_cl}
                    )
                    SELECT
                        COALESCE((SELECT COUNT(DISTINCT report_id) FROM base), 0)::bigint,
                        COALESCE((
                            SELECT COUNT(*)::bigint
                            FROM (SELECT DISTINCT resource_name, rg FROM base) s
                        ), 0)::bigint
                    """,
                    join_params,
                )
                scope_row = cur.fetchone()
                total_reports_scoped = int(scope_row[0] or 0)
                total_resources_scoped = int(scope_row[1] or 0)

                # 수동 점검 수: 필터에 걸린 리포트의 summary_total_manual 합 (리포트당 1회)
                cur.execute(
                    f"""
                    SELECT COALESCE(SUM(rr.summary_total_manual), 0)
                    FROM result_reports rr
                    WHERE rr.report_id IN (
                        SELECT DISTINCT rra.report_id
                        FROM result_resource_assessments rra
                        INNER JOIN result_reports rr2 ON rr2.report_id = rra.report_id
                        WHERE rr2.generated_at >= %s
                          {end_clause}
                          {sub_clause}
                          {rg_cl}
                          {rt_cl}
                    )
                    """,
                    join_params,
                )
                manual_row = cur.fetchone()
                total_manual_scoped = int(manual_row[0] or 0)

                # total_checks, pass_fail 건수 + avg_score (타입 불일치·0체크 제외)
                cur.execute(
                    f"""
                    SELECT
                        COALESCE(SUM(rra.summary_total_checks), 0),
                        COALESCE(SUM(rra.summary_passed), 0),
                        COALESCE(SUM(rra.summary_failed), 0),
                        COALESCE(SUM(rra.summary_warnings), 0),
                        COALESCE(ROUND(AVG(rra.overall_score)::numeric, 1), 0)
                    FROM result_resource_assessments rra
                    JOIN result_reports rr ON rr.report_id = rra.report_id
                    WHERE rr.generated_at >= %s
                      {end_clause}
                      AND rra.summary_total_checks > 0
                      AND NOT rra.is_type_mismatch
                      {sub_clause}
                      {rg_cl}
                      {rt_cl}
                    """,
                    join_params,
                )
                rra_row = cur.fetchone()

                # 성공/실패 카드 분모 외 표시용 (체크 0 또는 타입 불일치 행 수)
                cur.execute(
                    f"""
                    SELECT COALESCE(COUNT(*)::bigint, 0)
                    FROM result_resource_assessments rra
                    JOIN result_reports rr ON rr.report_id = rra.report_id
                    WHERE rr.generated_at >= %s
                      {end_clause}
                      {sub_clause}
                      {rg_cl}
                      {rt_cl}
                      AND (rra.summary_total_checks = 0 OR rra.is_type_mismatch)
                    """,
                    join_params,
                )
                tm_row = cur.fetchone()
                type_mismatch_count = int(tm_row[0] or 0)

                kpi = (
                    total_reports_scoped,
                    total_resources_scoped,
                    int(rra_row[0] or 0),
                    total_manual_scoped,
                    int(rra_row[1] or 0),
                    int(rra_row[2] or 0),
                    int(rra_row[3] or 0),
                    type_mismatch_count,
                )
                return kpi, float(rra_row[4]) if rra_row and rra_row[4] else 0.0
        finally:
            conn.close()

    # ── Q3: 필터 선택지 (RG / 유형) ───────────────────────────────────────────
    def _q_filters():
        rt_cl_for_groups = "AND LOWER(rra.resource_type) LIKE %s" if resource_type else ""
        rg_filter_params: list = list(base_params_sub)
        if resource_type:
            rg_filter_params.append(f"%{resource_type.lower()}%")

        rg_cl_for_types = "AND rra.resource_group = %s" if resource_group else ""
        rt_filter_params: list = list(base_params_sub)
        if resource_group:
            rt_filter_params.append(resource_group)
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT DISTINCT rra.resource_group
                    FROM result_resource_assessments rra
                    JOIN result_reports rr ON rr.report_id = rra.report_id
                    WHERE rr.generated_at >= %s
                      {end_clause}
                      AND rra.resource_group IS NOT NULL
                      AND trim(rra.resource_group) <> ''
                      AND rra.summary_total_checks > 0
                      AND NOT rra.is_type_mismatch
                      {sub_clause}
                      {rt_cl_for_groups}
                    ORDER BY 1
                    """,
                    rg_filter_params,
                )
                rgs = [r[0] for r in cur.fetchall()]

                cur.execute(
                    f"""
                    SELECT DISTINCT rra.resource_type
                    FROM result_resource_assessments rra
                    JOIN result_reports rr ON rr.report_id = rra.report_id
                    WHERE rr.generated_at >= %s
                      {end_clause}
                      AND rra.resource_type IS NOT NULL
                      AND trim(rra.resource_type) <> ''
                      AND rra.summary_total_checks > 0
                      AND NOT rra.is_type_mismatch
                      {sub_clause}
                      {rg_cl_for_types}
                    ORDER BY 1
                    """,
                    rt_filter_params,
                )
                rts = [r[0] for r in cur.fetchall()]
            return rgs, rts
        finally:
            conn.close()

    # ── Q4: 리소스별 최신 점수 ────────────────────────────────────────────────
    def _q_resources():
        rg_cl = "AND rra.resource_group = %s" if resource_group else ""
        rt_cl = "AND LOWER(rra.resource_type) LIKE %s" if resource_type else ""
        params: list = list(base_params_sub)
        if resource_group:
            params.append(resource_group)
        if resource_type:
            params.append(f"%{resource_type.lower()}%")
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT DISTINCT ON (rra.resource_name)
                        rra.resource_name,
                        rra.resource_type,
                        COALESCE(rra.resource_group, ''),
                        rra.assessment_time,
                        rra.overall_score,
                        rra.summary_total_checks,
                        rra.summary_passed,
                        rra.summary_failed,
                        rra.summary_warnings,
                        rr.report_id,
                        to_char(rr.generated_at AT TIME ZONE 'Asia/Seoul', 'YYYY-MM-DD"T"HH24:MI:SS'),
                        rra.is_type_mismatch
                    FROM result_resource_assessments rra
                    JOIN result_reports rr ON rr.report_id = rra.report_id
                    WHERE rr.generated_at >= %s
                      {end_clause}
                      {sub_clause}
                      {rg_cl}
                      {rt_cl}
                    ORDER BY rra.resource_name,
                             COALESCE(rra.assessment_time, rr.generated_at) DESC NULLS LAST
                    """,
                    params,
                )
                return cur.fetchall()
        finally:
            conn.close()

    # ── Q5: 점수 분포 (전체 평가 레코드 기준) ────────────────────────────────
    def _q_distribution():
        rg_cl = "AND rra.resource_group = %s" if resource_group else ""
        rt_cl = "AND LOWER(rra.resource_type) LIKE %s" if resource_type else ""
        params: list = [period_start] + end_params + ([subscription_id] if subscription_id else [])
        if resource_group:
            params.append(resource_group)
        if resource_type:
            params.append(f"%{resource_type.lower()}%")
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT
                        CASE
                            WHEN rra.overall_score <= 20 THEN '0-20'
                            WHEN rra.overall_score <= 40 THEN '21-40'
                            WHEN rra.overall_score <= 60 THEN '41-60'
                            WHEN rra.overall_score <= 80 THEN '61-80'
                            ELSE '81-100'
                        END AS range,
                        COUNT(*) AS cnt
                    FROM result_resource_assessments rra
                    JOIN result_reports rr ON rr.report_id = rra.report_id
                    WHERE rr.generated_at >= %s
                      {end_clause}
                      AND rra.summary_total_checks > 0
                      AND NOT rra.is_type_mismatch
                      {sub_clause}
                      {rg_cl}
                      {rt_cl}
                    GROUP BY 1
                    """,
                    params,
                )
                return {r[0]: int(r[1]) for r in cur.fetchall()}
        finally:
            conn.close()

    # ── Q6: 평균 점수 팝업용 리소스 행 (기간·필터 동일) ───────────────────────
    def _q_avg_score_resources():
        rg_cl = "AND rra.resource_group = %s" if resource_group else ""
        rt_cl = "AND LOWER(rra.resource_type) LIKE %s" if resource_type else ""
        params: list = [period_start] + end_params + ([subscription_id] if subscription_id else [])
        if resource_group:
            params.append(resource_group)
        if resource_type:
            params.append(f"%{resource_type.lower()}%")
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT
                        rra.resource_name,
                        rra.resource_type,
                        COALESCE(rra.resource_group, ''),
                        COALESCE(rra.assessment_time, rr.generated_at),
                        rra.overall_score,
                        rra.summary_passed,
                        rra.summary_failed,
                        rra.summary_warnings,
                        rra.summary_total_checks,
                        rra.is_type_mismatch,
                        COALESCE(NULLIF(trim(rra.subscription_name), ''), NULLIF(trim(rra.subscription_id), ''), '')
                    FROM result_resource_assessments rra
                    JOIN result_reports rr ON rr.report_id = rra.report_id
                    WHERE rr.generated_at >= %s
                      {end_clause}
                      {sub_clause}
                      {rg_cl}
                      {rt_cl}
                    ORDER BY rra.summary_total_checks DESC, rra.overall_score ASC
                    """,
                    params,
                )
                rows = cur.fetchall()
            return [
                {
                    "resource_name":      r[0],
                    "resource_type":      r[1],
                    "resource_group":     r[2],
                    "assessment_time":    r[3].isoformat() if r[3] and hasattr(r[3], "isoformat") else str(r[3] or ""),
                    "overall_score":      round(float(r[4]), 1),
                    "passed":             int(r[5]),
                    "failed":             int(r[6]),
                    "warnings":           int(r[7]),
                    "total_checks":       int(r[8]),
                    "no_checklist":       int(r[8]) == 0 or bool(r[9]),
                    "subscription_name":  r[10] or "",
                }
                for r in rows
            ]
        finally:
            conn.close()

    # ── Q7: 구독 필터 옵션 (구독·RG·유형 헤더/쿼리 필터 적용 안 함 — 기간 내 전체 표시용) ─
    dropdown_sub_params = [period_start] + end_params

    def _q_subscription_dropdown():
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT
                        rra.subscription_id,
                        COALESCE(
                            NULLIF(trim(MAX(rra.subscription_name)), ''),
                            NULLIF(trim(rra.subscription_id), ''),
                            ''
                        ) AS subscription_name
                    FROM result_resource_assessments rra
                    JOIN result_reports rr ON rr.report_id = rra.report_id
                    WHERE rr.generated_at >= %s
                      {end_clause}
                      AND rra.subscription_id IS NOT NULL
                      AND trim(rra.subscription_id) <> ''
                    GROUP BY rra.subscription_id
                    ORDER BY subscription_name, rra.subscription_id
                    """,
                    dropdown_sub_params,
                )
                return [
                    {
                        "subscription_id": str(row[0] or ""),
                        "name": str(row[1] or row[0] or ""),
                        "state": "",
                        "tenant_id": "",
                    }
                    for row in cur.fetchall()
                    if row[0]
                ]
        finally:
            conn.close()

    # ── 병렬 실행 ────────────────────────────────────────────────────────────
    with ThreadPoolExecutor(max_workers=7) as ex:
        f_trend      = ex.submit(_q_trend)
        f_kpi        = ex.submit(_q_kpi_agg)
        f_filters    = ex.submit(_q_filters)
        f_resources  = ex.submit(_q_resources)
        f_dist       = ex.submit(_q_distribution)
        f_avg_detail = ex.submit(_q_avg_score_resources)
        f_subscriptions = ex.submit(_q_subscription_dropdown)

    trend               = f_trend.result()
    kpi_row, avg_score  = f_kpi.result()
    resource_groups, resource_types = f_filters.result()
    resources           = f_resources.result()
    dist_counts         = f_dist.result()
    avg_score_resources = f_avg_detail.result()
    subscriptions_for_filter = f_subscriptions.result()

    # KPI 파싱 (kpi_row: reports, resources, checks, manual, passed, failed, warnings, type_mismatch_count)
    total_reports   = int(kpi_row[0])
    total_resources = int(kpi_row[1])
    total_checks    = int(kpi_row[2])
    total_manual    = int(kpi_row[3])
    total_auto      = max(0, total_checks - total_manual)
    total_passed    = int(kpi_row[4])
    total_failed    = int(kpi_row[5])
    total_warnings  = int(kpi_row[6])
    type_mismatch_count = int(kpi_row[7])

    # r 인덱스: 0=resource_name, 1=resource_type, 2=resource_group,
    #           3=assessment_time, 4=overall_score, 5=total_checks,
    #           6=passed, 7=failed, 8=warnings, 9=report_id, 10=generated_at,
    #           11=is_type_mismatch

    def _make_filename(report_id: Any, generated_at_str: str) -> str:
        dt_clean = (generated_at_str
                    .replace(":", "").replace("-", "").replace("T", "_"))[:15]
        return f"db/Report_{report_id}_{dt_clean}.json"

    def _to_resource_dict(r: Any, include_filename: bool = False) -> dict:
        d: dict = {
            "resource_name":   r[0],
            "resource_type":   r[1],
            "resource_group":  r[2],
            "assessment_time": r[3].isoformat() if r[3] else "",
            "overall_score":   float(r[4]),
            "total_checks":    int(r[5]),
            "passed":          int(r[6]),
            "failed":          int(r[7]),
            "warnings":        int(r[8]),
            "no_checklist":    int(r[5]) == 0 or bool(r[11]),
        }
        if include_filename:
            d["assessment_filename"] = _make_filename(r[9], r[10]) if r[9] and r[10] else ""
            d["report_id"] = int(r[9]) if r[9] else None
        return d

    # Score distribution (전체 평가 레코드 기준)
    score_distribution = [
        {"range": k, "count": dist_counts.get(k, 0)}
        for k in ("0-20", "21-40", "41-60", "61-80", "81-100")
    ]

    # Worst 10 (타입 불일치 제외)
    sorted_res = sorted(
        [r for r in resources if int(r[5]) > 0 and not bool(r[11])],
        key=lambda r: float(r[4]),
    )
    worst_resources = [_to_resource_dict(r, include_filename=True) for r in sorted_res[:10]]

    return {
        "kpi": {
            "total_reports":   total_reports,
            "avg_score":       avg_score,
            "total_resources": total_resources,
            "total_checks":    total_checks,
        },
        "trend": trend,
        "score_distribution": score_distribution,
        "worst_resources": worst_resources,
        "auto_manual": {
            "total_checks": total_checks,
            "automated":    total_auto,
            "manual":       total_manual,
        },
        "pass_fail": {
            "total_checks":         total_checks,
            "passed":               total_passed,
            "failed":               total_failed,
            "warnings":             total_warnings,
            "type_mismatch_count":  type_mismatch_count,
        },
        "filters": {
            "resource_groups": resource_groups,
            "resource_types":  resource_types,
        },
        "avg_score_resources": avg_score_resources,
        "subscriptions": subscriptions_for_filter,
    }


def get_score_range_resources(
    score_min: float,
    score_max: float,
    subscription_id: Optional[str] = None,
    period_days: int = 15,
    resource_group: Optional[str] = None,
    resource_type: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> list[dict]:
    """점수 구간에 해당하는 리소스 목록 (팝업 lazy load용).

    차트(_q_distribution)와 동일한 기준으로 집계:
    - 전체 평가 레코드 기준 (DISTINCT ON 없음)
    - summary_total_checks > 0 AND NOT is_type_mismatch 필터
    - 점수 범위 경계: score > score_min-1 AND score <= score_max
      (차트 CASE WHEN score <= N 로직과 일치)
    """
    from datetime import timedelta

    if start_date:
        try:
            period_start = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=KST)
        except ValueError:
            period_start = datetime.now(tz=KST) - timedelta(days=period_days)
    else:
        period_start = datetime.now(tz=KST) - timedelta(days=period_days)

    if end_date:
        try:
            period_end: Optional[datetime] = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=KST) + timedelta(days=1)
        except ValueError:
            period_end = None
    else:
        period_end = None

    end_params: list = [period_end] if period_end else []
    end_clause = "AND rr.generated_at < %s" if period_end else ""
    sub_clause = "AND rra.subscription_id = %s" if subscription_id else ""
    rg_clause  = "AND rra.resource_group = %s" if resource_group else ""
    rt_clause  = "AND LOWER(rra.resource_type) LIKE %s" if resource_type else ""

    # 차트 CASE WHEN 경계와 일치: '21-40'은 score > 20 AND score <= 40
    lower_bound = score_min - 1

    params: list = [period_start] + end_params + [lower_bound, score_max]
    params += ([subscription_id] if subscription_id else [])
    if resource_group:
        params.append(resource_group)
    if resource_type:
        params.append(f"%{resource_type.lower()}%")

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    rra.resource_name,
                    rra.resource_type,
                    COALESCE(rra.resource_group, ''),
                    rra.assessment_time,
                    rra.overall_score,
                    rra.summary_total_checks,
                    rra.summary_passed,
                    rra.summary_failed,
                    rra.summary_warnings,
                    rra.is_type_mismatch,
                    COALESCE(NULLIF(trim(rra.subscription_name), ''), NULLIF(trim(rra.subscription_id), ''), '')
                FROM result_resource_assessments rra
                JOIN result_reports rr ON rr.report_id = rra.report_id
                WHERE rr.generated_at >= %s
                  {end_clause}
                  AND rra.summary_total_checks > 0
                  AND NOT rra.is_type_mismatch
                  AND rra.overall_score > %s
                  AND rra.overall_score <= %s
                  {sub_clause}
                  {rg_clause}
                  {rt_clause}
                ORDER BY rra.overall_score DESC
                """,
                params,
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    return [
        {
            "resource_name":     r[0],
            "resource_type":     r[1],
            "resource_group":    r[2],
            "assessment_time":   r[3].isoformat() if r[3] else "",
            "overall_score":     round(float(r[4]), 1),
            "total_checks":      int(r[5]),
            "passed":            int(r[6]),
            "failed":            int(r[7]),
            "warnings":          int(r[8]),
            "no_checklist":      False,
            "subscription_name": r[10] or "",
        }
        for r in rows
    ]


def get_assessment_charts_summary(
    subscription_id: Optional[str],
    period_days: int = 15,
    trend_resource_group: Optional[str] = None,
    trend_resource_type: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    rg_subscription_id: Optional[str] = None,
) -> dict:
    """진단 요약 차트용: RG/유형 막대 + 일별 추이(DB 집계). RG 차트는 전체 구독 기준."""
    from datetime import timedelta

    base: dict = {
        "resource_group_bars": [],
        "resource_type_bars": [],
        "resource_group_trend": [],
        "resource_type_trend": [],
        "trend_resource_group_applied": trend_resource_group,
        "trend_resource_type_applied": trend_resource_type,
        "subscription_id": subscription_id,
        "db_configured": is_db_configured(),
    }
    if not is_db_configured():
        base["db_configured"] = False
        return base

    if start_date:
        try:
            period_start = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=KST)
        except ValueError:
            period_start = datetime.now(tz=KST) - timedelta(days=period_days)
    else:
        period_start = datetime.now(tz=KST) - timedelta(days=period_days)

    if end_date:
        try:
            period_end: Optional[datetime] = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=KST) + timedelta(days=1)
        except ValueError:
            period_end = None
    else:
        period_end = None

    end_params: list = [period_end] if period_end else []
    end_clause = "AND rr.generated_at < %s" if period_end else ""
    rg_sub = rg_subscription_id or None
    rg_sub_clause = "AND rra.subscription_id = %s" if rg_sub else ""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            rg_bar_params: list[Any] = [period_start] + end_params
            if rg_sub:
                rg_bar_params.append(rg_sub)
            cur.execute(
                f"""
                SELECT
                    nm,
                    ROUND(AVG(score)::numeric, 1) AS sc
                FROM (
                    SELECT
                        COALESCE(NULLIF(trim(rra.resource_group), ''), '(그룹 없음)') AS nm,
                        rra.overall_score AS score
                    FROM result_resource_assessments rra
                    JOIN result_reports rr ON rr.report_id = rra.report_id
                    WHERE rr.generated_at >= %s
                      {end_clause}
                      AND rra.summary_total_checks > 0
                      AND NOT rra.is_type_mismatch
                      {rg_sub_clause}
                ) raw
                GROUP BY nm
                ORDER BY sc DESC
                """,
                rg_bar_params,
            )
            resource_group_bars = [{"name": r[0], "score": float(r[1])} for r in cur.fetchall()]

            type_bar_clauses = [
                "rr.generated_at >= %s",
                "rra.subscription_id = %s",
                "rra.summary_total_checks > 0",
                "rra.resource_type IS NOT NULL",
                "rra.resource_type <> ''",
                "NOT rra.is_type_mismatch",
            ]
            type_bar_params: list[Any] = [period_start, subscription_id]
            if period_end:
                type_bar_clauses.insert(1, "rr.generated_at < %s")
                type_bar_params.insert(1, period_end)
            if trend_resource_group and str(trend_resource_group).strip():
                if str(trend_resource_group).strip() == "(그룹 없음)":
                    type_bar_clauses.append(
                        "(rra.resource_group IS NULL OR trim(rra.resource_group) = '')"
                    )
                else:
                    type_bar_clauses.append("rra.resource_group = %s")
                    type_bar_params.append(trend_resource_group)
            type_bar_wh = " AND ".join(type_bar_clauses)
            cur.execute(
                f"""
                SELECT nm, ROUND(AVG(score)::numeric, 1) AS sc
                FROM (
                    SELECT DISTINCT ON (rra.resource_name)
                        COALESCE(
                            NULLIF((regexp_match(rra.resource_type, '[^/]+$'))[1], ''),
                            '(unknown)'
                        ) AS nm,
                        rra.overall_score AS score
                    FROM result_resource_assessments rra
                    JOIN result_reports rr ON rr.report_id = rra.report_id
                    WHERE {type_bar_wh}
                    ORDER BY rra.resource_name,
                             COALESCE(rra.assessment_time, rr.generated_at) DESC NULLS LAST
                ) sub
                GROUP BY nm
                ORDER BY sc DESC
                LIMIT 24
                """,
                type_bar_params,
            )
            resource_type_bars = [{"name": r[0], "score": float(r[1])} for r in cur.fetchall()]

            def _daily_trend(
                rg: Optional[str],
                rt: Optional[str],
            ) -> list[dict[str, Any]]:
                clauses = [
                    "rr.generated_at >= %s",
                    "rra.summary_total_checks > 0",
                    "NOT rra.is_type_mismatch",
                ]
                params2: list[Any] = [period_start]
                if period_end:
                    clauses.insert(1, "rr.generated_at < %s")
                    params2.insert(1, period_end)
                if rg_sub:
                    clauses.append("rra.subscription_id = %s")
                    params2.append(rg_sub)
                if rg is not None and str(rg).strip() != "":
                    if str(rg).strip() == "(그룹 없음)":
                        clauses.append("(rra.resource_group IS NULL OR trim(rra.resource_group) = '')")
                    else:
                        clauses.append("rra.resource_group = %s")
                        params2.append(rg)
                if rt is not None and str(rt).strip() != "":
                    clauses.append(
                        "COALESCE(NULLIF((regexp_match(rra.resource_type, '[^/]+$'))[1], ''), '(unknown)') = %s"
                    )
                    params2.append(rt)
                wh = " AND ".join(clauses)
                cur.execute(
                    f"""
                    SELECT
                        to_char(rr.generated_at AT TIME ZONE 'Asia/Seoul', 'YYYY-MM-DD') AS dt,
                        ROUND(AVG(rra.overall_score)::numeric, 1)
                    FROM result_resource_assessments rra
                    JOIN result_reports rr ON rr.report_id = rra.report_id
                    WHERE {wh}
                    GROUP BY dt
                    ORDER BY dt ASC
                    """,
                    params2,
                )
                return [{"date": r[0], "score": float(r[1])} for r in cur.fetchall()]

            resource_group_trend = _daily_trend(trend_resource_group, None)
            # 유형 일별 추이: 선택 RG가 있으면 해당 그룹 안에서만 집계(유형 미선택 시 그룹 전체 일별 평균)
            resource_type_trend = _daily_trend(trend_resource_group, trend_resource_type)

        return {
            **base,
            "resource_group_bars": resource_group_bars,
            "resource_type_bars": resource_type_bars,
            "resource_group_trend": resource_group_trend,
            "resource_type_trend": resource_type_trend,
            "db_configured": True,
        }
    finally:
        conn.close()


def get_subscription_charts_summary(
    period_days: int = 15,
    trend_subscription: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> dict:
    """구독별 평균 점수 바 차트 + 구독별 일별 추이. 구독 필터 없이 전체 구독 집계."""
    from datetime import timedelta

    base: dict = {
        "subscription_bars": [],
        "subscription_trend": [],
        "trend_subscription_applied": trend_subscription,
        "db_configured": is_db_configured(),
    }
    if not is_db_configured():
        base["db_configured"] = False
        return base

    if start_date:
        try:
            period_start = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=KST)
        except ValueError:
            period_start = datetime.now(tz=KST) - timedelta(days=period_days)
    else:
        period_start = datetime.now(tz=KST) - timedelta(days=period_days)

    if end_date:
        try:
            period_end: Optional[datetime] = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=KST) + timedelta(days=1)
        except ValueError:
            period_end = None
    else:
        period_end = None

    end_params: list = [period_end] if period_end else []
    end_clause = "AND rr.generated_at < %s" if period_end else ""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # ── 구독별 평균 점수 막대 ─────────────────────────────────────
            cur.execute(
                f"""
                SELECT
                    sub_id,
                    MAX(sub_label) AS sub_label,
                    ROUND(AVG(score)::numeric, 1) AS sc
                FROM (
                    SELECT
                        COALESCE(NULLIF(trim(rra.subscription_id), ''), '(구독 없음)') AS sub_id,
                        COALESCE(
                            NULLIF(trim(rra.subscription_name), ''),
                            NULLIF(trim(rra.subscription_id), ''),
                            '(구독 없음)'
                        ) AS sub_label,
                        rra.overall_score AS score
                    FROM result_resource_assessments rra
                    JOIN result_reports rr ON rr.report_id = rra.report_id
                    WHERE rr.generated_at >= %s
                      {end_clause}
                      AND rra.summary_total_checks > 0
                      AND NOT rra.is_type_mismatch
                ) raw
                GROUP BY sub_id
                ORDER BY sc DESC
                """,
                [period_start] + end_params,
            )
            subscription_bars = [
                {"id": r[0], "name": r[1], "score": float(r[2])}
                for r in cur.fetchall()
            ]

            # ── 구독별 일별 추이 ─────────────────────────────────────────
            clauses = [
                "rr.generated_at >= %s",
                "rra.summary_total_checks > 0",
                "NOT rra.is_type_mismatch",
            ]
            params: list[Any] = [period_start]
            if period_end:
                clauses.insert(1, "rr.generated_at < %s")
                params.insert(1, period_end)
            if trend_subscription and str(trend_subscription).strip():
                clauses.append("rra.subscription_id = %s")
                params.append(trend_subscription.strip())
            wh = " AND ".join(clauses)
            cur.execute(
                f"""
                SELECT
                    to_char(rr.generated_at AT TIME ZONE 'Asia/Seoul', 'YYYY-MM-DD') AS dt,
                    ROUND(AVG(rra.overall_score)::numeric, 1)
                FROM result_resource_assessments rra
                JOIN result_reports rr ON rr.report_id = rra.report_id
                WHERE {wh}
                GROUP BY dt
                ORDER BY dt ASC
                """,
                params,
            )
            subscription_trend = [{"date": r[0], "score": float(r[1])} for r in cur.fetchall()]

        # trend_subscription_applied: id 대신 표시 이름으로 반환
        applied_label = next(
            (b["name"] for b in subscription_bars if b["id"] == (trend_subscription or "")),
            trend_subscription,
        )
        return {
            **base,
            "subscription_bars": subscription_bars,
            "subscription_trend": subscription_trend,
            "trend_subscription_applied": applied_label,
            "db_configured": True,
        }
    finally:
        conn.close()


def get_global_kpi(subscription_id: Optional[str] = None) -> dict:
    """필터(기간·RG·리소스 유형) 독립적인 구독 전체 KPI.

    - total_reports : 구독의 전체 리포트 수
    - avg_score     : 리소스별 최신 점수의 평균 (전 기간)
    - pass_fail     : 리소스별 최신 점수 기준 성공/실패/경고 합계
    """
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            sub_clause = "AND rra.subscription_id = %s" if subscription_id else ""
            sub_params = [subscription_id] if subscription_id else []

            # ── 전체 리포트 수 ─────────────────────────────────────────────
            if subscription_id:
                cur.execute(
                    """
                    SELECT COUNT(DISTINCT rr.report_id)
                    FROM result_reports rr
                    JOIN result_resource_assessments rra ON rra.report_id = rr.report_id
                    WHERE rra.subscription_id = %s
                    """,
                    (subscription_id,),
                )
            else:
                cur.execute("SELECT COUNT(*) FROM result_reports")
            total_reports = int(cur.fetchone()[0])

            # ── 리소스별 최신 점수 (전 기간, pass_fail 집계용) ───────────
            cur.execute(
                f"""
                SELECT DISTINCT ON (rra.resource_name)
                    rra.resource_name,
                    rra.resource_type,
                    COALESCE(rra.resource_group, ''),
                    COALESCE(rra.assessment_time, rr.generated_at),
                    rra.overall_score,
                    rra.summary_passed,
                    rra.summary_failed,
                    rra.summary_warnings,
                    rra.summary_total_checks,
                    rra.is_type_mismatch
                FROM result_resource_assessments rra
                JOIN result_reports rr ON rr.report_id = rra.report_id
                WHERE 1=1
                  {sub_clause}
                ORDER BY rra.resource_name,
                         COALESCE(rra.assessment_time, rr.generated_at) DESC NULLS LAST
                """,
                sub_params,
            )
            rows = cur.fetchall()

            # ── 전체 평가 레코드 (팝업 근거 데이터용) ─────────────────────
            cur.execute(
                f"""
                SELECT
                    rra.resource_name,
                    rra.resource_type,
                    COALESCE(rra.resource_group, ''),
                    COALESCE(rra.assessment_time, rr.generated_at),
                    rra.overall_score,
                    rra.summary_passed,
                    rra.summary_failed,
                    rra.summary_warnings,
                    rra.summary_total_checks,
                    rra.is_type_mismatch,
                    COALESCE(NULLIF(trim(rra.subscription_name), ''), NULLIF(trim(rra.subscription_id), ''), '')
                FROM result_resource_assessments rra
                JOIN result_reports rr ON rr.report_id = rra.report_id
                WHERE 1=1
                  {sub_clause}
                ORDER BY rra.summary_total_checks DESC, rra.overall_score ASC
                """,
                sub_params,
            )
            all_rows = cur.fetchall()

            # ── 전체 평가 합계 기준 평균 점수 ─────────────────────────────
            cur.execute(
                f"""
                SELECT COALESCE(ROUND(AVG(rra.overall_score)::numeric, 1), 0)
                FROM result_resource_assessments rra
                JOIN result_reports rr ON rr.report_id = rra.report_id
                WHERE rra.summary_total_checks > 0
                  AND NOT rra.is_type_mismatch
                  {sub_clause}
                """,
                sub_params,
            )
            avg_row = cur.fetchone()
            avg_score = float(avg_row[0]) if avg_row and avg_row[0] else 0.0

        # 성공/실패 비율 분모에서 제외할 리소스:
        # - 체크 항목 0건
        # - 체크리스트 타입 불일치
        type_mismatch_count = sum(1 for r in all_rows if int(r[8]) == 0 or bool(r[9]))
        normal_rows = [r for r in all_rows if not (int(r[8]) == 0 or bool(r[9]))]

        # 성공/실패 집계: 타입 불일치 제외
        total_passed   = sum(int(r[5]) for r in normal_rows)
        total_failed   = sum(int(r[6]) for r in normal_rows)
        total_warnings = sum(int(r[7]) for r in normal_rows)
        total_checks   = sum(int(r[8]) for r in normal_rows)

        resources = [
            {
                "resource_name":      r[0],
                "resource_type":      r[1],
                "resource_group":     r[2],
                "assessment_time":    r[3].isoformat() if r[3] and hasattr(r[3], "isoformat") else str(r[3] or ""),
                "overall_score":      round(float(r[4]), 1),
                "passed":             int(r[5]),
                "failed":             int(r[6]),
                "warnings":           int(r[7]),
                "total_checks":       int(r[8]),
                "no_checklist":       int(r[8]) == 0 or bool(r[9]),
                "subscription_name":  r[10] or "",
            }
            for r in all_rows
        ]

        return {
            "total_reports": total_reports,
            "avg_score":     avg_score,
            "pass_fail": {
                "total_checks":        total_checks,
                "passed":              total_passed,
                "failed":              total_failed,
                "warnings":            total_warnings,
                "type_mismatch_count": type_mismatch_count,
            },
            "resources": resources,
        }
    finally:
        conn.close()


def get_trend_date_resources(
    date: str,
    subscription_id: Optional[str] = None,
    resource_group: Optional[str] = None,
    resource_type: Optional[str] = None,
) -> list:
    """특정 일자(YYYY-MM-DD)에 평가된 리소스 목록을 반환한다."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            sub_clause = "AND rra.subscription_id = %s" if subscription_id else ""
            if resource_group and resource_group.strip() == "(그룹 없음)":
                rg_clause = "AND (rra.resource_group IS NULL OR trim(rra.resource_group) = '')"
            elif resource_group:
                rg_clause = "AND rra.resource_group = %s"
            else:
                rg_clause = ""
            rt_clause = "AND LOWER(rra.resource_type) LIKE %s" if resource_type else ""
            params: list = [date]
            if subscription_id:
                params.append(subscription_id)
            if resource_group and resource_group.strip() != "(그룹 없음)":
                params.append(resource_group)
            if resource_type:
                params.append(f"%{resource_type.lower()}%")
            cur.execute(
                f"""
                SELECT
                    rra.resource_name,
                    rra.resource_type,
                    COALESCE(rra.resource_group, '') AS resource_group,
                    rra.assessment_time,
                    rra.overall_score,
                    rra.summary_passed,
                    rra.summary_failed,
                    rra.summary_warnings,
                    rra.summary_total_checks,
                    rra.is_type_mismatch,
                    COALESCE(NULLIF(trim(rra.subscription_name), ''), NULLIF(trim(rra.subscription_id), ''), '')
                FROM result_resource_assessments rra
                JOIN result_reports rr ON rr.report_id = rra.report_id
                WHERE to_char(rr.generated_at AT TIME ZONE 'Asia/Seoul', 'YYYY-MM-DD') = %s
                  AND rra.summary_total_checks > 0
                  AND NOT rra.is_type_mismatch
                  {sub_clause}
                  {rg_clause}
                  {rt_clause}
                ORDER BY rra.overall_score DESC
                """,
                params,
            )
            rows = cur.fetchall()
        return [
            {
                "resource_name":     r[0],
                "resource_type":     r[1],
                "resource_group":    r[2],
                "assessment_time":   r[3].isoformat() if r[3] and hasattr(r[3], "isoformat") else str(r[3] or ""),
                "overall_score":     round(float(r[4]), 1),
                "passed":            int(r[5]),
                "failed":            int(r[6]),
                "warnings":          int(r[7]),
                "total_checks":      int(r[8]),
                "no_checklist":      False,
                "subscription_name": r[10] or "",
            }
            for r in rows
        ]
    finally:
        conn.close()


def get_report_detail(report_id: int) -> Optional[dict]:
    """report_id에 해당하는 리포트 + 리소스별 평가 결과를 반환한다."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM result_reports WHERE report_id = %s",
            (report_id,),
        )
        row = cur.fetchone()
        if not row:
            cur.close()
            return None
        cols = [d[0] for d in cur.description]
        report = dict(zip(cols, row))
        for k, v in report.items():
            if hasattr(v, "isoformat"):
                report[k] = v.isoformat()

        cur.execute(
            """
            SELECT assessment_id, subscription_id, resource_id, resource_name,
                   resource_type, resource_group, location, assessment_time,
                   overall_score, summary_total_checks, summary_passed,
                   summary_failed, summary_warnings
            FROM result_resource_assessments
            WHERE report_id = %s
            ORDER BY overall_score ASC
            """,
            (report_id,),
        )
        a_rows = cur.fetchall()
        a_cols = [d[0] for d in cur.description]
        assessments = []
        for a_row in a_rows:
            a_dict = dict(zip(a_cols, a_row))
            for k, v in a_dict.items():
                if hasattr(v, "isoformat"):
                    a_dict[k] = v.isoformat()
            a_id = a_dict["assessment_id"]
            cur.execute(
                """
                SELECT status, severity, question, finding, recommendation,
                       evidence_property, evidence_actual, evidence_expected
                FROM result_check_results
                WHERE assessment_id = %s
                ORDER BY result_id
                """,
                (a_id,),
            )
            check_rows = cur.fetchall()
            check_cols = [d[0] for d in cur.description]
            a_dict["check_results"] = [dict(zip(check_cols, cr)) for cr in check_rows]
            assessments.append(a_dict)

        report["assessments"] = assessments

        # ── result_file 리소스 목록 ──────────────────────────────────────────
        cur.execute(
            """
            SELECT id, scope_id, resource_id, resource_name,
                   resource_type, resource_group,
                   result_status, overall_score, trace_id, created_at
            FROM result_file
            WHERE report_id = %s
            ORDER BY overall_score ASC, id
            """,
            (report_id,),
        )
        rf_rows = cur.fetchall()
        rf_cols = [d[0] for d in cur.description]
        resource_files = []
        for rf_row in rf_rows:
            rf_dict = dict(zip(rf_cols, rf_row))
            for k, v in rf_dict.items():
                if hasattr(v, "isoformat"):
                    rf_dict[k] = v.isoformat()
                elif hasattr(v, "hex"):  # UUID
                    rf_dict[k] = str(v)
            resource_files.append(rf_dict)

        report["resource_files"] = resource_files
        cur.close()
        return report
    finally:
        conn.close()


def get_file_detail(file_id: int) -> Optional[dict]:
    """result_file 테이블에서 개별 리소스 진단 상세 내역을 반환한다."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM result_file WHERE id = %s", (file_id,))
        row = cur.fetchone()
        if not row:
            cur.close()
            return None

        cols = [d[0] for d in cur.description]
        data = dict(zip(cols, row))
        for k, v in data.items():
            if hasattr(v, "isoformat"):
                data[k] = v.isoformat()
            elif hasattr(v, "hex"):  # UUID
                data[k] = str(v)
            elif k == "details" and isinstance(v, (str, bytes)):
                try:
                    data[k] = json.loads(v)
                except Exception:
                    pass
        cur.close()
        return data
    finally:
        conn.close()


def list_individual_files(
    subscription_id: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """result_file 테이블에서 개별 리소스 진단 내역 목록을 반환한다."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        query = """
            SELECT id, report_id, scope_id, resource_name, resource_type,
                   result_status, overall_score, created_at
            FROM result_file
        """
        params = []
        if subscription_id:
            query += " WHERE scope_id = %s "
            params.append(subscription_id)

        query += " ORDER BY created_at DESC LIMIT %s "
        params.append(limit)

        cur.execute(query, tuple(params))
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        result = []
        for row in rows:
            d = dict(zip(cols, row))
            for k, v in d.items():
                if hasattr(v, "isoformat"):
                    d[k] = v.isoformat()
                elif hasattr(v, "hex"):
                    d[k] = str(v)
            result.append(d)
        cur.close()
        return result
    finally:
        conn.close()


def get_resource_check_results(report_id: int, resource_name: str) -> dict:
    """특정 리소스의 체크 결과 상세 조회 (팝업용)."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    rra.assessment_id,
                    rra.resource_name,
                    rra.resource_type,
                    COALESCE(rra.resource_group, '') AS resource_group,
                    rra.overall_score,
                    rra.summary_total_checks,
                    rra.summary_passed,
                    rra.summary_failed,
                    rra.summary_warnings,
                    rra.assessment_time,
                    rra.subscription_id,
                    COALESCE(NULLIF(trim(rra.subscription_name), ''), rra.subscription_id, '') AS subscription_name
                FROM result_resource_assessments rra
                WHERE rra.report_id = %s AND rra.resource_name = %s
                LIMIT 1
                """,
                (report_id, resource_name),
            )
            row = cur.fetchone()
            if not row:
                return {}

            assessment_id = row[0]

            cur.execute(
                """
                SELECT
                    rcr.status,
                    rcr.severity,
                    rcr.question,
                    rcr.finding,
                    rcr.recommendation,
                    rcr.evidence_property,
                    rcr.evidence_actual,
                    rcr.evidence_expected,
                    rcr.checklist_name
                FROM result_check_results rcr
                WHERE rcr.assessment_id = %s
                ORDER BY
                    CASE rcr.status
                        WHEN 'fail'          THEN 1
                        WHEN 'warning'       THEN 2
                        WHEN 'manual_review' THEN 3
                        WHEN 'pass'          THEN 4
                        ELSE 5
                    END,
                    CASE rcr.severity
                        WHEN 'critical' THEN 1
                        WHEN 'high'     THEN 2
                        WHEN 'medium'   THEN 3
                        WHEN 'low'      THEN 4
                        ELSE 5
                    END
                """,
                (assessment_id,),
            )
            check_results = [
                {
                    "status":             r[0],
                    "severity":           r[1],
                    "question":           r[2],
                    "finding":            r[3],
                    "recommendation":     r[4],
                    "evidence_property":  r[5],
                    "evidence_actual":    r[6],
                    "evidence_expected":  r[7],
                    "checklist_name":     r[8],
                }
                for r in cur.fetchall()
            ]

        return {
            "resource_name":      row[1],
            "resource_type":      row[2],
            "resource_group":     row[3],
            "overall_score":      float(row[4]),
            "total_checks":       int(row[5]),
            "passed":             int(row[6]),
            "failed":             int(row[7]),
            "warnings":           int(row[8]),
            "assessment_time":    row[9].isoformat() if row[9] else "",
            "subscription_id":    row[10] or "",
            "subscription_name":  row[11] or "",
            "check_results":      check_results,
        }
    finally:
        conn.close()
