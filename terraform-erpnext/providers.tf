provider "azurerm" {
  features {}

  # The operator registers Azure resource providers separately.
  resource_provider_registrations = "none"
  subscription_id                 = var.subscription_id
  tenant_id                       = var.tenant_id
}

# Authentication comes only from CLOUDFLARE_API_TOKEN in the environment.
provider "cloudflare" {}
