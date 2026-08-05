from __future__ import annotations

import json
import threading
from dataclasses import FrozenInstanceError

import aiohttp
import pytest

from enterprise.adapters.base import (
    ARM_HOST,
    ARM_SCOPE,
    DEFAULT_COLLECTION_TIMEOUT,
    DEFAULT_MAX_PAGES,
    AioHttpTransport,
    CollectionContext,
    CollectionFailure,
    CollectionResult,
    CredentialError,
    HttpResponse,
    HttpTransportError,
    MalformedJsonError,
    ScopeValidationError,
    UntrustedNextLinkError,
    validate_next_link,
)

from .conftest import RESOURCE_ID, SUBSCRIPTION_ID, TENANT_ID, FakeCredential


class _FakeResponse:
    def __init__(self, status, body, headers=None, json_error=None):
        self.status = status
        self._body = body
        self.headers = headers or {}
        self._json_error = json_error

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def json(self, *, content_type=None):
        if self._json_error is not None:
            raise self._json_error
        return self._body


class _FakeSession:
    def __init__(self, responses, requests, timeout_holder):
        self._responses = responses
        self._requests = requests
        self._timeout_holder = timeout_holder

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def request(self, method, url, **kwargs):
        self._requests.append((method, url, kwargs))
        return self._responses.pop(0)


def _session_factory(responses, requests, timeout_holder):
    def factory(*, timeout):
        timeout_holder.append(timeout)
        return _FakeSession(responses, requests, timeout_holder)

    return factory


def test_collection_contracts_are_immutable_and_tuple_backed(credential):
    context = CollectionContext(
        tenant_id=TENANT_ID,
        subscription_id=SUBSCRIPTION_ID,
        resource_ids=[RESOURCE_ID],
        credential=credential,
    )
    result = CollectionResult(evidence=[], failures=[], partial=False)

    assert context.resource_ids == (RESOURCE_ID,)
    assert context.collection_timeout == DEFAULT_COLLECTION_TIMEOUT == 120.0
    assert context.max_pages == DEFAULT_MAX_PAGES == 100
    assert result.evidence == ()
    assert result.failures == ()
    with pytest.raises(FrozenInstanceError):
        context.subscription_id = "changed"


@pytest.mark.parametrize(
    ("field_name", "value"),
    [("collection_timeout", 0), ("max_pages", 0)],
)
def test_collection_context_rejects_unbounded_collection_limits(credential, field_name, value):
    kwargs = {
        "tenant_id": TENANT_ID,
        "subscription_id": SUBSCRIPTION_ID,
        "credential": credential,
        field_name: value,
    }

    with pytest.raises(ValueError, match=field_name):
        CollectionContext(**kwargs)


def test_collection_failure_redacts_bearer_and_secret_values():
    failure = CollectionFailure(
        reason_code="source_unauthorized",
        source_kind="advisor",
        source_reference="advisor.recommendations",
        status_code=403,
        detail="Authorization: Bearer top-secret-token client_secret=also-secret",
    )

    assert "top-secret-token" not in failure.detail
    assert "also-secret" not in failure.detail
    assert "[REDACTED]" in failure.detail


def test_collection_context_rejects_resource_id_from_another_subscription(credential):
    other_resource_id = RESOURCE_ID.replace(SUBSCRIPTION_ID, "99999999-8888-7777-6666-555555555555")

    with pytest.raises(ValueError, match="selected subscription"):
        CollectionContext(
            tenant_id=TENANT_ID,
            subscription_id=SUBSCRIPTION_ID,
            resource_ids=(other_resource_id,),
            credential=credential,
        )


def test_next_link_requires_exact_selected_subscription_path_segment():
    with pytest.raises(ScopeValidationError, match="selected subscription"):
        validate_next_link(
            "https://management.azure.com/providers/Microsoft.Storage/storageAccounts?api-version=1",
            ARM_HOST,
            expected_subscription_id=SUBSCRIPTION_ID,
        )


