# Enterprise AIOps Production Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace LLM-owned Azure compliance verdicts with auditable deterministic evidence evaluation, then expose the new workflow through API v2, Foundry tools, verified Terraform remediation, and an enterprise UI while preserving the PoC rollback path.

**Architecture:** A new `enterprise` package owns versioned controls, evidence, deterministic verdicts, Azure managed-source adapters, persistence, and remediation verification. Existing FastAPI, Agent Framework, React, PostgreSQL, and Terraform deployment surfaces integrate through `/api/v2` and `ENTERPRISE_ASSESSMENT_ENABLED`; existing `/api` behavior remains available until parity is verified.

**Tech Stack:** Python 3.11, dataclasses, PyYAML, FastAPI, aiohttp, Azure Identity, Microsoft Agent Framework, PostgreSQL 16, pytest, React 18, TypeScript, Vite, Terraform.

## Global Constraints

- Compliance verdicts are produced only by deterministic evaluators; LLM output cannot change a verdict.
- Missing, partial, conflicting, throttled, or unauthorized evidence produces `unknown`, never an inferred `fail`.
- Verdict states are exactly `pass`, `fail`, `unknown`, `not_applicable`, `exempted`, and `manual_pending`.
- Every evidence record carries source kind, source reference, source version, observed time, and content hash.
- Azure access uses the existing delegated-user/Managed Identity credential boundaries; no credentials are stored in source or evidence.
- Terraform verification never invokes `terraform apply`; destructive, exposure, identity, and data-tier changes always require human approval.
- Existing endpoints and tables remain available behind the rollback path.
- New Python behavior follows TDD: add one failing test, verify the expected failure, implement minimally, and rerun the focused test.
- Do not commit or push changes unless the user explicitly requests it.

---

## Phase 1: Coverage Spike

### Task 1: Enterprise Domain Contracts

**Files:**
- Create: `backend/enterprise/__init__.py`
- Create: `backend/enterprise/domain.py`
- Create: `backend/tests/enterprise/test_domain.py`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Produces: `VerdictState`, `EvaluatorKind`, `EvidenceRecord`, `ControlDefinition`, `Verdict`, `EvaluationRun`.
- Consumes: Python standard library only.

- [ ] **Step 1: Add a failing domain contract test**

```python
def test_evidence_hash_is_stable_for_key_order():
    first = EvidenceRecord.create("arm", "resource", "2024-01-01", {"a": 1, "b": 2})
    second = EvidenceRecord.create("arm", "resource", "2024-01-01", {"b": 2, "a": 1})
    assert first.content_hash == second.content_hash
```

- [ ] **Step 2: Verify RED**

Run: `cd backend; uv run pytest tests/enterprise/test_domain.py -v`

Expected: FAIL because `enterprise.domain` does not exist.

- [ ] **Step 3: Implement immutable domain contracts**

`EvidenceRecord.create()` serializes payload with sorted keys and computes SHA-256. `ControlDefinition` rejects empty keys, versions, resource types, selectors, and assertions. `Verdict` requires at least one evidence hash for `pass` or `fail`. Add `pytest-asyncio>=0.24.0` to the `dev` extra for adapter and Agent tool tests.

- [ ] **Step 4: Verify GREEN**

Run: `cd backend; uv run pytest tests/enterprise/test_domain.py -v`

Expected: all domain tests pass.

### Task 2: Sample Registry and Deterministic Evaluator

**Files:**
- Create: `experiments/coverage_spike/checklists/azure_storage_production_readiness.yaml`
- Create: `experiments/coverage_spike/mappings/azure_storage_production_readiness.yaml`
- Create: `experiments/coverage_spike/fixtures/storage_account_compliant.json`
- Create: `experiments/coverage_spike/fixtures/storage_account_noncompliant.json`
- Create: `experiments/coverage_spike/fixtures/storage_account_partial.json`
- Create: `experiments/coverage_spike/expected/storage_account_compliant.json`
- Create: `experiments/coverage_spike/expected/storage_account_noncompliant.json`
- Create: `backend/enterprise/registry.py`
- Create: `backend/enterprise/evaluator.py`
- Create: `backend/tests/enterprise/test_registry.py`
- Create: `backend/tests/enterprise/test_evaluator.py`

