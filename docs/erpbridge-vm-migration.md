# ERPBridge VM migration

> **Historical POC documentation.** The recorded deployments were deleted in
> October 2026. The status, tool counts, approvals, and acceptance results below
> describe past work. They do not describe current services or give permission
> to deploy again. Before cloud changes, check the versioned configuration and
> get new approval.

Use this runbook to move ERPBridge from Azure Container Apps (ACA) to a
dedicated, always-on Linux VM and upgrade the server to the pinned
`v0.6.0-alpha.1` release. The authoritative task list is
[`Plan-ERPBridge-VM-Migration.md`](../.agents/plans/Plan-ERPBridge-VM-Migration.md).

**Status:** build in progress. Nothing is provisioned. No traffic, DNS record,
secret, or existing resource has been changed. Live acceptance has not run.

## Repeat the migration elsewhere

For a step-by-step procedure that includes every failure during the first
migration and the exact commands that worked, see
[the migration runbook](erpbridge-migration-runbook.md).

This document describes the current environment. The runbook describes how to
repeat the migration.

## Why the move

The live registry uses SQLite at `/tmp/erpbridge.db` inside a Container Apps
replica. `/tmp` and the `EmptyDir` `/data` mount are both ephemeral. Thus, every
image upgrade needs an explicit backup and restore. On the VM, the database is
on a persistent managed OS disk. An image upgrade restarts the container with
the same file instead of restoring the database.

## Target shape

| Component | Choice |
| --- | --- |
| VM | `Standard_B2ats_v2`, 2 burstable vCPU, 1 GiB RAM, Ubuntu 24.04 LTS |
| Operation | Always on, regular pay-as-you-go |
| Storage | 32 GiB Standard SSD OS disk only, SQLite in `/var/lib/erpbridge/data` |
| Secrets | Root-owned `/etc/erpbridge/runtime.env`, mode `0600` |
| TLS | Caddy on 80/443, ERPBridge published on `127.0.0.1:8080` |
| Public name | `https://erpbridge.nimendra.online` (unchanged) |

The OS disk cannot be smaller than the image's own OS disk size, which is 30 GiB
for this Ubuntu image. Standard SSD bills by tier, so 30 GiB and 32 GiB cost the
same. A 1 GiB Standard SSD is not a documented Azure size.

## Reference facts

These facts come from read-only checks during planning and are current as of
2026-10-17. Recheck before any cutover step. Tags and revisions change.

| Item | Value |
| --- | --- |
| Source app | `erpbridge-hq/erpbridge-server` |
| Source version | `0.5.0-alpha.5` (commit `5496483e46445db1774d115f64750d5014f504a3`) |
| Source image | `ghcr.io/nmdra/erpbridge-server@sha256:2ae683b46d4b7cacd4e23f058cbad8768c520ae73a66619e871ed101a09f0575` |
| Target release | `v0.6.0-alpha.1` (commit `7a2d7a4233cee679f745d2788c44b679f68916e8`) |
| Target Docker tag | `ghcr.io/nmdra/erpbridge-server:0.6.0-alpha.1` (no leading `v`) |
| Target image | `ghcr.io/nmdra/erpbridge-server@sha256:274940d90c368eec23b38529ac19b6b67e1a59b88399e236e6a984857d1ba7b6` |
| Region quota | `Standard Basv2 Family vCPUs` 2 of 10, `Total Regional vCPUs` 2 of 6 |

Cost inputs used for the estimate: VM compute USD 0.00615/hour in Central India
(USD 4.49 per 730 hours) and a 32 GiB Standard SSD OS disk at USD 2.64/month.
Static public IP, Blob backup, transaction, outbound, and retained ACA costs are
separate and must be approved before provisioning.

## Infrastructure root

`terraform-erpbridge/` is an independent root that owns only the new VM and its
supporting resources. It looks up the existing `erpbridge-hq` resource group
through a data source and never manages it. It declares no DNS records and no
resources for any other workload.

| Resource | Purpose |
| --- | --- |
| VNet `10.43.0.0/16`, subnet `10.43.1.0/24` | Dedicated non-overlapping network with a storage service endpoint |
| NSG | Inbound only: operator SSH `/32`, public 80 and 443 |
| Static Standard public IP | Stable address for the canonical DNS record |
| Linux VM | `Standard_B2ats_v2`, Ubuntu 24.04, system-assigned identity |
| OS disk | 32 GiB `StandardSSD_LRS`, explicit name `erpbridge-vm-osdisk` |
| Storage account and private container | Off-VM backups, TLS 1.2 minimum, no public blob access, no shared-key access, firewall denies all except the VM subnet |
| Role assignment | `Storage Blob Data Contributor` scoped to the one backup container |
| Lifecycle policy | Deletes backup blobs and versions after `backup_retention_days` |

