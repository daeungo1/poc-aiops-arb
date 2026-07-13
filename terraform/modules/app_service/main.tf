# App Service: 공용 Linux 플랜 + Frontend·Backend Web App + 사이트컨테이너 + 게시정책
# + ACR Pull / AI Services / 시크릿 KV 역할 할당 + (선택)관리 인증서 커스텀 도메인
#
# providers:
#   azurerm                      (default) — 플랜/웹앱/사이트컨테이너/역할 할당 (워크로드 구독)
#   azurerm.secrets_kv                     — 시크릿 KV 조회·접근 grant (다른 구독일 수 있음)
#   azurerm.resource_reader_uami           — resource_reader UAMI 조회 (다른 구독일 수 있음)

data "azurerm_subscription" "workload" {}

data "azurerm_user_assigned_identity" "resource_reader" {
  count               = var.resource_reader_uami_name != "" ? 1 : 0
  provider            = azurerm.resource_reader_uami
  name                = var.resource_reader_uami_name
  resource_group_name = var.resource_reader_uami_resource_group_name != "" ? var.resource_reader_uami_resource_group_name : var.resource_group_name
}

locals {
  has_resource_reader_uami = var.resource_reader_uami_name != ""

  resource_reader_uami_id           = local.has_resource_reader_uami ? data.azurerm_user_assigned_identity.resource_reader[0].id : ""
  resource_reader_uami_client_id    = local.has_resource_reader_uami ? data.azurerm_user_assigned_identity.resource_reader[0].client_id : ""
  resource_reader_uami_principal_id = local.has_resource_reader_uami ? data.azurerm_user_assigned_identity.resource_reader[0].principal_id : ""

  # Backend → AI Services 내장 역할.
  # 포털의 "Azure AI User"와 동일한 내장 역할은 GUID 53ca6127-db72-4b80-b1b0-d745d6d5456d.
  # role_definition_name 으로 조회하면 테넌트/API에 따라 실패할 수 있으므로 role_definition_id 만 사용.
  backend_ai_builtin_role_guids = {
    AzureAIUser           = "53ca6127-db72-4b80-b1b0-d745d6d5456d"
    CognitiveServicesUser = "a97b65f3-24c7-4388-baec-2e87135dc908"
  }
  backend_ai_resolved_definition_id = "${data.azurerm_subscription.workload.id}/providers/Microsoft.Authorization/roleDefinitions/${local.backend_ai_builtin_role_guids[var.backend_ai_builtin_role]}"
  backend_ai_role_definition_id     = var.backend_ai_role_definition_id != "" ? var.backend_ai_role_definition_id : local.backend_ai_resolved_definition_id

  # 백엔드 Linux Web App DB·SSO 시크릿 앱 설정(직접 값 — KV 데이터플레인 정책 차단 회피)
  backend_app_keyvault_settings = {
    AZURE_AUTH_STATE_SECRET = var.azure_auth_state_secret
    DB_USER                 = var.db_user
    DB_PASSWORD             = var.db_password
    DB_NAME                 = var.db_name
    DB_PORT                 = var.db_port
  }

  # 관리 인증서 모드 + DNS 변수 지정 시 도메인 검증 레코드(CNAME·asuid TXT)를 자동 생성
  create_appsvc_dns = var.frontend_exposure_mode == "appservice_managed_cert" && var.dns_zone_name != "" && var.dns_record_name != ""
}

resource "azurerm_service_plan" "app" {
  name                = "aiopspoc-app-plan-dev-krc"
  resource_group_name = var.resource_group_name
  location            = var.location
  os_type             = "Linux"
  sku_name            = "B1"
  tags                = var.tags
}

resource "azurerm_linux_web_app" "frontend" {
  name                = var.frontend_app_name
  resource_group_name = var.resource_group_name
  location            = var.location
  service_plan_id     = azurerm_service_plan.app.id
  https_only          = true
  # appgw 모드: 프론트를 Private(인바운드는 AppGW→Private Endpoint 경유)로. 그 외: 인터넷 직접 공개.
  # 아웃바운드는 항상 VNet 통합 서브넷으로 Backend(비공개)에 도달.
  public_network_access_enabled = var.frontend_is_private ? false : true
  virtual_network_subnet_id     = var.frontend_subnet_id
  tags                          = var.tags

  site_config {
    always_on              = false
    minimum_tls_version    = "1.2"
    ftps_state             = "FtpsOnly"
    vnet_route_all_enabled = true
    app_command_line       = ""
  }

  identity {
    type = "SystemAssigned"
  }

  app_settings = {
    BACKEND_URL = "https://${azurerm_linux_web_app.backend.default_hostname}"
    TZ          = var.tz
  }
}

