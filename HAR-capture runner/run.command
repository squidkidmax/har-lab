#!/bin/zsh
# Double-click in Finder, or run from Terminal. Uses an isolated browser profile.
set -e
cd -- "${0:A:h}"
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
if ! .venv/bin/python -c 'import playwright, jsonschema' >/dev/null 2>&1; then
  .venv/bin/python -m pip install -r requirements.txt
fi
print 'HAR Capture Runner — five profiles, ten configured websites'
print 'Enter a profile number (1–5), or all to capture every profile:'
read profile_choice
case "$profile_choice" in
  1|2|3|4|5)
    profile_args=(--profile "profile_0${profile_choice}")
    ;;
  all)
    profile_args=()
    ;;
  *)
    print 'Choose 1, 2, 3, 4, 5, or all.'
    exit 2
    ;;
esac
print 'Starting Chrome. Complete each website workflow, then return here and type done, blocked, or skip.'
.venv/bin/python -m capture_runner run --config websites.json --output captures --channel chrome "${profile_args[@]}"
