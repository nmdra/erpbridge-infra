provider "azurerm" {
  features {
    virtual_machine {
      # An accidental VM deletion must retain the OS disk, which holds the only
      # copy of the SQLite registry. Retention protects data, not service.
      delete_os_disk_on_deletion = false
    }
  }

  # The operator registers Azure resource providers separately.
  resource_provider_registrations = "none"
  subscription_id                 = var.subscription_id
  tenant_id                       = var.tenant_id

  # Shared-key access is disabled on the backup account, so the provider uses
  # Microsoft Entra data-plane authentication to manage the blob container.
  storage_use_azuread = true
}

provider "azapi" {
  subscription_id            = var.subscription_id
  tenant_id                  = var.tenant_id
  skip_provider_registration = true
}
