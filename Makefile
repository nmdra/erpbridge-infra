SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.ONESHELL:

TERRAFORM ?= terraform
AZ ?= az
TF_DIR := terraform
TF := $(TERRAFORM) -chdir=$(TF_DIR)

.PHONY: help check-tools init fmt fmt-check validate preflight plan apply destroy-plan destroy output summary tokens ingress-sources health check-bridge-compatibility verify-deployment verify-ingress export-output onboard-mockerp

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

fmt: ## Format Terraform files.
	$(TF) fmt -recursive

fmt-check: ## Check Terraform formatting without modifying files.
	$(TF) fmt -check -recursive

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

health: ## Check canonical ERPBridge and MockERP health endpoints.
	bridge_url="$$($(TF) output -raw erpbridge_url)"
	mockerp_url="$$($(TF) output -raw mockerp_url)"
	curl --fail --silent --show-error --max-time 20 "$$bridge_url/mcp/health" >/dev/null
	curl --fail --silent --show-error --max-time 20 "$$mockerp_url/health" >/dev/null
	@echo 'health checks passed'

check-bridge-compatibility: ## Verify the deployed server matches bridgectl control-plane routes.
	./scripts/check-bridge-compatibility.sh

verify-deployment: ## Verify DNS, TLS, health, auth, MCP, and ERPBridge-to-MockERP flow.
	./scripts/verify-deployment.sh

verify-ingress: verify-deployment ## Backward-compatible alias for the full deployment verification.

export-output: ## Write URLs, values, images, and demo credentials to a protected artifact.
	./scripts/export-demo-output.sh

onboard-mockerp: check-bridge-compatibility ## Generate, validate, apply, and verify approved MockERP tools.
	./scripts/onboard-mockerp-openapi.sh
