import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('onboard', ROOT / 'scripts/onboard-erpnext.py')
assert SPEC and SPEC.loader
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def manifest(name, version='1.0.0', endpoint='https://erp.example'):
    tool = {'metadata': {'name': name, 'version': version}, 'spec': {'url': endpoint + '/api/resource/Item', 'auth': {'credentialRef': 'ERP_PRIMARY_KEY'}}}
    if name in m.READ_ONLY_ADDITIONS:
        tool['spec']['execution'] = {'method': 'GET', 'endpoint': endpoint + '/api' + m.READ_ONLY_ADDITIONS[name]}
    return tool


class OnboardingTests(unittest.TestCase):
    def test_ownership_and_response_shapes(self):
        tools = [manifest('list_items')]
        self.assertEqual(m.inventory(tools), m.inventory({'tools': tools}))
        self.assertEqual(m.inventory(None), [])
        self.assertEqual(m.tool_inventory('NAME   MODULE   VERSION   STATUS\n'), [])
        with self.assertRaises(m.SafetyError):
            m.tool_inventory('unexpected non-JSON text')
        with self.assertRaises(m.SafetyError):
            m.inventory('unexpected response')
        with self.assertRaises(m.SafetyError):
            m.check_ownership(tools, None, 'https://erp.example', 'https://bridge.example')
        registry = {'target': 'erpnext', 'endpoint': 'https://erp.example', 'bridge': 'https://bridge.example', 'manifests': [{'name': 'list_items', 'version': '1.0.0'}]}
        m.check_ownership(tools, registry, registry['endpoint'], registry['bridge'])
        with self.assertRaises(m.SafetyError):
            m.check_ownership([manifest('list_items', '2')], registry, registry['endpoint'], registry['bridge'])
        with self.assertRaises(m.SafetyError):
            m.check_ownership([manifest('list_warehouses')], registry, registry['endpoint'], registry['bridge'])

    def test_table_inventory_fetches_exact_named_versions_as_json(self):
        tool = manifest('list_items')
        tool['metadata']['module'] = 'erpnext'
        with patch.object(m, 'cli', return_value=json.dumps(tool)) as cli:
            self.assertEqual(m.tool_inventory('NAME MODULE VERSION STATUS\nlist_items erpnext 1.0.0 READY\n'), [tool])
            cli.assert_called_once_with('tool', 'get', 'list_items@1.0.0', '--output', 'json')
        with self.assertRaises(m.SafetyError):
            m.tool_inventory('NAME MODULE VERSION STATUS\n--bad erpnext 1 READY\n')

    def test_binding_rejects_wrong_endpoint_or_literal_secret(self):
        with self.assertRaises(m.SafetyError):
            m.binding(manifest('list_items', endpoint='https://wrong.example'), 'https://erp.example')
        t = manifest('list_items')
        t['spec']['Authorization'] = 'secret'
        with self.assertRaises(m.SafetyError):
            m.binding(t, 'https://erp.example')

    def test_subprocess_stub_preflights_every_manifest_before_apply(self):
        calls = []
        generated = [manifest(n) for n in sorted(m.APPROVED)]
        applied = False

        def stub(args, **kwargs):
            nonlocal applied
            calls.append(args)
            output = ''
            if args[0] == 'terraform':
                output = json.dumps({'erp_target': 'erpnext', 'erpnext_url': 'https://erp.example', 'erpbridge_url': 'https://bridge.example', 'erpbridge_mcp_url': 'https://bridge.example/mcp/'}[args[-1]])
            elif 'api' in args and 'list' in args:
                output = json.dumps({'items': []})
            elif 'get' in args:
                self.assertEqual(os.environ.get('BRIDGE_SERVER'), 'https://bridge.example')
                self.assertEqual(os.environ.get('BRIDGE_MCP_SERVER'), 'https://bridge.example')
                output = json.dumps({'tools': generated if applied else []})
            elif 'generate' in args:
                self.assertIn('--api', args)
                self.assertIn('--openapi', args)
                output = json.dumps(generated)
            elif 'apply' in args:
                self.assertEqual(sum('validate' in c for c in calls), len(m.APPROVED))
                applied = True
            return subprocess.CompletedProcess(args, 0, output, '')

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'config').mkdir()
            (root / 'config/erpnext-openapi.yaml').write_text((ROOT / 'config/erpnext-openapi.yaml').read_text())
            with patch.object(m, 'verify_discovery'), patch.object(m, 'ROOT', root), patch.object(m, 'REGISTRY', root / 'artifacts/registry.json'), patch.object(m.subprocess, 'run', side_effect=stub), patch.dict(os.environ, {'BRIDGE_API_TOKEN': 'secret-sentinel', 'BRIDGE_SERVER': 'https://wrong.example'}), patch('sys.argv', ['onboard', '--confirm']), contextlib.redirect_stdout(io.StringIO()):
                m.main()
            self.assertTrue(applied)
            self.assertEqual((root / 'artifacts/registry.json').stat().st_mode & 0o777, 0o600)
            self.assertNotIn('secret-sentinel', json.dumps(calls))

    def test_local_validation_failure_prevents_any_apply(self):
        generated = [manifest(n) for n in sorted(m.APPROVED)]
        calls = []
        def stub(args, **kwargs):
            calls.append(args)
            value = ''
            if args[0] == 'terraform':
                value = json.dumps({'erp_target': 'erpnext', 'erpnext_url': 'https://erp.example', 'erpbridge_url': 'https://bridge.example', 'erpbridge_mcp_url': 'https://bridge.example/mcp/'}[args[-1]])
            elif 'api' in args and 'list' in args:
                value = json.dumps({'items': []})
            elif 'get' in args:
                value = '[]'
            elif 'generate' in args:
                value = json.dumps(generated)
            return subprocess.CompletedProcess(args, 1 if 'validate' in args else 0, value, 'secret-sentinel')
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(m, 'REGISTRY', Path(tmp) / 'registry.json'), patch.object(m.subprocess, 'run', side_effect=stub), patch.dict(os.environ, {'BRIDGE_API_TOKEN': 'secret-sentinel'}), patch('sys.argv', ['onboard', '--confirm']):
                with self.assertRaises(m.SafetyError):
                    m.main()
        self.assertFalse(any('apply' in c for c in calls))

    def test_mockerp_onboarding_refuses_active_erpnext_before_any_cli_write(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for name, content in [('terraform', '#!/bin/sh\nprintf erpnext\n'),
                                  ('bridgectl', '#!/bin/sh\ntouch "$UNEXPECTED_CLI_CALL"\n')]:
                file = folder / name
                file.write_text(content)
                file.chmod(0o755)
            spec, approved, marker = folder / 'spec.yaml', folder / 'approved.txt', folder / 'called'
            spec.write_text('{}')
            approved.write_text('list_warehouses\n')
            env = dict(os.environ, PATH=directory + os.pathsep + os.environ.get('PATH', ''),
                       BRIDGE_API_TOKEN='fixture-only', MOCKERP_OPENAPI_SPEC=str(spec),
                       MOCKERP_APPROVED_TOOLS=str(approved), UNEXPECTED_CLI_CALL=str(marker))
            result = subprocess.run(['bash', str(ROOT / 'scripts/onboard-mockerp-openapi.sh')],
                                    env=env, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 1)
            self.assertIn('another ERP target', result.stderr)
            self.assertFalse(marker.exists())

    def test_switch_is_fail_closed(self):
        p = subprocess.run(['python3', str(ROOT / 'scripts/switch-erp-target.py'), '--target', 'erpnext', '--confirm'], capture_output=True, text=True, timeout=5)
        self.assertEqual(p.returncode, 1)
        self.assertIn('Cutover blocked', p.stderr)

    def test_delete_preserves_native_acknowledgement_object(self):
        contract = json.loads((ROOT / 'config/erpnext-openapi.yaml').read_text().split('\n', 1)[1])
        tool = manifest('delete_item')
        tool['spec']['execution'] = {'responsePath': 'data', 'method': 'DELETE'}
        m.preserve_delete_envelope(tool, contract)
        self.assertNotIn('responsePath', tool['spec']['execution'])
        self.assertEqual(tool['spec']['execution']['method'], 'DELETE')
        self.assertEqual(tool['spec']['outputSchema']['type'], 'object')
        self.assertEqual(tool['spec']['outputSchema']['properties']['data'], {'type': 'string', 'enum': ['ok']})

    def test_spec_approved_operations_and_required_body(self):
        doc = json.loads((ROOT / 'config/erpnext-openapi.yaml').read_text().split('\n', 1)[1])
        assignments = json.loads((ROOT / 'config/erpnext-assignment-openapi.yaml').read_text().split('\n', 1)[1])
        assignment_ops = {op['operationId'] for path in assignments['paths'].values() for op in path.values()}
        self.assertEqual({op['operationId'] for path in doc['paths'].values() for op in path.values()},
                         m.APPROVED - set(m.READ_ALIASES) - assignment_ops)
        schema = doc['paths']['/resource/Item']['post']['requestBody']['content']['application/json']['schema']
        self.assertEqual(schema['type'], 'object')
        self.assertEqual(set(schema['required']), {'item_code', 'item_name', 'item_group', 'stock_uom', 'is_stock_item'})

    def test_lookup_aliases_preserve_sources_and_read_only_scope(self):
        import copy
        sources = [manifest('search_assignable_users'), manifest('list_assignments')]
        previous = copy.deepcopy(sources)
        expanded = m.expand_read_aliases(sources)
        self.assertEqual(sources, previous)
        by_name = {m.identity(tool)[0]: tool for tool in expanded}
        self.assertEqual(set(by_name), {'search_assignable_users', 'list_assignable_users',
                                       'list_assignments', 'list_document_assignments'})
        self.assertEqual(by_name['list_assignable_users']['spec']['inputSchema']['properties']['search_term']['enum'], [''])
        assignment = by_name['list_document_assignments']['spec']
        self.assertIn('filters', assignment['inputSchema']['required'])
        self.assertEqual(assignment['execution']['parameterLocations']['filters'], 'query')
        self.assertEqual(assignment['inputSchema']['properties']['fields']['enum'],
                         ['["name","reference_type","reference_name","allocated_to","assigned_by","status"]'])
        self.assertEqual(len(m.expand_read_aliases(expanded)), 4)
        for name in m.READ_ALIASES:
            m.constrain_read_addition(by_name[name], 'https://erp.example')
            self.assertFalse(by_name[name]['spec']['cache']['enabled'])
            self.assertEqual(by_name[name]['spec']['inputSchema']['properties']['cmd']['enum'], [''])
            self.assertEqual(by_name[name]['spec']['execution']['parameterLocations']['cmd'], 'query')
        with self.assertRaises(m.SafetyError):
            m.expand_read_aliases(sources + [sources[0]])

    def test_additive_generation_preserves_existing_contracts(self):
        import copy
        old = manifest('list_items')
        expanded = [copy.deepcopy(old), manifest('list_customers')]
        m.check_additive_generation([old], expanded)
        with self.assertRaises(m.SafetyError):
            m.check_additive_generation([old], expanded[1:])
        expanded[0]['spec']['inputSchema'] = {'type': 'string'}
        with self.assertRaises(m.SafetyError):
            m.check_additive_generation([old], expanded)

    def test_existing_api_base_and_identity_are_preserved(self):
        registration = {'name': 'erpnext', 'url': 'https://erp.example/api',
                        'method': 'GET', 'module': 'erpnext', 'authType': 'api-key',
                        'authHeader': 'Authorization', 'credentialRef': 'ERP_PRIMARY_KEY',
                        'credentialSource': 'env', 'status': 'active', 'id': 'unchanged'}
        with patch.object(m, 'cli', return_value=json.dumps({'items': [registration]})) as cli:
            m.ensure_api_registration('https://erp.example')
            cli.assert_called_once_with('api', 'list', '--output', 'json')
        for field, value in [('url', 'https://other.example/api'),
                             ('credentialRef', 'ANOTHER_KEY'), ('status', 'disabled')]:
            wrong = dict(registration, **{field: value})
            with patch.object(m, 'cli', return_value=json.dumps({'items': [wrong]})) as cli:
                with self.assertRaises(m.SafetyError):
                    m.ensure_api_registration('https://erp.example')
                self.assertEqual(cli.call_count, 1)

    def test_missing_api_registration_uses_base_without_force(self):
        with patch.object(m, 'cli', side_effect=[json.dumps({'items': []}), '']) as cli:
            m.ensure_api_registration('https://erp.example')
            args = cli.call_args_list[1].args
            self.assertNotIn('--force', args)
            self.assertEqual(args[args.index('--url') + 1], 'https://erp.example/api')

    def test_read_additions_cannot_generate_mutations_or_other_routes(self):
        for name in m.READ_ONLY_ADDITIONS:
            tool = manifest(name)
            m.constrain_read_addition(tool, 'https://erp.example')
            self.assertTrue(tool['spec']['annotations']['readOnlyHint'])
            self.assertFalse(tool['spec']['annotations']['destructiveHint'])
            tool['spec']['execution']['method'] = 'POST'
            with self.assertRaises(m.SafetyError):
                m.constrain_read_addition(tool, 'https://erp.example')
            tool['spec']['execution']['method'] = 'GET'
            tool['spec']['execution']['endpoint'] = 'https://erp.example/api/method/unsafe'
            with self.assertRaises(m.SafetyError):
                m.constrain_read_addition(tool, 'https://erp.example')

    def test_allowlist_and_new_contract_methods_match(self):
        doc = json.loads((ROOT / 'config/erpnext-openapi.yaml').read_text().split('\n', 1)[1])
        allowed = set((ROOT / 'config/erpnext-approved-tools.txt').read_text().splitlines())
        self.assertEqual(allowed, m.APPROVED)
        self.assertEqual(len(allowed), 152)
        self.assertTrue({'search_assignable_users', 'list_assignable_users',
                         'get_user_details', 'get_current_user',
                         'list_document_assignments'}.issubset(allowed))
        self.assertTrue({'list_assignments', 'get_assignment', 'list_assets', 'get_asset',
                         'list_asset_categories', 'get_asset_category', 'list_locations',
                         'get_location', 'list_asset_movements', 'get_asset_movement',
                         'list_asset_maintenance', 'get_asset_maintenance',
                         'list_asset_repairs', 'get_asset_repair',
                         'list_asset_depreciation_schedules',
                         'get_asset_depreciation_schedule'}.issubset(allowed))
        accounting = {'sales_invoices', 'purchase_invoices', 'payment_entries', 'journal_entries'}
        self.assertTrue({'list_' + name for name in accounting}.issubset(allowed))
        self.assertTrue({'get_sales_invoice', 'get_purchase_invoice', 'get_payment_entry', 'get_journal_entry'}.issubset(allowed))
        for path, operations in doc['paths'].items():
            for method, operation in operations.items():
                if operation['operationId'] in m.READ_ONLY_ADDITIONS:
                    self.assertEqual(method, 'get')
                    self.assertEqual(path, m.READ_ONLY_ADDITIONS[operation['operationId']])


if __name__ == '__main__':
    unittest.main()
