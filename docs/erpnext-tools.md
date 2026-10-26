# Active ERPNext MCP tools

> **Historical POC documentation.** The recorded deployments were torn down in
> October 2026. Status, tool counts, approvals, and acceptance results below
> describe dated work, not current services or permission to deploy again.
> Check the versioned configuration and obtain fresh approval before cloud changes.

The deployment exposes **141 unique tool names** and 141 stored revisions. Additions were
generated, validated, and applied through the installed `bridgectl` in context
`erpbridge-demo`. The existing MCP client token and API registration ending in
`/api` were preserved. No infrastructure was changed and no business record was
created, except one run-owned Item that the verification created and deleted.

## Catalogue

| Tools | ERPNext resource |
| --- | --- |
| `list_companies`, `get_company` | Company |
| `list_items`, `get_item`, `create_item`, `delete_item` | Item |
| `list_item_groups` | Item Group |
| `list_uoms` | UOM |
| `list_customers`, `get_customer` | Customer |
| `list_suppliers`, `get_supplier` | Supplier |
| `list_sales_orders`, `get_sales_order` | Sales Order |
| `list_purchase_orders`, `get_purchase_order` | Purchase Order |
| `list_sales_invoices`, `get_sales_invoice` | Sales Invoice |
| `list_purchase_invoices`, `get_purchase_invoice` | Purchase Invoice |
| `list_payment_entries`, `get_payment_entry` | Payment Entry |
| `list_journal_entries`, `get_journal_entry` | Journal Entry |
| `list_assets`, `get_asset` | Asset |
| `list_asset_categories`, `get_asset_category` | Asset Category |
| `list_locations`, `get_location` | Location |
| `list_asset_movements`, `get_asset_movement` | Asset Movement |
| `list_asset_maintenance`, `get_asset_maintenance` | Asset Maintenance |
| `list_asset_repairs`, `get_asset_repair` | Asset Repair |
| `list_asset_depreciation_schedules`, `get_asset_depreciation_schedule` | Asset Depreciation Schedule |
| `list_warehouses`, `get_warehouse` | Warehouse |
| `list_bins`, `get_bin` | Bin (stock on hand) |
| `list_stock_ledger_entries`, `get_stock_ledger_entry` | Stock Ledger Entry |
| `list_stock_entries`, `get_stock_entry` | Stock Entry |
| `list_item_prices`, `get_item_price` | Item Price |
| `list_batches`, `get_batch` | Batch |
| `list_serial_nos`, `get_serial_no` | Serial No |
| `list_quotations`, `get_quotation` | Quotation |
| `list_delivery_notes`, `get_delivery_note` | Delivery Note |
| `list_customer_groups`, `get_customer_group` | Customer Group |
| `list_territories`, `get_territory` | Territory |
| `list_price_lists`, `get_price_list` | Price List |
| `list_modes_of_payment`, `get_mode_of_payment` | Mode of Payment |
| `list_sales_taxes_and_charges_templates`, `get_sales_taxes_and_charges_template` | Sales Taxes and Charges Template |
| `list_terms_and_conditions`, `get_terms_and_conditions` | Terms and Conditions |
| `list_purchase_receipts`, `get_purchase_receipt` | Purchase Receipt |
| `list_supplier_groups`, `get_supplier_group` | Supplier Group |
| `list_request_for_quotations`, `get_request_for_quotation` | Request for Quotation |
| `list_supplier_quotations`, `get_supplier_quotation` | Supplier Quotation |
| `list_accounts`, `get_account` | Account |
| `list_cost_centers`, `get_cost_center` | Cost Center |
| `list_gl_entries`, `get_gl_entry` | GL Entry |
| `list_fiscal_years`, `get_fiscal_year` | Fiscal Year |
| `list_bank_accounts`, `get_bank_account` | Bank Account |
| `list_currencies`, `get_currency` | Currency |
| `list_asset_value_adjustments`, `get_asset_value_adjustment` | Asset Value Adjustment |
| `list_asset_capitalizations`, `get_asset_capitalization` | Asset Capitalization |
| `list_leads`, `get_lead` | Lead |
| `list_opportunities`, `get_opportunity` | Opportunity |
| `list_projects`, `get_project` | Project |
| `list_tasks`, `get_task` | Task |
| `list_timesheets`, `get_timesheet` | Timesheet |
| `list_boms`, `get_bom` | BOM |
| `list_work_orders`, `get_work_order` | Work Order |
| `list_employees`, `get_employee` | Employee |
| `list_departments`, `get_department` | Department |
| `list_designations`, `get_designation` | Designation |
| `list_contacts`, `get_contact` | Contact |
| `list_addresses`, `get_address` | Address |
| `list_salutations` | Salutation |
| `list_genders` | Gender |
| `list_payment_terms_templates` | Payment Terms Template |
| `count_records` | `frappe.client.get_count` over the granted DocTypes |
| `search_records` | `frappe.client.get_list` over the granted DocTypes |
| `update_item` | Item master update (`PUT`) |
| `get_item_group`, `get_uom`, `get_salutation`, `get_gender`, `get_payment_terms_template` | Named reads for already-granted reference DocTypes |
| `get_value` | `frappe.client.get_value` over the granted DocTypes |
| `search_link` | `frappe.desk.search.search_link` over the granted DocTypes |
| `run_report` | 13 native reports, company fixed, closed filter object |
| `create_assignment`, `remove_assignment`, `complete_assignment`, `reopen_assignment` | Native assignment actions on the reviewed business DocTypes |
| `list_assignments`, `get_assignment` | ToDo |
| `list_document_assignments` | ToDo, document-filtered read alias |
| `list_assignable_users`, `search_assignable_users` | Native mention directory |
| `get_user_details` | Native minimal user profile lookup |
| `get_current_user` | Authenticated ERP integration login |

