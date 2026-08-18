# AIOps - ARB

Azure Architecture Review Board(ARB) 체크리스트를 기준으로 Azure 리소스를 수집·진단하고, Microsoft Agent Framework(AG-UI)와 React(Vite) UI로 대화형 평가·대시보드를 제공하는 프로젝트입니다.

> **구조:** 백엔드(Python)는 **`backend/`**, 프론트엔드(React)는 **`frontend/`** 로 분리되어 있습니다. Python 명령은 `backend/`에서 실행합니다.

## 무엇을 하는가

엔지니어가 자기 구독을 선택하면 조직의 ARB 체크리스트 기준으로 배포된 리소스를 진단하고, 점수화된 리포트·대시보드를 확인하고, 위반 사항을 해결하는 Terraform 초안까지 받아가는 웹 도구입니다. 사람이 반복하던 아키텍처 리뷰를 자동화하는 것이 목표입니다.

```text
구독 선택 → 리소스 동기화 → 체크리스트 선택 → 평가 실행
→ 점수 · 리포트 · 대시보드 → (선택) 위반 해결 Terraform 생성 → 다운로드
```

**UI 버튼과 챗봇은 같은 기능의 이중 진입점입니다.** 평가 실행과 Terraform 생성은 보드 버튼으로도, 챗 대화로도 수행할 수 있고 완료 신호는 커스텀 이벤트(`CHAT_REFRESH_*`)로 양쪽 화면에 전파됩니다.

## 화면

React SPA + 우측 챗 사이드바. 4개 보드 + 로그인으로 구성됩니다.

| 화면 | 기능 |
|------|------|
| **로그인** | Entra ID SSO → HttpOnly 쿠키 세션. 로그인 후 구독 선택 드로어(사용자 OBO 접근 구독 ∩ 백엔드 MI Reader 구독의 교집합만 노출) |
| **대시보드** | 기간·RG·타입 필터 기반 종합 통계 — KPI, 점수 추이, 점수 분포, 하위 리소스, 자동/수동 비율, pass/fail 비율. 차트 클릭 → 리소스 목록 → 체크 상세로 drill-down |
| **평가** (3탭) | ① 차트: 구독·리소스별 시각화 ② 진단평가: 클라우드 동기화 → RG/리소스 선택 → 체크리스트 선택 → 평가 실행 ③ 평가결과: 리포트 트리(MD/JSON/HTML) → 뷰어 → 다운로드 → **테라폼 생성** 버튼 |
| **체크리스트** | YAML 업로드·목록·상세(카테고리→항목→질문 트리, 자동/수동 배지)·편집·다운로드·삭제 |
| **Terraform** | 생성 이력(구독·타임스탬프 단위) → 파일별 HCL 뷰어 → 다운로드 |
| **챗 사이드바** | AG-UI 스트리밍. 도구 실행 상태 표시, 평가·Terraform 완료 시 해당 보드 자동 갱신 |

## 두 갈래 평가 경로

프로덕션 전환 과정에서 **판정 신뢰성**을 확보하기 위해 결정론 평가 경로를 추가했습니다. 기존 경로는 그대로 유지되며 두 경로는 동시에 존재합니다.

| | **v1 — LLM 평가** (기본) | **v2 — 결정론 평가** (`ENTERPRISE_ASSESSMENT_ENABLED`) |
|---|---|---|
| 코드 | `backend/agent/` | `backend/enterprise/` |
| 라우트 | `/api/assessments/*` | `/api/v2/*` |
| 판정 주체 | Azure AI Foundry LLM | `evaluator.DeterministicEvaluator` (LLM은 verdict 변경 불가) |
| 증거 | 리소스 JSON을 프롬프트로 전달 | ARM · Resource Graph · Azure Policy · Defender for Cloud · Advisor · APRL 어댑터가 수집, 레코드마다 source kind/reference/version · 관찰 시각 · SHA-256 content hash 저장 |
| 결과 상태 | `pass` / `fail` / `warning` / `n/a` / `manual_review` | `pass` / `fail` / `unknown` / `not_applicable` / `exempted` / `manual_pending` |
| 증거 결손 시 | LLM 추론에 의존 | `fail`이 아니라 **`unknown`** 으로 확정, `CollectionFailure`로 사유 기록 |
| 산출물 | MD·JSON·HTML 리포트 + Terraform 초안 | control별 verdict + evidence 추적(감사 가능) |

