"""Offline staging preflight for ScanGrade Live; no network, database or writes.

Usage:
  python deploy/livekit_preflight.py --env deploy/livekit-staging.env.example
  python deploy/livekit_preflight.py --env /private/staging.env --pilot

Reads only names/values needed for its checks. Never prints credentials.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from urllib.parse import urlsplit

REQUIRED = ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")
FLAGS = ("SCANGRADE_LIVE_ENABLED", "SCANGRADE_LIVE_EXAM_ENABLED")
TRUTHY = {"yes", "on", "1", "true"}
PLACEHOLDERS = ("change-me", "replace-me", "example", "your-", "xxx", "todo")


def parse_env(text: str) -> dict[str, str]:
    """Parse KEY=VALUE from an env file without executing or interpolating code."""
    variables = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if not key.isidentifier():
            continue
        if len(value) >= 2 and value[:1] == value[-1:] and value[0] in "\"'":
            value = value[1:-1]
        variables[key] = value
    return variables


def audit(settings: dict[str, str], *, pilot: bool = False) -> list[str]:
    """Return fail-closed diagnostics suitable for display without secrets."""
    issues = []
    on = {key: settings.get(key, "false").strip().lower() in TRUTHY for key in FLAGS}
    if pilot:
        if not all(on.values()):
            issues.append("Pilot requires both Live feature flags enabled")
    elif any(on.values()):
        issues.append("Non-pilot configuration must leave Live flags OFF")

    if pilot:
        for key in REQUIRED:
            value = settings.get(key, "").strip()
            if not value:
                issues.append(key + " is missing")
            elif any(word in value.lower() for word in PLACEHOLDERS):
                issues.append(key + " appears to be a placeholder")
        url = settings.get("LIVEKIT_URL", "").strip()
        try:
            parsed = urlsplit(url)
            if parsed.scheme != "wss" or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
                issues.append("LIVEKIT_URL must be a bare secure wss:// endpoint")
            if parsed.hostname in ("localhost", "127.0.0.1", "::1"):
                issues.append("Pilot cannot use a localhost LiveKit endpoint")
        except ValueError:
            issues.append("LIVEKIT_URL could not be parsed")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", required=True, type=Path, help="Env file (never printed)")
    parser.add_argument("--pilot", action="store_true", help="Validate pilot staging flags and credentials")
    opts = parser.parse_args()
    try:
        settings = parse_env(opts.env.read_text(encoding="utf-8"))
    except OSError:
        print("FAIL: unable to read requested env file")
        return 2
    problems = audit(settings, pilot=opts.pilot)
    if problems:
        for problem in problems:
            print("FAIL: " + problem)
        return 1
    print("PASS: ScanGrade Live configuration checks (offline, no connectivity validation)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