There are 43 GET tools and the two original Item create/delete tools. No new
transaction-write, assignment-write, asset-write, financial-report, submission,
cancellation, disposal, or accounting-posting tool was published.

Resource list tools accept `fields` as a JSON-encoded array string. Verification uses
`limit_page_length=1000` and `limit_start=0`, not an exhaustive inventory.
Named gets require the exact document `name`. Assignment reads use native ToDo
access rules, not user/role administration.

### User and document lookup limits

- `list_assignable_users` takes `search_term=""`. The native directory includes
  enabled System Users who allow mentions. It excludes Administrator, Guest,
  Website Users, and users who disallow mentions. It is not a full directory.
- Directory results can include groups with `is_group=true`. Never select these
  as users. User rows contain `id`, display name `value`, and `link`.
- `search_assignable_users` searches display names, not email. For email matching,
  list candidates and compare `id`, or look up an exact login with profile lookup.
- `get_user_details` takes `users` as a JSON-encoded array of exact login IDs.
  It returns only `name`, `email`, `fullname`, `image`, and `time_zone`, keyed by
  login. Missing users are omitted. This does not report enabled status or roles.
- `get_current_user` returns the shared ERP integration login in `message`.
  This is not the human MCP caller. No human-identity mapping was added.
- `list_document_assignments` takes `filters` as a JSON object string containing
  `reference_type` and `reference_name`. Its required `fields` value is exactly
  `["name","reference_type","reference_name","allocated_to","assigned_by","status"]`.
  Native ToDo permissions apply; a candidate match does not prove document access.
- These five tools disable bridge response caching. Native Frappe caches may
  still apply. No User-table permissions, account changes, or roles were granted.
- Frappe's legacy `cmd` argument can override URL routing. The new five manifests
  declare `cmd` as an optional string with the single allowed value `""`.
  Leave it unset. A live nonempty override was rejected at input validation.
  This uses supported string enums, not the deferred root-closure fix. The
  older forty tools were not changed or hardened in this batch.