`ENTERPRISE_ASSESSMENT_ENABLED`가 꺼져 있으면 `/api/v2` 라우터를 등록하지 않으므로, 기존 `/api` 동작은 영향을 받지 않습니다(롤백 경로 보존).

## 아키텍처

Azure 리소스 기준의 배포 구성과 두 갈래 평가 경로(v1 LLM 평가 · v2 결정론 평가)입니다.

![AIOps - ARB Azure 아키텍처](.github/architecture.svg)

<details><summary>애플리케이션 상세 뷰 — 계층·모듈 단위 다이어그램</summary>

![AIOps - ARB 애플리케이션 상세 아키텍처](.github/architecture-detail.svg)

</details>

| 계층 | 구성 |
|------|------|
| 클라이언트 | React 18 + Vite SPA — `App.tsx` 라우팅, Context Provider, AG-UI 챗 사이드바 |
| 엣지 | nginx 컨테이너 — SPA 정적 서빙 + `/api/*` 리버스 프록시(동일 오리진) |
| API | FastAPI(`backend/agui_server.py`) — 위임 토큰 미들웨어, Azure 세션 미들웨어, `/api` · `/api/v2` · `/api/chat` |
| 도메인 | `backend/agent/`(v1 LLM 평가) · `backend/enterprise/`(v2 결정론 평가) · `backend/chat/`(AG-UI 도구) |
| 데이터 | PostgreSQL 16(v1·v2 테이블) + 로컬 리포트·Terraform 산출물 |
| 외부 | Entra ID, ARM·Resource Graph, Policy·Defender·Advisor·APRL, Azure AI Foundry |

**인증 경계(이중 구조)**: 리소스 조회는 **사용자 권한**으로, LLM 호출은 **백엔드 신원**으로 수행합니다.
`DelegatedUserTokenMiddleware`가 경로별로 사용자 위임 토큰(OBO)을 주입하고, 그 외 경로와 모든 Foundry 호출은
`DefaultAzureCredential`(SAMI/UAMI)을 사용합니다. UI가 보낸 `X-Azure-Tenant-Id`/`X-Azure-Subscription-Id`
헤더는 ARM 역조회로 위변조를 검증합니다.

**v2 결정론 평가 원칙**: 판정은 `enterprise/evaluator.py`만 생성하며 LLM은 verdict를 바꿀 수 없습니다.
증거 결손·충돌·스로틀링은 `fail`이 아니라 `unknown`으로 확정되고, 모든 evidence는 source kind/reference/version·
관찰 시각·SHA-256 content hash를 함께 저장합니다.

배포 토폴로지(App Service for Containers · Private Endpoint 전용)는 [.github/architecture.png](.github/architecture.png)를 참고합니다.

## 기술 스택

| 구분 | 내용 |
|------|------|
| 백엔드 | Python 3.11+, FastAPI, uvicorn, Microsoft Agent Framework(AG-UI), aiohttp |
| LLM | Azure AI Foundry root 엔드포인트 + 프로젝트명 + 배포 모델 (`AzureOpenAIResponsesClient`, 호출 자격 증명은 항상 백엔드 MI) |
| 인증(웹) | Entra ID OAuth2 — `agent/entra_sso.py`, `/api/auth/*`, UAMI 페더레이션 `client_assertion` |
| 저장소 | PostgreSQL 16(체크리스트·평가 결과·Terraform·enterprise evidence) + 로컬 파일 출력 |
| 프론트엔드 | React 18, Vite 5, TypeScript, Tailwind CSS, 경량 AG-UI 스트림 클라이언트 |
| 인프라 | Terraform (App Service, ACR, AI Foundry, PostgreSQL, VNet, Private Endpoint, App Gateway, Firewall) |
| 배포 | Docker 이미지 → Azure Container Registry → App Service for Containers |

