"""Capture isolated browser workflows; never log raw Playwright exceptions."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from playwright.async_api import async_playwright, expect

from .config import MissingCredential, resolve_credentials, select_pairs
from .validation import validate_har

SUCCESS = {"completed", "completed_manual"}
CLOSE_TIMEOUT_SECONDS = 30


class Blocked(Exception):
    pass


class Skipped(Exception):
    pass


class WorkflowFailure(Exception):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(value, f, indent=2)
        f.write("\n")
    temporary.replace(path)


def fingerprint(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def locator(page, definition):
    if "css" in definition:
        return page.locator(definition["css"])
    if "label" in definition:
        return page.get_by_label(definition["label"], exact=True)
    if "role" in definition:
        options = {"name": definition["name"], "exact": True} if "name" in definition else {}
        return page.get_by_role(definition["role"], **options)
    return page.get_by_text(definition["text"], exact=True)


def interpolate(value, profile, credentials):
    value = value.replace("{{profile_id}}", profile["id"])
    for key, replacement in credentials.items():
        value = value.replace("{{" + key + "}}", replacement)
    return value


async def terminal_prompt(message):
    """Keep Playwright's event loop alive while waiting for terminal input."""
    if not sys.stdin.isatty():
        raise Blocked("Interactive terminal required")
    print(message + "\nType done, blocked, or skip: ", end="", flush=True)
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def readable():
        line = sys.stdin.readline()
        if not future.done():
            future.set_result(line)

    try:
        loop.add_reader(sys.stdin.fileno(), readable)
    except (NotImplementedError, PermissionError):
        raise Blocked("Terminal input is unsupported on this platform") from None
    try:
        line = await future
    finally:
        loop.remove_reader(sys.stdin.fileno())
    if not line:
        raise Blocked("Terminal closed")
    return line.strip().lower()


async def execute_step(page, step, site, profile, credentials, prompt):
    action = step["action"]
    timeout = step.get("timeout_ms", site.get("timeout_ms", 30000))
    value = lambda text: interpolate(text, profile, credentials)
    if action == "manual":
        while True:
            answer = await prompt(step["message"])
            if answer == "done":
                return
            if answer == "blocked":
                raise Blocked()
            if answer == "skip":
                raise Skipped()
    elif action == "goto":
        response = await page.goto(urljoin(site["start_url"], value(step["url"])), wait_until="domcontentloaded", timeout=timeout)
        expected = step.get("expected_status")
        if expected is not None and (response is None or response.status != expected):
            raise WorkflowFailure("Unexpected navigation status")
        if expected is None and response and response.status >= 400:
            raise WorkflowFailure("Navigation HTTP error")
    elif action == "expect_url":
        # Unlike to_have_url(str), wait_for_url accepts documented glob patterns.
        await page.wait_for_url(value(step["url"]), wait_until="domcontentloaded", timeout=timeout)
    else:
        target = locator(page, step["locator"])
        if action == "fill":
            content = credentials[step["credential"]] if "credential" in step else value(step["value"])
            await target.fill(content, timeout=timeout)
        elif action == "click":
            await target.click(timeout=timeout)
        elif action == "select":
            await target.select_option(value(step["value"]), timeout=timeout)
        elif action == "press":
            await target.press(step["key"], timeout=timeout)
        elif action == "expect_visible":
            await expect(target).to_be_visible(timeout=timeout)
        elif action == "expect_text":
            await expect(target).to_have_text(value(step["text"]), timeout=timeout)
        else:
            raise WorkflowFailure("Unsupported action")


async def close_context(context):
    """Give HAR finalization a bounded grace period, including after cancellation."""
    task = asyncio.create_task(context.close())
    loop = asyncio.get_running_loop()
    deadline = loop.time() + CLOSE_TIMEOUT_SECONDS
    interrupted = False
    while True:
        try:
            await asyncio.wait_for(asyncio.shield(task), max(0, deadline - loop.time()))
            return None, interrupted
        except asyncio.CancelledError:
            interrupted = True
            if task.cancelled():
                return "CancelledError", interrupted
            # Cancellation of this caller must not stop the HAR flush. Retain
            # the original deadline so repeated cancellation cannot hang cleanup.
        except Exception as error:
            task.cancel()
            return type(error).__name__, interrupted


