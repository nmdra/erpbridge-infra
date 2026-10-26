# ERPBridge version upgrade runbook

> **Historical POC documentation.** The recorded deployments were torn down in
> October 2026. Status, tool counts, approvals, and acceptance results below
> describe dated work, not current services or permission to deploy again.
> Check the versioned configuration and obtain fresh approval before cloud changes.

## Purpose

This runbook tells you how to move the live ERPBridge Container App to a new image. The runbook
keeps the existing single-replica Azure Container Apps (ACA) setup, the SQLite file at
`/tmp/erpbridge.db`, and the EmptyDir volume `/data`. The runbook also tells you how to keep the
token records, the `erpnext` API registration, and the application configuration.

## Scope and constraints

- First requested image: `ghcr.io/nmdra/erpbridge-server:0.5.0-alpha.3`.
- Resolved immutable image for that request:
  `ghcr.io/nmdra/erpbridge-server@sha256:b91f2689d0cbc2d8d92084237e943c3b5cc9edb87a9d9108b7eceb5bf955c15f`.
- Keep the existing ACA app, single-revision mode, one replica per revision, CPU and memory values,
  networking, domains, secrets, and `/tmp/erpbridge.db`.
- Keep `/data` as an EmptyDir volume.
- Do not create Azure Files, a new database service, or a new host.
- Do not replace the installed CLI.
- Do not publish the assignment tools.
- Do not rotate credentials.
- Do not change the API registration.

## Tool names

The server publishes each stored revision twice:

- The unqualified name, for example `get_item`. This name always resolves to the serving revision.
- The revision-qualified alias, for example `get_item.rev_MS4wLjA`. `MS4wLjA` is the base64url form of
  `1.0.0`. This alias pins that exact revision.

If you clear the registry and apply the same revisions again, the server publishes both forms again.

## Prerequisites

