# Mock-provider contracts for the isolated ERPBridge VM root. No cloud calls are
# made and no real state, tfvars, or credential file is read.
mock_provider "azapi" {}

mock_provider "azurerm" {
  mock_data "azurerm_resource_group" {
    defaults = {
      name     = "erpbridge-hq"
      location = "eastus"
    }
  }
  mock_resource "azurerm_linux_virtual_machine" {
    defaults = {
      id       = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/erpbridge-hq/providers/Microsoft.Compute/virtualMachines/erpbridge-vm"
      identity = { principal_id = "11111111-1111-1111-1111-111111111111", tenant_id = "22222222-2222-2222-2222-222222222222" }
    }
  }
  mock_resource "azurerm_public_ip" {
    defaults = {
      id         = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/erpbridge-hq/providers/Microsoft.Network/publicIPAddresses/erpbridge-vm-ip"
      ip_address = "203.0.113.42"
    }
  }
  mock_resource "azurerm_virtual_network" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/erpbridge-hq/providers/Microsoft.Network/virtualNetworks/erpbridge-vm-vnet"
    }
  }
  mock_resource "azurerm_subnet" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/erpbridge-hq/providers/Microsoft.Network/virtualNetworks/erpbridge-vm-vnet/subnets/erpbridge-vm-subnet"
    }
  }
  mock_resource "azurerm_network_security_group" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/erpbridge-hq/providers/Microsoft.Network/networkSecurityGroups/erpbridge-vm-nsg"
    }
  }
  mock_resource "azurerm_network_interface" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/erpbridge-hq/providers/Microsoft.Network/networkInterfaces/erpbridge-vm-nic"
    }
  }
  mock_resource "azurerm_storage_account" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/erpbridge-hq/providers/Microsoft.Storage/storageAccounts/erpbridgebackups"
    }
  }
  mock_resource "azurerm_storage_container" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/erpbridge-hq/providers/Microsoft.Storage/storageAccounts/erpbridgebackups/blobServices/default/containers/erpbridge-backups"
    }
  }
}

variables {
  subscription_id      = "00000000-0000-0000-0000-000000000001"
  tenant_id            = "00000000-0000-0000-0000-000000000002"
  operator_ssh_cidr    = "203.0.113.10/32"
  ssh_public_key_path  = "tests/fixtures/operator.pub"
  ssh_private_key_path = "~/.ssh/erpbridge-vm"
}

