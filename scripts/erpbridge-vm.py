#!/usr/bin/env python3
"""ERPBridge VM migration helper.

Read-only by default. `preflight` records the source Container App's non-secret
identity into a protected manifest so a later cutover can refuse drift.
`cost` renders an itemized estimate from an explicit, operator-supplied price
sheet. This helper never reads or prints ACA secret values.
"""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import base64
import datetime
import hashlib
import json
import os
import re
import stat
import shlex
import tempfile
import subprocess
import sys
import time
from pathlib import Path
from typing import NoReturn
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARTIFACT_DIR = ROOT / "artifacts" / "erpbridge-vm-migration"
DEFAULT_MANIFEST = DEFAULT_ARTIFACT_DIR / "source-manifest.json"

CANONICAL_URL = "https://erpbridge.nimendra.online"
DIGEST_REFERENCE = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")
REVISION_ALIAS = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")
SUBSCRIPTION_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

# Required by the runtime image and the cutover design.
REQUIRED_PLAIN_ENV = ("DATABASE_PATH", "MCP_PORT", "MCP_TRANSPORT")
REQUIRED_SECRET_ENV = ("API_AUTH_TOKEN", "ERP_PRIMARY_KEY")
# Only these keys are known to carry non-secret settings, so only these values
# are copied into the manifest. Every other key is recorded by name alone.
PLAIN_ENV_ALLOWLIST = frozenset({
    "DATABASE_PATH", "MCP_PORT", "MCP_TRANSPORT", "BASE_URL", "APP_ENV", "LOG_LEVEL",
    "LOG_TO_STDERR", "ERP_BASE_URL", "MCP_ENABLE_TEST_TOOLS", "CACHE_MEMORY_MAX_ENTRIES",
    "RATE_LIMIT_RPS", "RATE_LIMIT_BURST", "CORS_ALLOWED_ORIGINS", "API_AUTH_ADMIN_ROLES",
})
COST_METERS = (
    "vm_hourly", "os_disk_monthly", "static_ip_monthly", "backup_capacity_gb_monthly",
    "backup_operations_per_10k", "outbound_gb", "aca_retained_monthly",
)


class SafeError(Exception):
    """Failure that is safe to print: no provider text, no secret values."""


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        # Never echo arbitrary CLI parameters, which can carry secret values.
        self.print_usage(sys.stderr)
        self.exit(2, "Invalid arguments; use --help.\n")


def run(executable: str, arguments: list[str], timeout: float = 60) -> str:
    try:
        result = subprocess.run([os.environ.get(executable.upper(), executable)] + arguments,
                                capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise SafeError("Local command failed or timed out.") from None
    if result.returncode:
        raise SafeError("Local command failed; provider diagnostics suppressed.")
    return result.stdout.strip()


def read_json(text: str, what: str):
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        raise SafeError(f"{what} was not valid JSON.") from None


def az_json(arguments: list[str], timeout: float = 60):
    return read_json(run("az", arguments + ["--only-show-errors", "--output", "json"], timeout),
                     "Azure response")


def tf_output(directory: str, name: str):
    return read_json(run("terraform", ["-chdir=" + directory, "output", "-json", name]), "Terraform output")


def require(value, what: str):
    if value is None or (isinstance(value, str) and not value.strip()):
        raise SafeError(f"Required value missing: {what}.")
    return value


def classify_environment(container: dict) -> tuple[dict, dict]:
    """Split container environment into allowlisted values and secret references.

    Returns the allowlisted plain values and a mapping of environment variable
    name to referenced secret-store entry name. Those are different namespaces:
    the environment variable name is not the secret-store name.
    """
    plain: dict[str, str] = {}
    secret_env: dict[str, str] = {}
    for entry in container.get("env") or []:
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise SafeError("Container environment entry has no name.")
        reference = entry.get("secretRef")
        if reference is not None:
            if not isinstance(reference, str) or not reference.strip():
                raise SafeError("Container environment entry has an empty secret reference.")
            secret_env[name] = reference.strip()
            continue
        if entry.get("value") is not None and name in PLAIN_ENV_ALLOWLIST:
            plain[name] = str(entry["value"])
    return plain, secret_env


def collect_source(resource_group: str, app_name: str, timeout: float) -> dict:
    app = az_json(["containerapp", "show", "--resource-group", resource_group, "--name", app_name],
                  timeout)
    if not isinstance(app, dict):
        raise SafeError("Container App response was not an object.")
    properties = app.get("properties") or {}
    configuration = properties.get("configuration") or {}
    # The live API reports configuration.activeRevisionsMode ("Single"). Accept
    # the older top-level spelling as well so the check is not provider-version
    # dependent.
    revision_mode = configuration.get("activeRevisionsMode") or properties.get("revisionMode")
    if str(revision_mode or "").lower() != "single":
        raise SafeError("The source app must stay in single revision mode.")
    template = properties.get("template") or {}
    containers = template.get("containers") or []
    if len(containers) != 1:
        raise SafeError("The source app must run exactly one container.")
    image = require(containers[0].get("image"), "container image")
    if not DIGEST_REFERENCE.fullmatch(image):
        raise SafeError("The source image must be pinned by sha256 digest.")
    plain, secret_env = classify_environment(containers[0])
    ingress = (properties.get("configuration") or {}).get("ingress") or {}
    scale = template.get("scale") or {}
    revisions = properties.get("latestReadyRevisionName"), properties.get("latestRevisionName")

    secret_names: list[str] = []
    listed = az_json(["containerapp", "secret", "list", "--resource-group", resource_group,
                      "--name", app_name], timeout)
    for item in listed or []:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            secret_names.append(item["name"])
    secret_names.sort()

    replicas: list[dict] = []
    revision_name = revisions[0] or revisions[1]
    if revision_name:
        try:
            found = az_json(["containerapp", "replica", "list", "--resource-group", resource_group,
                             "--name", app_name, "--revision", revision_name], timeout)
        except SafeError:
            found = []
        for item in found or []:
            if isinstance(item, dict):
                replicas.append({"name": item.get("name"), "runningState": item.get("runningState")})

    return {
        "resource_group": resource_group,
        "app": app_name,
        "revision_mode": revision_mode,
        "latest_ready_revision": revisions[0],
        "latest_revision": revisions[1],
        "image": image,
        "replicas": replicas,
        "replica_count": len(replicas),
        "plain_env": plain,
        "secret_env_names": sorted(secret_env),
        "secret_refs": sorted(set(secret_env.values())),
        "secret_names": secret_names,
        "ingress": {
            "external": ingress.get("external"),
            "target_port": ingress.get("targetPort"),
            "transport": ingress.get("transport"),
            "allow_insecure": ingress.get("allowInsecure"),
            "fqdn": ingress.get("fqdn"),
            "traffic": [
                {"latest_revision": entry.get("latestRevision"), "weight": entry.get("weight")}
                for entry in (ingress.get("traffic") or []) if isinstance(entry, dict)
            ],
        },
        "scale": {"min_replicas": scale.get("minReplicas"),
                  "max_replicas": scale.get("maxReplicas")},
    }


def evaluate(source: dict, summary: dict, domains: dict, expected_port: int) -> tuple[list[dict], list[str]]:
    checks: list[dict] = []
    blockers: list[str] = []

    def check(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})
        if not passed:
            blockers.append(f"{name}: {detail}")

    plain = source["plain_env"]
    missing_plain = [key for key in REQUIRED_PLAIN_ENV if key not in plain]
    check("required_plain_environment", not missing_plain,
          "all required non-secret settings present" if not missing_plain
          else f"missing {', '.join(missing_plain)}")
    missing_secret = [key for key in REQUIRED_SECRET_ENV if key not in source["secret_env_names"]]
    check("required_secret_references", not missing_secret,
          "all required secret references present" if not missing_secret
          else f"missing {', '.join(missing_secret)}")
    unreferenced = [ref for ref in source["secret_refs"] if ref not in source["secret_names"]]
    check("secret_references_resolvable", not unreferenced,
          "every referenced secret entry exists in the app secret store" if not unreferenced
          else "a referenced secret entry is absent from the app secret store")

    port = plain.get("MCP_PORT") or ""
    try:
        port_matches = int(source["ingress"]["target_port"]) == int(port) == expected_port
    except (TypeError, ValueError):
        port_matches = False
    check("target_port_matches_setting", port_matches,
          f"ingress target port {source['ingress']['target_port']} and MCP_PORT {port or 'unset'} "
          f"must match {expected_port}")
    check("ingress_external_https", bool(source["ingress"]["external"])
          and source["ingress"]["allow_insecure"] is False,
          "ingress must be external with insecure connections disallowed")
    weights = source["ingress"]["traffic"]
    check("single_revision_traffic",
          len(weights) == 1 and weights[0]["latest_revision"] is True and weights[0]["weight"] == 100,
          "traffic must send 100 percent to the latest revision only")
    check("one_replica", source["replica_count"] == 1,
          f"exactly one replica is expected, saw {source['replica_count']}")
    check("database_path_recorded", plain.get("DATABASE_PATH") == "/tmp/erpbridge.db",
          f"source database path is {plain.get('DATABASE_PATH')}")

    domains = domains if isinstance(domains, dict) else {}
    check("canonical_custom_domain",
          bool(domains.get("enabled")) and typeof_host(domains.get("erpbridge")) == CANONICAL_URL,
          f"canonical custom domain must be configured as {CANONICAL_URL}")
    base = plain.get("BASE_URL")
    check("base_url_matches_canonical", base == CANONICAL_URL,
          f"BASE_URL must equal {CANONICAL_URL} so clients keep the same URL")

    summary_url = ((summary or {}).get("erpbridge") or {}).get("url")
    check("summary_url_matches_canonical", summary_url == CANONICAL_URL,
          "Terraform summary URL must equal the canonical URL")
    check("azure_fallback_documented",
          bool(((summary or {}).get("erpbridge") or {}).get("azure_url")),
          "Azure fallback URL must stay recorded for rollback and client-impact review")
    return checks, blockers


