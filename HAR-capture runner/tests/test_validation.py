"""Independent invalid-input tests for offline HAR validation."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from capture_runner.validation import validate_har


def sample_har():
    return {
        "log": {
            "version": "1.2", "creator": {"name": "test", "version": "1"},
            "pages": [{"id": "page_1", "title": "Local test",
                       "startedDateTime": "2026-09-12T10:00:00.000Z",
                       "pageTimings": {"onContentLoad": -1, "onLoad": 45}}],
            "entries": [{
                "pageref": "page_1", "startedDateTime": "2026-09-12T10:00:00.000Z",
                "time": 60,
                "request": {
                    "method": "GET", "url": "https://example.test/account",
                    "httpVersion": "HTTP/2.0", "cookies": [], "headers": [],
                    "queryString": [], "headersSize": -1, "bodySize": 0,
                },
                "response": {
                    "status": 200, "statusText": "OK", "httpVersion": "HTTP/2.0",
                    "cookies": [], "headers": [], "redirectURL": "",
                    "headersSize": -1, "bodySize": 2,
                    "content": {"size": 2, "mimeType": "application/json", "text": "{}"},
                },
                "cache": {},
                "timings": {"blocked": -1, "dns": -1, "connect": 30,
                            "send": 5, "wait": 20, "receive": 5, "ssl": 25},
            }],
        }
    }


class ValidationTests(unittest.TestCase):
    def validate(self, document):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.har"
            path.write_text(json.dumps(document), encoding="utf-8")
            return validate_har(path)

    def validate_raw(self, text):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.har"
            path.write_text(text, encoding="utf-8")
            return validate_har(path)

    def test_realistic_multiple_entries_ssl_not_double_counted(self):
        document = sample_har()
        document["log"]["entries"].append(copy.deepcopy(document["log"]["entries"][0]))
        result = self.validate(document)
        self.assertTrue(result["valid"], result)
        self.assertEqual(result["warnings"], [])
        self.assertEqual(result["entry_count"], 2)
        self.assertEqual(result["response_body_count"], 2)
        self.assertEqual(result["status_counts"], {"200": 2})

    def test_invalid_calendar_or_missing_timezone(self):
        for value in ("2026-02-30T10:00:00Z", "2026-09-12T10:00:00", "2026-09-12T25:00:00Z", "secret"):
            with self.subTest(value=value):
                document = sample_har()
                document["log"]["entries"][0]["startedDateTime"] = value
                self.assertFalse(self.validate(document)["valid"])

    def test_invalid_optional_timestamps(self):
        document = sample_har()
        entry = document["log"]["entries"][0]
        entry["cache"] = {"beforeRequest": {"lastAccess": "2026-13-12T10:00:00Z", "eTag": "x", "hitCount": 1}}
        entry["request"]["cookies"] = [{"name": "session", "value": "dummy", "expires": "invalid"}]
        result = self.validate(document)
        self.assertFalse(result["valid"])
        self.assertTrue(any("lastAccess" in value for value in result["errors"]))
        self.assertTrue(any("expires" in value for value in result["errors"]))

    def test_negative_or_boolean_duration_is_invalid(self):
        for value in (-1, -0.5, True):
            document = sample_har()
            document["log"]["entries"][0]["time"] = value
            self.assertFalse(self.validate(document)["valid"])

    def test_timing_sum_mismatch_is_warning(self):
        document = sample_har()
        document["log"]["entries"][0]["time"] = 200
        result = self.validate(document)
        self.assertTrue(result["valid"])
        self.assertTrue(any("timing sum" in message for message in result["warnings"]))

    def test_invalid_timing_sentinel(self):
        for field, value in (("connect", -2), ("connect", -0.5), ("send", -1)):
            document = sample_har()
            document["log"]["entries"][0]["timings"][field] = value
            self.assertFalse(self.validate(document)["valid"])

    def test_missing_required_field(self):
        document = sample_har()
        del document["log"]["entries"][0]["response"]["status"]
        self.assertFalse(self.validate(document)["valid"])

    def test_upstream_schema_omissions_are_corrected(self):
        for field in ("cache", "timings"):
            document = sample_har()
            document["log"]["entries"][0][field] = []
            self.assertFalse(self.validate(document)["valid"])
        document = sample_har()
        document["log"]["entries"][0]["request"]["postData"] = {
            "mimeType": "application/x-www-form-urlencoded", "params": [{"value": "missing-name"}]}
        self.assertFalse(self.validate(document)["valid"])

    def test_postdata_both_representations_are_allowed_with_warning(self):
        document = sample_har()
        document["log"]["entries"][0]["request"]["postData"] = {
            "mimeType": "application/x-www-form-urlencoded", "text": "a=b",
            "params": [{"name": "a", "value": "b"}]}
        result = self.validate(document)
        self.assertTrue(result["valid"])
        self.assertTrue(any("both text and params" in message for message in result["warnings"]))

    def test_original_sizes_are_not_compared_to_sanitized_text(self):
        document = sample_har()
        entry = document["log"]["entries"][0]
        entry["request"]["bodySize"] = 9000
        entry["request"]["postData"] = {"mimeType": "text/plain", "text": "[redacted]"}
        entry["response"]["bodySize"] = 500
        entry["response"]["content"]["size"] = 800
        result = self.validate(document)
        self.assertTrue(result["valid"])
        self.assertEqual(result["warnings"], [])

    def test_empty_har_is_valid_structure_but_has_empty_warning(self):
        document = sample_har()
        document["log"]["entries"] = []
        result = self.validate(document)
        self.assertTrue(result["valid"])
        self.assertEqual(result["entry_count"], 0)
        self.assertIn("empty", result["warnings"][0])

    def test_invalid_json_root_and_values(self):
        for text in ("", "[]", "{}", "{", '{"log":{},"log":{}}', '{"log": NaN}', '{"x": 1e9999}', '{"x": ' + "9" * 1000 + "}"):
            with self.subTest(text=text):
                self.assertFalse(self.validate_raw(text)["valid"])

    def test_utf8_bom_accepted(self):
        self.assertTrue(self.validate_raw("\ufeff" + json.dumps(sample_har()))["valid"])

    def test_errors_do_not_disclose_secret_values_or_file_path(self):
        secret = "KEEP_THIS_SECRET_123"
        document = sample_har()
        document["log"]["entries"][0]["time"] = secret
        document["log"]["entries"][0]["request"]["url"] = secret
        result = self.validate(document)
        self.assertFalse(result["valid"])
        self.assertNotIn(secret, json.dumps(result))
        result = validate_har(Path("/missing/") / secret)
        self.assertNotIn(secret, json.dumps(result))

    def test_external_schema_declared_in_capture_is_never_retrieved(self):
        document = sample_har()
        document["$schema"] = "https://example.test/hostile-schema.json"
        with patch("urllib.request.urlopen", side_effect=AssertionError("No network allowed")):
            self.assertTrue(self.validate(document)["valid"])

    def test_missing_response_body_is_distinguished_from_empty(self):
        document = sample_har()
        del document["log"]["entries"][0]["response"]["content"]["text"]
        result = self.validate(document)
        self.assertTrue(result["valid"])
        self.assertEqual(result["response_body_count"], 0)
        self.assertTrue(any("no embedded text" in message for message in result["warnings"]))

    def test_base64_body_validation(self):
        for value, expected in (("e30=", True), ("not base64!", False)):
            document = sample_har()
            content = document["log"]["entries"][0]["response"]["content"]
            content.update(encoding="base64", text=value)
            self.assertEqual(self.validate(document)["valid"], expected)

    def test_page_references_and_duplicate_identifiers(self):
        document = sample_har()
        document["log"]["entries"][0]["pageref"] = "does-not-exist"
        self.assertFalse(self.validate(document)["valid"])
        document = sample_har()
        document["log"]["pages"] *= 2
        self.assertFalse(self.validate(document)["valid"])

    def test_network_failure_status_is_warning(self):
        document = sample_har()
        document["log"]["entries"][0]["response"]["status"] = 0
        result = self.validate(document)
        self.assertTrue(result["valid"])
        self.assertEqual(result["status_counts"], {"0": 1})
        self.assertTrue(result["warnings"])

    def test_integral_json_number_status_is_counted(self):
        document = sample_har()
        document["log"]["entries"][0]["response"]["status"] = 200.0
        result = self.validate(document)
        self.assertTrue(result["valid"])
        self.assertEqual(result["status_counts"], {"200": 1})

    def test_non_utf8_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.har"
            path.write_bytes(b"\xff\xfe")
            self.assertFalse(validate_har(path)["valid"])


if __name__ == "__main__":
    unittest.main()
