# AIOps Assessment Chatbot (AG-UI + CopilotKit)

Microsoft Agent Framework + AG-UI 프로토콜 + CopilotKit 기반의 대화형 AIOps Assessment 챗봇입니다.

## 기능

| 도구 | 설명 |
|------|------|
| `get_subscription_info` | Azure CLI 구독 정보 조회 |
| `list_azure_resources` | Azure 리소스 목록 조회 |
| `list_checklists` | 체크리스트 요약 조회 |
| `get_checklist_detail` | 체크리스트 세부 항목 조회 |
| `run_assessment` | 전체 평가 파이프라인 실행 |
| `get_latest_assessments` | AI Search에서 최신 Assessment 결과 조회 |
| `search_assessments` | 키워드 기반 Assessment 전문 검색 |
| `get_resource_detail` | 특정 리소스의 상세 Assessment 확인 |
| `generate_terraform_code` | fail/warning 권고사항 기반 Terraform 코드 생성 |

## 사전 요구사항

```bash
# 프로젝트 루트에서 chat 의존성 포함 설치
uv sync --extra chat

# 프론트엔드 설치
cd frontend && npm install

# Azure CLI 인증
az login
```

## 실행

```bash
# 1. AG-UI 백엔드 서버 실행 (터미널 1)
cd aiops_resource_assessment
python main.py

# 2. 프론트엔드 실행 (터미널 2)
cd aiops_resource_assessment/frontend
npm run dev
```

- AG-UI 서버: `http://localhost:5100`
- API 문서: `http://localhost:5100/docs`
- 프론트엔드: `http://localhost:5173`

## 환경변수 (.env)

```env
AZURE_AI_ENDPOINT=https://<foundry>.services.ai.azure.com
AZURE_AI_PROJECT_NAME=<project>
AZURE_AI_MODEL_DEPLOYMENT_NAME=gpt-5-mini
AZURE_OPENAI_RESPONSES_DEPLOYMENT_NAME=gpt-5-mini

# PostgreSQL (체크리스트/평가결과/Terraform 저장)
DB_HOST=localhost
DB_PORT=5432
DB_NAME=aiops
DB_USER=aiops
DB_PASSWORD=<password>
```

## 사용 예시

```
사용자: 최신 Assessment 결과 보여줘
사용자: 점수가 60% 미만인 리소스 알려줘
사용자: cosmosdb 관련 검색 결과
사용자: jinspark-mysql 리소스 상세 조회
사용자: fail 항목에 대한 Terraform 코드 생성해줘
```