def typeof_host(value) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    host = value.strip().lower()
    return host if host.startswith("https://") else "https://" + host


def identity(manifest: dict) -> dict:
    """The subset that must not change between preflight and cutover."""
    source = manifest["source"]
    return {
        "subscription_id": manifest["subscription_id"],
        "resource_group": source["resource_group"],
        "app": source["app"],
        "image": source["image"],
        "latest_ready_revision": source["latest_ready_revision"],
        "revision_mode": source["revision_mode"],
        "plain_env": source["plain_env"],
        "secret_env_names": source["secret_env_names"],
        "secret_refs": source["secret_refs"],
        "secret_names": source["secret_names"],
        "ingress": source["ingress"],
        "scale": source["scale"],
        "canonical_url": manifest["canonical_url"],
    }


def diff_identity(previous: dict, current: dict) -> list[str]:
    changes: list[str] = []
    old, new = identity(previous), identity(current)
    for key in sorted(set(old) | set(new)):
        if old.get(key) != new.get(key):
            changes.append(key)
    return changes


def write_protected(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + ".tmp")
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def render_summary(manifest: dict) -> str:
    source, checks = manifest["source"], manifest["checks"]
    lines = [
        f"Source app: {source['resource_group']}/{source['app']}",
        f"Serving revision: {source['latest_ready_revision']} ({source['revision_mode']})",
        f"Image: {source['image']}",
        f"Replicas: {source['replica_count']}; ingress target port: {source['ingress']['target_port']}",
        f"Database path: {manifest['source']['plain_env'].get('DATABASE_PATH')}",
        f"Canonical URL: {manifest['canonical_url']}",
        f"Secret references present: {len(source['secret_env_names'])} (values never read)",
        f"Secret store entries: {len(source['secret_names'])} (names only)",
        f"Checks passed: {sum(1 for item in checks if item['passed'])}/{len(checks)}",
    ]
    for item in checks:
        if not item["passed"]:
            lines.append(f"BLOCKER {item['name']}: {item['detail']}")
    lines.append("No secret value was read, printed, or stored by this command.")
    return "\n".join(lines)


def command_preflight(args) -> int:
    subscription = require(run("az", ["account", "show", "--query", "id", "-o", "tsv",
                                      "--only-show-errors"]), "subscription id")
    if not SUBSCRIPTION_ID.fullmatch(subscription.strip()):
        raise SafeError("Azure returned no usable subscription id.")
    if args.subscription_id and args.subscription_id.strip().lower() != subscription.strip().lower():
        raise SafeError("The active subscription does not match the expected subscription.")
    if not REVISION_ALIAS.fullmatch(args.app_name):
        raise SafeError("The app name is not a usable Container App name.")
    summary = tf_output(args.tf_dir, "deployment_summary")
    domains = tf_output(args.tf_dir, "cloudflare_domains")
    source = collect_source(args.resource_group, args.app_name, args.timeout)
    checks, blockers = evaluate(source, summary, domains, args.expected_port)
    manifest = {
        "schema_version": 1,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "subscription_id": subscription.strip().lower(),
        "canonical_url": CANONICAL_URL,
        "source": source,
        "checks": checks,
        "blockers": blockers,
    }
    if args.check:
        previous_path = Path(args.check)
        try:
            previous = json.loads(previous_path.read_text())
        except (OSError, ValueError):
            raise SafeError("The comparison manifest could not be read.") from None
        changes = diff_identity(previous, manifest)
        if changes:
            print("Source identity changed since the recorded preflight: " + ", ".join(changes),
                  file=sys.stderr)
            return 1
        print("Source identity matches the recorded preflight manifest.")
        return 0
    destination = Path(args.manifest)
    write_protected(destination, manifest)
    print(render_summary(manifest))
    print(f"Manifest written: {destination} (mode 0600)")
    if blockers:
        print("Blockers: " + "; ".join(item["name"] for item in checks if not item["passed"]),
              file=sys.stderr)
        print("Preflight found blockers; resolve them before cutover.", file=sys.stderr)
        return 1
    return 0


def decimal_input(value: str, what: str) -> Decimal:
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError):
        raise SafeError(f"{what} must be a decimal number.") from None
    if not number.is_finite() or number < 0:
        raise SafeError(f"{what} must be a finite non-negative number.")
    return number


def load_price_sheet(path: str) -> tuple[dict, str]:
    try:
        sheet = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        raise SafeError("The price sheet could not be read as JSON.") from None
    meters = sheet.get("meters") if isinstance(sheet, dict) else None
    if not isinstance(meters, dict):
        raise SafeError("The price sheet must contain a meters object.")
    missing = [key for key in COST_METERS if key not in meters]
    if missing:
        raise SafeError("The price sheet is missing meters: " + ", ".join(missing) + ".")
    currency = sheet.get("currency") or "USD"
    as_of = sheet.get("as_of") or "unspecified date"
    weights = {key: decimal_input(str(meters[key]), f"meter {key}") for key in COST_METERS}
    return weights, f"{currency} (price sheet {as_of}, region {sheet.get('region') or 'unspecified'})"


