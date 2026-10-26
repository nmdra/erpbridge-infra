mock_provider "azurerm" {
  mock_data "azurerm_resource_group" {
    defaults = {
      name     = "erpbridge-hq"
      location = "eastus"
    }
  }
  mock_resource "azurerm_linux_virtual_machine" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/erpbridge-hq/providers/Microsoft.Compute/virtualMachines/erpnext-demo"
    }
  }
  mock_resource "azurerm_public_ip" {
    defaults = {
      id         = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/erpbridge-hq/providers/Microsoft.Network/publicIPAddresses/erpnext-demo-ip"
      ip_address = "203.0.113.42"
    }
  }
  mock_resource "azurerm_virtual_network" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/erpbridge-hq/providers/Microsoft.Network/virtualNetworks/erpnext-demo-vnet"
    }
  }
  mock_resource "azurerm_subnet" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/erpbridge-hq/providers/Microsoft.Network/virtualNetworks/erpnext-demo-vnet/subnets/erpnext-demo-subnet"
    }
  }
  mock_resource "azurerm_network_security_group" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/erpbridge-hq/providers/Microsoft.Network/networkSecurityGroups/erpnext-demo-nsg"
    }
  }
  mock_resource "azurerm_network_interface" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/erpbridge-hq/providers/Microsoft.Network/networkInterfaces/erpnext-demo-nic"
    }
  }
}
mock_provider "cloudflare" {}

variables {
  subscription_id      = "00000000-0000-0000-0000-000000000001"
  tenant_id            = "00000000-0000-0000-0000-000000000002"
  operator_ssh_cidr    = "203.0.113.10/32"
  ssh_public_key_path  = "tests/fixtures/operator.pub"
  ssh_private_key_path = "~/.ssh/erpnext-demo"
}

run "network_and_output_contract" {
  command = apply

  assert {
    condition = (
      length(azurerm_network_security_group.erpnext.security_rule) == 3 &&
      alltrue([for rule in azurerm_network_security_group.erpnext.security_rule :
        rule.direction == "Inbound" && rule.access == "Allow" && rule.protocol == "Tcp" &&
        contains(["22", "80", "443"], rule.destination_port_range) &&
        (rule.destination_port_range == "22" ? rule.source_address_prefix == var.operator_ssh_cidr : rule.source_address_prefix == "Internet")
      ])
    )
    error_message = "Only operator SSH and public HTTP/HTTPS must be allowed."
  }
  assert {
    condition = (
      azurerm_linux_virtual_machine.erpnext.location == "centralindia" &&
      azurerm_virtual_network.erpnext.location == "centralindia" &&
      azurerm_public_ip.erpnext.location == "centralindia" &&
      azurerm_network_security_group.erpnext.location == "centralindia" &&
      azurerm_network_interface.erpnext.location == "centralindia" &&
      data.azurerm_resource_group.existing.location == "eastus" &&
      azurerm_linux_virtual_machine.erpnext.disable_password_authentication &&
      azurerm_linux_virtual_machine.erpnext.size == "Standard_B2as_v2" &&
      azurerm_linux_virtual_machine.erpnext.os_disk[0].storage_account_type == "StandardSSD_LRS" &&
      azurerm_public_ip.erpnext.allocation_method == "Static" &&
      azurerm_public_ip.erpnext.sku == "Standard" &&
      azurerm_network_interface_security_group_association.erpnext.network_security_group_id == azurerm_network_security_group.erpnext.id
    )
    error_message = "VM must use SSH keys, persistent disk, static Standard IP, and associated NSG."
  }
  assert {
    condition = (
      cloudflare_dns_record.erpnext.proxied == false &&
      cloudflare_dns_record.erpnext.type == "A" &&
      cloudflare_dns_record.erpnext.content == "203.0.113.42" &&
      cloudflare_dns_record.erpnext.zone_id == "c68ea0eb14e403a5454c04b6efecdcf2" &&
      output.erpnext_url == "https://erpnext.nimendra.online" &&
      output.deployment_summary == {
        name                 = "erpnext-demo"
        resource_group       = "erpbridge-hq"
        resource_id          = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/erpbridge-hq/providers/Microsoft.Compute/virtualMachines/erpnext-demo"
        location             = "centralindia"
        url                  = "https://erpnext.nimendra.online"
        public_ip            = "203.0.113.42"
        ssh_username         = "erpnext"
        ssh_private_key_path = "~/.ssh/erpnext-demo"
        vm_size              = "Standard_B2as_v2"
      }
    )
    error_message = "DNS and non-secret exporter output contract must match."
  }
}

run "select_deployment_region" {
  command = plan
  variables { deployment_location = "uaenorth" }
  assert {
    condition = (
      azurerm_linux_virtual_machine.erpnext.location == "uaenorth" &&
      azurerm_virtual_network.erpnext.location == "uaenorth" &&
      azurerm_public_ip.erpnext.location == "uaenorth" &&
      azurerm_network_security_group.erpnext.location == "uaenorth" &&
      azurerm_network_interface.erpnext.location == "uaenorth" &&
      output.deployment_summary.location == "uaenorth"
    )
    error_message = "All regional resources and deployment output must follow the selected region."
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
run "reject_invalid_ssh" {
  command = plan
  variables { operator_ssh_cidr = "not-a-cidr" }
  expect_failures = [var.operator_ssh_cidr]
}
run "reject_ipv6_ssh" {
  command = plan
  variables { operator_ssh_cidr = "2001:db8::1/32" }
  expect_failures = [var.operator_ssh_cidr]
}
