variable "subscription_id" {
  description = "Azure subscription that contains the existing ERPBridge resource group."
  type        = string
  sensitive   = true
}

variable "tenant_id" {
  description = "Microsoft Entra tenant ID used by the AzureRM provider."
  type        = string
  sensitive   = true
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

variable "mockerp_container_port" {
  description = "MockERP fixed listener port."
  type        = number
  default     = 8081
  validation {
    condition     = var.mockerp_container_port == 8081
    error_message = "MockERP listens on fixed port 8081."
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

variable "erpbridge_database_path" {
  description = "ERPBridge SQLite registry path in the ephemeral volume."
  type        = string
  default     = "/data/erpbridge.db"
  validation {
    condition     = var.erpbridge_database_path == "/data/erpbridge.db"
    error_message = "ERPBridge must use /data/erpbridge.db in the ephemeral volume."
  }
}

variable "api_auth_admin_roles" {
  description = "Comma-separated roles assigned to the API authentication token."
  type        = string
  default     = "platform_admin"
}

variable "mcp_enable_test_tools" {
  description = "Development-only test tools switch."
  type        = bool
  default     = false
  validation {
    condition     = var.mcp_enable_test_tools == false
    error_message = "Test tools must remain disabled in the public demo."
  }
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

variable "mockerp_database_path" {
  description = "MockERP SQLite database path."
  type        = string
  default     = "/data/mockerp.db"
  validation {
    condition     = var.mockerp_database_path == "/data/mockerp.db"
    error_message = "MockERP must use /data/mockerp.db in the ephemeral volume."
  }
}

variable "mockerp_app_env" {
  description = "MockERP environment."
  type        = string
  default     = "production"
}

variable "mockerp_allow_reset" {
  description = "MockERP reset switch."
  type        = bool
  default     = false
  validation {
    condition     = var.mockerp_allow_reset == false
    error_message = "MockERP reset must remain disabled."
  }
}

variable "mockerp_external_enabled" {
  description = "Expose MockERP for local Postman."
  type        = bool
  default     = true
  validation {
    condition     = var.mockerp_external_enabled
    error_message = "The demo requires external MockERP ingress."
  }
}

variable "mockerp_https_only" {
  description = "Require HTTPS ingress for MockERP."
  type        = bool
  default     = true
  validation {
    condition     = var.mockerp_https_only
    error_message = "MockERP must reject insecure HTTP connections."
  }
}

variable "mockerp_allowed_cidrs" {
  description = "Required IPv4 CIDR allow-list for external Postman clients."
  type        = list(string)
  validation {
    condition = (
      length(var.mockerp_allowed_cidrs) > 0 &&
      length(distinct(var.mockerp_allowed_cidrs)) == length(var.mockerp_allowed_cidrs) &&
      alltrue([
        for cidr in var.mockerp_allowed_cidrs :
        can(regex("^[0-9]{1,3}(\\.[0-9]{1,3}){3}/[0-9]{1,2}$", cidr)) &&
        can(cidrhost(cidr, 0)) && cidr != "0.0.0.0/0"
      ])
    )
    error_message = "mockerp_allowed_cidrs must be a non-empty unique IPv4 CIDR list without 0.0.0.0/0."
  }
}

variable "mockerp_aca_internal_cidrs" {
  description = "Azure Container Apps internal source CIDRs allowed to reach MockERP."
  type        = list(string)
  default     = ["100.100.0.0/16"]
  validation {
    condition = (
      length(distinct(var.mockerp_aca_internal_cidrs)) == length(var.mockerp_aca_internal_cidrs) &&
      alltrue([
        for cidr in var.mockerp_aca_internal_cidrs :
        can(regex("^[0-9]{1,3}(\\.[0-9]{1,3}){3}/[0-9]{1,2}$", cidr)) &&
        can(cidrhost(cidr, 0)) && cidr != "0.0.0.0/0"
      ])
    )
    error_message = "mockerp_aca_internal_cidrs must be unique valid IPv4 CIDRs without 0.0.0.0/0."
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

variable "erp_primary_key" {
  description = "ERPNext credential used by ERPBridge for upstream calls."
  type        = string
  sensitive   = true
  validation {
    condition     = trimspace(var.erp_primary_key) != ""
    error_message = "erp_primary_key must not be empty."
  }
}

variable "mockerp_credentials_json" {
  description = "MockERP credential configuration JSON."
  type        = string
  sensitive   = true
  validation {
    condition     = trimspace(var.mockerp_credentials_json) != ""
    error_message = "mockerp_credentials_json must not be empty."
  }
}

variable "tags" {
  description = "Additional Azure resource tags."
  type        = map(string)
  default     = {}
}