def command_cost(args) -> int:
    meters, provenance = load_price_sheet(args.price_sheet)
    hours = decimal_input(args.running_hours, "running hours")
    backup_gb = decimal_input(args.backup_gb, "backup capacity")
    backup_ops = decimal_input(args.backup_operations, "backup operations")
    outbound_gb = decimal_input(args.outbound_gb, "outbound traffic")
    aca_months = decimal_input(args.aca_retained_months, "retained ACA months")

    lines = [
        ("VM compute", meters["vm_hourly"] * hours),
        ("OS disk", meters["os_disk_monthly"]),
        ("Static public IP", meters["static_ip_monthly"]),
        ("Backup capacity", meters["backup_capacity_gb_monthly"] * backup_gb),
        ("Backup operations", meters["backup_operations_per_10k"] * backup_ops / Decimal(10000)),
        ("Outbound traffic", meters["outbound_gb"] * outbound_gb),
        ("Retained Container Apps overlap", meters["aca_retained_monthly"] * aca_months),
    ]
    total = sum((amount for _, amount in lines), Decimal(0))
    print(f"Itemized estimate in {provenance}:")
    for label, amount in lines:
        print(f"  {label:<34} {amount:.4f}")
    print(f"  {'Estimated subtotal':<34} {total:.4f}")
    print("This is an operator-supplied estimate, not a bill and not current billing data.")
    print("Excludes taxes, discounts, credit expiry, and any meter absent from the price sheet.")
    return 0


# --- VM operations ---------------------------------------------------------

# The pinned target release from the plan. Overridable, but always by digest.
DEFAULT_TARGET_IMAGE = (
    "ghcr.io/nmdra/erpbridge-server@sha256:"
    "274940d90c368eec23b38529ac19b6b67e1a59b88399e236e6a984857d1ba7b6"
)
ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Only this shape may be interpolated into a remote shell command.
SAFE_IMAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]*@sha256:[0-9a-f]{64}$")
VM_RESOURCE_ID = re.compile(
    r"^/subscriptions/(?P<sub>[0-9a-f-]{36})/resourceGroups/(?P<rg>[^/\s?#]+)"
    r"/providers/Microsoft\.Compute/virtualMachines/(?P<name>[^/\s?#]+)$", re.I)
REMOTE_ASSET_DIR = "/opt/erpbridge"
REMOTE_ENV_FILE = "/etc/erpbridge/runtime.env"
CONTAINER_DATABASE_PATH = "/data/erpbridge.db"
# Every asset the on-VM bootstrap expects. The upload and the local precheck use
# the same list so they cannot drift apart.
BUNDLED_ASSETS = ("cloud-config-runtime.sh", "require-database.sh", "erpbridge.service",
                  "Caddyfile", "Caddyfile.maintenance", "Caddyfile.tls-check", "backup.py",
                  "restore.py", "erpbridge-backup.service", "erpbridge-backup.timer")
# Fixed names on the VM so the temporary TLS configuration stays host-independent.
REMOTE_TLS_DIR = "/etc/caddy/certs"
REMOTE_TLS_CERT = "/etc/caddy/certs/erpbridge.crt"
REMOTE_TLS_KEY = "/etc/caddy/certs/erpbridge.key"
MINIMUM_CERTIFICATE_DAYS = 7


def run_command(executable: str, arguments: list[str], timeout: float,
                stdin_data: str | None = None, stdin_path: Path | None = None):
    """Run a local executable with optional standard input.

    Standard input is used for every secret-bearing transfer so values never
    reach a command line, a shell history, or a process list.
    """
    command = [os.environ.get(executable.upper(), executable)] + arguments
    handle = None
    try:
        if stdin_path is not None:
            handle = open(stdin_path, "rb")
        return subprocess.run(command, capture_output=True, text=stdin_path is None,
                              input=stdin_data, stdin=handle, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise SafeError("Local command failed or timed out.") from None
    finally:
        if handle is not None:
            handle.close()


def vm_summary(tf_dir: str, expected_group: str | None) -> dict:
    """Read and validate the dedicated VM identity from the new Terraform root."""
    summary = tf_output(tf_dir, "deployment_summary")
    if not isinstance(summary, dict):
        raise SafeError("The VM deployment summary was not an object.")
    match = VM_RESOURCE_ID.fullmatch(str(summary.get("resource_id", "")))
    if not match:
        raise SafeError("The VM resource id is missing or malformed.")
    if match.group("name").casefold() != str(summary.get("name", "")).casefold():
        raise SafeError("The VM resource id does not match the summary name.")
    if match.group("rg").casefold() != str(summary.get("resource_group", "")).casefold():
        raise SafeError("The VM resource id does not match the summary resource group.")
    if expected_group and match.group("rg").casefold() != expected_group.casefold():
        raise SafeError("The VM lives outside the expected resource group.")
    for key in ("public_ip", "ssh_username", "ssh_private_key_path", "url"):
        require(summary.get(key), f"VM summary {key}")
    if summary["url"] != CANONICAL_URL:
        raise SafeError(f"The VM URL must be {CANONICAL_URL}.")
    return summary


def key_line_fingerprint(line: str) -> str:
    """Compute an OpenSSH SHA256 fingerprint from a `host type base64key` line."""
    parts = line.split()
    if len(parts) < 3:
        raise SafeError("A scanned host key line is malformed.")
    try:
        blob = base64.b64decode(parts[-1], validate=True)
    except (ValueError, TypeError):
        raise SafeError("A scanned host key could not be decoded.") from None
    digest = base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
    return "SHA256:" + digest


def scan_host_keys(host: str, port: int, timeout: float) -> list[str]:
    result = run_command("ssh-keyscan", ["-p", str(port), "-T", str(max(1, int(timeout))), host],
                         timeout + 5)
    if result.returncode:
        raise SafeError("Could not read the VM host key; provider diagnostics suppressed.")
    lines = [line.strip() for line in result.stdout.splitlines()
             if line.strip() and not line.startswith("#")]
    if not lines:
        raise SafeError("The VM returned no host key.")
    return sorted(lines)


def verify_host_key(host: str, port: int, known_hosts: Path, accept: bool,
                    timeout: float) -> list[str]:
    """Pin the VM host key on first use and refuse any later change."""
    scanned = scan_host_keys(host, port, timeout)
    stored: list[str] = []
    if known_hosts.exists():
        stored = sorted(line.strip() for line in known_hosts.read_text().splitlines()
                        if line.strip() and not line.startswith("#"))
    if stored:
        if stored != scanned:
            raise SafeError("The VM host key changed. Stop and inspect the host before continuing.")
    elif not accept:
        shown = ", ".join(key_line_fingerprint(line) for line in scanned)
        raise SafeError(f"Host key not pinned yet. Verify {shown} out of band, "
                        "then re-run with --accept-host-key.")
    else:
        write_protected_text(known_hosts, "\n".join(scanned) + "\n")
    return [key_line_fingerprint(line) for line in scanned]


def write_protected_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + ".tmp")
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def ssh_command(target: dict, known_hosts: Path, remote: str, timeout: float,
                stdin_data: str | None = None, stdin_path: Path | None = None):
    arguments = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                 "-o", f"UserKnownHostsFile={known_hosts}", "-o", "ConnectTimeout=15",
                 "-i", str(Path(target["ssh_private_key_path"]).expanduser()),
                 f"{target['ssh_username']}@{target['public_ip']}", remote]
    return run_command("ssh", arguments, timeout, stdin_data=stdin_data, stdin_path=stdin_path)


def ssh_text(target: dict, known_hosts: Path, remote: str, timeout: float = 60) -> str:
    result = ssh_command(target, known_hosts, remote, timeout)
    if result.returncode:
        raise SafeError("Remote command failed; remote diagnostics suppressed.")
    return result.stdout.strip()


def validate_env_text(text: str) -> list[str]:
    """Validate env-file syntax and return the setting names in order."""
    names: list[str] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "\x00" in raw:
            raise SafeError(f"Line {number} of the environment file contains a NUL byte.")
        if "=" not in line:
            raise SafeError(f"Line {number} of the environment file is not a KEY=VALUE line.")
        key, value = line.split("=", 1)
        if not ENV_KEY.fullmatch(key):
            raise SafeError(f"Line {number} of the environment file has an invalid setting name.")
        if "\n" in value or "\r" in value:
            raise SafeError(f"Setting {key} contains a line break.")
        names.append(key)
    if not names:
        raise SafeError("The environment file contains no settings.")
    return names


