"""Azure Advisor recommendation evidence adapter."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from enterprise.adapters.base import (
    AsyncHttpTransport,
    CollectionContext,
    CollectionFailure,
    CollectionResult,
    build_arm_url,
    collect_json_pages,
    managed_payload,
    normalize_resource_id,
    resource_id_in_subscription,
    resource_type_from_id,
)
from enterprise.domain import EvidenceRecord, EvidenceStatus


ADVISOR_API_VERSION = "2025-01-01"
ADVISOR_SOURCE_REFERENCE = "advisor.recommendations"


class AdvisorAdapter:
    def __init__(
        self,
        transport: AsyncHttpTransport,
        *,
        source_reference: str,
        recommendation_type_id: str,
        source_version: str = ADVISOR_API_VERSION,
    ) -> None:
        if not isinstance(source_reference, str) or not source_reference.strip():
            raise ValueError("source_reference must not be empty")
        if not isinstance(recommendation_type_id, str) or not recommendation_type_id.strip():
            raise ValueError("recommendation_type_id must not be empty")
        if source_version != ADVISOR_API_VERSION:
            raise ValueError(f"source_version must match Advisor API version {ADVISOR_API_VERSION}")
        self._transport = transport
        self.source_reference = source_reference
        self.source_version = source_version
        self.recommendation_type_id = recommendation_type_id.strip()
        self._recommendation_type_id_key = self.recommendation_type_id.casefold()

    async def collect(self, context: CollectionContext) -> CollectionResult:
        path = (
            f"/subscriptions/{context.subscription_id}/providers/"
            "Microsoft.Advisor/recommendations"
        )
        pages = await collect_json_pages(
            self._transport,
            context,
            method="GET",
            url=build_arm_url(path, api_version=ADVISOR_API_VERSION),
            source_kind="advisor",
            source_reference=self.source_reference,
        )
        evidence: list[EvidenceRecord] = []
        failures = list(pages.failures)
        for page in pages.pages:
            values = page.get("value")
            if not isinstance(values, list):
                failures.append(self._malformed("Advisor value must be an array"))
                continue
            for value in values:
                if not isinstance(value, Mapping):
                    failures.append(self._malformed("Advisor recommendation must be an object"))
                    continue
                recommendation_type_id, identity_failure = self._recommendation_type_id(value)
                if identity_failure is not None:
                    failures.append(identity_failure)
                    continue
                if recommendation_type_id.casefold() != self._recommendation_type_id_key:
                    continue
                record, failure = self._record(value, context)
                if failure is not None:
                    failures.append(failure)
                if record is not None:
                    evidence.append(record)
        if context.resource_ids is not None:
            matching_resource_ids = {record.resource_id.casefold() for record in evidence}
            evidence.extend(
                self._absence_record(resource_id, collection_complete=not failures)
                for resource_id in context.resource_ids
                if resource_id.casefold() not in matching_resource_ids
            )
        return CollectionResult(
            evidence=evidence,
            failures=failures,
            partial=bool(failures) or any(record.status is EvidenceStatus.PARTIAL for record in evidence),
        )

    def _recommendation_type_id(
        self,
        recommendation: Mapping[str, Any],
    ) -> tuple[str | None, CollectionFailure | None]:
        properties = recommendation.get("properties")
        if not isinstance(properties, Mapping):
            return None, self._malformed("Advisor recommendation properties are missing")
        recommendation_type_id = properties.get("recommendationTypeId")
        if not isinstance(recommendation_type_id, str) or not recommendation_type_id.strip():
            return None, self._malformed("Advisor recommendationTypeId is missing")
        return recommendation_type_id, None

    def _absence_record(
        self,
        resource_id: str,
        *,
        collection_complete: bool,
    ) -> EvidenceRecord:
        resource_type = resource_type_from_id(resource_id)
        managed_status = "pass" if collection_complete else "unknown"
        payload = managed_payload(
            {
                "recommendation_type_id": self.recommendation_type_id,
                "recommendation_present": False,
            },
            resource_type=resource_type,
            managed_status=managed_status,
        )
        return EvidenceRecord.create(
            source_kind="advisor",
            source_reference=self.source_reference,
            source_version=self.source_version,
            resource_id=resource_id,
            status=(
                EvidenceStatus.COMPLETE
                if resource_type is not None and collection_complete
                else EvidenceStatus.PARTIAL
            ),
            payload=payload,
        )

    def _record(
        self,
        recommendation: Mapping[str, Any],
        context: CollectionContext,
    ) -> tuple[EvidenceRecord | None, CollectionFailure | None]:
        properties = recommendation.get("properties")
        if not isinstance(properties, Mapping):
            return None, self._malformed("Advisor recommendation properties are missing")
        metadata = properties.get("resourceMetadata")
        raw_resource_id = metadata.get("resourceId") if isinstance(metadata, Mapping) else None
        if isinstance(raw_resource_id, str) and not resource_id_in_subscription(
            raw_resource_id,
            context.subscription_id,
        ):
            return None, self._failure(
                "source_scope_conflict",
                "Advisor recommendation left the selected subscription",
            )
        resource_id = normalize_resource_id(
            raw_resource_id,
            context.resource_ids,
            subscription_id=context.subscription_id,
        )
        if resource_id is None:
            if isinstance(raw_resource_id, str) and context.resource_ids is not None:
                return None, None
            return None, self._malformed("Advisor recommendation resource id is missing")
        resource_type = properties.get("impactedField")
        if not isinstance(resource_type, str) or not resource_type.strip():
            resource_type = resource_type_from_id(resource_id)
        payload = managed_payload(
            recommendation,
            resource_type=resource_type,
            managed_status="fail",
        )
        status = EvidenceStatus.COMPLETE if resource_type is not None else EvidenceStatus.PARTIAL
        return (
            EvidenceRecord.create(
                source_kind="advisor",
                source_reference=self.source_reference,
                source_version=self.source_version,
                resource_id=resource_id,
                status=status,
                payload=payload,
            ),
            None,
        )

    def _malformed(self, detail: str) -> CollectionFailure:
        return self._failure("source_malformed", detail)

    def _failure(self, reason_code: str, detail: str) -> CollectionFailure:
        return CollectionFailure(reason_code, "advisor", self.source_reference, detail=detail)


