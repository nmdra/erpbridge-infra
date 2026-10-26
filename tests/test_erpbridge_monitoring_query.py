"""Local query-construction fixtures, separate from actual live KQL execution."""
import importlib.util
import json
from pathlib import Path
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("verify_monitoring", ROOT / "scripts/verify-erpbridge-monitoring.py")
assert SPEC is not None and SPEC.loader is not None
verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify)
VM = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/demo/providers/Microsoft.Compute/virtualMachines/erpbridge-vm"


class QueryContracts(unittest.TestCase):
    def test_seed_and_event_time_are_in_the_production_query(self):
        query = verify.production_query(VM)
        self.assertIn("datatable(ExpectedVm:string)", query)
        self.assertIn("join kind=leftouter latestVmSamples", query)
        self.assertIn("arg_max(TimeGenerated, *)", query)
        self.assertIn("TimeGenerated < ago(10m)", query)
        self.assertNotIn("ingestion_time", query)
        self.assertNotIn("let latest =", query)
        self.assertNotIn("${", query)

    def test_fixture_coverage_and_read_only_queries(self):
        cases = json.loads((ROOT / "tests/fixtures/erpbridge-monitoring/health-cases.json").read_text())
        self.assertTrue({"healthy", "seven-minutes-old", "older-than-ten-minutes", "never-seen",
                         "delayed-replay", "multiple-faults", "fresh-recovery"}.issubset({c["id"] for c in cases}))
        for case in cases:
            query = verify.fixture_query(case, VM)
            self.assertTrue(query.startswith("let ERPBridgeHealth_CL = (datatable("))
            self.assertNotIn(".ingest", query)
            self.assertNotIn(".set", query)
        self.assertNotIn("| union", verify.fixture_query(next(c for c in cases if c["id"] == "never-seen"), VM).split(";", 1)[0])

    def test_reject_invalid_identity_and_age_before_remote_call(self):
        with self.assertRaises(ValueError):
            verify.production_query(VM + "'; .drop table")
        with self.assertRaises(ValueError):
            verify.fixture_query({"rows": [{"age_seconds": "0; unsafe"}]}, VM)
        with mock.patch.object(verify.subprocess, "run") as remote:
            with self.assertRaises(ValueError):
                verify.query("invalid", "print 1")
            remote.assert_not_called()

    def test_collector_schema_matches_declared_types(self):
        schema = json.loads((ROOT / "terraform-erpbridge/monitoring-schema.json").read_text())
        collector = importlib.util.spec_from_file_location("collector_fixture", ROOT / "tests/test_erpbridge_monitor.py")
        assert collector is not None and collector.loader is not None
        module = importlib.util.module_from_spec(collector)
        collector.loader.exec_module(module)
        types = {"datetime": str, "string": str, "long": int, "int": int, "real": float, "boolean": bool}
        self.assertEqual({column["name"]: types[column["type"]] for column in schema}, module.FIELDS)


if __name__ == "__main__":
    unittest.main()
