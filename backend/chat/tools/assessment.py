"""
Assessment Pipeline Tools

Azure resource discovery, checklist matching, LLM-based assessment,
report generation, and AI Search upload.
"""

import asyncio
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Annotated, Optional

from agent_framework._tools import tool

from chat.tools._common import PROJECT_DIR
from chat.tools.azure_session import (
    effective_subscription_ids,
    get_session_subscription_id,
    get_session_tenant_id,
)

from agent.azure_resource_reader import AzureResource, AzureResourceReader
from agent.ai_foundry_config import get_ai_endpoint_from_env
from agent.checklist_loader import get_configured_checklist_loader
from agent.assessment_engine import (
    AssessmentEngine,
    ResourceAssessment,
    CheckResult,
    ComplianceStatus,
)
from agent.report_generator import ReportGenerator

logger = logging.getLogger(__name__)


def _truncate_for_tool_result(text: str, max_chars: int = 20000) -> str:
    """Keep tool output useful for the chatbot without flooding the model context."""
    if len(text) <= max_chars:
        return text
    return (
        text[:max_chars].rstrip()
        + "\n\n... (DB에 저장된 Markdown 리포트가 길어 일부만 표시했습니다.)"
    )


def _reader_for_session(subscription_tool_arg: str = "") -> AzureResourceReader:
    """UI 세션(헤더)의 테넌트·구독을 반영한 Resource Reader."""
    return AzureResourceReader(
        subscription_ids=effective_subscription_ids(subscription_tool_arg),
        tenant_id=get_session_tenant_id(),
    )


def _merge_resource_group_names(
    resource_group: str,
    resource_group_names: Optional[list[str]],
) -> list[str]:
    names: list[str] = []
    if resource_group and resource_group.strip():
        names.append(resource_group.strip())
    for g in resource_group_names or []:
        s = (g or "").strip()
        if s:
            names.append(s)
    return list(dict.fromkeys(names))


def _normalize_resource_ids(resource_ids: Optional[list[str]]) -> list[str]:
    out: list[str] = []
    for x in resource_ids or []:
        s = (x or "").strip()
        if s:
            out.append(s)
    return list(dict.fromkeys(out))


def _merge_resource_name_targets(
    resource_names: Optional[list[str]],
    resource_name: str,
) -> Optional[frozenset[str]]:
    """리소스 이름 정확 일치(대소문자 무시). 없으면 None → 필터 안 함."""
    parts: list[str] = []
    for n in resource_names or []:
        s = (n or "").strip()
        if s:
            parts.append(s)
    rn = (resource_name or "").strip()
    if rn:
        parts.append(rn)
    if not parts:
        return None
    return frozenset(p.lower() for p in dict.fromkeys(parts))


def _filter_resources_by_exact_name(
    resources: list[AzureResource],
    names_lower: frozenset[str],
) -> list[AzureResource]:
    return [r for r in resources if r.name.lower() in names_lower]


def _discover_resources_scoped(
    reader: AzureResourceReader,
    rg_names: list[str],
    resource_ids: list[str],
) -> tuple[list[AzureResource], list[str]]:
    """리소스 그룹 목록과 ARM 리소스 ID 목록의 합집합( id 기준 중복 제거 )."""
    by_id: dict[str, AzureResource] = {}
    for rg in rg_names:
        for r in reader.get_resources_by_resource_group(rg):
            by_id[r.id] = r
    missing: list[str] = []
    for rid in resource_ids:
        r = reader.get_resource_details(rid)
        if r:
            by_id[r.id] = r
        else:
            missing.append(rid)
    return list(by_id.values()), missing


def _checklist_loader():
    """API와 동일하게 DB에서 체크리스트를 로드."""
    return get_configured_checklist_loader(PROJECT_DIR)


def _normalize_checklist_ids(checklist_ids: Optional[list[str]]) -> list[str]:
    return list(
        dict.fromkeys(
            k.strip() for k in (checklist_ids or []) if k and str(k).strip()
        )
    )


