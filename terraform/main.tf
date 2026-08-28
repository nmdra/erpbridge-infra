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
    value = local.erp_primary_key
  }

  template {
    min_replicas = 1
    max_replicas = 1

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

      dynamic "env" {
        for_each = local.erpbridge_env
        content {
          name  = env.value.name
          value = env.value.value
        }
      }
      dynamic "env" {
        for_each = local.erpbridge_secret_env
        content {
          name        = env.value.name
          secret_name = env.value.secret_name
        }
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
    value = local.mockerp_credentials_json
  }

  template {
    min_replicas = 1
    max_replicas = 1

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

      dynamic "env" {
        for_each = local.mockerp_env
        content {
          name  = env.value.name
          value = env.value.value
        }
      }
      dynamic "env" {
        for_each = local.mockerp_secret_env
        content {
          name        = env.value.name
          secret_name = env.value.secret_name
        }
      }

      readiness_probe {
        transport               = "HTTP"
        port                    = local.mockerp_port
        path                    = "/health"
        interval_seconds        = 10
        timeout                 = 5
        failure_count_threshold = 3
      }
      liveness_probe {
        transport               = "HTTP"
        port                    = local.mockerp_port
        path                    = "/health"
        interval_seconds        = 30
        timeout                 = 5
        failure_count_threshold = 3
      }
    }
  }

  ingress {
    external_enabled           = true
    target_port                = local.mockerp_port
    transport                  = "http"
    allow_insecure_connections = false

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
