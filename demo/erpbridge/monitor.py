#!/usr/bin/env python3
"""Bounded, read-only host probes and an allow-listed daily JSONL writer.

--check runs the same probes without locks, directory creation, state changes,
rotation or writes. No response bodies, process environments or command lines
are collected. Only --once writes. Requires Linux and Python's standard library.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import socket
import json
import math
import os
from pathlib import Path
import re
import selectors
import subprocess
import sys
import time

UTC = dt.timezone.utc
MAX_INPUT = 16384
MAX_SAMPLE = 2048
MAX_DAILY = 4 * 1024 * 1024
SERVICES = ("erpbridge.service", "caddy.service", "azuremonitoragent.service")


def read_text(path, limit=MAX_INPUT):
    with Path(path).open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("oversized input")
    return data.decode("utf-8")


def command(argv):
    """Bound stdout and wall time; discard stderr rather than exposing errors."""
    with subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}) as proc:
        assert proc.stdout is not None
        try:
            data = bytearray()
            deadline = time.monotonic() + 1.0
            with selectors.DefaultSelector() as selector:
                selector.register(proc.stdout, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise TimeoutError("probe timeout")
                    chunk = os.read(proc.stdout.fileno(), 4096)
                    if not chunk:
                        break
                    data.extend(chunk)
                    if len(data) > MAX_INPUT:
                        raise ValueError("oversized command output")
            if proc.wait(timeout=max(0.01, deadline - time.monotonic())) != 0:
                raise ValueError("probe failed")
            return data.decode("utf-8")
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


def backend_status():
    # Read only a bounded status line, not headers or a response body. Apply an
    # absolute deadline so a slow trickle cannot extend the probe indefinitely.
    deadline = time.monotonic() + 1.0
    with socket.create_connection(("127.0.0.1", 8080), timeout=1) as connection:
        connection.settimeout(max(0.001, deadline - time.monotonic()))
        connection.sendall(b"GET /mcp/health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        line = bytearray()
        while len(line) < 128:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("health probe timeout")
            connection.settimeout(remaining)
            byte = connection.recv(1)
            if not byte:
                break
            line.extend(byte)
            if line.endswith(b"\r\n"):
                match = re.fullmatch(rb"HTTP/1\.[01] ([1-5][0-9]{2}) [^\r\n]*\r\n", line)
                if match is not None:
                    return int(match[1])
                break
    raise ValueError("invalid HTTP status line")


def integer(value):
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)):
        raise ValueError("invalid counter")
    result = int(value)
    if result > 2**63 - 1:
        raise ValueError("counter overflow")
    return result


def pairs(text):
    return dict(line.split(None, 1) for line in text.splitlines() if line.strip())


def stamp(value):
    if not isinstance(value, str):
        raise ValueError("invalid timestamp")
    if re.fullmatch(r"\d{8}T\d{6}Z", value):
        return dt.datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != dt.timedelta(0):
        raise ValueError("timestamp must be UTC")
    return parsed.astimezone(UTC)


def backup(path, now):
    result = {"BackupAgeSeconds": -1, "BackupFailed": False, "BackupStatusValid": False}
    try:
        status = json.loads(read_text(path))
        if not isinstance(status, dict):
            return result
        if type(status.get("schema_version")) is not int or status["schema_version"] != 1:
            return result
        if "last_error" not in status or not (status["last_error"] is None or isinstance(status["last_error"], str)):
            return result
        attempt = stamp(status["last_attempt"])
        success = stamp(status["last_success"]) if status["last_success"] is not None else None
        if attempt > now + dt.timedelta(minutes=5):
            return result
        if success is not None and (success > now + dt.timedelta(minutes=5) or success > attempt):
            return result
        # Consume only the existence of an error, never its value in telemetry.
        result["BackupFailed"] = status.get("last_error") is not None or success is None or attempt > success
        result["BackupAgeSeconds"] = max(0, int((now - success).total_seconds())) if success else -1
        result["BackupStatusValid"] = True
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
        pass
    return result


class Collector:
    def __init__(self, args, *, runner=command, http=backend_status,
                 clock=lambda: dt.datetime.now(UTC), statvfs=os.statvfs):
        self.args, self.runner, self.http = args, runner, http
        self.clock, self.statvfs = clock, statvfs
        self.proc, self.cgroup = Path(args.proc_root), Path(args.cgroup_root)

    def cgroup_counters(self, pid):
        # cgroup v2 only. Unsupported v1 is explicitly unavailable.
        lines = read_text(self.proc / str(integer(pid)) / "cgroup").splitlines()
        entry = next(line[3:] for line in lines if line.startswith("0::/"))
        root = self.cgroup.resolve()
        group = (root / entry.lstrip("/")).resolve()
        if not group.is_relative_to(root):
            raise ValueError("cgroup outside root")
        memory = integer(read_text(group / "memory.current").strip())
        kills = integer(pairs(read_text(group / "memory.events"))["oom_kill"])
        return memory, kills

    def sample(self):
        now = self.clock().astimezone(UTC)
        record = {"TimeGenerated": now.isoformat().replace("+00:00", "Z"),
                  "VmResourceId": self.args.vm_resource_id}
        for key in ("MemoryAvailableBytes", "MemoryTotalBytes", "MemoryPressureSomeAvg10",
                    "MemoryPressureFullAvg10", "SwapUsedBytes", "KernelOomKills",
                    "RootFreeBytes", "RootUsedPercent", "RootInodeUsedPercent",
                    "BackendHttpStatus", "ContainerMemoryBytes", "ContainerOomKills",
                    "ErpbridgeServiceMemoryBytes", "CaddyMemoryBytes", "AmaMemoryBytes"):
            record[key] = -1
        for key in ("MemoryPressureAvailable", "KernelOomAvailable", "ErpbridgeActive",
                    "CaddyActive", "ServiceFault", "DiskFault", "ContainerCountersAvailable",
                    "ServiceMemoryAvailable"):
            record[key] = False
        try:
            values = pairs(read_text(self.proc / "meminfo"))
            mem = {}
            for key in ("MemAvailable:", "MemTotal:", "SwapTotal:", "SwapFree:"):
                raw, unit = values[key].split()
                if unit != "kB":
                    raise ValueError("invalid memory unit")
                mem[key] = integer(integer(raw) * 1024)
            if mem["MemAvailable:"] > mem["MemTotal:"] or mem["SwapFree:"] > mem["SwapTotal:"]:
                raise ValueError("invalid memory range")
            record.update(MemoryAvailableBytes=mem["MemAvailable:"], MemoryTotalBytes=mem["MemTotal:"],
                          SwapUsedBytes=mem["SwapTotal:"] - mem["SwapFree:"])
        except (OSError, ValueError, KeyError):
            pass
        try:
            pressure = pairs(read_text(self.proc / "pressure/memory"))
            values = [float(dict(item.split("=", 1) for item in pressure[key].split())["avg10"])
                      for key in ("some", "full")]
            if not all(math.isfinite(value) and 0 <= value <= 100 for value in values):
                raise ValueError("invalid pressure")
            record.update(MemoryPressureSomeAvg10=values[0], MemoryPressureFullAvg10=values[1],
                          MemoryPressureAvailable=True)
        except (OSError, ValueError, KeyError):
            pass
        try:
            record["KernelOomKills"] = integer(pairs(read_text(self.proc / "vmstat"))["oom_kill"])
            record["KernelOomAvailable"] = True
        except (OSError, ValueError, KeyError):
            pass
        try:
            fs = self.statvfs("/")
            if (fs.f_blocks <= 0 or fs.f_files <= 0 or fs.f_frsize <= 0
                    or not 0 <= fs.f_bavail <= fs.f_bfree <= fs.f_blocks
                    or not 0 <= fs.f_ffree <= fs.f_files):
                raise ValueError("invalid filesystem counters")
            record.update(RootFreeBytes=fs.f_bavail * fs.f_frsize,
                          RootUsedPercent=100 * (fs.f_blocks - fs.f_bfree) / fs.f_blocks,
                          RootInodeUsedPercent=100 * (fs.f_files - fs.f_ffree) / fs.f_files)
        except (OSError, ValueError, ZeroDivisionError):
            pass
        memory_keys = ("ErpbridgeServiceMemoryBytes", "CaddyMemoryBytes", "AmaMemoryBytes")
        for index, unit in enumerate(SERVICES):
            try:
                fields = dict(line.split("=", 1) for line in self.runner([
                    "systemctl", "show", unit, "--property=ActiveState,MainPID,MemoryCurrent"
                ]).splitlines())
                if index < 2:
                    record[("ErpbridgeActive", "CaddyActive")[index]] = fields["ActiveState"] == "active"
                integer(fields["MainPID"])
                record[memory_keys[index]] = integer(fields["MemoryCurrent"])
            except (OSError, ValueError, KeyError, TimeoutError, subprocess.SubprocessError):
                pass
        record["ServiceMemoryAvailable"] = all(record[key] >= 0 for key in memory_keys)
        try:
            # Never request complete inspection data or any Config fields.
            state = json.loads(self.runner(["docker", "inspect", "--format",
                '{"Running":{{json .State.Running}},"Pid":{{json .State.Pid}}}', "erpbridge"]))
            if state["Running"] is True and integer(state["Pid"]) > 0:
                memory, kills = self.cgroup_counters(state["Pid"])
                record.update(ContainerMemoryBytes=memory, ContainerOomKills=kills,
                              ContainerCountersAvailable=True)
        except (OSError, ValueError, KeyError, TypeError, StopIteration, TimeoutError,
                RecursionError, subprocess.SubprocessError):
            pass
        try:
            status = integer(self.http())
            if 100 <= status <= 599:
                record["BackendHttpStatus"] = status
        except (OSError, ValueError, TimeoutError):
            pass
        record.update(backup(self.args.backup_status, now))
        return record, now


def next_state(record, now, path):
    previous = {}
    try:
        previous = json.loads(read_text(path, 1024))
        gap = (now - stamp(previous["time"])).total_seconds()
        for key in ("ServiceFault", "DiskFault"):
            if integer(previous[key]) > 2:
                raise ValueError("invalid state counter")
        if not 0 < gap <= 150:
            previous = {}
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        previous = {}
    service_bad = not record["ErpbridgeActive"] or not record["CaddyActive"] or record["BackendHttpStatus"] != 200
    disk_bad = (record["RootFreeBytes"] < 1024**3 or record["RootUsedPercent"] > 85
                or record["RootInodeUsedPercent"] > 85)
    state = {"time": record["TimeGenerated"]}
    for key, bad in (("ServiceFault", service_bad), ("DiskFault", disk_bad)):
        count = min(2, integer(previous.get(key, 0)) + 1) if bad else 0
        state[key] = count
        record[key] = count >= 2
    return state


def encode(record):
    line = (json.dumps(record, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    if len(line) > MAX_SAMPLE:
        raise ValueError("sample too large")
    return line


def write_sample(args, collector):
    state_path, output = Path(args.state_file), Path(args.output_dir)
    state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    with (state_path.parent / (state_path.name + ".lock")).open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        record, now = collector.sample()
        state = next_state(record, now, state_path)
        line = encode(record)
        output.mkdir(parents=True, exist_ok=True, mode=0o750)
        destination = output / ("health-" + now.date().isoformat() + ".jsonl")
        with destination.open("ab") as stream:
            if stream.tell() + len(line) > MAX_DAILY:
                raise ValueError("daily limit exceeded")
            stream.write(line)
        # Atomic state replacement is separate from JSONL rotation. Never rename logs.
        temporary = state_path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(state, stream, separators=(",", ":"))
        os.chmod(temporary, 0o600)
        temporary.replace(state_path)
        for path in output.glob("health-????-??-??.jsonl"):
            try:
                day = dt.date.fromisoformat(path.name[7:-6])
            except ValueError:
                continue
            # Keep the current UTC date and the preceding two dates only.
            # This also bounds retention after a backwards clock correction.
            if not now.date() - dt.timedelta(days=2) <= day <= now.date():
                path.unlink()
    return 0


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    modes = result.add_mutually_exclusive_group(required=True)
    modes.add_argument("--check", action="store_true")
    modes.add_argument("--once", action="store_true")
    result.add_argument("--vm-resource-id", default=os.environ.get("ERPBRIDGE_VM_RESOURCE_ID", ""))
    result.add_argument("--output-dir", default="/var/log/erpbridge-monitor")
    result.add_argument("--state-file", default="/var/lib/erpbridge-monitor/state.json")
    result.add_argument("--backup-status", default="/var/lib/erpbridge/backup-status.json")
    result.add_argument("--proc-root", default="/proc")
    result.add_argument("--cgroup-root", default="/sys/fs/cgroup")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    if (not args.vm_resource_id.startswith("/subscriptions/") or len(args.vm_resource_id) > 512
            or any(ord(char) < 33 or ord(char) > 126 for char in args.vm_resource_id)):
        print("monitor: valid VM resource ID required", file=sys.stderr)
        return 1
    try:
        collector = Collector(args)
        if args.check:
            record, now = collector.sample()
            next_state(record, now, args.state_file)
            sys.stdout.buffer.write(encode(record))
            return 0
        return write_sample(args, collector)
    except (OSError, ValueError, TypeError, KeyError):
        print("monitor: collection or write failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