def _ids_from_checklist_id_string(checklist_id: str) -> list[str]:
    """사용자가 한 줄로만 적은 id: 'system_stability' 또는 'a, b' 등."""
    if not checklist_id or not str(checklist_id).strip():
        return []
    raw = str(checklist_id).strip()
    parts = re.split(r"[\s,;]+", raw)
    return list(dict.fromkeys(p.strip() for p in parts if p and p.strip()))


def _merge_checklist_selection(
    checklist_ids: Optional[list[str]],
    checklist_id: str,
) -> list[str]:
    """checklist_ids 배열 + checklist_id 문자열(단일/쉼표 구분) 병합, 순서 유지·중복 제거."""
    base = _normalize_checklist_ids(checklist_ids)
    extra = _ids_from_checklist_id_string(checklist_id)
    return list(dict.fromkeys(base + extra))


def _checklist_applies_to_resource_type(checklist, resource_type: str) -> bool:
    resource_type_lower = (resource_type or "").lower()
    if not checklist.applicable_resource_types:
        return True
    return any(
        applicable_type.lower() in resource_type_lower
        for applicable_type in checklist.applicable_resource_types
    )


def _checklist_selection_required_response(
    *,
    loader=None,
    resources: Optional[list[AzureResource]] = None,
) -> str:
    """체크리스트 미선정 시 반환: 평가 대상 타입에 적용 가능한 목록 + 다시 호출 안내."""
    try:
        loader = loader or _checklist_loader()
        summary = loader.get_summary()
    except Exception as e:
        return (
            "체크리스트를 불러올 수 없어 평가를 시작할 수 없습니다.\n"
            f"원인: {e}"
        )

    clist = summary.get("checklists") or []
    if not clist:
        return (
            "로드된 체크리스트가 없습니다. 체크리스트 화면에서 YAML 체크리스트를 추가한 뒤 다시 시도하세요.\n"
            "체크리스트가 없으면 리소스 평가를 실행할 수 없습니다."
        )

    target_types: list[str] = []
    if resources is not None:
        target_types = sorted({r.type for r in resources if getattr(r, "type", "")})
        if target_types:
            applicable_ids = {
                key
                for key, checklist in loader.checklists.items()
                if any(
                    _checklist_applies_to_resource_type(checklist, rt)
                    for rt in target_types
                )
            }
            clist = [cl for cl in clist if cl.get("id") in applicable_ids]

    lines = [
        "【체크리스트 선택 필요】",
        "리소스 평가는 **사용자가 하나 이상의 체크리스트를 선택한 뒤**에만 실행할 수 있습니다. (다중 선택 가능)",
        "다시 호출할 때: **checklist_id**에 id 한 개만 문자열로 넣어도 됩니다 (예: `checklist_id=\"system_stability\"`). "
        "여러 개면 쉼표로 구분하거나 **checklist_ids** 배열을 사용하세요. (YAML 파일 stem과 동일)",
        "",
    ]
    if resources is not None:
        lines.append(f"평가 대상 리소스: {len(resources)}개")
        if target_types:
            lines.append("평가 대상 리소스 타입:")
            lines.extend(f"  - {rt}" for rt in target_types)
            lines.append("")
            lines.append("평가 대상 타입에 사용 가능한 체크리스트:")
        else:
            lines.append(
                "평가 대상 리소스 타입을 확인하지 못해 전체 체크리스트를 표시합니다."
            )
            lines.append("")
            lines.append("사용 가능한 체크리스트:")
    else:
        lines.append("사용 가능한 체크리스트:")

    if resources is not None and target_types and not clist:
        lines.extend(
            [
                "  - 적용 가능한 체크리스트가 없습니다.",
                "",
                "DB의 checklists.applicable_resource_types 값과 평가 대상 리소스 타입을 확인하세요.",
            ]
        )
        return "\n".join(lines)

    for cl in clist:
        cid = cl.get("id", "")
        cname = cl.get("name", "")
        lines.append(
            f"  - id=`{cid}` | {cname} — 점검 항목 {cl.get('total_checks', 0)}개 "
            f"(자동 {cl.get('automated_checks', 0)}, 수동 {cl.get('manual_checks', 0)})"
        )
        arts = cl.get("applicable_resource_types") or []
        if arts:
            lines.append(f"      적용 리소스 타입: {', '.join(arts)}")

    lines.extend(
        [
            "",
            "예시: checklist_id=\"system_stability\"  또는  checklist_ids=[\"azure_mysql\",\"system_stability\"]  (실제 id는 위 목록)",
        ]
    )
    return "\n".join(lines)


