# Reproduce the ERPNext and ERPBridge demo

> **Historical POC documentation.** The recorded deployments were torn down in
> October 2026. Status, tool counts, approvals, and acceptance results below
> describe dated work, not current services or permission to deploy again.
> Check the versioned configuration and obtain fresh approval before cloud changes.

This runbook reproduces the research POC tested on 2026-10-15. It covers restarting the existing deployment and building the same baseline from empty Terraform state. **Do not run the rebuild steps against the current running deployment.**

Commands assume Bash on a Linux operator machine, from the repository root. Keep the same shell for the rebuild's exported metadata. If you open a new shell, reload the metadata and existing credentials; do not rerun secret-generation blocks. Python examples use the standard library. This document contains no usable credentials. Run one reviewed step at a time. Stop on an error before continuing; the unconfirmed-start case has its own read-only checks below.

The current source includes approved expansions to **45 tools**, including accounting, asset, and assignment reads. New writes and reports remain deferred. The existing deployment preserves an operator-owned revision that blocks broad re-onboarding until reviewed; combined scans also encountered intermittent MCP errors. See [the active tool catalogue](erpnext-tools.md) for live verification evidence and permission limits. A fresh rebuild still seeds only the original SCP masters; it does not recreate existing Customers, Suppliers, or Orders. When those lists are empty, named-get verification records direct REST 404 plus an MCP `explicit-error-probe`, not a successful named-record read.

## 1. Choose the correct procedure

| Situation | Procedure |
| --- | --- |
| VM and both Terraform states still exist | Section 3: restart and verify |
| ACA registry/client tokens disappeared | Section 10: recover bridge registration |
| New deployment into the existing resource group and DNS zone | Sections 4–9: rebuild |
| State is missing but Azure resources still exist | Stop. Review resource ownership and imports; do not apply a new create plan |
| Need to restore custom ERPNext data | Stop. Backup/restore has not passed live acceptance |

A rebuild recreates the fictional baseline, **not arbitrary data from a lost VM**. Credentials and Terraform state are not database backups.

### Reference topology

| Component | Configuration |
| --- | --- |
| Resource group | Existing `erpbridge-hq`; neither root manages its lifecycle |
| Region | `centralindia`, independent of resource-group metadata location |
| ERPNext | `erpnext-demo`, Standard_B2as_v2, 2 vCPU/8 GiB, Ubuntu 24.04, 64 GiB StandardSSD OS disk |
| ERPNext services | Pinned Frappe/ERPNext, MariaDB, Redis; Docker volumes; host Caddy |
| ERPBridge / MockERP | Standard WorkloadProfiles ACA environment; Consumption profile; one 0.5-vCPU/1-GiB replica each |
| ACA environment | `erpbridge-demo-aca-standard`, **not Express** |
| DNS | Existing Cloudflare zone `nimendra.online`, DNS-only records |
| Tools | 45 tool names: original Item lifecycle, business reads, and limited user/document lookup |
| Data | SCP company, Sri Lanka/LKR, eight Item masters; no stock transactions |

Canonical endpoints:

- ERPNext: <https://erpnext.nimendra.online>
- ERPBridge: <https://erpbridge.nimendra.online>
- MCP: <https://erpbridge.nimendra.online/mcp/>
- MockERP: <https://mockerp.nimendra.online>

The procedure is specific to these names. The ERPNext zone ID and hostname are also defined in `terraform-erpnext/main.tf`; changing only shell variables does not move it to another domain.

## 2. Safety, prerequisites, and files

### Approval gates

Before provisioning, starting compute, changing DNS, registering providers, or replacing resources:

1. Confirm the intended Azure subscription and remaining student credit/expiry.
2. Check current quota, regional policy, SKU restrictions, and resource prices.
3. Agree on the running window and spending allowance.
4. Review each saved Terraform plan before applying it.
5. Obtain separate approval for replacement/deletion of existing resources.

Do not convert Azure for Students to pay-as-you-go or buy an AI service. The earlier test's USD 5 allowance is historical, not permission for unlimited future use.

The reference modeled base was about USD 0.16943/hour for VM, disk/IP, and two active ACA replicas before free grants, logs, network use, and other variable charges. Reprice before use. Deallocation stops VM compute charges only. ACA, disk, public IP, and logs can continue to use credit.

### Operator tools and source

