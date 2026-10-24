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


def validate_file(path: Path, checkout: Path) -> list[str]:
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
    try:
        mode = path.stat().st_mode
        if not stat.S_ISREG(mode):
            problems.append("Private staging env is not a regular file")
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            problems.append("Private staging env must have permissions 0600")
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
    print("PASS: isolated ScanMeet staging configuration (offline)")
    if args.probe_livekit:
        try:
            import asyncio
            from deploy.livekit_connectivity import probe
            asyncio.run(probe(settings))
        except Exception:
            print("FAIL: LiveKit read-only connectivity/authentication")
            return 1
        print("PASS: LiveKit RoomService authentication (read-only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