@tool
def get_subscription_info() -> str:
    """Get default Azure subscription context (ARM / get_effective: SSO 위임 경로면 사용자 토큰).
    Returns subscription ID, name, tenant ID, state, and principal hint from token when available."""

    try:
        info = AzureResourceReader.get_session_bootstrap()
        lines = [
            f"Azure Subscription Info:\n"
            f"  - Subscription ID: {info['subscription_id']}\n"
            f"  - Name: {info['name']}\n"
            f"  - Tenant ID: {info['tenant_id']}\n"
            f"  - State: {info['state']}\n"
            f"  - User: {info['user']}",
        ]
        st = get_session_tenant_id()
        ss = get_session_subscription_id()
        if ss or st:
            lines.append(
                "\nUI에서 선택된 평가 범위(채팅·리소스 조회에 적용됨):\n"
                f"  - Tenant ID: {st or '—'}\n"
                f"  - Subscription ID: {ss or '—'}"
            )
        return "\n".join(lines)
    except RuntimeError as e:
        return f"Error: {e}"


@tool
def list_azure_resources(
    subscription_id: Annotated[str, "Azure subscription ID. Empty string to auto-detect from Azure CLI."] = "",
    resource_group: Annotated[str, "Filter by resource group name. Empty string for all."] = "",
    resource_type: Annotated[str, "Filter by resource type (e.g. microsoft.dbformysql/flexibleservers). Empty string for all."] = "",
) -> str:
    """List Azure resources in a subscription.
    Can filter by resource group or resource type.
    Shows resource name, type, resource group, and location."""

    try:
        reader = _reader_for_session(subscription_id)

        if resource_group and resource_type:
            # Both filters: get by resource group, then filter by type
            resources = reader.get_resources_by_resource_group(resource_group)
            rt_lower = resource_type.lower()
            resources = [r for r in resources if rt_lower in r.type.lower()]
        elif resource_group:
            resources = reader.get_resources_by_resource_group(resource_group)
        elif resource_type:
            resources = reader.get_resources_by_type(resource_type)
        else:
            resources = reader.get_all_resources()
    except Exception as e:
        return f"Error listing resources: {e}"

    if not resources:
        return "No resources found."

    # Summary by type
    type_counts: dict[str, int] = {}
    for r in resources:
        type_counts[r.type] = type_counts.get(r.type, 0) + 1

    lines = [f"Total {len(resources)} resources found:\n"]
    lines.append("Resource Type Summary:")
    for rtype, count in sorted(type_counts.items()):
        lines.append(f"  - {rtype}: {count}")

    lines.append(f"\nResource List (showing up to 30; use `id` for run_assessment resource_ids):")
    for r in resources[:30]:
        lines.append(
            f"  - id={r.id}\n    {r.name} ({r.type}) [{r.resource_group}] @ {r.location}"
        )
    if len(resources) > 30:
        lines.append(f"  ... and {len(resources) - 30} more")

    return "\n".join(lines)


@tool
def list_checklists() -> str:
    """List all available assessment checklists with a HIGH-LEVEL SUMMARY only.
    Shows checklist names, applicable resource types, and total check counts.
    Does NOT return individual check items or questions.
    To get detailed check items, questions, and guidance, use get_checklist_detail instead."""

    loader = _checklist_loader()
    summary = loader.get_summary()

    lines = [
        f"Total {summary['total_checklists']} checklists loaded",
        f"Total {summary['total_checks']} check items (auto: {summary['automated_checks']}, manual: {summary['manual_checks']})\n",
    ]
    for cl in summary.get("checklists", []):
        cid = cl.get("id", "")
        lines.append(
            f"- id=`{cid}` | {cl['name']}: {cl['total_checks']} checks "
            f"(auto: {cl['automated_checks']}, manual: {cl['manual_checks']})"
        )
        if cl.get("applicable_resource_types"):
            for rt in cl["applicable_resource_types"]:
                lines.append(f"    -> {rt}")

    return "\n".join(lines)


