from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest


SUBSCRIPTION_ID = "11111111-2222-3333-4444-555555555555"
TENANT_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
RESOURCE_ID = (
    f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/Example-RG/"
    "providers/Microsoft.Storage/storageAccounts/ExampleStorage"
)


@dataclass
class FakeCredential:
    token: str = "test-access-token"

    def __post_init__(self) -> None:
        self.scopes: list[str] = []

    async def get_token(self, *scopes: str) -> SimpleNamespace:
        self.scopes.extend(scopes)
        return SimpleNamespace(token=self.token, expires_on=4_102_444_800)


class FakeTransport:
    def __init__(self, *responses: Any) -> None:
        self.responses = deque(responses)
        self.requests: list[dict[str, Any]] = []

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        credential: Any,
        json_body: Any = None,
    ) -> Any:
        self.requests.append(
            {
                "credential": credential,
                "json_body": json_body,
                "method": method,
                "url": url,
            }
        )
        response = self.responses.popleft()
        if isinstance(response, BaseException):
            raise response
        return response


@pytest.fixture
def credential() -> FakeCredential:
    return FakeCredential()


@pytest.fixture
def context(credential):
    from enterprise.adapters.base import CollectionContext

    return CollectionContext(
        tenant_id=TENANT_ID,
        subscription_id=SUBSCRIPTION_ID,
        resource_ids=(RESOURCE_ID,),
        credential=credential,
    )