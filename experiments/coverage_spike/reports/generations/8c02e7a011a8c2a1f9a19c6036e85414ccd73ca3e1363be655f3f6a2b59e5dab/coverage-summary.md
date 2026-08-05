# Coverage Spike Summary

## Gate

| Check | Result |
| --- | --- |
| Validation mode | synthetic_fixture |
| Implementation gate | passed |
| Deployment readiness | blocked |
| Internal totals | valid |
| Fixture mismatches | 0 / 12 (0.00%) |

Synthetic fixture evidence validates the implementation gate only; it does not validate live Azure adapters or APIs.

### Deployment Readiness Unmet Conditions

- `live_azure_adapter_api_validation`: Live Azure adapters and API behavior have not been validated.
- `rbac_api_limitations_validated`: Required RBAC and Azure API limitations are not yet documented and validated.
- `human_mapping_verdict_review`: Human review of source mappings and verdicts is pending.
- `ui_contract_approval`: The UI contract has not been approved.

## Control Coverage

| Category | Count | Denominator | Ratio |
| --- | ---: | ---: | ---: |
| Machine-verifiable | 6 | 6 | 100.00% |
| Managed source | 4 | 6 | 66.67% |
| Custom evaluator | 2 | 6 | 33.33% |
| Custom assertion | 6 | 6 | 100.00% |
| Agent-assisted | 0 | 6 | 0.00% |
| Manual | 0 | 6 | 0.00% |

Custom assertion coverage and managed source coverage overlap: a control can execute a local ARM/ARG/storage-service assertion and also carry a corroborating managed-source mapping.

## Evaluator Kinds

| Evaluator kind | Count |
| --- | ---: |
| agent_assisted | 0 |
| custom | 2 |
| managed | 4 |
| manual | 0 |

## Controls

| Control | Evaluator | Machine-verifiable | Custom assertion | Managed source | Sources | Unmapped reason |
| --- | --- | --- | --- | --- | --- | --- |
| storage.blob_soft_delete | custom | yes | yes | no | storage_service:arm.storage_account.blob_service@2023-05-01 (primary, required) | - |
| storage.minimum_tls | custom | yes | yes | no | arm:arm.storage_account.resource@2023-05-01 (primary, required) | - |
| storage.private_endpoint | managed | yes | yes | yes | arg:arg.storage_account.private_endpoints@api-version:2022-10-01;query-sha256:935f008945f1eb4da6130786aa666345e48c290396b045f623b4500f89579668 (primary, required)<br>aprl:synthetic.aprl.storage.private_endpoint@api-version:2022-10-01;query-sha256:1f48146fbb79425920f266f0f2e0fcc31b8224a8d35f7af6bee99aa32b4cf2b6 (corroborating, optional) | - |
| storage.public_network | managed | yes | yes | yes | arm:arm.storage_account.resource@2023-05-01 (primary, required)<br>defender:synthetic.defender.storage.public_network@2020-01-01 (corroborating, optional) | - |
| storage.redundancy | managed | yes | yes | yes | arm:arm.storage_account.resource@2023-05-01 (primary, required)<br>advisor:synthetic.advisor.storage.redundancy@2025-01-01 (corroborating, optional) | - |
| storage.secure_transfer | managed | yes | yes | yes | arm:arm.storage_account.resource@2023-05-01 (primary, required)<br>azure_policy:synthetic.azure_policy.storage.secure_transfer@2019-10-01 (corroborating, optional) | - |

## Fixture Verdicts

| Fixture | Control | State | Reason | Expected | Match |
| --- | --- | --- | --- | --- | --- |
| storage_account_compliant | storage.blob_soft_delete | pass | assertion_matched | pass / assertion_matched | yes |
| storage_account_compliant | storage.minimum_tls | pass | assertion_matched | pass / assertion_matched | yes |
| storage_account_compliant | storage.private_endpoint | pass | assertion_matched | pass / assertion_matched | yes |
| storage_account_compliant | storage.public_network | pass | assertion_matched | pass / assertion_matched | yes |
| storage_account_compliant | storage.redundancy | pass | assertion_matched | pass / assertion_matched | yes |
| storage_account_compliant | storage.secure_transfer | pass | assertion_matched | pass / assertion_matched | yes |
| storage_account_noncompliant | storage.blob_soft_delete | fail | assertion_not_matched | fail / assertion_not_matched | yes |
| storage_account_noncompliant | storage.minimum_tls | fail | assertion_not_matched | fail / assertion_not_matched | yes |
| storage_account_noncompliant | storage.private_endpoint | fail | assertion_not_matched | fail / assertion_not_matched | yes |
| storage_account_noncompliant | storage.public_network | fail | assertion_not_matched | fail / assertion_not_matched | yes |
| storage_account_noncompliant | storage.redundancy | fail | assertion_not_matched | fail / assertion_not_matched | yes |
| storage_account_noncompliant | storage.secure_transfer | fail | assertion_not_matched | fail / assertion_not_matched | yes |
| storage_account_partial | storage.blob_soft_delete | unknown | evidence_missing | not compared | - |
| storage_account_partial | storage.minimum_tls | unknown | evidence_partial | not compared | - |
| storage_account_partial | storage.private_endpoint | unknown | evidence_missing | not compared | - |
| storage_account_partial | storage.public_network | unknown | evidence_partial | not compared | - |
| storage_account_partial | storage.redundancy | unknown | evidence_partial | not compared | - |
| storage_account_partial | storage.secure_transfer | unknown | evidence_partial | not compared | - |

## Verdict Outcomes

| State | Count |
| --- | ---: |
| exempted | 0 |
| fail | 6 |
| manual_pending | 0 |
| not_applicable | 0 |
| pass | 6 |
| unknown | 6 |

Conflict reasons are an orthogonal subset of verdict outcomes: managed-source conflicts 0 / 18 (0.00%); all conflicts 0 / 18 (0.00%).
