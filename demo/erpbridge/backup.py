#!/usr/bin/env python3
"""Consistent ERPBridge registry backup.

Runs on the VM host. Takes a consistent snapshot of the live SQLite registry
with the SQLite online backup API, which is safe while the container holds the
database open, then verifies and uploads it to the private backup container.

Guarantees:

* The snapshot is taken by the SQLite backup API, not by copying an open file.
* The copy is verified with `PRAGMA integrity_check` before it is published.
* The staged file is published with an atomic rename inside one directory.
* A blob is only reported as uploaded after the service confirms success.
* Local staging is removed only after a verified upload.
* No credential value is read, printed, or logged. The upload uses the VM's
  managed identity through IMDS and sends no shared key.

This tool is non-destructive. It never writes to the source database and never
replaces the live registry. That is what `restore.py` is for.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SCHEMA_VERSION = 1
DEFAULT_DB = "/var/lib/erpbridge/data/erpbridge.db"
DEFAULT_STAGING = "/var/lib/erpbridge/staging"
DEFAULT_STATUS = "/var/lib/erpbridge/backup-status.json"
DEFAULT_IMDS = "http://169.254.169.254"
DEFAULT_STORAGE = "https://erpbridgebackups.blob.core.windows.net"
DEFAULT_CONTAINER = "erpbridge-backups"
STORAGE_RESOURCE = "https://storage.azure.com/"
BLOB_API_VERSION = "2021-08-06"
# Keep at most this many failed local staging files, so a repeated failure can
# never fill the OS disk.
MAX_STAGED_COPIES = 2
# Free space required on the data filesystem, as a multiple of the snapshot size.
FREE_SPACE_FACTOR = 3.0
FREE_SPACE_FLOOR = 128 * 1024 * 1024


class SafeError(Exception):
    """Failure that is safe to print: no provider body, no credential value."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError:
        raise SafeError("A snapshot file could not be read for hashing.") from None
    return digest.hexdigest()


def free_bytes(path: Path) -> int:
    stats = os.statvfs(str(path))
    return stats.f_bavail * stats.f_frsize


def require_space(path: Path, needed: int) -> None:
    available = free_bytes(path)
    if available < needed:
        raise SafeError(f"Not enough free space in {path}: {available} bytes available, "
                        f"{needed} bytes required. Free space or move staging, then retry.")


