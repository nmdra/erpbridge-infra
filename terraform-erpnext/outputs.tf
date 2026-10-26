output "erpnext_url" {
  description = "Canonical ERPNext HTTPS URL; availability requires separate app bootstrap."
  value       = "https://${local.hostname}"
}

output "deployment_summary" {
  description = "Non-secret ERPNext connection metadata for the protected deployment exporter."
  value = {
    name                 = azurerm_linux_virtual_machine.erpnext.name
    resource_id          = azurerm_linux_virtual_machine.erpnext.id
    resource_group       = data.azurerm_resource_group.existing.name
    location             = azurerm_linux_virtual_machine.erpnext.location
    url                  = "https://${local.hostname}"
    public_ip            = azurerm_public_ip.erpnext.ip_address
    ssh_username         = var.ssh_username
    ssh_private_key_path = var.ssh_private_key_path
    vm_size              = azurerm_linux_virtual_machine.erpnext.size
  }
}