The provider block sets `delete_os_disk_on_deletion = false`, so deleting the VM
retains the OS disk that holds the database. No separate data disk exists.

```bash
make erpbridge-vm-init       # terraform init, no resources created
make erpbridge-vm-validate   # terraform validate
make erpbridge-vm-plan       # saves tfplan for review
make erpbridge-vm-apply CONFIRM=yes   # applies the reviewed plan
```

`erpbridge-vm-apply` refuses to run without `CONFIRM=yes`. It applies the saved
plan rather than creating a new plan. You must obtain cost approval before you
run it.

### Contract checks

```bash
terraform -chdir=terraform-erpbridge fmt -check -recursive
terraform -chdir=terraform-erpbridge validate
python3 -m unittest discover -s tests -p 'test_terraform_contracts.py' -v
```

The contract test copies the root into a temporary directory. It copies the
source files, the committed lock file, the whole `tests/` directory, and
`cloud-init.yaml`, then creates symlinks to the locally installed providers. It
never copies state, real tfvars, backend configuration, or credentials. It
performs no network init. It also verifies that the root stays isolated: no
managed data disk, no data-disk attachment, no DNS provider or DNS resources, no
other workload, no `azurerm_resource_group` resource, and no credentials in
cloud-init.

## Preflight

`preflight` records the source app's non-secret identity in a protected
manifest. A later cutover can then reject drift. The operation is read-only.

```bash
python3 scripts/erpbridge-vm.py preflight
python3 scripts/erpbridge-vm.py --manifest artifacts/erpbridge-vm-migration/source-manifest.json preflight
```

Exit code `0` means no blockers. Exit code `1` means blockers were found. The
tool prints the blocker names to standard error and stores them in the manifest.

Checks performed:

- Exactly one replica, single revision mode, and 100 percent of traffic on the
  latest revision.
- The image is pinned by `sha256` digest, not a mutable tag.
- Required non-secret settings and required secret **references** exist.
- Every referenced secret entry resolves inside the app secret store.
- Ingress is external, disallows insecure connections, and its port matches
  `MCP_PORT`.
- The canonical custom domain is configured and `BASE_URL` equals it, so clients
  keep the same URL.
- The Terraform summary URL matches the canonical URL and the Azure fallback URL
  is recorded for rollback and client-impact review.
- The source database path is still the known ephemeral `/tmp/erpbridge.db`.

### Drift check

Compare the current state with a recorded manifest without rewriting it:

```bash
python3 scripts/erpbridge-vm.py preflight --check artifacts/erpbridge-vm-migration/source-manifest.json
```

If the image digest, revision, environment subset, secret reference set,
ingress, or scale setting has changed, the check fails with exit code `1`.
It names the changed fields.

### What preflight never does

- It never reads, prints, or stores secret values. Only secret **names** and
  environment-variable **names** are recorded.
- It only copies values for a fixed allowlist of known non-secret settings, such
  as `DATABASE_PATH`, `MCP_PORT`, and `BASE_URL`. Every other variable is
  recorded by name alone.
- It issues no Azure write, delete, `exec`, or update operation.
- It suppresses provider diagnostics on failure so errors cannot echo provider
  text into logs.
- The manifest is written with mode `0600` under a `0700` directory.

## Runtime assets and startup guard

| File | Role |
| --- | --- |
| `demo/erpbridge/require-database.sh` | Startup guard. Runs as `ExecStartPre` on every start. Read-only. |
| `demo/erpbridge/erpbridge.service` | systemd unit for the single container. |
| `demo/erpbridge/Caddyfile` | Normal serving configuration. |
| `demo/erpbridge/Caddyfile.maintenance` | Returns 503 for every path during migration. |
| `demo/erpbridge/cloud-config-runtime.sh` | On-VM bootstrap. Repeat-safe, refuses to run without `--confirm`. |

### What the guard checks

The guard blocks startup unless all these conditions are met. If a check fails,
the service does not start. This is always safer than serving an empty or wrong
registry.