- Terraform `>=1.9, <2.0`, Azure CLI, Git, GNU Make, Python 3, Bash, `curl`, `jq`, `getent`, and OpenSSH tools.
- A compatible `bridgectl` supporting API registration, OpenAPI generation, named/versioned tool reads, validation, application, and scoped client-token creation.
- Both committed Terraform lock files and the complete reviewed source tree: `terraform/`, `terraform-erpnext/`, `demo/erpnext/`, `config/`, `scripts/`, and `tests/`.
- The source changes must be saved in the revision you reproduce. An old `HEAD` alone does not include uncommitted or untracked implementation files. Record `git rev-parse HEAD` and `git status --short` privately before packaging a handoff.
- Do not copy provider caches between machines. Initialize each root independently. Keep provider versions from the lock files.
- PyYAML is needed only for the separate MockERP onboarding script, not this ERPNext path. Do not run MockERP onboarding while ERPNext is selected.

Record the installed CLI identity as well as the source revision. A `Version: dev` label alone does not identify the tested binary:

```bash
bridgectl version
sha256sum "$(command -v bridgectl)"
```

Run each complete Python block in a here-document, not directly as Bash or as separate REPL statements:

```bash
python3 - <<'PY'
# Replace this comment with one complete Python block from this runbook.
PY
```

Ubuntu image `latest` and archive packages are not fully version-pinned. This reproduces the topology/data/API behavior, not a byte-identical OS build.

### Private files

| File | Purpose |
| --- | --- |
| `secrets/cloudflare.token` | Cloudflare Zone Read / DNS Edit credential |
| `terraform-erpnext/demo.auto.tfvars.json` | VM inputs, including SSH key paths and operator CIDR |
| `terraform/erpnext-demo.auto.tfvars.json` | ACA configuration and application secrets |
| `artifacts/erpnext-credentials.json` | Initial admin and integration credentials; bootstrap administration |
| `artifacts/mcp-tokens.json` | Raw per-client tokens captured once at creation |
| `artifacts/deployment-output.json` | Main operator URLs and allowlisted login/token summary |
| Both roots' `terraform.tfstate*` | Managed resource identity and potentially sensitive values |

These files are Git-ignored. Keep secret files and state mode `0600` on a private filesystem. Transfer private backups separately from source code. Never publish a whole working-directory/archive copy containing ignored secrets.

```bash
set +x
umask 077
mkdir -p secrets artifacts
chmod 700 secrets artifacts
```

Do not use `make tokens`, Terraform sensitive outputs, HTTP verbose logging, or a private JSON file as a screen-share display. The main export intentionally excludes Azure/Cloudflare credentials and SSH private-key contents.

## 3. Restart the existing deployment

This path does not create a new environment, regenerate credentials, or reseed data.

Authenticate to the correct account, then check status:

```bash
az login
# Select the subscription that owns this deployment, if necessary.
make erpnext-status
```

If the VM is deallocated:

```bash
make erpnext-start CONFIRM=yes
make erpnext-ready
```

If `start` says completion is unconfirmed, **do not assume it failed**. In the observed restart, Azure had started the VM but a polling request failed. Check and wait using read-only commands:

```bash
make erpnext-status
make erpnext-ready
```

Expected result: `PowerState/running` and `HTTPS readiness: ready`. If the VM is already running, skip the start command.

Load the existing protected credentials without printing them. The tested client name is `erpnext-viva-test`; choose the current valid client name if it has since changed.

```bash
set +x
export MCP_CLIENT_NAME=erpnext-viva-test
export BRIDGE_API_TOKEN="$(jq -er '.demo_tokens.api_auth_token' artifacts/deployment-output.json)"
export ERP_PRIMARY_KEY="$(jq -er '.demo_tokens.erp_primary_key' artifacts/deployment-output.json)"
export MCP_TOKEN="$(jq -er --arg name "$MCP_CLIENT_NAME" '.mcp_tokens[$name].token' artifacts/deployment-output.json)"
export BRIDGE_SERVER="$(terraform -chdir=terraform output -raw erpbridge_url)"
export BRIDGE_MCP_SERVER="$BRIDGE_SERVER"
make erpnext-data-check
make verify-erpnext
```

Both checks must pass before a presentation. Optionally authorize the bounded write check:

```bash
make verify-erpnext-write CONFIRM=yes
```

When finished, use Section 11. Do not continue into the rebuild procedure for a normal restart.

## 4. Rebuild: verify account, ownership, and DNS access

**This section and those that follow are for a reviewed fresh deployment.** The resource group and DNS zone must already exist. Creation of a different group/domain is a separate operator decision.

Authenticate and select the approved subscription explicitly, then set non-secret metadata for this session:

