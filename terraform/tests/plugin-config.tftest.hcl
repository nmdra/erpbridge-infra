# All provider calls use local mocks. Do not run a cloud plan for these contracts.
mock_provider "azurerm" {
  override_during = plan
  mock_resource "azurerm_container_app_environment" {
    defaults = {
      id             = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/erpbridge-hq/providers/Microsoft.App/managedEnvironments/erpbridge-demo-aca"
      default_domain = "fixture.azurecontainerapps.io"
    }
  }
  mock_resource "azurerm_container_app" {
    defaults = {
      id                            = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/erpbridge-hq/providers/Microsoft.App/containerApps/fixture-app"
      custom_domain_verification_id = "fixture-verification"
    }
  }
  mock_resource "azurerm_log_analytics_workspace" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/erpbridge-hq/providers/Microsoft.OperationalInsights/workspaces/erpbridge-demo-logs"
    }
  }
}
mock_provider "azapi" {}
mock_provider "cloudflare" {}

variables {
  subscription_id       = "00000000-0000-0000-0000-000000000000"
  tenant_id             = "00000000-0000-0000-0000-000000000000"
  erpbridge_image       = "example.invalid/bridge@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  mockerp_image         = "example.invalid/mock@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  api_auth_token        = "fixture-only-bridge-token"
  mockerp_allowed_cidrs = ["203.0.113.10/32"]
  mockerp_credentials = {
    credentials = [{ api_key = "fixture-key", api_secret = "fixture-secret", role = "admin", identity = "fixture" }]
  }
}

run "plugin_defaults_omitted" {
  command = plan
  assert {
    condition = (
      var.plugin_api_key == null && var.plugin_endpoint_allowlist == "" &&
      length(azurerm_container_app.erpbridge.secret) == 2 &&
      alltrue([for secret in azurerm_container_app.erpbridge.secret : !startswith(secret.name, "plugin-api-")]) &&
      alltrue([for env in azurerm_container_app.erpbridge.template[0].container[0].env :
        !contains(["PLUGIN_API_KEY", "PLUGIN_ENDPOINT_ALLOWLIST"], env.name)
      ])
    )
    error_message = "Default plugin configuration must create no plugin secret or environment entries."
  }
}

run "shared_plugin_configured" {
  command = plan
  variables {
    plugin_api_key            = "fixture-only-shared-plugin-key-1234567890"
    plugin_endpoint_allowlist = "plugin-one.example.invalid:443,plugin-two.example.invalid:443"
  }
  assert {
    condition = (
      length([for secret in azurerm_container_app.erpbridge.secret : secret
        if secret.name == "plugin-api-${substr(sha256(var.plugin_api_key), 0, 12)}" && secret.value == var.plugin_api_key
      ]) == 1 &&
      length([for env in azurerm_container_app.erpbridge.template[0].container[0].env : env
        if env.name == "PLUGIN_API_KEY" && env.secret_name == "plugin-api-${substr(sha256(var.plugin_api_key), 0, 12)}"
      ]) == 1 &&
      alltrue([for env in azurerm_container_app.erpbridge.template[0].container[0].env :
        env.name != "PLUGIN_API_KEY" || (env.value == null || env.value == "")
      ]) &&
      alltrue([for env in local.erpbridge_env : env.name != "PLUGIN_API_KEY"])
    )
    error_message = "The shared plugin key must use exactly its hashed ACA secret reference, never a plain environment value."
  }
  assert {
    condition = length([for env in azurerm_container_app.erpbridge.template[0].container[0].env : env
      if env.name == "PLUGIN_ENDPOINT_ALLOWLIST" && env.value == var.plugin_endpoint_allowlist && (env.secret_name == null || env.secret_name == "")
    ]) == 1
    error_message = "The configured plugin endpoint allowlist must be passed unchanged as a plain environment value."
  }
}

run "plugin_key_too_short" {
  command = plan
  variables { plugin_api_key = "fixture-too-short" }
  expect_failures = [var.plugin_api_key]
}

run "plugin_key_too_long" {
  command = plan
  variables { plugin_api_key = "${join("", [for index in range(1024) : "x"])}x" }
  expect_failures = [var.plugin_api_key]
}

run "plugin_key_minimum_length" {
  command = plan
  variables { plugin_api_key = join("", [for index in range(32) : "x"]) }
}

run "plugin_key_maximum_length" {
  command = plan
  variables { plugin_api_key = join("", [for index in range(1024) : "x"]) }
}
