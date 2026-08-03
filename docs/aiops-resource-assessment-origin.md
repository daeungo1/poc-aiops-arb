# AIOps Resource Assessment — 설계·기능 원점 정리

> 이 문서의 기준: 저장소의 실제 코드(프론트엔드·백엔드·스키마).
> 목표 아키텍처 제안이 아닌, 이 레포가 만들려던 것과 현재 만들어진 것의 충실한 기록. 방향 논의는 §7.

---

## 1. 이 레포가 만들려던 것

**사내 Azure Architecture Review Board(ARB) 평가 포털**

엔지니어가 자기 구독을 선택하면, 조직의 ARB 체크리스트 기준으로 배포된 리소스를 진단하고,
점수화된 리포트·대시보드를 보고, 위반 사항을 해결하는 Terraform 초안까지 받아가는 웹 도구.
챗봇이 이 전 과정을 대화로도 수행 가능하게 보조.

```text
구독 선택 → 리소스 동기화 → 체크리스트 선택 → 평가 실행
→ 점수·리포트·대시보드 → (선택) 위반 해결 Terraform 생성 → 다운로드
```

한 줄 정체성: **"ARB 체크리스트의 자동 평가와 개선 코드 초안 생성"**

---

## 2. 사용자 경험 (프론트엔드)

React SPA + 우측 챗 사이드바. 4개 페이지 + 로그인.

| 화면 | 기능 |
|---|---|
| **로그인** | Entra ID SSO → HttpOnly cookie 세션. 로그인 후 구독 선택 드로어 (사용자 OBO 접근 가능 구독 ∩ 백엔드 MI Reader 구독의 교집합만 노출) |
| **대시보드** | 기간·RG·타입 필터의 종합 통계 — KPI, 점수 추이, 점수 분포, 하위 리소스, 자동/수동 비율, pass/fail 비율. 차트 클릭 시 해당 리소스 목록 팝업 → 리소스 클릭 시 체크 상세 팝업 (drill-down) |
| **평가** (3탭) | ① 차트: 구독·리소스별 시각화 ② **진단평가**: 클라우드 동기화 → RG/리소스 체크박스 선택 → 체크리스트 선택 → 평가 실행 (진행 상태 표시) ③ **평가결과**: 리포트 파일 트리 (MD/JSON/HTML) → 뷰어 → 다운로드 → **테라폼 생성 버튼** |
| **체크리스트** | YAML 업로드·목록·상세(카테고리→항목→질문 트리, 자동/수동 배지)·편집·다운로드·삭제 |
| **Terraform** | 생성 이력 목록 (구독·타임스탬프 단위) → 파일별 HCL 뷰어 → 다운로드 |
| **챗 사이드바** | AG-UI SSE 스트리밍. 도구 실행 상태 표시. 평가·Terraform 완료 시 이벤트로 해당 보드 자동 갱신 |

특징: **UI 버튼과 챗봇이 같은 기능의 이중 진입점**.

---

## 3. 기능 상세 (백엔드)

### 3.1 리소스 수집

- Azure Resource Graph KQL로 구독 내 리소스 조회: `id, name, type, resource_group, subscription, location, sku, properties, tags`
- 스코프: `X-Azure-Tenant-Id` / `X-Azure-Subscription-Id` 헤더 → 미들웨어 → ContextVar 전파
- 헤더 위변조 검증: subscription의 실제 tenant를 ARM으로 역조회해 대조

### 3.2 체크리스트

- YAML 1파일 = 체크리스트 1개
- `metadata`(name/version/description/`applicable_resource_types`) → `categories[] → items[] → checks[]`
- 저장: 업로드 시 `checklists` + `checklist_items` 평탄화. DB 미설정 시 파일 기반 폴백
- 리소스 매칭: `applicable_resource_types` case-insensitive substring 매칭

### 3.3 평가 파이프라인

```mermaid
flowchart LR
    A[리소스 필터링] --> B[체크리스트 로드]
    B --> C[리소스별 매칭]
    C --> D[병렬 LLM 호출]
    D --> E[응답 파싱]
    E --> F[점수 산정]
    F --> G[리포트 생성]
    G --> H[저장]
```

- LLM 출력(JSON 강제): `status`(pass/fail/warning/n:a/manual_review), `finding`, `recommendation`, `severity`, `evidence`
- 점수: `n/a`·`manual_review` 제외한 scorable 중 pass 비율 × 100
- 실패 시 해당 검사를 `manual_review`로 기록하고 계속 진행

