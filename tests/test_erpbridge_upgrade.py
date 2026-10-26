"""Guard the opt-in restore gate used for ephemeral SQLite upgrades."""
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ERPBridgeUpgradeTests(unittest.TestCase):
    def test_restore_gate_is_explicit_and_does_not_embed_database_or_credentials(self):
        script = ROOT / 'demo/erpbridge/await-database-restore.sh'
        self.assertTrue(script.is_file())
        subprocess.run(['sh', '-n', str(script)], check=True, capture_output=True)
        text = script.read_text()
        self.assertIn('while [ ! -s "$DB" ]', text)
        self.assertIn('exec /app/erpbridge-server', text)
        self.assertNotIn('ERPBRIDGE_RESTORE_SEED', text)
        self.assertNotIn('base64', text)

    def test_terraform_restore_gate_is_opt_in(self):
        variables = (ROOT / 'terraform/variables.tf').read_text()
        main = (ROOT / 'terraform/main.tf').read_text()
        self.assertIn('variable "erpbridge_require_database_restore"', variables)
        block = variables.split('variable "erpbridge_require_database_restore"', 1)[1].split('\n}', 1)[0]
        self.assertIn('default     = false', block)
        self.assertIn('command = var.erpbridge_require_database_restore ?', main)
        self.assertIn('args    = var.erpbridge_require_database_restore ?', main)
        self.assertIn('demo/erpbridge/await-database-restore.sh', main)
        self.assertIn('dynamic "startup_probe"', main)
        self.assertIn('failure_count_threshold = 240', main)


if __name__ == '__main__':
    unittest.main()
