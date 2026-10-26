variable "subscription_id" {
  description = "Existing Azure subscription that holds the ERPBridge resource group. No subscription changes are made."
  type        = string

  validation {
    condition     = trimspace(var.subscription_id) != ""
    error_message = "subscription_id must not be empty."
  }
}

variable "tenant_id" {
  description = "Microsoft Entra tenant ID used by the operator's existing authentication."
  type        = string

  validation {
    condition     = trimspace(var.tenant_id) != ""
    error_message = "tenant_id must not be empty."
  }
}

variable "resource_group_name" {
  description = "Existing ERPBridge resource group. It is looked up through a data source only and is never created or deleted here."
  type        = string
  default     = "erpbridge-hq"

  validation {
    condition     = can(regex("^[A-Za-z0-9._()-]{1,90}$", var.resource_group_name))
    error_message = "resource_group_name must be a non-empty existing Azure resource group name."
  }
}

variable "deployment_location" {
  description = "Azure region for the dedicated ERPBridge VM and its networking. Check regional quota and cost before applying."
  type        = string
  default     = "centralindia"

  validation {
    condition     = can(regex("^[a-z][a-z0-9]+$", var.deployment_location))
    error_message = "deployment_location must be an Azure region name such as centralindia."
  }
}

variable "vm_name" {
  description = "Name of the dedicated ERPBridge VM and the network resource prefix."
  type        = string
  default     = "erpbridge-vm"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,56}[a-z0-9]$", var.vm_name))
    error_message = "vm_name must use lowercase letters, numbers, and hyphens, and must start with a letter."
  }
}

variable "vm_size" {
  description = "Approved VM size. Standard_B2ats_v2 is the selected size; the larger candidate needs a separate priced decision."
  type        = string
  default     = "Standard_B2ats_v2"

  validation {
    condition     = contains(["Standard_B2ats_v2", "Standard_B2als_v2"], var.vm_size)
    error_message = "vm_size must be an approved size. Do not change it without a separate priced approval."
  }
}

variable "os_disk_name" {
  description = "Explicit name of the managed OS disk, so a retained disk stays identifiable in the resource group."
  type        = string
  default     = "erpbridge-vm-osdisk"

  validation {
    condition     = can(regex("^[A-Za-z0-9_-]{1,80}$", var.os_disk_name))
    error_message = "os_disk_name must use letters, numbers, underscores, or hyphens."
  }
}

variable "os_disk_size_gb" {
  description = "OS disk size. It must not be smaller than the image's OS disk size, and the selected size must stay at or above the reviewed 32 GiB."
  type        = number
  default     = 32

  validation {
    condition     = var.os_disk_size_gb >= 32 && var.os_disk_size_gb <= 1024
    error_message = "os_disk_size_gb must be at least 32 GiB and at most 1024 GiB."
  }
}

variable "ssh_username" {
  description = "SSH administrator username. Password authentication is disabled."
  type        = string
  default     = "erpbridge"

  validation {
    condition = (
      can(regex("^[a-z][a-z0-9-]{0,31}$", var.ssh_username)) &&
      !contains(["root", "admin", "administrator", "guest"], lower(var.ssh_username))
    )
    error_message = "ssh_username must be a lowercase Linux user name and must not be a reserved administrator name."
  }
}

variable "ssh_public_key_path" {
  description = "Path to an existing operator-owned SSH public key. No keys are generated."
  type        = string
  nullable    = false

  validation {
    condition     = can(file(pathexpand(var.ssh_public_key_path)))
    error_message = "ssh_public_key_path must name an existing readable public-key file."
  }
}

variable "ssh_private_key_path" {
  description = "Local private-key path for connection metadata only; Terraform never reads this file."
  type        = string
  nullable    = false

  validation {
    condition     = length(trimspace(var.ssh_private_key_path)) > 0
    error_message = "Provide the operator's private-key path as connection metadata."
  }
}

variable "operator_ssh_cidr" {
  description = "Explicit operator IPv4 /32 address allowed to connect on SSH."
  type        = string
  nullable    = false

  validation {
    condition = can(cidrnetmask(var.operator_ssh_cidr)) && can(regex(
      "^([0-9]{1,3}\\.){3}[0-9]{1,3}/32$",
      var.operator_ssh_cidr,
    )) && !contains(["0.0.0.0/32", "255.255.255.255/32"], var.operator_ssh_cidr)
    error_message = "operator_ssh_cidr must be one explicit IPv4 /32, not a subnet, IPv6 address, or unspecified address."
  }
}

