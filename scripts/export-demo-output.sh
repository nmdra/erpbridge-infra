#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Overrides: TF_DIR, ERPNEXT_TF_DIR, TERRAFORM, DEPLOYMENT_OUTPUT_FILE,
# MCP_TOKEN_FILE, ERPNEXT_CREDENTIALS_FILE. Values are never passed as CLI secrets.
exec python3 "$ROOT_DIR/scripts/export-demo-output.py" "$@"
