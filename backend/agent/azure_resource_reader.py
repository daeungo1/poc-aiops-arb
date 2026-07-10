"""
Azure Resource Reader Module
Azure Resource Graph를 사용하여 리소스 정보를 수집합니다.
"""

import base64
import json
import logging
import os
from functools import lru_cache
from typing import Any, Optional

from dataclasses import dataclass, field

from azure.core.exceptions import (
    ClientAuthenticationError,
    HttpResponseError,
    ResourceNotFoundError,
)
from azure.mgmt.resourcegraph import ResourceGraphClient
from azure.mgmt.resourcegraph.models import QueryRequest, QueryRequestOptions
from azure.mgmt.subscription import SubscriptionClient

from .azure_credential import (
    RESOURCE_READER_UAMI_CLIENT_ID_ENV,
    RESOURCE_READER_UAMI_NAME_ENV,
    RESOURCE_READER_UAMI_OBJECT_ID_ENV,
    RESOURCE_READER_UAMI_RESOURCE_ID_ENV,
    get_default_azure_credential,
    get_effective_azure_credential,
    get_resource_reader_azure_credential,
)
from .subscription_scope import normalize_subscription_id

logger = logging.getLogger(__name__)

def _subscription_state_str(state: Any) -> str:
    if state is None:
        return ""
    if hasattr(state, "value"):
        return str(state.value)
    return str(state)


def _subscription_to_account_dict(sub: Any) -> dict[str, Any]:
    return {
        "id": sub.subscription_id or "",
        "name": sub.display_name or "",
        "tenantId": sub.tenant_id or "",
        "state": _subscription_state_str(sub.state),
    }


def _management_token_payload(credential: Any) -> dict[str, Any]:
    """https://management.azure.com/.default 토큰 JWT payload (실패 시 {})."""
    try:
        tok = credential.get_token("https://management.azure.com/.default")
        parts = tok.token.split(".")
        if len(parts) < 2:
            return {}
        payload_b64 = parts[1]
        pad = (4 - len(payload_b64) % 4) % 4
        raw = base64.urlsafe_b64decode(payload_b64 + "=" * pad)
        return json.loads(raw)
    except Exception:
        return {}


def _tenant_id_from_management_token(credential: Any) -> str:
    """AAD 액세스 토큰의 tid(또는 동등 클레임). list/get 응답에 tenantId가 없을 때 폴백."""
    payload = _management_token_payload(credential)
    tid = (
        payload.get("tid")
        or payload.get("tenant_id")
        or payload.get("http://schemas.microsoft.com/identity/claims/tenantid")
    )
    return str(tid).strip() if tid else ""


def _list_subscriptions_raw(credential: Any) -> list[dict[str, Any]]:
    """ARM list에 tenantId가 비면 단건 subscriptions.get 으로 실제 디렉터리를 채운 뒤,
    마지막 수단으로만 management 토큰 tid 폴백.

    과거 순서(token tid로 먼저 채움)에서는 list 응답에 테넌트 필드가 비어 있는 항목이
    사용자 홈 테넌트로 잘못 태깅되어, 교집합(OBO∩MI) UI가 합집합처럼 보였다.

    브라우저 세션과 동일한 테넌트는 대부분 get 후에도 채워지거나, 극히 드물게 폴백된다.
    """
    client = SubscriptionClient(credential)
    items = [_subscription_to_account_dict(s) for s in client.subscriptions.list()]
    token_tid = _tenant_id_from_management_token(credential)
    for entry in items:
        if (entry.get("tenantId") or "").strip():
            continue
        sid = normalize_subscription_id(str(entry.get("id") or ""))
        if not sid:
            continue
        try:
            detail = client.subscriptions.get(sid)
            tid = detail.tenant_id
            if tid:
                entry["tenantId"] = str(tid)
        except ResourceNotFoundError:
            logger.warning("구독 단건 조회 실패(404): subscription_id=%s", sid)
        except HttpResponseError as e:
            logger.warning(
                "구독 단건 조회 실패: subscription_id=%s status=%s",
                sid,
                getattr(e, "status_code", None),
            )
        except ClientAuthenticationError:
            raise
    for entry in items:
        if (entry.get("tenantId") or "").strip() or not token_tid:
            continue
        entry["tenantId"] = token_tid
    return items


