"""
Terraform Generation Tools

Generate Terraform remediation code from assessment fail/warning items.
"""

import asyncio
from datetime import datetime
from pathlib import Path
import os
import json
from typing import Annotated
from agent_framework._tools import tool

from agent.azure_resource_reader import AzureResourceReader
from agent.storage_paths import (
    LEGACY_STORAGE_SUBSCRIPTION_KEY,
    subscription_scope_key,
)

from chat.tools._common import get_terraform_generator, PROJECT_DIR
from chat.tools.azure_session import (
    get_session_subscription_id,
    resolve_assessment_subscription_id,
)


# ── Download server config ───────────────────────────────────────
# Set by main.py at startup; tools read this to build download URLs.
TERRAFORM_DOWNLOAD_BASE_URL: str = ""  # e.g. "http://localhost:8081"


def _build_terraform_markdown(result: dict, timestamp: str) -> str:
    """Build a nicely formatted Markdown document containing all Terraform files."""
    lines = [
        "# Terraform Remediation Code",
        "",
        f"**Generated:** {timestamp}",
        f"**Target Resources:** {result['resources_count']}",
        f"**Recommendations Applied:** {result['recommendations_count']}",
        f"**Files:** {', '.join(result['files'].keys()) or 'N/A'}",
        "",
        "---",
        "",
    ]
    for filename, code in result["files"].items():
        lines.append(f"## {filename}")
        lines.append("")
        lines.append("```hcl")
        lines.append(code)
        lines.append("```")
        lines.append("")

    return "\n".join(lines)


def _active_subscription_segment() -> str:
    sid = (get_session_subscription_id() or "").strip()
    if not sid:
        info = AzureResourceReader.get_session_bootstrap()
        sid = (info.get("subscription_id") or "").strip()
    return subscription_scope_key(sid)


def _latest_report_for_subscription(subscription_id: str | None) -> dict | None:
    from agent.db.assessment import list_reports

    reports = list_reports(subscription_id=subscription_id, limit=1)
    return reports[0] if reports else None


def _load_report_assessments(report_id: int) -> list[dict] | None:
    from agent.db.assessment import get_report_detail

    r_detail = get_report_detail(report_id)
    if not r_detail or not r_detail.get("assessments"):
        return None

    assessments = r_detail["assessments"]
    for a in assessments:
        if isinstance(a, dict) and a.get("_db_report_id") is None:
            a["_db_report_id"] = report_id
    return assessments


def _filter_documents(
    documents: list[dict],
    resource_type: str,
    resource_group: str,
) -> list[dict]:
    filtered = documents
    rt = (resource_type or "").strip().lower()
    rg = (resource_group or "").strip().lower()
    if rt:
        filtered = [
            d for d in filtered
            if rt in (d.get("resource_type") or "").lower()
        ]
    if rg:
        filtered = [
            d for d in filtered
            if rg in (d.get("resource_group") or "").lower()
        ]
    return filtered


def _terraform_target_selection_response(subscription_id: str | None) -> str:
    from chat.tools.chat_state import get_last_assessment_report_id

    stored_report_id = get_last_assessment_report_id(subscription_id)
    latest = _latest_report_for_subscription(subscription_id)
    lines = [
        "Terraform 코드는 한 번에 하나의 평가 결과에 대해서만 생성할 수 있습니다.",
        "대상 평가 결과를 먼저 선택해 주세요.",
        "",
        "옵션:",
    ]
    if stored_report_id:
        lines.append(f"1. 방금 평가한 결과로 생성: report_id={stored_report_id}")
    elif latest:
        lines.append(
            f"1. 방금 평가한 결과로 생성: report_id={latest['report_id']} "
            f"(생성일시: {latest.get('generated_at', '-')}, 리소스 {latest.get('total_resources', 0)}개)"
        )
    else:
        lines.append("1. 방금 평가한 결과로 생성: 현재 구독에서 최근 평가 결과를 찾지 못했습니다.")
    lines.append("2. 평가 ID를 지정해서 생성: 예) report_id=123")
    lines.append("")
    lines.append("원하는 옵션을 선택해 주세요.")
    return "\n".join(lines)


