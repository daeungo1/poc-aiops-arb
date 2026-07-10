"""
AIOps Resource Assessment Agent - CopilotKit Interface

Azure Architecture Review Board 기반 리소스 진단 챗봇.
AG-UI 프로토콜 + CopilotKit 프론트엔드를 통해 대화형/대시보드 UX를 제공합니다.

사용법:
    # AG-UI 백엔드 서버 실행 (기본값, http://localhost:5100)
    python main.py

    # 포트 지정
    python main.py --port 9090

    # 프론트엔드는 docker(nginx) 컨테이너로 빌드·서빙 (docker/Dockerfile.frontend)
    docker build -f docker/Dockerfile.frontend -t aiops-fe .

환경 변수:
    AZURE_AI_ENDPOINT: Azure AI Foundry root 엔드포인트
    AZURE_AI_PROJECT_NAME: Azure AI Foundry 프로젝트 이름
    AZURE_AI_MODEL_DEPLOYMENT_NAME: 모델 배포 이름 (기본값: gpt-5-mini)
    DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD: PostgreSQL 연결
    웹: Entra SSO. CLI: main.py --subscription-id 시 Azure CLI 등 DefaultAzureCredential

대화 예시:
    - "현재 Azure 구독 정보를 알려줘"
    - "리소스 목록을 보여줘"
    - "전체 리소스 평가를 실행해줘"
    - "rg-myapp 리소스 그룹만 평가해줘"
    - "최근 평가 결과를 보여줘"
    - "backup 관련 평가 결과를 검색해줘"
    - "실패 항목에 대한 Terraform 코드를 생성해줘"
"""

import os
import sys
import argparse

from dotenv import load_dotenv


def main():
    """메인 실행 함수: CLI 모드 또는 AG-UI 서버 모드 선택 실행"""
    load_dotenv(override=True)

    parser = argparse.ArgumentParser(
        description="AIOps Resource Assessment - CLI & AG-UI Server"
    )
    # 공통/서버 옵션
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=5100,
        help="AG-UI 서버 포트 (기본값: 5100)",
    )

    # CLI 진단 옵션
    parser.add_argument(
        "--subscription-id", "-s",
        type=str,
        help="평가할 Azure 구독 ID (CLI 모드 활성화)",
    )
    parser.add_argument(
        "--resource-group", "-g",
        type=str,
        help="특정 리소스 그룹만 평가 (CLI 모드용)",
    )
    parser.add_argument(
        "--resource-type", "-t",
        type=str,
        help="특정 리소스 타입만 평가 (CLI 모드용)",
    )
    parser.add_argument(
        "--output-format", "-o",
        type=str,
        default="all",
        choices=["markdown", "json", "html", "all"],
        help="출력 형식 (기본값: all)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="./results",
        help="결과 저장 디렉토리 (기본값: ./results)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="리소스 목록만 확인하고 평가는 수행하지 않음",
    )

    args = parser.parse_args()

    # 1. CLI 모드 실행 (subscription_id가 제공된 경우)
    if args.subscription_id:
        return run_cli_assessment(args)

    # 2. AG-UI 서버 모드 실행 (기본값)
    return run_agui_server(args)


