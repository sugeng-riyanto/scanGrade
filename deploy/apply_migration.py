#!/usr/bin/env python3
"""Run a migration for real, then roll it back — before anyone trusts it.

Why this exists
---------------
Migrations here were pasted into the Supabase SQL editor, and that is how
`024_fix_pengumuman_school_id_type.sql` nearly cost a table. Its first draft cast
an `integer` holding `1` to `uuid` and aborts with
``invalid input syntax for type uuid: "1"`` — *after* it had dropped the foreign
key. Its second draft was no better, and the failure was subtler:

    psycopg2.errors.FeatureNotSupported: cannot use subquery in transform expression

Postgres forbids a subquery inside ``ALTER COLUMN ... TYPE ... USING``. Pasting it
by hand means discovering that in the middle of an edit to production, with the
foreign key already gone.

psycopg2 gives what PostgREST cannot: **a real transaction**. So the file can be
executed for real — actual DDL, actual planner, actual constraints — and then
rolled back with nothing persisted. The migration is proven before it is applied.

The dry run is never optional
-----------------------------
``--commit`` performs the dry run first, in the same invocation, and only applies
if it passed. There is no flag that skips the trial, so there is nothing to
forget under time pressure. The rollback is then *verified*, not assumed: the
schema is fingerprinted before and re-read on a fresh connection after, and a
mismatch is an error, not a warning.

Usage
-----
    python deploy/apply_migration.py supabase/migrations/025_x.sql
    python deploy/apply_migration.py supabase/migrations/025_x.sql --commit
    python deploy/apply_migration.py --status

Safety rails
------------
* **The target is checked.** The project ref in ``DIRECT_URL`` must match the one
  in ``SUPABASE_URL``. DDL against the wrong project is not recoverable by
  re-running this tool, so it is refused rather than confirmed.
* **A file that manages its own transactions is refused.** If the SQL commits by
  itself, the transaction is no longer ours and the rollback proves nothing — and
  a dry run that quietly applied changes is the worst outcome this tool could
  produce. The connection's transaction status is read *after* execution to
  catch this, which is reliable where pattern-matching for ``COMMIT`` is not
  (``DO $$ BEGIN ... END $$`` is not a transaction boundary).
* **Statements that cannot run in a transaction are refused** (``CREATE INDEX
  CONCURRENTLY``, ``VACUUM``, ``ALTER SYSTEM``, ...), with instructions to split
  them out. A dry run of those would be a lie.
* **``--commit`` takes a recovery point first**, using ``db_snapshot.py`` — the
  same gate the deploy applies to a release that ships SQL. If the snapshot
  cannot be taken, the migration is not applied.

Exit codes
----------
  0  dry run passed and was rolled back, or the migration is committed
  1  the SQL failed, or the rollback could not be verified
  2  nothing to do — bad arguments, no file, no usable credential
  3  refused: the file manages its own transactions
  4  refused: the file needs statements that cannot run in a transaction
  5  refused: the recovery point could not be taken
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

# Sibling import, so `.env` parsing has exactly one implementation. `db_snapshot`
# is import-safe (its CLI is under `if __name__`).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import db_snapshot  # noqa: E402

import psycopg2  # noqa: E402
from psycopg2.extensions import TRANSACTION_STATUS_INTRANS  # noqa: E402

DEFAULT_LEDGER = "/var/lib/scangrade-migrations"
NOT_IN_A_TRANSACTION = "25001"          # cannot run inside a transaction block
PLACEHOLDER = "[YOUR-PASSWORD]"


# ── credentials and target ───────────────────────────────────────────────────

def load_migration_url(repo: Path) -> str:
    """``DIRECT_URL`` — the only credential this tool needs, and only it needs.

    ``db_snapshot.py`` is deliberately built to need no password. This tool is the
    opposite: reading the SQL for the *planner's* opinion requires a real session,
    and that is what the session-mode pooler URL is for.
    """
    url = (os.environ.get("DIRECT_URL") or os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        env = db_snapshot.read_env_file(repo / ".env")
        url = (env.get("DIRECT_URL") or env.get("DATABASE_URL") or "").strip()

    if not url:
        raise SystemExit("no DIRECT_URL / DATABASE_URL in the environment or "
                         f"{repo / '.env'} — nothing to connect to")
    if PLACEHOLDER in url:
        raise SystemExit(
            "DIRECT_URL still contains the dashboard placeholder "
            f"`{PLACEHOLDER}` — put the real database password in {repo / '.env'} "
            "(Project Settings → Database). Nothing was run.")
    if "pooler.supabase.com" not in url:
        # The direct host (db.<ref>.supabase.co) is IPv6-only and unreachable from
        # here; the session-mode pooler on :5432 is what migrations should use.
        print("!! warning: DIRECT_URL does not point at the pooler — if this times "
              "out, that is why")
    return url


# The pooler username is `postgres.<ref>` and the password follows it, so the ref
# is terminated by `:` (or `@` when the password is empty) — not by `@`. Matching
# on `@` there is how the first version of this refused every real URL.
REF_IN_POOLER = re.compile(r"postgres\.([a-z0-9]{20})(?=[:@/])")
REF_IN_DIRECT = re.compile(r"db\.([a-z0-9]{20})\.supabase\.co")
# The API URL is the same ref in a different outfit: https://<ref>.supabase.co
REF_IN_API = re.compile(r"//([a-z0-9]{20})\.supabase\.(?:co|in)\b")


def project_ref(url: str) -> str | None:
    for pattern in (REF_IN_POOLER, REF_IN_DIRECT, REF_IN_API):
        match = pattern.search(url)
        if match:
            return match.group(1)
    return None


def check_target(url: str, repo: Path) -> str:
    """Refuse to run DDL against a project the app is not pointed at.

    A migration applied to the wrong project is not something this tool can undo:
    the dry run would pass, the apply would succeed, and it would succeed on the
    wrong database.
    """
    ref = project_ref(url)
    supabase_url, _ = db_snapshot.load_credentials(repo)
    expected = project_ref(supabase_url or "")

    if not ref:
        raise SystemExit(
            "could not read a project ref out of DIRECT_URL, so the target cannot "
            "be confirmed. Refusing to run migrations against an unidentified "
            "database.")
    if not expected:
        raise SystemExit(
            "could not read a project ref out of SUPABASE_URL, so there is nothing "
            "to check DIRECT_URL against. Refusing to run.")
    if ref != expected:
        raise SystemExit(
            f"DIRECT_URL points at project {ref} but the app is configured for "
            f"{expected}. Refusing: a migration applied to the wrong project "
            "cannot be undone by re-running this.")
    return ref


# ── schema fingerprints ──────────────────────────────────────────────────────

def schema_snapshot(cur) -> dict[str, str]:
    """Every schema object a migration can plausibly touch, as {name: definition}.

    Columns, indexes and constraints cover the usual migration; policies and
    functions are here because this app relies on row-level security and keeps
    logic in the database, so a migration that changes either would otherwise
    look like it changed nothing at all.
    """
    items: dict[str, str] = {}

    cur.execute("""
        SELECT 'table ' || tablename, 'exists'
          FROM pg_tables WHERE schemaname = 'public'
    """)
    items.update(cur.fetchall())

    cur.execute("""
        SELECT 'column ' || table_name || '.' || column_name,
               data_type
                 || ' null=' || is_nullable
                 || ' default=' || coalesce(column_default, '-')
          FROM information_schema.columns
         WHERE table_schema = 'public'
    """)
    items.update(cur.fetchall())

    cur.execute("""
        SELECT 'index ' || indexname, indexdef
          FROM pg_indexes WHERE schemaname = 'public'
    """)
    items.update(cur.fetchall())

    cur.execute("""
        SELECT 'constraint ' || c.conname, pg_get_constraintdef(c.oid)
          FROM pg_constraint c
          JOIN pg_namespace n ON n.oid = c.connamespace
         WHERE n.nspname = 'public'
    """)
    items.update(cur.fetchall())

    cur.execute("""
        SELECT 'policy ' || tablename || '.' || policyname, cmd || ' ' || coalesce(qual, '')
          FROM pg_policies WHERE schemaname = 'public'
    """)
    items.update(cur.fetchall())

    cur.execute("""
        SELECT 'function ' || p.proname, pg_get_function_identity_arguments(p.oid)
          FROM pg_proc p
          JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public'
    """)
    items.update(cur.fetchall())

    return items


def count_changes(delta: list[str]) -> int:
    """How many objects changed — the `+`/`-`/`~` lines, not their detail lines."""
    return sum(1 for line in delta if re.match(r"^  [+~-] ", line))


def schema_diff(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Human-readable schema changes, ignoring anything only in `after`'s order."""
    lines: list[str] = []
    for name in sorted(set(before) | set(after)):
        old, new = before.get(name), after.get(name)
        if old is None:
            lines.append(f"  + {name}")
        elif new is None:
            lines.append(f"  - {name}")
        elif old != new:
            lines.append(f"  ~ {name}")
            lines.append(f"      was: {old}")
            lines.append(f"      now: {new}")
    return lines


