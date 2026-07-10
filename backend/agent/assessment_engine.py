"""
Assessment Engine Module
LLM을 활용하여 Azure 리소스를 체크리스트 기준으로 진단합니다.
"""

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from .foundry_llm import responses_json

from .azure_resource_reader import AzureResource, AzureResourceReader
from .checklist_loader import ChecklistLoader, Checklist, CheckItem

logger = logging.getLogger(__name__)


class ComplianceStatus(Enum):
    """점검 결과 상태"""
    PASS = "pass"           # 준수
    FAIL = "fail"           # 미준수
    WARNING = "warning"     # 경고 (개선 권장)
    NOT_APPLICABLE = "n/a"  # 해당 없음
    MANUAL_REVIEW = "manual_review"  # 수동 검토 필요


@dataclass
class CheckResult:
    """개별 점검 결과"""
    check_question: str
    status: ComplianceStatus
    finding: str           # 발견 사항
    recommendation: str    # 개선 권장사항
    evidence: dict = field(default_factory=dict)  # 근거 데이터
    severity: str = "medium"  # low, medium, high, critical
    checklist_name: str = ""  # 출처 체크리스트 이름


@dataclass
class ResourceAssessment:
    """리소스별 평가 결과"""
    resource_id: str
    resource_name: str
    resource_type: str
    resource_group: str
    location: str
    assessment_time: str
    overall_score: float  # 0-100
    total_checks: int
    passed_checks: int
    failed_checks: int
    warning_checks: int
    results: list[CheckResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "resource_id": self.resource_id,
            "resource_name": self.resource_name,
            "resource_type": self.resource_type,
            "resource_group": self.resource_group,
            "location": self.location,
            "assessment_time": self.assessment_time,
            "overall_score": self.overall_score,
            "summary": {
                "total_checks": self.total_checks,
                "passed": self.passed_checks,
                "failed": self.failed_checks,
                "warnings": self.warning_checks
            },
            "results": [
                {
                    "question": r.check_question,
                    "status": r.status.value,
                    "finding": r.finding,
                    "recommendation": r.recommendation,
                    "severity": r.severity,
                    "evidence": r.evidence,
                    "checklist_name": r.checklist_name,
                }
                for r in self.results
            ]
        }


