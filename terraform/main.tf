# 루트는 provider 설정(providers.tf)·입력값(tfvars)·시나리오 분기·모듈 오케스트레이션만 담당.
# data source는 각 모듈이 이름을 받아 자체 조회(self-contained).

locals {
  # RG 이름은 var를 그대로 사용(데이터소스 조회 없이). 각 모듈이 필요 시 자체 조회.
  vnet_resource_group_name = var.vnet_resource_group_name != "" ? var.vnet_resource_group_name : var.resource_group_name

  private_dns_zone_names = {
    azurecr       = "privatelink.azurecr.io"
    azurewebsites = "privatelink.azurewebsites.net"
    cognitive     = "privatelink.cognitiveservices.azure.com"
    openai        = "privatelink.openai.azure.com"
    services_ai   = "privatelink.services.ai.azure.com"
    vaultcore     = "privatelink.vaultcore.azure.net"
    postgres      = "privatelink.postgres.database.azure.com"
  }

  # 토폴로지별 분기 헬퍼
  frontend_is_private = var.frontend_exposure_mode == "appgw"
  use_custom_domain   = var.frontend_exposure_mode == "appgw" || var.frontend_exposure_mode == "appservice_managed_cert"

  # 커스텀 도메인 FQDN — custom_domain_name 명시값 우선, 없으면 dns_record_name + dns_zone_name 으로 조립
  custom_domain = var.custom_domain_name != "" ? var.custom_domain_name : (var.dns_record_name != "" && var.dns_zone_name != "" ? "${var.dns_record_name}.${var.dns_zone_name}" : "")

  # 프론트 공개 호스트 — SSO redirect_uri 조립에 사용 (appgw/관리인증서면 커스텀 도메인, 기본이면 azurewebsites.net)
  public_host = var.frontend_exposure_mode == "appservice_default" ? "${var.frontend_app_name}.azurewebsites.net" : local.custom_domain

  # AppGW cert KV RG fallback: 명시값 → 시크릿 KV RG → 워크로드 RG
  appgw_cert_kv_rg = var.appgw_cert_key_vault_resource_group_name != "" ? var.appgw_cert_key_vault_resource_group_name : (trimspace(var.terraform_secrets_key_vault_resource_group_name) != "" ? var.terraform_secrets_key_vault_resource_group_name : var.resource_group_name)
  dns_zone_rg      = var.dns_zone_resource_group != "" ? var.dns_zone_resource_group : var.resource_group_name
}

# ── 네트워킹: NSG · 서브넷 · Private DNS zone ──
module "network" {
  source = "./modules/network"

  resource_group_name      = var.resource_group_name
  location                 = var.location
  tags                     = var.tags
  vnet_name                = var.vnet_name
  vnet_resource_group_name = local.vnet_resource_group_name
  private_dns_zone_names   = local.private_dns_zone_names
}

# ── ACR ──
module "acr" {
  source = "./modules/acr"

  resource_group_name = var.resource_group_name
  location            = var.location
  tags                = var.tags
  acr_name            = var.acr_name
}

# ── Azure AI Foundry ──
module "ai_foundry" {
  source = "./modules/ai_foundry"

  resource_group_name = var.resource_group_name
  location            = var.location
  ai_services_name    = var.ai_services_name
  ai_project_name     = var.ai_project_name
  ai_model_name       = var.ai_model_name
  ai_model_version    = var.ai_model_version
  allowed_ips         = var.allowed_ips
}

# ── PostgreSQL ──
module "database" {
  source = "./modules/database"

  resource_group_name  = var.resource_group_name
  location             = var.location
  tags                 = var.tags
  postgres_server_name = var.postgres_server_name

  admin_login    = var.postgres_admin_login
  admin_password = var.postgres_admin_password
  db_name        = var.postgres_db_name
}

# ── App Service: Frontend·Backend Web App ──
module "app_service" {
  source = "./modules/app_service"

  providers = {
    azurerm                      = azurerm
    azurerm.resource_reader_uami = azurerm.resource_reader_uami
    azurerm.dns                  = azurerm.dns
  }

  resource_group_name = var.resource_group_name
  location            = var.location
  tags                = var.tags
  tenant_id           = var.tenant_id

  frontend_app_name  = var.frontend_app_name
  backend_app_name   = var.backend_app_name
  frontend_subnet_id = module.network.frontend_subnet_id
  backend_subnet_id  = module.network.backend_subnet_id

