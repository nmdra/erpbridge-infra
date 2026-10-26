import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest
from typing import Any, cast
import urllib.parse
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('verify', ROOT / 'scripts/verify-erpnext-demo.py')
assert SPEC and SPEC.loader
v = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(v)
EXPECTED_TOOLS = {'list_companies', 'get_company', 'list_items', 'get_item', 'create_item', 'delete_item',
                  'list_item_groups', 'list_uoms', 'list_customers', 'get_customer',
                  'list_suppliers', 'get_supplier', 'list_sales_orders', 'get_sales_order',
                  'list_purchase_orders', 'get_purchase_order',
                  'list_sales_invoices', 'get_sales_invoice',
                  'list_purchase_invoices', 'get_purchase_invoice',
                  'list_payment_entries', 'get_payment_entry',
                  'list_journal_entries', 'get_journal_entry'} | {
                      'list_' + plural for plural, _, _ in (
                          ('assets', 'asset', 'Asset'),
                          ('asset_categories', 'asset_category', 'Asset Category'),
                          ('locations', 'location', 'Location'),
                          ('asset_movements', 'asset_movement', 'Asset Movement'),
                          ('asset_maintenance', 'asset_maintenance', 'Asset Maintenance'),
                          ('asset_repairs', 'asset_repair', 'Asset Repair'),
                          ('asset_depreciation_schedules', 'asset_depreciation_schedule', 'Asset Depreciation Schedule'),
                          ('assignments', 'assignment', 'ToDo'))} | {
                      'get_asset', 'get_asset_category', 'get_location', 'get_asset_movement',
                      'get_asset_maintenance', 'get_asset_repair',
                      'get_asset_depreciation_schedule', 'get_assignment'}
ASSET_ASSIGNMENT_READS = (
    ('assets', 'asset', 'Asset'),
    ('asset_categories', 'asset_category', 'Asset Category'),
    ('locations', 'location', 'Location'),
    ('asset_movements', 'asset_movement', 'Asset Movement'),
    ('asset_maintenance', 'asset_maintenance', 'Asset Maintenance'),
    ('asset_repairs', 'asset_repair', 'Asset Repair'),
    ('asset_depreciation_schedules', 'asset_depreciation_schedule', 'Asset Depreciation Schedule'),
    ('assignments', 'assignment', 'ToDo'),
)
ACCOUNTING_READS = (
    ('sales_invoices', 'sales_invoice', 'Sales Invoice'),
    ('purchase_invoices', 'purchase_invoice', 'Purchase Invoice'),
    ('payment_entries', 'payment_entry', 'Payment Entry'),
    ('journal_entries', 'journal_entry', 'Journal Entry'),
)
FIXTURE = json.loads((ROOT / 'demo/erpnext/fixtures/scp-masters.json').read_text())


class FixtureServer(ThreadingHTTPServer):
    items: dict
    deleted: list
    calls: list
    structured: bool
    fail_after_create: bool
    records: dict
    failure: str | None
    negative_success: bool
    rest_mismatch: bool
    reads: list


