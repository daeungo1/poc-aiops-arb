variable "resource_group_name" {
  type        = string
  description = "AI Services 계정을 생성할 워크로드 리소스 그룹 이름"
}

variable "location" {
  type = string
}

variable "ai_services_name" {
  type        = string
  description = "Azure AI Services 계정 이름 (전역 고유, custom subdomain으로 사용)"
}

variable "ai_project_name" {
  type        = string
  description = "Azure AI Foundry 프로젝트 이름"
}

variable "ai_model_name" {
  type        = string
  description = "배포할 모델 이름 (모델 배포 이름으로도 사용). 예: gpt-5.2"
}

variable "ai_model_version" {
  type        = string
  description = "배포할 모델 버전. 예: 2025-12-11"
}

variable "allowed_ips" {
  type        = list(string)
  default     = []
  description = "AI Services networkAcls 에 허용할 IP 목록"
}
