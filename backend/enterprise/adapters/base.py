"""Evidence adapter 공통 계약과 ARM HTTP 전송 경계."""

from __future__ import annotations

import asyncio
import inspect
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable
from urllib.parse import parse_qs, quote, urlencode, urlsplit

import aiohttp

from enterprise.domain import EvidenceRecord, EvidenceStatus


ARM_HOST = "management.azure.com"
ARM_SCOPE = "https://management.azure.com/.default"
DEFAULT_CONNECT_TIMEOUT = 5.0
DEFAULT_READ_TIMEOUT = 30.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_RETRY_AFTER_CAP = 30.0
DEFAULT_COLLECTION_TIMEOUT = 120.0
DEFAULT_MAX_PAGES = 100
DEFAULT_TOKEN_TIMEOUT = 10.0
_PAGINATION_QUERY_KEYS = frozenset(
    {
        "$skiptoken",
        "skiptoken",
        "$skip",
        "skip",
        "$top",
        "top",
        "$maxpagesize",
        "maxpagesize",
        "continuationtoken",
        "$continuationtoken",
    }
)

_BEARER_PATTERN = re.compile(r"(?i)(bearer\s+)[^\s,;]+")
_SECRET_PATTERN = re.compile(
    r"(?i)(client_secret|access_token|api[_-]?key|authorization)(\s*[:=]\s*)([^\s,;]+)"
)


@runtime_checkable
class TokenCredential(Protocol):
    def get_token(self, *scopes: str, **kwargs: Any) -> Any: ...


@dataclass(frozen=True, kw_only=True)
class CollectionContext:
    tenant_id: str
    subscription_id: str
    resource_ids: tuple[str, ...] | None = None
    credential: TokenCredential
    collection_timeout: float = DEFAULT_COLLECTION_TIMEOUT
    max_pages: int = DEFAULT_MAX_PAGES
    monotonic: Callable[[], float] = field(default=time.monotonic, repr=False, compare=False)

    def __post_init__(self) -> None:
        for name in ("tenant_id", "subscription_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be empty")
        if not isinstance(self.credential, TokenCredential):
            raise ValueError("credential must provide get_token")
        if (
            isinstance(self.collection_timeout, bool)
            or not isinstance(self.collection_timeout, (int, float))
            or self.collection_timeout <= 0
        ):
            raise ValueError("collection_timeout must be positive and bounded")
        if isinstance(self.max_pages, bool) or not isinstance(self.max_pages, int) or self.max_pages < 1:
            raise ValueError("max_pages must be at least one")
        if not callable(self.monotonic):
            raise ValueError("monotonic must be callable")
        object.__setattr__(self, "collection_timeout", float(self.collection_timeout))
        if self.resource_ids is not None:
            if isinstance(self.resource_ids, (str, bytes)):
                raise ValueError("resource_ids must be a collection of ARM resource IDs")
            resource_ids = tuple(self.resource_ids)
            if any(not isinstance(resource_id, str) or not resource_id.strip() for resource_id in resource_ids):
                raise ValueError("resource_ids must not contain empty values")
            if any(
                _resource_subscription_id(resource_id) != self.subscription_id.casefold()
                for resource_id in resource_ids
            ):
                raise ValueError("resource_ids must belong to the selected subscription")
            object.__setattr__(self, "resource_ids", resource_ids)


