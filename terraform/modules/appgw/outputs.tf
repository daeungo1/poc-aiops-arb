output "public_ip" {
  value = azurerm_public_ip.appgw.ip_address
}

output "appgw_id" {
  value = azurerm_application_gateway.main.id
}

output "identity_principal_id" {
  value = azurerm_user_assigned_identity.appgw.principal_id
}
