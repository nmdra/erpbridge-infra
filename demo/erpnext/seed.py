#!/usr/bin/env python3
"""Native SCP masters only. No stock transactions, bulk deletion, or password resets."""
import argparse
import json
from pathlib import Path
import re
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request

FIXTURES = Path(__file__).with_name('fixtures') / 'scp-masters.json'

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward an API credential to another origin.

class Client:
    def __init__(self, url, credentials, allow_local_http=False):
        parsed = urllib.parse.urlsplit(url)
        local = parsed.hostname in ('127.0.0.1', 'localhost', '::1')
        if (parsed.scheme != 'https' and not (allow_local_http and local and parsed.scheme == 'http')) or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
            raise ValueError('Use an HTTPS origin; test HTTP is limited to loopback')
        self.url = url.rstrip('/')
        self.credentials = credentials
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, method, doctype, name=None, data=None):
        path = '/api/resource/' + urllib.parse.quote(doctype, safe='')
        if name is not None: path += '/' + urllib.parse.quote(name, safe='')
        key, secret = self.credentials['api_key'], self.credentials['api_secret']
        req = urllib.request.Request(self.url + path, method=method,
            data=json.dumps(data).encode() if data is not None else None,
            headers={'Authorization': f'token {key}:{secret}', 'Content-Type': 'application/json'})
        try:
            with self.opener.open(req, timeout=45) as response:
                return json.load(response)['data']
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            if code == 404 and method == 'GET': return None
            # Do not print response bodies, headers, or credentials.
            raise ValueError(f'ERPNext {method} {doctype} failed (HTTP {code})') from None
        except (urllib.error.URLError, TimeoutError):
            raise ValueError('ERPNext connection failed') from None

    def get(self, doctype, name): return self.request('GET', doctype, name)


def dataset():
    return json.loads(FIXTURES.read_text())


def expected(actual, fields, label):
    if actual is None: raise ValueError(f'Missing {label}')
    for field, value in fields.items():
        if actual.get(field) != value: raise ValueError(f'{label}: conflicting {field}')


def ensure(client, doctype, name, fields):
    actual = client.get(doctype, name)
    if actual is None: actual = client.request('POST', doctype, data=dict(fields, doctype=doctype))
    expected(actual, fields, f'{doctype} {name}')


def seed(client):
    data = dataset()
    root = client.get('Item Group', 'All Item Groups')
    expected(root, {'is_group': 1}, 'native Item Group root')
    for uom in data['uoms']: ensure(client, 'UOM', uom['uom_name'], uom)
    for name in data['item_groups']:
        ensure(client, 'Item Group', name, {'item_group_name': name, 'parent_item_group': 'All Item Groups', 'is_group': 0})
    ensure(client, 'Company', data['company']['company_name'], data['company'])
    for item in data['items']: ensure(client, 'Item', item['item_code'], item)
    verify(client)


def verify(client):
    data = dataset()
    expected(client.get('Company', data['company']['company_name']), data['company'], 'SCP company')
    for item in data['items']: expected(client.get('Item', item['item_code']), item, item['item_code'])


def cleanup(client, names, confirm=False):
    if not confirm or not names: raise ValueError('Cleanup requires --confirm and explicit --item identifiers from this run')
    if any(not re.fullmatch(r'VIVA-RUN-[A-Za-z0-9][A-Za-z0-9_-]{0,110}', name) for name in names):
        raise ValueError('Cleanup permits exact VIVA-RUN-* identifiers only')
    for name in names:
        if client.get('Item', name) is not None: client.request('DELETE', 'Item', name)
        if client.get('Item', name) is not None: raise ValueError('Cleanup verification failed')


def protected_json(path):
    path = Path(path)
    if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise ValueError('Credential file must be a regular private mode-0600 file')
    if not path.is_file(): raise ValueError('Credential file must be regular')
    return json.loads(path.read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['seed', 'verify', 'cleanup'])
    parser.add_argument('--url', required=True)
    parser.add_argument('--credentials-file', default='artifacts/erpnext-credentials.json')
    parser.add_argument('--credential-role', choices=['admin', 'integration'], default='admin')
    parser.add_argument('--allow-local-http', action='store_true')
    parser.add_argument('--item', action='append', default=[])
    parser.add_argument('--confirm', action='store_true')
    args = parser.parse_args()
    try:
        if args.command == 'seed' and args.credential_role != 'admin': raise ValueError('Master creation requires separate admin bootstrap credentials')
        client = Client(args.url, protected_json(args.credentials_file)[args.credential_role], args.allow_local_http)
        if args.command == 'seed': seed(client)
        elif args.command == 'verify': verify(client)
        else: cleanup(client, args.item, args.confirm)
    except (ValueError, OSError, KeyError):
        print('ERPNext operation failed. Check protected credentials, connectivity, and expected master fields.', file=sys.stderr)
        return 1
    print(f'ERPNext {args.command} complete')
    return 0

if __name__ == '__main__': sys.exit(main())
