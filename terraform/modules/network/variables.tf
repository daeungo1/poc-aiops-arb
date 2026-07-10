variable "resource_group_name" {
  type        = string
  description = "NSG·Private DNS zone을 생성할 워크로드 리소스 그룹"
}

variable "location" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "vnet_name" {
  type        = string
  description = "기존 VNet 이름 (서브넷을 추가 생성)"
}

variable "vnet_resource_group_name" {
  type        = string
  description = "기존 VNet이 속한 리소스 그룹"
}

variable "private_dns_zone_names" {
  type        = map(string)
  description = "생성할 Private DNS zone 맵 (key → privatelink FQDN). vnet link 이름은 key 기반으로 조합됨"
}

variable "pe_subnet_address_prefixes" {
  type        = list(string)
  default     = ["10.0.1.0/27"]
  description = "Private Endpoint 서브넷 주소 범위"
}

variable "backend_subnet_address_prefixes" {
  type        = list(string)
  default     = ["10.0.2.0/27"]
  description = "Backend 서브넷 주소 범위 (Web 서버팜 위임)"
}

variable "frontend_subnet_address_prefixes" {
  type        = list(string)
  default     = ["10.0.3.0/27"]
  description = "Frontend 서브넷 주소 범위 (Web 서버팜 위임)"
}