run "vm_network_and_backup_contract" {
  command = apply

  # Exactly three inbound allow rules: operator SSH, and public HTTP/HTTPS.
  assert {
    condition = (
      length(azurerm_network_security_group.erpbridge.security_rule) == 3 &&
      toset([
        for rule in azurerm_network_security_group.erpbridge.security_rule :
        "${rule.destination_port_range}:${rule.source_address_prefix}"
      ]) == toset(["22:${var.operator_ssh_cidr}", "80:Internet", "443:Internet"]) &&
      alltrue([
        for rule in azurerm_network_security_group.erpbridge.security_rule :
        rule.direction == "Inbound" && rule.access == "Allow" && rule.protocol == "Tcp"
      ])
    )
    error_message = "Only operator SSH and public HTTP/HTTPS may be allowed inbound, and the SSH source must equal the operator CIDR."
  }

  # The resource group is looked up through a data source, never managed here.
  assert {
    condition = (
      data.azurerm_resource_group.existing.name == var.resource_group_name &&
      alltrue([
        for name in [
          azurerm_linux_virtual_machine.erpbridge.resource_group_name,
          azurerm_virtual_network.erpbridge.resource_group_name,
          azurerm_subnet.erpbridge.resource_group_name,
          azurerm_network_security_group.erpbridge.resource_group_name,
          azurerm_network_interface.erpbridge.resource_group_name,
          azurerm_public_ip.erpbridge.resource_group_name,
          azurerm_storage_account.backup.resource_group_name,
        ] : name == data.azurerm_resource_group.existing.name
      ])
    )
    error_message = "Every resource must reuse the looked-up resource group."
  }

  # One OS disk, no separate data disk, and no data-disk attachment is declared.
  assert {
    condition = (
      azurerm_linux_virtual_machine.erpbridge.size == "Standard_B2ats_v2" &&
      azurerm_linux_virtual_machine.erpbridge.disable_password_authentication &&
      length(azurerm_linux_virtual_machine.erpbridge.network_interface_ids) == 1 &&
      length(azurerm_linux_virtual_machine.erpbridge.os_disk) == 1 &&
      azurerm_linux_virtual_machine.erpbridge.os_disk[0].name == "erpbridge-vm-osdisk" &&
      azurerm_linux_virtual_machine.erpbridge.os_disk[0].storage_account_type == "StandardSSD_LRS" &&
      azurerm_linux_virtual_machine.erpbridge.os_disk[0].disk_size_gb == 32 &&
      azurerm_linux_virtual_machine.erpbridge.identity[0].type == "SystemAssigned" &&
      azurerm_linux_virtual_machine.erpbridge.location == "centralindia"
    )
    error_message = "The VM must use the approved size, key-only SSH, one 32 GiB Standard SSD OS disk with an explicit name, and a system-assigned identity."
  }

  assert {
    condition = (
      azurerm_public_ip.erpbridge.allocation_method == "Static" &&
      azurerm_public_ip.erpbridge.sku == "Standard" &&
      azurerm_network_interface_security_group_association.erpbridge.network_security_group_id == azurerm_network_security_group.erpbridge.id
    )
    error_message = "The public IP must be Static Standard and the NSG must be associated with the NIC."
  }

  assert {
    condition = (
      azurerm_storage_container.backup.container_access_type == "private" &&
      azurerm_storage_account.backup.shared_access_key_enabled == false &&
      azurerm_storage_account.backup.allow_nested_items_to_be_public == false &&
      azurerm_role_assignment.backup_writer.scope == azurerm_storage_container.backup.id &&
      azurerm_storage_management_policy.backup.storage_account_id == azurerm_storage_account.backup.id
    )
    error_message = "Backups must use a private container, no shared keys, container-scoped RBAC, and a lifecycle policy on the same account."
  }

  assert {
    condition = (
      output.erpbridge_url == "https://erpbridge.nimendra.online" &&
      output.os_disk.id != "" &&
      output.os_disk.retain_on_vm_deletion &&
      output.backup.container_id == azurerm_storage_container.backup.id
    )
    error_message = "The canonical URL and disk/backup identifiers must be exported."
  }

  # The summary is an explicit allow-list of non-secret fields. No field name
  # and no field value may carry a credential, token, key, or connection string.
  assert {
    condition = (
      toset(keys(output.deployment_summary)) == toset([
        "name", "resource_id", "resource_group", "location", "url", "public_ip",
        "ssh_username", "ssh_private_key_path", "vm_size", "os_disk_name",
        "os_disk_size_gb", "backup_storage_account", "backup_container",
      ]) &&
      alltrue([
        for value in values(output.deployment_summary) :
        !can(regex("(?i)(password|passwd|secret|token|credential|api[_-]?key|access[_-]?key|connection[_-]?string|BEGIN [A-Z ]*PRIVATE KEY)", tostring(value)))
      ]) &&
      output.deployment_summary.public_ip == "203.0.113.42" &&
      output.deployment_summary.resource_group == "erpbridge-hq" &&
      output.deployment_summary.os_disk_name == "erpbridge-vm-osdisk" &&
      output.deployment_summary.os_disk_size_gb == 32 &&
      output.deployment_summary.backup_storage_account == "erpbridgebackups" &&
      output.deployment_summary.backup_container == "erpbridge-backups" &&
      output.deployment_summary.url == "https://erpbridge.nimendra.online"
    )
    error_message = "The deployment summary must contain exactly the non-secret metadata fields."
  }
}

run "select_deployment_region" {
  command = plan
  variables { deployment_location = "uaenorth" }
  assert {
    condition = (
      azurerm_linux_virtual_machine.erpbridge.location == "uaenorth" &&
      azurerm_virtual_network.erpbridge.location == "uaenorth" &&
      azurerm_public_ip.erpbridge.location == "uaenorth" &&
      azurerm_network_security_group.erpbridge.location == "uaenorth" &&
      azurerm_network_interface.erpbridge.location == "uaenorth" &&
      azurerm_storage_account.backup.location == "uaenorth" &&
      output.deployment_summary.location == "uaenorth"
    )
    error_message = "All regional resources and the summary must follow the selected region."
  }
}

run "reject_world_ssh" {
  command = plan
  variables { operator_ssh_cidr = "0.0.0.0/0" }
  expect_failures = [var.operator_ssh_cidr]
}

run "reject_subnet_ssh" {
  command = plan
  variables { operator_ssh_cidr = "203.0.113.0/24" }
  expect_failures = [var.operator_ssh_cidr]
}

run "reject_ipv6_ssh" {
  command = plan
  variables { operator_ssh_cidr = "2001:db8::1/32" }
  expect_failures = [var.operator_ssh_cidr]
}

run "reject_junk_ssh" {
  command = plan
  variables { operator_ssh_cidr = "not-a-cidr" }
  expect_failures = [var.operator_ssh_cidr]
}

run "reject_insecure_canonical_url" {
  command = plan
  variables { canonical_url = "http://erpbridge.nimendra.online" }
  expect_failures = [var.canonical_url]
}

run "reject_malformed_canonical_url" {
  command = plan
  variables { canonical_url = "https://erpbridge" }
  expect_failures = [var.canonical_url]
}

run "reject_canonical_url_with_path" {
  command = plan
  variables { canonical_url = "https://erpbridge.nimendra.online/mcp/" }
  expect_failures = [var.canonical_url]
}

run "reject_unapproved_vm_size" {
  command = plan
  variables { vm_size = "Standard_B2ms" }
  expect_failures = [var.vm_size]
}
