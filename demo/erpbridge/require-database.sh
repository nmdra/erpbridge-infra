#!/bin/sh
# ERPBridge startup guard.
#
# Runs before the container on every start and on every boot. It only reads
# state. It never creates an empty registry, never overwrites the database, and
# never repairs anything automatically. Any unexpected condition blocks startup
# with a non-zero exit code so systemd refuses to start the service.
#
# Paths are overridable so the behaviour can be tested offline. The defaults are
# the production paths on the VM.
set -eu
umask 077

MOUNT_POINT="${ERPBRIDGE_MOUNT_POINT:-/var/lib/erpbridge}"
STATE_DIR="${ERPBRIDGE_STATE_DIR:-/var/lib/erpbridge/data}"
DB_PATH="${ERPBRIDGE_DB_PATH:-/var/lib/erpbridge/data/erpbridge.db}"
ENV_FILE="${ERPBRIDGE_ENV_FILE:-/etc/erpbridge/runtime.env}"
IDENTITY_FILE="${ERPBRIDGE_IDENTITY_FILE:-/etc/erpbridge/disk-identity}"
UID_FILE="${ERPBRIDGE_UID_FILE:-/etc/erpbridge/container-uid}"
EXPECTED_DB_IN_CONTAINER="/data/erpbridge.db"

fail() {
    printf 'ERPBridge startup blocked: %s\n' "$1" >&2
    exit 1
}

[ -d "$MOUNT_POINT" ] || fail "missing state directory $MOUNT_POINT"
[ -d "$STATE_DIR" ] || fail "missing data directory $STATE_DIR"

# The protected environment file carries the application settings. It is passed
# to Docker by path only; this guard never prints its values.
[ -f "$ENV_FILE" ] || fail "missing $ENV_FILE"
[ -s "$ENV_FILE" ] || fail "empty $ENV_FILE"
for key in API_AUTH_TOKEN ERP_PRIMARY_KEY DATABASE_PATH; do
    grep -q "^${key}=" "$ENV_FILE" || fail "$ENV_FILE has no ${key} setting"
done
grep -qx "DATABASE_PATH=${EXPECTED_DB_IN_CONTAINER}" "$ENV_FILE" \
    || fail "DATABASE_PATH must be ${EXPECTED_DB_IN_CONTAINER} on the VM"

# A reimaged or replaced OS disk must never be mistaken for the original one.
# The recorded filesystem UUID is written once at bootstrap.
[ -f "$IDENTITY_FILE" ] || fail "missing $IDENTITY_FILE"
current_fs="$(findmnt -no UUID --target "$MOUNT_POINT" 2>/dev/null || true)"
[ -n "$current_fs" ] || fail "cannot read the filesystem identity of $MOUNT_POINT"
expected_fs="$(sed -n 's/.*"filesystem_uuid"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$IDENTITY_FILE")"
[ -n "$expected_fs" ] || fail "recorded disk identity has no filesystem UUID"
[ "$current_fs" = "$expected_fs" ] \
    || fail "OS disk identity changed; refusing to treat this disk as the recorded one"

# The data directory must stay owned by the container's runtime user, otherwise
# the server cannot write its registry.
[ -f "$UID_FILE" ] || fail "missing $UID_FILE"
recorded_uid="$(tr -dc '0-9' <"$UID_FILE")"
[ -n "$recorded_uid" ] || fail "recorded container user id is not numeric"
owner_uid="$(stat -c %u "$STATE_DIR" 2>/dev/null || true)"
[ -n "$owner_uid" ] || fail "cannot read the owner of $STATE_DIR"
[ "$owner_uid" = "$recorded_uid" ] \
    || fail "data directory owner does not match the pinned image user"

# The database must exist and pass an integrity check. A missing database means
# the state was never restored or migrated; creating one here would silently
# start an empty registry.
[ -f "$DB_PATH" ] || fail "missing database file $DB_PATH"
[ -s "$DB_PATH" ] || fail "database file is empty"
command -v sqlite3 >/dev/null 2>&1 || fail "sqlite3 is required to verify the database"
if ! integrity="$(sqlite3 "$DB_PATH" 'PRAGMA quick_check;' 2>/dev/null)"; then
    fail "database integrity check could not run"
fi
[ "$integrity" = "ok" ] || fail "database integrity check failed"

printf 'ERPBridge startup guard passed: storage, identity, secrets, and database are ready.\n'
