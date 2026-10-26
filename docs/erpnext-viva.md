# ERPNext viva deployment

> **Historical POC documentation.** The recorded deployments were deleted in
> October 2026. The status, tool counts, approvals, and acceptance results below
> describe past work. They do not describe current services or give permission
> to deploy again. Before cloud changes, check the versioned configuration and
> get new approval.

Use [the reproduction runbook](reproduce-deployment.md) for the complete restart/fresh-build procedure. It includes explicit ARM environment creation and Terraform import.

## Scope and current status

This is a small research POC, not a production deployment. ERPBridge and MockERP use Azure Container Apps. Real ERPNext uses one Linux VM, Docker Compose, MariaDB, Redis, and a host Caddy HTTPS proxy. Cloudflare manages DNS-only records in the existing `nimendra.online` zone.

| Service | Canonical URL |
| --- | --- |
| ERPBridge | `https://erpbridge.nimendra.online` |
| MCP | `https://erpbridge.nimendra.online/mcp/` |
| MockERP | `https://mockerp.nimendra.online` |
| ERPNext | `https://erpnext.nimendra.online` |

## Current approved MCP expansion

The live MCP catalogue now has 45 tool names, including five limited user/document-assignment reads. This expansion added read-only master/order, accounting, asset, and assignment operations through `bridgectl`. Asset and assignment datasets were empty. Thus, their named gets have error-probe coverage only. The expansion preserved existing tool revisions, the `/api` registry base, credentials, and business-record counts. Combined full scans encountered intermittent MCP errors. Do not claim full uninterrupted semantic acceptance. New writes and reports remain deferred. See [the active tool catalogue](erpnext-tools.md) for names, permission limits, evidence, and Pi refresh instructions. The five-tool results below describe the historical initial test, not the current tool count.

## Verified deployment test: 2026-10-15

The approved live test passed checks of all three HTTPS endpoints, Administrator session login, two SCP seed runs, integration-user reads of the company/eight Items, exact five-tool MCP discovery, and MCP create/read/delete with a separate client token. Independent REST reads confirmed temporary Item deletion and unchanged baseline values. Protected evidence is `artifacts/erpnext-live-checks.json`. Final checks passed 57 local tests and a real no-drift ACA Terraform plan. Anonymous control-plane access returned HTTP 401.

**At the end of the deployment test, the ERPNext VM was deallocated.** It was later started on request, and HTTPS readiness passed. This historical record is not a live power-state check. Use `make erpnext-status`. Its disk/IP and the Container Apps can still use credit. If it is deallocated, start it before you open its URL or call its MCP tools:

```bash
make erpnext-start CONFIRM=yes
make erpnext-ready
```

The test allowance was USD 5. The user confirmed at least that much unexpired student credit. The test took approximately 1.34 hours. A conservative modeled base for that period is about USD 0.23 before free grants, logs, network use, and actual billing reconciliation. This is not a billing cap or a final invoice.

**Remaining limits:** the live backup attempt failed. Thus, backup/restore is not accepted. This work did not complete a disposable restore or natural-language AI-client rehearsal. Automatic switching of an existing target remains blocked. Do not describe these as verified.

Ignored mode-0600 rerun inputs are `terraform/erpnext-demo.auto.tfvars.json` and `terraform-erpnext/demo.auto.tfvars.json`. They preserve the tested target and environment without changing the original `terraform/terraform.tfvars`. The main private login/token summary is `artifacts/deployment-output.json`; the tested client token is under `mcp_tokens.erpnext-viva-test`.

Before the test, the resource group and managed Terraform state contained no deployed apps. The first new environment was Express and failed custom-domain/storage tests. After explicit user approval, the replacement included only those new test apps/environment and their app DNS records. It preserved the resource group, Log Analytics workspace, ERPNext VM/data, and DNS zone. No old-zone record was abandoned from managed state.

Local tests alone do not prove live success. To resolve a future state mismatch, do not automatically import, remove state, or delete resources.

## 1. Approve costs and account prerequisites

Before any apply:

