# The bridge record keeps this exact Terraform address in both hosting modes.
# Only its type and content change: a CNAME to the Container App, or an A record
# to the dedicated VM. Changing an existing record's type is not an atomic
# mutation, so review the plan and expect a controlled replacement.
resource "cloudflare_dns_record" "app_cname" {
  for_each = local.custom_domains

  zone_id = var.custom_domains.zone_id
  name    = each.value.hostname
  type    = each.value.record_type
  content = each.value.content
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

# The CNAME propagation check only applies while the bridge record points at the
# Container App. In VM mode the record is an A record instead, so the check is
# disabled with a harmless no-op command rather than removing the resource. That
# keeps the instance in state across the cutover, so the transition plans no
# destroys, and rollback simply re-enables the check.
resource "terraform_data" "dns_propagation" {
  for_each = local.custom_domains

  input = {
    cname_record_id      = cloudflare_dns_record.app_cname[each.key].id
    validation_record_id = cloudflare_dns_record.app_validation[each.key].id
  }

  # Record IDs can remain stable during an update. Recheck actual DNS inputs.
  triggers_replace = {
    zone_id   = var.custom_domains.zone_id
    hostname  = each.value.hostname
    target    = each.value.app_fqdn
    verify_id = each.value.verify_id
  }

  provisioner "local-exec" {
    command = each.value.verify ? "${path.module}/../scripts/wait-for-custom-domain-dns.sh" : "true"
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

  name                         = "${each.key}-${substr(sha256(each.value.hostname), 0, 12)}-managed-cert"
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
  when        = "apply"

  # Keep the existing action's address and resource type. Replacement repeats
  # the PATCH after a domain/certificate change; apply-only actions do nothing
  # on destroy. No Container App state migration is required.
  lifecycle {
    replace_triggered_by = [
      azurerm_container_app_custom_domain.app[each.key],
      azurerm_container_app_environment_managed_certificate.app[each.key],
      terraform_data.dns_propagation[each.key],
    ]
  }

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
