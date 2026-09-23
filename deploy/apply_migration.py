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
    python deploy/apply_migration.py --verify

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
* **``--verify`` only reads.** It opens the session read-only, takes no snapshot
  and writes no ledger entry, so reporting on a migration cannot change one.

What the ledger cannot answer
-----------------------------
``--status`` reports what this tool has *recorded*, and before this tool existed
nothing was recorded: on the VPS every file reads "no record", which means
"unknown", not "not applied". ``--verify`` answers from the schema instead — for
each file, which of the objects it declares are actually there, which are gone
because another file replaced them, and which are simply missing. It needs no
privilege: reading a ledger directory that does not exist is not a privileged
operation.

Exit codes
----------
  0  dry run passed and was rolled back, or the migration is committed
  1  the SQL failed, or the rollback could not be verified
  2  nothing to do — bad arguments, no file, no usable credential
  3  refused: the file manages its own transactions
  4  refused: the file needs statements that cannot run in a transaction
  5  refused: the recovery point could not be taken
  6  ``--verify``: at least one file declares objects that are not in the schema
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
import textwrap
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

def schema_snapshot(cur, extra_schemas: tuple[str, ...] = ()) -> dict[str, str]:
    """Every schema object a migration can plausibly touch, as {name: definition}.

    Columns, indexes and constraints cover the usual migration; policies,
    triggers and functions are here because this app relies on row-level security
    and keeps logic in the database, so a migration that changes any of them would
    otherwise look like it changed nothing at all.

    ``extra_schemas`` exists for ``--verify``. The app also owns RLS policies on
    ``storage.objects``, and a check that cannot see them calls a file absent when
    it is present — which is exactly how the first draft of that check reported
    `003_add_storage_rls.sql`. The dry run passes nothing, so the delta it prints
    stays the schema it has always reported.
    """
    items: dict[str, str] = {}
    schemas = ["public", *extra_schemas]

    cur.execute("""
        SELECT 'table ' || tablename, 'exists'
          FROM pg_tables WHERE schemaname = ANY(%s)
    """, (schemas,))
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
          FROM pg_indexes WHERE schemaname = ANY(%s)
    """, (schemas,))
    items.update(cur.fetchall())

    cur.execute("""
        SELECT 'constraint ' || c.conname, pg_get_constraintdef(c.oid)
          FROM pg_constraint c
          JOIN pg_namespace n ON n.oid = c.connamespace
         WHERE n.nspname = ANY(%s)
    """, (schemas,))
    items.update(cur.fetchall())

    cur.execute("""
        SELECT 'policy ' || tablename || '.' || policyname, cmd || ' ' || coalesce(qual, '')
          FROM pg_policies WHERE schemaname = ANY(%s)
    """, (schemas,))
    items.update(cur.fetchall())

    cur.execute("""
        SELECT 'function ' || p.proname, pg_get_function_identity_arguments(p.oid)
          FROM pg_proc p
          JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = ANY(%s)
    """, (schemas,))
    items.update(cur.fetchall())

    cur.execute("""
        SELECT 'trigger ' || t.tgname, 'on ' || c.relname
          FROM pg_trigger t
          JOIN pg_class c ON c.oid = t.tgrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = ANY(%s) AND NOT t.tgisinternal
    """, (schemas,))
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


def ledger_note(entry_path: Path, digest: str) -> str:
    """What the ledger says about one file — shared by ``--status`` and ``--verify``.

    "no record" is deliberately not "not applied": a migration pasted into the SQL
    editor was never recorded anywhere, so the ledger cannot speak for it at all.
    """
    if not entry_path.exists():
        return "no record"
    try:
        entry = json.loads(entry_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "record unreadable"
    if entry.get("sha256") == digest:
        return f"applied {str(entry.get('applied_at', ''))[:10]}"
    return "APPLIED, THEN EDITED"


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
        note = ledger_note(ledger / f"{path.stem}.json", digest)
        if note == "no record":
            unrecorded += 1
        print(f"{path.name:<58} {note:<30} {digest[:12]}")
    print()
    print(f"{unrecorded} of {len(files)} have no record.")
    print("Run --verify to settle those against the schema instead.")
    print("No record does not mean 'not applied'. Migrations pasted into the SQL")
    print("editor before this tool existed were never recorded anywhere, so")
    print("`--status` cannot see them; only a schema check can settle those.")
    return 0


# ── verifying against the live schema ────────────────────────────────────────
#
# `--status` reports what the ledger recorded, and before this tool existed
# nothing was recorded — on the VPS every file reads "no record", which means
# "unknown", not "not applied". So this asks the database instead: for each file,
# are the objects it declares actually there? Absence has four meanings and only
# one of them is a problem:
#
#   present     the object is in the catalogue
#   transient   this file creates it and later drops it again, so its absence is
#               what success looks like — 024's `school_id_new` is the example
#   superseded  another file drops that name, so the object was replaced
#   missing     nothing drops it and it is not there: the file did not take effect
#
# The reading is deliberately literal. It parses the DDL this project actually
# writes and reports what it cannot read rather than guessing, because a verifier
# that is confidently wrong is worse than no verifier at all.

IDENT = r"[a-z_][a-z0-9_$]*"
#: A quoted identifier — anything between double quotes, with a doubled quote
#: standing for a literal one. It needs its own pattern because `IDENT` cannot
#: match `"school-payment-demo"`: the match stops at the hyphen and the report read
#: the object as `school`, which is how a table that exists made 027 read PARTIAL.
QUOTED = r'"(?:[^"]|"")*"'
#: Either spelling, wherever a statement names an object.
NAME = rf"(?:{QUOTED}|{IDENT})"
DO_BODY = re.compile(r"\$[a-zA-Z_]*\$.*?\$[a-zA-Z_]*\$", re.S)
DDL_INSIDE_DO = re.compile(r"\b(?:CREATE|ALTER|DROP)\s+(?:TABLE|POLICY|INDEX|COLUMN)\b",
                           re.I)
DROPPABLE = ("COLUMN", "POLICY", "INDEX", "CONSTRAINT", "TABLE", "FUNCTION", "TRIGGER")


def dequote(name: str) -> str:
    """A captured identifier spelled the way the catalogue spells it.

    Membership in the snapshot is the entire test, so the two sides have to agree
    on spelling. Postgres folds an *unquoted* name to lower case and keeps a quoted
    one exactly as written, hyphens and all — so both halves of that rule are
    applied here rather than assumed about the SQL files.
    """
    if len(name) >= 2 and name.startswith('"') and name.endswith('"'):
        return name[1:-1].replace('""', '"')
    return name.lower()


def strip_sql_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"--[^\n]*", " ", text)


def mask_do_bodies(text: str) -> tuple[str, int]:
    """Replace ``$$ ... $$`` bodies with a placeholder, counting the DDL hidden in them.

    A policy or table created by dynamic SQL inside a DO block cannot be read out
    of the file at all, so the count is reported instead of quietly dropping those
    objects: an object nobody checked must not look like an object that is there.
    """
    hidden = 0

    def swap(match: re.Match) -> str:
        nonlocal hidden
        if DDL_INSIDE_DO.search(match.group(0)):
            hidden += 1
        return " DO_BODY "

    return DO_BODY.sub(swap, text), hidden


def sql_statements(text: str) -> list[str]:
    """Statements, split on `;` after comments and dollar-bodies are gone."""
    return [stmt.strip() for stmt in text.split(";") if stmt.strip()]


def declared_objects(text: str) -> list[tuple[str, str, int]]:
    """``(kind, key, statement index)`` for each object the file creates.

    Keys are built the way :func:`schema_snapshot` builds them — ``policy
    classes.x``, ``column classes.x`` — so membership in the snapshot is the
    entire test, and no second idea of "what exists" has to be kept in step.
    """
    found: list[tuple[str, str, int]] = []
    for index, stmt in enumerate(sql_statements(text)):
        # `ALTER TABLE t ADD COLUMN a ..., ADD COLUMN b ...` names the table once,
        # so the clauses are read from the statement rather than the pattern.
        alter = re.search(
            rf"\bALTER\s+TABLE\s+(?:ONLY\s+)?(?:{NAME}\.)?({NAME})", stmt, re.I)
        if alter:
            for column in re.findall(
                    rf"\bADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?({NAME})",
                    stmt, re.I):
                found.append(("column",
                              f"column {dequote(alter.group(1))}.{dequote(column)}", index))
            for constraint in re.findall(
                    rf"\bADD\s+CONSTRAINT\s+({NAME})", stmt, re.I):
                found.append(("constraint", f"constraint {dequote(constraint)}", index))
        for table in re.findall(
                rf"\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:{NAME}\.)?({NAME})",
                stmt, re.I):
            found.append(("table", f"table {dequote(table)}", index))
        for index_name in re.findall(
                rf"\bCREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:CONCURRENTLY\s+)?"
                rf"(?:IF\s+NOT\s+EXISTS\s+)?({NAME})", stmt, re.I):
            found.append(("index", f"index {dequote(index_name)}", index))
        for policy, owner in re.findall(
                rf"\bCREATE\s+POLICY\s+({NAME})\s+ON\s+(?:{NAME}\.)?({NAME})",
                stmt, re.I):
            found.append(("policy",
                          f"policy {dequote(owner)}.{dequote(policy)}", index))
        for function in re.findall(
                rf"\bCREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+(?:{NAME}\.)?({NAME})",
                stmt, re.I):
            found.append(("function", f"function {dequote(function)}", index))
        for trigger in re.findall(rf"\bCREATE\s+TRIGGER\s+({NAME})", stmt, re.I):
            found.append(("trigger", f"trigger {dequote(trigger)}", index))

    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str, int]] = []
    for kind, key, index in found:
        if (kind, key) in seen:
            continue
        seen.add((kind, key))
        unique.append((kind, key, index))
    return unique


def dropped_names(text: str) -> dict[str, list[int]]:
    """``{name: [statement index, ...]}`` for everything the file drops.

    A ``DROP`` names the object and not always the table it sat on, so matching is
    by bare name — which is also why a name is only ever used here to *explain* an
    absence, never to claim a presence.
    """
    found: dict[str, list[int]] = {}
    for index, stmt in enumerate(sql_statements(text)):
        for kind in DROPPABLE:
            for name in re.findall(
                    rf"\bDROP\s+{kind}\s+(?:IF\s+EXISTS\s+)?(?:{NAME}\.)?({NAME})",
                    stmt, re.I):
                found.setdefault(dequote(name), []).append(index)
    return found


def bare_name(key: str) -> str:
    """`policy classes.x` and `column classes.x` and `index x` -> the name a DROP uses."""
    return key.split(" ", 1)[1].split(".")[-1]


def gap_note(kind: str, key: str, live: dict[str, str]) -> str:
    """Name the reason an object cannot be created, when the file itself shows one."""
    if kind == "policy":
        owner = key.split(" ", 1)[1].split(".")[0]
        if f"table {owner}" not in live:
            return f"   (its table `{owner}` does not exist)"
    return ""


# ── declarations a later generation replaced under a different name ──────────
#
# The rule above explains an absent object by finding a `DROP` of that name. It
# cannot see a *generation* change, and that is what these three are:
# `001_enable_rls_and_policies.sql` carries policies that no other file creates and
# that no file drops — this database was built from `_COMPLETE_SETUP.sql` and 007,
# which carry the same scopes under role-specific names and never had the old ones
# to drop. Under the name rule alone they read as "missing, and nothing drops it",
# which is exactly the verdict this mode exists to hand out, so they are named here
# with their reason instead.
#
# A heuristic was available and is deliberately not used: "a later file declares
# policies on the same table" would clear these three, and would equally clear a
# policy that genuinely never ran the first time a later migration added one policy
# beside it. A verifier that is confidently wrong is worse than none, so each
# exception is written down one object at a time — and `tests/unit/
# test_apply_migration.py` fails if an entry stops naming something the file really
# declares, so this cannot rot into a blanket excuse.
SUPERSEDED: dict[str, dict[str, str]] = {
    "001_enable_rls_and_policies.sql": {
        "policy schools.schools_read_own":
            "replaced by the role-scoped generation in 007 / _COMPLETE_SETUP.sql "
            "(schools_admin_read_own + schools_guru_murid_read_own), which never "
            "created the old name",
        "policy classes.classes_read_own_school":
            "replaced by 007 / _COMPLETE_SETUP.sql "
            "(classes_guru_murid_read + classes_admin_all_own)",
        "policy subjects.subjects_read_own_school":
            "replaced by 007 / _COMPLETE_SETUP.sql "
            "(subjects_guru_murid_read + subjects_admin_all_own)",
    },
}


def connect_readonly(url: str):
    """A session that cannot write, so producing a report can never become a change."""
    conn = psycopg2.connect(url)
    conn.set_session(readonly=True, autocommit=True)
    return conn


def verify(ledger: Path, migrations: Path, cur) -> int:
    """Report every file's declared objects against the live catalogue."""
    files = sorted(migrations.glob("*.sql"))
    if not files:
        print(f"no .sql files in {migrations}")
        return 0

    # `storage` is included because the app owns policies on storage.objects, and
    # a check that cannot see them calls a present file absent.
    live = schema_snapshot(cur, extra_schemas=("storage",))

    prepared: dict[str, tuple[int, list[tuple[str, str, int]], dict[str, list[int]]]] = {}
    droppers: dict[str, set[str]] = {}
    for path in files:
        masked, hidden = mask_do_bodies(
            strip_sql_comments(path.read_text(encoding="utf-8-sig")))
        dropped = dropped_names(masked)
        prepared[path.name] = (hidden, declared_objects(masked), dropped)
        for name in dropped:
            droppers.setdefault(name, set()).add(path.name)

    width = max(len(path.name) for path in files) + 2
    print(f"{'file':<{width}} {'schema':<11} {'record':<22} sha256")
    print("-" * (width + 52))

    gaps: list[tuple[str, list[tuple[str, str]]]] = []
    named_superseded: list[tuple[str, str, str]] = []
    unchecked = 0
    tally = {"IN": 0, "PARTIAL": 0, "OUT": 0, "superseded": 0, "no objects": 0}

    for path in files:
        hidden, declared, dropped = prepared[path.name]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        present, explained, gap = 0, 0, []

        for kind, key, index in declared:
            if key in live:
                present += 1
                continue
            name = bare_name(key)
            if any(when > index for when in dropped.get(name, ())):
                explained += 1              # made and unmade by this file, on purpose
            elif any(who != path.name for who in droppers.get(name, ())):
                explained += 1              # a different file replaced it
            else:
                reason = SUPERSEDED.get(path.name, {}).get(key)
                if reason:
                    explained += 1          # named in this tool, with its reason
                    named_superseded.append((path.name, key, reason))
                else:
                    gap.append((kind, key + gap_note(kind, key, live)))

        if not declared:
            verdict = "no objects"
        elif gap:
            verdict = "PARTIAL" if present else "OUT"
        elif not present:
            verdict = "superseded"
        else:
            verdict = "IN"
        tally[verdict] += 1
        if gap:
            gaps.append((path.name, [text for _, text in gap]))

        mark = f"  [+{hidden} DO body, not checked]" if hidden else ""
        print(f"{path.name:<{width}} {verdict:<11} "
              f"{ledger_note(ledger / f'{path.stem}.json', digest):<22} "
              f"{digest[:12]}{mark}")
        if declared:
            print(f"{'':<{width}} {present}/{len(declared)} declared object(s) present, "
                  f"{explained} replaced or transient")
        else:
            unchecked += 1

    print()
    print(f"{tally['IN']} in, {tally['PARTIAL']} partial, {tally['OUT']} out, "
          f"{tally['superseded']} superseded, {tally['no objects']} with no objects "
          f"to check")

    if gaps:
        print()
        print("=== declared objects that exist nowhere and nothing drops ===")
        for name, missing in gaps:
            print(f"\n{name}")
            for text in missing:
                print(f"    MISSING  {text}")

    if named_superseded:
        print()
        print("=== superseded by a later generation — named here, with the reason ===")
        for file_name, key, reason in named_superseded:
            print(f"\n{file_name}")
            print(f"    SUPERSEDED  {key}")
            for line in textwrap.wrap(reason, 66):
                print(f"                {line}")

    if unchecked:
        print()
        print(f"{unchecked} file(s) declare no object to check. A data-only")
        print("migration (`INSERT`/`UPDATE`) or a placeholder ('copy schema.sql here')")
        print("cannot be settled from the catalogue either way, so neither is counted")
        print("as missing.")

    print()
    if gaps:
        print(f"{len(gaps)} file(s) declare objects the schema does not have.")
        print("A file that was applied by hand still shows its objects, so this is a")
        print("statement about the schema, not about how the file got there.")
        return 6
    print("Every declared object is present, replaced, or transient.")
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
    parser.add_argument("--verify", action="store_true",
                        help="report which migrations are actually in the schema")
    args = parser.parse_args()

    repo = Path(args.repo)
    ledger = Path(args.ledger)
    migrations = repo / "supabase" / "migrations"

    if args.status:
        return status(ledger, migrations)

    if args.verify:
        url = load_migration_url(repo)
        ref = check_target(url, repo)
        print("=" * 74)
        print("VERIFYING against the live schema")
        print("=" * 74)
        print(f"   repo:    {repo}")
        print(f"   target:  project {ref}")
        print("   session: read-only — this mode writes nothing, anywhere")
        print()
        conn = connect_readonly(url)
        try:
            with conn.cursor() as cur:
                return verify(ledger, migrations, cur)
        finally:
            conn.close()

    if not args.file:
        parser.error("give a .sql file, --status, or --verify")

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