**Interfaces:**
- Consumes: domain contracts from Task 1 and the existing `ChecklistLoader` schema.
- Produces: `ControlRegistry.load(checklist_path, mapping_path)` and `DeterministicEvaluator.evaluate(control, evidence)`.

- [ ] **Step 1: Add failing registry and evaluator tests**

```python
def test_missing_property_is_unknown(registry, partial_evidence):
    control = registry.get("storage.secure_transfer")
    verdict = DeterministicEvaluator().evaluate(control, [partial_evidence])
    assert verdict.state is VerdictState.UNKNOWN
    assert verdict.reason_code == "evidence_selector_missing"
```

- [ ] **Step 2: Verify RED**

Run: `cd backend; uv run pytest tests/enterprise/test_registry.py tests/enterprise/test_evaluator.py -v`

Expected: FAIL because registry and evaluator modules do not exist.

- [ ] **Step 3: Implement the six Storage controls**

Control keys are:

```text
storage.secure_transfer
storage.minimum_tls
storage.public_network
storage.blob_soft_delete
storage.redundancy
storage.private_endpoint
```

Selectors use dot-separated dictionary paths. Assertions support `equals`, `in`, `not_in`, `greater_than_or_equal`, and `exists`. Unsupported operators and conflicting source values return `unknown` with explicit reason codes.

- [ ] **Step 4: Verify GREEN**

Run: `cd backend; uv run pytest tests/enterprise/test_registry.py tests/enterprise/test_evaluator.py -v`

Expected: all registry and evaluator tests pass.

### Task 3: Coverage Runner and Gate Report

**Files:**
- Create: `backend/enterprise/coverage.py`
- Create: `backend/scripts/run_coverage_spike.py`
- Create: `backend/tests/enterprise/test_coverage.py`
- Create: `experiments/coverage_spike/README.md`
- Generate: `experiments/coverage_spike/reports/current.json`
- Generate: `experiments/coverage_spike/reports/generations/<generation_id>/coverage-summary.json`
- Generate: `experiments/coverage_spike/reports/generations/<generation_id>/coverage-summary.md`

**Interfaces:**
- Consumes: `ControlRegistry` and `DeterministicEvaluator`.
- Produces: `CoverageReport`, `build_coverage_report()`, `read_current_report_bundle()`, and a CLI returning non-zero when fixture verdicts differ from expected outputs or fixture IDs are duplicated.

- [ ] **Step 1: Add a failing coverage test**

```python
def test_coverage_totals_equal_control_count(registry):
    report = build_coverage_report(registry.controls)
    assert report.total_controls == 6
    assert sum(report.evaluator_kind_counts.values()) == 6
```

- [ ] **Step 2: Verify RED**

Run: `cd backend; uv run pytest tests/enterprise/test_coverage.py -v`

Expected: FAIL because coverage reporting does not exist.

- [ ] **Step 3: Implement deterministic report generation**

The JSON report contains counts and ratios for machine-verifiable, managed-source, custom, agent-assisted, manual, unknown, and conflicts. The Markdown report lists every control, source mapping, fixture verdict, and unmapped reason.

Canonical JSON and Markdown content hashes determine a stable `generation_id`. Both files are fsynced into an immutable `reports/generations/<generation_id>/` bundle before one small `reports/current.json` manifest is atomically replaced. That manifest is the only canonical publication boundary and carries the exact relative path and SHA-256 hash of each report. Existing matching generations are reused; conflicting content is rejected. Fixture IDs must be globally unique across fixture files, with duplicates recorded as structured implementation-gate errors.

Manifest temporary files live only under `reports/.staging`. Publish and read startup scavenge stale staging entries without following symlinks or reparse points. Immediate cleanup is best effort; a path retained by a Windows handle remains recoverable in `.staging` and is retried on the next run. Publishing fails closed if stale cleanup still cannot complete. Since the reader API has no structured cleanup-warning result, reading also fails closed and propagates the cleanup exception.

- [ ] **Step 4: Verify the Phase 1 gate**

Run: `cd backend; uv run pytest tests/enterprise -v; uv run python scripts/run_coverage_spike.py`

