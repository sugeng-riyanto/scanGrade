"""Fail-closed staging boundary for ScanMeet on a shared Biznet Gio VPS.

This command only reads a *private, external* env file. No credentials are ever
printed and no project, database, service, or Git checkout is modified.

    python -m deploy.scanmeet_staging_gate --env /home/USER/.config/scanmeet/staging.env
    python -m deploy.scanmeet_staging_gate --env ... --probe-livekit

Do not run on the production ScanGrade checkout or with its Supabase project.
"""
from __future__ import annotations

import argparse
import os
import stat
from pathlib import Path
from urllib.parse import urlsplit

from deploy.livekit_preflight import audit, parse_env

#: POSIX mode bits exist only where the platform implements them. On Windows
#: `stat().st_mode` reports 0o666 for a file that `chmod(0o600)` was applied to,
#: so requiring 0600 there refused *every* valid staging env — and, worse, the
#: "world-readable" refusal passed without reading anything, because both files
#: look the same. The mode is therefore measured only where it means something,
#: and `main()` says out loud when it was not measured: a pass that claims a
#: permission check nobody made is the defect, not a caveat.
MEASURE_MODES = os.name == "posix"

PRODUCTION_CHECKOUT = Path("/opt/scangrade")
PRODUCTION_SITE = "scan-grade.app"
REQUIRED = ("SUPABASE_URL", "SUPABASE_SERVICE_KEY",
            "SUPABASE_ANON_KEY", "FLASK_SECRET_KEY", "SCANMEET_STAGING_URL")


def validate(settings: dict[str, str], production_ref: str) -> list[str]:
    """Offline validation without reading actual credentials or using the network."""
    problems = audit(settings, pilot=True)
    if settings.get("SCANMEET_STAGING", "").strip().lower() != "true":
        problems.append("SCANMEET_STAGING=true is required for isolated staging")
    for key in REQUIRED:
        value = settings.get(key, "").strip()
        if not value:
            problems.append(f"{key} is required for staging")
        elif key in ("SUPABASE_SERVICE_KEY", "SUPABASE_ANON_KEY", "FLASK_SECRET_KEY"):
            if any(s in value.lower() for s in ("replace-me", "change-me", "your-", "todo")):
                problems.append(f"{key} is a placeholder")
    raw_db = settings.get("SUPABASE_URL", "").strip()
    raw_web = settings.get("SCANMEET_STAGING_URL", "").strip()
    try:
        db = urlsplit(raw_db)
        host = (db.hostname or "").lower()
        if (db.scheme != "https" or not host.endswith(".supabase.co")
                or host == f"{production_ref}.supabase.co"
                or db.username or db.password or db.path not in ("", "/")
                or db.query or db.fragment):
            problems.append("SUPABASE_URL must use an isolated staging Supabase project")
    except ValueError:
        problems.append("SUPABASE_URL is not a valid staging URL")
    try:
        web = urlsplit(raw_web)
        host = (web.hostname or "").lower()
        if (web.scheme != "https" or not host or host in (
                PRODUCTION_SITE, "www." + PRODUCTION_SITE, "localhost", "127.0.0.1")
                or host.endswith((".example", ".invalid", ".test"))
                or web.username or web.password or web.query or web.fragment):
            problems.append("SCANMEET_STAGING_URL must be a real separate HTTPS host")
    except ValueError:
        problems.append("SCANMEET_STAGING_URL is not a valid HTTPS URL")
    return problems


def mode_problem(mode: int, measure: bool | None = None) -> list[str]:
    """What a file's mode says about it — the half that is a pure function.

    Split out so the rule is testable on any platform with a synthetic integer:
    the Windows checkout cannot produce a 0644/0600 pair, and a test that only
    runs where the difference exists would leave the rule unread on the machine
    this repository is developed on.
    """
    measured = MEASURE_MODES if measure is None else measure
    problems = []
    if not stat.S_ISREG(mode):
        problems.append("Private staging env is not a regular file")
    if measured and mode & (stat.S_IRWXG | stat.S_IRWXO):
        problems.append("Private staging env must have permissions 0600")
    return problems


def validate_file(path: Path, checkout: Path, measure: bool | None = None) -> list[str]:
    """Refuse prod checkout, in-repo secrets, symlinks and unsafe file mode."""
    problems = []
    directory = checkout.resolve()
    external = path.resolve()
    if directory == PRODUCTION_CHECKOUT or PRODUCTION_CHECKOUT in directory.parents:
        problems.append("Refusing ScanGrade production checkout")
    if external == directory or directory in external.parents:
        problems.append("Private staging env must be outside the Git checkout")
    if path.is_symlink():
        problems.append("Private staging env must not be a symlink")
    if not path.exists():
        # `stat()` failing covered this case as "not readable", which is the
        # wrong sentence for the state a fresh box is in: there is nothing to
        # read yet. Reported before the mode is measured, because there is none.
        problems.append("Private staging env does not exist (create it with umask "
                        "077 on the staging host)")
        return problems
    try:
        problems.extend(mode_problem(path.stat().st_mode, measure))
    except OSError:
        problems.append("Private staging env is not readable")
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, required=True, help="private external env file")
    parser.add_argument("--probe-livekit", action="store_true",
                        help="also perform a read-only authenticated RoomService call")
    args = parser.parse_args(argv)
    checkout = Path(__file__).resolve().parents[1]
    issues = validate_file(args.env, checkout)
    if issues:
        for issue in issues:
            print("FAIL: " + issue)
        return 1
    try:
        settings = parse_env(args.env.read_text(encoding="utf-8"))
        # Read the canonical ref; never duplicate the production project ID here.
        from deploy.supabase_region import REF
        issues = validate(settings, REF)
    except (OSError, UnicodeError, ValueError):
        print("FAIL: unable to validate private staging environment")
        return 1
    if issues:
        for issue in issues:
            print("FAIL: " + issue)
        return 1
    if not MEASURE_MODES:
        # Said before PASS, because the reader has to know what this run did not
        # check. The staging host is Linux, so the file is made with `umask 077`
        # there; this note only keeps a Windows dry-run from overclaiming.
        print("NOTE: file permissions were NOT measured on this platform "
              "(no POSIX group/other bits); create the env file with umask 077 "
              "on the staging host")
    print("PASS: isolated ScanMeet staging configuration (offline)")
    if args.probe_livekit:
        # The import is separated from the call: this module is standard library
        # only, and the staging suite runs it where `requirements.txt` is not
        # installed, so a failed import here is about the probe -- not about the
        # credentials it would have checked.
        try:
            from deploy.livekit_connectivity import MissingSdk, probe
        except ImportError as exc:
            print(f"FAIL: the LiveKit probe could not be loaded ({exc}) - "
                  "pip install -r requirements-live.txt")
            return 2
        try:
            import asyncio
            asyncio.run(probe(settings))
        except MissingSdk as exc:
            # A venv without requirements-live.txt is not a credential problem,
            # and saying so is the difference between a one-line fix and an hour
            # spent re-reading the LiveKit dashboard.
            print(f"FAIL: {exc}")
            return 2
        except Exception:
            print("FAIL: LiveKit read-only connectivity/authentication")
            return 1
        print("PASS: LiveKit RoomService authentication (read-only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
