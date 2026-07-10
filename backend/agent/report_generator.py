"""
Report Generator Module
Assessment 결과를 다양한 형식의 리포트로 생성합니다.
"""

import json
from typing import Optional
from datetime import datetime
from pathlib import Path
import os
from .assessment_engine import ResourceAssessment, ComplianceStatus


class ReportGenerator:
    """
    Assessment 결과를 리포트로 생성하는 클래스
    """

    def __init__(self, output_dir: Optional[str] = None, subscription_id_hint: Optional[str] = None):
        """
        Args:
            output_dir: 리포트 출력 디렉토리
            subscription_id_hint: 로컬 경로용 구독 ID (미사용, 하위 호환성 유지)
        """
        if output_dir:
            self.output_dir = Path(output_dir)
        else:
            self.output_dir = Path(__file__).parent.parent / "results"

        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _finalize_report_text(
        self,
        assessments: list[ResourceAssessment],
        filename: str,
        text: str,
    ) -> str:
        """로컬 파일에 저장."""
        filepath = self.output_dir / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(text, encoding="utf-8")
        return str(filepath)
    
    def generate_markdown_report(
        self, 
        assessments: list[ResourceAssessment],
        title: str = "Azure Architecture Review Board Assessment Report",
        output_filename: Optional[str] = None
    ) -> str:
        """
        Markdown 형식의 리포트를 생성합니다.
        
        Args:
            assessments: 평가 결과 목록
            title: 리포트 제목
            output_filename: 출력 파일명 (None이면 타임스탬프 기반)
            
        Returns:
            로컬 절대 경로
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = output_filename or f"assessment_report_{timestamp}.md"

        lines = [
            f"# {title}",
            "",
            f"**생성일시:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "---",
            "",
        ]
        
        # Executive Summary
        lines.extend(self._generate_executive_summary(assessments))
        
        # 리소스별 상세 결과
        lines.append("## 리소스별 상세 평가 결과")
        lines.append("")
        
        for assessment in assessments:
            lines.extend(self._generate_resource_section(assessment))
        
        # 권장사항 요약
        lines.extend(self._generate_recommendations_summary(assessments))
        
        content = "\n".join(lines)
        return self._finalize_report_text(assessments, filename, content)
    
    def generate_json_report(
        self,
        assessments: list[ResourceAssessment],
        output_filename: Optional[str] = None
    ) -> str:
        """
        JSON 형식의 리포트를 생성합니다.
        
        Args:
            assessments: 평가 결과 목록
            output_filename: 출력 파일명
            
        Returns:
            로컬 절대 경로
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = output_filename or f"assessment_report_{timestamp}.json"

        report_data = {
            "report_metadata": {
                "generated_at": datetime.now().isoformat(),
                "total_resources": len(assessments),
                "report_version": "1.0"
            },
            "summary": self._calculate_summary_stats(assessments),
            "assessments": [a.to_dict() for a in assessments]
        }
        
        content = json.dumps(report_data, indent=2, ensure_ascii=False)
        return self._finalize_report_text(assessments, filename, content)
    
    def generate_html_report(
        self,
        assessments: list[ResourceAssessment],
        title: str = "Azure Architecture Review Board Assessment Report",
        output_filename: Optional[str] = None
    ) -> str:
        """
        HTML 형식의 리포트를 생성합니다.
        
        Args:
            assessments: 평가 결과 목록
            title: 리포트 제목
            output_filename: 출력 파일명
            
        Returns:
            로컬 절대 경로
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = output_filename or f"assessment_report_{timestamp}.html"

        summary = self._calculate_summary_stats(assessments)
        
        html_content = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            line-height: 1.6;
            color: #333;
            background: #f5f5f5;
            padding: 20px;
        }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
        .header {{
            background: linear-gradient(135deg, #0078d4, #00bcf2);
            color: white;
            padding: 30px;
            border-radius: 10px;
            margin-bottom: 20px;
        }}
        .header h1 {{ font-size: 28px; margin-bottom: 10px; }}
        .header .meta {{ opacity: 0.9; font-size: 14px; }}
        .summary-cards {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-bottom: 20px;
        }}
        .card {{
            background: white;
            padding: 20px;
            border-radius: 10px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }}
        .card h3 {{ font-size: 14px; color: #666; margin-bottom: 5px; }}
        .card .value {{ font-size: 32px; font-weight: bold; }}
        .card.pass .value {{ color: #107c10; }}
        .card.fail .value {{ color: #d13438; }}
        .card.warning .value {{ color: #ffb900; }}
        .card.score .value {{ color: #0078d4; }}
        .resource-section {{
            background: white;
            border-radius: 10px;
            margin-bottom: 20px;
            overflow: hidden;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }}
        .resource-header {{
            background: #f8f8f8;
            padding: 20px;
            border-bottom: 1px solid #eee;
        }}
        .resource-header h2 {{ font-size: 18px; margin-bottom: 5px; }}
        .resource-header .type {{ color: #666; font-size: 14px; }}
        .resource-score {{
            display: inline-block;
            padding: 5px 15px;
            border-radius: 20px;
            font-weight: bold;
            float: right;
        }}
        .resource-score.high {{ background: #dff6dd; color: #107c10; }}
        .resource-score.medium {{ background: #fff4ce; color: #797673; }}
        .resource-score.low {{ background: #fde7e9; color: #d13438; }}
        .check-results {{ padding: 20px; }}
        .check-item {{
            padding: 15px;
            border-bottom: 1px solid #eee;
        }}
        .check-item:last-child {{ border-bottom: none; }}
        .check-status {{
            display: inline-block;
            padding: 3px 10px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: bold;
            margin-right: 10px;
        }}
        .check-status.pass {{ background: #dff6dd; color: #107c10; }}
        .check-status.fail {{ background: #fde7e9; color: #d13438; }}
        .check-status.warning {{ background: #fff4ce; color: #797673; }}
        .check-status.manual {{ background: #f3f2f1; color: #605e5c; }}
        .check-question {{ font-weight: 500; margin-bottom: 10px; }}
        .check-finding {{ color: #666; font-size: 14px; margin-bottom: 5px; }}
        .check-recommendation {{
            background: #f8f8f8;
            padding: 10px;
            border-radius: 5px;
            font-size: 14px;
            margin-top: 10px;
        }}
        .severity {{
            font-size: 11px;
            padding: 2px 8px;
            border-radius: 3px;
            margin-left: 10px;
        }}
        .severity.critical {{ background: #d13438; color: white; }}
        .severity.high {{ background: #ff8c00; color: white; }}
        .severity.medium {{ background: #ffb900; color: black; }}
        .severity.low {{ background: #107c10; color: white; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>{title}</h1>
            <div class="meta">
                생성일시: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | 
                평가 리소스: {len(assessments)}개
            </div>
        </div>

        <div class="summary-cards">
            <div class="card score">
                <h3>평균 점수</h3>
                <div class="value">{summary['average_score']:.1f}%</div>
            </div>
            <div class="card pass">
                <h3>통과 항목</h3>
                <div class="value">{summary['total_passed']}</div>
            </div>
            <div class="card fail">
                <h3>실패 항목</h3>
                <div class="value">{summary['total_failed']}</div>
            </div>
            <div class="card warning">
                <h3>경고 항목</h3>
                <div class="value">{summary['total_warnings']}</div>
            </div>
        </div>

        {self._generate_html_resource_sections(assessments)}
    </div>
</body>
</html>"""
        
        return self._finalize_report_text(assessments, filename, html_content)

    # ── 파일 저장 없이 콘텐츠 문자열만 반환하는 메서드 ────────────────────

    def build_markdown_content(
        self,
        assessments: list[ResourceAssessment],
        title: str = "Azure Architecture Review Board Assessment Report",
    ) -> str:
        """Markdown 리포트 문자열 반환 (파일 저장/업로드 없음)."""
        lines = [
            f"# {title}",
            "",
            f"**생성일시:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "---",
            "",
        ]
        lines.extend(self._generate_executive_summary(assessments))
        lines.append("## 리소스별 상세 평가 결과")
        lines.append("")
        for assessment in assessments:
            lines.extend(self._generate_resource_section(assessment))
        lines.extend(self._generate_recommendations_summary(assessments))
        return "\n".join(lines)

    def build_html_content(
        self,
        assessments: list[ResourceAssessment],
        title: str = "Azure Architecture Review Board Assessment Report",
    ) -> str:
        """HTML 리포트 문자열 반환 (파일 저장/업로드 없음)."""
        summary = self._calculate_summary_stats(assessments)
        # generate_html_report 내부와 동일한 HTML 구성
        # (summary 변수 사용을 위해 generate_html_report에서 html_content 생성 로직 재사용)
        import io, contextlib
        buf = io.StringIO()
        # _finalize_report_text를 우회하기 위해 임시로 패치
        _orig = self._finalize_report_text
        result_holder: list[str] = []

        def _capture(assessments, filename, text):
            result_holder.append(text)
            return text

        self._finalize_report_text = _capture  # type: ignore[method-assign]
        try:
            self.generate_html_report(assessments, title=title)
        finally:
            self._finalize_report_text = _orig
        return result_holder[0] if result_holder else ""

    def _generate_executive_summary(
        self, 
        assessments: list[ResourceAssessment]
    ) -> list[str]:
        """Executive Summary 섹션 생성"""
        summary = self._calculate_summary_stats(assessments)
        
        lines = [
            "## Executive Summary",
            "",
            f"### 평가 개요",
            "",
            f"| 항목 | 값 |",
            f"|------|-----|",
            f"| 평가 리소스 수 | {len(assessments)} |",
            f"| 전체 점검 항목 | {summary['total_checks']} |",
            f"| 평균 준수 점수 | {summary['average_score']:.1f}% |",
            "",
            f"### 점검 결과 요약",
            "",
            f"| 상태 | 항목 수 | 비율 |",
            f"|------|---------|------|",
            f"| ✅ 통과 | {summary['total_passed']} | {summary['pass_rate']:.1f}% |",
            f"| ❌ 실패 | {summary['total_failed']} | {summary['fail_rate']:.1f}% |",
            f"| ⚠️ 경고 | {summary['total_warnings']} | {summary['warning_rate']:.1f}% |",
            f"| 📋 수동검토 | {summary['total_manual']} | {summary['manual_rate']:.1f}% |",
            "",
            "### 리소스 타입별 현황",
            "",
            "| 리소스 타입 | 개수 | 평균 점수 |",
            "|-------------|------|-----------|",
        ]
        
        # 리소스 타입별 통계
        type_stats = {}
        for a in assessments:
            if a.resource_type not in type_stats:
                type_stats[a.resource_type] = {"count": 0, "total_score": 0}
            type_stats[a.resource_type]["count"] += 1
            type_stats[a.resource_type]["total_score"] += a.overall_score
        
        for rtype, stats in type_stats.items():
            avg_score = stats["total_score"] / stats["count"]
            lines.append(f"| {rtype} | {stats['count']} | {avg_score:.1f}% |")
        
        lines.extend(["", "---", ""])
        
        return lines
    
    def _generate_resource_section(
        self, 
        assessment: ResourceAssessment
    ) -> list[str]:
        """리소스별 상세 섹션 생성"""
        score_emoji = "🟢" if assessment.overall_score >= 80 else "🟡" if assessment.overall_score >= 60 else "🔴"
        
        lines = [
            f"### {score_emoji} {assessment.resource_name}",
            "",
            f"- **타입:** {assessment.resource_type}",
            f"- **리소스 그룹:** {assessment.resource_group}",
            f"- **위치:** {assessment.location}",
            f"- **준수 점수:** {assessment.overall_score}%",
            f"- **평가 시간:** {assessment.assessment_time}",
            "",
            "#### 점검 결과",
            "",
            "| 상태 | 항목 | 발견사항 | 심각도 |",
            "|------|------|----------|--------|",
        ]
        
        status_icons = {
            ComplianceStatus.PASS: "✅",
            ComplianceStatus.FAIL: "❌",
            ComplianceStatus.WARNING: "⚠️",
            ComplianceStatus.NOT_APPLICABLE: "➖",
            ComplianceStatus.MANUAL_REVIEW: "📋"
        }
        
        for result in assessment.results:
            icon = status_icons.get(result.status, "❓")
            # 테이블 셀 내 줄바꿈 처리
            finding = result.finding.replace("\n", " ").replace("|", "\\|")[:100]
            if len(result.finding) > 100:
                finding += "..."
            
            lines.append(
                f"| {icon} | {result.check_question[:50]}... | {finding} | {result.severity} |"
            )
        
        # 실패 항목 상세
        failed_results = [r for r in assessment.results if r.status == ComplianceStatus.FAIL]
        if failed_results:
            lines.extend([
                "",
                "#### ❌ 개선 필요 항목",
                ""
            ])
            for i, result in enumerate(failed_results, 1):
                lines.extend([
                    f"**{i}. {result.check_question}**",
                    "",
                    f"- **발견사항:** {result.finding}",
                    f"- **권장사항:** {result.recommendation}",
                    f"- **심각도:** {result.severity}",
                    ""
                ])
        
        lines.extend(["", "---", ""])
        
        return lines
    
    def _generate_recommendations_summary(
        self, 
        assessments: list[ResourceAssessment]
    ) -> list[str]:
        """전체 권장사항 요약 섹션 생성"""
        lines = [
            "## 전체 권장사항 요약",
            "",
        ]
        
        # 심각도별 분류
        critical_items = []
        high_items = []
        medium_items = []
        
        for assessment in assessments:
            for result in assessment.results:
                if result.status == ComplianceStatus.FAIL:
                    item = {
                        "resource": assessment.resource_name,
                        "question": result.check_question,
                        "recommendation": result.recommendation
                    }
                    if result.severity == "critical":
                        critical_items.append(item)
                    elif result.severity == "high":
                        high_items.append(item)
                    else:
                        medium_items.append(item)
        
        if critical_items:
            lines.append("### 🔴 Critical (즉시 조치 필요)")
            lines.append("")
            for item in critical_items:
                lines.append(f"- **{item['resource']}**: {item['question']}")
                lines.append(f"  - 권장: {item['recommendation']}")
            lines.append("")
        
        if high_items:
            lines.append("### 🟠 High (빠른 조치 권장)")
            lines.append("")
            for item in high_items:
                lines.append(f"- **{item['resource']}**: {item['question']}")
                lines.append(f"  - 권장: {item['recommendation']}")
            lines.append("")
        
        if medium_items:
            lines.append("### 🟡 Medium (개선 권장)")
            lines.append("")
            for item in medium_items[:10]:  # 상위 10개만
                lines.append(f"- **{item['resource']}**: {item['question']}")
            if len(medium_items) > 10:
                lines.append(f"- ... 외 {len(medium_items) - 10}개 항목")
            lines.append("")
        
        return lines
    
    def _generate_html_resource_sections(
        self, 
        assessments: list[ResourceAssessment]
    ) -> str:
        """HTML 리소스 섹션 생성"""
        sections = []
        
        for assessment in assessments:
            score_class = "high" if assessment.overall_score >= 80 else "medium" if assessment.overall_score >= 60 else "low"
            
            check_items_html = ""
            for result in assessment.results:
                status_class = result.status.value.replace("_", "-")
                if status_class == "n/a":
                    status_class = "manual"
                
                check_items_html += f"""
                <div class="check-item">
                    <span class="check-status {status_class}">{result.status.value.upper()}</span>
                    <span class="severity {result.severity}">{result.severity.upper()}</span>
                    <div class="check-question">{result.check_question}</div>
                    <div class="check-finding">📋 {result.finding}</div>
                    {f'<div class="check-recommendation">💡 권장: {result.recommendation}</div>' if result.recommendation and result.status == ComplianceStatus.FAIL else ''}
                </div>
                """
            
            sections.append(f"""
            <div class="resource-section">
                <div class="resource-header">
                    <span class="resource-score {score_class}">{assessment.overall_score:.0f}%</span>
                    <h2>{assessment.resource_name}</h2>
                    <div class="type">{assessment.resource_type} | {assessment.resource_group} | {assessment.location}</div>
                </div>
                <div class="check-results">
                    {check_items_html}
                </div>
            </div>
            """)
        
        return "\n".join(sections)
    
    def _calculate_summary_stats(
        self, 
        assessments: list[ResourceAssessment]
    ) -> dict:
        """요약 통계 계산"""
        total_checks = 0
        total_passed = 0
        total_failed = 0
        total_warnings = 0
        total_manual = 0
        total_score = 0
        
        for a in assessments:
            total_checks += a.total_checks
            total_passed += a.passed_checks
            total_failed += a.failed_checks
            total_warnings += a.warning_checks
            total_manual += sum(
                1 for r in a.results 
                if r.status in [ComplianceStatus.MANUAL_REVIEW, ComplianceStatus.NOT_APPLICABLE]
            )
            total_score += a.overall_score
        
        avg_score = total_score / len(assessments) if assessments else 0
        
        return {
            "total_checks": total_checks,
            "total_passed": total_passed,
            "total_failed": total_failed,
            "total_warnings": total_warnings,
            "total_manual": total_manual,
            "average_score": avg_score,
            "pass_rate": (total_passed / total_checks * 100) if total_checks else 0,
            "fail_rate": (total_failed / total_checks * 100) if total_checks else 0,
            "warning_rate": (total_warnings / total_checks * 100) if total_checks else 0,
            "manual_rate": (total_manual / total_checks * 100) if total_checks else 0
        }
