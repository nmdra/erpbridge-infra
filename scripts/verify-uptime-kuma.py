#!/usr/bin/env python3
"""Read-only, bounded anonymous Uptime Kuma 2.5.6 acceptance checks.

The catalogue is a manual UI specification, not a Kuma import or admin API.
No credentials, response bodies, exception messages or heartbeat messages print.
"""
import argparse
from datetime import datetime, timezone
import ipaddress
import json
from pathlib import Path
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
# The footer lives in the catalogue so the page and this check cannot drift.
# It must be a nonempty, non-technical value with no private text.
FOOTER_FIELD = "footer"
HOST = "erpbridge-uptime.nimendra.online"
PUBLIC_PATHS = (
    "/", "/status/erpbridge", "/api/entry-page", "/api/status-page/erpbridge",
    "/api/status-page/heartbeat/erpbridge", "/api/status-page/erpbridge/manifest.json",
    "/api/status-page/erpbridge/incident-history", "/assets/*", "/icon.svg", "/favicon.ico",
)
# GETs only. Do not test write methods against live setup or management paths.
DENIED_PATHS = (
    "/setup", "/setup-database", "/setup-database-info", "/dashboard",
    "/manage-status-page", "/manage-status-page/erpbridge", "/socket.io/",
    "/socket.io/?EIO=4&transport=polling", "/metrics", "/api/status-page",
    "/api/push/not-a-real-token", "/api/badge/1/status", "/upload/",
    "/setup?return=/status/erpbridge", "/%73etup", "/dashboard/",
    "/status/erpbridge/../../setup", "/status/erpbridge/%2e%2e/%2e%2e/setup",
    "/api/status-page/erpbridge%2f..%2f..%2fsetup", "/assets/../setup",
    "/assets/%2e%2e/setup", "/status/erpbridge;setup",
)
# Stable IDs, labels, exact endpoints, codes, keywords, intervals and page groups.
CONTRACT = (
    ("erpbridge", "ERPBridge API", "https://erpbridge.nimendra.online/mcp/health", 200, '"status":"ok"', 60, "Core platform", True),
    ("erpnext", "ERPNext demo (on-demand)", "https://erpnext.nimendra.online/api/method/ping", 200, "pong", 60, "ERP demo", True),
    ("workflow", "Workflow Automation", "https://workflow-automation-44201.web.app/", 200, "Agentic Workflow Engine", 60, "Core platform", True),
    ("toolplane", "ToolPlane documentation", "https://toolplane.nimendra.online/", 200, None, 300, "Project resources", False),
    ("erpbridge-auth", "ERPBridge authentication boundary", "https://erpbridge.nimendra.online/mcp/", 401, None, 300, None, False),
    ("erpbridge-metrics", "ERPBridge metrics protection", "https://erpbridge.nimendra.online/metrics", 401, None, 300, None, False),
    ("erpbridge-info", "ERPBridge server-info protection", "https://erpbridge.nimendra.online/api/info", 401, None, 300, None, False),
    ("erpbridge-cache", "ERPBridge cache-stats protection", "https://erpbridge.nimendra.online/api/cache/stats", 401, None, 300, None, False),
    ("erpbridge-tools", "ERPBridge tool-registry protection", "https://erpbridge.nimendra.online/apis/erpbridge.io/v1/tools", 401, None, 300, None, False),
    ("mockerp", "MockERP demo", "https://mockerp.nimendra.online/health", 200, None, 300, None, False),
)


class VerificationError(Exception):
    """Contains a fixed non-sensitive failure code only."""


def require(condition, code):
    if not condition:
        raise VerificationError(code)


