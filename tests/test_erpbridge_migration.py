#!/usr/bin/env python3
"""Snapshot-helper tests against real SQLite databases.

The helper is the reviewed binary that ran the live alpha.5 upgrade. It is built
here from the promoted source when the Go toolchain and its module cache are
available, otherwise these tests skip rather than pretend to pass.
"""
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "demo" / "erpbridge" / "sqlite-snapshot"

SCHEMA = """
CREATE TABLE tools (name TEXT, version TEXT, PRIMARY KEY (name, version));
CREATE TABLE api_tokens (id TEXT PRIMARY KEY, name TEXT, revoked_at TEXT);
CREATE TABLE plugin_bindings (name TEXT PRIMARY KEY, is_active INTEGER);
CREATE TABLE future_release_table (id INTEGER PRIMARY KEY, note TEXT);
"""


def module_cache_present() -> bool:
    cache = Path(os.environ.get("GOMODCACHE") or Path.home() / "go" / "pkg" / "mod")
    return (cache / "modernc.org").is_dir()


def build_helper(destination: Path) -> bool:
    """Build the static helper offline. False when it cannot be built."""
    if shutil.which("go") is None or not module_cache_present():
        return False
    environment = dict(os.environ, GOPROXY="off", GOSUMDB="off", CGO_ENABLED="0",
                       GOOS="linux", GOARCH="amd64")
    try:
        result = subprocess.run(["go", "build", "-trimpath", "-o", str(destination), "."],
                                cwd=MODULE, env=environment, capture_output=True, text=True,
                                timeout=600)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and destination.is_file()


def build_database(path: Path, tools: int = 45):
    connection = sqlite3.connect(str(path))
    try:
        connection.executescript(SCHEMA)
        for index in range(tools):
            connection.execute("INSERT INTO tools VALUES (?,?)", (f"get_item_{index}", "1.0.0"))
        connection.execute("INSERT INTO api_tokens (id, name) VALUES ('t1','live')")
        connection.execute("INSERT INTO api_tokens (id, name, revoked_at) "
                           "VALUES ('t2','revoked','2026-01-01T00:00:00Z')")
        connection.execute("INSERT INTO plugin_bindings VALUES ('b1',1)")
        connection.execute("INSERT INTO future_release_table (note) VALUES ('unknown to today')")
        connection.commit()
    finally:
        connection.close()


class SnapshotHelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.temp.name) / "sqlite-snapshot"
        if not build_helper(cls.binary):
            cls.temp.cleanup()
            raise unittest.SkipTest("the Go toolchain or its module cache is unavailable")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.base = Path(self.temp.name) / f"case-{self._testMethodName}"
        self.base.mkdir()
        self.live = self.base / "live.db"
        build_database(self.live)
        self.before = hashlib.sha256(self.live.read_bytes()).hexdigest()

    def run_helper(self, *args):
        return subprocess.run([str(self.binary), *args], capture_output=True, text=True,
                              timeout=120)

    def test_snapshot_is_consistent_and_read_only_on_the_source(self):
        target = self.base / "snap.db"
        result = self.run_helper(str(self.live), str(target))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"snapshot_created":true', result.stdout)
        self.assertIn('"source_mode":"ro"', result.stdout)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        self.assertEqual(hashlib.sha256(self.live.read_bytes()).hexdigest(), self.before,
                         "the helper must not change the source database")

    def test_snapshot_preserves_every_table_and_revocation_state(self):
        target = self.base / "snap.db"
        self.assertEqual(self.run_helper(str(self.live), str(target)).returncode, 0)
        connection = sqlite3.connect(str(target))
        try:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            tables = sorted(row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"))
            self.assertEqual(tables, ["api_tokens", "future_release_table",
                                      "plugin_bindings", "tools"])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM tools").fetchone()[0], 45)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM api_tokens").fetchone()[0], 2)
            self.assertEqual(connection.execute(
                "SELECT revoked_at FROM api_tokens WHERE id = 't2'").fetchone()[0],
                "2026-01-01T00:00:00Z")
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM future_release_table").fetchone()[0], 1)
        finally:
            connection.close()

    def test_snapshot_while_another_connection_holds_the_database(self):
        target = self.base / "snap.db"
        writer = sqlite3.connect(str(self.live))
        try:
            writer.execute("INSERT INTO tools VALUES ('live_row','1.0.0')")
            writer.commit()
            result = self.run_helper(str(self.live), str(target))
        finally:
            writer.close()
        self.assertEqual(result.returncode, 0, result.stderr)
        connection = sqlite3.connect(str(target))
        try:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM tools").fetchone()[0], 46)
        finally:
            connection.close()

    def test_existing_destination_is_refused(self):
        target = self.base / "snap.db"
        target.write_bytes(b"a previous restore target")
        result = self.run_helper(str(self.live), str(target))
        self.assertNotEqual(result.returncode, 0)
        # The helper reports one generic failure line on purpose, so the test
        # pins the effect: the existing file is untouched.
        self.assertIn("no source writes were requested", result.stderr)
        self.assertEqual(target.read_bytes(), b"a previous restore target")

    def test_relative_and_identical_paths_are_refused(self):
        cases = ((str(self.live), "relative.db"),
                 ("relative.db", str(self.base / "other.db")),
                 (str(self.live), str(self.live)))
        for source, target in cases:
            with self.subTest(source=source, target=target):
                result = self.run_helper(source, target)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout.strip(), "")
                # The identical-path case names an existing file, so only
                # distinct destinations must be shown to be untouched.
                if Path(target) != Path(source):
                    self.assertFalse(Path(target).exists(), "no destination may be created")
        self.assertEqual(hashlib.sha256(self.live.read_bytes()).hexdigest(), self.before)

    def test_failure_text_never_claims_a_write(self):
        result = self.run_helper(str(self.base / "missing.db"), str(self.base / "out.db"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no source writes were requested", result.stderr)
        self.assertFalse((self.base / "out.db").exists())


if __name__ == "__main__":
    unittest.main()
