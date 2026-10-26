#!/usr/bin/env python3
"""Monitoring-only installer. No runtime environment or database content is read."""
import argparse
import ast
import base64
import datetime
import hashlib
import grp
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

ASSETS = {
    'monitor.py': '/opt/erpbridge/monitor.py',
    'erpbridge-monitor.service': '/etc/systemd/system/erpbridge-monitor.service',
    'erpbridge-monitor.timer': '/etc/systemd/system/erpbridge-monitor.timer',
    'journald-monitoring.conf': '/etc/systemd/journald.conf.d/erpbridge-monitoring.conf',
}
VM_ID = re.compile(r'/subscriptions/[0-9a-fA-F-]{36}/resourceGroups/[A-Za-z0-9_.()-]+/providers/Microsoft.Compute/virtualMachines/[A-Za-z0-9_.-]+')
MAX_ASSET = 131072
# Hash only non-secret configuration. Private data is stat-only.
HASH_PATHS = ('/etc/caddy/Caddyfile', '/etc/systemd/system/erpbridge.service',
              '/etc/systemd/system/caddy.service', '/etc/systemd/system/erpbridge-backup.service',
              '/etc/systemd/system/erpbridge-backup.timer', '/opt/erpbridge/backup.py',
              '/etc/erpbridge/container-uid', '/etc/erpbridge/disk-identity')
META_PATHS = ('/etc/erpbridge/runtime.env', '/etc/erpbridge/backup.env', '/var/lib/erpbridge/data/erpbridge.db',
              '/etc/erpbridge',
              '/var/lib/erpbridge/data')