def invalid_capture(reason):
    return {"valid": False, "errors": [reason], "warnings": [], "entry_count": 0,
            "response_body_count": 0, "status_counts": {}}


async def capture_pair(browser, profile, site, root, *, headless, timeout_ms, save_state=False, prompt=None):
    pair_id = profile["id"] + "__" + site["id"]
    har_path = root / "har" / (pair_id + ".har")
    metadata_path = root / "metadata" / (pair_id + ".json")
    steps = site.get("steps", [])
    checks = site.get("checks", [])
    result = {
        "profile_id": profile["id"], "site_id": site["id"], "started_at": now(),
        "workflow_status": "running", "har": str(har_path), "steps_completed": 0,
        "verification": "operator" if any(s["action"] == "manual" for s in checks) else "automatic",
        "recording": {"mode": "full", "content": "embed", "fresh_context": True, "url_filter": None},
    }
    write_json(metadata_path, result)
    context = None
    cancelled = False
    current_action = "setup"
    current_index = None
    credentials = {}
    try:
        manual = any(s["action"] == "manual" for s in steps + checks)
        if manual and (headless or (prompt is None and not sys.stdin.isatty())):
            raise Blocked("Manual workflow requires visible browser and terminal")
        credentials = resolve_credentials(profile, site["id"])
        for step in steps:
            if "credential" in step and step["credential"] not in credentials:
                raise Blocked("Missing configured credential")
        context = await browser.new_context(
            record_har_path=str(har_path), record_har_mode="full", record_har_content="embed",
            accept_downloads=False,
        )
        context.set_default_timeout(timeout_ms)
        context.set_default_navigation_timeout(timeout_ms)
        page = await context.new_page()
        current_action = "initial_navigation"
        response = await page.goto(site["start_url"], wait_until="domcontentloaded")
        if response and response.status >= 400:
            # Preserve this challenge/error page for manual operator handling.
            if not manual:
                raise WorkflowFailure("Initial navigation HTTP error")
        configured_site = dict(site, timeout_ms=site.get("timeout_ms", timeout_ms))
        for current_index, step in enumerate(steps + checks, 1):
            current_action = step["action"]
            await execute_step(page, step, configured_site, profile, credentials, prompt or terminal_prompt)
            result["steps_completed"] += 1
        result["workflow_status"] = "completed_manual" if result["verification"] == "operator" else "completed"
        result["final_origin"] = urlsplit(page.url).scheme + "://" + (urlsplit(page.url).hostname or "")
        if save_state:
            state_path = root / "auth" / (pair_id + ".json")
            write_json(state_path, await context.storage_state(indexed_db=True))
    except asyncio.CancelledError:
        cancelled = True
        result["workflow_status"] = "aborted"
    except (Blocked, MissingCredential):
        result["workflow_status"] = "blocked"
    except Skipped:
        result["workflow_status"] = "skipped"
    except Exception as error:
        result["workflow_status"] = "failed"
        # Exception messages can include credential-filled selectors and URLs.
        result["error"] = {"type": type(error).__name__, "action": current_action, "step": current_index}
    finally:
        result["har_finalized"] = False
        if context is not None:
            close_error, cleanup_cancelled = await close_context(context)
            cancelled = cancelled or cleanup_cancelled
            if close_error:
                result["workflow_status"] = "failed"
                result["close_error"] = close_error
            else:
                result["har_finalized"] = True
        if cancelled:
            result["workflow_status"] = "aborted"
        result["finished_at"] = now()
        result["capture_exists"] = har_path.is_file()
        result["validation"] = invalid_capture("No HAR captured")
        try:
            if result["capture_exists"]:
                har_path.chmod(0o600)
                if result["har_finalized"]:
                    result["validation"] = validate_har(har_path)
                    result["har_sha256"] = hashlib.sha256(har_path.read_bytes()).hexdigest()
                else:
                    result["validation"] = invalid_capture("HAR finalization did not complete")
        except Exception as error:
            result["validation"] = invalid_capture("Could not validate the saved HAR")
            result["validation_error"] = type(error).__name__
        if result["workflow_status"] in SUCCESS and (not result["validation"]["valid"] or result["validation"]["entry_count"] == 0):
            result["workflow_status"] = "failed"
            result["quality_failure"] = "Invalid or empty HAR"
        write_json(metadata_path, result)
        write_json(root / "labels" / (pair_id + ".json"), {
            "profile_id": profile["id"], "site_id": site["id"], "status": "needs_manual_review",
            "known_inputs": [{"field": key, "value": value, "source": "configured input; presence in HAR unverified"} for key, value in credentials.items()],
            "sensitive_occurrences": [], "safe_context": [],
            "note": "Incomplete answer key. Review server-generated tokens and all repeated/encoded occurrences locally.",
        })
    if cancelled:
        raise asyncio.CancelledError()
    return result