```bash
set +x
umask 077
az login
read -r -p 'Approved Azure subscription ID: ' SUBSCRIPTION_ID
az account set --subscription "$SUBSCRIPTION_ID"
export SUBSCRIPTION_ID
export TENANT_ID="$(az account show --query tenantId -o tsv)"
export RESOURCE_GROUP=erpbridge-hq
export LOCATION=centralindia
export ENVIRONMENT_NAME=erpbridge-demo-aca-standard
export WORKSPACE_NAME=erpbridge-demo-logs
az account show --query '{name:name,state:state}' -o json
az group show --name "$RESOURCE_GROUP" --query '{name:name,location:location}' -o json
az resource list --resource-group "$RESOURCE_GROUP" --query '[].{name:name,type:type}' -o json
```

Check both roots' state privately. **Empty local state does not mean Azure is empty.** Stop for an ownership/import review if resources or existing state overlap this deployment. Do not use `terraform state rm` or delete state to force a rebuild.

Inspect registration before requesting any registration change:

```bash
for provider in Microsoft.Compute Microsoft.Network Microsoft.App Microsoft.OperationalInsights; do
  az provider show --namespace "$provider" --query '{namespace:namespace,state:registrationState}' -o json
done
```

The roots disable automatic provider registration. Register only missing namespaces after approval and wait for `Registered`.

In the original subscription, B2as_v2 could be allocated without a zone. B2ms was location-restricted; do not assume it is a usable fallback. Quota alone is not proof of SKU availability.

Provide the existing Cloudflare token through a private editor or secure transfer, then load it:

```bash
chmod 600 secrets/cloudflare.token
export CLOUDFLARE_API_TOKEN="$(< secrets/cloudflare.token)"
```

Required zone: `c68ea0eb14e403a5454c04b6efecdcf2` for `nimendra.online`. Keep DNS-only mode. If existing records belong to another deployment, review their ownership before creating/replacing records. The supplied token did not grant access to the old `nimendra.xyz` zone; do not silently abandon old managed records.

List existing `*.tfvars` and `*.auto.tfvars.json` filenames before creating inputs. The two fresh-input examples below refuse to overwrite an existing file. Auto-variable files override ordinary `terraform.tfvars` and environment `TF_VAR_*` values; explicit `-var-file`/`-var` arguments have higher priority. Do not mix conflicting configurations.

## 5. Rebuild: VM, SSH trust, and ERPNext

### 5.1 Create VM inputs once

Use an existing operator-owned SSH key pair. Do not overwrite a private key. Set the paths and capture your current public IPv4:

```bash
export SSH_PUBLIC_KEY_PATH="$HOME/.ssh/id_ed25519.pub"
export SSH_PRIVATE_KEY_PATH="$HOME/.ssh/id_ed25519"
export OPERATOR_IPV4="$(curl --fail --silent --show-error --max-time 20 https://api.ipify.org)"
```

Create the ignored inputs, not a modified tracked example:

```python
import ipaddress, json, os
from pathlib import Path

ip = str(ipaddress.IPv4Address(os.environ['OPERATOR_IPV4']))
public = Path(os.environ['SSH_PUBLIC_KEY_PATH']).expanduser()
private = Path(os.environ['SSH_PRIVATE_KEY_PATH']).expanduser()
assert public.is_file() and private.is_file(), 'Existing SSH key pair required'
values = {
    'subscription_id': os.environ['SUBSCRIPTION_ID'],
    'tenant_id': os.environ['TENANT_ID'],
    'resource_group_name': os.environ['RESOURCE_GROUP'],
    'deployment_location': os.environ['LOCATION'],
    'vm_name': 'erpnext-demo', 'vm_size': 'Standard_B2as_v2',
    'ssh_username': 'erpnext',
    'ssh_public_key_path': str(public), 'ssh_private_key_path': str(private),
    'operator_ssh_cidr': ip + '/32',
}
path = Path('terraform-erpnext/demo.auto.tfvars.json')
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as stream:
    json.dump(values, stream, indent=2)
print('Private VM inputs created; no private-key contents read.')
```

Run Python blocks in this runbook using `python3` with the block on stdin or a private local script. Do not paste Python directly into Bash.

### 5.2 Initialize, review, and apply the VM plan

```bash
make erpnext-init
make erpnext-plan
chmod 600 terraform-erpnext/tfplan
# Review the saved plan and cost. Continue only after approval.
make erpnext-apply CONFIRM=yes
```

