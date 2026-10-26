"""Regressions found by the approved live ERPNext installation test."""
import ast
import os
from pathlib import Path
import runpy
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class RuntimeContracts(unittest.TestCase):
    def test_console_injection_supports_separate_globals_and_locals(self):
        source = (ROOT / 'demo/erpnext/setup_site.py').read_text().split('import frappe', 1)[0]
        sentinel = {'admin': {'username': 'Administrator'}}
        namespace = {'credentials': sentinel, 'permissions_source': '# reviewed permission helper'}
        exec(source, {}, namespace)
        self.assertIs(namespace['credentials'], sentinel)
        self.assertEqual(namespace['permissions_source'], '# reviewed permission helper')

    def test_new_site_matches_native_wrapper_order_and_sites_directory(self):
        calls = []
        frappe = types.ModuleType('frappe')
        installer = types.ModuleType('frappe.installer')
        setattr(frappe, 'init', lambda site, **kw: calls.append(('init', site, kw, Path.cwd().name)))
        setattr(frappe, 'destroy', lambda: calls.append(('destroy',)))

        def new_site(database, site, **kw):
            self.assertEqual(calls, [('init', 'frontend', {'new_site': True}, 'sites')])
            self.assertEqual(Path.cwd().name, 'sites')
            self.assertEqual(kw['install_apps'], ['erpnext'])
            calls.append(('new', site))

        setattr(installer, '_new_site', new_site)
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'sites').mkdir()
            original = Path.cwd()
            try:
                os.chdir(directory)
                with patch.dict(sys.modules, {'frappe': frappe, 'frappe.installer': installer}), \
                        patch.object(sys, 'path', list(sys.path)), \
                        patch.dict(os.environ, {'DB_ROOT_PASSWORD': 'fixture-root', 'ADMIN_PASSWORD': 'fixture-admin'}):
                    runpy.run_path(str(ROOT / 'demo/erpnext/create_site.py'))
            finally:
                os.chdir(original)
        self.assertEqual(calls[-2:], [('new', 'frontend'), ('destroy',)])

    def test_locale_is_set_before_native_fixture_install(self):
        tree = ast.parse((ROOT / 'demo/erpnext/setup_site.py').read_text())
        locale = next(i for i, node in enumerate(tree.body) if isinstance(node, ast.Assign)
                      and any(ast.unparse(t) == 'frappe.local.lang' for t in node.targets))
        install = next(i for i, node in enumerate(tree.body) if isinstance(node, ast.Expr)
                       and isinstance(node.value, ast.Call)
                       and ast.unparse(node.value.func) == 'stage_fixtures')
        self.assertLess(locale, install)


if __name__ == '__main__':
    unittest.main()
