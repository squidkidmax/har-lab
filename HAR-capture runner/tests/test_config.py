"""Configuration boundary tests; no browser or network is needed."""

import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from capture_runner.config import (
    ConfigError, MissingCredential, load_config, resolve_credentials,
    select_pairs, validate_config,
)


def plan():
    return {
        "profiles": [{"id": "profile_01", "accounts": {
            "site_01": {"username": {"value": "demo-user"},
                        "password": {"env": "HAR_TEST_PASSWORD"}}
        }}, {"id": "profile_02"}],
        "sites": [{"id": "site_01", "start_url": "https://example.test/login",
                   "steps": [{"action": "fill", "locator": {"label": "Username"},
                              "credential": "username"}],
                   "checks": [{"action": "expect_visible", "locator": {"css": "#account"}}]},
                  {"id": "site_02", "enabled": False, "start_url": "",
                   "steps": [], "checks": []}],
    }


class ConfigTests(unittest.TestCase):
    def test_load_defaults_without_requiring_env_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(plan()), encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                loaded = load_config(path)
        self.assertEqual(loaded["timeout_ms"], 30_000)

    def test_pair_selection_isolated_profiles_enabled_sites_and_filters(self):
        config = plan()
        pairs = select_pairs(config)
        self.assertEqual([(p["id"], s["id"]) for p, s in pairs],
                         [("profile_01", "site_01"), ("profile_02", "site_01")])
        self.assertEqual(len(select_pairs(config, profiles=["profile_02"], sites=["site_01"])), 1)
        for filters in ({"profiles": ["absent"]}, {"sites": ["absent"]},
                        {"sites": ["site_02"]}, {"profiles": "profile_01"}):
            with self.subTest(filters=filters), self.assertRaises(ConfigError):
                select_pairs(config, **filters)

    def test_ids_prevent_paths_and_duplicates(self):
        for bad_id in ("../escape", "a/b", "a\\b", "UPPER", "", "a" * 65):
            config = plan()
            config["profiles"][0]["id"] = bad_id
            with self.subTest(bad_id=bad_id), self.assertRaises(ConfigError):
                validate_config(config)
        config = plan()
        config["profiles"].append(copy.deepcopy(config["profiles"][0]))
        with self.assertRaises(ConfigError):
            validate_config(config)

    def test_enabled_sites_require_valid_start_and_completion_check(self):
        for url in ("", "file:///etc/passwd", "https://user:secret@example.test/",
                    "https://example.test:99999/", "https://exa mple.test/", "https://"):
            config = plan()
            config["sites"][0]["start_url"] = url
            with self.subTest(url=url), self.assertRaises(ConfigError) as caught:
                validate_config(config)
            self.assertNotIn("secret", str(caught.exception))
        config = plan()
        config["sites"][0]["checks"] = []
        with self.assertRaises(ConfigError):
            validate_config(config)

    def test_combined_capture_filenames_cannot_collide(self):
        config = plan()
        config["profiles"] = [{"id": "a__b"}, {"id": "a"}]
        config["sites"][0]["id"] = "c"
        config["sites"][1]["id"] = "b__c"
        # Even a disabled slot is checked, so enabling it cannot introduce an
        # overwrite between (a__b, c) and (a, b__c).
        with self.assertRaisesRegex(ConfigError, "duplicate capture filenames"):
            validate_config(config)

    def test_case_variants_are_rejected_for_case_insensitive_filesystems(self):
        config = plan()
        config["profiles"] = [{"id": "profile"}, {"id": "Profile"}]
        with self.assertRaises(ConfigError):
            validate_config(config)

    def test_checks_cannot_execute_mutating_actions(self):
        config = plan()
        config["sites"][0]["checks"] = [{"action": "click", "locator": {"text": "Delete"}}]
        with self.assertRaises(ConfigError):
            validate_config(config)

    def test_supported_steps_and_locator_types(self):
        config = plan()
        config["sites"][0]["steps"] = [
            {"action": "goto", "url": "/login", "expected_status": 200},
            {"action": "fill", "locator": {"label": "Name"}, "value": ""},
            {"action": "click", "locator": {"role": "button", "name": "Sign in"}},
            {"action": "select", "locator": {"css": "select"}, "value": "one"},
            {"action": "press", "locator": {"text": "Search"}, "key": "Enter"},
            {"action": "manual", "message": "Complete verification"},
            {"action": "expect_visible", "locator": {"css": "#ok"}},
            {"action": "expect_text", "locator": {"css": "h1"}, "text": "Welcome"},
            {"action": "expect_url", "url": "**/account", "timeout_ms": 1234},
        ]
        validate_config(config)

    def test_rejects_invalid_steps_and_types(self):
        invalid = [
            {"action": "fill", "locator": {"css": "input"}},
            {"action": "fill", "locator": {"css": "input"}, "value": "x", "credential": "password"},
            {"action": "click", "locator": {"css": "input", "text": "Other"}},
            {"action": "click", "locator": {"role": "button"}},
            {"action": "click", "locator": {"css": "input"}, "typo": "x"},
            {"action": "goto", "url": "javascript:alert(1)"},
            {"action": "goto", "url": "/test", "expected_status": True},
            {"action": "goto", "url": "/test", "expected_status": 600},
            {"action": "manual", "message": "Continue", "timeout_ms": 0},
            {"action": ["click"]},
        ]
        for step in invalid:
            config = plan()
            config["sites"][0]["steps"] = [step]
            with self.subTest(step=step), self.assertRaises(ConfigError):
                validate_config(config)
        for timeout in (0, 300001, True, 1.5, "1000"):
            config = plan()
            config["timeout_ms"] = timeout
            with self.subTest(timeout=timeout), self.assertRaises(ConfigError):
                validate_config(config)

    def test_credential_resolution_and_missing_values_do_not_echo_secrets(self):
        profile = plan()["profiles"][0]
        with patch.dict(os.environ, {"HAR_TEST_PASSWORD": "sensitive-value"}, clear=True):
            self.assertEqual(resolve_credentials(profile, "site_01"),
                             {"username": "demo-user", "password": "sensitive-value"})
        for env in ({}, {"HAR_TEST_PASSWORD": ""}):
            with patch.dict(os.environ, env, clear=True), self.assertRaises(MissingCredential) as caught:
                resolve_credentials(profile, "site_01")
            self.assertIn("HAR_TEST_PASSWORD", str(caught.exception))
            self.assertNotIn("demo-user", str(caught.exception))
        self.assertEqual(resolve_credentials({"id": "profile_01"}, "site_01"), {})

    def test_rejects_ambiguous_credentials_unknown_site_and_invalid_env(self):
        invalid = [{"env": "NAME", "value": "secret"}, "secret", {"env": "BAD NAME"},
                   {"value": 123}, {}, {"env": "NAME", "typo": True}]
        for source in invalid:
            config = plan()
            config["profiles"][0]["accounts"]["site_01"]["password"] = source
            with self.subTest(source=source), self.assertRaises(ConfigError) as caught:
                validate_config(config)
            self.assertNotIn("secret", str(caught.exception))
        config = plan()
        config["profiles"][0]["accounts"]["unknown"] = {}
        with self.assertRaises(ConfigError):
            validate_config(config)

    def test_parse_failure_does_not_echo_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text('{"password": "secret", malformed}', encoding="utf-8")
            with self.assertRaises(ConfigError) as caught:
                load_config(path)
        self.assertNotIn("secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
