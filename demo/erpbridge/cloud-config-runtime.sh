#!/bin/sh
# ERPBridge VM runtime bootstrap.
#
# Runs as root on the VM after the operator has uploaded these assets to
# /opt/erpbridge. It prepares storage and service wiring only. It never creates
# a database, never starts the server with an empty registry, and never writes,
# reads, or prints a credential value.
#
# Repeat-safe: a second run verifies the recorded state instead of replacing it,
# and refuses to change ownership of a populated data directory.
#
# Usage: cloud-config-runtime.sh --confirm --image IMAGE@sha256:...
set -eu
umask 077

ASSET_DIR="${ERPBRIDGE_ASSET_DIR:-/opt/erpbridge}"
STATE_ROOT="${ERPBRIDGE_STATE_ROOT:-/var/lib/erpbridge}"
DATA_DIR="${ERPBRIDGE_DATA_DIR:-/var/lib/erpbridge/data}"
CONF_DIR="${ERPBRIDGE_CONF_DIR:-/etc/erpbridge}"
ENV_FILE="${ERPBRIDGE_ENV_FILE:-/etc/erpbridge/runtime.env}"
SERVICE_ENV="${ERPBRIDGE_SERVICE_ENV:-/etc/erpbridge/service.env}"
IDENTITY_FILE="${ERPBRIDGE_IDENTITY_FILE:-/etc/erpbridge/disk-identity}"
UID_FILE="${ERPBRIDGE_UID_FILE:-/etc/erpbridge/container-uid}"
UNIT_TARGET="${ERPBRIDGE_UNIT_TARGET:-/etc/systemd/system/erpbridge.service}"
UNIT_DIR="${ERPBRIDGE_UNIT_DIR:-/etc/systemd/system}"
CADDY_TARGET="${ERPBRIDGE_CADDY_TARGET:-/etc/caddy/Caddyfile}"
MCP_PORT="${ERPBRIDGE_MCP_PORT:-8080}"
BACKUP_ENV="${ERPBRIDGE_BACKUP_ENV:-$CONF_DIR/backup.env}"
DOCKER="${DOCKER:-docker}"
SYSTEMCTL="${SYSTEMCTL:-systemctl}"
CADDY="${CADDY:-caddy}"

confirm=""
image=""
storage_endpoint=""
repair=""

usage() {
    printf 'Usage: %s --confirm --image IMAGE@sha256:<64 hex> --storage-endpoint URL\n' "${0##*/}" >&2
}

fail() {
    printf 'Bootstrap stopped: %s\n' "$1" >&2
    exit 1
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --confirm) confirm="yes" ;;
        --image) shift; image="${1:-}" ;;
        --storage-endpoint) shift; storage_endpoint="${1:-}" ;;
        --repair-ownership) repair="yes" ;;
        *) usage; exit 2 ;;
    esac
    shift
done

[ "$(id -u)" = "0" ] || fail "must run as root"
[ -n "$confirm" ] || fail "state changes require --confirm"
[ -n "$image" ] || fail "--image is required"
case "$image" in
    *@sha256:*) ;;
    *) fail "the image must be pinned by sha256 digest" ;;
esac
digest="${image##*@sha256:}"
printf '%s' "$digest" | grep -Eq '^[0-9a-f]{64}$' || fail "the image digest is malformed"

# The scheduled backup needs the deployment-specific storage endpoint. It is not
# a secret, but it must match the account Terraform created or every run fails.
[ -n "$storage_endpoint" ] || fail "--storage-endpoint is required; the timer would fail without it"
printf '%s' "$storage_endpoint" | grep -Eq '^https://[a-z0-9]{3,24}\.blob\.core\.windows\.net$' \
    || fail "the storage endpoint must be an https://<account>.blob.core.windows.net URL"

for asset in require-database.sh erpbridge.service Caddyfile Caddyfile.maintenance \
             Caddyfile.tls-check backup.py restore.py erpbridge-backup.service \
             erpbridge-backup.timer; do
    [ -f "$ASSET_DIR/$asset" ] || fail "missing asset $ASSET_DIR/$asset"
done
command -v "$DOCKER" >/dev/null 2>&1 || fail "docker is not installed"
command -v sqlite3 >/dev/null 2>&1 || fail "sqlite3 is required by the startup guard"

# 1. Restricted directories. The data directory is the bind-mount source for the
# container and holds the only registry copy on this host.
install -d -m 0700 -o root -g root "$CONF_DIR"
install -d -m 0755 -o root -g root "$STATE_ROOT"
# Ownership is set below, not here. `install -d -o` would re-own an existing
# populated directory and break the container's write access.
install -d -m 0750 "$DATA_DIR"

# 2. Resolve and record the container runtime user. The container image is
# non-root, so the bind-mounted directory must be writable by that identity.
if ! "$DOCKER" image inspect "$image" >/dev/null 2>&1; then
    "$DOCKER" pull "$image" >/dev/null || fail "could not pull the pinned image"
fi
ids="$("$DOCKER" run --rm --entrypoint /bin/sh "$image" -c 'id -u; id -g' 2>/dev/null)" \
    || fail "could not read the container runtime user"
container_uid="$(printf '%s' "$ids" | sed -n 1p | tr -dc '0-9')"
container_gid="$(printf '%s' "$ids" | sed -n 2p | tr -dc '0-9')"
[ -n "$container_uid" ] && [ -n "$container_gid" ] || fail "container user ids were not numeric"
[ "$container_uid" != "0" ] || fail "the container must not run as root"

if [ -e "$UID_FILE" ]; then
    recorded_uid="$(tr -dc '0-9' <"$UID_FILE")"
    [ "$recorded_uid" = "$container_uid" ] \
        || fail "the pinned image user changed; review before replacing the recorded value"
