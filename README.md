# AIOps Resource Assessment

Azure Architecture Review Board 체크리스트를 기준으로 Azure 리소스를 수집·진단하고, Microsoft Agent Framework(AG-UI)와 React(Vite) UI로 대화형 평가·대시보드를 제공하는 프로젝트입니다.

> **구조:** 백엔드(Python)는 **`backend/`**, 프론트엔드(React)는 **`frontend/`** 로 분리되어 있습니다. Python 명령은 `backend/`에서 실행합니다.

## 개요

---

### ✅ 주요 업데이트 기능 (2026-04-05)
- **리포트 기반 즉시 테라폼 생성**: 평가 결과 리포트 상세 화면에서 버튼 클릭 한 번으로 해당 리포트의 모든 위반 사항에 대한 테라폼 코드를 즉각 생성합니다. (기존 챗봇/선택 방식 보완)
- **특정 리포트 타겟팅**: 최신 리포트뿐만 아니라, 과거에 생성된 특정 리포트를 선택하여 해당 시점의 진단 결과를 바탕으로 코드를 생성할 수 있습니다.

---

1. **Azure 리소스 수집**: Azure Resource Graph 등으로 구독 내 리소스 정보 조회  
2. **체크리스트 기반 진단**: ARB 체크리스트 YAML을 기준으로 LLM 기반 평가  
3. **리포트·산출물**: Markdown / JSON / HTML 리포트, Terraform 초안 생성 및 다운로드  
4. **웹 UI**: Entra ID SSO, FastAPI REST + AG-UI 채팅(`POST /chat`), React 대시보드·보드 화면

## 기술 스택

| 구분 | 내용 |
|------|------|
| 백엔드 | Python 3.11+, FastAPI, uvicorn, Microsoft Agent Framework(AG-UI) |
| LLM | Azure AI Foundry root 엔드포인트 + 프로젝트명 + 배포 모델 (`DefaultAzureCredential`, 웹은 위임 토큰·CLI는 `az login`) |
| 인증(웹) | Entra ID OAuth2 — `agent/entra_sso.py`, `/api/auth/*` |
| 저장소 | PostgreSQL(체크리스트·평가 결과·Terraform 산출물) + 로컬 파일 출력 |
| 프론트엔드 | React 18, Vite 5, TypeScript, Tailwind CSS, 경량 AG-UI 스트림 클라이언트 |

## 설계·기능 원점 문서

- [AIOps Resource Assessment — 설계·기능 원점 정리](docs/aiops-resource-assessment-origin.md)

## Architecture Review Board 체크리스트 영역

| 체크리스트 | 설명 | 점검 항목(대략) |
|-----------|------|----------------|
| System Stability | 인프라·아키텍처·운영 안정성 | 50+ |
| Database Common | DB 공통 보안/운영 | 10+ |
| Azure MySQL | MySQL Flexible Server | 25+ |
| Azure PostgreSQL | PostgreSQL Flexible Server | 15+ |
| Azure CosmosDB | Cosmos DB for NoSQL | 15+ |

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

# 개발 도구(선택: ruff 등)
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
   | `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` / `DB_PASSWORD` | 체크리스트·평가 결과·Terraform 산출물 저장용 PostgreSQL |

3. **CLI 평가** 시 Azure 인증: **`az login`**. **웹 UI**는 SSO 로그인 플로우를 사용합니다.

## 사용법

### 웹 UI + 백엔드 (docker)

프론트엔드(nginx)와 백엔드는 docker 컨테이너로 실행한다. nginx가 정적 SPA 서빙과 `/api/*` 프록시를
동일 오리진으로 처리하므로 별도 dev 서버나 CORS 설정이 필요 없다.

백엔드(기본 포트 **5100**) — 직접 실행하려면:

```bash
cd backend && uv run python main.py
```

- AG-UI 채팅: `POST /chat`  
- REST API: `/api/*` (nginx가 백엔드로 프록시)  
- Swagger: `http://localhost:5100/docs`  
- Terraform 정적 다운로드: `http://localhost:5100/downloads`  

프론트엔드 이미지 빌드(저장소 루트에서):

