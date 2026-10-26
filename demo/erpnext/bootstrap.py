#!/usr/bin/env python3
"""Local SSH deployment driver. No Terraform apply, passwords in argv, or host-key bypass."""
import argparse
import base64
import io
import json
import os
from pathlib import Path
import secrets
import shlex
import stat
import subprocess
import sys
import tarfile
import tempfile

HERE = Path(__file__).resolve().parent
REMOTE = '/opt/erpnext-demo'

def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)

def credentials(path, fresh=False):
    path = Path(path)
    if path.exists():
        if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise ValueError('Credential file must be regular mode 0600')
        value = json.loads(path.read_text())
        if value['admin']['username'] != 'Administrator' or value['integration']['username'] == 'Administrator':
            raise ValueError('Invalid identity contract')
        for role in ['admin', 'integration']:
            for key in ['api_key', 'api_secret']:
                if not value[role][key]: raise ValueError('Incomplete credentials')
        if not value['admin']['password']: raise ValueError('Missing administrator password')
        return value
    if not fresh: raise ValueError('Saved credentials missing; deliberate recovery required')
    value = {
        'admin': {'username': 'Administrator', 'password': secrets.token_urlsafe(32)},
        'integration': {'username': 'erpbridge-demo@example.invalid'}
    }
    for role in value.values():
        role.update(api_key=secrets.token_hex(15), api_secret=secrets.token_urlsafe(32))
    atomic_json(path, value)
    return value

def ssh_command(summary):
    host = summary['public_ip']
    user = summary['ssh_username']
    if not host or any(c not in '0123456789abcdefABCDEF.:' for c in host):
        raise ValueError('Invalid VM IP')
    if not user or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in user):
        raise ValueError('Invalid SSH user')
    return ['ssh', '-o', 'StrictHostKeyChecking=yes', '-o', 'BatchMode=yes',
            '-o', 'ConnectTimeout=20', '-i', str(Path(summary['ssh_private_key_path']).expanduser()),
            user + '@' + host]

def run(command, data=None, timeout=1800):
    result = subprocess.run(command, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if result.returncode:
        # Remote diagnostics can contain environment values. Do not echo them.
        raise ValueError('Deployment command failed; inspect protected VM logs deliberately')
    return result.stdout

def local_main(args):
    summary = json.loads(run(['terraform', '-chdir=terraform-erpnext', 'output', '-json', 'deployment_summary']))
    ssh = ssh_command(summary)
    local_path = Path(args.credentials_file)
    existing = credentials(local_path) if local_path.exists() else None
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode='w') as tar:
        # Deploy code only. Never upload local backups, environment, or credential files.
        for name in ['bootstrap.py', 'create_site.py', 'setup_site.py', 'permissions.py', 'recovery.py',
                     'compose.yaml', 'Caddyfile', 'backup.sh', 'restore.sh']:
            tar.add(HERE / name, arcname=name)
        request = json.dumps({'expected': existing}).encode()
        info = tarfile.TarInfo('request.json')
        info.size = len(request)
        info.mode = 0o600
        tar.addfile(info, io.BytesIO(request))
    run(ssh + ['sudo install -d -m 700 ' + REMOTE + ' && sudo tar -xf - -C ' + REMOTE], archive.getvalue())
    run(ssh + ['sudo python3 ' + REMOTE + '/bootstrap.py --remote'])
    result = json.loads(run(ssh + ['sudo cat ' + REMOTE + '/credentials.json']))
    if existing is not None and existing != result: raise ValueError('Credential mismatch')
    atomic_json(local_path, result)
    print('ERPNext bootstrap complete. Protected credentials: ' + str(local_path))

def remote_main():
    os.chdir(REMOTE)
    os.umask(0o077)
    # Serialize bootstrap, including credential creation.
    import fcntl
    with open('.bootstrap.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        expected = json.loads(Path('request.json').read_text())['expected']
        known = Path('credentials.json').exists()
        if known and expected is None:
            raise ValueError('Local credentials missing for existing deployment')
        if not known and (expected is not None or Path('.env').exists()):
            raise ValueError('Remote credentials missing; no automatic reset')
        value = credentials('credentials.json', fresh=not known)
        if expected is not None and value != expected: raise ValueError('Saved credentials differ')
        env = Path('.env')
        if not env.exists():
            env.write_text('COMPOSE_PROJECT_NAME=erpnext-demo\nDB_ROOT_PASSWORD=' +
                           secrets.token_urlsafe(40) + '\nADMIN_PASSWORD=' + value['admin']['password'] + '\n')
            env.chmod(0o600)
        if env.is_symlink() or not env.is_file() or stat.S_IMODE(env.stat().st_mode) != 0o600:
            raise ValueError('Environment permissions unsafe')
        compose = ['docker', 'compose', '--env-file', '.env', '-f', 'compose.yaml']
        run(compose + ['config', '--quiet'])
        run(compose + ['up', '-d'], timeout=1800)
        # Execute the reviewed console program via stdin, not secret-bearing argv.
        run(compose + ['exec', '-T', 'backend', 'rm', '-f',
                       'sites/frontend/private/demo-bootstrap-ok'])
        source = Path('setup_site.py').read_text()
        prelude = ('credentials = ' + repr(value) + '\n' +
                   'permissions_source = ' + repr(Path('permissions.py').read_text()) + '\n')
        encoded = base64.b64encode((prelude + source).encode()).decode()
        console = ("exec(__import__('base64').b64decode(" + repr(encoded) + "))\n").encode()
        run(compose + ['exec', '-T', 'backend', 'bench', '--site', 'frontend', 'console'], console)
        # Console shells may return zero after a Python exception. Require a marker.
        run(compose + ['exec', '-T', 'backend', 'test', '-f',
                     'sites/frontend/private/demo-bootstrap-ok'])
        run(['install', '-o', 'root', '-g', 'root', '-m', '644', 'Caddyfile', '/etc/caddy/Caddyfile'])
        run(['systemctl', 'enable', '--now', 'caddy'])
        run(['systemctl', 'reload', 'caddy'])

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--credentials-file', default='artifacts/erpnext-credentials.json')
    parser.add_argument('--remote', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        remote_main() if args.remote else local_main(args)
    except (ValueError, OSError, KeyError, subprocess.TimeoutExpired):
        print('ERPNext bootstrap failed. No automatic credential reset. Check protected VM state.', file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__': sys.exit(main())
