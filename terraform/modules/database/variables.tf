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

variable "postgres_server_name" {
  type        = string
  description = "PostgreSQL Flexible Server 이름 (전역 고유, 3-63자)"
}

# ── Postgres 자격증명·DB명 (직접 전달) ──
variable "admin_login" {
  type        = string
  description = "PostgreSQL administrator_login"
}

variable "admin_password" {
  type        = string
  sensitive   = true
  description = "PostgreSQL administrator_password"
}

variable "db_name" {
  type        = string
  description = "초기 데이터베이스 이름"
}
