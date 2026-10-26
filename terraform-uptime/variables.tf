variable "subscription_id" {
  type      = string
  sensitive = true
}
variable "tenant_id" {
  type        = string
  sensitive   = true
  description = "Azure provider tenant identity only. Uptime Kuma uses its own login."
  validation {
    condition     = can(regex("^[0-9a-fA-F-]{36}$", var.tenant_id))
    error_message = "Supply the Azure provider tenant UUID."
  }
}
variable "resource_group_name" {
  type    = string
  default = "erpbridge-hq"
}
variable "container_app_environment_name" {
  type    = string
  default = "erpbridge-demo-aca-standard"
}
variable "publish" {
  type        = bool
  default     = false
  description = "Enable public HTTPS ingress using Uptime Kuma's own login, with no Entra or reset guard."
}
variable "accept_reset_setup_risk" {
  type        = bool
  default     = false
  description = "Explicitly accept that disposable SQLite loss erases all state and reopens public first-user setup, allowing anyone to claim administrator access. No reset guard is installed."
}
variable "enable_custom_domain" {
  type    = bool
  default = false
}
variable "cloudflare_zone_id" {
  type      = string
  default   = ""
  sensitive = true
}
