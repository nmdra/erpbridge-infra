# Build bridgectl configuration from Terraform output

Use this procedure after Terraform creates both Container Apps.
The procedure reads only non-sensitive Terraform outputs.

## 1. Read the URLs

Run these commands from the infrastructure repository:

```bash
ERPBRIDGE_URL=$(terraform -chdir=terraform output -raw erpbridge_url)
ERPBRIDGE_MCP_URL=$(terraform -chdir=terraform output -raw erpbridge_mcp_url)
MOCKERP_URL=$(terraform -chdir=terraform output -raw mockerp_url)

printf 'ERPBridge: %s\n' "$ERPBRIDGE_URL"
printf 'ERPBridge MCP: %s\n' "$ERPBRIDGE_MCP_URL"
printf 'MockERP: %s\n' "$MOCKERP_URL"
```

The output fields map to the `bridgectl` context fields as follows:

| Terraform output | Context field | Use |
| --- | --- | --- |
| `erpbridge_url` | `server` | ERPBridge control-plane URL |
| `erpbridge_url` | `mcp-server` | ERPBridge control-plane root |
| `erpbridge_mcp_url` | MCP clients | ERPBridge streamable MCP URL |
| `mockerp_url` | `erp-base` | MockERP API base URL |

## 2. Use the URLs without writing a token

Set the URL overrides in the shell that runs `bridgectl`:

```bash
export BRIDGE_SERVER="$ERPBRIDGE_URL"
export BRIDGE_MCP_SERVER="$ERPBRIDGE_URL"
export BRIDGE_ERP_BASE="$MOCKERP_URL"
export BRIDGE_API_TOKEN="$TF_VAR_api_auth_token"
```

Use an existing context with these overrides:

```bash
bridgectl --context erpbridge-hq api list
```

`BRIDGE_API_TOKEN` takes precedence over a token in the local context.
Do not save this token in `~/.bridgectl/config.yaml`.

## 3. Register MockERP

Register the API with the public MockERP URL:

```bash
bridgectl --context erpbridge-hq api register \
  --name mockerp \
  --url "$MOCKERP_URL" \
  --module demo \
  --description 'MockERP experiment API' \
  --credential-ref ERP_PRIMARY_KEY
```

`ERP_PRIMARY_KEY` is a logical reference.
It is not the ERP credential value.

## Context field mapping

- `server` sends control-plane requests to ERPBridge.
- `mcp-server` is the ERPBridge host root used by CLI control-plane requests.
- MCP clients use `erpbridge_mcp_url`, which ends in `/mcp/`.
- `erp-base` supplies the default upstream ERP URL for API definitions.
- `api-token` is optional and is not used by this procedure.

## Security notes

- Terraform outputs do not contain API tokens or ERP credentials.
- The shell expands URLs from Terraform output at run time.
- The shell keeps the bridge token outside the context file.
- MockERP accepts requests only from the configured Postman CIDR range.
- Generate and apply tool manifests as separate reviewed actions.
