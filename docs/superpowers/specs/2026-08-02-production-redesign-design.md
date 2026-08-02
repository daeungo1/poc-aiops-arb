# Production Redesign Design

## 1. 목적

현재 PoC의 사용자 흐름을 계승하되, LLM 단독 판정을 감사 가능한 결정론 판정으로 대체한다. 먼저 Azure Storage Account용 샘플 체크리스트로 관리형 판정 원천의 coverage와 실행 가능성을 측정하고, 검증된 결과를 기준으로 프로덕션 백엔드를 새로 구현한다.

작업은 다음 두 branch로 분리한다.

- `prod-redesign/coverage-spike`: 샘플 corpus, adapter 실험, coverage 보고서, 목표 계약을 완성한다.
- `prod-redesign`: 실험 결과가 진행 게이트를 통과한 후 생성하고 프로덕션 구현을 수행한다.

## 2. 설계 원칙

1. Compliance verdict는 결정론 evaluator만 확정한다.
2. LLM은 근거 설명, 후속 조사, `agent_assisted` 항목, remediation 초안에만 사용한다.
3. 모든 verdict는 관측한 evidence, 원천, API 또는 query 버전, 수집 시각을 포함한다.
4. evidence가 없거나 불완전하면 `Fail`로 추론하지 않고 `Unknown`으로 판정한다.
5. Terraform은 검증을 통과해도 자동 적용하지 않으며 draft PR과 사람 승인을 거친다.
6. 기존 PoC는 신규 경로의 동등성이 검증될 때까지 제거하지 않는다.

## 3. Phase 1 범위

### 3.1 샘플 체크리스트

샘플은 `Azure Storage Production Readiness` 한 개로 제한한다. 기존 로더와 호환되는 `metadata -> categories -> items -> checks[].azure_check` 구조를 유지한다.

포함할 control은 다음과 같다.

| Control | 목적 | 우선 판정 원천 | 보조 원천 |
|---|---|---|---|
| Secure transfer required | HTTPS 강제 | ARM/ARG property | Defender, Azure Policy |
| Minimum TLS 1.2 | 구형 TLS 차단 | ARM/ARG property | Defender, Azure Policy |
| Public network access restricted | 공용 노출 제한 | ARM/ARG + network rules | Defender, Azure Policy |
| Blob soft delete enabled | 삭제 복구 | Storage service property | APRL candidate |
| Zone 또는 geo redundancy | 가용성 확보 | SKU property | APRL, Advisor |
| Private endpoint configured | 사설 연결 | ARG relationship query | APRL, Defender |

이 control은 조직의 최종 정책이 아니라 adapter와 판정 계약을 검증하기 위한 샘플이다. 조직 corpus가 추가되면 동일한 `ControlDefinition` 계약으로 교체하거나 확장한다.

### 3.2 실험 artifact

Phase 1은 다음 artifact를 생성한다.

```text
experiments/coverage_spike/
  README.md
  checklists/azure_storage_production_readiness.yaml
  mappings/azure_storage_production_readiness.yaml
  fixtures/storage_account_compliant.json
  fixtures/storage_account_noncompliant.json
  expected/storage_account_compliant.json
  expected/storage_account_noncompliant.json
  reports/coverage-summary.md
```

샘플 fixture는 비밀값과 실제 구독 식별자를 포함하지 않는 합성 ARM/ARG 응답을 사용한다.

### 3.3 ControlDefinition 계약

각 control mapping은 다음 정보를 가진다.

- stable control key와 version
- 적용 resource type과 scope 조건
- `managed`, `custom`, `agent_assisted`, `manual` evaluator kind
- APRL query, Advisor recommendation, Defender assessment, Azure Policy 또는 ARM property 원천
- 원천 artifact의 version, commit SHA, API version 또는 query hash
- evidence selector와 assertion
- Pass, Fail, Unknown, NotApplicable, Exempted, ManualPending 상태 규칙
- remediation 설명과 검증 규칙

기존 체크리스트 YAML은 사용자용 질문과 계층 구조를 담당하고, mapping artifact가 실행 가능한 판정 계약을 담당한다.

## 4. Coverage 측정

총 control 수를 `N`이라고 할 때 다음 값을 함께 보고한다.

- Machine-verifiable coverage: 실행 가능한 결정론 원천이 있는 control 수 / `N`
- Managed-source coverage: APRL, Advisor, Defender 또는 Azure Policy에 매핑된 control 수 / `N`
- Custom-evaluator coverage: 자체 ARM/ARG assertion이 필요한 control 수 / `N`
- Agent-assisted ratio: 결정론 판정을 확정할 수 없어 LLM 보조가 필요한 control 수 / `N`
- Manual ratio: 사람의 확인만 가능한 control 수 / `N`
- Unknown rate: fixture 실행 결과 evidence 부족으로 `Unknown`이 된 판정 수 / 전체 판정 수
- Conflict count: 둘 이상의 관리형 원천이 서로 다른 상태를 반환한 control 수

