# Configure bridgectl from Terraform outputs

Run this procedure after Terraform applies the Container Apps and custom
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
erpbridge.nimendra.xyz
erpbridge.nimendra.xyz/mcp/
mockerp.nimendra.xyz
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

The workflow uses the local OpenAPI source, selects the approved 60 tools,
validates each manifest, applies each tool, and confirms discovery.

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
| `mockerp_url` | MockERP API base URL |
| `BRIDGE_API_TOKEN` | Temporary admin credential for `bridgectl` |
| `ERP_PRIMARY_KEY` | Logical upstream credential reference |

## Security notes

- Keep `BRIDGE_API_TOKEN` outside the bridgectl context file.
- Do not print `demo_tokens` in a shared terminal.
- Keep Terraform state and the output artifact protected.
- Use DNS-only Cloudflare records for Azure certificate validation.
- Run `make verify-deployment` after every new deployment or revision change.
