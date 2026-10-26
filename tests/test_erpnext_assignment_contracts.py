"""Offline assignment contract checks. These do not validate runtime enforcement."""
import hashlib
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / 'config/erpnext-assignment-openapi.yaml'
ACTIONS = {
    'create_assignment': '/method/frappe.desk.form.assign_to.add',
    'remove_assignment': '/method/frappe.desk.form.assign_to.remove',
    'complete_assignment': '/method/frappe.desk.form.assign_to.close',
    'reopen_assignment': '/method/frappe.client.set_value',
}


def document(path):
    text = path.read_text()
    return json.loads(text[text.index('{'):])


class AssignmentContracts(unittest.TestCase):
    def test_separate_contract_has_exact_native_post_actions(self):
        contract = document(CONTRACT)
        self.assertEqual(contract['openapi'], '3.0.3')
        self.assertEqual(contract['servers'], [{'url': 'https://erpnext.nimendra.online/api'}])
        self.assertEqual(set(contract['paths']), set(ACTIONS.values()))
        for name, path in ACTIONS.items():
            self.assertEqual(set(contract['paths'][path]), {'post'})
            operation = contract['paths'][path]['post']
            self.assertEqual(operation['operationId'], name)
            self.assertTrue(operation['requestBody']['required'])
            self.assertEqual(set(operation['requestBody']['content']), {'application/json'})

    def test_reviewed_manifests_match_closed_native_inputs_and_transport(self):
        contract = document(CONTRACT)
        directory = ROOT / 'manifests/erpnext-assignments'
        self.assertEqual({p.name for p in directory.iterdir()},
                         {name + '.json' for name in ACTIONS})
        business = ['Asset', 'Asset Category', 'Asset Depreciation Schedule',
                    'Asset Maintenance', 'Asset Movement', 'Asset Repair',
                    'Company', 'Customer', 'Item', 'Item Group', 'Journal Entry',
                    'Location', 'Payment Entry', 'Purchase Invoice', 'Purchase Order',
                    'Sales Invoice', 'Sales Order', 'Supplier', 'UOM']
        active = document(ROOT / 'config/erpnext-openapi.yaml')
        from urllib.parse import unquote
        current_business = {unquote(p.split('/')[2]) for p in active['paths']
                            if p.startswith('/resource/') and p.split('/')[2] != 'ToDo'}
        # The batch A and B expansion adds read-only DocTypes to the active contract. The
        # assignment documents must keep exactly the reviewed business set above.
        self.assertTrue(set(business).issubset(current_business))
        common = {'doctype': {'type': 'string', 'enum': business},
                  'name': {'type': 'string'}, 'assign_to': {'type': 'string'}}
        for name, path in ACTIONS.items():
            with self.subTest(action=name):
                properties = dict(common)
                required = ['doctype', 'name', 'assign_to']
                if name == 'create_assignment':
                    properties.update(assign_to={'type': 'array', 'items': {'type': 'string'}},
                                      description={'type': 'string'},
                                      priority={'type': 'string', 'enum': ['Low', 'Medium', 'High']},
                                      date={'type': 'string', 'format': 'date'})
                elif name == 'reopen_assignment':
                    properties = {'doctype': {'type': 'string', 'enum': ['ToDo']},
                                  'name': {'type': 'string'},
                                  'fieldname': {'type': 'string', 'enum': ['status']},
                                  'value': {'type': 'string', 'enum': ['Open']}}
                    required = ['doctype', 'name', 'fieldname', 'value']
                expected = {'type': 'object', 'properties': properties,
                            'required': required, 'additionalProperties': False}
                operation = contract['paths'][path]['post']
                self.assertEqual(operation['requestBody']['content']['application/json']['schema'], expected)
                tool = document(directory / (name + '.json'))
                self.assertEqual(tool['apiVersion'], 'erpbridge.io/v1')
                self.assertEqual(tool['kind'], 'MCPTool')
                self.assertEqual(tool['metadata'], {'name': name, 'version': '1.0.0', 'module': 'erpnext'})
                spec = tool['spec']
                self.assertEqual(spec['inputSchema'], expected)
                self.assertEqual(spec['execution'], {
                    'type': 'http', 'method': 'POST',
                    'endpoint': 'https://erpnext.nimendra.online/api' + path,
                    'mapping': {key: key for key in properties},
                    'parameterLocations': {key: 'body' for key in properties}})
                self.assertIs(spec['annotations']['readOnlyHint'], False)
                self.assertEqual(spec['cache'], {'enabled': False, 'ttlSeconds': 0,
                                                'isReadOnly': False, 'flushOn': []})
                self.assertEqual(spec['security'], {'authType': 'api-key',
                    'authHeader': 'Authorization', 'credentialRef': 'ERP_PRIMARY_KEY',
                    'credentialSource': 'env'})
                self.assertEqual(set(spec), {'description', 'annotations', 'inputSchema',
                                            'outputSchema', 'execution', 'cache', 'security'})
                self.assertTrue(set(properties).isdisjoint(
                    {'args', 'cmd', 'ignore_permissions', 'assigned_by', 'assignment_rule'}))

    def test_native_output_envelopes_are_not_projected_or_conflated(self):
        contract = document(CONTRACT)
        for name, path in ACTIONS.items():
            with self.subTest(action=name):
                message = {'type': 'array', 'items': {'type': 'object',
                    'required': ['owner', 'name'], 'properties': {
                        'owner': {'type': 'string'}, 'name': {'type': 'string'}},
                    'additionalProperties': True}}
                if name == 'reopen_assignment':
                    message = {'type': 'object', 'required': ['doctype', 'name', 'status'],
                        'properties': {'doctype': {'type': 'string', 'enum': ['ToDo']},
                                       'name': {'type': 'string'},
                                       'status': {'type': 'string', 'enum': ['Open']}},
                        'additionalProperties': True}
                expected = {'type': 'object', 'required': ['message'],
                            'properties': {'message': message}, 'additionalProperties': True}
                operation = contract['paths'][path]['post']
                self.assertEqual(operation['responses']['200']['content']['application/json']['schema'], expected)
                tool = document(ROOT / 'manifests/erpnext-assignments' / (name + '.json'))
                self.assertEqual(tool['spec']['outputSchema'], expected)
        add = contract['paths'][ACTIONS['create_assignment']]['post']['description']
        self.assertIn('auto-share', add)
        self.assertIn('notification delivery is not proven', add)
        close = contract['paths'][ACTIONS['complete_assignment']]['post']['description']
        self.assertIn('assignee-only', close)
        self.assertIn('shared integration identity', close)

    def test_active_catalogue_and_onboarding_are_unchanged(self):
        expected_hashes = {
            'config/erpnext-openapi.yaml': 'b992dc6f63efe739e9d6cc412a25f7caede510f58922e44560bcd8ce9e5f68c2',
            'config/erpnext-approved-tools.txt': 'a3b2b9fbb19c9768d5425092fab1081257ad13e2732b9f0fba1671d7e2c3f088',
            'scripts/onboard-erpnext.py': 'abe610ba1a984e952527e2da3636805e427a4f132405bb05ddfc25900372f5e5',
            'scripts/onboard-erpnext-openapi.sh': '1a1c42a91c3cf20ac6b1ce35733a175931df10ec0555748b2c35562613adfe57',
        }
        for path, digest in expected_hashes.items():
            with self.subTest(path=path):
                self.assertEqual(hashlib.sha256((ROOT / path).read_bytes()).hexdigest(), digest)
        approved = (ROOT / 'config/erpnext-approved-tools.txt').read_text().splitlines()
        names = {line.strip() for line in approved if line.strip() and not line.startswith('#')}
        self.assertEqual(len(names), 152)
        self.assertIn('create_assignment', names)


if __name__ == '__main__':
    unittest.main()
