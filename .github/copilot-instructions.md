# AIOps Resource Assessment — Copilot Instructions

이 저장소는 **Azure Architecture Review Board(ARB) 체크리스트**를 기준으로 Azure 리소스를 수집·진단하고,
대화형 챗봇 + 대시보드로 평가·리포트·Terraform 개선코드까지 생성하는 풀스택 애플리케이션입니다.

> 이 문서는 GitHub Copilot / AI 에이전트가 이 저장소에서 작업할 때 참고하는 구조·규칙 요약입니다.

## 아키텍처 한눈에 보기

![배포 아키텍처](architecture.png)

<!-- 편집 소스: architecture.excalidraw (https://excalidraw.com 에 드래그&드롭 또는 Excalidraw VS Code 확장으로 열기) -->

핵심 흐름: **Azure 리소스 수집 → 체크리스트(LLM) 진단 → 리포트(MD/JSON/HTML) → Terraform 개선코드 생성**

## 기술 스택

| 구분 | 내용 |
|------|------|
| 백엔드 | Python 3.11+, FastAPI, uvicorn, Microsoft Agent Framework (AG-UI) |
| LLM | Azure AI Foundry (`AzureOpenAIResponsesClient`, 배포 모델 `gpt-5.2`) |
| 인증(웹) | Microsoft Entra ID OAuth2 SSO (MSAL, UAMI 페더레이션 client_assertion) |
| 저장소 | PostgreSQL 16 (체크리스트·평가결과·Terraform) + 로컬 파일 출력 |
| 프론트엔드 | React 18, Vite 5, TypeScript, Tailwind CSS, 경량 AG-UI 스트림 클라이언트 |
| 인프라 | Terraform (App Service, ACR, AI Foundry, PostgreSQL, VNet, Private Endpoint) |
| 배포 | Docker 컨테이너 이미지 → Azure Container Registry → App Service for Containers |

## 저장소 구조

```
├── backend/                       # Python 백엔드 (uv 프로젝트)
│   ├── agent/                     # 코어 진단·저장소 모듈
│   │   ├── azure_credential.py    # LazyDefaultAzureCredential, 위임/CLI 토큰 스택
│   │   ├── azure_resource_reader.py  # Azure Resource Graph 리소스 조회
│   │   ├── subscription_scope.py  # 구독 정규화·범위 판별
│   │   ├── checklist_loader.py    # YAML/DB 체크리스트 로드
│   │   ├── assessment_engine.py   # LLM 기반 체크리스트 평가 (ComplianceStatus 등)
│   │   ├── report_generator.py    # Markdown/JSON/HTML 리포트 생성
│   │   ├── terraform_generator.py # 평가 스냅샷 → Terraform HCL 생성 (strict JSON Schema)
│   │   ├── search_query.py        # DB 조회·LLM 분석
│   │   ├── foundry_llm.py         # Foundry Responses API 래퍼 (responses_json/text)
│   │   ├── ai_foundry_config.py   # AI 엔드포인트/프로젝트 env 로딩
│   │   ├── entra_sso.py           # Entra ID OAuth2 (MSAL, UAMI 페더레이션)
│   │   ├── storage_paths.py       # 구독 스코프·레거시 경로 식별자
│   │   ├── db_init.py             # scripts/01_schema.sql 적용
│   │   └── db/                    # PostgreSQL 접근 (connection, assessment, checklist, terraform)
│   ├── chat/                      # AG-UI 챗봇
│   │   ├── agent.py               # SYSTEM_INSTRUCTIONS, create_agent(), ALL_TOOLS
│   │   └── tools/                 # 도구: assessment, search, terraform, azure_session
│   ├── scripts/01_schema.sql      # PostgreSQL 통합 스키마
│   ├── agui_server.py             # FastAPI 앱: auth · Azure REST · 체크리스트 · 평가 · Terraform · AG-UI
│   ├── main.py                    # 진입점: CLI 평가(-s) 또는 uvicorn 서버(기본)
│   └── pyproject.toml             # uv 의존성 (extra: chat, dev)
├── frontend/                      # React + Vite SPA (nginx 컨테이너로 서빙)
│   └── src/
│       ├── App.tsx                # 라우팅: dashboard/assessments/checklists/terraform + ChatSidebar
│       ├── components/            # 보드·패널·모달 (Dashboard, Assessment, Checklist, Terraform 등)
│       ├── context/               # AzureSession / AssessmentRun / TerraformRun Provider
│       ├── hooks/useAgUiChat.ts   # AG-UI 스트림 채팅 훅
│       └── lib/                   # authRest, ag-ui-client, azureIds, 헤더 유틸 등
├── terraform/                     # Azure 인프라 IaC
│   ├── main.tf / providers.tf / variables.tf / outputs.tf / terraform.tfvars
│   └── modules/                   # network, acr, ai_foundry, app_service, database,
│                                  # private_endpoints, appgw, firewall
├── docker/                        # Dockerfile.backend(.local), Dockerfile.frontend, nginx.conf.template
└── README.md
```

## 실행/빌드 명령 (모든 Python 명령은 `backend/`에서)

```bash
# 백엔드 의존성 (채팅/AG-UI 포함)
cd backend && uv sync --extra chat

# AG-UI 서버 실행 (기본 포트 5100) — Swagger: http://localhost:5100/docs
cd backend && uv run python main.py

# CLI 일회성 평가 (구독 ID 주면 서버 대신 CLI 모드)
cd backend && uv run python main.py -s <SUBSCRIPTION_ID> [-g <RG>] [-o all]

# 프론트엔드
cd frontend && npm install && npm run build   # (dev: npm run dev)

# 컨테이너 이미지 빌드 (저장소 루트 기준)
docker build -f docker/Dockerfile.backend  -t <acr>.azurecr.io/aiops-be:latest .
docker build -f docker/Dockerfile.frontend -t <acr>.azurecr.io/aiops-fe:latest .
```

## 백엔드 REST API 요약 (`/api`)

| 영역 | 주요 엔드포인트 |
|------|----------------|
| 인증 | `GET /api/auth/login` (→Entra 302), `GET /api/getAToken` (콜백), `POST /api/auth/logout`, `GET /api/auth/session` |
| Azure 세션 | `GET /api/azure/subscriptions` (OBO∩UAMI 교집합), `GET /api/azure/session-bootstrap`, `GET /api/azure/resources` |
| 대시보드 | `GET /api/dashboard/stats` (DB 집계 · subscriptions 필드는 "평가결과 있는 구독"만) |
| 평가 | `POST /api/assessments/run`, `GET /api/assessments`, `GET /api/assessments/{path}` |
| 체크리스트 | `GET/POST /api/checklists`, `…/upload`(YAML), `GET/PUT/DELETE /api/checklists/{name}`, `…/raw` |
| Terraform | `GET /api/terraform`, `POST /api/terraform/generate`, `POST /api/terraform/sync` |
| 채팅 | Agent Framework: `POST /api/chat` (AG-UI) |

## 아키텍처·구현 규칙 (중요)

- **인증 분리**: 웹은 사용자 위임 토큰(OBO, ARM 쿠키/Bearer)을 `UserOboCredential`로 사용. CLI는 `az login`.
  **Foundry LLM 호출은 항상 백엔드 자격증명(`DefaultAzureCredential` = SAMI/UAMI)** 로 수행.
  미들웨어 `DelegatedUserTokenMiddleware`가 경로별로 위임/기본 자격증명을 전환.
- **구독 스코프**: UI에서 테넌트+구독 선택 → `X-Azure-Tenant-Id` / `X-Azure-Subscription-Id` 헤더로 전달 →
  `chat/tools/azure_session.py`가 도구 컨텍스트에 반영. 평가는 이 스코프에서 Resource Graph로 조회.
- **평가 실행 전제**: `run_assessment`는 유효한 `checklist_id`(문자열) 또는 `checklist_ids`(배열)가 있어야 실행됨.
  없으면 카탈로그만 반환. 체크리스트는 **DB에 등록된 YAML**에서 로드됨(레포에 YAML 원본은 두지 않음).
- **대시보드 "구독 ID" 드롭다운**은 실시간 구독 목록이 아니라 **DB에 평가결과가 있는 구독**만 표시 → 신규 배포 시 비어 있음(정상).
- **체크리스트 YAML 스키마**: `metadata`(name/version/description/applicable_resource_types) →
  `categories[].items[].checks[]`, 각 check는 `question` + `azure_check`(type: automated|manual, guidance 등).
  `applicable_resource_types`가 비면 모든 리소스 타입에 적용되는 범용 체크리스트.
- **Terraform 생성**: strict JSON Schema로 provider/variables/main/outputs 4개 HCL 필드를 받음
  (`terraform_generator.py`의 `TERRAFORM_RESPONSE_JSON_SCHEMA`, 파일 순서 고정).
- **DB 모드 전환**: `DB_HOST`가 설정되면 DB 모드, 비면 파일 기반. 스키마는 기동 시 `db_init`이 적용.

## 배포 관련 주의 (거버넌스가 엄격한 구독)

- 이 앱의 Terraform은 기본적으로 **App Service·ACR·AI·PostgreSQL을 Private Endpoint 전용**으로 배포.
  App Service가 사설 ACR에서 이미지를 pull하려면 **`WEBSITE_PULL_IMAGE_OVER_VNET=true`** + ACR `publicNetworkAccess=Disabled`(PE 전용) 조합이 필요.
- **Key Vault 공용 접근이 정책으로 강제 Disabled인 구독**에서는 로컬 `terraform apply`가 KV 시크릿을 읽지 못함.
  이 경우 database·app_service 모듈이 KV 대신 **직접 변수/앱 설정**으로 자격증명을 받도록 구성되어 있음
  (민감값은 gitignore된 `terraform/*.auto.tfvars`에 분리).
- `frontend_exposure_mode`로 프론트 노출 토폴로지 분기: `appgw` | `appservice_managed_cert` | `appservice_default`(기본, azurewebsites.net).

## 코딩 컨벤션

- 코드 주석·문서는 한국어, 기술 용어·식별자·리소스 이름은 영어 그대로 유지.
- 챗봇 응답은 한국어로, 60% 미만 점수 리소스 강조, 심각도(high>medium>low) 순 정렬 (`chat/agent.py` 참고).
- 비밀값은 커밋 금지: `.env`, `*.pfx/.p12`, `*.tfstate`, `*.auto.tfvars`는 `.gitignore` 처리됨.
