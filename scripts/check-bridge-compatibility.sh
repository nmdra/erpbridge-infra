#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TF_DIR="$ROOT_DIR/terraform"
CONTEXT="${BRIDGE_CONTEXT:-erpbridge-demo}"

command -v terraform >/dev/null || { echo "terraform is required" >&2; exit 1; }
command -v curl >/dev/null || { echo "curl is required" >&2; exit 1; }
command -v jq >/dev/null || { echo "jq is required" >&2; exit 1; }
command -v bridgectl >/dev/null || { echo "bridgectl is required" >&2; exit 1; }
[[ -n "${BRIDGE_API_TOKEN:-}" ]] || { echo "BRIDGE_API_TOKEN must be set" >&2; exit 1; }
bridgectl version >/dev/null 2>&1

bridge_url="$(terraform -chdir="$TF_DIR" output -raw erpbridge_url)"
mockerp_url="$(terraform -chdir="$TF_DIR" output -raw mockerp_url)"
headers=(-H "Authorization: Bearer ${BRIDGE_API_TOKEN}" -H 'Accept: application/json')

curl --fail --silent --show-error --max-time 20 "${headers[@]}" \
  "$bridge_url/mcp/health" >/dev/null

curl --fail --silent --show-error --max-time 20 "${headers[@]}" \
  "$bridge_url/apis/erpbridge.io/v1/tools" | jq -e 'type == "array"' >/dev/null

probe_body="$(jq -nc --arg url "${mockerp_url}/api/resource/Customer" '{
  url: $url,
  method: "GET",
  authType: "api-key",
  credentialRef: "ERP_PRIMARY_KEY",
  credentialSource: "env"
}')"
probe_response_file="$(mktemp)"
trap 'rm -f "$probe_response_file"' EXIT
probe_status="$(curl --silent --show-error --max-time 30 \
  -o "$probe_response_file" \
  -w '%{http_code}' \
  "${headers[@]}" \
  -H 'Content-Type: application/json' \
  -X POST "$bridge_url/api/apis/test" \
  --data "$probe_body")"

case "$probe_status" in
  2*)
    jq -e '.success == true' "$probe_response_file" >/dev/null
    ;;
  404|405)
    printf 'ERPBridge API probe route is unavailable; onboarding will use the local bridgectl probe fallback.\n'
    ;;
  *)
    echo "ERPBridge API probe returned HTTP $probe_status" >&2
    exit 1
    ;;
esac

rm -f "$probe_response_file"
trap - EXIT
printf 'ERPBridge and bridgectl compatibility checks passed for context %s\n' "$CONTEXT"
