
output "resource_group_name" {
  value = var.resource_group_name
}

output "vnet_name" {
  value = module.network.vnet_name
}

output "frontend_default_hostname" {
  value = module.app_service.frontend_default_hostname
}

output "backend_default_hostname" {
  value = module.app_service.backend_default_hostname
}

output "secrets_key_vault_name" {
  description = "Postgres 자격증명·앱 시크릿용 Key Vault(기본적으로 key_vault_name과 동일할 수 있음)"
  value       = local.secrets_key_vault_name
}

output "secrets_key_vault_rg" {
  description = "시크릿용 Key Vault 리소스 그룹"
  value       = local.secrets_key_vault_rg
}

output "acr_name" {
  value = module.acr.acr_name
}

output "ai_services_id" {
  value = module.ai_foundry.ai_services_id
}

output "ai_foundry_endpoint" {
  value = "https://${var.ai_services_name}.services.ai.azure.com/api/projects/${var.ai_project_name}"
}

output "postgres_fqdn" {
  value = module.database.fqdn
}

output "postgres_database_name" {
  description = "KV 시크릿에서 주입되어 Terraform이 값을 민감 처리함"
  value       = module.database.database_name
  sensitive   = true
}

# 토폴로지별 접속 정보
output "frontend_url" {
  description = "프론트 접속 URL (토폴로지에 따라 커스텀 도메인 또는 기본 도메인)"
  value       = "https://${local.public_host}"
}

output "appgw_public_ip" {
  description = "AppGW 공인 IP (appgw 모드일 때만, 그 외 null). DNS A 레코드를 이 IP로 지정"
  value       = one(module.appgw[*].public_ip)
}

output "firewall_private_ip" {
  description = "Azure Firewall private IP (enable_firewall=true일 때만, 그 외 null)"
  value       = one(module.firewall[*].firewall_private_ip)
}
