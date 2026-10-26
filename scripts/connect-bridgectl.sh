#!/usr/bin/env bash
# Source this file in Bash. It reads existing credentials; it never creates tokens.
# No secret values are stored in this script or passed as command arguments.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  printf 'Source this script in Bash: source scripts/connect-bridgectl.sh\n' >&2
  exit 2
fi

# Do not expose credentials when the caller has shell tracing enabled.
set +x

_erpbridge_connect_existing() {
  local root
  root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)" || return 1
  local -a credentials=()
  mapfile -t credentials < <(python3 - "$root/artifacts/deployment-output.json" <<'PY'
import json
import os
from pathlib import Path
import stat
import sys

try:
    path = Path(sys.argv[1])
    info = path.stat()
    if path.is_symlink() or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError('Unsafe artifact')
    deployment = json.loads(path.read_text())
    if deployment['deployment']['erp_target'] != 'erpnext':
        raise ValueError('Wrong target')
    expected = {
        'erpbridge': 'https://erpbridge.nimendra.online',
        'erpnext': 'https://erpnext.nimendra.online',
    }
    if any(not isinstance(deployment['urls'][key], str) or deployment['urls'][key].rstrip('/') != url
           for key, url in expected.items()):
        raise ValueError('Wrong endpoint')
    values = [deployment['demo_tokens'][key] for key in ('api_auth_token', 'erp_primary_key')]
    if any(not isinstance(value, str) or not value or any(c in value for c in '\r\n\0') for value in values):
        raise ValueError('Invalid credential')
except (OSError, ValueError, KeyError, TypeError):
    print('Cannot load the protected ERPNext deployment credentials. No values were printed.', file=sys.stderr)
    sys.exit(1)
for value in values:
    print(value)
PY
  )
  if [[ "${#credentials[@]}" -ne 2 ]]; then
    printf 'Connection settings were not changed. Check the protected deployment artifact.\n' >&2
    return 1
  fi

  # Use the saved context URLs, not old per-shell URL/authentication overrides.
  unset BRIDGE_SERVER BRIDGE_MCP_SERVER BRIDGE_ERP_BASE || return 1
  unset BRIDGE_AUTH_TYPE BRIDGE_AUTH_HEADER BRIDGE_API_KEY || return 1
  unset BRIDGE_TOKEN BRIDGE_USERNAME BRIDGE_PASSWORD || return 1
  export BRIDGE_CONTEXT=erpbridge-demo || return 1
  export BRIDGE_API_TOKEN="${credentials[0]}" || return 1
  export ERP_PRIMARY_KEY="${credentials[1]}" || return 1
  printf 'Credentials loaded for bridgectl context erpbridge-demo (ERPNext).\n'
}

if _erpbridge_connect_existing; then
  unset -f _erpbridge_connect_existing
else
  unset -f _erpbridge_connect_existing
  return 1
fi
