from __future__ import annotations

from pathlib import Path

import pytest

from enterprise.adapters.base import CollectionContext, CollectionFailure, CollectionResult
from enterprise.domain import EvidenceRecord, EvidenceStatus, VerdictState
from enterprise.registry import ControlRegistry
from enterprise.repository import InMemoryEnterpriseRepository
from enterprise.service import EnterpriseAssessmentService, EnterpriseServiceError


ROOT = Path(__file__).resolve().parents[3]
SPIKE_ROOT = ROOT / "experiments/coverage_spike"
CHECKLIST_PATH = SPIKE_ROOT / "checklists/azure_storage_production_readiness.yaml"
MAPPING_PATH = SPIKE_ROOT / "mappings/azure_storage_production_readiness.yaml"


class DummyCredential:
    async def get_token(self, *_scopes, **_kwargs):
        class _T:
            token = "dummy"
            expires_on = 9999999999

        return _T()


class StubAdapter:
    def __init__(self, source_kind: str, source_reference: str, source_version: str):
        self.key = (source_kind, source_reference, source_version)
        self.calls: list[CollectionContext] = []
        self._result = CollectionResult()

    def set_result(self, result: CollectionResult) -> None:
        self._result = result

    async def collect(self, context: CollectionContext) -> CollectionResult:
        self.calls.append(context)
        return self._result


def _evidence(source_kind: str, source_reference: str, source_version: str, resource_id: str, payload: dict) -> EvidenceRecord:
    return EvidenceRecord.create(
        source_kind=source_kind,
        source_reference=source_reference,
        source_version=source_version,
        resource_id=resource_id,
        status=EvidenceStatus.COMPLETE,
        payload=payload,
    )


@pytest.fixture
def registry() -> ControlRegistry:
    return ControlRegistry.load(CHECKLIST_PATH, MAPPING_PATH)


@pytest.mark.asyncio
async def test_service_discovery_first_and_source_dedup(registry: ControlRegistry):
    repository = InMemoryEnterpriseRepository()
    adapters: dict[tuple[str, str, str], StubAdapter] = {}

    def adapter_factory(source, _transport):
        key = (source.source_kind, source.reference, source.version)
        adapters.setdefault(key, StubAdapter(*key))
        return adapters[key]

    discovery_control = registry.get("storage.secure_transfer")
    primary = discovery_control.primary_source
    discovered_id = "/subscriptions/sub-a/resourceGroups/rg/providers/Microsoft.Storage/storageAccounts/sa"
    adapters.setdefault((primary.source_kind, primary.reference, primary.version), StubAdapter(primary.source_kind, primary.reference, primary.version)).set_result(
        CollectionResult(
            evidence=(
                _evidence(
                    primary.source_kind,
                    primary.reference,
                    primary.version,
                    discovered_id,
                    {
                        "resource_type": "Microsoft.Storage/storageAccounts",
                        "properties": {
                            "supportsHttpsTrafficOnly": True,
                            "minimumTlsVersion": "TLS1_2",
                            "publicNetworkAccess": "Disabled",
                        },
                        "sku": {"name": "Standard_ZRS"},
                    },
                ),
            ),
        )
    )

    service = EnterpriseAssessmentService(
        registry=registry,
        repository=repository,
        transport=object(),
        credential=DummyCredential(),
        adapter_factory=adapter_factory,
    )

    run_id = await service.run_assessment(
        tenant_id="tenant-a",
        subscription_id="sub-a",
        resource_ids=None,
        control_keys=["storage.secure_transfer", "storage.minimum_tls"],
    )

    run = await repository.get_run(run_id, "sub-a")
    assert run is not None
    assert run.state in {"completed", "partial"}
    assert adapters[(primary.source_kind, primary.reference, primary.version)].calls


@pytest.mark.asyncio
async def test_service_explicit_resource_ids_without_discovery(registry: ControlRegistry):
    repository = InMemoryEnterpriseRepository()

    def adapter_factory(source, _transport):
        adapter = StubAdapter(source.source_kind, source.reference, source.version)
        adapter.set_result(CollectionResult())
        return adapter

    service = EnterpriseAssessmentService(
        registry=registry,
        repository=repository,
        transport=object(),
        credential=DummyCredential(),
        adapter_factory=adapter_factory,
    )

    target = "/subscriptions/sub-a/resourceGroups/rg/providers/Microsoft.Storage/storageAccounts/sa"
    run_id = await service.run_assessment(
        tenant_id="tenant-a",
        subscription_id="sub-a",
        resource_ids=[target],
        control_keys=["storage.secure_transfer"],
    )
    run = await repository.get_run(run_id, "sub-a")
    assert run is not None
    assert run.requested_resource_ids == (target,)