def command_capture_config(args) -> int:
    """Read the serving revision's environment and write a protected staging file."""
    source = collect_source(args.resource_group, args.app_name, args.timeout)
    values: dict[str, str] = {}
    listed = az_json(["containerapp", "secret", "list", "--resource-group", args.resource_group,
                      "--name", args.app_name, "--show-values"], args.timeout)
    secret_values: dict[str, str] = {}
    for item in listed or []:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str):
            continue
        if item.get("keyVaultUrl"):
            raise SafeError("A secret is backed by Key Vault; resolve it through the operator "
                            "identity before capturing, because this path cannot read it.")
        if not isinstance(item.get("value"), str):
            raise SafeError("A secret entry has no readable value in this deployment.")
        secret_values[name] = item["value"]

    app = az_json(["containerapp", "show", "--resource-group", args.resource_group,
                   "--name", args.app_name], args.timeout)
    containers = ((app.get("properties") or {}).get("template") or {}).get("containers") or []
    for entry in containers[0].get("env") or []:
        name = entry.get("name")
        reference = entry.get("secretRef")
        if reference:
            if reference not in secret_values:
                raise SafeError("A referenced secret could not be resolved; refusing to substitute.")
            values[name] = secret_values[reference]
        elif entry.get("value") is not None:
            values[name] = str(entry["value"])

    for key in REQUIRED_SECRET_ENV:
        if not values.get(key):
            raise SafeError("A required credential setting is missing from the source app.")
    # The only intentional change: the VM keeps the database in the mounted data
    # directory instead of the ephemeral container path.
    values["DATABASE_PATH"] = CONTAINER_DATABASE_PATH
    text = "".join(f"{key}={values[key]}\n" for key in sorted(values))
    names = validate_env_text(text)
    if set(names) != set(values):
        raise SafeError("The captured environment did not round-trip.")
    destination = Path(args.staged_env)
    write_protected_text(destination, text)
    print(f"Captured {len(names)} settings from {source['latest_ready_revision']}.")
    print("Setting names: " + ", ".join(sorted(names)))
    print(f"Protected staging file: {destination} (mode 0600). No value was printed.")
    return 0


def command_install_runtime_env(args) -> int:
    if not args.confirm:
        raise SafeError("Installing the runtime environment requires --confirm; nothing changed.")
    staged = Path(args.staged_env)
    try:
        staged_text = staged.read_text()
    except OSError:
        raise SafeError("The staged environment file could not be read.") from None
    names = validate_env_text(staged_text)
    parsed = dict(line.split("=", 1) for line in staged_text.splitlines()
                  if line.strip() and not line.startswith("#"))
    if parsed.get("DATABASE_PATH") != CONTAINER_DATABASE_PATH:
        raise SafeError("The staged environment does not point the database at the VM data path.")
    for key in REQUIRED_SECRET_ENV:
        if not parsed.get(key):
            raise SafeError("The staged environment is missing a required credential setting.")
    target = vm_summary(args.vm_tf_dir, args.resource_group)
    verify_host_key(target["public_ip"], args.ssh_port, Path(args.known_hosts),
                    args.accept_host_key, args.timeout)
    remote = (f"umask 077; {priv(args)}install -d -m 0700 /etc/erpbridge; "
              f"{priv(args)}sh -c 'cat > /etc/erpbridge/.runtime.env.new && "
              "chmod 0600 /etc/erpbridge/.runtime.env.new"
              f" && mv -f /etc/erpbridge/.runtime.env.new {REMOTE_ENV_FILE}'")
    result = ssh_command(target, Path(args.known_hosts), remote, args.timeout, stdin_data=staged_text)
    if result.returncode:
        raise SafeError("Remote environment install failed; remote diagnostics suppressed.")
    observed = ssh_text(target, Path(args.known_hosts),
                        f"{priv(args)}stat -c '%a %U' {REMOTE_ENV_FILE}; "
                        f"{priv(args)}grep -c '^[A-Za-z_]' {REMOTE_ENV_FILE}",
                        args.timeout)
    lines = observed.splitlines()
    if len(lines) < 2 or lines[0].split()[:2] != ["600", "root"]:
        raise SafeError("The installed environment file has unexpected ownership or mode.")
    if lines[1].strip() != str(len(names)):
        raise SafeError("The installed environment file has an unexpected setting count.")
    print(f"Installed {len(names)} settings to {REMOTE_ENV_FILE} (mode 0600, owner root).")
    print("No value was printed or passed as a command argument.")
    return 0


def command_bootstrap(args) -> int:
    if not args.confirm:
        raise SafeError("Bootstrap requires --confirm; nothing changed.")
    if not DIGEST_REFERENCE.fullmatch(args.image) or not SAFE_IMAGE.fullmatch(args.image):
        raise SafeError("The target image must be a plain digest-pinned reference.")
    target = vm_summary(args.vm_tf_dir, args.resource_group)
    fingerprints = verify_host_key(target["public_ip"], args.ssh_port, Path(args.known_hosts),
                                   args.accept_host_key, args.timeout)
    endpoint = safe_argument(storage_endpoint(args.vm_tf_dir),
                             r"https://[a-z0-9]{3,24}[.]blob[.]core[.]windows[.]net",
                             "The storage endpoint")
    asset_dir = Path(args.asset_dir)
    for name in BUNDLED_ASSETS:
        if not (asset_dir / name).is_file():
            raise SafeError(f"Missing local asset: {name}.")
    archive = Path(args.manifest).parent / "erpbridge-assets.tar.gz"
    archive.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    packed = run_command("tar", ["-czf", str(archive), "-C", str(asset_dir), *BUNDLED_ASSETS],
                         args.timeout)
    if packed.returncode:
        raise SafeError("Could not package the runtime assets.")
    remote_unpack = (f"umask 022; {priv(args)}install -d -m 0755 {REMOTE_ASSET_DIR}; "
                     f"{priv(args)}tar -xzf - -C {REMOTE_ASSET_DIR}; "
                     f"{priv(args)}chmod 0755 {REMOTE_ASSET_DIR}/cloud-config-runtime.sh "
                     f"{REMOTE_ASSET_DIR}/require-database.sh")
    uploaded = ssh_command(target, Path(args.known_hosts), remote_unpack, args.timeout,
                           stdin_path=archive)
    if uploaded.returncode:
        raise SafeError("Uploading the runtime assets failed; remote diagnostics suppressed.")
    remote_install = (f"{priv(args)}{REMOTE_ASSET_DIR}/cloud-config-runtime.sh --confirm "
                      f"--image {args.image} --storage-endpoint {endpoint}"
                      + (" --repair-ownership" if args.repair_ownership else ""))
    outcome = ssh_command(target, Path(args.known_hosts), remote_install, max(args.timeout, 600))
    if outcome.returncode:
        safe_lines = [line for line in (outcome.stdout + outcome.stderr).splitlines()
                      if line.startswith("Bootstrap stopped:")]
        raise SafeError("The runtime bootstrap failed. " +
                        (safe_lines[-1] if safe_lines else "It returned no recognized reason."))
    print(outcome.stdout.strip())
    print("Host key fingerprint(s): " + ", ".join(fingerprints))
    return 0


MONITORING_ASSETS = ("monitor.py", "erpbridge-monitor.service", "erpbridge-monitor.timer",
                     "journald-monitoring.conf")
MONITORING_INSTALLER = ROOT / "demo" / "erpbridge" / "install-monitoring.py"


def monitoring_script(args, target: dict, install: bool) -> str:
    """Only explicitly allowlisted non-secret source is placed in this script."""
    source = MONITORING_INSTALLER.read_bytes()
    if len(source) > 131072:
        raise SafeError("Monitoring installer exceeds size bound.")
    payload = {}
    if install:
        directory = Path(args.asset_dir)
        for name in MONITORING_ASSETS:
            path = directory / name
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 131072:
                raise SafeError("Monitoring asset missing or invalid.")
            payload[name] = base64.b64encode(path.read_bytes()).decode("ascii")
    argv = (["--confirm", "--vm-resource-id", target["resource_id"]] if install else ["--status"])
    # Input is supplied inside the script because Azure RunCommand has no stdin channel.
    program = ("import base64,io,sys;sys.argv=" + repr(["install-monitoring.py"] + argv)
               + ";sys.stdin=io.StringIO(" + repr(json.dumps(payload)) + ");exec(compile(base64.b64decode("
               + repr(base64.b64encode(source).decode("ascii"))
               + "),'install-monitoring.py','exec'))")
    prefix = "" if args.transport == "azure-run-command" else priv(args)
    return prefix + "python3 -c " + shlex.quote(program)


