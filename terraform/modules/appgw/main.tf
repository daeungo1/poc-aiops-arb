# 시나리오 1: Application Gateway(WAF_v2) 공개 + Frontend WebApp Private
# KeyVault 인증서 + 커스텀 도메인을 AppGW에 매핑. frontend_exposure_mode="appgw" 일 때만 count=1로 호출됨.
#
# providers:
#   azurerm     (default) — AppGW/UAMI/PIP/서브넷/NSG/WAF policy (워크로드 구독)
#   azurerm.kv            — cert KV 접근 grant (다른 구독일 수 있음)
#   azurerm.dns           — DNS Zone A 레코드 (도메인이 있는 구독)

locals {
  appgw_subnet_cidr   = var.appgw_subnet_address_prefixes[0]
  create_dns_a_record = var.dns_zone_name != "" && var.dns_record_name != ""
}

# cert가 든 KeyVault (다른 구독일 수 있으므로 kv provider 사용)
data "azurerm_key_vault" "cert" {
  provider            = azurerm.kv
  name                = var.cert_key_vault_name
  resource_group_name = var.cert_key_vault_resource_group_name
}

resource "azurerm_user_assigned_identity" "appgw" {
  name                = "aiopspoc-appgw-dev-krc-identity"
  resource_group_name = var.resource_group_name
  location            = var.location
  tags                = var.tags
}

# AppGW UAMI → cert KV에서 인증서/시크릿 Get (KV 인증 모델에 따라 분기)
resource "azurerm_role_assignment" "appgw_kv_secrets_user" {
  count = var.key_vault_permission_model == "rbac" ? 1 : 0

  provider             = azurerm.kv
  scope                = data.azurerm_key_vault.cert.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.appgw.principal_id
}

resource "azurerm_key_vault_access_policy" "appgw" {
  count = var.key_vault_permission_model == "access_policy" ? 1 : 0

  provider                = azurerm.kv
  key_vault_id            = data.azurerm_key_vault.cert.id
  tenant_id               = var.tenant_id
  object_id               = azurerm_user_assigned_identity.appgw.principal_id
  secret_permissions      = ["Get"]
  certificate_permissions = ["Get"]
}

resource "azurerm_public_ip" "appgw" {
  name                = "aiopspoc-appgw-dev-krc-pip"
  location            = var.location
  resource_group_name = var.resource_group_name
  allocation_method   = "Static"
  sku                 = "Standard"
  ip_version          = "IPv4"
  zones               = ["1", "2", "3"]
  tags                = var.tags
}

resource "azurerm_subnet" "appgw" {
  name                 = "aiopspoc-appgw-dev-krc-subnet"
  resource_group_name  = var.vnet_resource_group_name
  virtual_network_name = var.vnet_name
  address_prefixes     = var.appgw_subnet_address_prefixes
}

resource "azurerm_network_security_group" "appgw" {
  name                = "aiopspoc-appgw-dev-krc-subnet-nsg"
  location            = var.location
  resource_group_name = var.resource_group_name
  tags                = var.tags

  security_rule {
    name                       = "GatewayManagerToAPPGW-Allow"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "65200-65535"
    source_address_prefix      = "GatewayManager"
    destination_address_prefix = "*"
  }

  security_rule {
    name                       = "AnyToAPPGW-Allow"
    priority                   = 300
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_ranges    = ["80", "443"]
    source_address_prefix      = "*"
    destination_address_prefix = local.appgw_subnet_cidr
  }

  security_rule {
    name                       = "ALBToAny-Allow"
    priority                   = 320
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = "AzureLoadBalancer"
    destination_address_prefix = "*"
  }

  security_rule {
    name                       = "All-Deny"
    priority                   = 4096
    direction                  = "Inbound"
    access                     = "Deny"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "8080"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }
}

resource "azurerm_subnet_network_security_group_association" "appgw" {
  subnet_id                 = azurerm_subnet.appgw.id
  network_security_group_id = azurerm_network_security_group.appgw.id
}

