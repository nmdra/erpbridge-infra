"""Offline policy and HTTP fixtures. These do not prove ACA enforcement."""
import contextlib
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
from pathlib import Path
import threading
import unittest
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("uptime_verifier", ROOT / "scripts/verify-uptime-kuma.py")
assert SPEC is not None and SPEC.loader is not None
v = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(v)


def catalogue():
    return json.loads((ROOT / "config/uptime-kuma-monitors.json").read_text())


def fixtures(status=1, age=0):
    cat = catalogue()

    page = {
        "config": {"slug": "erpbridge", "published": True, "footerText": cat["footer"]},
        "incidents": [], "maintenanceList": [], "publicGroupList": [],
    }
    heartbeat = {"heartbeatList": {}, "uptimeList": {}}
    for name in cat["groups"]:
        group = {"id": len(page["publicGroupList"]) + 1, "name": name, "monitorList": []}
        for row in cat["monitors"]:
            if row["public"] and row["group"] == name:
                monitor_id = len(heartbeat["heartbeatList"]) + 1
                group["monitorList"].append({"id": monitor_id, "name": row["label"], "sendUrl": 0, "type": "keyword" if row["keyword"] else "http"})
                timestamp = datetime.now(timezone.utc) - timedelta(seconds=age)
                heartbeat["heartbeatList"][str(monitor_id)] = [{"status": status, "time": timestamp.strftime("%Y-%m-%d %H:%M:%S.%f"), "msg": "", "ping": 32}]
                heartbeat["uptimeList"][str(monitor_id) + "_24"] = 1
        page["publicGroupList"].append(group)
    if status == 3:
        page["maintenanceList"] = [{"id": 1, "title": "Scheduled demo maintenance", "description": "Ends at the published time"}]
    return page, heartbeat