def monitoring_remote(args, target: dict, script: str) -> dict:
    if len(script.encode()) > 1500000:
        raise SafeError("Monitoring transport payload exceeds size bound.")
    if args.transport == "ssh":
        verify_host_key(target["public_ip"], args.ssh_port, Path(args.known_hosts),
                        args.accept_host_key, args.timeout)
        text = ssh_text(target, Path(args.known_hosts), script, max(args.timeout, 120))
    else:
        # A random protected file avoids a shell argument containing the script.
        with tempfile.TemporaryDirectory(prefix="erpbridge-monitor-") as directory:
            path = Path(directory) / "command.sh"
            write_protected_text(path, script + "\n")
            response = az_json(["vm", "run-command", "invoke", "--ids", target["resource_id"],
                                "--command-id", "RunShellScript", "--scripts", "@" + str(path)],
                               max(args.timeout, 180))
        if not isinstance(response, dict) or not isinstance(response.get("value"), list):
            raise SafeError("Azure RunCommand returned an invalid response.")
        messages = response["value"]
        if any(not isinstance(item, dict) or not str(item.get("code", "")).endswith("/succeeded")
               for item in messages):
            raise SafeError("Azure RunCommand failed; diagnostics suppressed.")
        text = "\n".join(str(item.get("message", "")) for item in messages
                         if "StdOut" in str(item.get("code", ""))
                         or "[stdout]" in str(item.get("message", "")))
        # RunCommand commonly wraps stdout/stderr in one message.
        if "[stdout]" in text:
            text = text.split("[stdout]", 1)[1].split("[stderr]", 1)[0].strip()
    if len(text) > 8192:
        raise SafeError("Monitoring response exceeds size bound.")
    result = read_json(text, "Monitoring response")
    if not isinstance(result, dict):
        raise SafeError("Monitoring response was not an object.")
    return result


def command_install_monitoring(args) -> int:
    if not args.confirm:
        raise SafeError("Installing monitoring requires --confirm; nothing changed.")
    target = vm_summary(args.vm_tf_dir, args.resource_group)
    # Tight identity syntax also prevents generated argument injection.
    if not re.fullmatch(r"/subscriptions/[0-9a-fA-F-]{36}/resourceGroups/[A-Za-z0-9_.()-]+/providers/Microsoft.Compute/virtualMachines/[A-Za-z0-9_.-]+", target["resource_id"]):
        raise SafeError("Monitoring VM identity is not valid.")
    result = monitoring_remote(args, target, monitoring_script(args, target, True))
    if result != {"installed": True, "preserved": True}:
        raise SafeError("Monitoring installation did not confirm preservation.")
    print("Monitoring installed; protected workload metadata preserved.")
    return 0


def command_status(args) -> int:
    target = vm_summary(args.vm_tf_dir, args.resource_group)
    if args.transport == "azure-run-command":
        if args.expect_key:
            raise SafeError("Azure monitoring status does not inspect runtime environment keys.")
        observed = monitoring_remote(args, target, monitoring_script(args, target, False))
        allowed = {"monitor_timer", "monitor_service", "backup_age_seconds", "last_sample_age_seconds"}
        if set(observed) != allowed:
            raise SafeError("Unexpected monitoring status fields.")
        print(json.dumps(observed, sort_keys=True))
        return 0
    verify_host_key(target["public_ip"], args.ssh_port, Path(args.known_hosts),
                    args.accept_host_key, args.timeout)
    power = run_command("az", ["vm", "get-instance-view", "--ids", target["resource_id"],
                              "--query", "instanceView.statuses[?starts_with(code, 'PowerState/')].code | [0]",
                              "--output", "tsv", "--only-show-errors"], args.timeout)
    power_state = power.stdout.strip() if not power.returncode else "unknown"
    remote = (
        f"printf 'service=%s\n' \"$({priv(args)}systemctl is-active erpbridge.service 2>/dev/null || true)\"; "
        f"printf 'proxy=%s\n' \"$({priv(args)}systemctl is-active caddy.service 2>/dev/null || true)\"; "
        f"{priv(args)}test -s {REMOTE_ENV_FILE} && printf 'runtime_env=present\n' || printf 'runtime_env=absent\n'; "
        f"{priv(args)}test -s /var/lib/erpbridge/data/erpbridge.db "
        "&& printf 'database=present\n' || printf 'database=absent\n'; "
        # The server answers on loopback only, so readiness is probed from the
        # VM itself with python3, which the bootstrap already requires.
        "printf 'health=%s\n' \"$(python3 -c \"import urllib.request as u;"
        "print(u.urlopen('http://127.0.0.1:8080/mcp/health', timeout=10).status)\" 2>/dev/null "
        "|| echo unreachable)\"; "
        f"{priv(args)}docker ps --filter name=^erpbridge$ --format 'container={{{{.Status}}}}'"
        " 2>/dev/null | head -1; "
        # The guard checks these two values. Reporting them makes a write-permission
        # problem visible before it becomes a failed write.
        f"printf 'data_dir_owner=%s\\n' \"$({priv(args)}stat -c %u /var/lib/erpbridge/data "
        "2>/dev/null || echo unknown)\"; "
        f"printf 'data_dir_mode=%s\\n' \"$({priv(args)}stat -c %a /var/lib/erpbridge/data "
        "2>/dev/null || echo unknown)\"; "
        f"printf 'recorded_container_uid=%s\\n' \"$({priv(args)}cat /etc/erpbridge/container-uid "
        "2>/dev/null || echo unknown)\"; "
        f"printf 'database_file_owner=%s\\n' \"$({priv(args)}stat -c %u "
        "/var/lib/erpbridge/data/erpbridge.db 2>/dev/null || echo unknown)\"; "
        "true")
    # Prove a named variable reached the running container. Only presence is
    # reported, never a value, so this is safe to run at any time.
    for key in (args.expect_key or []):
        safe = safe_argument(key, r"[A-Za-z_][A-Za-z0-9_]*", "The environment key name")
        quote = "'"
        remote += ("; " + priv(args) + "docker exec erpbridge sh -c " + quote
                   + 'if [ -n "$' + safe + '" ]; then echo key_' + safe + '=present; '
                   + 'else echo key_' + safe + '=absent; fi' + quote
                   + " 2>/dev/null || echo key_" + safe + "=unknown")
    observed = ssh_text(target, Path(args.known_hosts), remote, args.timeout)
    print(f"VM power: {power_state}")
    print(observed or "remote status unavailable")
    monitoring = monitoring_remote(args, target, monitoring_script(args, target, False))
    print(json.dumps({key: monitoring.get(key) for key in
                      ("monitor_timer", "monitor_service", "backup_age_seconds", "last_sample_age_seconds")},
                     sort_keys=True))
    return 0


def command_start(args) -> int:
    if not args.confirm:
        raise SafeError("Starting the service requires --confirm; nothing changed.")
    target = vm_summary(args.vm_tf_dir, args.resource_group)
    verify_host_key(target["public_ip"], args.ssh_port, Path(args.known_hosts),
                    args.accept_host_key, args.timeout)
    if args.serving:
        promote = (f"umask 022; {priv(args)}install -m 0644 -o root -g root /opt/erpbridge/Caddyfile.serving "
                   f"/etc/caddy/Caddyfile && {priv(args)}caddy validate --config /etc/caddy/Caddyfile "
                   f"--adapter caddyfile >/dev/null && {priv(args)}systemctl reload caddy")
        ssh_text(target, Path(args.known_hosts), promote, args.timeout)
        print("Reverse proxy switched to the serving configuration.")
    else:
        print("Reverse proxy stays in maintenance mode; pass --serving to promote it.")
    state = ssh_text(target, Path(args.known_hosts),
                     f"{priv(args)}systemctl start erpbridge.service && "
                     f"{priv(args)}systemctl is-active erpbridge.service",
                     max(args.timeout, 300))
    print(f"Service state: {state}")
    return 0 if state.strip() == "active" else 1


