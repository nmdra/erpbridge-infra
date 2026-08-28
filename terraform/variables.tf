variable "subscription_id" {
  description = "Azure subscription that contains the existing ERPBridge resource group."
  type        = string
}

variable "tenant_id" {
  description = "Microsoft Entra tenant ID used by the AzureRM provider."
  type        = string
}

variable "resource_group_name" {
  description = "Existing resource group. Terraform does not create or delete it."
  type        = string
  default     = "erpbridge-hq"
}

variable "deployment_location" {
  description = "Azure region for new resources. The subscription policy allows this region."
  type        = string
  default     = "centralindia"
  validation {
    condition = contains([
      "centralindia",
      "indiasouthcentral",
      "koreacentral",
      "malaysiawest",
      "uaenorth",
    ], lower(var.deployment_location))
    error_message = "deployment_location must be an allowed Container Apps region."
  }
}

variable "log_analytics_workspace_name" {
  description = "Name of the Log Analytics workspace."
  type        = string
  default     = "erpbridge-demo-logs"
}

variable "container_app_environment_name" {
  description = "Name of the Container Apps environment."
  type        = string
  default     = "erpbridge-demo-aca"
}

variable "log_retention_days" {
  description = "Log Analytics retention in days."
  type        = number
  default     = 30
  validation {
    condition     = var.log_retention_days >= 30 && var.log_retention_days <= 730
    error_message = "log_retention_days must be between 30 and 730."
  }
}

variable "erpbridge_app_name" {
  description = "ERPBridge Container App name."
  type        = string
  default     = "erpbridge-server"
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{0,31}$", var.erpbridge_app_name))
    error_message = "erpbridge_app_name must use lowercase letters, numbers, and hyphens."
  }
}

variable "mockerp_app_name" {
  description = "MockERP Container App name."
  type        = string
  default     = "mockerp"
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{0,31}$", var.mockerp_app_name))
    error_message = "mockerp_app_name must use lowercase letters, numbers, and hyphens."
  }
}

variable "erpbridge_image" {
  description = "Immutable ERPBridge image reference, including a sha256 digest."
  type        = string
  validation {
    condition     = can(regex("^[^@[:space:]]+@sha256:[0-9a-f]{64}$", var.erpbridge_image))
    error_message = "erpbridge_image must be an image@sha256:<64 lowercase hex> reference."
  }
}

variable "mockerp_image" {
  description = "Immutable MockERP 0.2.1 image reference, including a sha256 digest."
  type        = string
  validation {
    condition     = can(regex("^[^@[:space:]]+@sha256:[0-9a-f]{64}$", var.mockerp_image))
    error_message = "mockerp_image must be an image@sha256:<64 lowercase hex> reference."
  }
}

variable "erpbridge_mcp_port" {
  description = "ERPBridge HTTP port used by the container, ingress, and probes."
  type        = number
  default     = 8080
  validation {
    condition     = var.erpbridge_mcp_port >= 1 && var.erpbridge_mcp_port <= 65535
    error_message = "erpbridge_mcp_port must be a valid TCP port."
  }
}

variable "erpbridge_mcp_transport" {
  description = "ERPBridge transport mode for the public application."
  type        = string
  default     = "http"
  validation {
    condition     = lower(trimspace(var.erpbridge_mcp_transport)) != "stdio" && trimspace(var.erpbridge_mcp_transport) != ""
    error_message = "The public application must use a non-empty non-stdio transport."
  }
}

variable "erpbridge_base_url" {
  description = "Optional explicit BASE_URL. Null omits BASE_URL."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition     = var.erpbridge_base_url == null || can(regex("^https://[^[:space:]]+$", var.erpbridge_base_url))
    error_message = "erpbridge_base_url must be null or an HTTPS URL."
  }
}

variable "erpbridge_erp_base_url" {
  description = "Optional upstream ERP URL. Null omits ERP_BASE_URL."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition     = var.erpbridge_erp_base_url == null || can(regex("^https://[^[:space:]]+$", var.erpbridge_erp_base_url))
    error_message = "erpbridge_erp_base_url must be null or an HTTPS URL."
  }
}

variable "api_auth_admin_roles" {
  description = "Comma-separated roles assigned to the API authentication token."
  type        = string
  default     = "platform_admin"
}

variable "erpbridge_app_env" {
  description = "ERPBridge application environment."
  type        = string
  default     = "production"
}

variable "erpbridge_log_level" {
  description = "ERPBridge log level."
  type        = string
  default     = "info"
}

variable "erpbridge_log_to_stderr" {
  description = "Send ERPBridge logs to stderr."
  type        = bool
  default     = true
}

