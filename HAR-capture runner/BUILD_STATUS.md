# Build and verification

Implemented on 2026-09-12 for macOS with installed Google Chrome.

The installed Desktop copy passed all 51 tests, including the real Chrome
integration tests, on 2026-09-12. Verification command:
`HAR_RUN_BROWSER_TESTS=1 .venv/bin/python -m unittest discover -s tests -v`.

Configured: 5 profile IDs × the user's 10 selected websites = 50 operator-guided
capture pairs. The source includes declarative automated steps and completion
checks, demonstrated against an included local website with five fake accounts.

Tests cover:
- Five authenticated real-browser captures, with distinct session cookies and
  the correct profile response in each HAR.
- Failed login retaining a partial HAR; later profiles continuing successfully.
- Retry selecting the failed pair and preserving prior output.
- Manual mode blocked when headless, cancellation during browser close, close
  timeouts, validation failures, and metadata that omits credential values.
- Config validation, unsafe/colliding IDs, credential resolution, and safe errors.
- HAR structure, invalid timestamps, negative durations, multiple requests,
  Base64 response bodies, schema-source isolation, and timing checks.

The external websites were not signed into during development. Their supplied
adapters require you to perform login and browsing, then confirm completion.
They are recorded as `completed_manual`, not automatically verified login.

This is a capture and validation tool. It does not claim to discover all secrets
or sanitize captures. The generated label files explicitly require manual review.