@tool
def get_checklist_detail(
    keyword: Annotated[str, "Keyword to filter check items (e.g. 'backup', 'SSL', 'HA', 'monitoring'). Empty string for all."] = "",
    resource_type: Annotated[str, "Azure resource type to filter checklists (e.g. 'mysql', 'postgresql', 'cosmosdb'). Empty string for all."] = "",
) -> str:
    """Get DETAILED assessment check items including individual questions, guidance, and check types.
    Shows every check question grouped by checklist → category → item, with guidance text.
    Use this when users ask about: checklist details, specific check items, evaluation criteria,
    assessment policies, check questions, guidance, 체크리스트 세부 항목, 점검 항목, 평가 기준, etc.
    Use keyword/resource_type filters to narrow results when the user mentions specific topics."""

    loader = _checklist_loader()

    keyword_lower = keyword.lower().strip()
    resource_type_lower = resource_type.lower().strip()

    lines: list[str] = []
    total_shown = 0

    for _name, checklist in loader.checklists.items():
        # Filter by resource type if specified
        if resource_type_lower:
            type_match = False
            for rt in checklist.applicable_resource_types:
                if resource_type_lower in rt.lower():
                    type_match = True
                    break
            # Also match checklists with no resource type (generic)
            if not type_match and checklist.applicable_resource_types:
                continue

        checklist_lines: list[str] = []
        checklist_count = 0

        for category in checklist.categories:
            cat_lines: list[str] = []
            for item in category.items:
                item_lines: list[str] = []
                for check in item.checks:
                    # Filter by keyword if specified
                    if keyword_lower:
                        searchable = f"{checklist.name} {category.name} {item.name} {check.question} {check.guidance}".lower()
                        if keyword_lower not in searchable:
                            continue
                    check_type_badge = "[auto]" if check.check_type == "automated" else "[manual]"
                    line = f"      {check_type_badge} {check.question}"
                    if check.guidance:
                        line += f"\n        -> guidance: {check.guidance}"
                    item_lines.append(line)

                if item_lines:
                    cat_lines.append(f"    {item.id}. {item.name}")
                    cat_lines.extend(item_lines)
                    checklist_count += len(item_lines)

            if cat_lines:
                checklist_lines.append(f"  [{category.id}] {category.name}")
                checklist_lines.extend(cat_lines)

        if checklist_lines:
            applicable = ", ".join(checklist.applicable_resource_types) if checklist.applicable_resource_types else "(all resource types)"
            lines.append(f"## {checklist.name} (v{checklist.version})")
            lines.append(f"  Applicable: {applicable}")
            if checklist.description:
                lines.append(f"  {checklist.description}")
            lines.extend(checklist_lines)
            lines.append("")
            total_shown += checklist_count

    if not lines:
        filter_desc = []
        if keyword:
            filter_desc.append(f"keyword='{keyword}'")
        if resource_type:
            filter_desc.append(f"resource_type='{resource_type}'")
        return f"No check items found matching {', '.join(filter_desc) or 'any filter'}." 

    header = f"Assessment Policy Check Items ({total_shown} items shown):\n"
    if keyword:
        header += f"  Keyword filter: '{keyword}'\n"
    if resource_type:
        header += f"  Resource type filter: '{resource_type}'\n"
    header += "\n"

    return header + "\n".join(lines)


