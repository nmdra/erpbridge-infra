"""Offline contracts for the native metadata-only permission callable."""
from pathlib import Path
import runpy
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
HELPER = runpy.run_path(str(ROOT / 'demo/erpnext/permissions.py'))
ROLE = HELPER['ROLE']


class Document(types.SimpleNamespace):
    def __init__(self, events, **values):
        super().__init__(**values)
        self.events = events

    def set(self, field, value):
        setattr(self, field, value)

    def save(self):
        self.events.append(('save', self.doctype))

    def insert(self):
        self.events.append(('insert', self.doctype))


class PermissionContracts(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.user = Document(self.events, doctype='User', enabled=1,
                             user_type='System User', roles=[types.SimpleNamespace(role=ROLE)])
        self.role = Document(self.events, doctype='Role', desk_access=1)
        self.docs = {name: Document(self.events, doctype='Custom DocPerm',
                                   **{field: 1 for field in HELPER['PERMISSION_FIELDS']})
                     for name in HELPER['PERMISSIONS']}
        self.rows = {'DocPerm': [], 'Custom DocPerm': []}
        self.missing_user = False
        self.missing_role = False
        self.fail_save = False
        self.missing_doctypes = set()
        self.frappe = types.SimpleNamespace()
        self.frappe.session = types.SimpleNamespace(user='Administrator')
        self.frappe.db = types.SimpleNamespace(
            exists=self.db_exists,
            savepoint=lambda name: self.events.append(('savepoint', name)),
            rollback=lambda **kw: self.events.append(('rollback', kw)))
        self.frappe.get_doc = self.get_doc
        self.frappe.get_all = self.get_all
        self.frappe.clear_cache = lambda: self.events.append(('cache',))
        self.native = types.SimpleNamespace()
        self.native.add_permission = lambda doctype, role: self.events.append(('add', doctype, role))
        self.modules = patch.dict(sys.modules, {'frappe': self.frappe, 'frappe.permissions': self.native})
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def db_exists(self, table, name):
        if table == 'DocType':
            return name not in self.missing_doctypes
        if table == 'User':
            return not self.missing_user
        return not self.missing_role

    def get_doc(self, table, name=None):
        if isinstance(table, dict):
            return Document(self.events, **table)
        if table == 'User':
            return self.user
        if table == 'Role':
            return self.role
        if self.fail_save:
            raise ValueError('native validation failed')
        return self.docs[name]

    def get_all(self, table, filters, fields=None, pluck=None):
        if pluck:
            return [filters['parent']]
        return [types.SimpleNamespace(**row) for row in self.rows[table]]

    def apply(self, **kw):
        HELPER['apply_demo_permissions']('integration@example.invalid', **kw)

    # The permission matrix is pinned here by group, so a new doctype or a
    # changed level fails this test until it is reviewed deliberately.
    READ_ONLY = {
        'Accounting Dimension', 'Accounting Period', 'Batch', 'Bin', 'Company',
        'Currency', 'Finance Book', 'Fiscal Year', 'GL Entry', 'Gender',
        'Monthly Distribution', 'Salutation', 'Serial No',
        'Stock Ledger Entry', 'Tax Withholding Category',
    }
    MASTER = {
        'Account', 'Address', 'Asset Category', 'Bank', 'Bank Account',
        'Budget', 'Contact', 'Cost Center', 'Currency Exchange', 'Customer',
        'Customer Group', 'Department', 'Designation', 'Downtime Entry',
        'Employee', 'Item', 'Item Group', 'Item Price', 'Item Tax Template',
        'Journal Entry Template', 'Lead', 'Location',
        'Master Production Schedule', 'Mode of Payment', 'Operation',
        'Opportunity', 'Payment Term', 'Payment Terms Template', 'Price List',
        'Project', 'Purchase Taxes and Charges Template', 'Routing',
        'Sales Forecast', 'Sales Taxes and Charges Template',
        'Subcontracting BOM', 'Subscription Plan', 'Supplier',
        'Supplier Group', 'Task', 'Tax Category', 'Tax Rule',
        'Terms and Conditions', 'Territory', 'Timesheet', 'UOM', 'Warehouse',
        'Workstation', 'Workstation Type',
    }
    DOCUMENT = {
        'Asset', 'Asset Capitalization', 'Asset Depreciation Schedule',
        'Asset Maintenance', 'Asset Movement', 'Asset Repair',
        'Asset Value Adjustment', 'BOM', 'Bank Clearance', 'Delivery Note',
        'Exchange Rate Revaluation', 'Job Card', 'Journal Entry',
        'Payment Entry', 'Period Closing Voucher', 'Production Plan',
        'Purchase Invoice', 'Purchase Order', 'Purchase Receipt', 'Quotation',
        'Request for Quotation', 'Sales Invoice', 'Sales Order', 'Stock Entry',
        'Subcontracting Order', 'Subcontracting Receipt', 'Subscription',
        'Supplier Quotation', 'Work Order',
    }

    def test_exact_permissions_and_metadata_only_writes(self):
        self.apply()
        groups = {'READ': self.READ_ONLY, 'MASTER': self.MASTER,
                  'DOCUMENT': self.DOCUMENT}
        expected = set().union(*groups.values())
        self.assertEqual(set(self.docs), expected)
        for group, names in groups.items():
            allowed = set(HELPER[group])
            for name in names:
                doc = self.docs[name]
                for field in HELPER['PERMISSION_FIELDS']:
                    self.assertEqual(getattr(doc, field), int(field in allowed),
                                     (name, field))
        self.assertEqual(self.role.desk_access, 0)
        self.assertTrue(all(event[1] in {'Role', 'Custom DocPerm'}
                            for event in self.events if event[0] in {'insert', 'save'}))
        self.assertFalse(any(event[0] == 'rollback' for event in self.events))
        self.apply()  # Idempotent policy, no users or business documents are saved.

    def test_unknown_doctype_fails_before_writes(self):
        self.missing_doctypes = {'Workstation Type'}
        with self.assertRaisesRegex(ValueError, 'Unknown DocType.*Workstation Type'):
            self.apply()
        self.assertEqual(self.events, [])

    def test_companion_report_roles_are_accepted(self):
        self.user.roles = [types.SimpleNamespace(role=name) for name in
                           (ROLE, 'Prepared Report User', 'Report Manager',
                            'All')]
        self.apply()
        self.assertTrue(any(event[0] == 'save' for event in self.events))
        self.assertFalse(any(event[0] == 'rollback' for event in self.events))

    def test_privilege_drift_fails_before_writes(self):
        for roles in ([ROLE, 'System Manager'], ['System Manager'], []):
            with self.subTest(roles=roles):
                self.user.roles = [types.SimpleNamespace(role=role) for role in roles]
                with self.assertRaisesRegex(ValueError, 'unexpected roles'):
                    self.apply()
                self.assertEqual(self.events, [])

    def test_existing_enabled_api_user_required(self):
        self.missing_user = True
        with self.assertRaisesRegex(ValueError, 'Existing integration user'):
            self.apply()
        self.assertEqual(self.events, [])
        self.apply(require_existing_user=False)
        self.assertFalse(any(event == ('insert', 'User') for event in self.events))
        self.missing_user = False
        for field, value in [('enabled', 0), ('user_type', 'Unexpected User Type')]:
            with self.subTest(field=field):
                original = getattr(self.user, field)
                setattr(self.user, field, value)
                with self.assertRaisesRegex(ValueError, 'enabled API-capable user'):
                    self.apply()
                setattr(self.user, field, original)

    def test_native_website_user_keeps_type_and_receives_only_metadata_updates(self):
        self.user.user_type = 'Website User'
        self.apply()
        self.assertEqual(self.user.user_type, 'Website User')
        self.assertNotIn(('save', 'User'), self.events)

    def test_unexpected_rules_fail_before_writes(self):
        for table in self.rows:
            for rows in ([{'parent': 'User', 'permlevel': 0}],
                         [{'parent': 'Item', 'permlevel': 1}],
                         [{'parent': 'Item', 'permlevel': 0}] * 2):
                with self.subTest(table=table, rows=rows):
                    self.rows[table] = rows
                    with self.assertRaisesRegex(ValueError, 'unexpected permission rules'):
                        self.apply()
                    self.assertEqual(self.events, [])
            self.rows[table] = []

    def test_native_failure_rolls_back_and_propagates(self):
        self.fail_save = True
        with self.assertRaisesRegex(ValueError, 'native validation failed'):
            self.apply()
        self.assertIn(('rollback', {'save_point': 'erpbridge_demo_permissions'}), self.events)

    def test_duplicate_custom_rule_after_native_copy_is_rejected(self):
        original = self.frappe.get_all
        self.frappe.get_all = lambda table, **kw: ['one', 'two'] if kw.get('pluck') else original(table, **kw)
        with self.assertRaisesRegex(ValueError, 'Expected one native'):
            self.apply()
        self.assertTrue(any(event[0] == 'rollback' for event in self.events))

    def test_only_administrator_can_apply_and_special_users_are_rejected(self):
        self.frappe.session.user = 'integration@example.invalid'
        with self.assertRaisesRegex(ValueError, 'require Administrator'):
            self.apply()
        self.frappe.session.user = 'Administrator'
        for identity in ('Administrator', 'Guest', ''):
            with self.assertRaisesRegex(ValueError, 'Invalid integration identity'):
                HELPER['apply_demo_permissions'](identity)
        self.assertEqual(self.events, [])

    def test_missing_role_is_created_without_desk_access(self):
        self.missing_role = True
        self.apply()
        self.assertIn(('insert', 'Role'), self.events)


if __name__ == '__main__':
    unittest.main()