async def run_capture(config, output, *, headless=False, profiles=None, sites=None, retry_failed=None, save_state=False, channel=None):
    pairs = select_pairs(config, profiles, sites)
    if retry_failed:
        previous = json.loads((Path(retry_failed) / "manifest.json").read_text())
        if previous["config_sha256"] != fingerprint(config):
            raise ValueError("Configuration changed; create a new run instead of retrying")
        successful = {(r["profile_id"], r["site_id"]) for r in previous["cases"] if r["workflow_status"] in SUCCESS}
        pairs = [(p, s) for p, s in pairs if (p["id"], s["id"]) not in successful]
    if not pairs:
        raise ValueError("No enabled profile/site pairs selected")
    run_id = datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%SZ_") + uuid.uuid4().hex[:8]
    root = Path(output).expanduser().resolve() / run_id
    root.mkdir(mode=0o700, parents=True)
    for folder in ("har", "metadata", "labels", "auth"):
        (root / folder).mkdir(mode=0o700)
    manifest = {
        "run_id": run_id, "started_at": now(), "config_sha256": fingerprint(config),
        "playwright_version": version("playwright"), "browser_version": None,
        "headless": headless, "status": "running",
        "cases": [{"profile_id": p["id"], "site_id": s["id"], "workflow_status": "not_attempted"} for p, s in pairs],
    }

    def persist():
        for i, (profile, site) in enumerate(pairs):
            file = root / "metadata" / (profile["id"] + "__" + site["id"] + ".json")
            if file.exists():
                manifest["cases"][i] = json.loads(file.read_text())
        manifest["counts"] = {state: sum(c["workflow_status"] == state for c in manifest["cases"]) for state in ("completed", "completed_manual", "failed", "blocked", "skipped", "aborted", "running", "not_attempted")}
        write_json(root / "manifest.json", manifest)

    persist()
    print("Run folder:", root, flush=True)
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=headless, channel=channel)
            manifest["browser_version"] = browser.version
            try:
                for index, (profile, site) in enumerate(pairs, 1):
                    print(f"[{index}/{len(pairs)}] {profile['id']} / {site['id']}", flush=True)
                    result = await capture_pair(browser, profile, site, root, headless=headless,
                        timeout_ms=config.get("timeout_ms", 30000), save_state=save_state)
                    print(" ", result["workflow_status"], "—", result["validation"]["entry_count"], "requests", flush=True)
                    persist()
            finally:
                await browser.close()
        manifest["status"] = "finished"
    except asyncio.CancelledError:
        manifest["status"] = "aborted"
        raise
    except Exception as error:
        manifest["status"] = "failed"
        manifest["runner_error"] = type(error).__name__
        raise
    finally:
        manifest["finished_at"] = now()
        persist()
    return root, manifest
