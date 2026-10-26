#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TF_DIR="$ROOT_DIR/terraform"
SPEC="${MOCKERP_OPENAPI_SPEC:-$ROOT_DIR/../mockerp/openapi.yaml}"
APPROVED_NAMES="${MOCKERP_APPROVED_TOOLS:-$ROOT_DIR/config/mockerp-approved-tools.txt}"
CONTEXT="${BRIDGE_CONTEXT:-erpbridge-demo}"

command -v terraform >/dev/null || { echo "terraform is required" >&2; exit 1; }
command -v bridgectl >/dev/null || { echo "bridgectl is required" >&2; exit 1; }
command -v jq >/dev/null || { echo "jq is required" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
[[ -n "${BRIDGE_API_TOKEN:-}" ]] || { echo "BRIDGE_API_TOKEN must be set" >&2; exit 1; }
[[ -f "$SPEC" ]] || { echo "OpenAPI spec not found: $SPEC" >&2; exit 1; }
[[ -f "$APPROVED_NAMES" ]] || { echo "approved tool list not found: $APPROVED_NAMES" >&2; exit 1; }

selected_target="$(terraform -chdir="$TF_DIR" output -raw erp_target)"
[[ "$selected_target" == mockerp ]] || { echo 'MockERP onboarding refused: Terraform selected another ERP target.' >&2; exit 1; }
bridge_url="$(terraform -chdir="$TF_DIR" output -raw erpbridge_url)"
export BRIDGE_SERVER="$bridge_url" BRIDGE_MCP_SERVER="$bridge_url"
mockerp_url="$(terraform -chdir="$TF_DIR" output -raw mockerp_url)"
work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT
mkdir -p "$ROOT_DIR/artifacts"
output_dir="$ROOT_DIR/artifacts/mockerp-generated-manifests"
rm -rf "$output_dir"
mkdir -p "$output_dir"
chmod 700 "$output_dir"

python3 - "$SPEC" "$work_dir/mockerp-openapi.yaml" "${mockerp_url%/}" <<'PY'
from pathlib import Path
import sys
import yaml

source, destination, base_url = sys.argv[1:]
document = yaml.safe_load(Path(source).read_text(encoding="utf-8"))
servers = document.setdefault("servers", [{}])
servers[0]["url"] = base_url.rstrip("/") + "/api"
Path(destination).write_text(
    yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
)
PY
chmod 600 "$work_dir/mockerp-openapi.yaml"

bridgectl --context "$CONTEXT" api register \
  --force \
  --name mockerp \
  --url "${mockerp_url%/}/api/resource/Asset%20Category" \
  --method GET \
  --module mockerp \
  --description 'MockERP experiment API' \
  --auth-type api-key \
  --auth-header Authorization \
  --credential-ref ERP_PRIMARY_KEY \
  >/dev/null

bridgectl --context "$CONTEXT" tool generate \
  --api mockerp \
  --openapi "$work_dir/mockerp-openapi.yaml" \
  --output json > "$work_dir/generated.json"

approved_json="$(jq -Rsc 'split("\n") | map(select(length > 0))' "$APPROVED_NAMES")"
jq --argjson approved "$approved_json" \
  '[.[] | select(.metadata.name as $name | $approved | index($name))]' \
  "$work_dir/generated.json" > "$work_dir/selected.json"

expected_count="$(jq 'length' <<<"$approved_json")"
selected_count="$(jq 'length' "$work_dir/selected.json")"
if [[ "$selected_count" -ne "$expected_count" ]]; then
  echo "OpenAPI generation produced $selected_count of $expected_count approved tools" >&2
  jq --argjson approved "$approved_json" \
    '[.[] | .metadata.name] - $approved' "$work_dir/generated.json" >&2
  exit 1
fi

while IFS= read -r tool_json; do
  name="$(jq -r '.metadata.name' <<<"$tool_json")"
  manifest="$output_dir/$name.json"
  printf '%s\n' "$tool_json" > "$manifest"
  chmod 600 "$manifest"
  bridgectl --context "$CONTEXT" tool validate --file "$manifest" >/dev/null
done < <(jq -c '.[]' "$work_dir/selected.json")

bridgectl --context "$CONTEXT" tool apply --file "$output_dir" >/dev/null
listed_json="$(bridgectl --context "$CONTEXT" tool get --output json)"
jq -e --argjson approved "$approved_json" '
  (.tools // []) as $tools
  | ($tools | map(.metadata.name)) as $names
  | all($approved[]; . as $name | ($names | index($name)) != null)
' <<<"$listed_json" >/dev/null

printf 'Applied and verified %s MockERP tools from %s\n' "$selected_count" "$SPEC"