class FixtureHandler(BaseHTTPRequestHandler):
    @property
    def fixture(self):
        return cast(FixtureServer, self.server)

    def log_message(self, format, *args):
        pass

    def send(self, status, value):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Mcp-Session-Id', 'fixture-session')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.fixture.reads.append((self.path, self.headers.get('Authorization')))
        parsed = urllib.parse.urlsplit(self.path)
        parts = parsed.path.split('/')
        doctype = urllib.parse.unquote(parts[3])
        records = self.fixture.records[doctype]
        if len(parts) == 4:
            query = urllib.parse.parse_qs(parsed.query)
            fields = json.loads(query['fields'][0])
            rows = [{key: row.get(key) for key in fields} for row in records.values()]
            if self.fixture.rest_mismatch and doctype == 'Customer':
                rows = [{'name': 'REST-only', 'customer_name': 'REST-only'}]
            self.send(200, {'data': rows})
        else:
            name = urllib.parse.unquote(parts[-1])
            self.send(200 if name in records else 404, {'data': records.get(name)})

    def do_DELETE(self):
        name = urllib.parse.unquote(self.path.rsplit('/', 1)[-1])
        self.fixture.deleted.append(name)
        self.fixture.items.pop(name, None)
        self.send(202, {'data': None})

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        method = payload['method']
        result = {}
        if method == 'initialize':
            result = {'protocolVersion': '2025-06-18'}
        elif method == 'notifications/initialized':
            self.send(202, {})
            return
        elif method == 'tools/list':
            result = {'tools': [{'name': n} for n in v.APPROVED]}
        elif method == 'tools/call':
            params = payload['params']
            name, args = params['name'], params['arguments']
            self.fixture.calls.append((name, args))
            doctypes = {'companies': 'Company', 'items': 'Item', 'item_groups': 'Item Group',
                        'uoms': 'UOM', 'customers': 'Customer', 'suppliers': 'Supplier',
                        'sales_orders': 'Sales Order', 'purchase_orders': 'Purchase Order',
                        **{plural: doctype for plural, _, doctype in ACCOUNTING_READS + ASSET_ASSIGNMENT_READS}}
            if name == self.fixture.failure:
                self.send(200, {'jsonrpc': '2.0', 'id': payload['id'], 'result': {'isError': True}})
                return
            if name.startswith('list_'):
                records = self.fixture.records[doctypes[name[5:]]]
                fields = json.loads(args['fields'])
                data = [{key: row.get(key) for key in fields} for row in records.values()]
            elif name.startswith('get_'):
                plurals: dict[str, str] = {'company': 'companies', **{singular: plural for plural, singular, _ in ACCOUNTING_READS + ASSET_ASSIGNMENT_READS}}
                singular = str(name[4:])
                plural = plurals.get(singular, singular + 's')
                records = self.fixture.records[doctypes[plural]]
                if args['name'] not in records and not self.fixture.negative_success:
                    self.send(200, {'jsonrpc': '2.0', 'id': payload['id'], 'result': {'isError': True}})
                    return
                data = records.get(args['name'])
            elif name == 'create_item':
                data = dict(args)
                self.fixture.items[data['item_code']] = dict(data, name=data['item_code'])
            elif name == 'delete_item':
                self.fixture.deleted.append(args['name'])
                self.fixture.items.pop(args['name'], None)
                data = None
            else:
                raise AssertionError(name)
            if self.fixture.fail_after_create and name == 'create_item':
                result = {'isError': True, 'content': [{'type': 'text', 'text': 'intentional fixture failure'}]}
            elif self.fixture.structured:
                result = {'structuredContent': {'data': data}}
            else:
                result = {'content': [{'type': 'text', 'text': json.dumps({'data': data})}]}
        self.send(200, {'jsonrpc': '2.0', 'id': payload['id'], 'result': result})


