# Isolated root for the dedicated ERPBridge VM only. It looks up the existing
# resource group and never manages it, and it declares no DNS records, no
# container apps, and no other workload's resources.

data "azurerm_resource_group" "existing" {
  name = var.resource_group_name
}

locals {
  # canonical_url is validated as a bare https://<fqdn> value with no path.
  hostname = replace(var.canonical_url, "https://", "")

  tags = merge({
    project = "erpbridge"
    service = "erpbridge-vm"
  }, var.tags)
}

resource "azurerm_virtual_network" "erpbridge" {
  name                = "${var.vm_name}-vnet"
  location            = var.deployment_location
  resource_group_name = data.azurerm_resource_group.existing.name
  address_space       = ["10.43.0.0/16"]
  tags                = local.tags
}

resource "azurerm_subnet" "erpbridge" {
  name                 = "${var.vm_name}-subnet"
  resource_group_name  = data.azurerm_resource_group.existing.name
  virtual_network_name = azurerm_virtual_network.erpbridge.name
  address_prefixes     = ["10.43.1.0/24"]

  # The backup account denies public traffic, so this subnet needs the
  # Microsoft.Storage service endpoint to reach it.
  service_endpoint {
    service = "Microsoft.Storage"
  }
}

resource "azurerm_public_ip" "erpbridge" {
  name                = "${var.vm_name}-ip"
  location            = var.deployment_location
  resource_group_name = data.azurerm_resource_group.existing.name
  allocation_method   = "Static"
  sku                 = "Standard"
  tags                = local.tags
}

resource "azurerm_network_security_group" "erpbridge" {
  name                = "${var.vm_name}-nsg"
  location            = var.deployment_location
  resource_group_name = data.azurerm_resource_group.existing.name
  tags                = local.tags

  security_rule {
    name                       = "operator-ssh"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "22"
    source_address_prefix      = var.operator_ssh_cidr
    destination_address_prefix = "*"
  }

  security_rule {
    name                       = "public-http"
    priority                   = 110
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "80"
    source_address_prefix      = "Internet"
    destination_address_prefix = "*"
  }

  security_rule {
    name                       = "public-https"
    priority                   = 120
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "443"
    source_address_prefix      = "Internet"
    destination_address_prefix = "*"
  }
}

resource "azurerm_network_interface" "erpbridge" {
  name                = "${var.vm_name}-nic"
  location            = var.deployment_location
  resource_group_name = data.azurerm_resource_group.existing.name
  tags                = local.tags

  ip_configuration {
    name                          = "primary"
    subnet_id                     = azurerm_subnet.erpbridge.id
    private_ip_address_allocation = "Dynamic"
    public_ip_address_id          = azurerm_public_ip.erpbridge.id
  }
}

resource "azurerm_network_interface_security_group_association" "erpbridge" {
  network_interface_id      = azurerm_network_interface.erpbridge.id
  network_security_group_id = azurerm_network_security_group.erpbridge.id
}

resource "azurerm_linux_virtual_machine" "erpbridge" {
  name                            = var.vm_name
  location                        = var.deployment_location
  resource_group_name             = data.azurerm_resource_group.existing.name
  size                            = var.vm_size
  admin_username                  = var.ssh_username
  disable_password_authentication = true
  network_interface_ids           = [azurerm_network_interface.erpbridge.id]
  tags                            = local.tags

  admin_ssh_key {
    username   = var.ssh_username
    public_key = file(pathexpand(var.ssh_public_key_path))
  }

  # System-assigned identity for the backup container data-plane role only.
  identity {
    type = "SystemAssigned"
  }

  # One managed, non-ephemeral OS disk with an explicit name. No separate data
  # disk and no data-disk attachment are declared; the 32 GiB OS disk holds the
  # SQLite database, and the provider retains it if the VM is deleted.
  os_disk {
    name                 = var.os_disk_name
    caching              = "ReadWrite"
    storage_account_type = "StandardSSD_LRS"
    disk_size_gb         = var.os_disk_size_gb
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "ubuntu-24_04-lts"
    sku       = "server"
    version   = "latest"
  }

  # Managed diagnostics must be reviewed as an in-place update. Never accept
  # VM replacement to enable monitoring. No custom_data changes are involved.
  dynamic "boot_diagnostics" {
    for_each = var.monitoring_enabled ? [1] : []
    content {}
  }

  # Package installation and SSH hardening only. The runtime stack and its
  # protected environment file are transferred separately by the operator.
  custom_data = base64encode(file("${path.module}/cloud-init.yaml"))
  depends_on  = [azurerm_network_interface_security_group_association.erpbridge]
}

resource "azurerm_storage_account" "backup" {
  name                            = var.backup_storage_account_name
  location                        = var.deployment_location
  resource_group_name             = data.azurerm_resource_group.existing.name
  account_kind                    = "StorageV2"
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  access_tier                     = "Hot"
  min_tls_version                 = "TLS1_2"
  https_traffic_only_enabled      = true
  shared_access_key_enabled       = false
  allow_nested_items_to_be_public = false
  public_network_access_enabled   = true
  tags                            = local.tags

  # No anonymous access and no shared-key access. Public network access stays
  # enabled because a private endpoint is out of scope, but the firewall denies
  # everything except the VM subnet through its storage service endpoint.
  network_rules {
    default_action             = "Deny"
    bypass                     = ["None"]
    virtual_network_subnet_ids = [azurerm_subnet.erpbridge.id]
  }
}

resource "azurerm_storage_container" "backup" {
  name                  = var.backup_container_name
  storage_account_id    = azurerm_storage_account.backup.id
  container_access_type = "private"
}

# The VM identity may write and read backups in this one container only.
resource "azurerm_role_assignment" "backup_writer" {
  scope                = azurerm_storage_container.backup.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_linux_virtual_machine.erpbridge.identity[0].principal_id
}

resource "azurerm_storage_management_policy" "backup" {
  storage_account_id = azurerm_storage_account.backup.id

  rule {
    name    = "expire-old-backups"
    enabled = true

    filters {
      prefix_match = ["${var.backup_container_name}/"]
      blob_types   = ["blockBlob"]
    }

    actions {
      base_blob {
        delete_after_days_since_creation_greater_than = var.backup_retention_days
      }

      snapshot {
        delete_after_days_since_creation_greater_than = var.backup_retention_days
      }

      version {
        delete_after_days_since_creation = var.backup_retention_days
      }
    }
  }
}
