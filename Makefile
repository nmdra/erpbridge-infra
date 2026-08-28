SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.ONESHELL:

TERRAFORM ?= terraform
AZ ?= az
TF_DIR := terraform
TF := $(TERRAFORM) -chdir=$(TF_DIR)

.PHONY: help check-tools init fmt fmt-check validate preflight plan apply destroy-plan destroy output summary tokens ingress-sources health verify-ingress

help: ## Show available Terraform operations.
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z0-9_-]+:.*##/ {printf "%-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

check-tools: ## Verify Terraform, Azure CLI, curl, and jq are available.
	@command -v $(TERRAFORM) >/dev/null
	@command -v $(AZ) >/dev/null
	@command -v curl >/dev/null
	@command -v jq >/dev/null

init: check-tools ## Initialize Terraform providers in the single root.
	$(TF) init -input=false

fmt: ## Format Terraform files.
	$(TF) fmt -recursive

fmt-check: ## Check Terraform formatting without modifying files.
	$(TF) fmt -check -recursive

validate: init ## Validate Terraform configuration.
	$(TF) validate -no-color

preflight: check-tools fmt-check validate ## Run local non-destructive checks.

plan: preflight ## Create terraform/tfplan from the current TF_VAR_* inputs.
	$(TF) plan -input=false -out=tfplan

apply: preflight ## Interactively apply the current TF_VAR_* inputs.
	$(TF) apply

destroy-plan: preflight ## Create a destroy plan for review.
	$(TF) plan -destroy -input=false -out=destroy.tfplan

destroy: preflight ## Interactively destroy only resources in this Terraform state.
	$(TF) destroy

output: ## Print all Terraform outputs, including demo credentials.
	$(TF) output

summary: ## Print the non-sensitive deployment summary as JSON.
	$(TF) output -json deployment_summary

tokens: ## Print demo-only bridge and upstream credentials as JSON.
	@echo 'WARNING: demo credentials will be written to this terminal.'
	$(TF) output -json demo_tokens

ingress-sources: ## Print configured MockERP ingress sources and ACA public egress IPs.
	@echo 'Configured MockERP ingress sources:'
	$(TF) output -json mockerp_ingress_sources
	@echo
	@echo 'Azure-reported ERPBridge public egress IPs (informational):'
	@$(AZ) containerapp show --name erpbridge-server --resource-group erpbridge-hq --query 'properties.outboundIpAddresses' -o json

health: ## Check public ERPBridge and MockERP health endpoints.
	bridge_url="$$($(TF) output -raw erpbridge_url)"
	mockerp_url="$$($(TF) output -raw mockerp_url)"
	curl --fail --silent --show-error --max-time 15 "$$bridge_url/mcp/health" >/dev/null
	curl --fail --silent --show-error --max-time 15 "$$mockerp_url/health" >/dev/null
	@echo 'health checks passed'

verify-ingress: health ## Verify ERPBridge can call MockERP through MCP.
	mcp_url="$$($(TF) output -raw erpbridge_mcp_url)"
	api_token="$$($(TF) output -json demo_tokens | jq -r '.api_auth_token')"
	tmp_dir="$$(mktemp -d)"
	trap 'rm -rf "$$tmp_dir"' EXIT
	curl --fail --silent --show-error --max-time 20 -D "$$tmp_dir/init.headers" -o "$$tmp_dir/init.body" \
		-X POST "$$mcp_url" \
		-H "Authorization: Bearer $$api_token" \
		-H 'Content-Type: application/json' \
		-H 'Accept: application/json, text/event-stream' \
		-H 'Mcp-Protocol-Version: 2025-06-18' \
		--data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"terraform-verify","version":"1"}}}'
	session_id="$$(awk 'BEGIN {IGNORECASE=1} /^Mcp-Session-Id:/ {gsub("\r", "", $$2); print $$2}' "$$tmp_dir/init.headers")"
	test -n "$$session_id"
	curl --fail --silent --show-error --max-time 20 -o /dev/null \
		-X POST "$$mcp_url" \
		-H "Authorization: Bearer $$api_token" \
		-H 'Content-Type: application/json' \
		-H 'Accept: application/json, text/event-stream' \
		-H 'Mcp-Protocol-Version: 2025-06-18' \
		-H "Mcp-Session-Id: $$session_id" \
		--data '{"jsonrpc":"2.0","method":"notifications/initialized"}'
	curl --fail --silent --show-error --max-time 30 -o "$$tmp_dir/call.body" \
		-X POST "$$mcp_url" \
		-H "Authorization: Bearer $$api_token" \
		-H 'Content-Type: application/json' \
		-H 'Accept: application/json, text/event-stream' \
		-H 'Mcp-Protocol-Version: 2025-06-18' \
		-H "Mcp-Session-Id: $$session_id" \
		--data '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"list-warehouses","arguments":{"page":1}}}'
	jq -e '[.. | objects | select(.isError == true)] | length == 0' "$$tmp_dir/call.body" >/dev/null
	@echo 'ERPBridge-to-MockERP ingress verification passed'
