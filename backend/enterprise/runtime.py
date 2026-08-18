"""Runtime composition root for enterprise assessment services."""

from __future__ import annotations

import inspect
import threading
from pathlib import Path
from typing import Any, Callable

from agent.azure_credential import get_effective_azure_credential
from agent.entra_sso import UserOboCredential
from enterprise.adapters.base import TokenCredential
from enterprise.service import EnterpriseAssessmentService

_runtime_lock = threading.Lock()
_registry_singleton: Any = None
_repository_singleton: Any = None
_transport_singleton: Any = None


class AsyncDelegatedRequestCredential(TokenCredential):
    """Async TokenCredential that delegates to the current request delegated credential only."""

    def __init__(
        self,
        credential_resolver: Callable[[], Any] | None = None,
    ) -> None:
        self._credential_resolver = credential_resolver or get_effective_azure_credential

    @staticmethod
    def _is_delegated_request_credential(credential: Any) -> bool:
        if isinstance(credential, UserOboCredential):
            return True
        return bool(getattr(credential, "__enterprise_delegated_request__", False))

    async def get_token(self, *scopes: str, **kwargs: Any) -> Any:
        credential = self._credential_resolver()
        if not self._is_delegated_request_credential(credential):
            raise PermissionError(
                "Delegated ARM credential is required for enterprise tools in chat scope"
            )

        token_or_awaitable = credential.get_token(*scopes, **kwargs)
        if inspect.isawaitable(token_or_awaitable):
            return await token_or_awaitable
        return token_or_awaitable


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_registry_singleton() -> Any:
    from enterprise.registry import ControlRegistry

    repo_root = _repo_root()
    checklist_path = (
        repo_root
        / "experiments"
        / "coverage_spike"
        / "checklists"
        / "azure_storage_production_readiness.yaml"
    )
    mapping_path = (
        repo_root
        / "experiments"
        / "coverage_spike"
        / "mappings"
        / "azure_storage_production_readiness.yaml"
    )
    return ControlRegistry.load(checklist_path, mapping_path)


def _load_repository_singleton() -> Any:
    from agent.db.connection import is_db_configured
    from enterprise.postgres_repository import PostgresEnterpriseRepository
    from enterprise.repository import InMemoryEnterpriseRepository

    if is_db_configured():
        return PostgresEnterpriseRepository()
    return InMemoryEnterpriseRepository()


def _load_transport_singleton() -> Any:
    from enterprise.adapters.base import AioHttpTransport

    return AioHttpTransport()


def get_enterprise_service(credential: TokenCredential) -> EnterpriseAssessmentService:
    """Build a request-scoped service with process-singleton registry/repository/transport."""

    global _registry_singleton
    global _repository_singleton
    global _transport_singleton

    with _runtime_lock:
        if _registry_singleton is None:
            _registry_singleton = _load_registry_singleton()
        if _repository_singleton is None:
            _repository_singleton = _load_repository_singleton()
        if _transport_singleton is None:
            _transport_singleton = _load_transport_singleton()

    return EnterpriseAssessmentService(
        registry=_registry_singleton,
        repository=_repository_singleton,
        transport=_transport_singleton,
        credential=credential,
    )


def _reset_enterprise_runtime_for_tests() -> None:
    """Reset process singletons for isolated unit tests."""

    global _registry_singleton
    global _repository_singleton
    global _transport_singleton
    with _runtime_lock:
        _registry_singleton = None
        _repository_singleton = None
        _transport_singleton = None
