#!/usr/bin/env python3
"""Backup and restore tests with real SQLite files and a loopback blob stand-in."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
BACKUP = ROOT / "demo" / "erpbridge" / "backup.py"
RESTORE = ROOT / "demo" / "erpbridge" / "restore.py"

SCHEMA = """
CREATE TABLE tools (name TEXT, version TEXT, data TEXT, PRIMARY KEY (name, version));
CREATE TABLE api_tokens (id TEXT PRIMARY KEY, name TEXT NOT NULL, token_hash TEXT NOT NULL,
                         revoked_at TEXT, created_at TEXT NOT NULL DEFAULT 'x');
CREATE TABLE plugin_bindings (name TEXT PRIMARY KEY, plugin_name TEXT, is_active INTEGER);
CREATE TABLE future_release_table (id INTEGER PRIMARY KEY, note TEXT);
"""


def build_database(path: Path, tools: int = 3, revoked: bool = True):
    connection = sqlite3.connect(str(path))
    try:
        connection.executescript(SCHEMA)
        for index in range(tools):
            connection.execute("INSERT INTO tools VALUES (?,?,?)",
                               (f"get_item_{index}", "1.0.0", json.dumps({"index": index})))
        connection.execute("INSERT INTO api_tokens (id, name, token_hash) VALUES ('t1','live','hash1')")
        if revoked:
            connection.execute("INSERT INTO api_tokens (id, name, token_hash, revoked_at) "
                               "VALUES ('t2','revoked','hash2','2026-01-01T00:00:00Z')")
        connection.execute("INSERT INTO plugin_bindings VALUES ('b1','p1',1)")
        connection.execute("INSERT INTO future_release_table (note) VALUES ('not in today schema')")
        connection.commit()
    finally:
        connection.close()


class BlobHandler(BaseHTTPRequestHandler):
    """Minimal stand-in for IMDS and Blob storage."""
    objects: dict = {}
    put_count = 0
    fail_put = False
    fail_get = False
    corrupt = False

    def log_message(self, format, *args):  # keep the test output quiet
        pass

    def do_GET(self):
        if self.path.startswith("/metadata/identity/oauth2/token"):
            body = json.dumps({"access_token": "fake-token", "expires_in": "3600"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/container/"):
            name = self.path.rsplit("/", 1)[-1]
            if type(self).fail_get or name not in type(self).objects:
                self.send_error(404)
                return
            body = type(self).objects[name]
            if type(self).corrupt:
                body = b"not a sqlite database at all"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def do_PUT(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        if type(self).fail_put:
            self.send_error(500)
            return
        if not self.headers.get("Authorization", "").startswith("Bearer "):
            self.send_error(401)
            return
        name = self.path.rsplit("/", 1)[-1]
        type(self).objects[name] = body
        type(self).put_count += 1
        self.send_response(201)
        self.send_header("Content-Length", "0")
        self.end_headers()


class BlobServerCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.staging = self.base / "staging"
        self.status = self.base / "backup-status.json"
        self.db = self.base / "erpbridge.db"
        BlobHandler.objects = {}
        BlobHandler.put_count = 0
        BlobHandler.fail_put = False
        BlobHandler.fail_get = False
        BlobHandler.corrupt = False
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), BlobHandler)
        self.addCleanup(self.server.server_close)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        endpoint = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.env = dict(
            os.environ,
            ERPBRIDGE_DB_PATH=str(self.db),
            ERPBRIDGE_STAGING_DIR=str(self.staging),
            ERPBRIDGE_BACKUP_STATUS=str(self.status),
            ERPBRIDGE_IMDS_ENDPOINT=endpoint,
            ERPBRIDGE_STORAGE_ENDPOINT=endpoint,
            ERPBRIDGE_BACKUP_CONTAINER="container",
        )

    def run_backup(self, *args):
        return subprocess.run([sys.executable, str(BACKUP), *args], env=self.env,
                              capture_output=True, text=True)

    def run_restore(self, *args):
        return subprocess.run([sys.executable, str(RESTORE), *args], env=self.env,
                              capture_output=True, text=True)

    def staged_files(self):
        return sorted(path.name for path in self.staging.glob("*.db")) if self.staging.exists() else []


class BackupTests(BlobServerCase):
    def test_snapshot_is_verified_and_reports_every_table(self):
        build_database(self.db)
        result = self.run_backup("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("tools=3", result.stdout)
        self.assertIn("api_tokens=2", result.stdout)
        self.assertIn("future_release_table=1", result.stdout)
        self.assertEqual(BlobHandler.put_count, 0, "a dry run must not upload")
        self.assertEqual(self.staged_files(), [])

    def test_upload_verifies_then_removes_staging(self):
        build_database(self.db)
        result = self.run_backup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(BlobHandler.put_count, 1)
        self.assertEqual(self.staged_files(), [], "a verified upload must remove the local copy")
        status = json.loads(self.status.read_text())
        self.assertIsNone(status["last_error"])
        self.assertTrue(status["object_name"].endswith(".db"))
        self.assertEqual(len(status["sha256"]), 64)
        self.assertEqual(status["manifest"]["tools"], 3)
        stored = BlobHandler.objects[status["object_name"]]
        self.assertEqual(hashlib.sha256(stored).hexdigest(), status["sha256"])

    def test_backup_while_the_database_is_being_written(self):
        build_database(self.db)
        stop = threading.Event()
        errors = []

        def writer():
            connection = sqlite3.connect(str(self.db), timeout=30)
            index = 0
            try:
                while not stop.is_set():
                    index += 1
                    connection.execute("INSERT INTO tools VALUES (?,?,?)",
                                       (f"live_{index}", "1.0.0", "{}"))
                    connection.commit()
            except sqlite3.Error as error:  # pragma: no cover - reported below
                errors.append(str(error))
            finally:
                connection.close()

        thread = threading.Thread(target=writer, daemon=True)
        thread.start()
        try:
            result = self.run_backup()
        finally:
            stop.set()
            thread.join(timeout=10)
        self.assertEqual(errors, [])
        self.assertEqual(result.returncode, 0, result.stderr)
        status = json.loads(self.status.read_text())
        # The snapshot is a single consistent point, so the count is at least the
        # initial three rows and the copy must still pass an integrity check.
        self.assertGreaterEqual(status["manifest"]["tools"], 3)
        stored = BlobHandler.objects[status["object_name"]]
        check = self.base / "uploaded.db"
        check.write_bytes(stored)
        connection = sqlite3.connect(str(check))
        try:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            connection.close()

    def test_upload_failure_keeps_bounded_staging_and_records_the_error(self):
        build_database(self.db)
        BlobHandler.fail_put = True
        for _ in range(3):
            result = self.run_backup()
            self.assertEqual(result.returncode, 1)
            self.assertIn("Backup failed", result.stderr)
        self.assertIn("rejected with status 500", self.run_backup().stderr)
        self.assertLessEqual(len(self.staged_files()), 2,
                             "failed runs must not grow staging without bound")
        status = json.loads(self.status.read_text())
        self.assertIsNone(status["last_success"])
        self.assertIn("rejected with status 500", status["last_error"])

    def test_checksum_mismatch_after_upload_is_rejected(self):
        build_database(self.db)
        BlobHandler.corrupt = True
        result = self.run_backup()
        self.assertEqual(result.returncode, 1)
        self.assertIn("does not match the local snapshot", result.stderr)
        self.assertEqual(len(self.staged_files()), 1, "the local copy must be kept on failure")

    def test_missing_database_fails_clearly(self):
        result = self.run_backup()
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing or empty", result.stderr)

    def test_invalid_database_is_rejected(self):
        self.db.write_bytes(b"this is not a sqlite database")
        result = self.run_backup()
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("this is not a sqlite database", result.stdout)

    def test_disk_space_failure_is_clear_and_safe(self):
        build_database(self.db)
        result = self.run_backup("--require-free-bytes", str(1 << 62))
        self.assertEqual(result.returncode, 1)
        self.assertIn("Not enough free space", result.stderr)
        self.assertEqual(BlobHandler.put_count, 0)
        self.assertEqual(self.status_mode(), 0o600)

    def status_mode(self):
        return 0o600 if not self.status.exists() else self.status.stat().st_mode & 0o777


class RestoreTests(BlobServerCase):
    def setUp(self):
        super().setUp()
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.calls = self.base / "calls"
        fake = self.bin / "fake"
        fake.write_text('''#!/usr/bin/env python3
import json, os, sys
name = os.path.basename(sys.argv[0])
with open(os.environ['CALLS'], 'a') as stream:
    stream.write(json.dumps([name] + sys.argv[1:]) + '\\n')
if name == 'systemctl':
    print(os.environ.get('SERVICE_STATE', 'inactive'))
    sys.exit(0)
if name == 'findmnt':
    print(os.environ.get('FS_UUID', 'fs-uuid-fixed'))
    sys.exit(0)
sys.exit(127)
''')
        fake.chmod(0o700)
        for name in ("systemctl", "findmnt"):
            (self.bin / name).symlink_to(fake)
        self.env["PATH"] = str(self.bin) + os.pathsep + os.environ["PATH"]
        self.env["CALLS"] = str(self.calls)
        self.env["SYSTEMCTL"] = str(self.bin / "systemctl")
        self.env["FINDMNT"] = str(self.bin / "findmnt")
        self.env["ERPBRIDGE_MOUNT_POINT"] = str(self.base)
        self.identity = self.base / "disk-identity"
        self.env["ERPBRIDGE_IDENTITY_FILE"] = str(self.identity)
        self.uid_file = self.base / "container-uid"
        self.uid_file.write_text("0\n")
        self.env["ERPBRIDGE_UID_FILE"] = str(self.uid_file)
        self.object_name = "20260101T000000Z-abc.db"

    def publish(self, source: Path, name: str | None = None):
        name = name or self.object_name
        BlobHandler.objects[name] = source.read_bytes()
        return name

    def test_confirmation_is_required(self):
        result = self.run_restore("--backup-id", self.object_name)
        self.assertEqual(result.returncode, 1)
        self.assertIn("--confirm", result.stderr)
        self.assertFalse(self.staging.exists())

    def test_refuses_while_the_service_is_running(self):
        self.env["SERVICE_STATE"] = "active"
        result = self.run_restore("--backup-id", self.object_name, "--confirm")
        self.assertEqual(result.returncode, 1)
        self.assertIn("is running", result.stderr)

    def test_refuses_on_unknown_service_state(self):
        self.env["SERVICE_STATE"] = "unknown-state"
        result = self.run_restore("--backup-id", self.object_name, "--confirm")
        self.assertEqual(result.returncode, 1)
        self.assertIn("unknown", result.stderr)

    def test_refuses_when_the_disk_identity_changed(self):
        self.identity.write_text(json.dumps({"filesystem_uuid": "the-original-disk"}))
        self.env["FS_UUID"] = "a-different-disk"
        result = self.run_restore("--backup-id", self.object_name, "--confirm")
        self.assertEqual(result.returncode, 1)
        self.assertIn("filesystem identity", result.stderr)

    def test_restores_over_crash_leftover_sidecars(self):
        source = self.base / "source.db"
        build_database(source)
        self.publish(source)
        target = self.db
        build_database(target, tools=1)
        for suffix in ("-wal", "-shm", "-journal"):
            Path(f"{target}{suffix}").write_bytes(b"stale crash leftover")
        result = self.run_restore("--backup-id", self.object_name, "--confirm")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("tools=3", result.stdout)
        self.assertIn("moved aside", result.stdout)
        self.assertIn("No stale sidecar files remain", result.stdout)
        for suffix in ("-wal", "-shm", "-journal"):
            self.assertFalse(Path(f"{target}{suffix}").exists(),
                             f"{suffix} must not remain beside the restored database")
        superseded = sorted(self.base.glob("erpbridge.db-*.superseded-*"))
        self.assertEqual(len(superseded), 3)

    def test_token_revocation_rows_survive_the_round_trip(self):
        source = self.base / "source.db"
        build_database(source)
        self.publish(source)
        result = self.run_restore("--backup-id", self.object_name, "--confirm")
        self.assertEqual(result.returncode, 0, result.stderr)
        connection = sqlite3.connect(str(self.db))
        try:
            rows = dict(connection.execute("SELECT id, revoked_at FROM api_tokens"))
        finally:
            connection.close()
        self.assertEqual(rows["t1"], None)
        self.assertEqual(rows["t2"], "2026-01-01T00:00:00Z")
        self.assertIn("api_tokens=2", result.stdout)

    def test_unknown_tables_are_retained(self):
        source = self.base / "source.db"
        build_database(source)
        self.publish(source)
        result = self.run_restore("--backup-id", self.object_name, "--confirm")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("future_release_table=1", result.stdout)

    def test_checksum_mismatch_leaves_the_target_untouched(self):
        source = self.base / "source.db"
        build_database(source)
        self.publish(source)
        build_database(self.db, tools=1)
        before = self.db.read_bytes()
        result = self.run_restore("--backup-id", self.object_name, "--confirm",
                                  "--expect-sha256", "0" * 64)
        self.assertEqual(result.returncode, 1)
        self.assertIn("expected checksum", result.stderr)
        self.assertEqual(self.db.read_bytes(), before)

    def test_manifest_mismatch_leaves_the_target_untouched(self):
        source = self.base / "source.db"
        build_database(source)
        self.publish(source)
        build_database(self.db, tools=1)
        before = self.db.read_bytes()
        wrong = self.base / "wrong.json"
        wrong.write_text(json.dumps({"tools": 99}))
        result = self.run_restore("--backup-id", self.object_name, "--confirm",
                                  "--expect-manifest", str(wrong))
        self.assertEqual(result.returncode, 1)
        self.assertIn("differ from the expected manifest", result.stderr)
        self.assertEqual(self.db.read_bytes(), before)

    def test_download_that_is_not_a_database_is_rejected(self):
        build_database(self.db, tools=1)
        before = self.db.read_bytes()
        BlobHandler.corrupt = True
        result = self.run_restore("--backup-id", self.object_name, "--confirm")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Restore failed", result.stderr)
        self.assertEqual(self.db.read_bytes(), before)

    def test_missing_object_is_rejected(self):
        build_database(self.db, tools=1)
        before = self.db.read_bytes()
        BlobHandler.fail_get = True
        result = self.run_restore("--backup-id", self.object_name, "--confirm")
        self.assertEqual(result.returncode, 1)
        self.assertIn("rejected with status 404", result.stderr)
        self.assertEqual(self.db.read_bytes(), before)

    def test_rehearsal_from_a_local_file_sets_ownership_and_mode(self):
        source = self.base / "source.db"
        build_database(source)
        result = self.run_restore("--backup-id", self.object_name, "--confirm",
                                  "--source-file", str(source))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.db.stat().st_mode & 0o777, 0o640)
        self.assertIn("api_tokens=2", result.stdout)

    def test_rejects_unusable_object_names(self):
        for name in ("../escape", "", "with space", "a" * 300):
            with self.subTest(name=name[:12]):
                result = self.run_restore("--backup-id", name, "--confirm")
                self.assertEqual(result.returncode, 1)
                self.assertIn("not a usable object name", result.stderr)

    def test_staging_must_share_a_filesystem_with_the_target(self):
        other = Path("/dev/shm")
        if not other.is_dir() or other.stat().st_dev == self.base.stat().st_dev:
            self.skipTest("no second filesystem available for this check")
        source = self.base / "source.db"
        build_database(source)
        result = self.run_restore("--backup-id", self.object_name, "--confirm",
                                  "--source-file", str(source),
                                  "--staging-dir", str(other / f"staging-{os.getpid()}"))
        try:
            self.assertEqual(result.returncode, 1)
            self.assertIn("share one filesystem", result.stderr)
        finally:
            shutil.rmtree(other / f"staging-{os.getpid()}", ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
