#!/usr/bin/env python3
"""Explicit ERPBridge registry restore.

Runs on the VM host, only when an operator asks for it. It never runs at boot
and it never runs on a schedule.

Safety rules, in order:

1. `--confirm` is required. Without it nothing is read or written.
2. The ERPBridge service must be stopped, otherwise the database may be held
   open by the container.
3. The filesystem identity must match the value recorded at bootstrap, so a
   replaced OS disk cannot receive a restore by accident.
4. The download is verified by SHA-256 and by `PRAGMA integrity_check` before
   anything is installed.
5. Existing SQLite sidecar files (`-wal`, `-shm`, `-journal`) are moved aside
   first. Installing only the main file next to a stale write-ahead log can make
   SQLite replay an incompatible log over the restored data.
6. The verified file is installed with an atomic rename on the same filesystem,
   then owned and permissioned for the container user.
7. The installed database is reopened once and checked again, and the table
   counts are compared with the recorded manifest when one is supplied.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SIDECARS = ("-wal", "-shm", "-journal")
# A restore may only proceed when the service is definitively not running.
SAFE_STATES = {"inactive", "failed"}
DEFAULT_TARGET = "/var/lib/erpbridge/data/erpbridge.db"
DEFAULT_STAGING = "/var/lib/erpbridge/staging"
DEFAULT_IDENTITY = "/etc/erpbridge/disk-identity"
DEFAULT_UID_FILE = "/etc/erpbridge/container-uid"
DEFAULT_MOUNT = "/var/lib/erpbridge"
DEFAULT_IMDS = "http://169.254.169.254"
DEFAULT_STORAGE = "https://erpbridgebackups.blob.core.windows.net"
DEFAULT_CONTAINER = "erpbridge-backups"
STORAGE_RESOURCE = "https://storage.azure.com/"
BLOB_API_VERSION = "2021-08-06"
OBJECT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,200}$")


class SafeError(Exception):
    """Failure that is safe to print: no provider body, no credential value."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError:
        raise SafeError("The downloaded file could not be read for hashing.") from None
    return digest.hexdigest()