variable "cache_memory_max_entries" {
  description = "Bounded in-memory cache size when Redis is absent."
  type        = number
  default     = 10000
  validation {
    condition     = var.cache_memory_max_entries >= 0
    error_message = "cache_memory_max_entries must be zero or greater."
  }
}

variable "rate_limit_rps" {
  description = "ERPBridge per-session request rate."
  type        = number
  default     = 5
  validation {
    condition     = var.rate_limit_rps > 0
    error_message = "rate_limit_rps must be greater than zero."
  }
}

variable "rate_limit_burst" {
  description = "ERPBridge token-bucket burst."
  type        = number
  default     = 10
  validation {
    condition     = var.rate_limit_burst > 0
    error_message = "rate_limit_burst must be greater than zero."
  }
}

variable "cors_allowed_origins" {
  description = "Comma-separated browser origins allowed for MCP CORS."
  type        = string
  default     = ""
}

variable "mockerp_allow_all_ingress" {
  description = "Explicit demo-only opt-in for unrestricted MockERP ingress."
  type        = bool
  default     = false
}

variable "mockerp_allowed_cidrs" {
  description = "IPv4 CIDR allow-list for external Postman clients. 0.0.0.0/0 requires mockerp_allow_all_ingress=true."
  type        = set(string)
  validation {
    condition = (
      length(var.mockerp_allowed_cidrs) > 0 &&
      alltrue([
        for cidr in var.mockerp_allowed_cidrs :
        can(regex("^[0-9]{1,3}(\\.[0-9]{1,3}){3}/[0-9]{1,2}$", cidr)) &&
        can(cidrhost(cidr, 0))
      ])
    )
    error_message = "mockerp_allowed_cidrs must be a non-empty set of valid IPv4 CIDRs."
  }

  validation {
    condition = (
      !contains(var.mockerp_allowed_cidrs, "0.0.0.0/0") ||
      var.mockerp_allow_all_ingress
    )
    error_message = "0.0.0.0/0 requires the explicit demo-only mockerp_allow_all_ingress=true opt-in."
  }
}

variable "custom_domains" {
  description = "Cloudflare custom-domain configuration. Null disables custom domains."
  type = object({
    zone_id   = string
    erpbridge = string
    mockerp   = string
  })
  default  = null
  nullable = true

  validation {
    condition = (
      var.custom_domains == null || (
        can(regex("^[0-9a-f]{32}$", lower(trimspace(var.custom_domains.zone_id)))) &&
        length(trimspace(var.custom_domains.erpbridge)) <= 253 &&
        can(regex("^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$", trimspace(var.custom_domains.erpbridge))) &&
        length(trimspace(var.custom_domains.mockerp)) <= 253 &&
        can(regex("^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$", trimspace(var.custom_domains.mockerp))) &&
        lower(trimspace(var.custom_domains.erpbridge)) != lower(trimspace(var.custom_domains.mockerp))
      )
    )
    error_message = "custom_domains must contain a valid zone ID and two distinct lowercase fully qualified hostnames."
  }
}

variable "mockerp_aca_internal_cidrs" {
  description = "Observed Azure Container Apps source CIDRs allowed to reach MockERP; this is environment-specific, not a universal Azure range."
  type        = set(string)
  default     = ["100.100.0.0/16"]
  validation {
    condition = alltrue([
      for cidr in var.mockerp_aca_internal_cidrs :
      can(regex("^[0-9]{1,3}(\\.[0-9]{1,3}){3}/[0-9]{1,2}$", cidr)) &&
      can(cidrhost(cidr, 0)) && cidr != "0.0.0.0/0"
    ])
    error_message = "mockerp_aca_internal_cidrs must contain valid IPv4 CIDRs without 0.0.0.0/0."
  }
}

variable "api_auth_token" {
  description = "ERPBridge bearer token. Stored in state for this short-lived demo."
  type        = string
  sensitive   = true
  validation {
    condition     = trimspace(var.api_auth_token) != ""
    error_message = "api_auth_token must not be empty."
  }
}

variable "mockerp_credentials" {
  description = "MockERP API credentials."
  type = object({
    credentials = list(object({
      api_key    = string
      api_secret = string
      role       = string
      identity   = string
    }))
  })
  sensitive = true

  validation {
    condition = (
      length(var.mockerp_credentials.credentials) > 0 &&
      alltrue([
        for credential in var.mockerp_credentials.credentials :
        trimspace(credential.api_key) != "" &&
        trimspace(credential.api_secret) != "" &&
        trimspace(credential.role) != "" &&
        trimspace(credential.identity) != ""
      ])
    )
    error_message = "At least one complete MockERP credential is required."
  }
}

variable "tags" {
  description = "Additional Azure resource tags."
  type        = map(string)
  default     = {}
}
