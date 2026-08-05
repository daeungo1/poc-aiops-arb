"""Azure 관리형 evidence source adapter."""

from collections.abc import Mapping
from typing import Any

from enterprise.adapters.advisor import AdvisorAdapter
from enterprise.adapters.aprl import AprlAdapter
from enterprise.adapters.arm import ArmStorageAccountAdapter
from enterprise.adapters.base import (
    AioHttpTransport,
    AsyncHttpTransport,
    CollectionContext,
    CollectionFailure,
    CollectionResult,
    EvidenceAdapter,
)
from enterprise.adapters.defender import DefenderAdapter
from enterprise.adapters.policy import PolicyStatesAdapter
from enterprise.domain import EvidenceSource


def adapter_from_source(source: EvidenceSource, transport: AsyncHttpTransport) -> EvidenceAdapter:
    config = source.adapter_config
    if source.source_kind in {"arm", "storage_service"}:
        return _arm_storage_adapter_from_source(source, config, transport)
    if source.source_kind == "arg":
        return AprlAdapter(
            transport,
            query=_read_required_text(config, "query"),
            projection=_read_projection(config),
            source_kind="arg",
            source_reference=source.reference,
            source_version=source.version,
        )
    if source.source_kind == "aprl":
        return AprlAdapter(
            transport,
            query=_read_required_text(config, "query"),
            projection=_read_projection(config),
            status_field=_read_required_text(config, "status_field"),
            pass_status_values=_read_required_text_tuple(config, "pass_status_values"),
            fail_status_values=_read_required_text_tuple(config, "fail_status_values"),
            source_kind="aprl",
            source_reference=source.reference,
            source_version=source.version,
        )
    if source.source_kind == "azure_policy":
        return PolicyStatesAdapter(
            transport,
            policy_definition_id=_read_required_text(config, "policy_definition_id"),
            assignment_id=_read_optional_text(config, "assignment_id"),
            definition_reference_id=_read_optional_text(config, "definition_reference_id"),
            source_reference=source.reference,
            source_version=source.version,
        )
    if source.source_kind == "defender":
        return DefenderAdapter(
            transport,
            source_reference=source.reference,
            source_version=source.version,
            assessment_names=_read_required_text_tuple(config, "assessment_names"),
        )
    if source.source_kind == "advisor":
        return AdvisorAdapter(
            transport,
            source_reference=source.reference,
            source_version=source.version,
            recommendation_type_id=_read_required_text(config, "recommendation_type_id"),
        )
    raise ValueError(f"unsupported source kind for adapter factory: {source.source_kind}")


def _arm_storage_adapter_from_source(
    source: EvidenceSource,
    config: Mapping[str, Any],
    transport: AsyncHttpTransport,
) -> EvidenceAdapter:
    resource_detail = _read_required_text(config, "resource_detail")
    if source.source_kind == "arm" and resource_detail != "account":
        raise ValueError("arm source must set adapter_config.resource_detail to 'account'")
    if source.source_kind == "storage_service" and resource_detail != "blob_service":
        raise ValueError("storage_service source must set adapter_config.resource_detail to 'blob_service'")
    _read_required_text(config, "adapter")
    api_version = _read_required_text(config, "api_version")
    if source.version != api_version:
        raise ValueError("source.version must match adapter_config.api_version")
    return ArmStorageAccountAdapter(transport, resource_detail=resource_detail)


def _read_projection(config: Mapping[str, Any]) -> Mapping[str, str]:
    projection = config.get("projection")
    if not isinstance(projection, Mapping):
        raise ValueError("adapter_config.projection must be a mapping")
    if set(projection) != {"resource_id", "resource_type"}:
        raise ValueError("adapter_config.projection must contain resource_id and resource_type")
    resource_id_selector = _read_required_text(projection, "resource_id")
    resource_type_selector = _read_required_text(projection, "resource_type")
    return {
        "resource_id": resource_id_selector,
        "resource_type": resource_type_selector,
    }


def _read_required_text(config: Mapping[str, Any], key: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"adapter_config.{key} must be a non-empty string")
    return value


def _read_optional_text(config: Mapping[str, Any], key: str) -> str | None:
    value = config.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"adapter_config.{key} must be a non-empty string when provided")
    return value


def _read_required_text_tuple(config: Mapping[str, Any], key: str) -> tuple[str, ...]:
    values = config.get(key)
    if not isinstance(values, (list, tuple)) or not values:
        raise ValueError(f"adapter_config.{key} must be a non-empty sequence")
    normalized = tuple(_read_required_text({"value": value}, "value") for value in values)
    return normalized

__all__ = [
    "AioHttpTransport",
    "AsyncHttpTransport",
    "CollectionContext",
    "CollectionFailure",
    "CollectionResult",
    "EvidenceAdapter",
    "adapter_from_source",
]