def command_stop(args) -> int:
    if not args.confirm:
        raise SafeError("Stopping the service requires --confirm; nothing changed.")
    target = vm_summary(args.vm_tf_dir, args.resource_group)
    verify_host_key(target["public_ip"], args.ssh_port, Path(args.known_hosts),
                    args.accept_host_key, args.timeout)
    remote = (f"{priv(args)}systemctl stop erpbridge.service; "
              f"{priv(args)}install -m 0644 -o root -g root /opt/erpbridge/Caddyfile.maintenance /etc/caddy/Caddyfile; "
              f"{priv(args)}caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null; "
              f"{priv(args)}systemctl reload caddy; printf 'stopped\n'")
    print(ssh_text(target, Path(args.known_hosts), remote, max(args.timeout, 300)))
    print("The reverse proxy now returns maintenance for every path.")
    return 0


def command_ready(args) -> int:
    parsed = urlsplit(CANONICAL_URL)
    url = CANONICAL_URL.rstrip("/") + "/mcp/health"
    deadline = time.monotonic() + args.timeout
    while True:
        arguments = ["--fail", "--silent", "--show-error", "--max-time", "15",
                     "--output", "/dev/null"]
        if args.resolve_ip:
            arguments += ["--resolve", f"{parsed.hostname}:443:{args.resolve_ip}"]
        result = run_command("curl", arguments + [url], 30)
        if result.returncode == 0:
            print(f"Readiness: {url} responded over HTTPS.")
            return 0
        if time.monotonic() >= deadline:
            print(f"Readiness: {url} did not become ready before the deadline.", file=sys.stderr)
            return 1
        time.sleep(args.poll_interval)


def safe_argument(value: str, pattern: str, what: str) -> str:
    """Only strict values may be interpolated into a remote shell command."""
    if not re.fullmatch(pattern, value):
        raise SafeError(f"{what} contains characters that are not allowed here.")
    return value


def priv(args) -> str:
    """Prefix for remote commands that need root.

    The login user is not root, and the runtime bootstrap, the protected
    environment file, and systemctl all require root. Non-interactive sudo fails
    loudly instead of hanging on a password prompt.
    """
    return "" if getattr(args, "remote_root", False) else "sudo -n "


def openssl(arguments: list[str], timeout: float, stdin_data: str | None = None) -> str:
    result = run_command("openssl", arguments, timeout, stdin_data=stdin_data)
    if result.returncode:
        raise SafeError("The local certificate or key could not be read.")
    return result.stdout


def san_covers(san_text: str, hostname: str) -> bool:
    """True when an exact or single-label wildcard DNS entry covers the host."""
    host = hostname.lower()
    for entry in re.findall(r"DNS:([^,\s]+)", san_text):
        candidate = entry.lower()
        if candidate == host:
            return True
        if candidate.startswith("*.") and "." in host and host.split(".", 1)[1] == candidate[2:]:
            return True
    return False


def normalize_fingerprint(value: str) -> str:
    """Reduce an openssl fingerprint line to bare uppercase hex."""
    _, _, tail = value.partition("=")
    return re.sub(r"[^0-9A-Fa-f]", "", tail or value).upper()


def inspect_certificate(cert_path: str, key_path: str, timeout: float,
                        minimum_days: int) -> dict:
    cert = Path(cert_path)
    key = Path(key_path)
    for path, what in ((cert, "certificate"), (key, "private key")):
        if not path.is_file():
            raise SafeError(f"The {what} file does not exist.")
    key_mode = stat.S_IMODE(key.stat().st_mode)
    if key_mode & 0o077:
        raise SafeError(f"The private key is mode {key_mode:04o}. Restrict it to 0600 first.")
    # Validity for at least the requested window, so a nearly expired certificate
    # is never staged as the migration certificate.
    check = run_command("openssl", ["x509", "-in", str(cert), "-noout", "-checkend",
                                    str(minimum_days * 86400)], timeout)
    if check.returncode:
        raise SafeError(f"The certificate is expired or valid for fewer than {minimum_days} days.")
    end_date = openssl(["x509", "-in", str(cert), "-noout", "-enddate"], timeout).strip()
    san_text = openssl(["x509", "-in", str(cert), "-noout", "-ext", "subjectAltName"], timeout)
    host = urlsplit(CANONICAL_URL).hostname or ""
    if not san_covers(san_text, host):
        raise SafeError(f"The certificate does not cover {host}.")
    cert_public = openssl(["x509", "-in", str(cert), "-noout", "-pubkey"], timeout)
    key_public = openssl(["pkey", "-in", str(key), "-pubout"], timeout)
    if cert_public.strip() != key_public.strip():
        raise SafeError("The private key does not belong to this certificate.")
    fingerprint = normalize_fingerprint(
        openssl(["x509", "-in", str(cert), "-noout", "-fingerprint", "-sha256"], timeout))
    if len(fingerprint) != 64:
        raise SafeError("The local certificate fingerprint could not be computed.")
    return {"end_date": end_date, "fingerprint": fingerprint, "host": host, "key_mode": key_mode}


def command_tls_stage(args) -> int:
    """Install a pre-issued certificate and prove it is the one being served."""
    if not args.confirm:
        raise SafeError("Staging TLS material requires --confirm; nothing changed.")
    details = inspect_certificate(args.cert, args.key, args.timeout, args.min_valid_days)
    target = vm_summary(args.vm_tf_dir, args.resource_group)
    verify_host_key(target["public_ip"], args.ssh_port, Path(args.known_hosts),
                    args.accept_host_key, args.timeout)
    host = details["host"]
    cert_text = Path(args.cert).read_text()
    key_text = Path(args.key).read_text()
    remote_setup = (f"umask 077; install -d -m 0700 -o root -g root {REMOTE_TLS_DIR}; "
                    "cat > /etc/caddy/certs/erpbridge.crt && chmod 0644 /etc/caddy/certs/erpbridge.crt")
    staged_cert = ssh_command(target, Path(args.known_hosts), remote_setup, args.timeout,
                              stdin_data=cert_text)
    if staged_cert.returncode:
        raise SafeError("Staging the certificate failed; remote diagnostics suppressed.")
    staged_key = ssh_command(target, Path(args.known_hosts),
                             "umask 077; cat > /etc/caddy/certs/erpbridge.key "
                             "&& chmod 0600 /etc/caddy/certs/erpbridge.key",
                             args.timeout, stdin_data=key_text)
    if staged_key.returncode:
        raise SafeError("Staging the private key failed; remote diagnostics suppressed.")
    remote_activate = ("install -m 0644 -o root -g root /opt/erpbridge/Caddyfile.tls-check /etc/caddy/Caddyfile "
                       "&& caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null "
                       "&& systemctl reload caddy")
    # ssh_text fails the whole command when any step fails, so a clean exit is
    # the signal that the temporary configuration is active.
    ssh_text(target, Path(args.known_hosts), remote_activate, max(args.timeout, 300))
    served = ssh_text(target, Path(args.known_hosts),
                      "echo | openssl s_client -connect 127.0.0.1:443 "
                      f"-servername {host} 2>/dev/null | openssl x509 -noout -fingerprint -sha256",
                      max(args.timeout, 120))
    served_fingerprint = normalize_fingerprint(served)
    if served_fingerprint != details["fingerprint"]:
        raise SafeError("The certificate now served on the VM is not the certificate that was staged.")
    arguments = ["--fail", "--silent", "--show-error", "--output", "/dev/null",
                 "--write-out", "%{http_code}", "--max-time", "30",
                 "--resolve", f"{host}:443:{target['public_ip']}",
                 f"{CANONICAL_URL.rstrip('/')}/mcp/health"]
    probe = run_command("curl", arguments, 60)
    status = probe.stdout.strip()
    if status != "503":
        # --fail makes any 4xx or 5xx non-zero, so inspect the code only when it
        # was reported at all; a working API here would also be wrong.
        raise SafeError(f"The maintenance probe returned '{status or 'no status'}', not 503.")
    print(f"Staged certificate verified for {host}.")
    print(f"Certificate expiry: {details['end_date']}")
    print(f"Fingerprint match: {details['fingerprint'][:16]}...")
    print("Public probe honoured the certificate and returned the maintenance response.")
    print("No private key value was printed or passed as a command argument.")
    return 0


