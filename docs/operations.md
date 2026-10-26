# Operations runbook

> **Historical POC documentation.** The recorded deployments were deleted in
> October 2026. The status, tool counts, approvals, and acceptance results below
> describe past work. They do not describe current services or give permission
> to deploy again. Before cloud changes, check the versioned configuration and
> get new approval.

For the real ERPNext VM, SCP fixtures, credentials, budget gates, and presentation
sequence, use [ERPNext viva deployment](erpnext-viva.md). The following sections
keep the original MockERP path. When `erp_target = "erpnext"`, do not use the
MockERP resource checks or tool name. `make verify-deployment` selects the
configured target.

## 1. Authenticate

Log in to Azure and select the deployment subscription:

```bash
az login
az account set --subscription '<subscription-id>'
```

Set the Cloudflare API token in the current shell:

```bash
read -rsp 'Cloudflare API token: ' CLOUDFLARE_API_TOKEN
export CLOUDFLARE_API_TOKEN
echo
```

The token needs `Zone - DNS - Edit` and `Zone - Zone - Read` permissions for
`nimendra.online`. If the actual state still owns records in an old zone, access to that zone is
also required. The existing token file is `secrets/cloudflare.token`
(mode `0600`). Load the token into `CLOUDFLARE_API_TOKEN`. Do not print the token.
Do not store the token in Terraform files.

## 2. Apply the deployment

Register the Azure resource providers once:

```bash
az provider register --namespace Microsoft.App
az provider register --namespace Microsoft.OperationalInsights
```

Run the preflight:

```bash
make preflight
```

Review the plan:

```bash
make plan
```

Apply the plan:

```bash
make apply
```

The apply creates these resources in `erpbridge-hq`:

- One Log Analytics workspace.
- One Container Apps environment.
- One ERPBridge Container App.
- One MockERP Container App.
- Two Cloudflare CNAME records.
- Two `asuid` Cloudflare TXT records.
- Two Azure managed certificates.
- Two AzAPI certificate bindings.

Terraform does not create the resource group, Redis, or persistent SQLite
storage.

Azure certificate issuance can take several minutes.

## 3. Verify the deployment

Run:

```bash
make verify-deployment
```

The command checks that:

- Both custom names resolve.
- Both HTTPS health endpoints return success.
- MockERP accepts the ERP credential.
- ERPBridge exposes its control-plane routes.
- The API probe route works.
- MCP creates a session.
- The read-only `list_warehouses` tool call succeeds.

To run only the server and CLI route check, use:

```bash
make check-bridge-compatibility
```

The check fails for unexpected control-plane errors.
If the image does not contain the API probe route, the check reports the local
`bridgectl` fallback used by the onboarding workflow.

## 4. DNS and certificates

Cloudflare records use DNS-only mode.
The app CNAME points directly to the Azure Container App FQDN.
The `asuid.<hostname>` TXT record contains the Azure verification ID.

Do not enable the Cloudflare proxy for these records.
An intermediate CNAME can prevent Azure certificate issuance and renewal.

If a zone has a CAA record, make sure that the record permits DigiCert:

```text
0 issue digicert.com
```

The AzAPI binding uses:

```text
Microsoft.App/containerApps@2025-07-01
```

The binding body contains the hostname, managed certificate ID, and
`SniEnabled` binding type.
Do not add external custom domains to these apps unless you also merge the
domains into the AzAPI custom-domain list.

## 5. Secret behavior

The following values are sensitive Terraform inputs. Policy Gate, Data API,
and Resend keys are optional. If these keys are unset, Terraform omits them
from the runtime:

- `api_auth_token`
- `policy_gate_api_key`
- `external_service_api_key`
- `resend_api_key`
- `mockerp_credentials`

Terraform passes these values to Container Apps as application secrets. The Policy Gate
credential is exposed to ERPBridge only as the `POLICY_GATE_API_KEY` secret
reference used by the registered API. The Data API credential is exposed only
as the `DATA_API_KEY` secret reference. The Resend credential is exposed only
as the `RESEND_API_KEY` secret reference.
Terraform state contains their values for this demo.
Use encrypted, access-controlled remote state for shared work.

The MockERP credentials object must contain at least one complete token record.
When `erp_target = "mockerp"`, Terraform derives `ERP_PRIMARY_KEY` from the first
credential as:

```text
token <api_key>:<api_secret>
```

For `erp_target = "erpnext"`, set its HTTPS origin and separate integration token.
Do not set a conflicting `erpbridge_erp_base_url` override. Before you change
the credential, pause demo calls and disable the previous target's owned tools.
Then change the credential, onboard the selected set, and verify the selected set.
Do not delete unrelated tools or APIs.
Invalid credentials stop the plan before Azure creates a Container App revision.

## 6. Output artifact

Run:

```bash
make export-output
```

The command writes:

```text
artifacts/deployment-output.json
```

The file contains URLs, Azure fallback URLs, image references, ingress values,
and demo credentials. When ERPNext is selected, the export reads both Terraform
roots and the protected bootstrap file. When ERPNext is selected, the export
also includes the ERPNext Administrator login. If you export the file again,
the export does not rotate passwords or create client tokens.
The file has mode `0600` and is ignored by Git.

Do not print the file in a shared terminal.
Do not put the Cloudflare API token in the file.

## 7. Token lifecycle

Set the ERPBridge admin credential for bridgectl:

```bash
export BRIDGE_API_TOKEN="$TF_VAR_api_auth_token"
```

Create a client token:

```bash
./scripts/create-mcp-token.sh opencode
```

The script saves the one-time token in:

```text
artifacts/mcp-tokens.json
```

ERPBridge stores only a token hash.
The raw token cannot be recovered after creation.

## 8. OpenAPI onboarding

Run:

```bash
make onboard-mockerp
```

The command registers MockERP and changes the OpenAPI server URL to the
canonical MockERP URL. Then the command generates the approved tool set,
validates each file, applies each tool, and confirms tool discovery.

The default source is:

```text
../mockerp/openapi.yaml
```

Set `MOCKERP_OPENAPI_SPEC` to use another source.
The old generated files with Azure default hostnames are not authoritative.

## 9. Persistence boundary

Both applications use `EmptyDir` storage.
SQLite data can disappear during revision replacement or rescheduling.
A destroy operation also removes the registry, tokens, and published tools.

After a reset, run:

```bash
make apply
make verify-deployment
export BRIDGE_API_TOKEN="$TF_VAR_api_auth_token"
make onboard-mockerp
```

Azure Files persistence is not enabled by this deployment.

## 10. Teardown

Create a destroy plan:

```bash
make destroy-plan
```

Destroy only the resources in the Terraform state:

```bash
make destroy
```

Do not destroy `erpbridge-hq`.