variable "canonical_url" {
  description = "Canonical public HTTPS URL served by the VM reverse proxy. It must be a bare hostname with no path, port, credentials, or trailing slash."
  type        = string
  default     = "https://erpbridge.nimendra.online"

  validation {
    condition = can(regex(
      "^https://[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$",
      var.canonical_url,
    ))
    error_message = "canonical_url must be an https:// URL with a lowercase fully qualified hostname and no path, port, credentials, or trailing slash."
  }
}

variable "backup_storage_account_name" {
  description = "Globally unique name of the private backup storage account. Confirm availability before applying."
  type        = string
  default     = "erpbridgebackups"

  validation {
    condition     = can(regex("^[a-z0-9]{3,24}$", var.backup_storage_account_name))
    error_message = "backup_storage_account_name must be 3 to 24 lowercase letters or numbers."
  }
}

variable "backup_container_name" {
  description = "Private blob container that holds the off-VM database backups."
  type        = string
  default     = "erpbridge-backups"

  validation {
    condition     = can(regex("^[a-z0-9]([a-z0-9-]{1,61})[a-z0-9]$", var.backup_container_name))
    error_message = "backup_container_name must be a valid lowercase blob container name."
  }
}

variable "backup_retention_days" {
  description = "Lifecycle deletion age, in days, for backup blobs in the backup container."
  type        = number
  default     = 7

  validation {
    condition     = var.backup_retention_days >= 1 && var.backup_retention_days <= 365
    error_message = "backup_retention_days must be between 1 and 365."
  }
}

variable "tags" {
  description = "Additional Azure resource tags."
  type        = map(string)
  default     = {}
}

variable "monitoring_enabled" {
  description = "Opt in only after ownership, import targets, provider registration and deployment approval are confirmed. Does not install or change AMA."
  type        = bool
  default     = false
}

variable "monitoring_existing_resources_confirmed" {
  description = "Confirm the existing action group and VM availability rule have no other state owner and will be imported before apply."
  type        = bool
  default     = false
}

variable "monitoring_email_receiver" {
  description = "Privately confirmed existing receiver identity, address and action-group short name. Stored in protected Terraform state; never export it."
  type = object({
    name             = string
    email_address    = string
    group_short_name = string
  })
  sensitive = true
  default   = null
  validation {
    condition = var.monitoring_email_receiver == null ? true : (
      length(trimspace(var.monitoring_email_receiver.name)) > 0 &&
      can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.monitoring_email_receiver.email_address)) &&
      length(var.monitoring_email_receiver.group_short_name) >= 1 &&
      length(var.monitoring_email_receiver.group_short_name) <= 12
    )
    error_message = "Supply the existing receiver name, email address and action-group short name (1-12 characters) privately."
  }
}

variable "monitoring_guest_alert_enabled" {
  description = "One-time commissioning gate: enable immediately after fresh ingestion and query checks. False is not a maintenance mute or healthy-state indicator."
  type        = bool
  default     = false
  validation {
    condition     = !var.monitoring_guest_alert_enabled || var.monitoring_enabled
    error_message = "The guest alert requires monitoring_enabled."
  }
}

variable "monitoring_thresholds" {
  description = "Reviewed VM metric thresholds. Memory is bytes; CPU is percent. Changing policy requires deployment review."
  type = object({
    memory_warning_bytes  = number
    memory_critical_bytes = number
    cpu_percent           = number
  })
  default = {
    memory_warning_bytes  = 134217728
    memory_critical_bytes = 67108864
    cpu_percent           = 85
  }
  validation {
    condition = (
      var.monitoring_thresholds.memory_critical_bytes > 0 &&
      var.monitoring_thresholds.memory_warning_bytes > var.monitoring_thresholds.memory_critical_bytes &&
      var.monitoring_thresholds.cpu_percent > 0 && var.monitoring_thresholds.cpu_percent < 100
    )
    error_message = "Memory warning must exceed positive critical bytes; CPU must be between 0 and 100 percent."
  }
}