def run_assessment_for_resources(
    resources: list,
    reader: AzureResourceReader,
    *,
    subscription_id_tool_arg: str = "",
    output_format: str = "all",
    checklist_ids: Optional[list[str]] = None,
) -> str:
    """
    이미 확정된 AzureResource 목록에 대해 체크리스트 매칭·LLM 평가·리포트 생성을 수행합니다.
    (REST /api/assessments/run 및 run_assessment 도구에서 공통 사용)

    checklist_ids가 주어지면 해당 ID(파일 stem)에 한해 리소스 타입과 매칭되는 체크리스트만 LLM 평가에 사용합니다.
    None이면 기존과 같이 타입별 자동 매칭 전체를 사용합니다.
    """
    try:
        ai_endpoint = get_ai_endpoint_from_env()
    except ValueError as e:
        return f"Error: {e}"
    deployment_name = os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-4o")

    if not resources:
        return "No resources found to assess."

    checklist_loader = _checklist_loader()

    # (resource, None) = 자동 매칭, (resource, [Checklist]) = 선택 체크리스트만 사용
    llm_jobs: list = []
    unmatched_resources: list = []

    if checklist_ids is not None:
        keys = [k.strip() for k in checklist_ids if k and str(k).strip()]
        logger.debug("[assessment] selected checklist keys: %s", keys)
        for resource in resources:
            applicable = checklist_loader.get_selected_checklists_for_resource_type(
                resource.type, keys
            )
            logger.debug(
                "[assessment] resource=%s type=%s → applicable checklists=%s",
                resource.name, resource.type, [c.name for c in applicable],
            )
            if applicable:
                llm_jobs.append((resource, applicable))
            else:
                unmatched_resources.append(resource)
    else:
        for resource in resources:
            applicable = checklist_loader.get_checklist_for_resource_type(resource.type)
            logger.debug(
                "[assessment] resource=%s type=%s → applicable checklists=%s",
                resource.name, resource.type, [c.name for c in applicable],
            )
            if applicable:
                llm_jobs.append((resource, None))
            else:
                unmatched_resources.append(resource)

    engine = AssessmentEngine(
        ai_endpoint=ai_endpoint,
        deployment_name=deployment_name,
        checklist_loader=checklist_loader,
    )

    assessments = []
    errors = []

    for resource, checklists_override in llm_jobs:
        try:
            if checklists_override is not None:
                assessment = engine.assess_resource(resource, checklists=checklists_override)
            else:
                assessment = engine.assess_resource(resource)
            logger.debug(
                "[assessment] %s → total_checks=%d results=%d",
                resource.name, assessment.total_checks, len(assessment.results),
            )
            assessments.append(assessment)
        except Exception as e:
            logger.error("[assessment] %s → EXCEPTION: %s", resource.name, e, exc_info=True)
            errors.append(f"{resource.name}: {e}")

    for resource in unmatched_resources:
        if checklist_ids is not None:
            cq = "선택 체크리스트 적용 여부"
            finding = (
                "선택한 체크리스트가 이 리소스 타입에 적용되지 않습니다. "
                "체크리스트의 적용 리소스 타입과 평가 대상을 맞추거나 다른 체크리스트를 선택하세요."
            )
            recommendation = (
                f"리소스 타입: {resource.type}. YAML의 applicable_resource_types와 목록을 확인하세요."
            )
            reason = "Selected checklists do not apply to this resource type"
        else:
            cq = "체크리스트 정의 여부"
            finding = (
                f"리소스 타입 '{resource.type}'에 대한 체크리스트가 "
                f"checklists/ 폴더에 정의되어 있지 않습니다."
            )
            recommendation = (
                f"'{resource.type}' 리소스 타입에 대한 체크리스트 YAML 파일을 "
                f"checklists/ 폴더에 추가하세요."
            )
            reason = "No checklist defined for this resource type"
        assessments.append(ResourceAssessment(
            resource_id=resource.id,
            resource_name=resource.name,
            resource_type=resource.type,
            resource_group=resource.resource_group,
            location=resource.location,
            assessment_time=datetime.now().isoformat(),
            overall_score=0.0,
            total_checks=1,
            passed_checks=0,
            failed_checks=1,
            warning_checks=0,
            results=[CheckResult(
                check_question=cq,
                status=ComplianceStatus.FAIL,
                finding=finding,
                recommendation=recommendation,
                severity="high",
                evidence={
                    "resource_type": resource.type,
                    "resource_name": resource.name,
                    "reason": reason,
                },
            )],
        ))

    if not assessments:
        return "All assessments failed:\n" + "\n".join(errors)

    sub_ids = getattr(reader, "subscription_ids", None) or []
    sub_hint = sub_ids[0] if sub_ids else (subscription_id_tool_arg.strip() or None)

    # ── 리포트 콘텐츠 생성 & 저장 ───────────────────────────────────────────
    report_generator = ReportGenerator(subscription_id_hint=sub_hint)
    _md_content: Optional[str] = None
    _html_content: Optional[str] = None

    # DB 콘텐츠 생성 (파일 저장 없이 문자열만)
    if output_format in ("markdown", "all"):
        try:
            _md_content = report_generator.build_markdown_content(assessments)
        except Exception:
            pass
    if output_format in ("html", "all"):
        try:
            _html_content = report_generator.build_html_content(assessments)
        except Exception:
            pass

    # ── DB 저장 ───────────────────────────────────────────────────────────────
    db_msg = ""
    report_id: Optional[int] = None
    stored_report_md: Optional[str] = _md_content
    try:
        from agent.db.assessment import get_report_detail, save_assessment_report
        report_id = save_assessment_report(
            assessments,
            subscription_id=sub_hint,
            report_md=_md_content,
            report_html=_html_content,
        )
        stored_report = get_report_detail(report_id)
        if stored_report and stored_report.get("report_md"):
            stored_report_md = stored_report["report_md"]
        try:
            from chat.tools.chat_state import set_last_assessment_report_id
            set_last_assessment_report_id(report_id, sub_hint)
        except Exception:
            pass
        db_msg = f"\nDB 저장 완료 (report_id={report_id})"
    except Exception as _db_err:
        db_msg = f"\nDB 저장 실패: {_db_err}"

    successful = [a for a in assessments if a.total_checks > 0]
    total_checks = sum(a.total_checks for a in assessments)
    total_passed = sum(a.passed_checks for a in assessments)
    total_failed = sum(a.failed_checks for a in assessments)
    total_warnings = sum(a.warning_checks for a in assessments)
    avg_score = (
        sum(a.overall_score for a in successful) / len(successful)
        if successful
        else 0
    )

    resource_lines = []
    for a in sorted(assessments, key=lambda x: x.overall_score):
        icon = "PASS" if a.overall_score >= 80 else "WARN" if a.overall_score >= 60 else "FAIL"
        short_type = a.resource_type.split("/")[-1] if "/" in a.resource_type else a.resource_type
        resource_lines.append(
            f"  [{icon}] {a.resource_name} ({short_type}) - {a.overall_score:.1f}%"
        )

    summary_text = (
        f"Assessment Complete!\n"
        f"  Total resources: {len(resources)} "
        f"(checklist matched: {len(llm_jobs)}, "
        f"no checklist (FAIL): {len(unmatched_resources)})\n"
        f"  Total checks: {total_checks}\n"
        f"  Average score: {avg_score:.1f}%\n"
        f"  Pass: {total_passed}, Fail: {total_failed}, Warning: {total_warnings}\n"
        f"\nPer-Resource Scores:\n" + "\n".join(resource_lines) + "\n"
        + db_msg
    )

    if report_id and stored_report_md:
        summary_text += (
            "\n\nSaved Markdown Report From DB:\n"
            + _truncate_for_tool_result(stored_report_md)
        )

    if errors:
        summary_text += f"\n\nAssessment Errors ({len(errors)}):\n" + "\n".join(
            f"  - {e}" for e in errors
        )

    return summary_text


