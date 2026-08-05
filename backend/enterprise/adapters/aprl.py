"""APRL 및 일반 ARG/KQL query evidence adapter."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping
from typing import Any

from enterprise.adapters.base import (
    AsyncHttpTransport,
    CollectionBudget,
    CollectionContext,
    CollectionFailure,
    CollectionResult,
    CredentialError,
    HttpTransportError,
    MalformedJsonError,
    build_arm_url,
    failure_from_response,
    managed_payload,
    normalize_resource_id,
    resource_id_in_subscription,
)
from enterprise.domain import EvidenceRecord, EvidenceStatus


RESOURCE_GRAPH_API_VERSION = "2022-10-01"
APRL_SOURCE_REFERENCE = "aprl.resource_graph.query"
_MISSING = object()


class AprlAdapter:
    def __init__(
        self,
        transport: AsyncHttpTransport,
        *,
        query: str,
        projection: Mapping[str, str],
        status_field: str | None = None,
        pass_status_values: tuple[str, ...] | None = None,
        fail_status_values: tuple[str, ...] | None = None,
        source_kind: str = "aprl",
        source_reference: str = APRL_SOURCE_REFERENCE,
        source_version: str | None = None,
    ) -> None:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must not be empty")
        if source_kind not in {"aprl", "arg"}:
            raise ValueError("source_kind must be aprl or arg")
        if not isinstance(source_reference, str) or not source_reference.strip():
            raise ValueError("source_reference must not be empty")
        if not isinstance(projection, Mapping) or set(projection) != {"resource_id", "resource_type"}:
            raise ValueError("projection must exactly map resource_id and resource_type")
        if any(not isinstance(value, str) or not value.strip() for value in projection.values()):
            raise ValueError("projection selectors must be non-empty strings")
        if source_kind == "aprl":
            if not isinstance(status_field, str) or not status_field.strip():
                raise ValueError("status_field is required for APRL sources")
            pass_values = _normalize_allowed_statuses(pass_status_values, "pass_status_values")
            fail_values = _normalize_allowed_statuses(fail_status_values, "fail_status_values")
            if pass_values & fail_values:
                raise ValueError("APRL pass and fail status values must be disjoint")
        else:
            if status_field is not None or pass_status_values is not None or fail_status_values is not None:
                raise ValueError("status configuration is only valid for APRL sources")
            pass_values = frozenset()
            fail_values = frozenset()
        self._transport = transport
        self.query = query
        self.projection = dict(projection)
        self.status_field = status_field
        self.pass_status_values = pass_values
        self.fail_status_values = fail_values
        self.source_kind = source_kind
        self.source_reference = source_reference
        expected_source_version = resource_graph_source_version(query)
        if source_version is not None and source_version != expected_source_version:
            raise ValueError(
                "source_version must match the Resource Graph API and query version"
            )
        self.source_version = expected_source_version

    async def collect(self, context: CollectionContext) -> CollectionResult:
        path = "/providers/Microsoft.ResourceGraph/resources"
        budget = CollectionBudget.start(context)
        pages, page_failures = await self._collect_resource_graph_pages(
            context,
            build_arm_url(path, api_version=RESOURCE_GRAPH_API_VERSION),
            budget,
        )
        evidence: list[EvidenceRecord] = []
        failures = list(page_failures)
        for page in pages:
            rows = page.get("data")
            if not isinstance(rows, list):
                failures.append(self._malformed("Resource Graph data must be an array"))
                continue
            for row in rows:
                if not isinstance(row, Mapping):
                    failures.append(self._malformed("Resource Graph row must be an object"))
                    continue
                record, failure = self._record(row, context)
                if failure is not None:
                    failures.append(failure)
                if record is not None:
                    evidence.append(record)
        return CollectionResult(
            evidence=evidence,
            failures=failures,
            partial=bool(failures) or any(record.status is EvidenceStatus.PARTIAL for record in evidence),
        )

    async def _collect_resource_graph_pages(
        self,
        context: CollectionContext,
        url: str,
        budget: CollectionBudget,
    ) -> tuple[tuple[Mapping[str, Any], ...], tuple[CollectionFailure, ...]]:
        pages: list[Mapping[str, Any]] = []
        failures: list[CollectionFailure] = []
        seen_tokens: set[str] = set()
        skip_token: str | None = None

        while True:
            if budget.pages_used >= budget.max_pages:
                failures.append(
                    self._failure(
                        "source_page_limit",
                        "Resource Graph query exceeded its bounded page limit",
                    )
                )
                break
            remaining = budget.remaining()
            if remaining <= 0:
                failures.append(
                    self._failure(
                        "source_timeout",
                        "Resource Graph query exceeded its total wall-clock timeout",
                    )
                )
                break
            options = {"resultFormat": "objectArray"}
            if skip_token is not None:
                options["$skipToken"] = skip_token
            request_body = {
                "subscriptions": [context.subscription_id],
                "query": self.query,
                "options": options,
            }
            budget.pages_used += 1
            try:
                response = await asyncio.wait_for(
                    self._transport.request_json(
                        "POST",
                        url,
                        credential=context.credential,
                        json_body=request_body,
                    ),
                    timeout=remaining,
                )
            except (asyncio.TimeoutError, TimeoutError):
                failures.append(self._failure("source_timeout", "Resource Graph query timed out"))
                break
            except MalformedJsonError:
                failures.append(self._failure("source_malformed", "Resource Graph response was not valid JSON"))
                break
            except HttpTransportError:
                failures.append(
                    self._failure(
                        "source_transport_error",
                        "Resource Graph connection failed before receiving a response",
                    )
                )
                break
            except CredentialError:
                failures.append(
                    self._failure(
                        "source_unauthorized",
                        "Credential could not acquire an ARM access token",
                    )
                )
                break
            if not 200 <= response.status_code <= 299:
                failures.append(
                    failure_from_response(response, self.source_kind, self.source_reference)
                )
                break
            if not isinstance(response.body, Mapping):
                failures.append(self._failure("source_malformed", "Resource Graph JSON root must be an object"))
                break
            pages.append(response.body)

            truncated = response.body.get("resultTruncated")
            is_truncated = truncated is True or (
                isinstance(truncated, str) and truncated.casefold() == "true"
            )
            next_token = response.body.get("$skipToken")
            if not is_truncated and next_token is None:
                break
            if not isinstance(next_token, str) or not next_token.strip():
                failures.append(
                    self._failure(
                        "source_truncated",
                        "Resource Graph reported truncated results without a continuation token",
                    )
                )
                break
            if next_token in seen_tokens:
                failures.append(
                    self._failure(
                        "source_pagination_loop",
                        "Resource Graph repeated a continuation token",
                    )
                )
                break
            seen_tokens.add(next_token)
            skip_token = next_token

        return tuple(pages), tuple(failures)

    def _record(
        self,
        row: Mapping[str, Any],
        context: CollectionContext,
    ) -> tuple[EvidenceRecord | None, CollectionFailure | None]:
        malformed_fields: list[str] = []
        raw_resource_id = _resolve_selector(row, self.projection["resource_id"])
        if raw_resource_id is _MISSING:
            raw_resource_id = None
            malformed_fields.append("resource_id")
        if isinstance(raw_resource_id, str) and not resource_id_in_subscription(
            raw_resource_id,
            context.subscription_id,
        ):
            return None, self._failure(
                "source_scope_conflict",
                "Resource Graph row left the selected subscription",
            )
        resource_id = normalize_resource_id(
            raw_resource_id,
            context.resource_ids,
            subscription_id=context.subscription_id,
        )
        if resource_id is None:
            if isinstance(raw_resource_id, str) and context.resource_ids is not None:
                return None, None
            if context.resource_ids is None or len(context.resource_ids) != 1:
                return None, self._malformed("Resource Graph row has no projected resource id")
            resource_id = context.resource_ids[0]
        resource_type = _resolve_selector(row, self.projection["resource_type"])
        if not isinstance(resource_type, str) or not resource_type.strip():
            resource_type = None
            malformed_fields.append("resource_type")

        payload = dict(row)
        if resource_type is not None:
            payload["resource_type"] = resource_type
        status = EvidenceStatus.COMPLETE if not malformed_fields else EvidenceStatus.PARTIAL
        if self.source_kind == "aprl":
            raw_status = _resolve_selector(row, self.status_field or "")
            managed_status = self._normalize_status(raw_status)
            if managed_status == "unknown":
                malformed_fields.append("status")
            payload = managed_payload(
                payload,
                resource_type=resource_type,
                managed_status=managed_status,
            )
            if malformed_fields:
                status = EvidenceStatus.PARTIAL

        record = EvidenceRecord.create(
            source_kind=self.source_kind,
            source_reference=self.source_reference,
            source_version=self.source_version,
            resource_id=resource_id,
            status=status,
            payload=payload,
        )
        failure = (
            self._malformed(
                "Resource Graph row is missing or has an unsupported projected value: "
                + ", ".join(dict.fromkeys(malformed_fields))
            )
            if malformed_fields
            else None
        )
        return record, failure

    def _normalize_status(self, value: Any) -> str:
        if not isinstance(value, str):
            return "unknown"
        normalized = value.casefold()
        if normalized in self.pass_status_values:
            return "pass"
        if normalized in self.fail_status_values:
            return "fail"
        return "unknown"

    def _malformed(self, detail: str) -> CollectionFailure:
        return self._failure("source_malformed", detail)

    def _failure(self, reason_code: str, detail: str) -> CollectionFailure:
        return CollectionFailure(reason_code, self.source_kind, self.source_reference, detail=detail)


def _resolve_selector(payload: Mapping[str, Any], selector: str) -> Any:
    value: Any = payload
    for segment in selector.split("."):
        if not isinstance(value, Mapping) or segment not in value:
            return _MISSING
        value = value[segment]
    return value


def _normalize_allowed_statuses(
    values: tuple[str, ...] | None,
    field_name: str,
) -> frozenset[str]:
    if values is None or not values:
        raise ValueError(f"{field_name} must contain non-empty strings")
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError(f"{field_name} must contain non-empty strings")
    return frozenset(value.casefold() for value in values)


def resource_graph_source_version(query: str) -> str:
    query_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()
    return f"api-version:{RESOURCE_GRAPH_API_VERSION};query-sha256:{query_hash}"