data "azurerm_resource_group" "existing" {
  name = var.resource_group_name
}
data "azurerm_container_app_environment" "existing" {
  name                = var.container_app_environment_name
  resource_group_name = data.azurerm_resource_group.existing.name
}
locals {
  hostname = "erpbridge-uptime.nimendra.online"
  # Official Docker Hub 2.5.6 Linux/amd64 manifest, verified against registry API.
  image = "louislam/uptime-kuma:2.5.6@sha256:99286a05eb86191f3f1aab9b537950aa55b958e77d9f7a2385bc9332674e6e00"
}
resource "azurerm_container_app" "uptime" {
  name                         = "erpbridge-uptime"
  resource_group_name          = data.azurerm_resource_group.existing.name
  container_app_environment_id = data.azurerm_container_app_environment.existing.id
  workload_profile_name        = "Consumption"
  revision_mode                = "Single"
  tags                         = { purpose = "disposable-uptime-prototype" }
  ingress {
    external_enabled           = var.publish
    target_port                = 3001
    transport                  = "http"
    allow_insecure_connections = false
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }
  template {
    min_replicas = 1
    max_replicas = 1
    volume {
      name         = "data"
      storage_type = "EmptyDir"
    }
    container {
      name   = "uptime-kuma"
      image  = local.image
      cpu    = 0.25
      memory = "0.5Gi"
      env {
        name  = "UPTIME_KUMA_DB_TYPE"
        value = "sqlite"
      }
      volume_mounts {
        name = "data"
        path = "/app/data"
      }
      # Native root returns 302, accepted by ACA HTTP probes. These are process
      # checks, not evidence that a populated public page or its monitors work.
      startup_probe {
        transport               = "HTTP"
        port                    = 3001
        path                    = "/"
        interval_seconds        = 10
        timeout                 = 3
        failure_count_threshold = 30
      }
      liveness_probe {
        transport               = "HTTP"
        port                    = 3001
        path                    = "/"
        interval_seconds        = 30
        timeout                 = 3
        failure_count_threshold = 5
      }
      readiness_probe {
        transport               = "HTTP"
        port                    = 3001
        path                    = "/"
        interval_seconds        = 10
        timeout                 = 3
        failure_count_threshold = 3
      }
    }
  }
  lifecycle {
    precondition {
      condition     = !var.publish || var.accept_reset_setup_risk
      error_message = "Publication requires accept_reset_setup_risk=true: loss of the disposable SQLite database reopens public first-user setup, allowing anyone to claim the administrator account."
    }
  }
}
