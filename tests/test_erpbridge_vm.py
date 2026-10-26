#!/usr/bin/env python3
"""ERPBridge VM migration helper tests with fake local executables, not Azure."""
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "erpbridge-vm.py"
SUBSCRIPTION = "11111111-2222-3333-4444-555555555555"
IMAGE = "ghcr.io/nmdra/erpbridge-server@sha256:" + "a" * 64
ENV_SENTINEL = "SENTINEL-ENV-VALUE"
SECRET_SENTINEL = "SENTINEL-SECRET-VALUE"

FAKE = '''#!/usr/bin/env python3
import json, os, sys
name = os.path.basename(sys.argv[0])
with open(os.environ['CALLS'], 'a') as stream:
    stream.write(json.dumps([name] + sys.argv[1:]) + '\\n')
if os.environ.get('FAIL') == name:
    print('PRIVATE_PROVIDER_DIAGNOSTIC', file=sys.stderr)
    sys.exit(1)
if name == 'az':
    if sys.argv[1:3] == ['account', 'show']:
        print(os.environ['SUB_ID'])
    elif 'containerapp' in sys.argv and 'show' in sys.argv:
        print(os.environ['APP_JSON'])
    elif 'secret' in sys.argv:
        print(os.environ.get('SECRETS_JSON', '[]'))
    elif 'replica' in sys.argv:
        print(os.environ.get('REPLICAS_JSON', '[]'))
    else:
        sys.exit(1)
elif name == 'terraform':
    if 'deployment_summary' in sys.argv:
        print(os.environ['SUMMARY_JSON'])
    elif 'cloudflare_domains' in sys.argv:
        print(os.environ['DOMAINS_JSON'])
    else:
        sys.exit(1)
else:
    sys.exit(1)
'''


def default_app():
    return {
        "properties": {
            "latestReadyRevisionName": "erpbridge-server--test-1234567890",
            "latestRevisionName": "erpbridge-server--test-1234567890",
            "template": {
                "scale": {"minReplicas": 1, "maxReplicas": 1},
                "containers": [{
                    "image": IMAGE,
                    "env": [
                        {"name": "MCP_PORT", "value": "8080"},
                        {"name": "MCP_TRANSPORT", "value": "http"},
                        {"name": "DATABASE_PATH", "value": "/tmp/erpbridge.db"},
                        {"name": "BASE_URL", "value": "https://erpbridge.nimendra.online"},
                        {"name": "API_AUTH_TOKEN", "secretRef": "api-auth-1234567890ab"},
                        {"name": "ERP_PRIMARY_KEY", "secretRef": "erp-primary-1234567890ab"},
                        {"name": "INTERNAL_NOTE", "value": ENV_SENTINEL},
                    ],
                }],
            },
            "configuration": {
                # The live API reports the mode here, not at properties.revisionMode.
                "activeRevisionsMode": "Single",
                "ingress": {
                    "external": True,
                    "targetPort": 8080,
                    "transport": "http",
                    "allowInsecure": False,
                    "fqdn": "erpbridge-server.example.azurecontainerapps.io",
                    "traffic": [{"latestRevision": True, "weight": 100}],
                },
            },
        },
    }


