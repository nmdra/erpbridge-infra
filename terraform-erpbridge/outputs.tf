# Non-secret connection, disk, and backup metadata only. No credential, token,
# password, private key, or connection string may be added here.

output "erpbridge_url" {
  description = "Canonical public HTTPS URL. Availability requires the separate runtime bootstrap."
  value       = "https://${local.hostname}"
}

output "vm_resource_id" {
  description = "Full subscription-qualified resource ID of the dedicated ERPBridge VM."
  value       = azurerm_linux_virtual_machine.erpbridge.id
}

output "vm_name" {
  description = "Name of the dedicated ERPBridge VM."
  value       = azurerm_linux_virtual_machine.erpbridge.name
}

output "public_ip_address" {
  description = "Static Standard public IPv4 address of the VM."
  value       = azurerm_public_ip.erpbridge.ip_address
}

output "public_ip_resource_id" {
  description = "Full resource ID of the static public IP address."
  value       = azurerm_public_ip.erpbridge.id
}

output "network_resource_ids" {
  description = "Full resource IDs of the dedicated network resources."
  value = {
    virtual_network_id        = azurerm_virtual_network.erpbridge.id
    subnet_id                 = azurerm_subnet.erpbridge.id
    network_security_group_id = azurerm_network_security_group.erpbridge.id
    network_interface_id      = azurerm_network_interface.erpbridge.id
  }
}

output "ssh_metadata" {
  description = "Non-secret SSH connection metadata. The private key itself is never read by Terraform."
  value = {
    username          = var.ssh_username
    public_key_path   = var.ssh_public_key_path
    private_key_path  = var.ssh_private_key_path
    operator_ssh_cidr = var.operator_ssh_cidr
  }
}

output "os_disk" {
  description = "OS disk identifiers. The provider retains this disk if the VM is deleted."
  value = {
    name                  = azurerm_linux_virtual_machine.erpbridge.os_disk[0].name
    id                    = azurerm_linux_virtual_machine.erpbridge.os_disk[0].id
    storage_account_type  = azurerm_linux_virtual_machine.erpbridge.os_disk[0].storage_account_type
    disk_size_gb          = azurerm_linux_virtual_machine.erpbridge.os_disk[0].disk_size_gb
    retain_on_vm_deletion = true
  }
}

output "backup" {
  description = "Backup storage and retention identifiers."
  value = {
    storage_account_name = azurerm_storage_account.backup.name
    storage_account_id   = azurerm_storage_account.backup.id
    container_name       = azurerm_storage_container.backup.name
    container_id         = azurerm_storage_container.backup.id
    role_assignment_id   = azurerm_role_assignment.backup_writer.id
    lifecycle_policy_id  = azurerm_storage_management_policy.backup.id
    retention_days       = var.backup_retention_days
  }
}

output "deployment_summary" {
  description = "Non-secret ERPBridge VM connection metadata for the protected deployment exporter."
  value = {
    name                   = azurerm_linux_virtual_machine.erpbridge.name
    resource_id            = azurerm_linux_virtual_machine.erpbridge.id
    resource_group         = data.azurerm_resource_group.existing.name
    location               = azurerm_linux_virtual_machine.erpbridge.location
    url                    = "https://${local.hostname}"
    public_ip              = azurerm_public_ip.erpbridge.ip_address
    ssh_username           = var.ssh_username
    ssh_private_key_path   = var.ssh_private_key_path
    vm_size                = azurerm_linux_virtual_machine.erpbridge.size
    os_disk_name           = azurerm_linux_virtual_machine.erpbridge.os_disk[0].name
    os_disk_size_gb        = azurerm_linux_virtual_machine.erpbridge.os_disk[0].disk_size_gb
    backup_storage_account = azurerm_storage_account.backup.name
    backup_container       = azurerm_storage_container.backup.name
  }
}

output "monitoring" {
  description = "Non-secret monitoring IDs and commissioning state. No receiver or Application Insights credentials."
  value = var.monitoring_enabled ? {
    workspace_id               = azurerm_log_analytics_workspace.monitoring[0].id
    application_insights_id    = azurerm_application_insights.monitoring[0].id
    action_group_id            = azurerm_monitor_action_group.monitoring[0].id
    web_test_id                = azurerm_application_insights_standard_web_test.health[0].id
    web_test_url               = "${var.canonical_url}/mcp/health"
    web_test_frequency_seconds = 900
    web_test_locations         = azurerm_application_insights_standard_web_test.health[0].geo_locations
    table_id                   = azapi_resource.health_table[0].id
    data_collection_rule_id    = azurerm_monitor_data_collection_rule.health[0].id
    association_id             = azurerm_monitor_data_collection_rule_association.health[0].id
    guest_alert_id             = azurerm_monitor_scheduled_query_rules_alert_v2.guest[0].id
    guest_alert_enabled        = var.monitoring_guest_alert_enabled
    workbook_id                = azurerm_application_insights_workbook.monitoring[0].id
  } : null
}