# ── executing ────────────────────────────────────────────────────────────────

def execute_migration(conn, sql: str, label: str) -> None:
    """Execute the file and prove the transaction is still ours afterwards."""
    conn.notices = []
    cur = conn.cursor()
    cur.execute(sql)
    cur.close()

    # Labelled, because the re-run of an `IF NOT EXISTS` migration emits
    # "already exists, skipping" — which reads like something was left behind if
    # it is not obvious which pass said it.
    for notice in conn.notices:
        print(f"   notice ({label}): {notice.strip()}")

    if conn.info.transaction_status != TRANSACTION_STATUS_INTRANS:
        raise TransactionEscaped(
            f"{label} left the transaction: it contains COMMIT, BEGIN or ROLLBACK")


class TransactionEscaped(RuntimeError):
    """The migration ended the transaction itself, so a rollback proves nothing."""


def is_transaction_refusal(exc: Exception) -> bool:
    code = getattr(exc, "pgcode", None)
    text = str(exc)
    if code == NOT_IN_A_TRANSACTION:
        return True
    return "cannot run inside a transaction block" in text


def trial(conn, sql: str, require_idempotent: bool) -> None:
    """Execute, then re-execute inside a savepoint. Raises on either failure.

    The second pass is inside a SAVEPOINT so that a non-idempotent file — which is
    ordinary, not a defect — leaves the transaction usable and the first pass
    still reportable, instead of poisoning it into INERROR.
    """
    execute_migration(conn, sql, "first pass")

    cur = conn.cursor()
    cur.execute("SAVEPOINT sg_rerun")
    try:
        execute_migration(conn, sql, "re-run")
    except TransactionEscaped:
        raise
    except psycopg2.Error as exc:
        cur.execute("ROLLBACK TO SAVEPOINT sg_rerun")
        verdict = ("REFUSING: re-running this file must be safe when "
                   "--require-idempotent is set" if require_idempotent else
                   "not idempotent — applying it a second time raises")
        print(f"   re-run: {verdict}: {str(exc).strip().splitlines()[0][:110]}")
        if require_idempotent:
            raise SystemExit(3)
    else:
        cur.execute("RELEASE SAVEPOINT sg_rerun")
        print("   re-run: clean — applying this file twice is a no-op")
    finally:
        cur.close()


