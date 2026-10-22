"""Offline HAR structure and capture-quality validation.

This checks a pinned, bundled HAR schema plus documented semantic checks. It
does not prove that login succeeded, all traffic was recorded, or secrets were
removed. Diagnostics contain schema field paths, never captured field values.
"""

from __future__ import annotations

import base64
import binascii
import ipaddress
import json
import math
import re
from collections import Counter
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from jsonschema import Draft6Validator, FormatChecker
from referencing import Registry, Resource

_BASE = "https://har-capture-runner.invalid/schemas/"
_SCHEMA_DIR = Path(__file__).with_name("schemas")
_TIMING_FIELDS = ("blocked", "dns", "connect", "send", "wait", "receive", "ssl")
_TOTAL_FIELDS = _TIMING_FIELDS[:-1]  # TLS time is already part of connect.
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})\Z")
_MAX_MESSAGES = 100


def _timestamp(value: Any) -> bool:
    if not isinstance(value, str):
        return True  # Type validation belongs to the schema.
    if not _DATE_RE.fullmatch(value):
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


def _absolute_uri(value: Any) -> bool:
    if not isinstance(value, str):
        return True
    if any(char.isspace() or ord(char) < 32 for char in value):
        return False
    try:
        parsed = urlsplit(value)
        if not parsed.scheme:
            return False
        if parsed.scheme.lower() in {"http", "https", "ws", "wss"}:
            _ = parsed.port
            return bool(parsed.hostname)
        return True
    except ValueError:
        return False


def _ip_format(version: int):
    def check(value: Any) -> bool:
        if not isinstance(value, str):
            return True
        try:
            return ipaddress.ip_address(value).version == version
        except ValueError:
            return False
    return check


@lru_cache(maxsize=1)
def _validator() -> Draft6Validator:
    schemas = {path.name: json.loads(path.read_text(encoding="utf-8"))
               for path in _SCHEMA_DIR.glob("*.json")}
    # The bundled upstream schema has these known omissions. Apply the small
    # corrections in memory; preserve the upstream files and license unchanged.
    for name in ("cache.json", "timings.json"):
        schemas[name]["type"] = "object"
    params = schemas["postData.json"]["properties"]["params"]
    params["items"] = {"type": "object", "required": params.pop("required"),
                       "properties": params.pop("properties")}
    for schema in schemas.values():
        for item in schema.get("properties", {}).values():
            if "min" in item:
                item["minimum"] = item.pop("min")
    registry = Registry().with_resources(
        (_BASE + name, Resource.from_contents(schema))
        for name, schema in schemas.items()
    )
    formats = FormatChecker(formats=[])
    formats.checks("date-time")(_timestamp)
    formats.checks("uri")(_absolute_uri)
    formats.checks("ipv4")(_ip_format(4))
    formats.checks("ipv6")(_ip_format(6))
    root = dict(schemas["har.json"], **{"$id": _BASE + "har.json"})
    return Draft6Validator(root, registry=registry, format_checker=formats)


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON member")
        result[key] = value
    return result


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Non-finite JSON number")
    return number


def _finite_int(value: str) -> int:
    number = int(value)
    try:
        if not math.isfinite(number):
            raise ValueError("JSON number exceeds the validator's numeric range")
    except OverflowError:
        raise ValueError("JSON number exceeds the validator's numeric range") from None
    return number


def _reject_constant(_: str) -> None:
    raise ValueError("Non-standard JSON numeric constant")


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _add(messages: list[str], message: str) -> None:
    if len(messages) < _MAX_MESSAGES:
        messages.append(message)
    elif len(messages) == _MAX_MESSAGES:
        messages.append("Further diagnostics omitted.")


def _check_range(container: dict, field: str, path: str, errors: list[str],
                 unknown_allowed: bool = False) -> None:
    value = container.get(field)
    if not _number(value):
        return
    if value < 0 and not (unknown_allowed and value == -1):
        rule = "nonnegative or -1" if unknown_allowed else "nonnegative"
        _add(errors, f"{path}.{field}: must be {rule}.")


