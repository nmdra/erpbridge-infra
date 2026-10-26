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
  mock_resource "azurerm_monitor_action_group" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/erpbridge-hq/providers/Microsoft.Insights/actionGroups/VMI-ActionGroup-erpbridge-vm" }
  }
  mock_resource "azurerm_log_analytics_workspace" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/erpbridge-hq/providers/Microsoft.OperationalInsights/workspaces/erpbridge-vm-monitor-logs" }
  }
  mock_resource "azurerm_application_insights" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/erpbridge-hq/providers/Microsoft.Insights/components/erpbridge-vm-monitor" }
  }
  mock_resource "azurerm_application_insights_standard_web_test" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/erpbridge-hq/providers/Microsoft.Insights/webTests/erpbridge-vm-https-health" }
  }
  mock_resource "azurerm_monitor_data_collection_rule" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/erpbridge-hq/providers/Microsoft.Insights/dataCollectionRules/erpbridge-vm-health-json" }
  }

}

variables {
  subscription_id      = "00000000-0000-0000-0000-000000000001"
  tenant_id            = "00000000-0000-0000-0000-000000000002"
  operator_ssh_cidr    = "203.0.113.10/32"
  ssh_public_key_path  = "tests/fixtures/operator.pub"
  ssh_private_key_path = "~/.ssh/erpbridge-vm"
}

run "disabled_by_default" {
  command = apply
  assert {
    condition     = output.monitoring == null && length(azurerm_monitor_action_group.monitoring) == 0 && length(azurerm_linux_virtual_machine.erpbridge.boot_diagnostics) == 0 && length(azurerm_monitor_data_collection_rule.health) == 0
    error_message = "Default configuration must not deploy monitoring or change boot diagnostics."
  }
}

