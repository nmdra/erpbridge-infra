# ERPBridge monitoring operations

> **Historical POC documentation.** The recorded deployments were torn down in
> October 2026. Status, tool counts, approvals, and acceptance results below
> describe dated work, not current services or permission to deploy again.
> Check the versioned configuration and obtain fresh approval before cloud changes.

## Execution status

Implementation and gated deployment were approved on 2026-10-18. The user approved:

- Adoption of `VMI-ActionGroup-erpbridge-vm` and `VM Availability - erpbridge-vm`, with no other Terraform owner.
- Reuse of the existing email receiver without publishing its name or address.
- The scoped AzAPI provider addition, new monitoring resources, managed boot diagnostics, and monitoring-only installation through Azure Run Command.
- Deployment of the planned fixed-size resources before the cost review. The earlier USD 10/month ceiling is now a reporting target, not a deployment gate or an Azure spending cap.

No application restart, deliberate outage, synthetic rule mutation, or VM replacement is approved. Configuration and acceptance are separate. Do not report email delivery, KQL execution, or unattended backup observation as passed until measured.

## Scope and ownership

The isolated `terraform-erpbridge/` root owns the dedicated ERPBridge VM and its approved monitoring resources. It does not manage ERPNext, MockERP, Cloudflare DNS, the shared ACA environment, or the existing shared Log Analytics workspace.

Reuse the existing `AzureMonitorLinuxAgent` installation. Preserve the existing `msvmi-centralindia-erpbridge-vm` DCR and `VirtualMachineInsightsMetricsExtension` association. Their `Microsoft-OtelPerfMetrics` stream targets an Azure Monitor workspace, not a Log Analytics workspace. Do not import or alter these resources.

Local state inventory found no existing owner for the action group or availability alert. The operator explicitly confirmed adoption. Protected Terraform state can contain receiver PII. Protect all state, saved plans, private variable files, and plan JSON with mode 0600 and a private parent directory. Never print a complete plan JSON or action-group response.

## Live preflight evidence

Read-only checks on 2026-10-18 found:

| Check | Result |
| --- | --- |
| `Microsoft.Insights`, `Microsoft.OperationalInsights`, `Microsoft.App` | Registered |
| Existing action group | Enabled, one email receiver, common alert schema enabled |
| Existing availability alert | Enabled, five-minute evaluation/window, severity 3 before adoption |
| Existing AMA extension | Provisioning succeeded, handler version 1.0 |
| Available Memory Bytes | Average aggregation, Bytes; recent sample 375,521,280 bytes |
| Percentage CPU | Average aggregation, Percent; recent sample 0.845% |
| VmAvailabilityMetric | Average aggregation, Count; recent sample 1.0 |

These metric samples were timestamped 2026-10-18T10:34:00Z. They are not current health claims. The original VM freeze remains unexplained. Low memory before the freeze does not establish an ERPBridge leak.

## Alert policy

- Independent Standard HTTPS test: `/mcp/health`, exact 200, valid TLS, no redirects or dependent requests, three locations every 900 seconds, 30-second timeout and retries.
- External incident: two failed locations, five-minute evaluation, 30-minute window. Expected sustained-failure detection is approximately 15–35 minutes, not a guarantee.
- Availability: Average below 1 over five minutes.
- Memory warning: Average below 128 MiB over 15 minutes. Critical: below 64 MiB over five minutes.
- CPU: Average above 85% over 15 minutes.
- Combined guest incident: no sample for ten minutes, no sample ever, two consecutive service/backend or disk faults, failed/invalid/missing backup status, or backup success older than 26 hours.
- Disk: space/inodes above 85%, or less than 1 GiB free.
- VM-scoped Resource Health and administrative restart/power-off/deallocate events provide context.

The combined guest query seeds the expected VM and left-joins the latest event. Missing input must produce a failure row. Event time maps to `TimeGenerated`; delayed replay must not recover a no-data incident. One incident can remain active as reasons change. Use the current-reason query/workbook to inspect all reasons.

The guest alert starts disabled only for commissioning. Enable it immediately after fresh ingestion and live query verification. Do not use this switch as a permanent mute. A missing guest sample indicates loss of observation, not proof that the VM is off.

## Collector and installation

The standard-library collector samples numeric operational state once per minute. It does not read runtime environment values, database rows, raw errors, process arguments, full Docker inspection, or HTTP response bodies. Unsupported counters have explicit availability flags and unknown values.

Limits: 15-second oneshot, 64 MiB memory limit, no overlapping instances, low CPU/IO priority, at most 2 KiB per JSONL record, three daily files. Persistent journald is bounded to 64 MiB and seven days. No unrestricted journal shipping is added.

Use only the monitoring-specific installer:

```bash
make erpbridge-install-monitoring CONFIRM=yes
```

This selects the approved Azure Run Command channel. Do not use the runtime `bootstrap` command for monitoring. Bootstrap has a separate persisted-maintenance Caddyfile defect. Do not bypass the SSH-key access restriction.

The installer must preserve Caddy configuration hashes, workload units, database/runtime.env metadata, and backup wiring. It must not restart ERPBridge, reload Caddy, modify application settings, or reboot the guest. Repeat installation must remain safe.

## Deployment procedure

