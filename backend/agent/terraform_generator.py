"""
Terraform Generator Module
Assessment 권고 사항을 기반으로 Terraform 코드를 생성합니다.
DB(result_file)에서 가장 최근 Assessment를 조회하고 LLM으로 Terraform 코드를 생성합니다.
"""

import json
from typing import Optional
from pathlib import Path
from datetime import datetime

from .foundry_llm import responses_text
from .subscription_scope import assessment_dict_matches_subscription

# Chat Completions structured output (strict JSON Schema: 속성명은 [a-zA-Z0-9_]+ 만 허용)
_TF_JSON_FIELD_TO_FILENAME: dict[str, str] = {
    "provider_tf": "provider.tf",
    "variables_tf": "variables.tf",
    "main_tf": "main.tf",
    "outputs_tf": "outputs.tf",
}

# 마커 concat 시 고정 순서 (README·로그와 동일하게 유지)
_TERRAFORM_MARKER_FILE_ORDER: tuple[str, ...] = (
    "provider.tf",
    "variables.tf",
    "main.tf",
    "outputs.tf",
)

TERRAFORM_RESPONSE_JSON_SCHEMA: dict[str, object] = {
    "name": "terraform_remediation",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "provider_tf": {
                "type": "string",
                "description": "provider.tf 전체 HCL (terraform, provider azurerm 등)",
            },
            "variables_tf": {
                "type": "string",
                "description": "variables.tf 전체 HCL",
            },
            "main_tf": {
                "type": "string",
                "description": "main.tf 전체 HCL; 상단에 해결하는 FAIL/WARNING 목록 주석",
            },
            "outputs_tf": {
                "type": "string",
                "description": "outputs.tf 전체 HCL",
            },
        },
        "required": ["provider_tf", "variables_tf", "main_tf", "outputs_tf"],
        "additionalProperties": False,
    },
}