@pytest.mark.asyncio
async def test_aiohttp_transport_uses_arm_scope_bounded_timeouts_and_never_persists_token(caplog):
    secret = "transport-secret-token"
    credential = FakeCredential(secret)
    requests = []
    timeouts = []
    response = _FakeResponse(200, {"value": []})
    transport = AioHttpTransport(
        connect_timeout=2.0,
        read_timeout=5.0,
        session_factory=_session_factory([response], requests, timeouts),
    )

    actual = await transport.request_json(
        "GET",
        "https://management.azure.com/subscriptions/example?api-version=1",
        credential=credential,
    )

    assert actual == HttpResponse(status_code=200, body={"value": []}, headers={})
    assert credential.scopes == [ARM_SCOPE]
    assert timeouts[0].connect == 2.0
    assert timeouts[0].sock_read == 5.0
    assert requests[0][2]["allow_redirects"] is False
    assert requests[0][2]["headers"]["Authorization"] == f"Bearer {secret}"
    assert secret not in requests[0][1]
    assert secret not in repr(transport)
    assert secret not in caplog.text
    assert secret not in repr(actual)


@pytest.mark.asyncio
async def test_aiohttp_transport_retries_only_retryable_statuses_and_caps_retry_after(credential):
    requests = []
    timeouts = []
    sleeps = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    transport = AioHttpTransport(
        max_attempts=3,
        retry_after_cap=4.0,
        session_factory=_session_factory(
            [
                _FakeResponse(500, {"error": {}}),
                _FakeResponse(429, {"error": {}}, {"Retry-After": "120"}),
                _FakeResponse(200, {"value": []}),
            ],
            requests,
            timeouts,
        ),
        sleep=fake_sleep,
    )

    response = await transport.request_json(
        "GET",
        "https://management.azure.com/subscriptions/example?api-version=1",
        credential=credential,
    )

    assert response.status_code == 200
    assert len(requests) == 3
    assert sleeps == [0.5, 4.0]


@pytest.mark.asyncio
async def test_aiohttp_transport_does_not_retry_403(credential):
    requests = []
    timeouts = []
    transport = AioHttpTransport(
        max_attempts=3,
        session_factory=_session_factory(
            [_FakeResponse(403, {"error": {"code": "AuthorizationFailed"}})],
            requests,
            timeouts,
        ),
    )

    response = await transport.request_json(
        "GET",
        "https://management.azure.com/subscriptions/example?api-version=1",
        credential=credential,
    )

    assert response.status_code == 403
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_aiohttp_transport_rejects_untrusted_initial_url_before_getting_token(credential):
    requests = []
    timeouts = []
    transport = AioHttpTransport(
        session_factory=_session_factory([], requests, timeouts),
    )

    with pytest.raises(UntrustedNextLinkError, match="trusted HTTPS ARM host"):
        await transport.request_json(
            "GET",
            "https://attacker.example/collect",
            credential=credential,
        )

    assert credential.scopes == []
    assert requests == []


@pytest.mark.asyncio
async def test_aiohttp_transport_retries_non_json_retryable_response(credential):
    requests = []
    timeouts = []
    sleeps = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    transport = AioHttpTransport(
        max_attempts=2,
        session_factory=_session_factory(
            [
                _FakeResponse(
                    503,
                    None,
                    {"Retry-After": "2"},
                    json_error=json.JSONDecodeError("bad", "service unavailable", 0),
                ),
                _FakeResponse(200, {"value": []}),
            ],
            requests,
            timeouts,
        ),
        sleep=fake_sleep,
    )

    response = await transport.request_json(
        "GET",
        "https://management.azure.com/subscriptions/example?api-version=1",
        credential=credential,
    )

    assert response.status_code == 200
    assert len(requests) == 2
    assert sleeps == [2.0]