def storage_endpoint(vm_tf_dir: str) -> str:
    """Derive the blob endpoint from the applied VM root.

    The account name is generated per deployment, so a hardcoded default in the
    on-VM tools would silently point at a non-existent account.
    """
    summary = tf_output(vm_tf_dir, "deployment_summary")
    account = summary.get("backup_storage_account")
    if not isinstance(account, str) or not re.fullmatch(r"[a-z0-9]{3,24}", account):
        raise SafeError("The VM root reported no usable backup storage account.")
    return f"https://{account}.blob.core.windows.net"


def command_backup(args) -> int:
    """Run the on-VM consistent backup. Non-destructive, so no confirmation."""
    target = vm_summary(args.vm_tf_dir, args.resource_group)
    verify_host_key(target["public_ip"], args.ssh_port, Path(args.known_hosts),
                    args.accept_host_key, args.timeout)
    endpoint = storage_endpoint(args.vm_tf_dir)
    command = (f"{priv(args)}/usr/bin/python3 /opt/erpbridge/backup.py "
               f"--storage-endpoint {endpoint}")
    if args.dry_run:
        command += " --dry-run"
    if args.scheduled:
        # Run the unit itself. This is the only way to prove the scheduled path,
        # which differs from a manual run because the unit supplies the endpoint.
        ssh_text(target, Path(args.known_hosts),
                 f"{priv(args)}systemctl start erpbridge-backup.service || true",
                 max(args.timeout, 900))
        report = ssh_text(target, Path(args.known_hosts),
                          f"{priv(args)}systemctl show -p Result -p ExecMainStatus "
                          f"erpbridge-backup.service; "
                          f"{priv(args)}tail -c 600 /var/lib/erpbridge/backup-status.json",
                          max(args.timeout, 300))
        print(report)
        return 0
    result = ssh_command(target, Path(args.known_hosts), command, max(args.timeout, 900))
    output = (result.stdout + result.stderr).strip()
    if result.returncode:
        # The backup tool prints no credential value, so its own failure line is
        # safe to show and is the difference between a fixable and an opaque run.
        detail = " | ".join(line for line in output.splitlines()[-3:]) or "no output"
        raise SafeError(f"The backup command failed on the VM: {detail}")
    print(output)
    return 0


def command_restore(args) -> int:
    if not args.confirm:
        raise SafeError("Restoring requires --confirm; nothing was read or changed.")
    # Every argument is validated before anything reads Terraform or opens a
    # session, so a rejected value can never reach a remote shell.
    backup_id = safe_argument(args.backup_id, r"[A-Za-z0-9][A-Za-z0-9._:-]{0,200}",
                              "The backup identifier")
    checksum = (safe_argument(args.expect_sha256.lower(), r"[0-9a-f]{64}",
                              "The expected checksum") if args.expect_sha256 else None)
    source = (safe_argument(args.source_file, r"/[A-Za-z0-9._/-]{1,200}",
                            "The source path") if args.source_file else None)
    manifest = (safe_argument(args.expect_manifest, r"/[A-Za-z0-9._/-]{1,200}",
                              "The manifest path") if args.expect_manifest else None)
    parts = [f"{priv(args)}/usr/bin/python3 /opt/erpbridge/restore.py",
             "--storage-endpoint", storage_endpoint(args.vm_tf_dir),
             "--backup-id", backup_id, "--confirm"]
    if checksum:
        parts += ["--expect-sha256", checksum]
    if source:
        parts += ["--source-file", source]
    if manifest:
        parts += ["--expect-manifest", manifest]
    target = vm_summary(args.vm_tf_dir, args.resource_group)
    verify_host_key(target["public_ip"], args.ssh_port, Path(args.known_hosts),
                    args.accept_host_key, args.timeout)
    print(ssh_text(target, Path(args.known_hosts), " ".join(parts), max(args.timeout, 900)))
    print("Restore command completed. Start the service explicitly when you are ready.")
    return 0


def command_stage(args) -> int:
    """Copy a verified local file into the VM staging directory.

    Used to move a snapshot to the VM without Blob, so a restore rehearsal can
    run before any backup has been uploaded.
    """
    if not args.confirm:
        raise SafeError("Staging a file requires --confirm; nothing changed.")
    source = Path(args.file)
    if not source.is_file():
        raise SafeError("The local file does not exist.")
    name = safe_argument(args.remote_name or source.name, r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}",
                         "The remote file name")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    target = vm_summary(args.vm_tf_dir, args.resource_group)
    verify_host_key(target["public_ip"], args.ssh_port, Path(args.known_hosts),
                    args.accept_host_key, args.timeout)
    remote = (f"umask 077; {priv(args)}install -d -m 0700 -o root -g root /var/lib/erpbridge/staging; "
              f"{priv(args)}sh -c 'cat > /var/lib/erpbridge/staging/{name}.new && "
              f"chmod 0600 /var/lib/erpbridge/staging/{name}.new && "
              f"mv -f /var/lib/erpbridge/staging/{name}.new /var/lib/erpbridge/staging/{name}'")
    result = ssh_command(target, Path(args.known_hosts), remote, max(args.timeout, 600),
                         stdin_path=source)
    if result.returncode:
        raise SafeError("Staging the file failed; remote diagnostics suppressed.")
    observed = ssh_text(target, Path(args.known_hosts),
                        f"{priv(args)}sha256sum /var/lib/erpbridge/staging/{name}", args.timeout)
    if digest not in observed:
        raise SafeError("The staged file does not match the local checksum.")
    print(f"Staged {name} ({source.stat().st_size} bytes) and verified sha256 {digest[:16]}...")
    return 0


def command_restart(args) -> int:
    """Restart the service so changed environment values take effect.

    The startup guard runs first and on its own. If the guard rejects the current
    configuration, the service is left running instead of being taken down by a
    bad change. That is the difference between a refused change and an outage.
    """
    if not args.confirm:
        raise SafeError("Restarting requires --confirm; nothing changed.")
    target = vm_summary(args.vm_tf_dir, args.resource_group)
    verify_host_key(target["public_ip"], args.ssh_port, Path(args.known_hosts),
                    args.accept_host_key, args.timeout)
    guard = ssh_command(target, Path(args.known_hosts),
                        f"{priv(args)}/opt/erpbridge/require-database.sh", args.timeout)
    if guard.returncode:
        # Relay only the guard's own safe line. Anything else on the wire could
        # be provider or shell diagnostics, which every other command suppresses.
        blocked = [line for line in (guard.stdout + guard.stderr).splitlines()
                   if line.startswith("ERPBridge startup blocked:")]
        reason = blocked[-1] if blocked else "the guard returned no recognized reason"
        raise SafeError("The startup guard rejected the current configuration, so the running "
                        f"service was left untouched. {reason}")
    state = ssh_text(target, Path(args.known_hosts),
                     f"{priv(args)}systemctl restart erpbridge.service && "
                     f"{priv(args)}systemctl is-active erpbridge.service",
                     max(args.timeout, 300))
    print(f"Service state after restart: {state}")
    return 0 if state.strip() == "active" else 1