Expected: all tests pass, `current.json` resolves to one hash-validated immutable report bundle, fixture mismatches are zero, and the command exits 0.

- [ ] **Step 5: Create the implementation branch**

Run: `& 'C:\Program Files\Git\cmd\git.exe' switch -c prod-redesign`

Expected: current branch is `prod-redesign`; uncommitted Phase 1 artifacts remain in the working tree.

---

## Phase 2: Enterprise Implementation

### Task 4: Azure Managed-Source Adapters

**Files:**
- Create: `backend/enterprise/adapters/__init__.py`
- Create: `backend/enterprise/adapters/base.py`
- Create: `backend/enterprise/adapters/arm.py`
- Create: `backend/enterprise/adapters/aprl.py`
- Create: `backend/enterprise/adapters/advisor.py`
- Create: `backend/enterprise/adapters/defender.py`
- Create: `backend/enterprise/adapters/policy.py`
- Create: `backend/tests/enterprise/adapters/test_arm.py`
- Create: `backend/tests/enterprise/adapters/test_managed_sources.py`

**Interfaces:**
- Consumes: Azure `TokenCredential`, subscription scope, and injected async HTTP transport.
- Produces: `EvidenceAdapter.collect(context) -> list[EvidenceRecord]` implementations and `CollectionResult` with partial failures.

- [ ] **Step 1: Add failing adapter contract tests**

```python
async def test_unauthorized_source_is_partial_unknown(fake_transport):
    fake_transport.respond(403, {"error": {"code": "AuthorizationFailed"}})
    result = await AdvisorAdapter(fake_transport).collect(context)
    assert result.evidence == []
    assert result.failures[0].reason_code == "source_unauthorized"
```

- [ ] **Step 2: Verify RED**

Run: `cd backend; uv run pytest tests/enterprise/adapters -v`

Expected: FAIL because adapters do not exist.

- [ ] **Step 3: Implement typed async adapters**

All ARM requests use injected transport, bounded timeout, pagination, retry metadata, and API versions declared in one adapter constant. Responses are normalized before `EvidenceRecord.create()`; access tokens and authorization headers are never persisted or logged.

- [ ] **Step 4: Verify GREEN**

Run: `cd backend; uv run pytest tests/enterprise/adapters -v`

Expected: success, pagination and 403/429/partial-response tests pass.

### Task 5: Enterprise Persistence and API v2

**Files:**
- Modify: `backend/scripts/01_schema.sql`
- Create: `backend/enterprise/repository.py`
- Create: `backend/enterprise/postgres_repository.py`
- Create: `backend/enterprise/service.py`
- Create: `backend/enterprise/api.py`
- Modify: `backend/agui_server.py`
- Create: `backend/tests/enterprise/test_repository.py`
- Create: `backend/tests/enterprise/test_service.py`
- Create: `backend/tests/enterprise/test_api.py`

**Interfaces:**
- Consumes: registry, adapters, evaluator, existing Azure session headers.
- Produces: `EnterpriseAssessmentService`, repository protocol, PostgreSQL implementation, and `/api/v2/controls`, `/api/v2/assessments`, `/api/v2/assessments/{run_id}`, `/api/v2/findings/{finding_id}`.

- [ ] **Step 1: Add failing service and API tests**

```python
def test_v2_disabled_returns_404(client, monkeypatch):
    monkeypatch.setenv("ENTERPRISE_ASSESSMENT_ENABLED", "false")
    assert client.get("/api/v2/controls").status_code == 404
```

- [ ] **Step 2: Verify RED**

Run: `cd backend; uv run pytest tests/enterprise/test_repository.py tests/enterprise/test_service.py tests/enterprise/test_api.py -v`

Expected: FAIL because persistence, service, and router do not exist.

- [ ] **Step 3: Add idempotent enterprise tables**

Tables are `control_definitions`, `snapshot_runs`, `evidence_records`, `enterprise_evaluation_runs`, `enterprise_verdicts`, `remediation_runs`, and `remediation_artifacts`. Foreign keys use cascade only for run-owned records; control versions remain immutable.

- [ ] **Step 4: Implement service and guarded router**