def default_summary():
    return {
        "erpbridge": {
            "name": "erpbridge-server",
            "url": "https://erpbridge.nimendra.online",
            "azure_url": "https://erpbridge-server.example.azurecontainerapps.io",
            "mcp_url": "https://erpbridge.nimendra.online/mcp/",
            "port": 8080,
        },
        "erp_target": "erpnext",
    }


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.calls = self.base / "calls"
        self.manifest = self.base / "state" / "source-manifest.json"
        fake = self.base / "fake"
        fake.write_text(FAKE)
        fake.chmod(0o700)
        for name in ("az", "terraform"):
            (self.base / name).symlink_to(fake)
        self.env = dict(
            os.environ,
            AZ=str(self.base / "az"),
            TERRAFORM=str(self.base / "terraform"),
            CALLS=str(self.calls),
            SUB_ID=SUBSCRIPTION,
            APP_JSON=json.dumps(default_app()),
            SUMMARY_JSON=json.dumps(default_summary()),
            DOMAINS_JSON=json.dumps({"enabled": True, "erpbridge": "erpbridge.nimendra.online",
                                     "mockerp": "mockerp.nimendra.online"}),
            SECRETS_JSON=json.dumps([{"name": "api-auth-1234567890ab", "value": SECRET_SENTINEL},
                                     {"name": "erp-primary-1234567890ab", "value": SECRET_SENTINEL}]),
            REPLICAS_JSON=json.dumps([{"name": "replica-1", "runningState": "Running"}]),
            AZURE_SUBSCRIPTION_ID=SUBSCRIPTION,
        )
        for key in ("TF_DIR", "RESOURCE_GROUP", "BRIDGE_APP"):
            self.env.pop(key, None)

    def run_preflight(self, *args):
        result = subprocess.run(["python3", str(SCRIPT), "--manifest", str(self.manifest),
                                 "preflight", *args],
                                env=self.env, capture_output=True, text=True)
        combined = result.stdout + result.stderr
        self.assertNotIn(SECRET_SENTINEL, combined, "a secret value reached the terminal")
        self.assertNotIn("PRIVATE_PROVIDER_DIAGNOSTIC", combined, "provider diagnostics leaked")
        return result

    def manifest_data(self):
        return json.loads(self.manifest.read_text())

    def test_happy_path_writes_protected_manifest(self):
        result = self.run_preflight()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Serving revision: erpbridge-server--test-1234567890", result.stdout)
        self.assertIn("No secret value was read, printed, or stored", result.stdout)
        mode = stat.S_IMODE(self.manifest.stat().st_mode)
        self.assertEqual(mode, 0o600)
        self.assertEqual(stat.S_IMODE(self.manifest.parent.stat().st_mode), 0o700)
        data = self.manifest_data()
        self.assertEqual(data["source"]["image"], IMAGE)
        self.assertEqual(data["source"]["secret_env_names"], ["API_AUTH_TOKEN", "ERP_PRIMARY_KEY"])
        self.assertEqual(data["blockers"], [])
        self.assertNotIn(ENV_SENTINEL, json.dumps(data), "a non-allowlisted env value was stored")
        secrets_json = json.dumps(data)
        self.assertNotIn(SECRET_SENTINEL, secrets_json)
        self.assertEqual(data["source"]["plain_env"]["BASE_URL"], "https://erpbridge.nimendra.online")

    def test_missing_required_secret_reference_blocks(self):
        app = default_app()
        env = [item for item in app["properties"]["template"]["containers"][0]["env"]
               if item["name"] != "ERP_PRIMARY_KEY"]
        app["properties"]["template"]["containers"][0]["env"] = env
        self.env["APP_JSON"] = json.dumps(app)
        result = self.run_preflight()
        self.assertEqual(result.returncode, 1)
        self.assertIn("required_secret_references", result.stderr)
        self.assertIn("required_secret_references", json.dumps(self.manifest_data()["blockers"]))

    def test_unresolvable_secret_reference_blocks(self):
        self.env["SECRETS_JSON"] = json.dumps([{"name": "api-auth-1234567890ab", "value": SECRET_SENTINEL}])
        result = self.run_preflight()
        self.assertEqual(result.returncode, 1)
        self.assertIn("secret_references_resolvable", result.stderr)

    def test_unpinned_image_fails_before_manifest(self):
        app = default_app()
        app["properties"]["template"]["containers"][0]["image"] = "ghcr.io/nmdra/erpbridge-server:latest"
        self.env["APP_JSON"] = json.dumps(app)
        result = self.run_preflight()
        self.assertEqual(result.returncode, 1)
        self.assertIn("pinned by sha256 digest", result.stderr)
        self.assertFalse(self.manifest.exists())

    def test_ingress_and_scale_violations_block(self):
        cases = {
            "traffic": lambda app: app["properties"]["configuration"]["ingress"].update(
                {"traffic": [{"latestRevision": True, "weight": 50}]}),
            "external": lambda app: app["properties"]["configuration"]["ingress"].update({"external": False}),
            "insecure": lambda app: app["properties"]["configuration"]["ingress"].update({"allowInsecure": True}),
            "port": lambda app: app["properties"]["configuration"]["ingress"].update({"targetPort": 9090}),
        }
        for label, mutate in cases.items():
            with self.subTest(case=label):
                app = default_app()
                mutate(app)
                self.env["APP_JSON"] = json.dumps(app)
                self.manifest.unlink(missing_ok=True)
                result = self.run_preflight()
                self.assertEqual(result.returncode, 1, f"{label} should block")
                self.assertTrue(self.manifest_data()["blockers"])

    def test_two_replicas_block(self):
        self.env["REPLICAS_JSON"] = json.dumps([{"name": "replica-1"}, {"name": "replica-2"}])
        result = self.run_preflight()
        self.assertEqual(result.returncode, 1)
        self.assertIn("one_replica", result.stderr)

    def test_missing_custom_domain_blocks_with_remediation(self):
        self.env["DOMAINS_JSON"] = json.dumps({"enabled": False, "erpbridge": None, "mockerp": None})
        result = self.run_preflight()
        self.assertEqual(result.returncode, 1)
        self.assertIn("canonical_custom_domain", result.stderr)

    def test_base_url_mismatch_blocks(self):
        app = default_app()
        for item in app["properties"]["template"]["containers"][0]["env"]:
            if item["name"] == "BASE_URL":
                item["value"] = "https://erpbridge-server.example.azurecontainerapps.io"
        self.env["APP_JSON"] = json.dumps(app)
        result = self.run_preflight()
        self.assertEqual(result.returncode, 1)
        self.assertIn("base_url_matches_canonical", result.stderr)

    def test_source_database_path_is_checked(self):
        app = default_app()
        for item in app["properties"]["template"]["containers"][0]["env"]:
            if item["name"] == "DATABASE_PATH":
                item["value"] = "/data/erpbridge.db"
        self.env["APP_JSON"] = json.dumps(app)
        result = self.run_preflight()
        self.assertEqual(result.returncode, 1)
        self.assertIn("database_path_recorded", result.stderr)

    def test_drift_detection(self):
        self.assertEqual(self.run_preflight().returncode, 0)
        self.assertEqual(self.run_preflight("--check", str(self.manifest)).returncode, 0)
        app = default_app()
        app["properties"]["template"]["containers"][0]["image"] = (IMAGE.replace("a" * 64, "b" * 64))
        self.env["APP_JSON"] = json.dumps(app)
        drift = self.run_preflight("--check", str(self.manifest))
        self.assertEqual(drift.returncode, 1)
        self.assertIn("image", drift.stderr)

    def test_revision_change_is_drift(self):
        self.assertEqual(self.run_preflight().returncode, 0)
        app = default_app()
        app["properties"]["latestReadyRevisionName"] = "erpbridge-server--other-0987654321"
        self.env["APP_JSON"] = json.dumps(app)
        drift = self.run_preflight("--check", str(self.manifest))
        self.assertEqual(drift.returncode, 1)
        self.assertIn("latest_ready_revision", drift.stderr)

    def test_wrong_subscription_fails(self):
        self.env["AZURE_SUBSCRIPTION_ID"] = "99999999-2222-3333-4444-555555555555"
        result = self.run_preflight()
        self.assertEqual(result.returncode, 1)
        self.assertIn("does not match the expected subscription", result.stderr)
        self.assertFalse(self.manifest.exists())

    def test_app_and_provider_failures_are_safe(self):
        self.env["FAIL"] = "az"
        result = self.run_preflight()
        self.assertEqual(result.returncode, 1)
        self.assertIn("provider diagnostics suppressed", result.stderr)
        self.env.pop("FAIL")
        self.env["APP_JSON"] = "not json"
        result = self.run_preflight()
        self.assertEqual(result.returncode, 1)
        self.assertIn("not valid JSON", result.stderr)

    def test_only_expected_azure_reads_are_issued(self):
        self.assertEqual(self.run_preflight().returncode, 0)
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        for call in calls:
            if call[0] == "az":
                self.assertNotIn("delete", call)
                self.assertNotIn("update", call)
                self.assertNotIn("exec", call)
        self.assertTrue(any("secret" in call and "list" in call for call in calls))

    def test_argument_errors_are_not_echoed(self):
        result = subprocess.run(["python3", str(SCRIPT), "preflight",
                                 "--api-auth-token", SECRET_SENTINEL],
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(SECRET_SENTINEL, result.stdout + result.stderr)
        self.assertIn("Invalid arguments", result.stderr)


class CostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.sheet = self.base / "prices.json"
        self.sheet.write_text(json.dumps({
            "currency": "USD",
            "as_of": "2026-10-17",
            "region": "centralindia",
            "meters": {
                "vm_hourly": 0.00615,
                "os_disk_monthly": 2.64,
                "static_ip_monthly": 3.65,
                "backup_capacity_gb_monthly": 0.02,
                "backup_operations_per_10k": 0.05,
                "outbound_gb": 0.087,
                "aca_retained_monthly": 1.0,
            },
        }))

    def run_cost(self, *args):
        return subprocess.run(["python3", str(SCRIPT), "cost", "--price-sheet", str(self.sheet), *args],
                              capture_output=True, text=True)

    def test_itemized_estimate_matches_arithmetic(self):
        result = self.run_cost("--running-hours", "730", "--backup-gb", "5",
                              "--backup-operations", "10000", "--aca-retained-months", "0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("4.4895", result.stdout)
        self.assertIn("10.9295", result.stdout)
        self.assertIn("not a bill", result.stdout)
        self.assertIn("USD (price sheet 2026-10-17, region centralindia)", result.stdout)

    def test_invalid_quantities_and_meters_rejected(self):
        for value in ("-1", "NaN", "Infinity", "abc"):
            with self.subTest(value=value):
                self.assertNotEqual(self.run_cost("--running-hours", value).returncode, 0)
        broken = json.loads(self.sheet.read_text())
        del broken["meters"]["os_disk_monthly"]
        self.sheet.write_text(json.dumps(broken))
        result = self.run_cost("--running-hours", "1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing meters", result.stderr)

    def test_missing_price_sheet_is_safe(self):
        result = subprocess.run(["python3", str(SCRIPT), "cost", "--price-sheet",
                                 str(self.base / "absent.json"), "--running-hours", "1"],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not be read", result.stderr)


STORAGE_ENDPOINT = "https://erpbridgebackupfixture.blob.core.windows.net"
GUARD = ROOT / "demo" / "erpbridge" / "require-database.sh"
BOOTSTRAP = ROOT / "demo" / "erpbridge" / "cloud-config-runtime.sh"

FAKE_TOOLBOX = '''#!/usr/bin/env python3
import json, os, subprocess, sys
name = os.path.basename(sys.argv[0])
args = sys.argv[1:]
with open(os.environ['CALLS'], 'a') as stream:
    stream.write(json.dumps([name] + args) + '\\n')
if name == 'sqlite3':
    print(os.environ.get('SQLITE_RESULT', 'ok'))
    sys.exit(int(os.environ.get('SQLITE_EXIT', '0')))
if name == 'findmnt':
    print(os.environ.get('FAKE_FS_UUID', 'fs-uuid-fixed'))
    sys.exit(0)
if name == 'ssh-keyscan':
    print('203.0.113.42 ssh-rsa ' + os.environ.get('HOST_KEY', 'ZmFrZS1ob3N0LWtleQ=='))
    sys.exit(0)
if name == 'ssh':
    if os.environ.get('FAIL') == 'ssh':
        print('PRIVATE_REMOTE_DIAGNOSTIC', file=sys.stderr)
        sys.exit(1)
    remote = args[-1] if args else ''
    if "stat -c '%a %U'" in remote:
        print(os.environ.get('STAT_LINE', '600 root'))
        print(os.environ.get('STAT_COUNT', '3'))
    elif 'cloud-config-runtime.sh' in remote:
        print('Bootstrap complete. No runtime env yet: install it, then restore, then start.')
    elif 'install-monitoring.py' in remote:
        print(json.dumps({'monitor_timer': 'active', 'monitor_service': 'inactive', 'backup_age_seconds': 10, 'last_sample_age_seconds': 1}))
    elif 'is-active' in remote or 'systemctl start' in remote:
        print(os.environ.get('SERVICE_STATE', 'active'))
    elif 's_client' in remote:
        print(os.environ.get('SERVED_FINGERPRINT',
                             'sha256 Fingerprint=' + ':'.join(['AB'] * 32)))
    else:
        print('ok')
    sys.exit(0)
if name == 'openssl':
    joined = ' '.join(args)
    if os.environ.get('FAIL') == 'openssl':
        print('PRIVATE_KEY_MATERIAL', file=sys.stderr)
        sys.exit(1)
    if '-pubkey' in args:
        print(os.environ.get('CERT_PUB', '-----BEGIN PUBLIC KEY-----\\nAAAA\\n-----END PUBLIC KEY-----'))
    elif args[:1] == ['pkey'] or '-pubout' in args:
        print(os.environ.get('KEY_PUB', '-----BEGIN PUBLIC KEY-----\\nAAAA\\n-----END PUBLIC KEY-----'))
    elif '-enddate' in args:
        print(os.environ.get('END_DATE', 'notAfter=Nov  3 12:00:00 2026 GMT'))
    elif '-fingerprint' in args:
        print(os.environ.get('CERT_FINGERPRINT',
                             'sha256 Fingerprint=' + ':'.join(['AB'] * 32)))
    elif 'subjectAltName' in joined or '-ext' in args:
        print('X509v3 Subject Alternative Name: \\n    ' + os.environ.get('SAN', 'DNS:erpbridge.nimendra.online'))
    else:
        print('notBefore=x')
    sys.exit(0)
if name == 'curl':
    if any('--write-out' in item for item in args):
        print(os.environ.get('HTTP_STATUS', '503'))
    sys.exit(int(os.environ.get('CURL_EXIT', '0')))
if name == 'tar':
    for index, item in enumerate(args):
        if item == '-czf' and index + 1 < len(args):
            open(args[index + 1], 'wb').write(b'fake-archive')
    sys.exit(0)
if name == 'terraform':
    if 'deployment_summary' in args:
        print(os.environ['VM_SUMMARY'])
    elif 'cloudflare_domains' in args:
        print(os.environ.get('DOMAINS_JSON', '{}'))
    else:
        sys.exit(1)
    sys.exit(0)
if name == 'az':
    joined = ' '.join(args)
    if args[:2] == ['account', 'show']:
        print(os.environ['SUB_ID'])
    elif 'get-instance-view' in joined:
        print(os.environ.get('POWER', 'PowerState/running'))
    elif 'secret' in args and 'list' in args:
        print(os.environ['SECRETS_JSON'])
    elif 'containerapp' in args and 'show' in args:
        print(os.environ['APP_JSON'])
    else:
        sys.exit(1)
    sys.exit(0)
if name == 'id' and args[:1] == ['-u']:
    print('0')
    sys.exit(0)
if name == 'chown':
    sys.exit(0)
if name == 'systemctl' or name == 'caddy':
    sys.exit(0)
if name == 'docker':
    if args[:2] == ['image', 'inspect']:
        sys.exit(1 if os.environ.get('IMAGE_MISSING') == '1' else 0)
    if args[:1] == ['pull']:
        sys.exit(0)
    if args[:1] == ['run']:
        print(os.environ.get('CONTAINER_UID', '1000'))
        print(os.environ.get('CONTAINER_GID', '1000'))
        sys.exit(0)
    sys.exit(0)
if name == 'install':
    cleaned, skip = [], False
    for item in args:
        if skip:
            skip = False
            continue
        if item in ('-o', '-g'):
            skip = True
            continue
        cleaned.append(item)
    sys.exit(subprocess.call(['/usr/bin/install'] + cleaned))
print('unsupported fake: ' + name, file=sys.stderr)
sys.exit(127)
'''


def make_fake_bin(base, names):
    """Create an isolated fake executable directory and return its path."""
    bindir = base / "bin"
    bindir.mkdir(exist_ok=True)
    fake = bindir / "fake"
    fake.write_text(FAKE_TOOLBOX)
    fake.chmod(0o700)
    for name in names:
        (bindir / name).symlink_to(fake)
    return bindir


class StartupGuardTests(unittest.TestCase):
    """The guard runs before every start and must block on any surprise."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.calls = self.base / "calls"
        self.bin = make_fake_bin(self.base, ["sqlite3", "findmnt"])
        self.state = self.base / "state"
        self.state.mkdir()
        self.db = self.state / "erpbridge.db"
        self.db.write_bytes(b"SQLite format 3\x00" + b"payload")
        self.etc = self.base / "etc"
        self.etc.mkdir()
        self.env_file = self.etc / "runtime.env"
        self.env_file.write_text("API_AUTH_TOKEN=value-a\nERP_PRIMARY_KEY=value-b\n"
                                 "DATABASE_PATH=/data/erpbridge.db\n")
        self.identity = self.etc / "disk-identity"
        self.identity.write_text('{\n  "schema_version": 1,\n  "filesystem_uuid": "fs-uuid-fixed"\n}\n')
        self.uid_file = self.etc / "container-uid"
        self.uid_file.write_text(f"{os.getuid()}\n")
        self.env = dict(
            os.environ,
            PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
            CALLS=str(self.calls),
            ERPBRIDGE_MOUNT_POINT=str(self.state),
            ERPBRIDGE_STATE_DIR=str(self.state),
            ERPBRIDGE_DB_PATH=str(self.db),
            ERPBRIDGE_ENV_FILE=str(self.env_file),
            ERPBRIDGE_IDENTITY_FILE=str(self.identity),
            ERPBRIDGE_UID_FILE=str(self.uid_file),
        )

    def run_guard(self):
        return subprocess.run(["sh", str(GUARD)], env=self.env, capture_output=True, text=True)

    def test_guard_passes_when_everything_matches(self):
        result = self.run_guard()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("startup guard passed", result.stdout)
        self.assertNotIn("value-a", result.stdout + result.stderr)

    def test_missing_or_empty_database_blocks(self):
        self.db.unlink()
        self.assertIn("missing database", self.run_guard().stderr)
        self.db.write_bytes(b"")
        self.assertIn("database file is empty", self.run_guard().stderr)

    def test_failed_integrity_check_blocks(self):
        self.env["SQLITE_RESULT"] = "database disk image is malformed"
        self.assertIn("integrity check failed", self.run_guard().stderr)
        self.env["SQLITE_RESULT"] = "ok"
        self.env["SQLITE_EXIT"] = "1"
        self.assertIn("could not run", self.run_guard().stderr)

    def test_replaced_os_disk_blocks(self):
        self.identity.write_text('{"filesystem_uuid": "a-different-disk"}\n')
        self.assertIn("OS disk identity changed", self.run_guard().stderr)
        self.identity.write_text("{}\n")
        self.assertIn("no filesystem UUID", self.run_guard().stderr)
        self.identity.unlink()
        self.assertIn("missing", self.run_guard().stderr)

    def test_wrong_directory_owner_blocks(self):
        self.uid_file.write_text("4242\n")
        self.assertIn("does not match the pinned image user", self.run_guard().stderr)
        self.uid_file.write_text("not-a-number\n")
        self.assertIn("not numeric", self.run_guard().stderr)

    def test_environment_file_contract_blocks(self):
        self.env_file.write_text("API_AUTH_TOKEN=a\n")
        self.assertIn("has no ERP_PRIMARY_KEY", self.run_guard().stderr)
        self.env_file.write_text("API_AUTH_TOKEN=a\nERP_PRIMARY_KEY=b\nDATABASE_PATH=/tmp/erpbridge.db\n")
        self.assertIn("DATABASE_PATH must be", self.run_guard().stderr)
        self.env_file.unlink()
        self.assertIn("missing", self.run_guard().stderr)

    def test_missing_secret_file_blocks(self):
        self.env_file.write_text("")
        self.assertIn("empty", self.run_guard().stderr)


class BootstrapScriptTests(unittest.TestCase):
    """The on-VM bootstrap prepares storage and wiring and never starts the app."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.calls = self.base / "calls"
        self.bin = make_fake_bin(self.base, ["docker", "systemctl", "caddy", "findmnt", "id", "chown",
                                             "install", "sqlite3"])
        self.assets = self.base / "assets"
        self.assets.mkdir()
        # Copy every runtime asset so the bootstrap list and this fixture cannot
        # drift apart.
        for source in sorted((ROOT / "demo" / "erpbridge").iterdir()):
            if source.is_file():
                shutil.copy2(source, self.assets / source.name)
        (self.base / "systemd").mkdir()
        (self.base / "caddy").mkdir()
        self.env = dict(
            os.environ,
            PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
            CALLS=str(self.calls),
            ERPBRIDGE_ASSET_DIR=str(self.assets),
            ERPBRIDGE_STATE_ROOT=str(self.base / "var" / "lib" / "erpbridge"),
            ERPBRIDGE_DATA_DIR=str(self.base / "var" / "lib" / "erpbridge" / "data"),
            ERPBRIDGE_CONF_DIR=str(self.base / "etc" / "erpbridge"),
            ERPBRIDGE_ENV_FILE=str(self.base / "etc" / "erpbridge" / "runtime.env"),
            ERPBRIDGE_SERVICE_ENV=str(self.base / "etc" / "erpbridge" / "service.env"),
            ERPBRIDGE_IDENTITY_FILE=str(self.base / "etc" / "erpbridge" / "disk-identity"),
            ERPBRIDGE_UID_FILE=str(self.base / "etc" / "erpbridge" / "container-uid"),
            ERPBRIDGE_UNIT_TARGET=str(self.base / "systemd" / "erpbridge.service"),
            ERPBRIDGE_UNIT_DIR=str(self.base / "systemd"),
            ERPBRIDGE_CADDY_TARGET=str(self.base / "caddy" / "Caddyfile"),
            CONTAINER_UID=str(os.getuid()),
            CONTAINER_GID=str(os.getgid()),
            FAKE_FS_UUID="fs-uuid-fixed",
        )

    def run_bootstrap(self, *args):
        # The storage endpoint is required by the script, so supply it centrally
        # rather than at every call site.
        if "--storage-endpoint" not in args:
            args = (*args, "--storage-endpoint", STORAGE_ENDPOINT)
        return subprocess.run(["sh", str(BOOTSTRAP), *args], env=self.env, capture_output=True, text=True)

    def commands(self):
        return [json.loads(line) for line in self.calls.read_text().splitlines()] if self.calls.exists() else []

    def test_confirmation_required(self):
        result = self.run_bootstrap("--image", "ghcr.io/nmdra/erpbridge-server@sha256:" + "b" * 64)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--confirm", result.stderr)
        self.assertFalse((self.base / "etc").exists())

    def test_unpinned_image_rejected(self):
        result = self.run_bootstrap("--confirm", "--storage-endpoint", STORAGE_ENDPOINT,
                                    "--storage-endpoint", STORAGE_ENDPOINT,
                                    "--image", "ghcr.io/nmdra/erpbridge-server:latest")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("sha256 digest", result.stderr)

    def test_first_run_prepares_everything_and_does_not_start_the_app(self):
        image = "ghcr.io/nmdra/erpbridge-server@sha256:" + "b" * 64
        result = self.run_bootstrap("--confirm", "--storage-endpoint", STORAGE_ENDPOINT,
                                    "--storage-endpoint", STORAGE_ENDPOINT,
                                    "--image", image,
                                    "--storage-endpoint", STORAGE_ENDPOINT)
        self.assertEqual(result.returncode, 0, result.stderr)
        conf = self.base / "etc" / "erpbridge"
        self.assertEqual(stat.S_IMODE(conf.stat().st_mode), 0o700)
        service_env = (conf / "service.env").read_text()
        self.assertIn(f"ERPBRIDGE_IMAGE={image}", service_env)
        self.assertNotIn("TOKEN", service_env)
        self.assertEqual(stat.S_IMODE((conf / "service.env").stat().st_mode), 0o600)
        identity = json.loads((conf / "disk-identity").read_text())
        self.assertEqual(identity["filesystem_uuid"], "fs-uuid-fixed")
        self.assertEqual((conf / "container-uid").read_text().strip(), str(os.getuid()))
        self.assertTrue((self.base / "systemd" / "erpbridge.service").is_file())
        self.assertIn("maintenance", (self.base / "caddy" / "Caddyfile").read_text())
        systemctl = [c for c in self.commands() if c[0] == "systemctl"]
        self.assertTrue(any("enable" in c and "erpbridge.service" in c for c in systemctl))
        self.assertFalse(any("start" in c and "erpbridge.service" in c for c in systemctl),
                         "bootstrap must never start the server")
        self.assertTrue(any("--now" in c and "caddy.service" in c for c in systemctl),
                        "the reverse proxy must be enabled and started in maintenance mode")

    def test_bootstrap_writes_the_backup_endpoint_for_the_timer(self):
        image = "ghcr.io/nmdra/erpbridge-server@sha256:" + "b" * 64
        result = self.run_bootstrap("--confirm", "--image", image)
        self.assertEqual(result.returncode, 0, result.stderr)
        backup_env = self.base / "etc" / "erpbridge" / "backup.env"
        self.assertTrue(backup_env.is_file(), "the timer needs this file to reach storage")
        self.assertIn(STORAGE_ENDPOINT, backup_env.read_text())
        self.assertEqual(stat.S_IMODE(backup_env.stat().st_mode), 0o600)
        unit = (self.base / "systemd" / "erpbridge-backup.service")
        self.assertTrue(unit.is_file())
        self.assertNotIn("secret", backup_env.read_text().lower())

    def test_bootstrap_rejects_a_bad_backup_endpoint(self):
        for bad in ("http://insecure.blob.core.windows.net",
                    "https://example.com",
                    "https://UPPER.blob.core.windows.net",
                    ""):
            with self.subTest(endpoint=bad):
                result = self.run_bootstrap("--confirm", "--image",
                                            "ghcr.io/nmdra/erpbridge-server@sha256:" + "b" * 64,
                                            "--storage-endpoint", bad)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.base / "etc" / "erpbridge" / "backup.env").exists())

    def test_second_run_is_repeat_safe_and_rejects_image_change(self):
        image = "ghcr.io/nmdra/erpbridge-server@sha256:" + "b" * 64
        self.assertEqual(self.run_bootstrap("--confirm", "--image", image).returncode, 0)
        self.assertEqual(self.run_bootstrap("--confirm", "--image", image).returncode, 0)
        other = "ghcr.io/nmdra/erpbridge-server@sha256:" + "c" * 64
        result = self.run_bootstrap("--confirm", "--storage-endpoint", STORAGE_ENDPOINT,
                                    "--storage-endpoint", STORAGE_ENDPOINT,
                                    "--image", other)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("different image", result.stderr)

    def test_changed_disk_identity_blocks_repeat(self):
        image = "ghcr.io/nmdra/erpbridge-server@sha256:" + "b" * 64
        self.assertEqual(self.run_bootstrap("--confirm", "--image", image).returncode, 0)
        self.env["FAKE_FS_UUID"] = "a-different-disk"
        result = self.run_bootstrap("--confirm", "--storage-endpoint", STORAGE_ENDPOINT,
                                    "--storage-endpoint", STORAGE_ENDPOINT,
                                    "--image", image,
                                    "--storage-endpoint", STORAGE_ENDPOINT)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("OS disk identity changed", result.stderr)

    def test_populated_directory_with_other_owner_blocks(self):
        data = Path(self.env["ERPBRIDGE_DATA_DIR"])
        data.mkdir(parents=True)
        (data / "erpbridge.db").write_bytes(b"SQLite format 3\x00")
        self.env["CONTAINER_UID"] = "4242"
        result = self.run_bootstrap("--confirm", "--storage-endpoint", STORAGE_ENDPOINT,
                                    "--storage-endpoint", STORAGE_ENDPOINT,
                                    "--image",
                                    "ghcr.io/nmdra/erpbridge-server@sha256:" + "b" * 64)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("populated", result.stderr)

    def test_root_container_rejected(self):
        self.env["CONTAINER_UID"] = "0"
        result = self.run_bootstrap("--confirm", "--storage-endpoint", STORAGE_ENDPOINT,
                                    "--storage-endpoint", STORAGE_ENDPOINT,
                                    "--image",
                                    "ghcr.io/nmdra/erpbridge-server@sha256:" + "b" * 64)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must not run as root", result.stderr)

    def test_missing_docker_blocks(self):
        self.env["DOCKER"] = str(self.base / "no-such-docker")
        result = self.run_bootstrap("--confirm", "--storage-endpoint", STORAGE_ENDPOINT,
                                    "--storage-endpoint", STORAGE_ENDPOINT,
                                    "--image",
                                    "ghcr.io/nmdra/erpbridge-server@sha256:" + "b" * 64)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("docker is not installed", result.stderr)
        self.assertFalse([c for c in self.commands() if c[0] == "docker"],
                         "no docker command may run after the install check fails")


VM_RESOURCE = ("/subscriptions/11111111-2222-3333-4444-555555555555/resourceGroups/erpbridge-hq"
               "/providers/Microsoft.Compute/virtualMachines/erpbridge-vm")


def vm_summary_json(**overrides):
    summary = {
        "name": "erpbridge-vm",
        "resource_id": VM_RESOURCE,
        "resource_group": "erpbridge-hq",
        "location": "centralindia",
        "url": "https://erpbridge.nimendra.online",
        "public_ip": "203.0.113.42",
        "ssh_username": "erpbridge",
        "ssh_private_key_path": "~/.ssh/erpbridge-vm",
        "vm_size": "Standard_B2ats_v2",
        "os_disk_name": "erpbridge-vm-osdisk",
        "os_disk_size_gb": 32,
        "backup_storage_account": "erpbridgebackupfixture",
    }
    summary.update(overrides)
    return summary


def capture_app():
    app = default_app()
    app["properties"]["template"]["containers"][0]["env"] = [
        {"name": "MCP_PORT", "value": "8080"},
        {"name": "MCP_TRANSPORT", "value": "http"},
        {"name": "BASE_URL", "value": "https://erpbridge.nimendra.online"},
        {"name": "MCP_ENABLE_TEST_TOOLS", "value": "false"},
        {"name": "API_AUTH_TOKEN", "secretRef": "api-auth-secret"},
        {"name": "ERP_PRIMARY_KEY", "secretRef": "erp-primary-secret"},
    ]
    return app


CAPTURE_SECRETS = [{"name": "api-auth-secret", "value": "SECRET-A"},
                   {"name": "erp-primary-secret", "value": "SECRET-B"}]


class VmCommandTests(unittest.TestCase):
    """Host-side VM operations: pinning, protected transfer, and confirmation gates."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.calls = self.base / "calls"
        self.bin = make_fake_bin(self.base, ["az", "terraform", "ssh", "ssh-keyscan", "tar", "curl",
                                             "sqlite3", "findmnt"])
        self.artifacts = self.base / "artifacts"
        self.artifacts.mkdir()
        self.known_hosts = self.artifacts / "known_hosts"
        self.staged = self.artifacts / "runtime.env.staged"
        self.env = dict(
            os.environ,
            PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
            CALLS=str(self.calls),
            SUB_ID=SUBSCRIPTION,
            APP_JSON=json.dumps(capture_app()),
            SECRETS_JSON=json.dumps(CAPTURE_SECRETS),
            VM_SUMMARY=json.dumps(vm_summary_json()),
            AZURE_SUBSCRIPTION_ID=SUBSCRIPTION,
        )

    def run_cli(self, *args):
        result = subprocess.run(["python3", str(SCRIPT), "--manifest", str(self.artifacts / "source.json"),
                                 *args], env=self.env, capture_output=True, text=True)
        self.assertNotIn("SECRET-A", result.stdout + result.stderr, "a secret value reached the terminal")
        self.assertNotIn("SECRET-B", result.stdout + result.stderr, "a secret value reached the terminal")
        self.assertNotIn("PRIVATE_REMOTE_DIAGNOSTIC", result.stdout + result.stderr)
        return result

    def commands(self):
        return [json.loads(line) for line in self.calls.read_text().splitlines()] if self.calls.exists() else []

    def ssh_calls(self):
        return [c for c in self.commands() if c[0] == "ssh"]

    def write_staged(self, text):
        self.staged.parent.mkdir(parents=True, exist_ok=True)
        self.staged.write_text(text)
        self.staged.chmod(0o600)

    def test_capture_writes_protected_file_and_rewrites_database_path(self):
        result = self.run_cli("capture-config", "--staged-env", str(self.staged))
        self.assertEqual(result.returncode, 0, result.stderr)
        text = self.staged.read_text()
        self.assertIn("DATABASE_PATH=/data/erpbridge.db", text)
        self.assertIn("API_AUTH_TOKEN=SECRET-A", text)
        self.assertIn("MCP_TRANSPORT=http", text)
        self.assertNotIn("/tmp/erpbridge.db", text)
        self.assertEqual(stat.S_IMODE(self.staged.stat().st_mode), 0o600)
        self.assertIn("Setting names: API_AUTH_TOKEN, BASE_URL", result.stdout)
        self.assertIn("No value was printed", result.stdout)

    def test_capture_rejects_key_vault_backed_secret(self):
        self.env["SECRETS_JSON"] = json.dumps([
            {"name": "api-auth-secret", "value": "SECRET-A"},
            {"name": "erp-primary-secret", "keyVaultUrl": "https://vault.example/secrets/x"},
        ])
        result = self.run_cli("capture-config", "--staged-env", str(self.staged))
        self.assertEqual(result.returncode, 1)
        self.assertIn("Key Vault", result.stderr)
        self.assertFalse(self.staged.exists())

    def test_capture_rejects_unresolvable_reference(self):
        self.env["SECRETS_JSON"] = json.dumps([{"name": "api-auth-secret", "value": "SECRET-A"}])
        result = self.run_cli("capture-config", "--staged-env", str(self.staged))
        self.assertEqual(result.returncode, 1)
        self.assertIn("refusing to substitute", result.stderr)
        self.assertFalse(self.staged.exists())

    def test_install_requires_confirmation(self):
        self.write_staged("API_AUTH_TOKEN=x\nERP_PRIMARY_KEY=y\nDATABASE_PATH=/data/erpbridge.db\n")
        result = self.run_cli("install-runtime-env", "--staged-env", str(self.staged))
        self.assertEqual(result.returncode, 1)
        self.assertIn("--confirm", result.stderr)
        self.assertEqual(self.ssh_calls(), [])

    def test_install_rejects_wrong_database_path(self):
        self.write_staged("API_AUTH_TOKEN=x\nERP_PRIMARY_KEY=y\nDATABASE_PATH=/tmp/erpbridge.db\n")
        result = self.run_cli("install-runtime-env", "--staged-env", str(self.staged),
                              "--accept-host-key", "--confirm")
        self.assertEqual(result.returncode, 1)
        self.assertIn("database at the VM data path", result.stderr)
        self.assertEqual(self.ssh_calls(), [])

    def test_install_streams_over_stdin_and_verifies_mode(self):
        self.write_staged("API_AUTH_TOKEN=x\nERP_PRIMARY_KEY=y\nDATABASE_PATH=/data/erpbridge.db\n")
        result = self.run_cli("install-runtime-env", "--staged-env", str(self.staged),
                              "--accept-host-key", "--confirm", "--known-hosts", str(self.known_hosts),
                              "--vm-tf-dir", str(self.base))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Installed 3 settings", result.stdout)
        for call in self.ssh_calls():
            self.assertNotIn("API_AUTH_TOKEN=", " ".join(call))
        self.assertEqual(stat.S_IMODE(self.known_hosts.stat().st_mode), 0o600)

    def test_install_rejects_unexpected_remote_mode(self):
        self.write_staged("API_AUTH_TOKEN=x\nERP_PRIMARY_KEY=y\nDATABASE_PATH=/data/erpbridge.db\n")
        self.env["STAT_LINE"] = "644 erpbridge"
        result = self.run_cli("install-runtime-env", "--staged-env", str(self.staged),
                              "--accept-host-key", "--confirm", "--known-hosts", str(self.known_hosts),
                              "--vm-tf-dir", str(self.base))
        self.assertEqual(result.returncode, 1)
        self.assertIn("unexpected ownership or mode", result.stderr)

    def test_host_key_must_be_pinned_deliberately(self):
        result = self.run_cli("status", "--known-hosts", str(self.known_hosts),
                              "--vm-tf-dir", str(self.base))
        self.assertEqual(result.returncode, 1)
        self.assertIn("Host key not pinned yet", result.stderr)
        self.assertIn("SHA256:", result.stderr)
        self.assertEqual(self.ssh_calls(), [])
        self.assertFalse(self.known_hosts.exists())

    def test_changed_host_key_refuses_to_connect(self):
        self.known_hosts.write_text("203.0.113.42 ssh-rsa ZGlmZmVyZW50LWtleQ==\n")
        self.known_hosts.chmod(0o600)
        result = self.run_cli("status", "--known-hosts", str(self.known_hosts),
                              "--vm-tf-dir", str(self.base))
        self.assertEqual(result.returncode, 1)
        self.assertIn("host key changed", result.stderr)
        self.assertEqual(self.ssh_calls(), [])

    def test_status_reports_state(self):
        self.env["SERVICE_STATE"] = "service=active\nproxy=active\nruntime_env=present\ndatabase=present"
        result = self.run_cli("status", "--known-hosts", str(self.known_hosts),
                              "--accept-host-key", "--vm-tf-dir", str(self.base))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("VM power: PowerState/running", result.stdout)
        self.assertIn("database=present", result.stdout)

    def test_vm_summary_must_match_resource_id(self):
        self.env["VM_SUMMARY"] = json.dumps(vm_summary_json(name="other-vm"))
        result = self.run_cli("status", "--known-hosts", str(self.known_hosts),
                              "--accept-host-key", "--vm-tf-dir", str(self.base))
        self.assertEqual(result.returncode, 1)
        self.assertIn("does not match", result.stderr)
        self.assertEqual(self.ssh_calls(), [])

    def test_start_and_stop_require_confirmation(self):
        for action in ("start", "stop"):
            with self.subTest(action=action):
                result = self.run_cli(action, "--known-hosts", str(self.known_hosts),
                                      "--accept-host-key", "--vm-tf-dir", str(self.base))
                self.assertEqual(result.returncode, 1)
                self.assertIn("--confirm", result.stderr)
        self.assertEqual(self.ssh_calls(), [])

    def test_start_promotes_proxy_only_with_serving(self):
        base_args = ("--known-hosts", str(self.known_hosts), "--accept-host-key",
                     "--vm-tf-dir", str(self.base), "--confirm")
        maintenance = self.run_cli("start", *base_args)
        self.assertEqual(maintenance.returncode, 0, maintenance.stderr)
        self.assertIn("stays in maintenance", maintenance.stdout)
        self.assertFalse(any("Caddyfile.serving" in " ".join(c) for c in self.ssh_calls()))
        self.calls.unlink(missing_ok=True)
        serving = self.run_cli("start", *base_args, "--serving")
        self.assertEqual(serving.returncode, 0, serving.stderr)
        self.assertTrue(any("Caddyfile.serving" in " ".join(c) for c in self.ssh_calls()))

    def test_stop_returns_proxy_to_maintenance(self):
        result = self.run_cli("stop", "--known-hosts", str(self.known_hosts),
                              "--accept-host-key", "--vm-tf-dir", str(self.base), "--confirm")
        self.assertEqual(result.returncode, 0, result.stderr)
        joined = " ".join(" ".join(c) for c in self.ssh_calls())
        self.assertIn("Caddyfile.maintenance", joined)
        self.assertIn("systemctl stop erpbridge.service", joined)

    def test_bootstrap_requires_confirmation_and_a_pinned_image(self):
        unpinned = self.run_cli("bootstrap", "--image", "ghcr.io/nmdra/erpbridge-server:latest",
                                "--vm-tf-dir", str(self.base), "--known-hosts", str(self.known_hosts),
                                "--asset-dir", str(ROOT / "demo" / "erpbridge"), "--confirm")
        self.assertEqual(unpinned.returncode, 1)
        self.assertIn("digest-pinned", unpinned.stderr)
        unconfirmed = self.run_cli("bootstrap", "--vm-tf-dir", str(self.base),
                                   "--known-hosts", str(self.known_hosts),
                                   "--asset-dir", str(ROOT / "demo" / "erpbridge"))
        self.assertEqual(unconfirmed.returncode, 1)
        self.assertIn("--confirm", unconfirmed.stderr)
        self.assertEqual(self.ssh_calls(), [])

    def test_bootstrap_uploads_and_runs_the_runtime_setup(self):
        result = self.run_cli("bootstrap", "--vm-tf-dir", str(self.base),
                              "--known-hosts", str(self.known_hosts),
                              "--asset-dir", str(ROOT / "demo" / "erpbridge"),
                              "--accept-host-key", "--confirm")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Host key fingerprint(s): SHA256:", result.stdout)
        joined = " ".join(" ".join(c) for c in self.ssh_calls())
        self.assertIn("tar -xzf", joined)
        self.assertIn("cloud-config-runtime.sh --confirm --image ghcr.io/nmdra/erpbridge-server@sha256:",
                      joined)
        self.assertFalse(any("--env-file" in " ".join(c) for c in self.ssh_calls()))

    def test_ready_checks_https_without_insecure_flags(self):
        result = self.run_cli("ready", "--resolve-ip", "198.51.100.7", "--timeout", "1",
                              "--poll-interval", "0.01")
        self.assertEqual(result.returncode, 0, result.stderr)
        curl = [c for c in self.commands() if c[0] == "curl"]
        self.assertTrue(curl)
        self.assertIn("--resolve", curl[0])
        self.assertIn("erpbridge.nimendra.online:443:198.51.100.7", curl[0])
        self.assertEqual(curl[0][-1], "https://erpbridge.nimendra.online/mcp/health")
        self.assertNotIn("-k", curl[0])
        self.assertNotIn("--insecure", curl[0])

    def test_ready_reports_failure(self):
        self.env["CURL_EXIT"] = "1"
        result = self.run_cli("ready", "--timeout", "1", "--poll-interval", "0.01")
        self.assertEqual(result.returncode, 1)
        self.assertIn("did not become ready", result.stderr)

    def test_restart_requires_confirmation(self):
        result = self.run_cli("restart", "--known-hosts", str(self.known_hosts),
                              "--accept-host-key", "--vm-tf-dir", str(self.base))
        self.assertEqual(result.returncode, 1)
        self.assertIn("--confirm", result.stderr)
        self.assertEqual(self.ssh_calls(), [])

    def test_restart_validates_before_touching_a_running_service(self):
        result = self.run_cli("restart", "--confirm", "--known-hosts", str(self.known_hosts),
                              "--accept-host-key", "--vm-tf-dir", str(self.base))
        self.assertEqual(result.returncode, 0, result.stderr)
        joined = " ".join(" ".join(c) for c in self.ssh_calls())
        self.assertIn("/opt/erpbridge/require-database.sh", joined)
        self.assertIn("systemctl restart erpbridge.service", joined)
        self.assertLess(joined.index("require-database.sh"), joined.index("systemctl restart"))

    def test_restart_refuses_when_the_guard_rejects_the_configuration(self):
        self.env["FAIL"] = "ssh"
        result = self.run_cli("restart", "--confirm", "--known-hosts", str(self.known_hosts),
                              "--accept-host-key", "--vm-tf-dir", str(self.base))
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("systemctl restart", " ".join(" ".join(c) for c in self.ssh_calls()))

    def test_backup_runs_on_the_vm_without_confirmation(self):
        result = self.run_cli("backup", "--known-hosts", str(self.known_hosts),
                              "--accept-host-key", "--vm-tf-dir", str(self.base))
        self.assertEqual(result.returncode, 0, result.stderr)
        joined = " ".join(" ".join(c) for c in self.ssh_calls())
        self.assertIn("/opt/erpbridge/backup.py", joined)
        self.assertNotIn("--dry-run", joined)

    def test_backup_dry_run_passes_the_flag(self):
        result = self.run_cli("backup", "--dry-run", "--known-hosts", str(self.known_hosts),
                              "--accept-host-key", "--vm-tf-dir", str(self.base))
        self.assertEqual(result.returncode, 0, result.stderr)
        joined = " ".join(" ".join(c) for c in self.ssh_calls())
        self.assertIn("--dry-run", joined)

    def test_restore_requires_confirmation(self):
        result = self.run_cli("restore", "--backup-id", "20260101T000000Z-abc.db",
                              "--known-hosts", str(self.known_hosts),
                              "--accept-host-key", "--vm-tf-dir", str(self.base))
        self.assertEqual(result.returncode, 1)
        self.assertIn("--confirm", result.stderr)
        self.assertEqual(self.ssh_calls(), [])

    def test_restore_rejects_unsafe_arguments(self):
        cases = (
            ("--backup-id", "x; rm -rf /"),
            ("--backup-id", "../escape"),
            ("--source-file", "/tmp/x; reboot"),
            ("--source-file", "/tmp/with space"),
            ("--expect-sha256", "nothex"),
        )
        for flag, value in cases:
            with self.subTest(flag=flag):
                args = ["restore", "--backup-id", "20260101T000000Z-abc.db", "--confirm",
                        "--known-hosts", str(self.known_hosts), "--accept-host-key",
                        "--vm-tf-dir", str(self.base), flag, value]
                result = self.run_cli(*args)
                self.assertEqual(result.returncode, 1, f"{flag}={value} must be refused")
                self.assertIn("not allowed here", result.stderr)
        self.assertEqual(self.ssh_calls(), [])

    def test_restore_passes_confirmation_and_expectations(self):
        result = self.run_cli("restore", "--backup-id", "20260101T000000Z-abc.db", "--confirm",
                              "--expect-sha256", "A" * 64,
                              "--known-hosts", str(self.known_hosts),
                              "--accept-host-key", "--vm-tf-dir", str(self.base))
        self.assertEqual(result.returncode, 0, result.stderr)
        joined = " ".join(" ".join(c) for c in self.ssh_calls())
        self.assertIn("/opt/erpbridge/restore.py", joined)
        self.assertIn("--backup-id 20260101T000000Z-abc.db", joined)
        self.assertIn("--expect-sha256 " + "a" * 64, joined)
        self.assertIn("--confirm", joined)

    def test_backup_bundle_covers_every_bootstrap_asset(self):
        assets = ROOT / "demo" / "erpbridge"
        result = self.run_cli("bootstrap", "--vm-tf-dir", str(self.base),
                              "--known-hosts", str(self.known_hosts),
                              "--asset-dir", str(assets), "--accept-host-key", "--confirm")
        self.assertEqual(result.returncode, 0, result.stderr)
        packed = [c for c in self.commands() if c[0] == "tar"]
        self.assertEqual(len(packed), 1)
        script = (assets / "cloud-config-runtime.sh").read_text()
        for name in ("backup.py", "restore.py", "erpbridge-backup.service",
                     "erpbridge-backup.timer"):
            self.assertIn(name, script, "the on-VM bootstrap must install this asset")
        bootstrap_args = " ".join(packed[0])
        self.assertIn("backup.py", bootstrap_args)
        self.assertIn("erpbridge-backup.timer", bootstrap_args)

    def test_remote_failure_is_safe(self):
        self.env["FAIL"] = "ssh"
        result = self.run_cli("status", "--known-hosts", str(self.known_hosts),
                              "--accept-host-key", "--vm-tf-dir", str(self.base))
        self.assertEqual(result.returncode, 1)
        self.assertIn("diagnostics suppressed", result.stderr)


class TlsStageTests(unittest.TestCase):
    """The pre-cutover certificate step must prove the certificate being served."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.calls = self.base / "calls"
        self.bin = make_fake_bin(self.base, ["az", "terraform", "ssh", "ssh-keyscan", "tar",
                                             "curl", "openssl"])
        self.artifacts = self.base / "artifacts"
        self.artifacts.mkdir()
        self.known_hosts = self.artifacts / "known_hosts"
        self.cert = self.base / "fullchain.pem"
        self.cert.write_text("-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n")
        self.key = self.base / "privkey.pem"
        self.key.write_text("-----BEGIN PRIVATE KEY-----\nSECRETKEYMATERIAL\n-----END PRIVATE KEY-----\n")
        self.key.chmod(0o600)
        self.env = dict(
            os.environ,
            PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
            CALLS=str(self.calls),
            VM_SUMMARY=json.dumps(vm_summary_json()),
            AZURE_SUBSCRIPTION_ID=SUBSCRIPTION,
        )

    def run_cli(self, *args):
        result = subprocess.run(["python3", str(SCRIPT), "--manifest", str(self.artifacts / "m.json"),
                                 *args], env=self.env, capture_output=True, text=True)
        self.assertNotIn("SECRETKEYMATERIAL", result.stdout + result.stderr,
                         "private key material reached the terminal")
        self.assertNotIn("PRIVATE_KEY_MATERIAL", result.stdout + result.stderr)
        return result

    def stage(self, *extra):
        return self.run_cli("tls-stage", "--cert", str(self.cert), "--key", str(self.key),
                            "--known-hosts", str(self.known_hosts), "--accept-host-key",
                            "--vm-tf-dir", str(self.base), *extra)

    def commands(self):
        return [json.loads(line) for line in self.calls.read_text().splitlines()] if self.calls.exists() else []

    def ssh_calls(self):
        return [c for c in self.commands() if c[0] == "ssh"]

    def test_confirmation_is_required(self):
        result = self.stage()
        self.assertEqual(result.returncode, 1)
        self.assertIn("--confirm", result.stderr)
        self.assertEqual(self.ssh_calls(), [])

    def test_world_readable_key_is_refused(self):
        self.key.chmod(0o644)
        result = self.stage("--confirm")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Restrict it to 0600", result.stderr)
        self.assertEqual(self.ssh_calls(), [])

    def test_expiring_certificate_is_refused(self):
        self.env["FAIL"] = "openssl"
        result = self.stage("--confirm")
        self.assertEqual(result.returncode, 1)
        self.assertIn("expired or valid for fewer than", result.stderr)
        self.assertEqual(self.ssh_calls(), [])

    def test_certificate_must_cover_the_canonical_hostname(self):
        self.env["SAN"] = "DNS:example.invalid, DNS:other.invalid"
        result = self.stage("--confirm")
        self.assertEqual(result.returncode, 1)
        self.assertIn("does not cover", result.stderr)
        self.assertEqual(self.ssh_calls(), [])

    def test_wildcard_covering_the_host_is_accepted(self):
        self.env["SAN"] = "DNS:*.nimendra.online"
        result = self.stage("--confirm")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_mismatched_key_is_refused(self):
        self.env["KEY_PUB"] = "-----BEGIN PUBLIC KEY-----\nDIFFERENT\n-----END PUBLIC KEY-----"
        result = self.stage("--confirm")
        self.assertEqual(result.returncode, 1)
        self.assertIn("does not belong to this certificate", result.stderr)
        self.assertEqual(self.ssh_calls(), [])

    def test_happy_path_stages_and_proves_the_served_certificate(self):
        result = self.stage("--confirm")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Fingerprint match", result.stdout)
        self.assertIn("maintenance response", result.stdout)
        self.assertIn("No private key value was printed", result.stdout)
        for call in self.ssh_calls():
            self.assertNotIn("SECRETKEYMATERIAL", " ".join(call))
        joined = " ".join(" ".join(c) for c in self.ssh_calls())
        self.assertIn("/opt/erpbridge/Caddyfile.tls-check", joined)
        self.assertIn("chmod 0600 /etc/caddy/certs/erpbridge.key", joined)
        curl = [c for c in self.commands() if c[0] == "curl"]
        self.assertTrue(curl)
        self.assertIn("--resolve", curl[0])
        self.assertIn("erpbridge.nimendra.online:443:203.0.113.42", curl[0])
        self.assertNotIn("-k", curl[0])
        self.assertNotIn("--insecure", curl[0])
        self.assertEqual(curl[0][-1], "https://erpbridge.nimendra.online/mcp/health")

    def test_a_different_served_certificate_is_refused(self):
        self.env["SERVED_FINGERPRINT"] = "sha256 Fingerprint=" + ":".join(["CD"] * 32)
        result = self.stage("--confirm")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not the certificate that was staged", result.stderr)

    def test_unexpected_probe_status_is_refused(self):
        self.env["CURL_EXIT"] = "0"
        self.env["HTTP_STATUS"] = "200"
        result = self.stage("--confirm")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not 503", result.stderr)



class MonitoringInstallerTests(unittest.TestCase):
    from typing import Any
    installer: Any
    helper: Any
    def setUp(self):
        import importlib.util
        from unittest.mock import patch
        self.patch = patch
        for name, path in [('installer', ROOT / 'demo/erpbridge/install-monitoring.py'),
                           ('helper', SCRIPT)]:
            spec = importlib.util.spec_from_file_location(name, path)
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            setattr(self, name, module)

    def payload(self):
        import base64
        return {name: base64.b64encode((b'print("ok")\n' if name == 'monitor.py' else
                 (ROOT / 'demo/erpbridge' / name).read_bytes())).decode()
                for name in self.installer.ASSETS}

    def test_confirmation_before_any_read_or_transport(self):
        args = self.helper.build_parser().parse_args(['install-monitoring'])
        with self.patch.object(self.helper, 'vm_summary') as summary:
            with self.assertRaises(self.helper.SafeError):
                args.handler(args)
            summary.assert_not_called()

    def test_allowlist_rejects_extra_assets(self):
        payload = self.payload()
        payload['cloud-config-runtime.sh'] = 'eA=='
        with self.assertRaises(ValueError):
            self.installer.validate(payload)

    def test_check_has_no_writes(self):
        result = subprocess.run(['python3', str(ROOT / 'demo/erpbridge/install-monitoring.py'),
                                 '--check', '--vm-resource-id', '/subscriptions/' + SUBSCRIPTION +
                                 '/resourceGroups/test/providers/Microsoft.Compute/virtualMachines/test'],
                                input=json.dumps(self.payload()), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {'valid': True})

    def test_repeat_install_preserves_workload_and_syslog_permissions(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in self.installer.HASH_PATHS + self.installer.META_PATHS:
                path = root / name.lstrip('/')
                path.parent.mkdir(parents=True, exist_ok=True)
                if not path.exists():
                    path.write_text(SECRET_SENTINEL)
            log = root / 'var/log/erpbridge-monitor/health-2026-10-18.jsonl'
            log.parent.mkdir(parents=True)
            log.write_text('{}\n')
            before = self.installer.snapshot(root)
            calls, owners = [], []
            vm_id = '/subscriptions/' + SUBSCRIPTION + '/resourceGroups/test/providers/Microsoft.Compute/virtualMachines/test'
            with self.patch.object(self.installer.os, 'chown', side_effect=lambda *args: owners.append(args)), \
                 self.patch.object(self.installer.grp, 'getgrnam', return_value=SimpleNamespace(gr_gid=123)):
                for _ in range(2):
                    self.assertEqual(self.installer.install(self.payload(), vm_id, root,
                                                           lambda *args: calls.append(args)),
                                     {'installed': True, 'preserved': True})
                    self.assertEqual(before, self.installer.snapshot(root))
            self.assertEqual(calls.count(('restart', 'systemd-journald.service')), 1)
            self.assertEqual(calls.count(('enable', '--now', 'erpbridge-monitor.timer')), 2)
            self.assertIn((log.parent, 0, 123), owners)
            self.assertIn((log, 0, 123), owners)
            self.assertEqual(stat.S_IMODE(log.parent.stat().st_mode), 0o2750)
            self.assertEqual(stat.S_IMODE(log.stat().st_mode), 0o640)
            self.assertEqual(stat.S_IMODE((root / 'var/lib/erpbridge-monitor').stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((root / 'etc/erpbridge/monitor.env').stat().st_mode), 0o600)
            self.assertNotIn(SECRET_SENTINEL, (root / 'var/lib/erpbridge-monitor/install-evidence.json').read_text())
            self.assertTrue(all('erpbridge.service' not in call and 'caddy.service' not in call for call in calls))

    def test_live_database_writes_do_not_break_preservation_check(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / 'var/lib/erpbridge/data/erpbridge.db'
            database.parent.mkdir(parents=True)
            database.write_bytes(b'initial fixture')
            database.chmod(0o640)
            before = self.installer.snapshot(root)
            database.write_bytes(b'normal concurrent SQLite write or checkpoint')
            self.assertEqual(before, self.installer.snapshot(root))
            database.chmod(0o600)
            self.assertNotEqual(before, self.installer.snapshot(root))

    def test_azure_response_and_protected_script(self):
        args = self.helper.build_parser().parse_args(['install-monitoring', '--confirm',
                                                     '--transport', 'azure-run-command'])
        target = {'resource_id': '/subscriptions/' + SUBSCRIPTION + '/resourceGroups/test/providers/Microsoft.Compute/virtualMachines/test'}
        def fake(arguments, timeout):
            self.assertEqual(arguments[:3], ['vm', 'run-command', 'invoke'])
            path = Path(arguments[-1][1:])
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(path.read_text(), 'safe script\n')
            return {'value': [{'code': 'ComponentStatus/StdOut/succeeded',
                               'message': 'Enable succeeded:\n[stdout]\n{"installed":true,"preserved":true}\n[stderr]\n'}]}
        with self.patch.object(self.helper, 'az_json', side_effect=fake), self.patch.object(self.helper, 'ssh_command') as ssh:
            self.assertEqual(self.helper.monitoring_remote(args, target, 'safe script'),
                             {'installed': True, 'preserved': True})
            ssh.assert_not_called()

    def test_azure_provisioning_state_wrapper_contains_stdout(self):
        args = self.helper.build_parser().parse_args(['status', '--transport', 'azure-run-command'])
        response = {'value': [{'code': 'ProvisioningState/succeeded',
                              'message': 'Enable succeeded:\n[stdout]\n{"installed":true,"preserved":true}\n[stderr]\n'}]}
        with self.patch.object(self.helper, 'az_json', return_value=response):
            self.assertEqual(self.helper.monitoring_remote(args, {'resource_id': 'fixture'}, 'safe'),
                             {'installed': True, 'preserved': True})

    def test_azure_failed_response_is_suppressed(self):
        args = self.helper.build_parser().parse_args(['status', '--transport', 'azure-run-command'])
        with self.patch.object(self.helper, 'az_json', return_value={'value': [
                {'code': 'ComponentStatus/StdErr/failed', 'message': SECRET_SENTINEL}]}):
            with self.assertRaises(self.helper.SafeError) as raised:
                self.helper.monitoring_remote(args, {'resource_id': 'fixture'}, 'safe')
            self.assertNotIn(SECRET_SENTINEL, str(raised.exception))


if __name__ == "__main__":
    unittest.main()