@dataclass(frozen=True)
class CollectionFailure:
    reason_code: str
    source_kind: str
    source_reference: str
    status_code: int | None = None
    retry_after: float | None = None
    detail: str = ""

    def __post_init__(self) -> None:
        for name in ("reason_code", "source_kind", "source_reference"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be empty")
        if self.status_code is not None and not isinstance(self.status_code, int):
            raise ValueError("status_code must be an int or None")
        if self.retry_after is not None and self.retry_after < 0:
            raise ValueError("retry_after must not be negative")
        object.__setattr__(self, "detail", redact_detail(self.detail))


@dataclass
class CollectionBudget:
    deadline: float
    max_pages: int
    monotonic: Callable[[], float] = field(repr=False)
    pages_used: int = 0

    @classmethod
    def start(cls, context: CollectionContext) -> CollectionBudget:
        return cls(
            deadline=context.monotonic() + context.collection_timeout,
            max_pages=context.max_pages,
            monotonic=context.monotonic,
        )

    def remaining(self) -> float:
        return self.deadline - self.monotonic()


@dataclass(frozen=True)
class CollectionResult:
    evidence: tuple[EvidenceRecord, ...] = ()
    failures: tuple[CollectionFailure, ...] = ()
    partial: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.evidence, (str, bytes)) or isinstance(self.failures, (str, bytes)):
            raise ValueError("evidence and failures must be collections")
        evidence = tuple(self.evidence)
        failures = tuple(self.failures)
        if any(not isinstance(record, EvidenceRecord) for record in evidence):
            raise ValueError("evidence must contain EvidenceRecord values")
        if any(not isinstance(failure, CollectionFailure) for failure in failures):
            raise ValueError("failures must contain CollectionFailure values")
        if not isinstance(self.partial, bool):
            raise ValueError("partial must be a bool")
        is_partial = self.partial or bool(failures) or any(
            record.status is EvidenceStatus.PARTIAL for record in evidence
        )
        if is_partial:
            evidence = tuple(
                record
                if record.status is EvidenceStatus.PARTIAL
                else replace(record, status=EvidenceStatus.PARTIAL)
                for record in evidence
            )
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(self, "failures", failures)
        object.__setattr__(self, "partial", is_partial)


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    body: Any
    headers: Mapping[str, str]

    def __post_init__(self) -> None:
        if not isinstance(self.status_code, int):
            raise ValueError("status_code must be an int")
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))


class MalformedJsonError(ValueError):
    """응답 JSON을 안전하게 해석할 수 없음을 나타낸다."""


class HttpTransportError(ConnectionError):
    """HTTP 연결 계층이 응답을 받기 전에 실패했음을 나타낸다."""


class CredentialError(PermissionError):
    """주입된 credential이 ARM token을 제공하지 못했음을 나타낸다."""


class UntrustedNextLinkError(ValueError):
    """페이지 링크가 신뢰한 ARM host 경계를 벗어났음을 나타낸다."""


class ScopeValidationError(ValueError):
    """요청 또는 evidence가 선택한 subscription scope를 벗어났음을 나타낸다."""


@runtime_checkable
class AsyncHttpTransport(Protocol):
    async def request_json(
        self,
        method: str,
        url: str,
        *,
        credential: TokenCredential,
        json_body: Any = None,
    ) -> HttpResponse: ...


@runtime_checkable
class EvidenceAdapter(Protocol):
    async def collect(self, context: CollectionContext) -> CollectionResult: ...


class AioHttpTransport:
    def __init__(
        self,
        *,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        read_timeout: float = DEFAULT_READ_TIMEOUT,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        retry_after_cap: float = DEFAULT_RETRY_AFTER_CAP,
        token_timeout: float = DEFAULT_TOKEN_TIMEOUT,
        session_factory: Callable[..., Any] = aiohttp.ClientSession,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if connect_timeout <= 0 or read_timeout <= 0:
            raise ValueError("timeouts must be positive and bounded")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        if retry_after_cap < 0:
            raise ValueError("retry_after_cap must not be negative")
        if token_timeout <= 0:
            raise ValueError("token_timeout must be positive and bounded")
        self._connect_timeout = float(connect_timeout)
        self._read_timeout = float(read_timeout)
        self._max_attempts = max_attempts
        self._retry_after_cap = float(retry_after_cap)
        self._token_timeout = float(token_timeout)
        self._session_factory = session_factory
        self._sleep = sleep

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        credential: TokenCredential,
        json_body: Any = None,
    ) -> HttpResponse:
        validate_next_link(url, ARM_HOST)
        get_token = credential.get_token
        if not inspect.iscoroutinefunction(get_token):
            raise CredentialError("AioHttpTransport requires an async credential get_token")
        try:
            token = await asyncio.wait_for(
                get_token(ARM_SCOPE),
                timeout=self._token_timeout,
            )
            if inspect.isawaitable(token):
                token = await asyncio.wait_for(token, timeout=self._token_timeout)
        except CredentialError:
            raise
        except Exception:
            raise CredentialError("ARM credential acquisition failed") from None
        access_token = getattr(token, "token", None)
        if not isinstance(access_token, str) or not access_token:
            raise CredentialError("credential returned an invalid access token")

        timeout = aiohttp.ClientTimeout(
            total=self._connect_timeout + self._read_timeout,
            connect=self._connect_timeout,
            sock_connect=self._connect_timeout,
            sock_read=self._read_timeout,
        )
        authorization_headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        try:
            async with self._session_factory(timeout=timeout) as session:
                for attempt in range(1, self._max_attempts + 1):
                    async with session.request(
                        method.upper(),
                        url,
                        allow_redirects=False,
                        headers=authorization_headers,
                        json=json_body,
                    ) as response:
                        parse_error: ValueError | None = None
                        try:
                            body = await response.json(content_type=None)
                        except (aiohttp.ContentTypeError, ValueError) as exc:
                            body = None
                            parse_error = exc
                        headers = {
                            str(name): str(value)
                            for name, value in response.headers.items()
                            if str(name).casefold() != "authorization"
                        }
                        result = HttpResponse(response.status, body, headers)
                    if parse_error is not None:
                        if self._is_retryable(result.status_code) and attempt < self._max_attempts:
                            await self._sleep(self._retry_delay(result.headers, attempt))
                            continue
                        if not 200 <= result.status_code <= 299:
                            return result
                        raise MalformedJsonError("response body is not valid JSON") from parse_error
                    if not self._is_retryable(result.status_code) or attempt == self._max_attempts:
                        return result
                    await self._sleep(self._retry_delay(result.headers, attempt))
        except (asyncio.TimeoutError, TimeoutError):
            raise
        except aiohttp.ClientError:
            raise HttpTransportError("ARM HTTP transport failed before receiving a response") from None
        raise RuntimeError("HTTP retry loop terminated unexpectedly")

    @staticmethod
    def _is_retryable(status_code: int) -> bool:
        return status_code == 429 or 500 <= status_code <= 599

    def _retry_delay(self, headers: Mapping[str, str], attempt: int) -> float:
        retry_after = parse_retry_after(headers)
        if retry_after is not None:
            return min(retry_after, self._retry_after_cap)
        return min(0.5 * (2 ** (attempt - 1)), self._retry_after_cap)


