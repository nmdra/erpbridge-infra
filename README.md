# ERPBridge demo infrastructure

This repository deploys ERPBridge and MockERP to Azure Container Apps.
It uses the existing `erpbridge-hq` resource group.
Terraform does not create or delete that resource group.

The deployment uses these public names when Cloudflare DNS is enabled:

- ERPBridge: `https://erpbridge.nimendra.xyz`
- ERPBridge MCP: `https://erpbridge.nimendra.xyz/mcp/`
- MockERP: `https://mockerp.nimendra.xyz`

This deployment is for short-lived experiments.
Both applications use one replica and ephemeral SQLite storage.

## Requirements

Install these tools:

- Terraform 1.9 or later
- Azure CLI
- Cloudflare API token
- `curl`
- `jq`
- `python3`
- `bridgectl`

Use immutable `image@sha256:<64-hex-digest>` references.
The AzureRM provider is pinned to `5.2.0`.
The AzAPI provider manages the Azure managed-certificate binding.

## Authenticate

Log in to Azure:

```bash
az login
az account set --subscription '<subscription-id>'
```

Create a Cloudflare API token with these permissions for the `nimendra.xyz`
zone:

- `Zone - DNS - Edit`
- `Zone - Zone - Read`

Set the token in the shell that runs Terraform:

```bash
read -rsp 'Cloudflare API token: ' CLOUDFLARE_API_TOKEN
export CLOUDFLARE_API_TOKEN
echo
```

Do not put this token in `terraform.tfvars`.
The Cloudflare provider reads `CLOUDFLARE_API_TOKEN` automatically.

Register these Azure resource providers before the first apply:

```bash
az provider register --namespace Microsoft.App
az provider register --namespace Microsoft.OperationalInsights
```

Wait until both providers show `Registered`.

## Configure

Copy the example file:

```bash
cp terraform/terraform.tfvars.example terraform/terraform.tfvars
```

Set your subscription, tenant, immutable image digests, and client CIDR.
The example contains the Cloudflare settings for a reusable deployment.
Set secret values through `TF_VAR_*` environment variables:

```bash
export TF_VAR_subscription_id='<subscription-id>'
export TF_VAR_tenant_id='<tenant-id>'
export TF_VAR_api_auth_token="$(openssl rand -hex 32)"
read -rsp 'MockERP API key: ' MOCKERP_API_KEY
echo
read -rsp 'MockERP API secret: ' MOCKERP_API_SECRET
echo
export TF_VAR_mockerp_credentials="$(jq -n \
  --arg api_key "$MOCKERP_API_KEY" \
  --arg api_secret "$MOCKERP_API_SECRET" \
  '{credentials: [{api_key: $api_key, api_secret: $api_secret, role: "admin", identity: "demo"}]}')"
unset MOCKERP_API_KEY MOCKERP_API_SECRET
```

Terraform derives the ERPBridge `ERP_PRIMARY_KEY` value from the first
MockERP credential as `token <api_key>:<api_secret>`.

For the temporary public demo, set these values in the local ignored
`terraform/terraform.tfvars` file:

```hcl
mockerp_allowed_cidrs     = ["0.0.0.0/0"]
mockerp_allow_all_ingress = true
```

Do not use this setting for a production deployment.
Use the public `/32` CIDR of the Postman client instead.

## Deploy

Run the local preflight:

```bash
make preflight
```

Create and review the plan:

```bash
make plan
```

Apply the plan:

```bash
make apply
```

Certificate issuance can take several minutes.
The apply creates two Cloudflare CNAME records and two `asuid` TXT records.
The CNAME records use DNS-only mode.
The AzAPI update binds each Azure managed certificate with SNI.

## Verify

Run the complete deployment verification:

```bash
make verify-deployment
```

This command checks:

- DNS resolution for both custom names
- HTTPS health for ERPBridge and MockERP
- Authenticated MockERP resource access
- ERPBridge control-plane routes
- The ERPBridge API probe route, or the documented local fallback
- MCP initialization and session creation
- A read-only `list_warehouses` tool call

Run the compatibility check alone with:

```bash
make check-bridge-compatibility
```

## Save deployment values

`make apply` writes this protected file:

```text
artifacts/deployment-output.json
```

The file contains the canonical URLs, Azure fallback URLs, image references,
ingress values, and demo Terraform credentials.
The file has mode `0600` and is ignored by Git.

Write the file again with:

```bash
make export-output
```

Do not print sensitive Terraform outputs in a shared terminal.
Terraform state and state backups also contain secret values.
Use encrypted, access-controlled state for shared work.

## Create MCP client tokens

Set the ERPBridge admin token without saving it in the bridgectl context:

```bash
export BRIDGE_API_TOKEN="$TF_VAR_api_auth_token"
```

Create and save an MCP token one time:

```bash
./scripts/create-mcp-token.sh opencode
```

The script saves the token in the ignored, mode-`0600`
`artifacts/mcp-tokens.json` file.
ERPBridge does not return a raw token after creation.

## Configure bridgectl

Use the canonical URLs from Terraform:

```bash
export BRIDGE_SERVER="$(terraform -chdir=terraform output -raw erpbridge_url)"
export BRIDGE_MCP_SERVER="$BRIDGE_SERVER"
export BRIDGE_ERP_BASE="$(terraform -chdir=terraform output -raw mockerp_url)"
export BRIDGE_API_TOKEN="$TF_VAR_api_auth_token"
```

Use the existing context with these environment values:

```bash
BRIDGE_CONTEXT="${BRIDGE_CONTEXT:-erpbridge-demo}"
bridgectl --context "$BRIDGE_CONTEXT" api list
```

Do not save `BRIDGE_API_TOKEN` in `~/.bridgectl/config.yaml`.
See [`docs/bridgectl-configuration.md`](docs/bridgectl-configuration.md).

## Onboard MockERP tools

Generate and apply the approved MockERP tools from the OpenAPI document:

```bash
make onboard-mockerp
```

The script uses the canonical MockERP URL and the local OpenAPI source at
`../mockerp/openapi.yaml` by default.
Override the source with `MOCKERP_OPENAPI_SPEC`.
Generated manifests are stored under the ignored `artifacts/` directory.
The old manifests with Azure default hostnames are not authoritative.

## Persistence and teardown

The applications use `EmptyDir` volumes.
SQLite data can disappear during revision replacement or rescheduling.
`terraform destroy` also removes the Container Apps environment and its data.
Destroying the environment removes the ERPBridge registry, MCP tokens, and
published tools.

Recreate the demo with this sequence:

```bash
make apply
make verify-deployment
make export-output
export BRIDGE_API_TOKEN="$TF_VAR_api_auth_token"
make onboard-mockerp
```

Review the destroy plan before teardown:

```bash
make destroy-plan
make destroy
```

Do not destroy `erpbridge-hq`.
