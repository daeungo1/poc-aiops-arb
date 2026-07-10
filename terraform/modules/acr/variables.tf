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

variable "acr_name" {
  type        = string
  description = "Container Registry 이름 (전역 고유, 영숫자, 최대 50자)"
}