class AssessmentEngine:
    """
    LLM 기반 리소스 평가 엔진
    """

    SYSTEM_PROMPT = """당신은 Azure 클라우드 아키텍처 전문가입니다.
주어진 Azure 리소스 구성 정보를 Architecture Review Board 체크리스트 기준으로 평가합니다.

평가 시 다음 원칙을 따르세요:
1. 객관적 사실에 기반하여 평가합니다.
2. 리소스 속성 정보에서 확인 가능한 항목은 명확히 판단합니다.
3. 정보가 부족한 경우 "manual_review"로 표시합니다.
4. 각 항목에 대해 구체적인 개선 방안을 제시합니다.

반드시 JSON 형식으로 응답하세요."""

    ASSESSMENT_PROMPT_TEMPLATE = """
## 평가 대상 리소스

**리소스 정보:**
```json
{resource_json}
```

## 적용할 체크리스트

{checklist_items}

## 평가 요청

위 리소스에 대해 체크리스트 항목들을 평가하고, 다음 JSON 형식으로 응답해주세요:

```json
{{
  "assessments": [
    {{
      "question": "체크리스트 질문",
      "status": "pass|fail|warning|n/a|manual_review",
      "finding": "발견 사항 (리소스 속성 기반)",
      "recommendation": "개선 권장사항",
      "severity": "low|medium|high|critical",
      "evidence": {{
        "property_checked": "확인한 속성",
        "actual_value": "실제 값",
        "expected_value": "기대 값"
      }}
    }}
  ],
  "overall_summary": "전체 평가 요약"
}}
```

중요:
- status가 "pass"면 finding에 준수하고 있는 내용을 기술
- status가 "fail"이면 미준수 사항과 구체적인 개선 방법을 recommendation에 기술
- evidence에는 판단의 근거가 된 리소스 속성 정보를 포함
"""

    def __init__(
        self,
        ai_endpoint: str,
        deployment_name: str = "gpt-4o",
        checklist_loader: Optional[ChecklistLoader] = None
    ):
        """
        Args:
            ai_endpoint: Azure AI Foundry root 엔드포인트
            deployment_name: 배포 모델 이름
            checklist_loader: 체크리스트 로더 (None이면 기본 경로에서 로드)
        """
        self.base_endpoint = ai_endpoint.strip().rstrip("/")
        if not self.base_endpoint:
            raise ValueError("AZURE_AI_ENDPOINT is required")
        self.deployment_name = deployment_name

        # 체크리스트: 외부에서 주입된 로더는 이미 로드됨. 미주입 시에만 로컬 load_all().
        if checklist_loader is not None:
            self.checklist_loader = checklist_loader
        else:
            self.checklist_loader = ChecklistLoader()
            self.checklist_loader.load_all()


    # ------------------------------------------------------------------
    # 체크리스트 분류
    # ------------------------------------------------------------------

    def _split_checklists(
        self,
        checklists: list[Checklist],
        resource_type: str,
    ) -> tuple[list[Checklist], list[Checklist]]:
        """
        체크리스트를 specific / universal 로 분류합니다.

        Returns:
            (specific, universal)
            - specific  : applicable_resource_types 가 있고 resource_type 에 매칭되는 것
            - universal : applicable_resource_types 가 없는 범용 체크리스트
        """
        resource_type_lower = resource_type.lower()
        specific: list[Checklist] = []
        universal: list[Checklist] = []

        for cl in checklists:
            if cl.applicable_resource_types:
                if any(at.lower() in resource_type_lower for at in cl.applicable_resource_types):
                    specific.append(cl)
                # 매칭되지 않는 specific 체크리스트는 제외 (잘못 전달된 경우 방어)
            else:
                universal.append(cl)

        return specific, universal

    # ------------------------------------------------------------------
    # LLM 호출 단위
    # ------------------------------------------------------------------

    def _run_llm_job(
        self,
        resource: AzureResource,
        job_checklists: list[Checklist],
        job_label: str,
        shared_results: list[CheckResult],
        lock: threading.Lock,
    ) -> None:
        """
        단일 LLM 호출 작업. 결과를 shared_results 에 lock 으로 보호하여 추가합니다.
        ThreadPoolExecutor 의 worker 함수로 사용됩니다.
        """
        logger.debug(
            "[engine] LLM job start | resource=%s | checklists=%s",
            resource.name, [c.name for c in job_checklists],
        )
        checklist_text = self._format_checklist_items(job_checklists, resource.type)
        prompt = self.ASSESSMENT_PROMPT_TEMPLATE.format(
            resource_json=json.dumps(resource.to_dict(), indent=2, ensure_ascii=False),
            checklist_items=checklist_text,
        )
        response = self._call_llm(prompt)
        job_results = self._parse_check_results(response, job_label)
        logger.debug(
            "[engine] LLM job done  | resource=%s | label=%s | results=%d | raw_assessments=%d",
            resource.name, job_label, len(job_results),
            len(response.get("assessments", [])),
        )

        with lock:
            shared_results.extend(job_results)

    # ------------------------------------------------------------------
    # Public: 단일 리소스 평가
    # ------------------------------------------------------------------

    def assess_resource(
        self,
        resource: AzureResource,
        checklists: Optional[list[Checklist]] = None
    ) -> ResourceAssessment:
        """
        단일 리소스를 평가합니다.

        체크리스트를 applicable_resource_types 기준으로 분류한 뒤
        ThreadPoolExecutor 로 병렬 LLM 호출을 수행합니다.

        - specific 체크리스트(여러 개) → 합쳐서 LLM 1회
        - universal 체크리스트        → 각각 별도 LLM 1회
        """
        if checklists is None:
            checklists = self.checklist_loader.get_checklist_for_resource_type(
                resource.type
            )

        if not checklists:
            checklists = [
                cl for cl in self.checklist_loader.checklists.values()
                if not cl.applicable_resource_types
            ]

        specific, universal = self._split_checklists(checklists, resource.type)
        logger.debug(
            "[engine] split | resource=%s | specific=%s | universal=%s",
            resource.name,
            [c.name for c in specific],
            [c.name for c in universal],
        )

        # LLM 작업 목록: (체크리스트 그룹, 레이블)
        llm_jobs: list[tuple[list[Checklist], str]] = []
        if specific:
            label = " + ".join(cl.name for cl in specific)
            llm_jobs.append((specific, label))
        for cl in universal:
            llm_jobs.append(([cl], cl.name))

        if not llm_jobs:
            logger.warning("[engine] no jobs after split | resource=%s | falling back to full checklists", resource.name)
            llm_jobs = [(checklists, "전체")]

        # 공유 결과 리스트 + Lock
        shared_results: list[CheckResult] = []
        lock = threading.Lock()

        with ThreadPoolExecutor(max_workers=len(llm_jobs)) as executor:
            future_to_label = {
                executor.submit(
                    self._run_llm_job,
                    resource, job_cls, job_label, shared_results, lock,
                ): job_label
                for job_cls, job_label in llm_jobs
            }
            for future in as_completed(future_to_label):
                label = future_to_label[future]
                try:
                    future.result()
                except Exception as e:
                    # 개별 잡 실패는 리소스 전체를 날리지 않고 경고 항목으로 대체
                    logger.error(
                        "[engine] LLM job failed | resource=%s | label=%s | error=%s",
                        resource.name, label, e, exc_info=True,
                    )
                    with lock:
                        shared_results.append(CheckResult(
                            check_question=f"[{label}] 체크리스트 평가",
                            status=ComplianceStatus.MANUAL_REVIEW,
                            finding=f"LLM 평가 중 오류 발생: {e}",
                            recommendation="수동 검토 필요",
                            severity="high",
                            checklist_name=label,
                        ))

        return self._build_assessment(resource, shared_results)

    # ------------------------------------------------------------------
    # Public: 여러 리소스 평가
    # ------------------------------------------------------------------

    def assess_resources(
        self,
        resources: list[AzureResource]
    ) -> list[ResourceAssessment]:
        """
        여러 리소스를 순차적으로 평가합니다.
        (리소스 내부의 체크리스트 호출은 병렬 처리됩니다.)
        """
        assessments = []
        for resource in resources:
            try:
                assessment = self.assess_resource(resource)
                assessments.append(assessment)
            except Exception as e:
                print(f"리소스 평가 실패 ({resource.name}): {e}")
                assessments.append(ResourceAssessment(
                    resource_id=resource.id,
                    resource_name=resource.name,
                    resource_type=resource.type,
                    resource_group=resource.resource_group,
                    location=resource.location,
                    assessment_time=datetime.now().isoformat(),
                    overall_score=0,
                    total_checks=0,
                    passed_checks=0,
                    failed_checks=0,
                    warning_checks=0,
                    results=[CheckResult(
                        check_question="평가 수행",
                        status=ComplianceStatus.MANUAL_REVIEW,
                        finding=f"평가 중 오류 발생: {str(e)}",
                        recommendation="수동 검토 필요",
                        severity="high",
                    )]
                ))

        return assessments

    def assess_by_resource_group(
        self,
        resource_reader: AzureResourceReader,
        resource_group: str
    ) -> list[ResourceAssessment]:
        """
        특정 리소스 그룹의 모든 리소스를 평가합니다.
        """
        resources = resource_reader.get_resources_by_resource_group(resource_group)
        return self.assess_resources(resources)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _format_checklist_items(
        self,
        checklists: list[Checklist],
        resource_type: str
    ) -> str:
        """체크리스트 항목을 텍스트로 포맷팅"""
        lines = []

        for checklist in checklists:
            lines.append(f"### {checklist.name}")

            for category in checklist.categories:
                lines.append(f"\n**{category.name}**")

                for item in category.items:
                    lines.append(f"\n*{item.id}. {item.name}*")

                    for check in item.checks:
                        check_type = "자동" if check.check_type == "automated" else "수동"
                        lines.append(f"- [{check_type}] {check.question}")
                        if check.guidance:
                            lines.append(f"  - 가이드: {check.guidance}")

        return "\n".join(lines)

    def _call_llm(self, prompt: str) -> dict:
        """Responses API(agent_framework)로 평가를 수행하고 JSON 응답을 파싱합니다."""
        return responses_json(
            self.deployment_name,
            self.SYSTEM_PROMPT,
            prompt,
            {"type": "json_object"},
        )

    def _parse_check_results(
        self,
        response: dict,
        checklist_name: str,
    ) -> list[CheckResult]:
        """LLM 응답에서 CheckResult 리스트를 파싱합니다."""
        status_map = {
            "pass": ComplianceStatus.PASS,
            "fail": ComplianceStatus.FAIL,
            "warning": ComplianceStatus.WARNING,
            "n/a": ComplianceStatus.NOT_APPLICABLE,
            "manual_review": ComplianceStatus.MANUAL_REVIEW,
        }
        results: list[CheckResult] = []
        for item in response.get("assessments", []):
            status_str = item.get("status", "manual_review").lower()
            status = status_map.get(status_str, ComplianceStatus.MANUAL_REVIEW)
            results.append(CheckResult(
                check_question=item.get("question", ""),
                status=status,
                finding=item.get("finding", ""),
                recommendation=item.get("recommendation", ""),
                evidence=item.get("evidence", {}),
                severity=item.get("severity", "medium"),
                checklist_name=checklist_name,
            ))
        return results

    def _build_assessment(
        self,
        resource: AzureResource,
        results: list[CheckResult],
    ) -> ResourceAssessment:
        """CheckResult 리스트로 ResourceAssessment 를 생성합니다."""
        total = len(results)
        passed  = sum(1 for r in results if r.status == ComplianceStatus.PASS)
        failed  = sum(1 for r in results if r.status == ComplianceStatus.FAIL)
        warnings = sum(1 for r in results if r.status == ComplianceStatus.WARNING)
        excluded = sum(1 for r in results if r.status in (
            ComplianceStatus.NOT_APPLICABLE, ComplianceStatus.MANUAL_REVIEW
        ))

        scorable = total - excluded
        score = (passed / scorable * 100) if scorable > 0 else 0

        return ResourceAssessment(
            resource_id=resource.id,
            resource_name=resource.name,
            resource_type=resource.type,
            resource_group=resource.resource_group,
            location=resource.location,
            assessment_time=datetime.now().isoformat(),
            overall_score=round(score, 2),
            total_checks=total,
            passed_checks=passed,
            failed_checks=failed,
            warning_checks=warnings,
            results=results,
        )

    # ------------------------------------------------------------------
    # 레거시: _parse_assessment_response (하위 호환)
    # ------------------------------------------------------------------

    def _parse_assessment_response(
        self,
        resource: AzureResource,
        response: dict
    ) -> ResourceAssessment:
        results = self._parse_check_results(response, checklist_name="")
        return self._build_assessment(resource, results)

    def get_assessment_context(self) -> str:
        """
        LLM 컨텍스트로 사용할 체크리스트 전체 내용을 반환합니다.
        """
        all_items = self.checklist_loader.get_all_check_items()
        return json.dumps(all_items, indent=2, ensure_ascii=False)
