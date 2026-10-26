# One VM only. Import the two existing resources before any enabled apply.
# Existing AMA, OTel DCR/DCRA and shared workspaces remain outside this root.
locals {
  monitoring_count = var.monitoring_enabled ? 1 : 0
  health_columns   = jsondecode(file("${path.module}/monitoring-schema.json"))
  vm_monitor_id    = lower(azurerm_linux_virtual_machine.erpbridge.id)
  monitoring_metrics = var.monitoring_enabled ? {
    availability    = { name = "VM Availability - ${var.vm_name}", metric = "VmAvailabilityMetric", operator = "LessThan", threshold = 1, severity = 1, frequency = "PT1M", window = "PT5M" }
    memory_warning  = { name = "${var.vm_name}-memory-warning", metric = "Available Memory Bytes", operator = "LessThan", threshold = var.monitoring_thresholds.memory_warning_bytes, severity = 2, frequency = "PT1M", window = "PT15M" }
    memory_critical = { name = "${var.vm_name}-memory-critical", metric = "Available Memory Bytes", operator = "LessThan", threshold = var.monitoring_thresholds.memory_critical_bytes, severity = 1, frequency = "PT1M", window = "PT5M" }
    cpu             = { name = "${var.vm_name}-cpu", metric = "Percentage CPU", operator = "GreaterThan", threshold = var.monitoring_thresholds.cpu_percent, severity = 2, frequency = "PT5M", window = "PT15M" }
  } : {}

  # Event time is authoritative. Old replay cannot recover a no-data incident.
  # Keep the seed OUTSIDE the table expression, including when the table is empty.
  health_current_query = templatefile("${path.module}/monitoring/health-current.kql.tftpl", { vm_resource_id = local.vm_monitor_id })
  health_alert_query   = "${local.health_current_query}\n| where NoData or Service or Disk or Backup"
}

resource "azurerm_monitor_action_group" "monitoring" {
  count               = local.monitoring_count
  name                = "VMI-ActionGroup-${var.vm_name}"
  resource_group_name = data.azurerm_resource_group.existing.name
  short_name          = var.monitoring_email_receiver == null ? "unset" : var.monitoring_email_receiver.group_short_name
  enabled             = true
  tags                = local.tags
  dynamic "email_receiver" {
    for_each = var.monitoring_email_receiver == null ? [] : [var.monitoring_email_receiver]
    content {
      name                    = email_receiver.value.name
      email_address           = email_receiver.value.email_address
      use_common_alert_schema = true
    }
  }
  lifecycle {
    # Full POC teardown approved. Keep the ownership precondition.
    precondition {
      condition     = var.monitoring_existing_resources_confirmed && var.monitoring_email_receiver != null
      error_message = "Confirm ownership/import targets and provide the existing recipient privately before enabling monitoring. Import existing resources before apply."
    }
  }
}

# availability is the import address for the existing portal rule, not a new rule.
resource "azurerm_monitor_metric_alert" "vm" {
  for_each            = local.monitoring_metrics
  name                = each.value.name
  resource_group_name = data.azurerm_resource_group.existing.name
  scopes              = [azurerm_linux_virtual_machine.erpbridge.id]
  severity            = each.value.severity
  frequency           = each.value.frequency
  window_size         = each.value.window
  enabled             = true
  auto_mitigate       = true
  tags                = local.tags
  criteria {
    metric_namespace       = "Microsoft.Compute/virtualMachines"
    metric_name            = each.value.metric
    aggregation            = "Average"
    operator               = each.value.operator
    threshold              = each.value.threshold
    skip_metric_validation = false
  }
  action { action_group_id = azurerm_monitor_action_group.monitoring[0].id }
}

resource "azurerm_log_analytics_workspace" "monitoring" {
  count               = local.monitoring_count
  name                = "${var.vm_name}-monitor-logs"
  location            = var.deployment_location
  resource_group_name = data.azurerm_resource_group.existing.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = local.tags
}