def _list_subscription_entries_cached(credential: Any) -> list[dict[str, Any]]:
    cred = credential or get_effective_azure_credential()
    return _list_subscriptions_raw(cred)


def _cached_subscription_tenant(want: str) -> str:
    """tenantId 또는 빈 문자열(미존재·거부). 응답에 tenantId가 없으면 토큰 tid 폴백.

    사용자별 SSO 토큰이 달라질 수 있어 프로세스 전역 lru_cache는 사용하지 않습니다.
    """
    cred = get_effective_azure_credential()
    try:
        sub = SubscriptionClient(cred).subscriptions.get(want)
    except ResourceNotFoundError:
        return ""
    except HttpResponseError as e:
        if getattr(e, "status_code", None) in (403, 404):
            return ""
        raise RuntimeError(f"구독 테넌트 조회 API 오류: {e}") from e
    tid = sub.tenant_id
    if tid:
        return str(tid)
    return _tenant_id_from_management_token(cred)


def _principal_hint_from_credential(credential: Any) -> str:
    """액세스 토큰 클레임에서 사람이 읽을 수 있는 주체 힌트(upn 등)."""
    payload = _management_token_payload(credential)
    return str(
        payload.get("upn")
        or payload.get("unique_name")
        or payload.get("preferred_username")
        or ""
    )


def _pick_bootstrap_subscription_from_candidates(
    candidates: list[dict[str, Any]], env_sub_normalized: str
) -> dict[str, Any]:
    """AZURE_SUBSCRIPTION_ID(교집합 내) → Enabled 첫 항목 → 목록 첫 항목."""
    chosen: dict[str, Any] | None = None
    if env_sub_normalized:
        for a in candidates:
            if normalize_subscription_id(str(a.get("id") or "")) == env_sub_normalized:
                chosen = a
                break
    if not chosen:
        for a in candidates:
            if str(a.get("state") or "").lower() == "enabled":
                chosen = a
                break
    if not chosen:
        chosen = candidates[0]
    return chosen


@dataclass
class AzureResource:
    """Azure 리소스 정보를 담는 데이터 클래스"""
    id: str
    name: str
    type: str
    resource_group: str
    subscription_id: str
    location: str
    sku: Optional[dict] = None
    properties: dict = field(default_factory=dict)
    tags: dict = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "resource_group": self.resource_group,
            "subscription_id": self.subscription_id,
            "location": self.location,
            "sku": self.sku,
            "properties": self.properties,
            "tags": self.tags
        }