The router validates the same `X-Azure-Tenant-Id` and `X-Azure-Subscription-Id` scope used by existing endpoints. `ENTERPRISE_ASSESSMENT_ENABLED=false` leaves `/api/v2` unavailable. API responses expose provenance and reason codes but never raw authorization data.

- [ ] **Step 5: Verify GREEN**

Run: `cd backend; uv run pytest tests/enterprise/test_repository.py tests/enterprise/test_service.py tests/enterprise/test_api.py -v`

Expected: all persistence, service, and API tests pass.

### Task 6: Foundry Agent Evidence Tools

**Files:**
- Create: `backend/chat/tools/enterprise.py`
- Modify: `backend/chat/tools/__init__.py`
- Modify: `backend/chat/agent.py`
- Create: `backend/tests/chat/test_enterprise_tools.py`

**Interfaces:**
- Consumes: `EnterpriseAssessmentService` and current Azure/chat session context.
- Produces: `run_enterprise_assessment`, `get_enterprise_finding`, and `explain_enterprise_evidence` Agent Framework tools.

- [ ] **Step 1: Add a failing tool-boundary test**

```python
async def test_explanation_cannot_override_verdict(fake_service):
    result = await explain_enterprise_evidence(finding_id="finding-1")
    assert result["verdict"] == "fail"
    assert "verdict_override" not in result
```

- [ ] **Step 2: Verify RED**

Run: `cd backend; uv run pytest tests/chat/test_enterprise_tools.py -v`

Expected: FAIL because enterprise tools do not exist.

- [ ] **Step 3: Implement read/orchestrate-only tools**

Tool schemas use stable identifiers and structured dictionaries. System instructions explicitly state that deterministic verdicts are authoritative, source citations are mandatory, and the agent must abstain when the service returns `unknown` or `manual_pending`.

- [ ] **Step 4: Verify GREEN**

Run: `cd backend; uv run pytest tests/chat/test_enterprise_tools.py -v`

Expected: all enterprise tool tests pass.

### Task 7: Verified Terraform Remediation

**Files:**
- Create: `backend/enterprise/remediation.py`
- Create: `backend/enterprise/terraform_verifier.py`
- Modify: `backend/enterprise/api.py`
- Create: `backend/tests/enterprise/test_remediation.py`
- Create: `backend/tests/enterprise/test_terraform_verifier.py`

**Interfaces:**
- Consumes: one persisted failing verdict and generated Terraform artifacts.
- Produces: `RemediationService.create_draft(finding_id)`, `TerraformVerifier.verify(directory)`, verifier events, risk flags, and draft-only artifacts.

- [ ] **Step 1: Add failing verifier tests**

```python
def test_verifier_never_accepts_apply_command(fake_runner):
    verifier = TerraformVerifier(fake_runner)
    verifier.verify(Path("fixture"))
    assert all("apply" not in command for command in fake_runner.commands)
```

- [ ] **Step 2: Verify RED**

Run: `cd backend; uv run pytest tests/enterprise/test_remediation.py tests/enterprise/test_terraform_verifier.py -v`

Expected: FAIL because remediation modules do not exist.

- [ ] **Step 3: Implement verifier stages**

Stages are `terraform fmt -check`, `terraform init -backend=false`, `terraform validate`, optional isolated `terraform plan`, and optional security scanner. Every subprocess uses an argument list, fixed working directory, sanitized environment, timeout, captured output, and no shell. Missing executables produce a rejected verifier event rather than a false success.

- [ ] **Step 4: Add API endpoints**

Add `POST /api/v2/findings/{finding_id}/remediations` and `GET /api/v2/remediations/{run_id}`. Only one finding is accepted per remediation run. Responses include `draft`, `verification_status`, `risk_flags`, and artifact download metadata.

- [ ] **Step 5: Verify GREEN**

Run: `cd backend; uv run pytest tests/enterprise/test_remediation.py tests/enterprise/test_terraform_verifier.py tests/enterprise/test_api.py -v`

Expected: all remediation, verifier, and API tests pass.

### Task 8: Enterprise UI and Compatibility Path

**Files:**
- Create: `frontend/src/lib/enterpriseApi.ts`
- Create: `frontend/src/components/EnterpriseAssessmentBoard.tsx`
- Create: `frontend/src/components/EnterpriseEvidencePanel.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/Sidebar.tsx`
- Create: `frontend/src/components/EnterpriseAssessmentBoard.test.tsx`
- Modify: `frontend/package.json`