1. The state and data directories exist.
2. The protected environment file exists, is non-empty, defines `API_AUTH_TOKEN`,
   `ERP_PRIMARY_KEY`, and `DATABASE_PATH`, and sets `DATABASE_PATH=/data/erpbridge.db`.
3. The filesystem UUID of the data path matches the value recorded at bootstrap.
   Thus, the guard cannot mistake a reimaged or replaced OS disk for the original.
4. The data directory owner matches the container user recorded from the pinned image.
5. The database exists, is non-empty, and `PRAGMA quick_check` returns `ok`.

The guard never creates a database, never repairs one, and never changes ownership.

### Bootstrap order

1. `capture-config` reads the serving revision environment and writes a protected
   staging file. Every value goes through a protected file, never an argument.
2. `terraform-erpbridge` is applied after cost approval, which creates the VM.
3. `bootstrap` pins the host key, uploads the assets, and runs the on-VM script.
   The script records the disk identity, resolves the container user from the
   pinned image, writes the service wiring, installs the unit, enables the
   service without starting it, and activates the maintenance proxy.
4. `install-runtime-env --confirm` streams the staged file over SSH standard
   input and installs it mode `0600` under a `0700` directory.
5. The Task 4 tooling restores the database. Until then, the guard blocks startup,
   and the proxy returns the maintenance response.
6. `start --confirm` starts the service in maintenance mode. `start --confirm
   --serving` also promotes the normal proxy configuration.
7. `stop --confirm` stops the service and returns the proxy to maintenance.

The tooling pins host keys on first use. If a host key changes, the tooling
refuses to connect and requires operator inspection. There is no automatic
trust-on-first-use bypass.

## Hosting switch, DNS, and TLS

The bridge keeps the same public hostname in both hosting modes. Only the
record's type and target change, and only at an operator's request.

| Variable | Default | Effect |
| --- | --- | --- |
| `erpbridge_hosting` | `aca` | `aca` keeps the Container App authoritative; `vm` moves the canonical hostname to the dedicated VM |
| `erpbridge_vm_ipv4` | `null` | Static VM address. Required for `vm`, and validated as IPv4 |
| `erpbridge_source_fenced` | `false` | Closes the bridge Container App ingress |

Cross-checks refuse `erpbridge_hosting = vm` unless `erpbridge_source_fenced` is
true and a valid address is set. Thus, the configuration cannot express an
accidental two-writer state.

### Staged order

1. **Fence only.** Set `erpbridge_source_fenced = true` with hosting still `aca`.
   This is an application-scope change. It does not create a new revision, and
   the serving replica keeps its state. The canonical record is unchanged.
2. **Move the record.** Set `erpbridge_hosting = vm` and `erpbridge_vm_ipv4`.
   Only `cloudflare_dns_record.app_cname["erpbridge"]` changes: from a CNAME to
   the Container App to an A record at the VM. The MockERP record, type, and target
   do not change.

The bridge Container App keeps its custom-domain binding, `asuid` TXT record,
and managed certificate throughout. To roll back, change the record and set
`erpbridge_source_fenced = false`.

Changing an existing DNS record's type is **not an atomic mutation**. Review the
plan and expect a controlled replacement, not an edit without interruption.

### The CNAME wait

While the bridge record points at the Container App, Terraform waits for the
CNAME and validation TXT to resolve before attaching the domain. In `vm` mode,
that check would never pass. A harmless no-op command disables the check without
removing the resource. Keeping the instance in state means the cutover plans no
destroys. Rollback only re-enables the check.

### TLS before cutover

Until the record changes, certificate issuance cannot be tested against the live
hostname. Thus, the plan first prepares a certificate with operator-side DNS
validation. The procedure validates the certificate and key locally, then
transfers them over verified SSH with the key at mode `0600`. It installs them
into the temporary proxy configuration. A hostname-aware check then confirms
them. The check pins the address with `curl --resolve` and never disables
certificate verification. During this step, the proxy returns the maintenance
response and serves no API calls.

### Staging the certificate

```bash
python3 scripts/erpbridge-vm.py tls-stage --cert fullchain.pem --key privkey.pem --confirm
```

The private key must already be mode `0600`. The tool refuses a readable key.
The certificate must cover the canonical hostname, exactly or through a
single-label wildcard. By default, it must stay valid for at least seven days.
The key must match the certificate.

After the local checks, the tool streams the certificate and key over verified
SSH standard input. It installs the key with mode `0600` and activates the
temporary TLS-check configuration. It verifies the result in two ways:

