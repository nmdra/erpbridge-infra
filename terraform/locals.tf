locals {
  location = lower(var.deployment_location)

  tags = merge(var.tags, {
    managed_by = "terraform"
    demo       = "erpbridge-azure-container-apps"
  })

  # A changed secret name forces ACA to put the changed value in a new
  # revision. The names contain only a short hash, never a secret value.
  api_auth_token_secret_name      = "api-auth-${nonsensitive(substr(sha256(var.api_auth_token), 0, 12))}"
  erp_primary_key_secret_name     = "erp-primary-${nonsensitive(substr(sha256(var.erp_primary_key), 0, 12))}"
  mockerp_credentials_secret_name = "mockerp-credentials-${nonsensitive(substr(sha256(var.mockerp_credentials_json), 0, 12))}"

  erpbridge_url = "https://${azurerm_container_app.erpbridge.name}.${azurerm_container_app_environment.demo.default_domain}"
  mockerp_url   = "https://${azurerm_container_app.mockerp.name}.${azurerm_container_app_environment.demo.default_domain}"
}