### 3.4 리포트·대시보드

- 3형식 생성: Markdown / JSON / HTML
- DB 3계층 저장: `result_reports` → `result_resource_assessments` → `result_check_results` + `result_file`

### 3.5 Terraform 생성

- 입력: 평가 리포트 1건
- strict JSON Schema로 `provider.tf / variables.tf / main.tf / outputs.tf` 4파일 생성
- 저장: `terraform_runs`/`terraform_run_files` + 다운로드 링크

### 3.6 챗봇

- Agent Framework + AG-UI (`POST /api/chat` SSE)
- 도구 8종: 구독/리소스/체크리스트/평가/검색/Terraform 관련
- 스레드 상태에서 마지막 `report_id`를 저장해 "방금 결과로 Terraform" 흐름 지원

### 3.7 인증·자격증명

- 리소스 조회: **사용자 권한(OBO)**
- LLM 호출: **백엔드 자격증명(DefaultAzureCredential)**

---

## 4. 데이터 모델 (PostgreSQL 8테이블)

- `checklists`, `checklist_items`
- `result_reports`, `result_resource_assessments`, `result_check_results`, `result_file`
- `terraform_runs`, `terraform_run_files`

DB 미설정 시 로컬 `results/{subscription}/` 파일 기반 폴백.

---

## 5. 시스템 구성

Frontend(React SPA) ↔ Backend(FastAPI + AG-UI) ↔ Azure Resource Graph/OBO, Azure AI Foundry/MI, PostgreSQL.

배포: Docker 이미지 → ACR → App Service for Containers.

---

## 6. 구현 상태의 사실 기록

동작하는 것:

- 수집 → 평가 → 리포트 → 대시보드 → Terraform → 다운로드 end-to-end
- UI·챗 이중 진입과 완료 이벤트 동기화
- 구독 스코프 격리, 특정 리포트 타겟 Terraform 생성, CLI 평가 모드, DB/파일 폴백

설계 의도는 있으나 미구현인 것:

- `check_type=automated`/`check_method`/`condition_field`의 결정론 실행 로직
- `search_assessments` 도구 stub
- Terraform 사후 검증(`terraform validate`, scanner) 파이프라인
- 테스트 코드·평가 정확도 측정 체계

---

## 7. 방향 논의 지점

1. `automated` 항목의 결정론 실행과 evidence 실측 대조
2. 체크리스트 corpus 유지/확장 전략
3. Terraform 산출물 최소 검증 수준 결정
4. 챗봇 역할 재정의(실행 런처 vs 해석/조사)
5. 판정 정확도·생성 품질 평가 체계

---

## Ⅱ부 — 제품화 방향: 프로덕션 아키텍처 재평가

> 이 파트는 Ⅰ부(§1~§7)의 현황 기록과 분리된 **제안/의사결정 보류 영역**이다.

---

## 8. 방향 요약

**이 레포가 제품에 기여하는 핵심 자산은 도메인 학습(스코프 모델·UX 흐름·본 문서 정리)이다.**
PoC는 E2E 흐름과 사용자 경험을 증명했다. 다만 프로덕션은 감사 가능한 판정을 요구하므로,
현재 LLM 단독 판정 코어는 개조보다 재설계가 적합하다는 판단이다.

---

## 9. 코드 자산의 분해 (실측)

백엔드 ~9.9k LOC + 프론트 ~7.3k LOC:

| 분류 | 규모 | 평가 |
|---|---|---|
| **판정·생성 코어** (`assessment_engine.py` 491 + `terraform_generator.py` 635) | ~1.1k LOC | 실체는 프롬프트 + JSON 파싱 중심. PoC 검증에는 충분했으나 프로덕션의 감사 가능 판정 요구와는 간극이 커 재설계 대상 |
| **수제 배관** (`entra_sso.py` 406, `agent/db/*` ~2.5k, `agui_server.py` 1.35k, `useAgUiChat.ts` 251 등) | ~5k+ LOC | 품질은 양호하나 관리형 대체재가 존재하는 자체 구현 비중이 높음 |
| **UI 보드·차트·팝업** | ~6k LOC | 제품 UX 원형으로 계승 가치 높음. 단 판정 모델 변경 시 데이터 계약 재정의 필요 |
| **배포 Terraform IaC** | - | PE 전용·거버넌스 대응 노하우 포함, 재사용 가치 높음 |

