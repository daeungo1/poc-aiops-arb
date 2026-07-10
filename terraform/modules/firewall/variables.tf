variable "resource_group_name" {
  type        = string
  description = "Firewall·route table·PIP을 생성할 워크로드 리소스 그룹"
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
  description = "기존 VNet 이름 (AzureFirewallSubnet을 추가 생성)"
}

variable "vnet_resource_group_name" {
  type        = string
  description = "기존 VNet이 속한 리소스 그룹"
}

variable "vnet_address_space" {
  type        = string
  default     = "10.0.0.0/16"
  description = "VNet 주소 공간. route table의 VnetLocal 경로·firewall policy private range에 사용"
}

variable "firewall_subnet_address_prefixes" {
  type        = list(string)
  default     = ["10.0.5.0/26"]
  description = "AzureFirewallSubnet 주소 범위 (이름은 반드시 AzureFirewallSubnet)"
}

variable "pe_subnet_id" {
  type        = string
  description = "PE 서브넷 ID (UDR 연결 대상)"
}

variable "backend_subnet_id" {
  type        = string
  description = "Backend 서브넷 ID (UDR 연결 대상)"
}

variable "frontend_subnet_id" {
  type        = string
  description = "Frontend 서브넷 ID (UDR 연결 대상)"
}