### Batch A and B expansion (2026-10-15)

`count_records` and `search_records` are generic tools. Each declares the granted DocTypes as a
schema enum, so the server rejects every other DocType at input validation. They need no new ERP
permission.

Every other read tool above has one `read` and `select` grant on the `ERPBridge Demo` role. The
grants are additive. `update_item` uses the `write` permission that the Item DocType already had.

Three candidates from the survey were not added:

- `Leave Type` does not exist on this site (the leave DocTypes belong to the HRMS app, which is not
  installed).
- `Asset Finance Book` is a child table of `Asset`. Child tables are read through the parent.
- `Payment Reconciliation` is a Single DocType. It has no list resource.

The ERPNext side holds records for the new reads where the stock and accounting stories need them:
`Bin` 19, `Item Price` 20, `Warehouse` 15, `Stock Ledger Entry` 16, `Account` 286, `GL Entry` 42,
`Cost Center` 6, `Currency` 149, `Department` 40, `Designation` 31, `Customer Group` 6,
`Territory` 3, `Price List` 2, `Sales Taxes and Charges Template` 3, `Mode of Payment` 5,
`Supplier Group` 9, `Fiscal Year` 1, `Contact` 1, `Salutation` 9, `Gender` 7. Several DocTypes are
empty: `Stock Entry`, `Batch`, `Serial No`, `Quotation`, `Delivery Note`, `Purchase Receipt`,
`Request for Quotation`, `Supplier Quotation`, `Bank Account`, `Asset Value Adjustment`,
`Asset Capitalization`, `Address`, `Lead`, `Opportunity`, `Project`, `Task`, `Timesheet`, `BOM`,
`Work Order`, `Employee`, and `Payment Terms Template`.

### Assignment actions and reports (2026-10-16)

The four assignment actions are live. A run-owned Item lifecycle passed: create, complete, reopen,
and remove. Native `assign_to.remove` **cancels** the ToDo; it does not delete the record. Clean up
run-owned ToDos through `artifacts/cleanup-assignment-tests.py`. Evidence:
`artifacts/erpnext-phase-C1-verification.json`.

`run_report` exposes 13 native reports with the company fixed to
`Serendib Consumer Products (Pvt) Ltd` and a closed filter object. Eleven reports match an
independent REST call. Two reports, `Balance Sheet` and `Profit and Loss Statement`, fail inside the
native report with `KeyError: None` for every filter set tried, including the same call made
directly against ERPNext without ERPBridge. Report access was granted by adding the `ERPBridge Demo`
role to each Report and granting `report` on the reference DocTypes. Evidence:
`artifacts/erpnext-report-permissions.json`, `artifacts/erpnext-phase-B-verification.json`.

Working filter sets: `Trial Balance` needs `filter_based_on: Fiscal Year` and `fiscal_year: 2026`;
`General Ledger` and `Stock Ledger` need `from_date` and `to_date`; `Gross Profit` needs the dates
plus `group_by`. The register and balance reports accept the company alone.

## Permissions and preservation

- The `ERPBridge Demo` role has read/select for the approved master, order,
  invoice, payment, journal, and seven asset-related DocTypes.
- Existing Item read/create/write/delete/select permissions remain unchanged.
- New accounting and asset grants do not include create, write, delete, submit,
  cancel, amend, report, import, export, share, print, or email.
- ToDo uses its existing native `All` role permissions. These already include
  capabilities beyond reads; this expansion did not add or change them and
  exposes only GET tools. It is not a global read-only ERP credential.
- The enabled integration account remains a Website User with desk access off.
  Its identity, roles, API credentials, and existing client token were retained.
- Strict-SSH bench-console execution applied only reviewed permission metadata.
  Effective grants and unchanged counts across 21 business tables were checked.
  Full bootstrap and business fixtures were not rerun.
