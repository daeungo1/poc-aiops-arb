"""Microsoft Defender for Cloud assessment evidence adapter."""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlsplit

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


DEFENDER_API_VERSION = "2020-01-01"
DEFENDER_SOURCE_REFERENCE = "defender.security_assessments"


class DefenderAdapter:
    def __init__(
        self,
        transport: AsyncHttpTransport,
        *,
        source_reference: str = DEFENDER_SOURCE_REFERENCE,
        source_version: str = DEFENDER_API_VERSION,
        assessment_names: tuple[str, ...] | None = None,
    ) -> None:
        if source_version != DEFENDER_API_VERSION:
            raise ValueError(f"source_version must match Defender API version {DEFENDER_API_VERSION}")
        if assessment_names is None:
            raise ValueError("assessment_names must contain non-empty strings")
        self._transport = transport
        self.source_reference = source_reference
        self.source_version = source_version
        self.assessment_names = _normalize_selectors(assessment_names, "assessment_names")

    async def collect(self, context: CollectionContext) -> CollectionResult:
        path = (
            f"/subscriptions/{context.subscription_id}/providers/"
            "Microsoft.Security/assessments"
        )
        pages = await collect_json_pages(
            self._transport,
            context,
            method="GET",
            url=build_arm_url(path, api_version=DEFENDER_API_VERSION),
            source_kind="defender",
            source_reference=self.source_reference,
        )
        evidence: list[EvidenceRecord] = []
        failures = list(pages.failures)
        for page in pages.pages:
            values = page.get("value")
            if not isinstance(values, list):
                failures.append(self._malformed("Defender value must be an array"))
                continue
            for value in values:
                if not isinstance(value, Mapping):
                    failures.append(self._malformed("Defender assessment must be an object"))
                    continue
                if not self._matches_control(value):
                    continue
                record, failure = self._record(value, context)
                if failure is not None:
                    failures.append(failure)
                if record is not None:
                    evidence.append(record)
        return CollectionResult(
            evidence=evidence,
            failures=failures,
            partial=bool(failures) or any(record.status is EvidenceStatus.PARTIAL for record in evidence),
        )

    def _matches_control(self, assessment: Mapping[str, object]) -> bool:
        assessment_name = _assessment_name(assessment)
        return isinstance(assessment_name, str) and assessment_name.casefold() in self.assessment_names

    def _record(
        self,
        assessment: Mapping[str, object],
        context: CollectionContext,
    ) -> tuple[EvidenceRecord | None, CollectionFailure | None]:
        properties = assessment.get("properties")
        if not isinstance(properties, Mapping):
            return None, self._malformed("Defender assessment properties are missing")
        resource_details = properties.get("resourceDetails")
        if not isinstance(resource_details, Mapping):
            return None, self._malformed("Defender resource details are missing")
        source = resource_details.get("source")
        if not isinstance(source, str) or source.casefold() != "azure":
            return (
                self._unsupported_resource_details_record(assessment, context),
                self._malformed("Defender resource details use an unsupported source variant"),
            )
        raw_resource_id = resource_details.get("id")
        if isinstance(raw_resource_id, str) and not resource_id_in_subscription(
            raw_resource_id,
            context.subscription_id,
        ):
            return None, self._failure(
                "source_scope_conflict",
                "Defender assessment left the selected subscription",
            )
        resource_id = normalize_resource_id(
            raw_resource_id,
            context.resource_ids,
            subscription_id=context.subscription_id,
        )
        if resource_id is None:
            if isinstance(raw_resource_id, str) and context.resource_ids is not None:
                return None, None
            return None, self._malformed("Defender assessed resource id is missing")
        resource_type = resource_details.get("resourceType")
        if not isinstance(resource_type, str) or not resource_type.strip():
            resource_type = resource_type_from_id(resource_id)
        raw_status = properties.get("status")
        status_code = raw_status.get("code") if isinstance(raw_status, Mapping) else None
        managed_status = _normalize_status(status_code)
        payload = managed_payload(
            assessment,
            resource_type=resource_type,
            managed_status=managed_status,
        )
        status = (
            EvidenceStatus.COMPLETE
            if resource_type is not None and managed_status != "unknown"
            else EvidenceStatus.PARTIAL
        )
        return (
            EvidenceRecord.create(
                source_kind="defender",
                source_reference=self.source_reference,
                source_version=self.source_version,
                resource_id=resource_id,
                status=status,
                payload=payload,
            ),
            None,
        )

    def _unsupported_resource_details_record(
        self,
        assessment: Mapping[str, object],
        context: CollectionContext,
    ) -> EvidenceRecord | None:
        if context.resource_ids is None or len(context.resource_ids) != 1:
            return None
        resource_id = context.resource_ids[0]
        payload = managed_payload(
            assessment,
            resource_type=resource_type_from_id(resource_id),
            managed_status="unknown",
        )
        return EvidenceRecord.create(
            source_kind="defender",
            source_reference=self.source_reference,
            source_version=self.source_version,
            resource_id=resource_id,
            status=EvidenceStatus.PARTIAL,
            payload=payload,
        )

    def _malformed(self, detail: str) -> CollectionFailure:
        return self._failure("source_malformed", detail)

    def _failure(self, reason_code: str, detail: str) -> CollectionFailure:
        return CollectionFailure(reason_code, "defender", self.source_reference, detail=detail)


def _normalize_status(value: object) -> str:
    if not isinstance(value, str):
        return "unknown"
    normalized = value.strip().casefold()
    if normalized == "healthy":
        return "pass"
    if normalized == "unhealthy":
        return "fail"
    return "unknown"


def _normalize_selectors(values: tuple[str, ...], field_name: str) -> frozenset[str]:
    selectors = tuple(values)
    if not selectors or any(not isinstance(value, str) or not value.strip() for value in selectors):
        raise ValueError(f"{field_name} must contain non-empty strings")
    return frozenset(value.casefold() for value in selectors)


def _assessment_name(assessment: Mapping[str, object]) -> str | None:
    name = assessment.get("name")
    if isinstance(name, str) and name.strip():
        return name
    identifier = assessment.get("id")
    if not isinstance(identifier, str) or not identifier.strip():
        return None
    path = urlsplit(identifier).path if "://" in identifier else identifier
    segments = tuple(segment for segment in path.strip("/").split("/") if segment)
    return segments[-1] if segments else None