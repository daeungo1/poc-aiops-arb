variable "resource_group_name" {
  type = string
}

variable "location" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "tenant_id" {
  type = string
}

# ── Web App 이름·서브넷 ──
variable "frontend_app_name" {
  type = string
}

variable "backend_app_name" {
  type = string
}

variable "frontend_subnet_id" {
  type        = string
  description = "Frontend Web App VNet 통합 서브넷"
}

variable "backend_subnet_id" {
  type        = string
  description = "Backend Web App VNet 통합 서브넷"
}

# ── 토폴로지 분기 ──
variable "frontend_is_private" {
  type        = bool
  description = "true면 프론트 public_network_access 차단(AppGW→PE 경유). root local.frontend_is_private"
}

variable "public_host" {
  type        = string
  description = "프론트 공개 호스트 (SSO redirect_uri 조립). root local.public_host"
}

variable "frontend_exposure_mode" {
  type        = string
  description = "프론트 노출 토폴로지 (appgw | appservice_managed_cert | appservice_default)"
}

variable "custom_domain_name" {
  type        = string
  default     = ""
  description = "appservice_managed_cert 모드의 커스텀 도메인(전체 FQDN)"
}

variable "tz" {
  type = string
}

# ── Entra SSO ──
variable "azure_auth_client_id" {
  type = string
}

# ── Resource reader UAMI (없으면 빈 문자열) — resource_reader_uami provider로 자체 조회 ──
variable "resource_reader_uami_name" {
  type    = string
  default = ""
}

variable "resource_reader_uami_resource_group_name" {
  type        = string
  default     = ""
  description = "UAMI가 속한 RG. 비우면 resource_group_name 사용"
}

# ── AI Foundry 연동 ──
variable "ai_services_name" {
  type = string
}

variable "ai_project_name" {
  type = string
}

variable "ai_deployment_name" {
  type = string
}

variable "ai_services_id" {
  type        = string
  description = "Backend WebApp → AI Services 역할 할당 scope"
}

variable "backend_ai_builtin_role" {
  type        = string
  default     = "AzureAIUser"
  description = "Backend → AI Services 내장 역할 (AzureAIUser | CognitiveServicesUser)"
}

variable "backend_ai_role_definition_id" {
  type        = string
  default     = ""
  description = "비우면 backend_ai_builtin_role에 따라 내장 GUID 조합. 커스텀 역할일 때만 전체 경로 지정"
}

# ── PostgreSQL ──
variable "postgres_fqdn" {
  type = string
}

# ── ACR ──
variable "acr_name" {
  type        = string
  description = "사이트컨테이너 이미지 경로 조립용 (<acr>.azurecr.io/...)"
}

variable "acr_id" {
  type        = string
  description = "AcrPull 역할 할당 scope"
}

# ── 백엔드 DB·SSO 시크릿 (직접 값 전달) ──
variable "db_user" {
  type = string
}

variable "db_password" {
  type      = string
  sensitive = true
}

variable "db_name" {
  type = string
}

variable "db_port" {
  type    = string
  default = "5432"
}

variable "azure_auth_state_secret" {
  type      = string
  sensitive = true
}

# ── DNS (선택: appservice_managed_cert 모드에서 도메인 검증 레코드 자동 생성. azurerm.dns provider) ──
# dns_zone_name·dns_record_name 이 모두 있을 때만 CNAME + asuid TXT 생성. 비우면 DNS는 수동 관리.
variable "dns_zone_name" {
  type    = string
  default = ""
}

variable "dns_zone_resource_group" {
  type    = string
  default = ""
}

variable "dns_record_name" {
  type        = string
  default     = ""
  description = "DNS zone 내 레코드 이름(서브도메인). 무료 관리 인증서/CNAME은 apex 미지원 → 서브도메인 필수"
}
