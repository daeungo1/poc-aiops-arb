# PostgreSQL Flexible Server (Private, public access 차단) + 초기 DB
# 자격증명·DB명은 직접 변수로 전달(로컬 apply 시 KV 데이터플레인 정책 차단 회피).

resource "azurerm_postgresql_flexible_server" "main" {
  name                          = var.postgres_server_name
  resource_group_name           = var.resource_group_name
  location                      = var.location
  version                       = "16"
  administrator_login           = var.admin_login
  administrator_password        = var.admin_password
  public_network_access_enabled = false
  zone                          = "1"
  storage_mb                    = 32768
  sku_name                      = "B_Standard_B1ms"
  tags                          = var.tags
}

resource "azurerm_postgresql_flexible_server_database" "main" {
  name      = var.db_name
  server_id = azurerm_postgresql_flexible_server.main.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}
