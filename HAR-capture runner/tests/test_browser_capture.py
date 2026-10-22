"""Opt-in real browser integration tests against the loopback demo server."""
import json
import os
import tempfile
import unittest
from pathlib import Path

from capture_runner.__main__ import demo_config
from capture_runner.demo import demo_server
from capture_runner.runner import run_capture


@unittest.skipUnless(os.environ.get("HAR_RUN_BROWSER_TESTS") == "1", "Set HAR_RUN_BROWSER_TESTS=1 to launch local Chrome")
class BrowserCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_five_profiles_capture_distinct_authenticated_sessions(self):
        with tempfile.TemporaryDirectory() as temporary, demo_server() as url:
            root, report = await run_capture(demo_config(url), Path(temporary), headless=True, channel="chrome")
            self.assertEqual(report["counts"]["completed"], 5)
            session_values = set()
            for number, case in enumerate(report["cases"], 1):
                self.assertTrue(case["validation"]["valid"])
                self.assertGreaterEqual(case["validation"]["entry_count"], 5)
                entries = json.loads(Path(case["har"]).read_text())["log"]["entries"]
                profiles = [e for e in entries if e["request"]["url"].endswith("/api/profile")]
                self.assertEqual(len(profiles), 1)
                payload = json.loads(profiles[0]["response"]["content"]["text"])
                self.assertIn(f"profile_{number:02d}", json.dumps(payload))
                self.assertNotIn(f"profile_{number % 5 + 1:02d}", json.dumps(payload))
                for entry in entries:
                    for cookie in entry["response"]["cookies"]:
                        if cookie["name"] == "demo_session":
                            session_values.add(cookie["value"])
                metadata = (root / "metadata" / f"profile_{number:02d}__demo.json").read_text()
                self.assertNotIn("DemoPass-", metadata)
                self.assertEqual(Path(case["har"]).stat().st_mode & 0o777, 0o600)
            self.assertEqual(len(session_values), 5)
            self.assertNotIn("DemoPass-", (root / "manifest.json").read_text())

    async def test_failed_login_keeps_partial_har_and_continues(self):
        with tempfile.TemporaryDirectory() as temporary, demo_server() as url:
            config = demo_config(url, profile_count=2)
            config["timeout_ms"] = 1000
            config["profiles"][0]["accounts"]["demo"]["password"] = {"value": "deliberately-incorrect"}
            root, report = await run_capture(config, Path(temporary), headless=True, channel="chrome")
            self.assertEqual([c["workflow_status"] for c in report["cases"]], ["failed", "completed"])
            self.assertTrue(report["cases"][0]["capture_exists"])
            self.assertTrue(report["cases"][0]["validation"]["valid"])
            self.assertNotIn("deliberately-incorrect", (root / "manifest.json").read_text())
            retry_root, retried = await run_capture(config, Path(temporary), headless=True,
                channel="chrome", retry_failed=root)
            self.assertNotEqual(root, retry_root)
            self.assertEqual(len(retried["cases"]), 1)
            self.assertEqual(retried["cases"][0]["profile_id"], "profile_01")

    async def test_manual_steps_are_blocked_in_headless_mode(self):
        with tempfile.TemporaryDirectory() as temporary, demo_server() as url:
            config = demo_config(url, profile_count=1)
            config["sites"][0]["steps"] = []
            config["sites"][0]["checks"] = [{"action": "manual", "message": "Confirm demo completion"}]
            _, report = await run_capture(config, Path(temporary), headless=True, channel="chrome")
            self.assertEqual(report["cases"][0]["workflow_status"], "blocked")
            self.assertFalse(report["cases"][0]["capture_exists"])


if __name__ == "__main__":
    unittest.main()