1. The fingerprint of the certificate the VM actually serves must equal the
   fingerprint of the local certificate.
2. A hostname-aware `curl --resolve` request to `/mcp/health` must honour the
   certificate and return the maintenance response. Certificate verification is
   never disabled, so a chain or hostname problem fails the check.

The proxy returns the maintenance response throughout. It serves no API calls
before cutover.

## Backup and restore

| File | Role |
| --- | --- |
| `demo/erpbridge/backup.py` | Consistent snapshot, verification, and off-VM upload. |
| `demo/erpbridge/restore.py` | Explicit, guarded restore. |
| `demo/erpbridge/erpbridge-backup.timer` | Daily schedule. Persistent across a missed window. |
| `demo/erpbridge/erpbridge-backup.service` | Runs the backup once per trigger. |

### What the backup does

1. Checks free space on the data filesystem, using three times the database size
   plus a floor, so a snapshot cannot fill the disk.
2. Takes the snapshot with the **SQLite online backup API**, which is consistent
   while the container holds the database open. It never copies an open file.
3. Runs `PRAGMA integrity_check` on the copy, not on the source.
4. Records a per-table row count so a later restore can prove logical equality.
5. Publishes the file with an atomic rename inside one directory.
6. Uploads with the VM managed identity. No shared key is used and no credential
   value is read, printed, or stored.
7. Downloads the object again and compares its SHA-256 before claiming success.
8. Removes the local copy only after that confirmation, and keeps at most two
   failed copies so repeated failures cannot fill the disk.
9. Writes a protected status file with the object name, checksum, size, table
   counts, and the last error if any.

The storage lifecycle policy enforces Blob retention (seven days). This tool
does not enforce retention.

### What the restore refuses to do

- It refuses to run without `--confirm`.
- It refuses when the service is not definitively stopped. It accepts only
  `inactive` or `failed`. It treats `activating`, `deactivating`, and unreadable
  states as unsafe.
- It refuses when the filesystem identity differs from the bootstrap record.
- It refuses when staging and the target are on different filesystems, because
  the install must be an atomic rename.
- It refuses a checksum mismatch, a failed integrity check, or a manifest mismatch.
- It **moves existing `-wal`, `-shm`, and `-journal` files aside before**
  installing. Installing the main file next to a stale write-ahead log can make
  SQLite replay an incompatible log over the restored data.
- It never runs at boot and is never scheduled.

After installation, it reopens the database once, rechecks integrity, and
compares the table counts with the verified snapshot.

### Operator commands

```bash
python3 scripts/erpbridge-vm.py backup                      # run now
python3 scripts/erpbridge-vm.py backup --dry-run            # verify without uploading
python3 scripts/erpbridge-vm.py restore --backup-id NAME --confirm
```

`backup` needs no confirmation because it is non-destructive. `restore` requires
`--confirm` on the host and passes its own confirmation to the VM script.
Arguments that reach the remote command are validated against strict patterns,
so a backup identifier or path cannot inject a shell command.

### Recovery point

Daily backups mean up to 24 hours of recovery loss. Restoring an older copy can
undo a token revocation. Every disaster restore needs a security review, not
only a successful checksum.

## Operator outputs

`make export-output` writes the protected artifact. It now also records:

| Field | Meaning |
| --- | --- |
| `urls.erpbridge_hosting` | `aca` or `vm` |
| `urls.erpbridge_record_type` | `CNAME` or `A` |
| `urls.erpbridge_azure_fallback_state` | `active-fallback`, or `retired-fenced-diagnostic-only` once the source is fenced |
| `deployment.erpbridge.staged_env_comparison` | `absent`, or `match` when a staged environment equals the authoritative values |
| `deployment.erpbridge_vm` | VM identity, address, disk name, and backup identifiers, only when hosting is `vm` |

If a staged environment differs from the authoritative credential, the export
fails. The export compares the staged environment but never prints it. The
staged environment must not be accessible to the group or others. When hosting
is `vm`, the bridge A record content must equal the VM public address. If the
record points at the wrong host, the export fails.

```bash
make erpbridge-hosting      # print the hosting mode, fence state, and record shape
make export-output          # write the protected artifact
```

`scripts/verify-deployment.sh` refuses to run while the bridge is fenced or
hosted on the VM. A check of the old endpoint would prove nothing about the
deployment that serves clients. Thus, the script fails closed.

