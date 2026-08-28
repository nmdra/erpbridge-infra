#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 || -z "$1" ]]; then
  echo "usage: $0 <token-name>" >&2
  exit 2
fi

TOKEN_NAME="$1"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARTIFACT_DIR="${MCP_TOKEN_ARTIFACT_DIR:-$ROOT_DIR/artifacts}"
TOKEN_FILE="$ARTIFACT_DIR/mcp-tokens.json"
CONTEXT="${BRIDGE_CONTEXT:-erpbridge-demo}"

command -v bridgectl >/dev/null || { echo "bridgectl is required" >&2; exit 1; }
command -v jq >/dev/null || { echo "jq is required" >&2; exit 1; }
[[ -n "${BRIDGE_API_TOKEN:-}" ]] || {
  echo "BRIDGE_API_TOKEN must be set; the token value will not be read from or written to the command line" >&2
  exit 1
}

umask 077
mkdir -p "$ARTIFACT_DIR"
response_file="$(mktemp "$ARTIFACT_DIR/.mcp-token-response.XXXXXX")"
error_file="$(mktemp "$ARTIFACT_DIR/.mcp-token-error.XXXXXX")"
token_file="$(mktemp "$ARTIFACT_DIR/.mcp-token-value.XXXXXX")"
result_file="$(mktemp "$ARTIFACT_DIR/.mcp-tokens.XXXXXX")"
trap 'rm -f "$response_file" "$error_file" "$token_file" "$result_file"' EXIT
chmod 600 "$response_file" "$error_file" "$token_file" "$result_file"

if ! bridgectl --context "$CONTEXT" token create \
  --name "$TOKEN_NAME" \
  --scope mcp \
  --output json >"$response_file" 2>"$error_file"; then
  cat "$error_file" >&2
  exit 1
fi

token="$(jq -er '.token // empty' "$response_file")" || {
  echo "bridgectl did not return a one-time token" >&2
  exit 1
}
printf '%s' "$token" > "$token_file"

if [[ -f "$TOKEN_FILE" ]]; then
  jq empty "$TOKEN_FILE" >/dev/null || {
    echo "$TOKEN_FILE is not valid JSON" >&2
    exit 1
  }
else
  printf '%s\n' '{"schema_version":1,"tokens":{}}' > "$TOKEN_FILE"
  chmod 600 "$TOKEN_FILE"
fi

created_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
jq -n \
  --slurpfile previous "$TOKEN_FILE" \
  --slurpfile created "$response_file" \
  --rawfile token "$token_file" \
  --arg name "$TOKEN_NAME" \
  --arg updated_at "$created_at" \
  '($previous[0]) as $base
   | ($created[0]) as $item
   | $base + {
       schema_version: 1,
       updated_at: $updated_at,
       tokens: (($base.tokens // {}) + {
         ($name): {
           token: $token,
           id: ($item.id // null),
           name: ($item.name // $name),
           scopes: ($item.scopes // []),
           roles: ($item.roles // []),
           expires_at: ($item.expiresAt // null),
           created_at: ($item.createdAt // null)
         }
       })
     }' > "$result_file"

chmod 600 "$result_file"
mv -f "$result_file" "$TOKEN_FILE"
trap - EXIT
rm -f "$response_file" "$error_file" "$token_file"
chmod 600 "$TOKEN_FILE"
printf 'MCP token %s was saved to %s\n' "$TOKEN_NAME" "$TOKEN_FILE"
