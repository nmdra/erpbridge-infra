#!/usr/bin/env bash
# Run on the VM. Backup data and full site config stay in the protected sites volume.
set -euo pipefail
umask 077
cd /opt/erpnext-demo
log=$(mktemp)
trap 'rm -f "$log"' EXIT
if ! docker compose --env-file .env -f compose.yaml run --rm --no-deps -T recovery backup >"$log" 2>&1; then
    echo 'Backup failed. Inspect protected VM state.' >&2
    exit 1
fi
# Only print the validated non-secret identifier, never arbitrary container logs.
selector=$(grep -Eo '^Protected backup: backup-[a-f0-9]{24}$' "$log")
[[ -n "$selector" ]] || exit 1
printf '%s\n' "$selector"