@dataclass(frozen=True)
class PageCollection:
    pages: tuple[Mapping[str, Any], ...]
    failures: tuple[CollectionFailure, ...]


async def collect_json_pages(
    transport: AsyncHttpTransport,
    context: CollectionContext,
    *,
    method: str,
    url: str,
    source_kind: str,
    source_reference: str,
    json_body: Any = None,
    budget: CollectionBudget | None = None,
) -> PageCollection:
    pages: list[Mapping[str, Any]] = []
    failures: list[CollectionFailure] = []
    trusted_host = _trusted_host(url)
    expected_path, expected_api_version, expected_non_pagination_query = _initial_provenance(url)
    current_url = url
    seen: set[str] = set()
    budget = budget or CollectionBudget.start(context)

    while current_url:
        if current_url in seen:
            failures.append(
                CollectionFailure(
                    "source_pagination_loop",
                    source_kind,
                    source_reference,
                    detail="pagination nextLink repeated a previously requested URL",
                )
            )
            break
        seen.add(current_url)
        if budget.pages_used >= budget.max_pages:
            failures.append(
                CollectionFailure(
                    "source_page_limit",
                    source_kind,
                    source_reference,
                    detail="collection exceeded its bounded page limit",
                )
            )
            break
        remaining = budget.remaining()
        if remaining <= 0:
            failures.append(
                CollectionFailure(
                    "source_timeout",
                    source_kind,
                    source_reference,
                    detail="collection exceeded its total wall-clock timeout",
                )
            )
            break
        budget.pages_used += 1
        try:
            response = await asyncio.wait_for(
                transport.request_json(
                    method,
                    current_url,
                    credential=context.credential,
                    json_body=json_body if not pages else None,
                ),
                timeout=remaining,
            )
        except (asyncio.TimeoutError, TimeoutError):
            failures.append(
                CollectionFailure(
                    "source_timeout",
                    source_kind,
                    source_reference,
                    detail="source request exceeded its bounded timeout",
                )
            )
            break
        except MalformedJsonError:
            failures.append(
                CollectionFailure(
                    "source_malformed",
                    source_kind,
                    source_reference,
                    detail="source response was not valid JSON",
                )
            )
            break
        except HttpTransportError:
            failures.append(
                CollectionFailure(
                    "source_transport_error",
                    source_kind,
                    source_reference,
                    detail="source connection failed before receiving a response",
                )
            )
            break
        except CredentialError:
            failures.append(
                CollectionFailure(
                    "source_unauthorized",
                    source_kind,
                    source_reference,
                    detail="credential could not acquire an ARM access token",
                )
            )
            break

        if not 200 <= response.status_code <= 299:
            failures.append(failure_from_response(response, source_kind, source_reference))
            break
        if not isinstance(response.body, Mapping):
            failures.append(
                CollectionFailure(
                    "source_malformed",
                    source_kind,
                    source_reference,
                    status_code=response.status_code,
                    detail="source JSON root must be an object",
                )
            )
            break
        pages.append(response.body)

        next_link = response.body.get("nextLink", response.body.get("@odata.nextLink"))
        if next_link is None:
            break
        if not isinstance(next_link, str) or not next_link.strip():
            failures.append(
                CollectionFailure(
                    "source_malformed",
                    source_kind,
                    source_reference,
                    status_code=response.status_code,
                    detail="pagination nextLink must be a non-empty string",
                )
            )
            break
        try:
            validate_next_link(
                next_link,
                trusted_host,
                expected_subscription_id=context.subscription_id,
            )
        except ScopeValidationError:
            failures.append(
                CollectionFailure(
                    "source_scope_conflict",
                    source_kind,
                    source_reference,
                    detail="pagination nextLink left the selected subscription",
                )
            )
            break
        except UntrustedNextLinkError:
            failures.append(
                CollectionFailure(
                    "source_untrusted_next_link",
                    source_kind,
                    source_reference,
                    detail="pagination nextLink left the trusted ARM host",
                )
            )
            break
        try:
            _validate_next_link_provenance(
                next_link,
                expected_path=expected_path,
                expected_api_version=expected_api_version,
                expected_non_pagination_query=expected_non_pagination_query,
            )
        except ValueError:
            failures.append(
                CollectionFailure(
                    "source_provenance_conflict",
                    source_kind,
                    source_reference,
                    detail="pagination nextLink changed endpoint provenance",
                )
            )
            break
        if next_link in seen:
            failures.append(
                CollectionFailure(
                    "source_pagination_loop",
                    source_kind,
                    source_reference,
                    detail="pagination nextLink repeated a previously requested URL",
                )
            )
            break
        current_url = next_link

    return PageCollection(tuple(pages), tuple(failures))


