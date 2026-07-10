output "frontend_web_app_id" {
  value = azurerm_linux_web_app.frontend.id
}

output "backend_web_app_id" {
  value = azurerm_linux_web_app.backend.id
}

output "frontend_default_hostname" {
  value = azurerm_linux_web_app.frontend.default_hostname
}

output "backend_default_hostname" {
  value = azurerm_linux_web_app.backend.default_hostname
}
