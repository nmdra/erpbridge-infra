"""Public DNS resolver fallback without network access or provider credentials."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('jq'), 'jq is required')
class DNSWaitTests(unittest.TestCase):
    def run_wait(self, mismatch=False):
        with tempfile.TemporaryDirectory() as directory:
            stub = Path(directory) / 'curl'
            stub.write_text('''#!/usr/bin/env python3
import json, os, sys
if 'https://cloudflare-dns.com/dns-query' in sys.argv:
    print(json.dumps({'Status': 3, 'Answer': []}))
else:
    kind = 'CNAME' if 'type=CNAME' in sys.argv else 'TXT'
    value = os.environ['DNS_TARGET'] + '.' if kind == 'CNAME' else '"' + os.environ['DNS_VERIFY_ID'] + '"'
    if os.environ.get('FIXTURE_MISMATCH'): value = 'wrong.example.'
    print(json.dumps({'Status': 0, 'Answer': [{'data': value}]}))
''')
            stub.chmod(0o755)
            environment = dict(os.environ, PATH=directory + os.pathsep + os.environ.get('PATH', ''),
                               DNS_NAME='fixture.example', DNS_TARGET='fixture.azure.example',
                               DNS_VERIFY_ID='public-verification-id', DNS_WAIT_TIMEOUT_SECONDS='0',
                               DNS_POLL_SECONDS='0')
            if mismatch:
                environment['FIXTURE_MISMATCH'] = '1'
            return subprocess.run(['bash', str(ROOT / 'scripts/wait-for-custom-domain-dns.sh')],
                                  env=environment, capture_output=True, text=True, timeout=5)

    def test_cached_nxdomain_uses_second_resolver(self):
        result = self.run_wait()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('https://dns.google/resolve', result.stdout)

    def test_mismatch_still_fails_closed(self):
        result = self.run_wait(mismatch=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('not visible', result.stderr)


if __name__ == '__main__':
    unittest.main()