def service_state(systemctl: str) -> str:
    try:
        result = subprocess.run([systemctl, "is-active", "erpbridge.service"],
                                capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise SafeError("Service state could not be read; refusing to restore.") from None
    return result.stdout.strip() or "unknown"


def recorded_filesystem_uuid(identity_file: Path) -> str | None:
    if not identity_file.exists():
        return None
    try:
        payload = json.loads(identity_file.read_text())
    except (OSError, ValueError):
        raise SafeError("The recorded disk identity could not be read.") from None
    value = payload.get("filesystem_uuid")
    return value if isinstance(value, str) and value else None


def current_filesystem_uuid(mount_point: Path, findmnt: str) -> str:
    try:
        result = subprocess.run([findmnt, "-no", "UUID", "--target", str(mount_point)],
                                capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise SafeError("The filesystem identity could not be read.") from None
    value = result.stdout.strip()
    if not value:
        raise SafeError("The filesystem reports no UUID; refusing to restore.")
    return value


def access_token(imds: str, timeout: float) -> str:
    query = urllib.parse.urlencode({"api-version": "2018-02-01", "resource": STORAGE_RESOURCE})
    request = urllib.request.Request(f"{imds}/metadata/identity/oauth2/token?{query}",
                                     headers={"Metadata": "true"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read())
    except (urllib.error.URLError, ValueError, OSError):
        raise SafeError("The VM managed identity token could not be obtained.") from None
    token = payload.get("access_token")
    if not isinstance(token, str) or not token:
        raise SafeError("The managed identity response contained no access token.")
    return token


def download(storage: str, container: str, name: str, destination: Path, token: str,
             timeout: float) -> None:
    url = f"{storage.rstrip('/')}/{container}/{urllib.parse.quote(name)}"
    request = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "x-ms-version": BLOB_API_VERSION,
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as error:
        raise SafeError(f"The backup download was rejected with status {error.code}.") from None
    except (urllib.error.URLError, OSError):
        raise SafeError("The backup download did not complete.") from None
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with os.fdopen(os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as stream:
        stream.write(body)


def table_counts(path: Path) -> dict:
    try:
        connection = sqlite3.connect(f"{path}", timeout=30)
    except sqlite3.Error:
        raise SafeError("The restored database could not be opened.") from None
    try:
        check = connection.execute("PRAGMA integrity_check").fetchone()
        if not check or check[0] != "ok":
            raise SafeError("The restored database failed its integrity check.")
        tables = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
        counts = {}
        for table in tables:
            counts[table] = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        return counts
    except sqlite3.Error:
        raise SafeError("The restored database could not be read.") from None
    finally:
        connection.close()


def move_sidecars_aside(target: Path, stamp: str) -> list[str]:
    """Move any existing sidecar files out of the way before installing."""
    moved = []
    for suffix in SIDECARS:
        sidecar = Path(f"{target}{suffix}")
        if sidecar.exists():
            superseded = sidecar.with_name(f"{sidecar.name}.superseded-{stamp}")
            os.replace(sidecar, superseded)
            moved.append(superseded.name)
    return moved


def install(target: Path, staged: Path, uid_file: Path) -> dict:
    owner = None
    if uid_file.exists():
        recorded = uid_file.read_text().strip()
        if recorded.isdigit():
            owner = int(recorded)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    os.replace(staged, target)
    os.chmod(target, 0o640)
    if owner is not None and os.geteuid() == 0:
        os.chown(target, owner, -1)
    return {"owner": owner}


def run(args) -> int:
    if not args.confirm:
        raise SafeError("Restore requires --confirm; nothing was read or changed.")
    if not OBJECT_NAME.fullmatch(args.backup_id):
        raise SafeError("The backup identifier is not a usable object name.")
    target = Path(args.target_db)
    staging = Path(args.staging_dir)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    state = service_state(args.systemctl)
    if state == "active":
        raise SafeError("The ERPBridge service is running. Stop it before restoring.")
    if state not in SAFE_STATES:
        # Only a definitively stopped service may be replaced. Anything else,
        # including an unreadable or transitional state, is treated as unsafe.
        raise SafeError(f"The ERPBridge service state is '{state}'; refusing to restore.")

    expected_fs = recorded_filesystem_uuid(Path(args.identity_file))
    if expected_fs is not None:
        observed_fs = current_filesystem_uuid(Path(args.mount_point), args.findmnt)
        if observed_fs != expected_fs:
            raise SafeError("The filesystem identity does not match the recorded disk.")

    staging.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(staging, 0o700)
    if target.parent.stat().st_dev != staging.stat().st_dev:
        raise SafeError("Staging and the target must share one filesystem for an atomic install.")

    staged = staging / f"restore-{args.backup_id}"
    if args.source_file:
        source = Path(args.source_file)
        if not source.is_file():
            raise SafeError("The local restore source does not exist.")
        staged.write_bytes(source.read_bytes())
        os.chmod(staged, 0o600)
    else:
        token = access_token(args.imds_endpoint, args.timeout)
        download(args.storage_endpoint, args.container, args.backup_id, staged, token, args.timeout)

    staged_sha = sha256_file(staged)
    if args.expect_sha256 and staged_sha != args.expect_sha256.lower():
        staged.unlink(missing_ok=True)
        raise SafeError("The restored file does not match the expected checksum.")

    counts = table_counts(staged)

    expected_manifest = None
    if args.expect_manifest:
        try:
            expected_manifest = json.loads(Path(args.expect_manifest).read_text())
        except (OSError, ValueError):
            raise SafeError("The expected manifest could not be read.") from None
        if expected_manifest != counts:
            raise SafeError("The restored table counts differ from the expected manifest.")

    moved = move_sidecars_aside(target, stamp)
    details = install(target, staged, Path(args.uid_file))
    # Reopen once so SQLite settles the journal, then verify the installed file.
    installed_counts = table_counts(target)
    if installed_counts != counts:
        raise SafeError("The installed database does not match the verified snapshot.")
    leftovers = [f"{target.name}{suffix}" for suffix in SIDECARS
                 if Path(f"{target}{suffix}").exists()]

    print(f"Restored {args.backup_id} to {target}.")
    print(f"Verified: sha256 {staged_sha[:16]}..., {target.stat().st_size} bytes.")
    print("Tables: " + ", ".join(f"{table}={count}" for table, count in sorted(counts.items())))
    if details["owner"] is not None:
        print(f"Ownership set to uid {details['owner']} with mode 0640.")
    if moved:
        print("Previous sidecar files moved aside: " + ", ".join(moved))
    if leftovers:
        print("Sidecar files present after the restore: " + ", ".join(leftovers))
    else:
        print("No stale sidecar files remain beside the restored database.")
    print("Start the service explicitly when you are ready.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Explicit ERPBridge registry restore.")
    parser.add_argument("--backup-id", required=True)
    parser.add_argument("--target-db", default=os.environ.get("ERPBRIDGE_DB_PATH", DEFAULT_TARGET))
    parser.add_argument("--staging-dir", default=os.environ.get("ERPBRIDGE_STAGING_DIR",
                                                               DEFAULT_STAGING))
    parser.add_argument("--identity-file", default=os.environ.get("ERPBRIDGE_IDENTITY_FILE",
                                                                 DEFAULT_IDENTITY))
    parser.add_argument("--uid-file", default=os.environ.get("ERPBRIDGE_UID_FILE", DEFAULT_UID_FILE))
    parser.add_argument("--mount-point", default=os.environ.get("ERPBRIDGE_MOUNT_POINT",
                                                               DEFAULT_MOUNT))
    parser.add_argument("--imds-endpoint", default=os.environ.get("ERPBRIDGE_IMDS_ENDPOINT",
                                                                 DEFAULT_IMDS))
    parser.add_argument("--storage-endpoint", default=os.environ.get("ERPBRIDGE_STORAGE_ENDPOINT",
                                                                    DEFAULT_STORAGE))
    parser.add_argument("--container", default=os.environ.get("ERPBRIDGE_BACKUP_CONTAINER",
                                                              DEFAULT_CONTAINER))
    parser.add_argument("--source-file", help="Local rehearsal source instead of a download.")
    parser.add_argument("--expect-sha256", help="Required checksum for the restored file.")
    parser.add_argument("--expect-manifest", help="JSON file of expected table counts.")
    parser.add_argument("--systemctl", default=os.environ.get("SYSTEMCTL", "systemctl"))
    parser.add_argument("--findmnt", default=os.environ.get("FINDMNT", "findmnt"))
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--confirm", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.timeout <= 0:
        raise SafeError("Timeout must be positive.")
    return run(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SafeError as error:
        print(f"Restore failed: {error}", file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, sqlite3.Error):
        print("Restore failed; check local configuration.", file=sys.stderr)
        sys.exit(1)
