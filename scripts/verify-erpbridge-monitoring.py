#!/usr/bin/env python3
"""Execute read-only Log Analytics commissioning queries. Never ingest fixture rows."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
FAULTS = ("NoData", "Service", "Disk", "Backup")
VM_ID = re.compile(r"/subscriptions/[0-9a-fA-F-]{36}/resourceGroups/[A-Za-z0-9_.()-]+/providers/Microsoft.Compute/virtualMachines/[A-Za-z0-9_.-]+", re.I)


def production_query(vm_id):
    if not VM_ID.fullmatch(vm_id):
        raise ValueError("Invalid VM resource ID")
    return (ROOT / "terraform-erpbridge/monitoring/health-current.kql.tftpl").read_text().replace("${vm_resource_id}", vm_id.lower())


def fixture_query(case, vm_id):
    columns = "TimeGenerated:datetime,VmResourceId:string,ServiceFault:bool,DiskFault:bool,BackupFailed:bool,BackupStatusValid:bool,BackupAgeSeconds:long"
    rows = []
    for row in case["rows"]:
        age = row["age_seconds"]
        if type(age) is not int or abs(age) > 86400:
            raise ValueError("Invalid fixture age")
        values = {
            "TimeGenerated": f"now() - ({age}s)",
            "VmResourceId": json.dumps(vm_id.lower() + ("-other" if row.get("other_vm") else "")),
            "ServiceFault": str(row.get("ServiceFault", False)).lower(),
            "DiskFault": str(row.get("DiskFault", False)).lower(),
            "BackupFailed": str(row.get("BackupFailed", False)).lower(),
            "BackupStatusValid": str(row.get("BackupStatusValid", True)).lower(),
            "BackupAgeSeconds": str(row.get("BackupAgeSeconds", 3600)),
        }
        rows.append("print " + ", ".join(f"{key}={value}" for key, value in values.items()))
    source = "datatable(" + columns + ")[]"
    for row in rows:
        source += " | union (" + row + ")"
    # A local let binding shadows the table only inside this query. No rows are
    # written to the workspace and no production alert predicate is changed.
    return "let ERPBridgeHealth_CL = (" + source + ");\n" + production_query(vm_id) + "\n| project " + ", ".join(FAULTS)


def query(workspace_id, kql):
    arm_workspace = re.fullmatch(r"/subscriptions/[0-9a-fA-F-]{36}/resourceGroups/[A-Za-z0-9_.()-]+/providers/Microsoft.OperationalInsights/workspaces/[A-Za-z0-9_.-]+", workspace_id, re.I)
    if not arm_workspace and not re.fullmatch(r"[0-9a-fA-F-]{36}", workspace_id):
        raise ValueError("Invalid workspace identity")
    url = ("https://management.azure.com" + workspace_id + "/api/query?api-version=2017-01-01-preview"
           if arm_workspace else f"https://api.loganalytics.azure.com/v1/workspaces/{workspace_id}/query")
    audience = "https://management.azure.com/" if arm_workspace else "https://api.loganalytics.io"
    with tempfile.TemporaryDirectory(prefix="erpbridge-kql-") as directory:
        path = Path(directory) / "query.json"
        path.write_text(json.dumps({"query": kql, "timespan": "PT1H"}))
        path.chmod(0o600)
        result = subprocess.run([
            "az", "rest", "--method", "post", "--url",
            url, "--resource", audience, "--body", "@" + str(path),
            "--only-show-errors", "-o", "json",
        ], capture_output=True, text=True, timeout=60)
    if result.returncode or len(result.stdout) > 65536:
        raise ValueError("Log Analytics query failed; provider response suppressed")
    response = json.loads(result.stdout)
    if response.get("error"):
        raise ValueError("Log Analytics returned a query error; response suppressed")
    if arm_workspace:
        tables = response.get("Tables", [])
        for table in tables:
            columns = [column["ColumnName"] for column in table.get("Columns", [])]
            if "StatusCode" in columns:
                index = columns.index("StatusCode")
                if any(row[index] != 0 for row in table.get("Rows", [])):
                    raise ValueError("ARM query reported a partial or execution error")
        if not tables:
            raise ValueError("ARM query returned no result")
        first = tables[0]
        return [dict(zip([column["ColumnName"] for column in first["Columns"]], row)) for row in first["Rows"]]
    tables = response.get("tables", [])
    if len(tables) != 1:
        raise ValueError("Unexpected query table count")
    table = tables[0]
    names = [column["name"] for column in table["columns"]]
    return [dict(zip(names, row)) for row in table["rows"]]


def run(workspace_id, vm_id, include_fixtures=True):
    if include_fixtures:
        cases = json.loads((ROOT / "tests/fixtures/erpbridge-monitoring/health-cases.json").read_text())
        for case in cases:
            rows = query(workspace_id, fixture_query(case, vm_id))
            if len(rows) != 1 or set(rows[0]) != set(FAULTS):
                raise ValueError("Fixture did not return exactly one seeded row")
            actual = [key for key in FAULTS if rows[0][key] is True]
            if actual != case["faults"]:
                raise ValueError("KQL fixture mismatch: " + case["id"])
            print(json.dumps({"fixture": case["id"], "passed": True}))
    rows = query(workspace_id, production_query(vm_id) + "\n| project NoData, Service, Disk, Backup, SampleAgeSeconds")
    if len(rows) != 1:
        raise ValueError("Live query did not return one VM")
    row = rows[0]
    print(json.dumps({"live": True, **{key: row.get(key) for key in (*FAULTS, "SampleAgeSeconds")}}))
    return 0 if row.get("NoData") is False else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-id", required=True,
                        help="Workspace customer UUID, or full workspace ARM ID for the TLS-verified ARM query channel.")
    parser.add_argument("--vm-resource-id", required=True)
    parser.add_argument("--live-only", action="store_true")
    args = parser.parse_args()
    try:
        return run(args.workspace_id, args.vm_resource_id, not args.live_only)
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        print(json.dumps({"verified": False, "reason": "Query or fixture validation failed; raw response suppressed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
