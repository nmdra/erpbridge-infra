import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any
import unittest

ROOT = Path(__file__).resolve().parents[1]
ADMIN = 'fixture-admin-not-a-real-token'
ERP = 'token fixture-key:fixture-secret'


class ConnectBridgectlTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / 'scripts').mkdir()
        (self.root / 'artifacts').mkdir()
        self.script = self.root / 'scripts/connect-bridgectl.sh'
        shutil.copyfile(ROOT / 'scripts/connect-bridgectl.sh', self.script)
        self.artifact = self.root / 'artifacts/deployment-output.json'
        self.deployment: dict[str, dict[str, Any]] = {
            'deployment': {'erp_target': 'erpnext'},
            'urls': {
                'erpbridge': 'https://erpbridge.nimendra.online',
                'erpnext': 'https://erpnext.nimendra.online',
            },
            'demo_tokens': {'api_auth_token': ADMIN, 'erp_primary_key': ERP},
        }
        self.save(self.deployment)

    def save(self, value):
        self.artifact.write_text(json.dumps(value))
        self.artifact.chmod(0o600)

    def run_bash(self, body, trace=False):
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith('BRIDGE_') and key != 'ERP_PRIMARY_KEY'}
        return subprocess.run(
            ['bash', '-xc' if trace else '-c', body, 'loader-test', str(self.script)],
            capture_output=True, text=True, env=environment, timeout=15,
        )

    def assert_no_credentials(self, result):
        for value in (ADMIN, ERP):
            self.assertNotIn(value, result.stdout + result.stderr)

    def test_loads_credentials_clears_stale_overrides_and_disables_trace(self):
        before = self.artifact.read_bytes()
        result = self.run_bash('''
export BRIDGE_SERVER=https://stale.example BRIDGE_API_KEY=old
source "$1" || exit 1
[[ "$BRIDGE_CONTEXT" == erpbridge-demo ]] || exit 2
[[ "$BRIDGE_API_TOKEN" == fixture-admin-not-a-real-token ]] || exit 3
[[ "$ERP_PRIMARY_KEY" == 'token fixture-key:fixture-secret' ]] || exit 4
[[ -z ${BRIDGE_SERVER+x} && -z ${BRIDGE_API_KEY+x} ]] || exit 5
[[ $- != *x* ]] || exit 6
''', trace=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_no_credentials(result)
        self.assertEqual(self.artifact.read_bytes(), before)

    def test_invalid_inputs_leave_existing_credentials_unchanged(self):
        cases = []
        for area, key, value in (
            ('deployment', 'erp_target', 'mockerp'),
            ('urls', 'erpbridge', 'https://wrong.example'),
            ('urls', 'erpnext', 1),
            ('demo_tokens', 'api_auth_token', ''),
            ('demo_tokens', 'erp_primary_key', 'bad\ncredential'),
        ):
            deployment = copy.deepcopy(self.deployment)
            deployment[area][key] = value
            cases.append(deployment)
        for deployment in cases:
            with self.subTest(deployment=deployment):
                self.save(deployment)
                self.assert_failed_without_environment_change()

    def assert_failed_without_environment_change(self):
        result = self.run_bash('''
export BRIDGE_API_TOKEN=existing ERP_PRIMARY_KEY=existing-erp BRIDGE_SERVER=https://existing.example
if source "$1"; then exit 10; fi
[[ "$BRIDGE_API_TOKEN" == existing && "$ERP_PRIMARY_KEY" == existing-erp ]] || exit 11
[[ "$BRIDGE_SERVER" == https://existing.example ]] || exit 12
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Connection settings were not changed', result.stderr)
        self.assert_no_credentials(result)

    def test_rejects_missing_world_readable_and_symlink_artifacts(self):
        self.artifact.chmod(0o644)
        self.assert_failed_without_environment_change()
        self.artifact.unlink()
        self.assert_failed_without_environment_change()
        target = self.root / 'saved.json'
        target.write_text(json.dumps(self.deployment))
        target.chmod(0o600)
        self.artifact.symlink_to(target)
        self.assert_failed_without_environment_change()

    def test_readonly_context_does_not_export_new_credentials(self):
        result = self.run_bash('''
export BRIDGE_API_TOKEN=existing ERP_PRIMARY_KEY=existing-erp
declare -rx BRIDGE_CONTEXT=another-context
if source "$1"; then exit 10; fi
[[ "$BRIDGE_API_TOKEN" == existing && "$ERP_PRIMARY_KEY" == existing-erp ]] || exit 11
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_no_credentials(result)

    def test_execution_requires_sourcing(self):
        result = subprocess.run(['bash', str(self.script)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertIn('Source this script', result.stderr)
        self.assert_no_credentials(result)


if __name__ == '__main__':
    unittest.main()
