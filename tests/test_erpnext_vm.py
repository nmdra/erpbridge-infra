"""Named-VM lifecycle tests with fake local executables, not Azure."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RESOURCE_ID = ('/subscriptions/11111111-2222-3333-4444-555555555555'
               '/resourceGroups/demo-rg/providers/Microsoft.Compute/virtualMachines/demo-vm')


class VMTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.calls = self.base / 'calls'
        fake = self.base / 'fake'
        fake.write_text('''#!/usr/bin/env python3
import json, os, sys
name=os.path.basename(sys.argv[0])
with open(os.environ['CALLS'], 'a') as f: f.write(json.dumps([name]+sys.argv[1:])+'\\n')
if os.environ.get('FAIL') == name:
 print('PRIVATE_PROVIDER_SECRET', file=sys.stderr); sys.exit(1)
if name == 'terraform':
 print(os.environ.get('SUMMARY', json.dumps({'name':'demo-vm','resource_group':'demo-rg',
  'resource_id':os.environ['RESOURCE_ID'],'url':os.environ.get('URL','https://erp.test')})))
elif name == 'az' and 'get-instance-view' in sys.argv:
 from pathlib import Path
 states = os.environ.get('POWER','PowerState/running').split(',')
 counter = Path(os.environ['CALLS'] + '.power')
 index = int(counter.read_text()) if counter.exists() else 0
 counter.write_text(str(index + 1))
 print(states[min(index, len(states)-1)])
elif name == 'curl':
 print(os.environ.get('PING','{"message":"pong"}'))
''')
        fake.chmod(0o700)
        for name in ('terraform', 'az', 'curl'):
            (self.base / name).symlink_to(fake)
        self.env = dict(os.environ, TERRAFORM=str(self.base / 'terraform'), AZ=str(self.base / 'az'),
                        CURL=str(self.base / 'curl'), CALLS=str(self.calls),
                        ERPNEXT_TF_DIR=str(self.base / 'tf'), RESOURCE_ID=RESOURCE_ID,
                        AZURE_SUBSCRIPTION_ID='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee')

    def run_vm(self, *args):
        result = subprocess.run(['bash', str(ROOT / 'scripts/erpnext-vm.sh'), *args],
                                env=self.env, capture_output=True, text=True)
        self.assertNotIn('PRIVATE_PROVIDER_SECRET', result.stdout + result.stderr)
        for command in self.commands():
            if command[0] == 'az':
                self.assertEqual(command[command.index('--ids') + 1], RESOURCE_ID)
                self.assertNotIn('--name', command)
                self.assertNotIn('--resource-group', command)
                self.assertNotIn('--subscription', command)
        return result

    def commands(self):
        return [json.loads(line) for line in self.calls.read_text().splitlines()] if self.calls.exists() else []

    def test_confirmation_before_any_calls(self):
        for action in ('start', 'deallocate'):
            self.assertNotEqual(self.run_vm(action).returncode, 0)
        self.assertEqual(self.commands(), [])

    def test_status_and_ready_ping(self):
        for action in ('status', 'ready'):
            self.assertEqual(self.run_vm(action, '--timeout', '0').returncode, 0)
        curls = [c for c in self.commands() if c[0] == 'curl']
        self.assertTrue(curls)
        self.assertEqual(curls[0][-1], 'https://erp.test/api/method/ping')
        self.assertNotIn('-k', curls[0])

    def test_unready_body_and_power(self):
        self.env['PING'] = '{"message":"not ready"}'
        self.assertNotEqual(self.run_vm('ready', '--timeout', '0').returncode, 0)
        self.env['POWER'] = 'PowerState/deallocated'
        result = self.run_vm('status')
        self.assertEqual(result.returncode, 0)
        self.assertIn('deallocated', result.stdout)

    def test_start_only_named_vm(self):
        self.assertEqual(self.run_vm('start', '--confirm').returncode, 0)
        az = [c for c in self.commands() if c[0] == 'az']
        self.assertTrue(any('start' in c for c in az))
        self.assertIn('--no-wait', next(c for c in az if 'start' in c))
        self.assertNotEqual(self.run_vm('start', '--confirm', '--name', 'other').returncode, 0)

    def test_deallocate_verified_not_guest_shutdown(self):
        self.env['POWER'] = 'PowerState/stopped'
        self.assertNotEqual(self.run_vm('deallocate', '--confirm', '--timeout', '0').returncode, 0)
        self.env['POWER'] = 'PowerState/deallocated'
        self.assertEqual(self.run_vm('deallocate', '--confirm').returncode, 0)
        self.assertTrue(any('deallocate' in c for c in self.commands()))
        self.assertFalse(any('stop' in c for c in self.commands()))

    def test_transition_states_are_polled(self):
        for action, states in (
                ('start', 'PowerState/stopped,PowerState/starting,PowerState/running'),
                ('deallocate', 'PowerState/running,PowerState/deallocating,PowerState/deallocated')):
            with self.subTest(action=action):
                self.calls.unlink(missing_ok=True)
                Path(str(self.calls) + '.power').unlink(missing_ok=True)
                self.env['POWER'] = states
                result = self.run_vm(action, '--confirm', '--timeout', '3', '--poll-interval', '0.01')
                self.assertEqual(result.returncode, 0, result.stderr)
                az = [c for c in self.commands() if c[0] == 'az']
                self.assertIn('--no-wait', az[0])
                self.assertEqual(len([c for c in az if 'get-instance-view' in c]), 3)
                self.assertFalse(any(c[0] == 'curl' for c in self.commands()))

    def test_deadline_does_not_claim_completion(self):
        for action, state in (('start', 'PowerState/starting'),
                              ('deallocate', 'PowerState/deallocating')):
            with self.subTest(action=action):
                self.env['POWER'] = state
                result = self.run_vm(action, '--confirm', '--timeout', '0.15', '--poll-interval', '0.01')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('completion unconfirmed', result.stderr)
                self.assertIn('Run status', result.stderr)
                self.assertNotIn('VM deallocated', result.stdout)
                self.assertNotIn('VM is running', result.stdout)

    def test_invalid_resource_id_fails_before_azure(self):
        invalid = ('', RESOURCE_ID.replace('demo-vm', 'other-vm'),
                   RESOURCE_ID.replace('demo-rg', 'other-rg'),
                   RESOURCE_ID.replace('virtualMachines', 'disks'),
                   RESOURCE_ID.replace('11111111-2222-3333-4444-555555555555', 'bad'),
                   RESOURCE_ID + '/extensions/extra')
        for resource_id in invalid:
            for action in ('status', 'start', 'deallocate'):
                with self.subTest(resource_id=resource_id, action=action):
                    self.calls.unlink(missing_ok=True)
                    self.env['RESOURCE_ID'] = resource_id
                    result = self.run_vm(action, '--confirm', '--timeout', '0')
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(any(c[0] == 'az' for c in self.commands()))
        self.env['SUMMARY'] = json.dumps({'name': 'demo-vm', 'resource_group': 'demo-rg',
                                         'url': 'https://erp.test'})
        self.assertNotEqual(self.run_vm('start', '--confirm').returncode, 0)
        self.assertFalse(any(c[0] == 'az' for c in self.commands()))

    def test_mutation_failure_is_unconfirmed_and_safe(self):
        self.env['FAIL'] = 'az'
        for action in ('start', 'deallocate'):
            result = self.run_vm(action, '--confirm', '--timeout', '0')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('completion unconfirmed', result.stderr)
            self.assertIn('Run status', result.stderr)
            self.assertNotIn('Azure confirms', result.stdout)

    def test_cost_arithmetic_no_cloud_or_credit_claim(self):
        result = self.run_vm('cost', '--hourly-rate', '0.0492', '--running-hours', '50')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('2.4600', result.stdout)
        self.assertIn('not a full bill', result.stdout)
        self.assertEqual(self.commands(), [])
        for value in ('NaN', '-1', 'Infinity'):
            self.assertNotEqual(self.run_vm('cost', '--hourly-rate', value, '--running-hours', '50').returncode, 0)

    def test_errors_safe_and_http_rejected(self):
        self.env['FAIL'] = 'az'
        self.assertNotEqual(self.run_vm('status').returncode, 0)
        self.env.pop('FAIL')
        self.env['URL'] = 'http://erp.test'
        self.assertNotEqual(self.run_vm('ready', '--timeout', '0').returncode, 0)


if __name__ == '__main__':
    unittest.main()
