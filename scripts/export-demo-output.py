#!/usr/bin/env python3
"""Assemble opt-in credentials without logging values or provider diagnostics.

Environment: TF_DIR, ERPNEXT_TF_DIR, TERRAFORM, DEPLOYMENT_OUTPUT_FILE,
ERPNEXT_CREDENTIALS_FILE and MCP_TOKEN_FILE override repository-local defaults.
Only Terraform output reads are performed. This command never creates tokens.
"""
import datetime
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
VM_FIELDS = ('name', 'resource_group', 'location', 'url', 'public_ip',
             'ssh_username', 'ssh_private_key_path', 'vm_size')
BRIDGE_VM_FIELDS = ('name', 'resource_group', 'location', 'url', 'public_ip',
                    'ssh_username', 'ssh_private_key_path', 'vm_size')
HOSTING_FIELDS = ('hosting', 'source_fenced', 'canonical_hostname', 'dns_record_type',
                  'dns_record_content', 'cname_check_applies')
# Staged environment settings that must match the authoritative Terraform values.
STAGED_CREDENTIAL_PAIRS = (('API_AUTH_TOKEN', 'api_auth_token'),
                           ('ERP_PRIMARY_KEY', 'erp_primary_key'))


class ExportError(Exception):
    """Safe, fixed-text operator error."""


def output(directory, name):
    try:
        result = subprocess.run([os.environ.get('TERRAFORM', 'terraform'),
                                 '-chdir=' + str(directory), 'output', '-json', name],
                                capture_output=True, text=True, check=False)
        if result.returncode:
            raise ExportError('Terraform output read failed.')
        value = json.loads(result.stdout)
        if not isinstance(value, dict):
            raise ExportError('Terraform output must be an object.')
        return value
    except (OSError, ValueError):
        raise ExportError('Terraform output read failed.') from None


def protected_json(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd) as source:
            info = os.fstat(source.fileno())
            if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_uid != os.getuid()):
                raise ExportError('Credential inputs must be owned regular files with mode 0600.')
            value = json.load(source)
        if not isinstance(value, dict):
            raise ExportError('Credential input must be a JSON object.')
        return value
    except (OSError, ValueError):
        raise ExportError('Protected credential input is missing or invalid.') from None


def pick(value, fields):
    if not isinstance(value, dict):
        raise ExportError('Output object is incomplete.')
    selected = {key: value[key] for key in fields if key in value}
    for key, item in selected.items():
        if key in ('ingress_allowed_cidrs', 'scopes', 'roles'):
            if not isinstance(item, list) or any(not isinstance(entry, str) for entry in item):
                raise ExportError('Output list contains invalid metadata.')
        elif isinstance(item, (dict, list)):
            raise ExportError('Output field contains unexpected nested metadata.')
    return selected


def required(value, fields):
    selected = pick(value, fields)
    if any(not isinstance(selected.get(key), str) or not selected[key].strip() for key in fields):
        raise ExportError('Required output or credential fields are incomplete.')
    return selected