A new VM-root plan creates eight managed resources. It must not delete or alter the ACA deployment or existing resource group. SSH must allow only your actual `/32`; public HTTP/HTTPS are for Caddy. No database/Redis port should be public.

### 5.3 Verify SSH host identity through Azure

Never use `StrictHostKeyChecking=no` or trust an unverified `ssh-keyscan` result. The following obtains only the VM's public host key through the authenticated Azure control plane. It refuses an existing mismatching host entry.

```python
import json, re, subprocess
from pathlib import Path

vm = json.loads(subprocess.check_output([
    'terraform', '-chdir=terraform-erpnext', 'output', '-json', 'deployment_summary']))
result = json.loads(subprocess.check_output([
    'az', 'vm', 'run-command', 'invoke', '--ids', vm['resource_id'],
    '--command-id', 'RunShellScript', '--scripts',
    "awk '{print $1,$2}' /etc/ssh/ssh_host_ed25519_key.pub", '-o', 'json'], timeout=180))
messages = '\n'.join(x.get('message', '') for x in result.get('value', []))
keys = re.findall(r'^ssh-ed25519 [A-Za-z0-9+/=]+$', messages, re.M)
assert len(keys) == 1, 'Unique Azure-attested host key required'
known = Path.home() / '.ssh/known_hosts'
known.parent.mkdir(mode=0o700, exist_ok=True)
found = subprocess.run(['ssh-keygen', '-F', vm['public_ip'], '-f', str(known)],
                       capture_output=True, text=True) if known.exists() else None
if found is not None and found.returncode not in (0, 1):
    raise SystemExit('Could not inspect known_hosts; stop')
if found is not None and found.returncode == 0:
    assert keys[0] in found.stdout, 'Host identity differs: review, do not replace automatically'
else:
    with known.open('a') as stream:
        stream.write(vm['public_ip'] + ' ' + keys[0] + '\n')
    known.chmod(0o600)
print('Verified public host key installed.')
```

Wait for package setup through the verified SSH connection:

```bash
VM_JSON="$(terraform -chdir=terraform-erpnext output -json deployment_summary)"
VM_IP="$(jq -r '.public_ip' <<<"$VM_JSON")"
SSH_USER="$(jq -r '.ssh_username' <<<"$VM_JSON")"
SSH_KEY="$(jq -r '.ssh_private_key_path' <<<"$VM_JSON")"
ssh -o StrictHostKeyChecking=yes -o BatchMode=yes -i "$SSH_KEY" \
  "$SSH_USER@$VM_IP" 'sudo cloud-init status --wait'
```

Expected: cloud-init completes successfully. Resolve package failures before app bootstrap.

### 5.4 Bootstrap and seed

```bash
make erpnext-bootstrap CONFIRM=yes
make erpnext-ready
make erpnext-seed CONFIRM=yes
make erpnext-seed CONFIRM=yes
make erpnext-data-check
```

The driver transfers reviewed code, creates the explicit `frontend` site, initializes locale/country fixtures, creates separate credentials, and enables Caddy. It saves local `artifacts/erpnext-credentials.json` with mode `0600`. The API integration identity is not Administrator.

Success means the two seed runs and integration-user data check pass. The eight fixture Items must retain their expected codes/names/groups/units. These are masters only, not stock balances or financial transactions. If the first bootstrap fails before local credentials are saved, follow Section 10; do not reset passwords or delete volumes.

## 6. Rebuild: prepare the selected ERPNext ACA inputs

Keep both immutable image references from the tested example. Changing images is a new compatibility decision. This creates application secrets **once** and refuses an existing configuration file.

