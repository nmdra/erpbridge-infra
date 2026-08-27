# Demo operations

## Deployment

Run Terraform from the repository root:

```bash
terraform -chdir=terraform init
terraform -chdir=terraform plan
terraform -chdir=terraform apply
```

Terraform creates these resources in `erpbridge-hq`:

- One Log Analytics workspace.
- One Consumption Container Apps environment.
- One ERPBridge Container App.
- One MockERP Container App.

Terraform does not create a resource group, Redis instance, or persistent
SQLite storage.

## Secret behavior

The three secret variables are marked `sensitive`:

- `api_auth_token`
- `erp_primary_key`
- `mockerp_credentials_json`

Terraform passes them to Container Apps as application secrets. Terraform state
contains their values for this short-lived demo. Keep state files outside Git.

Change `api_auth_token` and run `terraform apply`. Its hash changes the
ERPBridge revision suffix, so a new revision receives the new token. Set the
new value in `BRIDGE_API_TOKEN` before you use `bridgectl`.

## Outputs

Print the safe deployment summary:

```bash
terraform -chdir=terraform output
terraform -chdir=terraform output -json deployment_summary
```

The outputs include HTTPS URLs, application names, ports, replica counts,
resource group, location, HTTPS status, and Redis status. They do not include
secret values.

See [`bridgectl-configuration.md`](bridgectl-configuration.md) for the manual
configuration procedure.

## Ingress

ERPBridge uses HTTPS ingress on `erpbridge_mcp_port`.
Both ERPBridge probes use `/mcp/health` on that port.

MockERP uses HTTPS ingress on fixed port `8081`.
Both MockERP probes use `/health` on that port.

MockERP accepts traffic only from `mockerp_allowed_cidrs`.
The variable must contain at least one IPv4 CIDR and must not contain
`0.0.0.0/0`.

## Manual onboarding

Use the MockERP URL from Terraform output with Postman.
Then register the API manually:

```bash
export BRIDGE_API_TOKEN="$TF_VAR_api_auth_token"
bridgectl --context erpbridge-hq api register \
  --name mockerp \
  --url '<mockerp-url>' \
  --module demo \
  --description 'MockERP experiment API' \
  --credential-ref ERP_PRIMARY_KEY
```

`ERP_PRIMARY_KEY` is a logical reference. It is not the credential value.

## Teardown

Review the destroy plan before you remove the demo:

```bash
terraform -chdir=terraform plan -destroy
terraform -chdir=terraform destroy
rm -f terraform/terraform.tfstate terraform/terraform.tfstate.backup
```

Do not destroy `erpbridge-hq`.
