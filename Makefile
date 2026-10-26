SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.ONESHELL:

TERRAFORM ?= terraform
AZ ?= az
TF_DIR := terraform
TF := $(TERRAFORM) -chdir=$(TF_DIR)
ERPNEXT_TF := $(TERRAFORM) -chdir=terraform-erpnext
ERBRIDGE_TF := $(TERRAFORM) -chdir=terraform-erpbridge
UPTIME_TF := $(TERRAFORM) -chdir=terraform-uptime

.PHONY: help check-tools init fmt fmt-check validate preflight plan apply destroy-plan destroy output summary tokens ingress-sources health check-bridge-compatibility verify-deployment verify-ingress export-output onboard-mockerp erpnext-init erpnext-validate erpnext-plan erpnext-apply erpnext-bootstrap erpnext-seed erpnext-data-check onboard-erpnext verify-erpnext verify-erpnext-write erpnext-status erpnext-start erpnext-ready erpnext-deallocate erpbridge-vm-init erpbridge-vm-validate erpbridge-vm-plan erpbridge-vm-apply test

help: ## Show available Terraform operations.
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z0-9_-]+:.*##/ {printf "%-24s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

check-tools: ## Verify local deployment tools are available.
	@command -v $(TERRAFORM) >/dev/null
	@command -v $(AZ) >/dev/null
	@command -v curl >/dev/null
	@command -v jq >/dev/null
	@command -v python3 >/dev/null
	@command -v getent >/dev/null

init: check-tools ## Initialize Terraform providers in the single root.
	$(TF) init -input=false

fmt: ## Format all four Terraform roots.
	$(TF) fmt -recursive
	$(ERPNEXT_TF) fmt -recursive
	$(ERBRIDGE_TF) fmt -recursive
	$(UPTIME_TF) fmt -recursive

fmt-check: ## Check all four Terraform roots without modifying files.
	$(TF) fmt -check -recursive
	$(ERPNEXT_TF) fmt -check -recursive
	$(ERBRIDGE_TF) fmt -check -recursive
	$(UPTIME_TF) fmt -check -recursive

validate: init ## Validate Terraform configuration.
	$(TF) validate -no-color

preflight: check-tools fmt-check validate ## Run local non-destructive checks.
	@echo 'preflight checks passed'

plan: preflight ## Create terraform/tfplan from the current inputs.
	$(TF) plan -input=false -out=tfplan

apply: preflight ## Apply the current inputs and write a protected output artifact.
	$(TF) apply -input=false
	$(MAKE) export-output

destroy-plan: preflight ## Create a destroy plan for review.
	$(TF) plan -destroy -input=false -out=destroy.tfplan

destroy: preflight ## Interactively destroy only resources in this Terraform state.
	$(TF) destroy

output: ## Print non-sensitive Terraform outputs.
	$(TF) output

summary: ## Print the non-sensitive deployment summary as JSON.
	$(TF) output -json deployment_summary

tokens: export-output ## Export demo credentials without printing them.
	@echo 'Demo credentials are stored only in the protected deployment artifact.'

ingress-sources: ## Print configured MockERP ingress sources and ACA public egress IPs.
	summary_json="$$($(TF) output -json deployment_summary)"
	rg="$$($(TF) output -raw resource_group_name)"
	app_name="$$(jq -r '.erpbridge.name' <<<"$$summary_json")"
	printf 'Configured MockERP ingress sources:\n'
	$(TF) output -json mockerp_ingress_sources
	printf '\nAzure-reported ERPBridge public egress IPs (informational):\n'
	@$(AZ) containerapp show --name "$$app_name" --resource-group "$$rg" --query 'properties.outboundIpAddresses' -o json

health: ## Check ERPBridge and the selected ERP target's health endpoint.
	bridge_url="$$($(TF) output -raw erpbridge_url)"
	target="$$($(TF) output -raw erp_target)"
	if [[ "$$target" == "erpnext" ]]; then
		curl --fail --silent --show-error --max-time 20 "$$($(TF) output -raw erpnext_url)/api/method/ping" >/dev/null
	else
		curl --fail --silent --show-error --max-time 20 "$$($(TF) output -raw mockerp_url)/health" >/dev/null
	fi
	curl --fail --silent --show-error --max-time 20 "$$bridge_url/mcp/health" >/dev/null
	@echo 'health checks passed'

check-bridge-compatibility: ## Verify the deployed server matches bridgectl control-plane routes.
	./scripts/check-bridge-compatibility.sh

verify-deployment: ## Verify the selected ERP target, health, authentication, and MCP.
	./scripts/verify-deployment.sh

verify-ingress: verify-deployment ## Backward-compatible alias for the full deployment verification.

export-output: ## Write URLs, values, images, and demo credentials to a protected artifact.
	./scripts/export-demo-output.sh

onboard-mockerp: check-bridge-compatibility ## Generate, validate, apply, and verify approved MockERP tools.
	./scripts/onboard-mockerp-openapi.sh

erpnext-init: ## Initialize the independent VM root, without applying resources.
	$(ERPNEXT_TF) init -input=false

