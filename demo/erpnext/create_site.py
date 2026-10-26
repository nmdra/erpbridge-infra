"""Run inside the pinned Frappe container. Secrets never enter CLI arguments."""
import os
from pathlib import Path
import sys

# The interpreter is invoked with this script outside the bench directory.
sys.path.insert(0, str(Path.cwd() / 'apps/frappe'))
import frappe  # pyright: ignore[reportMissingImports]
from frappe.installer import _new_site  # pyright: ignore[reportMissingImports]

if Path('sites/frontend/site_config.json').exists():
    # Authentication is checked separately. Never reset an existing site.
    sys.exit(0)
if Path('sites/frontend').exists():
    raise RuntimeError('Partial site exists; deliberate recovery required')
# Frappe's site command APIs run with the sites directory as their cwd.
# _new_site calls frappe.init itself, so sites_path alone is not sufficient.
os.chdir('sites')
# Match the pinned bench new-site wrapper before the installer initializes again.
frappe.init('frontend', new_site=True)
_new_site(None, 'frontend', db_root_username='root',
          db_root_password=os.environ['DB_ROOT_PASSWORD'],
          admin_password=os.environ['ADMIN_PASSWORD'], install_apps=['erpnext'],
          db_type='mariadb', db_host='db', db_port=3306,
          mariadb_user_host_login_scope='%')
frappe.destroy()
