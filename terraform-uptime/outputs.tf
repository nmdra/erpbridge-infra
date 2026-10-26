output "container_app_id" {
  value = azurerm_container_app.uptime.id
}
output "azure_default_fqdn" {
  description = "Azure ingress hostname. Public when publish=true; Uptime Kuma provides its own login."
  value       = azurerm_container_app.uptime.ingress[0].fqdn
}
output "public_url" {
  value = var.enable_custom_domain ? "https://${local.hostname}" : null
}
output "reset_risk" {
  value = "Disposable EmptyDir SQLite: restart, reschedule or revision replacement may erase ALL state. Database loss reopens public first-user setup when published, allowing anyone to claim the administrator account. No Entra or reset guard is installed. Rebuild manually."
}