- All existing tool revisions were preserved, including the operator-added
  `get_company@1.0.1`. Only the eight accounting and sixteen asset/assignment
  GET additions were applied in their respective batches. The later five-read
  batch preserved all 41 pre-existing revisions. Inventory is 46 READY revisions
  for 45 unique names; the operator's additional Company revision remains.

## Verification and limits

Exact 45-name MCP discovery passed with the existing dedicated client token.
The original eight Item fixtures were checked. Accounting lists and existing
Sales Invoice, Purchase Invoice, and Payment Entry named gets passed independent
REST comparisons. Journal Entry had only empty-list/error-probe coverage.

All sixteen new asset/assignment tools passed bounded component checks. All eight
of those datasets were empty. Named gets therefore have `explicit-error-probe`
coverage: direct REST confirmed a missing name and MCP returned an explicit
error. A generic MCP error does not prove its cause or a successful named read.
No assets or assignments were created to manufacture positive coverage.

Repeated combined full-catalogue scans encountered intermittent MCP errors,
including an existing Customer read. A standalone Customer MCP/REST check and
the bounded asset/assignment checks passed afterward. The root cause is not
established. Do not claim one accepted uninterrupted all-catalogue semantic run
or production availability from these component checks.

The five new reads passed native REST/MCP component comparisons before their
legacy-dispatch guard update. Candidate-profile and field-selection probes also
passed without exposing security fields. The guard update was read back on all
five manifests; its negative input test and normal identity read passed live.
A subsequent combined guarded-read attempt reported an intermittent MCP error.
No uninterrupted all-45 semantic run is accepted. Document assignments were
empty, so this is not positive existing-assignment coverage.

All 101 offline tests passed. No paid AI-client rehearsal was performed.

## Source and future onboarding

- [Active OpenAPI contract](../config/erpnext-openapi.yaml)
- [Approved tool names](../config/erpnext-approved-tools.txt)
- [Permission policy](../demo/erpnext/permissions.py)
- [Deferred expansion notes](../.agents/plans/pending-erpnext-expansion/README.md)

Fresh bootstrap includes the permission helper. Do not rerun full bootstrap on
an existing site merely to add permission metadata.

The API registration is preserved when its target and credential reference
match. Onboarding does not force-register over the operator's `/api` base.
Its known base-path probe issue remains deferred; resource execution URLs are
full URLs. Existing unknown ownership and changed contracts still fail closed.

The operator-added `get_company@1.0.1` was outside the original ownership file
and had blocked whole-catalogue `make onboard-erpnext` and the ownership-gated
`make verify-erpnext`. It was reviewed and deleted by owner decision on
2026-10-16, along with every other non-serving revision, so the registry now
holds exactly one serving revision per tool and both gates pass. Restoring it
would require re-applying the manifest from
`erpbridge-demo/archive/control-plane-registry-full-20261016T173539Z.json`.

Private mode-0600, Git-ignored evidence includes:

- `artifacts/erpnext-accounting-live-checks.json`
- `artifacts/erpnext-accounting-permissions.json`
- `artifacts/erpnext-asset-live-checks.json`
- `artifacts/erpnext-asset-read-coverage.json`
- `artifacts/erpnext-asset-permissions.json`
- `artifacts/erpnext-asset-preflight.json`
- `artifacts/erpnext-user-preflight.json`
- `artifacts/erpnext-user-apply.json`
- `artifacts/erpnext-user-live-checks.json`
- `artifacts/erpnext-user-projection-checks.json`
- `artifacts/erpnext-user-guard-check.json`

## Demo dataset write surface (2026-10-16)

Eleven write tools are published and serving, which brings the live catalogue to
152 tools. Ten of them bound to the same `ERP_PRIMARY_KEY` credential are listed
in `WRITE_ADDITIONS` inside `scripts/onboard-erpnext.py`, and all of them are
present in `config/erpnext-openapi.yaml`:

