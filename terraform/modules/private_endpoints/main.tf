# Private Endpoint 일괄 생성 (모두 pe 서브넷 + 대응 Private DNS zone group)
# backend / frontend(appgw 모드) / acr / ai_account / postgres

resource "azurerm_private_endpoint" "backend" {
  name                = "${var.backend_app_name}-pe"
  location            = var.location
  resource_group_name = var.resource_group_name
  subnet_id           = var.pe_subnet_id
  tags                = var.tags

  private_service_connection {
    name                           = "${var.backend_app_name}-pe-conn"
    private_connection_resource_id = var.backend_web_app_id
    subresource_names              = ["sites"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "default"
    private_dns_zone_ids = [var.private_dns_zone_ids["azurewebsites"]]
  }
}

# Frontend Private Endpoint — appgw 모드에서만(프론트가 Private). AppGW가 privatelink로 프론트에 도달.
resource "azurerm_private_endpoint" "frontend" {
  count               = var.frontend_is_private ? 1 : 0
  name                = "${var.frontend_app_name}-pe"
  location            = var.location
  resource_group_name = var.resource_group_name
  subnet_id           = var.pe_subnet_id
  tags                = var.tags

  private_service_connection {
    name                           = "${var.frontend_app_name}-pe-conn"
    private_connection_resource_id = var.frontend_web_app_id
    subresource_names              = ["sites"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "default"
    private_dns_zone_ids = [var.private_dns_zone_ids["azurewebsites"]]
  }
}

resource "azurerm_private_endpoint" "acr" {
  name                = "${var.acr_name}-pe"
  location            = var.location
  resource_group_name = var.resource_group_name
  subnet_id           = var.pe_subnet_id
  tags                = var.tags

  private_service_connection {
    name                           = "${var.acr_name}-pe-conn"
    private_connection_resource_id = var.acr_id
    subresource_names              = ["registry"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "default"
    private_dns_zone_ids = [var.private_dns_zone_ids["azurecr"]]
  }
}

resource "azurerm_private_endpoint" "ai_account" {
  name                = "${var.ai_services_name}-pe"
  location            = var.location
  resource_group_name = var.resource_group_name
  subnet_id           = var.pe_subnet_id
  tags                = var.tags

  private_service_connection {
    name                           = "${var.ai_services_name}-pe-conn"
    private_connection_resource_id = var.ai_services_id
    subresource_names              = ["account"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name = "default"
    private_dns_zone_ids = [
      var.private_dns_zone_ids["cognitive"],
      var.private_dns_zone_ids["openai"],
      var.private_dns_zone_ids["services_ai"],
    ]
  }
}

resource "azurerm_private_endpoint" "postgres" {
  name                = "${var.postgres_server_name}-pe"
  location            = var.location
  resource_group_name = var.resource_group_name
  subnet_id           = var.pe_subnet_id
  tags                = var.tags

  private_service_connection {
    name                           = "${var.postgres_server_name}-pe-conn"
    private_connection_resource_id = var.postgres_server_id
    subresource_names              = ["postgresqlServer"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "default"
    private_dns_zone_ids = [var.private_dns_zone_ids["postgres"]]
  }
}