class VerifierTests(unittest.TestCase):
    def setUp(self):
        self.server = FixtureServer(('127.0.0.1', 0), FixtureHandler)
        # Keep the original forty-tool scenarios independent of native lookups.
        lookup_patch = patch.object(v, 'lookup_reads', return_value={})
        lookup_patch.start()
        self.addCleanup(lookup_patch.stop)
        self.approved_patch = patch.object(v, 'APPROVED', EXPECTED_TOOLS)
        self.approved_patch.start()
        self.addCleanup(self.approved_patch.stop)
        onboarding_patch = patch.object(v.onboarding, 'APPROVED', EXPECTED_TOOLS)
        onboarding_patch.start()
        self.addCleanup(onboarding_patch.stop)
        self.server.items = {i['item_code']: dict(i, name=i['item_code']) for i in FIXTURE['items']}
        company = dict(FIXTURE['company'], name=FIXTURE['company']['company_name'])
        self.server.records = {
            'Item': self.server.items, 'Company': {company['name']: company},
            'Item Group': {name: {'name': name, 'item_group_name': name} for name in FIXTURE['item_groups']},
            'UOM': {row['uom_name']: dict(row, name=row['uom_name']) for row in FIXTURE['uoms']},
            'Customer': {}, 'Supplier': {}, 'Sales Order': {}, 'Purchase Order': {},
            **{doctype: {} for _, _, doctype in ACCOUNTING_READS + ASSET_ASSIGNMENT_READS}}
        self.server.failure = None
        self.server.negative_success = self.server.rest_mismatch = False
        self.server.reads = []
        self.server.calls, self.server.deleted = [], []
        self.server.structured, self.server.fail_after_create = False, False
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.endpoint = 'http://127.0.0.1:' + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def verify(self, write=False):
        return v.verify(v.MCP(self.endpoint + '/mcp/', 'Bearer fixture'), v.ERP(self.endpoint, 'token fixture'), FIXTURE, write)

    def test_default_readonly(self):
        self.verify()
        self.assertFalse(self.server.deleted)
        self.assertFalse(any(n in ('create_item', 'delete_item') for n, _ in self.server.calls))
        self.assertTrue(all('fields' in a for n, a in self.server.calls if n.startswith('list_')))

    def test_empty_lists_report_probes_and_read_every_new_tool(self):
        coverage = self.verify()
        self.assertEqual(coverage['get_company'], 'existing-record')
        self.assertEqual(sum(value == 'explicit-error-probe' for value in coverage.values()), 16)
        for _, singular, doctype in ACCOUNTING_READS:
            self.assertEqual(coverage['get_' + singular], 'explicit-error-probe')
            name = next(args['name'] for tool, args in self.server.calls if tool == 'get_' + singular)
            path = '/api/resource/' + urllib.parse.quote(doctype, safe='') + '/' + name
            self.assertIn((path, 'token fixture'), self.server.reads)
            self.assertNotIn(name, self.server.records[doctype])
        self.assertTrue(EXPECTED_TOOLS - {'create_item', 'delete_item'} <= {n for n, _ in self.server.calls})
        for name, args in self.server.calls:
            if name.startswith('list_') and name not in ('list_items',):
                self.assertEqual(args.get('limit_page_length'), 1000)
        self.assertTrue(all(token == 'token fixture' for _, token in self.server.reads))

    def test_existing_records_have_positive_get_coverage(self):
        for doctype, fields in (('Customer', {'customer_name': 'Test customer'}),
                                ('Supplier', {'supplier_name': 'Test supplier'}),
                                ('Sales Order', {'company': 'SCP', 'status': 'Draft', 'currency': 'LKR', 'grand_total': 1}),
                                ('Purchase Order', {'company': 'SCP', 'status': 'Draft', 'currency': 'LKR', 'grand_total': 2})):
            self.server.records[doctype]['fixture'] = dict(fields, name='fixture')
        for _, _, doctype in ACCOUNTING_READS:
            self.server.records[doctype]['fixture'] = {'name': 'fixture', 'company': 'SCP', 'docstatus': 0}
        for _, _, doctype in ASSET_ASSIGNMENT_READS:
            self.server.records[doctype]['fixture'] = {'name': 'fixture'}
        coverage = self.verify()
        self.assertEqual(len(coverage), 17)
        self.assertEqual(set(coverage.values()), {'existing-record'})
        self.assertFalse(any('VIVA-NOT-FOUND-' in args.get('name', '') for _, args in self.server.calls))

    def test_asset_and_assignment_reads_have_no_mutation_inputs(self):
        coverage = self.verify()
        for plural, singular, doctype in ASSET_ASSIGNMENT_READS:
            self.assertEqual(coverage['get_' + singular], 'explicit-error-probe')
            calls = [(name, args) for name, args in self.server.calls
                     if name in ('list_' + plural, 'get_' + singular)]
            self.assertEqual([name for name, _ in calls], ['list_' + plural, 'get_' + singular])
            self.assertEqual(calls[0][1], {'fields': json.dumps(['name']),
                                          'limit_page_length': 1000, 'limit_start': 0})
            self.assertEqual(set(calls[1][1]), {'name'})
        self.assertFalse(self.server.deleted)
        self.assertFalse(any(name.startswith(('create_', 'update_', 'delete_'))
                             for name, _ in self.server.calls))

    def test_accounting_reads_use_get_semantics_and_bounded_pages(self):
        with patch.object(v, 'request', wraps=v.request) as requests:
            self.verify()
        for plural, singular, doctype in ACCOUNTING_READS:
            calls = [(name, args) for name, args in self.server.calls
                     if name in ('list_' + plural, 'get_' + singular)]
            self.assertEqual([name for name, _ in calls], ['list_' + plural, 'get_' + singular])
            self.assertEqual(calls[0][1], {'fields': json.dumps(['name', 'company', 'docstatus']),
                                          'limit_page_length': 1000, 'limit_start': 0})
            self.assertEqual(set(calls[1][1]), {'name'})
            prefix = self.endpoint + '/api/resource/' + urllib.parse.quote(doctype, safe='')
            direct = [call for call in requests.call_args_list if call.args[0].startswith(prefix)]
            self.assertEqual(len(direct), 2)
            for call in direct:
                self.assertLessEqual(len(call.args), 2)
                self.assertIsNone(call.kwargs.get('method'))  # urllib defaults to GET without a payload.
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(direct[0].args[0]).query)
            self.assertEqual(query, {'fields': [json.dumps(['name', 'company', 'docstatus'])],
                                     'limit_page_length': ['1000'], 'limit_start': ['0']})
        self.assertFalse(self.server.deleted)
        self.assertFalse(any(name in ('create_item', 'delete_item') for name, _ in self.server.calls))

    def test_accounting_normal_read_errors_are_rejected(self):
        for plural, singular, doctype in ACCOUNTING_READS:
            self.server.records[doctype]['fixture'] = {'name': 'fixture', 'company': 'SCP', 'docstatus': 0}
            for tool in ('list_' + plural, 'get_' + singular):
                with self.subTest(tool=tool):
                    self.server.failure = tool
                    with self.assertRaises(v.MCPReportedError):
                        self.verify()
        self.server.failure = None

    def test_accounting_named_get_business_fields_match_rest(self):
        original = v.MCP.tool
        for _, singular, doctype in ACCOUNTING_READS:
            self.server.records[doctype]['fixture'] = {'name': 'fixture', 'company': 'SCP', 'docstatus': 0}
            def mismatch(mcp, name, arguments):
                if name == 'get_' + singular:
                    return {'structuredContent': {'data': {'name': 'fixture', 'company': 'SCP',
                                                          'docstatus': 0, 'remarks': 'MCP-only'}}}
                return original(mcp, name, arguments)
            with self.subTest(doctype=doctype), patch.object(v.MCP, 'tool', mismatch):
                with self.assertRaises(v.SafetyError):
                    self.verify()

    def test_success_is_not_an_acceptable_negative_probe(self):
        self.server.negative_success = True
        with self.assertRaises(v.SafetyError):
            self.verify()

    def test_normal_read_error_is_rejected(self):
        self.server.failure = 'list_customers'
        with self.assertRaises(v.MCPReportedError):
            self.verify()
        self.server.failure = 'get_company'
        with self.assertRaises(v.MCPReportedError):
            self.verify()

    def test_existing_named_get_error_is_rejected(self):
        self.server.records['Customer']['fixture'] = {'name': 'fixture', 'customer_name': 'Fixture'}
        self.server.failure = 'get_customer'
        with self.assertRaises(v.MCPReportedError):
            self.verify()

    def test_negative_probe_accepts_explicit_jsonrpc_error_only(self):
        original = v.MCP.tool
        def missing(mcp, name, arguments):
            if name == 'get_customer':
                return v.rpc_result(json.dumps({'jsonrpc': '2.0', 'id': 1,
                                               'error': {'code': -32000, 'message': 'Not found'}}), 1)
            return original(mcp, name, arguments)
        with patch.object(v.MCP, 'tool', missing):
            self.assertEqual(self.verify()['get_customer'], 'explicit-error-probe')
        for error in ({}, {'code': -1}, {'code': 'invalid', 'message': 'bad'}):
            with self.assertRaises(v.SafetyError) as raised:
                v.rpc_result(json.dumps({'jsonrpc': '2.0', 'id': 1, 'error': error}), 1)
            self.assertNotIsInstance(raised.exception, v.MCPReportedError)

    def test_rest_list_mismatch_is_rejected(self):
        self.server.rest_mismatch = True
        with self.assertRaises(v.SafetyError):
            self.verify()

    def test_fixture_metadata_mismatch_is_rejected(self):
        self.server.records['UOM']['Kg']['must_be_whole_number'] = 1
        with self.assertRaises(v.SafetyError):
            self.verify()

    def test_negative_probe_does_not_accept_transport_or_parse_failure(self):
        original = v.MCP.tool
        for failure in (v.SafetyError('transport'), v.SafetyError('invalid JSON')):
            def fail(mcp, name, arguments):
                if name == 'get_customer':
                    raise failure
                return original(mcp, name, arguments)
            with patch.object(v.MCP, 'tool', fail), self.assertRaises(v.SafetyError):
                self.verify()

    def test_write_and_baseline_preserved_text(self):
        self.verify(True)
        self.assertEqual(set(self.server.items), {i['item_code'] for i in FIXTURE['items']})
        self.assertEqual(len(self.server.deleted), 1)
        self.assertTrue(self.server.deleted[0].startswith('VIVA-RUN-'))
        created = next(args for name, args in self.server.calls if name == 'create_item')
        self.assertEqual(set(created), {'item_code', 'item_name', 'item_group', 'stock_uom', 'is_stock_item'})

    def test_structured_content(self):
        self.server.structured = True
        self.verify(True)

    def test_cleanup_after_tool_failure_only_run_owned(self):
        self.server.fail_after_create = True
        self.server.items['VIVA-RUN-preexisting'] = {'item_code': 'VIVA-RUN-preexisting'}
        with self.assertRaises(v.SafetyError):
            self.verify(True)
        self.assertIn('VIVA-RUN-preexisting', self.server.items)
        self.assertNotIn('VIVA-RUN-preexisting', self.server.deleted)
        self.assertEqual(len(self.server.deleted), 1)

    def test_record_value_mismatch_fails(self):
        self.server.items['FG-CLEANER-5L']['stock_uom'] = 'Kg'
        with self.assertRaises(v.SafetyError):
            self.verify()

    def test_sse_and_errors(self):
        body = 'event: message\r\ndata: ' + json.dumps({'jsonrpc': '2.0', 'id': 7, 'result': {'ok': 1}}) + '\r\n\r\n'
        self.assertEqual(v.rpc_result(body, 7), {'ok': 1})
        for value in ({'jsonrpc': '2.0', 'id': 7, 'error': {'code': -1}}, {'jsonrpc': '2.0', 'id': 7, 'result': {'isError': True}}):
            with self.assertRaises(v.SafetyError):
                v.rpc_result(json.dumps(value), 7)
        with self.assertRaises(v.SafetyError):
            v.resource({'content': [{'type': 'text', 'text': 'success'}]})
        with self.assertRaises(v.SafetyError):
            v.resource({'structuredContent': None})

    def test_cli_uses_artifact_bridge_and_separate_admin_credential(self):
        tools = [{'metadata': {'name': name, 'version': '1'}, 'spec': {
            'execution': {'endpoint': self.endpoint + '/api/resource/Item'},
            'security': {'credentialRef': 'ERP_PRIMARY_KEY'}}} for name in v.APPROVED]
        artifact = {'deployment': {'erp_target': 'erpnext'}, 'urls': {
            'erpbridge': self.endpoint, 'erpbridge_mcp': self.endpoint + '/mcp/',
            'erpnext': self.endpoint}, 'demo_tokens': {
                'api_auth_token': 'fixture-admin', 'erp_primary_key': 'token fixture'}}
        registry = {'target': 'erpnext', 'endpoint': self.endpoint, 'bridge': self.endpoint,
                    'manifests': [{'name': n, 'version': '1'} for n in v.APPROVED]}

        def stub(args, **kwargs):
            self.assertEqual(os.environ['BRIDGE_SERVER'], self.endpoint)
            self.assertEqual(os.environ['BRIDGE_MCP_SERVER'], self.endpoint)
            self.assertEqual(os.environ['BRIDGE_API_TOKEN'], 'fixture-admin')
            self.assertNotIn('fixture-admin', args)
            return subprocess.CompletedProcess(args, 0, json.dumps({'tools': tools}), '')

        with tempfile.TemporaryDirectory() as directory:
            deployment_path = Path(directory) / 'deployment.json'
            ownership_path = Path(directory) / 'ownership.json'
            for path, value in ((deployment_path, artifact), (ownership_path, registry)):
                path.write_text(json.dumps(value))
                path.chmod(0o600)
            with patch.dict(os.environ, {'BRIDGE_SERVER': 'https://wrong.example', 'MCP_TOKEN': 'fixture-client'}, clear=True), \
                    patch.object(v.onboarding, 'REGISTRY', ownership_path), \
                    patch.object(v.onboarding.subprocess, 'run', side_effect=stub), \
                    patch('sys.argv', ['verify', '--deployment-file', str(deployment_path), '--allow-local-http']), \
                    contextlib.redirect_stdout(io.StringIO()):
                v.main()
        self.assertFalse(self.server.deleted)

    def test_http_requires_loopback_opt_in(self):
        with self.assertRaises(v.SafetyError):
            v.onboarding.url(self.endpoint)
        self.assertEqual(v.onboarding.url(self.endpoint, True), self.endpoint)
        with self.assertRaises(v.SafetyError):
            v.onboarding.url('http://example.com', True)


