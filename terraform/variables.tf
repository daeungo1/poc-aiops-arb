# ============================================================
# 전역/구독·테넌트·RG·태그 (provider 설정 및 모든 모듈 공통)
# ============================================================

variable "subscription_id" {
  type = string
}

variable "secrets_key_vault_subscription_id" {
  type        = string
  default     = ""
  description = "(미사용) 시크릿 Key Vault 구독 ID. KV 의존 제거로 미사용. 비우면 subscription_id 사용"
}

variable "resource_reader_uami_subscription_id" {
  type        = string
  description = "리더 UAMI(arb-agent-mi)가 속한 구독 ID. 동일 구독이면 subscription_id와 같은 값을 입력"
}

variable "dns_subscription_id" {
  type        = string
  default     = ""
  description = "DNS Zone(도메인)이 속한 구독 ID (appgw A 레코드·관리 인증서 검증 레코드 생성용). 비우면 워크로드 구독(subscription_id) 사용"
}

variable "tenant_id" {
  type    = string
  default = "ab52f787-ce3d-4591-9fe3-64d2671b53ad"
}

variable "resource_group_name" {
  type = string
}

variable "location" {
  type    = string
  default = "koreacentral"
}

variable "allowed_ips" {
  type    = list(string)
  default = []
}

variable "tags" {
  type = map(string)
  default = {
    env     = "dev"
    project = "secaiops-poc"
    source  = "terraform"
  }
}

# ============================================================
# 네트워크 (기존 VNet 참조)
# ============================================================

variable "vnet_name" {
  type        = string
  default     = "aiopspoc-vnet-dev-krc"
  description = "기존에 배포된 VNet 이름"
}

variable "vnet_resource_group_name" {
  type        = string
  default     = ""
  description = "기존 VNet이 속한 리소스 그룹. 비워두면 resource_group_name 사용"
}

# ============================================================
# App Service (Frontend·Backend Web App) 및 SSO
# ============================================================

variable "frontend_app_name" {
  type        = string
  default     = "aiopspoc-frontend-dev-krc"
  description = "Frontend Web App 이름 (전역 고유)"
}

variable "backend_app_name" {
  type        = string
  default     = "aiopspoc-backend-dev-krc"
  description = "Backend Web App 이름 (전역 고유)"
}

variable "tz" {
  type        = string
  default     = "Asia/Seoul"
  description = "프론트·백엔드 Web App 앱 설정 TZ (IANA 시간대)"
}

variable "azure_auth_client_id" {
  type        = string
  description = "Entra ID 앱 등록 Client ID (SSO용)"
}

# ============================================================
# Container Registry
# ============================================================

variable "acr_name" {
  type        = string
  default     = "aiopspocacrdevkrc"
  description = "Container Registry 이름 (전역 고유, 영숫자, 최대 50자)"
}

# ============================================================
# Azure AI Foundry (AI Services + 프로젝트) 및 백엔드 역할
# ============================================================

variable "ai_services_name" {
  type        = string
  default     = "aiopspoc-aif-dev-krc"
  description = "Azure AI Services 계정 이름 (전역 고유, custom subdomain으로 사용)"
}

variable "ai_project_name" {
  type        = string
  default     = "proj-default"
  description = "Azure AI Foundry 프로젝트 이름"
}

variable "ai_model_name" {
  type        = string
  default     = "gpt-5.2"
  description = "AI Foundry에 배포할 모델 이름 (모델 배포 이름으로도 사용)"
}

variable "ai_model_version" {
  type        = string
  default     = "2025-12-11"
  description = "AI Foundry에 배포할 모델 버전"
}

# Terraform에서는 role_definition_name(이름 조회) 대신 role_definition_id(GUID) 사용 권장.
variable "backend_ai_builtin_role" {
  type        = string
  default     = "AzureAIUser"
  description = "백엔드 → AI Services 내장 역할. AzureAIUser = 포털의 'Azure AI User'와 동일(GUID 직접 지정). 이름 조회 오류가 났던 경우와 무관하게 할당 가능."
  validation {
    condition     = contains(["AzureAIUser", "CognitiveServicesUser"], var.backend_ai_builtin_role)
    error_message = "backend_ai_builtin_role must be AzureAIUser or CognitiveServicesUser."
  }
}

variable "backend_ai_role_definition_id" {
  type        = string
  default     = ""
  description = "선택. 비우면 backend_ai_builtin_role에 따라 내장 GUID가 조합됨. 커스텀/다른 범위 역할을 쓸 때만 전체 role_definition_id 경로를 넣음."
}