def _generate_terraform_code_sync(
    resource_type: str,
    resource_group: str,
    resource_names: list[str] | None,
    assessment_filename: str,
    assessment_report_id: int,
    assessment_target: str,
) -> str:
    try:
        generator = get_terraform_generator()
        sub_for_snap = resolve_assessment_subscription_id()
        source_report_ids_fallback: list[int] = []

        # Support partial resource_type matching (e.g. 'mysql' instead of full type)
        rt = resource_type.strip() if resource_type else ""

        target = (assessment_target or "").strip().lower()
        report_id = int(assessment_report_id or 0)
        if report_id <= 0 and target in {"latest", "last", "recent", "current", "just_assessed", "방금", "최근"}:
            from chat.tools.chat_state import get_last_assessment_report_id

            stored_report_id = get_last_assessment_report_id(sub_for_snap)
            if stored_report_id:
                report_id = stored_report_id
            else:
                latest = _latest_report_for_subscription(sub_for_snap)
                if not latest:
                    return "현재 채팅 세션에서 방금 평가한 결과를 찾지 못했습니다. 평가 ID를 지정해 주세요."
                report_id = int(latest["report_id"])

        # 챗봇 경로에서는 대상 평가를 먼저 선택하게 하여 여러 회차 결과가 섞이지 않도록 한다.
        if report_id <= 0 and not (assessment_filename and assessment_filename.strip()):
            return _terraform_target_selection_response(sub_for_snap)

        custom_docs = None
        if report_id > 0:
            custom_docs = _load_report_assessments(report_id)
            if not custom_docs:
                return f"report_id={report_id}에 해당하는 평가 결과를 찾지 못했습니다."
            source_report_ids_fallback = [report_id]
            custom_docs = _filter_documents(custom_docs, rt, resource_group)

        # MODIFIED: 특정 파일명이 전달되면 해당 파일의 데이터를 우선 로드
        if report_id <= 0 and assessment_filename and assessment_filename.strip():
            fname = assessment_filename.strip()
            if fname.startswith("db/"):
                # DB 기반 파일인 경우 ID 추출하여 조회
                import re as _re
                # 리소스 상세: db/Resource_..._ID_...json
                res_match = _re.search(r"Resource_.*?_(\d+)_", fname)
                if res_match:
                    file_id = int(res_match.group(1))
                    from agent.db.assessment import get_file_detail
                    f_detail = get_file_detail(file_id)
                    if f_detail and f_detail.get("details"):
                        # detail 데이터가 있으면 문서 목록으로 사용
                        d = f_detail["details"]
                        if isinstance(d, str):
                            try: d = json.loads(d)
                            except: pass
                        custom_docs = [d]
                else:
                    # 회차 요약: db/Report_ID_...
                    rep_match = _re.search(r"Report_(\d+)_", fname)
                    if rep_match:
                        report_id = int(rep_match.group(1))
                        source_report_ids_fallback = [report_id]
                        from agent.db.assessment import get_report_detail
                        r_detail = get_report_detail(report_id)
                        if r_detail and r_detail.get("assessments"):
                            assessments = r_detail["assessments"]
                            # report 단위에서 가져온 문서에도 추적용 report_id를 명시해 저장 시 반영
                            for a in assessments:
                                if isinstance(a, dict) and a.get("_db_report_id") is None:
                                    a["_db_report_id"] = report_id
                            custom_docs = _filter_documents(assessments, rt, resource_group)
            else:
                # 파일명이 JSON이 아니면 JSON으로 변환 시도 (리포트 폴더 구조 반영)
                if not fname.endswith(".json"):
                    # .md나 .html을 .json으로 변환하여 메타데이터 파일 조회 시도
                    fname = fname.rsplit(".", 1)[0] + ".json"

                # 특정 파일 정보 로드 시도 (Local)
                try:
                    single_doc = generator.get_specific_assessment_file(fname)
                    if single_doc:
                        custom_docs = [single_doc]
                except Exception as e:
                    print(f"Warning: Failed to load specific assessment file {fname}: {e}")

        if rt and "/" not in rt and custom_docs is None:
            all_docs = generator.get_latest_assessments(subscription_id=sub_for_snap)
            rt_lower = rt.lower()
            filtered_docs = [d for d in all_docs if rt_lower in d.get("resource_type", "").lower()]
            result = generator.generate(documents=filtered_docs, resource_names=resource_names)
        else:
            result = generator.generate(
                resource_type=rt or None,
                resource_group=resource_group or None,
                subscription_id=sub_for_snap,
                resource_names=resource_names,
                documents=custom_docs # MODIFIED: 특정 파일의 문서 데이터 전달
            )

        if result["recommendations_count"] == 0:
            return "일치하는 리소스에 대해 개선이 필요한 항목이 없거나 최신 진단 결과가 존재하지 않습니다."

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        sub_seg = _active_subscription_segment()

        # ── 파일 목록 수집 ──────────────────────────────────────────────────
        md_content = _build_terraform_markdown(result, timestamp)
        md_filename = f"terraform_{timestamp}.md"
        readme_content = result.get("readme", "")

        db_files: list[dict] = []
        for filename, code in result["files"].items():
            db_files.append({"file_name": filename, "content": code})
        db_files.append({"file_name": md_filename, "content": md_content})
        if readme_content:
            db_files.append({"file_name": "README.md", "content": readme_content})

        # ── DB 저장 ─────────────────────────────────────────────────────────
        db_msg = ""
        try:
            from agent.db.terraform import is_db_configured, save_terraform_run
            if is_db_configured():
                run_id = save_terraform_run(
                    scope_id=sub_seg if sub_seg.lower() != "legacy" else None,
                    run_timestamp=timestamp,
                    files=db_files,
                    resources_count=result.get("resources_count", 0),
                    recommendations_count=result.get("recommendations_count", 0),
                    source_report_ids=result.get("source_report_ids") or source_report_ids_fallback,
                    source_resource_names=result.get("source_resource_names") or [],
                )
                db_msg = f"\nDB 저장 완료 (run_id={run_id})"
        except Exception as _db_err:
            db_msg = f"\nDB 저장 실패: {_db_err}"

        all_file_names = [f["file_name"] for f in db_files]
        # 챗 UI에서 바로 클릭 가능한 다운로드 링크를 summary에 포함
        base = (TERRAFORM_DOWNLOAD_BASE_URL or "/api/terraform").rstrip("/")
        sub_q = quote(sub_seg, safe="")
        ts_q = quote(timestamp, safe="")
        download_links = [
            f"- [{name}]({base}/{sub_q}/{ts_q}/{quote(name, safe='')}/raw)"
            for name in all_file_names
        ]

        summary = (
            f"Terraform code generated!\n"
            f"  Timestamp: {timestamp}\n"
            f"  Target Resources: {result['resources_count']}\n"
            f"  Recommendations Applied: {result['recommendations_count']}\n"
            f"  Files: {', '.join(all_file_names)}"
            f"{db_msg}"
        )

        return summary
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        return f"Error during terraform generation: {str(e)}\n\nTraceback:\n{error_trace}"


