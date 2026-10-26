# ERPBridge migration runbook: Container Apps to a dedicated VM

> **Historical POC documentation.** The recorded deployments were deleted in
> October 2026. The status, tool counts, approvals, and acceptance results below
> describe past work. They do not describe current services or give permission
> to deploy again. Before cloud changes, check the versioned configuration and
> get new approval.

Use this procedure to move the ERPBridge server from Azure Container Apps
(ACA) to a small dedicated VM with persistent SQLite on the OS disk. The procedure
preserves the public hostname, client tokens, and registrations.

This runbook is based on the migration completed on 2026-10-17. Every warning
below describes a failure that caused a delay during that migration. Read the
warnings before you run the commands.

Environment-specific values in this document:

| Value | Used here |
| --- | --- |
| Resource group | `erpbridge-hq` |
| ACA app | `erpbridge-server` |
| Canonical hostname | `erpbridge.nimendra.online` |
| Terraform roots | `terraform/` (ACA), `terraform-erpbridge/` (VM) |
| Migration working directory | `artifacts/erpbridge-vm-migration/` |

## Current monitoring

Azure monitoring and alerts were deployed on 2026-10-18. Use the
[monitoring runbook](erpbridge-monitoring.md) for the monitoring-only installer,
alert policy, commissioning evidence, and remaining email/observation checks.
Do not run runtime bootstrap to install monitoring. Runtime bootstrap has a
separate persisted maintenance-Caddyfile defect. Monitoring does not restart or
repair ERPBridge.

## Why this migration exists

The ACA registry uses SQLite at `/tmp/erpbridge.db` inside a replica. `/tmp` and
the `EmptyDir` mount are ephemeral. Thus, every image upgrade needed an explicit
backup and restore. On a VM, the database is on a persistent managed OS disk.
An upgrade restarts the container with the same file.

## Approvals required before starting

Do not begin without all four approvals. Errors in three of these areas can
cause high costs or irreversible changes.

1. **Spend approval** for the VM, its OS disk, the static IP, and backup storage.
2. **Maintenance window** and a **maximum outage budget**. Measured downtime for
   this migration was **4 minutes 44 seconds**; budget at least 30 minutes.
3. **Reachability confirmation**: clients must use the canonical hostname. Clients
   using the Azure `*.azurecontainerapps.io` hostname cannot be migrated silently.
4. **Recovery confirmation**: a verified backup exists and a restore has been
   rehearsed. If backup has never passed in your environment, verify it first.

---

## Phase 1: Build and verify, offline

This phase makes no cloud calls, has no cost, and is fully reversible.

```bash
make fmt-check
python3 -m unittest discover -s tests
make erpbridge-snapshot-helper
```

Expected: three formatted Terraform roots, all tests in the suite pass, and a
static linux/amd64 helper at `artifacts/erpbridge-sqlite-snapshot`.

**Warning.** The snapshot helper is an unchanged copy of the helper that
performed the earlier live upgrade. Do not rewrite it before a cutover. If you
rebuild it from new code, you remove the only part of this process that was
already verified in production.

---

## Phase 2: Provision the VM

Charges start at this point. This phase does not affect the running service.

1. Create `terraform-erpbridge/terraform.tfvars` (mode `0600`) from values that
   already exist in other roots: `subscription_id`, `tenant_id`,
   `operator_ssh_cidr`, `ssh_public_key_path`, `ssh_private_key_path`,
   `resource_group_name`, and a **globally unique** backup storage account name.
2. Review the plan.

```bash
make erpbridge-vm-plan
```

3. Before you apply the plan, verify that it only adds resources. Reject it if
   it deletes or replaces any resource.

```python
changes = [(c['address'], c['change']['actions'])
           for c in plan['resource_changes'] if c['change']['actions'] != ['no-op']]
assert not [a for a, acts in changes if 'delete' in acts], 'plan deletes resources'
```

4. Apply.

```bash
make erpbridge-vm-apply CONFIRM=yes
```

**Warning.** `Standard_B2ats_v2` provides 2 vCPU and 1 GiB RAM and passed this
workload. However, capacity under load was never measured. A 4-vCPU fallback
would use the full regional vCPU quota. A larger VM needs a quota increase.

**Warning.** A 1 GiB Standard SSD data disk is not a valid Azure configuration.
Standard SSD starts at 4 GiB. This migration stores SQLite on the 32 GiB OS disk
instead, and sets `delete_os_disk_on_deletion = false` so an accidental VM
deletion retains it.

---

## Phase 3: Reachability and host key

```bash
python3 scripts/erpbridge-vm.py status --accept-host-key --vm-tf-dir terraform-erpbridge
```

Expected on the first run: three host key fingerprints, then the VM power and
service state.