erpnext-validate: erpnext-init ## Validate the independent VM root.
	$(ERPNEXT_TF) validate -no-color

erpnext-plan: erpnext-validate ## Save a VM/DNS plan for cost and replacement review.
	$(ERPNEXT_TF) plan -input=false -out=tfplan

erpnext-apply: ## Apply the reviewed VM plan only after explicit spending approval.
	@test "$(CONFIRM)" = "yes" || { echo 'Requires approved cost/plan and CONFIRM=yes.' >&2; exit 1; }
	$(ERPNEXT_TF) apply -input=false tfplan

erpnext-bootstrap: ## Transfer the stack and bootstrap the approved VM over verified SSH.
	@test "$(CONFIRM)" = "yes" || { echo 'Remote setup requires CONFIRM=yes.' >&2; exit 1; }
	./demo/erpnext/bootstrap.sh

erpnext-seed: ## Create the SCP baseline using protected bootstrap credentials.
	@test "$(CONFIRM)" = "yes" || { echo 'Seeding requires CONFIRM=yes.' >&2; exit 1; }
	python3 demo/erpnext/seed.py seed --url "$$($(ERPNEXT_TF) output -raw erpnext_url)"

erpnext-data-check: ## Read and verify the SCP baseline with the integration identity.
	python3 demo/erpnext/seed.py verify --credential-role integration --url "$$($(ERPNEXT_TF) output -raw erpnext_url)"

onboard-erpnext: ## Admit 45 approved tools; stop on unreviewed revisions or ownership.
	@test "$(CONFIRM)" = "yes" || { echo 'Tool onboarding requires CONFIRM=yes.' >&2; exit 1; }
	./scripts/onboard-erpnext-openapi.sh --confirm

verify-erpnext: ## Read-only MCP and real ERPNext record verification.
	python3 scripts/verify-erpnext-demo.py

verify-erpnext-write: ## Create/read/delete one unique temporary Item; preserve the baseline.
	@test "$(CONFIRM)" = "yes" || { echo 'Write smoke test requires CONFIRM=yes.' >&2; exit 1; }
	python3 scripts/verify-erpnext-demo.py --write

erpnext-status: ## Show VM power and HTTPS readiness.
	./scripts/erpnext-vm.sh status

erpnext-start: ## Start only the named ERPNext VM after explicit confirmation.
	@test "$(CONFIRM)" = "yes" || { echo 'Starting compute requires CONFIRM=yes.' >&2; exit 1; }
	./scripts/erpnext-vm.sh start --confirm

erpnext-ready: ## Wait for the named VM and ERPNext HTTPS endpoint.
	./scripts/erpnext-vm.sh ready

erpnext-deallocate: ## Deallocate the named VM, preserving its disk and data.
	@test "$(CONFIRM)" = "yes" || { echo 'Stopping the demo requires CONFIRM=yes.' >&2; exit 1; }
	./scripts/erpnext-vm.sh deallocate --confirm

erpbridge-vm-init: ## Initialize the isolated dedicated ERPBridge VM root, without applying resources.
	$(ERBRIDGE_TF) init -input=false

erpbridge-vm-validate: erpbridge-vm-init ## Validate the isolated dedicated ERPBridge VM root.
	$(ERBRIDGE_TF) validate -no-color

erpbridge-vm-plan: erpbridge-vm-validate ## Save a VM/disk/backup plan for cost and replacement review.
	$(ERBRIDGE_TF) plan -input=false -out=tfplan

erpbridge-vm-apply: ## Apply only the reviewed saved plan after explicit spending approval.
	@test "$(CONFIRM)" = "yes" || { echo 'Requires approved cost/plan and CONFIRM=yes.' >&2; exit 1; }
	@test -f terraform-erpbridge/tfplan || { echo 'No reviewed saved plan; run make erpbridge-vm-plan first.' >&2; exit 1; }
	$(ERBRIDGE_TF) apply -input=false tfplan

.PHONY: erpbridge-snapshot-helper

erpbridge-snapshot-helper: ## Build the static snapshot helper that runs inside the container.
	mkdir -p artifacts
	cd demo/erpbridge/sqlite-snapshot && GOPROXY=off CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
		go build -trimpath -o "$(CURDIR)/artifacts/erpbridge-sqlite-snapshot" .
	@echo 'Built artifacts/erpbridge-sqlite-snapshot. Its SHA-256 is verified on the VM before execution.'

.PHONY: erpbridge-hosting

erpbridge-hosting: ## Print the ERPBridge hosting mode, fence state, and DNS record shape.
	$(TF) output -json erpbridge_hosting

.PHONY: erpbridge-install-monitoring

erpbridge-install-monitoring: ## Install only bounded monitoring assets through the approved Azure channel.
	@test "$(CONFIRM)" = "yes" || { echo 'Monitoring installation requires CONFIRM=yes.' >&2; exit 1; }
	python3 scripts/erpbridge-vm.py install-monitoring --confirm --transport azure-run-command

test: ## Run local HTTP/CLI fixtures and mocked Terraform contracts, not cloud acceptance.
	python3 -m unittest discover -s tests -v
