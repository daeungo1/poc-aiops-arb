"""Azure Policy Insights policy state evidence adapter."""

from __future__ import annotations

from collections.abc import Mapping

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


POLICY_STATES_API_VERSION = "2019-10-01"
POLICY_SOURCE_REFERENCE = "azure_policy.policy_states.latest"


class PolicyStatesAdapter:
    def __init__(
        self,
        transport: AsyncHttpTransport,
        *,
        policy_definition_id: str,
        assignment_id: str | None = None,
        definition_reference_id: str | None = None,
        source_reference: str = POLICY_SOURCE_REFERENCE,
        source_version: str = POLICY_STATES_API_VERSION,
    ) -> None:
        if not isinstance(policy_definition_id, str) or not policy_definition_id.strip():
            raise ValueError("policy_definition_id must not be empty")
        for field_name, value in (
            ("assignment_id", assignment_id),
            ("definition_reference_id", definition_reference_id),
        ):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{field_name} must be a non-empty string or None")
        if source_version != POLICY_STATES_API_VERSION:
            raise ValueError(
                f"source_version must match Policy States API version {POLICY_STATES_API_VERSION}"
            )
        self._transport = transport
        self.source_reference = source_reference
        self.source_version = source_version
        self._policy_definition_id = policy_definition_id.casefold()
        self._assignment_id = assignment_id.casefold() if assignment_id is not None else None
        self._definition_reference_id = (
            definition_reference_id.casefold() if definition_reference_id is not None else None
        )

    async def collect(self, context: CollectionContext) -> CollectionResult:
        path = (
            f"/subscriptions/{context.subscription_id}/providers/"
            "Microsoft.PolicyInsights/policyStates/latest/queryResults"
        )
        pages = await collect_json_pages(
            self._transport,
            context,
            method="POST",
            url=build_arm_url(path, api_version=POLICY_STATES_API_VERSION),
            source_kind="azure_policy",
            source_reference=self.source_reference,
            json_body={},
        )
        evidence: list[EvidenceRecord] = []
        failures = list(pages.failures)
        for page in pages.pages:
            values = page.get("value")
            if not isinstance(values, list):
                failures.append(self._malformed("Policy states value must be an array"))
                continue
            for value in values:
                if not isinstance(value, Mapping):
                    failures.append(self._malformed("Policy state must be an object"))
                    continue
                definition_decision = self._definition_decision(value)
                if definition_decision == "ignore":
                    continue
                if definition_decision == "malformed":
                    record = self._incomplete_identity_record(value, context)
                    failures.append(
                        self._malformed("Policy state policyDefinitionId is missing")
                    )
                    if record is not None:
                        evidence.append(record)
                    continue

                missing_identity_fields = self._missing_optional_identity_fields(value)
                if missing_identity_fields:
                    record = self._incomplete_identity_record(value, context)
                    failures.append(
                        self._malformed(
                            "Policy state is missing configured identity fields: "
                            + ", ".join(missing_identity_fields)
                        )
                    )
                    if record is not None:
                        evidence.append(record)
                    continue
                if not self._matches_optional_identities(value):
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

    def _definition_decision(self, policy_state: Mapping[str, object]) -> str:
        definition = policy_state.get("policyDefinitionId")
        if not isinstance(definition, str) or not definition.strip():
            return "malformed"
        if definition.casefold() != self._policy_definition_id:
            return "ignore"
        return "match"

    def _missing_optional_identity_fields(self, policy_state: Mapping[str, object]) -> tuple[str, ...]:
        missing: list[str] = []
        if self._assignment_id is not None:
            assignment = policy_state.get("policyAssignmentId")
            if not isinstance(assignment, str) or not assignment.strip():
                missing.append("policyAssignmentId")
        if self._definition_reference_id is not None:
            definition_reference = policy_state.get("policyDefinitionReferenceId")
            if not isinstance(definition_reference, str) or not definition_reference.strip():
                missing.append("policyDefinitionReferenceId")
        return tuple(missing)

    def _matches_optional_identities(self, policy_state: Mapping[str, object]) -> bool:
        if self._assignment_id is not None:
            assignment = policy_state.get("policyAssignmentId")
            if str(assignment).casefold() != self._assignment_id:
                return False
        if self._definition_reference_id is not None:
            definition_reference = policy_state.get("policyDefinitionReferenceId")
            if str(definition_reference).casefold() != self._definition_reference_id:
                return False
        return True

    def _incomplete_identity_record(
        self,
        policy_state: Mapping[str, object],
        context: CollectionContext,
    ) -> EvidenceRecord | None:
        resource_id = normalize_resource_id(
            policy_state.get("resourceId"),
            context.resource_ids,
            subscription_id=context.subscription_id,
        )
        if resource_id is None:
            return None
        resource_type = policy_state.get("resourceType")
        if not isinstance(resource_type, str) or not resource_type.strip():
            resource_type = resource_type_from_id(resource_id)
        else:
            resource_type = resource_type.lstrip("/")
        payload = managed_payload(
            policy_state,
            resource_type=resource_type,
            managed_status="unknown",
        )
        return EvidenceRecord.create(
            source_kind="azure_policy",
            source_reference=self.source_reference,
            source_version=self.source_version,
            resource_id=resource_id,
            status=EvidenceStatus.PARTIAL,
            payload=payload,
        )

    def _record(
        self,
        policy_state: Mapping[str, object],
        context: CollectionContext,
    ) -> tuple[EvidenceRecord | None, CollectionFailure | None]:
        raw_resource_id = policy_state.get("resourceId")
        if isinstance(raw_resource_id, str) and not resource_id_in_subscription(
            raw_resource_id,
            context.subscription_id,
        ):
            return None, self._failure(
                "source_scope_conflict",
                "Policy state left the selected subscription",
            )
        resource_id = normalize_resource_id(
            raw_resource_id,
            context.resource_ids,
            subscription_id=context.subscription_id,
        )
        if resource_id is None:
            if isinstance(raw_resource_id, str) and context.resource_ids is not None:
                return None, None
            return None, self._malformed("Policy state resource id is missing")
        resource_type = policy_state.get("resourceType")
        if not isinstance(resource_type, str) or not resource_type.strip():
            resource_type = resource_type_from_id(resource_id)
        else:
            resource_type = resource_type.lstrip("/")
        managed_status = _normalize_status(policy_state.get("complianceState"))
        payload = managed_payload(
            policy_state,
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
                source_kind="azure_policy",
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
        return CollectionFailure(reason_code, "azure_policy", self.source_reference, detail=detail)


def _normalize_status(value: object) -> str:
    if not isinstance(value, str):
        return "unknown"
    normalized = value.strip().casefold()
    if normalized == "compliant":
        return "pass"
    if normalized == "noncompliant":
        return "fail"
    return "unknown"

