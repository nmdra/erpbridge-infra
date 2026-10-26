#!/usr/bin/env python3
"""Curated onboarding. Child diagnostics are suppressed because they may contain secrets.

Only BRIDGE_API_TOKEN in the environment is used for bridge authentication.
No token is passed as a command-line argument. Unknown manifest layouts fail closed.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
READ_ONLY_ADDITIONS = {
    'get_company': '/resource/Company/{name}',
    'list_item_groups': '/resource/Item%20Group',
    'list_uoms': '/resource/UOM',
    'list_customers': '/resource/Customer',
    'get_customer': '/resource/Customer/{name}',
    'list_suppliers': '/resource/Supplier',
    'get_supplier': '/resource/Supplier/{name}',
    'list_sales_orders': '/resource/Sales%20Order',
    'get_sales_order': '/resource/Sales%20Order/{name}',
    'list_purchase_orders': '/resource/Purchase%20Order',
    'get_purchase_order': '/resource/Purchase%20Order/{name}',
    'list_sales_invoices': '/resource/Sales%20Invoice',
    'get_sales_invoice': '/resource/Sales%20Invoice/{name}',
    'list_purchase_invoices': '/resource/Purchase%20Invoice',
    'get_purchase_invoice': '/resource/Purchase%20Invoice/{name}',
    'list_payment_entries': '/resource/Payment%20Entry',
    'get_payment_entry': '/resource/Payment%20Entry/{name}',
    'list_journal_entries': '/resource/Journal%20Entry',
    'get_journal_entry': '/resource/Journal%20Entry/{name}',
    'list_assets': '/resource/Asset',
    'get_asset': '/resource/Asset/{name}',
    'list_asset_categories': '/resource/Asset%20Category',
    'get_asset_category': '/resource/Asset%20Category/{name}',
    'list_locations': '/resource/Location',
    'get_location': '/resource/Location/{name}',
    'list_asset_movements': '/resource/Asset%20Movement',
    'get_asset_movement': '/resource/Asset%20Movement/{name}',
    'list_asset_maintenance': '/resource/Asset%20Maintenance',
    'get_asset_maintenance': '/resource/Asset%20Maintenance/{name}',
    'list_asset_repairs': '/resource/Asset%20Repair',
    'get_asset_repair': '/resource/Asset%20Repair/{name}',
    'list_asset_depreciation_schedules': '/resource/Asset%20Depreciation%20Schedule',
    'get_asset_depreciation_schedule': '/resource/Asset%20Depreciation%20Schedule/{name}',
    'list_assignments': '/resource/ToDo',
    'get_assignment': '/resource/ToDo/{name}',
    'search_assignable_users': '/method/frappe.desk.search.get_names_for_mentions',
    'list_assignable_users': '/method/frappe.desk.search.get_names_for_mentions',
    'get_user_details': '/method/frappe.desk.form.load.get_user_info_for_viewers',
    'get_current_user': '/method/frappe.auth.get_logged_user',
    'list_document_assignments': '/resource/ToDo',

    # Batch A and B surface expansion. See
    # .agents/plans/Plan-ERPNext-Surface-Batch-A-B.md.
    'list_accounts': '/resource/Account',
    'get_account': '/resource/Account/{name}',
    'list_addresses': '/resource/Address',
    'get_address': '/resource/Address/{name}',
    'list_asset_capitalizations': '/resource/Asset%20Capitalization',
    'get_asset_capitalization': '/resource/Asset%20Capitalization/{name}',
    'list_asset_value_adjustments': '/resource/Asset%20Value%20Adjustment',
    'get_asset_value_adjustment': '/resource/Asset%20Value%20Adjustment/{name}',
    'list_boms': '/resource/BOM',
    'get_bom': '/resource/BOM/{name}',
    'list_bank_accounts': '/resource/Bank%20Account',
    'get_bank_account': '/resource/Bank%20Account/{name}',
    'list_batches': '/resource/Batch',
    'get_batch': '/resource/Batch/{name}',
    'list_bins': '/resource/Bin',
    'get_bin': '/resource/Bin/{name}',
    'list_contacts': '/resource/Contact',
    'get_contact': '/resource/Contact/{name}',
    'list_cost_centers': '/resource/Cost%20Center',
    'get_cost_center': '/resource/Cost%20Center/{name}',
    'list_currencies': '/resource/Currency',
    'get_currency': '/resource/Currency/{name}',
    'list_customer_groups': '/resource/Customer%20Group',
    'get_customer_group': '/resource/Customer%20Group/{name}',
    'list_delivery_notes': '/resource/Delivery%20Note',
    'get_delivery_note': '/resource/Delivery%20Note/{name}',
    'list_departments': '/resource/Department',
    'get_department': '/resource/Department/{name}',
    'list_designations': '/resource/Designation',
    'get_designation': '/resource/Designation/{name}',
    'list_employees': '/resource/Employee',
    'get_employee': '/resource/Employee/{name}',
    'list_fiscal_years': '/resource/Fiscal%20Year',
    'get_fiscal_year': '/resource/Fiscal%20Year/{name}',
    'list_gl_entries': '/resource/GL%20Entry',
    'get_gl_entry': '/resource/GL%20Entry/{name}',
    'list_item_prices': '/resource/Item%20Price',
    'get_item_price': '/resource/Item%20Price/{name}',
    'list_leads': '/resource/Lead',
    'get_lead': '/resource/Lead/{name}',
    'list_modes_of_payment': '/resource/Mode%20of%20Payment',
    'get_mode_of_payment': '/resource/Mode%20of%20Payment/{name}',
    'list_opportunities': '/resource/Opportunity',
    'get_opportunity': '/resource/Opportunity/{name}',
    'list_price_lists': '/resource/Price%20List',
    'get_price_list': '/resource/Price%20List/{name}',
    'list_projects': '/resource/Project',
    'get_project': '/resource/Project/{name}',
    'list_purchase_receipts': '/resource/Purchase%20Receipt',
    'get_purchase_receipt': '/resource/Purchase%20Receipt/{name}',
    'list_quotations': '/resource/Quotation',
    'get_quotation': '/resource/Quotation/{name}',
    'list_request_for_quotations': '/resource/Request%20for%20Quotation',
    'get_request_for_quotation': '/resource/Request%20for%20Quotation/{name}',
    'list_sales_taxes_and_charges_templates': '/resource/Sales%20Taxes%20and%20Charges%20Template',
    'get_sales_taxes_and_charges_template': '/resource/Sales%20Taxes%20and%20Charges%20Template/{name}',
    'list_serial_nos': '/resource/Serial%20No',
    'get_serial_no': '/resource/Serial%20No/{name}',
    'list_stock_entries': '/resource/Stock%20Entry',
    'get_stock_entry': '/resource/Stock%20Entry/{name}',
    'list_stock_ledger_entries': '/resource/Stock%20Ledger%20Entry',
    'get_stock_ledger_entry': '/resource/Stock%20Ledger%20Entry/{name}',
    'list_supplier_groups': '/resource/Supplier%20Group',
    'get_supplier_group': '/resource/Supplier%20Group/{name}',
    'list_supplier_quotations': '/resource/Supplier%20Quotation',
    'get_supplier_quotation': '/resource/Supplier%20Quotation/{name}',
    'list_tasks': '/resource/Task',
    'get_task': '/resource/Task/{name}',
    'list_terms_and_conditions': '/resource/Terms%20and%20Conditions',
    'get_terms_and_conditions': '/resource/Terms%20and%20Conditions/{name}',
    'list_territories': '/resource/Territory',
    'get_territory': '/resource/Territory/{name}',
    'list_timesheets': '/resource/Timesheet',
    'get_timesheet': '/resource/Timesheet/{name}',
    'list_warehouses': '/resource/Warehouse',
    'get_warehouse': '/resource/Warehouse/{name}',
    'list_work_orders': '/resource/Work%20Order',
    'get_work_order': '/resource/Work%20Order/{name}',
    'list_genders': '/resource/Gender',
    'list_payment_terms_templates': '/resource/Payment%20Terms%20Template',
    'list_salutations': '/resource/Salutation',
    'count_records': '/method/frappe.client.get_count',
    'search_records': '/method/frappe.client.get_list',
    # Phase A read additions.
    'get_item_group': '/resource/Item%20Group/{name}',
    'get_uom': '/resource/UOM/{name}',
    'get_salutation': '/resource/Salutation/{name}',
    'get_gender': '/resource/Gender/{name}',
    'get_payment_terms_template': '/resource/Payment%20Terms%20Template/{name}',
    'get_value': '/method/frappe.client.get_value',
    'search_link': '/method/frappe.desk.search.search_link',
}
READ_ALIASES = {
    'list_assignable_users': 'search_assignable_users',
    'list_document_assignments': 'list_assignments',
}
LOOKUP_READS = {'search_assignable_users', 'list_assignable_users', 'get_user_details',
                'get_current_user', 'list_document_assignments'}
# Write tools added for the demo dataset programme. They are bound to the same
# ERP credential as every other tool, so they must be owned and listed here.
# submit_document is present again as version 2.0.0: the 1.0.0 revision used
# frappe.client.submit, which this ERP (Frappe 16) does not expose over REST,
# so it was removed and rebuilt on frappe.desk.form.save.savedocs.
WRITE_ADDITIONS = {
    'create_customer', 'delete_customer', 'create_supplier', 'delete_supplier',
    'create_item_price', 'delete_item_price', 'insert_document',
    'submit_document', 'cancel_document', 'delete_document', 'update_document',
}
APPROVED = {'list_companies', 'list_items', 'get_item', 'create_item', 'delete_item',
            'update_item', 'create_assignment', 'remove_assignment', 'complete_assignment',
            'reopen_assignment', 'run_report'} | set(READ_ONLY_ADDITIONS) | WRITE_ADDITIONS
REGISTRY = ROOT / 'artifacts/erp-target-manifests.json'


class SafetyError(Exception):
    pass


def run(args):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=45)
    except (OSError, subprocess.TimeoutExpired):
        raise SafetyError('Command unavailable or timed out; details suppressed.') from None
    if p.returncode:
        raise SafetyError('Command failed: ' + args[0] + '; diagnostics suppressed.')
    return p.stdout


def cli(*args):
    return run(['bridgectl', '--context', os.environ.get('BRIDGE_CONTEXT', 'erpbridge-demo'), *args])


def parse_json(text):
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        raise SafetyError('Invalid JSON input; details suppressed.') from None


def protected_json(path):
    import stat
    info = path.stat()
    if path.is_symlink() or not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
        raise SafetyError('Protected state must be an owned regular file with mode 0600.')
    return parse_json(path.read_text())


def verify_discovery(endpoint):
    import importlib.util
    spec = importlib.util.spec_from_file_location('erpnext_verify', ROOT / 'scripts/verify-erpnext-demo.py')
    assert spec is not None and spec.loader is not None
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    tools = verifier.MCP(endpoint + '/', 'Bearer ' + os.environ['BRIDGE_API_TOKEN']).discover()
    names = [tool.get('name') for tool in tools if isinstance(tool, dict)]
    if any(names.count(name) != 1 for name in APPROVED):
        raise SafetyError('MCP discovery differs from approved tools.')


def tf(name):
    return parse_json(run(['terraform', '-chdir=' + os.environ.get('TF_DIR', str(ROOT / 'terraform')), 'output', '-json', name]))


def url(value, local=False):
    p = urlsplit(value)
    if (not p.hostname or p.username or p.password or p.query or p.fragment
            or not (p.scheme == 'https' or (local and p.scheme == 'http' and p.hostname in ('127.0.0.1', 'localhost', '::1')))):
        raise SafetyError('Expected HTTPS URL without credentials; HTTP requires explicit loopback fixture opt-in.')
    return value.rstrip('/')


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(value, f, indent=2)
            f.write('\n')
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def walk(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k, v
            yield from walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from walk(v)


def identity(manifest):
    m = manifest.get('metadata', {})
    name, version = m.get('name'), m.get('version')
    if not isinstance(name, str) or not isinstance(version, str) or not version:
        raise SafetyError('Unknown live manifest name/version shape; no changes made.')
    return name, version


def inventory(value):
    # The deployed Go server encodes its empty tool slice as JSON null.
    if value is None:
        return []
    if not isinstance(value, (list, dict)):
        raise SafetyError('Unknown registry response shape; no changes made.')
    tools = value if isinstance(value, list) else value.get('tools')
    if not isinstance(tools, list) or any(not isinstance(t, dict) for t in tools):
        raise SafetyError('Unknown registry response shape; no changes made.')
    for t in tools:
        identity(t)
    return tools


def tool_inventory(text):
    # This CLI always formats list results as tables. Named/versioned get honors
    # JSON, so use each exact identity from the table to read the full manifest.
    import re
    lines = [line.split() for line in text.splitlines() if line.strip()]
    if lines and lines[0] == ['NAME', 'MODULE', 'VERSION', 'STATUS']:
        tools, seen = [], set()
        for row in lines[1:]:
            if (len(row) != 4 or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', row[0])
                    or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+-]*', row[2])):
                raise SafetyError('Unknown CLI inventory row; no changes made.')
            key = (row[0], row[2])
            if key in seen:
                raise SafetyError('Duplicate CLI tool identity; no changes made.')
            seen.add(key)
            tool = parse_json(cli('tool', 'get', row[0] + '@' + row[2], '--output', 'json'))
            if identity(tool) != key or tool.get('metadata', {}).get('module') != row[1]:
                raise SafetyError('Named tool differs from listed identity; no changes made.')
            tools.append(tool)
        return inventory(tools)
    return inventory(parse_json(text))


def selected(manifest):
    return any(v == 'ERP_PRIMARY_KEY' for _, v in walk(manifest))


def binding(manifest, endpoint):
    pairs = list(walk(manifest))
    refs = [v for k, v in pairs if k in ('credentialRef', 'credential_ref')]
    urls = [v for k, v in pairs if isinstance(v, str) and v.startswith(('https://', 'http://'))]
    if refs != ['ERP_PRIMARY_KEY'] or not urls:
        raise SafetyError('Manifest must have exactly one ERP_PRIMARY_KEY reference and an explicit endpoint.')
    for u in urls:
        if not (u == endpoint + '/api' or u.startswith(endpoint + '/api/')):
            raise SafetyError('Manifest endpoint differs from selected target.')
    forbidden = {'authorization', 'apikey', 'api_key', 'api_secret', 'password', 'token'}
    if any(k.lower() in forbidden and v not in ('', None) for k, v in pairs):
        raise SafetyError('Literal authentication material is forbidden in manifests.')


def preserve_delete_envelope(tool, contract):
    # ERPNext DELETE returns HTTP 202 with {"data":"ok"}. Keep its object
    # envelope instead of the generator's scalar data extraction for MCP.
    schema = contract['paths']['/resource/Item/{name}']['delete']['responses']['202']['content']['application/json']['schema']
    tool['spec']['outputSchema'] = schema
    tool['spec'].setdefault('execution', {}).pop('responsePath', None)


def check_ownership(tools, registry, endpoint, bridge):
    overlapping = [t for t in tools if selected(t) or identity(t)[0] in APPROVED]
    if not overlapping:
        return
    if not registry or registry.get('target') != 'erpnext' or registry.get('endpoint') != endpoint or registry.get('bridge') != bridge:
        raise SafetyError('Unknown or previous target ownership overlaps selected credentials; explicit cutover required.')
    owned = {(m['name'], m['version']) for m in registry.get('manifests', [])}
    for t in overlapping:
        if identity(t) not in owned or identity(t)[0] not in APPROVED:
            raise SafetyError('Unowned or stale tool overlaps selected credential/name; refusing onboarding.')
        binding(t, endpoint)


def expand_read_aliases(tools):
    """OpenAPI has one operation per route; derive only the reviewed read aliases."""
    import copy
    if not isinstance(tools, list):
        raise SafetyError('Unknown generated tool collection.')
    result = copy.deepcopy(tools)
    by_name = {identity(tool)[0]: tool for tool in result}
    if len(by_name) != len(result):
        raise SafetyError('Duplicate generated operation names.')
    for name, source in READ_ALIASES.items():
        if name in by_name:
            continue
        if source not in by_name:
            raise SafetyError('Missing source operation for a reviewed read alias.')
        alias = copy.deepcopy(by_name[source])
        alias['metadata']['name'] = name
        schema = alias['spec'].setdefault('inputSchema', {'type': 'object'})
        properties = schema.setdefault('properties', {})
        if name == 'list_assignable_users':
            alias['metadata']['description'] = (
                'List mention-visible enabled System User candidates. Groups can also appear: '
                'exclude entries with is_group=true. Excludes Administrator, Guest, Website Users '
                'and users who disallow mentions. Does not prove document access. Send search_term=""; '
                'compare id to an email when needed.')
            properties['search_term'] = {'type': 'string', 'enum': [''], 'default': ''}
            schema['required'] = ['search_term']
        else:
            alias['metadata']['description'] = (
                'Read ToDo assignments for a document visible to the integration user. '
                'Supply filters as a JSON object containing reference_type and reference_name, '
                'for example {"reference_type":"Item","reference_name":"FG-TEA-001"}. '
                'Uses native ToDo read permissions; this does not grant access to the target document.')
            properties['filters'] = {'type': 'string', 'description':
                                    'JSON object with reference_type and reference_name.'}
            fields = '["name","reference_type","reference_name","allocated_to","assigned_by","status"]'
            properties['fields'] = {'type': 'string', 'enum': [fields], 'default': fields}
            schema['required'] = list(dict.fromkeys(schema.get('required', []) + ['filters', 'fields']))
            execution = alias['spec']['execution']
            execution.setdefault('parameterLocations', {}).update(filters='query', fields='query')
            execution.setdefault('mapping', {}).update(filters='filters', fields='fields')
        by_name[name] = alias
        result.append(alias)
    return result


def constrain_read_addition(tool, endpoint):
    name, _ = identity(tool)
    if name not in READ_ONLY_ADDITIONS:
        return
    execution = tool.get('spec', {}).get('execution', {})
    if execution.get('method') != 'GET' or execution.get('endpoint') != endpoint + '/api' + READ_ONLY_ADDITIONS[name]:
        raise SafetyError('New read tool differs from the approved GET resource route.')
    tool['spec'].setdefault('annotations', {}).update({
        'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True,
    })
    if name in LOOKUP_READS:
        # User directory and assignment results must not reuse bridge response caches.
        tool['spec'].setdefault('cache', {}).update(enabled=False)
        # Frappe checks legacy cmd before URL routing. Block nonempty dispatch
        # overrides with a supported string enum, independently of root closure.
        schema = tool['spec'].setdefault('inputSchema', {'type': 'object'})
        schema.setdefault('properties', {})['cmd'] = {
            'type': 'string', 'enum': [''], 'default': '',
            'description': 'Reserved dispatcher parameter. Leave empty.',
        }
        execution.setdefault('parameterLocations', {})['cmd'] = 'query'
        execution.setdefault('mapping', {})['cmd'] = 'cmd'


def check_additive_generation(existing, generated):
    candidates = {identity(t): t for t in generated}
    for previous in existing:
        if not selected(previous):
            continue
        candidate = candidates.get(identity(previous))
        if candidate is None:
            raise SafetyError('Generated versions omit an active owned tool; reviewed cutover required.')
        if previous.get('metadata', {}).get('module') != candidate.get('metadata', {}).get('module'):
            raise SafetyError('Existing tool module changed; no tools applied.')
        for field in ('inputSchema', 'outputSchema', 'execution', 'security', 'annotations'):
            if previous.get('spec', {}).get(field) != candidate.get('spec', {}).get(field):
                raise SafetyError('Existing tool contract changed; no tools applied.')


def ensure_api_registration(endpoint):
    """Keep the operator's existing base URL and API identity unchanged."""
    registry = parse_json(cli('api', 'list', '--output', 'json'))
    entries = registry.get('items') if isinstance(registry, dict) else None
    if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
        raise SafetyError('Unknown API registry shape; no registration changed.')
    matches = [entry for entry in entries if entry.get('name') == 'erpnext']
    if len(matches) > 1:
        raise SafetyError('Duplicate ERPNext API registrations; no changes made.')
    if matches:
        entry = matches[0]
        expected = {'method': 'GET', 'module': 'erpnext', 'authType': 'api-key',
                    'authHeader': 'Authorization', 'credentialRef': 'ERP_PRIMARY_KEY',
                    'status': 'active'}
        if (any(entry.get(key) != value for key, value in expected.items())
                or entry.get('credentialSource', 'env') != 'env'
                or entry.get('url') not in (endpoint + '/api', endpoint + '/api/resource/Company')):
            raise SafetyError('Existing API binding differs from the approved target; no registration changed.')
        return
    cli('api', 'register', '--name', 'erpnext', '--description',
        'Curated ERPNext SCP demo and approved business reads', '--url', endpoint + '/api',
        '--method', 'GET', '--module', 'erpnext', '--auth-type', 'api-key',
        '--auth-header', 'Authorization', '--credential-ref', 'ERP_PRIMARY_KEY')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", action="store_true", help="Consent to register the API and apply the approved tools")
    args = parser.parse_args()
    if not args.confirm:
        parser.error("Onboarding requires --confirm. No changes made.")
    if not os.environ.get('BRIDGE_API_TOKEN'):
        raise SafetyError('BRIDGE_API_TOKEN must be set in the environment.')
    if tf('erp_target') != 'erpnext':
        raise SafetyError('Terraform selected target is not erpnext.')
    endpoint, bridge = url(tf('erpnext_url')), url(tf('erpbridge_url'))
    # The target's authoritative Terraform URL overrides a stale CLI context.
    os.environ['BRIDGE_SERVER'] = bridge
    os.environ['BRIDGE_MCP_SERVER'] = bridge
    # Verify CLI/server compatibility before registration or generation.
    run([str(ROOT / 'scripts/check-bridge-compatibility.sh')])
    tools = tool_inventory(cli('tool', 'get', '--output', 'json'))
    saved = protected_json(REGISTRY) if REGISTRY.exists() else None
    check_ownership(tools, saved, endpoint, bridge)
    spec = parse_json((ROOT / 'config/erpnext-openapi.yaml').read_text().split('\n', 1)[1])
    spec['servers'] = [{'url': endpoint + '/api'}]
    with tempfile.TemporaryDirectory() as directory:
        d = Path(directory)
        atomic(d / 'openapi.yaml', spec)
        ensure_api_registration(endpoint)
        generated = expand_read_aliases(parse_json(cli('tool', 'generate', '--api', 'erpnext', '--openapi', str(d / 'openapi.yaml'), '--output', 'json')))
        if not isinstance(generated, list) or len(generated) != len(APPROVED) or {identity(t)[0] for t in generated} != APPROVED:
            raise SafetyError('Generation did not produce exactly the approved operations.')
        manifests = d / 'manifests'
        for t in generated:
            if identity(t)[0] == 'delete_item':
                preserve_delete_envelope(t, spec)
            constrain_read_addition(t, endpoint)
            binding(t, endpoint)
            name, _ = identity(t)
            atomic(manifests / (name + '.json'), t)
        check_additive_generation(tools, generated)
        # Every file must validate offline before any tool apply.
        for file in sorted(manifests.iterdir()):
            cli('tool', 'validate', '--file', str(file))
        ownership = {'target': 'erpnext', 'endpoint': endpoint, 'bridge': bridge,
                     'state': 'pending', 'manifests': [dict(zip(('name', 'version'), identity(t))) for t in generated]}
        # Keep ownership recoverable even if apply succeeds only in part.
        atomic(REGISTRY, ownership)
        cli('tool', 'apply', '--file', str(manifests))
        live = tool_inventory(cli('tool', 'get', '--output', 'json'))
        check_ownership(live, ownership, endpoint, bridge)
        if {identity(t) for t in live if selected(t)} != {identity(t) for t in generated}:
            raise SafetyError('Applied discovery differs from expected manifest versions.')
        verify_discovery(url(tf('erpbridge_mcp_url')))
        ownership['state'] = 'verified'
        atomic(REGISTRY, ownership)
        out = ROOT / 'artifacts/erpnext-generated-manifests'
        for t in generated:
            atomic(out / (identity(t)[0] + '.json'), t)
    print(str(len(APPROVED)) + ' ERPNext manifests validated, applied, and discovered through MCP. Run the read-only record verifier next.')


if __name__ == '__main__':
    try:
        main()
    except (SafetyError, ValueError, KeyError, TypeError, OSError):
        import sys
        print('ERPNext onboarding failed safely. Check target ownership, endpoint, CLI schema, and protected inputs. No broad deletion was attempted.', file=sys.stderr)
        sys.exit(1)
