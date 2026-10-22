"""CLI: initialize, plan, capture, validate, and run a local demo."""
import argparse
import asyncio
import json
import os
from pathlib import Path

from .config import ConfigError, load_config, select_pairs
from .runner import SUCCESS, run_capture, write_json
from .validation import validate_har


def template():
    return {
        "timeout_ms": 30000,
        "profiles": [{"id": f"profile_{i:02d}", "accounts": {}} for i in range(1, 6)],
        "sites": [{
            "id": f"site_{i:02d}", "enabled": False, "start_url": "", "steps": [],
            "checks": [{"action": "manual", "message": "Log in with this test profile, perform your chosen workflow, and verify completion in the browser. Type done only after completion."}],
        } for i in range(1, 11)],
    }


def demo_config(base_url, profile_count=5):
    return {
        "timeout_ms": 10000,
        "profiles": [{"id": f"profile_{i:02d}", "accounts": {"demo": {
            "username": {"value": f"profile_{i:02d}"}, "password": {"value": f"DemoPass-{i:02d}!"},
        }}} for i in range(1, profile_count + 1)],
        "sites": [{"id": "demo", "enabled": True, "start_url": base_url + "/login", "steps": [
            {"action": "fill", "locator": {"label": "Username"}, "credential": "username"},
            {"action": "fill", "locator": {"label": "Password"}, "credential": "password"},
            {"action": "click", "locator": {"role": "button", "name": "Log in"}},
            {"action": "expect_visible", "locator": {"role": "heading", "name": "Dashboard"}},
            {"action": "expect_visible", "locator": {"css": "#profile-ready[data-loaded='true']"}},
            {"action": "fill", "locator": {"label": "Search"}, "value": "har testing {{profile_id}}"},
            {"action": "click", "locator": {"role": "button", "name": "Search"}},
        ], "checks": [
            {"action": "expect_visible", "locator": {"role": "heading", "name": "Search results"}},
            {"action": "expect_url", "url": "**/search?**"},
        ]}],
    }


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Capture browser workflows into local HAR files.")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="Create five profiles and ten disabled website slots")
    init.add_argument("--config", type=Path, default=Path("config.json"))
    for name in ("plan", "run"):
        command = sub.add_parser(name)
        command.add_argument("--config", type=Path, default=Path("config.json"))
        command.add_argument("--profile", action="append")
        command.add_argument("--site", action="append")
        if name == "run":
            command.add_argument("--output", type=Path, default=Path("captures"))
            command.add_argument("--headless", action="store_true")
            command.add_argument("--channel", choices=["chrome", "msedge"])
            command.add_argument("--retry-failed", type=Path)
            command.add_argument("--save-state", action="store_true")
    validate = sub.add_parser("validate")
    validate.add_argument("har", type=Path)
    demo = sub.add_parser("demo", help="Test all five profiles against a local fake website")
    demo.add_argument("--output", type=Path, default=Path("demo-captures"))
    demo.add_argument("--headed", action="store_true")
    demo.add_argument("--channel", choices=["chrome", "msedge"])
    args = parser.parse_args()
    try:
        if args.command == "init":
            if args.config.exists():
                raise ValueError("Config already exists; choose a new path")
            write_json(args.config, template())
            print("Created", args.config, "— enable chosen sites before running.")
            return 0
        if args.command == "validate":
            result = validate_har(args.har)
            print(json.dumps(result, indent=2))
            return 0 if result["valid"] and result["entry_count"] else 1
        if args.command == "demo":
            from .demo import demo_server
            with demo_server() as base_url:
                root, report = asyncio.run(run_capture(demo_config(base_url), args.output,
                    headless=not args.headed, channel=args.channel))
        else:
            config = load_config(args.config)
            if args.command == "plan":
                pairs = select_pairs(config, args.profile, args.site)
                print(f"{len(pairs)} captures planned")
                for profile, site in pairs:
                    mode = "manual" if any(s["action"] == "manual" for s in site.get("steps", []) + site.get("checks", [])) else "automated"
                    print(profile["id"], site["id"], mode)
                return 0
            root, report = asyncio.run(run_capture(config, args.output, headless=args.headless,
                profiles=args.profile, sites=args.site, retry_failed=args.retry_failed,
                save_state=args.save_state, channel=args.channel))
        print("Manifest:", root / "manifest.json")
        print(json.dumps(report["counts"]))
        return 0 if all(c["workflow_status"] in SUCCESS for c in report["cases"]) else 1
    except KeyboardInterrupt:
        print("Capture interrupted. See the run folder for saved partial results.")
        return 130
    except ConfigError as error:
        print("Could not run:", str(error))
        return 2
    except (ValueError, OSError) as error:
        print("Could not run:", type(error).__name__, "— check configuration, selected pairs, and output paths.")
        return 2
    except Exception as error:
        print("Runner failed:", type(error).__name__, "— check dependencies/browser installation and the run manifest.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
