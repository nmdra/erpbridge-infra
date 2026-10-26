# Assignment actions: offline drafts

> **Historical POC documentation.** The recorded deployments were torn down in
> October 2026. Status, tool counts, approvals, and acceptance results below
> describe dated work, not current services or permission to deploy again.
> Check the versioned configuration and obtain fresh approval before cloud changes.

## Status

Four tools are prepared, not published. The user selected **offline preparation only**. The live catalogue remains 45 names and 46 revisions. No assignment write, permission change, installed CLI replacement, server upgrade, or new token was performed.

Reviewed source manifests: [`manifests/erpnext-assignments/`](../manifests/erpnext-assignments/).
Separate generation contract: [`config/erpnext-assignment-openapi.yaml`](../config/erpnext-assignment-openapi.yaml).
These are not part of the active OpenAPI contract or its approved-tool list. Do not pass this directory to `tool apply` without fresh approval.

## Contracts

All four use POST, version `1.0.0`, module `erpnext`, and the existing `ERP_PRIMARY_KEY` environment reference. No credentials are embedded. Write caching is disabled.

| Tool | Native Frappe method | Required inputs |
| --- | --- | --- |
| `create_assignment` | `frappe.desk.form.assign_to.add` | `doctype`, document `name`, `assign_to` array of existing user logins |
| `remove_assignment` | `frappe.desk.form.assign_to.remove` | `doctype`, document `name`, `assign_to` login |
| `complete_assignment` | `frappe.desk.form.assign_to.close` | `doctype`, document `name`, `assign_to` login |
| `reopen_assignment` | `frappe.client.set_value` | `doctype="ToDo"`, ToDo `name`, `fieldname="status"`, `value="Open"` |

Create also accepts optional `description`, `priority` (`Low`, `Medium`, `High`), and `date`. Date formatting remains subject to native validation; preserving a format hint in a draft does not establish enforcement by the installed schema model.

### Live behaviour observed 2026-10-16

The four actions are published and verified against a run-owned Item:

- `create_assignment` creates a native ToDo with status `Open`, allocated to the named login, and
  returns the native response.
- `complete_assignment` (`assign_to.close`) sets the ToDo to `Closed`. Native rules apply, so only the
  assignee can complete it.
- `reopen_assignment` (`frappe.client.set_value`) sets `ToDo.status` back to `Open`.
- `remove_assignment` (`assign_to.remove`) **cancels** the ToDo. It does not delete the record. Delete
  a run-owned ToDo through the native bench console when a clean dataset is required.

Cleanup helper for run-owned test records: `artifacts/cleanup-assignment-tests.py`. Verification
evidence: `artifacts/erpnext-phase-C1-verification.json`.

Create, remove, and complete permit only the 19 business DocTypes represented by the existing resource catalogue. They exclude ToDo itself, User, Role, and other security DocTypes. Reopen instead uses the assignment's ToDo name, not the referenced business document's name.

Every input root explicitly sets `additionalProperties: false`. There is no caller-controlled `args`, `cmd`, `ignore_permissions`, `assigned_by`, or `assignment_rule`. Reopen cannot select another DocType, field, or status. These boundaries require the fixed runtime; descriptions and annotations alone do not enforce them.

## Native behavior and limits

- Native add checks access to the referenced document. It can attempt to share that document with an assignee who lacks access. Existing native share permissions still apply; the drafts add no grants and no bypass flag.
- The shared ERP integration account remains the actor. A selected user login does not authenticate a human MCP caller.
- Native close requires `assign_to` to equal the authenticated ERP user. The shared integration account cannot complete a human user's assignment. Do not bypass this with a generic ToDo status update.
- Remove marks a matching assignment `Cancelled`; it does not delete the business document or ToDo record.
- Reopen uses native `doc.save()` permission checks and hooks. It does not perform a direct database update.
- Add, remove, and close return a `message` array of remaining open assignments, limited by the native method to five. This is not a complete assignment inventory. Use the existing document-assignment read for independent state checks.
- Reopen returns a `message` object containing the updated ToDo document. Its output schema is not a field projection. Descriptions and other native document fields can remain present.
- Native assignment and notification side effects are possible. Email, queue processing, and notification delivery were not tested. A successful write would not prove delivery.

Sources, pinned to Frappe v16.17.0:
[assignment methods](https://github.com/frappe/frappe/blob/v16.17.0/frappe/desk/form/assign_to.py),
[client.set_value](https://github.com/frappe/frappe/blob/v16.17.0/frappe/client.py),
[ToDo hooks](https://github.com/frappe/frappe/blob/v16.17.0/frappe/desk/doctype/todo/todo.py),
[ToDo permissions](https://github.com/frappe/frappe/blob/v16.17.0/frappe/desk/doctype/todo/todo.json).

## Offline checks

The installed CLI generated four drafts locally from its existing context registry and the separate contract. No ERP or bridge request was needed for generation. The installed generator dropped root closure and the date format; the reviewed manifests restore the request schema from the contract.

Installed `bridgectl tool validate` passed for all four reviewed files. **This does not prove that the old CLI or running server preserves or enforces the restored constraints.** Four stdlib contract tests check exact endpoints, schemas, parameter maps, native envelopes, credential references, disabled caching, excluded bypass fields, and the unchanged active catalogue. The full offline suite passed 105 tests.

```sh
python3 -m unittest discover -s tests -p test_erpnext_assignment_contracts.py -v
python3 -m unittest discover -s tests
```

## Before publication

Obtain fresh approval for a tested registry/token-preserving server and CLI upgrade. The source schema fix alone does not change the running image or stored tools. A tool-definition export does not preserve the ephemeral database or client tokens.

Then confirm the exact four revisions, API binding, native permissions, output privacy, affected-read cache behavior, and existing revision fingerprints. Test normal and malicious arguments through the upgraded runtime, including dispatcher, permission-bypass, and impersonation attempts. Use only unique run-owned documents and assignments, with independent REST state checks and cleanup. Preserve the eight baseline Items and all existing records. No run-owned live lifecycle test is accepted yet.

## Current tool export

The separately requested read-only export is at:

`/home/nimendra/erpbridge-demo/tools-export-20261015T200453.304303Z`

It includes every one of the 46 current revisions across 45 tool names, including the operator-owned Company revision. `index.json` contains per-file SHA-256 hashes. Files are mode `0600`, and the export directory is mode `0700`. Existing files were not overwritten, and export did not adopt ownership of any revision.

This export excludes tokens, credentials, API registrations, and the bridge database. It is not a complete recovery backup. The four offline assignment drafts are intentionally absent from this live export.
