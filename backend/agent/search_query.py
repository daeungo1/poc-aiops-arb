"""
Assessment Query Module
DB(result_file)에서 Assessment 결과를 조회하고 LLM으로 분석합니다.
"""

import json
from typing import Optional

from .foundry_llm import responses_text


class SearchQueryClient:
    """
    DB에서 Assessment 결과를 조회하고 LLM 분석을 수행하는 클래스.
    """

    SYSTEM_PROMPT = """당신은 Azure 클라우드 인프라 운영 전문가입니다.
Azure Architecture Review Board 평가 결과를 분석하여 사용자의 질문에 답변합니다.

답변 시 다음 원칙을 따르세요:
1. 제공된 평가 데이터에 기반하여 정확하게 답변합니다.
2. 심각도(critical > high > medium > low) 순으로 우선순위를 제시합니다.
3. 구체적인 리소스 이름과 점검 항목을 언급합니다.
4. 실행 가능한 개선 방안을 제시합니다.
5. 데이터에 없는 내용은 추측하지 않습니다."""

    def __init__(
        self,
        ai_endpoint: str,
        deployment_name: str = "gpt-4o",
        credential: Optional[object] = None,
    ):
        """
        Args:
            ai_endpoint: Azure AI Foundry root 엔드포인트
            deployment_name: LLM 배포 이름
            credential: Azure 자격 증명 (None이면 LazyDefaultAzureCredential / DefaultAzureCredential)
        """
        # LLM (Responses API는 foundry_llm 헬퍼가 프로젝트 엔드포인트로 호출)
        self.base_endpoint = ai_endpoint.strip().rstrip("/") if ai_endpoint else None
        self.deployment_name = deployment_name

    # ------------------------------------------------------------------
    # 검색 쿼리
    # ------------------------------------------------------------------

    def get_latest_assessments(
        self,
        top: int = 50,
        resource_type: Optional[str] = None,
        resource_group: Optional[str] = None,
        min_score: Optional[float] = None,
        max_score: Optional[float] = None,
        subscription_id: Optional[str] = None,
    ) -> list[dict]:
        """최근 assessment 결과를 DB에서 조회합니다."""
        results = self._get_from_db(top, resource_type, resource_group, subscription_id)
        if min_score is not None:
            results = [r for r in results if r.get("overall_score", 0) >= min_score]
        if max_score is not None:
            results = [r for r in results if r.get("overall_score", 0) <= max_score]
        return results

    def search_assessments(
        self,
        query: str,
        top: int = 10,
        subscription_id: Optional[str] = None,
    ) -> list[dict]:
        """키워드로 assessment 결과를 검색합니다. (현재 미구현)"""
        return []

    def _get_from_db(
        self,
        top: int = 50,
        resource_type: Optional[str] = None,
        resource_group: Optional[str] = None,
        subscription_id: Optional[str] = None,
    ) -> list[dict]:
        """DB result_file 테이블에서 최신 assessment 결과를 조회합니다."""
        try:
            from .db.assessment import is_db_configured, list_individual_files, get_file_detail
            if not is_db_configured():
                return []
            rows = list_individual_files(subscription_id=subscription_id, limit=top)
            results = []
            for row in rows:
                if resource_type and resource_type.lower() not in (row.get("resource_type") or "").lower():
                    continue
                if resource_group:
                    detail = get_file_detail(row["id"])
                    if detail and resource_group.lower() not in (detail.get("resource_group") or "").lower():
                        continue
                    if detail:
                        results.append(self._normalize_db_row(row, detail))
                        continue
                results.append(self._normalize_db_row(row, None))
            return results
        except Exception as e:
            print(f"Failed to fetch from DB in SearchQueryClient: {e}")
            return []

    @staticmethod
    def _normalize_db_row(row: dict, detail: Optional[dict]) -> dict:
        """DB row를 공통 dict 스키마로 변환합니다."""
        details_data = {}
        if detail:
            raw = detail.get("details") or {}
            if isinstance(raw, str):
                try:
                    details_data = json.loads(raw)
                except Exception:
                    pass
            elif isinstance(raw, dict):
                details_data = raw

        results_list = details_data.get("results") or []
        findings, recs = [], []
        for r in results_list:
            status = (r.get("status") or "").lower()
            if status in ("fail", "warning"):
                findings.append(f"- {r.get('check_question') or r.get('question')}: {r.get('finding')}")
                recs.append(f"- {r.get('check_question') or r.get('question')}: {r.get('recommendation')}")

        return {
            "resource_name": row.get("resource_name", ""),
            "resource_type": row.get("resource_type", ""),
            "resource_group": details_data.get("resource_group", ""),
            "location": details_data.get("location", ""),
            "overall_score": float(row.get("overall_score") or 0),
            "passed_checks": details_data.get("passed_checks", 0),
            "failed_checks": details_data.get("failed_checks", 0),
            "warning_checks": details_data.get("warning_checks", 0),
            "results": results_list,
            "results_json": json.dumps(results_list),
            "findings_text": "\n".join(findings),
            "recommendations_text": "\n".join(recs),
        }

    # ------------------------------------------------------------------
    # LLM 분석
    # ------------------------------------------------------------------

    def _format_assessments_for_llm(self, documents: list[dict]) -> str:
        """검색 결과를 LLM 프롬프트용 텍스트로 변환"""
        if not documents:
            return "(평가 데이터 없음)"

        sections = []
        for doc in documents:
            section = (
                f"### {doc.get('resource_name', 'N/A')} "
                f"({doc.get('resource_type', 'N/A')})\n"
                f"- 리소스 그룹: {doc.get('resource_group', 'N/A')}\n"
                f"- 위치: {doc.get('location', 'N/A')}\n"
                f"- 평가 시각: {doc.get('assessment_time', 'N/A')}\n"
                f"- 점수: {doc.get('overall_score', 'N/A')}% "
                f"(Pass: {doc.get('passed_checks', 0)}, "
                f"Fail: {doc.get('failed_checks', 0)}, "
                f"Warning: {doc.get('warning_checks', 0)})\n\n"
                f"**발견 사항:**\n{doc.get('findings_text', '없음')}\n\n"
                f"**권장 사항:**\n{doc.get('recommendations_text', '없음')}"
            )
            sections.append(section)

        return "\n\n---\n\n".join(sections)

    def ask(
        self,
        question: str,
        documents: Optional[list[dict]] = None,
        top: int = 30,
        subscription_id: Optional[str] = None,
    ) -> str:
        """
        Assessment 데이터를 컨텍스트로 LLM에 질문합니다.

        documents를 직접 전달하거나, None이면 최신 assessment를 자동 조회합니다.

        Args:
            question: 사용자 질문
            documents: 컨텍스트로 사용할 문서 (None이면 최신 조회)
            top: 자동 조회 시 가져올 문서 수
            subscription_id: 자동 조회 시 이 구독 범위로 스냅샷 제한

        Returns:
            LLM 응답 텍스트
        """
        if documents is None:
            documents = self.get_latest_assessments(top=top, subscription_id=subscription_id)

        context = self._format_assessments_for_llm(documents)

        user_prompt = (
            f"## 평가 데이터\n\n{context}\n\n"
            f"---\n\n## 질문\n\n{question}"
        )

        return responses_text(self.deployment_name, self.SYSTEM_PROMPT, user_prompt)

    def analyze_latest(
        self,
        question: str = "전체 평가 결과를 요약하고, 가장 시급하게 개선해야 할 항목 Top 5를 알려주세요.",
        resource_type: Optional[str] = None,
        resource_group: Optional[str] = None,
        subscription_id: Optional[str] = None,
    ) -> dict:
        """
        최신 Assessment를 조회하고 LLM 분석 결과를 반환합니다.

        Args:
            question: LLM에 전달할 질문
            resource_type: 필터링할 리소스 타입 (선택)
            resource_group: 필터링할 리소스 그룹 (선택)
            subscription_id: 스냅샷을 이 구독 범위로 제한 (선택)

        Returns:
            {
                "documents_count": int,
                "question": str,
                "answer": str,
                "documents": list[dict]  # 조회된 원본 문서
            }
        """
        documents = self.get_latest_assessments(
            resource_type=resource_type,
            resource_group=resource_group,
            subscription_id=subscription_id,
        )

        answer = self.ask(question=question, documents=documents)

        return {
            "documents_count": len(documents),
            "question": question,
            "answer": answer,
            "documents": documents,
        }
