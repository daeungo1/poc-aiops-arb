from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from enterprise.adapters.arm import (
    ARM_STORAGE_API_VERSION,
    ArmStorageAccountAdapter,
)
from enterprise.adapters.base import (
    CollectionContext,
    CredentialError,
    HttpResponse,
    HttpTransportError,
    MalformedJsonError,
)
from enterprise.domain import EvidenceRecord, EvidenceStatus, VerdictState
from enterprise.evaluator import DeterministicEvaluator
from enterprise.registry import ControlRegistry

from .conftest import RESOURCE_ID, SUBSCRIPTION_ID, TENANT_ID, FakeTransport


ROOT = Path(__file__).resolve().parents[4]
SPIKE_ROOT = ROOT / "experiments/coverage_spike"


def _account(resource_id=RESOURCE_ID):
    return {
        "id": resource_id.lower(),
        "type": "Microsoft.Storage/storageAccounts",
        "properties": {
            "supportsHttpsTrafficOnly": True,
            "minimumTlsVersion": "TLS1_2",
            "publicNetworkAccess": "Disabled",
        },
        "sku": {"name": "Standard_ZRS"},
    }


def _blob_service(resource_id=RESOURCE_ID):
    return {
        "id": f"{resource_id}/blobServices/default",
        "type": "Microsoft.Storage/storageAccounts/blobServices",
        "properties": {"deleteRetentionPolicy": {"enabled": True, "days": 14}},
    }


@pytest.mark.asyncio
async def test_arm_collects_account_and_blob_details_with_mapping_provenance(context):
    transport = FakeTransport(
        HttpResponse(200, _account(), {}),
        HttpResponse(200, _blob_service(), {}),
    )

    result = await ArmStorageAccountAdapter(transport, resource_detail="all").collect(context)

    assert result.failures == ()
    assert result.partial is False
    assert len(result.evidence) == 2
    account, blob = result.evidence
    assert account.resource_id == RESOURCE_ID
    assert account.source_kind == "arm"
    assert account.source_reference == "arm.storage_account.resource"
    assert account.source_version == ARM_STORAGE_API_VERSION == "2023-05-01"
    assert account.status is EvidenceStatus.COMPLETE
    assert blob.resource_id == RESOURCE_ID
    assert blob.source_kind == "storage_service"
    assert blob.source_reference == "arm.storage_account.blob_service"
    assert blob.payload["resource_type"] == "Microsoft.Storage/storageAccounts"
    assert blob.status is EvidenceStatus.COMPLETE
    assert all(
        f"api-version={record.source_version}" in request["url"]
        for record, request in zip(result.evidence, transport.requests, strict=True)
    )
    assert all("Authorization" not in repr(request) for request in transport.requests)


@pytest.mark.asyncio
async def test_arm_account_mode_only_collects_account_request_and_emits_arm_record(context):
    transport = FakeTransport(HttpResponse(200, _account(), {}))

    result = await ArmStorageAccountAdapter(transport, resource_detail="account").collect(context)

    assert len(transport.requests) == 1
    assert result.failures == ()
    assert result.partial is False
    assert len(result.evidence) == 1
    assert result.evidence[0].source_kind == "arm"
    assert result.evidence[0].source_reference == "arm.storage_account.resource"


@pytest.mark.asyncio
async def test_arm_blob_service_mode_only_collects_blob_request_and_emits_storage_service_record(context):
    transport = FakeTransport(HttpResponse(200, _blob_service(), {}))

    result = await ArmStorageAccountAdapter(transport, resource_detail="blob_service").collect(context)

    assert len(transport.requests) == 1
    assert result.failures == ()
    assert result.partial is False
    assert len(result.evidence) == 1
    assert result.evidence[0].source_kind == "storage_service"
    assert result.evidence[0].source_reference == "arm.storage_account.blob_service"


