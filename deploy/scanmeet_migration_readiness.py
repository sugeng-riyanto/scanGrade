"""ScanMeet migration readiness check: offline files or read-only live DB.

Operator uses --live AFTER applying 073, 074, 075 and BEFORE retrying deploy.
Never modifies database, migration ledger, backups, deployment or feature flags.

The live half runs on the session `apply_migration.connect_readonly()` opens,
which is ``autocommit=True``. Each statement is therefore its own transaction:
a query that raises leaves no aborted transaction behind, so the probe needs no
savepoint to recover from. Asking for one is not merely redundant there —
PostgreSQL refuses it (25P01, "can only be used in transaction blocks"), and the
rollback written in a ``finally`` then raised *out* of the probe, so ``--live``
finished in a traceback instead of a verdict. A unit stub that returned quietly
for those statements is what hid it; the guard is now a stub that answers the
way the server does.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REQUIRED = {
    "073": "073_scan_live_cleanup_reconcile.sql",
    "074": "074_scan_live_cleanup_debt_survives.sql",
    "075": "075_scanmeet_school_entitlements.sql",
}


def files_ready(repo: Path) -> list[str]:
    missing = [f"missing SQL {name}" for name in REQUIRED.values()
               if not (repo / "supabase" / "migrations" / name).is_file()]
    return missing


def database_findings(cur) -> list[str]:
    """Check observable postconditions, including 074 FK behaviour and RLS.

    Every query is independent: the caller's session is read-only *and*
    autocommit, so one that raises cannot poison the next.
    """
    queries = {
        "073 cleanup jobs table": "SELECT to_regclass('public.scan_live_cleanup_jobs') IS NOT NULL",
        "075 school entitlements table": "SELECT to_regclass('public.scanmeet_school_entitlements') IS NOT NULL",
        "073 sessions media_state": """SELECT EXISTS (SELECT 1 FROM information_schema.columns
            WHERE table_schema='public' AND table_name='scan_live_sessions'
              AND column_name='media_state')""",
        "074 cleanup orphaned_at": """SELECT EXISTS (SELECT 1 FROM information_schema.columns
            WHERE table_schema='public' AND table_name='scan_live_cleanup_jobs'
              AND column_name='orphaned_at')""",
        "074 sessions missing_since": """SELECT EXISTS (SELECT 1 FROM information_schema.columns
            WHERE table_schema='public' AND table_name='scan_live_sessions'
              AND column_name='missing_since')""",
        "074 cleanup FK SET NULL": """SELECT count(*)=2 FROM pg_constraint
            WHERE conrelid='public.scan_live_cleanup_jobs'::regclass
              AND conname IN ('scan_live_cleanup_jobs_session_id_fkey',
                              'scan_live_cleanup_jobs_school_id_fkey')
              AND confdeltype='n'""",
        "075 enabled DEFAULT false": """SELECT column_default='false' FROM information_schema.columns
            WHERE table_schema='public' AND table_name='scanmeet_school_entitlements'
              AND column_name='enabled'""",
        "075 entitlement RLS": """SELECT relrowsecurity FROM pg_class
            WHERE oid='public.scanmeet_school_entitlements'::regclass""",
    }
    findings = []
    for label, query in queries.items():
        try:
            cur.execute(query)
            row = cur.fetchone()
            if not row or row[0] is not True:
                findings.append(label)
        except Exception:
            findings.append(label + " (query unavailable)")
    return findings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=REPO)
    parser.add_argument("--live", action="store_true", help="read-only live DB postconditions; requires DIRECT_URL")
    args = parser.parse_args(argv)
    repo = args.repo.resolve()
    missing = files_ready(repo)
    if missing:
        for item in missing:
            print("HOLD:", item)
        return 2
    if not args.live:
        print("PASS: SQL files 073, 074, 075 present (OFFLINE ONLY; not database readiness)")
        return 0

    # Reuse the repository's target-project matching and read-only transaction.
    from deploy import apply_migration
    url = apply_migration.load_migration_url(repo)
    apply_migration.check_target(url, repo)
    conn = apply_migration.connect_readonly(url)
    try:
        with conn.cursor() as cur:
            findings = database_findings(cur)
    finally:
        conn.close()
    if findings:
        for finding in findings:
            print("HOLD:", finding)
        return 6
    print("PASS: 073/074/075 schema postconditions confirmed on target (READ ONLY)")
    print("NOTE: backup restore, active exams, feature flags and smoke tests are separate gates")
    return 0


if __name__ == "__main__":
    sys.exit(main())
