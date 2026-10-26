#!/bin/sh
set -eu
umask 077
DB="${DATABASE_PATH:-/tmp/erpbridge.db}"
if [ ! -s "$DB" ]; then
    printf '%s\n' 'Waiting for an authorized database restore before starting ERPBridge.' >&2
fi
while [ ! -s "$DB" ]; do
    sleep 1
done
exec /app/erpbridge-server