def manifest(connection: sqlite3.Connection) -> dict:
    """Row counts for every table, so a restore can prove logical equality."""
    tables = [row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
    counts = {}
    for table in tables:
        # Table names come from the schema, never from input.
        counts[table] = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    return counts


def snapshot(source_db: Path, destination: Path) -> dict:
    """Take a consistent snapshot and verify the copy, not the source."""
    if not source_db.exists() or source_db.stat().st_size == 0:
        raise SafeError("The source database is missing or empty.")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if destination.exists():
        destination.unlink()
    # A plain connection is used on purpose. In WAL mode a read-only connection
    # can fail when the shared-memory file is absent, and the backup API only
    # reads from the source.
    source = sqlite3.connect(f"{source_db}", timeout=30)
    try:
        target = sqlite3.connect(str(destination))
        try:
            source.backup(target)
            result = target.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise SafeError("The snapshot failed its integrity check.")
            counts = manifest(target)
        finally:
            target.close()
    except sqlite3.Error:
        raise SafeError("The SQLite snapshot could not be completed.") from None
    finally:
        source.close()
    return counts


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


def put_blob(storage: str, container: str, blob_name: str, path: Path, token: str,
             timeout: float) -> None:
    url = f"{storage.rstrip('/')}/{container}/{urllib.parse.quote(blob_name)}"
    body = path.read_bytes()
    request = urllib.request.Request(url, data=body, method="PUT", headers={
        "Authorization": f"Bearer {token}",
        "x-ms-version": BLOB_API_VERSION,
        "x-ms-blob-type": "BlockBlob",
        "Content-Type": "application/octet-stream",
        "Content-Length": str(len(body)),
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status not in (200, 201):
                raise SafeError(f"The backup upload returned status {response.status}.")
    except urllib.error.HTTPError as error:
        raise SafeError(f"The backup upload was rejected with status {error.code}.") from None
    except (urllib.error.URLError, OSError):
        raise SafeError("The backup upload did not complete.") from None


def urlopen(url: str, token: str, timeout: float) -> bytes:
    request = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "x-ms-version": BLOB_API_VERSION,
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        raise SafeError(f"The backup download was rejected with status {error.code}.") from None
    except (urllib.error.URLError, OSError):
        raise SafeError("The backup download did not complete.") from None


def write_status(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + ".tmp")
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
    os.replace(temporary, path)


def prune_staging(staging: Path, keep: int) -> list[str]:
    """Remove the oldest staged copies, keeping at most `keep` files."""
    files = sorted((path for path in staging.glob("*.db") if path.is_file()),
                   key=lambda path: path.stat().st_mtime, reverse=True)
    removed = []
    for path in files[keep:]:
        try:
            path.unlink()
            removed.append(path.name)
        except OSError:
            continue
    return removed


def object_name(stamp: str, sha: str) -> str:
    return f"{stamp}-{sha[:16]}.db"


def run(args) -> int:
    db_path = Path(args.db_path)
    staging = Path(args.staging_dir)
    status_path = Path(args.status_file)
    staging.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(staging, 0o700)
    now = datetime.datetime.now(datetime.timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    staged = staging / f"{stamp}-snapshot.db"

    try:
        size = db_path.stat().st_size if db_path.exists() else 0
        needed = int(size * FREE_SPACE_FACTOR) + FREE_SPACE_FLOOR
        if args.require_free_bytes:
            needed = max(needed, args.require_free_bytes)
        require_space(staging, needed)
        counts = snapshot(db_path, staged)
        sha = sha256_file(staged)
        size = staged.stat().st_size
        name = object_name(stamp, sha)
        if args.dry_run:
            print(f"Snapshot verified: {size} bytes, sha256 {sha[:16]}...")
            print("Tables: " + ", ".join(f"{table}={count}"
                                          for table, count in sorted(counts.items())))
            if args.keep_staged:
                print(f"Dry run: no upload was attempted. Snapshot kept at {staged}.")
            else:
                staged.unlink(missing_ok=True)
                print("Dry run: no upload was attempted and staging was removed.")
            return 0
        token = access_token(args.imds_endpoint, args.timeout)
        put_blob(args.storage_endpoint, args.container, name, staged, token, args.timeout)
        # Two independent confirmations before the local copy is removed.
        remote = urlopen(f"{args.storage_endpoint.rstrip('/')}/{args.container}/"
                         f"{urllib.parse.quote(name)}", token, args.timeout)
        if hashlib.sha256(remote).hexdigest() != sha:
            raise SafeError("The uploaded object does not match the local snapshot.")
        staged.unlink()
        removed = prune_staging(staging, MAX_STAGED_COPIES)
        write_status(status_path, {
            "schema_version": SCHEMA_VERSION,
            "last_attempt": stamp,
            "last_success": stamp,
            "object_name": name,
            "sha256": sha,
            "bytes": size,
            "manifest": counts,
            "last_error": None,
        })
        print(f"Backup uploaded and verified: {name} ({size} bytes).")
        print("Tables: " + ", ".join(f"{table}={count}" for table, count in sorted(counts.items())))
        if removed:
            print("Removed older staged copies: " + ", ".join(removed))
        return 0
    except SafeError as error:
        try:
            if staged.exists():
                os.chmod(staged, 0o600)
            prune_staging(staging, MAX_STAGED_COPIES)
            write_status(status_path, {
                "schema_version": SCHEMA_VERSION,
                "last_attempt": stamp,
                "last_success": (json.loads(status_path.read_text()).get("last_success")
                                 if status_path.exists() else None),
                "object_name": None,
                "sha256": None,
                "bytes": staged.stat().st_size if staged.exists() else None,
                "manifest": None,
                "last_error": str(error),
            })
        except (OSError, ValueError):
            pass
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Consistent ERPBridge registry backup.")
    parser.add_argument("--db-path", default=os.environ.get("ERPBRIDGE_DB_PATH", DEFAULT_DB))
    parser.add_argument("--staging-dir", default=os.environ.get("ERPBRIDGE_STAGING_DIR",
                                                                DEFAULT_STAGING))
    parser.add_argument("--status-file", default=os.environ.get("ERPBRIDGE_BACKUP_STATUS",
                                                                DEFAULT_STATUS))
    parser.add_argument("--imds-endpoint", default=os.environ.get("ERPBRIDGE_IMDS_ENDPOINT",
                                                                  DEFAULT_IMDS))
    parser.add_argument("--storage-endpoint", default=os.environ.get("ERPBRIDGE_STORAGE_ENDPOINT",
                                                                    DEFAULT_STORAGE))
    parser.add_argument("--container", default=os.environ.get("ERPBRIDGE_BACKUP_CONTAINER",
                                                              DEFAULT_CONTAINER))
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--dry-run", action="store_true",
                        help="Verify the snapshot without uploading it.")
    parser.add_argument("--keep-staged", action="store_true",
                        help="Keep the staged copy for inspection, including on a dry run.")
    parser.add_argument("--require-free-bytes", type=int, default=0,
                        help="Raise the free-space requirement, for small disks.")
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
        print(f"Backup failed: {error}", file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, sqlite3.Error):
        print("Backup failed; check local configuration.", file=sys.stderr)
        sys.exit(1)