# ============================================================
# PostgreSQL Flexible Server. 자격증명·DB명·포트는 아래 시크릿 이름으로 Key Vault에서 읽음.
# ============================================================

variable "postgres_server_name" {
  type        = string
  default     = "aiopspoc-pg-dev-krc"
  description = "PostgreSQL Flexible Server 이름 (전역 고유, 영숫자·하이픈, 3-63자)"
}

variable "postgres_admin_login" {
  type        = string
  description = "PostgreSQL 관리자 로그인 (KV 미사용 · 직접 전달)"
}

variable "postgres_admin_password" {
  type        = string
  sensitive   = true
  description = "PostgreSQL 관리자 비밀번호 (KV 미사용 · 직접 전달)"
}

variable "postgres_db_name" {
  type        = string
  default     = "aiops"
  description = "초기 데이터베이스 이름"
}

variable "postgres_db_port" {
  type        = string
  default     = "5432"
  description = "백엔드 DB_PORT 앱 설정"
}

variable "azure_auth_state_secret" {
  type        = string
  sensitive   = true
  description = "백엔드 AZURE_AUTH_STATE_SECRET (OAuth state HMAC)"
}

variable "backend_kv_secret_postgres_admin_login" {
  type        = string
  default     = "postgres-admin-login"
  description = "Key Vault 시크릿 이름 · Terraform Postgres administrator_login 및 백엔드 DB_USER"
}

variable "backend_kv_secret_postgres_admin_password" {
  type        = string
  default     = "postgres-admin-password"
  description = "Key Vault 시크릿 이름 · Terraform Postgres administrator_password 및 백엔드 DB_PASSWORD"
}

variable "backend_kv_secret_postgres_db_name" {
  type        = string
  default     = "postgres-db-name"
  description = "Key Vault 시크릿 이름 · Terraform 초기 DB 이름 및 백엔드 DB_NAME"
}

variable "backend_kv_secret_postgres_db_port" {
  type        = string
  default     = "postgres-db-port"
  description = "Key Vault 시크릿 이름 · 백엔드 앱 설정 DB_PORT"
}

# ============================================================
# 시크릿 Key Vault (Postgres 자격증명·백엔드 앱 설정 시크릿 저장소)
# 미리 생성·시크릿 채워 둠. Terraform 실행 주체에게 이 Vault Secrets 읽기 권한 필요.
# ============================================================

variable "terraform_secrets_key_vault_name" {
  type        = string
  default     = ""
  description = "(미사용) KV 의존 제거로 미사용."
}

variable "terraform_secrets_key_vault_resource_group_name" {
  type        = string
  default     = ""
  description = "위 Key Vault 리소스 그룹. 비워두면 워크로드 RG(resource_group_name) 사용."
}

# 기존 Key Vault 설정과 반드시 일치해야 합니다.
# - rbac: Key Vault에서 RBAC 권한 모델 사용(enable_rbac_authorization = true)일 때
# - access_policy: 레거시 액세스 정책 모델일 때 (RBAC 역할 할당은 건너뜀)
variable "key_vault_permission_model" {
  type        = string
  default     = "rbac"
  description = "Key Vault 인증 모델: 'rbac' 또는 'access_policy'"
  validation {
    condition     = contains(["rbac", "access_policy"], var.key_vault_permission_model)
    error_message = "key_vault_permission_model must be rbac or access_policy."
  }
}

variable "backend_kv_secret_azure_auth_state_secret" {
  type        = string
  default     = "azure-auth-state-secret"
  description = "Key Vault 시크릿 이름 · 백엔드 AZURE_AUTH_STATE_SECRET (OAuth state HMAC용)"
}

# ============================================================
# 리소스 조회·평가용 User-Assigned Managed Identity (resource_reader)
# ============================================================

variable "resource_reader_uami_name" {
  type        = string
  default     = ""
  description = "구독 교집합·Resource Graph·Entra SSO MSAL 페더레이션(UAMI client_assertion)에 쓰는 User-Assigned MI 이름. 비워두면 백엔드는 시스템 할당 MI만 사용하며, SSO 페더레이션은 AZURE_AUTH_SSO_UAMI_CLIENT_ID 를 앱 설정에 직접 넣어야 합니다."
}

variable "resource_reader_uami_resource_group_name" {
  type        = string
  default     = ""
  description = "리소스 조회·평가용 UAMI가 속한 리소스 그룹. 비워두면 resource_group_name 사용"
}

