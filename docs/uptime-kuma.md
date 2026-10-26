# Disposable Uptime Kuma dashboard

> **Historical POC documentation.** The recorded deployments were deleted in
> October 2026. The status, tool counts, approvals, and acceptance results below
> describe past work. They do not describe current services or give permission
> to deploy again. Before cloud changes, check the versioned configuration and
> get new approval.

This root is an isolated prototype, not a durable monitoring service. It does not
replace Azure Monitor. It uses the existing ACA Consumption environment through
data sources only. It does not own that environment or its workspace.

**Deployment status:** local implementation and offline tests do not prove live
Azure authentication, browser callbacks, route normalization, sizing or public
publication. Record those results separately before you enable anonymous paths.
The user waived the earlier USD 5/10 pre-deployment gates and requested cost
review after deployment. This does not authorize unrelated resources or resizing.
The local verifier and unit tests do not make cloud changes.

## Ownership and image evidence

`terraform-uptime/` owns only the `erpbridge-uptime` app, its auth policy, and its
new DNS/custom-domain/certificate binding. Resource-group and environment lookups
are data only. There is no storage account, durable database, registration,
workspace, extra VM, private endpoint or identity registration in this root.

Official Docker Hub registry API retrieval for `louislam/uptime-kuma:2.5.6`
returned this multi-platform index digest:

`sha256:9912da7d7d9b6ddc0c2d904beb84d24689c1ed9c33b81419eb9008663a92a329`

The index identifies this Linux/amd64 child manifest, pinned by the root:

`sha256:99286a05eb86191f3f1aab9b537950aa55b958e77d9f7a2385bc9332674e6e00`

Primary sources used during implementation:

- Registry manifest: `https://registry-1.docker.io/v2/louislam/uptime-kuma/manifests/2.5.6`
  using a public repository pull token from `https://auth.docker.io/token`.