## 체크리스트

YAML 1파일 = 체크리스트 1개이며, 업로드 시 원문(`raw_yaml`)과 3단 계층 평탄화 결과가 DB에 저장됩니다. **YAML 원본은 저장소에 두지 않고 앱의 체크리스트 화면에서 등록**합니다.

```yaml
metadata:            # name · version · description · applicable_resource_types
categories:
  - items:
      - checks:      # question · priority · check_type(manual|automated) · check_method
                     # condition_field/equals · expected_value · policy_effect · guidance
```

- `applicable_resource_types`가 비어 있으면 모든 리소스 타입에 적용되는 **universal** 체크리스트, 값이 있으면 case-insensitive substring 매칭으로 **specific** 체크리스트가 됩니다.
- 대표 평가 영역: System Stability(인프라·아키텍처·운영), Database Common, Azure MySQL, Azure PostgreSQL, Azure CosmosDB.
- 지원 리소스 타입은 체크리스트의 `applicable_resource_types`와 `azure_resource_reader.SUPPORTED_RESOURCE_TYPES`를 따릅니다(Compute·Database·Networking·Storage·Monitoring·Security).
- v2 경로는 여기에 더해 control ↔ 관리형 원천(Policy·Defender·Advisor·APRL) 매핑 YAML을 사용합니다(`experiments/coverage_spike/mappings/`).

## 사전 요구사항