resource "azurerm_linux_web_app" "backend" {
  name                          = var.backend_app_name
  resource_group_name           = var.resource_group_name
  location                      = var.location
  service_plan_id               = azurerm_service_plan.app.id
  https_only                    = true
  public_network_access_enabled = false
  virtual_network_subnet_id     = var.backend_subnet_id
  tags                          = var.tags

  site_config {
    always_on              = false
    minimum_tls_version    = "1.2"
    ftps_state             = "FtpsOnly"
    vnet_route_all_enabled = true
    app_command_line       = ""
  }

  identity {
    type         = local.has_resource_reader_uami ? "SystemAssigned, UserAssigned" : "SystemAssigned"
    identity_ids = local.has_resource_reader_uami ? [local.resource_reader_uami_id] : null
  }

  app_settings = merge(
    {
      # Entra SSO: MSAL 기밀 클라이언트 — UAMI 페더레이션(api://AzureADTokenExchange client_assertion). SAMI는 Foundry 등 DefaultAzureCredential 전용.
      AZURE_AUTH_CLIENT_ID = var.azure_auth_client_id
      AZURE_AUTH_TENANT_ID = var.tenant_id
      # Entra redirect_uri 는 공개된 Frontend 호스트의 백엔드 콜백 경로 `/api/getAToken` (nginx가 /api/* 를 BE로 프록시).
      # 토폴로지에 따라 공개 호스트가 달라짐(public_host). 이 값과 동일한 URI를 Entra 앱 등록의 Web redirect URI에도 등록해야 함.
      AZURE_AUTH_REDIRECT_URI       = "https://${var.public_host}/api/getAToken"
      AZURE_AUTH_SSO_UAMI_CLIENT_ID = local.has_resource_reader_uami ? local.resource_reader_uami_client_id : ""
      # Frontend(azurewebsites.net)는 HTTPS 종단이므로 secure 쿠키 강제(SameSite=Lax + Secure)
      COOKIE_SECURE = "true"

      # Azure AI Foundry
      AZURE_AI_ENDPOINT              = "https://${var.ai_services_name}.services.ai.azure.com"
      AZURE_AI_PROJECT_NAME          = var.ai_project_name
      AZURE_AI_MODEL_DEPLOYMENT_NAME = var.ai_deployment_name

      # PostgreSQL (호스트만 Terraform — DB 자격·DB명·포트는 Key Vault 시크릿 참조)
      DB_HOST = var.postgres_fqdn

      # Resource discovery/assessment Managed Identity
      AZURE_RESOURCE_READER_UAMI_CLIENT_ID   = local.has_resource_reader_uami ? local.resource_reader_uami_client_id : ""
      AZURE_RESOURCE_READER_UAMI_OBJECT_ID   = local.has_resource_reader_uami ? local.resource_reader_uami_principal_id : ""
      AZURE_RESOURCE_READER_UAMI_RESOURCE_ID = local.has_resource_reader_uami ? local.resource_reader_uami_id : ""
      AZURE_RESOURCE_READER_UAMI_NAME        = var.resource_reader_uami_name

      # Timezone
      TZ = var.tz
    },
    local.backend_app_keyvault_settings
  )
}

resource "azapi_resource" "backend_sitecontainer" {
  type      = "Microsoft.Web/sites/sitecontainers@2024-11-01"
  name      = "main"
  parent_id = azurerm_linux_web_app.backend.id

  body = {
    properties = {
      image                                  = "${var.acr_name}.azurecr.io/aiops-be:latest"
      targetPort                             = "5100"
      isMain                                 = true
      authType                               = "SystemIdentity"
      userManagedIdentityClientId            = "SystemIdentity"
      volumeMounts                           = []
      environmentVariables                   = []
      inheritAppSettingsAndConnectionStrings = true
    }
  }
}

resource "azapi_resource" "frontend_sitecontainer" {
  type      = "Microsoft.Web/sites/sitecontainers@2024-11-01"
  name      = "main"
  parent_id = azurerm_linux_web_app.frontend.id

  body = {
    properties = {
      image                                  = "${var.acr_name}.azurecr.io/aiops-fe:latest"
      targetPort                             = "80"
      isMain                                 = true
      authType                               = "SystemIdentity"
      userManagedIdentityClientId            = "SystemIdentity"
      volumeMounts                           = []
      environmentVariables                   = []
      inheritAppSettingsAndConnectionStrings = true
    }
  }
}

resource "azapi_update_resource" "backend_ftp_policy" {
  type      = "Microsoft.Web/sites/basicPublishingCredentialsPolicies@2024-11-01"
  name      = "ftp"
  parent_id = azurerm_linux_web_app.backend.id
  body      = { properties = { allow = false } }
}

