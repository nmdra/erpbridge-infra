"""Container-only recovery. Accept code-owned bundle selectors, never arbitrary paths.

Bundles contain secrets and must come from this trusted demo. This is not a safe
SQL importer for files from third parties. Restore never copies source DB routing.
"""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile

SITES = Path('/home/frappe/frappe-bench/sites')
BUNDLE_PATTERN = r'backup-[a-f0-9]{24}'


def private_json(path, data):
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def regular(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('Expected a regular protected backup file')
    return path


def bundle_path(selector):
    if not re.fullmatch(BUNDLE_PATTERN, selector):
        raise ValueError('Use the exact backup identifier, not a path')
    base = SITES / 'frontend/private/backups'
    path = base / selector
    if path.resolve() != SITES.resolve() / 'frontend/private/backups' / selector:
        raise ValueError('Backup symlinks are forbidden')
    return path


def backup():
    selector = 'backup-' + secrets.token_hex(12)
    bundle = bundle_path(selector)
    bundle.mkdir(parents=True, mode=0o700)
    # Explicit names make the bundle unambiguous. Ignore configured partial backups.
    files = {'database': 'database.sql.gz', 'public': 'public.tar.gz',
             'private': 'private.tar.gz', 'config': 'site_config.json'}
    subprocess.run(['bench', '--site', 'frontend', 'backup', '--with-files', '--compress',
                    '--ignore-backup-conf', '--backup-path', str(bundle),
                    '--backup-path-db', str(bundle / files['database']),
                    '--backup-path-files', str(bundle / files['public']),
                    '--backup-path-private-files', str(bundle / files['private'])],
                   check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    # A Frappe config backup can redact secrets. Retain the actual private config.
    shutil.copyfile(regular(SITES / 'frontend/site_config.json'), bundle / files['config'])
    for name in files.values(): regular(bundle / name).chmod(0o600)
    private_json(bundle / 'manifest.json', files)
    return selector


def prepare_restore(site, selector, confirm):
    if not confirm or not re.fullmatch(r'restore-[a-z0-9][a-z0-9-]{0,48}', site):
        raise ValueError('Explicit disposable restore target required')
    target = SITES / site
    if target.resolve() != SITES.resolve() / site or not target.is_dir():
        raise ValueError('Pre-create a separate disposable site, without symlinks')
    bundle = bundle_path(selector)
    manifest = json.loads(regular(bundle / 'manifest.json').read_text())
    expected = {'database': 'database.sql.gz', 'public': 'public.tar.gz',
                'private': 'private.tar.gz', 'config': 'site_config.json'}
    if manifest != expected: raise ValueError('Untrusted bundle file selectors')
    for name in expected.values(): regular(bundle / name)
    source = json.loads((bundle / 'site_config.json').read_text())
    frontend = json.loads(regular(SITES / 'frontend/site_config.json').read_text())
    config = json.loads(regular(target / 'site_config.json').read_text())
    if not config.get('db_name') or config['db_name'] in (source.get('db_name'), frontend.get('db_name')):
        raise ValueError('Restore DB must be separate from source and frontend')
    if not isinstance(source.get('encryption_key'), str) or not source['encryption_key']:
        raise ValueError('Source encryption_key missing; refuse unusable DB secrets')
    # Only cryptographic keys are portable. Preserve target DB credentials, hosts,
    # routing and other settings. Never import source db_name/db_password.
    for key in ('encryption_key', 'backup_encryption_key'):
        if key in source: config[key] = source[key]
    private_json(target / 'site_config.json', config)
    return bundle, source


def restore(site, selector, confirm):
    bundle, source = prepare_restore(site, selector, confirm)
    # Pinned Frappe v16 API. Root password stays in the one-shot tool environment.
    sys.path.insert(0, str(Path.cwd() / 'apps/frappe'))
    import frappe  # pyright: ignore[reportMissingImports]
    from frappe.commands.site import _restore  # pyright: ignore[reportMissingImports]
    # Match bench CLI's working directory for Frappe's relative site paths.
    previous = Path.cwd()
    os.chdir(SITES)
    frappe.init(site)
    try:
        _restore(site=site, sql_file_path=str(bundle / 'database.sql.gz'),
                 encryption_key=source.get('backup_encryption_key'),
                 db_root_username='root', db_root_password=os.environ['DB_ROOT_PASSWORD'],
                 with_public_files=str(bundle / 'public.tar.gz'),
                 with_private_files=str(bundle / 'private.tar.gz'), force=False)
    finally:
        frappe.destroy()
        os.chdir(previous)
    # Restore creates the target config again. Reapply keys without DB routing.
    prepare_restore(site, selector, confirm)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['backup', 'restore'])
    parser.add_argument('--site', default='')
    parser.add_argument('--bundle', default='')
    parser.add_argument('--confirm-disposable-restore', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.command == 'backup': print('Protected backup: ' + backup())
        else:
            restore(args.site, args.bundle, args.confirm_disposable_restore)
            print('Disposable restore complete. Verify encrypted credentials and records before use.')
    except Exception:
        print('Recovery failed. Inspect protected disposable target and logs deliberately.', file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__': sys.exit(main())