**Interfaces:**
- Consumes: API v2 controls, runs, verdicts, evidence, and remediation endpoints.
- Produces: `enterprise` page with run status, six-state filtering, evidence provenance, conflict/unknown presentation, and finding-level remediation action.

- [ ] **Step 1: Add failing component tests**

```tsx
it('renders unknown separately from fail', async () => {
  render(<EnterpriseAssessmentBoard api={fakeApiWithUnknownVerdict} />);
  expect(await screen.findByText('Unknown')).toBeInTheDocument();
  expect(screen.queryByText('Fail 1')).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Verify RED**

Run: `cd frontend; npm test -- --run`

Expected: FAIL because the component and test runner configuration do not exist.

- [ ] **Step 3: Implement the enterprise page**

Use the existing sidebar, headers, loading conventions, and Azure session context. Do not replace the current assessment pages. Evidence rows show source, observed time, version, hash prefix, selector, actual value, expected value, and reason code. Remediation remains disabled for non-fail verdicts and for unverified findings. Add `vitest`, `jsdom`, `@testing-library/react`, `@testing-library/jest-dom`, and `@testing-library/user-event` as dev dependencies and configure the `test` script as `vitest`.

- [ ] **Step 4: Verify GREEN and build**

Run: `cd frontend; npm test -- --run; npm run type-check; npm run build`

Expected: component tests, TypeScript, and production build succeed.

### Task 9: Deployment Configuration and Operations

**Files:**
- Modify: `terraform/variables.tf`
- Modify: `terraform/main.tf`
- Modify: `terraform/modules/app_service/variables.tf`
- Modify: `terraform/modules/app_service/main.tf`
- Modify: `.env.template`
- Modify: `README.md`
- Create: `docs/enterprise-operations.md`

**Interfaces:**
- Consumes: existing App Service module and environment configuration.
- Produces: explicit `enterprise_assessment_enabled` setting, documented RBAC/API requirements, rollout, rollback, diagnostics, and migration procedure.

- [ ] **Step 1: Add the feature flag wiring**

`enterprise_assessment_enabled` defaults to `false`. Terraform passes `ENTERPRISE_ASSESSMENT_ENABLED` to the backend App Service. No secret or token is added to Terraform state beyond the repository's existing secret handling.

- [ ] **Step 2: Document RBAC and rollout**

Document required read access for Resource Graph, Advisor, Defender assessments, and Policy Insights; PostgreSQL migration behavior; feature-flag activation; API health checks; rollback; and evidence retention.

- [ ] **Step 3: Validate Terraform formatting**

Run: `terraform fmt -check -recursive terraform`

Expected: exit 0. If Terraform is unavailable, run formatting diagnostics available in the editor and report the unavailable executable explicitly.

### Task 10: Whole-System Verification and Gate Evidence

**Files:**
- Generate: `experiments/coverage_spike/reports/implementation-verification.md`

**Interfaces:**
- Consumes: all previous tasks.
- Produces: reproducible verification evidence and residual-risk statement.

- [ ] **Step 1: Run backend checks**

Run: `cd backend; uv run pytest -v; uv run ruff check .`

Expected: all tests pass and Ruff reports no errors introduced by this work.

- [ ] **Step 2: Run the coverage gate again**

Run: `cd backend; uv run python scripts/run_coverage_spike.py`

Expected: zero fixture mismatches and a regenerated `current.json` pointing to the deterministic immutable JSON/Markdown generation bundle.

- [ ] **Step 3: Run frontend checks**

Run: `cd frontend; npm test -- --run; npm run type-check; npm run build`

Expected: tests, type-check, and build pass.

- [ ] **Step 4: Run repository checks**

Run: `& 'C:\Program Files\Git\cmd\git.exe' diff --check`

Expected: exit 0.

- [ ] **Step 5: Record evidence**

Write exact commands, exit codes, pass/fail counts, unavailable external tools, and remaining live-Azure validation requirements to `implementation-verification.md`. Do not claim live Azure validation unless a real subscription run was executed.