비율 하나로 진행 여부를 결정하지 않는다. 각 미매핑 control의 이유, 필요한 권한, 예상 운영 비용을 함께 검토한다.

## 5. 실험 데이터 흐름

```text
Checklist YAML
  -> ControlDefinition mapping
  -> fixture 또는 Azure collector
  -> normalized EvidenceRecord
  -> deterministic evaluator
  -> Verdict
  -> coverage/conflict report
```

Phase 1의 기본 검증은 합성 fixture로 수행한다. 실제 Azure 호출은 명시적인 환경 설정과 권한이 제공된 경우에만 별도 integration test로 실행한다.

## 6. 오류 처리

- API 권한 부족, throttling, partial response는 `Unknown`과 구조화된 reason code로 기록한다.
- source artifact version이 없거나 mapping assertion이 유효하지 않으면 해당 control 실행을 거부한다.
- 여러 원천의 결과가 충돌하면 자동으로 한쪽을 선택하지 않고 `Unknown`과 conflict evidence를 반환한다.
- 지원하지 않는 resource type은 `NotApplicable`로 처리한다.
- exemption은 근거 ID와 유효 기간이 확인된 경우에만 `Exempted`로 처리한다.
- LLM 오류는 결정론 verdict를 변경하지 않는다.

## 7. Phase 1 테스트

1. 기존 `ChecklistLoader`가 샘플 YAML을 파싱하는지 확인한다.
2. 모든 mapping이 유효한 checklist control을 참조하는지 검증한다.
3. compliant fixture가 예상 Pass 결과를 생성하는지 검증한다.
4. noncompliant fixture가 예상 Fail 결과를 생성하는지 검증한다.
5. 속성이 누락된 fixture가 Fail이 아니라 Unknown을 생성하는지 검증한다.
6. 충돌 원천 입력이 conflict reason을 생성하는지 검증한다.
7. coverage 보고서의 분자 합계와 전체 control 수를 검증한다.

## 8. Phase 2 진입 게이트

다음 조건을 모두 충족한 후 `prod-redesign` branch를 생성한다.

1. 샘플 control 전체에 evaluator kind와 미매핑 이유가 기록돼 있다.
2. fixture 기반 expected verdict 테스트가 통과한다.
3. `Unknown`, conflict, partial failure가 Fail과 구분된다.
4. 관리형 원천별 권한과 API 제한이 문서화돼 있다.
5. 사람이 샘플 mapping과 verdict를 검토했다.
6. 목표 데이터 계약과 기존 UI로의 변환 경로가 승인됐다.

## 9. Phase 2 목표 아키텍처

프로덕션 백엔드는 다음 경계로 구성한다.

- Control Registry: versioned control과 source mapping 관리
- Evidence Collectors: ARG/ARM, APRL, Advisor, Defender, Azure Policy adapter
- Evidence Store: snapshot, provenance, content hash, partial failure 저장
- Evaluation Engine: 결정론 상태 계산과 conflict 처리
- Agent Tier: 결과 설명, 근거 인용, 후속 조사, remediation orchestration
- Remediation Engine: finding 단위 Terraform 생성과 검증
- API v2: 실행, 리소스, finding, evidence, remediation 계약 제공
- Compatibility Adapter: 신규 결과를 기존 UI가 소비할 수 있는 형태로 변환

## 10. Terraform 검증 흐름

```text
Finding
  -> Terraform draft
  -> terraform fmt
  -> terraform validate
  -> 격리된 plan
  -> policy/security scan
  -> verifier feedback 기반 수정
  -> draft PR
  -> human approval
```

자격증명 생성, 실제 apply, 파괴적 변경 승인은 자동화 범위에서 제외한다. 삭제, 공용 노출, 권한 확대, 데이터 계층 변경은 별도 위험 표시와 사람 승인을 요구한다.

## 11. 마이그레이션과 롤백

- 신규 API는 `/api/v2` 또는 비활성 feature flag 아래 추가한다.
- 동일 입력에 대해 기존 LLM 결과와 신규 verdict를 병렬 생성하되 신규 결과는 초기에는 사용자 점수에 반영하지 않는다.
- 비교 결과와 사람이 승인한 golden fixture가 안정되면 UI 화면을 순차 전환한다.
- 기존 API와 테이블은 기능 동등성 확인 전까지 유지한다.
- 신규 경로에 문제가 있으면 feature flag를 끄고 기존 경로로 즉시 복귀한다.

## 12. 완료 기준

Phase 1 완료는 샘플 파일 생성만을 의미하지 않는다. 파싱, mapping 검증, expected verdict, coverage 보고서가 재현 가능한 명령으로 실행되고 모두 통과해야 한다.

Phase 2 완료는 신규 판정과 remediation 흐름이 기존 핵심 사용자 여정을 대체하고, 결정론 판정·근거 추적·Terraform 검증·사람 승인·롤백 경로가 자동 테스트로 검증된 상태를 의미한다.