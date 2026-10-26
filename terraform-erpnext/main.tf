data "azurerm_resource_group" "existing" {
  name = var.resource_group_name
}

locals {
  hostname = "erpnext.nimendra.online"
  tags = {
    project = "erpbridge"
    service = "erpnext-demo"
  }
}

resource "azurerm_virtual_network" "erpnext" {
  name                = "${var.vm_name}-vnet"
  location            = var.deployment_location
  resource_group_name = data.azurerm_resource_group.existing.name
  address_space       = ["10.42.0.0/16"]
  tags                = local.tags
}

resource "azurerm_subnet" "erpnext" {
  name                 = "${var.vm_name}-subnet"
  resource_group_name  = data.azurerm_resource_group.existing.name
  virtual_network_name = azurerm_virtual_network.erpnext.name
  address_prefixes     = ["10.42.1.0/24"]
}

resource "azurerm_public_ip" "erpnext" {
  name                = "${var.vm_name}-ip"
  location            = var.deployment_location
  resource_group_name = data.azurerm_resource_group.existing.name
  allocation_method   = "Static"
  sku                 = "Standard"
  tags                = local.tags
}

resource "azurerm_network_security_group" "erpnext" {
  name                = "${var.vm_name}-nsg"
  location            = var.deployment_location
  resource_group_name = data.azurerm_resource_group.existing.name
  tags                = local.tags

  dynamic "security_rule" {
    for_each = {
      ssh   = { priority = 100, port = "22", source = var.operator_ssh_cidr }
      http  = { priority = 110, port = "80", source = "Internet" }
      https = { priority = 120, port = "443", source = "Internet" }
    }
    content {
      name                       = security_rule.key
      priority                   = security_rule.value.priority
      direction                  = "Inbound"
      access                     = "Allow"
      protocol                   = "Tcp"
      source_port_range          = "*"
      destination_port_range     = security_rule.value.port
      source_address_prefix      = security_rule.value.source
      destination_address_prefix = "*"
    }
  }
}

resource "azurerm_network_interface" "erpnext" {
  name                = "${var.vm_name}-nic"
  location            = var.deployment_location
  resource_group_name = data.azurerm_resource_group.existing.name
  tags                = local.tags

  ip_configuration {
    name                          = "primary"
    subnet_id                     = azurerm_subnet.erpnext.id
    private_ip_address_allocation = "Dynamic"
    public_ip_address_id          = azurerm_public_ip.erpnext.id
  }
}

resource "azurerm_network_interface_security_group_association" "erpnext" {
  network_interface_id      = azurerm_network_interface.erpnext.id
  network_security_group_id = azurerm_network_security_group.erpnext.id
}

resource "azurerm_linux_virtual_machine" "erpnext" {
  name                            = var.vm_name
  location                        = var.deployment_location
  resource_group_name             = data.azurerm_resource_group.existing.name
  size                            = var.vm_size
  admin_username                  = var.ssh_username
  disable_password_authentication = true
  network_interface_ids           = [azurerm_network_interface.erpnext.id]
  tags                            = local.tags

  admin_ssh_key {
    username   = var.ssh_username
    public_key = file(pathexpand(var.ssh_public_key_path))
  }

  # A managed, non-ephemeral OS disk holds Docker volumes and Caddy state.
  # Deallocation preserves it; VM/disk deletion is not a backup strategy.
  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "StandardSSD_LRS"
    disk_size_gb         = 64
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "ubuntu-24_04-lts"
    sku       = "server"
    version   = "latest"
  }

  # Package installation only. Transfer application config/secrets separately.
  custom_data = base64encode(file("${path.module}/cloud-init.yaml"))
  depends_on  = [azurerm_network_interface_security_group_association.erpnext]
}

resource "cloudflare_dns_record" "erpnext" {
  zone_id = "c68ea0eb14e403a5454c04b6efecdcf2"
  name    = local.hostname
  type    = "A"
  content = azurerm_public_ip.erpnext.ip_address
  ttl     = 300
  proxied = false
  comment = "ERPNext viva demo VM, DNS-only for Caddy HTTPS"
}