class CatalogueTests(unittest.TestCase):
    def test_canonical_catalogue_and_paths(self):
        v.validate_catalogue(catalogue(), json.loads((ROOT / "config/uptime-kuma-public-paths.json").read_text()))

    def test_reject_policy_mutations(self):
        mutations = [
            lambda c: c["monitors"][0].update(expected_status="200-299"),
            lambda c: c["monitors"][0].update(follow_redirects=True),
            lambda c: c["monitors"][0].update(headers={"Authorization": "not-a-secret"}),
            lambda c: c["monitors"][0].update(url="https://user:pass@example.org/"),
            lambda c: c["monitors"][0].update(url="https://erpbridge.nimendra.online/"),
            lambda c: c["monitors"][0].update(keyword=None),
            lambda c: c["monitors"][0].update(enabled=False),
            lambda c: c["monitors"][0].update(verify_tls=False),
            lambda c: c["monitors"][0].update(save_response_body=True),
            lambda c: c["monitors"][0].update(notifications=["smtp"]),
            lambda c: c["monitors"][2].update(url="https://workflow-automation.web.app/"),
            lambda c: c["monitors"][4].update(public=True, group="Core platform"),
            lambda c: c["monitors"][4].update(expected_status=200),
            lambda c: c["monitors"][4].update(expected_status=403),
            lambda c: c["monitors"][4].update(expected_status="400-499"),
            lambda c: c["monitors"][-1].update(enabled=True),
            lambda c: c.update(footer=""),
            lambda c: c.update(footer="x" * 201),
            lambda c: c.update(footer=None),
            lambda c: c.update(notifications=["email"]),
            lambda c: c.update(monitors=[]),
            lambda c: [r.update(required=False) for r in c["monitors"]],
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutations.index(mutation)):
                data = catalogue()
                mutation(data)
                with self.assertRaises(v.VerificationError):
                    v.validate_catalogue(data)

    def test_public_path_policy(self):
        for path in ("/*", "/api/*", "/socket.io*", "/socket.io/*", "/setup*", "/dashboard*", "/uploads/*"):
            with self.subTest(path=path), self.assertRaises(v.VerificationError):
                v.validate_catalogue(catalogue(), {"excluded_paths": list(v.PUBLIC_PATHS) + [path]})

    def test_exact_health_keyword(self):
        row = catalogue()["monitors"][0]
        self.assertTrue(v.monitor_result(row, 200, '{"status":"ok"}'))
        for status, body in ((200, "maintenance"), (200, '{"status":"bad"}'), (301, '{"status":"ok"}'), (503, '{"status":"ok"}')):
            self.assertFalse(v.monitor_result(row, status, body))

    def test_protected_checks_accept_only_401(self):
        rows = catalogue()["monitors"][4:9]
        self.assertEqual(len(rows), 5)
        for row in rows:
            for code in (200, 204, 301, 302, 307, 308, 400, 401, 403, 404, 500, 503):
                with self.subTest(monitor=row["id"], code=code):
                    self.assertEqual(v.monitor_result(row, code, "not retained"), code == 401)

    def test_offline_cli(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(v.main(["--catalogue-only"]), 0)
        self.assertEqual(json.loads(output.getvalue()), {"catalogue": True})


class PayloadTests(unittest.TestCase):
    def test_up_down_pending_maintenance_distinct(self):
        for state, label in ((0, "down"), (1, "up"), (2, "pending"), (3, "maintenance")):
            with self.subTest(state=state):
                page, beats = fixtures(state)
                result = v.verify_payload(page, beats, catalogue())
                self.assertEqual(result[label], 4)
                self.assertTrue(result["fresh"])
                self.assertEqual(sum(result[k] for k in ("up", "down", "pending", "maintenance")), 4)

    def test_unconfigured_empty_stale_missing_private_disclosure(self):
        mutations = [
            lambda p, h: p["config"].update(published=False),
            lambda p, h: p.update(publicGroupList=[]),
            lambda p, h: p["publicGroupList"][0]["monitorList"].clear(),
            lambda p, h: p["config"].update(footerText=""),
            lambda p, h: h["heartbeatList"].update({"1": []}),
            lambda p, h: h["heartbeatList"].update({"999": h["heartbeatList"]["1"]}),
            lambda p, h: h["uptimeList"].update({"999_24": 1}),
            lambda p, h: h["heartbeatList"]["1"][0].update(msg="raw response secret"),
            lambda p, h: h["heartbeatList"]["1"][0].update(response="raw response"),
            lambda p, h: h["heartbeatList"]["1"][0].update(status=4),
            lambda p, h: h["heartbeatList"]["1"][0].update(time="not a time"),
            lambda p, h: p["publicGroupList"][0]["monitorList"][0].update(name="ERPBridge metrics protection"),
            lambda p, h: p["publicGroupList"][0]["monitorList"][0].update(url="https://erpbridge.nimendra.online/mcp/health", sendUrl=1),
            lambda p, h: p["incidents"].append({"title": "ERPBridge cache-stats protection"}),
            lambda p, h: p["incidents"].append({"title": "Azure", "content": "10.20.30.40"}),
            lambda p, h: p["config"].update(description="https://private.centralindia.azurecontainerapps.io"),
            lambda p, h: p["config"].update(description="operator@example.org"),
            lambda p, h: p["config"].update(description="00000000-0000-0000-0000-000000000004"),
            lambda p, h: p["config"].update(analyticsScriptUrl="https://example.org/code.js"),
            lambda p, h: p["config"].update(description="Bearer not-a-token"),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                page, heartbeat = fixtures()
                mutate(page, heartbeat)
                with self.assertRaises(v.VerificationError):
                    v.verify_payload(page, heartbeat, catalogue())
        for age in (901, -120):
            with self.subTest(age=age), self.assertRaises(v.VerificationError):
                v.verify_payload(*fixtures(age=age), catalogue())


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.responses = {}
        cls.requested = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                cls.requested.append(self.path)
                code, headers, data = cls.responses.get(self.path, (401, {}, b""))
                self.send_response(code)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format, *args):
                pass

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.local = "http://127.0.0.1:" + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.responses.clear()
        self.requested.clear()
        page, heartbeat = fixtures()
        for path in ("/", "/status/erpbridge"):
            self.responses[path] = (200, {"Content-Type": "text/html"}, b'<html><script src="/assets/index-fixture.js"></script></html>')
        for path in ("/assets/index-fixture.js", "/icon.svg", "/favicon.ico"):
            self.responses[path] = (200, {}, b"fixture")
        for path, data in (
            ("/api/entry-page", {"type": "statusPageMatchedDomain", "statusPageSlug": "erpbridge"}),
            ("/api/status-page/erpbridge", page),
            ("/api/status-page/heartbeat/erpbridge", heartbeat),
            ("/api/status-page/erpbridge/manifest.json", {"start_url": "/status/erpbridge"}),
            ("/api/status-page/erpbridge/incident-history", {"incidents": [], "total": 0, "nextCursor": None, "hasMore": False}),
        ):
            self.responses[path] = (200, {"Content-Type": "application/json"}, json.dumps(data).encode())
        self.fetcher = v.Fetcher(timeout=2)

    def fetch(self, url):
        # Production requires HTTPS canonical hosts. Only test transport rewrites
        # these to loopback; no production allow-insecure switch exists.
        parsed = urlsplit(url)
        return self.fetcher(self.local + parsed.path + (("?" + parsed.query) if parsed.query else ""))

    def verify(self):
        return v.verify_site("https://" + v.HOST, catalogue(), fetch=self.fetch)

    def test_full_public_auth_matrix(self):
        self.assertTrue(self.verify()["anonymous_boundary"])
        for path in v.DENIED_PATHS:
            self.assertIn(path, self.requested)

    def test_exposed_or_redirected_admin_fails(self):
        for path in v.DENIED_PATHS:
            for code in (200, 302, 403, 404):
                with self.subTest(path=path, code=code):
                    self.responses[path] = (code, {"Location": "/setup"}, b"never printed")
                    with self.assertRaises(v.VerificationError):
                        self.verify()
                    del self.responses[path]

    def test_redirects_not_followed(self):
        self.responses["/redirect"] = (302, {"Location": "/destination"}, b"not retained")
        status, _, _ = self.fetcher(self.local + "/redirect")
        self.assertEqual(status, 302)
        self.assertNotIn("/destination", self.requested)

    def test_bounded_body(self):
        self.responses["/large"] = (200, {}, b"x" * 30)
        with self.assertRaises(v.VerificationError):
            v.Fetcher(max_bytes=20)(self.local + "/large")

    def test_public_redirect_unconfigured_invalid_json_private_leak(self):
        path = "/api/status-page/erpbridge"
        for status, body in ((302, b""), (404, b""), (200, b"invalid"), (200, b'{"password":"never-print-this"}')):
            with self.subTest(status=status, body=body):
                self.responses[path] = (status, {"Content-Type": "application/json"}, body)
                with self.assertRaises(v.VerificationError):
                    self.verify()

    def test_default_host_exact_entry_redirect(self):
        self.responses["/"] = (302, {"Location": "/status/erpbridge"}, b"")
        self.responses["/api/entry-page"] = (200, {"Content-Type": "application/json"}, b'{"type":"entryPage","entryPage":"statusPage-erpbridge"}')
        result = v.verify_site("https://erpbridge-uptime.offline.centralindia.azurecontainerapps.io", catalogue(), fetch=self.fetch, default_host=True)
        self.assertTrue(result["anonymous_boundary"])
        self.responses["/"] = (302, {"Location": "/setup"}, b"")
        with self.assertRaises(v.VerificationError):
            v.verify_site("https://erpbridge-uptime.offline.centralindia.azurecontainerapps.io", catalogue(), fetch=self.fetch, default_host=True)

    def test_wrong_host_or_credentials_fail_before_fetch(self):
        for base in ("http://" + v.HOST, "https://wrong.example.org", "https://user:secret@" + v.HOST, "https://" + v.HOST + "/setup", "https://" + v.HOST + "?token=x"):
            with self.subTest(base=base), self.assertRaises(v.VerificationError):
                v.verify_site(base, catalogue(), fetch=self.fetch)
        self.assertEqual(self.requested, [])


if __name__ == "__main__":
    unittest.main()