def run_cli_assessment(args):
    """터미널에서 직접 리소스 진단 수행 (자격 증명: Azure CLI 등 DefaultAzureCredential)"""
    print("=" * 60)
    print("🔬 AIOps Resource Assessment - CLI Mode")
    print("=" * 60)

    from azure.identity import DefaultAzureCredential

    from agent.azure_credential import pop_cli_credential, push_cli_credential

    _cli_cred_tok = push_cli_credential(DefaultAzureCredential())

    try:
        try:
            from agent.azure_resource_reader import AzureResourceReader
            from agent.checklist_loader import ChecklistLoader, get_configured_checklist_loader
            from agent.assessment_engine import AssessmentEngine
            from agent.ai_foundry_config import get_ai_endpoint_from_env
            from agent.report_generator import ReportGenerator
        except ImportError as e:
            print(f"❌ 모듈 로드 오류: {e}")
            print("   필요한 패키지가 설치되어 있는지 확인하세요: uv sync")
            return 1

        # 모델 정보 확인 (환경 변수)
        try:
            ai_endpoint = get_ai_endpoint_from_env()
        except ValueError as e:
            print(f"❌ {e}")
            return 1
        deployment = os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-5-mini")

        print(f"📡 Azure 구독 연결: {args.subscription_id}")
        reader = AzureResourceReader(subscription_ids=[args.subscription_id])

        # 리소스 조회
        if args.resource_group:
            print(f"📂 리소스 그룹 필터: {args.resource_group}")
            resources = reader.get_resources_by_resource_group(args.resource_group)
        elif args.resource_type:
            print(f"🔍 리소스 타입 필터: {args.resource_type}")
            resources = reader.get_resources_by_type(args.resource_type)
        else:
            print("🌐 전체 구독 리소스 조회 중...")
            resources = reader.get_all_resources()

        if not resources:
            print("⚠️ 평가할 리소스를 찾지 못했습니다.")
            return 0

        print(f"✅ 총 {len(resources)}개의 리소스를 발견했습니다.")

        if args.dry_run:
            print("\n--- Dry Run: 리소스 목록 ---")
            for r in resources:
                print(f"- [{r.get('type')}] {r.get('name')}")
            return 0

        # 체크리스트 로드
        print("📚 체크리스트 로딩 중...")
        import os as _os
        _project_dir = _os.path.dirname(_os.path.abspath(__file__))
        loader = get_configured_checklist_loader(_project_dir)
        print(f"   체크리스트 {len(loader.checklists)}개 로드 완료")

        # 평가 엔진 실행
        print(f"🧠 AI 평가 시작 (Model: {deployment})...")
        engine = AssessmentEngine(
            ai_endpoint=ai_endpoint,
            deployment_name=deployment,
            checklist_loader=loader
        )
        assessments = engine.assess_resources(resources)

        # 리포트 생성
        print(f"📊 리포트 생성 중 (형식: {args.output_format})...")
        report_gen = ReportGenerator(
            output_dir=args.output_dir,
            subscription_id_hint=args.subscription_id,
        )
        paths = []
        if args.output_format in ["markdown", "all"]:
            paths.append(report_gen.generate_markdown_report(assessments))
        if args.output_format in ["json", "all"]:
            paths.append(report_gen.generate_json_report(assessments))
        if args.output_format in ["html", "all"]:
            paths.append(report_gen.generate_html_report(assessments))

        print("\n" + "✨" * 30)
        print("🎉 평가 완료!")
        for p in paths:
            if p:
                print(f"   📄 {p}")
        print("✨" * 30)

        return 0
    finally:
        pop_cli_credential(_cli_cred_tok)


def run_agui_server(args):
    """AG-UI 백엔드 서버 실행"""
    # 환경 변수 확인
    from agent.ai_foundry_config import (
        get_ai_endpoint_from_env,
        get_ai_project_name_from_env,
    )

    try:
        ai_endpoint = get_ai_endpoint_from_env()
        ai_project_name = get_ai_project_name_from_env()
    except ValueError as e:
        print("❌ 환경 변수가 설정되지 않았습니다.")
        print(f"   {e}")
        print("   웹에서는 Entra SSO 로그인이 필요합니다.")
        return 1

    # 의존성 확인
    try:
        from agent_framework.ag_ui import add_agent_framework_fastapi_endpoint  # noqa: F401
        import uvicorn
    except ImportError as e:
        print(f"❌ 필요한 패키지가 설치되어 있지 않습니다: {e}")
        print("   uv pip install --extra chat .")
        return 1

    # 환경 변수에 PORT 설정 (agui_server.py에서 사용)
    os.environ["PORT"] = str(args.port)

    print("=" * 60)
    print("🤖 AIOps Resource Assessment - AG-UI Server")
    print("   Azure Architecture Review Board 기반 대화형 리소스 진단")
    print("=" * 60)
    print()
    print("  사용 가능한 기능:")
    print("    📌 Azure 구독/리소스 조회")
    print("    📝 체크리스트 확인")
    print("    🔬 리소스 평가 실행 (LLM 기반)")
    print("    📊 평가 결과 리포트 생성")
    print("    🗄️  DB(PostgreSQL) 기반 평가 결과 조회/검색")
    print("    🏗️  Terraform 코드 생성 (실패 항목 기반)")
    print()
    print(f"  환경:")
    print(f"    - AI Endpoint: {ai_endpoint}")
    print(f"    - AI Project: {ai_project_name}")
    print(f"    - Model: {os.getenv('AZURE_AI_MODEL_DEPLOYMENT_NAME', 'gpt-5-mini')}")
    db_host = os.getenv("DB_HOST")
    if db_host:
        print(f"    - DB: {db_host}:{os.getenv('DB_PORT', '5432')}/{os.getenv('DB_NAME', 'aiops')}")
    else:
        print(f"    - DB: (미설정 - DB_HOST 환경 변수 필요)")
    print()
    print(f"  🌐 AG-UI Backend: http://localhost:{args.port}")
    print(f"  📊 API Docs: http://localhost:{args.port}/docs")
    print(f"  💻 Frontend: docker(nginx) 컨테이너로 서빙 — docker/Dockerfile.frontend")
    print(f"  📥 Downloads: http://localhost:{args.port}/api/downloads")
    print()

    import uvicorn
    uvicorn.run(
        "agui_server:get_app",
        host="0.0.0.0",
        port=args.port,
        factory=True,
    )
    return 0


if __name__ == "__main__":
    exit(main())
