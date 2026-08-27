# ERPBridge demo infrastructure

This private repository deploys one ERPBridge Container App and one MockERP
Container App in the existing `erpbridge-hq` resource group.

The design is for short-lived experiments. Terraform uses local state by
default. Secret values are marked sensitive, but Terraform state contains those
values. Do not commit state, plan files, or `terraform.tfvars`.

## Requirements

Install:

- Terraform 1.6 or later
- Azure CLI
- An Azure identity that can create resources in `erpbridge-hq`
- An immutable image digest for ERPBridge
- The MockERP `0.2.1` image digest

The AzureRM provider is pinned to `5.2.0`.

## Deploy

Log in to Azure and set the Terraform variables in the shell.

```bash
az login
export TF_VAR_subscription_id='00000000-0000-0000-0000-000000000000'
export TF_VAR_tenant_id='00000000-0000-0000-0000-000000000000'
export TF_VAR_deployment_location='centralindia'
export TF_VAR_erpbridge_image='ghcr.io/nmdra/erpbridge-server@sha256:<64-hex-digest>'
export TF_VAR_mockerp_image='ghcr.io/nmdra/mockerp@sha256:<64-hex-digest>'
export TF_VAR_mockerp_allowed_cidrs='["203.0.113.10/32"]'
export TF_VAR_api_auth_token="$(openssl rand -hex 32)"
read -r -s TF_VAR_erp_primary_key
export TF_VAR_erp_primary_key
read -r -s TF_VAR_mockerp_credentials_json
export TF_VAR_mockerp_credentials_json
```

Enter the ERPNext credential at the first prompt and the MockERP JSON at the
second prompt. Replace the example values before you run Terraform. Keep secret
values out of shell history and source control.

Initialize and apply the single Terraform root:

```bash
terraform -chdir=terraform init
terraform -chdir=terraform plan
terraform -chdir=terraform apply
```

Terraform creates the Log Analytics workspace, Container Apps environment, and
two HTTPS Container Apps. MockERP uses external ingress with the configured
Postman CIDR allow-list. Both applications use one replica and ephemeral
SQLite storage. Redis is not provisioned.

## Read the deployment output

Print the essential non-sensitive details:

```bash
terraform -chdir=terraform output
terraform -chdir=terraform output -json deployment_summary
```

The output includes the resource group, location, application names, HTTPS
URLs, MCP URL, ports, replica counts, HTTPS status, and Redis status. It does
not output secret values.

See [`docs/bridgectl-configuration.md`](docs/bridgectl-configuration.md) for
how to build a local `bridgectl` configuration from these outputs.

## Manual verification

Use the printed URLs with Postman. MockERP API calls must come from the
configured CIDR range and must use the seeded ERPNext credential.

Set the same bridge token for `bridgectl`:

```bash
export BRIDGE_API_TOKEN="$TF_VAR_api_auth_token"
bridgectl --context erpbridge-hq api list
```

Register MockERP manually:

```bash
bridgectl --context erpbridge-hq api register \
  --name mockerp \
  --url '<mockerp-url>' \
  --module demo \
  --description 'MockERP experiment API' \
  --credential-ref ERP_PRIMARY_KEY
```

The first deployment leaves `ERP_BASE_URL` unset. This avoids an unverified
ACA-to-MockERP source restriction. Run server-side probes only after you
confirm that the MockERP CIDR rules admit the ACA caller.

## Change the bridge token

Generate a new value and apply Terraform again:

```bash
export TF_VAR_api_auth_token="$(openssl rand -hex 32)"
export BRIDGE_API_TOKEN="$TF_VAR_api_auth_token"
terraform -chdir=terraform apply
```

The token hash changes the ERPBridge revision suffix. A new Container App
revision receives the new token. The old token stops working after the new
revision becomes active.

## Destroy the demo

Review the plan before you destroy resources:

```bash
terraform -chdir=terraform plan -destroy
terraform -chdir=terraform destroy
rm -f terraform/terraform.tfstate terraform/terraform.tfstate.backup
```

Do not destroy the existing resource group.