class TerraformGenerator:
    """
    Assessment 권고 사항을 기반으로 Terraform 코드를 생성하는 클래스.
    DB에서 최신 Assessment를 조회하고, fail/warning 항목의 권고 사항을
    반영한 Terraform 코드를 LLM이 작성합니다.
    """

    SYSTEM_PROMPT = """당신은 Azure 인프라를 Terraform으로 구현하는 전문가입니다.
Azure Architecture Review Board 체크리스트 평가 결과에서 fail/warning 으로 판정된 문제점을
**직접 해결**하는 Terraform 코드를 생성합니다.

핵심 목표:
- 각 fail/warning 항목의 "발견(finding)"에 기술된 **구체적 문제를 해결**하는 Terraform 구성을 작성합니다.
- "권고(recommendation)"에 제시된 개선 방안을 Terraform 코드로 **구현**합니다.
- "근거(evidence)"의 현재 리소스 구성 데이터를 참고하여 **현재 상태에서 개선된 상태로** 변경합니다.

코드 작성 원칙:
1. azurerm provider 최신 버전 기준으로 작성합니다.
2. 리소스의 현재 구성(evidence)을 기반으로 기존 설정은 유지하되, 문제가 지적된 부분만 수정합니다.
3. 각 문제 해결이 반영된 부분에 주석을 달아 어떤 체크리스트 항목의 문제를 해결하는지 명시합니다.
   형식: `# FIX [체크리스트 항목 ID/이름]: 문제 설명 → 해결 방법`
4. 변수(variable)를 적절히 사용하여 재사용 가능하게 작성합니다.
5. 리소스 간 의존성을 고려합니다.
6. status가 "pass" 인 항목의 현재 구성은 유지합니다.
7. status가 "fail" 또는 "warning" 인 항목의 문제를 **반드시 해결**합니다.
8. status가 "manual_review" 또는 "n/a" 인 항목은 무시합니다.

문제 해결 예시:
- "Private Endpoint가 구성되어 있지 않습니다" → azurerm_private_endpoint 리소스 추가
- "VNet 연계가 되어 있지 않습니다" → network_rules에 virtual_network_subnet_ids 추가
- "백업이 설정되어 있지 않습니다" → backup 블록 추가 및 retention 설정
- "암호화 키가 서비스 관리 키입니다" → azurerm_key_vault_key + customer_managed_key 블록 추가
- "네트워크 방화벽 기본 정책이 Allow입니다" → default_action = "Deny"로 변경
- "Geo-Replication이 구성되지 않았습니다" → SKU를 GRS/RAGRS로 변경 또는 복제 설정 추가
- "SSL 미적용" → ssl_enforcement_enabled = true 설정
- "고가용성 미구성" → high_availability 블록 추가

출력 형식 (API가 강제하는 JSON — 자유 텍스트·마크다운 금지):
- 응답은 JSON 객체 하나뿐이며, 다음 네 문자열 필드만 포함합니다.
  - provider_tf → 디스크상 파일명 provider.tf 의 전체 본문
  - variables_tf → variables.tf
  - main_tf → main.tf
  - outputs_tf → outputs.tf
- 각 값은 해당 .tf 파일에 넣을 HCL 전체입니다. 설명은 코드 내 주석(#)으로만 작성합니다.
- main.tf 상단에 해결하는 FAIL/WARNING 문제 목록을 주석으로 요약합니다."""

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

    def get_specific_assessment_file(self, filename: str) -> Optional[dict]:
        """지정된 파일명(JSON)을 로컬에서 직접 로드합니다."""
        results_dir = Path(__file__).parent.parent / "results"
        filepath = results_dir / filename
        if filepath.exists():
            try:
                data = json.loads(filepath.read_text(encoding="utf-8"))
                if "assessments" in data:
                    ass = data["assessments"][0] if data["assessments"] else {}
                else:
                    ass = data
                if "results" in ass and "results_json" not in ass:
                    ass["results_json"] = json.dumps(ass["results"])
                return ass
            except Exception as e:
                print(f"Failed to load local file {filename}: {e}")
        return None

    def get_latest_assessments(
        self,
        top: int = 50,
        resource_type: Optional[str] = None,
        resource_group: Optional[str] = None,
        subscription_id: Optional[str] = None,
    ) -> list[dict]:
        """가장 최근 assessment 결과를 DB에서 조회합니다."""
        return self._get_latest_from_db(top, resource_type, resource_group, subscription_id)

    def _get_latest_from_db(
        self,
        top: int = 50,
        resource_type: Optional[str] = None,
        resource_group: Optional[str] = None,
        subscription_id: Optional[str] = None,
    ) -> list[dict]:
        """DB result_file 테이블에서 최신 assessment 결과를 읽어옵니다."""
        try:
            from .db.assessment import is_db_configured, list_individual_files, get_file_detail
            if not is_db_configured():
                return []

            rows = list_individual_files(subscription_id=subscription_id, limit=top)
            all_results = []
            for row in rows:
                if resource_type and resource_type.lower() not in (row.get("resource_type") or "").lower():
                    continue
                file_id = row.get("id")
                detail = get_file_detail(file_id) if file_id else None
                if not detail:
                    continue

                details_data = detail.get("details") or {}
                if isinstance(details_data, str):
                    try:
                        details_data = json.loads(details_data)
                    except Exception:
                        details_data = {}

                raw_results = details_data.get("results") or []
                normalized = []
                for r in raw_results:
                    evidence = r.get("evidence") or {}
                    if not isinstance(evidence, dict):
                        evidence = {}
                    normalized.append({
                        "status": r.get("status", ""),
                        "severity": r.get("severity", ""),
                        "question": r.get("check_question") or r.get("question", ""),
                        "finding": r.get("finding", ""),
                        "recommendation": r.get("recommendation", ""),
                        "evidence": evidence,
                    })

                if resource_group and resource_group.lower() not in (detail.get("resource_group") or "").lower():
                    continue
                if not assessment_dict_matches_subscription(detail, subscription_id):
                    continue

                doc = {
                    "resource_name": detail.get("resource_name", ""),
                    "resource_type": detail.get("resource_type", ""),
                    "resource_group": detail.get("resource_group", ""),
                    "location": details_data.get("location", ""),
                    "overall_score": float(detail.get("overall_score") or 0),
                    "results": normalized,
                    # 추적용 메타데이터 (Terraform 저장 시 사용, 렌더링엔 미포함)
                    "_db_report_id": row.get("report_id"),
                    "_db_file_id": file_id,
                }
                all_results.append(doc)

            return all_results
        except Exception as e:
            print(f"Failed to fetch assessments from DB: {e}")
            return []

    # ------------------------------------------------------------------
    # 권고 사항 추출
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_recommendations(documents: list[dict], resource_names: Optional[list[str]] = None) -> list[dict]:
        """
        Assessment 문서에서 fail/warning 항목의 권고 사항을 추출합니다.
        pass 항목도 현재 구성 유지 컨텍스트로 포함합니다.
        MODIFIED: resource_names가 제공될 경우 해당 리소스 이름인 경우만 추출함.
        """
        recommendations = []
        for doc in documents:
            # MODIFIED: 대소문자/공백 무시 비교로 매칭 정확도 향상
            doc_name = (doc.get("resource_name") or "").strip().lower()
            if resource_names:
                target_names = [n.strip().lower() for n in resource_names]
                if doc_name not in target_names:
                    continue

            # DB 데이터(results/check_results) 지원, results_json fallback 포함
            results = doc.get("results") or doc.get("check_results")
            if not results:
                results_json = doc.get("results_json", "[]")
                try:
                    results = json.loads(results_json) if isinstance(results_json, str) else results_json
                except (json.JSONDecodeError, TypeError):
                    results = []

            resource_recs = {
                "resource_name": doc.get("resource_name", "N/A"),
                "resource_type": doc.get("resource_type", "N/A"),
                "resource_group": doc.get("resource_group", "N/A"),
                "location": doc.get("location", "N/A"),
                "overall_score": doc.get("overall_score", 0),
                "items": [],
                "pass_items": [],
            }

            for item in results:
                status = item.get("status", "").lower()
                if status in ("fail", "warning"):
                    resource_recs["items"].append({
                        "question": item.get("question", ""),
                        "status": status,
                        "finding": item.get("finding", ""),
                        "recommendation": item.get("recommendation", ""),
                        "severity": item.get("severity", "medium"),
                        "evidence": item.get("evidence", {}),
                    })
                elif status == "pass":
                    resource_recs["pass_items"].append({
                        "question": item.get("question", ""),
                        "finding": item.get("finding", ""),
                        "evidence": item.get("evidence", {}),
                    })

            if resource_recs["items"]:
                recommendations.append(resource_recs)

        return recommendations

    def _format_recommendations_for_llm(self, recommendations: list[dict]) -> str:
        """권고 사항을 LLM 프롬프트용 텍스트로 변환"""
        if not recommendations:
            return "(개선이 필요한 항목이 없습니다)"

        sections = []
        for rec in recommendations:
            lines = [
                f"### {rec['resource_name']} ({rec['resource_type']})",
                f"- 리소스 그룹: {rec['resource_group']}",
                f"- 위치: {rec['location']}",
                f"- 현재 점수: {rec['overall_score']}%",
                "",
            ]

            # Pass 항목 (현재 정상 구성 - 유지 필요)
            pass_items = rec.get("pass_items", [])
            if pass_items:
                lines.append("**현재 정상 구성 (유지 필요):**")
                for item in pass_items:
                    lines.append(f"  - ✅ {item['question']}")
                    if item.get('finding'):
                        lines.append(f"    현재: {item['finding']}")
                lines.append("")

            # Fail/Warning 항목 (문제 해결 필요)
            lines.append("**해결이 필요한 문제점:**")
            for i, item in enumerate(rec["items"], 1):
                lines.append(f"\n{i}. [{item['status'].upper()}] [{item['severity'].upper()}] {item['question']}")
                lines.append(f"   - 문제점: {item['finding']}")
                lines.append(f"   - 해결방법: {item['recommendation']}")
                if item["evidence"]:
                    lines.append(f"   - 현재 구성: {json.dumps(item['evidence'], ensure_ascii=False)}")

            sections.append("\n".join(lines))

        return "\n\n---\n\n".join(sections)

    # ------------------------------------------------------------------
    # Terraform 코드 생성
    # ------------------------------------------------------------------

    def generate(
        self,
        documents: Optional[list[dict]] = None,
        resource_type: Optional[str] = None,
        resource_group: Optional[str] = None,
        subscription_id: Optional[str] = None,
        resource_names: Optional[list[str]] = None,  # MODIFIED: 선택된 리소스 이름 목록 필터 파라미터 추가
    ) -> dict:
        """
        최신 Assessment의 권고 사항을 반영한 Terraform 코드를 생성합니다.

        Args:
            documents: 사용할 Assessment 문서 (None이면 최신 조회)
            resource_type: 리소스 타입 필터
            resource_group: 리소스 그룹 필터
            subscription_id: DB 스냅샷을 이 구독 범위로 제한
            resource_names: MODIFIED: 선택된 리소스 이름 목록

        Returns:
            {
                "resources_count": int,
                "recommendations_count": int,
                "terraform_code": str,
                "files": dict[str, str]  # 파일명 → 코드 매핑
            }
        """
        if documents is None:
            documents = self.get_latest_assessments(
                resource_type=resource_type,
                resource_group=resource_group,
                subscription_id=subscription_id,
            )

        # 소스 추적: 사용된 report_id / resource_name 수집
        # - 우선순위: _db_report_id (내부 메타) -> report_id (DB row) -> _report_id (보조 메타)
        source_report_ids_set: set[int] = set()
        for d in documents:
            for key in ("_db_report_id", "report_id", "_report_id"):
                v = d.get(key)
                if v is None:
                    continue
                try:
                    source_report_ids_set.add(int(v))
                    break
                except (TypeError, ValueError):
                    continue
        source_report_ids: list[int] = sorted(source_report_ids_set)
        source_resource_names: list[str] = [
            d["resource_name"] for d in documents if d.get("resource_name")
        ]

        # MODIFIED: 선택된 리소스 목록이 있을 경우 해당 리소스만 추출하도록 인자 전달
        recommendations = self._extract_recommendations(documents, resource_names=resource_names)

        total_items = sum(len(r["items"]) for r in recommendations)
        if total_items == 0:
            # MODIFIED: 리소스가 선택되었으나 항목이 없는 경우 메시지 구체화
            empty_msg = "선택하신 리소스에 대해 개선이 필요한 항목이 없습니다." if resource_names else "# 개선이 필요한 항목이 없습니다."
            return {
                "resources_count": len(documents),
                "recommendations_count": 0,
                "terraform_code": empty_msg,
                "files": {},
                "readme": "",
                "source_report_ids": source_report_ids,
                "source_resource_names": source_resource_names,
            }

        context = self._format_recommendations_for_llm(recommendations)

        # MODIFIED: 프롬프트에 '사용자가 선택한 리소스'임을 명시하여 LLM 정확도 향상
        selection_info = f"(사용자가 선택한 리소스: {', '.join(resource_names)})" if resource_names else ""
        user_prompt = (
            f"## 체크리스트 평가에서 발견된 문제점 {selection_info} ({len(recommendations)}개 리소스, {total_items}개 항목)\n\n"
            f"{context}\n\n"
            f"---\n\n"
            f"## 요청사항\n\n"
            f"위 체크리스트 평가에서 FAIL/WARNING으로 지적된 각 문제점을 **해결**하는 Terraform 코드를 생성해주세요.\n\n"
            f"**필수 요구사항:**\n"
            f"1. 각 FAIL/WARNING 항목의 '문제점'에 기술된 내용을 직접 해결하는 리소스 구성을 작성하세요.\n"
            f"2. '해결방법'에 제시된 방안을 Terraform 코드로 구현하세요.\n"
            f"3. '현재 구성' 데이터를 참고하여 기존 설정은 유지하면서 문제 부분만 수정/추가하세요.\n"
            f"4. 정상(PASS) 항목의 현재 구성은 변경하지 마세요.\n"
            f"5. API 스키마에 따라 provider_tf, variables_tf, main_tf, outputs_tf 네 필드에 "
            f"각각 provider.tf, variables.tf, main.tf, outputs.tf 파일 내용을 담으세요."
        )

        raw = responses_text(
            self.deployment_name,
            self.SYSTEM_PROMPT,
            user_prompt,
            {"type": "json_schema", "json_schema": TERRAFORM_RESPONSE_JSON_SCHEMA},
        ).strip()
        files = self._terraform_files_from_json_response(raw)
        terraform_code = (
            self._concat_files_with_markers(files)
            if files
            else raw
        )

        # README.md 생성
        readme_content = self.generate_readme(
            files=files,
            recommendations=recommendations,
            resources_count=len(recommendations),
            recommendations_count=total_items,
            terraform_marked=terraform_code if files else None,
        )

        return {
            "resources_count": len(recommendations),
            "recommendations_count": total_items,
            "terraform_code": terraform_code,
            "files": files,
            "readme": readme_content,
            "source_report_ids": source_report_ids,
            "source_resource_names": source_resource_names,
        }

    @staticmethod
    def _terraform_files_from_json_response(raw: str) -> dict[str, str]:
        """structured output JSON → { provider.tf: hcl, ... }."""
        files: dict[str, str] = {}
        if not raw:
            return files
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return TerraformGenerator._parse_terraform_files(raw)

        if not isinstance(data, dict):
            return files
        for json_key, filename in _TF_JSON_FIELD_TO_FILENAME.items():
            v = data.get(json_key)
            if isinstance(v, str):
                files[filename] = v
        return files

    @staticmethod
    def _concat_files_with_markers(files: dict[str, str]) -> str:
        """파일 dict → 기존과 동일한 `# === 파일명.tf ===` 구획 문자열 (한 덩어리)."""
        if not files:
            return ""
        sections: list[str] = []
        seen: set[str] = set()
        for name in _TERRAFORM_MARKER_FILE_ORDER:
            if name not in files:
                continue
            seen.add(name)
            body = (files.get(name) or "").rstrip()
            sections.append(f"# === {name} ===\n{body}")
        for name in sorted(k for k in files if k not in seen):
            body = (files.get(name) or "").rstrip()
            sections.append(f"# === {name} ===\n{body}")
        return "\n\n".join(sections).strip()

    @staticmethod
    def _parse_terraform_files(code: str) -> dict[str, str]:
        """LLM 출력에서 `# === 파일명.tf ===` 구분자로 파일을 분리합니다."""
        files: dict[str, str] = {}
        current_file = None
        current_lines: list[str] = []

        for line in code.split("\n"):
            stripped = line.strip()
            # `# === provider.tf ===` 또는 ```hcl / ``` 처리
            if stripped.startswith("# ===") and stripped.endswith("==="):
                if current_file and current_lines:
                    files[current_file] = "\n".join(current_lines).strip()
                filename = stripped.replace("# ===", "").replace("===", "").strip()
                current_file = filename
                current_lines = []
            elif stripped in ("```hcl", "```terraform", "```tf", "```"):
                continue  # 코드 펜스 제거
            else:
                current_lines.append(line)

        if current_file and current_lines:
            files[current_file] = "\n".join(current_lines).strip()

        return files

    README_SYSTEM_PROMPT = """당신은 Terraform 코드 문서화 전문가입니다.
주어진 Terraform 코드와 평가 권고 사항을 바탕으로 README.md 문서를 작성합니다.

README.md 작성 원칙:
1. 한국어로 작성합니다.
2. Markdown 형식으로 작성합니다.
3. 다음 섹션을 반드시 포함합니다:

   ## 개요
   - 이 Terraform 코드의 전체 목적과 배경을 요약합니다.

   ## 대상 리소스
   - 관리되는 Azure 리소스 목록과 각 리소스의 역할을 설명합니다.

   ## 적용된 권고 사항
   - 각 리소스별로 어떤 개선이 반영되었는지 표(| 리소스 | 심각도 | 권고 항목 | 적용 내용 |) 형태로 정리합니다.

   ## Terraform 코드 설명
   - **이 섹션이 가장 중요합니다.**
   - 각 .tf 파일별로 정의된 리소스 블록, data 블록, module 블록 등을 하나씩 설명합니다.
   - 각 resource 블록에 대해:
     - 리소스의 목적과 역할
     - 주요 속성(argument)들이 왜 그런 값으로 설정되었는지
     - `# RECOMMENDATION:` 주석이 달린 부분은 어떤 권고 사항을 반영한 것인지 구체적으로 설명
     - 다른 리소스와의 의존 관계 (depends_on, 참조 등)
   - 파일 순서: provider.tf → variables.tf → main.tf → outputs.tf 순으로 설명합니다.

   ## 변수 설명
   - variables.tf에 정의된 변수들을 표(| 변수명 | 타입 | 기본값 | 설명 |) 형태로 정리합니다.

   ## 사전 요구 사항
   - Terraform 버전, provider 버전, 필요한 Azure 권한 등을 명시합니다.

   ## 사용 방법
   - terraform init / plan / apply 명령어를 단계별로 안내합니다.
   - 변수 파일(terraform.tfvars) 작성 예시를 포함합니다.

   ## 주의 사항
   - 적용 시 유의할 점, 기존 리소스에 미치는 영향, 롤백 방법 등을 안내합니다.

4. 코드 설명 섹션에서는 필요할 경우 핵심 코드 스니펫을 인용하여 설명할 수 있습니다.
5. 간결하면서도 실무자가 코드를 이해하고 적용하기에 충분한 수준으로 작성합니다."""

    def generate_readme(
        self,
        files: dict[str, str],
        recommendations: list[dict],
        resources_count: int,
        recommendations_count: int,
        terraform_marked: Optional[str] = None,
    ) -> str:
        """
        생성된 Terraform 코드에 대한 README.md 내용을 LLM으로 작성합니다.

        Args:
            files: 파일명 → 코드 매핑
            recommendations: 권고 사항 목록
            resources_count: 대상 리소스 수
            recommendations_count: 적용된 권고 수
            terraform_marked: `# === 파일명.tf ===` 로 concat 한 전체 문자열(있으면 우선 사용)

        Returns:
            README.md 내용 문자열
        """
        bundle = (terraform_marked or "").strip()
        if bundle:
            code_context = (
                "### 전체 Terraform (`# === 파일명.tf ===` 구획)\n\n"
                f"```hcl\n{bundle}\n```\n"
            )
        else:
            code_context = ""
            ordered = [n for n in _TERRAFORM_MARKER_FILE_ORDER if n in files]
            extra = sorted(k for k in files if k not in set(_TERRAFORM_MARKER_FILE_ORDER))
            for filename in ordered + extra:
                code = files[filename]
                code_context += f"\n### {filename}\n```hcl\n{code}\n```\n"

        rec_context = self._format_recommendations_for_llm(recommendations)

        user_prompt = (
            f"## Terraform 코드 README.md 작성 요청\n\n"
            f"**대상 리소스 수:** {resources_count}\n"
            f"**적용된 권고 사항 수:** {recommendations_count}\n\n"
            f"### 적용된 권고 사항\n{rec_context}\n\n"
            f"### 생성된 Terraform 코드\n{code_context}\n\n"
            f"위 정보를 바탕으로 README.md 문서를 작성해주세요."
        )

        return responses_text(
            self.deployment_name,
            self.README_SYSTEM_PROMPT,
            user_prompt,
        )

    def save_terraform_files(
        self,
        files: dict[str, str],
        output_dir: Optional[str] = None,
        subscription_id: Optional[str] = None,
    ) -> str:
        """
        Terraform 파일을 디스크에 저장합니다.

        Args:
            files: 파일명 → 코드 매핑
            output_dir: 출력 디렉토리 (지정 시 subscription/timestamp 무시)
            subscription_id: 지정 시 terraform_output/{sub}/{timestamp}/

        Returns:
            저장된 디렉토리 경로
        """
        if output_dir:
            out = Path(output_dir)
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            base = Path(__file__).parent.parent / "terraform_output"
            if subscription_id:
                from .storage_paths import subscription_scope_key

                sub = subscription_scope_key(subscription_id)
                out = base / sub / timestamp
            else:
                out = base / timestamp

        out.mkdir(parents=True, exist_ok=True)

        for filename, content in files.items():
            filepath = out / filename
            filepath.write_text(content, encoding="utf-8")

        return str(out)