resource "azurerm_web_application_firewall_policy" "main" {
  name                = "aiopspoc-waf-policy"
  location            = var.location
  resource_group_name = var.resource_group_name
  tags                = var.tags

  policy_settings {
    enabled                     = true
    mode                        = "Detection"
    request_body_check          = true
    file_upload_limit_in_mb     = 100
    max_request_body_size_in_kb = 128
  }

  managed_rules {
    managed_rule_set {
      type    = "OWASP"
      version = "3.2"
    }
  }
}

resource "azurerm_application_gateway" "main" {
  name                = "aiopspoc-appgw-dev-krc"
  resource_group_name = var.resource_group_name
  location            = var.location
  http2_enabled       = true
  firewall_policy_id  = azurerm_web_application_firewall_policy.main.id
  tags                = var.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.appgw.id]
  }

  sku {
    name = "WAF_v2"
    tier = "WAF_v2"
  }

  autoscale_configuration {
    min_capacity = 0
    max_capacity = 2
  }

  gateway_ip_configuration {
    name      = "appGatewayIpConfig"
    subnet_id = azurerm_subnet.appgw.id
  }

  frontend_port {
    name = "port_80"
    port = 80
  }

  frontend_port {
    name = "port_443"
    port = 443
  }

  frontend_ip_configuration {
    name                 = "appGwPublicFrontendIpIPv4"
    public_ip_address_id = azurerm_public_ip.appgw.id
  }

  ssl_certificate {
    name                = var.ssl_cert_name
    key_vault_secret_id = "${trimsuffix(data.azurerm_key_vault.cert.vault_uri, "/")}/secrets/${var.cert_secret_name}"
  }

  backend_address_pool {
    name  = "frontend-backend-pool"
    fqdns = [var.frontend_default_hostname]
  }

  backend_http_settings {
    name                                = "frontend-https-setting"
    cookie_based_affinity               = "Disabled"
    port                                = 443
    protocol                            = "Https"
    request_timeout                     = 300
    probe_name                          = "frontend-probe"
    pick_host_name_from_backend_address = true
  }

  probe {
    name                                      = "frontend-probe"
    protocol                                  = "Https"
    path                                      = "/health"
    interval                                  = 30
    timeout                                   = 20
    unhealthy_threshold                       = 3
    pick_host_name_from_backend_http_settings = true

    match {
      status_code = ["200-300"]
    }
  }

  http_listener {
    name                           = "http-listener"
    frontend_ip_configuration_name = "appGwPublicFrontendIpIPv4"
    frontend_port_name             = "port_80"
    protocol                       = "Http"
  }

  http_listener {
    name                           = "https-listener"
    frontend_ip_configuration_name = "appGwPublicFrontendIpIPv4"
    frontend_port_name             = "port_443"
    protocol                       = "Https"
    ssl_certificate_name           = var.ssl_cert_name
    host_names                     = [var.custom_domain_name]
  }

  redirect_configuration {
    name                 = "http-to-https-redirect"
    redirect_type        = "Permanent"
    target_listener_name = "https-listener"
    include_path         = true
    include_query_string = true
  }

  request_routing_rule {
    name                        = "http-rule"
    rule_type                   = "Basic"
    http_listener_name          = "http-listener"
    redirect_configuration_name = "http-to-https-redirect"
    priority                    = 100
  }

  request_routing_rule {
    name                       = "https-rule"
    rule_type                  = "Basic"
    http_listener_name         = "https-listener"
    backend_address_pool_name  = "frontend-backend-pool"
    backend_http_settings_name = "frontend-https-setting"
    priority                   = 110
  }

  # AppGW가 KV 인증서를 읽으려면 UAMI 권한이 먼저 부여돼야 함
  depends_on = [
    azurerm_role_assignment.appgw_kv_secrets_user,
    azurerm_key_vault_access_policy.appgw,
  ]
}

# 기존 Azure DNS Zone(App Service Domain 구매 시 자동 생성)에 A 레코드 추가
resource "azurerm_dns_a_record" "root" {
  count = local.create_dns_a_record ? 1 : 0

  provider            = azurerm.dns
  name                = var.dns_record_name
  zone_name           = var.dns_zone_name
  resource_group_name = var.dns_zone_resource_group
  ttl                 = 300
  records             = [azurerm_public_ip.appgw.ip_address]
}