@pytest.mark.asyncio
async def test_arm_marks_missing_fields_partial_without_inventing_false_values(context):
    account = _account()
    del account["properties"]["supportsHttpsTrafficOnly"]
    blob = _blob_service()
    del blob["properties"]["deleteRetentionPolicy"]["enabled"]
    transport = FakeTransport(HttpResponse(200, account, {}), HttpResponse(200, blob, {}))

    result = await ArmStorageAccountAdapter(transport, resource_detail="all").collect(context)

    assert {record.status for record in result.evidence} == {EvidenceStatus.PARTIAL}
    assert "supportsHttpsTrafficOnly" not in result.evidence[0].payload["properties"]
    assert "enabled" not in result.evidence[1].payload["properties"]["deleteRetentionPolicy"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "reason_code", "status_code", "retry_after"),
    [
        (HttpResponse(403, {"error": {"code": "AuthorizationFailed"}}, {}), "source_unauthorized", 403, None),
        (HttpResponse(401, {"error": {"code": "Unauthorized"}}, {}), "source_unauthorized", 401, None),
        (
            HttpResponse(302, {"error": {"code": "Redirect"}}, {"Location": "https://attacker.example"}),
            "untrusted_redirect",
            302,
            None,
        ),
        (HttpResponse(429, {"error": {"code": "TooManyRequests"}}, {"Retry-After": "17"}), "source_throttled", 429, 17.0),
        (asyncio.TimeoutError(), "source_timeout", None, None),
        (MalformedJsonError("response body is not valid JSON"), "source_malformed", None, None),
        (HttpTransportError("source connection failed"), "source_transport_error", None, None),
        (CredentialError("credential acquisition failed"), "source_unauthorized", None, None),
    ],
)
async def test_arm_normalizes_source_failures(context, response, reason_code, status_code, retry_after):
    result = await ArmStorageAccountAdapter(FakeTransport(response), resource_detail="all").collect(context)

    assert result.evidence == ()
    assert result.partial is True
    assert result.failures[0].reason_code == reason_code
    assert result.failures[0].status_code == status_code
    assert result.failures[0].retry_after == retry_after


@pytest.mark.asyncio
async def test_arm_paginates_list_and_preserves_original_ids(credential):
    second_id = RESOURCE_ID.replace("ExampleStorage", "SecondStorage")
    next_link = (
        f"https://management.azure.com/subscriptions/{SUBSCRIPTION_ID}/providers/"
        "Microsoft.Storage/storageAccounts?api-version=2023-05-01&$skiptoken=next"
    )
    transport = FakeTransport(
        HttpResponse(200, {"value": [_account()], "nextLink": next_link}, {}),
        HttpResponse(200, {"value": [_account(second_id)]}, {}),
        HttpResponse(200, _blob_service(), {}),
        HttpResponse(200, _blob_service(second_id), {}),
    )
    context = CollectionContext(
        tenant_id=TENANT_ID,
        subscription_id=SUBSCRIPTION_ID,
        resource_ids=None,
        credential=credential,
    )

    result = await ArmStorageAccountAdapter(transport, resource_detail="all").collect(context)

    assert result.failures == ()
    assert len(result.evidence) == 4
    assert {record.resource_id.casefold() for record in result.evidence} == {
        RESOURCE_ID.casefold(),
        second_id.casefold(),
    }
    assert transport.requests[1]["url"] == next_link


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("next_link", "reason_code"),
    [
        ("https://evil.example/steal", "source_untrusted_next_link"),
        (
            "https://management.azure.com/subscriptions/example/providers/"
            "Microsoft.Storage/storageAccounts?api-version=2023-05-01",
            "source_pagination_loop",
        ),
    ],
)
async def test_arm_rejects_untrusted_or_looping_pagination_but_preserves_page_evidence(
    credential,
    next_link,
    reason_code,
):
    first_url = (
        f"https://management.azure.com/subscriptions/{SUBSCRIPTION_ID}/providers/"
        f"Microsoft.Storage/storageAccounts?api-version={ARM_STORAGE_API_VERSION}"
    )
    if reason_code == "source_pagination_loop":
        next_link = first_url
    transport = FakeTransport(
        HttpResponse(200, {"value": [_account()], "nextLink": next_link}, {}),
        HttpResponse(200, _blob_service(), {}),
    )
    context = CollectionContext(
        tenant_id=TENANT_ID,
        subscription_id=SUBSCRIPTION_ID,
        resource_ids=None,
        credential=credential,
    )

    result = await ArmStorageAccountAdapter(transport, resource_detail="all").collect(context)

    assert result.partial is True
    assert result.failures[0].reason_code == reason_code
    assert any(record.source_kind == "arm" for record in result.evidence)


