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

variable "pe_subnet_id" {
  type        = string
  description = "Private Endpoint를 배치할 서브넷"
}

variable "private_dns_zone_ids" {
  type        = map(string)
  description = "key(azurecr/azurewebsites/cognitive/openai/services_ai/vaultcore/postgres) → Private DNS zone ID 맵"
}

variable "frontend_is_private" {
  type        = bool
  description = "true면 Frontend Web App용 PE 생성(appgw 모드)"
}

# ── PE 대상 리소스 (id + 이름) ──
variable "backend_app_name" {
  type = string
}

variable "backend_web_app_id" {
  type = string
}

variable "frontend_app_name" {
  type = string
}

variable "frontend_web_app_id" {
  type = string
}

variable "acr_name" {
  type = string
}

variable "acr_id" {
  type = string
}

variable "ai_services_name" {
  type = string
}

variable "ai_services_id" {
  type = string
}

variable "postgres_server_name" {
  type = string
}

variable "postgres_server_id" {
  type = string
}