```python
import ipaddress, json, os, secrets, stat
from pathlib import Path

credentials = Path('artifacts/erpnext-credentials.json')
assert not credentials.is_symlink() and credentials.is_file()
assert stat.S_IMODE(credentials.stat().st_mode) == 0o600
integration = json.loads(credentials.read_text())['integration']
assert integration['username'] != 'Administrator'
ip = str(ipaddress.IPv4Address(os.environ['OPERATOR_IPV4']))
values = {
    'subscription_id': os.environ['SUBSCRIPTION_ID'],
    'tenant_id': os.environ['TENANT_ID'],
    'resource_group_name': os.environ['RESOURCE_GROUP'],
    'deployment_location': os.environ['LOCATION'],
    'container_app_environment_name': os.environ['ENVIRONMENT_NAME'],
    'log_analytics_workspace_name': os.environ['WORKSPACE_NAME'],
    'erpbridge_image': 'ghcr.io/nmdra/erpbridge-server@sha256:511539c80c028427000cb9471f3eda8c2cd3a145095461d788f79f3a2609d6ee',
    'mockerp_image': 'ghcr.io/nmdra/mockerp@sha256:67a25057cbb2a3c4422be8e97b8a3096219645730bf42ae5e9a0a53c86c6c345',
    'erp_target': 'erpnext', 'erpnext_url': 'https://erpnext.nimendra.online',
    'erpnext_api_token': 'token ' + integration['api_key'] + ':' + integration['api_secret'],
    'api_auth_token': secrets.token_urlsafe(32),
    'mockerp_credentials': {'credentials': [{
        'api_key': secrets.token_hex(15), 'api_secret': secrets.token_urlsafe(32),
        'role': 'admin', 'identity': 'demo',
    }]},
    'mockerp_allowed_cidrs': [ip + '/32'], 'mockerp_allow_all_ingress': False,
    'custom_domains': {
        'zone_id': 'c68ea0eb14e403a5454c04b6efecdcf2',
        'erpbridge': 'erpbridge.nimendra.online', 'mockerp': 'mockerp.nimendra.online',
    },
}
path = Path('terraform/erpnext-demo.auto.tfvars.json')
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as stream:
    json.dump(values, stream, indent=2)
print('Private ACA inputs created once. Preserve them for reruns.')
```

Leave Policy Gate, Data API, and Resend unset for this scope. Do not set a conflicting `erpbridge_erp_base_url`. The default ACA internal CIDR is environment-specific, not a universal Azure range; the real ERPNext path does not require bridge-to-MockERP access.

## 7. Rebuild: create the standard ACA environment explicitly

**Do not skip this step.** AzureRM 5.2.0 does not expose `environmentMode`. The initial default environment became Express, which rejected custom domains. A Consumption profile block alone did not establish the required mode.

The tested workaround is: create the workspace first, create an environment with explicit ARM `environmentMode=WorkloadProfiles` and its logging configuration, verify the mode, then import it into Terraform. Do not create a logs-disabled substitute and assume it will reproduce the tested deployment.

### 7.1 Create only the Log Analytics workspace

```bash
make init
terraform -chdir=terraform plan -input=false \
  -target=azurerm_log_analytics_workspace.demo -out=workspace.tfplan
chmod 600 terraform/workspace.tfplan
# Review: only the new workspace should be created. Obtain approval.
terraform -chdir=terraform apply -input=false workspace.tfplan
```

This targeted apply is a bootstrap exception, not the normal deployment workflow. All ACA input variables must already be valid, even though only the workspace is targeted.

### 7.2 Create and verify WorkloadProfiles mode

This block makes an approved Azure write. It uses a temporary mode-0600 request file for the workspace key, removes that file, and never places the key in command arguments or prints it. Existing environments cause a stop, not an overwrite. An ambiguous PUT failure requires a GET/ownership review before any retry.

```python
import json, os, subprocess, tempfile, time
from pathlib import Path

os.umask(0o077)

def az_json(*args):
    result = subprocess.run(['az', *args, '-o', 'json'],
                            capture_output=True, text=True, timeout=60)
    if result.returncode:
        Path('artifacts/environment-operation-private.log').write_text(result.stderr)
        raise SystemExit('Azure operation failed; inspect private diagnostics. Do not retry creation blindly.')
    return json.loads(result.stdout)

subscription = os.environ['SUBSCRIPTION_ID']
group = os.environ['RESOURCE_GROUP']
name = os.environ['ENVIRONMENT_NAME']
workspace_name = os.environ['WORKSPACE_NAME']
existing = az_json('containerapp', 'env', 'list', '-g', group, '--query', '[].name')
assert name not in existing, 'Environment already exists: review mode, ownership, and import separately'
base = f'https://management.azure.com/subscriptions/{subscription}/resourceGroups/{group}/providers/'
workspace = az_json('rest', '--method', 'get', '--url',
                    base + f'Microsoft.OperationalInsights/workspaces/{workspace_name}?api-version=2023-09-01')
keys = az_json('monitor', 'log-analytics', 'workspace', 'get-shared-keys',
               '-g', group, '-n', workspace_name)
uri = base + f'Microsoft.App/managedEnvironments/{name}?api-version=2026-07-01'
body = {'location': os.environ['LOCATION'], 'properties': {
    'environmentMode': 'WorkloadProfiles',
    'workloadProfiles': [{'name': 'Consumption', 'workloadProfileType': 'Consumption'}],
    'appLogsConfiguration': {'destination': 'log-analytics', 'logAnalyticsConfiguration': {
        'customerId': workspace['properties']['customerId'], 'sharedKey': keys['primarySharedKey'],
    }},
}}
fd, temporary = tempfile.mkstemp(prefix='.aca-environment-', suffix='.json', dir='artifacts')
try:
    with os.fdopen(fd, 'w') as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(body, stream)
    az_json('rest', '--method', 'put', '--url', uri, '--body', '@' + temporary)
finally:
    Path(temporary).unlink(missing_ok=True)
for _ in range(30):
    environment = az_json('rest', '--method', 'get', '--url', uri)
    properties = environment['properties']
    state = properties.get('provisioningState')
    if state == 'Succeeded':
        break
    if state in ('Failed', 'Canceled'):
        raise SystemExit('Environment failed; inspect without recreating it')
    time.sleep(10)
else:
    raise SystemExit('Environment not ready within deadline; inspect existing resource before retry')
assert properties.get('environmentMode') == 'WorkloadProfiles', 'Wrong mode: do not deploy apps'
assert any(p.get('name') == 'Consumption' and p.get('workloadProfileType') == 'Consumption'
           for p in properties.get('workloadProfiles', [])), 'Consumption profile required'
print('Standard environment verified. No workspace key displayed.')
```

