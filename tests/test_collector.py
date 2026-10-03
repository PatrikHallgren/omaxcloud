import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("collector", ROOT / "collector.py")
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)
EXAMPLES = json.loads((ROOT / "tests/api_examples.json").read_text())
NOW = 1780000000


class FixtureAPI:
    unavailable_until = 0

    def get(self, path, params=None):
        import re
        canonical = re.sub(r"/(servers|sites)/[^/]+", r"/\1/{uuid}", path)
        if canonical not in EXAMPLES:
            raise c.DataError("Unavailable in fixture")
        return copy.deepcopy(EXAMPLES[canonical]["data"])

    def items(self, path):
        return self.get(path)["items"]


def backup(status, ts):
    return {"status": status, "created_at": c.datetime.fromtimestamp(ts, c.timezone.utc).isoformat()}


class CollectorTests(unittest.TestCase):
    def test_uses_published_api_response_shapes(self):
        data = c.collect(FixtureAPI(), c.DEFAULTS, NOW)
        self.assertEqual(data["site_count"], 1)
        server = data["servers"][0]
        self.assertEqual(server["metrics"]["memory_usage"], 68.1)
        self.assertIn("/server/", server["url"])
        site = server["sites"][0]
        self.assertIn("/site/", site["url"])
        self.assertEqual(site["updates"]["summary"]["total_pending"], 7)
        self.assertIsNone(server["maintenance"])

    def test_failed_attempt_does_not_hide_success(self):
        result = c.backup_summary([backup("failed", NOW - 60), backup("completed", NOW - 3600)], NOW, 36)
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["last_success"], NOW - 3600)
        self.assertEqual(result["last_attempt"], NOW - 60)

    def test_unsorted_backups_find_latest_success(self):
        result = c.backup_summary([backup("completed", NOW - 9000), backup("completed", NOW - 300)], NOW, 36)
        self.assertEqual(result["last_success"], NOW - 300)

    def test_backup_unknown_is_not_missing_or_healthy(self):
        self.assertEqual(c.backup_summary(None, NOW, 36)["state"], "unavailable")
        self.assertEqual(c.backup_summary([], NOW, 36)["state"], "missing")
        self.assertEqual(c.backup_summary([{"status": "completed", "date": "bad"}], NOW, 36)["state"], "unknown")

    def test_overdue_even_when_latest_attempt_is_running(self):
        result = c.backup_summary([backup("running", NOW - 10), backup("completed", NOW - 150000)], NOW, 36)
        self.assertEqual(result["state"], "overdue")

    def test_pagination_uses_all_pages(self):
        api = c.API("fake", spacing=0)
        with patch.object(api, "get", side_effect=[{"items": [{"id": 1}], "pagination": {"last_page": 2}}, {"items": [{"id": 2}], "pagination": {"last_page": 2}}]) as get:
            self.assertEqual(len(api.items("/servers")), 2)
            self.assertEqual(get.call_args.args[1]["page"], 2)

    def test_missing_pagination_is_explicit_error(self):
        api = c.API("fake", spacing=0)
        with patch.object(api, "get", return_value={"items": []}):
            with self.assertRaises(c.DataError): api.items("/servers")

    def test_optional_error_does_not_remove_server(self):
        api = FixtureAPI()
        original = api.get
        def get(path, params=None):
            if path.endswith("/ssl"): raise c.DataError("Permission unavailable")
            return original(path, params)
        api.get = get
        result = c.collect(api, c.DEFAULTS, NOW)
        self.assertEqual(result["site_count"], 1)
        self.assertGreater(result["unavailable_count"], 0)

    def test_no_credentials_in_error_messages(self):
        api = c.API("super-secret-token", spacing=0)
        with patch.object(api.opener, "open", side_effect=HTTPError("url", 401, "secret", {}, None)):
            with self.assertRaises(c.DataError) as ctx: api.get("/servers")
            self.assertNotIn("super-secret", str(ctx.exception))
            self.assertIn("rejected", str(ctx.exception))

    def test_rate_limit_cooldown(self):
        api = c.API("fake", spacing=0)
        with patch.object(api.opener, "open", side_effect=HTTPError("url", 429, "limited", {"Retry-After": "180"}, None)) as opener:
            with self.assertRaises(c.DataError): api.get("/servers")
            self.assertGreater(api.unavailable_until, time.time() + 170)
            with self.assertRaises(c.DataError): api.get("/servers")
            self.assertEqual(opener.call_count, 1)

    def test_api_does_not_follow_redirect_with_token(self):
        self.assertIsNone(c.NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.com"))

    def test_dashboard_urls_reject_unsafe_schemes(self):
        for url in ["file:///etc/passwd", "javascript:alert(1)", "https://app.xcloud.host@evil.test", "http://app.xcloud.host"]:
            self.assertEqual(c.safe_url(url, True), "")

    def test_ssh_host_cannot_inject_arguments(self):
        for host in ["-oProxyCommand=evil", "host;reboot", "host $(evil)", "user@host\nreboot"]:
            with self.assertRaises(c.DataError): c.ssh_health(host)

    def test_http_checks_disabled_by_default(self):
        with patch.object(c, "http_check") as http:
            c.collect(FixtureAPI(), c.DEFAULTS, NOW)
            http.assert_not_called()

    def test_http_check_skips_private_network(self):
        with patch.object(c.socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]):
            self.assertEqual(c.http_check("https://example.com"), "skipped: non-public address")

    def test_offline_keeps_previous_timestamp(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(c, "CACHE", Path(directory)), patch.object(c, "read_token", return_value="fake"), patch.object(c, "load_config", return_value=c.DEFAULTS):
            previous = {"checked_at": NOW, "servers": [{"id": "a"}], "next_check": 0}
            c.atomic_json(c.CACHE / "snapshot.json", previous)
            with patch.object(c, "collect", side_effect=c.DataError("Offline")):
                current = c.tick()
            self.assertEqual(current["checked_at"], NOW)
            self.assertEqual(current["servers"], previous["servers"])
            self.assertEqual(current["error"], "Offline")

    def test_not_due_never_reads_token_or_calls_api(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(c, "CACHE", Path(directory)), patch.object(c, "read_token") as token:
            c.atomic_json(c.CACHE / "snapshot.json", {"next_check": time.time() + 3600})
            c.tick()
            token.assert_not_called()

    def test_insecure_token_permissions_rejected(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(c, "CONFIG", Path(directory)):
            token = c.CONFIG / "token"; token.write_text("fake"); token.chmod(0o644)
            with self.assertRaises(c.DataError): c.read_token()
            token.chmod(0o600)
            self.assertEqual(c.read_token(), "fake")

    def test_no_false_recovery_when_site_disappears(self):
        old = {"servers": [{"id": "s", "issues": [], "sites": [{"id": "site", "issues": [{"key": "ssl"}]}]}]}
        new = {"servers": [{"id": "s", "issues": [], "sites": []}], "unavailable_count": 0}
        with patch.object(c.subprocess, "run") as notify:
            c.notification_changes(old, new)
            notify.assert_not_called()

    def test_unchanged_issue_does_not_repeat_notification(self):
        state = {"servers": [{"id": "s", "issues": [{"key": "disk"}], "sites": []}]}
        with patch.object(c.subprocess, "run") as notify:
            c.notification_changes(state, state)
            notify.assert_not_called()


if __name__ == "__main__": unittest.main()
