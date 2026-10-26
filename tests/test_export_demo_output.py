"""Local subprocess fixtures only. No Terraform providers are invoked."""
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.fixture = self.base / 'outputs.json'
        self.calls = self.base / 'calls'
        self.fake = self.base / 'terraform'
        self.fake.write_text('''#!/usr/bin/env python3
import json, os, sys
with open(os.environ['CALLS'], 'a') as f: f.write(' '.join(sys.argv[1:])+'\\n')
if os.environ.get('FAIL_TF'):
 print('PRIVATE_PROVIDER_SECRET', file=sys.stderr); sys.exit(1)
data=json.load(open(os.environ['FIXTURE']))
print(json.dumps(data[('vm' if 'erpnext' in sys.argv[1] else ('bridge_vm' if 'erpbridge-vm' in sys.argv[1] else 'root'))][sys.argv[-1]]))
''')
        self.fake.chmod(0o700)
        self.output = self.base / 'result.json'
        self.creds = self.base / 'credentials.json'
        self.mcp = self.base / 'mcp.json'
        self.env = dict(os.environ, TERRAFORM=str(self.fake), TF_DIR=str(self.base / 'root'),
                        ERPNEXT_TF_DIR=str(self.base / 'erpnext'),
                        ERPBRIDGE_VM_TF_DIR=str(self.base / 'erpbridge-vm'),
                        ERPBRIDGE_STAGED_ENV=str(self.base / 'runtime.env.staged'),
                        DEPLOYMENT_OUTPUT_FILE=str(self.output),
                        ERPNEXT_CREDENTIALS_FILE=str(self.creds), MCP_TOKEN_FILE=str(self.mcp),
                        FIXTURE=str(self.fixture), CALLS=str(self.calls))
        self.data = {'root': {
            'deployment_summary': {'erp_target': 'erpnext', 'resource_group': 'demo-rg',
                'erpbridge': {'url': 'https://bridge.test', 'mcp_url': 'https://bridge.test/mcp/', 'azure_url': 'https://bridge.azure.test'},
                'mockerp': {'url': 'https://mock.test', 'azure_url': 'https://mock.azure.test'},
                'erpnext': {'url': 'https://erp.test'}},
            'demo_tokens': {'api_auth_token': 'BRIDGE_SECRET', 'erp_primary_key': 'token API_KEY:API_SECRET', 'cloudflare_api_token': 'FORBIDDEN'},
            'image_references': {'erpbridge': 'bridge@sha256:123', 'mockerp': 'mock@sha256:456', 'azure_password': 'FORBIDDEN'},
            'cloudflare_domains': {'enabled': True, 'erpbridge': 'bridge.test', 'mockerp': 'mock.test', 'cloudflare_api_token': 'FORBIDDEN'},
            'erpbridge_hosting': {'hosting': 'aca', 'source_fenced': False,
                'canonical_hostname': 'bridge.test', 'dns_record_type': 'CNAME',
                'dns_record_content': 'bridge.azure.test', 'cname_check_applies': True,
                'cloudflare_api_token': 'FORBIDDEN'}},
            'bridge_vm': {'deployment_summary': {'name': 'erpbridge-vm', 'resource_group': 'demo-rg',
                'location': 'centralindia', 'url': 'https://bridge.test', 'public_ip': '192.0.2.9',
                'ssh_username': 'erpbridge', 'ssh_private_key_path': '~/.ssh/erpbridge-vm',
                'vm_size': 'Standard_B2ats_v2', 'os_disk_name': 'erpbridge-vm-osdisk',
                'resource_id': '/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/demo-rg/providers/Microsoft.Compute/virtualMachines/erpbridge-vm',
                'backup_storage_account': 'erpbridgebackups', 'private_key': 'FORBIDDEN'}},
            'vm': {'deployment_summary': {'name': 'demo-vm', 'resource_group': 'demo-rg', 'location': 'centralindia',
                'url': 'https://erp.test', 'public_ip': '192.0.2.1', 'ssh_username': 'operator',
                'resource_id': '/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/demo-rg/providers/Microsoft.Compute/virtualMachines/demo-vm',
                'ssh_private_key_path': '~/.ssh/demo', 'vm_size': 'Standard_B2as_v2', 'private_key': 'FORBIDDEN'}}}
        self.credentials = {'admin': {'username': 'Administrator', 'password': 'ADMIN_SECRET', 'db_password': 'FORBIDDEN'},
                            'integration': {'username': 'integration@test', 'api_key': 'API_KEY', 'api_secret': 'API_SECRET'},
                            'ssh_private_key': 'FORBIDDEN'}
        self.save(self.creds, self.credentials)
        self.save(self.mcp, {'tokens': {'client': {'token': 'MCP_SECRET', 'id': 'id', 'ssh_private_key': 'FORBIDDEN'}}, 'cloudflare_token': 'FORBIDDEN'})

    def save(self, path, data):
        path.write_text(json.dumps(data))
        path.chmod(0o600)

    def run_export(self):
        self.save(self.fixture, self.data)
        result = subprocess.run(['bash', str(ROOT / 'scripts/export-demo-output.sh')], env=self.env, capture_output=True, text=True)
        for secret in ('ADMIN_SECRET', 'API_KEY', 'API_SECRET', 'BRIDGE_SECRET', 'MCP_SECRET', 'FORBIDDEN', 'PRIVATE_PROVIDER_SECRET'):
            self.assertNotIn(secret, result.stdout + result.stderr)
        return result

    def test_success_repeat_private_and_allowlisted(self):
        first = {}
        for _ in range(2):
            result = self.run_export()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), str(self.output))
            self.assertEqual(result.stderr, '')
            artifact = json.loads(self.output.read_text())
            self.assertEqual(artifact['schema_version'], 2)
            self.assertEqual(artifact['urls']['erpnext'], 'https://erp.test')
            self.assertTrue(artifact['deployment']['erpnext']['resource_id'].endswith('/virtualMachines/demo-vm'))
            self.assertEqual(artifact['erpnext_credentials']['admin']['password'], 'ADMIN_SECRET')
            self.assertEqual(artifact['mcp_tokens']['client']['token'], 'MCP_SECRET')
            self.assertNotIn('FORBIDDEN', self.output.read_text())
            self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o600)
            if _ == 0:
                first = artifact
                self.output.chmod(0o644)
            else:
                artifact.pop('generated_at'); first.pop('generated_at')
                self.assertEqual(first, artifact)

    def test_hosting_metadata_is_recorded(self):
        self.assertEqual(self.run_export().returncode, 0)
        artifact = json.loads(self.output.read_text())
        self.assertEqual(artifact['urls']['erpbridge_hosting'], 'aca')
        self.assertEqual(artifact['urls']['erpbridge_record_type'], 'CNAME')
        self.assertEqual(artifact['urls']['erpbridge_azure_fallback_state'], 'active-fallback')
        self.assertEqual(artifact['deployment']['erpbridge']['hosting'], 'aca')
        self.assertEqual(artifact['deployment']['erpbridge']['staged_env_comparison'], 'absent')
        self.assertNotIn('erpbridge_vm', artifact['deployment'])

    def test_fenced_and_vm_hosting_add_vm_metadata(self):
        self.data['root']['erpbridge_hosting'].update(
            {'hosting': 'vm', 'source_fenced': True, 'dns_record_type': 'A',
             'dns_record_content': '192.0.2.9', 'cname_check_applies': False})
        self.assertEqual(self.run_export().returncode, 0)
        artifact = json.loads(self.output.read_text())
        self.assertEqual(artifact['urls']['erpbridge_hosting'], 'vm')
        self.assertEqual(artifact['urls']['erpbridge_record_type'], 'A')
        self.assertEqual(artifact['urls']['erpbridge_azure_fallback_state'],
                         'retired-fenced-diagnostic-only')
        vm = artifact['deployment']['erpbridge_vm']
        self.assertEqual(vm['public_ip'], '192.0.2.9')
        self.assertEqual(vm['os_disk_name'], 'erpbridge-vm-osdisk')
        self.assertTrue(vm['resource_id'].endswith('/virtualMachines/erpbridge-vm'))
        self.assertNotIn('FORBIDDEN', self.output.read_text())

    def test_a_record_must_point_at_the_vm(self):
        self.data['root']['erpbridge_hosting'].update(
            {'hosting': 'vm', 'source_fenced': True, 'dns_record_type': 'A',
             'dns_record_content': '198.51.100.4'})
        result = self.run_export()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('does not point at the VM address', result.stderr)

    def test_staged_credentials_must_match(self):
        staged = self.base / 'runtime.env.staged'
        staged.write_text('API_AUTH_TOKEN=BRIDGE_SECRET\nERP_PRIMARY_KEY=token API_KEY:API_SECRET\n')
        staged.chmod(0o600)
        self.assertEqual(self.run_export().returncode, 0)
        self.assertEqual(json.loads(self.output.read_text())['deployment']['erpbridge']
                         ['staged_env_comparison'], 'match')
        staged.write_text('API_AUTH_TOKEN=DIFFERENT\nERP_PRIMARY_KEY=token API_KEY:API_SECRET\n')
        staged.chmod(0o600)
        result = self.run_export()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('does not match the authoritative value', result.stderr)
        self.assertNotIn('DIFFERENT', result.stdout + result.stderr)

    def test_staged_environment_must_be_private(self):
        staged = self.base / 'runtime.env.staged'
        staged.write_text('API_AUTH_TOKEN=BRIDGE_SECRET\nERP_PRIMARY_KEY=token API_KEY:API_SECRET\n')
        staged.chmod(0o644)
        result = self.run_export()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('must not be group or other accessible', result.stderr)

    def test_missing_credentials_fails_without_replacing(self):
        self.output.write_text('previous')
        self.creds.unlink()
        self.assertNotEqual(self.run_export().returncode, 0)
        self.assertEqual(self.output.read_text(), 'previous')

    def test_mismatch_or_incomplete_fails(self):
        for token in ('token wrong:wrong', ''):
            self.data['root']['demo_tokens']['erp_primary_key'] = token
            self.assertNotEqual(self.run_export().returncode, 0)
        self.data['root']['demo_tokens']['erp_primary_key'] = 'token API_KEY:API_SECRET'
        self.credentials['integration'].pop('api_secret')
        self.save(self.creds, self.credentials)
        self.assertNotEqual(self.run_export().returncode, 0)

    def test_url_mismatch_fails(self):
        self.data['vm']['deployment_summary']['url'] = 'https://different.test'
        self.assertNotEqual(self.run_export().returncode, 0)

    def test_insecure_inputs_fail(self):
        for path in (self.creds, self.mcp):
            path.chmod(0o644)
            self.assertNotEqual(self.run_export().returncode, 0)
            path.chmod(0o600)

    def test_mockerp_legacy_does_not_query_vm_or_credentials(self):
        del self.data['root']['deployment_summary']['erp_target']
        self.creds.unlink()
        self.assertEqual(self.run_export().returncode, 0)
        artifact = json.loads(self.output.read_text())
        self.assertEqual(artifact['schema_version'], 1)
        self.assertNotIn('erpnext_credentials', artifact)
        self.assertNotIn('erpnext', self.calls.read_text())

    def test_provider_error_is_safe(self):
        self.env['FAIL_TF'] = '1'
        self.assertNotEqual(self.run_export().returncode, 0)


if __name__ == '__main__':
    unittest.main()