Transient network errors can occur during polling after a successful PUT. Inspect the existing resource instead of rerunning the creation block:

```bash
ENVIRONMENT_ID="/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.App/managedEnvironments/$ENVIRONMENT_NAME"
az rest --method get \
  --url "https://management.azure.com${ENVIRONMENT_ID}?api-version=2026-07-01" \
  --query 'properties.{mode:environmentMode,state:provisioningState}' -o json
```

Continue only when mode is `WorkloadProfiles` and state is `Succeeded`.

### 7.3 Adopt the exact environment into Terraform

Use only the resource you just created and verified. If the Terraform address is already occupied, stop and review state; do not remove it automatically.

```bash
ENVIRONMENT_ID="/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.App/managedEnvironments/$ENVIRONMENT_NAME"
terraform -chdir=terraform import -input=false \
  azurerm_container_app_environment.demo "$ENVIRONMENT_ID"
```

## 8. Rebuild: deploy apps, DNS, and certificates

```bash
make plan
chmod 600 terraform/tfplan
# Review the complete saved plan and approve its cost/actions.
terraform -chdir=terraform apply -input=false tfplan
```

Apply the exact saved plan. `make apply` instead plans/applies current inputs interactively; it does not apply the saved `tfplan`. Do not use it as a substitute for this reviewed-plan step.

Expected changes are new apps, CNAME/TXT records, DNS waits, custom domains, certificates, and bindings. An environment tags-only reconciliation can be expected after manual creation. Stop for an unexplained environment replacement, disk deletion, resource-group change, old-zone change, or unrelated resource change.

Wait for apply completion. DNS/certificate issuance can take several minutes. Do not recreate apps just because a recursive resolver still has an old negative answer. The DNS wait checks matching CNAME and TXT values through two resolvers.

Verify transport and authentication before tool onboarding:

```bash
curl --fail --silent --show-error --max-time 20 https://erpbridge.nimendra.online/mcp/health
curl --fail --silent --show-error --max-time 20 https://mockerp.nimendra.online/health
# Expect 401, not 200:
curl --silent --output /dev/null --write-out '%{http_code}\n' \
  --max-time 20 https://erpbridge.nimendra.online/apis/erpbridge.io/v1/tools
```

The pinned bridge runs as non-root. Its active SQLite file is `/tmp/erpbridge.db`, which is writable but ephemeral. The `/data` mount is not the active registry path. A successful public health response alone does not prove storage or authenticated operations work.

## 9. Rebuild: onboard, export, and prove the data path

Load administrative values privately. These values must remain stable across reruns:

```bash
set +x
export BRIDGE_API_TOKEN="$(terraform -chdir=terraform output -json demo_tokens | jq -er '.api_auth_token')"
export ERP_PRIMARY_KEY="$(terraform -chdir=terraform output -json demo_tokens | jq -er '.erp_primary_key')"
export BRIDGE_SERVER="$(terraform -chdir=terraform output -raw erpbridge_url)"
export BRIDGE_MCP_SERVER="$BRIDGE_SERVER"
make check-bridge-compatibility
make onboard-erpnext CONFIRM=yes
export MCP_CLIENT_NAME=viva-client
./scripts/create-mcp-token.sh "$MCP_CLIENT_NAME"
make export-output
export MCP_TOKEN="$(jq -er --arg name "$MCP_CLIENT_NAME" '.mcp_tokens[$name].token' artifacts/deployment-output.json)"
make verify-erpnext
make verify-erpnext-write CONFIRM=yes
make verify-erpnext
```