- Python **3.11 이상**
- [uv](https://docs.astral.sh/uv/getting-started/installation/) (패키지·가상환경 관리)
- Node.js / npm (프론트엔드)
- Azure CLI — 서버·CLI 평가 시 `az login` 및 구독·리소스 접근 권한  
- 웹 SSO 사용 시 Entra 앱 등록 및 `.env`의 `AZURE_AUTH_*` 설정

## 설치

백엔드는 `backend/`에서 실행합니다.

```bash
# 백엔드(채팅/AG-UI 서버 포함) 의존성
cd backend && uv sync --extra chat

# 개발 도구(테스트·린트)
cd backend && uv sync --extra dev

# 프론트엔드
cd frontend && npm install
```

## 설정

1. `.env.template`을 복사해 `.env`를 만듭니다.

   ```bash
   # Linux / macOS
   cp .env.template .env

   # Windows (PowerShell)
   Copy-Item .env.template .env
   ```

2. `.env`에서 최소한 다음을 채웁니다(자세한 주석은 `.env.template` 참고).

   | 변수 | 설명 |
   |------|------|
   | `AZURE_AUTH_CLIENT_ID` / `AZURE_AUTH_TENANT_ID` / `AZURE_AUTH_REDIRECT_URI` | 웹 Entra SSO |
   | `AZURE_AUTH_STATE_SECRET` | OAuth `state` HMAC용 앱 로컬 비밀(Entra 미등록, 16자 이상 권장) |
   | **UAMI 페더레이션** `AZURE_AUTH_SSO_UAMI_CLIENT_ID` 또는 `AZURE_RESOURCE_READER_UAMI_CLIENT_ID` | Entra 앱에 페더레이션된 관리 ID 클라이언트 ID(MSAL `client_assertion`) |
   | `AZURE_AI_ENDPOINT` | Azure AI Foundry root 엔드포인트 |
   | `AZURE_AI_PROJECT_NAME` | Azure AI Foundry 프로젝트 이름 |
   | `AZURE_AI_MODEL_DEPLOYMENT_NAME` | 배포된 모델 이름 |
   | `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` / `DB_PASSWORD` | 체크리스트·평가 결과·Terraform 산출물 저장용 PostgreSQL (비면 파일 기반 폴백) |
   | `ENTERPRISE_ASSESSMENT_ENABLED` | `true`일 때만 v2 결정론 평가 라우터(`/api/v2`) 등록 |

3. **CLI 평가** 시 Azure 인증: **`az login`**. **웹 UI**는 SSO 로그인 플로우를 사용합니다.

## 사용법

### 웹 UI + 백엔드 (docker)

프론트엔드(nginx)와 백엔드는 docker 컨테이너로 실행한다. nginx가 정적 SPA 서빙과 `/api/*` 프록시를
동일 오리진으로 처리하므로 별도 dev 서버나 CORS 설정이 필요 없다.

백엔드(기본 포트 **5100**) — 직접 실행하려면:

```bash
cd backend && uv run python main.py
```

- AG-UI 채팅: `POST /api/chat`  
- REST API: `/api/*` (nginx가 백엔드로 프록시)  
- Swagger: `http://localhost:5100/docs`  
- Terraform 정적 다운로드: `http://localhost:5100/api/downloads`  

컨테이너 이미지 빌드(저장소 루트에서):

```bash
docker build -f docker/Dockerfile.backend  -t <acr>.azurecr.io/aiops-be:latest .
docker build -f docker/Dockerfile.frontend -t <acr>.azurecr.io/aiops-fe:latest .
```

> 컨테이너는 80 포트로 SPA를 서빙하고 `BACKEND_URL`(기본 `http://localhost:5100`)로 `/api/*`를 프록시한다.
> 정적 빌드만 확인하려면 `cd frontend && npm run build`.

### CLI로 구독 평가만 실행

구독 ID를 주면 **웹 서버가 아니라** 일회성 CLI 평가가 실행됩니다.

```bash
cd backend && uv run python main.py --subscription-id <SUBSCRIPTION_ID>

# 리소스 그룹 / 타입 필터
cd backend && uv run python main.py -s <SUBSCRIPTION_ID> -g <RESOURCE_GROUP>
cd backend && uv run python main.py -s <SUBSCRIPTION_ID> -t "microsoft.dbformysql/flexibleservers"

# 출력 형식·디렉터리
cd backend && uv run python main.py -s <SUBSCRIPTION_ID> -o all --output-dir ./results
```

### CLI 옵션

| 옵션 | 설명 |
|------|------|
| `-s, --subscription-id` | 평가할 구독 ID (**CLI 모드일 때 필수**) |
| `-g, --resource-group` | 특정 리소스 그룹만 |
| `-t, --resource-type` | 특정 리소스 타입만 |
| `-o, --output-format` | `markdown` / `json` / `html` / `all` |
| `--output-dir` | CLI 결과 저장 경로(기본 `./results`) |
| `--dry-run` | 리소스 목록만 출력 |
| `-p, --port` | 서버 모드일 때 포트(기본 `5100`) |

### 테스트 · coverage gate

```powershell
# enterprise 결정론 평가 테스트
cd backend; uv run pytest tests/enterprise -v

# 합성 Storage fixture로 evaluator·coverage 게이트 검증 (저장소 루트)
uv run --project backend python backend/scripts/run_coverage_spike.py
```

canonical 산출물의 진입점은 `experiments/coverage_spike/reports/current.json` 하나입니다. 이 manifest는
content hash 기반 immutable generation 아래의 JSON과 Markdown 경로 및 SHA-256을 함께 지정합니다.
consumer는 `enterprise.coverage.read_current_report_bundle()`로 manifest와 두 파일을 검증해 읽어야 합니다.
상세한 gate 의미와 artifact 계약은 `experiments/coverage_spike/README.md`를 참고합니다.

### Python에서 평가 엔진만 사용(예시)

```python
from agent.azure_resource_reader import AzureResourceReader
from agent.checklist_loader import ChecklistLoader, get_configured_checklist_loader
from pathlib import Path

from agent.assessment_engine import AssessmentEngine
from agent.report_generator import ReportGenerator

reader = AzureResourceReader(subscription_ids=["<SUBSCRIPTION_ID>"])
resources = reader.get_all_resources()

# 권장: .env DB 설정과 동일하게 로드
loader = get_configured_checklist_loader(Path("."))

engine = AssessmentEngine(
    ai_endpoint="https://<your-foundry>.services.ai.azure.com",
    deployment_name="<DEPLOYMENT_NAME>",
    checklist_loader=loader,
)
assessments = engine.assess_resources(resources)

report_gen = ReportGenerator(output_dir="./results", subscription_id_hint="<SUBSCRIPTION_ID>")
report_gen.generate_html_report(assessments)
```

## 프로젝트 구조

```
├── backend/                       # Python 백엔드 (uv 프로젝트)
│   ├── agent/                     # v1: 리소스 조회 · 체크리스트 · LLM 평가 · 리포트 · Terraform
│   │   ├── azure_credential.py    # LazyDefaultAzureCredential, 위임/CLI 자격 증명 스택
│   │   ├── azure_resource_reader.py  # Resource Graph 조회, 지원 리소스 타입
│   │   ├── subscription_scope.py  # 구독 정규화·범위 판별
│   │   ├── checklist_loader.py    # YAML/DB 체크리스트 로드
│   │   ├── assessment_engine.py   # LLM 기반 체크리스트 평가
│   │   ├── report_generator.py    # Markdown/JSON/HTML 리포트
│   │   ├── terraform_generator.py # 평가 스냅샷 → HCL (strict JSON Schema)
│   │   ├── search_query.py        # DB 조회·LLM 분석
│   │   ├── entra_sso.py           # Entra ID OAuth2 · UserOboCredential
│   │   ├── db_init.py             # scripts/01_schema.sql 적용
│   │   └── db/                    # PostgreSQL 접근 (connection/assessment/checklist/terraform)
│   ├── enterprise/                # v2: 결정론 평가
│   │   ├── domain.py              # 불변 계약 (VerdictState, EvidenceRecord, ControlDefinition)
│   │   ├── registry.py            # control + source mapping YAML 로드
│   │   ├── adapters/              # arm · arg · aprl · policy · defender · advisor
│   │   ├── evaluator.py           # 결정론 판정 (LLM 미개입)
│   │   ├── service.py             # 수집 → 평가 → 영속화 오케스트레이션
│   │   ├── postgres_repository.py # DB 미설정 시 InMemory 폴백
│   │   ├── coverage.py            # coverage 리포트 immutable publish/read
│   │   └── api.py                 # /api/v2 라우터 (feature flag)
│   ├── chat/                      # AG-UI 챗봇 (agent.py, tools/)
│   ├── tests/enterprise/          # domain·registry·evaluator·adapters·service·api·coverage 테스트
│   ├── scripts/01_schema.sql      # PostgreSQL 통합 스키마
│   ├── agui_server.py             # FastAPI 앱 (미들웨어 · REST · AG-UI)
│   ├── main.py                    # 진입점: CLI 평가(-s) 또는 uvicorn 서버
│   └── pyproject.toml             # uv 의존성 (extra: chat, dev)
├── frontend/src/                  # App.tsx(라우팅), components/, context/, hooks/, lib/
├── docker/                        # Dockerfile.backend(.local), Dockerfile.frontend, nginx.conf.template
├── terraform/                     # Azure 인프라 (network, acr, ai_foundry, app_service,
│                                  # database, private_endpoints, appgw, firewall)
├── experiments/coverage_spike/    # Storage control 스파이크 (checklists · mappings · fixtures · reports)
├── docs/superpowers/              # 프로덕션 재설계 스펙·플랜
├── .github/architecture.svg       # 애플리케이션 아키텍처 다이어그램
├── .env.template
└── README.md
```

### 백엔드 REST API 요약

| 영역 | 주요 엔드포인트 |
|------|----------------|
| 인증 | `GET /api/auth/login` (→ Entra 302), `GET /api/getAToken` (콜백·쿠키 설정 후 `/` 302), `POST /api/auth/logout`, `GET /api/auth/session` |
| Azure 세션 | `GET /api/azure/subscriptions` (OBO ∩ UAMI 교집합), `GET /api/azure/session-bootstrap`, `GET /api/azure/resources` |
| 대시보드 | `GET /api/dashboard/stats` · `/kpi` · `/trend-detail` · `/score-range-resources` (평가 결과가 있는 구독만 노출) |
| 평가 (v1) | `POST /api/assessments/run`, `GET /api/assessments`, `GET /api/assessments/{path}`, `GET /api/assessments/charts-summary` · `/resource-check-results` |
| 평가 (v2) | `GET /api/v2/controls`, `POST /api/v2/assessments`, `GET /api/v2/assessments/{run_id}`, `GET /api/v2/findings/{finding_id}` |
| 체크리스트 | `GET /api/checklists`, `POST /api/checklists/upload`, `GET/PUT/DELETE /api/checklists/{name}`, `GET /api/checklists/{name}/raw` |
| Terraform | `GET /api/terraform`, `POST /api/terraform/generate` (리포트 기반 즉시 생성), `DELETE /api/terraform/{sub}/{ts}`, `GET /api/terraform/{sub}/{ts}/{file}[/raw]` |
| 다운로드 | `GET /api/downloads/...` (생성된 HCL 정적 서빙) |
| 채팅 | `POST /api/chat` (AG-UI · Microsoft Agent Framework) |

### `agent/` — 코어 진단·저장소

| 파일 | 역할 |
|------|------|
| `azure_credential.py` | `LazyDefaultAzureCredential`, 위임·CLI 자격 증명 전환(`push_cli_credential` 등) |
| `azure_resource_reader.py` | Resource Graph, 지원 리소스 타입 필터, 구독·RG·ID 조회 |
| `subscription_scope.py` | 구독 정규화, 평가 JSON이 특정 구독에 속하는지 판별 |
| `checklist_loader.py` | YAML 파싱, DB 체크리스트 로드, `get_configured_checklist_loader(project_dir)` |
| `assessment_engine.py` | Foundry 클라이언트로 체크리스트 매칭·LLM 평가 |
| `report_generator.py` | 리포트 파일 생성 |
| `terraform_generator.py` | 평가 스냅샷 기반 Terraform 생성 |
| `search_query.py` | DB 조회·LLM 분석 |
| `db/` | PostgreSQL 접근 모듈 (커넥션 풀, assessment/checklist/terraform) |
| `storage_paths.py` | 구독 스코프·레거시 경로 식별자 |
| `entra_sso.py` | 웹 SSO 토큰 교환·세션 쿠키 |

`results/`는 CLI 로컬 평가 결과 출력 및 DB 미설정 시 폴백 저장소로 사용됩니다(저장소에 커밋하지 않음).

### `enterprise/` — 결정론 평가

| 파일 | 역할 |
|------|------|
| `domain.py` | `VerdictState`, `EvidenceRecord`(SHA-256 content hash), `ControlDefinition`, `Verdict` 불변 계약 |
| `registry.py` | control 체크리스트 + evidence source mapping YAML 로드·검증 |
| `adapters/` | `arm` · `arg` · `aprl` · `policy` · `defender` · `advisor` — aiohttp 기반 비동기 수집 |
| `evaluator.py` | canonical state 규칙으로 verdict 산출 (LLM 미개입) |
| `service.py` | 대상 탐색 → source별 수집 → 평가 → 영속화 오케스트레이션 |
| `repository.py` / `postgres_repository.py` | InMemory / PostgreSQL 영속화 (DB 미설정 시 자동 폴백) |
| `coverage.py` | coverage 리포트 immutable generation publish·검증 read |
| `api.py` | `/api/v2` 라우터, `ENTERPRISE_ASSESSMENT_ENABLED` 게이트, 요청 스코프 자격 증명 |

### `chat/` — AG-UI 챗봇 도구 9종

| 파일 | 도구 |
|------|------|
| `agent.py` | `SYSTEM_INSTRUCTIONS`, `create_agent()`, `ALL_TOOLS` |
| `tools/assessment.py` | `get_subscription_info` · `list_azure_resources` · `list_checklists` · `get_checklist_detail` · `run_assessment` |
| `tools/search.py` | `get_latest_assessments` · `search_assessments` · `get_resource_detail` |
| `tools/terraform.py` | `generate_terraform_code` (코드 붙여넣기 대신 다운로드 링크 반환) |
| `tools/azure_session.py` | UI가 보낸 테넌트·구독 스코프를 도구 컨텍스트에 주입 |

`run_assessment`는 유효한 `checklist_id`/`checklist_ids`가 있어야 실행되며, 없으면 카탈로그만 반환합니다. 스레드 상태에 마지막 `report_id`를 저장해 "방금 결과로 Terraform 생성" 흐름을 지원합니다. 응답은 한국어로, 60% 미만 점수 리소스를 강조하고 심각도(high>medium>low) 순으로 정렬합니다.

## 데이터 모델 (PostgreSQL)

기동 시 `agent/db_init.py`가 `backend/scripts/01_schema.sql`을 적용합니다. `DB_HOST`가 비면 로컬 `results/{subscription}/` 파일 기반으로 폴백합니다.

| 그룹 | 테이블 |
|------|--------|
| 체크리스트 | `checklists`(raw_yaml 원문·등록자) → `checklist_items`(categories→items→checks 평탄화) |
| v1 평가 결과 | `result_reports`(실행) → `result_resource_assessments`(리소스) → `result_check_results`(검사) + `result_file`(파일 원문·details JSONB) |
| Terraform | `terraform_runs` → `terraform_run_files` (source report 추적) |
| v2 결정론 평가 | `control_definitions` · `enterprise_evaluation_runs` · `snapshot_runs` · `evidence_records` · `enterprise_collection_failures` · `enterprise_verdicts` |
| 개선 실행 | `remediation_runs` · `remediation_artifacts` |

v1 점수는 `n/a`·`manual_review`를 제외한 scorable 항목 중 pass 비율 × 100으로 산정합니다.

## 현재 구현 상태

프로덕션 전환은 [Issue #1 — 설계·기능 원점 정리](https://github.com/daeungo1/poc-aiops-arb/issues/1)의 §7 방향 논의를 기준으로 진행 중입니다.

| 논의 지점 | 현재 상태 |
|-----------|-----------|
| ① 판정 신뢰성 — 결정론 evaluator, evidence 실측 대조 | ✅ `backend/enterprise/` 도메인·레지스트리·어댑터·evaluator·영속화·`/api/v2` 구현 |
| ② 체크리스트 corpus — 관리형 원천(Policy·Defender·Advisor·APRL)과의 관계 | 🟡 source mapping 계약 확정, 현재 Azure Storage control 스파이크 범위 |
| ③ Terraform 산출물 품질 — validate·scan 검증 | ⬜ `remediation_*` 스키마만 선반영, 검증 단계 미구현 |
| ④ 챗봇 역할 — evidence 조회·해석 도구 | ⬜ 미구현 (현재 챗봇은 v1 파이프라인 런처) |
| ⑤ 평가 방법 — 정확도 측정 기준 | 🟡 합성 fixture 기반 coverage gate + `backend/tests/enterprise/` |

v1 경로(전체 파이프라인 end-to-end, UI·챗 이중 진입, 구독 스코프 격리, 특정 리포트 타겟 Terraform 생성, CLI 평가, DB/파일 폴백)는 그대로 동작합니다.

## 라이선스

Internal Use Only — [PROPRIETARY.md](PROPRIETARY.md)
