# Azure AI Foundry: AI Services 계정 + 프로젝트 + 모델 배포 (모두 azapi)
# Backend WebApp → AI Services 역할 할당은 app_service 모듈에서 수행(웹앱 ID에 부여).

data "azurerm_resource_group" "rg" {
  name = var.resource_group_name
}

resource "azapi_resource" "ai_services" {
  type      = "Microsoft.CognitiveServices/accounts@2025-10-01-preview"
  name      = var.ai_services_name
  parent_id = data.azurerm_resource_group.rg.id
  location  = var.location

  identity {
    type = "SystemAssigned"
  }

  body = {
    kind = "AIServices"
    sku = {
      name = "S0"
    }
    properties = {
      apiProperties             = {}
      customSubDomainName       = var.ai_services_name
      allowProjectManagement    = true
      defaultProject            = var.ai_project_name
      associatedProjects        = [var.ai_project_name]
      publicNetworkAccess       = "Disabled"
      disableLocalAuth          = true
      storedCompletionsDisabled = false
      networkAcls = {
        bypass              = "AzureServices"
        defaultAction       = "Deny"
        virtualNetworkRules = []
        ipRules = [
          for ip in var.allowed_ips : {
            value = ip
          }
        ]
      }
    }
  }

  response_export_values = ["id", "name"]
}

resource "azapi_resource" "ai_project" {
  type      = "Microsoft.CognitiveServices/accounts/projects@2025-10-01-preview"
  name      = var.ai_project_name
  parent_id = azapi_resource.ai_services.id
  location  = var.location

  identity {
    type = "SystemAssigned"
  }

  body = {
    properties = {
      description = "Default project created with the resource"
      displayName = var.ai_project_name
    }
  }

  depends_on = [time_sleep.wait_for_ai_services]
}

resource "time_sleep" "wait_for_ai_services" {
  depends_on      = [azapi_resource.ai_services]
  create_duration = "300s"
}

resource "azapi_update_resource" "ai_defender_settings" {
  type      = "Microsoft.CognitiveServices/accounts/defenderForAISettings@2025-10-01-preview"
  name      = "Default"
  parent_id = azapi_resource.ai_services.id

  body = {
    properties = {
      state = "Disabled"
    }
  }

  depends_on = [azapi_resource.ai_project]
}

resource "azapi_resource" "ai_deployment" {
  type      = "Microsoft.CognitiveServices/accounts/deployments@2025-10-01-preview"
  name      = var.ai_model_name
  parent_id = azapi_resource.ai_services.id

  body = {
    sku = {
      name     = "GlobalStandard"
      capacity = 130
    }
    properties = {
      model = {
        format  = "OpenAI"
        name    = var.ai_model_name
        version = var.ai_model_version
      }
      versionUpgradeOption = "OnceNewDefaultVersionAvailable"
      currentCapacity      = 130
      raiPolicyName        = "Microsoft.DefaultV2"
      deploymentState      = "Running"
    }
  }

  depends_on = [azapi_update_resource.ai_defender_settings]
}