프로덕션 관점에서 재검토가 필요한 두 지점:

1. `check_method`·`condition_field` 미사용 컬럼 구현을 자체 룰엔진으로 완성할지 여부  
   → 유지보수 최소화 목표와 상충 가능
2. 기존 경계 내 점진 개조 vs 재설계 비용  
   → 판정 권위가 바뀌면 engine→DB→report→chat→dashboard 계약이 연쇄 영향이라, 점진 개조와 재설계의 비용 차가 크지 않음

---

## 10. 유지보수 관점 검토 — 현 구조를 그대로 제품화할 경우 반복 비용

1. **체크리스트 corpus 수작업 유지**: Azure 변화에 조직이 직접 갱신해야 함
2. **LLM 전판정 드리프트 관리 부재**: eval 없이 판정 안정성 보장 어려움
3. **수제 인증 스택**: Easy Auth 대체 가능
4. **수제 DB 계층(수기 SQL 45개) + 수제 AG-UI 클라이언트**: 소유 코드 부담 증가
5. **자체 agent 런타임 배관**: Foundry hosted agent/evaluation/tracing으로 관리형 이관 가능

---

## 11. 목표 그림 — build vs adopt

원칙: **Microsoft가 유지하는 것은 adopt, 차별화 지점은 build**.

| 계층 | 방향 | 근거 |
|---|---|---|
| **판정 corpus** | **ADOPT**: APRL(KQL pin) + Advisor API + Defender assessments + Azure Policy state | 기준 유지보수 부담을 Microsoft 쪽으로 이관 |
| **판정 실행** | **BUILD (소~중, 미결정)**: ControlDefinition registry + 6-상태 모델(Pass/Fail/Unknown/N:A/Exempted/ManualPending), 기본은 결정론, 매핑 불가만 `agent_assisted` | **제품의 실질 IP ①**. 단 registry 저작·유지 방식은 **§14에서 A/B/C/D 택일** |
| **Evidence** | **BUILD (소)**: snapshot_run/resource_snapshot + provenance | 감사 가능성의 최소 단위 |
| **Terraform 생성·검증 루프** | **BUILD (핵심)**: 생성→fmt/validate→fabricated-credential plan→Checkov→error certificate 재투입→draft PR + semantic 위험 human review | **제품의 실질 IP ②** |
| **Agent 런타임** | **ADOPT**: Foundry hosted agent + middleware + tracing | 챗봇 역할을 실행 런처 중심에서 결과 해석·후속 질의 중심으로 재정의 |
| **Eval** | **ADOPT + BUILD (소)**: Foundry evaluation + golden fixture oracle + CI gate | 모델 드리프트 감지 |
| **인증** | **ADOPT**: Easy Auth + OBO | 수제 MSAL/UAMI 배관 단순화 |
| **UI** | **RESPEC**: 보드/드릴다운 UX 계승, 데이터 계약 재정의 | |

---

## 12. 계승 자산과 이관 대상

**계승:**
- 구독 스코프 격리 모델(OBO∩MI 교집합, 헤더 검증)
- 실행→리소스→검사 3계층 결과 granularity
- 체크리스트 계층 스키마 개념(categories→items→checks)
- UI 흐름(4보드 + 챗 + drill-down)
- 배포 IaC의 거버넌스 대응(PE 전용 등)

**이관/대체:**
- LLM 전판정 엔진 → 결정론 기본 + LLM 보조
- 수작업 YAML 중심 corpus 운영 → 관리형 source + 조직 고유 항목
- 수제 인증·챗·DB 배관 → Easy Auth·Foundry·프레임워크

---

## 13. 남는 불확실성 (축소 없이)

1. **coverage 실측 선행 필요**: ARB 항목 중 결정론 매핑 가능한 비율 미지수
2. **Terraform 루프 한계**: validate/plan/scanner만으로 semantic 안전 보장 불가
3. **Foundry 관리형 종속 반대급부**: region/기능 제약과 플랫폼 의존성
4. **source 편중 보완**: reliability/security/cost/ops 전 영역 밀도 균형 필요

---

## 14. 결정 보류 — 체크리스트 저작·유지 방식 비교 (A/B/C/D)