- [Pinned release](https://github.com/louislam/uptime-kuma/releases/tag/2.5.6).
- [Database selection](https://github.com/louislam/uptime-kuma/blob/2.5.6/server/setup-database.js):
  `UPTIME_KUMA_DB_TYPE=sqlite` writes replica-local `db-config.json`.
- [Native root and entry-page behavior](https://github.com/louislam/uptime-kuma/blob/2.5.6/server/server.js).
- [Public routes](https://github.com/louislam/uptime-kuma/blob/2.5.6/server/routers/status-page-router.js).
- [Public data model](https://github.com/louislam/uptime-kuma/blob/2.5.6/server/model/status_page.js).
- [Public heartbeat serialization](https://github.com/louislam/uptime-kuma/blob/2.5.6/server/model/heartbeat.js).
- [Auth schema](https://learn.microsoft.com/en-us/azure/templates/microsoft.app/2025-07-01/containerapps/authconfigs).

The selected release uses root HTTP 302 for the unmapped entry page. ACA HTTP
probes accept successful 2xx/3xx responses. Startup, readiness and liveness check
`/` on port 3001. These process probes do not prove monitor configuration.
Verify runtime probe behavior during deployment. Allocation is
0.25 CPU/0.5 GiB, Single revision, min=max=1, native HTTP/WebSocket ingress, TLS
required. If sizing fails, stop for a decision. Do not silently resize or scale
to zero. There are no browser monitors, sidecar, Docker socket or notification delivery.

## Protected operator inputs

Prepare private files outside the repository with mode 0600 and a parent directory
with mode 0700. Use a protected `-var-file` path, not secret values in arguments.
Disable shell tracing and `TF_LOG`. Protect state, backups and saved plan files.
Terraform sensitive values can still enter state and provider requests. Do not
upload state, saved plans, cookies, recovery codes, screenshots containing account
identifiers or unredacted provider diagnostics.

Required Terraform variable names:

| Variable | Private value |
| --- | --- |
| `subscription_id` | Approved Azure subscription UUID |
| `tenant_id` | Exact tenant UUID, never `common` or `organizations` |
| `entra_client_id` | Approved Kuma-only application client UUID |
| `operator_object_ids` | Nonempty set of explicitly approved operator object UUIDs in that tenant |
| `entra_client_secret_file` | Absolute protected file containing only the approved client secret |

Defaults: `resource_group_name=erpbridge-hq`,
`container_app_environment_name=erpbridge-demo-aca-standard`. Privately confirm
Central India and Consumption, provider registration and operator permissions.
The root registers no resource providers and creates no Entra identities.
The existing provider lock is copied unchanged: AzureRM 5.2.0, AzAPI 2.12.0,
Cloudflare 5.24.0. No existing root is upgraded.

Cloudflare reads `CLOUDFLARE_API_TOKEN` from a protected operator environment. Use
only the approved zone's DNS edit permission. Supply `cloudflare_zone_id` privately
when you enable the domain. If the token is missing, stop deployment. Do not
broaden credentials or bypass DNS ownership review.

Register these exact web redirect URIs in the approved Entra app:

- `https://erpbridge-uptime.nimendra.online/.auth/login/aad/callback`
- `https://<azure_default_fqdn>/.auth/login/aad/callback`

Obtain the second host from Terraform output after internal creation. Do not
invent it. The root uses a tenant-specific v2 issuer, client-ID audience,
`allowedPrincipals.identities`, `Return401` and HTTPS. The client secret is stored
as ACA `entra-client-secret`; it is never output. Track its expiry privately.

## Three-stage commissioning

Every stage requires a reviewed plan from **this root only**. The plan must not
replace or modify the shared environment, workspace or existing application/DNS.
Do not use `-target` as the normal deployment workflow.

1. **Internal installation:** all five flags below remain false. Apply internal
   ingress plus the always-enabled auth policy. `excludedPaths=[]`. Inspect the
   persisted auth configuration privately. Set the exact Azure callback after
   reading `azure_default_fqdn`.
2. **External, authenticated-only commissioning:** after reviewing the installed
   control-plane policy, set `publish=true`, `auth_boundary_verified=true`.
   Keep `public_status_enabled=false`, `public_status_verified=false`. These
   settings create **no anonymous app exceptions**, including root/static files.
   `auth_boundary_verified` acknowledges installation/policy review only, not
   browser proof. Optionally set `enable_custom_domain=true` with the zone/token
   to establish the approved hostname before browser login. This adds only its
   DNS-only CNAME, `asuid` TXT, ACA custom domain, managed certificate and binding.
   DNS propagation uses the existing bounded repository script. Inspect binding
   changes separately from template changes that can erase the local database.
3. **Anonymous public status:** only after protected operator browser proof,
   denied unauthorized-user proof, completed monitors, UI page configuration and
   reviewed public data, set `public_status_enabled=true` and
   `public_status_verified=true`. Both `publish` and `auth_boundary_verified`
   must stay true. This enables only the ten reviewed read/static exclusions.
   These booleans are explicit human acknowledgements, not runtime evidence.
   If interactive login is unavailable, stop at stage 2 without anonymous paths.

`publish=false` is internal by default. There is no auth-disable input. Never
remove platform auth to work around setup, Socket.IO, callback or static failures.

Because `Return401` does not redirect visitors to login, operators must explicitly open:

`https://erpbridge-uptime.nimendra.online/.auth/login/aad?post_login_redirect_uri=/dashboard`

Use the equivalent default-host URL for that host's test. Complete Entra login,
then Kuma's own login/setup. Prove that the allowed operator can use authenticated
Socket.IO. Before you allow public paths, prove that another authenticated
identity is denied. Do not save either session in repository evidence.

## Supported UI reconstruction

The two JSON files under `config/` are specifications, **not import files**.
There is no administrative REST API client or unsupported Socket.IO automation.
After every reset, use the normal UI for this release:

1. Complete protected first-user setup. Use a unique strong administrator password
   and enable Kuma 2FA. Store recovery material privately. Do not disable Kuma
   login because Entra also exists. Database selection is SQLite from the env.
2. Set history retention to seven days in Settings. Leave notifications empty.
   Disable response-body storage on every monitor. Keep TLS verification on.
3. Create all ten catalogue rows with their exact labels and URLs. Select HTTP
   keyword type for the three keyword rows, ordinary HTTP for the other rows.
   Set the accepted status-code list to the single catalogue code, not a range.
   Set redirects to zero, GET, no authorization/headers/body, timeout 15 seconds,
   retries 2, retry interval 30 seconds, and each specified check interval.
   Do not enable upside-down mode. Check the pinned UI's retry behavior rather
   than promising a fixed detection time.
4. Keep `MockERP demo` disabled. Do not accept 403 as health or broaden ingress.
   Enabling it requires separate source-access approval and actual 200 evidence.
   The five private ERPBridge checks accept **only 401**. They check anonymous
   denial, not service health. Do not copy ERP/API credentials into Kuma.
5. Create status page slug `erpbridge`. Add the four public rows only in catalogue
   group order: Core platform, ERP demo, Project resources. Core platform contains
   ERPBridge and Workflow Automation. ERP demo contains ERPNext. Project resources
   contains ToolPlane. All three required rows must exist before publication.
6. Disable monitor URL publication (`sendUrl`), tags, custom CSS, analytics,
   uploads, badges, metrics and certificate-detail publication. Default icon is
   `/icon.svg`. Keep public incidents and maintenance text free of private data.
7. Set footer exactly to the catalogue footer (`Provided for information only.`). Set the status page as entry page
   and map `erpbridge-uptime.nimendra.online` to it in the status-page UI. Publish
   this populated page while it remains behind stage-2 authentication. Preview
   and inspect its public JSON through the protected session. Do not publish an
   empty required group. Only then acknowledge stage 3.

A mapped canonical root serves HTTP 200. When `/status/erpbridge` is the entry page,
the default host can redirect exactly to that path. The verifier never follows
that redirect. It checks the exact Location and separately checks the status URL.
Do not permit redirects from monitored services.

ERPBridge `/mcp/health` needs exact 200 plus literal `"status":"ok"`; a root 200
or wrong health body fails. This is basic liveness, not upstream/database readiness.
ERPNext needs exact 200 plus `pong`. Workflow must use exactly
`https://workflow-automation-44201.web.app/` plus `Agentic Workflow Engine`; this
checks a UI shell, not JavaScript boot, login, backend APIs or workflow execution.
ToolPlane checks its documentation root only.

Before a deliberate ERPNext shutdown, create a scheduled maintenance window for
that monitor and a public notice with an end time. Verify how maintenance
actually appears. Pausing a monitor does not automatically create a maintenance
notice. Extend maintenance explicitly. Remove it after readiness returns. Never
hide unexpected failure or reconstruct erased history as UP.

## Verification and publication limits

Offline checks:

```sh
python3 scripts/verify-uptime-kuma.py --catalogue-only
python3 -m unittest discover -s tests -p test_uptime_kuma.py -v
terraform -chdir=terraform-uptime fmt -check -recursive
terraform -chdir=terraform-uptime init -backend=false -lockfile=readonly
terraform -chdir=terraform-uptime validate
terraform -chdir=terraform-uptime test
```

The isolated mock harness must copy the root's `.tf` files, lock, `tests/` including
`entra-client-secret.fixture`, sibling `config/uptime-kuma-public-paths.json`, and
sibling `scripts/wait-for-custom-domain-dns.sh`. Copy no real tfvars, state or
credentials. The fixture is a clearly marked fake offline credential. Mock tests
never execute the DNS provisioner or cloud mutations.

After stage 3, run this read-only anonymous check on both hosts:

```sh
python3 scripts/verify-uptime-kuma.py --default-fqdn '<azure_default_fqdn>'
```

Optional `--check-targets` performs bounded unauthenticated GETs of the nine enabled
catalogue targets. It never calls disabled MockERP. It prints only counts and
booleans, never raw bodies, URLs with credentials or heartbeat messages. Target
failure does not mean the dashboard failed to publish. DOWN, pending and
maintenance are reported distinctly from UP. The deployment verifier can succeed
with truthful DOWN monitors. An explicitly requested target check returns failure
if an expected status or keyword fails.

Network limits: no redirects, TLS verification, 10-second request timeout, 4 MiB
maximum per response, 180-second request-start deadline per fetcher, bounded
static asset count, and 100 heartbeat samples per monitor. Default sample age is
900 seconds, configurable from 60 to 3600 seconds with `--max-age`. The check
rejects future samples, private IDs/labels, nonempty public heartbeat messages,
raw response fields, and obvious credential/IP/identity text.
These checks provide defense in depth. They do not prove that arbitrary free text
contains no secrets. Manually review all public text. The tool saves no bodies.

The verifier probes anonymous setup/database setup, dashboard, management,
Socket.IO polling, push/badge/metrics, query and encoded/traversal-like paths.
An unexpected 200, redirect, 403 or 404 on protected paths fails its strict 401
contract. Some normalization variants may fail on the real platform. If this
occurs, block publication and investigate. Do not weaken exclusions.
The sole wildcard is `/assets/*`; there is no Socket.IO or generic API wildcard.
Public routes cache config for five minutes and heartbeat for one minute. Measure
visible delay rather than claiming immediate updates.

Offline fixtures prove verifier decisions, **not** live ACA route enforcement.
Before publication, manually test both hostnames with an anonymous browser and
allowed/disallowed Entra sessions. Review methods and encoded/query/path
normalization against pinned source and Azure behavior. Do not send state-changing
POSTs to live setup endpoints as a test. Authenticated administration must still
work. If a required read path is missing, stop and review pinned-source/browser
trace before adding it. Never exempt setup, management or Socket.IO.

## Reset, operation and acceptance record

Replica-local EmptyDir `/app/data` can disappear after restart, rescheduling or
revision replacement. This removes users, 2FA, monitors, incidents and history.
The control-plane auth policy survives database loss. Public reads may become
unavailable. Protected setup must remain unclaimable. Return to authenticated-only
stage 2 (`public_status_enabled=false`) before an approved reset/rebuild. Restore
the catalogue through the UI, then repeat stage-3 verification. Never restart
ERPBridge/ERPNext or modify their endpoints for Kuma testing.

Obtain approval for a Kuma-only reset rehearsal. Prove retained auth denies
anonymous setup on both hosts, then rebuild manually. Use a temporary fixture
monitor to demonstrate DOWN/recovery and scheduled maintenance, then remove it.
Do not add a permanent fixture to the public catalogue or infer missing samples.

Record only redacted evidence: app/auth policy present, callback success,
allowed-operator success, unauthorized-user denial, anonymous route matrix,
all four public labels, five private rows absent, sample freshness, footer text,
maintenance/DOWN/recovery separation, no notifications, restart/rebuild result.
Do not mark these complete based on mocks. The operator must still complete
desktop/mobile browser acceptance and actual ACA/TLS policy tests.

Observe at least 24 hours of CPU, memory, replica continuity, temporary-storage
use, database growth, heartbeat freshness and public response time. Review costs
daily for the first week, including shared subscription grants, active/idle CPU,
memory, requests, logs and network. Record 31-day estimate and actual costs after
deployment. Without unused grants, historical retail bounds were about USD
6.03–20.09 for compute in a 31-day month, before other charges. This is not a bill
or a promise that credit/free allowance makes the service free. If costs or sizing
fail expectations, return for a decision. No automatic spending shutdown or
silent resize is configured.

This provides one ACA probe location, not global or independent multi-region coverage.
An environment outage can remove both Kuma and MockERP. A Kuma self-check is not
independent monitoring. Public percentages cover retained observations only.
They do not provide an SLA. Rollback removes only reviewed Kuma-owned resources and its two new DNS
records, never the shared environment/workspace or another application record.
