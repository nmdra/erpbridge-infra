#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TF_DIR="$ROOT_DIR/terraform"
ARTIFACT_FILE="${DEPLOYMENT_OUTPUT_FILE:-$ROOT_DIR/artifacts/deployment-output.json}"
ARTIFACT_DIR="$(dirname "$ARTIFACT_FILE")"
MCP_TOKEN_FILE="${MCP_TOKEN_FILE:-$ARTIFACT_DIR/mcp-tokens.json}"

command -v terraform >/dev/null || { echo "terraform is required" >&2; exit 1; }
command -v jq >/dev/null || { echo "jq is required" >&2; exit 1; }

umask 077
mkdir -p "$ARTIFACT_DIR"
tmp_file="$(mktemp "$ARTIFACT_DIR/.deployment-output.XXXXXX")"
trap 'rm -f "$tmp_file"' EXIT

# Explicit output reads are intentional: this is the opt-in demo credential export.
deployment_json="$(terraform -chdir="$TF_DIR" output -json deployment_summary)"
tokens_json="$(terraform -chdir="$TF_DIR" output -json demo_tokens)"
images_json="$(terraform -chdir="$TF_DIR" output -json image_references)"
domains_json="$(terraform -chdir="$TF_DIR" output -json cloudflare_domains)"
if [[ -f "$MCP_TOKEN_FILE" ]]; then
  jq empty "$MCP_TOKEN_FILE" >/dev/null || { echo "$MCP_TOKEN_FILE is not valid JSON" >&2; exit 1; }
  mcp_tokens_json="$(jq '.tokens // {}' "$MCP_TOKEN_FILE")"
else
  mcp_tokens_json='{}'
fi

jq -n \
  --arg generated_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --argjson deployment "$deployment_json" \
  --argjson tokens "$tokens_json" \
  --argjson images "$images_json" \
  --argjson domains "$domains_json" \
  --argjson mcp_tokens "$mcp_tokens_json" \
  '{
    schema_version: 1,
    generated_at: $generated_at,
    deployment: $deployment,
    urls: {
      erpbridge: $deployment.erpbridge.url,
      erpbridge_mcp: $deployment.erpbridge.mcp_url,
      mockerp: $deployment.mockerp.url,
      erpbridge_azure_fallback: $deployment.erpbridge.azure_url,
      mockerp_azure_fallback: $deployment.mockerp.azure_url
    },
    image_references: $images,
    custom_domains: $domains,
    demo_tokens: $tokens,
    mcp_tokens: $mcp_tokens
  }' > "$tmp_file"

chmod 600 "$tmp_file"
mv -f "$tmp_file" "$ARTIFACT_FILE"
trap - EXIT
chmod 600 "$ARTIFACT_FILE"
printf 'Deployment artifact written to %s\n' "$ARTIFACT_FILE"