fi

# Populated directory: never silently change ownership of live data.
if [ -n "$(ls -A "$DATA_DIR" 2>/dev/null || true)" ]; then
    current_owner="$(stat -c %u "$DATA_DIR")"
    if [ "$current_owner" != "$container_uid" ]; then
        # Never silently re-own live data. The operator must ask for it.
        [ -n "$repair" ] \
            || fail "data directory is populated and owned by uid $current_owner, not $container_uid; rerun with --repair-ownership to correct only the directory"
        chown "$container_uid:$container_gid" "$DATA_DIR"
        printf 'Repaired the data directory ownership to uid %s. No file inside it was changed.\n' "$container_uid"
    fi
else
    chown "$container_uid:$container_gid" "$DATA_DIR"
fi
chmod 0750 "$DATA_DIR"
printf '%s\n' "$container_uid" >"$UID_FILE"
chmod 0600 "$UID_FILE"

# 3. Record the OS disk identity once. A reimaged or replaced disk has a
# different filesystem UUID, which the startup guard rejects.
current_fs="$(findmnt -no UUID --target "$STATE_ROOT" 2>/dev/null || true)"
[ -n "$current_fs" ] || fail "cannot read the filesystem identity of $STATE_ROOT"
if [ -e "$IDENTITY_FILE" ]; then
    recorded_fs="$(sed -n 's/.*"filesystem_uuid"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$IDENTITY_FILE")"
    [ "$recorded_fs" = "$current_fs" ] \
        || fail "OS disk identity changed; do not continue with this disk"
else
    vm_name="$(hostname)"
    printf '{\n  "schema_version": 1,\n  "filesystem_uuid": "%s",\n  "vm_name": "%s"\n}\n' \
        "$current_fs" "$vm_name" >"$IDENTITY_FILE"
    chmod 0600 "$IDENTITY_FILE"
fi

# 4. Non-secret service wiring. The protected runtime env file is installed
# separately by the operator and is never created here.
# Non-secret backup wiring. Rewritten on every bootstrap because it is derived
# from the applied root, not entered by an operator.
printf 'ERPBRIDGE_STORAGE_ENDPOINT=%s\n' "$storage_endpoint" >"$BACKUP_ENV"
chmod 0600 "$BACKUP_ENV"
chown root:root "$BACKUP_ENV"

if [ -e "$SERVICE_ENV" ]; then
    recorded_image="$(sed -n 's/^ERPBRIDGE_IMAGE=//p' "$SERVICE_ENV")"
    [ "$recorded_image" = "$image" ] \
        || fail "service wiring pins a different image; change it deliberately"
else
    printf 'ERPBRIDGE_IMAGE=%s\nERPBRIDGE_DATA_DIR=%s\nERPBRIDGE_MCP_PORT=%s\n' \
        "$image" "$DATA_DIR" "$MCP_PORT" >"$SERVICE_ENV"
fi
chmod 0600 "$SERVICE_ENV"
chown root:root "$SERVICE_ENV"

# 5. Install the guard, unit, reverse-proxy configuration, and database tools.
# The maintenance configuration is active until an operator promotes the serving
# one. The database tools run as root because they read the registry and use the
# VM managed identity; only root may read or execute them.
install -m 0755 -o root -g root "$ASSET_DIR/require-database.sh" "$ASSET_DIR/require-database.sh.new"
mv -f "$ASSET_DIR/require-database.sh.new" "$ASSET_DIR/require-database.sh"
for tool in backup.py restore.py; do
    install -m 0700 -o root -g root "$ASSET_DIR/$tool" "$ASSET_DIR/$tool.new"
    mv -f "$ASSET_DIR/$tool.new" "$ASSET_DIR/$tool"
done
install -m 0644 -o root -g root "$ASSET_DIR/erpbridge.service" "$UNIT_TARGET"
install -m 0644 -o root -g root "$ASSET_DIR/erpbridge-backup.service" \
    "$UNIT_DIR/erpbridge-backup.service"
install -m 0644 -o root -g root "$ASSET_DIR/erpbridge-backup.timer" \
    "$UNIT_DIR/erpbridge-backup.timer"
install -m 0644 -o root -g root "$ASSET_DIR/Caddyfile" "$ASSET_DIR/Caddyfile.serving"
# The temporary TLS-check configuration stays where it was uploaded. The
# operator-side certificate step reads it from here, activates it before
# cutover, and the serving file replaces it at cutover. Nothing activates it
# automatically, so only its mode is made explicit.
chmod 0644 "$ASSET_DIR/Caddyfile.tls-check"
install -m 0644 -o root -g root "$ASSET_DIR/Caddyfile.maintenance" "$CADDY_TARGET"

"$CADDY" validate --config "$CADDY_TARGET" --adapter caddyfile >/dev/null \
    || fail "the maintenance reverse-proxy configuration did not validate"

"$SYSTEMCTL" daemon-reload
# Enabled only. Starting requires a restored database and an explicit decision.
"$SYSTEMCTL" enable erpbridge.service >/dev/null
"$SYSTEMCTL" enable --now caddy.service >/dev/null
# The daily backup timer is safe to enable now: it only reads the database and
# uploads a verified copy. Restore is never scheduled and never runs at boot.
"$SYSTEMCTL" enable --now erpbridge-backup.timer >/dev/null

if [ -e "$ENV_FILE" ]; then
    printf 'Bootstrap complete. Runtime env present; the server stays stopped until an explicit start.\n'
else
    printf 'Bootstrap complete. No runtime env yet: install it, then restore, then start.\n'
fi
