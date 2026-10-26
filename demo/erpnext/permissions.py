"""Native, metadata-only permission update, safe to exec in a bench console.

Call apply_demo_permissions(existing_integration_username), then commit explicitly.
This module does not read secrets, create users, or install business fixtures.

Scope history. The role began as Item write plus read-only reads. The demo
dataset programme widened it so the integration identity can build a realistic
trading, accounting, asset and production dataset through the bridge. Grants
stay per-doctype and permlevel 0. No user, role, permission or link bypass is
added, and native validation still applies to every document.

Three levels are used:
  READ     read and select only. Reference data the dataset reads.
  MASTER   configuration and master records the dataset creates and edits.
  DOCUMENT transactional documents, including submit and cancel, because a
           posted history is the point of the dataset.
"""

ROLE = 'ERPBridge Demo'
# Report roles are granted deliberately. Frappe checks Report roles as well as
# the reference DocType's report permission, and Prepared Report User is what
# lets a heavy report run as a queued Prepared Report. The same identity also
# holds the intrinsic All and Guest roles. Anything else is drift.
COMPANION_ROLES = {'Prepared Report User', 'Report Manager'}
ALLOWED_ROLES = {ROLE, 'All', 'Guest'} | COMPANION_ROLES

# 'report' is included at every level: the report tools check the reference
# DocType's report permission as well as the report's own role list.
READ = ('read', 'select', 'report')
MASTER = ('read', 'write', 'create', 'delete', 'select', 'report')
DOCUMENT = ('read', 'write', 'create', 'delete', 'submit', 'cancel', 'select',
            'report')

# Company, Fiscal Year and Currency stay read-only on purpose. Creating or
# editing them reshapes the accounting calendar or the ledger base, and a POC
# does not need that class of change. Everything else is a candidate for the
# CRUD surface the demo exercises.
PERMISSIONS = {
    # Company and items
    'Company': READ,
    'Item': MASTER,
    'UOM': MASTER,
    'Item Group': MASTER,
    'Item Price': MASTER,
    'Price List': MASTER,
    # Parties and contact data
    'Customer': MASTER,
    'Customer Group': MASTER,
    'Territory': MASTER,
    'Supplier': MASTER,
    'Supplier Group': MASTER,
    'Contact': MASTER,
    'Address': MASTER,
    'Salutation': READ,
    'Gender': READ,
    'Lead': MASTER,
    'Opportunity': MASTER,
    # Selling
    'Quotation': DOCUMENT,
    'Sales Order': DOCUMENT,
    'Delivery Note': DOCUMENT,
    'Sales Invoice': DOCUMENT,
    # Buying
    'Request for Quotation': DOCUMENT,
    'Supplier Quotation': DOCUMENT,
    'Purchase Order': DOCUMENT,
    'Purchase Receipt': DOCUMENT,
    'Purchase Invoice': DOCUMENT,
    # Stock
    'Warehouse': MASTER,
    'Stock Entry': DOCUMENT,
    'Bin': READ,
    'Stock Ledger Entry': READ,
    'Batch': READ,
    'Serial No': READ,
    # Accounting and tax
    'Account': MASTER,
    'Cost Center': MASTER,
    'Journal Entry': DOCUMENT,
    'Payment Entry': DOCUMENT,
    'GL Entry': READ,
    'Fiscal Year': READ,
    'Currency': READ,
    'Currency Exchange': MASTER,
    'Bank': MASTER,
    'Bank Account': MASTER,
    'Tax Category': MASTER,
    'Tax Rule': MASTER,
    'Item Tax Template': MASTER,
    'Sales Taxes and Charges Template': MASTER,
    'Purchase Taxes and Charges Template': MASTER,
    'Payment Term': MASTER,
    'Payment Terms Template': MASTER,
    'Terms and Conditions': MASTER,
    'Mode of Payment': MASTER,
    'Journal Entry Template': MASTER,
    'Tax Withholding Category': READ,
    'Finance Book': READ,
    'Accounting Period': READ,
    'Accounting Dimension': READ,
    'Budget': MASTER,
    'Monthly Distribution': READ,
    'Period Closing Voucher': DOCUMENT,
    'Exchange Rate Revaluation': DOCUMENT,
    'Bank Clearance': DOCUMENT,
    'Subscription Plan': MASTER,
    'Subscription': DOCUMENT,
    # Assets
    'Asset': DOCUMENT,
    'Asset Category': MASTER,
    'Location': MASTER,
    'Asset Movement': DOCUMENT,
    'Asset Maintenance': DOCUMENT,
    'Asset Repair': DOCUMENT,
    'Asset Depreciation Schedule': DOCUMENT,
    'Asset Value Adjustment': DOCUMENT,
    'Asset Capitalization': DOCUMENT,
    # Production
    'BOM': DOCUMENT,
    'Work Order': DOCUMENT,
    'Job Card': DOCUMENT,
    'Production Plan': DOCUMENT,
    'Subcontracting Order': DOCUMENT,
    'Subcontracting Receipt': DOCUMENT,
    'Workstation': MASTER,
    'Workstation Type': MASTER,
    'Operation': MASTER,
    'Routing': MASTER,
    'Sales Forecast': MASTER,
    'Downtime Entry': MASTER,
    'Subcontracting BOM': MASTER,
    'Master Production Schedule': MASTER,
    # Projects and people
    'Project': MASTER,
    'Task': MASTER,
    'Timesheet': MASTER,
    'Employee': MASTER,
    'Department': MASTER,
    'Designation': MASTER,
}
# ToDo reads use its existing native All-role policy. Do not add or alter
# assignment write permissions when publishing the read-only tools.
PERMISSION_FIELDS = (
    'read', 'write', 'create', 'delete', 'select', 'submit', 'cancel', 'amend',
    'report', 'export', 'import', 'share', 'print', 'email', 'if_owner',
)


