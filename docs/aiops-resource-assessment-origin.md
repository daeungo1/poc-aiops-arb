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

## 8. 제품화 방향 요약

PoC의 핵심 자산은 도메인 학습(스코프 모델·UX 흐름·정리 문서)이며,
프로덕션은 감사 가능한 판정을 위해 코어를 재설계하는 방향이 적합.

---

## 9. 코드 자산 분해(실측)

- 판정·생성 코어: `assessment_engine.py` + `terraform_generator.py` 중심
- 수제 배관: 인증, DB SQL, AG-UI 배관
- UI 보드·차트·팝업: UX 자산은 계승 가능
- 배포 Terraform IaC: 거버넌스 대응 노하우 재사용 가치

---

## 10. 유지보수 관점 검토

- 체크리스트 수작업 유지 비용 큼
- LLM 전판정 드리프트 관리 부재
- 인증/DB/agent 배관의 관리형 대체 가능성 높음

---

## 11. 목표 그림 (build vs adopt)

- 판정 corpus: APRL/Advisor/Defender/Policy 등 관리형 source 우선
- 판정 실행: 결정론 기본 + LLM 보조
- Evidence: snapshot/provenance 축 구축
- Terraform: 생성→검증→재투입→draft PR의 폐루프
- Agent/Eval/Auth: Foundry/Easy Auth 등 관리형 채택

---

## 12. 계승 자산과 이관 대상

계승:
- 구독 스코프 격리 모델
- 실행→리소스→검사 3계층 결과 개념
- UI 흐름, 배포 IaC 운영 노하우

이관/대체:
- LLM 전판정 엔진
- 수작업 YAML corpus 중심 운영
- 수제 인증·챗·DB 배관

---

## 13. 남는 불확실성

1. 관리형 source 기반 coverage 실측 필요
2. Terraform semantic 안전성은 human review 필요
3. Foundry 관리형 종속의 제약 평가 필요
4. reliability/security/cost/ops 전 영역 커버리지 균형 필요

---

## 14. 결정 보류 항목

체크리스트 저작·유지 방식(A/B/C/D안)과 온보딩 실험을 통해,
registry 운영 방식과 단계적 조합 전략을 확정한다.

---

## 참고

- 이슈: `daeungo1/poc-aiops-arb#1`
- 이슈 코멘트 리서치 노트: 0, A, B, C, D, E

