terraform {
  required_version = ">= 1.9.0, < 2.0.0"

  required_providers {
    # The Cloudflare provider is intentionally absent: this root owns no DNS.
    azapi = {
      source  = "Azure/azapi"
      version = "~> 2.0"
    }
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "= 5.2.0"
    }
  }
}