def validate_catalogue(catalogue, paths=None):
    require(catalogue.get("version") == 1, "catalogue_version")
    require(catalogue.get("slug") == "erpbridge" and catalogue.get("hostname") == HOST, "catalogue_page")
    require(isinstance(catalogue.get(FOOTER_FIELD), str) and 1 <= len(catalogue[FOOTER_FIELD].strip()) <= 200, "catalogue_footer")
    require(catalogue.get("history_retention_days") == 7 and catalogue.get("notifications") == [], "catalogue_settings")
    require(catalogue.get("groups") == ["Core platform", "ERP demo", "Project resources"], "catalogue_groups")
    rows = catalogue.get("monitors", [])
    require(len(rows) == len(CONTRACT), "catalogue_count")
    require({r.get("id") for r in rows} == {r[0] for r in CONTRACT}, "catalogue_ids")
    for spec in CONTRACT:
        row = next(r for r in rows if r["id"] == spec[0])
        expected = dict(zip(("id", "label", "url", "expected_status", "keyword", "interval_seconds", "group", "required"), spec))
        expected.update(timeout_seconds=15, retries=2, retry_interval_seconds=30,
                        follow_redirects=False, verify_tls=True, method="GET", headers={}, body=None,
                        notifications=[], save_response_body=False, public=spec[6] is not None,
                        enabled=spec[0] != "mockerp", publish_url=False)
        require(row == expected, "catalogue_monitor_policy")
    if paths is not None:
        require(paths == {"excluded_paths": list(PUBLIC_PATHS)}, "public_path_policy")
    return catalogue


def monitor_result(row, status, body):
    """Exact codes and optional case-sensitive keyword, never inverted non-200."""
    return status == row["expected_status"] and (row["keyword"] is None or row["keyword"] in body)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Fetcher:
    def __init__(self, timeout=10, max_bytes=4194304):
        self.timeout = timeout
        self.deadline = time.monotonic() + 180
        self.max_bytes = max_bytes
        self.opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))

    def __call__(self, url):
        require(time.monotonic() < self.deadline, "http_total_deadline")
        request = urllib.request.Request(url, headers={"User-Agent": "erpbridge-uptime-verifier/1", "Accept": "application/json,text/html"})
        try:
            response = self.opener.open(request, timeout=self.timeout)
        except urllib.error.HTTPError as error:
            response = error
        except (OSError, ValueError):
            raise VerificationError("http_transport") from None
        with response:
            body = response.read(self.max_bytes + 1)
            require(len(body) <= self.max_bytes, "http_body_limit")
            return response.code, response.headers, body


