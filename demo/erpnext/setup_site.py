"""Fed to bench console via stdin. 'credentials' is supplied by the private driver."""
from pathlib import Path
# IPython can use separate globals and locals for protected stdin execution.
credentials = locals()['credentials']
permissions_source = locals()['permissions_source']
import frappe  # pyright: ignore[reportMissingImports]
from frappe.utils.password import check_password, get_decrypted_password  # pyright: ignore[reportMissingImports]
from erpnext.setup.setup_wizard.setup_wizard import stage_fixtures  # pyright: ignore[reportMissingImports]

marker = Path(frappe.get_site_path('private', 'demo-bootstrap-ok'))
if marker.exists():
    marker.unlink()
frappe.set_user('Administrator')
# Bench console has no HTTP request to establish the locale for Jinja fixtures.
frappe.local.lang = 'en'
check_password('Administrator', credentials['admin']['password'])
# Same setup-wizard entry point used by the retained evaluation bootstrap.
stage_fixtures({'country': 'Sri Lanka'})
role = 'ERPBridge Demo'
permission_namespace = {}
# The private driver supplies this reviewed code, not site data or credentials.
exec(permissions_source, permission_namespace)
# One source of truth for the role name and for the roles that are not drift.
# The integration identity deliberately also holds the report roles, so a
# duplicate hard-coded set here would reject an authorised bootstrap.
role = permission_namespace['ROLE']
allowed_roles = permission_namespace['ALLOWED_ROLES']
permission_namespace['apply_demo_permissions'](
    credentials['integration']['username'], require_existing_user=False)

for kind in ['admin', 'integration']:
    saved = credentials[kind]
    username = saved['username']
    exists = frappe.db.exists('User', username)
    user = frappe.get_doc('User', username) if exists else frappe.get_doc({
        'doctype': 'User', 'email': username, 'first_name': 'ERPBridge Demo',
        'enabled': 1, 'user_type': 'System User', 'send_welcome_email': 0,
        'roles': [{'role': role}]})
    if kind == 'integration':
        # Reject privilege drift instead of silently accepting an administrator.
        if exists and {r.role for r in user.roles} - allowed_roles:
            raise ValueError('Integration user has unexpected roles')
        if not exists: user.insert()
    if user.api_key:
        if user.api_key != saved['api_key'] or get_decrypted_password('User', username, 'api_secret') != saved['api_secret']:
            raise ValueError('Existing API credentials differ; no automatic reset')
    else:
        user.api_key = saved['api_key']
        user.api_secret = saved['api_secret']
        user.save()
frappe.clear_cache()
frappe.db.commit()
marker.parent.mkdir(parents=True, exist_ok=True)
marker.write_text('ok')
