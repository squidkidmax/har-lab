# Local HAR Capture Runner

Capture five test identities across the ten websites you selected: YouTube,
Apple, Amazon, eBay, Wikipedia, Reddit, Facebook, X, Netflix, and GitHub.
One fresh browser context and one HAR file are created for each identity/site
pair. The supplied plan contains 50 pairs.

The supplied website adapters are **operator-guided**: the runner opens the site,
you sign in using that test identity and complete the displayed browsing steps,
then type `done`, `blocked`, or `skip` in the terminal. Recording, saving, metadata,
validation, and batch progression are automatic. `done` is an operator assertion;
it is not represented as independently verified login. The runner does not create
website accounts, subscriptions, solve challenges, or send messages for you.

Five local profile IDs are provided. They do not themselves create five accounts
on each website. Use the intended test account for each profile. A fresh context
prevents accidental reuse of another profile's cookies. Existing personal Chrome
profiles are not opened. Google Chrome is installed on the target Mac; the launcher
uses it through Playwright's supported `chrome` channel.

## Quick start on this Mac

Double-click `run.command`, choose a profile number, and follow the terminal prompts.
For a first run, choose profile 1 and use `skip` for any sites you do not want to
capture. Use `blocked` for account, MFA, CAPTCHA, subscription, or access issues.
Keep the browser context open until the runner closes it to finalize the HAR.

If the local Python environment is absent, the launcher creates `.venv` and
installs the pinned packages in `requirements.txt`. It requires Python 3.10+ and
an installed Google Chrome. On another machine, use the setup commands below.

## Terminal setup and commands

Run from this project directory:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m capture_runner plan --config websites.json
.venv/bin/python -m capture_runner run --config websites.json --profile profile_01 --site github --channel chrome
```

To use Playwright's bundled Chromium instead of installed Chrome:

```sh
.venv/bin/python -m playwright install chromium
.venv/bin/python -m capture_runner run --config websites.json
```

To test five automatically logged-in fake accounts against the included local
website, without visiting external websites:

```sh
.venv/bin/python -m capture_runner demo --channel chrome
```

Capture all 50 operator-guided pairs:

```sh
.venv/bin/python -m capture_runner run --config websites.json --channel chrome
```

Use `--output /your/output/folder` to choose a destination. Each run creates a new
timestamped folder. `--profile` and `--site` can be repeated to select several IDs.

## What gets saved

```text
captures/run_<UTC timestamp>_<unique ID>/
  manifest.json                       Run versions, status, case list and counts
  har/profile_01__github.har           Full recording, response bodies embedded
  metadata/profile_01__github.json     Workflow status, HAR hash, validation counts
  labels/profile_01__github.json       Incomplete answer-key worksheet for review
  auth/                               Optional --save-state output
```

HARs contain actual captured data, which can include live credentials. Run folders
use owner-only directory permissions and files use owner-only read/write access.
They are not encrypted. Keep raw files and label worksheets local while reviewing.
Progress messages and normal metadata do not include passwords, URL query strings,
or raw Playwright exception messages. Configured credential values, when supplied,
appear only in the private label worksheet and potentially the raw capture.

Metadata distinguishes:

- `completed`: all configured automated completion checks passed and capture is
  structurally valid and nonempty.
- `completed_manual`: the operator confirmed completion and capture is structurally
  valid and nonempty. This does not prove login or a particular request occurred.
- `failed`: a step, browser close, or capture-quality check failed.
- `blocked`: required manual interaction or credentials were unavailable, or the
  operator reported an access/challenge issue.
- `skipped`: the operator deliberately omitted this pair.
- `aborted` / `not_attempted`: interruption or remaining unstarted work.

Failed cases retain any partial HAR. A valid HAR alone never turns a failed
workflow into a successful one. A completed run can still contain failed cases;
the command exits nonzero in that situation.

The label worksheet is **not automatic ground truth**. It records known configured
inputs but leaves sensitive occurrences and useful context for manual review.
It cannot identify every server-issued token or every encoded occurrence.

## Add automation for a website

Edit its `steps` and `checks` in `websites.json`. Each site has its own selectors.
The generic engine supports `goto`, `fill`, `click`, `select`, `press`, `manual`,
`expect_visible`, `expect_text`, and `expect_url`. URL expectations use Playwright
glob patterns. No arbitrary Python or JavaScript is loaded from the configuration.

Example fragment (selectors must be verified against your chosen website):

```json
{
  "id": "example",
  "enabled": true,
  "start_url": "https://example.test/login",
  "steps": [
    {"action": "fill", "locator": {"label": "Username"}, "credential": "username"},
    {"action": "fill", "locator": {"label": "Password"}, "credential": "password"},
    {"action": "click", "locator": {"role": "button", "name": "Log in"}}
  ],
  "checks": [
    {"action": "expect_visible", "locator": {"role": "heading", "name": "Dashboard"}}
  ]
}
```

Corresponding account entry within one profile:

```json
{
  "id": "profile_01",
  "accounts": {
    "example": {
      "username": {"env": "HAR_PROFILE_01_EXAMPLE_USERNAME"},
      "password": {"env": "HAR_PROFILE_01_EXAMPLE_PASSWORD"}
    }
  }
}
```

Set those environment variables locally before running. For artificial local demo
accounts only, a `{"value": "..."}` credential source is also supported.
`{{profile_id}}` and `{{username}}`-style substitutions are available in step values.
Locators support CSS, exact label, exact text, or role plus accessible name.
Check the application state after asynchronous activity; avoid closing immediately
after clicking while the response you need is still pending.

`--headless` is intended for automated adapters. Manual flows are marked blocked
instead of silently passing. A terminal capable of asyncio input notifications is
required for manual mode; the implementation has been built for macOS/Linux.

## Retry interrupted work

```sh
.venv/bin/python -m capture_runner run --config websites.json --channel chrome --retry-failed captures/run_PREVIOUS
```

Retries write to a new run folder and omit pairs completed in that prior run.
The configuration fingerprint must match. If you change workflows or the plan,
start a new run instead. Retrying may repeat workflow actions; the provided
workflows involve browsing only. Repeated retry runs compare against the one
specified manifest, not an automatically merged history of every previous run.

`--save-state` optionally records each pair's cookies/local storage/IndexedDB for
later inspection. Normal captures intentionally begin fresh and do not restore
that state automatically. This preserves login traffic and profile isolation.

## Validation and limits

The validator uses bundled community HAR 1.2 schemas plus semantic timestamp and
timing checks. See `capture_runner/schemas/PROVENANCE.md` for source/license details.
It supports multiple entries, reports empty captures, and does not mistake historic
wire byte sizes for the size of a rewritten body. Validation is distinct from
workflow completion and secret labeling.

HTTP recording is not proof of complete application observability. WebSocket
messages, browser-internal traffic, cache/service-worker behavior, streamed media,
and bodies that have not completed may require separate investigation. No capture
option is described here as guaranteeing every byte or every secret.

The ten external login flows are operator-guided and have not been signed into or
verified automatically. The local demo provides automated end-to-end evidence for
the shared recording system. The earlier experimental sanitizer is not called by
this runner; original HARs remain available for independent evaluation.

## Tests

```sh
.venv/bin/python -m unittest discover -s tests -v
HAR_RUN_BROWSER_TESTS=1 .venv/bin/python -m unittest discover -s tests -p test_browser_capture.py -v
```

The browser integration tests use installed Chrome and loopback-only test servers.

References:
- https://playwright.dev/python/docs/api/class-browser#browser-new-context
- https://playwright.dev/python/docs/auth
- https://github.com/ahmadnassri/har-spec/blob/master/versions/1.2.md
