variable "subscription_id" {
  description = "Existing Azure subscription ID. No subscription changes are made."
  type        = string
}

variable "tenant_id" {
  description = "Azure tenant ID used by the operator's existing authentication."
  type        = string
}

variable "resource_group_name" {
  description = "Existing ERPBridge resource group, looked up but not managed here."
  type        = string
  default     = "erpbridge-hq"
}

variable "deployment_location" {
  description = "Azure region for the VM and networking, independent of the existing resource group's metadata location. Check regional quota and cost before applying."
  type        = string
  default     = "centralindia"

  validation {
    condition     = can(regex("^[a-z][a-z0-9]+$", var.deployment_location))
    error_message = "deployment_location must be an Azure region name such as centralindia."
  }
}

variable "vm_name" {
  description = "Name of the separate ERPNext demo VM and network resource prefix."
  type        = string
  default     = "erpnext-demo"
}

variable "vm_size" {
  description = "Trial size only. Check regional SKU, quota, and cost before applying."
  type        = string
  default     = "Standard_B2as_v2"
}

variable "ssh_username" {
  description = "SSH administrator username. Password authentication is disabled."
  type        = string
  default     = "erpnext"
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
    condition = can(cidrnetmask(var.operator_ssh_cidr)) && can(regex("/32$", var.operator_ssh_cidr)) && !contains(
      ["0.0.0.0/32", "255.255.255.255/32"], var.operator_ssh_cidr
    )
    error_message = "operator_ssh_cidr must be one explicit IPv4 /32, not a subnet, IPv6 address, or unspecified address."
  }
}
