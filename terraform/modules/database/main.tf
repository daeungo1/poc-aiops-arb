# PostgreSQL Flexible Server (Private, public access 차단) + 초기 DB
# 자격증명·DB명은 시크릿 KV에서 직접 조회(secrets_kv provider, 다른 구독일 수 있음).

data "azurerm_key_vault" "secrets" {
  provider            = azurerm.secrets_kv
  name                = var.secrets_key_vault_name
  resource_group_name = var.secrets_key_vault_resource_group_name
}

data "azurerm_key_vault_secret" "admin_login" {
  provider     = azurerm.secrets_kv
  name         = var.secret_name_admin_login
  key_vault_id = data.azurerm_key_vault.secrets.id
}

data "azurerm_key_vault_secret" "admin_password" {
  provider     = azurerm.secrets_kv
  name         = var.secret_name_admin_password
  key_vault_id = data.azurerm_key_vault.secrets.id
}

data "azurerm_key_vault_secret" "db_name" {
  provider     = azurerm.secrets_kv
  name         = var.secret_name_db_name
  key_vault_id = data.azurerm_key_vault.secrets.id
}

resource "azurerm_postgresql_flexible_server" "main" {
  name                          = var.postgres_server_name
  resource_group_name           = var.resource_group_name
  location                      = var.location
  version                       = "16"
  administrator_login           = data.azurerm_key_vault_secret.admin_login.value
  administrator_password        = data.azurerm_key_vault_secret.admin_password.value
  public_network_access_enabled = false
  zone                          = "1"
  storage_mb                    = 32768
  sku_name                      = "B_Standard_B1ms"
  tags                          = var.tags
}

resource "azurerm_postgresql_flexible_server_database" "main" {
  name      = data.azurerm_key_vault_secret.db_name.value
  server_id = azurerm_postgresql_flexible_server.main.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}
