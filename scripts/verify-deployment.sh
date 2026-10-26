#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TF_DIR="$ROOT_DIR/terraform"

command -v terraform >/dev/null || { echo "terraform is required" >&2; exit 1; }
command -v curl >/dev/null || { echo "curl is required" >&2; exit 1; }
command -v jq >/dev/null || { echo "jq is required" >&2; exit 1; }
command -v getent >/dev/null || { echo "getent is required" >&2; exit 1; }
[[ -n "${BRIDGE_API_TOKEN:-}" ]] || { echo "BRIDGE_API_TOKEN must be set" >&2; exit 1; }

bridge_url="$(terraform -chdir="$TF_DIR" output -raw erpbridge_url)"
mcp_url="$(terraform -chdir="$TF_DIR" output -raw erpbridge_mcp_url)"
selected_target="$(terraform -chdir="$TF_DIR" output -raw erp_target)"

# Refuse to verify a bridge that is fenced or hosted elsewhere. A fenced
# Container App returns no service, and verifying the old Azure endpoint would
# prove nothing about the deployment that actually serves clients.
hosting_state="$(terraform -chdir="$TF_DIR" output -json erpbridge_hosting)"
hosting_mode="$(jq -r '.hosting' <<<"$hosting_state")"
source_fenced="$(jq -r '.source_fenced' <<<"$hosting_state")"
if [[ "$hosting_mode" == vm ]]; then
  echo "ERPBridge hosting is vm. Verify the VM with scripts/erpbridge-vm.py status and ready," \
       "not this Container App script." >&2
  exit 1
fi
if [[ "$source_fenced" == true ]]; then
  echo "The ERPBridge Container App is fenced and serves nothing. Nothing can be verified" \
       "until the fence is lifted or hosting is moved to the VM." >&2
  exit 1
fi
if [[ "$selected_target" == erpnext ]]; then
  "$ROOT_DIR/scripts/check-bridge-compatibility.sh"
  exec python3 "$ROOT_DIR/scripts/verify-erpnext-demo.py" "$@"
fi
[[ "$selected_target" == mockerp ]] || { echo "Unsupported ERP target" >&2; exit 1; }
mockerp_url="$(terraform -chdir="$TF_DIR" output -raw mockerp_url)"

if [[ -n "${ERP_PRIMARY_KEY:-}" ]]; then
  erp_primary_key="$ERP_PRIMARY_KEY"
else
  erp_primary_key="$(terraform -chdir="$TF_DIR" output -json demo_tokens | jq -er '.erp_primary_key')"
fi

bridge_host="${bridge_url#https://}"
bridge_host="${bridge_host%%/*}"
mockerp_host="${mockerp_url#https://}"
mockerp_host="${mockerp_host%%/*}"
getent ahostsv4 "$bridge_host" >/dev/null
getent ahostsv4 "$mockerp_host" >/dev/null

curl --fail --silent --show-error --max-time 20 "$bridge_url/mcp/health" >/dev/null
curl --fail --silent --show-error --max-time 20 "$mockerp_url/health" >/dev/null

curl --fail --silent --show-error --max-time 30 \
  --config <(printf 'header = "Authorization: %s"\n' "$erp_primary_key") \
  -H 'Accept: application/json' \
  "$mockerp_url/api/resource/Customer" | jq -e 'type == "object" and has("data")' >/dev/null

"$ROOT_DIR/scripts/check-bridge-compatibility.sh"

work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT
headers=(-H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Mcp-Protocol-Version: 2025-06-18')

curl --fail --silent --show-error --max-time 20 \
  -D "$work_dir/initialize.headers" \
  -o "$work_dir/initialize.body" \
  -X POST "$mcp_url" \
  --config <(printf 'header = "Authorization: Bearer %s"\n' "$BRIDGE_API_TOKEN") "${headers[@]}" \
  --data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"deployment-verify","version":"1"}}}'

session_id="$(awk 'BEGIN {IGNORECASE=1} /^Mcp-Session-Id:/ {gsub("\r", "", $2); print $2}' "$work_dir/initialize.headers")"
[[ -n "$session_id" ]] || { echo "MCP initialize did not return a session ID" >&2; exit 1; }

curl --fail --silent --show-error --max-time 20 \
  -o /dev/null \
  -X POST "$mcp_url" \
  --config <(printf 'header = "Authorization: Bearer %s"\n' "$BRIDGE_API_TOKEN") "${headers[@]}" \
  -H "Mcp-Session-Id: $session_id" \
  --data '{"jsonrpc":"2.0","method":"notifications/initialized"}'

curl --fail --silent --show-error --max-time 30 \
  -o "$work_dir/call.body" \
  -X POST "$mcp_url" \
  --config <(printf 'header = "Authorization: Bearer %s"\n' "$BRIDGE_API_TOKEN") "${headers[@]}" \
  -H "Mcp-Session-Id: $session_id" \
  --data '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"list_warehouses","arguments":{}}}'
jq -e '[.. | objects | select(.isError == true)] | length == 0' "$work_dir/call.body" >/dev/null

printf 'deployment verification passed for %s and %s\n' "$bridge_url" "$mockerp_url"
