variable "resource_group_name" {
  type        = string
  description = "AppGW·UAMI·PIP·서브넷을 생성할 워크로드 리소스 그룹"
}

variable "location" {
  type = string
}

variable "tenant_id" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "vnet_name" {
  type        = string
  description = "기존 VNet 이름 (AppGW 서브넷을 추가 생성)"
}

variable "vnet_resource_group_name" {
  type        = string
  description = "기존 VNet이 속한 리소스 그룹"
}

variable "appgw_subnet_address_prefixes" {
  type        = list(string)
  default     = ["10.0.4.0/24"]
  description = "AppGW 전용 서브넷 주소 범위"
}

variable "frontend_default_hostname" {
  type        = string
  description = "Frontend Web App의 default_hostname (백엔드 풀 fqdn). Private면 privatelink DNS로 해소됨"
}

variable "custom_domain_name" {
  type        = string
  description = "HTTPS 리스너 host_names에 사용할 전체 도메인 (예: aiops.example.com)"
}

# ── KeyVault 인증서 (인증서가 secret으로 import되어 있어야 함) ──
variable "cert_key_vault_name" {
  type = string
}

variable "cert_key_vault_resource_group_name" {
  type = string
}

variable "cert_secret_name" {
  type        = string
  description = "KV에 import된 인증서의 secret 이름 (버전 없이 = 최신 버전 사용)"
}

variable "ssl_cert_name" {
  type        = string
  default     = "appgw-ssl-cert"
  description = "AppGW 내부 ssl_certificate 리소스 이름"
}

variable "key_vault_permission_model" {
  type        = string
  default     = "rbac"
  description = "cert KV 인증 모델: rbac → 'Key Vault Secrets User' role, access_policy → secret/certificate Get 정책"
}

# ── DNS A 레코드 (선택: 기존 Azure DNS Zone에 추가. dns_zone_name·dns_record_name 모두 있을 때만 생성) ──
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
  description = "DNS zone 내 레코드 이름(서브도메인). 예: custom_domain_name=aiops.example.com, zone=example.com → 'aiops'"
}
