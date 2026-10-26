import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
class BootstrapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('bootstrap', ROOT / 'demo/erpnext/bootstrap.py')
        assert spec and spec.loader
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    def test_private_atomic_credentials_and_no_rotation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'credentials.json'
            first = self.mod.credentials(path, fresh=True)
            self.assertEqual(0o600, path.stat().st_mode & 0o777)
            self.assertEqual(first, self.mod.credentials(path, fresh=False))
            self.assertEqual('Administrator', first['admin']['username'])
            self.assertNotEqual('Administrator', first['integration']['username'])
            path.unlink()
            with self.assertRaises(ValueError): self.mod.credentials(path, fresh=False)

    def test_refuse_broad_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'credentials.json'
            self.mod.credentials(path, fresh=True)
            os.chmod(path, 0o644)
            with self.assertRaises(ValueError): self.mod.credentials(path, fresh=False)

    def test_create_site_runs_installer_from_sites_directory(self):
        import runpy
        import sys
        from unittest.mock import Mock, patch
        with tempfile.TemporaryDirectory() as directory:
            bench = Path(directory)
            (bench / 'sites').mkdir()
            frappe = Mock()
            frappe.destroy = Mock()
            installer = Mock()
            installer._new_site = Mock(side_effect=lambda *a, **k: self.assertEqual(bench / 'sites', Path.cwd()))
            previous = Path.cwd()
            try:
                os.chdir(bench)
                with patch.dict(sys.modules, {'frappe': frappe, 'frappe.installer': installer}), \
                     patch.dict(os.environ, {'DB_ROOT_PASSWORD': 'fixture-root', 'ADMIN_PASSWORD': 'fixture-admin'}):
                    runpy.run_path(str(ROOT / 'demo/erpnext/create_site.py'))
                installer._new_site.assert_called_once()
                frappe.destroy.assert_called_once()
            finally:
                os.chdir(previous)

    def test_ssh_strict_and_no_secret_arguments(self):
        command = self.mod.ssh_command({'public_ip': '192.0.2.1', 'ssh_username': 'demo', 'ssh_private_key_path': '/tmp/key'})
        self.assertIn('StrictHostKeyChecking=yes', command)
        self.assertIn('BatchMode=yes', command)
        self.assertFalse(any('password' in value for value in command))

if __name__ == '__main__': unittest.main()

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        import json
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location('recovery', ROOT / 'demo/erpnext/recovery.py')
        assert spec and spec.loader
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.sites = Path(self.temp.name)
        self.patch = patch.object(self.mod, 'SITES', self.sites)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.selector = 'backup-' + 'a' * 24
        self.bundle = self.mod.bundle_path(self.selector)
        self.bundle.mkdir(parents=True)
        self.manifest = {'database': 'database.sql.gz', 'public': 'public.tar.gz',
                         'private': 'private.tar.gz', 'config': 'site_config.json'}
        for name in self.manifest.values(): (self.bundle / name).write_text('fixture')
        (self.bundle / 'manifest.json').write_text(json.dumps(self.manifest))
        self.source = {'db_name': 'source-db', 'db_password': 'source-only-fixture',
                       'encryption_key': 'source-encryption-fixture', 'db_host': 'untrusted'}
        (self.bundle / 'site_config.json').write_text(json.dumps(self.source))
        (self.sites / 'frontend/site_config.json').write_text(json.dumps({'db_name': 'frontend-db'}))
        self.target = self.sites / 'restore-check'
        self.target.mkdir()
        self.config = {'db_name': 'isolated-db', 'db_password': 'target-fixture', 'db_host': 'db'}
        (self.target / 'site_config.json').write_text(json.dumps(self.config))

    def test_config_keys_restored_without_source_database_routing(self):
        import json
        self.mod.prepare_restore('restore-check', self.selector, True)
        config = json.loads((self.target / 'site_config.json').read_text())
        self.assertEqual(dict(self.config, encryption_key='source-encryption-fixture'), config)
        self.assertEqual(0o600, (self.target / 'site_config.json').stat().st_mode & 0o777)

    def test_restore_refuses_frontend_paths_and_missing_confirmation(self):
        for site, selector, confirm in [('frontend', self.selector, True),
                                        ('restore-check', '../outside', True),
                                        ('restore-check', self.selector, False)]:
            with self.assertRaises(ValueError): self.mod.prepare_restore(site, selector, confirm)

    def test_refuses_shared_database_missing_key_and_manifest_traversal(self):
        import json
        for source, manifest, config in [
            (dict(self.source, encryption_key=''), self.manifest, self.config),
            (self.source, dict(self.manifest, database='../database.sql.gz'), self.config),
            (self.source, self.manifest, dict(self.config, db_name='frontend-db')),
            (self.source, self.manifest, dict(self.config, db_name='source-db'))]:
            (self.bundle / 'site_config.json').write_text(json.dumps(source))
            (self.bundle / 'manifest.json').write_text(json.dumps(manifest))
            (self.target / 'site_config.json').write_text(json.dumps(config))
            with self.assertRaises(ValueError): self.mod.prepare_restore('restore-check', self.selector, True)

    def test_refuses_symlinked_backup_file(self):
        path = self.bundle / 'database.sql.gz'
        path.unlink()
        path.symlink_to(self.target / 'site_config.json')
        with self.assertRaises(ValueError): self.mod.prepare_restore('restore-check', self.selector, True)

    def test_restore_shell_rejects_path_before_docker(self):
        import subprocess
        result = subprocess.run(['bash', str(ROOT / 'demo/erpnext/restore.sh'),
                                 '--confirm-disposable-restore', 'restore-check', '../bad'], capture_output=True)
        self.assertEqual(2, result.returncode)
        self.assertNotIn(b'fixture', result.stderr)

    def test_restore_api_receives_password_in_process_only_and_reapplies_key(self):
        import sys
        from unittest.mock import Mock, patch
        frappe = Mock()
        frappe.init = Mock()
        frappe.destroy = Mock()
        commands = Mock()
        previous = Path.cwd()
        commands._restore = Mock(side_effect=lambda **kw: self.assertEqual(self.sites, Path.cwd()))
        with patch.dict(sys.modules, {'frappe': frappe, 'frappe.commands.site': commands}), \
             patch.dict(os.environ, {'DB_ROOT_PASSWORD': 'fixture-root'}):
            self.mod.restore('restore-check', self.selector, True)
        self.assertEqual(previous, Path.cwd())
        kwargs = commands._restore.call_args.kwargs
        self.assertEqual('restore-check', kwargs['site'])
        self.assertEqual('fixture-root', kwargs['db_root_password'])
        self.assertFalse(kwargs['force'])
        frappe.destroy.assert_called_once()
