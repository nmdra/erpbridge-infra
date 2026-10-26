#!/usr/bin/env python3
"""Deterministic MCP and independent ERPNext checks. Read-only unless --write.

Credentials are read from a mode-0600 deployment artifact or environment, never
from argv. HTTP is permitted only for explicitly opted-in loopback fixtures.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('onboarding', ROOT / 'scripts/onboard-erpnext.py')
assert spec is not None and spec.loader is not None
onboarding = importlib.util.module_from_spec(spec)
spec.loader.exec_module(onboarding)
APPROVED = onboarding.APPROVED
SafetyError = onboarding.SafetyError


class MCPReportedError(SafetyError):
    """An explicit MCP error response, not a transport or parsing failure."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SafetyError('Redirect refused to protect credentials.')


def request(url, token, payload=None, headers=None, method=None):
    h = {'Authorization': token, 'Accept': 'application/json, text/event-stream'}
    h.update(headers or {})
    data = None if payload is None else json.dumps(payload).encode()
    if data is not None:
        h['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=25) as response:
            return response.status, dict(response.headers.items()), response.read(4 * 1024 * 1024).decode()
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        return status, {}, ''
    except (urllib.error.URLError, OSError, UnicodeError):
        raise SafetyError('HTTP transport failed; details suppressed.') from None


def rpc_result(body, request_id):
    try:
        if body.lstrip().startswith('{'):
            messages = [json.loads(body)]
        else:
            messages = []
            # SSE permits multiline data and CRLF. Ignore unrelated events.
            for event in body.replace('\r\n', '\n').split('\n\n'):
                data = '\n'.join(line[5:].lstrip(' ') for line in event.splitlines() if line.startswith('data:'))
                if data and data != '[DONE]':
                    messages.append(json.loads(data))
        for message in messages:
            if isinstance(message, dict) and message.get('id') == request_id:
                if message.get('jsonrpc') != '2.0':
                    raise SafetyError('Invalid MCP JSON-RPC response.')
                if 'error' in message:
                    error = message['error']
                    if ('result' in message or not isinstance(error, dict)
                            or type(error.get('code')) is not int or not isinstance(error.get('message'), str)):
                        raise SafetyError('Invalid MCP JSON-RPC error response.')
                    raise MCPReportedError('MCP JSON-RPC reported an error.')
                if 'result' not in message:
                    raise SafetyError('Invalid MCP JSON-RPC response.')
                if isinstance(message['result'], dict) and message['result'].get('isError'):
                    if message['result']['isError'] is not True:
                        raise SafetyError('Invalid MCP isError flag.')
                    raise MCPReportedError('MCP tool reported isError.')
                return message['result']
    except (ValueError, TypeError):
        pass
    raise SafetyError('Missing or invalid MCP JSON-RPC response.')


def resource(result):
    """Unwrap documented MCP content containers, never infer success from text."""
    if not isinstance(result, dict) or result.get('isError'):
        raise SafetyError('Invalid MCP tool result.')
    if 'structuredContent' in result:
        value = result['structuredContent']
    else:
        blocks = result.get('content')
        if not isinstance(blocks, list):
            raise SafetyError('MCP result lacks resource content.')
        texts = [b.get('text') for b in blocks if isinstance(b, dict) and b.get('type') == 'text']
        if len(texts) != 1 or not isinstance(texts[0], str):
            raise SafetyError('Ambiguous MCP text content.')
        try:
            value = json.loads(texts[0])
        except ValueError:
            raise SafetyError('MCP text is not resource JSON.') from None
    if isinstance(value, dict) and 'data' in value:
        return value['data']
    if isinstance(value, (dict, list)):
        return value
    raise SafetyError('Unexpected MCP resource payload.')


class MCP:
    def __init__(self, endpoint, token):
        self.endpoint, self.token, self.sequence = endpoint, token, 0
        self.headers = {'Mcp-Protocol-Version': '2025-06-18'}
        result, headers = self.call('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 'erpnext-viva-verify', 'version': '1'}})
        if not isinstance(result, dict) or not result.get('protocolVersion'):
            raise SafetyError('MCP initialize did not negotiate a protocol.')
        self.headers['Mcp-Protocol-Version'] = result['protocolVersion']
        for key, value in headers.items():
            if key.lower() == 'mcp-session-id':
                self.headers['Mcp-Session-Id'] = value
        status, _, _ = request(self.endpoint, self.token, {'jsonrpc': '2.0', 'method': 'notifications/initialized'}, self.headers)
        if status not in (200, 202, 204):
            raise SafetyError('MCP initialized notification failed.')

    def call(self, method, params):
        self.sequence += 1
        status, headers, body = request(self.endpoint, self.token, {'jsonrpc': '2.0', 'id': self.sequence, 'method': method, 'params': params}, self.headers)
        if status != 200:
            raise SafetyError('MCP HTTP request failed.')
        return rpc_result(body, self.sequence), headers

    def tool(self, name, arguments):
        return self.call('tools/call', {'name': name, 'arguments': arguments})[0]

    def discover(self):
        tools, cursor, seen = [], None, set()
        for _ in range(20):
            result, _ = self.call('tools/list', {'cursor': cursor} if cursor else {})
            if not isinstance(result, dict) or not isinstance(result.get('tools'), list):
                raise SafetyError('Invalid MCP discovery.')
            tools.extend(result['tools'])
            cursor = result.get('nextCursor')
            if not cursor:
                return tools
            if cursor in seen:
                break
            seen.add(cursor)
        raise SafetyError('MCP discovery pagination exceeded its safe bound.')


class ERP:
    def __init__(self, endpoint, token):
        self.endpoint, self.token = endpoint, token

    def get(self, doctype, name):
        status, _, body = request(self.endpoint + '/api/resource/' + urllib.parse.quote(doctype, safe='') + '/' + urllib.parse.quote(name, safe=''), self.token)
        if status == 404:
            return None
        if status != 200:
            raise SafetyError('Direct ERP read failed.')
        try:
            value = json.loads(body)['data']
        except (ValueError, KeyError):
            raise SafetyError('Direct ERP response is invalid.') from None
        if not isinstance(value, dict):
            raise SafetyError('Expected direct ERP record.')
        return value

    def list(self, doctype, fields, limit=1000, start=0):
        query = urllib.parse.urlencode({'fields': json.dumps(fields),
                                       'limit_page_length': limit, 'limit_start': start})
        status, _, body = request(self.endpoint + '/api/resource/' +
                                  urllib.parse.quote(doctype, safe='') + '?' + query, self.token)
        if status != 200:
            raise SafetyError('Direct ERP list read failed.')
        try:
            value = json.loads(body)['data']
        except (ValueError, KeyError, TypeError):
            raise SafetyError('Direct ERP list response is invalid.') from None
        return value

    def delete(self, name):
        status, _, _ = request(self.endpoint + '/api/resource/Item/' + urllib.parse.quote(name, safe=''), self.token, method='DELETE')
        if status not in (200, 202, 204):
            raise SafetyError('Run-owned Item cleanup failed.')


def assert_fields(record, expected):
    if not isinstance(record, dict) or any(record.get(k) != v for k, v in expected.items()):
        raise SafetyError('Business record fields differ from expected fixture values.')


def baseline(mcp, erp, fixture):
    company_fields = ['name', 'company_name', 'country', 'default_currency']
    companies = resource(mcp.tool('list_companies', {'fields': json.dumps(company_fields), 'limit_page_length': 1000}))
    company = fixture['company']
    if not isinstance(companies, list):
        raise SafetyError('Company list is not an array.')
    matches = [c for c in companies if isinstance(c, dict) and c.get('company_name') == company['company_name']]
    if len(matches) != 1:
        raise SafetyError('Expected exactly one SCP company.')
    assert_fields(matches[0], {k: company[k] for k in ('company_name', 'country', 'default_currency')})
    fields = ['name', 'item_code', 'item_name', 'item_group', 'stock_uom', 'is_stock_item']
    items = resource(mcp.tool('list_items', {'fields': json.dumps(fields), 'limit_page_length': 1000}))
    if not isinstance(items, list):
        raise SafetyError('Item list is not an array.')
    for expected in fixture['items']:
        found = [i for i in items if isinstance(i, dict) and i.get('item_code') == expected['item_code']]
        if len(found) != 1:
            raise SafetyError('Missing or duplicate baseline Item.')
        assert_fields(found[0], expected)
        assert_fields(erp.get('Item', expected['item_code']), expected)
    cleaner = next(i for i in fixture['items'] if i['item_code'] == 'FG-CLEANER-5L')
    assert_fields(resource(mcp.tool('get_item', {'name': cleaner['item_code']})), cleaner)



def compared_list(mcp, erp, tool, doctype, fields):
    """Compare the same bounded first page, not a claim of exhaustive inventory."""
    arguments = {'fields': json.dumps(fields), 'limit_page_length': 1000, 'limit_start': 0}
    records = resource(mcp.tool(tool, arguments))
    direct = erp.list(doctype, fields)

    def indexed(rows):
        if not isinstance(rows, list) or len(rows) > 1000:
            raise SafetyError('Expected bounded resource list.')
        result = {}
        for row in rows:
            if (not isinstance(row, dict) or any(field not in row for field in fields)
                    or not isinstance(row.get('name'), str) or not row['name']
                    or row['name'] in result):
                raise SafetyError('Invalid or duplicate named list record.')
            result[row['name']] = {field: row[field] for field in fields}
        return result

    if indexed(records) != indexed(direct):
        raise SafetyError('MCP list differs from independent REST page.')
    return records


def additional_reads(mcp, erp, fixture):
    coverage = {}
    companies = compared_list(mcp, erp, 'list_companies', 'Company',
                              ['name', 'company_name', 'country', 'default_currency'])
    matches = [row for row in companies if row['company_name'] == fixture['company']['company_name']]
    if len(matches) != 1:
        raise SafetyError('Expected exactly one fixture Company.')
    company = matches[0]
    expected = {key: fixture['company'][key] for key in ('company_name', 'country', 'default_currency')}
    assert_fields(resource(mcp.tool('get_company', {'name': company['name']})), dict(company, **expected))
    assert_fields(erp.get('Company', company['name']), dict(company, **expected))
    coverage['get_company'] = 'existing-record'

    groups = compared_list(mcp, erp, 'list_item_groups', 'Item Group', ['name', 'item_group_name'])
    for name in fixture['item_groups']:
        matches = [row for row in groups if row['item_group_name'] == name]
        if len(matches) != 1:
            raise SafetyError('Missing or duplicate fixture Item Group.')
        assert_fields(erp.get('Item Group', matches[0]['name']), matches[0])
    uoms = compared_list(mcp, erp, 'list_uoms', 'UOM', ['name', 'uom_name', 'must_be_whole_number'])
    for expected in fixture['uoms']:
        matches = [row for row in uoms if row['uom_name'] == expected['uom_name']]
        if len(matches) != 1:
            raise SafetyError('Missing or duplicate fixture UOM.')
        assert_fields(matches[0], expected)
        assert_fields(erp.get('UOM', matches[0]['name']), dict(matches[0], **expected))

    for plural, singular, doctype, fields in (
            ('customers', 'customer', 'Customer', ['name', 'customer_name']),
            ('suppliers', 'supplier', 'Supplier', ['name', 'supplier_name']),
            ('sales_orders', 'sales_order', 'Sales Order', ['name', 'company', 'status', 'currency', 'grand_total']),
            ('purchase_orders', 'purchase_order', 'Purchase Order', ['name', 'company', 'status', 'currency', 'grand_total']),
            ('sales_invoices', 'sales_invoice', 'Sales Invoice', ['name', 'company', 'docstatus']),
            ('purchase_invoices', 'purchase_invoice', 'Purchase Invoice', ['name', 'company', 'docstatus']),
            ('payment_entries', 'payment_entry', 'Payment Entry', ['name', 'company', 'docstatus']),
            ('journal_entries', 'journal_entry', 'Journal Entry', ['name', 'company', 'docstatus']),
            ('assets', 'asset', 'Asset', ['name']),
            ('asset_categories', 'asset_category', 'Asset Category', ['name']),
            ('locations', 'location', 'Location', ['name']),
            ('asset_movements', 'asset_movement', 'Asset Movement', ['name']),
            ('asset_maintenance', 'asset_maintenance', 'Asset Maintenance', ['name']),
            ('asset_repairs', 'asset_repair', 'Asset Repair', ['name']),
            ('asset_depreciation_schedules', 'asset_depreciation_schedule', 'Asset Depreciation Schedule', ['name']),
            ('assignments', 'assignment', 'ToDo', ['name'])):
        rows = compared_list(mcp, erp, 'list_' + plural, doctype, fields)
        tool = 'get_' + singular
        if rows:
            row = min(rows, key=lambda record: record['name'])
            direct = erp.get(doctype, row['name'])
            actual = resource(mcp.tool(tool, {'name': row['name']}))
            assert_fields(direct, row)
            assert_fields(actual, row)
            # Compare all business fields returned by the named MCP read.
            if not isinstance(actual, dict) or not actual:
                raise SafetyError('Expected named MCP record.')
            assert_fields(direct, actual)
            coverage[tool] = 'existing-record'
        else:
            name = 'VIVA-NOT-FOUND-' + str(uuid.uuid4())
            if erp.get(doctype, name) is not None:
                raise SafetyError('Negative probe name unexpectedly exists.')
            try:
                mcp.tool(tool, {'name': name})
            except MCPReportedError:
                # REST proved absence; a generic MCP error cannot prove its cause.
                coverage[tool] = 'explicit-error-probe'
            else:
                raise SafetyError('Missing-name MCP probe unexpectedly succeeded.')
    return coverage

def lookup_reads(mcp, erp, fixture):
    """Compare native read envelopes without claiming human identity or access.

    The mention directory is not document authorization evidence. Profiles do
    not expose enabled status. All calls use GET and fixed minimal projections.
    """
    def compared(tool, arguments, path, query=None):
        url = erp.endpoint + path
        if query is not None:
            url += '?' + urllib.parse.urlencode(query)
        actual = resource(mcp.tool(tool, arguments))
        status, _, body = request(url, erp.token)
        if status != 200:
            raise SafetyError('Direct ERP lookup read failed.')
        try:
            direct = json.loads(body)
        except (ValueError, TypeError):
            raise SafetyError('Direct ERP lookup response is invalid.') from None
        if actual != direct:
            raise SafetyError('MCP lookup differs from independent REST response.')
        return actual

    def message(envelope):
        if not isinstance(envelope, dict) or set(envelope) != {'message'}:
            raise SafetyError('Invalid native lookup envelope.')
        return envelope['message']

    login = message(compared('get_current_user', {}, '/api/method/frappe.auth.get_logged_user'))
    if not isinstance(login, str) or not login.strip() or login in ('Administrator', 'Guest'):
        raise SafetyError('Invalid integration user identity.')
    users = {'users': json.dumps([login])}
    profiles = message(compared('get_user_details', users,
                                '/api/method/frappe.desk.form.load.get_user_info_for_viewers', users))
    allowed_profile = {'fullname', 'image', 'name', 'email', 'time_zone'}
    if not isinstance(profiles, dict) or set(profiles) != {login}:
        raise SafetyError('Known integration user profile is missing or invalid.')
    profile = profiles[login]
    if (not isinstance(profile, dict) or set(profile) != allowed_profile or profile.get('name') != login
            or any(not isinstance(value, str) and not (value is None and key in ('image', 'time_zone'))
                   for key, value in profile.items())):
        raise SafetyError('Invalid minimal user profile projection.')

    def directory(tool, term):
        args = {'search_term': term}
        rows = message(compared(tool, args,
                                '/api/method/frappe.desk.search.get_names_for_mentions', args))
        if not isinstance(rows, list):
            raise SafetyError('Invalid mention directory list.')
        for row in rows:
            if (not isinstance(row, dict) or not {'id', 'value', 'link'} <= set(row)
                    or not set(row) <= {'id', 'value', 'link', 'is_group'}
                    or any(not isinstance(row[key], str) for key in ('id', 'value', 'link'))
                    or ('is_group' in row and type(row['is_group']) is not bool)
                    or (not row.get('is_group', False) and row['id'] in ('Administrator', 'Guest'))):
                raise SafetyError('Invalid minimal mention directory projection.')
        return rows

    rows = directory('list_assignable_users', '')
    # Native search matches display names, not email IDs. Groups can be listed
    # but must never become a selected user candidate.
    candidate = next((row for row in rows if not row.get('is_group', False) and row['value'].strip()), None)
    term = candidate['value'] if candidate else 'VIVA-NO-DISPLAY-NAME-MATCH'
    directory('search_assignable_users', term)

    target = fixture['items'][0]['item_code']
    fields = '["name","reference_type","reference_name","allocated_to","assigned_by","status"]'
    args = {'filters': json.dumps({'reference_type': 'Item', 'reference_name': target}),
            'fields': fields, 'limit_page_length': 1000, 'limit_start': 0}
    # resource() unwraps the standard data list for this resource endpoint.
    assignments = resource(mcp.tool('list_document_assignments', args))
    status, _, body = request(erp.endpoint + '/api/resource/ToDo?' + urllib.parse.urlencode(args), erp.token)
    if status != 200:
        raise SafetyError('Direct ERP document assignment read failed.')
    try:
        direct = json.loads(body)
    except (ValueError, TypeError):
        raise SafetyError('Direct ERP document assignment response is invalid.') from None
    if not isinstance(direct, dict) or set(direct) != {'data'} or assignments != direct['data']:
        raise SafetyError('MCP assignments differ from independent REST response.')
    allowed_assignment = {'name', 'reference_type', 'reference_name', 'allocated_to', 'assigned_by', 'status'}
    if not isinstance(assignments, list) or len(assignments) > 1000:
        raise SafetyError('Invalid bounded document assignment list.')
    for row in assignments:
        if (not isinstance(row, dict) or set(row) != allowed_assignment
                or row['reference_type'] != 'Item' or row['reference_name'] != target
                or not isinstance(row['name'], str) or not row['name']):
            raise SafetyError('Invalid document assignment target or projection.')
    return {
        'get_current_user': 'integration-identity-rest-match',
        'get_user_details': 'minimal-integration-profile-rest-match',
        'list_assignable_users': 'mention-directory-rest-match' if rows else 'empty-mention-directory-rest-match',
        'search_assignable_users': 'display-name-search-rest-match' if candidate else 'sentinel-search-rest-match',
        'list_document_assignments': 'existing-document-assignment-rest-match' if assignments else 'empty-document-assignments-rest-match',
    }


def verify(mcp, erp, fixture, write=False):
    tools = mcp.discover()
    names = [t.get('name') for t in tools if isinstance(t, dict)]
    if not APPROVED.issubset(names) or any(names.count(n) != 1 for n in APPROVED):
        raise SafetyError('MCP discovery lacks unique approved tools.')
    baseline(mcp, erp, fixture)
    coverage = additional_reads(mcp, erp, fixture)
    coverage.update(lookup_reads(mcp, erp, fixture))
    if not write:
        return coverage
    sample = {'item_code': 'VIVA-RUN-' + str(uuid.uuid4()), 'item_name': 'Serendib Floor Cleaner 1L (Viva Sample)', 'item_group': 'Finished Goods', 'stock_uom': 'Nos', 'is_stock_item': 1}
    name = sample['item_code']
    if erp.get('Item', name) is not None:
        raise SafetyError('Unique sample already exists; refusing to modify it.')
    attempted = False
    try:
        attempted = True
        # The OpenAPI generator flattens JSON body properties into tool inputs.
        created = resource(mcp.tool('create_item', sample))
        assert_fields(created, sample)
        assert_fields(erp.get('Item', name), sample)
        assert_fields(resource(mcp.tool('get_item', {'name': name})), sample)
        # The pinned API returns HTTP 202 with {"data":"ok"}. RPC/isError
        # checks apply; independent HTTP 404, not its text, proves deletion.
        mcp.tool('delete_item', {'name': name})
        if erp.get('Item', name) is not None:
            raise SafetyError('Deleted sample still exists in direct ERP read.')
    finally:
        try:
            if attempted:
                remaining = erp.get('Item', name)
                if remaining is not None:
                    # Random run ID was absent before create. Never remove a
                    # mismatching record, even if a concurrent writer changed it.
                    assert_fields(remaining, sample)
                    erp.delete(name)
                    if erp.get('Item', name) is not None:
                        raise SafetyError('Cleanup did not remove run-owned sample.')
        finally:
            baseline(mcp, erp, fixture)

    return coverage


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--deployment-file', type=Path, default=ROOT / 'artifacts/deployment-output.json')
    p.add_argument('--write', action='store_true', help='Consent to create and delete one unique run-owned Item')
    p.add_argument('--allow-local-http', action='store_true', help='Loopback HTTP fixtures only')
    args = p.parse_args()
    info = args.deployment_file.stat()
    if stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid() or args.deployment_file.is_symlink():
        raise SafetyError('Deployment artifact must be owned, non-symlink, and mode 0600.')
    deployment = onboarding.protected_json(args.deployment_file)
    if deployment['deployment']['erp_target'] != 'erpnext':
        raise SafetyError('Deployment artifact does not select erpnext.')
    endpoint = onboarding.url(deployment['urls']['erpnext'], args.allow_local_http)
    mcp_url = onboarding.url(deployment['urls']['erpbridge_mcp'], args.allow_local_http)
    bridge = onboarding.url(deployment['urls']['erpbridge'], args.allow_local_http)
    admin_token = os.environ.get('BRIDGE_API_TOKEN') or deployment['demo_tokens']['api_auth_token']
    if not isinstance(admin_token, str) or not admin_token:
        raise SafetyError('Bridge admin credential is missing for read-only registry checks.')
    # Registry checks must use the same bridge as MCP, not a stale CLI context.
    os.environ['BRIDGE_SERVER'] = bridge
    os.environ['BRIDGE_MCP_SERVER'] = bridge
    os.environ['BRIDGE_API_TOKEN'] = admin_token
    registry = onboarding.protected_json(onboarding.REGISTRY)
    live = onboarding.tool_inventory(onboarding.cli('tool', 'get', '--output', 'json'))
    onboarding.check_ownership(live, registry, endpoint, bridge)
    if {onboarding.identity(t)[0] for t in live if onboarding.selected(t)} != APPROVED:
        raise SafetyError('Registry contains stale or missing selected-credential tools.')
    token = os.environ.get('MCP_TOKEN') or os.environ.get('BRIDGE_API_TOKEN') or deployment['demo_tokens']['api_auth_token']
    erp_token = os.environ.get('ERP_PRIMARY_KEY') or deployment['demo_tokens']['erp_primary_key']
    if not isinstance(token, str) or not token or not isinstance(erp_token, str) or not erp_token:
        raise SafetyError('Required credentials are missing.')
    fixture = onboarding.parse_json((ROOT / 'demo/erpnext/fixtures/scp-masters.json').read_text())
    coverage = verify(MCP(mcp_url + '/', 'Bearer ' + token), ERP(endpoint, erp_token), fixture, args.write)
    print('ERPNext ' + ('write smoke' if args.write else 'read-only verification') + ' passed; eight baseline Items verified. Added lists match bounded first REST pages.')
    print('Named get coverage: ' + ', '.join(name + '=' + result for name, result in sorted(coverage.items())))


if __name__ == '__main__':
    try:
        main()
    except (SafetyError, ValueError, KeyError, TypeError, OSError, StopIteration):
        print('ERPNext verification failed. Check protected inputs, target ownership, MCP result shape, and expected record fields. Transport details suppressed.', file=sys.stderr)
        sys.exit(1)
