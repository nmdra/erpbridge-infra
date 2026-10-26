locals {
  domain = var.enable_custom_domain ? { uptime = local.hostname } : {}
}
resource "cloudflare_dns_record" "cname" {
  for_each = local.domain
  zone_id  = var.cloudflare_zone_id
  name     = each.value
  type     = "CNAME"
  content  = azurerm_container_app.uptime.ingress[0].fqdn
  ttl      = 1
  proxied  = false
  lifecycle {
    precondition {
      condition     = var.publish && var.accept_reset_setup_risk && trimspace(var.cloudflare_zone_id) != ""
      error_message = "Custom DNS requires publication, explicit reset/setup risk acceptance and the approved Cloudflare zone."
    }
  }
}
resource "cloudflare_dns_record" "validation" {
  for_each = local.domain
  zone_id  = var.cloudflare_zone_id
  name     = "asuid.${each.value}"
  type     = "TXT"
  content  = azurerm_container_app.uptime.custom_domain_verification_id
  ttl      = 300
  proxied  = false
}
resource "terraform_data" "dns_propagation" {
  for_each = local.domain
  triggers_replace = {
    hostname  = each.value
    target    = cloudflare_dns_record.cname[each.key].content
    verify_id = cloudflare_dns_record.validation[each.key].content
  }
  provisioner "local-exec" {
    command = "${path.module}/../scripts/wait-for-custom-domain-dns.sh"
    environment = {
      DNS_NAME      = each.value
      DNS_TARGET    = cloudflare_dns_record.cname[each.key].content
      DNS_VERIFY_ID = cloudflare_dns_record.validation[each.key].content
    }
  }
}
resource "azurerm_container_app_custom_domain" "uptime" {
  for_each         = local.domain
  name             = each.value
  container_app_id = azurerm_container_app.uptime.id
  lifecycle {
    ignore_changes = [certificate_binding_type, container_app_environment_certificate_id]
  }
  depends_on = [terraform_data.dns_propagation]
}
resource "azurerm_container_app_environment_managed_certificate" "uptime" {
  for_each                     = local.domain
  name                         = "uptime-${substr(sha256(each.value), 0, 12)}-managed-cert"
  container_app_environment_id = data.azurerm_container_app_environment.existing.id
  subject_name                 = each.value
  domain_control_validation    = "CNAME"
  timeouts {
    create = "30m"
    read   = "5m"
    update = "30m"
    delete = "30m"
  }
  depends_on = [azurerm_container_app_custom_domain.uptime]
}
resource "azapi_resource_action" "binding" {
  for_each    = local.domain
  type        = "Microsoft.App/containerApps@2025-07-01"
  resource_id = azurerm_container_app.uptime.id
  method      = "PATCH"
  when        = "apply"
  body = {
    properties = {
      configuration = {
        ingress = {
          customDomains = [{
            name          = each.value
            certificateId = azurerm_container_app_environment_managed_certificate.uptime[each.key].id
            bindingType   = "SniEnabled"
          }]
        }
      }
    }
  }
  lifecycle {
    replace_triggered_by = [azurerm_container_app_custom_domain.uptime, azurerm_container_app_environment_managed_certificate.uptime, terraform_data.dns_propagation]
  }
  response_export_values = ["properties.configuration.ingress.customDomains"]
}
