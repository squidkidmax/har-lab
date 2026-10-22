# HAR Lab

A toolkit for capturing, validating, generating, and replaying HTTP Archive (HAR) files, with labeled synthetic datasets for testing sensitive-data detection and sanitization.

HAR files record HTTP requests and responses for debugging and analysis. HAR Lab brings together three independent command-line tools: a browser capture runner, a synthetic fixture generator, and API traffic generation and replay utilities. Automatic secret detection and redaction are not implemented in this repository.

## What's included

| Tool | Purpose | Requirements |
| --- | --- | --- |
| [Browser capture runner](HAR-capture%20runner/README.md) | Record isolated browser sessions, validate HAR structure, and save run metadata and review worksheets. | Python 3.10+, Playwright, and Chromium or Google Chrome |
| [Synthetic fixture generator](HAR-synthetic%20file%20generator/generate_har_dataset.py) | Generate 30 controlled HAR examples with dummy secrets, clean cases, and a separate answer file. | Python 3; standard library only |
| [HAR utilities](har-utils-dev/README.md) | Map SQL schemas to API requests, generate synthetic traffic, and replay HARs with performance metrics. | Node.js 18+ and npm |

## Get started

```sh
git clone https://github.com/squidkidmax/har-lab.git
cd har-lab
```

Choose the tool you need below. Each section starts from the repository root; the commands use macOS/Linux shell paths.

### Generate synthetic test fixtures

This is the quickest way to explore the project. It requires no browser, network access, or third-party Python packages.

```sh
python3 "HAR-synthetic file generator/generate_har_dataset.py" \
  --output-dir "HAR-synthetic file generator/output"
```

The command creates 30 `.har` files and `ground_truth.json` in the selected output directory. Always pass `--output-dir`: the script's current default is an absolute path from the original development machine.

The fixtures cover authorization headers, cookies, query strings, form data, nested JSON, encoded response bodies, JWTs, XML, HTML, and clean content that should remain intact. Their dummy secret values are deterministic.

Use the generated `ground_truth.json` to check which values a sanitizer should remove and which debugging information it should preserve. Keep that answer file separate from the HAR files you feed into a sanitizer. These fixtures are development examples, not a complete benchmark or an automatic scoring system.

### Capture browser traffic

Install the capture runner's dependencies and Playwright's Chromium browser:

```sh
cd "HAR-capture runner"
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
```

Try the local demo first. It records five fake accounts against an included local website without visiting external websites:

```sh
.venv/bin/python -m capture_runner demo
```

Add `--headed` to watch the demo in a browser window. To use an installed Google Chrome instead of downloading Chromium, skip the browser installation command and add `--channel chrome` to `demo` or `run`.

Preview the supplied capture plan, then record one profile on GitHub:

```sh
.venv/bin/python -m capture_runner plan --config websites.json
.venv/bin/python -m capture_runner run --config websites.json \
  --profile profile_01 --site github
```

The supplied plan has five profile IDs and ten websites, for 50 possible captures. For external websites, you sign in with your intended test account, follow the browsing steps, and enter `done`, `blocked`, or `skip` in the terminal. Profile IDs do not create website accounts.

Each profile/site pair uses a fresh browser context. The runner saves:

```text
captures/run_<timestamp>_<id>/
  manifest.json   Run status and case list
  har/            Browser recordings with embedded response bodies
  metadata/       Capture status, hashes, and validation results
  labels/         Worksheets requiring manual review
  auth/           Authentication state, only with --save-state
```

Demo output goes to `demo-captures/`. To validate a saved HAR, replace the example path below with an actual capture path:

```sh
.venv/bin/python -m capture_runner validate "captures/run_example/har/profile_01__github.har"
```

Validation checks HAR 1.2 structure and additional timestamp and timing rules. It does not check whether a file is free of secrets. An operator-confirmed capture is recorded as `completed_manual`; it is not independent proof that login or a particular action succeeded. Capture label worksheets also require review and are distinct from the synthetic generator's answer file.

See the [capture runner guide](HAR-capture%20runner/README.md) for custom workflows, retrying failed runs, credential configuration, and capture limitations.

### Generate and replay API traffic

Run the Node.js tools from their own directory:

```sh
cd har-utils-dev
npm ci
node har-utils.js
```

Choose a mode from the interactive menu:

| Mode | Action |
| --- | --- |
| `C` | Build API/entity mappings from SQL schemas and recorded HAR examples. |
| `G` | Generate randomized HAR requests for create, update, and get operations. |
| `R` | Replay HAR files concurrently and report latency, response statuses, and per-URL metrics. |
| `X` | Exit. |

Replay sends real requests to the configured endpoints, including requests that can create or update data. Use a test environment you control. The replay picker reads `.har` files from the current directory; keep local recordings and generated traffic files out of Git.

The package file includes Volta pins for Node.js 20.12.0 and npm 10.5.0. See the [HAR utilities guide](har-utils-dev/README.md) for mapping, generation, authentication, and performance reporting details.

## Handling captures and credentials

Real HAR captures can include passwords, authorization tokens, cookies, personal data, and response bodies. The capture runner preserves original traffic; it does not sanitize or encrypt recordings.

The repository's ignore rules exclude the default capture folders, demo captures, generated synthetic output, `.env` files, token files, Python virtual environments, and Node dependencies. Keep custom output paths, local configuration containing credentials, and exported authentication state outside Git as well. Ignore rules do not remove files that are already tracked.

The synthetic fixtures use artificial values. For comparisons or sanitizer experiments, regenerate their output locally instead of committing browser recordings.

## Capture runner tests

After setting up the capture runner, run these commands from the repository root:

```sh
cd "HAR-capture runner"
.venv/bin/python -m unittest discover -s tests -v
```

Browser integration tests use installed Google Chrome and local test servers. Enable them with:

```sh
HAR_RUN_BROWSER_TESTS=1 .venv/bin/python -m unittest discover \
  -s tests -p test_browser_capture.py -v
```

## Licensing and attribution

There is no repository-wide license file. Component notices are preserved in their original locations:

- The [HAR utilities README](har-utils-dev/README.md#license) identifies that component as proprietary, for internal use only, attributed to Clear C2, Inc. and Allen Swope.
- The bundled HAR schemas retain their [ISC license](HAR-capture%20runner/capture_runner/schemas/LICENSE) and [provenance notes](HAR-capture%20runner/capture_runner/schemas/PROVENANCE.md).

This README does not change those notices or grant additional permissions.