@pytest.mark.asyncio
async def test_arm_partial_second_page_preserves_first_page_evidence(credential):
    next_link = (
        f"https://management.azure.com/subscriptions/{SUBSCRIPTION_ID}/providers/"
        "Microsoft.Storage/storageAccounts?api-version=2023-05-01&$skiptoken=next"
    )
    transport = FakeTransport(
        HttpResponse(200, {"value": [_account()], "nextLink": next_link}, {}),
        HttpResponse(500, {"error": {"code": "InternalServerError"}}, {}),
        HttpResponse(200, _blob_service(), {}),
    )
    context = CollectionContext(
        tenant_id=TENANT_ID,
        subscription_id=SUBSCRIPTION_ID,
        resource_ids=None,
        credential=credential,
    )

    result = await ArmStorageAccountAdapter(transport, resource_detail="all").collect(context)

    assert result.partial is True
    assert result.failures[0].reason_code == "source_http_error"
    assert {record.source_kind for record in result.evidence} == {"arm", "storage_service"}


@pytest.mark.asyncio
async def test_arm_partial_second_page_evidence_yields_unknown_in_evaluator(credential):
    next_link = (
        f"https://management.azure.com/subscriptions/{SUBSCRIPTION_ID}/providers/"
        "Microsoft.Storage/storageAccounts?api-version=2023-05-01&$skiptoken=next"
    )
    transport = FakeTransport(
        HttpResponse(200, {"value": [_account()], "nextLink": next_link}, {}),
        HttpResponse(500, {"error": {"code": "InternalServerError"}}, {}),
        HttpResponse(200, _blob_service(), {}),
    )
    context = CollectionContext(
        tenant_id=TENANT_ID,
        subscription_id=SUBSCRIPTION_ID,
        resource_ids=None,
        credential=credential,
    )
    registry = ControlRegistry.load(
        SPIKE_ROOT / "checklists/azure_storage_production_readiness.yaml",
        SPIKE_ROOT / "mappings/azure_storage_production_readiness.yaml",
    )

    result = await ArmStorageAccountAdapter(transport, resource_detail="all").collect(context)
    verdict = DeterministicEvaluator().evaluate(
        registry.get("storage.secure_transfer"),
        result.evidence,
    )

    assert result.evidence
    assert all(record.status is EvidenceStatus.PARTIAL for record in result.evidence)
    account = next(record for record in result.evidence if record.source_kind == "arm")
    assert account.payload["id"] == RESOURCE_ID.lower()
    assert account.source_reference == "arm.storage_account.resource"
    assert account.source_version == ARM_STORAGE_API_VERSION
    assert account.content_hash == EvidenceRecord.create(
        source_kind=account.source_kind,
        source_reference=account.source_reference,
        source_version=account.source_version,
        resource_id=account.resource_id,
        status=EvidenceStatus.COMPLETE,
        payload=account.payload,
        observed_at=account.observed_at,
    ).content_hash
    assert verdict.state is VerdictState.UNKNOWN
    assert verdict.reason_code == "evidence_partial"


@pytest.mark.asyncio
async def test_arm_collection_page_limit_preserves_prior_evidence(credential):
    next_link = (
        f"https://management.azure.com/subscriptions/{SUBSCRIPTION_ID}/providers/"
        "Microsoft.Storage/storageAccounts?api-version=2023-05-01&$skiptoken=next"
    )
    transport = FakeTransport(
        HttpResponse(200, {"value": [_account()], "nextLink": next_link}, {}),
    )
    context = CollectionContext(
        tenant_id=TENANT_ID,
        subscription_id=SUBSCRIPTION_ID,
        resource_ids=None,
        credential=credential,
        max_pages=1,
    )

    result = await ArmStorageAccountAdapter(transport, resource_detail="all").collect(context)

    assert len(transport.requests) == 1
    assert len(result.evidence) == 1
    assert result.evidence[0].status is EvidenceStatus.PARTIAL
    assert result.failures[0].reason_code == "source_page_limit"


