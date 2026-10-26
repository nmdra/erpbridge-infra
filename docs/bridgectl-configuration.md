# Configure bridgectl from Terraform outputs

> **Historical POC documentation.** The recorded deployments were torn down in
> October 2026. Status, tool counts, approvals, and acceptance results below
> describe dated work, not current services or permission to deploy again.
> Check the versioned configuration and obtain fresh approval before cloud changes.

## Connect the existing ERPNext deployment

The installed CLI uses context `erpbridge-demo`. Its saved endpoints are
`https://erpbridge.nimendra.online` for the bridge and
`https://erpnext.nimendra.online` for ERPNext. The local evaluation context is
separate and remains unchanged.

From the repository root, load credentials into each Bash session:

```bash
source scripts/connect-bridgectl.sh
bridgectl context list
bridgectl tool get
make check-bridge-compatibility
```

The loader reads the existing mode-0600 `artifacts/deployment-output.json`.
It does not create credentials, register tools, or modify cloud resources.
It disables shell tracing and clears stale per-shell URL/upstream-auth
settings. The context stores environment references, not literal secrets.
Do not change to an unrelated context while these credentials are loaded.

For a fresh deployment, use [the reproduction runbook](reproduce-deployment.md).
**Do not run the MockERP registration steps below while ERPNext is selected.**

## Manual MockERP setup for a separately selected target

The following procedure is for MockERP, not the current ERPNext deployment.
Run it only after Terraform applies that target's Container Apps and custom
hostnames.

## 1. Set the admin token

Use the Terraform admin token in the current shell:

```bash
export BRIDGE_API_TOKEN="$TF_VAR_api_auth_token"
```

`BRIDGE_API_TOKEN` takes precedence over a token stored in a local context.
Do not save this value in `~/.bridgectl/config.yaml`.

## 2. Read the canonical URLs

Run:

```bash
ERPBRIDGE_URL="$(terraform -chdir=terraform output -raw erpbridge_url)"
ERPBRIDGE_MCP_URL="$(terraform -chdir=terraform output -raw erpbridge_mcp_url)"
MOCKERP_URL="$(terraform -chdir=terraform output -raw mockerp_url)"

printf 'ERPBridge: %s\n' "$ERPBRIDGE_URL"
printf 'ERPBridge MCP: %s\n' "$ERPBRIDGE_MCP_URL"
printf 'MockERP: %s\n' "$MOCKERP_URL"
```

When Cloudflare DNS is enabled, these values use:

```text
erpbridge.nimendra.online
erpbridge.nimendra.online/mcp/
mockerp.nimendra.online
```

The outputs use Azure hostnames only when custom domains are disabled.

## 3. Set shell URL overrides

Run:

```bash
export BRIDGE_SERVER="$ERPBRIDGE_URL"
export BRIDGE_MCP_SERVER="$ERPBRIDGE_URL"
export BRIDGE_ERP_BASE="$MOCKERP_URL"
```

Use the existing context:

```bash
BRIDGE_CONTEXT="${BRIDGE_CONTEXT:-erpbridge-demo}"
bridgectl --context "$BRIDGE_CONTEXT" api list
```

The context name does not store the current URL overrides.
The shell values take precedence for this command.

## 4. Register MockERP

Run:

```bash
bridgectl --context "$BRIDGE_CONTEXT" api register \
  --force \
  --name mockerp \
  --url "${MOCKERP_URL%/}/api/resource/Asset%20Category" \
  --method GET \
  --module mockerp \
  --description 'MockERP experiment API' \
  --auth-type api-key \
  --auth-header Authorization \
  --credential-ref ERP_PRIMARY_KEY
```

`ERP_PRIMARY_KEY` is a logical credential reference.
It is not the credential value.
ERPBridge resolves this reference inside the Container App.

## 5. Generate and apply tools

Run the deterministic onboarding workflow:

```bash
make onboard-mockerp
```

The MockERP workflow uses the local OpenAPI source and the explicit approved
name list. Do not assume the entire MockERP API is admitted. For real ERPNext,
use `make onboard-erpnext CONFIRM=yes` after the guarded target cutover; its
curated contract contains exactly the five Company/Item operations. Follow
[the ERPNext viva runbook](erpnext-viva.md) for URLs, credentials, and verification.

## 6. Verify the connection

Run:

```bash
make verify-deployment
```

The command checks both health endpoints, the API probe route, MCP session
creation, MockERP access, and the read-only `list_warehouses` tool.

## 7. Create a client token

Create a token for an MCP client:

```bash
./scripts/create-mcp-token.sh opencode
```

The script saves the token once in the ignored file:

```text
artifacts/mcp-tokens.json
```

ERPBridge does not return the raw token again.
Use the saved file to configure the client, and do not commit it.

## Context field mapping

| Value | Use |
| --- | --- |
| `erpbridge_url` | ERPBridge control-plane host |
| `erpbridge_mcp_url` | Streamable MCP endpoint |
| `mockerp_url` | MockERP API base URL, only for its selected target |
| `erpnext_url` | ERPNext API origin when `erp_target = "erpnext"` |
| `BRIDGE_API_TOKEN` | Temporary admin credential for `bridgectl` |
| `ERP_PRIMARY_KEY` | Logical upstream credential reference |

## Security notes

- Keep `BRIDGE_API_TOKEN` outside the bridgectl context file.
- Do not print `demo_tokens` in a shared terminal.
- Keep Terraform state and the output artifact protected.
- Use DNS-only Cloudflare records for Azure certificate validation.
- Run `make verify-deployment` after every new deployment or revision change.