def build_arm_url(resource_path: str, *, api_version: str, host: str = ARM_HOST) -> str:
    if not isinstance(resource_path, str) or not resource_path.startswith("/"):
        raise ValueError("resource_path must be an absolute ARM path")
    normalized_host = host.strip().casefold()
    if not normalized_host or any(character in normalized_host for character in "/?#@"):
        raise ValueError("host must be a hostname without credentials or path data")
    if not isinstance(api_version, str) or not api_version.strip():
        raise ValueError("api_version must not be empty")
    encoded_path = quote(resource_path, safe="/:()_-.")
    return f"https://{normalized_host}{encoded_path}?{urlencode({'api-version': api_version})}"


def validate_next_link(
    next_link: str,
    trusted_host: str,
    *,
    expected_subscription_id: str | None = None,
) -> None:
    parsed = urlsplit(next_link)
    hostname = (parsed.hostname or "").casefold()
    if (
        parsed.scheme.casefold() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or hostname not in {ARM_HOST, trusted_host.casefold()}
    ):
        raise UntrustedNextLinkError("nextLink must remain on a trusted HTTPS ARM host")
    if expected_subscription_id is not None:
        expected_segment = f"/subscriptions/{expected_subscription_id.casefold()}/"
        if expected_segment not in parsed.path.casefold():
            raise ScopeValidationError("nextLink must remain in the selected subscription")


def failure_from_response(
    response: HttpResponse,
    source_kind: str,
    source_reference: str,
) -> CollectionFailure:
    if 300 <= response.status_code <= 399:
        reason_code = "untrusted_redirect"
    elif response.status_code in {401, 403}:
        reason_code = "source_unauthorized"
    elif response.status_code == 429:
        reason_code = "source_throttled"
    else:
        reason_code = "source_http_error"
    return CollectionFailure(
        reason_code,
        source_kind,
        source_reference,
        status_code=response.status_code,
        retry_after=parse_retry_after(response.headers) if response.status_code == 429 else None,
        detail=f"source request failed with HTTP {response.status_code}",
    )


def parse_retry_after(headers: Mapping[str, str]) -> float | None:
    for name, value in headers.items():
        if name.casefold() == "retry-after":
            try:
                delay = float(value)
            except (TypeError, ValueError):
                return None
            return max(0.0, delay)
    return None


def redact_detail(detail: str) -> str:
    if not isinstance(detail, str):
        return "[REDACTED]"
    redacted = _BEARER_PATTERN.sub(r"\1[REDACTED]", detail)
    return _SECRET_PATTERN.sub(r"\1\2[REDACTED]", redacted)


