from __future__ import annotations

import base64
import inspect
import json
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from enterprise.api import (
    AsyncStaticTokenCredential,
    EnterpriseAssessmentRequest,
    create_enterprise_router,
    enterprise_assessment_enabled,
)
from enterprise.repository import FindingRecord, RunRecord


def _jwt(aud: str) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode("utf-8")).decode("ascii").rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps({"aud": aud, "exp": 4102444800}).encode("utf-8")).decode("ascii").rstrip("=")
    return f"{header}.{payload}."


def _jwt_with_exp(aud: str, exp: int) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode("utf-8")).decode("ascii").rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps({"aud": aud, "exp": exp}).encode("utf-8")).decode("ascii").rstrip("=")
    return f"{header}.{payload}."


def _scope_validator(subscription_id: str | None, tenant_id: str | None) -> tuple[str, str]:
    if not subscription_id:
        raise HTTPException(status_code=400, detail="missing subscription")
    if tenant_id == "bad-tenant":
        raise HTTPException(status_code=400, detail="invalid tenant")
    return tenant_id or "tenant-a", subscription_id


class FakeService:
    def __init__(self):
        self.created: list[EnterpriseAssessmentRequest] = []

    async def list_controls(self):
        return [{"control_key": "storage.secure_transfer", "version": "1.0.0"}]

    async def run_assessment(self, tenant_id, subscription_id, resource_ids=None, control_keys=None):
        self.created.append(
            EnterpriseAssessmentRequest(
                resource_ids=list(resource_ids or []),
                control_keys=list(control_keys or []),
            )
        )
        return "run-123"

    async def get_run(self, run_id, subscription_id):
        if run_id == "missing" or subscription_id != "sub-a":
            return None
        return RunRecord(
            run_id=run_id,
            tenant_id="tenant-a",
            subscription_id="sub-a",
            state="completed",
            requested_resource_ids=(),
            control_keys=("storage.secure_transfer",),
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
            reason_code=None,
            verdict_counts={
                "pass": 1,
                "fail": 0,
                "unknown": 0,
                "not_applicable": 0,
                "exempted": 0,
                "manual_pending": 0,
            },
            evidence_provenance=(),
            findings=(),
            collection_failures=(),
        )

    async def get_finding(self, finding_id, subscription_id):
        if finding_id == "missing" or subscription_id != "sub-a":
            return None
        return FindingRecord(
            finding_id=finding_id,
            run_id="run-123",
            subscription_id="sub-a",
            resource_id="/subscriptions/sub-a/resourceGroups/rg/providers/Microsoft.Storage/storageAccounts/sa",
            control_key="storage.secure_transfer",
            verdict_state="pass",
            reason_code="assertion_matched",
            evidence_hashes=("a" * 64,),
            provenance=(),
        )


class SignatureSpoofedProvider:
    __signature__ = inspect.Signature()

    def __init__(self, service: FakeService):
        self.service = service
        self.credential = None

    def __call__(self, credential):
        self.credential = credential
        return self.service


def _build_client(provider) -> TestClient:
    app = FastAPI()
    app.include_router(create_enterprise_router(provider, _scope_validator))
    return TestClient(app)


def test_feature_flag_parser_true_values(monkeypatch):
    for value in ("true", "TRUE", "1", "yes", "Yes"):
        monkeypatch.setenv("ENTERPRISE_ASSESSMENT_ENABLED", value)
        assert enterprise_assessment_enabled() is True


def test_feature_flag_parser_false_values(monkeypatch):
    for value in ("", "false", "0", "no", "random"):
        monkeypatch.setenv("ENTERPRISE_ASSESSMENT_ENABLED", value)
        assert enterprise_assessment_enabled() is False


def test_router_required_headers_and_scope_enforcement():
    client = _build_client(SignatureSpoofedProvider(FakeService()))

    missing = client.get("/api/v2/assessments/run-123")
    assert missing.status_code == 400

    scoped = client.get(
        "/api/v2/assessments/run-123",
        headers={"X-Azure-Subscription-Id": "sub-a", "X-Azure-Tenant-Id": "tenant-a"},
    )
    assert scoped.status_code == 200

    cross_sub = client.get(
        "/api/v2/assessments/run-123",
        headers={"X-Azure-Subscription-Id": "sub-b", "X-Azure-Tenant-Id": "tenant-a"},
    )
    assert cross_sub.status_code == 404


def test_router_endpoints_create_run_and_lookup_finding():
    provider = SignatureSpoofedProvider(FakeService())
    client = _build_client(provider)
    headers = {
        "X-Azure-Subscription-Id": "sub-a",
        "X-Azure-Tenant-Id": "tenant-a",
        "Authorization": f"Bearer {_jwt('https://management.azure.com')}"
    }

    controls = client.get("/api/v2/controls")
    assert controls.status_code == 200
    assert controls.json()[0]["control_key"] == "storage.secure_transfer"

    created = client.post(
        "/api/v2/assessments",
        json={"resource_ids": [], "control_keys": []},
        headers=headers,
    )
    assert created.status_code == 202
    assert created.json()["run_id"] == "run-123"
    assert provider.credential is not None
    assert provider.credential.__class__.__name__ == "AsyncStaticTokenCredential"

    finding = client.get("/api/v2/findings/f-1", headers=headers)
    assert finding.status_code == 200
    payload = finding.json()
    assert "authorization" not in json.dumps(payload).lower()
    assert "token" not in json.dumps(payload).lower()


def test_router_rejects_post_without_cookie_or_bearer():
    provider = SignatureSpoofedProvider(FakeService())
    client = _build_client(provider)

    response = client.post(
        "/api/v2/assessments",
        json={"resource_ids": [], "control_keys": []},
        headers={
            "X-Azure-Subscription-Id": "sub-a",
            "X-Azure-Tenant-Id": "tenant-a",
        },
    )

    assert response.status_code == 401
    assert provider.credential is None


@pytest.mark.asyncio
async def test_async_static_token_credential_accepts_exact_arm_scope_and_audience():
    credential = AsyncStaticTokenCredential(_jwt("https://management.azure.com"))
    token = await credential.get_token("https://management.azure.com/.default")
    assert token.token


@pytest.mark.asyncio
async def test_async_static_token_credential_accepts_legacy_core_scope_and_audience():
    credential = AsyncStaticTokenCredential(_jwt("https://management.core.windows.net/"))
    token = await credential.get_token("https://management.core.windows.net/.default")
    assert token.token


@pytest.mark.asyncio
async def test_async_static_token_credential_rejects_confusable_arm_scope():
    credential = AsyncStaticTokenCredential(_jwt("https://management.azure.com"))
    with pytest.raises(Exception):
        await credential.get_token("https://management.azure.com.evil.com/.default")


@pytest.mark.asyncio
async def test_async_static_token_credential_rejects_confusable_audience():
    credential = AsyncStaticTokenCredential(_jwt("https://management.azure.com.evil.com/"))
    with pytest.raises(Exception):
        await credential.get_token("https://management.azure.com/.default")


@pytest.mark.asyncio
async def test_async_static_token_credential_rejects_expired_token():
    expired = int(datetime.now(UTC).timestamp()) - 60
    credential = AsyncStaticTokenCredential(_jwt_with_exp("https://management.azure.com", expired))
    with pytest.raises(Exception):
        await credential.get_token("https://management.azure.com/.default")


def test_disabled_router_returns_404_when_not_included():
    app = FastAPI()
    client = TestClient(app)
    assert client.get("/api/v2/controls").status_code == 404
