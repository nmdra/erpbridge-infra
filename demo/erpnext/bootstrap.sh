#!/usr/bin/env bash
set -euo pipefail
# Run locally from the repository root. The SSH host key must already be trusted.
exec python3 "$(dirname "$0")/bootstrap.py" "$@"