> 상태: 미결정. coverage 실측(§13-1)과 파일럿 이후 최종 결정.

### 14.1 4안 정의 (canonical·생성·갱신 흐름)

| 안 | canonical | registry 생성 | 갱신 흐름 |
|---|---|---|---|
| **A. 원본 소스 형태** | 자연어 문서(Excel/위키/YAML 자유 기술) | 문서를 LLM이 매 갱신마다 재컴파일(파생 산출물) | 문서 편집 → 전체 재컴파일 |
| **B. 구조화 canonical + 저작 지원** | 구조화 registry 레코드 | 자연어(+구조 힌트) → LLM 초안 → 결정론 게이트(스키마·source 실존·KQL dry-run) → 사람 확정 | 항목 편집 → 항목 재구조화 → 버전 커밋 → 고정 스냅샷 회귀 |
| **C. 필드별 저작 표면 + 개선 플라이휠** | 구조화 registry 레코드(B 동일) | B + 필드별 UI(텍스트/NL, scope picker, source 매핑 브라우저, introspection builder, 파라미터화 KQL 템플릿) | B + 운영 데이터 기반 승격/검토 신호 자동 생산 |
| **D. 자연어 skills 기반 agentic 판정** | skill 문서(체크리스트 직접 해석) | registry 컴파일 없이 agent가 런타임 판정 | 평가마다 재해석/재판정 |

### 14.2 8축 비교표

| 축 | A | B | C | D |
|---|---|---|---|---|
| 운영자 진입장벽 | 최저 | 낮음 | 중간 | 낮음 |
| 판정 재현성/감사성 | 낮음 | 높음 | 높음 | 낮음(전면 적용 시) |
| 깨지기 쉬움 | 높음 | 중간 | 낮음 | 높음 |
| 재사용성/이식성 | 낮음 | 중간 | 높음 | 중간 |
| 구축 비용 | 최저 | 중간 | 최고 | 중간 |
| 유지보수 소유 구조 | LLM 종속 큼 | 게이트/회귀 중심 | 템플릿/플라이휠까지 포함 | eval 인프라 부담 큼 |
| 표현력 확장 방식 | LLM 임의 | custom KQL 예산 | 템플릿 추가 | 프롬프트/skills |
| coverage 개선 경로 | 약함 | 수동 재매핑 | 구조적 플라이휠 | eval 의존 |

### 14.3 D안 장점/단점 대응 (1~3)

| # | 장점(맞는 지점) | 단점(판정 권위로 쓸 때) |
|---|---|---|
| 1 | `agent_assisted` 항목(구조화 어려운 항목)에 자연스러움 | 외부 oracle 부재 시 verdict 자체 신뢰 확보가 어려움 |
| 2 | Terraform 수리 루프처럼 validate/plan/scanner oracle이 있는 영역과 궁합 | 신뢰 보강을 위해 결국 golden fixture/eval 구조가 필요(구조 보존) |
| 3 | 챗봇의 조사/해석 UX에는 유효 | hot path(대량 판정)에서 비용·지연·비결정성 증가 |

위치 정리: D는 판정 corpus의 대체재라기보다 **agent tier 포장 형식**으로 제한 적용이 안전.

### 14.4 공통 전제 (어느 안이든 필요)

- registry **버전 관리**
- 고정 스냅샷 기반 **자동 회귀**(신·구 병렬 실행)
- **diff 기준면 분리**(registry 영향 vs 환경 드리프트)
- 매핑 의미 오류에 대한 **사람 확정 단계 불가결**

### 14.5 각 안의 주요 리스크

- **A**: 비결정 재컴파일로 감사성 저하
- **B**: NL→source 매핑 정확도 미검증
- **C**: 구축 비용 최대, 잘못 설계하면 전부 `agent_assisted`로 우회
- **D**: 판정 전면 적용 시 A와 유사한 신뢰 문제 + eval 비용 증가

### 14.6 결정 시점·판단 재료

1. **coverage 실측**: 관리형 source 매핑률 확인
2. **온보딩 파일럿**: 145+ 항목 기준 저작 시간/수락률/정확도 실측
3. **깔때기 전략 검토**: A(느슨한 import) → B(확정·버전화) → C(플라이휠) 조합 가능성 평가

---

## 참고

- 이슈: `daeungo1/poc-aiops-arb#1`
- 이슈 코멘트 리서치 노트: 0, A, B, C, D, E