def staged_environment(path):
    """Read a staged runtime environment file for a value comparison only.

    Returns None when the file is absent, so the export stays usable before the
    migration starts. Values are compared, never printed.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    with os.fdopen(fd) as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ExportError('The staged environment file must be a regular file.')
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise ExportError('The staged environment file must not be group or other accessible.')
        text = source.read()
    values = {}
    for line in text.splitlines():
        if line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        values[key.strip()] = value
    return values


def compare_staged_credentials(tokens, path):
    """Fail closed when a staged value differs from the authoritative one."""
    values = staged_environment(path)
    if values is None:
        return 'absent'
    for staged_key, token_key in STAGED_CREDENTIAL_PAIRS:
        if staged_key not in values:
            raise ExportError('The staged environment is missing a required credential setting.')
        if values[staged_key] != tokens.get(token_key):
            raise ExportError('A staged credential does not match the authoritative value.')
    return 'match'


def https_url(value):
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment):
            raise ValueError()
    except (TypeError, ValueError):
        raise ExportError('ERPNext URL must use public HTTPS without credentials.') from None
    return value


def build():
    tf_dir = Path(os.environ.get('TF_DIR', ROOT / 'terraform'))
    deployment = output(tf_dir, 'deployment_summary')
    tokens = required(output(tf_dir, 'demo_tokens'), ('api_auth_token', 'erp_primary_key'))
    images = pick(output(tf_dir, 'image_references'), ('erpbridge', 'mockerp'))
    domains = pick(output(tf_dir, 'cloudflare_domains'), ('enabled', 'erpbridge', 'mockerp'))
    selected = pick(deployment, ('resource_group', 'location', 'custom_domains_enabled', 'erp_target'))
    selected['erpbridge'] = pick(deployment.get('erpbridge'), ('name', 'url', 'azure_url', 'mcp_url', 'port'))
    # Hosting ownership and the DNS record shape. The Azure endpoint stops being a
    # working fallback as soon as the source is fenced.
    hosting = pick(output(tf_dir, 'erpbridge_hosting'), HOSTING_FIELDS)
    hosting_kind = hosting.get('hosting')
    if hosting_kind not in ('aca', 'vm'):
        raise ExportError('ERPBridge hosting mode is missing or unsupported.')
    selected['erpbridge'].update(hosting)
    selected['erpbridge']['azure_url_state'] = (
        'retired-fenced-diagnostic-only' if hosting.get('source_fenced') else 'active-fallback')
    selected['erpbridge']['staged_env_comparison'] = compare_staged_credentials(
        tokens, Path(os.environ.get('ERPBRIDGE_STAGED_ENV',
                                    ROOT / 'artifacts' / 'erpbridge-vm-migration' / 'runtime.env.staged')))
    selected['mockerp'] = pick(deployment.get('mockerp'), ('name', 'url', 'azure_url', 'port', 'ingress_allowed_cidrs'))
    target = deployment.get('erp_target', 'mockerp')
    if target not in ('mockerp', 'erpnext'):
        raise ExportError('Unsupported ERP target.')
    artifact = {
        'schema_version': 1,
        'generated_at': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'deployment': selected,
        'urls': {'erpbridge': selected['erpbridge'].get('url'),
                 'erpbridge_mcp': selected['erpbridge'].get('mcp_url'),
                 'erpbridge_hosting': hosting_kind,
                 'erpbridge_record_type': selected['erpbridge'].get('dns_record_type'),
                 'mockerp': selected['mockerp'].get('url'),
                 'erpbridge_azure_fallback': selected['erpbridge'].get('azure_url'),
                 'erpbridge_azure_fallback_state': selected['erpbridge'].get('azure_url_state'),
                 'mockerp_azure_fallback': selected['mockerp'].get('azure_url')},
        'image_references': images, 'custom_domains': domains, 'demo_tokens': tokens,
        'mcp_tokens': {},
    }
    destination = Path(os.environ.get('DEPLOYMENT_OUTPUT_FILE', ROOT / 'artifacts/deployment-output.json'))
    mcp_path = Path(os.environ.get('MCP_TOKEN_FILE', destination.parent / 'mcp-tokens.json'))
    if os.path.lexists(mcp_path):
        captured = protected_json(mcp_path).get('tokens', {})
        if not isinstance(captured, dict):
            raise ExportError('MCP token input is invalid.')
        for name, item in captured.items():
            token = required(item, ('token',))
            token.update(pick(item, ('id', 'name', 'scopes', 'roles', 'expires_at', 'created_at')))
            artifact['mcp_tokens'][name] = token
    if hosting_kind == 'vm':
        bridge_vm_output = output(Path(os.environ.get('ERPBRIDGE_VM_TF_DIR',
                                                      ROOT / 'terraform-erpbridge')),
                                  'deployment_summary')
        bridge_vm = required(bridge_vm_output, BRIDGE_VM_FIELDS)
        bridge_vm.update(pick(bridge_vm_output, ('resource_id', 'os_disk_name')))
        if https_url(bridge_vm['url']) != https_url(selected['erpbridge']['url']):
            raise ExportError('The VM URL differs from the canonical bridge URL.')
        record_content = selected['erpbridge'].get('dns_record_content')
        if record_content and record_content != bridge_vm['public_ip']:
            raise ExportError('The bridge DNS record does not point at the VM address.')
        artifact['urls']['erpbridge_vm'] = bridge_vm['url']
        artifact['deployment']['erpbridge_vm'] = bridge_vm
    if target == 'erpnext':
        vm_output = output(Path(os.environ.get('ERPNEXT_TF_DIR', ROOT / 'terraform-erpnext')),
                           'deployment_summary')
        vm = required(vm_output, VM_FIELDS)
        vm.update(pick(vm_output, ('resource_id',)))
        configured = required(deployment.get('erpnext'), ('url',))['url']
        if https_url(vm['url']) != https_url(configured):
            raise ExportError('ERPNext URL differs between Terraform roots.')
        credentials = protected_json(Path(os.environ.get('ERPNEXT_CREDENTIALS_FILE',
                                                         ROOT / 'artifacts/erpnext-credentials.json')))
        admin = required(credentials.get('admin'), ('username', 'password'))
        integration = required(credentials.get('integration'), ('username', 'api_key', 'api_secret'))
        if admin['username'] != 'Administrator' or integration['username'].lower() == 'administrator':
            raise ExportError('ERPNext identities must separate admin and integration access.')
        if tokens['erp_primary_key'] != 'token ' + integration['api_key'] + ':' + integration['api_secret']:
            raise ExportError('Selected ERP credential does not match saved integration credentials.')
        artifact['schema_version'] = 2
        artifact['urls']['erpnext'] = vm['url']
        artifact['deployment']['erpnext'] = vm
        artifact['erpnext_credentials'] = {'admin': admin, 'integration': integration}
    return destination, artifact


def main():
    temporary = None
    try:
        destination, artifact = build()
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(prefix='.deployment-output.', dir=destination.parent)
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(artifact, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        temporary = None
        print(destination)
        return 0
    except ExportError as error:
        print(str(error), file=sys.stderr)
    except (OSError, TypeError, ValueError):
        print('Deployment export failed; check local input files and output directory.', file=sys.stderr)
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass
    return 1


if __name__ == '__main__':
    sys.exit(main())
