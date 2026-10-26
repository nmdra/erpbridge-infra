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
  erp_primary_key = var.erp_target == "erpnext" ? var.erpnext_api_token : format(
    "token %s:%s",
    local.mockerp_primary_credential.api_key,
    local.mockerp_primary_credential.api_secret,
  )

  # A changed secret name forces ACA to put the changed value in a new
  # revision. The names contain only a short hash, never a secret value.
  api_auth_token_secret_name      = "api-auth-${nonsensitive(substr(sha256(var.api_auth_token), 0, 12))}"
  plugin_api_key_secret_name      = "plugin-api-${nonsensitive(substr(sha256(var.plugin_api_key == null ? "" : var.plugin_api_key), 0, 12))}"
  policy_gate_api_key_secret_name = "policy-gate-api-${nonsensitive(substr(sha256(var.policy_gate_api_key == null ? "" : var.policy_gate_api_key), 0, 12))}"
  data_api_key_secret_name        = "data-api-${nonsensitive(substr(sha256(var.external_service_api_key == null ? "" : var.external_service_api_key), 0, 12))}"
  resend_api_key_secret_name      = "resend-api-${nonsensitive(substr(sha256(var.resend_api_key == null ? "" : var.resend_api_key), 0, 12))}"
  erp_primary_key_secret_name     = "erp-primary-${nonsensitive(substr(sha256(local.erp_primary_key == null ? "" : local.erp_primary_key), 0, 12))}"
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

  # DNS ownership is separate from certificate ownership. The bridge record can
  # become an A record that points at the VM while the Container App keeps its
  # binding and certificate for the rollback window.
  erpbridge_dns_type    = var.erpbridge_hosting == "vm" ? "A" : "CNAME"
  erpbridge_dns_content = var.erpbridge_hosting == "vm" ? trimspace(var.erpbridge_vm_ipv4) : azurerm_container_app.erpbridge.ingress[0].fqdn
  # The CNAME propagation wait only makes sense while the bridge record points
  # at the Container App. In VM mode it would fight the A record, so it is not
  # created, while the binding, TXT record, and certificate are retained.
  erpbridge_verify_cname = var.erpbridge_hosting == "aca"

  custom_domains = var.custom_domains == null ? {} : {
    erpbridge = {
      hostname    = trimspace(lower(var.custom_domains.erpbridge))
      record_type = local.erpbridge_dns_type
      content     = local.erpbridge_dns_content
      verify      = local.erpbridge_verify_cname
      app_id      = azurerm_container_app.erpbridge.id
      app_fqdn    = azurerm_container_app.erpbridge.ingress[0].fqdn
      verify_id   = azurerm_container_app.erpbridge.custom_domain_verification_id
    }
    mockerp = {
      hostname    = trimspace(lower(var.custom_domains.mockerp))
      record_type = "CNAME"
      content     = azurerm_container_app.mockerp.ingress[0].fqdn
      verify      = true
      app_id      = azurerm_container_app.mockerp.id
      app_fqdn    = azurerm_container_app.mockerp.ingress[0].fqdn
      verify_id   = azurerm_container_app.mockerp.custom_domain_verification_id
    }
  }

  # Domains that still need the Container App CNAME propagation check.
  custom_domains_verified = {
    for key, value in local.custom_domains : key => value if value.verify
  }

  erpbridge_url = var.custom_domains == null ? local.erpbridge_azure_url : "https://${trimspace(lower(var.custom_domains.erpbridge))}"
  mockerp_url   = var.custom_domains == null ? local.mockerp_azure_url : "https://${trimspace(lower(var.custom_domains.mockerp))}"

  erpbridge_configured_base_url = var.erpbridge_base_url != null ? var.erpbridge_base_url : (
    var.custom_domains != null ? local.erpbridge_url : null
  )
  # Always derive both URL and credential from the same target. The legacy
  # override is checked on the app resource, not used as a second routing path.
  erpbridge_configured_erp_base_url = var.erp_target == "erpnext" ? var.erpnext_url : local.mockerp_url

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
        # The pinned image runs as non-root. ACA EmptyDir mounts are root-owned.
        # This POC already has ephemeral state, so use the writable container /tmp.
        name  = "DATABASE_PATH"
        value = "/tmp/erpbridge.db"
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
    var.plugin_endpoint_allowlist == "" ? [] : [{
      name  = "PLUGIN_ENDPOINT_ALLOWLIST"
      value = var.plugin_endpoint_allowlist
    }],
  )

  erpbridge_secret_env = concat([
    {
      name        = "API_AUTH_TOKEN"
      secret_name = local.api_auth_token_secret_name
    },
    {
      name        = "ERP_PRIMARY_KEY"
      secret_name = local.erp_primary_key_secret_name
    },
    ],
    nonsensitive(var.plugin_api_key == null) ? [] : [{
      name        = "PLUGIN_API_KEY"
      secret_name = local.plugin_api_key_secret_name
    }],
    nonsensitive(var.policy_gate_api_key == null) ? [] : [{
      name        = "POLICY_GATE_API_KEY"
      secret_name = local.policy_gate_api_key_secret_name
    }],
    nonsensitive(var.external_service_api_key == null) ? [] : [{
      name        = "DATA_API_KEY"
      secret_name = local.data_api_key_secret_name
    }],
    nonsensitive(var.resend_api_key == null) ? [] : [{
      name        = "RESEND_API_KEY"
      secret_name = local.resend_api_key_secret_name
    }],
  )

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