Every helper that operates on the bridge reads the canonical `erpbridge_url`
output. None uses the Azure fallback URL. Thus, no helper can silently target
the fenced Container App.

## Snapshot helper

`demo/erpbridge/sqlite-snapshot/` holds the helper that takes a consistent
snapshot from inside the running container, which has no `sqlite3` CLI and no
Python.

This source is not new. It is an unchanged copy of the reviewed helper that
performed the live alpha.5 upgrade. It comes from the ignored evidence directory,
so the cutover does not depend on an unreviewed rebuild:

```bash
make erpbridge-snapshot-helper   # builds artifacts/erpbridge-sqlite-snapshot
```

Invocation on the VM:

```text
sqlite-snapshot <absolute-source> <absolute-new-destination>
```

It prints one JSON line with `snapshot_created`, `source_mode`, and
`journal_mode`.

### Properties that matter

- The source connection is **read-only**, so the helper cannot change the live
  registry. Its failure text always requests no source writes.
- The destination must not already exist. An existing file is never overwritten,
  because that file may be a restore target.
- Both paths must be absolute and must differ.
- `VACUUM INTO` produces a transactionally consistent, standalone snapshot.
- The destination is set to mode `0600`.
- A failure prints exactly one generic line, so operators do not read provider
  or SQLite detail. Thus, tests verify effects, not messages.

### Transfer protocol

The container's only transport is a terminal. Thus, the protocol transfers the
helper and its output as text. This protocol was verified in the alpha.5 upgrade:

1. gzip and base64 the helper locally.
2. Write it into a private temporary directory in 3000-character chunks.
3. Decode, decompress, and **verify the helper SHA-256 before executing it**.
4. Run the helper against `/tmp/erpbridge.db`.
5. Export the snapshot with base64 and compare the remote and local SHA-256.
6. Remove the remote temporary directory.

No binary framing is required, and every step is checksum-verified.

`demo/erpbridge/aca-session.py` implements that channel. It uses the transport
from the same reviewed file and preserves its wire behaviour:

- a terminal resize packet on connect;
- `\x00\x00` command framing, bounded at 3900 bytes of terminal input;
- an unguessable marker that returns the command's exit status, so a failed
  command cannot be mistaken for success;
- both output channels captured in arrival order;
- an export delimiter, so command echo cannot be confused with payload.

**Prerequisite:** the WebSocket client comes from the Azure CLI package, not from
PyPI. Before you run this file, log in. Use the interpreter supplied with the CLI:

```bash
AZ_PY="$(dirname "$(readlink -f "$(command -v az)")")/../bin/python"
"$AZ_PY" demo/erpbridge/aca-session.py --probe-only
```

`--probe-only` connects and acknowledges one command. This is the lowest-cost
way to verify that the channel works before a cutover night.

## Cost estimate

`cost` produces an itemized estimate from an operator-supplied price sheet.
This makes the numbers explicit and available for review, not hidden in code.

```bash
python3 scripts/erpbridge-vm.py cost \
  --price-sheet artifacts/erpbridge-vm-migration/prices.json \
  --running-hours 730 --backup-gb 5 --backup-operations 10000 \
  --outbound-gb 1 --aca-retained-months 1
```

The price sheet is JSON with a `meters` object containing `vm_hourly`,
`os_disk_monthly`, `static_ip_monthly`, `backup_capacity_gb_monthly`,
`backup_operations_per_10k`, `outbound_gb`, and `aca_retained_monthly`, plus
optional `currency`, `as_of`, and `region`. A missing meter is an error.

The output is an estimate, not a bill. It excludes taxes, discounts, credit
expiry, and any meter the sheet omits.

## Verification

```bash
python3 -m unittest discover -s tests -p 'test_erpbridge_vm.py' -v
```

The suite uses fake `az` and `terraform` executables. It never calls Azure and
never reads real state. It verifies the blocker paths, the drift check, the
redaction guarantees, the protected file modes, and the cost arithmetic.

## Known limits

- Exact `Standard_B2ats_v2` availability in this subscription is **not**
  verified. This subscription has per-size restrictions. Thus, availability
  remains a preflight item.
- The VM replacement path keeps the OS disk only because the new root will set
  the provider feature `delete_os_disk_on_deletion = false`. A retained disk
  still incurs charges and still needs cleanup.
- Backup and restore have not passed acceptance in this repository. They must
  pass acceptance before cutover, not in parallel with it.
