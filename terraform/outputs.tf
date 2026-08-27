output "deployment_summary" {
  description = "Essential non-sensitive deployment details."
  value = {
    resource_group = data.azurerm_resource_group.existing.name
    location       = local.location
    redis_enabled  = false

    erpbridge = {
      name       = azurerm_container_app.erpbridge.name
      url        = local.erpbridge_url
      mcp_url    = "${local.erpbridge_url}/mcp/"
      port       = var.erpbridge_mcp_port
      replicas   = 1
      https_only = true
    }

    mockerp = {
      name       = azurerm_container_app.mockerp.name
      url        = local.mockerp_url
      port       = var.mockerp_container_port
      replicas   = 1
      https_only = var.mockerp_https_only
    }
  }
}

output "erpbridge_url" {
  description = "Public HTTPS ERPBridge base URL."
  value       = local.erpbridge_url
}

output "erpbridge_mcp_url" {
  description = "Public HTTPS ERPBridge MCP endpoint URL."
  value       = "${local.erpbridge_url}/mcp/"
}

output "mockerp_url" {
  description = "Public HTTPS MockERP URL for local Postman."
  value       = local.mockerp_url
}

output "resource_group_name" {
  description = "Existing resource group used by the deployment."
  value       = data.azurerm_resource_group.existing.name
}

output "container_app_environment_name" {
  description = "Container Apps environment name."
  value       = azurerm_container_app_environment.demo.name
}