**Warning: this is the most likely first failure.** The NSG allows SSH only from
`operator_ssh_cidr`. If your public address changed, the NSG filters port 22.
The port check times out, and the tool reports `Could not read the VM host key`.
Correct the CIDR, re-apply, and try again:

```bash
curl -s https://api.ipify.org                     # your current address
# update operator_ssh_cidr in terraform-erpbridge/terraform.tfvars, then apply
```

**Warning.** `--accept-host-key` pins the key on first use. Before you continue,
verify the printed fingerprints out of band. If the key changes later, the tool
refuses to connect. This is the intended behaviour.

---

## Phase 4: Bootstrap the runtime

```bash
python3 scripts/erpbridge-vm.py bootstrap --confirm --vm-tf-dir terraform-erpbridge
python3 scripts/erpbridge-vm.py status --vm-tf-dir terraform-erpbridge
```

Expected after bootstrap:

```text
service=inactive      # the server has no database yet, so the guard blocks it
proxy=active          # Caddy serves maintenance (503) on every path
runtime_env=absent
database=absent
```

The bootstrap uploads nine assets and pulls the pinned image. It records the
container user and the filesystem identity, then installs the unit. It enables
the service without starting it, enables the maintenance proxy, and enables the
daily backup timer.

**Warning.** Remote commands need `sudo -n` because the login user is not root.
If you extend the tooling, add the privilege prefix. Without it, you will see
generic `Remote command failed` messages with no detail.

---

## Phase 5: Move the runtime environment

```bash
python3 scripts/erpbridge-vm.py capture-config
python3 scripts/erpbridge-vm.py install-runtime-env --confirm --vm-tf-dir terraform-erpbridge
```

`capture-config` reads the live serving revision's environment and resolves each
secret reference. It writes `artifacts/erpbridge-vm-migration/runtime.env.staged`
at mode `0600` and prints **setting names only**.

`install-runtime-env` validates syntax and streams the file over SSH standard
input. It installs the file with mode `0600`, owned by `root`, and verifies that
the remote setting count matches.

**Warning.** The ACA API field is `properties.configuration.activeRevisionsMode`
with value `Single`. An earlier version read `properties.revisionMode`, which
does not exist, so every preflight failed.

**Warning.** Never pass a secret as a command argument. Use standard input only.

**Warning.** `capture-config` overwrites the staged file with values from the ACA
app. If you later add a key directly on the VM and then re-capture, the next
install removes that key without a warning. Either keep both sources
synchronized, or stop capturing after you retire the ACA app.

---

## Phase 6: Take the live snapshot

The snapshot runs inside the running replica, which has no `sqlite3` and no
Python. The tooling transfers the helper over an acknowledged terminal channel
and verifies its checksum before it runs.

**Warning: two hard prerequisites.**

1. **Interpreter.** The WebSocket client comes from the Azure CLI package, not
   from PyPI. Use the interpreter the CLI ships, or the import fails:
   ```bash
   AZ_PY=/opt/azure-cli/bin/python
   ```
2. **No `az containerapp exec`.** That path requires a real terminal and fails
   under automation with `termios.error: Inappropriate ioctl for device`.
   The acknowledged session exists precisely because of this.

Pre-push the helper once to a fixed path. This saves more time than any other
step in the process: it reduces the cutover snapshot time from **5m08s to 18 seconds**.

```bash
REV=$(az containerapp show -g erpbridge-hq -n erpbridge-server \
      --query properties.latestReadyRevisionName -o tsv --only-show-errors)
REP=$(az containerapp replica list -g erpbridge-hq -n erpbridge-server \
      --revision "$REV" --query '[0].name' -o tsv --only-show-errors)

"$AZ_PY" demo/erpbridge/aca-session.py \
  --revision "$REV" --replica "$REP" --container erpbridge \
  --helper /tmp/helper-stripped \
  --remote-dir /tmp/erpbridge-cutover --keep-remote \
  --snapshot-out artifacts/erpbridge-vm-migration/pre-cutover-snapshot.db
```

Verify the snapshot locally before you use it:

```python
import hashlib, sqlite3
con = sqlite3.connect('artifacts/erpbridge-vm-migration/pre-cutover-snapshot.db')
assert con.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
tables = sorted(r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'"))
counts = {t: con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
```

Record the counts. Compare them again after the cutover snapshot.

---

## Phase 7: Rehearse the restore on the VM

This phase does not affect public traffic. It verifies that the data restores
correctly before any downtime.

```bash
SHA=$(sha256sum artifacts/erpbridge-vm-migration/pre-cutover-snapshot.db | cut -d' ' -f1)

python3 scripts/erpbridge-vm.py stage \
  --file artifacts/erpbridge-vm-migration/pre-cutover-snapshot.db \
  --remote-name snapshot.db --confirm --vm-tf-dir terraform-erpbridge

python3 scripts/erpbridge-vm.py stop --confirm --vm-tf-dir terraform-erpbridge

python3 scripts/erpbridge-vm.py restore \
  --backup-id snapshot.db --source-file /var/lib/erpbridge/staging/snapshot.db \
  --expect-sha256 "$SHA" --confirm --vm-tf-dir terraform-erpbridge

python3 scripts/erpbridge-vm.py start --confirm --vm-tf-dir terraform-erpbridge
```