@pytest.mark.asyncio
async def test_arm_collection_timeout_preserves_prior_evidence_without_sleeping(credential):
    clock_values = iter((0.0, 0.0, 2.0))

    def monotonic():
        return next(clock_values, 2.0)

    next_link = (
        f"https://management.azure.com/subscriptions/{SUBSCRIPTION_ID}/providers/"
        "Microsoft.Storage/storageAccounts?api-version=2023-05-01&$skiptoken=next"
    )
    transport = FakeTransport(
        HttpResponse(200, {"value": [_account()], "nextLink": next_link}, {}),
    )
    context = CollectionContext(
        tenant_id=TENANT_ID,
        subscription_id=SUBSCRIPTION_ID,
        resource_ids=None,
        credential=credential,
        collection_timeout=1.0,
        monotonic=monotonic,
    )

    result = await ArmStorageAccountAdapter(transport, resource_detail="all").collect(context)

    assert len(transport.requests) == 1
    assert len(result.evidence) == 1
    assert result.evidence[0].status is EvidenceStatus.PARTIAL
    assert result.failures[0].reason_code == "source_timeout"


@pytest.mark.asyncio
async def test_arm_rejects_same_host_cross_subscription_next_link(credential):
    other_subscription = "99999999-8888-7777-6666-555555555555"
    next_link = (
        f"https://management.azure.com/subscriptions/{other_subscription}/providers/"
        f"Microsoft.Storage/storageAccounts?api-version={ARM_STORAGE_API_VERSION}"
    )
    transport = FakeTransport(
        HttpResponse(200, {"value": [_account()], "nextLink": next_link}, {}),
        HttpResponse(200, _blob_service(), {}),
    )
    context = CollectionContext(
        tenant_id=TENANT_ID,
        subscription_id=SUBSCRIPTION_ID,
        resource_ids=None,
        credential=credential,
    )

    result = await ArmStorageAccountAdapter(transport, resource_detail="all").collect(context)

    assert result.partial is True
    assert result.failures[0].reason_code == "source_scope_conflict"
    assert all(other_subscription not in request["url"] for request in transport.requests)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "next_link",
    [
        (
            f"https://management.azure.com/subscriptions/{SUBSCRIPTION_ID}/providers/"
            "Microsoft.Compute/virtualMachines?api-version=2023-05-01"
        ),
        (
            f"https://management.azure.com/subscriptions/{SUBSCRIPTION_ID}/providers/"
            "Microsoft.Storage/storageAccounts?api-version=2022-09-01"
        ),
    ],
)
async def test_arm_rejects_next_link_with_path_or_api_version_provenance_mismatch(
    credential,
    next_link,
):
    transport = FakeTransport(
        HttpResponse(200, {"value": [_account()], "nextLink": next_link}, {}),
        HttpResponse(200, _blob_service(), {}),
    )
    context = CollectionContext(
        tenant_id=TENANT_ID,
        subscription_id=SUBSCRIPTION_ID,
        resource_ids=None,
        credential=credential,
    )

    result = await ArmStorageAccountAdapter(transport, resource_detail="all").collect(context)

    assert result.partial is True
    assert result.failures[0].reason_code == "source_provenance_conflict"
    assert len(transport.requests) == 2


@pytest.mark.asyncio
async def test_arm_rejects_cross_subscription_record(credential):
    other_resource_id = RESOURCE_ID.replace(SUBSCRIPTION_ID, "99999999-8888-7777-6666-555555555555")
    transport = FakeTransport(HttpResponse(200, {"value": [_account(other_resource_id)]}, {}))
    context = CollectionContext(
        tenant_id=TENANT_ID,
        subscription_id=SUBSCRIPTION_ID,
        resource_ids=None,
        credential=credential,
    )

    result = await ArmStorageAccountAdapter(transport, resource_detail="all").collect(context)

    assert result.evidence == ()
    assert result.partial is True
    assert result.failures[0].reason_code == "source_scope_conflict"