class NativeLookupFake:
    """Independent HTTP and MCP surfaces, with no network or writes."""
    endpoint = 'https://erp.invalid'
    token = 'token fixture'

    def __init__(self):
        self.login: Any = 'integration@example.invalid'
        self.profiles: dict[str, Any] = {self.login: {'fullname': 'Integration User', 'image': None,
                                     'name': self.login, 'email': self.login, 'time_zone': None}}
        self.rows = [{'id': 'group', 'value': 'Group First', 'link': '/app/group', 'is_group': True},
                     {'id': self.login, 'value': 'Integration User', 'link': '/app/user', 'is_group': False}]
        self.assignments = []
        self.calls = []
        self.requests = []
        self.override = {}

    def discover(self):
        return [{'name': name} for name in v.APPROVED]

    def native(self, path, query):
        if path == '/api/method/frappe.auth.get_logged_user':
            assert query == {}
            return {'message': self.login}
        if path == '/api/method/frappe.desk.form.load.get_user_info_for_viewers':
            assert json.loads(query['users']) == [self.login]
            return {'message': self.profiles}
        if path == '/api/method/frappe.desk.search.get_names_for_mentions':
            term = query['search_term']
            return {'message': [row for row in self.rows if term.lower() in row['value'].lower()]}
        assert path == '/api/resource/ToDo'
        assert json.loads(query['filters']) == {'reference_type': 'Item',
                                              'reference_name': FIXTURE['items'][0]['item_code']}
        assert query['fields'] == '["name","reference_type","reference_name","allocated_to","assigned_by","status"]'
        assert str(query['limit_page_length']) == '1000'
        assert str(query['limit_start']) == '0'
        return {'data': self.assignments}

    def request(self, url, token):
        self.requests.append((url, token))
        assert token == self.token
        parsed = urllib.parse.urlsplit(url)
        query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
        return 200, {}, json.dumps(self.native(parsed.path, query))

    def tool(self, name, arguments):
        self.calls.append((name, arguments))
        paths = {'get_current_user': '/api/method/frappe.auth.get_logged_user',
                 'get_user_details': '/api/method/frappe.desk.form.load.get_user_info_for_viewers',
                 'list_assignable_users': '/api/method/frappe.desk.search.get_names_for_mentions',
                 'search_assignable_users': '/api/method/frappe.desk.search.get_names_for_mentions',
                 'list_document_assignments': '/api/resource/ToDo'}
        value = self.override[name] if name in self.override else self.native(paths[name], arguments)
        return {'structuredContent': value}