1. Initialize using the root's checked-in provider lock. Keep AzureRM at 5.2.0.
2. Prepare a private variable file containing monitoring enablement, the confirmed existing receiver object, and ownership confirmation. Leave the guest alert disabled for initial commissioning.
3. Import only `azurerm_monitor_action_group.monitoring[0]` and `azurerm_monitor_metric_alert.vm["availability"]` using their exact existing resource IDs. Review state ownership before each import.
4. Save the plan to a protected path. Review changes using a redacted summary. Reject any delete/replacement or changes to disk, public IP, NSG, custom data, identity, application, or another root. Boot diagnostics must update in place.
5. Apply only that saved, reviewed plan. Install monitoring assets separately.
6. Verify public 200 and anonymous MCP 401, fresh guest ingestion, collector/AMA overhead, and actual KQL fixture behavior.
7. Enable the combined guest alert through a second reviewed plan after ingestion is proven.
8. Test the action group and obtain recipient confirmation. API success alone does not prove delivery. Obtain separate approval before synthetic rule tests, deliberate outages, or restarts.

Actual fired/resolved rule emails, action-group delivery tests, offline predicates, and live KQL fixture results are distinct evidence. Record them separately.

## Cost review after deployment

The user deferred the complete estimate until after deployment. Record actual usage daily during the first week. Do not assume subscription-wide free allowances remain available.

| Component | Known basis | Remaining measurement |
| --- | --- | --- |
| Standard web test | USD 0.0007/execution; 3 locations × 4/hour; approximately USD 6.25 per 31 days | Confirm current agreement and execution meter |
| Metric/availability alerts | Four VM series plus the web-test alert | Current alert pricing and free allowance |
| Guest log alert | One unsplit five-minute rule | Actual rule charge |
| Custom/synthetic logs | Collector raw target below 3 MiB/day | Billable ingestion overhead and retention |
| Existing OTel collection | Preserved unchanged | ERPBridge-attributable ingestion/query charge |
| Email and diagnostics | Existing receiver; managed diagnostics | Delivery volume and storage charges |

Report total with 20% margin on variable charges. USD 10 remains the comparison target. Azure budgets and ingestion caps are not hard spending caps. Never automatically stop ERPBridge to meet a cost target.

## Observation and rollback

Observe at least 24 hours before marking acceptance complete. Confirm one truly unattended scheduled backup, continuous fresh telemetry, public/MCP health, collector/AMA memory, alert noise, and current costs. Do not substitute a manually triggered backup for this evidence.

Maintenance suppression requires a defined end time and automatic expiry. Preserve independent public monitoring where possible and record the maintenance reason. Never leave the no-data alert suppressed indefinitely.

Rollback disables only the new collector timer, new alert rules, and new collector DCR association. Preserve the VM, database, backups, prior portal resources, existing AMA/OTel association, and shared workspaces. Review any resource removal separately. Never run root-wide `terraform destroy`.

## Acceptance record

### Deployed, 2026-10-18

- Imported the existing action group and VM availability rule into the approved addresses. Preserved the receiver unchanged.
- Applied the reviewed monitoring plan. Managed boot diagnostics was the only VM change. There were no VM replacements, disk/IP/NSG/custom_data changes, application restarts, or changes to another root.
- Ten rules are enabled: five metric/HTTPS rules, four Resource Health/administrative rules, and one combined guest rule.
- Installed the collector through Azure Run Command. Protected workload metadata was preserved. Logs are root:syslog with group-read access for AMA; private state remains mode 0700 and monitor.env mode 0600.
- Verified fresh custom-table ingestion. The live combined query returned no faults with a sample age of 86 seconds. An early measurement found 11 samples averaging 302 billed bytes, container memory at most 53,297,152 bytes and AMA service memory at most 47,558,656 bytes. These are short observations, not sizing or billing guarantees.
- Verified successful external samples from West Europe, Southeast Asia, and East US, with no observed failures in the initial sample set. The component-specific synthetic-location API confirmed all three selected location tags are not retired.
- Executed all 12 controlled KQL cases against Azure. All passed. Fixtures use query-local rows and do not write fake health events to the workspace.
- Azure rejected the initial KQL identifier `latest`; it was changed to `latestVmSamples`, retested against Azure, and deployed successfully.
- The direct Log Analytics endpoint failed local TLS-chain verification. No certificate check was disabled. Commissioning used the TLS-verified ARM query endpoint instead.
- Public health remained 200; anonymous MCP remained 401. Collector timer is active. An inactive oneshot between samples is expected.
- All 279 local Python tests passed. Isolated Terraform contracts passed for the three active roots, including 15 VM-root mocked cases. Formatting and systemd verification passed. LSP checked the changed source paths without primary errors; auxiliary checks retain two caller-guarded JSON parsing advisories and two existing unpinned CI action findings.
- The approved notification-only query retained production fault detection. It was removed through a reviewed Terraform plan to meet the operator's request to finish promptly. Production configuration was restored and a final Terraform plan returned no drift at **2026-10-18T11:29:39Z**.

### Remaining acceptance

The standalone action-group test API returned `Conflict: Free subscription not supported`. This does not prove that real alert email is unavailable. No recipient-confirmed delivery or actual fired/resolved pair was established before test cleanup. Do not claim notification delivery is proven.

Still pending: recipient-confirmed email delivery, a completed fired/resolved rule test if required, 24-hour observation, a genuinely unattended backup, and post-deployment cost review. Source changes remain uncommitted alongside pre-existing migration work; no unrelated changes were committed. Kuma deployment is paused at the user's request.