Create the named client token once, not on every verification. After a successful initial export, use the saved artifact to reload credentials as in Section 3. Each client gets its own token; do not give an AI client the Administrator password or bridge admin token. Configure the remote MCP client with the `/mcp/` HTTPS URL and `Authorization: Bearer <client-token>`, stored privately. This is not the ERPNext `token <key>:<secret>` authorization value.

### Acceptance checklist

- [ ] All three canonical HTTPS endpoints pass; certificates are valid without disabling TLS checks.
- [ ] Anonymous control-plane access returns 401; authenticated compatibility succeeds.
- [ ] SCP seeding succeeds twice and integration-user reads verify the company and eight Items.
- [ ] Exactly the 45 approved tool names are discovered, with no stale target's tools.
- [ ] Read-only verification passes with the dedicated MCP client token.
- [ ] Write verification passes, including direct REST readback, deletion/404, and baseline preservation.
- [ ] Final read-only verification passes.
- [ ] `artifacts/deployment-output.json` is an ignored mode-0600 file with URLs, initial login, and separate tokens.
- [ ] Administrator login and visual Item inspection work in the browser. Inspect the password privately, not on a shared terminal.
- [ ] The chosen existing AI client is configured with the remote MCP URL and its client token, then rehearsed separately.

A 200 response containing JSON-RPC `error` or MCP `isError` is a failure. The verifier must not be weakened to accept it.

Two important native contracts are already handled by the checked-in onboarding/verification code:

- Create inputs are the flat Item fields, not `{"body": {...}}`.
- DELETE returns HTTP 202 with `{"data":"ok"}`. Onboarding preserves its object envelope for MCP output validation.

Do not replace the checked-in authored contract with a broad/full ERPNext schema. The added Customer/Supplier and Sales/Purchase Order operations are read-only. This POC does not validate invoice writes, stock quantities, payroll, or submission/approval workflows.

A post-deployment `terraform -chdir=terraform plan -input=false -detailed-exitcode` should return 0 when there is no drift. Exit 2 means there are differences to review, not permission to apply them. Store any detailed plan privately.

## 10. Recovery and troubleshooting

### First bootstrap failed before local credentials were saved

The remote file may already exist at `/opt/erpnext-demo/credentials.json`. The driver intentionally refuses a retry with missing local credentials. Do not delete the remote file, `.env`, site, database, or Docker volumes, and do not generate replacement passwords.

After verifying SSH host identity, recover the **existing** file without printing it. This refuses an existing local destination:

```python
import json, os, subprocess
from pathlib import Path

vm = json.loads(subprocess.check_output([
    'terraform', '-chdir=terraform-erpnext', 'output', '-json', 'deployment_summary']))
ssh = ['ssh', '-o', 'StrictHostKeyChecking=yes', '-o', 'BatchMode=yes',
       '-o', 'ConnectTimeout=20', '-i', str(Path(vm['ssh_private_key_path']).expanduser()),
       vm['ssh_username'] + '@' + vm['public_ip']]
result = subprocess.run(ssh + [
    "sudo python3 -c 'from pathlib import Path; import sys; "
    "sys.stdout.write(Path(\"/opt/erpnext-demo/credentials.json\").read_text())'"],
    capture_output=True, timeout=45)
assert result.returncode == 0, 'Could not recover saved credentials; stop'
value = json.loads(result.stdout)
assert value['admin']['username'] == 'Administrator'
assert value['integration']['username'] != 'Administrator'
for role in ('admin', 'integration'):
    assert value[role]['api_key'] and value[role]['api_secret']
assert value['admin']['password']
path = Path('artifacts/erpnext-credentials.json')
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as stream:
    json.dump(value, stream, indent=2)
print('Existing credentials recovered privately; no rotation.')
```

Inspect the concrete bootstrap failure in protected VM/container logs, fix its cause, and retry `make erpnext-bootstrap CONFIRM=yes`. Native site creation, protected console namespace handling, and locale initialization fixes are already in this source tree. Do not copy the older evaluation bootstrap over them.

### ACA registry or client token lost

A revision replacement/reschedule can remove the bridge database. ERPNext data is separate.