def build_parser() -> Parser:
    parser = Parser(description="ERPBridge VM migration helper. Read-only by default.")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST),
                        help="Protected manifest path for preflight output.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_timeout(target: Parser, default: float = 30.0) -> None:
        """Every operation that talks to Azure or the VM accepts its own timeout."""
        target.add_argument("--timeout", type=float, default=default,
                            help="Per-command timeout in seconds.")

    preflight = subparsers.add_parser("preflight", help="Record the source app identity.")
    preflight.add_argument("--resource-group", default=os.environ.get("RESOURCE_GROUP", "erpbridge-hq"))
    preflight.add_argument("--app-name", default=os.environ.get("BRIDGE_APP", "erpbridge-server"))
    preflight.add_argument("--tf-dir", default=os.environ.get("TF_DIR", str(ROOT / "terraform")))
    preflight.add_argument("--expected-port", type=int, default=8080)
    preflight.add_argument("--subscription-id", default=os.environ.get("AZURE_SUBSCRIPTION_ID"))
    preflight.add_argument("--check", help="Compare against an existing manifest instead of writing.")
    add_timeout(preflight)
    preflight.set_defaults(handler=command_preflight)

    cost = subparsers.add_parser("cost", help="Render an itemized estimate from a price sheet.")
    cost.add_argument("--price-sheet", required=True)
    cost.add_argument("--running-hours", required=True)
    cost.add_argument("--backup-gb", default="0")
    cost.add_argument("--backup-operations", default="0")
    cost.add_argument("--outbound-gb", default="0")
    cost.add_argument("--aca-retained-months", default="1")
    cost.set_defaults(handler=command_cost)
    add_timeout(cost, default=1.0)

    vm_defaults = {
        "tf_dir": os.environ.get("ERPBridge_VM_TF_DIR", str(ROOT / "terraform-erpbridge")),
        "known_hosts": str(DEFAULT_ARTIFACT_DIR / "known_hosts"),
    }

    def add_vm_options(parser: Parser) -> None:
        parser.add_argument("--vm-tf-dir", default=vm_defaults["tf_dir"])
        parser.add_argument("--known-hosts", default=vm_defaults["known_hosts"])
        parser.add_argument("--ssh-port", type=int, default=22)
        add_timeout(parser)
        parser.add_argument("--accept-host-key", action="store_true",
                            help="Pin the scanned host key on first use, after verifying it.")
        parser.add_argument("--remote-root", action="store_true",
                            help="The remote login user is already root; do not use sudo.")
        parser.add_argument("--resource-group", default=os.environ.get("RESOURCE_GROUP", "erpbridge-hq"))

    capture = subparsers.add_parser("capture-config",
                                    help="Capture the serving revision environment into a protected file.")
    capture.add_argument("--app-name", default=os.environ.get("BRIDGE_APP", "erpbridge-server"))
    capture.add_argument("--resource-group", default=os.environ.get("RESOURCE_GROUP", "erpbridge-hq"))
    capture.add_argument("--staged-env", default=str(DEFAULT_ARTIFACT_DIR / "runtime.env.staged"))
    add_timeout(capture)
    capture.set_defaults(handler=command_capture_config)

    install = subparsers.add_parser("install-runtime-env",
                                    help="Install the staged environment on the VM over SSH.")
    install.add_argument("--staged-env", default=str(DEFAULT_ARTIFACT_DIR / "runtime.env.staged"))
    install.add_argument("--confirm", action="store_true")
    add_vm_options(install)
    install.set_defaults(handler=command_install_runtime_env)

    bootstrap = subparsers.add_parser("bootstrap", help="Upload runtime assets and prepare the VM.")
    bootstrap.add_argument("--image", default=DEFAULT_TARGET_IMAGE)
    bootstrap.add_argument("--asset-dir", default=str(ROOT / "demo" / "erpbridge"))
    bootstrap.add_argument("--confirm", action="store_true")
    bootstrap.add_argument("--repair-ownership", action="store_true",
                           help="Correct only the data directory owner when it drifted.")
    add_vm_options(bootstrap)
    bootstrap.set_defaults(handler=command_bootstrap)

    monitoring = subparsers.add_parser("install-monitoring", help="Install only monitoring assets.")
    monitoring.add_argument("--confirm", action="store_true")
    monitoring.add_argument("--asset-dir", default=str(ROOT / "demo" / "erpbridge"))
    monitoring.add_argument("--transport", choices=("ssh", "azure-run-command"), default="ssh")
    add_vm_options(monitoring)
    monitoring.set_defaults(handler=command_install_monitoring)

    status = subparsers.add_parser("status", help="Report VM power and service state.")
    status.add_argument("--transport", choices=("ssh", "azure-run-command"), default="ssh")
    status.add_argument("--expect-key", action="append", metavar="NAME",
                        help="Report whether a variable reached the running container.")
    add_vm_options(status)
    status.set_defaults(handler=command_status)

    start = subparsers.add_parser("start", help="Start the service, optionally promoting the proxy.")
    start.add_argument("--serving", action="store_true")
    start.add_argument("--confirm", action="store_true")
    add_vm_options(start)
    start.set_defaults(handler=command_start)

    stop = subparsers.add_parser("stop", help="Stop the service and return the proxy to maintenance.")
    stop.add_argument("--confirm", action="store_true")
    add_vm_options(stop)
    stop.set_defaults(handler=command_stop)

    restart = subparsers.add_parser("restart",
                                     help="Restart the service after validating the guard.")
    restart.add_argument("--confirm", action="store_true")
    add_vm_options(restart)
    restart.set_defaults(handler=command_restart)

    stage = subparsers.add_parser("stage", help="Copy a verified local file into VM staging.")
    stage.add_argument("--file", required=True)
    stage.add_argument("--remote-name")
    stage.add_argument("--confirm", action="store_true")
    add_vm_options(stage)
    stage.set_defaults(handler=command_stage)

    tls = subparsers.add_parser("tls-stage",
                               help="Stage a pre-issued certificate and prove it is served.")
    tls.add_argument("--cert", required=True, help="Local PEM certificate chain.")
    tls.add_argument("--key", required=True, help="Local PEM private key, mode 0600.")
    tls.add_argument("--min-valid-days", type=int, default=MINIMUM_CERTIFICATE_DAYS)
    tls.add_argument("--confirm", action="store_true")
    add_vm_options(tls)
    tls.set_defaults(handler=command_tls_stage)

    ready = subparsers.add_parser("ready", help="Wait for public HTTPS readiness.")
    backup = subparsers.add_parser("backup", help="Run the on-VM consistent backup now.")
    backup.add_argument("--scheduled", action="store_true",
                        help="Trigger the systemd timer unit and report its result.")
    backup.add_argument("--dry-run", action="store_true",
                        help="Verify a snapshot on the VM without uploading it.")
    add_vm_options(backup)
    backup.set_defaults(handler=command_backup)

    restore = subparsers.add_parser("restore", help="Restore a backup into the stopped VM service.")
    restore.add_argument("--backup-id", required=True)
    restore.add_argument("--expect-sha256")
    restore.add_argument("--expect-manifest")
    restore.add_argument("--source-file", help="Rehearsal source on the VM instead of a download.")
    restore.add_argument("--confirm", action="store_true")
    add_vm_options(restore)
    restore.set_defaults(handler=command_restore)

    ready.add_argument("--resolve-ip", help="Pin the check to a specific address without changing DNS.")
    ready.add_argument("--poll-interval", type=float, default=5)
    add_timeout(ready)
    ready.set_defaults(handler=command_ready)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if getattr(args, "timeout", 1) <= 0:
        raise SafeError("Timeout must be positive.")
    return args.handler(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SafeError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, KeyError, AttributeError):
        print("Migration helper failed; check local configuration.", file=sys.stderr)
        sys.exit(1)