def snapshot(root=Path('/')):
    result = {}
    for name in HASH_PATHS + META_PATHS:
        path = root / name.lstrip('/')
        try:
            s = path.lstat()
            entry: dict = dict(mode=stat.S_IMODE(s.st_mode), uid=s.st_uid, gid=s.st_gid,
                         inode=s.st_ino)
            # Live SQLite writes/checkpoints and directory entries can change
            # during installation. Compare protection and identity, not content.
            if not stat.S_ISDIR(s.st_mode) and name != '/var/lib/erpbridge/data/erpbridge.db':
                entry.update(size=s.st_size, mtime_ns=s.st_mtime_ns)
            if name in HASH_PATHS and stat.S_ISREG(s.st_mode):
                if s.st_size > MAX_ASSET:
                    raise ValueError('Configuration exceeds safety bound')
                entry['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            result[name] = entry
        except FileNotFoundError:
            result[name] = None
    return result


def validate(payload):
    if not isinstance(payload, dict) or set(payload) != set(ASSETS):
        raise ValueError('Unexpected asset list')
    decoded = {}
    for name, encoded in payload.items():
        if not isinstance(encoded, str) or len(encoded) > MAX_ASSET * 2:
            raise ValueError('Asset too large')
        data = base64.b64decode(encoded, validate=True)
        if not data or len(data) > MAX_ASSET or b'\x00' in data:
            raise ValueError('Invalid asset')
        data.decode('utf-8')
        decoded[name] = data
    ast.parse(decoded['monitor.py'], filename='monitor.py')
    if decoded['journald-monitoring.conf'] != b'[Journal]\nStorage=persistent\nSystemMaxUse=64M\nRuntimeMaxUse=64M\nMaxRetentionSec=7day\n':
        raise ValueError('Unexpected journald settings')
    return decoded


def write_file(path, data, mode):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError('Symlink destination refused')
    if path.exists() and path.read_bytes() == data:
        os.chmod(path, mode)
        os.chown(path, 0, 0)
        return False
    fd, temporary = tempfile.mkstemp(prefix='.monitor-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.chown(temporary, 0, 0)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True


def systemctl(*arguments):
    result = subprocess.run(['systemctl', *arguments], capture_output=True, timeout=30)
    if result.returncode:
        raise ValueError('Systemd operation failed')


def install(payload, vm_id, root=Path('/'), runner=systemctl):
    assets = validate(payload)
    if not VM_ID.fullmatch(vm_id):
        raise ValueError('Invalid VM resource id')
    before = snapshot(root)
    # AMA mdsd uses syslog. Do not expose root-private state to that group.
    log_dir = root / 'var/log/erpbridge-monitor'
    state_dir = root / 'var/lib/erpbridge-monitor'
    if log_dir.is_symlink() or state_dir.is_symlink():
        raise ValueError('Symlink monitoring directory refused')
    log_gid = grp.getgrnam('syslog').gr_gid
    log_dir.mkdir(parents=True, exist_ok=True, mode=0o2750)
    os.chown(log_dir, 0, log_gid)
    os.chmod(log_dir, 0o2750)
    for path in log_dir.glob('health-????-??-??.jsonl'):
        if path.is_symlink() or not path.is_file():
            raise ValueError('Invalid monitoring log')
        os.chown(path, 0, log_gid)
        os.chmod(path, 0o640)
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chown(state_dir, 0, 0)
    os.chmod(state_dir, 0o700)
    journal_changed = False
    for name, destination in ASSETS.items():
        changed = write_file(root / destination.lstrip('/'), assets[name],
                             0o755 if name.endswith('.py') else 0o644)
        if name == 'journald-monitoring.conf':
            journal_changed = changed
    write_file(root / 'etc/erpbridge/monitor.env',
               ('ERPBRIDGE_VM_RESOURCE_ID=' + vm_id + '\n').encode(), 0o600)
    runner('daemon-reload')
    if journal_changed:
        runner('restart', 'systemd-journald.service')
    runner('enable', '--now', 'erpbridge-monitor.timer')
    after = snapshot(root)
    if before != after:
        raise ValueError('Protected workload metadata changed during installation')
    evidence = dict(before=before, after=after, preserved=True)
    write_file(root / 'var/lib/erpbridge-monitor/install-evidence.json',
               (json.dumps(evidence, sort_keys=True) + '\n').encode(), 0o600)
    return {'installed': True, 'preserved': True}


def diagnostic_status():
    result = {}
    for key, unit in [('monitor_timer', 'erpbridge-monitor.timer'),
                      ('monitor_service', 'erpbridge-monitor.service')]:
        response = subprocess.run(['systemctl', 'is-active', unit], capture_output=True,
                                  text=True, timeout=5)
        value = response.stdout.strip()
        result[key] = value if value in ('active', 'inactive', 'failed', 'activating') else 'unknown'
    now = datetime.datetime.now(datetime.timezone.utc)
    try:
        path = Path('/var/lib/erpbridge/backup-status.json')
        with path.open('rb') as stream:
            raw = stream.read(8193)
        if len(raw) > 8192:
            raise ValueError()
        timestamp = json.loads(raw)['last_success']
        age = (now - datetime.datetime.fromisoformat(timestamp.replace('Z', '+00:00'))).total_seconds()
        result['backup_age_seconds'] = int(age) if age >= 0 else None
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        result['backup_age_seconds'] = None
    # Use file metadata only. Never return or dump a telemetry record.
    try:
        files = list(Path('/var/log/erpbridge-monitor').glob('health-????-??-??.jsonl'))
        result['last_sample_age_seconds'] = max(0, int(now.timestamp() - max(
            p.stat().st_mtime for p in files if p.is_file() and p.stat().st_size)))
    except (OSError, ValueError):
        result['last_sample_age_seconds'] = None
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--confirm', action='store_true')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--vm-resource-id')
    args = parser.parse_args()
    if args.status:
        print(json.dumps(diagnostic_status(), sort_keys=True))
        return
    if not args.check and (not args.confirm or os.geteuid() != 0):
        raise ValueError('Root and explicit --confirm are required')
    raw = sys.stdin.read(MAX_ASSET * 8 + 1)
    if len(raw) > MAX_ASSET * 8:
        raise ValueError('Payload too large')
    try:
        payload = json.loads(raw)
    except ValueError:
        raise ValueError("Invalid payload JSON") from None
    validate(payload)
    if not args.vm_resource_id or not VM_ID.fullmatch(args.vm_resource_id):
        raise ValueError('Invalid VM resource id')
    if args.check:
        print(json.dumps({'valid': True}))
    else:
        print(json.dumps(install(payload, args.vm_resource_id), sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Monitoring operation failed; diagnostics suppressed.', file=sys.stderr)
        sys.exit(1)
