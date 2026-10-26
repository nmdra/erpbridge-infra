output "deployment_summary" {
  description = "Essential non-sensitive deployment details."
  value = {
    resource_group = data.azurerm_resource_group.existing.name
    location       = local.location
    erp_target     = var.erp_target
    erpnext        = var.erpnext_url == null ? null : { url = var.erpnext_url }

    erpbridge = {
      name            = azurerm_container_app.erpbridge.name
      url             = local.erpbridge_url
      azure_url       = local.erpbridge_azure_url
      mcp_url         = "${local.erpbridge_url}/mcp/"
      port            = var.erpbridge_mcp_port
      hosting         = var.erpbridge_hosting
      source_fenced   = var.erpbridge_source_fenced
      min_replicas    = var.erpbridge_min_replicas
      dns_record_type = local.erpbridge_dns_type
    }

    mockerp = {
      name                  = azurerm_container_app.mockerp.name
      url                   = local.mockerp_url
      azure_url             = local.mockerp_azure_url
      port                  = local.mockerp_port
      ingress_allowed_cidrs = local.mockerp_ingress_allowed_cidrs
    }

    custom_domains_enabled = var.custom_domains != null
  }
}

output "erp_target" {
  description = "Selected upstream ERP target."
  value       = var.erp_target
}

output "erpnext_url" {
  description = "Configured ERPNext HTTPS origin, or null when not configured."
  value       = var.erpnext_url
}

output "erpbridge_url" {
  description = "Canonical public HTTPS ERPBridge base URL."
  value       = local.erpbridge_url
}

output "erpbridge_mcp_url" {
  description = "Canonical public HTTPS ERPBridge MCP endpoint URL."
  value       = "${local.erpbridge_url}/mcp/"
}

output "mockerp_url" {
  description = "Canonical public HTTPS MockERP URL for local Postman."
  value       = local.mockerp_url
}

output "erpbridge_azure_url" {
  description = "Azure-generated ERPBridge URL for diagnostics and fallback."
  value       = local.erpbridge_azure_url
}

output "mockerp_azure_url" {
  description = "Azure-generated MockERP URL for diagnostics and fallback."
  value       = local.mockerp_azure_url
}

output "erpbridge_hosting" {
  description = "ERPBridge hosting ownership, the fence state, and the bridge DNS record shape."
  value = {
    hosting             = var.erpbridge_hosting
    source_fenced       = var.erpbridge_source_fenced
    canonical_hostname  = var.custom_domains == null ? null : trimspace(lower(var.custom_domains.erpbridge))
    dns_record_type     = local.erpbridge_dns_type
    dns_record_content  = var.custom_domains == null ? null : local.erpbridge_dns_content
    cname_check_applies = local.erpbridge_verify_cname
  }
}

output "cloudflare_domains" {
  description = "Configured custom domains and whether Cloudflare DNS is enabled."
  value = {
    enabled   = var.custom_domains != null
    erpbridge = try(var.custom_domains.erpbridge, null)
    mockerp   = try(var.custom_domains.mockerp, null)
  }
}

output "image_references" {
  description = "Immutable container image references used by the deployment."
  value = {
    erpbridge = var.erpbridge_image
    mockerp   = var.mockerp_image
  }
}

output "mockerp_ingress_sources" {
  description = "Effective MockERP ingress CIDRs and their intended source types."
  value       = local.mockerp_ingress_sources
}

output "demo_tokens" {
  description = "DEMO ONLY: bridge and upstream credentials for the protected export artifact."
  value = {
    api_auth_token  = var.api_auth_token
    erp_primary_key = local.erp_primary_key
  }
  sensitive = true
}

output "resource_group_name" {
  description = "Existing resource group used by the deployment."
  value       = data.azurerm_resource_group.existing.name
}

output "container_app_environment_name" {
  description = "Container Apps environment name."
  value       = azurerm_container_app_environment.demo.name
}
