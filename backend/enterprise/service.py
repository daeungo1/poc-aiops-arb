"""Enterprise deterministic assessment service orchestration."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any, Callable, Sequence

from enterprise.adapters import AsyncHttpTransport, adapter_from_source
from enterprise.adapters.base import CollectionContext, CollectionFailure, EvidenceAdapter
from enterprise.domain import ControlDefinition, EvidenceSource
from enterprise.evaluator import DeterministicEvaluator
from enterprise.registry import ControlRegistry
from enterprise.repository import (
    CollectionFailureInput,
    EnterpriseRepository,
    EvaluationVerdictInput,
)


_SUBSCRIPTION_RE = re.compile(r"/subscriptions/([^/]+)", re.IGNORECASE)


class EnterpriseServiceError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 503) -> None:
        super().__init__(message)
        self.status_code = status_code


class EnterpriseAssessmentService:
    def __init__(
        self,
        *,
        registry: ControlRegistry,
        repository: EnterpriseRepository,
        transport: AsyncHttpTransport,
        credential: Any,
        adapter_factory: Callable[[EvidenceSource, AsyncHttpTransport], EvidenceAdapter] = adapter_from_source,
    ) -> None:
        self._registry = registry
        self._repository = repository
        self._transport = transport
        self._credential = credential
        self._adapter_factory = adapter_factory
        self._evaluator = DeterministicEvaluator()

    async def run_assessment(
        self,
        tenant_id: str,
        subscription_id: str,
        resource_ids: Sequence[str] | None = None,
        control_keys: Sequence[str] | None = None,
    ) -> str:
        resolved_controls = self._resolve_controls(control_keys)
        normalized_resource_ids = self._normalize_resource_ids(resource_ids, subscription_id)
        await self._repository.register_controls(self._registry.controls)
        run_id = await self._repository.create_run(
            tenant_id=tenant_id,
            subscription_id=subscription_id,
            requested_resource_ids=normalized_resource_ids,
            control_keys=tuple(control.key for control in resolved_controls),
        )

        try:
            discovery_sources = self._discovery_sources(resolved_controls)
            collected_by_source: dict[tuple[str, str, str, str], tuple[Any, tuple[CollectionFailure, ...]]] = {}
            discovery_evidence = []
            discovery_failures: list[CollectionFailure] = []

            if normalized_resource_ids:
                target_ids = tuple(normalized_resource_ids)
            else:
                target_ids = ()
                for source in discovery_sources:
                    identity = _source_identity(source)
                    result = await self._collect_source(source, tenant_id, subscription_id, None)
                    collected_by_source[identity] = result
                    evidence, failures = result
                    discovery_evidence.extend(evidence)
                    discovery_failures.extend(failures)
                target_ids = tuple(dict.fromkeys(record.resource_id for record in discovery_evidence))
                if not target_ids:
                    await self._repository.complete_run(
                        run_id,
                        tuple(discovery_evidence),
                        (),
                        tuple(CollectionFailureInput(
                            reason_code=f.reason_code,
                            source_kind=f.source_kind,
                            source_reference=f.source_reference,
                            status_code=f.status_code,
                            retry_after=f.retry_after,
                            detail=f.detail,
                        ) for f in discovery_failures),
                    )
                    return run_id

            evidence_all = list(discovery_evidence)
            failures_all: list[CollectionFailure] = list(discovery_failures)

            for source in self._all_sources(resolved_controls):
                identity = _source_identity(source)
                if identity in collected_by_source:
                    continue
                result = await self._collect_source(
                    source,
                    tenant_id,
                    subscription_id,
                    tuple(target_ids),
                )
                collected_by_source[identity] = result
                evidence, failures = result
                evidence_all.extend(evidence)
                failures_all.extend(failures)

            evidence_by_resource: dict[str, list[Any]] = defaultdict(list)
            for record in evidence_all:
                evidence_by_resource[record.resource_id.casefold()].append(record)

            verdict_inputs: list[EvaluationVerdictInput] = []
            for resource_id in target_ids:
                resource_evidence = tuple(evidence_by_resource.get(resource_id.casefold(), ()))
                for control in resolved_controls:
                    verdict = self._evaluator.evaluate(control, resource_evidence)
                    verdict_inputs.append(
                        EvaluationVerdictInput(resource_id=resource_id, verdict=verdict)
                    )

            normalized_failures = tuple(
                CollectionFailureInput(
                    reason_code=failure.reason_code,
                    source_kind=failure.source_kind,
                    source_reference=failure.source_reference,
                    status_code=failure.status_code,
                    retry_after=failure.retry_after,
                    detail=failure.detail,
                )
                for failure in failures_all
            )
            await self._repository.complete_run(
                run_id,
                tuple(evidence_all),
                tuple(verdict_inputs),
                normalized_failures,
            )
            return run_id
        except EnterpriseServiceError:
            await self._repository.fail_run(run_id, "service_error")
            raise
        except Exception as exc:
            await self._repository.fail_run(run_id, "internal_error")
            raise EnterpriseServiceError("assessment execution failed", status_code=503) from exc

    async def get_run(self, run_id: str, subscription_id: str):
        return await self._repository.get_run(run_id, subscription_id)

    async def get_finding(self, finding_id: str, subscription_id: str):
        return await self._repository.get_finding(finding_id, subscription_id)

    async def list_controls(self):
        return await self._repository.list_controls()

    async def _collect_source(
        self,
        source: EvidenceSource,
        tenant_id: str,
        subscription_id: str,
        target_ids: tuple[str, ...] | None,
    ):
        adapter = self._adapter_factory(source, self._transport)
        context = CollectionContext(
            tenant_id=tenant_id,
            subscription_id=subscription_id,
            resource_ids=target_ids,
            credential=self._credential,
        )
        result = await adapter.collect(context)
        return result.evidence, result.failures

    def _resolve_controls(self, control_keys: Sequence[str] | None) -> tuple[ControlDefinition, ...]:
        if not control_keys:
            return tuple(self._registry.controls.values())
        controls: list[ControlDefinition] = []
        missing: list[str] = []
        for key in control_keys:
            control = self._registry.controls.get(key)
            if control is None:
                missing.append(key)
            else:
                controls.append(control)
        if missing:
            raise EnterpriseServiceError(f"unknown control keys: {', '.join(sorted(missing))}", status_code=422)
        return tuple(controls)

    @staticmethod
    def _normalize_resource_ids(
        resource_ids: Sequence[str] | None,
        subscription_id: str,
    ) -> tuple[str, ...]:
        if not resource_ids:
            return ()
        normalized: list[str] = []
        expected = subscription_id.casefold()
        for resource_id in resource_ids:
            if not isinstance(resource_id, str) or not resource_id.strip():
                raise EnterpriseServiceError("resource_ids must not contain empty values", status_code=422)
            match = _SUBSCRIPTION_RE.search(resource_id)
            if match is None or match.group(1).casefold() != expected:
                raise EnterpriseServiceError("resource_id does not belong to selected subscription", status_code=422)
            normalized.append(resource_id.strip())
        return tuple(dict.fromkeys(normalized))

    @staticmethod
    def _discovery_sources(controls: Sequence[ControlDefinition]) -> tuple[EvidenceSource, ...]:
        sources = []
        for control in controls:
            source = control.primary_source
            if source.source_kind != "arm":
                continue
            detail = source.adapter_config.get("resource_detail")
            if detail != "account":
                continue
            sources.append(source)
        unique: dict[tuple[str, str, str, str], EvidenceSource] = {}
        for source in sources:
            unique[_source_identity(source)] = source
        return tuple(unique.values())

    @staticmethod
    def _all_sources(controls: Sequence[ControlDefinition]) -> tuple[EvidenceSource, ...]:
        unique: dict[tuple[str, str, str, str], EvidenceSource] = {}
        for control in controls:
            for source in control.sources:
                unique[_source_identity(source)] = source
        return tuple(unique.values())


def _source_identity(source: EvidenceSource) -> tuple[str, str, str, str]:
    config = json.dumps(dict(source.adapter_config), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return (source.source_kind, source.reference, source.version, config)