1. Keep the configured bridge admin and ERPNext integration credentials.
2. Run compatibility checks and `make onboard-erpnext CONFIRM=yes`.
3. If ownership/version checks refuse, stop and review the exact live manifests. Do not broadly delete tools.
4. Explicitly create a replacement client token with a new name, export output, select that name, and update the AI client.
5. Repeat read-only/write/read-only verification.

Old raw tokens in a local artifact do not restore server-side token hashes. `make export-output` never creates or rotates tokens. Automatic switching of an existing ERP target remains deliberately blocked.

### Common failures

| Symptom | Action |
| --- | --- |
| Custom-domain error says `ExpressEnvironmentFeatureNotSupported` | Verify ARM environment mode; stop. Replace/adopt only after separate approval, never automatically |
| `/mcp/health` is 200 but authenticated registry fails | Check mode, storage path/permissions, and private logs; liveness is not end-to-end success |
| CLI list output is a table despite `--output json` | Use checked-in onboarding; it fetches exact named/versioned JSON manifests |
| DELETE reports an output-contract error after removing the Item | Use the current acknowledgement schema/envelope handling; confirm the exact run-owned Item is absent before retry |
| DNS wait times out | Check both CNAME/TXT values, old resolver caches, DNS-only mode, and certificate status; review a new plan after correction |
| Start completion is unconfirmed | Check status and readiness before issuing another state change |
| Operator IP changed / MockERP returns 403 / SSH times out | Review the operator `/32` in both private input sets; approve updated plans. Do not open SSH or MockERP to everyone |
| Host key differs | Stop and verify identity through Azure; never bypass SSH checking |
| Terraform init/download stalls or cache links break | Check registry/GitHub access and repair the exact pinned cache with review. Do not upgrade providers or delete state |

Do not use `-plugin-dir` pointing at a root's `.terraform/providers` while the same packages resolve into the active plugin cache. That combination created a symlink loop during the original trial. Do not package caches as deployment inputs.

### Backup limitation

The live backup attempt failed and no disposable restore has passed. `backup.sh`/`restore.sh` are not an accepted disaster-recovery path yet. Do not destroy the VM/disk if its data is needed.

Any future restore must use a separate explicitly named disposable site/database, preserve target database routing, restore the source encryption material, and verify records and encrypted credentials. Never restore over `frontend` as an automatic fix. See [the viva operations notes](erpnext-viva.md#9-stop-compute-and-recover) for the guarded interface and limits.

## 11. End a session and preserve evidence

```bash
make erpnext-deallocate CONFIRM=yes
make erpnext-status
```

Expected final power state: `PowerState/deallocated`, not merely guest `stopped`. If completion is unconfirmed, inspect state rather than claiming billing has stopped.

Do not deallocate during a presentation or an active write/backup. Disk/IP, ACA, and logging charges continue afterwards. Stopping/deleting/rescaling ACA or deleting disks needs a separate decision; deallocation is not full teardown.

Record the source revision/dirty state, date, region/SKU, environment mode, plan summaries, checks passed/failed, elapsed running time, and final power state. Keep credential files and detailed logs private. Redact screenshots. Do not claim backup recovery or AI-client success from a deterministic MCP script.

### Offline checks before a future deployment

```bash
make fmt-check
make test
terraform -chdir=terraform validate -no-color
terraform -chdir=terraform-erpnext validate -no-color
```

Validation needs initialized providers. Mocked tests do not allocate Azure resources or prove current credit/capacity. The reference final suite passed 57 tests; the live test also passed exact MCP discovery and real create/read/delete. The rebuild examples in this document are syntax-checked instructions based on that deployment, not a second deployment executed while writing the runbook.

## References

- [Viva sequence, dataset, and operational notes](erpnext-viva.md)
- [Dated live acceptance report](deployment-report.md#live-acceptance-update-2026-10-15)
- [Terraform VM input example](../terraform-erpnext/terraform.tfvars.example)
- [Terraform ACA input example](../terraform/terraform.tfvars.example)
- [Native SCP fixture definitions](../demo/erpnext/fixtures/scp-masters.json)
- [Azure managed environment API and environmentMode](https://learn.microsoft.com/en-us/rest/api/resource-manager/containerapps/managed-environments/create-or-update?view=rest-resource-manager-containerapps-2026-07-01)
- [Azure Container Apps Express limits](https://learn.microsoft.com/en-us/azure/container-apps/express-faq)
- [Azure Retail Prices API](https://prices.azure.com/api/retail/prices)
- [VM power states and billing](https://learn.microsoft.com/en-us/azure/virtual-machines/states-billing)
