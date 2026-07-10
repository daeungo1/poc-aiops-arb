# Premium ACR (Private Endpoint 전용, public/anonymous/data endpoint 차단)
# Frontend·Backend SAMI → AcrPull 역할 할당은 app_service 모듈에서 수행(웹앱 ID에 부여).

resource "azurerm_container_registry" "main" {
  name                          = var.acr_name
  resource_group_name           = var.resource_group_name
  location                      = var.location
  sku                           = "Premium"
  admin_enabled                 = false
  public_network_access_enabled = false
  data_endpoint_enabled         = false
  anonymous_pull_enabled        = false
  tags                          = var.tags

  network_rule_set {
    default_action = "Deny"
  }
}