resource "azurerm_application_insights" "monitoring" {
  count               = local.monitoring_count
  name                = "${var.vm_name}-monitor"
  location            = var.deployment_location
  resource_group_name = data.azurerm_resource_group.existing.name
  workspace_id        = azurerm_log_analytics_workspace.monitoring[0].id
  application_type    = "web"
  retention_in_days   = 30
  tags                = local.tags
}

resource "azurerm_application_insights_standard_web_test" "health" {
  count                   = local.monitoring_count
  name                    = "${var.vm_name}-https-health"
  location                = var.deployment_location
  resource_group_name     = data.azurerm_resource_group.existing.name
  application_insights_id = azurerm_application_insights.monitoring[0].id
  geo_locations           = ["apac-sg-sin-azr", "emea-nl-ams-azr", "us-va-ash-azr"]
  frequency               = 900
  timeout                 = 30
  retry_enabled           = true
  enabled                 = true
  tags                    = local.tags
  request {
    url                              = "${var.canonical_url}/mcp/health"
    http_verb                        = "GET"
    follow_redirects_enabled         = false
    parse_dependent_requests_enabled = false
  }
  validation_rules {
    expected_status_code        = 200
    ssl_check_enabled           = true
    ssl_cert_remaining_lifetime = 1
  }
}

resource "azurerm_monitor_metric_alert" "https" {
  count               = local.monitoring_count
  name                = "${var.vm_name}-https-health"
  resource_group_name = data.azurerm_resource_group.existing.name
  scopes              = [azurerm_application_insights_standard_web_test.health[0].id, azurerm_application_insights.monitoring[0].id]
  severity            = 1
  frequency           = "PT5M"
  window_size         = "PT30M"
  enabled             = true
  auto_mitigate       = true
  tags                = local.tags
  application_insights_web_test_location_availability_criteria {
    component_id          = azurerm_application_insights.monitoring[0].id
    web_test_id           = azurerm_application_insights_standard_web_test.health[0].id
    failed_location_count = 2
  }
  action { action_group_id = azurerm_monitor_action_group.monitoring[0].id }
}

# Activity Log alerts do not expose configurable severity in AzureRM or the API.
resource "azurerm_monitor_activity_log_alert" "resource_health" {
  count               = local.monitoring_count
  name                = "${var.vm_name}-resource-health"
  location            = "global"
  resource_group_name = data.azurerm_resource_group.existing.name
  scopes              = [azurerm_linux_virtual_machine.erpbridge.id]
  enabled             = true
  tags                = local.tags
  criteria {
    category    = "ResourceHealth"
    resource_id = azurerm_linux_virtual_machine.erpbridge.id
    resource_health { current = ["Unavailable", "Degraded"] }
  }
  action { action_group_id = azurerm_monitor_action_group.monitoring[0].id }
}

resource "azurerm_monitor_activity_log_alert" "operator" {
  for_each            = var.monitoring_enabled ? toset(["restart", "powerOff", "deallocate"]) : toset([])
  name                = "${var.vm_name}-${lower(each.key)}"
  location            = "global"
  resource_group_name = data.azurerm_resource_group.existing.name
  scopes              = [azurerm_linux_virtual_machine.erpbridge.id]
  enabled             = true
  tags                = local.tags
  criteria {
    category       = "Administrative"
    resource_id    = azurerm_linux_virtual_machine.erpbridge.id
    operation_name = "Microsoft.Compute/virtualMachines/${each.key}/action"
  }
  action { action_group_id = azurerm_monitor_action_group.monitoring[0].id }
}

# AzAPI is restricted to the custom table, which AzureRM cannot create.
resource "azapi_resource" "health_table" {
  count     = local.monitoring_count
  type      = "Microsoft.OperationalInsights/workspaces/tables@2022-10-01"
  name      = "ERPBridgeHealth_CL"
  parent_id = azurerm_log_analytics_workspace.monitoring[0].id
  body = {
    properties = {
      plan                 = "Analytics"
      retentionInDays      = 30
      totalRetentionInDays = 30
      schema = {
        name = "ERPBridgeHealth_CL"
        columns = [for column in local.health_columns : {
          name = column.name
          type = column.type == "datetime" ? "dateTime" : column.type
        }]
      }
    }
  }
}