- Check remaining Azure student credit and expiry in the Azure Sponsorships/education billing portal. The student offer is not evidence of the current balance.
- Confirm Central India SKU availability, restrictions, and quota for the selected VM. The live test successfully allocated `Standard_B2as_v2` without a zone. The read-only SKU result restricted zones 2/3, not the entire location. `Standard_B2ms` had a location-wide subscription restriction and was not used. Before a future deployment, check again.
- Price VM compute, its persistent managed disk, Standard public IP, network use, both retained ACA services, and logs. Deallocation stops VM compute charges, not the other charges.
- Obtain an explicit spending allowance and approve the exact Terraform plans.
- Confirm that Microsoft.Compute, Microsoft.Network, Microsoft.App, and Microsoft.OperationalInsights are registered as required. Provider configuration does not automatically register them. If registration is needed, the operator must do it separately.
- Confirm the SSH public key and operator public IPv4 `/32`. Do not use the sample CIDR.

Do not upgrade Azure for Students, remove its spending limit, use Spot capacity, or purchase a model service automatically.

## 2. Load Cloudflare access privately

The token is already stored in ignored `secrets/cloudflare.token`, mode `0600`:

```bash
set +x
export CLOUDFLARE_API_TOKEN="$(< secrets/cloudflare.token)"
```

The existing `nimendra.online` Zone ID is `c68ea0eb14e403a5454c04b6efecdcf2`. The token needs Zone DNS Edit and Zone Read. The approved deployment successfully tested its active status, read access, and DNS Edit permission.

The current token cannot access `nimendra.xyz`. If the actual Terraform state still manages old-zone records, obtain scoped access to that zone before migration, or agree on a separate cleanup procedure. Do not silently abandon Terraform-managed records.

Never print the token or save it in Terraform variables. Cloudflare proxying, Tunnel, and WAF are not part of this setup.

## 3. Prepare the independent VM root

There are two separate states:

- `terraform/`: ACA, bridge/mock DNS, certificates.
- `terraform-erpnext/`: VM, its network/disk/IP, and ERPNext DNS.

```bash
cp terraform-erpnext/terraform.tfvars.example terraform-erpnext/terraform.tfvars
```

In the ignored file, set your subscription, tenant, deployment region, SSH public/private key paths, and actual operator `/32`. Terraform reads only the public key. The private-key path is connection metadata, not the private key content.

```bash
make erpnext-plan
```

Before you run this command, review the saved VM plan and cost, and obtain separate approval:

```bash
make erpnext-apply CONFIRM=yes
```

The VM bootstrap installs Docker Compose and Caddy. It does not receive app passwords through Terraform/cloud-init and does not start ERPNext automatically. The managed disk holds Docker volumes and Caddy certificate state. If you delete the disk, you lose those volumes.

## 4. Bootstrap ERPNext

Wait for cloud-init to finish on the VM. Verify its SSH host key through an independent trusted channel. Then add the verified key to your local `known_hosts`. The deployment driver uses `StrictHostKeyChecking=yes`. It will not trust an unknown host automatically.

```bash
make erpnext-bootstrap CONFIRM=yes
```

This transfers the reviewed stack to `/opt/erpnext-demo`. It creates the `frontend` site if absent, initializes Sri Lanka setup fixtures, creates the integration identity, and starts HTTPS at `erpnext.nimendra.online`. Runtime containers restart automatically. The stack has no worker/scheduler because the selected demo uses synchronous Company/Item REST operations.

The bootstrap saves initial credentials in local `artifacts/erpnext-credentials.json`, mode `0600`. On reruns, it preserves working credentials. If saved credentials are missing or conflicting, it refuses to continue instead of silently resetting them.

- `admin.username` is `Administrator`; `admin.password` is the generated browser password.
- The local bootstrap credential file can also contain an Administrator API pair for master-data seeding. Do not give that pair to ERPBridge or the AI client.
- `integration` contains the separate restricted user's API key/secret used by ERPBridge.
- The database root password stays in the VM's protected environment file.

The API credential supports Company, metadata, Customer/Supplier, and Sales/Purchase Order reads, plus the original Item permissions. This work added no new business-write permissions. The integration account remains a native no-desk Website User. This site contains only synthetic data. Generic Item delete must not be exposed against valuable data.

## 5. Load the realistic SCP baseline

```bash
make erpnext-seed CONFIRM=yes
make erpnext-data-check
```

The versioned fixture file is `demo/erpnext/fixtures/scp-masters.json`:

- Serendib Consumer Products (Pvt) Ltd, Sri Lanka, LKR.
- Seven Item Groups and four units.
- Eight catalogue Items, including tea, floor cleaner, ingredients, packaging, office paper, and a conveyor belt.

