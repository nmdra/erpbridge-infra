#!/usr/bin/env bash
# Only operator-trusted backup bundles and separate pre-created disposable sites.
set -euo pipefail
umask 077
if [[ $# != 3 || "$1" != --confirm-disposable-restore || ! "$2" =~ ^restore-[a-z0-9][a-z0-9-]{0,48}$ || ! "$3" =~ ^backup-[a-f0-9]{24}$ ]]; then
    echo 'Usage: restore.sh --confirm-disposable-restore restore-NAME backup-IDENTIFIER' >&2
    exit 2
fi
cd /opt/erpnext-demo
log=$(mktemp)
trap 'rm -f "$log"' EXIT
if ! docker compose --env-file .env -f compose.yaml run --rm --no-deps -T recovery \
    restore --site "$2" --bundle "$3" --confirm-disposable-restore >"$log" 2>&1; then
    echo 'Restore failed. Inspect protected disposable target deliberately.' >&2
    exit 1
fi
echo 'Disposable restore complete. Verify encrypted credentials and records before use.'