resource "azurerm_monitor_data_collection_rule" "health" {
  count               = local.monitoring_count
  name                = "${var.vm_name}-health-json"
  location            = var.deployment_location
  resource_group_name = data.azurerm_resource_group.existing.name
  kind                = "Linux"
  tags                = local.tags
  destinations {
    log_analytics {
      name                  = "health-workspace"
      workspace_resource_id = azurerm_log_analytics_workspace.monitoring[0].id
    }
  }
  stream_declaration {
    stream_name = "Custom-ERPBridgeHealth"
    dynamic "column" {
      for_each = local.health_columns
      content {
        name = column.value.name
        type = column.value.type
      }
    }
  }
  data_sources {
    log_file {
      name          = "erpbridge-health-json"
      format        = "json"
      streams       = ["Custom-ERPBridgeHealth"]
      file_patterns = ["/var/log/erpbridge-monitor/health-*.jsonl"]
    }
  }
  data_flow {
    streams       = ["Custom-ERPBridgeHealth"]
    destinations  = ["health-workspace"]
    output_stream = "Custom-ERPBridgeHealth_CL"
    transform_kql = "source | project ${join(", ", [for column in local.health_columns : column.name == "TimeGenerated" ? "TimeGenerated = todatetime(TimeGenerated)" : column.name])}"
  }
  depends_on = [azapi_resource.health_table]
}

resource "azurerm_monitor_data_collection_rule_association" "health" {
  count                   = local.monitoring_count
  name                    = "erpbridge-health-json"
  target_resource_id      = azurerm_linux_virtual_machine.erpbridge.id
  data_collection_rule_id = azurerm_monitor_data_collection_rule.health[0].id
  description             = "Additional numeric JSON health stream. Preserve the existing OTel association and AMA installation."
}

resource "azurerm_monitor_scheduled_query_rules_alert_v2" "guest" {
  count                     = local.monitoring_count
  name                      = "${var.vm_name}-guest-health"
  location                  = var.deployment_location
  resource_group_name       = data.azurerm_resource_group.existing.name
  scopes                    = [azurerm_log_analytics_workspace.monitoring[0].id]
  severity                  = 2
  enabled                   = var.monitoring_guest_alert_enabled
  evaluation_frequency      = "PT5M"
  window_duration           = "PT30M"
  query_time_range_override = "PT30M"
  auto_mitigation_enabled   = true
  skip_query_validation     = false
  description               = "One unsplit incident for this VM: missing telemetry, persisted service/disk faults and backup faults. Workbook shows all current reasons. Enable after commissioning."
  tags                      = local.tags
  criteria {
    query                   = local.health_alert_query
    time_aggregation_method = "Count"
    operator                = "GreaterThan"
    threshold               = 0
    failing_periods {
      minimum_failing_periods_to_trigger_alert = 1
      number_of_evaluation_periods             = 1
    }
  }
  action { action_groups = [azurerm_monitor_action_group.monitoring[0].id] }
  depends_on = [azapi_resource.health_table]
}

resource "azurerm_application_insights_workbook" "monitoring" {
  count               = local.monitoring_count
  name                = uuidv5("url", "${azurerm_linux_virtual_machine.erpbridge.id}/monitoring")
  display_name        = "ERPBridge VM monitoring"
  location            = var.deployment_location
  resource_group_name = data.azurerm_resource_group.existing.name
  source_id           = lower(azurerm_log_analytics_workspace.monitoring[0].id)
  tags                = local.tags
  data_json = templatefile("${path.module}/monitoring/workbook.json.tftpl", {
    vm_resource_id = local.vm_monitor_id
    workspace_id   = azurerm_log_analytics_workspace.monitoring[0].id
    current_query  = "${local.health_current_query}\n| project-away ExpectedVm, VmKey"
  })
}
