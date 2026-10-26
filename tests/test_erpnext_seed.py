"""Real loopback HTTP seam; no ERPNext service or third-party packages."""
import importlib.util
import json
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest

PATH = Path(__file__).resolve().parents[1] / 'demo/erpnext/seed.py'

def load():
    spec = importlib.util.spec_from_file_location('erpnext_seed', PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

class SeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load()

    def setUp(self):
        self.records: dict = {('Item Group', 'All Item Groups'): {'name': 'All Item Groups', 'is_group': 1}}
        self.writes = []
        self.auth = []
        records, writes, auth = self.records, self.writes, self.auth
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args): pass
            def handle_request(self):
                from urllib.parse import unquote, urlsplit
                auth.append(self.headers.get('Authorization'))
                parts = unquote(urlsplit(self.path).path).split('/')[3:]
                key = tuple(parts)
                status = 200
                if self.command == 'POST':
                    data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                    name = data.get('item_code') or data.get('company_name') or data.get('item_group_name') or data.get('uom_name')
                    key = (parts[0], name)
                    records[key] = dict(data, name=name)
                    writes.append(key)
                    data = records[key]
                elif self.command == 'DELETE':
                    records.pop(key, None)
                    writes.append(('DELETE',) + key)
                    data = {}
                    status = 202
                elif key not in records:
                    data, status = {}, 404
                else: data = records[key]
                body = json.dumps({'data': data}).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(body)
            do_GET = do_POST = do_DELETE = handle_request
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = self.mod.Client(f'http://127.0.0.1:{self.server.server_port}', {'api_key': 'fixture-key', 'api_secret': 'fixture-secret'}, allow_local_http=True)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_seed_twice_and_verify_exact_baseline(self):
        self.mod.seed(self.client)
        count = len(self.writes)
        self.mod.seed(self.client)
        self.assertEqual(count, len(self.writes))
        self.mod.verify(self.client)
        self.assertEqual(8, len([k for k in self.records if k[0] == 'Item']))
        self.assertEqual('Sri Lanka', self.records['Company', 'Serendib Consumer Products (Pvt) Ltd']['country'])
        self.assertTrue(all(a == 'token fixture-key:fixture-secret' for a in self.auth))

    def test_conflicting_existing_item_is_not_overwritten(self):
        self.mod.seed(self.client)
        self.records['Item', 'FG-CLEANER-5L']['stock_uom'] = 'Kg'
        with self.assertRaisesRegex(ValueError, 'stock_uom'):
            self.mod.seed(self.client)

    def test_cleanup_only_explicit_run_identifiers(self):
        for name in ['FG-CLEANER-5L', 'TP-RUN-123', 'VIVA-RUN-', 'VIVA-RUN-a/b']:
            with self.assertRaises(ValueError): self.mod.cleanup(self.client, [name], confirm=True)
        with self.assertRaises(ValueError): self.mod.cleanup(self.client, ['VIVA-RUN-abc'], confirm=False)
        self.records['Item', 'VIVA-RUN-abc'] = {'name': 'VIVA-RUN-abc'}
        self.mod.cleanup(self.client, ['VIVA-RUN-abc'], confirm=True)
        self.assertNotIn(('Item', 'VIVA-RUN-abc'), self.records)

    def test_https_required_and_redirects_disabled(self):
        with self.assertRaises(ValueError): self.mod.Client('http://example.com', {})
        with self.assertRaises(ValueError): self.mod.Client('http://example.com', {}, allow_local_http=True)
        with self.assertRaises(ValueError): self.mod.Client('https://user:password@example.com', {})

    def test_cli_seed_verify_keeps_credentials_and_exact_names(self):
        import os
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture-credentials.json'
            credentials = {'admin': {'api_key': 'fixture-key', 'api_secret': 'fixture-secret'}}
            path.write_text(json.dumps(credentials))
            os.chmod(path, 0o600)
            original = path.read_bytes()
            for command in ['seed', 'seed', 'verify']:
                result = subprocess.run(['python3', str(PATH), command,
                    '--url', self.client.url, '--allow-local-http',
                    '--credentials-file', str(path)], capture_output=True, text=True)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertNotIn('fixture-secret', result.stdout + result.stderr)
            self.assertEqual(original, path.read_bytes())
            self.assertEqual({'RM-SUGAR-001', 'RM-CLEANER-CONC-001', 'PKG-BOTTLE-001',
                              'FG-TEA-001', 'FG-CLEANER-5L', 'IMP-SPICE-001',
                              'OFF-PAPER-001', 'SPARE-BELT-001'},
                             {key[1] for key in self.records if key[0] == 'Item'})

    def test_verify_fails_for_missing_baseline(self):
        with self.assertRaises(ValueError): self.mod.verify(self.client)

if __name__ == '__main__': unittest.main()
