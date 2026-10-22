"""Validate capture plans and resolve account secrets without logging values."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit


class ConfigError(ValueError):
    """A capture plan is invalid."""


class MissingCredential(ConfigError):
    """A configured account value is unavailable."""


ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
ENV_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
ACTIONS = {
    "goto", "fill", "click", "select", "press", "manual",
    "expect_visible", "expect_text", "expect_url",
}
CHECK_ACTIONS = {"manual", "expect_visible", "expect_text", "expect_url"}


def _error(message: str) -> None:
    # Do not append the offending input: URLs/config values can contain secrets.
    raise ConfigError(message)


def _text(value: object, field: str, *, empty: bool = False) -> None:
    if not isinstance(value, str) or (not empty and not value.strip()):
        _error(f"{field} must be a nonempty string")


def _identifier(value: object, field: str) -> None:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        _error(f"{field} must match [a-z0-9][a-z0-9_-]{{0,63}}")


def _timeout(value: object) -> None:
    if type(value) is not int or not 1 <= value <= 300_000:
        _error("timeout_ms must be an integer between 1 and 300000")


def _url(value: object, field: str, *, relative: bool = False) -> None:
    _text(value, field)
    if any(char.isspace() or ord(char) < 32 for char in value):
        _error(f"{field} must be a valid HTTP(S) URL")
    try:
        parts = urlsplit(value)
        # Accessing port validates malformed/out-of-range port values.
        _ = parts.port
        if relative and not parts.scheme and not parts.netloc:
            return
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
        ):
            _error(f"{field} must be an HTTP(S) URL without embedded credentials")
    except ValueError:
        _error(f"{field} must be a valid HTTP(S) URL")


def _locator(value: object) -> None:
    if not isinstance(value, dict):
        _error("locator must be an object")
    main = set(value) & {"css", "label", "role", "text"}
    if len(main) != 1:
        _error("locator must have exactly one of css, label, role, or text")
    kind = next(iter(main))
    allowed = {kind, "name"} if kind == "role" else {kind}
    if set(value) - allowed:
        _error("locator contains unsupported fields")
    _text(value[kind], "locator value")
    if kind == "role":
        _text(value.get("name"), "role locator name")


def _step(step: object, *, check: bool = False) -> None:
    if not isinstance(step, dict):
        _error("Each step/check must be an object")
    action = step.get("action")
    if not isinstance(action, str) or action not in ACTIONS:
        _error("A step has an unsupported action")
    if check and action not in CHECK_ACTIONS:
        _error("checks may contain only manual or expect_* actions")
    allowed = {"action", "timeout_ms"}
    if "timeout_ms" in step:
        _timeout(step["timeout_ms"])
    if action in {"fill", "click", "select", "press", "expect_visible", "expect_text"}:
        allowed.add("locator")
        _locator(step.get("locator"))
    if action == "goto":
        allowed |= {"url", "expected_status"}
        _url(step.get("url"), "goto URL", relative=True)
        if "expected_status" in step and (
            type(step["expected_status"]) is not int
            or not 100 <= step["expected_status"] <= 599
        ):
            _error("expected_status must be an integer between 100 and 599")
    elif action == "fill":
        allowed |= {"value", "credential"}
        if ("value" in step) == ("credential" in step):
            _error("fill needs exactly one of value or credential")
        if "value" in step:
            _text(step["value"], "fill value", empty=True)
        else:
            _identifier(step["credential"], "credential key")
    elif action == "select":
        allowed.add("value")
        _text(step.get("value"), "select value", empty=True)
    elif action == "press":
        allowed.add("key")
        _text(step.get("key"), "press key")
    elif action == "manual":
        allowed.add("message")
        _text(step.get("message"), "manual message")
    elif action == "expect_text":
        allowed.add("text")
        _text(step.get("text"), "expected text")
    elif action == "expect_url":
        allowed.add("url")
        _text(step.get("url"), "expected URL pattern")
    if set(step) - allowed:
        _error("A step contains unsupported fields")


def _credentials(account: object) -> None:
    if not isinstance(account, dict):
        _error("Each account must be an object")
    for key, source in account.items():
        _identifier(key, "credential key")
        if not isinstance(source, dict) or set(source) not in ({"env"}, {"value"}):
            _error("Each credential must have exactly one env or value field")
        if "env" in source:
            if not isinstance(source["env"], str) or not ENV_RE.fullmatch(source["env"]):
                _error("Credential env must be a valid environment variable name")
        else:
            _text(source["value"], "credential value")


def validate_config(config: object) -> None:
    """Check structural validity; environment values are resolved only at capture time."""
    if not isinstance(config, dict):
        _error("Configuration must be a JSON object")
    _timeout(config.get("timeout_ms", 30_000))
    for field in ("profiles", "sites"):
        if not isinstance(config.get(field), list) or not config[field]:
            _error(f"{field} must be a nonempty list")
        seen = set()
        for item in config[field]:
            if not isinstance(item, dict):
                _error(f"Each {field} entry must be an object")
            _identifier(item.get("id"), f"{field} ID")
            if item["id"] in seen:
                _error(f"{field} IDs must be unique")
            seen.add(item["id"])

    site_ids = {site["id"] for site in config["sites"]}
    capture_names = set()
    for profile in config["profiles"]:
        for site in config["sites"]:
            # Separating IDs with "__" is ambiguous when IDs contain that
            # sequence. Reject collisions before any case could overwrite HAR,
            # metadata, or label files. casefold also covers case-insensitive
            # filesystems; IDs currently only permit lowercase ASCII.
            name = (profile["id"] + "__" + site["id"]).casefold()
            if name in capture_names:
                _error("Profile/site IDs produce duplicate capture filenames; choose distinct IDs")
            capture_names.add(name)
    for site in config["sites"]:
        enabled = site.get("enabled", True)
        if type(enabled) is not bool:
            _error("Site enabled must be true or false")
        if enabled or site.get("start_url"):
            _url(site.get("start_url"), "Site start_url")
        elif "start_url" in site and not isinstance(site["start_url"], str):
            _error("Site start_url must be a string")
        for field in ("steps", "checks"):
            items = site.get(field, [])
            if not isinstance(items, list):
                _error(f"Site {field} must be a list")
            if field == "checks" and enabled and not items:
                _error("Enabled sites require at least one completion check")
            for item in items:
                _step(item, check=field == "checks")
        if "timeout_ms" in site:
            _timeout(site["timeout_ms"])

    for profile in config["profiles"]:
        accounts = profile.get("accounts", {})
        if not isinstance(accounts, dict):
            _error("Profile accounts must be an object")
        for site_id, account in accounts.items():
            if site_id not in site_ids:
                _error("An account refers to an unknown site ID")
            _credentials(account)


def load_config(path: str | Path) -> dict:
    """Read and validate JSON without echoing potentially sensitive input."""
    try:
        with Path(path).open(encoding="utf-8") as handle:
            config = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConfigError("Could not read configuration as UTF-8 JSON") from None
    validate_config(config)
    config.setdefault("timeout_ms", 30_000)
    return config


def select_pairs(config: dict, profiles: list[str] | None = None,
                 sites: list[str] | None = None) -> list[tuple[dict, dict]]:
    """Return selected pairs in configuration order, skipping disabled sites by default."""
    validate_config(config)

    def selected(field: str, names: list[str] | None) -> list[dict]:
        entries = config[field]
        if names is None:
            return entries
        if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
            _error(f"Selected {field} must be a list of IDs")
        available = {entry["id"] for entry in entries}
        if set(names) - available:
            _error(f"Unknown selected {field} ID")
        return [entry for entry in entries if entry["id"] in names]

    chosen_profiles = selected("profiles", profiles)
    chosen_sites = selected("sites", sites)
    if sites is not None and any(not site.get("enabled", True) for site in chosen_sites):
        _error("A selected site is disabled; enable it before capture")
    return [(profile, site) for profile in chosen_profiles for site in chosen_sites
            if site.get("enabled", True)]


def resolve_credentials(profile: dict, site_id: str) -> dict[str, str]:
    """Resolve only this pair's account; unset or empty env values are blocked."""
    account = profile.get("accounts", {}).get(site_id, {})
    _credentials(account)
    result = {}
    for key, source in account.items():
        if "env" in source:
            value = os.environ.get(source["env"])
            if not value:
                raise MissingCredential(f"Missing environment variable {source['env']} for {key}")
        else:
            value = source["value"]
        result[key] = value
    return result
