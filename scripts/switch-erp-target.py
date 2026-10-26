#!/usr/bin/env python3
"""Fail-closed cutover gate. Never updates Terraform or deletes arbitrary tools.

The installed CLI advertises tool delete NAME VERSION, but live version and
soft-delete/discovery semantics have not been accepted against this deployment.
Keep demo calls stopped and obtain a reviewed exact-version cutover procedure.
"""
import argparse
import sys


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--target', required=True, choices=('erpnext', 'mockerp'))
    p.add_argument('--confirm', action='store_true', help='Explicit consent to bounded cutover, not permission for broad deletion')
    args = p.parse_args()
    if not args.confirm:
        p.error('Cutover requires --confirm. No changes made.')
    print('Cutover blocked: live registry version and disable/delete behavior have not been verified. No tools or Terraform inputs changed. Review exact previous-target manifest names and versions from artifacts/erp-target-manifests.json; preserve unrelated APIs.', file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
