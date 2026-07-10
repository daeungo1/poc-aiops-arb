output "vnet_name" {
  value = data.azurerm_virtual_network.main.name
}

output "pe_subnet_id" {
  value = azurerm_subnet.pe.id
}

output "backend_subnet_id" {
  value = azurerm_subnet.backend.id
}

output "frontend_subnet_id" {
  value = azurerm_subnet.frontend.id
}

output "private_dns_zone_ids" {
  description = "key(azurecr/azurewebsites/...) → Private DNS zone ID 맵"
  value       = { for k, z in azurerm_private_dns_zone.this : k => z.id }
}
