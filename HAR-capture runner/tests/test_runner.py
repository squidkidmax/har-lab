"""Failure-path tests around the browser boundary; browsers are mocked here."""

import asyncio
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from capture_runner.runner import Blocked, capture_pair


class RunnerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "har").mkdir()
        self.har = self.root / "har" / "profile_01__site_01.har"
        self.metadata = self.root / "metadata" / "profile_01__site_01.json"
        self.profile = {"id": "profile_01"}
        self.site = {"id": "site_01", "start_url": "https://example.test/login",
                     "checks": [{"action": "expect_url", "url": "**/account?**"}]}
        self.page = SimpleNamespace(
            goto=AsyncMock(return_value=SimpleNamespace(status=200)),
            wait_for_url=AsyncMock(), url="https://example.test/account?token=not-for-metadata",
        )

        async def flush():
            self.har.write_text('{"log": {"entries": []}}', encoding="utf-8")

        self.context = SimpleNamespace(
            new_page=AsyncMock(return_value=self.page),
            set_default_timeout=Mock(), set_default_navigation_timeout=Mock(),
            close=AsyncMock(side_effect=flush), storage_state=AsyncMock(return_value={}),
        )
        self.browser = SimpleNamespace(new_context=AsyncMock(return_value=self.context))
        self.valid = {"valid": True, "errors": [], "warnings": [], "entry_count": 1,
                      "response_body_count": 1, "status_counts": {"200": 1}}
        validator = patch("capture_runner.runner.validate_har", return_value=self.valid)
        self.validate = validator.start()
        self.addCleanup(validator.stop)

    async def capture(self, **kwargs):
        options = {"headless": True, "timeout_ms": 1234}
        options.update(kwargs)
        return await capture_pair(self.browser, self.profile, self.site, self.root, **options)

    def saved(self):
        return json.loads(self.metadata.read_text(encoding="utf-8"))

    async def test_defaults_steps_and_glob_url_completion(self):
        result = await self.capture()
        self.assertEqual(result["workflow_status"], "completed")
        self.assertTrue(result["har_finalized"])
        self.context.close.assert_awaited_once()
        self.page.wait_for_url.assert_awaited_once_with(
            "**/account?**", wait_until="domcontentloaded", timeout=1234)
        self.validate.assert_called_once_with(self.har)
        self.assertEqual(self.saved()["final_origin"], "https://example.test")
        self.assertNotIn("not-for-metadata", self.metadata.read_text())

    async def test_missing_environment_credential_is_blocked_before_navigation(self):
        self.profile["accounts"] = {"site_01": {"password": {"env": "HAR_UNITTEST_PASSWORD"}}}
        with patch.dict(os.environ, {}, clear=True):
            result = await self.capture()
        self.assertEqual(result["workflow_status"], "blocked")
        self.assertFalse(result["capture_exists"])
        self.browser.new_context.assert_not_awaited()

    async def test_headless_manual_workflow_is_blocked_before_browser_context(self):
        self.site["checks"] = [{"action": "manual", "message": "Finish login"}]
        result = await self.capture()
        self.assertEqual(result["workflow_status"], "blocked")
        self.browser.new_context.assert_not_awaited()

    async def test_failed_check_preserves_har_and_redacts_exception_message(self):
        self.page.wait_for_url.side_effect = RuntimeError("password=secret-from-webpage")
        result = await self.capture()
        self.assertEqual(result["workflow_status"], "failed")
        self.assertTrue(result["validation"]["valid"])
        self.assertTrue(result["har_finalized"])
        self.assertEqual(result["error"]["action"], "expect_url")
        self.assertNotIn("secret-from-webpage", self.metadata.read_text())

    async def test_error_http_response_does_not_count_as_completion(self):
        self.page.goto.return_value = SimpleNamespace(status=500)
        result = await self.capture()
        self.assertEqual(result["workflow_status"], "failed")
        self.assertTrue(result["har_finalized"])
        self.page.wait_for_url.assert_not_awaited()

    async def test_close_failure_does_not_validate_unfinished_capture(self):
        async def failed_close():
            self.har.write_text("partial", encoding="utf-8")
            raise RuntimeError("secret-in-close-error")

        self.context.close.side_effect = failed_close
        result = await self.capture()
        self.assertEqual(result["workflow_status"], "failed")
        self.assertFalse(result["har_finalized"])
        self.assertFalse(result["validation"]["valid"])
        self.validate.assert_not_called()
        self.assertNotIn("secret-in-close-error", self.metadata.read_text())

    async def test_close_timeout_is_bounded_and_recorded(self):
        pending = asyncio.Event()
        self.context.close.side_effect = pending.wait
        with patch("capture_runner.runner.CLOSE_TIMEOUT_SECONDS", 0.01):
            result = await self.capture()
        self.assertEqual(result["workflow_status"], "failed")
        self.assertEqual(result["close_error"], "TimeoutError")
        self.assertFalse(result["har_finalized"])

    async def test_cancellation_flushes_har_and_records_aborted(self):
        self.page.goto.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.capture()
        result = self.saved()
        self.assertEqual(result["workflow_status"], "aborted")
        self.assertTrue(result["har_finalized"])
        self.assertTrue(self.har.exists())

    async def test_cancellation_during_close_finishes_flush(self):
        closing = asyncio.Event()
        release = asyncio.Event()

        async def delayed_close():
            closing.set()
            await release.wait()
            self.har.write_text('{"log": {"entries": []}}', encoding="utf-8")

        self.context.close.side_effect = delayed_close
        task = asyncio.create_task(self.capture())
        await closing.wait()
        task.cancel()
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.saved()["workflow_status"], "aborted")
        self.assertTrue(self.saved()["har_finalized"])

    async def test_validator_exception_still_saves_failure_metadata(self):
        self.validate.side_effect = OSError("private-value")
        result = await self.capture()
        self.assertEqual(result["workflow_status"], "failed")
        self.assertEqual(result["validation_error"], "OSError")
        self.assertNotIn("private-value", self.metadata.read_text())

    async def test_terminal_eof_blocked_keeps_partial_har(self):
        self.site["checks"] = [{"action": "manual", "message": "Finish login"}]
        result = await self.capture(headless=False, prompt=AsyncMock(side_effect=Blocked("Terminal closed")))
        self.assertEqual(result["workflow_status"], "blocked")
        self.assertTrue(result["har_finalized"])
        self.assertTrue(result["capture_exists"])


if __name__ == "__main__":
    unittest.main()