def _schema_path(parts: Any) -> str:
    # Schema traversals only visit fixed HAR properties, but do not trust an
    # unexpected key if a future schema begins validating extension objects.
    known = {
        "log", "version", "creator", "browser", "pages", "entries", "comment",
        "name", "value", "startedDateTime", "id", "title", "pageTimings",
        "onContentLoad", "onLoad", "pageref", "time", "request", "response",
        "cache", "timings", "serverIPAddress", "connection", "method", "url",
        "httpVersion", "cookies", "headers", "queryString", "postData",
        "headersSize", "bodySize", "status", "statusText", "content",
        "redirectURL", "path", "domain", "expires", "httpOnly", "secure",
        "mimeType", "text", "params", "fileName", "contentType", "size",
        "compression", "encoding", "beforeRequest", "afterRequest",
        "lastAccess", "eTag", "hitCount", *_TIMING_FIELDS,
    }
    path = "$"
    for part in parts:
        path += f"[{part}]" if isinstance(part, int) else "." + (part if part in known else "<field>")
    return path


def _check_times(container: dict, fields: tuple[str, ...], path: str,
                 errors: list[str]) -> None:
    for field in fields:
        if isinstance(container.get(field), str) and not _timestamp(container[field]):
            _add(errors, f"{path}.{field}: expected an ISO timestamp with timezone and a valid calendar date.")