  frontend_is_private    = local.frontend_is_private
  public_host            = local.public_host
  frontend_exposure_mode = var.frontend_exposure_mode
  custom_domain_name     = local.custom_domain
  tz                     = var.tz

  azure_auth_client_id                     = var.azure_auth_client_id
  resource_reader_uami_name                = var.resource_reader_uami_name
  resource_reader_uami_resource_group_name = var.resource_reader_uami_resource_group_name

  ai_services_name              = var.ai_services_name
  ai_project_name               = var.ai_project_name
  ai_deployment_name            = module.ai_foundry.ai_deployment_name
  ai_services_id                = module.ai_foundry.ai_services_id
  backend_ai_builtin_role       = var.backend_ai_builtin_role
  backend_ai_role_definition_id = var.backend_ai_role_definition_id

  postgres_fqdn = module.database.fqdn

  acr_name = var.acr_name
  acr_id   = module.acr.acr_id

  db_user                 = var.postgres_admin_login
  db_password             = var.postgres_admin_password
  db_name                 = var.postgres_db_name
  db_port                 = var.postgres_db_port
  azure_auth_state_secret = var.azure_auth_state_secret

  # 시나리오 2: 관리 인증서 도메인 검증 레코드 자동 생성용 (dns 변수 비우면 미생성)
  dns_zone_name           = var.dns_zone_name
  dns_zone_resource_group = local.dns_zone_rg
  dns_record_name         = var.dns_record_name
}

# ── Private Endpoint 일괄 생성 ──
module "private_endpoints" {
  source = "./modules/private_endpoints"

  resource_group_name  = var.resource_group_name
  location             = var.location
  tags                 = var.tags
  pe_subnet_id         = module.network.pe_subnet_id
  private_dns_zone_ids = module.network.private_dns_zone_ids
  frontend_is_private  = local.frontend_is_private

  backend_app_name                      = var.backend_app_name
  backend_web_app_id                    = module.app_service.backend_web_app_id
  frontend_app_name                     = var.frontend_app_name
  frontend_web_app_id                   = module.app_service.frontend_web_app_id
  acr_name                              = var.acr_name
  acr_id                                = module.acr.acr_id
  ai_services_name                      = var.ai_services_name
  ai_services_id                        = module.ai_foundry.ai_services_id
  postgres_server_name                  = var.postgres_server_name
  postgres_server_id                    = module.database.server_id

  # AI account PE는 모델 배포 완료 후 생성(원본 depends_on 유지)
  depends_on = [module.ai_foundry]
}

# ── 시나리오 1: Application Gateway (frontend_exposure_mode=appgw) ──
module "appgw" {
  count  = var.frontend_exposure_mode == "appgw" ? 1 : 0
  source = "./modules/appgw"

  providers = {
    azurerm     = azurerm
    azurerm.kv  = azurerm.secrets_kv
    azurerm.dns = azurerm.dns
  }

  resource_group_name      = var.resource_group_name
  location                 = var.location
  tenant_id                = var.tenant_id
  tags                     = var.tags
  vnet_name                = var.vnet_name
  vnet_resource_group_name = local.vnet_resource_group_name

  frontend_default_hostname = module.app_service.frontend_default_hostname
  custom_domain_name        = local.custom_domain

  cert_key_vault_name                = var.appgw_cert_key_vault_name
  cert_key_vault_resource_group_name = local.appgw_cert_kv_rg
  cert_secret_name                   = var.appgw_cert_secret_name
  ssl_cert_name                      = var.appgw_ssl_cert_name
  key_vault_permission_model         = var.key_vault_permission_model

  dns_zone_name           = var.dns_zone_name
  dns_zone_resource_group = local.dns_zone_rg
  dns_record_name         = var.dns_record_name
}

# ── 시나리오 4: Azure Firewall + UDR (enable_firewall=true) ──
module "firewall" {
  count  = var.enable_firewall ? 1 : 0
  source = "./modules/firewall"

  resource_group_name      = var.resource_group_name
  location                 = var.location
  tags                     = var.tags
  vnet_name                = var.vnet_name
  vnet_resource_group_name = local.vnet_resource_group_name

  pe_subnet_id       = module.network.pe_subnet_id
  backend_subnet_id  = module.network.backend_subnet_id
  frontend_subnet_id = module.network.frontend_subnet_id
}
