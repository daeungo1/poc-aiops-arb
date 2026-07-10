# 네트워킹: NSG · 서브넷(pe/backend/frontend) · NSG 연결 · Private DNS zone + vnet link

data "azurerm_virtual_network" "main" {
  name                = var.vnet_name
  resource_group_name = var.vnet_resource_group_name
}

resource "azurerm_network_security_group" "backend" {
  name                = "aiopspoc-backend-dev-krc-subnet-nsg"
  location            = var.location
  resource_group_name = var.resource_group_name
  tags                = var.tags
}

resource "azurerm_network_security_group" "frontend" {
  name                = "aiopspoc-frontend-dev-krc-subnet-nsg"
  location            = var.location
  resource_group_name = var.resource_group_name
  tags                = var.tags
}

resource "azurerm_network_security_group" "pe" {
  name                = "aiopspoc-pe-dev-krc-subnet-nsg"
  location            = var.location
  resource_group_name = var.resource_group_name
  tags                = var.tags
}

resource "azurerm_subnet" "pe" {
  name                              = "aiopspoc-pe-dev-krc-subnet"
  resource_group_name               = var.vnet_resource_group_name
  virtual_network_name              = var.vnet_name
  address_prefixes                  = var.pe_subnet_address_prefixes
  private_endpoint_network_policies = "Disabled"
}

resource "azurerm_subnet" "backend" {
  name                 = "aiopspoc-backend-dev-krc-subnet"
  resource_group_name  = var.vnet_resource_group_name
  virtual_network_name = var.vnet_name
  address_prefixes     = var.backend_subnet_address_prefixes

  delegation {
    name = "Microsoft.Web.serverFarms"
    service_delegation {
      name    = "Microsoft.Web/serverFarms"
      actions = ["Microsoft.Network/virtualNetworks/subnets/action"]
    }
  }
}

resource "azurerm_subnet" "frontend" {
  name                 = "aiopspoc-frontend-dev-krc-subnet"
  resource_group_name  = var.vnet_resource_group_name
  virtual_network_name = var.vnet_name
  address_prefixes     = var.frontend_subnet_address_prefixes

  delegation {
    name = "Microsoft.Web.serverFarms"
    service_delegation {
      name    = "Microsoft.Web/serverFarms"
      actions = ["Microsoft.Network/virtualNetworks/subnets/action"]
    }
  }
}

resource "azurerm_subnet_network_security_group_association" "pe" {
  subnet_id                 = azurerm_subnet.pe.id
  network_security_group_id = azurerm_network_security_group.pe.id
}

resource "azurerm_subnet_network_security_group_association" "backend" {
  subnet_id                 = azurerm_subnet.backend.id
  network_security_group_id = azurerm_network_security_group.backend.id
}

resource "azurerm_subnet_network_security_group_association" "frontend" {
  subnet_id                 = azurerm_subnet.frontend.id
  network_security_group_id = azurerm_network_security_group.frontend.id
}

# ── Private DNS zone + vnet link (privatelink 해소용) ──
resource "azurerm_private_dns_zone" "this" {
  for_each            = var.private_dns_zone_names
  name                = each.value
  resource_group_name = var.resource_group_name
  tags                = var.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "this" {
  for_each              = var.private_dns_zone_names
  name                  = "vnet-link-${replace(each.key, "_", "-")}"
  resource_group_name   = var.resource_group_name
  private_dns_zone_name = azurerm_private_dns_zone.this[each.key].name
  virtual_network_id    = data.azurerm_virtual_network.main.id
  registration_enabled  = false
}
