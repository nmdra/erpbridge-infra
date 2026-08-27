provider "azurerm" {
  features {}

  resource_provider_registrations = "all"
  subscription_id                 = var.subscription_id
  tenant_id                       = var.tenant_id
}
