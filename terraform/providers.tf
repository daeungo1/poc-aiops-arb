
terraform {
  required_version = ">= 1.6.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.24"
    }
    azapi = {
      source  = "Azure/azapi"
      version = "~> 2.4"
    }
    time = {
      source  = "hashicorp/time"
      version = "~> 0.9"
    }
  }
}

provider "azurerm" {
  features {}
  subscription_id = var.subscription_id
  tenant_id       = var.tenant_id
}

provider "azurerm" {
  alias = "secrets_kv"
  features {}
  subscription_id = var.secrets_key_vault_subscription_id
  tenant_id       = var.tenant_id
}

provider "azurerm" {
  alias = "resource_reader_uami"
  features {}
  subscription_id = var.resource_reader_uami_subscription_id
  tenant_id       = var.tenant_id
}

# DNS Zone(도메인)이 있는 구독. 비우면 워크로드 구독 사용
provider "azurerm" {
  alias = "dns"
  features {}
  subscription_id = var.dns_subscription_id != "" ? var.dns_subscription_id : var.subscription_id
  tenant_id       = var.tenant_id
}

provider "azapi" {}