def privacy_check(value, catalogue):
    """Defense-in-depth screening, not a proof against arbitrary secret text."""
    text = json.dumps(value, ensure_ascii=False)
    lower = text.lower()
    for row in catalogue["monitors"]:
        if not row["public"]:
            require(row["label"].lower() not in lower and row["url"].lower() not in lower, "private_monitor_leak")
    require(not re.search(r"azurecontainerapps\.io|\b(?:bearer|password|client_secret|authorization|api[_-]?key)\b|[\w.+-]+@[\w.-]+\.[a-z]{2,}", lower), "private_text_leak")
    require(not re.search(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", lower), "identity_leak")
    require(not re.search(r"https?://[^/\s\"]+:[^/\s\"]+@|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", text), "credential_leak")
    for candidate in re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b|\[[0-9a-fA-F:]+\]", text):
        try:
            ipaddress.ip_address(candidate.strip("[]"))
        except ValueError:
            continue
        raise VerificationError("address_leak")
    def walk(obj):
        if isinstance(obj, dict):
            for key, child in obj.items():
                require(key.lower() not in {"password", "username", "headers", "authorization", "token", "secret", "response", "body", "tenantid", "operatorid"}, "private_field_leak")
                walk(child)
        elif isinstance(obj, list):
            for child in obj:
                walk(child)
    walk(value)


def verify_payload(page, heartbeat, catalogue, now=None, max_age=900):
    require(isinstance(page, dict) and isinstance(heartbeat, dict), "payload_shape")
    privacy_check(page, catalogue)
    privacy_check(heartbeat, catalogue)
    config = page.get("config", {})
    require(config.get("slug") == "erpbridge" and config.get("published") is True, "page_unpublished")
    require(catalogue[FOOTER_FIELD] in config.get("footerText", ""), "page_footer")
    require(not config.get("analyticsId") and not config.get("analyticsScriptUrl") and not config.get("customCSS"), "page_extra_content")
    groups = page.get("publicGroupList", [])
    require([g.get("name") for g in groups] == catalogue["groups"], "page_groups")
    expected = {r["label"]: r for r in catalogue["monitors"] if r["public"]}
    ids = set()
    labels = set()
    for group in groups:
        for row in group.get("monitorList", []):
            require(isinstance(row, dict) and set(row) <= {"id", "name", "sendUrl", "type"}, "monitor_private_fields")
            label = row.get("name")
            require(label in expected and label not in labels, "page_monitor_labels")
            require(expected[label]["group"] == group["name"], "page_monitor_group")
            require(row.get("sendUrl") in (False, 0) and row.get("type") in ("http", "keyword"), "monitor_public_policy")
            require(type(row.get("id")) is int and row["id"] > 0 and str(row["id"]) not in ids, "page_monitor_ids")
            labels.add(label)
            ids.add(str(row["id"]))
    require(labels == set(expected), "page_missing_monitors")
    beats = heartbeat.get("heartbeatList", {})
    require(set(beats) == ids, "heartbeat_monitor_set")
    require(set(heartbeat.get("uptimeList", {})) <= {i + "_24" for i in ids}, "uptime_monitor_set")
    now = now or datetime.now(timezone.utc)
    counts = {"up": 0, "down": 0, "pending": 0, "maintenance": 0}
    for samples in beats.values():
        require(isinstance(samples, list) and 0 < len(samples) <= 100, "heartbeat_empty")
        latest = None
        for sample in samples:
            require(isinstance(sample, dict) and set(sample) <= {"status", "time", "msg", "ping"}, "heartbeat_private_fields")
            require(sample.get("msg", "") == "", "heartbeat_message_leak")
            require(type(sample.get("status")) is int and sample["status"] in (0, 1, 2, 3), "heartbeat_status")
            try:
                timestamp = datetime.fromisoformat(sample["time"].replace("Z", "+00:00"))
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
            except (ValueError, KeyError, TypeError, AttributeError):
                raise VerificationError("heartbeat_time") from None
            require((now - timestamp).total_seconds() >= -60, "heartbeat_future")
            require(latest is None or timestamp >= latest[0], "heartbeat_order")
            latest = (timestamp, sample["status"])
        require((now - latest[0]).total_seconds() <= max_age, "heartbeat_stale")
        counts[{0: "down", 1: "up", 2: "pending", 3: "maintenance"}[latest[1]]] += 1
    return {"monitors": len(ids), "fresh": True, "disclosure": True, "private_leak": False, **counts}


def read_json_response(fetch, base, path, catalogue):
    status, headers, body = fetch(base + path)
    require(status == 200, "public_http_status")
    require("application/json" in headers.get("Content-Type", "").lower(), "public_content_type")
    try:
        result = json.loads(body)
    except (ValueError, UnicodeError):
        raise VerificationError("public_json") from None
    privacy_check(result, catalogue)
    return result


def verify_site(base, catalogue, fetch=None, max_age=900, default_host=False):
    parts = urllib.parse.urlsplit(base)
    require(parts.scheme == "https" and not parts.username and not parts.password and parts.path in ("", "/") and not parts.query and not parts.fragment and parts.port in (None, 443), "base_url_policy")
    if default_host:
        require(bool(re.fullmatch(r"erpbridge-uptime\.[a-z0-9.-]+\.azurecontainerapps\.io", parts.hostname or "")), "default_host_policy")
    else:
        require(parts.hostname == HOST, "canonical_host_policy")
    base = base.rstrip("/")
    fetch = fetch or Fetcher()
    for path in ("/", "/status/erpbridge", "/icon.svg", "/favicon.ico"):
        status, headers, body = fetch(base + path)
        if default_host and path == "/" and status == 302:
            require(headers.get("Location") == "/status/erpbridge", "default_entry_redirect")
        else:
            require(status == 200, "public_http_status")
        if path in ("/", "/status/erpbridge") and status == 200:
            privacy_check(body.decode("utf-8", "replace"), catalogue)
            # The Vite bundle is the only approved wildcard. Do not fetch arbitrary
            # remote references or follow redirects even for static resources.
            assets = set(re.findall(rb'(?:src|href)="(/assets/[^"?#]+)"', body))
            require(bool(assets), "page_assets_missing")
            require(len(assets) <= 20, "page_asset_count")
            for asset in sorted(assets):
                target = asset.decode("ascii")
                require(bool(re.fullmatch(r"/assets/[A-Za-z0-9_.-]+", target)), "page_asset_path")
                require(fetch(base + target)[0] == 200, "page_asset_http")
    entry = read_json_response(fetch, base, "/api/entry-page", catalogue)
    require((entry.get("type") == "statusPageMatchedDomain" and entry.get("statusPageSlug") == "erpbridge") or (default_host and entry.get("type") == "entryPage" and entry.get("entryPage") == "statusPage-erpbridge"), "entry_page")
    page = read_json_response(fetch, base, "/api/status-page/erpbridge", catalogue)
    heartbeat = read_json_response(fetch, base, "/api/status-page/heartbeat/erpbridge", catalogue)
    manifest = read_json_response(fetch, base, "/api/status-page/erpbridge/manifest.json", catalogue)
    require(manifest.get("start_url") == "/status/erpbridge", "manifest_page")
    read_json_response(fetch, base, "/api/status-page/erpbridge/incident-history", catalogue)
    result = verify_payload(page, heartbeat, catalogue, max_age=max_age)
    for path in DENIED_PATHS:
        require(fetch(base + path)[0] == 401, "anonymous_boundary")
    return {**result, "anonymous_boundary": True, "public_routes": True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalogue", type=Path, default=ROOT / "config/uptime-kuma-monitors.json")
    parser.add_argument("--public-paths", type=Path, default=ROOT / "config/uptime-kuma-public-paths.json")
    parser.add_argument("--base-url", default="https://" + HOST)
    parser.add_argument("--default-fqdn", help="Also check this exact Azure default hostname, without credentials")
    parser.add_argument("--catalogue-only", action="store_true")
    parser.add_argument("--check-targets", action="store_true", help="Read-only direct catalogue checks; never calls disabled MockERP")
    parser.add_argument("--max-age", type=int, default=900)
    args = parser.parse_args(argv)
    try:
        require(60 <= args.max_age <= 3600, "max_age_policy")
        catalogue = validate_catalogue(json.loads(args.catalogue.read_text()), json.loads(args.public_paths.read_text()))
        result = {"catalogue": True}
        if not args.catalogue_only:
            result.update(verify_site(args.base_url, catalogue, max_age=args.max_age))
            if args.default_fqdn:
                require("/" not in args.default_fqdn, "default_host_policy")
                verify_site("https://" + args.default_fqdn, catalogue, max_age=args.max_age, default_host=True)
                result["default_host"] = True
            if args.check_targets:
                fetch = Fetcher()
                passed = failed = 0
                for row in catalogue["monitors"]:
                    if row["enabled"]:
                        status, _, body = fetch(row["url"])
                        good = monitor_result(row, status, body.decode("utf-8", "replace"))
                        passed += int(good)
                        failed += int(not good)
                result.update(targets_passed=passed, targets_failed=failed)
                # Endpoint failure is not a dashboard deployment failure, but it
                # still makes this explicitly requested direct-target check fail.
                if failed:
                    print(json.dumps(result, sort_keys=True))
                    return 1
        print(json.dumps(result, sort_keys=True))
        return 0
    except VerificationError as error:
        print(json.dumps({"verified": False, "failure": str(error)}))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        print(json.dumps({"verified": False, "failure": "invalid_input_or_transport"}))
    return 1


if __name__ == "__main__":
    sys.exit(main())