def _semantics(log: dict, result: dict) -> None:
    errors, warnings = result["errors"], result["warnings"]
    if isinstance(log.get("version"), str) and log["version"] != "1.2":
        _add(errors, "$.log.version: only HAR 1.2 is supported.")
    page_ids: set[str] = set()
    pages = log.get("pages", [])
    for i, page in enumerate(pages if isinstance(pages, list) else []):
        if not isinstance(page, dict):
            continue
        path = f"$.log.pages[{i}]"
        if isinstance(page.get("id"), str):
            if page["id"] in page_ids:
                _add(errors, f"{path}.id: duplicate page identifier.")
            page_ids.add(page["id"])
        _check_times(page, ("startedDateTime",), path, errors)
        if isinstance(page.get("pageTimings"), dict):
            for field in ("onContentLoad", "onLoad"):
                _check_range(page["pageTimings"], field, path + ".pageTimings", errors, True)
    entries = log.get("entries")
    if not isinstance(entries, list):
        return
    result["entry_count"] = len(entries)
    if not entries:
        _add(warnings, "Capture is empty: no HTTP entries were recorded.")
    statuses: Counter[str] = Counter()
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        path = f"$.log.entries[{i}]"
        _check_times(entry, ("startedDateTime",), path, errors)
        _check_range(entry, "time", path, errors)
        if isinstance(entry.get("pageref"), str) and entry["pageref"] not in page_ids:
            _add(errors, f"{path}.pageref: does not refer to a recorded page.")
        timings = entry.get("timings")
        if isinstance(timings, dict):
            for field in _TIMING_FIELDS:
                _check_range(timings, field, path + ".timings", errors,
                             field not in {"send", "wait", "receive"})
            known = [timings.get(field, -1) for field in _TOTAL_FIELDS]
            if all(_number(value) for value in known) and _number(entry.get("time")):
                total = sum(value for value in known if value >= 0)
                tolerance = max(1.0, abs(entry["time"]) * 0.01)
                if abs(entry["time"] - total) > tolerance:
                    _add(warnings, f"{path}.time: differs from the available timing sum (SSL is included in connect; tolerance is 1 ms or 1%).")
            ssl, connect = timings.get("ssl"), timings.get("connect")
            if _number(ssl) and _number(connect) and connect >= 0 and ssl > connect + 1:
                _add(warnings, f"{path}.timings.ssl: exceeds connect time, which should include TLS setup.")
        cache = entry.get("cache")
        if isinstance(cache, dict):
            for stage in ("beforeRequest", "afterRequest"):
                if isinstance(cache.get(stage), dict):
                    _check_times(cache[stage], ("expires", "lastAccess"), path + ".cache." + stage, errors)
                    _check_range(cache[stage], "hitCount", path + ".cache." + stage, errors)
        for direction in ("request", "response"):
            message = entry.get(direction)
            if not isinstance(message, dict):
                continue
            message_path = path + "." + direction
            for field in ("headersSize", "bodySize"):
                _check_range(message, field, message_path, errors, True)
            cookies = message.get("cookies", [])
            for j, cookie in enumerate(cookies if isinstance(cookies, list) else []):
                if isinstance(cookie, dict):
                    _check_times(cookie, ("expires",), f"{message_path}.cookies[{j}]", errors)
        request = entry.get("request")
        if isinstance(request, dict) and isinstance(request.get("postData"), dict):
            data = request["postData"]
            if "text" in data and "params" in data:
                _add(warnings, f"{path}.request.postData: contains both text and params; browsers may export both representations.")
            elif "text" not in data and "params" not in data:
                _add(warnings, f"{path}.request.postData: body content was not embedded.")
        response = entry.get("response")
        if not isinstance(response, dict):
            continue
        status = response.get("status")
        if _number(status) and int(status) == status:
            if status == 0:
                statuses["0"] += 1
                _add(warnings, f"{path}.response.status: zero indicates no HTTP response was recorded.")
            elif 100 <= status <= 599:
                statuses[str(int(status))] += 1
            else:
                _add(warnings, f"{path}.response.status: outside the standard HTTP status range.")
        content = response.get("content")
        if not isinstance(content, dict):
            continue
        _check_range(content, "size", path + ".response.content", errors, True)
        text = content.get("text")
        if isinstance(text, str):
            result["response_body_count"] += 1
            if content.get("encoding") == "base64":
                try:
                    base64.b64decode(text, validate=True)
                except (ValueError, binascii.Error):
                    _add(errors, f"{path}.response.content.text: invalid base64 content.")
            elif content.get("encoding") not in (None, ""):
                _add(warnings, f"{path}.response.content.encoding: unsupported encoding; body decoding was not checked.")
        elif _number(content.get("size")) and content["size"] > 0:
            _add(warnings, f"{path}.response.content: body has a recorded size but no embedded text.")
    result["status_counts"] = dict(sorted(statuses.items()))


def validate_har(path: Path) -> dict[str, Any]:
    """Return validation and coverage counts without exposing capture values.

    An empty HAR may be valid JSON/HAR but is not a useful capture; it produces
    a warning and entry_count=0. Workflow success must be checked separately.
    Original bodySize/content.size are deliberately not compared to text byte
    lengths: decompression, transcoding and sanitization can change the latter.
    """
    result: dict[str, Any] = {
        "valid": False, "errors": [], "warnings": [], "entry_count": 0,
        "response_body_count": 0, "status_counts": {},
    }
    try:
        with Path(path).open(encoding="utf-8-sig") as stream:
            document = json.load(stream, object_pairs_hook=_no_duplicates,
                                 parse_float=_finite_float, parse_int=_finite_int,
                                 parse_constant=_reject_constant)
    except OSError:
        result["errors"].append("HAR file could not be read.")
        return result
    except (ValueError, RecursionError):
        result["errors"].append("Invalid UTF-8 JSON, duplicate members, non-finite numbers, or excessive nesting.")
        return result
    for error in _validator().iter_errors(document):
        _add(result["errors"], f"{_schema_path(error.absolute_path)}: schema {error.validator} constraint failed.")
        if len(result["errors"]) > _MAX_MESSAGES:
            break
    if isinstance(document, dict) and isinstance(document.get("log"), dict):
        _semantics(document["log"], result)
    result["valid"] = not result["errors"]
    return result
