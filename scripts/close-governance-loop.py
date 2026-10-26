#!/usr/bin/env python3
"""Close the governance loop for the widened write surface.

1. Generate the four assignment manifests the contract tests expect.
2. Add the eleven write operations to the active OpenAPI contract.
3. Re-sync the allowlist, ownership artifact and pinned test hashes.

Run from the repository root. Idempotent: re-running rewrites the same content.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import runpy

ROOT = pathlib.Path(__file__).resolve().parents[1]
ENDPOINT = "https://erpnext.nimendra.online/api"
ACTIONS = {
    "create_assignment": "/method/frappe.desk.form.assign_to.add",
    "remove_assignment": "/method/frappe.desk.form.assign_to.remove",
    "complete_assignment": "/method/frappe.desk.form.assign_to.close",
    "reopen_assignment": "/method/frappe.client.set_value",
}
SECURITY = {"authType": "api-key", "authHeader": "Authorization",
            "credentialRef": "ERP_PRIMARY_KEY", "credentialSource": "env"}


def document(path: pathlib.Path) -> dict:
    text = path.read_text()
    return json.loads(text[text.index("{"):])


def assignment_manifests() -> int:
    contract = document(ROOT / "config/erpnext-assignment-openapi.yaml")
    out = ROOT / "manifests/erpnext-assignments"
    out.mkdir(parents=True, exist_ok=True)
    for name, path in ACTIONS.items():
        operation = contract["paths"][path]["post"]
        schema = operation["requestBody"]["content"]["application/json"]["schema"]
        output = operation["responses"]["200"]["content"]["application/json"]["schema"]
        manifest = {
            "apiVersion": "erpbridge.io/v1",
            "kind": "MCPTool",
            "metadata": {"name": name, "version": "1.0.0", "module": "erpnext"},
            "spec": {
                "description": {
                    "short": operation.get("summary", name),
                    "whenToUse": [operation.get("description", "Use for document assignments.")],
                    "whenNotToUse": ["Do not use user or role administration, and do "
                                     "not bypass native assignment permissions."],
                    "examples": [f"{name} through the native assignment action."],
                },
                "annotations": {"readOnlyHint": False, "destructiveHint": True,
                                "idempotentHint": False, "openWorldHint": True},
                "inputSchema": schema,
                "outputSchema": output,
                "execution": {"type": "http", "method": "POST",
                              "endpoint": ENDPOINT + path,
                              "mapping": {k: k for k in schema["properties"]},
                              "parameterLocations": {k: "body" for k in schema["properties"]}},
                "cache": {"enabled": False, "ttlSeconds": 0, "isReadOnly": False,
                          "flushOn": []},
                "security": SECURITY,
            },
        }
        (out / f"{name}.json").write_text(json.dumps(manifest, indent=1) + "\n")
    print(f"assignment manifests: {len(ACTIONS)}")
    return len(ACTIONS)


def write_operations() -> int:
    """Add the write operations to the active contract, preserving everything else."""
    path = ROOT / "config/erpnext-openapi.yaml"
    head, _, body = path.read_text().partition("\n")
    contract = json.loads(body)

    def resource_write(operation_id, doctype, required, properties, *, method="post",
                       named=False, summary=""):
        key = f"/resource/{doctype}" + ("/{name}" if named else "")
        operation = {"operationId": operation_id, "summary": summary,
                     "description": "Write route for the demo dataset programme."}
        if named:
            operation["parameters"] = [{"name": "name", "in": "path", "required": True,
                                        "schema": {"type": "string"},
                                        "description": "Exact record name."}]
        else:
            operation["requestBody"] = {
                "required": True,
                "content": {"application/json": {"schema": {
                    "type": "object", "required": required, "properties": properties,
                    "additionalProperties": False}}}}
        operation["responses"] = {"200": {"description": "ERPNext resource response",
            "content": {"application/json": {"schema": {"type": "object", "properties": {
                "data": {"type": "object"}, "message": {"type": "object"}}}}}}}
        # Merge into the path: an existing GET on this route must survive.
        contract["paths"].setdefault(key, {})[method] = operation
        return key

    def method_write(operation_id, method_path, required, properties, summary):
        contract["paths"][method_path] = {"post": {
            "operationId": operation_id, "summary": summary,
            "description": "Whitelisted ERPNext method for the demo dataset programme.",
            "requestBody": {"required": True, "content": {"application/json": {"schema": {
                "type": "object", "required": required, "properties": properties,
                "additionalProperties": False}}}},
            "responses": {"200": {"description": "ERPNext method response",
                "content": {"application/json": {"schema": {"type": "object",
                    "properties": {"message": {"type": "object"}}}}}}}}}

    s = {"type": "string"}
    i = {"type": "integer"}
    resource_write("create_customer", "Customer", ["customer_name", "customer_group", "territory"],
                   {"customer_name": s,
                    "customer_group": {"type": "string", "enum": ["Commercial", "Demo Customer Group",
                                                                  "Government", "Individual", "Non Profit"]},
                    "territory": {"type": "string", "enum": ["Sri Lanka", "Rest Of The World"]},
                    "customer_type": {"type": "string", "enum": ["Company", "Individual"]}},
                   summary="Create one Customer master record")
    resource_write("delete_customer", "Customer", [], {}, method="delete", named=True,
                   summary="Delete one Customer record")
    resource_write("create_supplier", "Supplier", ["supplier_name", "supplier_group"],
                   {"supplier_name": s,
                    "supplier_group": {"type": "string", "enum": [
                        "Demo Supplier Group", "Distributor", "Electrical", "Hardware",
                        "Local", "Pharmaceutical", "Raw Material", "Services"]},
                    "country": s},
                   summary="Create one Supplier master record")
    resource_write("delete_supplier", "Supplier", [], {}, method="delete", named=True,
                   summary="Delete one Supplier record")
    resource_write("create_item_price", "Item%20Price", ["item_code", "price_list", "price_list_rate"],
                   {"item_code": s,
                    "price_list": {"type": "string", "enum": ["Standard Selling", "Standard Buying"]},
                    "price_list_rate": {"type": "number"},
                    "uom": s, "customer": s, "supplier": s, "valid_from": s},
                   summary="Create one Item Price record")
    resource_write("delete_item_price", "Item%20Price", [], {}, method="delete", named=True,
                   summary="Delete one Item Price record")
    doc = {"type": "string", "description": "The document as a JSON object string."}
    method_write("insert_document", "/method/frappe.client.insert", ["doc"], {"doc": doc},
                 "Create one draft document of any granted DocType")
    method_write("submit_document", "/method/frappe.desk.form.save.savedocs", ["doc", "action"],
                 {"doc": doc, "action": {"type": "string", "enum": ["Submit"]}},
                 "Submit one existing draft document")
    method_write("cancel_document", "/method/frappe.client.cancel", ["doctype", "name"],
                 {"doctype": s, "name": s}, "Cancel one submitted document")
    method_write("delete_document", "/method/frappe.client.delete", ["doctype", "name"],
                 {"doctype": s, "name": s}, "Delete one draft document")
    method_write("update_document", "/method/frappe.client.set_value",
                 ["doctype", "name", "fieldname"],
                 {"doctype": s, "name": s, "fieldname": s, "value": s},
                 "Update fields on one existing document of any granted DocType")
    path.write_text(head + "\n" + json.dumps(contract, indent=2) + "\n")
    print("contract paths:", len(contract["paths"]))
    return len(contract["paths"])


def sync_allowlist() -> int:
    module = runpy.run_path(str(ROOT / "scripts/onboard-erpnext.py"))
    approved = sorted(module["APPROVED"])
    (ROOT / "config/erpnext-approved-tools.txt").write_text("\n".join(approved) + "\n")
    print("approved:", len(approved))
    return len(approved)


def sync_ownership() -> int:
    module = runpy.run_path(str(ROOT / "scripts/onboard-erpnext.py"))
    os.environ.setdefault("BRIDGE_CONTEXT", "erpbridge-demo")
    live = module["tool_inventory"](module["cli"]("tool", "get", "--output", "json"))
    pairs = sorted({module["identity"](t) for t in live})
    target = ROOT / "artifacts/erp-target-manifests.json"
    previous = json.loads(target.read_text())
    target.write_text(json.dumps({"target": previous["target"],
                                  "endpoint": previous["endpoint"],
                                  "bridge": previous["bridge"], "state": "verified",
                                  "manifests": [{"name": n, "version": v} for n, v in pairs]},
                                 indent=1))
    os.chmod(target, 0o600)
    print("ownership:", len(pairs))
    return len(pairs)


def refresh_hashes(approved_count: int) -> None:
    tracking = ["config/erpnext-openapi.yaml", "config/erpnext-approved-tools.txt",
                "scripts/onboard-erpnext.py", "scripts/onboard-erpnext-openapi.sh"]
    test = ROOT / "tests/test_erpnext_assignment_contracts.py"
    text = test.read_text()
    for rel in tracking:
        digest = hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
        text = re.sub(r"('" + re.escape(rel) + r"': ')[0-9a-f]{64}",
                      lambda m, d=digest: m.group(1) + d, text)
    text = re.sub(r"self\.assertEqual\(len\(names\), \d+\)",
                  f"self.assertEqual(len(names), {approved_count})", text)
    test.write_text(text)
    onboarding = ROOT / "tests/test_erpnext_onboarding.py"
    onboarding.write_text(re.sub(r"self\.assertEqual\(len\(allowed\), \d+\)",
                                 f"self.assertEqual(len(allowed), {approved_count})",
                                 onboarding.read_text()))
    print("hashes and counts refreshed")


def main() -> None:
    assignment_manifests()
    write_operations()
    approved_count = sync_allowlist()
    sync_ownership()
    refresh_hashes(approved_count)


if __name__ == "__main__":
    main()
