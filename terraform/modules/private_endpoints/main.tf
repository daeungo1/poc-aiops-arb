# Private Endpoint 일괄 생성 (모두 pe 서브넷 + 대응 Private DNS zone group)
# backend / frontend(appgw 모드) / secrets KV / acr / ai_account / postgres

# 시크릿 KV는 다른 구독일 수 있어 secrets_kv provider로 id 조회
data "azurerm_key_vault" "secrets" {
  provider            = azurerm.secrets_kv
  name                = var.secrets_key_vault_name
  resource_group_name = var.secrets_key_vault_resource_group_name
}

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

# 앱·Postgres 시크릿 Vault(arb-env-kv 등) — VNet에서 Key Vault 참조 해소
resource "azurerm_private_endpoint" "secrets_keyvault" {
  name                = "${var.secrets_key_vault_name}-pe"
  location            = var.location
  resource_group_name = var.resource_group_name
  subnet_id           = var.pe_subnet_id
  tags                = var.tags

  private_service_connection {
    name                           = "${var.secrets_key_vault_name}-pe-conn"
    private_connection_resource_id = data.azurerm_key_vault.secrets.id
    subresource_names              = ["vault"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "default"
    private_dns_zone_ids = [var.private_dns_zone_ids["vaultcore"]]
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
