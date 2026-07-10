output "backend_pe_id" {
  value = azurerm_private_endpoint.backend.id
}

output "frontend_pe_id" {
  value = one(azurerm_private_endpoint.frontend[*].id)
}

output "secrets_keyvault_pe_id" {
  value = azurerm_private_endpoint.secrets_keyvault.id
}

output "acr_pe_id" {
  value = azurerm_private_endpoint.acr.id
}

output "ai_account_pe_id" {
  value = azurerm_private_endpoint.ai_account.id
}

output "postgres_pe_id" {
  value = azurerm_private_endpoint.postgres.id
}