resource "azapi_update_resource" "backend_scm_policy" {
  type      = "Microsoft.Web/sites/basicPublishingCredentialsPolicies@2024-11-01"
  name      = "scm"
  parent_id = azurerm_linux_web_app.backend.id
  body      = { properties = { allow = false } }
}

resource "azapi_update_resource" "frontend_ftp_policy" {
  type      = "Microsoft.Web/sites/basicPublishingCredentialsPolicies@2024-11-01"
  name      = "ftp"
  parent_id = azurerm_linux_web_app.frontend.id
  body      = { properties = { allow = false } }
}

resource "azapi_update_resource" "frontend_scm_policy" {
  type      = "Microsoft.Web/sites/basicPublishingCredentialsPolicies@2024-11-01"
  name      = "scm"
  parent_id = azurerm_linux_web_app.frontend.id
  body      = { properties = { allow = false } }
}

# ── ACR: Frontend·Backend SAMI → AcrPull (이미지 pull 인증용) ──
resource "azurerm_role_assignment" "frontend_acr_pull" {
  scope                = var.acr_id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_linux_web_app.frontend.identity[0].principal_id
}

resource "azurerm_role_assignment" "backend_acr_pull" {
  scope                = var.acr_id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_linux_web_app.backend.identity[0].principal_id
}

# ── Foundry: Backend WebApp → AI Services 역할 할당 ──
resource "azurerm_role_assignment" "backend_ai_user" {
  scope              = var.ai_services_id
  role_definition_id = local.backend_ai_role_definition_id
  principal_id       = azurerm_linux_web_app.backend.identity[0].principal_id
}

# ── 시나리오 2: App Service 무료 관리 인증서 + 커스텀 도메인 (KeyVault 미사용) ──
# 선행 조건: DNS에 asuid.<host> TXT(도메인 검증) 및 <host> CNAME(→ <app>.azurewebsites.net) 레코드 필요.
# dns_zone_name·dns_record_name 지정 시 아래에서 Terraform이 자동 생성(azurerm.dns provider). 비우면 수동 관리.
# 무료 관리 인증서는 apex(naked) 도메인 미지원 → 서브도메인 사용.

resource "azurerm_dns_cname_record" "frontend" {
  count               = local.create_appsvc_dns ? 1 : 0
  provider            = azurerm.dns
  name                = var.dns_record_name
  zone_name           = var.dns_zone_name
  resource_group_name = var.dns_zone_resource_group
  ttl                 = 300
  record              = azurerm_linux_web_app.frontend.default_hostname
}

resource "azurerm_dns_txt_record" "frontend_asuid" {
  count               = local.create_appsvc_dns ? 1 : 0
  provider            = azurerm.dns
  name                = "asuid.${var.dns_record_name}"
  zone_name           = var.dns_zone_name
  resource_group_name = var.dns_zone_resource_group
  ttl                 = 300

  record {
    value = azurerm_linux_web_app.frontend.custom_domain_verification_id
  }
}

# Azure가 바인딩·인증서 발급 시 공개 DNS를 실제 검증하므로 레코드 전파 대기 후 바인딩
resource "time_sleep" "wait_for_dns_propagation" {
  count      = local.create_appsvc_dns ? 1 : 0
  depends_on = [azurerm_dns_cname_record.frontend, azurerm_dns_txt_record.frontend_asuid]

  create_duration = "180s"
}

resource "azurerm_app_service_custom_hostname_binding" "frontend" {
  count               = var.frontend_exposure_mode == "appservice_managed_cert" ? 1 : 0
  hostname            = var.custom_domain_name
  app_service_name    = azurerm_linux_web_app.frontend.name
  resource_group_name = var.resource_group_name

  # DNS 자동 생성 시 전파 대기 후 바인딩(count=0이면 no-op → 수동 DNS 동작 보존)
  depends_on = [time_sleep.wait_for_dns_propagation]

  # 인증서 바인딩이 ssl_state/thumbprint를 관리하므로 여기서는 무시
  lifecycle {
    ignore_changes = [ssl_state, thumbprint]
  }
}

resource "azurerm_app_service_managed_certificate" "frontend" {
  count                      = var.frontend_exposure_mode == "appservice_managed_cert" ? 1 : 0
  custom_hostname_binding_id = azurerm_app_service_custom_hostname_binding.frontend[0].id
}

resource "azurerm_app_service_certificate_binding" "frontend" {
  count               = var.frontend_exposure_mode == "appservice_managed_cert" ? 1 : 0
  hostname_binding_id = azurerm_app_service_custom_hostname_binding.frontend[0].id
  certificate_id      = azurerm_app_service_managed_certificate.frontend[0].id
  ssl_state           = "SniEnabled"
}