def apply_demo_permissions(integration_username, *, require_existing_user=True):
    """Validate identity and role drift, then update only Role/Custom DocPerm.

    Run as Administrator on the intended site. Native insert/save validation is
    retained. The caller owns commit; on error this function rolls back its work.
    Bootstrap alone can permit a missing integration user before creating it.
    """
    import frappe  # pyright: ignore[reportMissingImports]
    from frappe.permissions import add_permission  # pyright: ignore[reportMissingImports]

    if frappe.session.user != 'Administrator':
        raise ValueError('Permission updates require Administrator')
    if not integration_username or integration_username in {'Administrator', 'Guest'}:
        raise ValueError('Invalid integration identity')
    exists = frappe.db.exists('User', integration_username)
    if not exists and require_existing_user:
        raise ValueError('Existing integration user required')
    if exists:
        user = frappe.get_doc('User', integration_username)
        roles = {row.role for row in user.roles}
        if ROLE not in roles or roles - ALLOWED_ROLES:
            raise ValueError('Integration user has unexpected roles')
        # Frappe makes users with only desk_access=0 roles Website Users.
        # Keep that native API-capable identity; do not upgrade its user type.
        if not user.enabled or user.user_type not in {'System User', 'Website User'}:
            raise ValueError('Integration user must be an enabled API-capable user')

    # Do not silently accept extra doctypes, elevated levels, or duplicate rules.
    # Inspect native rules too: custom permission creation can copy those rules.
    for table in ('DocPerm', 'Custom DocPerm'):
        seen = set()
        for row in frappe.get_all(table, filters={'role': ROLE},
                                  fields=['parent', 'permlevel']):
            key = (row.parent, row.permlevel)
            if row.parent not in PERMISSIONS or row.permlevel != 0 or key in seen:
                raise ValueError('Demo role has unexpected permission rules')
            seen.add(key)

    # Refuse unknown DocTypes before any write, so a site that lacks one
    # reports its name instead of failing part way through the apply.
    missing = sorted(doctype for doctype in PERMISSIONS
                     if not frappe.db.exists('DocType', doctype))
    if missing:
        raise ValueError('Unknown DocType in the demo permission policy: '
                         + ', '.join(missing))

    frappe.db.savepoint('erpbridge_demo_permissions')
    try:
        if not frappe.db.exists('Role', ROLE):
            frappe.get_doc({'doctype': 'Role', 'role_name': ROLE, 'desk_access': 0}).insert()
        else:
            role = frappe.get_doc('Role', ROLE)
            role.desk_access = 0
            role.save()
        for doctype, allowed in PERMISSIONS.items():
            add_permission(doctype, ROLE)
            names = frappe.get_all('Custom DocPerm', filters={
                'parent': doctype, 'role': ROLE, 'permlevel': 0}, pluck='name')
            if len(names) != 1:
                raise ValueError('Expected one native custom permission rule')
            doc = frappe.get_doc('Custom DocPerm', names[0])
            for field in PERMISSION_FIELDS:
                doc.set(field, int(field in allowed))
            doc.save()
        frappe.clear_cache()
    except Exception:
        frappe.db.rollback(save_point='erpbridge_demo_permissions')
        frappe.clear_cache()
        raise