Expected restore output: matching table counts, ownership set to the container
uid, and `No stale sidecar files remain`.

**Warning.** `restore` refuses to run while the service is active, and rejects a
checksum or manifest mismatch. It also moves any existing `-wal`, `-shm`, and
`-journal` files aside first. Installing the main database beside a stale
write-ahead log lets SQLite replay an incompatible log over restored data.

---

## Phase 8: Cutover

**Downtime starts here.** Record the time.

### 8a. Fence the Container App

Create `terraform/zz-erpbridge-migration.auto.tfvars.json`:

```json
{ "erpbridge_source_fenced": true }
```

**Warning.** Export the Cloudflare token first. Without it, you cannot trust the
plan. An unauthenticated plan prints `400 Bad Request ... Missing X-Auth-Key`
and **can still be saved to disk**. Never apply it.

```bash
export CLOUDFLARE_API_TOKEN="$(tr -d '\n\r' < secrets/cloudflare.token)"
terraform -chdir=terraform plan -out=tfplan-cutover -no-color
```

Verify exactly one change: the bridge ingress. Verify that the plan deletes
nothing. Then apply it.

Fencing is an application-scope change. It creates **no new revision**, and the
serving replica keeps its state.

### 8b. Fresh snapshot, and prove quiescence

```bash
"$AZ_PY" demo/erpbridge/aca-session.py \
  --revision "$REV" --replica "$REP" --container erpbridge \
  --helper /tmp/helper-stripped --remote-dir /tmp/erpbridge-cutover \
  --reuse-helper --keep-remote \
  --snapshot-out artifacts/erpbridge-vm-migration/cutover-snapshot.db
```

With the helper pre-pushed, this takes seconds. **Compare its checksum with the
pre-freeze snapshot.** An identical checksum is evidence that no write occurred
during the transition. If the checksum differs, compare the table counts and
explain the difference before you continue.

### 8c. Switch the record

Add to the same auto-variable file:

```json
{ "erpbridge_source_fenced": true,
  "erpbridge_hosting": "vm",
  "erpbridge_vm_ipv4": "<vm public ip>" }
```

Create the plan. Then verify that the changes are **bridge-only**, that MockERP
is unchanged, and that the live A record changes only in type and content.

**Warning.** Changing a DNS record's type is not atomic. Expect `delete` then
`create` for `cloudflare_dns_record.app_cname["erpbridge"]`. For a few seconds,
the name will not exist. Reject the plan if it changes the MockERP record or the
ACA environment.

### 8d. Restore the final snapshot and bring up TLS

```bash
python3 scripts/erpbridge-vm.py stop --confirm --vm-tf-dir terraform-erpbridge
python3 scripts/erpbridge-vm.py restore --backup-id snapshot.db \
  --source-file /var/lib/erpbridge/staging/snapshot.db --expect-sha256 "$SHA" \
  --confirm --vm-tf-dir terraform-erpbridge
python3 scripts/erpbridge-vm.py start --confirm --vm-tf-dir terraform-erpbridge
```

Wait for a valid certificate. Expect **503** with working TLS once DNS resolves:

```bash
for i in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 \
         https://erpbridge.nimendra.online/mcp/health || echo tls-error)
  [ "$code" = "503" ] && break
  sleep 10
done
```

Caddy obtained its certificate **10 seconds** after the record resolved.

### 8e. Promote to serving

```bash
python3 scripts/erpbridge-vm.py start --confirm --serving --vm-tf-dir terraform-erpbridge
```