1. Obtain an explicit acceptance of the restore gate and of its future cold-start behaviour. Refer
   to [Required restore gate](#required-restore-gate).
2. Make sure that you can open a terminal session to the exact running replica. The restore step
   needs this access. If the access is not available, stop and inspect the state. Do not bypass the
   restore gate.

## Target images

| Request | Tag | Immutable digest |
| --- | --- | --- |
| First upgrade | `ghcr.io/nmdra/erpbridge-server:0.5.0-alpha.3` | `ghcr.io/nmdra/erpbridge-server@sha256:b91f2689d0cbc2d8d92084237e943c3b5cc9edb87a9d9108b7eceb5bf955c15f` |
| Current live image | `v0.5.0-alpha.5` | `ghcr.io/nmdra/erpbridge-server@sha256:2ae683b46d4b7cacd4e23f058cbad8768c520ae73a66619e871ed101a09f0575` |

## Required restore gate

The new revision starts a small shell gate before the server. The gate waits for a nonempty SQLite
file. The gate does not create an empty registry and does not contain an embedded database or a
restore seed.

The operator sends the compressed verified snapshot into a private temporary file in the exact new
running replica. Verify the SHA-256 value and mode `0600`. Then rename the file atomically to
`/tmp/erpbridge.db`. Only then does the gate execute the server.

A TCP startup probe allows 240 attempts at one-second intervals, with a three-second timeout. The
existing readiness and liveness checks remain. These values give the operator a bounded restore
window before normal liveness processing.

**Storage remains ephemeral.** A future replacement replica with no database waits for another
authorized restore and remains unready. It does not automatically replay an old snapshot and it does
not resurrect revoked tokens. This is not persistent storage and it is not a zero-downtime
guarantee. MCP clients may need to reconnect after the revision change.

## Procedure

Do the steps in this order. Complete each step before you start the next step.

1. **Freeze the administration.**
   1. Pause the registry administration and the token administration.
   2. Keep the freeze in force through the final readback.
   3. Avoid business calls during the traffic transition. In single revision mode, the old replica
      and the new replica can overlap temporarily. Their SQLite copies are separate.

2. **Compare the live state.**
   1. Compare the live logical database contents, the image, and the configuration with the verified
      backup.
   2. If you find a difference, stop and take a fresh consistent snapshot. A raw observation that
      you use for this comparison is not a new consistent snapshot.

3. **Update the app template only.**
   1. Set the pinned image.
   2. Set the startup gate.
   3. Set the bounded startup probe.
   4. Preserve the secret references, the ingress, the roles, the mounts, and the scaling.
   5. Do not apply unrelated Terraform changes.

4. **Locate the new revision.**
   1. Find the exact new revision and its running replica before you restore the database.
   2. Never overwrite a nonempty database. Never overwrite an already-open database.

5. **Restore the snapshot into the new replica.**
   1. Upload the compressed payload of approximately 10 KB with acknowledgements.
   2. Verify the expected SHA-256 value.
   3. Publish the file with an atomic rename on the same filesystem.

6. **Wait for the new revision.**
   1. Wait until the new revision is ready.

7. **Verify the upgrade.** Refer to [Verify the upgrade](#verify-the-upgrade).

8. **Pin the effective values.**
   1. Write the effective image and the restore-gate flag to the protected ignored Terraform
      auto-variable file.
   2. Keep the opt-in Terraform startup gate and probe aligned with the live command. Fresh
      deployments keep the existing default, because the flag defaults to `false`.

9. **Resume the administration.**
   1. Resume the administration after the final readback only.
   2. Do not claim durability for `/tmp` or for EmptyDir.

## Verify the upgrade

Verify all items in this table after the new revision is ready.

| Item | Expected result |
| --- | --- |
| Health endpoint | HTTP 200 |
| Anonymous tool listing | Denied |
| Admin authentication | Passes |
| Stored revision identities and specifications | All revisions and all business names |
| Token records | All records, unchanged |
| MCP discovery with an existing token | All business names |
| Native identity read | Matches the independent ERPNext REST response |
| Configuration, secrets, ingress, scale, revision mode, API registration | Unchanged |

Report the native identity read separately. That single read is not a full-catalogue semantic
acceptance.

Do not claim a full-catalogue execution test from these items. No uninterrupted all-tool execution
scan exists.

## Rollback

Use this procedure while the administration freeze is in force.

1. Create a **fresh revision** with the original pinned image.
2. Apply the same startup gate.
3. Restore the matching pre-upgrade database explicitly.
4. Verify the restore checksum before you publish the database into the empty database path.

Do not do these actions:

- Do not activate an old revision and expect its ephemeral database to remain.
- Do not reuse a migrated open database.
- Do not replace a live SQLite file in place.

After the registry and token changes resume, a restore of the old snapshot can undo those changes or
resurrect revoked tokens. Capture fresh state and obtain an operator review before a later rollback.

The operator-owned `get_company@1.0.1` revision was reviewed and deleted by owner
decision on 2026-10-16. The registry now holds exactly one serving revision per
tool, so there is no operator revision to preserve or adopt. Restore it from the
`erpbridge-demo` archive export if that decision is ever reversed.

## Records

### alpha.5 upgrade (2026-10-15)

The live app runs `ghcr.io/nmdra/erpbridge-server@sha256:2ae683b46d4b7cacd4e23f058cbad8768c520ae73a66619e871ed101a09f0575`
(`v0.5.0-alpha.5`) on revision `erpbridge-server--schema-a5-1016043623`.

The requested order was: update the app first, then restore the state. The patch created the new
revision behind the restore gate, so the new revision waited while the alpha.3 revision kept
serving. Then the operator took a read-only snapshot of the serving revision's database. The
snapshot command was `VACUUM INTO` through the verified helper. The operator uploaded the snapshot
into the waiting replica, verified the SHA-256 value, and published the file with an atomic rename
before the server started.

Results after startup:

- Health was 200.
- The anonymous tool listing was denied.
- The registry held 45 revisions and 45 names.
- Discovery returned 45 names. No `.rev_` alias was present.
- The pinned name `get_current_user.rev_MS4wLjA` still executed.
- The live database was identical to the snapshot for `tools` (45 rows) and `api_tokens` (3 rows).
- The configuration, the secrets, the ingress, the scale, the revision mode, and the `erpnext` API
  registration were unchanged.
- One ERP read occurred. That read was the pinned identity check. No ERP write occurred.

Evidence: `artifacts/erpbridge-alpha5-upgrade-prep/` (`schema-a5-acceptance.json`,
`post-start-dbcheck.json`, `live-snapshot.db`, the patch payload, and the failure logs, if any).

Terraform is pinned to the new digest with `erpbridge_require_database_restore = true`. The rollback
is a fresh revision with the alpha.3 digest plus an explicit restore of the same snapshot.

### alpha.3 upgrade (2026-10-15)

The approved rollout completed on 2026-10-15. Revision `erpbridge-server--schema-a3-1015213500` ran
the pinned alpha.3 image. The results were:

- Health returned 200.
- All 46 revisions and all 45 business names were retained.
- All three token records were retained.
- One existing client token authenticated and discovered all business names plus the revision
  aliases.
- A harmless native identity read matched the independent ERPNext REST response.
- The resolved Azure secret values were unchanged.
- No ERP write occurred.
- No assignment tool was published.

Private acceptance evidence: `artifacts/erpbridge-alpha3-upgrade-prep/schema-a3-acceptance.json`.

The Terraform restore gate is enabled. The image is pinned through the protected ignored
`terraform/zz-erpbridge-upgrade.auto.tfvars.json`. Review all unrelated pending changes before a
future Terraform apply.

### Registry re-apply (2026-10-15)

The operator cleared the live registry and rebuilt it from a fresh definitions export:

1. The preflight passed. The context was `erpbridge-demo`. Health was 200. One API was registered
   (`erpnext`). No legacy global registry was present.
2. A fresh rollback export was captured at `tools-export-20261015T215737.780606Z`. The export held
   46 revisions and 45 unique names. The hashes were verified.
3. All 46 stored revisions were hard-deleted with
   `bridgectl tool delete <name> <version> --hard --yes`. The registry read back empty.
4. All 46 manifests were re-applied with `bridgectl tool apply -f <file>`, one file per call. The
   registry reconciled to 46 revisions and 45 names.
5. Verification passed: health 200, anonymous tool listing denied, admin authentication passed, a
   held client token discovered 45 unqualified names plus 46 `name.rev_<base64url(version)>` aliases,
   and one native identity read was served. The API registration and all client tokens were
   untouched. No ERP write occurred.

One benign drift exists. `get_company@1.0.0` reports `spec.cache.ttlSeconds` 30 instead of 300, and
`spec.description.whenNotToUse` `[]` instead of `null`. Every other field matches the export,
including the endpoint, the mapping, the schema, the security, and `execution.approvedOrigins`. The
drift affects the read-cache duration only.

### Clean re-apply (2026-10-15, second pass)

The operator cleared the whole registry again and rebuilt it with **one revision per tool name**, to
remove the only duplicate revision:

- 46 revisions were hard-deleted. The registry read back empty.
- 45 manifests were re-applied: `get_company@1.0.1` (the operator revision) plus the newest revision
  of every other name. `get_company@1.0.0` was dropped.
- The readback was 45 revisions and 45 names. No name holds more than one revision.
- Discovery returned 90 names: 45 unqualified functions plus 45 `name.rev_<base64url(version)>`
  aliases.
- Health was 200. The anonymous tool listing was denied. The admin readback matched the clean set. A
  held client token discovered all names. One native identity read was served. The API registration
  and the tokens were untouched. No ERP write occurred.

Pinned clients that call `get_company.rev_MS4wLjA` (version 1.0.0) now fail. The unqualified
`get_company` serves the operator revision `1.0.1`. The export at
`tools-export-20261015T220630.983935Z` holds all 46 revisions and restores the previous state.

Every stored revision produces an alias. Therefore, a registry cannot publish the 45 functions
alone. Filter the aliases in the client, for example with
`--exclude-tools 'mcp__erpbridge__*.rev_*'`. Evidence: `artifacts/erpbridge-clean-reapply/`.

### Registry cleared (2026-10-15, on request)

All 45 stored revisions were hard-deleted with
`bridgectl tool delete <name> <version> --hard --yes`. The registry reported 0 revisions. MCP
discovery returned 0 business tools. Health was 200. The anonymous tool listing stayed denied. The
held client token still authenticated. The `erpnext` API registration and all token records were
untouched. No ERP call was made.

Rollback export: `tools-export-20261015T221725.721002Z` (45 revisions). Restore the tools with one
`bridgectl --context erpbridge-demo tool apply -f <file>` command per revision. The earlier export
`tools-export-20261015T220630.983935Z` holds all 46 revisions, including `get_company@1.0.0`.
Evidence: `artifacts/erpbridge-clear-all/`.

An empty registry means that every MCP client sees no ERP functions until the tools are applied
again. The `.rev_` aliases return with any re-apply. Only a server change can stop the publication
of the aliases.

### Verified local evidence

The pinned alpha.3 image was tested with a copy of the verified backup on an isolated Docker internal
network:

- The health endpoint did not become ready before the database was explicitly restored.
- All 46 stored revisions and all 45 business tool names survived the migration.
- The expected migration added `execution.approvedOrigins` for `https://erpnext.nimendra.online`. No
  other executable specification differences were accepted.
- All three token records remained unchanged. One locally held existing token authenticated
  successfully.
- Discovery returned 45 business names plus 46 exact-revision aliases, 91 entries in total. These are
  not 91 different business operations.
- Eight invalid-input cases were rejected through both MCP and direct invocation, with a
  sandbox-only fixture. No live fixture was published and no ERP write was invoked.
- One harmless native identity read (`get_current_user`) matched the independent ERPNext REST
  response after the upgrade. That is one read, not a full-catalogue execution proof.
- The old image and the pre-upgrade backup also passed the earlier local recovery test.

Private evidence is under `artifacts/erpbridge-alpha3-upgrade-prep/`. The source backup and its
checksummed archive remain unchanged.

## Client CLI note

The installed CLI at `/home/nimendra/.local/bin/bridgectl` reports `Version: 0.5.0-alpha.6`, is
22,560,930 bytes, and has SHA-256
`e5e494d27e24ed58a497943d06bd3c434d28af2717e1aa0394b8ede216573a43`. It came from the release asset
`bridgectl_Linux_x86_64.tar.gz` of `v0.5.0-alpha.6`, checked against `checksums.txt`. The previous
`0.5.0-alpha.3` binary is kept at `artifacts/bridgectl-install/bridgectl-0.5.0-alpha.3.bak`.

The `erpbridge-demo` context stores `api-token: ${BRIDGE_API_TOKEN}`. Export `BRIDGE_API_TOKEN`, or
pass `--token`, before you use the CLI against the live server:

- `BRIDGE_API_TOKEN=<token> bridgectl --context erpbridge-demo tool get get_company`
- `bridgectl --context erpbridge-demo --token <token> tool get get_company`

Without one of those two options, the command sends no usable bearer token and fails with
`json: cannot unmarshal object into Go value of type []*mcp.Tool`, because the server returns an
error envelope instead of a tool list.

The alpha.6 CLI generates read tools with `cache.ttlSeconds` 60 and keeps write tools at
`cache.enabled: false`. Install record: `artifacts/bridgectl-install/install-record.json`.
