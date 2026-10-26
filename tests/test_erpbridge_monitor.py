#!/usr/bin/env python3
"""Local collector fixtures. No cloud access, host service changes or secrets."""
import contextlib
import datetime as dt
import fcntl
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("erpbridge_monitor", ROOT / "demo/erpbridge/monitor.py")
assert SPEC is not None and SPEC.loader is not None
monitor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(monitor)
NOW = dt.datetime(2026, 10, 18, 12, tzinfo=dt.timezone.utc)
VM = "/subscriptions/test/resourceGroups/demo/providers/Microsoft.Compute/virtualMachines/erpbridge-vm"
SECRET = "SECRET_SENTINEL_DO_NOT_EMIT"
FIELDS = {
    "TimeGenerated": str, "VmResourceId": str,
    "MemoryAvailableBytes": int, "MemoryTotalBytes": int,
    "MemoryPressureSomeAvg10": float, "MemoryPressureFullAvg10": float,
    "MemoryPressureAvailable": bool, "SwapUsedBytes": int,
    "KernelOomKills": int, "KernelOomAvailable": bool,
    "RootFreeBytes": int, "RootUsedPercent": float, "RootInodeUsedPercent": float,
    "ErpbridgeActive": bool, "CaddyActive": bool, "BackendHttpStatus": int,
    "ServiceFault": bool, "DiskFault": bool, "BackupAgeSeconds": int,
    "BackupFailed": bool, "BackupStatusValid": bool,
    "ContainerMemoryBytes": int, "ContainerOomKills": int,
    "ContainerCountersAvailable": bool, "ErpbridgeServiceMemoryBytes": int,
    "CaddyMemoryBytes": int, "AmaMemoryBytes": int, "ServiceMemoryAvailable": bool,
}


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.args = monitor.parser().parse_args([
            "--once", "--vm-resource-id", VM,
            "--output-dir", str(self.root / "out"),
            "--state-file", str(self.root / "state/state.json"),
            "--backup-status", str(self.root / "backup.json"),
            "--proc-root", str(self.root / "proc"),
            "--cgroup-root", str(self.root / "cgroup"),
        ])
        self.now = NOW
        self.calls = []
        self.active, self.http_code = True, 200
        self.fs = SimpleNamespace(f_blocks=10_000_000, f_bfree=5_000_000,
                                  f_bavail=4_000_000, f_frsize=4096,
                                  f_files=1_000_000, f_ffree=900_000)
        self.put("proc/meminfo", "MemTotal: 2048000 kB\nMemAvailable: 512000 kB\n"
                 "SwapTotal: 2048 kB\nSwapFree: 1024 kB\n")
        self.put("proc/vmstat", "oom_kill 3\n")
        self.put("proc/pressure/memory", "some avg10=1.25 avg60=0.20 total=5\n"
                 "full avg10=0.25 avg60=0.00 total=1\n")
        self.put("proc/123/cgroup", "0::/system.slice/docker-test.scope\n")
        self.put("cgroup/system.slice/docker-test.scope/memory.current", "123456\n")
        self.put("cgroup/system.slice/docker-test.scope/memory.events", "oom 4\noom_kill 2\n")
        # These files must never be read by the collector.
        self.put("proc/123/environ", SECRET)
        self.put("proc/123/cmdline", SECRET)
        self.status()

    def put(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def status(self, **changes):
        data = {"schema_version": 1, "last_attempt": "20261018T110000Z",
                "last_success": "20261018T110000Z", "last_error": None,
                "manifest": {SECRET: SECRET}, "object_name": SECRET, "sha256": SECRET}
        data.update(changes)
        self.put("backup.json", json.dumps(data))

    def runner(self, argv):
        self.calls.append(argv)
        if argv[0] == "systemctl":
            self.assertEqual(argv[1], "show")
            self.assertIn(argv[2], monitor.SERVICES)
            self.assertEqual(argv[3:], ["--property=ActiveState,MainPID,MemoryCurrent"])
            return ("ActiveState=" + ("active" if self.active else "failed")
                    + "\nMainPID=123\nMemoryCurrent=234567\n")
        self.assertEqual(argv, ["docker", "inspect", "--format",
                         '{"Running":{{json .State.Running}},"Pid":{{json .State.Pid}}}', "erpbridge"])
        return '{"Running":true,"Pid":123}'

    def collector(self, **kwargs):
        return monitor.Collector(self.args, runner=kwargs.get("runner", self.runner),
                                 http=kwargs.get("http", lambda: self.http_code),
                                 clock=lambda: self.now, statvfs=lambda _: self.fs)

    def sample(self):
        return self.collector().sample()[0]

    def write(self):
        self.assertEqual(monitor.write_sample(self.args, self.collector()), 0)
        logs = sorted(Path(self.args.output_dir).glob("*.jsonl"))
        return json.loads(logs[-1].read_text().splitlines()[-1])

    def test_healthy_schema_types_and_allowlisted_commands(self):
        record = self.sample()
        self.assertEqual(set(record), set(FIELDS))
        for key, kind in FIELDS.items():
            self.assertIs(type(record[key]), kind, key)
        self.assertEqual(record["TimeGenerated"], "2026-10-18T12:00:00Z")
        self.assertEqual(record["MemoryAvailableBytes"], 512000 * 1024)
        self.assertEqual(record["SwapUsedBytes"], 1024 * 1024)
        self.assertEqual(record["KernelOomKills"], 3)
        self.assertEqual(record["ContainerOomKills"], 2)
        self.assertTrue(record["ContainerCountersAvailable"])
        self.assertTrue(record["ServiceMemoryAvailable"])
        self.assertTrue(record["BackupStatusValid"])
        self.assertFalse(record["BackupFailed"])
        self.assertEqual(record["BackupAgeSeconds"], 3600)
        self.assertEqual(len(self.calls), 4)
        self.assertLessEqual(len(monitor.encode(record)), 2048)
        self.assertNotIn(SECRET, monitor.encode(record).decode())

    def test_failed_backup_retains_age_without_error_or_secret(self):
        self.status(last_attempt="20261018T113000Z", last_error=SECRET)
        record = self.write()
        self.assertTrue(record["BackupFailed"])
        self.assertTrue(record["BackupStatusValid"])
        self.assertEqual(record["BackupAgeSeconds"], 3600)
        for path in self.root.rglob("*"):
            if path.is_file() and ("out" in path.parts or "state" in path.parts):
                self.assertNotIn(SECRET, path.read_text())

    def test_backup_age_boundary(self):
        for seconds in (26 * 3600 - 1, 26 * 3600, 26 * 3600 + 1):
            stamp = (NOW - dt.timedelta(seconds=seconds)).strftime("%Y%m%dT%H%M%SZ")
            self.status(last_attempt=stamp, last_success=stamp)
            record = self.sample()
            self.assertEqual(record["BackupAgeSeconds"], seconds)
            self.assertEqual(record["BackupAgeSeconds"] > 26 * 3600, seconds > 26 * 3600)

    def test_backup_missing_invalid_oversized_and_future(self):
        cases = ["{", "[]", "null", SECRET * 2000]
        for text in cases:
            self.put("backup.json", text)
            self.assertFalse(self.sample()["BackupStatusValid"])
        Path(self.args.backup_status).unlink()
        record = self.sample()
        self.assertFalse(record["BackupStatusValid"])
        self.assertEqual(record["BackupAgeSeconds"], -1)
        for bad in ("2026-10-18T11:00:00", "2026-10-18T11:00:00+02:00", "bad",
                    "2026-10-18T12:05:01Z", "20260230T120000Z", 100):
            self.status(last_success=bad, last_attempt="20261018T120000Z")
            self.assertFalse(self.sample()["BackupStatusValid"])
        self.status(last_attempt="20261018T120501Z")
        self.assertFalse(self.sample()["BackupStatusValid"])
        self.status(last_success="20261018T113000Z", last_attempt="20261018T110000Z")
        self.assertFalse(self.sample()["BackupStatusValid"])
        self.status(schema_version=True)
        self.assertFalse(self.sample()["BackupStatusValid"])
        for bad in ("", False):
            self.status(last_success=bad)
            self.assertFalse(self.sample()["BackupStatusValid"])
        self.status(last_error=123)
        self.assertFalse(self.sample()["BackupStatusValid"])
        self.put("backup.json", '{"schema_version":1,"last_attempt":"20261018T110000Z","last_success":"20261018T110000Z"}')
        self.assertFalse(self.sample()["BackupStatusValid"])
        self.put("backup.json", "[" * 2000 + "]" * 2000)
        self.assertFalse(self.sample()["BackupStatusValid"])

    def test_future_tolerance_and_no_success(self):
        self.status(last_success="20261018T120500Z", last_attempt="20261018T120500Z")
        self.assertTrue(self.sample()["BackupStatusValid"])
        self.assertEqual(self.sample()["BackupAgeSeconds"], 0)
        self.status(last_success=None, last_error=SECRET)
        record = self.sample()
        self.assertTrue(record["BackupStatusValid"])
        self.assertTrue(record["BackupFailed"])
        self.assertEqual(record["BackupAgeSeconds"], -1)

    def test_inaccessible_and_malformed_counters_are_unavailable(self):
        for path in ("proc/meminfo", "proc/vmstat", "proc/pressure/memory",
                     "cgroup/system.slice/docker-test.scope/memory.events"):
            (self.root / path).unlink()
        record = self.sample()
        for key in ("MemoryAvailableBytes", "SwapUsedBytes", "MemoryPressureSomeAvg10",
                    "KernelOomKills", "ContainerMemoryBytes", "ContainerOomKills"):
            self.assertEqual(record[key], -1)
        for key in ("MemoryPressureAvailable", "KernelOomAvailable", "ContainerCountersAvailable"):
            self.assertFalse(record[key])
        self.put("proc/meminfo", "MemTotal: nonsense kB\n")
        self.put("proc/vmstat", "oom_kill -1\n")
        self.put("proc/pressure/memory", "some avg10=nan\nfull avg10=0\n")
        self.assertEqual(self.sample()["KernelOomKills"], -1)
        self.assertFalse(self.sample()["MemoryPressureAvailable"])

    def test_timeouts_and_malformed_command_data_are_safe(self):
        def timeout(*_):
            raise TimeoutError(SECRET)
        record, _ = self.collector(runner=timeout, http=timeout).sample()
        self.assertFalse(record["ErpbridgeActive"])
        self.assertFalse(record["CaddyActive"])
        self.assertEqual(record["BackendHttpStatus"], -1)
        self.assertFalse(record["ServiceMemoryAvailable"])
        self.assertNotIn(SECRET, monitor.encode(record).decode())
        for value in ("{}", "null", "[]", SECRET):
            record, _ = self.collector(runner=lambda _, v=value: v).sample()
            self.assertFalse(record["ContainerCountersAvailable"])

    def test_cgroup_traversal_and_v1_rejected(self):
        for line in ("0::/../../outside\n", "5:memory:/docker/test\n"):
            self.put("proc/123/cgroup", line)
            self.assertFalse(self.sample()["ContainerCountersAvailable"])

    def test_two_sample_persistence_recovery_and_gap_reset(self):
        self.active, self.http_code = False, 503
        self.fs.f_bavail = 10
        first = self.write()
        self.assertFalse(first["ServiceFault"])
        self.assertFalse(first["DiskFault"])
        self.now += dt.timedelta(minutes=1)
        second = self.write()  # A new Collector object reads the persisted state.
        self.assertTrue(second["ServiceFault"])
        self.assertTrue(second["DiskFault"])
        self.active, self.http_code = True, 200
        self.fs.f_bavail = 4_000_000
        self.now += dt.timedelta(minutes=1)
        recovery = self.write()
        self.assertFalse(recovery["ServiceFault"])
        self.assertFalse(recovery["DiskFault"])
        self.active = False
        self.now += dt.timedelta(minutes=1)
        self.write()
        self.now += dt.timedelta(minutes=3)
        self.assertFalse(self.write()["ServiceFault"])
        self.now -= dt.timedelta(minutes=1)
        self.assertFalse(self.write()["ServiceFault"])

    def test_malformed_state_resets_safely(self):
        self.active = False
        for text in ("null", "[]", "{", '{"time":"2026-10-18T11:59:00Z","ServiceFault":"secret","DiskFault":0}'):
            self.put("state/state.json", text)
            self.assertFalse(self.write()["ServiceFault"])

    def test_disk_exact_boundaries_and_inode_fault(self):
        record = self.sample()
        record.update(RootFreeBytes=1024**3, RootUsedPercent=85.0, RootInodeUsedPercent=85.0)
        state = monitor.next_state(record, NOW, self.args.state_file)
        self.assertEqual(state["DiskFault"], 0)
        for key, value in (("RootFreeBytes", 1024**3 - 1), ("RootUsedPercent", 85.01),
                           ("RootInodeUsedPercent", 85.01)):
            changed = dict(record)
            changed[key] = value
            self.assertEqual(monitor.next_state(changed, NOW, self.args.state_file)["DiskFault"], 1)

    def test_daily_rotation_append_and_size_bounds(self):
        for day in range(14, 19):
            self.now = NOW.replace(day=day)
            self.write()
        output = Path(self.args.output_dir)
        self.assertEqual([path.name for path in sorted(output.glob("*.jsonl"))],
                         [f"health-2026-10-{day}.jsonl" for day in (16, 17, 18)])
        today = output / "health-2026-10-18.jsonl"
        original = today.read_bytes()
        self.now += dt.timedelta(minutes=1)
        self.write()
        self.assertTrue(today.read_bytes().startswith(original))
        for path in output.glob("*.jsonl"):
            for line in path.read_bytes().splitlines(keepends=True):
                self.assertLessEqual(len(line), 2048)
        with today.open("wb") as stream:
            stream.truncate(monitor.MAX_DAILY)
        with self.assertRaises(ValueError):
            monitor.write_sample(self.args, self.collector())
        self.assertEqual(today.stat().st_size, monitor.MAX_DAILY)
        with self.assertRaises(ValueError):
            monitor.encode({"not_allowed": "x" * 2048})

    def test_no_overlap(self):
        state = Path(self.args.state_file)
        state.parent.mkdir()
        with (state.parent / "state.json.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(monitor.write_sample(self.args, self.collector()), 0)
        self.assertFalse(Path(self.args.output_dir).exists())
        self.assertEqual(self.calls, [])

    def test_check_has_no_filesystem_side_effects(self):
        # Use the production main mode dispatch with injected local probes.
        binary = io.BytesIO()
        stdout = SimpleNamespace(buffer=binary)
        argv = ["--check", "--vm-resource-id", VM, "--output-dir", self.args.output_dir,
                "--state-file", self.args.state_file, "--backup-status", self.args.backup_status]
        before = {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        collector = self.collector()
        with mock.patch.object(monitor, "Collector", return_value=collector), mock.patch.object(sys, "stdout", stdout):
            self.assertEqual(monitor.main(argv), 0)
        after = {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertFalse(Path(self.args.output_dir).exists())
        self.assertFalse(Path(self.args.state_file).parent.exists())
        self.assertEqual(json.loads(binary.getvalue())["VmResourceId"], VM)

    def test_real_command_runner_bounds_and_timeout(self):
        self.assertEqual(monitor.command([sys.executable, "-c", "print('ok')"]), "ok\n")
        with self.assertRaises(ValueError):
            monitor.command([sys.executable, "-c", "print('x'*20000)"])
        with self.assertRaises(TimeoutError):
            monitor.command([sys.executable, "-c", "import time; time.sleep(10)"])
        with self.assertRaises(ValueError):
            monitor.command([sys.executable, "-c", "raise SystemExit(2)"])

    def test_http_probe_reads_only_status_line_without_following_redirect(self):
        for status in (200, 302, 503):
            connection = mock.MagicMock()
            connection.__enter__.return_value = connection
            status_line = f"HTTP/1.1 {status} result\r\n".encode()
            unread = b"Authorization: " + SECRET.encode() + b"\r\n\r\n" + SECRET.encode()
            connection.recv.side_effect = [bytes([byte]) for byte in status_line + unread]
            with mock.patch.object(monitor.socket, "create_connection", return_value=connection) as create:
                self.assertEqual(monitor.backend_status(), status)
            create.assert_called_once_with(("127.0.0.1", 8080), timeout=1)
            self.assertEqual(connection.recv.call_count, len(status_line))
            self.assertEqual(connection.recv.call_args_list, [mock.call(1)] * len(status_line))
            connection.sendall.assert_called_once_with(
                b"GET /mcp/health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        connection = mock.MagicMock()
        connection.__enter__.return_value = connection
        connection.recv.return_value = b"x"
        with mock.patch.object(monitor.socket, "create_connection", return_value=connection):
            with self.assertRaises(ValueError):
                monitor.backend_status()
        self.assertEqual(connection.recv.call_count, 128)

    def test_unit_limits_paths_and_timer(self):
        service = (ROOT / "demo/erpbridge/erpbridge-monitor.service").read_text()
        timer = (ROOT / "demo/erpbridge/erpbridge-monitor.timer").read_text()
        for text in ("MemoryMax=64M", "RuntimeMaxSec=15", "TimeoutStartSec=15",
                     "Type=oneshot", "Nice=19", "IOSchedulingClass=idle",
                     "EnvironmentFile=/etc/erpbridge/monitor.env",
                     "ExecStart=/usr/bin/python3 /opt/erpbridge/monitor.py --once"):
            self.assertIn(text, service)
        self.assertIn("OnCalendar=*-*-* *:*:00", timer)
        self.assertIn("Unit=erpbridge-monitor.service", timer)
        self.assertNotIn("Restart=", service)


if __name__ == "__main__":
    unittest.main()
