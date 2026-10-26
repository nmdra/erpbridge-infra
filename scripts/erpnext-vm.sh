#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export ERPNEXT_TF_DIR="${ERPNEXT_TF_DIR:-$ROOT_DIR/terraform-erpnext}"
# Local executable seams: TERRAFORM, AZ, CURL. No subscription/VM override flags.
# Usage: erpnext-vm.sh status|ready [--timeout SECONDS] [--poll-interval SECONDS]
#        erpnext-vm.sh start|deallocate --confirm [--timeout SECONDS] [--poll-interval SECONDS]
#        erpnext-vm.sh cost --hourly-rate USD --running-hours HOURS
exec python3 - "$@" <<'PY'
import argparse
from decimal import Decimal, InvalidOperation
import json
import math
import os
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit


class SafeError(Exception):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # Do not echo arbitrary CLI parameters, which can contain secret values.
        self.print_usage(sys.stderr)
        self.exit(2, 'Invalid arguments; use --help.\n')


def run(executable, arguments, timeout=60):
    try:
        result = subprocess.run([os.environ.get(executable.upper(), executable)] + arguments,
                                capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise SafeError('Local command failed or timed out.') from None
    if result.returncode:
        raise SafeError('Local command failed; provider diagnostics suppressed.')
    return result.stdout.strip()


def vm_summary():
    try:
        vm = json.loads(run('terraform', ['-chdir=' + os.environ['ERPNEXT_TF_DIR'],
                                          'output', '-json', 'deployment_summary']))
        for key in ('name', 'resource_group', 'resource_id', 'url'):
            if not isinstance(vm[key], str) or not vm[key].strip():
                raise ValueError()
        if vm['name'].startswith('-') or vm['resource_group'].startswith('-'):
            raise ValueError()
        resource = re.fullmatch(
            r'/subscriptions/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})'
            r'/resourceGroups/([^/\s?#]+)/providers/Microsoft\.Compute/virtualMachines/([^/\s?#]+)',
            vm['resource_id'], re.IGNORECASE)
        if (not resource or resource[2].casefold() != vm['resource_group'].casefold()
                or resource[3].casefold() != vm['name'].casefold()):
            raise ValueError()
        parsed = urlsplit(vm['url'])
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in ('', '/')):
            raise ValueError()
        return vm
    except (ValueError, TypeError, KeyError):
        raise SafeError('ERPNext Terraform summary is incomplete, VM resource ID is inconsistent, or URL is not HTTPS.') from None


def az_vm(vm, action, extra=None, timeout=60):
    return run('az', ['vm', action, '--ids', vm['resource_id']]
               + (extra or []) + ['--only-show-errors'], timeout=timeout)


def power(vm, timeout=60):
    state = az_vm(vm, 'get-instance-view', ['--query',
        "instanceView.statuses[?starts_with(code, 'PowerState/')].code | [0]", '--output', 'tsv'],
        timeout=timeout)
    allowed = {'PowerState/' + state for state in ('running', 'starting', 'stopping', 'stopped',
                                                   'deallocating', 'deallocated', 'unknown')}
    if state not in allowed:
        raise SafeError('Azure returned no recognized VM power state.')
    return state


def ping(vm):
    try:
        body = run('curl', ['--fail', '--silent', '--show-error', '--max-time', '10',
                            vm['url'].rstrip('/') + '/api/method/ping'])
        return json.loads(body).get('message') == 'pong'
    except (SafeError, ValueError, AttributeError):
        return False


def nonnegative(value):
    try:
        number = Decimal(value)
        if not number.is_finite() or number < 0:
            raise InvalidOperation()
        return number
    except InvalidOperation:
        raise SafeError('Rate and hours must be finite non-negative decimal numbers.') from None


def main():
    parser = Parser(description='Operate only the ERPNext VM named by its Terraform root.')
    parser.add_argument('action', choices=('status', 'start', 'ready', 'deallocate', 'cost'))
    parser.add_argument('--confirm', action='store_true', help='Authorize the requested VM state change')
    parser.add_argument('--hourly-rate', help='Supplied indicative USD compute rate')
    parser.add_argument('--running-hours', help='Supplied running hours, not current usage')
    parser.add_argument('--timeout', type=float, default=300)
    parser.add_argument('--poll-interval', type=float, default=5)
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout < 0 or not math.isfinite(args.poll_interval) or args.poll_interval <= 0:
        raise SafeError('Timeout must be non-negative and poll interval must be positive.')
    if args.action == 'cost':
        if args.hourly_rate is None or args.running_hours is None:
            raise SafeError('Cost requires --hourly-rate and --running-hours.')
        total = nonnegative(args.hourly_rate) * nonnegative(args.running_hours)
        print(f'Indicative VM compute only: USD {total:.4f}; not a full bill or current billing data.')
        print('Excludes disks, public IP, networking, ACA and logging. Remaining credit is unknown.')
        return 0
    if args.action in ('start', 'deallocate') and not args.confirm:
        raise SafeError('VM state changes require --confirm; no action was taken.')
    vm = vm_summary()
    if args.action in ('start', 'deallocate'):
        deadline = time.monotonic() + args.timeout
        # A short minimum permits one observation even with --timeout 0.
        def command_budget():
            return max(0.1, min(60, deadline - time.monotonic()))

        target = 'PowerState/running' if args.action == 'start' else 'PowerState/deallocated'
        try:
            az_vm(vm, args.action, ['--no-wait', '--output', 'none'], timeout=command_budget())
            while True:
                state = power(vm, timeout=command_budget())
                if state == target:
                    if args.action == 'start':
                        print('VM is running. Run ready to check ERPNext HTTPS readiness.')
                    else:
                        print('Azure confirms VM deallocated. Disk, IP and retained ACA costs can continue.')
                    return 0
                if time.monotonic() >= deadline:
                    break
                time.sleep(min(args.poll_interval, max(0, deadline - time.monotonic())))
        except SafeError:
            raise SafeError('VM state change completion unconfirmed. Run status to check the current state.') from None
        raise SafeError('VM state change completion unconfirmed: deadline reached. Run status to check the current state.')
    deadline = time.monotonic() + args.timeout
    while True:
        state = power(vm)
        ready = state == 'PowerState/running' and ping(vm)
        if args.action == 'status' or ready or time.monotonic() >= deadline:
            print(f'VM power: {state}; HTTPS readiness: {"ready" if ready else "not ready"}.')
            return 0 if args.action == 'status' or ready else 1
        time.sleep(min(args.poll_interval, max(0, deadline - time.monotonic())))


try:
    sys.exit(main())
except SafeError as error:
    print(str(error), file=sys.stderr)
    sys.exit(1)
except (OSError, ValueError, ArithmeticError):
    print('VM command failed; check local configuration.', file=sys.stderr)
    sys.exit(1)
PY