@pytest.mark.asyncio
async def test_service_zero_discovered_resources_completes_without_false_pass(registry: ControlRegistry):
    repository = InMemoryEnterpriseRepository()

    def adapter_factory(source, _transport):
        adapter = StubAdapter(source.source_kind, source.reference, source.version)
        adapter.set_result(CollectionResult(evidence=()))
        return adapter

    service = EnterpriseAssessmentService(
        registry=registry,
        repository=repository,
        transport=object(),
        credential=DummyCredential(),
        adapter_factory=adapter_factory,
    )

    run_id = await service.run_assessment("tenant-a", "sub-a", None, ["storage.secure_transfer"])
    run = await repository.get_run(run_id, "sub-a")
    assert run is not None
    assert run.state == "completed"
    assert run.verdict_counts[VerdictState.PASS.value] == 0
    assert run.verdict_counts[VerdictState.FAIL.value] == 0
    assert run.verdict_counts[VerdictState.UNKNOWN.value] == 0


@pytest.mark.asyncio
async def test_service_partial_collection_sets_partial_state_and_unknown_verdict(registry: ControlRegistry):
    repository = InMemoryEnterpriseRepository()

    control = registry.get("storage.secure_transfer")
    partial_evidence = EvidenceRecord.create(
        source_kind=control.primary_source.source_kind,
        source_reference=control.primary_source.reference,
        source_version=control.primary_source.version,
        resource_id="/subscriptions/sub-a/resourceGroups/rg/providers/Microsoft.Storage/storageAccounts/sa",
        status=EvidenceStatus.PARTIAL,
        payload={
            "resource_type": control.resource_type,
            "properties": {},
        },
    )

    def adapter_factory(source, _transport):
        adapter = StubAdapter(source.source_kind, source.reference, source.version)
        adapter.set_result(
            CollectionResult(
                evidence=(partial_evidence,),
                failures=(
                    CollectionFailure(
                        reason_code="source_partial",
                        source_kind=source.source_kind,
                        source_reference=source.reference,
                        status_code=206,
                        detail="partial collection",
                    ),
                ),
            )
        )
        return adapter

    service = EnterpriseAssessmentService(
        registry=registry,
        repository=repository,
        transport=object(),
        credential=DummyCredential(),
        adapter_factory=adapter_factory,
    )

    run_id = await service.run_assessment("tenant-a", "sub-a", None, ["storage.secure_transfer"])
    run = await repository.get_run(run_id, "sub-a")
    assert run is not None
    assert run.state == "partial"
    assert run.verdict_counts[VerdictState.UNKNOWN.value] >= 1
    assert run.collection_failures


@pytest.mark.asyncio
async def test_service_rejects_invalid_control_key(registry: ControlRegistry):
    service = EnterpriseAssessmentService(
        registry=registry,
        repository=InMemoryEnterpriseRepository(),
        transport=object(),
        credential=DummyCredential(),
    )
    with pytest.raises(EnterpriseServiceError, match="unknown control"):
        await service.run_assessment("tenant-a", "sub-a", None, ["unknown.control"])


@pytest.mark.asyncio
async def test_service_unexpected_exception_marks_run_failed(registry: ControlRegistry):
    repository = InMemoryEnterpriseRepository()

    def adapter_factory(source, _transport):
        class BrokenAdapter:
            async def collect(self, _context):
                raise RuntimeError("boom")

        return BrokenAdapter()

    service = EnterpriseAssessmentService(
        registry=registry,
        repository=repository,
        transport=object(),
        credential=DummyCredential(),
        adapter_factory=adapter_factory,
    )

    with pytest.raises(EnterpriseServiceError):
        await service.run_assessment("tenant-a", "sub-a", None, ["storage.secure_transfer"])

    runs = repository._list_runs_for_testing()
    assert runs
    assert runs[-1].state == "failed"
