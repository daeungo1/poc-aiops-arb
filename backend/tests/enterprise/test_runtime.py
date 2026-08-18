from __future__ import annotations

import importlib
import sys

import pytest


class _FakeToken:
    def __init__(self, token: str = "arm-token", expires_on: int = 4102444800):
        self.token = token
        self.expires_on = expires_on


class _DelegatedCredential:
    __enterprise_delegated_request__ = True

    def __init__(self):
        self.calls = []

    def get_token(self, *scopes, **kwargs):
        self.calls.append((scopes, kwargs))
        return _FakeToken()


class _DefaultCredentialLike:
    def get_token(self, *scopes, **kwargs):
        raise AssertionError("must not be called")


@pytest.mark.asyncio
async def test_async_delegated_request_credential_allows_only_delegated_credentials(monkeypatch):
    runtime = importlib.import_module("enterprise.runtime")
    delegated = _DelegatedCredential()
    monkeypatch.setattr(runtime, "get_effective_azure_credential", lambda: delegated)

    credential = runtime.AsyncDelegatedRequestCredential()
    token = await credential.get_token("https://management.azure.com/.default")
    assert token.token == "arm-token"
    assert delegated.calls

    monkeypatch.setattr(runtime, "get_effective_azure_credential", lambda: _DefaultCredentialLike())
    credential = runtime.AsyncDelegatedRequestCredential()
    with pytest.raises(PermissionError):
        await credential.get_token("https://management.azure.com/.default")


def test_runtime_singletons_are_shared_but_service_and_credential_are_fresh(monkeypatch):
    runtime = importlib.import_module("enterprise.runtime")
    runtime._reset_enterprise_runtime_for_tests()

    class FakeRegistry:
        controls = {"storage.secure_transfer": object()}

    class FakeRepository:
        pass

    class FakeTransport:
        pass

    monkeypatch.setattr("enterprise.registry.ControlRegistry.load", lambda *_args, **_kwargs: FakeRegistry())
    monkeypatch.setattr("enterprise.repository.InMemoryEnterpriseRepository", FakeRepository)
    monkeypatch.setattr("enterprise.postgres_repository.PostgresEnterpriseRepository", FakeRepository)
    monkeypatch.setattr("enterprise.adapters.base.AioHttpTransport", FakeTransport)
    monkeypatch.setattr("agent.db.connection.is_db_configured", lambda: False)

    cred1 = _DelegatedCredential()
    cred2 = _DelegatedCredential()
    service1 = runtime.get_enterprise_service(cred1)
    service2 = runtime.get_enterprise_service(cred2)

    assert service1 is not service2
    assert service1._registry is service2._registry
    assert service1._repository is service2._repository
    assert service1._transport is service2._transport
    assert service1._credential is cred1
    assert service2._credential is cred2


def test_importing_chat_enterprise_tools_does_not_import_agui_server():
    sys.modules.pop("agui_server", None)
    sys.modules.pop("chat.tools.enterprise", None)

    importlib.import_module("chat.tools.enterprise")

    assert "agui_server" not in sys.modules


def test_runtime_provider_used_by_router_matches_function_identity():
    runtime = importlib.import_module("enterprise.runtime")
    agui_server = importlib.import_module("agui_server")
    assert agui_server.get_enterprise_service is runtime.get_enterprise_service
