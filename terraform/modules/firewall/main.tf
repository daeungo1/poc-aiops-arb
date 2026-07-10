# 시나리오 4: Azure Firewall + 서브넷별 UDR(0.0.0.0/0 → firewall)
# enable_firewall=true 일 때만 root에서 count=1로 호출됨.

resource "azurerm_subnet" "firewall" {
  name                 = "AzureFirewallSubnet"
  resource_group_name  = var.vnet_resource_group_name
  virtual_network_name = var.vnet_name
  address_prefixes     = var.firewall_subnet_address_prefixes
}

resource "azurerm_public_ip" "firewall" {
  name                = "aiopspoc-fw-dev-pip"
  location            = var.location
  resource_group_name = var.resource_group_name
  allocation_method   = "Static"
  sku                 = "Standard"
  ip_version          = "IPv4"
  tags                = var.tags
}

resource "azurerm_firewall_policy" "main" {
  name                              = "aiopspoc-fw-dev-krc-policy"
  resource_group_name               = var.resource_group_name
  location                          = var.location
  sku                               = "Standard"
  threat_intelligence_mode          = "Alert"
  private_ip_ranges                 = [var.vnet_address_space]
  auto_learn_private_ranges_enabled = true
  tags                              = var.tags
}

resource "azurerm_firewall_policy_rule_collection_group" "network" {
  name               = "DefaultNetworkRuleCollectionGroup"
  firewall_policy_id = azurerm_firewall_policy.main.id
  priority           = 200

  network_rule_collection {
    name     = "NetworkRuleCollection"
    priority = 100
    action   = "Allow"

    rule {
      name                  = "Rule01"
      protocols             = ["TCP"]
      source_addresses      = [var.vnet_address_space]
      destination_addresses = ["*"]
      destination_ports     = ["*"]
    }
  }
}

resource "azurerm_firewall" "main" {
  name                = "aiopspoc-fw-dev-krc"
  location            = var.location
  resource_group_name = var.resource_group_name
  sku_name            = "AZFW_VNet"
  sku_tier            = "Standard"
  firewall_policy_id  = azurerm_firewall_policy.main.id
  tags                = var.tags

  ip_configuration {
    name                 = "configuration"
    subnet_id            = azurerm_subnet.firewall.id
    public_ip_address_id = azurerm_public_ip.firewall.id
  }
}

# ── UDR: 서브넷별 route table (VNet 내부는 로컬, 인터넷은 firewall 경유) ──

locals {
  route_tables = {
    pe       = "udr-dev-krc-pe-subnet"
    backend  = "udr-backend-dev-krc-subnet"
    frontend = "udr-frontend-dev-krc-subnet"
  }
  subnet_ids = {
    pe       = var.pe_subnet_id
    backend  = var.backend_subnet_id
    frontend = var.frontend_subnet_id
  }
}

resource "azurerm_route_table" "this" {
  for_each            = local.route_tables
  name                = each.value
  location            = var.location
  resource_group_name = var.resource_group_name
  tags                = var.tags

  route {
    name           = "local-vnet"
    address_prefix = var.vnet_address_space
    next_hop_type  = "VnetLocal"
  }

  route {
    name                   = "udr-internet-hubfw"
    address_prefix         = "0.0.0.0/0"
    next_hop_type          = "VirtualAppliance"
    next_hop_in_ip_address = azurerm_firewall.main.ip_configuration[0].private_ip_address
  }
}

resource "azurerm_subnet_route_table_association" "this" {
  for_each       = local.subnet_ids
  subnet_id      = each.value
  route_table_id = azurerm_route_table.this[each.key].id
}
