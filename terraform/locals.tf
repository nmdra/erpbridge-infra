locals {
  location = lower(var.deployment_location)

  tags = merge(var.tags, {
    managed_by = "terraform"
    demo       = "erpbridge-azure-container-apps"
  })

  mockerp_port = 8081

  mockerp_credentials_json = jsonencode(var.mockerp_credentials)
  mockerp_primary_credential = try(
    var.mockerp_credentials.credentials[0],
    {
      api_key    = ""
      api_secret = ""
      role       = ""
      identity   = ""
    },
  )
  erp_primary_key = format(
    "token %s:%s",
    local.mockerp_primary_credential.api_key,
    local.mockerp_primary_credential.api_secret,
  )

  # A changed secret name forces ACA to put the changed value in a new
  # revision. The names contain only a short hash, never a secret value.
  api_auth_token_secret_name      = "api-auth-${nonsensitive(substr(sha256(var.api_auth_token), 0, 12))}"
  erp_primary_key_secret_name     = "erp-primary-${nonsensitive(substr(sha256(local.erp_primary_key), 0, 12))}"
  mockerp_credentials_secret_name = "mockerp-credentials-${nonsensitive(substr(sha256(local.mockerp_credentials_json), 0, 12))}"

  mockerp_ingress_allowed_cidrs = setunion(
    var.mockerp_allowed_cidrs,
    var.mockerp_aca_internal_cidrs,
  )

  mockerp_ingress_sources = {
    for cidr in local.mockerp_ingress_allowed_cidrs : cidr => {
      name_prefix = contains(var.mockerp_aca_internal_cidrs, cidr) ? "aca-internal" : "postman"
      description = contains(var.mockerp_aca_internal_cidrs, cidr) ? "ERPBridge ACA internal source range" : "Approved Postman IPv4 source range"
    }
  }

  # Derive the fallback hostnames from input names to avoid a dependency cycle
  # when the selected custom URL is injected into the app environment.
  erpbridge_azure_url = "https://${var.erpbridge_app_name}.${azurerm_container_app_environment.demo.default_domain}"
  mockerp_azure_url   = "https://${var.mockerp_app_name}.${azurerm_container_app_environment.demo.default_domain}"

  custom_domains = var.custom_domains == null ? {} : {
    erpbridge = {
      hostname  = trimspace(lower(var.custom_domains.erpbridge))
      app_id    = azurerm_container_app.erpbridge.id
      app_fqdn  = azurerm_container_app.erpbridge.ingress[0].fqdn
      verify_id = azurerm_container_app.erpbridge.custom_domain_verification_id
    }
    mockerp = {
      hostname  = trimspace(lower(var.custom_domains.mockerp))
      app_id    = azurerm_container_app.mockerp.id
      app_fqdn  = azurerm_container_app.mockerp.ingress[0].fqdn
      verify_id = azurerm_container_app.mockerp.custom_domain_verification_id
    }
  }

  erpbridge_url = var.custom_domains == null ? local.erpbridge_azure_url : "https://${trimspace(lower(var.custom_domains.erpbridge))}"
  mockerp_url   = var.custom_domains == null ? local.mockerp_azure_url : "https://${trimspace(lower(var.custom_domains.mockerp))}"

  erpbridge_configured_base_url = var.erpbridge_base_url != null ? var.erpbridge_base_url : (
    var.custom_domains != null ? local.erpbridge_url : null
  )
  erpbridge_configured_erp_base_url = var.erpbridge_erp_base_url != null ? var.erpbridge_erp_base_url : (
    var.custom_domains != null ? local.mockerp_url : null
  )

  # Keep this list in the same order as the provider's nested env blocks to
  # avoid an otherwise harmless dynamic-block reorder in the plan.
  erpbridge_env = concat(
    [
      {
        name  = "MCP_PORT"
        value = tostring(var.erpbridge_mcp_port)
      },
      {
        name  = "MCP_TRANSPORT"
        value = var.erpbridge_mcp_transport
      },
    ],
    local.erpbridge_configured_base_url == null ? [] : [{
      name  = "BASE_URL"
      value = local.erpbridge_configured_base_url
    }],
    local.erpbridge_configured_erp_base_url == null ? [] : [{
      name  = "ERP_BASE_URL"
      value = local.erpbridge_configured_erp_base_url
    }],
    [
      {
        name  = "DATABASE_PATH"
        value = "/data/erpbridge.db"
      },
      {
        name  = "API_AUTH_ADMIN_ROLES"
        value = var.api_auth_admin_roles
      },
      {
        name  = "MCP_ENABLE_TEST_TOOLS"
        value = "false"
      },
      {
        name  = "APP_ENV"
        value = var.erpbridge_app_env
      },
      {
        name  = "LOG_LEVEL"
        value = var.erpbridge_log_level
      },
      {
        name  = "LOG_TO_STDERR"
        value = tostring(var.erpbridge_log_to_stderr)
      },
      {
        name  = "CACHE_MEMORY_MAX_ENTRIES"
        value = tostring(var.cache_memory_max_entries)
      },
      {
        name  = "RATE_LIMIT_RPS"
        value = tostring(var.rate_limit_rps)
      },
      {
        name  = "RATE_LIMIT_BURST"
        value = tostring(var.rate_limit_burst)
      },
      {
        name  = "CORS_ALLOWED_ORIGINS"
        value = var.cors_allowed_origins
      },
    ],
  )

  erpbridge_secret_env = [
    {
      name        = "API_AUTH_TOKEN"
      secret_name = local.api_auth_token_secret_name
    },
    {
      name        = "ERP_PRIMARY_KEY"
      secret_name = local.erp_primary_key_secret_name
    },
  ]

  mockerp_env = [
    {
      name  = "MOCK_ERP_DB_PATH"
      value = "/data/mockerp.db"
    },
    {
      name  = "MOCK_ERP_ENV"
      value = "production"
    },
    {
      name  = "MOCK_ERP_ALLOW_RESET"
      value = "false"
    },
  ]

  mockerp_secret_env = [{
    name        = "MOCK_ERP_CREDENTIALS_JSON"
    secret_name = local.mockerp_credentials_secret_name
  }]
}