class LookupReadTests(unittest.TestCase):
    def setUp(self):
        self.fake = NativeLookupFake()

    def lookup(self):
        with patch.object(v, 'request', side_effect=self.fake.request):
            return v.lookup_reads(self.fake, self.fake, FIXTURE)

    def test_nullable_profile_and_group_skipped_for_display_name_search(self):
        coverage = self.lookup()
        self.assertEqual(len(coverage), 5)
        self.assertIn(('get_current_user', {}), self.fake.calls)
        self.assertIn(('get_user_details', {'users': json.dumps([self.fake.login])}), self.fake.calls)
        self.assertIn(('list_assignable_users', {'search_term': ''}), self.fake.calls)
        self.assertIn(('search_assignable_users', {'search_term': 'Integration User'}), self.fake.calls)
        self.assertEqual(len(self.fake.requests), 5)
        self.assertEqual(coverage['list_document_assignments'], 'empty-document-assignments-rest-match')

    def test_empty_and_group_only_directory_use_sentinel(self):
        for rows in ([], self.fake.rows[:1]):
            with self.subTest(rows=bool(rows)):
                self.fake.rows = rows
                coverage = self.lookup()
                self.assertIn(('search_assignable_users', {'search_term': 'VIVA-NO-DISPLAY-NAME-MATCH'}), self.fake.calls)
                self.assertEqual(coverage['search_assignable_users'], 'sentinel-search-rest-match')
                if not rows:
                    self.assertEqual(coverage['list_assignable_users'], 'empty-mention-directory-rest-match')

    def test_incomplete_profile_is_rejected(self):
        self.fake.profiles[self.fake.login] = {'name': self.fake.login}
        with self.assertRaises(v.SafetyError):
            self.lookup()

    def test_missing_known_integration_profile_fails(self):
        for profiles in ({}, {self.fake.login: {}}, {self.fake.login: None}):
            self.fake.profiles = profiles
            with self.subTest(profiles=bool(profiles)), self.assertRaises(v.SafetyError):
                self.lookup()

    def test_invalid_integration_identity_fails(self):
        for login in ('', ' ', 'Administrator', 'Guest', None, 5):
            self.fake.login = login
            with self.subTest(login=login), self.assertRaises(v.SafetyError):
                self.lookup()

    def test_unexpected_profile_and_directory_fields_fail_without_payload_output(self):
        for surface in ('profile', 'directory'):
            for field in ('api_key', 'api_secret', 'password', 'enabled', 'unexpected'):
                self.fake = NativeLookupFake()
                row = self.fake.profiles[self.fake.login] if surface == 'profile' else self.fake.rows[1]
                row[field] = 'SENSITIVE-SENTINEL'
                output = io.StringIO()
                with self.subTest(surface=surface, field=field), contextlib.redirect_stdout(output), \
                        contextlib.redirect_stderr(output), self.assertRaises(v.SafetyError) as raised:
                    self.lookup()
                self.assertEqual(output.getvalue(), '')
                self.assertNotIn('SENSITIVE-SENTINEL', str(raised.exception))

    def test_exact_mcp_rest_mismatch_fails_for_every_lookup(self):
        overrides = {'get_current_user': {'message': 'other@example.invalid'},
                     'get_user_details': {'message': {self.fake.login: {'name': self.fake.login}}},
                     'list_assignable_users': {'message': []},
                     'search_assignable_users': {'message': []},
                     'list_document_assignments': {'data': [{'name': 'MCP-only'}]}}
        for tool, value in overrides.items():
            self.fake.override = {tool: value}
            with self.subTest(tool=tool), self.assertRaises(v.SafetyError):
                self.lookup()

    def assignment(self):
        return {'name': 'TODO-1', 'reference_type': 'Item',
                'reference_name': FIXTURE['items'][0]['item_code'],
                'allocated_to': self.fake.login, 'assigned_by': self.fake.login, 'status': 'Open'}

    def test_existing_document_assignment_is_distinct_from_empty(self):
        self.fake.assignments = [self.assignment()]
        self.assertEqual(self.lookup()['list_document_assignments'], 'existing-document-assignment-rest-match')

    def test_assignment_wrong_document_and_projection_fail(self):
        for changes in ({'reference_name': 'OTHER'}, {'reference_type': 'Customer'},
                        {'api_secret': 'SENSITIVE-SENTINEL'}):
            self.fake.assignments = [dict(self.assignment(), **changes)]
            with self.subTest(fields=list(changes)), self.assertRaises(v.SafetyError) as raised:
                self.lookup()
            self.assertNotIn('SENSITIVE-SENTINEL', str(raised.exception))

    def test_native_http_and_envelope_failures(self):
        for response in ((403, {}, ''), (200, {}, 'not-json'), (200, {}, '[]'),
                         (200, {}, '{"message":"integration@example.invalid","api_key":"secret"}')):
            with self.subTest(status=response[0]), patch.object(v, 'request', return_value=response), \
                    self.assertRaises(v.SafetyError):
                v.lookup_reads(self.fake, self.fake, FIXTURE)

    def test_verify_merges_lookup_coverage_before_readonly_return(self):
        with patch.object(v, 'baseline'), patch.object(v, 'additional_reads', return_value={'old': 'existing-record'}), \
                patch.object(v, 'request', side_effect=self.fake.request):
            coverage = v.verify(self.fake, self.fake, FIXTURE)
        self.assertEqual(len(coverage), 6)
        self.assertEqual(coverage['old'], 'existing-record')
        self.assertIn('get_current_user', coverage)


if __name__ == '__main__':
    unittest.main()
