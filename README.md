# ERPBridge demo infrastructure

> **POC teardown complete, 2026-10-21.** All four deployments, their seven
> managed DNS records, both VM OS disks, backup storage, and POC monitoring
> resources were deleted with explicit approval and no application backups.
> A later approved cleanup also deleted all four remaining resource groups,
> including the shared Monitor workspace and Network Watcher. The subscription
> now lists zero resource groups and zero ARM resources.
> Detailed teardown evidence is retained locally, outside version control.
> Deployment status notes below are historical. Start commands cannot recover
> deleted VMs; applying infrastructure again creates new resources and costs.

This repository configures ERPBridge and MockERP on Azure Container Apps,
and a separate on-demand Linux VM for real ERPNext. Both independent Terraform
roots look up the existing `erpbridge-hq` resource group without managing it.

For a restart or a fresh rebuild, use **[the reproduction runbook](docs/reproduce-deployment.md)**.
[The migration runbook](docs/erpbridge-migration-runbook.md) covers moving ERPBridge from Container Apps to the dedicated VM.

**ERPBridge monitoring deployed, 2026-10-18:** ten enabled Azure alert rules,
three-location HTTPS checks every 15 minutes, bounded guest/backup telemetry,
and managed boot diagnostics. The existing email action group is reused.
See [monitoring operations and acceptance](docs/erpbridge-monitoring.md).
Email delivery and the 24-hour observation are not yet proven. Kuma is paused.

For the real ERPNext viva presentation, follow **[docs/erpnext-viva.md](docs/erpnext-viva.md)**.
It covers realistic SCP data, initial admin credentials, guarded tool onboarding,
student-credit checks, and VM deallocation. The procedure below retains the
MockERP deployment path and must not be used to switch the active ERPNext target.

**Live test passed on 2026-10-15:** three HTTPS endpoints, two SCP seed runs,
Administrator session login, exact five-tool MCP discovery, and create/read/delete
with a separate client token. The VM was deallocated after that test, then
started again on request. Check `make erpnext-status` for its current state.
If needed, run `make erpnext-start CONFIRM=yes` and `make erpnext-ready`.
Private logins/tokens are in mode-0600 `artifacts/deployment-output.json`.
Backup/restore and an existing-AI-client rehearsal remain unverified.
See the viva runbook for the required standard ACA environment-mode workaround.

**Historical MCP set: 45 tools.** Approved additions cover master/order,
accounting, asset, assignment, and limited user-directory reads through
`bridgectl`. Existing Item
create/delete tools remain. No new business-write tools or credentials were
added. Asset and assignment datasets were empty, so their named gets have
error-probe coverage only. Combined scans also had intermittent MCP errors;
see [the active tool catalogue](docs/erpnext-tools.md) for exact coverage and
the deferred schema-fix and write plans.

The deployment uses these public names when Cloudflare DNS is enabled:

- ERPBridge: `https://erpbridge.nimendra.online`
- ERPBridge MCP: `https://erpbridge.nimendra.online/mcp/`
- MockERP: `https://mockerp.nimendra.online`
- ERPNext: `https://erpnext.nimendra.online`

This deployment is for short-lived experiments.
The two Container Apps use one replica and ephemeral SQLite storage.
ERPNext uses persistent Docker volumes on the VM's managed disk. Deallocate
the VM between sessions, but include disk/IP and retained ACA costs in the budget.

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

Create a Cloudflare API token with these permissions for the existing
`nimendra.online` zone:

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
read -rsp 'Policy Gate API key: ' TF_VAR_policy_gate_api_key
echo
export TF_VAR_policy_gate_api_key
read -rsp 'Data API key: ' TF_VAR_external_service_api_key
echo
export TF_VAR_external_service_api_key
read -rsp 'Resend API key: ' TF_VAR_resend_api_key
echo
export TF_VAR_resend_api_key
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

For `erp_target = "mockerp"`, Terraform derives `ERP_PRIMARY_KEY` from the first
MockERP credential. For `erp_target = "erpnext"`, it uses the separate protected
ERPNext integration token and URL. Never use the ERPNext Administrator credential
for normal MCP calls. Policy Gate, Data API, and Resend are optional: omit their
inputs if those services are not used. Keep already configured values unchanged.

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

The file contains canonical URLs, Azure fallback URLs, image references,
ingress values, and demo credentials. When ERPNext is selected, it also merges
VM metadata and the initial Administrator login from protected
`artifacts/erpnext-credentials.json`. Export fails if selected URLs or integration
credentials disagree. Cloudflare tokens, Azure credentials, and SSH private keys
are excluded.
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

## Onboard real ERPNext tools

After the approved VM bootstrap, SCP seed, and target configuration:

```bash
make onboard-erpnext CONFIRM=yes
make verify-erpnext
make verify-erpnext-write CONFIRM=yes
```

Only the last command creates/deletes a unique temporary sample Item.
The five curated tools are `list_companies`, `list_items`, `get_item`,
`create_item`, and `delete_item`. See the viva runbook before any apply or target
cutover. Do not leave old tools active with the newly selected credential.

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