run "enabled_monitoring_contract" {
  command = apply
  variables {
    monitoring_enabled                      = true
    monitoring_existing_resources_confirmed = true
    monitoring_email_receiver               = { name = "fixture-only", email_address = "fixture@example.invalid", group_short_name = "fixture" }
  }
  assert {
    condition = (
      azurerm_application_insights_standard_web_test.health[0].frequency == 900 &&
      toset(azurerm_application_insights_standard_web_test.health[0].geo_locations) == toset(["apac-sg-sin-azr", "emea-nl-ams-azr", "us-va-ash-azr"]) &&
      azurerm_application_insights_standard_web_test.health[0].timeout == 30 &&
      azurerm_application_insights_standard_web_test.health[0].retry_enabled &&
      azurerm_application_insights_standard_web_test.health[0].request[0].url == "${var.canonical_url}/mcp/health" &&
      !azurerm_application_insights_standard_web_test.health[0].request[0].follow_redirects_enabled &&
      !azurerm_application_insights_standard_web_test.health[0].request[0].parse_dependent_requests_enabled &&
      azurerm_application_insights_standard_web_test.health[0].validation_rules[0].expected_status_code == 200 &&
      azurerm_application_insights_standard_web_test.health[0].validation_rules[0].ssl_check_enabled &&
      azurerm_monitor_metric_alert.https[0].application_insights_web_test_location_availability_criteria[0].failed_location_count == 2 &&
      azurerm_monitor_metric_alert.https[0].frequency == "PT5M" &&
      azurerm_monitor_metric_alert.https[0].window_size == "PT30M"
    )
    error_message = "Independent HTTPS coverage must use exact status, TLS, retries and the approved three locations and timing."
  }
  assert {
    condition = (
      azurerm_monitor_action_group.monitoring[0].name == "VMI-ActionGroup-erpbridge-vm" &&
      azurerm_monitor_metric_alert.vm["availability"].name == "VM Availability - erpbridge-vm" &&
      length(azurerm_monitor_metric_alert.vm) == 4 &&
      alltrue([for rule in azurerm_monitor_metric_alert.vm : rule.scopes == toset([azurerm_linux_virtual_machine.erpbridge.id]) && rule.criteria[0].metric_namespace == "Microsoft.Compute/virtualMachines"]) &&
      azurerm_monitor_metric_alert.vm["memory_warning"].criteria[0].threshold == 134217728 &&
      azurerm_monitor_metric_alert.vm["memory_critical"].criteria[0].threshold == 67108864 &&
      azurerm_monitor_metric_alert.vm["cpu"].criteria[0].threshold == 85 &&
      length(azurerm_linux_virtual_machine.erpbridge.boot_diagnostics) == 1 &&
      azurerm_linux_virtual_machine.erpbridge.custom_data == base64encode(file("cloud-init.yaml"))
    )
    error_message = "Reuse the existing alert identities, scope metrics to one VM, and enable only managed diagnostics without changing custom_data."
  }
  assert {
    condition = (
      azurerm_log_analytics_workspace.monitoring[0].retention_in_days == 30 &&
      azurerm_application_insights.monitoring[0].workspace_id == azurerm_log_analytics_workspace.monitoring[0].id &&
      azapi_resource.health_table[0].body.properties.plan == "Analytics" &&
      azapi_resource.health_table[0].body.properties.retentionInDays == 30 &&
      azapi_resource.health_table[0].body.properties.schema.columns[0].type == "dateTime" &&
      length(azapi_resource.health_table[0].body.properties.schema.columns) == 28 &&
      alltrue([for i, column in local.health_columns :
        azapi_resource.health_table[0].body.properties.schema.columns[i].name == column.name &&
        azapi_resource.health_table[0].body.properties.schema.columns[i].type == (column.type == "datetime" ? "dateTime" : column.type) &&
        one(azurerm_monitor_data_collection_rule.health[0].stream_declaration).column[i].name == column.name &&
        one(azurerm_monitor_data_collection_rule.health[0].stream_declaration).column[i].type == column.type
      ]) &&
      one(azurerm_monitor_data_collection_rule.health[0].stream_declaration).column[0].type == "datetime" &&
      azurerm_monitor_data_collection_rule.health[0].data_sources[0].log_file[0].format == "json" &&
      azurerm_monitor_data_collection_rule.health[0].data_sources[0].log_file[0].file_patterns == tolist(["/var/log/erpbridge-monitor/health-*.jsonl"]) &&
      length(azurerm_monitor_data_collection_rule.health[0].data_sources[0].log_file[0].settings) == 0 &&
      length(azurerm_monitor_data_collection_rule.health[0].data_sources[0].performance_counter) == 0 &&
      azurerm_monitor_data_collection_rule_association.health[0].target_resource_id == azurerm_linux_virtual_machine.erpbridge.id
    )
    error_message = "Dedicated typed JSON ingestion must preserve existing OTel and use event time, with the custom table created first."
  }
  assert {
    condition = (
      !azurerm_monitor_scheduled_query_rules_alert_v2.guest[0].enabled &&
      azurerm_monitor_scheduled_query_rules_alert_v2.guest[0].evaluation_frequency == "PT5M" &&
      azurerm_monitor_scheduled_query_rules_alert_v2.guest[0].window_duration == "PT30M" &&
      length(azurerm_monitor_scheduled_query_rules_alert_v2.guest[0].criteria[0].dimension) == 0 &&
      strcontains(azurerm_monitor_scheduled_query_rules_alert_v2.guest[0].criteria[0].query, "datatable(ExpectedVm:string)") &&
      strcontains(azurerm_monitor_scheduled_query_rules_alert_v2.guest[0].criteria[0].query, "join kind=leftouter") &&
      strcontains(azurerm_monitor_scheduled_query_rules_alert_v2.guest[0].criteria[0].query, "arg_max(TimeGenerated, *)") &&
      strcontains(azurerm_monitor_scheduled_query_rules_alert_v2.guest[0].criteria[0].query, "TimeGenerated < ago(10m)") &&
      !strcontains(azurerm_monitor_scheduled_query_rules_alert_v2.guest[0].criteria[0].query, "ingestion_time") &&
      strcontains(azurerm_application_insights_workbook.monitoring[0].data_json, "AllFaults") &&
      length(azurerm_monitor_activity_log_alert.operator) == 3 &&
      azurerm_monitor_activity_log_alert.resource_health[0].scopes == toset([azurerm_linux_virtual_machine.erpbridge.id])
    )
    error_message = "The seeded no-data rule must stay unsplit and gated during commissioning; workbook shows all faults."
  }
  assert {
    condition     = !contains(keys(output.monitoring), "email_receiver") && output.monitoring.web_test_frequency_seconds == 900 && length(keys(output.deployment_summary)) == 13
    error_message = "Monitoring output must not expose recipient PII or change deployment_summary."
  }
}

run "commission_guest_rule" {
  command = plan
  variables {
    monitoring_enabled                      = true
    monitoring_existing_resources_confirmed = true
    monitoring_email_receiver               = { name = "fixture-only", email_address = "fixture@example.invalid", group_short_name = "fixture" }
    monitoring_guest_alert_enabled          = true
  }
  assert {
    condition     = azurerm_monitor_scheduled_query_rules_alert_v2.guest[0].enabled
    error_message = "The one-time gate must permit normal no-data detection after commissioning."
  }
}

run "reject_unconfirmed_adoption" {
  command = plan
  variables {
    monitoring_enabled = true
  }
  expect_failures = [azurerm_monitor_action_group.monitoring]
}

run "reject_invalid_thresholds" {
  command = plan
  variables {
    monitoring_enabled                      = true
    monitoring_existing_resources_confirmed = true
    monitoring_email_receiver               = { name = "fixture-only", email_address = "fixture@example.invalid", group_short_name = "fixture" }
    monitoring_thresholds                   = { memory_warning_bytes = 1, memory_critical_bytes = 2, cpu_percent = 100 }
  }
  expect_failures = [var.monitoring_thresholds]
}
