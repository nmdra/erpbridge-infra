# All provider calls use local mocks. Do not run a cloud plan for these contracts.
mock_provider "azurerm" {
  override_during = plan
  mock_resource "azurerm_container_app_environment" {
    defaults = {
      id             = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/erpbridge-hq/providers/Microsoft.App/managedEnvironments/erpbridge-demo-aca"
      default_domain = "fixture.azurecontainerapps.io"
    }
  }
  mock_resource "azurerm_container_app" {
    defaults = {
      id                            = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/erpbridge-hq/providers/Microsoft.App/containerApps/fixture-app"
      custom_domain_verification_id = "fixture-verification"
    }
  }
  mock_resource "azurerm_log_analytics_workspace" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/erpbridge-hq/providers/Microsoft.OperationalInsights/workspaces/erpbridge-demo-logs"
    }
  }
}
mock_provider "azapi" {}
mock_provider "cloudflare" {}

variables {
  subscription_id       = "00000000-0000-0000-0000-000000000000"
  tenant_id             = "00000000-0000-0000-0000-000000000000"
  erpbridge_image       = "example.invalid/bridge@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  mockerp_image         = "example.invalid/mock@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  api_auth_token        = "fixture-only-bridge-token"
  mockerp_allowed_cidrs = ["203.0.113.10/32"]
  mockerp_credentials = {
    credentials = [{ api_key = "fixture-key", api_secret = "fixture-secret", role = "admin", identity = "fixture" }]
  }
}

run "mockerp_defaults" {
  command = plan
  assert {
    condition     = output.erp_target == "mockerp" && output.deployment_summary.erpnext == null
    error_message = "MockERP must remain the default with no ERPNext metadata."
  }
  assert {
    condition     = local.erp_primary_key == "token fixture-key:fixture-secret" && local.erpbridge_configured_erp_base_url == "https://mockerp.fixture.azurecontainerapps.io"
    error_message = "The default credential and URL must both select MockERP."
  }
  assert {
    condition     = length(local.erpbridge_secret_env) == 2 && length(azurerm_container_app.erpbridge.secret) == 2
    error_message = "Unset optional service keys must not create secrets or environment entries."
  }
}

run "erpnext_selected" {
  command = plan
  variables {
    erp_target        = "erpnext"
    erpnext_url       = "https://erpnext.nimendra.online"
    erpnext_api_token = "token fixture-next-key:fixture-next-secret"
  }
  assert {
    condition     = local.erp_primary_key == var.erpnext_api_token && local.erpbridge_configured_erp_base_url == var.erpnext_url
    error_message = "ERPNext must select its own URL and credential."
  }
  assert {
    condition     = output.deployment_summary.erp_target == "erpnext" && output.deployment_summary.erpnext.url == var.erpnext_url && output.erpnext_url == var.erpnext_url
    error_message = "ERPNext output metadata must identify the configured target."
  }
}

run "canonical_domains" {
  command = plan
  variables {
    custom_domains = {
      zone_id   = "c68ea0eb14e403a5454c04b6efecdcf2"
      erpbridge = "erpbridge.nimendra.online"
      mockerp   = "mockerp.nimendra.online"
    }
  }
  assert {
    condition = (
      output.erpbridge_url == "https://erpbridge.nimendra.online" &&
      output.erpbridge_mcp_url == "https://erpbridge.nimendra.online/mcp/" &&
      output.mockerp_url == "https://mockerp.nimendra.online" &&
      local.erpbridge_configured_erp_base_url == output.mockerp_url &&
      alltrue([for key, record in cloudflare_dns_record.app_cname :
        record.proxied == false && record.type == "CNAME" &&
        record.zone_id == var.custom_domains.zone_id &&
        terraform_data.dns_propagation[key].triggers_replace.hostname == record.name &&
        terraform_data.dns_propagation[key].triggers_replace.target == record.content &&
        terraform_data.dns_propagation[key].triggers_replace.zone_id == record.zone_id &&
        terraform_data.dns_propagation[key].triggers_replace.verify_id == cloudflare_dns_record.app_validation[key].content &&
        cloudflare_dns_record.app_validation[key].name == "asuid.${record.name}" &&
        cloudflare_dns_record.app_validation[key].type == "TXT" &&
        cloudflare_dns_record.app_validation[key].proxied == false &&
        azurerm_container_app_environment_managed_certificate.app[key].name == "${key}-${substr(sha256(record.name), 0, 12)}-managed-cert"
      ])
    )
    error_message = "Canonical domains must keep DNS-only validation and hostname-specific certificate and DNS wait inputs."
  }
}

run "staged_source_fence_only" {
  command = plan
  variables {
    custom_domains = {
      zone_id   = "c68ea0eb14e403a5454c04b6efecdcf2"
      erpbridge = "erpbridge.nimendra.online"
      mockerp   = "mockerp.nimendra.online"
    }
    erpbridge_source_fenced = true
  }
  assert {
    condition = (
      azurerm_container_app.erpbridge.ingress[0].external_enabled == false &&
      output.deployment_summary.erpbridge.hosting == "aca" &&
      output.deployment_summary.erpbridge.source_fenced == true
    )
    error_message = "The staged fence must close the bridge ingress."
  }
  assert {
    # Fencing is an application-scope change, so nothing else may move with it.
    # These locals carry the DNS intent, and a static contract test asserts the
    # record resource still uses them unchanged.
    condition = (
      length(local.custom_domains) == 2 &&
      length(local.custom_domains_verified) == 2 &&
      local.custom_domains.erpbridge.record_type == "CNAME" &&
      local.custom_domains.erpbridge.content == local.custom_domains.erpbridge.app_fqdn &&
      local.custom_domains.mockerp.record_type == "CNAME"
    )
    error_message = "Fencing must not change any DNS record."
  }
}

