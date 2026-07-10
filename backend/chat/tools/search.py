"""
AI Search Query Tools

Query and analyze assessment results stored in Azure AI Search or Azure Storage.
"""

import json
from typing import Annotated
import traceback

from agent_framework._tools import tool

from chat.tools._common import get_search_query_client
from chat.tools.azure_session import resolve_assessment_subscription_id


@tool
def get_latest_assessments(
    resource_type: Annotated[str, "Azure resource type filter. Can be a full type (e.g. 'microsoft.dbformysql/flexibleservers') or a partial keyword (e.g. 'mysql', 'cosmosdb'). Empty string for all."] = "",
    resource_group: Annotated[str, "Resource group name filter. Empty string for all."] = "",
    top: Annotated[int, "Maximum number of documents to retrieve"] = 20,
) -> str:
    """Query the latest Assessment results from Storage or Search.
    Returns summary info including resource name, type, score, pass/fail counts.
    Supports partial resource_type matching (e.g. 'mysql' matches 'Microsoft.DBforMySQL/flexibleServers')."""

    try:
        client = get_search_query_client()
        sub_id = resolve_assessment_subscription_id()

        # If resource_type looks like a partial keyword (no '/'), fetch all and filter in Python
        rt_filter = resource_type.strip() if resource_type else ""
        use_partial = rt_filter and "/" not in rt_filter

        docs = client.get_latest_assessments(
            top=top if not use_partial else max(top * 3, 50),
            resource_type=None if use_partial else (rt_filter or None),
            resource_group=resource_group or None,
            subscription_id=sub_id,
        )

        if use_partial:
            rt_lower = rt_filter.lower()
            docs = [d for d in docs if rt_lower in d.get("resource_type", "").lower()][:top]

        if not docs:
            return "최신 진단 리포트가 스토리지(results 컨테이너)에 존재하지 않거나, 필터 조건과 일치하는 항목이 없습니다."

        lines = [f"총 {len(docs)}건의 진단 결과를 클라우드에서 찾았습니다:\n"]
        for doc in docs:
            score = doc.get("overall_score", 0)
            icon = "PASS" if score >= 80 else "WARN" if score >= 60 else "FAIL"
            lines.append(
                f"[{icon}] {doc.get('resource_name', 'N/A')} "
                f"({doc.get('resource_type', 'N/A')})\n"
                f"  - 리소스 그룹: {doc.get('resource_group', 'N/A')}\n"
                f"  - 위치: {doc.get('location', 'N/A')}\n"
                f"  - 점수: {score:.1f}%\n"
                f"  - 준수: {doc.get('passed_checks', 0)}, "
                f"미준수: {doc.get('failed_checks', 0)}, "
                f"경고: {doc.get('warning_checks', 0)}\n"
                f"  - 진단 시각: {str(doc.get('assessment_time', ''))[:19]}\n"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"진단 결과를 불러오는 중 오류가 발생했습니다: {str(e)}\n{traceback.format_exc()}"


@tool
def search_assessments(
    query: Annotated[str, "Search keyword (e.g. 'backup', 'private endpoint', 'SSL')"],
    top: Annotated[int, "Maximum number of results to return"] = 10,
) -> str:
    """Search assessment results by keyword.
    Performs full-text search across various fields."""

    try:
        client = get_search_query_client()
        sub_id = resolve_assessment_subscription_id()
        docs = client.search_assessments(query=query, top=top, subscription_id=sub_id)

        if not docs:
            return f" '{query}' 검색 결과와 일치하는 진단 리포트 항목이 없습니다."

        lines = [f"'{query}' 검색 결과: {len(docs)}개 항목 발견\n"]
        for doc in docs:
            score = doc.get("overall_score", 0)
            lines.append(
                f"- {doc.get('resource_name', 'N/A')} ({doc.get('resource_type', '')}) - 점수: {score:.1f}%\n"
                f"  발견 사항 요약: {(doc.get('findings_text', '') or '')[:300]}...\n"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"검색 수행 중 오류가 발생했습니다: {str(e)}\n{traceback.format_exc()}"


@tool
def get_resource_detail(
    resource_name: Annotated[str, "Name of the resource to get detailed assessment for"],
) -> str:
    """Get detailed Assessment results for a specific resource including recommendations, findings, and evidence."""

    try:
        client = get_search_query_client()
        sub_id = resolve_assessment_subscription_id()
        docs = client.search_assessments(
            query=resource_name, top=3, subscription_id=sub_id
        )

        if not docs:
            return f"'{resource_name}' 리소스에 대한 상세 진단 결과를 찾을 수 없습니다."

        # 이름이 정확히 일치하는 리소스를 우선적으로 찾음
        doc = docs[0]
        for d in docs:
            if d.get("resource_name", "").lower() == resource_name.lower():
                doc = d
                break

        lines = [
            f"## {doc.get('resource_name', 'N/A')} 상세 평가 리포트\n",
            f"- 리소스 타입: ({doc.get('resource_type', 'N/A')})",
            f"- 리소스 그룹: {doc.get('resource_group', 'N/A')}",
            f"- 위치: {doc.get('location', 'N/A')}",
            f"- 최종 점수: {doc.get('overall_score', 0):.1f}%",
            f"- 진단 시각: {str(doc.get('assessment_time', ''))[:19]}",
            f"- 통계: [준수: {doc.get('passed_checks', 0)}, 미준수: {doc.get('failed_checks', 0)}, 경고: {doc.get('warning_checks', 0)}]\n",
            "### 🔎 주요 발견 사항",
            doc.get("findings_text", "(없음)"),
            "\n### 💡 개선 권고사항",
            doc.get("recommendations_text", "(없음)"),
        ]

        results_json = doc.get("results_json", "[]")
        try:
            results = json.loads(results_json) if isinstance(results_json, str) else results_json
            fail_items = [r for r in results if r.get("status") in ("fail", "warning")]
            if fail_items:
                lines.append("\n### ⚠️ 상세 조치 필요 항목")
                for item in fail_items:
                    lines.append(
                        f"\n**[{item['status'].upper()}] [{item.get('severity', 'medium').upper()}]** {item.get('question', '')}\n"
                        f"- 발견: {item.get('finding', '')}\n"
                        f"- 권고: {item.get('recommendation', '')}\n"
                        f"- 근거: {json.dumps(item.get('evidence', {}), ensure_ascii=False)}"
                    )
        except (json.JSONDecodeError, TypeError):
            pass

        return "\n".join(lines)
    except Exception as e:
        return f"상세 정보를 불러오는 중 오류가 발생했습니다: {str(e)}\n{traceback.format_exc()}"
