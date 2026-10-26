mock_provider "azurerm" {
  mock_data "azurerm_resource_group" {
    defaults = {
      name     = "erpbridge-hq"
      location = "centralindia"
    }
  }
  mock_data "azurerm_container_app_environment" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/erpbridge-hq/providers/Microsoft.App/managedEnvironments/erpbridge-demo-aca-standard"
    }
  }
  mock_resource "azurerm_container_app" {
    defaults = {
      id                            = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/erpbridge-hq/providers/Microsoft.App/containerApps/erpbridge-uptime"
      custom_domain_verification_id = "offline-validation-id"
      ingress = [{
        fqdn = "erpbridge-uptime.offline.centralindia.azurecontainerapps.io"
      }]
    }
  }
}
mock_provider "azapi" {}
mock_provider "cloudflare" {}
variables {
  subscription_id = "00000000-0000-0000-0000-000000000001"
  tenant_id       = "00000000-0000-0000-0000-000000000002"
}
run "internal_default" {
  command = plan
  assert {
    condition     = !azurerm_container_app.uptime.ingress[0].external_enabled && !azurerm_container_app.uptime.ingress[0].allow_insecure_connections && azurerm_container_app.uptime.ingress[0].target_port == 3001
    error_message = "Default ingress must be internal and HTTPS-only on native port 3001."
  }
  assert {
    condition     = azurerm_container_app.uptime.template[0].min_replicas == 1 && azurerm_container_app.uptime.template[0].max_replicas == 1
    error_message = "Exactly one replica is required."
  }
  assert {
    condition     = azurerm_container_app.uptime.template[0].container[0].cpu == 0.25 && azurerm_container_app.uptime.template[0].container[0].memory == "0.5Gi"
    error_message = "Trial allocation must not change silently."
  }
  assert {
    condition     = azurerm_container_app.uptime.template[0].container[0].image == "louislam/uptime-kuma:2.5.6@sha256:99286a05eb86191f3f1aab9b537950aa55b958e77d9f7a2385bc9332674e6e00"
    error_message = "Pin the verified Linux/amd64 release digest."
  }
  assert {
    condition     = azurerm_container_app.uptime.template[0].volume[0].storage_type == "EmptyDir" && azurerm_container_app.uptime.template[0].container[0].env[0].name == "UPTIME_KUMA_DB_TYPE" && azurerm_container_app.uptime.template[0].container[0].env[0].value == "sqlite" && azurerm_container_app.uptime.template[0].container[0].volume_mounts[0].path == "/app/data"
    error_message = "Only temporary local SQLite mounted at /app/data is approved."
  }
  assert {
    condition     = length(azurerm_container_app.uptime.secret) == 0 && azurerm_container_app.uptime.container_app_environment_id == data.azurerm_container_app_environment.existing.id
    error_message = "Use the existing ACA environment with no Entra client secret."
  }
  assert {
    condition     = length(cloudflare_dns_record.cname) == 0 && length(cloudflare_dns_record.validation) == 0 && length(azurerm_container_app_custom_domain.uptime) == 0 && length(azurerm_container_app_environment_managed_certificate.uptime) == 0 && length(azapi_resource_action.binding) == 0
    error_message = "Do not publish DNS or certificates by default."
  }
}
run "publication_requires_risk_acceptance" {
  command = plan
  variables { publish = true }
  expect_failures = [azurerm_container_app.uptime]
}
run "explicit_publication" {
  command = plan
  variables {
    publish                 = true
    accept_reset_setup_risk = true
    enable_custom_domain    = true
    cloudflare_zone_id      = "offline-zone"
  }
  assert {
    condition     = azurerm_container_app.uptime.ingress[0].external_enabled && !azurerm_container_app.uptime.ingress[0].allow_insecure_connections && azurerm_container_app.uptime.ingress[0].target_port == 3001
    error_message = "Publication must retain HTTPS-only native ingress."
  }
  assert {
    condition     = length(cloudflare_dns_record.cname) == 1 && !cloudflare_dns_record.cname["uptime"].proxied && cloudflare_dns_record.cname["uptime"].type == "CNAME" && cloudflare_dns_record.cname["uptime"].name == "erpbridge-uptime.nimendra.online" && cloudflare_dns_record.validation["uptime"].type == "TXT" && cloudflare_dns_record.validation["uptime"].name == "asuid.erpbridge-uptime.nimendra.online"
    error_message = "Own only the new DNS-only dashboard CNAME and verification TXT."
  }
  assert {
    condition     = length(azurerm_container_app_custom_domain.uptime) == 1 && length(azurerm_container_app_environment_managed_certificate.uptime) == 1 && length(azapi_resource_action.binding) == 1 && azurerm_container_app_environment_managed_certificate.uptime["uptime"].subject_name == "erpbridge-uptime.nimendra.online" && azapi_resource_action.binding["uptime"].body.properties.configuration.ingress.customDomains[0].bindingType == "SniEnabled"
    error_message = "Publish only the new hostname with its managed certificate binding."
  }
}
run "domain_requires_publication" {
  command = plan
  variables {
    accept_reset_setup_risk = true
    enable_custom_domain    = true
    cloudflare_zone_id      = "offline-zone"
  }
  expect_failures = [cloudflare_dns_record.cname]
}
run "domain_requires_risk_acceptance" {
  command = plan
  variables {
    publish              = true
    enable_custom_domain = true
    cloudflare_zone_id   = "offline-zone"
  }
  # The app precondition stops planning before dependent DNS is evaluated.
  expect_failures = [azurerm_container_app.uptime]
}
run "domain_requires_zone" {
  command = plan
  variables {
    publish                 = true
    accept_reset_setup_risk = true
    enable_custom_domain    = true
  }
  expect_failures = [cloudflare_dns_record.cname]
}
run "publication_without_dns" {
  command = plan
  variables {
    publish                 = true
    accept_reset_setup_risk = true
  }
  assert {
    condition     = azurerm_container_app.uptime.ingress[0].external_enabled && length(cloudflare_dns_record.cname) == 0 && output.public_url == null
    error_message = "Accepted publication may use the default Azure hostname without custom DNS."
  }
}