@tool
async def run_assessment(
    subscription_id: Annotated[str, "Azure subscription ID. Empty string to auto-detect from Azure CLI."] = "",
    resource_group: Annotated[str, "Single resource group name (legacy). Combined with resource_group_names. Empty if unused."] = "",
    resource_group_names: Annotated[
        Optional[list[str]],
        "Additional resource group names; all resources in each group are assessed. Merged with resource_group (deduplicated).",
    ] = None,
    resource_ids: Annotated[
        Optional[list[str]],
        "Preferred scope: full ARM resource IDs from list_azure_resources (id=...). "
        "e.g. /subscriptions/{sub}/resourceGroups/{rg}/providers/.../resourceName. Union with resource groups.",
    ] = None,
    resource_name: Annotated[
        str,
        "After discovery, keep only resources with this exact name (case-insensitive). "
        "Prefer passing resource_ids from list_azure_resources instead of name-only scope. "
        "If using names without IDs: combine with resource_group / resource_group_names when possible; "
        "otherwise discovery uses all supported types in the subscription (may match duplicate names).",
    ] = "",
    resource_names: Annotated[
        Optional[list[str]],
        "Multiple exact resource names; merged with resource_name. Only these names are assessed after discovery.",
    ] = None,
    checklist_ids: Annotated[
        Optional[list[str]],
        "Checklist stems (YAML stem, same as UI); multiple allowed. "
        "May be omitted if checklist_id is set. Omit both → applicable checklist catalog only, no assessment.",
    ] = None,
    checklist_id: Annotated[
        str,
        "When the user replies with only checklist id(s): pass the stem here, e.g. system_stability. "
        "Comma/space/semicolon-separated for multiple. Merged with checklist_ids. Prefer this for a single id (no JSON array needed).",
    ] = "",
    output_format: Annotated[str, "Output format: markdown, json, html, or all (default: all)"] = "all",
) -> str:
    """Run the full Azure resource assessment pipeline:
    1. Discover Azure resources
    2. Load checklists and match to resources
    3. Assess each resource using LLM
    4. Generate report (markdown/json/html)

    **Checklist policy (chatbot):** At least one valid id via checklist_ids and/or checklist_id; otherwise the tool returns checklists applicable to the discovered resource types only.

    Discovery order: if any resource_group/resource_group_names/resource_ids is set, use their union;
    else assess all supported resource types in the subscription scope (same as list_azure_resources with no filters).

    If resource_name / resource_names is set, the discovered list is narrowed to those exact names only
    (prevents assessing an entire resource group or subscription when the user asked for one resource).

    Returns a summary of the assessment results."""

    selected = _merge_checklist_selection(checklist_ids, checklist_id)

    try:
        loader = _checklist_loader()
    except Exception as e:
        return f"체크리스트 로드 실패: {e}"

    unknown = [k for k in selected if k not in loader.checklists]
    if unknown:
        return (
            f"알 수 없는 checklist_ids: {unknown}\n\n"
            + _checklist_selection_required_response(loader=loader)
        )

    # Heavy I/O: Azure Resource Graph queries + LLM calls per resource.
    # Run in a thread pool so the event loop stays free for other requests.
    # asyncio.to_thread() copies the current contextvars snapshot to the
    # worker thread, so the Azure session context (tenant/subscription) is
    # automatically available inside _sync().
    def _sync() -> str:
        rg_names = _merge_resource_group_names(resource_group, resource_group_names)
        ids = _normalize_resource_ids(resource_ids)
        prefix_note = ""

        try:
            reader = _reader_for_session(subscription_id)
            if rg_names or ids:
                resources, missing = _discover_resources_scoped(reader, rg_names, ids)
                if missing:
                    shown = ", ".join(missing[:5])
                    if len(missing) > 5:
                        shown += ", ..."
                    prefix_note = (
                        f"[참고] 다음 리소스 ID는 조회되지 않았습니다(Resource Graph에 없거나 권한/구독 범위 밖일 수 있음): {shown}\n\n"
                    )
            else:
                resources = reader.get_all_resources()
        except Exception as e:
            return f"Error discovering resources: {e}"

        name_targets = _merge_resource_name_targets(resource_names, resource_name)
        if name_targets is not None:
            n_before = len(resources)
            resources = _filter_resources_by_exact_name(resources, name_targets)
            if not resources:
                return (
                    f"지정한 리소스 이름 {sorted(name_targets)!r}에 해당하는 리소스가 없습니다 "
                    f"(조회된 후보 {n_before}개 중 일치 없음). "
                    "이름·리소스 그룹을 맞추거나 list_azure_resources의 id로 resource_ids를 지정해 확인하세요."
                )
            if n_before > len(resources):
                prefix_note += (
                    f"[참고] 리소스 이름 필터 적용: {n_before}개 → {len(resources)}개만 평가합니다.\n\n"
                )

        if not selected:
            response = _checklist_selection_required_response(
                loader=loader,
                resources=resources,
            )
            return prefix_note + response if prefix_note else response

        summary = run_assessment_for_resources(
            resources,
            reader,
            subscription_id_tool_arg=subscription_id,
            output_format=output_format,
            checklist_ids=selected,
        )
        return prefix_note + summary if prefix_note else summary

    return await asyncio.to_thread(_sync)