| Tool | Route |
| --- | --- |
| `create_customer`, `delete_customer` | `POST`/`DELETE /resource/Customer` |
| `create_supplier`, `delete_supplier` | `POST`/`DELETE /resource/Supplier` |
| `create_item_price`, `delete_item_price` | `POST`/`DELETE /resource/Item%20Price` |
| `insert_document` | `POST /method/frappe.client.insert` |
| `submit_document` | `POST /method/frappe.desk.form.save.savedocs` |
| `cancel_document` | `POST /method/frappe.client.cancel` |
| `delete_document` | `POST /method/frappe.client.delete` |
| `update_document` | `POST /method/frappe.client.set_value` |

### Posting recipe

`frappe.client.submit` is **not callable over REST on this ERP** (Frappe
16.17.5). `submit_document@1.0.0` used it and could never work; it was removed
and rebuilt as `2.0.0` on `frappe.desk.form.save.savedocs`.

1. `insert_document` creates the draft and returns the full document.
2. `submit_document` resends that full document **including its `modified`
   value** with `action: Submit`.

A submit payload without `modified` fails with `TimestampMismatchError`.

### Permission levels

`demo/erpnext/permissions.py` grants the `ERPBridge Demo` role three levels
across 92 doctypes: `READ` (read, select, report), `MASTER` (adds write, create,
delete) and `DOCUMENT` (adds submit, cancel). Company, Fiscal Year and Currency
stay read-only so the accounting calendar cannot be reshaped. The integration
identity also holds `Prepared Report User` and `Report Manager`; those are
declared in `COMPANION_ROLES` and are not treated as drift.

`report` matters at every level. Frappe checks the reference DocType's report
permission as well as the report's own role list, so denying it breaks every
report tool with a 403.

### Operational findings

- The bridge suppresses ERP error bodies. A failed write reports only
  `the ERP returned an unexpected response`. Replay the same call against the
  ERP with the admin key to read the real exception.
- `metadata.isServing: true` must be present in a submitted manifest or the new
  revision is admitted but never promoted.
- `metadata.isActive: false` is ignored on apply. Retirement needs
  `bridgectl tool delete <name> <version>`.
- Applying changed content to the same `name@version` fails with
  `REGISTRY_CONFLICT`; bump the version.
- Read tools cache for about 60 seconds, so a read straight after a write can
  return the previous state and make a correct UPDATE or DELETE look failed.
- MCP marks failure with `result.isError: true` and omits the field on success.

### Verification

- `make verify-erpnext` passes against the live catalogue.
- `python3 -m unittest discover -s tests` passes 109 tests.
- The demo dataset itself is seeded and checked by `datasets/seed_items.py`,
  `datasets/seed_transactions.py` and `datasets/seed_phase2.py` in the
  `erpbridge-demo` checkout.

## Deferred writes and schema fix

Integer/boolean enums are stringified by the current generator, and explicit
`additionalProperties: false` is dropped. No runtime or installed CLI upgrade
was made. The implementation plan is saved in the sibling ERPBridge checkout:

`../ERPBridge/.agents/plans/upcoming/Plan-Schema-Constraint-Preservation.md`

The failing regression source is stored outside its Go test tree as plan
evidence. Assignment create/remove/complete/reopen writes are now published, and
the master, order and asset-master writes listed above are also published. The
schema dependency is not resolved: the installed CLI and live server still drop
`additionalProperties: false` and stringify integer/boolean enums when they
generate manifests, which is why the live manifests were hardened by hand and
why `make onboard-erpnext` must not be run until the upgrade lands. Native
completion is assignee-only; the integration account must not impersonate
another assignee or bypass that check. Financial reports remain SCP-only.

## Refresh Pi

The MCP URL and token are unchanged:

```text
/mcp reconnect erpbridge
```

Inspect `/mcp` for the refreshed list. Bridge registry/token storage remains
ephemeral. Backup/restore remains unverified. See the
[reproduction runbook](reproduce-deployment.md) for lifecycle and credential
handling. No live image replacement was performed.