These master records support stock. They are not seeded stock balances. The baseline fixture does not seed Item Prices, Customers, Suppliers, Orders, invoices, payroll, or custom approval workflows. Existing site records are separate from this fixture. Native company setup may create standard ERPNext defaults. Do not present those as tested transactions.

Seeding checks existing values and does not overwrite conflicting records. Repeat seeding must preserve the same eight baseline Items. Temporary Items use exact `VIVA-RUN-*` names. Do not bulk-delete a prefix or run the historical paper reset scripts here.

## 6. Configure ERPBridge and review ACA deployment

### Standard environment mode is required

The actual environment is `erpbridge-demo-aca-standard`, with ARM `properties.environmentMode = "WorkloadProfiles"` and the `Consumption` profile. **Express mode does not support the required custom domains.** The initial deployment also returned bridge storage errors. Even if `/mcp/health` returns 200, do not use Express mode.

The pinned AzureRM provider does not expose `environmentMode`. During this test, an explicit ARM `2026-07-01` PUT created the standard environment. It was then imported into `azurerm_container_app_environment.demo`. Its logs use the retained workspace. Terraform now manages it with an explicit Consumption profile. Before creating the apps, a fresh rebuild must repeat a reviewed explicit-mode creation/adoption step. The profile block alone does not prove the mode. Before applying the app plan, check the ARM GET result. Do not change provider versions automatically.

The pinned bridge image runs as a non-root user. `DATABASE_PATH=/tmp/erpbridge.db` uses writable, ephemeral container storage. The `/data` mount is not the active registry path. If a new revision or reschedule causes registry/token loss, you must still re-onboard and create new client tokens.

Use the existing `terraform/terraform.tfvars.example` for the ACA root. Preserve external-service credentials that are already configured. Policy Gate, Data API, and Resend are optional for this Company/Item demo.

Set the actual ignored ACA inputs to:

```hcl
erp_target  = "erpnext"
erpnext_url = "https://erpnext.nimendra.online"
custom_domains = {
  zone_id   = "c68ea0eb14e403a5454c04b6efecdcf2"
  erpbridge = "erpbridge.nimendra.online"
  mockerp   = "mockerp.nimendra.online"
}
```

Leave `erpbridge_erp_base_url` unset. A conflicting override is rejected. Supply the integration token through the process environment. Do not print it:

```bash
set +x
export TF_VAR_erpnext_api_token="$(jq -r '.integration | "token \(.api_key):\(.api_secret)"' artifacts/erpnext-credentials.json)"
```

Also supply your existing ERPBridge admin bearer token and MockERP credentials through the documented environment inputs. On repeat deployments, reuse the admin token. Do not regenerate it in every shell.

If switching an already active bridge, pause calls. Before changing its credential, disable only the previous target's owned tools. Never rely on ACA ephemeral storage loss to clear them. Keep unrelated APIs and tools intact. Onboarding supports a fresh empty registry or the same previously owned ERPNext manifest set. It refuses unknown ownership, stale previous-target tools, and changed active versions. `scripts/switch-erp-target.py` is an explicit fail-closed gate. It currently refuses even with `--confirm` because live version/disable behavior is unverified. For an existing registered target, stop here. Before applying the new credential, approve an exact-name/version disable procedure. Do not treat that switch gate as completed migration automation.

```bash
make plan
chmod 600 terraform/tfplan
```

Inspect the plan for unexpected application/environment replacement, old-zone DNS changes, and certificate changes. In a real domain migration, keep the previous inputs and Azure fallback URLs for rollback. A short maintenance window is acceptable. After separate approval, apply the exact reviewed plan:

```bash
terraform -chdir=terraform apply -input=false tfplan
```

After changing a plan's inputs, do not reuse that plan. You should not run deployment commands as part of local tests.

## 7. Onboard and export

Set `BRIDGE_API_TOKEN` to the existing admin token without storing it in a bridgectl context. Use the canonical bridge URL for both CLI and MCP clients.

```bash
export BRIDGE_SERVER="$(terraform -chdir=terraform output -raw erpbridge_url)"
export BRIDGE_MCP_SERVER="$BRIDGE_SERVER"
make onboard-erpnext CONFIRM=yes
export MCP_CLIENT_NAME=viva-client
./scripts/create-mcp-token.sh "$MCP_CLIENT_NAME"
make export-output
```