def normalize_resource_id(
    resource_id: Any,
    resource_ids: Sequence[str] | None,
    *,
    subscription_id: str | None = None,
) -> str | None:
    if not isinstance(resource_id, str) or not resource_id.strip():
        return None
    if subscription_id is not None and not resource_id_in_subscription(resource_id, subscription_id):
        return None
    if resource_ids is None:
        return resource_id
    by_normalized_id = {candidate.casefold(): candidate for candidate in resource_ids}
    return by_normalized_id.get(resource_id.casefold())


def resource_id_in_subscription(resource_id: str, subscription_id: str) -> bool:
    return _resource_subscription_id(resource_id) == subscription_id.casefold()


def resource_type_from_id(resource_id: str) -> str | None:
    segments = tuple(segment for segment in resource_id.strip("/").split("/") if segment)
    provider_indexes = [index for index, segment in enumerate(segments) if segment.casefold() == "providers"]
    if not provider_indexes:
        return None
    provider_index = provider_indexes[-1]
    if len(segments) <= provider_index + 2:
        return None
    namespace = segments[provider_index + 1]
    type_segments = segments[provider_index + 2 :: 2]
    return "/".join((namespace, *type_segments)) if type_segments else None


def managed_payload(
    raw_record: Mapping[str, Any],
    *,
    resource_type: str | None,
    managed_status: str,
) -> dict[str, Any]:
    payload = dict(raw_record)
    if resource_type is not None:
        payload["resource_type"] = resource_type
    payload["managed_status"] = managed_status
    payload["verdict"] = {"status": managed_status}
    return payload


def selectors_present(payload: Mapping[str, Any], selectors: Sequence[str]) -> bool:
    for selector in selectors:
        value: Any = payload
        for segment in selector.split("."):
            if not isinstance(value, Mapping) or segment not in value:
                return False
            value = value[segment]
    return True


def evidence_status(payload: Mapping[str, Any], selectors: Sequence[str]) -> EvidenceStatus:
    return EvidenceStatus.COMPLETE if selectors_present(payload, selectors) else EvidenceStatus.PARTIAL


def _trusted_host(url: str) -> str:
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").casefold()
    if parsed.scheme.casefold() != "https" or not hostname:
        raise ValueError("ARM URL must use HTTPS and include a host")
    return hostname


def _initial_provenance(url: str) -> tuple[str, str, Mapping[str, tuple[str, ...]]]:
    parsed = urlsplit(url)
    path = parsed.path.casefold()
    api_version = _single_api_version(url)
    non_pagination_query = _non_pagination_query(parsed.query)
    return path, api_version, non_pagination_query


def _single_api_version(url: str) -> str:
    query = parse_qs(urlsplit(url).query, keep_blank_values=True)
    values = query.get("api-version")
    if values is None or len(values) != 1 or not isinstance(values[0], str) or not values[0]:
        raise ValueError("ARM URL must contain exactly one non-empty api-version query value")
    return values[0]


def _non_pagination_query(raw_query: str) -> Mapping[str, tuple[str, ...]]:
    parsed_query = parse_qs(raw_query, keep_blank_values=True)
    normalized: dict[str, tuple[str, ...]] = {}
    for key, values in parsed_query.items():
        key_folded = key.casefold()
        if key_folded in _PAGINATION_QUERY_KEYS or key_folded == "api-version":
            continue
        normalized[key_folded] = tuple(values)
    return MappingProxyType(normalized)


def _validate_next_link_provenance(
    next_link: str,
    *,
    expected_path: str,
    expected_api_version: str,
    expected_non_pagination_query: Mapping[str, tuple[str, ...]],
) -> None:
    parsed = urlsplit(next_link)
    if parsed.path.casefold() != expected_path:
        raise ValueError("nextLink path changed")
    if _single_api_version(next_link) != expected_api_version:
        raise ValueError("nextLink api-version changed")
    if dict(_non_pagination_query(parsed.query)) != dict(expected_non_pagination_query):
        raise ValueError("nextLink includes non-pagination query changes")


def _resource_subscription_id(resource_id: str) -> str | None:
    segments = tuple(segment for segment in resource_id.strip("/").split("/") if segment)
    for index, segment in enumerate(segments[:-1]):
        if segment.casefold() == "subscriptions":
            return segments[index + 1].casefold()
    return None