```bash
docker build -f docker/Dockerfile.frontend -t aiops-fe .
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
aiops/
├── backend/                       # Python 백엔드 (uv 프로젝트)
│   ├── agent/                     # 리소스 조회, 체크리스트, 평가, 리포트, 검색, Terraform
│   │   ├── azure_credential.py    # LazyDefaultAzureCredential, CLI/위임 토큰 스택
│   │   ├── azure_resource_reader.py
│   │   ├── checklist_loader.py    # YAML/DB 로드, get_configured_checklist_loader()
│   │   ├── assessment_engine.py   # LLM 기반 체크리스트 평가
│   │   ├── report_generator.py    # Markdown/JSON/HTML 로컬 파일 생성
│   │   ├── terraform_generator.py # 평가 기반 Terraform 생성
│   │   ├── search_query.py        # DB 기반 평가 결과 조회·LLM 분석
│   │   ├── db/                    # PostgreSQL 접근 (assessment/checklist/terraform)
│   │   ├── db_init.py             # scripts/01_schema.sql 적용
│   │   └── entra_sso.py           # Entra ID OAuth2 (웹 로그인)
│   ├── chat/                      # AG-UI 에이전트·도구 (agent.py, tools/)
│   ├── scripts/01_schema.sql      # PostgreSQL 스키마
│   ├── agui_server.py             # FastAPI 앱: auth, Azure REST, 체크리스트, 평가, Terraform, AG-UI
│   ├── main.py                    # 진입점: CLI 평가 또는 uvicorn 서버
│   └── pyproject.toml             # uv 의존성 (extra: chat, dev)
├── frontend/                      # React + Vite SPA (nginx 컨테이너로 서빙)
│   ├── src/                       # App.tsx(라우팅), components/, context/, hooks/, lib/
│   └── vite.config.ts             # 정적 SPA 빌드 전용
├── docker/                        # Dockerfile.backend, Dockerfile.frontend, nginx.conf.template
├── terraform/                     # Azure 인프라 (App Service, App Gateway, Key Vault)
├── docker-compose.yaml            # 로컬 스택 (build 컨텍스트 = 루트, env_file = .env)
├── .env.template
└── README.md
```

### 백엔드 REST API 요약 (`/api`)

| 영역 | 주요 엔드포인트 |
|------|----------------|
| 인증 | `GET /api/auth/login` (→ Entra 302), `GET /api/getAToken` (Entra 콜백 수신·쿠키 설정 후 `/` 로 302), `POST /api/auth/logout`, `GET /api/auth/session` |
| Azure 세션 | `GET /api/azure/subscriptions`, `GET /api/azure/session-bootstrap`, `GET /api/azure/resources` |
| 평가 | `POST /api/assessments/run`, `GET /api/assessments`, `GET /api/assessments/{path}`, `POST /api/assessments/sync` |
| 체크리스트 | `GET/POST /api/checklists`, `…/sync`, `…/upload`, `GET/PUT/DELETE /api/checklists/{name}`, `GET …/raw` |
| Terraform | `GET /api/terraform`, `POST /api/terraform/generate` (리포트 기반 즉시 생성), `POST /api/terraform/sync` 등 |
| 채팅 | Agent Framework: **`POST /chat`** (AG-UI) |

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

로컬 `checklists/`는 체크리스트 YAML 원본/샘플 보관용으로, `results/`는 CLI 로컬 평가 결과 출력 용도로 사용됩니다.

### `chat/` — AG-UI 챗봇·도구

| 파일 | 역할 |
|------|------|
| `agent.py` | `SYSTEM_INSTRUCTIONS`, `create_agent()`, `ALL_TOOLS` |
| `tools/assessment.py` | 구독 정보, 리소스 목록, 체크리스트, **`run_assessment`**(스코프는 `resource_ids`/RG/이름 등; 타입 전용 인자 없음) |
| `tools/search.py` | 최근 평가, 검색, 리소스 상세 |
| `tools/terraform.py` | Terraform 생성·다운로드 URL |
| `tools/azure_session.py` | UI에서 넘긴 테넌트·구독 헤더와 도구 연동 |

## 체크리스트 커스터마이징

- **앱에서 추가**: 체크리스트 화면의 `체크리스트 추가 (YAML)` 기능으로 YAML을 PostgreSQL에 등록합니다.
- **로컬 원본 관리**: `checklists/`에 YAML 원본이나 샘플을 보관할 수 있습니다.

YAML 형식은 기존 체크리스트 파일을 참고합니다(`metadata`, `categories`, `items`, `azure_check` 등).

## 지원 리소스 타입(요약)

Compute(VM, VMSS, AKS, App Service 등), Database(MySQL/PostgreSQL Flexible, Cosmos DB, SQL 등), Networking(VNet, NSG, App Gateway, LB, Private Endpoint, Bastion 등), Storage, Monitoring, Security(Key Vault, Recovery Services Vault) 등 — 구체적 매핑은 체크리스트의 `applicable_resource_types`와 `azure_resource_reader.SUPPORTED_RESOURCE_TYPES`를 따릅니다.

## 라이선스

Internal Use Only
