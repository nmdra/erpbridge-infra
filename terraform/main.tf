data "azurerm_resource_group" "existing" {
  name = var.resource_group_name
}

resource "azurerm_log_analytics_workspace" "demo" {
  name                = var.log_analytics_workspace_name
  location            = local.location
  resource_group_name = data.azurerm_resource_group.existing.name
  sku                 = "PerGB2018"
  retention_in_days   = var.log_retention_days
  tags                = local.tags
}

resource "azurerm_container_app_environment" "demo" {
  name                       = var.container_app_environment_name
  location                   = local.location
  resource_group_name        = data.azurerm_resource_group.existing.name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.demo.id
  logs_destination           = "log-analytics"
  tags                       = local.tags
}

resource "azurerm_container_app" "erpbridge" {
  name                         = var.erpbridge_app_name
  container_app_environment_id = azurerm_container_app_environment.demo.id
  resource_group_name          = data.azurerm_resource_group.existing.name
  revision_mode                = "Single"
  tags                         = local.tags

  secret {
    name  = local.api_auth_token_secret_name
    value = var.api_auth_token
  }

  secret {
    name  = local.erp_primary_key_secret_name
    value = var.erp_primary_key
  }

  template {
    revision_suffix = "r-${substr(sha256(var.api_auth_token), 0, 12)}"
    min_replicas    = 1
    max_replicas    = 1

    volume {
      name         = "erpbridge-data"
      storage_type = "EmptyDir"
    }

    container {
      name   = "erpbridge"
      image  = var.erpbridge_image
      cpu    = 0.5
      memory = "1Gi"

      volume_mounts {
        name = "erpbridge-data"
        path = "/data"
      }

      env {
        name  = "MCP_PORT"
        value = tostring(var.erpbridge_mcp_port)
      }
      env {
        name  = "MCP_TRANSPORT"
        value = var.erpbridge_mcp_transport
      }
      dynamic "env" {
        for_each = var.erpbridge_base_url == null ? [] : [var.erpbridge_base_url]
        content {
          name  = "BASE_URL"
          value = env.value
        }
      }
      dynamic "env" {
        for_each = var.erpbridge_erp_base_url == null ? [] : [var.erpbridge_erp_base_url]
        content {
          name  = "ERP_BASE_URL"
          value = env.value
        }
      }
      env {
        name  = "DATABASE_PATH"
        value = var.erpbridge_database_path
      }
      env {
        name  = "API_AUTH_ADMIN_ROLES"
        value = var.api_auth_admin_roles
      }
      env {
        name  = "MCP_ENABLE_TEST_TOOLS"
        value = tostring(var.mcp_enable_test_tools)
      }
      env {
        name  = "APP_ENV"
        value = var.erpbridge_app_env
      }
      env {
        name  = "LOG_LEVEL"
        value = var.erpbridge_log_level
      }
      env {
        name  = "LOG_TO_STDERR"
        value = tostring(var.erpbridge_log_to_stderr)
      }
      env {
        name  = "CACHE_MEMORY_MAX_ENTRIES"
        value = tostring(var.cache_memory_max_entries)
      }
      env {
        name  = "RATE_LIMIT_RPS"
        value = tostring(var.rate_limit_rps)
      }
      env {
        name  = "RATE_LIMIT_BURST"
        value = tostring(var.rate_limit_burst)
      }
      env {
        name  = "CORS_ALLOWED_ORIGINS"
        value = var.cors_allowed_origins
      }
      env {
        name        = "API_AUTH_TOKEN"
        secret_name = local.api_auth_token_secret_name
      }
      env {
        name        = "ERP_PRIMARY_KEY"
        secret_name = local.erp_primary_key_secret_name
      }

      readiness_probe {
        transport               = "HTTP"
        port                    = var.erpbridge_mcp_port
        path                    = "/mcp/health"
        interval_seconds        = 10
        timeout                 = 5
        failure_count_threshold = 3
      }
      liveness_probe {
        transport               = "HTTP"
        port                    = var.erpbridge_mcp_port
        path                    = "/mcp/health"
        interval_seconds        = 30
        timeout                 = 5
        failure_count_threshold = 3
      }
    }
  }

  ingress {
    external_enabled           = true
    target_port                = var.erpbridge_mcp_port
    transport                  = "http"
    allow_insecure_connections = false

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }
}

resource "azurerm_container_app" "mockerp" {
  name                         = var.mockerp_app_name
  container_app_environment_id = azurerm_container_app_environment.demo.id
  resource_group_name          = data.azurerm_resource_group.existing.name
  revision_mode                = "Single"
  tags                         = local.tags

  secret {
    name  = local.mockerp_credentials_secret_name
    value = var.mockerp_credentials_json
  }

  template {
    revision_suffix = "r-${substr(sha256(var.mockerp_credentials_json), 0, 12)}"
    min_replicas    = 1
    max_replicas    = 1

    volume {
      name         = "mockerp-data"
      storage_type = "EmptyDir"
    }

    container {
      name   = "mockerp"
      image  = var.mockerp_image
      cpu    = 0.5
      memory = "1Gi"

      volume_mounts {
        name = "mockerp-data"
        path = "/data"
      }

      env {
        name  = "MOCK_ERP_DB_PATH"
        value = var.mockerp_database_path
      }
      env {
        name  = "MOCK_ERP_ENV"
        value = var.mockerp_app_env
      }
      env {
        name  = "MOCK_ERP_ALLOW_RESET"
        value = tostring(var.mockerp_allow_reset)
      }
      env {
        name        = "MOCK_ERP_CREDENTIALS_JSON"
        secret_name = local.mockerp_credentials_secret_name
      }

      readiness_probe {
        transport               = "HTTP"
        port                    = var.mockerp_container_port
        path                    = "/health"
        interval_seconds        = 10
        timeout                 = 5
        failure_count_threshold = 3
      }
      liveness_probe {
        transport               = "HTTP"
        port                    = var.mockerp_container_port
        path                    = "/health"
        interval_seconds        = 30
        timeout                 = 5
        failure_count_threshold = 3
      }
    }
  }

  ingress {
    external_enabled           = var.mockerp_external_enabled
    target_port                = var.mockerp_container_port
    transport                  = "http"
    allow_insecure_connections = !var.mockerp_https_only

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }

    dynamic "ip_security_restriction" {
      for_each = local.mockerp_ingress_sources
      content {
        name             = "${ip_security_restriction.value.name_prefix}-${replace(replace(ip_security_restriction.key, "/", "-"), ".", "-")}"
        action           = "Allow"
        ip_address_range = ip_security_restriction.key
        description      = ip_security_restriction.value.description
      }
    }
  }
}
