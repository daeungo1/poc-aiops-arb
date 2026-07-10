# 배포 대상 구독 (azurerm provider)
subscription_id = "cfe39f45-6e84-4a68-b9f8-f719ac9db021"

# 배포 대상 리소스 그룹 - unique value 입력
resource_group_name = "azureaiops-arb"

# 리소스 배포 리전
location = "koreacentral"



# AI Services 방화벽 허용 IP 목록
allowed_ips = []

# 전체 리소스 공통 태그
tags = {
  env     = "dev"
  project = "azureaiops-poc"
  source  = "terraform"
}

# VNet (서브넷·Private DNS link 대상) - unique value 입력
vnet_name = "azureaiops-arb-vnet"

# VNet 리소스 그룹
vnet_resource_group_name = "azureaiops-arb"

# Frontend Linux Web App - unique value 입력
frontend_app_name = "aiopspoc-arb-frontend"

# Backend Linux Web App - unique value 입력
backend_app_name = "aiopspoc-arb-backend"

# Container Registry (ACR) - unique value 입력
acr_name = "aiopsarbacr"

# Azure AI Services 계정 (Foundry) - unique value 입력
ai_services_name = "azureaiops-arb-aif"

# Azure AI Foundry 프로젝트 - unique value 입력
ai_project_name = "azureaiops-arb-aif-proj"

# AI Foundry 배포 모델 이름
ai_model_name = "gpt-5.2"

# AI Foundry 배포 모델 버전
ai_model_version = "2025-12-11"

# PostgreSQL Flexible Server - unique value 입력
postgres_server_name = "azureaiops-arb-db"

# Web App 앱 설정 TZ (시간대)
tz = "Asia/Seoul"

# =============================================================================================================================================
# Entra Id SSO
# =============================================================================================================================================

# Entra ID 테넌트
tenant_id = "ab52f787-ce3d-4591-9fe3-64d2671b53ad"

# Entra ID 앱 등록 Client ID (SSO)
azure_auth_client_id = "23016aca-9b08-456a-8580-7928c2dfd4ab"

# =============================================================================================================================================
# Key Vault for Backend Webapp Environment Variables
# =============================================================================================================================================

# 시크릿 Key Vault가 있는 구독 (azurerm.secrets_kv provider)
secrets_key_vault_subscription_id = "ee00eb07-8e7e-4b90-aa99-37b8bc7a1b19"

# 시크릿 Key Vault가 속한 리소스 그룹
terraform_secrets_key_vault_resource_group_name = "azureaiops-test-rg"

# 시크릿 Key Vault (백엔드 앱 환경변수)
terraform_secrets_key_vault_name = "arb-env-kv"

# =============================================================================================================================================
# User Assigned Managed Identity (UAMI) for Agent
# =============================================================================================================================================

# 리더 UAMI가 있는 구독 (azurerm.resource_reader_uami provider)
resource_reader_uami_subscription_id = "ee00eb07-8e7e-4b90-aa99-37b8bc7a1b19"

# 리소스 조회·평가용 UAMI가 속한 리소스 그룹
resource_reader_uami_resource_group_name = "azureaiops-test-rg"

# 리소스 조회·평가용 User-Assigned Managed Identity
resource_reader_uami_name = "arb-agent-mi"


# =============================================================================================================================================
# 시나리오별로 설정
# - 시나리오 1 ~ 3: frontend_exposure_mode (appgw / appservice_managed_cert / appservice_default), 각 시나리오별 필요한 내용
# - 사나리오 4: enable_firewall (true / false)
# =============================================================================================================================================

# [시나리오 1] AppGW + KeyVault 인증서 + 커스텀 도메인 (필수 조건: Key Vault Public Access 허용)
# frontend_exposure_mode                   = "appgw"
# dns_subscription_id                      = "ee00eb07-8e7e-4b90-aa99-37b8bc7a1b19" 
# dns_zone_resource_group                  = "azureaiops-test-rg"            
# dns_zone_name                            = "metaaxagent.com"       
# dns_record_name                          = "arb"             

# appgw_cert_key_vault_name                = "arb-cert-kv"                        
# appgw_cert_key_vault_resource_group_name = "azureaiops-test-rg"
# appgw_cert_secret_name                   = "aiops-cert"                          
# appgw_ssl_cert_name                      = "appgw-cert"

# =============================================================================================================================================

# [시나리오 2] App Service 관리 인증서 + 커스텀 도메인
frontend_exposure_mode  = "appservice_managed_cert"
dns_subscription_id     = "ee00eb07-8e7e-4b90-aa99-37b8bc7a1b19"
dns_zone_resource_group = "azureaiops-test-rg"
dns_zone_name           = "metaaxagent.com"
dns_record_name         = "arb"

# =============================================================================================================================================

# [시나리오 3] App Service 기본 도메인
# frontend_exposure_mode = "appservice_default"

# =============================================================================================================================================

# [시나리오 4] Azure Firewall + UDR
enable_firewall = false