@pytest.mark.asyncio
async def test_aiohttp_transport_preserves_final_non_json_throttling_metadata(credential):
    requests = []
    timeouts = []
    malformed = json.JSONDecodeError("bad", "too many requests", 0)
    transport = AioHttpTransport(
        max_attempts=1,
        session_factory=_session_factory(
            [_FakeResponse(429, None, {"Retry-After": "9"}, json_error=malformed)],
            requests,
            timeouts,
        ),
    )

    response = await transport.request_json(
        "GET",
        "https://management.azure.com/subscriptions/example?api-version=1",
        credential=credential,
    )

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "9"
    assert response.body is None


@pytest.mark.asyncio
async def test_aiohttp_transport_redacts_connection_errors(credential):
    class FailingSession(_FakeSession):
        def request(self, method, url, **kwargs):
            raise aiohttp.ClientConnectionError("Authorization: Bearer connection-secret")

    def session_factory(*, timeout):
        return FailingSession([], [], [])

    transport = AioHttpTransport(session_factory=session_factory)

    with pytest.raises(HttpTransportError) as exc_info:
        await transport.request_json(
            "GET",
            "https://management.azure.com/subscriptions/example?api-version=1",
            credential=credential,
        )

    assert "connection-secret" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_aiohttp_transport_redacts_credential_acquisition_errors():
    class FailingCredential:
        def get_token(self, *scopes):
            raise RuntimeError("client_secret=credential-secret")

    transport = AioHttpTransport()

    with pytest.raises(CredentialError) as exc_info:
        await transport.request_json(
            "GET",
            "https://management.azure.com/subscriptions/example?api-version=1",
            credential=FailingCredential(),
        )

    assert "credential-secret" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_aiohttp_transport_bounds_sync_credential_acquisition_off_event_loop():
    class BlockingCredential:
        def get_token(self, *scopes):
            threading.Event().wait(0.02)
            return FakeCredential().get_token(*scopes)

    transport = AioHttpTransport(token_timeout=0.001)

    with pytest.raises(CredentialError, match="async credential"):
        await transport.request_json(
            "GET",
            "https://management.azure.com/subscriptions/example?api-version=1",
            credential=BlockingCredential(),
        )


@pytest.mark.asyncio
async def test_aiohttp_transport_rejects_sync_credentials_without_starting_threads(monkeypatch):
    class SyncCredential:
        def get_token(self, *scopes):
            return FakeCredential().get_token(*scopes)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("asyncio.to_thread must not be used")

    monkeypatch.setattr("enterprise.adapters.base.asyncio.to_thread", fail_if_called)
    transport = AioHttpTransport()

    with pytest.raises(CredentialError, match="async credential"):
        await transport.request_json(
            "GET",
            "https://management.azure.com/subscriptions/example?api-version=1",
            credential=SyncCredential(),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("token_value", [None, "", 42])
async def test_aiohttp_transport_normalizes_invalid_token_shape_as_credential_error(token_value):
    class InvalidTokenCredential:
        async def get_token(self, *scopes):
            class Token:
                token = token_value

            return Token()

    transport = AioHttpTransport()

    with pytest.raises(CredentialError, match="invalid access token"):
        await transport.request_json(
            "GET",
            "https://management.azure.com/subscriptions/example?api-version=1",
            credential=InvalidTokenCredential(),
        )


@pytest.mark.asyncio
async def test_aiohttp_transport_wraps_malformed_json_without_response_content(credential):
    requests = []
    timeouts = []
    malformed = json.JSONDecodeError("bad", "not-json", 0)
    transport = AioHttpTransport(
        session_factory=_session_factory(
            [_FakeResponse(200, None, json_error=malformed)],
            requests,
            timeouts,
        )
    )

    with pytest.raises(MalformedJsonError, match="response body is not valid JSON") as exc_info:
        await transport.request_json(
            "GET",
            "https://management.azure.com/subscriptions/example?api-version=1",
            credential=credential,
        )

    assert "not-json" not in str(exc_info.value)