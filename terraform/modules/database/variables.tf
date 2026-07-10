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

# ── 자격증명·DB명을 읽어올 시크릿 Key Vault (secrets_kv provider) ──
variable "secrets_key_vault_name" {
  type        = string
  description = "Postgres 자격증명·DB명 시크릿이 든 Key Vault 이름"
}

variable "secrets_key_vault_resource_group_name" {
  type        = string
  description = "위 Key Vault 리소스 그룹"
}

variable "secret_name_admin_login" {
  type        = string
  description = "administrator_login 값을 담은 시크릿 이름"
}

variable "secret_name_admin_password" {
  type        = string
  description = "administrator_password 값을 담은 시크릿 이름"
}

variable "secret_name_db_name" {
  type        = string
  description = "초기 DB 이름 값을 담은 시크릿 이름"
}
