# =============================================================================================================================================
# AIOps ARB POC — appservice_default 시나리오 (KV 의존 제거 버전)
# 민감값(postgres_admin_password, azure_auth_state_secret)은 secret.auto.tfvars(gitignore)에 있음
# =============================================================================================================================================

# 배포 대상 구독
subscription_id = "2cf925b6-80cb-4567-abda-5ccd3010aab5"

# 배포 대상 리소스 그룹 (사전 생성됨)
resource_group_name = "rg-aiops-arb-poc"

# 리소스 배포 리전
location = "koreacentral"

# AI Services 방화벽 허용 IP 목록
allowed_ips = []

# 전체 리소스 공통 태그
tags = {
  env     = "dev"
  project = "aiops-arb-poc"
  source  = "terraform"
}

# VNet (사전 생성됨, 10.0.0.0/16). Terraform이 서브넷을 이 안에 생성
vnet_name = "vnet-aiops-arb-poc"

# VNet 리소스 그룹
vnet_resource_group_name = "rg-aiops-arb-poc"

# Frontend Linux Web App (전역 고유)
frontend_app_name = "aiops-arb-fe-3jzra"

# Backend Linux Web App (전역 고유)
backend_app_name = "aiops-arb-be-3jzra"

# Container Registry (ACR, 전역 고유)
acr_name = "aiopsarb3jzra"

# Azure AI Services 계정 (Foundry, 전역 고유)
ai_services_name = "aiops-arb-aif-3jzra"

# Azure AI Foundry 프로젝트
ai_project_name = "proj-aiops-arb"

# AI Foundry 배포 모델 이름/버전
ai_model_name    = "gpt-5.2"
ai_model_version = "2025-12-11"

# PostgreSQL Flexible Server (전역 고유)
postgres_server_name = "aiops-arb-pg-3jzra"

# Postgres 자격증명·DB명 (KV 미사용 · 직접 전달). 비밀번호는 secret.auto.tfvars
postgres_admin_login = "aiopsadmin"
postgres_db_name     = "aiops"
postgres_db_port     = "5432"

# Web App 앱 설정 TZ (시간대)
tz = "Asia/Seoul"

# =============================================================================================================================================
# Entra Id SSO (사전 생성됨)
# =============================================================================================================================================

tenant_id = "f6d04047-ac77-4fc2-941f-798ed4d54fcf"

# Entra ID 앱 등록 Client ID (SSO)
azure_auth_client_id = "3f39d0d8-e47f-4482-b409-004b8808b30c"

# =============================================================================================================================================
# User Assigned Managed Identity (UAMI) for Agent (사전 생성됨)
# =============================================================================================================================================

resource_reader_uami_subscription_id     = "2cf925b6-80cb-4567-abda-5ccd3010aab5"
resource_reader_uami_resource_group_name = "rg-aiops-arb-poc"
resource_reader_uami_name                = "id-aiops-arb-reader"

# =============================================================================================================================================
# 시나리오: [3] App Service 기본 도메인 (azurewebsites.net), 방화벽 없음
# =============================================================================================================================================

frontend_exposure_mode = "appservice_default"
enable_firewall        = false