### 8f. Verify publicly, then back up immediately

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://erpbridge.nimendra.online/mcp/health
python3 scripts/erpbridge-vm.py status --vm-tf-dir terraform-erpbridge
python3 scripts/erpbridge-vm.py backup --vm-tf-dir terraform-erpbridge
```

**Warning.** When you count discovered tools, parse **all** `data:` lines of the
streamed response. The first line is often a server notification. If you read
only that line, a healthy registry can appear empty.

**Warning.** `backup.py` must receive the real storage endpoint. A hardcoded
account name points at a host that does not exist. The upload then fails at DNS
with `The backup upload did not complete`. The tooling derives the endpoint
from the applied root.

---

## Phase 9: After the cutover

1. **Stop paying for the fenced app** without removing your rollback path:
   set `erpbridge_min_replicas = 0`, apply, then deactivate the previous revision.
   The new revision reports `replicas: 0`. Confirm with
   `terraform -chdir=terraform plan` that there is no drift.
2. **Reboot the VM once, deliberately**, and confirm the service returns without
   a restore. Until this is done, persistence is unproven.
3. **Run a load test** if confidence in single-host sizing matters.
4. **Observe for 24 hours**, then retire the app using
   `.agents/plans/Plan-ERPBridge-ACA-Retirement.md`.

Never run `terraform destroy` on the ACA root. It holds the live A record and
MockERP. Destroying it deletes the migrated service's DNS record and takes the
service down.

---

## Rollback

During the observation window, use these configuration changes to roll back:

1. In `terraform/zz-erpbridge-migration.auto.tfvars.json` set
   `erpbridge_source_fenced = false`, `erpbridge_hosting = "aca"`, and raise
   `erpbridge_min_replicas` back to `1`.
2. Plan, then apply. Expect the same bridge-only DNS replacement.
3. The ACA replica holds no usable database. Before you expect it to serve,
   restore a verified snapshot through the revision's startup gate.

**Warning.** Treat the source `/tmp` database as lost as soon as its process is
deactivated. After the VM has accepted writes, do not roll back to a stale
snapshot. Capture the VM's current state first. A restore can also undo a token
revocation.

---

## Secrets

| Layer | Location | Protection |
| --- | --- | --- |
| Application environment | `/etc/erpbridge/runtime.env` on the VM | root, mode `0600`, parent `0700` |
| Service wiring | `/etc/erpbridge/service.env` | image digest, data dir, port |
| Client MCP tokens | SQLite `api_tokens` | hashes, roles, expiry, revocation |
| Backup upload | VM managed identity | no stored key, IMDS token |

The container receives the application environment with
`--env-file /etc/erpbridge/runtime.env`. Only the **path** appears in the process
arguments.

The startup guard requires exactly three settings: `API_AUTH_TOKEN`,
`ERP_PRIMARY_KEY`, and `DATABASE_PATH=/data/erpbridge.db`. Other keys are
optional, so a wrong optional value cannot block startup.

### Adding or changing a key

1. Edit `artifacts/erpbridge-vm-migration/runtime.env.staged` (mode `0600`).
2. `python3 scripts/erpbridge-vm.py install-runtime-env --confirm`
3. `python3 scripts/erpbridge-vm.py restart --confirm`

`restart` runs the startup guard first. If the guard fails, it leaves a running
service unchanged. **A restart is mandatory:** `systemctl start` does nothing to
a service that is already running. The service would keep the old environment
without a warning.

Verify that a key reached the container without printing its value:

```bash
python3 scripts/erpbridge-vm.py status --expect-key MY_NEW_KEY --vm-tf-dir terraform-erpbridge
```

---

## Timings observed

| Step | Duration |
| --- | --- |
| Helper push to a fixed path | 5m08s |
| Post-freeze snapshot with the helper reused | 18s |
| Certificate issuance after DNS resolved | 10s |
| **Total service downtime** | **4m44s** |

## Failure checklist

If a failure occurs, use this checklist first.

| Symptom | Cause | Fix |
| --- | --- | --- |
| `Could not read the VM host key` | Operator CIDR is stale, so SSH is filtered | Update `operator_ssh_cidr`, re-apply |
| `Must run as root` or generic remote failure | Missing `sudo -n` on a privileged remote command | Add the privilege prefix |
| `The specified disk size 1 GB is smaller than ...` | Standard SSD has no tier below 4 GiB | Use the OS disk, or 4 GiB |
| `400 Bad Request ... Missing X-Auth-Key` | `CLOUDFLARE_API_TOKEN` not exported | Export it, re-plan, discard the saved plan |
| `termios.error: Inappropriate ioctl for device` | `az containerapp exec` without a TTY | Use `demo/erpbridge/aca-session.py` |
| `Run this file with the interpreter the Azure CLI ships` | Wrong Python | Use `/opt/azure-cli/bin/python` |
| `activeRevisionsMode` check fails | Reading `properties.revisionMode` | Read `properties.configuration.activeRevisionsMode` |
| `The backup upload did not complete` | Wrong storage account in the endpoint | Derive it from the applied root |
| `destination must not exist` | A restore target already exists | Move it aside; never overwrite |
| Tools listed as 0 | Parsed only the first `data:` line | Parse the response payload, not the notification |
| Env change has no effect | Service was started, not restarted | Use `restart --confirm` |
| `install ... are the same file` | `install` with identical source and destination | Use `chmod`, or a `.new` then rename |

## Related documents

- `.agents/plans/Plan-ERPBridge-VM-Migration.md` — the task plan and its records.
- `.agents/plans/Plan-ERPBridge-ACA-Retirement.md` — retiring the fenced app.
- `docs/erpbridge-vm-migration.md` — reference for the current environment.