# ── the two phases ───────────────────────────────────────────────────────────

def connect(url: str):
    conn = psycopg2.connect(url)
    conn.autocommit = False
    return conn


def describe_file(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    return raw.decode("utf-8-sig"), hashlib.sha256(raw).hexdigest()


def dry_run(url: str, sql: str, require_idempotent: bool) -> list[str]:
    """Trial the migration and roll it back. Returns the schema delta it would make.

    Raises SystemExit(1) if the SQL fails or the rollback cannot be verified,
    3 if the file manages its own transactions, 4 if it needs statements that
    cannot run in a transaction.
    """
    before_conn = connect(url)
    try:
        with before_conn.cursor() as cur:
            before = schema_snapshot(cur)
    finally:
        before_conn.rollback()
        before_conn.close()

    print(f"   schema before: {len(before)} object(s)")
    print("   executing inside a transaction (this will be rolled back)...")

    conn = connect(url)
    try:
        try:
            trial(conn, sql, require_idempotent)
        except TransactionEscaped as exc:
            conn.rollback()
            print()
            print(f"   REFUSED: {exc}")
            print("   A rollback could no longer undo it, so this dry run proves "
                  "nothing — and may already have changed something.")
            print("   Take the file's transaction control out and let this tool own it.")
            raise SystemExit(3)
        except psycopg2.Error as exc:
            conn.rollback()
            if is_transaction_refusal(exc):
                print()
                print(f"   REFUSED: {str(exc).strip().splitlines()[0]}")
                print("   That statement cannot run inside a transaction, so it cannot "
                      "be trialled first.")
                print("   Split it into its own file and run it deliberately, or rewrite "
                      "it to be transactional.")
                raise SystemExit(4)
            print()
            print(f"   FAILED: {type(exc).__name__}: {str(exc).strip()}")
            print("   The transaction was rolled back; nothing was applied.")
            raise SystemExit(1)

        with conn.cursor() as cur:
            mid = schema_snapshot(cur)
    finally:
        conn.rollback()
        conn.close()

    delta = schema_diff(before, mid)
    print()
    print(f"   this migration would change {count_changes(delta)} schema object(s):")
    print("\n".join(delta) if delta else "     (no schema change detected)")

    # The rollback is verified on a fresh connection, because the closed one is
    # gone and its view of the schema is not evidence about what persisted.
    after_conn = connect(url)
    try:
        with after_conn.cursor() as cur:
            after = schema_snapshot(cur)
    finally:
        after_conn.rollback()
        after_conn.close()

    leaked = schema_diff(before, after)
    if leaked:
        print()
        print("   ROLLBACK NOT VERIFIED — the schema differs after rollback:")
        print("\n".join(leaked))
        raise SystemExit(1)

    print()
    print(f"   ROLLED BACK. Schema verified identical to before ({len(after)} objects).")
    return delta


def take_recovery_point(url: str, repo: Path, out_dir: Path, keep: int, label: str):
    """Snapshot the data before applying, the same gate the deploy uses."""
    supabase_url, key = db_snapshot.load_credentials(repo)
    if not supabase_url or not key:
        print("   REFUSING: no SUPABASE_URL / SUPABASE_SERVICE_KEY, so a recovery "
              "point cannot be taken.")
        print("   Pass --no-snapshot to accept that the data would not be "
              "recoverable.")
        raise SystemExit(5)
    print(f"   taking a recovery point into {out_dir} (label {label})...")
    try:
        archive = db_snapshot.make_snapshot(supabase_url, key, out_dir, label, keep,
                                            quiet=True)
    except Exception as exc:                                   # noqa: BLE001
        print(f"   REFUSING: could not snapshot ({type(exc).__name__}: {exc})")
        print("   Nothing was applied. Pass --no-snapshot to apply without a "
              "recovery point.")
        raise SystemExit(5)
    print(f"   recovery point: {archive}")
    return archive


def commit_run(url: str, path: Path, sql: str, repo: Path, out_dir: Path, keep: int,
               label: str, snapshot: bool) -> tuple[list[str], str | None]:
    before_conn = connect(url)
    try:
        with before_conn.cursor() as cur:
            before = schema_snapshot(cur)
    finally:
        before_conn.rollback()
        before_conn.close()

    archive = take_recovery_point(url, repo, out_dir, keep, label) if snapshot else None
    if not snapshot:
        print("   no recovery point taken (--no-snapshot)")

    print("   applying for real...")
    conn = connect(url)
    try:
        with conn.cursor() as cur:
            conn.notices = []
            cur.execute(sql)
            for notice in conn.notices:
                print(f"   notice (commit): {notice.strip()}")
        conn.commit()
    except psycopg2.Error as exc:
        conn.rollback()
        print(f"   FAILED: {type(exc).__name__}: {str(exc).strip()}")
        print("   Rolled back; nothing was applied.")
        if archive:
            print(f"   The recovery point is still there if you need it: {archive}")
        raise SystemExit(1)
    finally:
        conn.close()

    after_conn = connect(url)
    try:
        with after_conn.cursor() as cur:
            after = schema_snapshot(cur)
    finally:
        after_conn.rollback()
        after_conn.close()

    print("   COMMITTED.")
    return schema_diff(before, after), str(archive) if archive else None


# ── ledger ───────────────────────────────────────────────────────────────────

def _current_user() -> str:
    """`USER` is empty in some shells (and on Windows); `getpass` knows both worlds."""
    for name in ("USER", "LOGNAME", "USERNAME"):
        if os.environ.get(name):
            return os.environ[name]
    try:
        return getpass.getuser()
    except Exception:                                          # noqa: BLE001
        return "unknown"


def record(ledger: Path, path: Path, digest: str, delta: list[str],
           archive: str | None) -> Path:
    """Note what was applied, so "is migration X in?" stops being a guess.

    A JSON file per migration rather than a table: a ledger table would itself need
    a migration, and the first thing anyone asks about a migration system is which
    migrations it has run.
    """
    ledger.mkdir(parents=True, exist_ok=True)
    entry = {
        "file": path.name,
        "path": str(path),
        "sha256": digest,
        "applied_at": datetime.now(timezone.utc).isoformat(),
        # SUDO_USER first: on the VPS this runs as root, and "root" says nothing
        # about who actually decided to apply it.
        "by": f"{os.environ.get('SUDO_USER') or _current_user()}@{socket.gethostname()}",
        "schema_changes": delta,
        "recovery_point": archive,
    }
    target = ledger / f"{path.stem}.json"
    target.write_text(json.dumps(entry, indent=2) + "\n", encoding="utf-8")
    return target


def status(ledger: Path, migrations: Path) -> int:
    files = sorted(migrations.glob("*.sql"))
    if not files:
        print(f"no .sql files in {migrations}")
        return 0

    print(f"{'file':<58} {'record':<30} sha256")
    print("-" * 100)
    unrecorded = 0
    for path in files:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        entry_path = ledger / f"{path.stem}.json"
        if not entry_path.exists():
            note = "no record"
            unrecorded += 1
        else:
            try:
                entry = json.loads(entry_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                note = "record unreadable"
            else:
                if entry.get("sha256") == digest:
                    note = f"applied {str(entry.get('applied_at', ''))[:10]}"
                else:
                    note = "APPLIED, THEN EDITED"
        print(f"{path.name:<58} {note:<30} {digest[:12]}")
    print()
    print(f"{unrecorded} of {len(files)} have no record.")
    print("No record does not mean 'not applied'. Migrations pasted into the SQL")
    print("editor before this tool existed were never recorded anywhere, so")
    print("`--status` cannot see them; only a schema check can settle those.")
    return 0


# ── cli ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Trial a migration in a rolled-back transaction, then apply it.")
    parser.add_argument("file", nargs="?", help="the .sql file to apply")
    parser.add_argument("--commit", action="store_true",
                        help="apply for real, after the dry run passes")
    parser.add_argument("--repo", default="/opt/scangrade",
                        help="checkout holding .env (default: /opt/scangrade)")
    parser.add_argument("--out", default=db_snapshot.DEFAULT_OUT,
                        help=f"snapshot directory (default: {db_snapshot.DEFAULT_OUT})")
    parser.add_argument("--keep", type=int, default=db_snapshot.DEFAULT_KEEP,
                        help=f"snapshots to retain (default: {db_snapshot.DEFAULT_KEEP})")
    parser.add_argument("--ledger", default=DEFAULT_LEDGER,
                        help=f"where applied migrations are recorded "
                             f"(default: {DEFAULT_LEDGER})")
    parser.add_argument("--no-snapshot", action="store_true",
                        help="with --commit: apply without taking a recovery point")
    parser.add_argument("--require-idempotent", action="store_true",
                        help="fail unless re-running the file is a no-op")
    parser.add_argument("--status", action="store_true",
                        help="report which migrations have a record")
    args = parser.parse_args()

    repo = Path(args.repo)
    ledger = Path(args.ledger)

    if args.status:
        return status(ledger, repo / "supabase" / "migrations")

    if not args.file:
        parser.error("give a .sql file, or --status")

    path = Path(args.file)
    if not path.is_file():
        print(f"no such file: {path}")
        return 2

    sql, digest = describe_file(path)
    url = load_migration_url(repo)
    ref = check_target(url, repo)

    print("=" * 74)
    print(f"{'APPLYING' if args.commit else 'DRY RUN'}  {path.name}")
    print("=" * 74)
    print(f"   file:    {path}  ({len(sql)} chars, sha256 {digest[:12]})")
    print(f"   target:  project {ref}")

    trial_delta = dry_run(url, sql, args.require_idempotent)

    if not args.commit:
        print()
        print("   Nothing was applied. Re-run with --commit to keep it.")
        return 0

    print()
    print("   dry run passed — now applying")
    label = path.stem
    delta, archive = commit_run(url, path, sql, repo, Path(args.out), args.keep,
                                label, snapshot=not args.no_snapshot)

    print()
    print("   schema after:")
    print("\n".join(delta) if delta else "      (no schema change detected)")

    entry = record(ledger, path, digest, delta, archive)
    print(f"   recorded in {entry}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(2)
