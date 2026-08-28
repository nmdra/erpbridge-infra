resource "cloudflare_dns_record" "app_cname" {
  for_each = local.custom_domains

  zone_id = var.custom_domains.zone_id
  name    = each.value.hostname
  type    = "CNAME"
  content = each.value.app_fqdn
  ttl     = 1
  proxied = false
}

resource "cloudflare_dns_record" "app_validation" {
  for_each = local.custom_domains

  zone_id = var.custom_domains.zone_id
  name    = "asuid.${each.value.hostname}"
  type    = "TXT"
  content = each.value.verify_id
  ttl     = 300
  proxied = false
}

resource "terraform_data" "dns_propagation" {
  for_each = local.custom_domains

  input = {
    cname_record_id      = cloudflare_dns_record.app_cname[each.key].id
    validation_record_id = cloudflare_dns_record.app_validation[each.key].id
  }

  provisioner "local-exec" {
    command = "${path.module}/../scripts/wait-for-custom-domain-dns.sh"
    environment = {
      DNS_NAME      = each.value.hostname
      DNS_TARGET    = each.value.app_fqdn
      DNS_VERIFY_ID = each.value.verify_id
    }
  }
}

resource "azurerm_container_app_custom_domain" "app" {
  for_each = local.custom_domains

  name             = each.value.hostname
  container_app_id = each.value.app_id

  lifecycle {
    ignore_changes = [
      certificate_binding_type,
      container_app_environment_certificate_id,
    ]
  }

  depends_on = [terraform_data.dns_propagation]
}

resource "azurerm_container_app_environment_managed_certificate" "app" {
  for_each = local.custom_domains

  name                         = "${each.key}-managed-cert"
  container_app_environment_id = azurerm_container_app_environment.demo.id
  subject_name                 = each.value.hostname
  domain_control_validation    = "CNAME"
  tags                         = local.tags

  timeouts {
    create = "30m"
    read   = "5m"
    update = "30m"
    delete = "30m"
  }

  depends_on = [azurerm_container_app_custom_domain.app]
}

resource "azapi_resource_action" "custom_domain_binding" {
  for_each = local.custom_domains

  type        = "Microsoft.App/containerApps@2025-07-01"
  resource_id = each.value.app_id
  method      = "PATCH"

  body = {
    properties = {
      configuration = {
        ingress = {
          customDomains = [
            {
              name          = each.value.hostname
              certificateId = azurerm_container_app_environment_managed_certificate.app[each.key].id
              bindingType   = "SniEnabled"
            }
          ]
        }
      }
    }
  }

  response_export_values = ["properties.configuration.ingress.customDomains"]

}