@tool
async def generate_terraform_code(
    resource_type: Annotated[str, "Resource type filter for Terraform generation (e.g. microsoft.dbformysql/flexibleservers). Empty string for all."] = "",
    resource_group: Annotated[str, "Resource group filter. Empty string for all."] = "",
    resource_names: Annotated[list[str], "특정 리소스 이름들만 선택하여 생성하고 싶은 경우 리소스 이름 목록 (예: ['my-storage-01', 'my-db-02'])"] = None,
    assessment_filename: Annotated[str, "특정 진단 결과 파일(리포트)을 기반으로 코드를 생성하고 싶은 경우 파일명을 지정하세요."] = "",
    assessment_report_id: Annotated[int, "Terraform 코드를 생성할 단일 평가 결과 report_id. 한 번에 하나만 지정하세요."] = 0,
    assessment_target: Annotated[str, "대상 선택 방식. 방금 평가한 결과를 사용할 때 'latest' 또는 'just_assessed'. ID 지정 시 비워두고 assessment_report_id를 사용하세요."] = "",
) -> str:
    """Generate Terraform code based on fail/warning recommendations from one selected Assessment result.

    If no assessment_filename, assessment_report_id, or assessment_target is supplied, this tool
    returns selection options instead of generating code.
    Saves generated files to the configured DB.
    Returns a short summary.

    LLM 호출과 DB 저장을 threadpool에서 수행하여 이벤트 루프 블로킹을 방지."""
    return await asyncio.to_thread(
        _generate_terraform_code_sync,
        resource_type,
        resource_group,
        resource_names,
        assessment_filename,
        assessment_report_id,
        assessment_target,
    )


@tool
def delete_terraform_output(
    timestamp: Annotated[str, "The timestamp subdirectory to delete (e.g. '20260322_123456')"],
    subscription_id: Annotated[
        str,
        "구독 ID. 비우면 UI 세션/CLI 기본 구독. 레거시 2단 경로는 'legacy'.",
    ] = "",
) -> str:
    """Delete a generated Terraform output set from DB.
    Removes the terraform run matching subscription_id/timestamp."""
    try:
        from agent.db.terraform import is_db_configured, delete_run
        if not is_db_configured():
            return "Error: DB not configured."

        sub = (subscription_id or "").strip()
        if not sub:
            try:
                sub = _active_subscription_segment()
            except Exception:
                sub = LEGACY_STORAGE_SUBSCRIPTION_KEY

        scope = None if sub.lower() == LEGACY_STORAGE_SUBSCRIPTION_KEY else sub
        count = delete_run(scope, timestamp)
        if count > 0:
            return f"Successfully deleted terraform run '{timestamp}' from DB."
        return f"No terraform output found for timestamp '{timestamp}'."
    except Exception as e:
        return f"Failed to delete terraform output: {e}"