The curated contract now exposes 45 tools (43 native operations plus two reviewed read aliases): the original `list_companies`, `list_items`, `get_item`, `create_item`, and `delete_item`, plus forty read-only additions in [the active tool catalogue](erpnext-tools.md). Generated create inputs are flat Item fields, not a nested `body`. Native DELETE returns HTTP 202 with `{"data":"ok"}`. Onboarding preserves that object envelope for MCP output validation. The contract was written for this research scenario. It is not an official full ERPNext OpenAPI specification.

The main private summary is `artifacts/deployment-output.json`:

- Canonical URLs and VM connection metadata.
- ERPNext Administrator browser login.
- Restricted ERPNext integration credential and selected `ERP_PRIMARY_KEY`.
- ERPBridge admin bearer token and saved per-client MCP tokens.

When ERPNext is selected, the exporter reads both states. The exporter validates URL/credential agreement, writes atomically with mode `0600`, and never creates or rotates tokens. It does not export Cloudflare/Azure tokens or SSH private keys. These are plaintext secret files protected by local permissions, not encrypted vault files. Do not screen-share them or include them in reports.

## 8. Rehearse

```bash
make erpnext-ready
# Read the dedicated token privately. Do not echo it or use shell tracing.
export MCP_CLIENT_NAME="${MCP_CLIENT_NAME:-erpnext-viva-test}"
export MCP_TOKEN="$(jq -er --arg name "$MCP_CLIENT_NAME" '.mcp_tokens[$name].token' artifacts/deployment-output.json)"
make verify-erpnext
make verify-erpnext-write CONFIRM=yes
```

The default check is read-only. The write check creates one unique sample Item. It reads the Item through MCP and independently through ERPNext, deletes it, verifies its absence, and checks that the baseline remains. A JSON-RPC error, MCP `isError`, missing expected record, or incorrect field means the check failed, even if HTTP status is 200.

Configure your existing AI client with `https://erpbridge.nimendra.online/mcp/` and the saved `viva-client` token. Do not buy a provider automatically. Confirm its tool calls separately. Deterministic script success does not prove natural-language workflow success.

Presentation sequence:

1. Show SCP's products in ERPNext.
2. Discover the five MCP tools.
3. Ask for product details of `FG-CLEANER-5L`.
4. Create a uniquely named sample Floor Cleaner 1L Item.
5. Refresh the ERPNext UI and show the same code/name.
6. Delete only the sample and confirm the baseline remains.

Before presentation, run two full rehearsals. If the AI client fails, use the deterministic MCP check. This remains a cloud demo and requires usable Internet. Keep redacted rehearsal screenshots or a short recording. Do not report recorded evidence as a live success.

## 9. Stop compute and recover

```bash
make erpnext-status
make erpnext-deallocate CONFIRM=yes
# Next preparation session:
make erpnext-start CONFIRM=yes
make erpnext-ready
```

The stop command verifies `PowerState/deallocated`, not ordinary guest shutdown. Disk, public IP, ACA, and logging costs may continue.

`demo/erpnext/backup.sh` and `restore.sh` are VM-side maintenance commands. On the VM, backup emits a non-secret `backup-<24hex>` identifier stored under the protected `frontend/private/backups` volume. Restore accepts exactly `restore.sh --confirm-disposable-restore restore-NAME backup-IDENTIFIER`. The separate target site must already exist with a distinct database. The recovery code preserves that target's DB/routing and restores the source encryption keys. Keep backups private and copy them off the VM. Restore only to a separate explicitly named disposable site/target. Never overwrite `frontend` silently. Verify required site configuration/encryption material as well as SQL and files. Until an actual restore has passed, do not claim recovery.

ERPNext volumes survive VM deallocation, but not disk deletion. ACA `EmptyDir` registry/token storage is separate and can disappear. After registry loss, re-onboard the approved tools and explicitly create replacement client tokens. Then update the AI client and repeat verification. Do not destroy the resource group or either Terraform state as a recovery shortcut.

## Local checks

```bash
make fmt-check
make test
terraform -chdir=terraform validate -no-color
terraform -chdir=terraform-erpnext validate -no-color
```

Mocked Terraform tests and HTTP/CLI fixtures must not use real credentials, cloud mutation, SSH, or Docker runtime. Live provisioning, DNS/TLS, ERPNext UI login, backup/restore, and AI-client rehearsal remain separate acceptance checks.
