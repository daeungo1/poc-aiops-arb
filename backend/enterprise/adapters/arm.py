"""Storage Account와 Blob service ARM evidence 수집기."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from enterprise.adapters.base import (
    AsyncHttpTransport,
    CollectionBudget,
    CollectionContext,
    CollectionFailure,
    CollectionResult,
    build_arm_url,
    collect_json_pages,
    evidence_status,
    normalize_resource_id,
    resource_id_in_subscription,
)
from enterprise.domain import EvidenceRecord, EvidenceStatus


ARM_STORAGE_API_VERSION = "2023-05-01"
STORAGE_ACCOUNT_SOURCE_KIND = "arm"
STORAGE_ACCOUNT_SOURCE_REFERENCE = "arm.storage_account.resource"
BLOB_SERVICE_SOURCE_KIND = "storage_service"
BLOB_SERVICE_SOURCE_REFERENCE = "arm.storage_account.blob_service"
STORAGE_ACCOUNT_RESOURCE_TYPE = "Microsoft.Storage/storageAccounts"
ARM_STORAGE_RESOURCE_DETAIL = Literal["account", "blob_service", "all"]

_ACCOUNT_REQUIRED_FIELDS = (
    "resource_type",
    "properties.supportsHttpsTrafficOnly",
    "properties.minimumTlsVersion",
    "properties.publicNetworkAccess",
    "sku.name",
)
_BLOB_REQUIRED_FIELDS = (
    "resource_type",
    "properties.deleteRetentionPolicy.enabled",
    "properties.deleteRetentionPolicy.days",
)


class ArmStorageAccountAdapter:
    def __init__(self, transport: AsyncHttpTransport, *, resource_detail: ARM_STORAGE_RESOURCE_DETAIL) -> None:
        if resource_detail not in {"account", "blob_service", "all"}:
            raise ValueError("resource_detail must be 'account', 'blob_service', or 'all'")
        self._transport = transport
        self.resource_detail = resource_detail

    async def collect(self, context: CollectionContext) -> CollectionResult:
        evidence: list[EvidenceRecord] = []
        failures: list[CollectionFailure] = []
        budget = CollectionBudget.start(context)

        if self.resource_detail in {"account", "all"}:
            if context.resource_ids is not None:
                for resource_id in context.resource_ids:
                    account, account_failures = await self._collect_account(context, resource_id, budget)
                    failures.extend(account_failures)
                    if account is not None:
                        evidence.append(account)
                        if self.resource_detail == "all":
                            blob, blob_failures = await self._collect_blob_service(context, resource_id, budget)
                            failures.extend(blob_failures)
                            if blob is not None:
                                evidence.append(blob)
            else:
                accounts, list_failures = await self._list_accounts(context, budget)
                evidence.extend(accounts)
                failures.extend(list_failures)
                if self.resource_detail == "all":
                    for account in accounts:
                        blob, blob_failures = await self._collect_blob_service(context, account.resource_id, budget)
                        failures.extend(blob_failures)
                        if blob is not None:
                            evidence.append(blob)
        elif context.resource_ids is not None:
            for resource_id in context.resource_ids:
                blob, blob_failures = await self._collect_blob_service(context, resource_id, budget)
                failures.extend(blob_failures)
                if blob is not None:
                    evidence.append(blob)
        else:
            accounts, list_failures = await self._list_accounts(context, budget)
            failures.extend(list_failures)
            for account in accounts:
                blob, blob_failures = await self._collect_blob_service(context, account.resource_id, budget)
                failures.extend(blob_failures)
                if blob is not None:
                    evidence.append(blob)

        return CollectionResult(
            evidence=tuple(evidence),
            failures=tuple(failures),
            partial=bool(failures) or any(record.status is EvidenceStatus.PARTIAL for record in evidence),
        )

    async def _collect_account(
        self,
        context: CollectionContext,
        resource_id: str,
        budget: CollectionBudget,
    ) -> tuple[EvidenceRecord | None, tuple[CollectionFailure, ...]]:
        pages = await collect_json_pages(
            self._transport,
            context,
            method="GET",
            url=build_arm_url(resource_id, api_version=ARM_STORAGE_API_VERSION),
            source_kind=STORAGE_ACCOUNT_SOURCE_KIND,
            source_reference=STORAGE_ACCOUNT_SOURCE_REFERENCE,
            budget=budget,
        )
        if not pages.pages:
            return None, pages.failures
        record, normalization_failure = self._account_record(
            pages.pages[0],
            context.resource_ids,
            context.subscription_id,
        )
        failures = pages.failures + ((normalization_failure,) if normalization_failure else ())
        return record, failures

    async def _list_accounts(
        self,
        context: CollectionContext,
        budget: CollectionBudget,
    ) -> tuple[list[EvidenceRecord], tuple[CollectionFailure, ...]]:
        path = (
            f"/subscriptions/{context.subscription_id}/providers/"
            "Microsoft.Storage/storageAccounts"
        )
        pages = await collect_json_pages(
            self._transport,
            context,
            method="GET",
            url=build_arm_url(path, api_version=ARM_STORAGE_API_VERSION),
            source_kind=STORAGE_ACCOUNT_SOURCE_KIND,
            source_reference=STORAGE_ACCOUNT_SOURCE_REFERENCE,
            budget=budget,
        )
        evidence: list[EvidenceRecord] = []
        failures = list(pages.failures)
        for page in pages.pages:
            values = page.get("value")
            if not isinstance(values, list):
                failures.append(self._malformed_failure("storage account list value must be an array"))
                continue
            for value in values:
                if not isinstance(value, Mapping):
                    failures.append(self._malformed_failure("storage account list item must be an object"))
                    continue
                record, failure = self._account_record(value, None, context.subscription_id)
                if failure is not None:
                    failures.append(failure)
                if record is not None:
                    evidence.append(record)
        return evidence, tuple(failures)

    async def _collect_blob_service(
        self,
        context: CollectionContext,
        resource_id: str,
        budget: CollectionBudget,
    ) -> tuple[EvidenceRecord | None, tuple[CollectionFailure, ...]]:
        blob_path = f"{resource_id.rstrip('/')}/blobServices/default"
        pages = await collect_json_pages(
            self._transport,
            context,
            method="GET",
            url=build_arm_url(blob_path, api_version=ARM_STORAGE_API_VERSION),
            source_kind=BLOB_SERVICE_SOURCE_KIND,
            source_reference=BLOB_SERVICE_SOURCE_REFERENCE,
            budget=budget,
        )
        if not pages.pages:
            return None, pages.failures
        payload = dict(pages.pages[0])
        payload["resource_type"] = STORAGE_ACCOUNT_RESOURCE_TYPE
        record = EvidenceRecord.create(
            source_kind=BLOB_SERVICE_SOURCE_KIND,
            source_reference=BLOB_SERVICE_SOURCE_REFERENCE,
            source_version=ARM_STORAGE_API_VERSION,
            resource_id=resource_id,
            status=evidence_status(payload, _BLOB_REQUIRED_FIELDS),
            payload=payload,
        )
        return record, pages.failures

    @staticmethod
    def _account_record(
        item: Mapping[str, Any],
        resource_ids: tuple[str, ...] | None,
        subscription_id: str,
    ) -> tuple[EvidenceRecord | None, CollectionFailure | None]:
        raw_resource_id = item.get("id")
        if isinstance(raw_resource_id, str) and not resource_id_in_subscription(
            raw_resource_id,
            subscription_id,
        ):
            return None, CollectionFailure(
                "source_scope_conflict",
                STORAGE_ACCOUNT_SOURCE_KIND,
                STORAGE_ACCOUNT_SOURCE_REFERENCE,
                detail="storage account response left the selected subscription",
            )
        resource_id = normalize_resource_id(
            raw_resource_id,
            resource_ids,
            subscription_id=subscription_id,
        )
        if resource_id is None:
            return None, ArmStorageAccountAdapter._malformed_failure(
                "storage account response has no in-scope resource id"
            )
        payload = dict(item)
        if "type" in item:
            payload["resource_type"] = item["type"]
        record = EvidenceRecord.create(
            source_kind=STORAGE_ACCOUNT_SOURCE_KIND,
            source_reference=STORAGE_ACCOUNT_SOURCE_REFERENCE,
            source_version=ARM_STORAGE_API_VERSION,
            resource_id=resource_id,
            status=evidence_status(payload, _ACCOUNT_REQUIRED_FIELDS),
            payload=payload,
        )
        return record, None

    @staticmethod
    def _malformed_failure(detail: str) -> CollectionFailure:
        return CollectionFailure(
            reason_code="source_malformed",
            source_kind=STORAGE_ACCOUNT_SOURCE_KIND,
            source_reference=STORAGE_ACCOUNT_SOURCE_REFERENCE,
            detail=detail,
        )