# ============================================================
# 배포 토폴로지 분기 (시나리오 플래그는 terraform.tfvars에서 설정)
# ============================================================

# 시나리오 1·2·3 (상호배타적): 프론트 노출/인증서 방식
variable "frontend_exposure_mode" {
  type        = string
  default     = "appservice_default"
  description = "프론트 노출/인증서 토폴로지: appgw(AppGW+KV cert+도메인, 프론트 Private) | appservice_managed_cert(프론트 Public + App Service 무료 관리 인증서 + 도메인) | appservice_default(프론트 Public + 기본 도메인)"
  validation {
    condition     = contains(["appgw", "appservice_managed_cert", "appservice_default"], var.frontend_exposure_mode)
    error_message = "frontend_exposure_mode must be appgw | appservice_managed_cert | appservice_default."
  }
  # appgw 모드: 도메인(custom_domain_name 또는 dns_zone_name+dns_record_name) + cert KV 정보 필수
  validation {
    condition     = var.frontend_exposure_mode != "appgw" || ((var.custom_domain_name != "" || (var.dns_zone_name != "" && var.dns_record_name != "")) && var.appgw_cert_key_vault_name != "" && var.appgw_cert_secret_name != "")
    error_message = "frontend_exposure_mode=appgw 에서는 도메인(custom_domain_name 또는 dns_zone_name+dns_record_name)과 appgw_cert_key_vault_name, appgw_cert_secret_name 이 필요합니다."
  }
  # 관리 인증서 모드: 도메인 필수 (custom_domain_name 또는 dns_zone_name+dns_record_name)
  validation {
    condition     = var.frontend_exposure_mode != "appservice_managed_cert" || var.custom_domain_name != "" || (var.dns_zone_name != "" && var.dns_record_name != "")
    error_message = "frontend_exposure_mode=appservice_managed_cert 에서는 custom_domain_name 또는 dns_zone_name+dns_record_name 이 필요합니다."
  }
}

# 시나리오 4 (직교): Firewall + UDR
variable "enable_firewall" {
  type        = bool
  default     = false
  description = "true면 Azure Firewall + 서브넷별 UDR(0.0.0.0/0 → firewall) 생성"
}

# 시나리오 1·2 공통: 커스텀 도메인 (전체 FQDN)
# 비우면 dns_record_name + dns_zone_name 으로 자동 조립됨 (예: arb + metaaiops.org → arb.metaaiops.org)
variable "custom_domain_name" {
  type        = string
  default     = ""
  description = "프론트 공개 도메인(전체 FQDN). 비우면 dns_record_name.dns_zone_name 으로 조립. appgw / appservice_managed_cert 모드에서 사용"
}

# 시나리오 1 전용: AppGW가 사용할 KeyVault 인증서
variable "appgw_cert_key_vault_name" {
  type        = string
  default     = ""
  description = "AppGW SSL 인증서(secret으로 import)가 든 Key Vault 이름"
}

variable "appgw_cert_key_vault_resource_group_name" {
  type        = string
  default     = ""
  description = "위 Key Vault 리소스 그룹. 비우면 terraform_secrets_key_vault_resource_group_name → 워크로드 RG 순으로 fallback"
}

variable "appgw_cert_secret_name" {
  type        = string
  default     = ""
  description = "KV에 import된 인증서의 secret 이름 (버전 없이 = 최신)"
}

variable "appgw_ssl_cert_name" {
  type        = string
  default     = "appgw-ssl-cert"
  description = "AppGW 내부 ssl_certificate 리소스 이름"
}

# 시나리오 1·2: DNS A 레코드를 Terraform이 생성할 경우 (Azure DNS Zone 사용 시). 비우면 DNS는 수동 관리.
variable "dns_zone_name" {
  type        = string
  default     = ""
  description = "Azure DNS public zone 이름 (예: example.com). 비우면 DNS 레코드 미생성"
}

variable "dns_zone_resource_group" {
  type        = string
  default     = ""
  description = "DNS zone이 속한 리소스 그룹. 비우면 워크로드 RG 사용"
}

variable "dns_record_name" {
  type        = string
  default     = ""
  description = "DNS zone 내 레코드(서브도메인) 이름. 예: custom=aiops.example.com, zone=example.com → 'aiops'"
  validation {
    condition     = var.dns_record_name != "@"
    error_message = "dns_record_name 은 apex(@)일 수 없습니다(CNAME/무료 관리 인증서는 서브도메인만 지원)."
  }
}
