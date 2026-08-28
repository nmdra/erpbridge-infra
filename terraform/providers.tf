provider "azurerm" {
  features {}

  # Resource providers must be registered explicitly by the Azure operator.
  # This prevents Terraform from changing unrelated subscription registrations.
  resource_provider_registrations = "none"
  subscription_id                 = var.subscription_id
  tenant_id                       = var.tenant_id
}