class AzureResourceReader:
    """
    Azure Resource Graph를 사용하여 리소스 정보를 읽어오는 클래스
    """
    
    # 평가 대상 리소스 타입
    SUPPORTED_RESOURCE_TYPES = {
        # Compute
        "microsoft.compute/virtualmachines": "Virtual Machines",
        "microsoft.compute/virtualmachinescalesets": "VM Scale Sets",
        "microsoft.containerservice/managedclusters": "AKS Clusters",
        "microsoft.web/sites": "App Services",
        "microsoft.containerregistry/registries": "Container Registries",
        
        # Database
        "microsoft.dbformysql/flexibleservers": "MySQL Flexible Servers",
        "microsoft.dbforpostgresql/flexibleservers": "PostgreSQL Flexible Servers",
        "microsoft.documentdb/databaseaccounts": "Cosmos DB Accounts",
        "microsoft.sql/servers": "SQL Servers",
        "microsoft.sql/servers/databases": "SQL Databases",
        
        # Networking
        "microsoft.network/virtualnetworks": "Virtual Networks",
        "microsoft.network/networksecuritygroups": "Network Security Groups",
        "microsoft.network/applicationgateways": "Application Gateways",
        "microsoft.network/loadbalancers": "Load Balancers",
        "microsoft.network/publicipaddresses": "Public IP Addresses",
        "microsoft.network/privateendpoints": "Private Endpoints",
        "microsoft.network/bastionhosts": "Bastion Hosts",
        "microsoft.cdn/profiles": "CDN Profiles",
        "microsoft.network/frontdoors": "Front Doors",
        "microsoft.network/trafficmanagerprofiles": "Traffic Manager Profiles",
        
        # App Configuration
        "microsoft.appconfiguration/configurationstores": "App Configuration Stores",
        
        # Storage
        "microsoft.storage/storageaccounts": "Storage Accounts",
        
        # Monitoring & Security
        "microsoft.insights/components": "Application Insights",
        "microsoft.operationalinsights/workspaces": "Log Analytics Workspaces",
        "microsoft.insights/autoscalesettings": "Autoscale Settings",
        "microsoft.insights/metricalerts": "Metric Alerts",
        "microsoft.keyvault/vaults": "Key Vaults",
        "microsoft.recoveryservices/vaults": "Recovery Services Vaults",
        
        # AI & Search (추가)
        "microsoft.cognitiveservices/accounts": "Azure AI Services",
        "microsoft.search/searchservices": "Azure AI Search",
        "microsoft.machinelearningservices/workspaces": "Machine Learning Workspaces",

        # Identity
        "microsoft.authorization/roleassignments": "Role Assignments",
    }
    
    @staticmethod
    def get_session_bootstrap(credential: Optional[Any] = None) -> dict:
        """
        세션 부트스트랩용 기본 구독·테넌트·사용자 힌트.

        웹 위임 경로에서는 HttpOnly ARM(로그인 사용자) 토큰으로, 그 외는 get_effective(기본 Default)로
        구독 목록을 조회한 뒤 하나를 "현재" 구독으로 선택합니다.

        기본값은 **사용자(OBO) 목록 ∩ 리소스 조회용 MI가 볼 수 있는 구독** 중에서 고릅니다
        (GET /api/azure/subscriptions 교집합과 동일 원칙).

        선택 순서 (교집합 후보 내): ``AZURE_SUBSCRIPTION_ID`` (교집합에 있을 때만) → Enabled 첫 항목
        → 교집합에서 ARM 목록 순서의 첫 항목.

        MI 구독 목록을 가져오지 못하면(로컬 등) 경고 후 **사용자 전체 목록**으로 이전 동작에 폴백한다.

        Returns:
            {"subscription_id": str, "name": str, "tenant_id": str, "state": str, "user": str}
        """
        cred = credential or get_effective_azure_credential()
        try:
            entries = _list_subscription_entries_cached(cred)
        except ClientAuthenticationError as e:
            raise RuntimeError(
                "Azure 구독 정보를 가져올 수 없습니다. Entra SSO 로그인·OBO 권한을 확인하세요.\n"
                f"{e}"
            ) from e
        except HttpResponseError as e:
            raise RuntimeError(f"Azure 구독 API 오류: {e}") from e

        if not entries:
            raise RuntimeError("접근 가능한 Azure 구독이 없습니다. 계정 권한을 확인하세요.")

        env_sub = normalize_subscription_id((os.environ.get("AZURE_SUBSCRIPTION_ID") or "").strip())

        mi_ids: Optional[set[str]] = None
        try:
            mi_ids = AzureResourceReader.list_mi_accessible_subscription_ids()
        except RuntimeError as e:
            logger.warning(
                "get_session_bootstrap: MI 구독 목록 실패, 사용자 목록만으로 기본 구독 선택합니다. detail=%s",
                e,
            )

        candidates: list[dict[str, Any]]
        if mi_ids is not None:
            intersected = [
                e for e in entries if normalize_subscription_id(str(e.get("id") or "")) in mi_ids
            ]
            if intersected:
                candidates = intersected
            elif not mi_ids:
                logger.warning(
                    "get_session_bootstrap: MI 접근 가능 구독이 비어 있어 사용자 전체 목록으로 선택합니다."
                )
                candidates = entries
            else:
                raise RuntimeError(
                    "로그인 사용자 구독과 리소스 조회용 MI(UAMI/System MI) 접근 가능 구독의 교집합이 비어 있습니다. "
                    "백엔드 MI에 해당 구독에서 Reader 역할 등을 부여했는지 확인하세요."
                )
        else:
            candidates = entries

        chosen = _pick_bootstrap_subscription_from_candidates(candidates, env_sub)

        user_hint = _principal_hint_from_credential(cred)
        tid_out = (chosen.get("tenantId") or "").strip()
        if not tid_out:
            tid_out = _tenant_id_from_management_token(cred)
        return {
            "subscription_id": chosen.get("id", ""),
            "name": chosen.get("name", ""),
            "tenant_id": tid_out,
            "state": chosen.get("state", ""),
            "user": user_hint,
        }

    @staticmethod
    def list_account_entries(credential: Optional[Any] = None) -> list[dict[str, Any]]:
        """구독 목록(ARM ``/subscriptions``). CLI ``az account list``와 동일한 키(id, name, tenantId, state) 형태."""
        cred = credential or get_effective_azure_credential()
        try:
            return _list_subscription_entries_cached(cred)
        except ClientAuthenticationError as e:
            raise RuntimeError(
                "Azure 구독 목록을 가져올 수 없습니다. Entra SSO·OBO를 확인하세요.\n"
                f"{e}"
            ) from e
        except HttpResponseError as e:
            raise RuntimeError(f"Azure 구독 API 오류: {e}") from e

    @staticmethod
    def list_subscriptions_for_tenant(tenant_id: str) -> list[dict[str, str]]:
        """해당 테넌트에 속한 구독 목록 (전체 목록에서 필터)."""
        want_t = (tenant_id or "").strip().lower().replace("{", "").replace("}", "")
        entries = AzureResourceReader.list_account_entries()
        out: list[dict[str, str]] = []
        for a in entries:
            tid = (a.get("tenantId") or "").strip().lower().replace("{", "").replace("}", "")
            if tid != want_t:
                continue
            out.append(
                {
                    "subscription_id": a.get("id", ""),
                    "name": a.get("name", ""),
                    "state": a.get("state", ""),
                    "tenant_id": (a.get("tenantId") or "").strip() or want_t,
                }
            )
        return out

    @staticmethod
    def get_self_mi_info() -> dict[str, str]:
        """백엔드 리소스 조회용 MI 정보를 반환한다.

        타 구독 IAM에 MI를 등록할 때 필요한 식별 정보:
            - UAMI env가 있으면 AZURE_RESOURCE_READER_UAMI_* 값을 그대로 반환
            - resource_name: Web App 리소스명 (env ``WEBSITE_SITE_NAME``)
            - subscription_id: 백엔드가 배포된 구독 ID (env ``WEBSITE_OWNER_NAME`` 파싱)
            - subscription_name: 구독 표시명 (ARM SubscriptionClient)
            - object_id: MI 서비스 주체의 Object ID (Resource Graph identity.principalId)

        App Service 외부(로컬 개발 등)에서는 env가 비어 빈 문자열을 반환할 수 있다.
        """
        uami_client_id = (os.environ.get(RESOURCE_READER_UAMI_CLIENT_ID_ENV) or "").strip()
        if uami_client_id:
            resource_id = (os.environ.get(RESOURCE_READER_UAMI_RESOURCE_ID_ENV) or "").strip()
            subscription_id = ""
            if resource_id:
                parts = resource_id.strip("/").split("/")
                for idx, part in enumerate(parts):
                    if part.lower() == "subscriptions" and idx + 1 < len(parts):
                        subscription_id = normalize_subscription_id(parts[idx + 1])
                        break
            return {
                "resource_name": (os.environ.get(RESOURCE_READER_UAMI_NAME_ENV) or "").strip(),
                "subscription_id": subscription_id,
                "subscription_name": "",
                "object_id": (os.environ.get(RESOURCE_READER_UAMI_OBJECT_ID_ENV) or "").strip(),
                "uami_client_id": uami_client_id,
                "uami_object_id": (os.environ.get(RESOURCE_READER_UAMI_OBJECT_ID_ENV) or "").strip(),
                "uami_resource_id": resource_id,
                "uami_name": (os.environ.get(RESOURCE_READER_UAMI_NAME_ENV) or "").strip(),
            }

        site_name = (os.environ.get("WEBSITE_SITE_NAME") or "").strip()
        owner_name = (os.environ.get("WEBSITE_OWNER_NAME") or "").strip()

        # WEBSITE_OWNER_NAME 형식: "{subscription_id}+{rg}-{region}webspace"
        subscription_id = ""
        if "+" in owner_name:
            subscription_id = owner_name.split("+", 1)[0].strip()
        subscription_id = normalize_subscription_id(subscription_id)

        result: dict[str, str] = {
            "resource_name": site_name,
            "subscription_id": subscription_id,
            "subscription_name": "",
            "object_id": "",
            "uami_client_id": "",
            "uami_object_id": "",
            "uami_resource_id": "",
            "uami_name": "",
        }
        if not site_name or not subscription_id:
            return result

        cred = get_default_azure_credential()

        try:
            sub = SubscriptionClient(cred).subscriptions.get(subscription_id)
            result["subscription_name"] = sub.display_name or ""
        except (ResourceNotFoundError, HttpResponseError) as e:
            logger.warning(
                "get_self_mi_info: 구독 표시명 조회 실패. subscription_id=%s detail=%s",
                subscription_id, e,
            )

        try:
            site_esc = AzureResourceReader._escape_kql_literal(site_name)
            query = (
                "Resources\n"
                "| where type =~ 'microsoft.web/sites'\n"
                f"| where name =~ '{site_esc}'\n"
                "| project identity"
            )
            req = QueryRequest(
                subscriptions=[subscription_id],
                query=query,
                options=QueryRequestOptions(top=1),
            )
            resp = ResourceGraphClient(cred).resources(req)
            if resp.data:
                identity = resp.data[0].get("identity") or {}
                principal_id = identity.get("principalId") if isinstance(identity, dict) else None
                result["object_id"] = str(principal_id or "")
        except HttpResponseError as e:
            logger.warning(
                "get_self_mi_info: Resource Graph identity 조회 실패. site=%s detail=%s",
                site_name, e,
            )

        return result

    @staticmethod
    def list_mi_accessible_subscription_ids() -> set[str]:
        """리소스 조회용 MI(UAMI 설정 시 UAMI, 아니면 Default)로 접근 가능한 구독 ID 집합.

        HYBRID_AUTH_PLAN.md Step 1: 타 구독 IAM에서 백엔드 MI에 Reader 권한을
        부여한 구독들만 SubscriptionClient.subscriptions.list()에 노출된다.
        OBO 구독 목록과의 교집합 필터에 사용.
        """
        try:
            client = SubscriptionClient(get_resource_reader_azure_credential())
            return {
                normalize_subscription_id(str(s.subscription_id))
                for s in client.subscriptions.list()
                if s.subscription_id
            }
        except ClientAuthenticationError as e:
            raise RuntimeError(
                "MI 자격 증명으로 구독 목록을 가져올 수 없습니다. "
                "App Service의 관리 ID 또는 AZURE_RESOURCE_READER_UAMI_CLIENT_ID 설정을 확인하세요.\n"
                f"{e}"
            ) from e
        except HttpResponseError as e:
            raise RuntimeError(f"MI 구독 API 오류: {e}") from e

    @staticmethod
    def resolve_subscription_tenant(subscription_id: str) -> str | None:
        """구독 ID에 해당하는 tenantId (SubscriptionClient.get, 프로세스 내 캐시)."""
        want = normalize_subscription_id(subscription_id)
        if not want:
            return None
        try:
            tid = _cached_subscription_tenant(want)
        except ClientAuthenticationError as e:
            raise RuntimeError(
                f"구독 테넌트를 조회할 수 없습니다. Entra SSO·OBO를 확인하세요.\n{e}"
            ) from e
        return tid if tid else None

    def __init__(
        self,
        subscription_ids: Optional[list[str]] = None,
        credential: Optional[Any] = None,
        tenant_id: Optional[str] = None,
    ):
        """
        Args:
            subscription_ids: 조회할 구독 ID 목록. None이면 MI 기준 기본 구독 사용
            credential: 리소스 조회에 사용할 credential. None이면 리소스 조회 전용
                MI(UAMI 설정 시 UAMI, 아니면 DefaultAzureCredential)를 사용한다.
            tenant_id: 설정 시 Resource Graph 쿼리에 tenantId 조건 추가 (UI 세션 등)
        """
        # 사용자 권한(Owner 등)과 무관하게 리소스 조회용 MI Reader 권한으로 조회되도록 고정.
        # 교집합 검증(OBO ∩ MI)을 통과한 구독에 대해서만 호출되는 것이 전제.
        self.credential = credential or get_resource_reader_azure_credential()
        self._tenant_id_filter = (tenant_id or "").strip() or None

        # subscription_ids가 없으면 환경 변수·Enabled 우선으로 기본 구독 선택
        if subscription_ids:
            self.subscription_ids = subscription_ids
        else:
            boot = self.get_session_bootstrap(self.credential)
            self.subscription_ids = [boot["subscription_id"]]

        self.client = ResourceGraphClient(self.credential)
        
    def _execute_query(
        self, 
        query: str, 
        subscriptions: Optional[list[str]] = None
    ) -> list[dict]:
        """
        Azure Resource Graph 쿼리 실행
        
        Args:
            query: KQL 쿼리
            subscriptions: 조회할 구독 목록
            
        Returns:
            쿼리 결과 리스트
        """
        subs = subscriptions or self.subscription_ids
        if not subs:
            raise ValueError("구독 ID가 지정되지 않았습니다.")

        query = self._apply_tenant_filter(query)

        results = []
        skip_token = None
        
        while True:
            options = QueryRequestOptions(
                skip_token=skip_token,
                top=1000  # 최대 1000개씩 조회
            )
            
            request = QueryRequest(
                subscriptions=subs,
                query=query,
                options=options
            )
            
            response = self.client.resources(request)
            results.extend(response.data)
            
            skip_token = response.skip_token
            if not skip_token:
                break
                
        return results

    def _apply_tenant_filter(self, query: str) -> str:
        """Resources 테이블 직후에 tenantId 조건 삽입."""
        tid = self._tenant_id_filter
        if not tid:
            return query
        # KQL 인젝션 방지: GUID 형태만 허용
        if len(tid) != 36 or tid.count("-") != 4:
            raise ValueError("tenant_id must be a UUID")
        return query.replace(
            "Resources",
            f"Resources\n| where tenantId == '{tid}'",
            1,
        )

    def get_all_resources(self) -> list[AzureResource]:
        """
        지원하는 모든 리소스 타입의 리소스를 조회합니다.
        
        Returns:
            AzureResource 객체 리스트
        """
        type_filter = " or ".join(
            f"type =~ '{t}'" for t in self.SUPPORTED_RESOURCE_TYPES.keys()
        )
        
        query = f"""
        Resources
        | where {type_filter}
        | project id, name, type, resourceGroup, subscriptionId, location, 
                  sku, properties, tags
        """

        raw_results = self._execute_query(query)
        return [self._parse_resource(r) for r in raw_results]
    
    def get_resources_by_type(self, resource_type: str) -> list[AzureResource]:
        """
        특정 타입의 리소스를 조회합니다.
        
        Args:
            resource_type: Azure 리소스 타입 (예: microsoft.compute/virtualmachines)
            
        Returns:
            AzureResource 객체 리스트
        """
        query = f"""
        Resources
        | where type =~ '{resource_type}'
        | project id, name, type, resourceGroup, subscriptionId, location,
                  sku, properties, tags
        """

        raw_results = self._execute_query(query)
        return [self._parse_resource(r) for r in raw_results]
    
    @staticmethod
    def _escape_kql_literal(value: str) -> str:
        """KQL 단일 인용 문자열 내부용 이스케이프 (작은따옴표 이중화)."""
        return (value or "").replace("'", "''")

    def get_resources_by_resource_group(
        self, 
        resource_group: str
    ) -> list[AzureResource]:
        """
        특정 리소스 그룹의 리소스를 조회합니다.
        
        Args:
            resource_group: 리소스 그룹 이름
            
        Returns:
            AzureResource 객체 리스트
        """
        type_filter = " or ".join(
            f"type =~ '{t}'" for t in self.SUPPORTED_RESOURCE_TYPES.keys()
        )
        rg_esc = self._escape_kql_literal(resource_group)
        
        query = f"""
        Resources
        | where resourceGroup =~ '{rg_esc}'
        | where {type_filter}
        | project id, name, type, resourceGroup, subscriptionId, location,
                  sku, properties, tags
        """

        raw_results = self._execute_query(query)
        return [self._parse_resource(r) for r in raw_results]
    
    def get_resource_details(self, resource_id: str) -> Optional[AzureResource]:
        """
        특정 리소스의 상세 정보를 조회합니다.
        
        Args:
            resource_id: Azure 리소스 ID
            
        Returns:
            AzureResource 객체 또는 None
        """
        rid_esc = self._escape_kql_literal(resource_id)
        query = f"""
        Resources
        | where id =~ '{rid_esc}'
        | project id, name, type, resourceGroup, subscriptionId, location,
                  sku, properties, tags
        """

        raw_results = self._execute_query(query)
        if raw_results:
            return self._parse_resource(raw_results[0])
        return None
    
    def get_network_topology(self) -> dict:
        """
        네트워크 토폴로지 정보를 조회합니다.
        VNet, Subnet, NSG 간의 관계를 포함합니다.
        
        Returns:
            네트워크 토폴로지 딕셔너리
        """
        # VNet 조회
        vnet_query = """
        Resources
        | where type =~ 'microsoft.network/virtualnetworks'
        | project id, name, resourceGroup, subscriptionId, location,
                  subnets = properties.subnets,
                  addressSpace = properties.addressSpace
        """
        
        # NSG 조회
        nsg_query = """
        Resources
        | where type =~ 'microsoft.network/networksecuritygroups'
        | project id, name, resourceGroup, 
                  securityRules = properties.securityRules
        """
        
        vnets = self._execute_query(vnet_query)
        nsgs = self._execute_query(nsg_query)
        
        return {
            "virtual_networks": vnets,
            "network_security_groups": nsgs
        }
    
    def get_security_posture(self) -> dict:
        """
        보안 관련 리소스 구성을 조회합니다.
        
        Returns:
            보안 구성 딕셔너리
        """
        # Key Vault 조회
        keyvault_query = """
        Resources
        | where type =~ 'microsoft.keyvault/vaults'
        | project id, name, resourceGroup,
                  enableSoftDelete = properties.enableSoftDelete,
                  enablePurgeProtection = properties.enablePurgeProtection,
                  networkAcls = properties.networkAcls
        """
        
        # Private Endpoint 조회
        pe_query = """
        Resources
        | where type =~ 'microsoft.network/privateendpoints'
        | project id, name, resourceGroup,
                  privateLinkServiceConnections = properties.privateLinkServiceConnections
        """
        
        keyvaults = self._execute_query(keyvault_query)
        private_endpoints = self._execute_query(pe_query)
        
        return {
            "key_vaults": keyvaults,
            "private_endpoints": private_endpoints
        }
    
    def get_monitoring_configuration(self) -> dict:
        """
        모니터링 관련 구성을 조회합니다.
        
        Returns:
            모니터링 구성 딕셔너리
        """
        # Log Analytics Workspace 조회
        law_query = """
        Resources
        | where type =~ 'microsoft.operationalinsights/workspaces'
        | project id, name, resourceGroup, sku, 
                  retentionInDays = properties.retentionInDays
        """
        
        # Application Insights 조회
        appinsights_query = """
        Resources
        | where type =~ 'microsoft.insights/components'
        | project id, name, resourceGroup,
                  applicationType = properties.Application_Type,
                  workspaceResourceId = properties.WorkspaceResourceId
        """
        
        # Metric Alerts 조회
        alerts_query = """
        Resources
        | where type =~ 'microsoft.insights/metricalerts'
        | project id, name, resourceGroup,
                  enabled = properties.enabled,
                  severity = properties.severity,
                  scopes = properties.scopes
        """
        
        workspaces = self._execute_query(law_query)
        app_insights = self._execute_query(appinsights_query)
        alerts = self._execute_query(alerts_query)
        
        return {
            "log_analytics_workspaces": workspaces,
            "application_insights": app_insights,
            "metric_alerts": alerts
        }
    
    def get_database_resources(self) -> dict:
        """
        데이터베이스 관련 리소스를 상세 조회합니다.
        
        Returns:
            데이터베이스 리소스 딕셔너리
        """
        # MySQL Flexible Server
        mysql_query = """
        Resources
        | where type =~ 'microsoft.dbformysql/flexibleservers'
        | project id, name, resourceGroup, location, sku,
                  version = properties.version,
                  state = properties.state,
                  haEnabled = properties.highAvailability.mode,
                  backup = properties.backup,
                  network = properties.network,
                  storage = properties.storage
        """
        
        # PostgreSQL Flexible Server
        pgsql_query = """
        Resources
        | where type =~ 'microsoft.dbforpostgresql/flexibleservers'
        | project id, name, resourceGroup, location, sku,
                  version = properties.version,
                  state = properties.state,
                  haEnabled = properties.highAvailability.mode,
                  backup = properties.backup,
                  network = properties.network,
                  storage = properties.storage
        """
        
        # Cosmos DB
        cosmos_query = """
        Resources
        | where type =~ 'microsoft.documentdb/databaseaccounts'
        | project id, name, resourceGroup, location,
                  kind = kind,
                  consistencyPolicy = properties.consistencyPolicy,
                  locations = properties.locations,
                  capabilities = properties.capabilities,
                  isVirtualNetworkFilterEnabled = properties.isVirtualNetworkFilterEnabled,
                  publicNetworkAccess = properties.publicNetworkAccess,
                  backupPolicy = properties.backupPolicy
        """
        
        mysql_servers = self._execute_query(mysql_query)
        pgsql_servers = self._execute_query(pgsql_query)
        cosmos_accounts = self._execute_query(cosmos_query)
        
        return {
            "mysql_flexible_servers": mysql_servers,
            "postgresql_flexible_servers": pgsql_servers,
            "cosmos_db_accounts": cosmos_accounts
        }
    
    def _parse_resource(self, raw: dict) -> AzureResource:
        """
        Raw 쿼리 결과를 AzureResource 객체로 변환합니다.
        """
        return AzureResource(
            id=raw.get("id", ""),
            name=raw.get("name", ""),
            type=raw.get("type", ""),
            resource_group=raw.get("resourceGroup", ""),
            subscription_id=raw.get("subscriptionId", ""),
            location=raw.get("location", ""),
            sku=raw.get("sku"),
            properties=raw.get("properties", {}),
            tags=raw.get("tags", {})
        )
    
    def export_to_json(self, resources: list[AzureResource], filepath: str):
        """
        리소스 목록을 JSON 파일로 내보냅니다.
        
        Args:
            resources: AzureResource 객체 리스트
            filepath: 저장할 파일 경로
        """
        data = [r.to_dict() for r in resources]
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    
    def get_resource_summary(self) -> dict:
        """
        리소스 요약 정보를 반환합니다.
        
        Returns:
            리소스 타입별 개수 및 요약 정보
        """
        query = """
        Resources
        | summarize count() by type
        | order by count_ desc
        """
        
        results = self._execute_query(query)
        
        summary = {
            "total_resources": sum(r.get("count_", 0) for r in results),
            "by_type": {
                r.get("type", "unknown"): r.get("count_", 0) 
                for r in results
            }
        }
        
        return summary