run "vm_hosting_retains_fence_and_moves_one_record" {
  command = plan
  variables {
    custom_domains = {
      zone_id   = "c68ea0eb14e403a5454c04b6efecdcf2"
      erpbridge = "erpbridge.nimendra.online"
      mockerp   = "mockerp.nimendra.online"
    }
    erpbridge_hosting       = "vm"
    erpbridge_vm_ipv4       = "203.0.113.50"
    erpbridge_source_fenced = true
  }
  assert {
    condition = (
      cloudflare_dns_record.app_cname["erpbridge"].type == "A" &&
      cloudflare_dns_record.app_cname["erpbridge"].content == "203.0.113.50" &&
      cloudflare_dns_record.app_cname["erpbridge"].proxied == false
    )
    error_message = "VM hosting must move the bridge record to an A record."
  }
  assert {
    condition = (
      # Only the bridge loses its CNAME check.
      contains(keys(local.custom_domains_verified), "mockerp") &&
      !contains(keys(local.custom_domains_verified), "erpbridge") &&
      # Both domains keep their binding, TXT record, and certificate entries.
      length(local.custom_domains) == 2 &&
      # MockERP keeps its type and target unchanged.
      local.custom_domains.mockerp.record_type == "CNAME" &&
      local.custom_domains.mockerp.content == local.custom_domains.mockerp.app_fqdn &&
      local.custom_domains.erpbridge.app_id == azurerm_container_app.erpbridge.id
    )
    error_message = "VM hosting must skip the bridge CNAME check while keeping its binding and certificate."
  }
  assert {
    condition = (
      output.erpbridge_hosting.dns_record_type == "A" &&
      output.erpbridge_hosting.dns_record_content == "203.0.113.50" &&
      output.erpbridge_hosting.cname_check_applies == false &&
      output.deployment_summary.erpbridge.source_fenced == true &&
      output.deployment_summary.erpbridge.hosting == "vm" &&
      # The canonical URL is unchanged: clients keep the same address.
      output.erpbridge_url == "https://erpbridge.nimendra.online"
    )
    error_message = "VM hosting must keep the fence and the canonical URL."
  }
}

run "vm_hosting_requires_the_fence" {
  command = plan
  variables {
    erpbridge_hosting = "vm"
    erpbridge_vm_ipv4 = "203.0.113.50"
  }
  expect_failures = [var.erpbridge_source_fenced]
}
run "vm_hosting_requires_an_address" {
  command = plan
  variables {
    erpbridge_hosting       = "vm"
    erpbridge_source_fenced = true
  }
  expect_failures = [var.erpbridge_vm_ipv4]
}
run "vm_hosting_rejects_a_bad_address" {
  command = plan
  variables {
    erpbridge_hosting       = "vm"
    erpbridge_vm_ipv4       = "203.0.113.999"
    erpbridge_source_fenced = true
  }
  expect_failures = [var.erpbridge_vm_ipv4]
}
run "unknown_hosting_mode" {
  command = plan
  variables { erpbridge_hosting = "vm2" }
  expect_failures = [var.erpbridge_hosting]
}

run "optional_keys_preserved" {
  command = plan
  variables {
    policy_gate_api_key      = "fixture-policy"
    external_service_api_key = "fixture-data"
    resend_api_key           = "fixture-resend"
  }
  assert {
    condition     = length(local.erpbridge_secret_env) == 5 && length(azurerm_container_app.erpbridge.secret) == 5
    error_message = "Configured optional integration keys must remain available."
  }
}

run "invalid_target" {
  command = plan
  variables { erp_target = "other" }
  expect_failures = [var.erp_target]
}
run "missing_erpnext_url" {
  command = plan
  variables {
    erp_target        = "erpnext"
    erpnext_api_token = "token fixture:secret"
  }
  expect_failures = [var.erpnext_url]
}
run "missing_erpnext_token" {
  command = plan
  variables {
    erp_target  = "erpnext"
    erpnext_url = "https://erpnext.nimendra.online"
  }
  expect_failures = [var.erpnext_api_token]
}
run "invalid_erpnext_origin" {
  command = plan
  variables { erpnext_url = "https://user:password@erpnext.nimendra.online/api" }
  expect_failures = [var.erpnext_url]
}
run "invalid_token_header" {
  command = plan
  variables { erpnext_api_token = "token key:" }
  expect_failures = [var.erpnext_api_token]
}
run "conflicting_override" {
  command = plan
  variables {
    erp_target             = "erpnext"
    erpnext_url            = "https://erpnext.nimendra.online"
    erpnext_api_token      = "token fixture:secret"
    erpbridge_erp_base_url = "https://mockerp.nimendra.online"
  }
  expect_failures = [azurerm_container_app.erpbridge]
}
run "matching_override" {
  command = plan
  variables {
    erp_target             = "erpnext"
    erpnext_url            = "https://erpnext.nimendra.online"
    erpnext_api_token      = "token fixture:secret"
    erpbridge_erp_base_url = "https://erpnext.nimendra.online"
  }
}
