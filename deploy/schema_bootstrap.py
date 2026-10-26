#!/usr/bin/env python3
"""Bring an empty database up to the schema this repository's SQL describes.

There are three ways to read this repository's SQL as a bootstrap, and all three
were measured on an empty PostgreSQL 16 rather than argued about:

* **`supabase/schema.sql` on its own is not one.** Run statement by statement it
  refuses **52 of its 102 statements** and leaves **one** table behind
  (`school_settings`). The first statement creates `profiles` with
  `class_id UUID REFERENCES classes(id)` and the second creates `classes` with
  `teacher_id UUID REFERENCES profiles(id)`: a cycle, so *neither* order works and
  no amount of re-ordering fixes it. Everything that follows fails on the table
  that was never created, which is why the cascade is 52 statements wide.
* **The numbered migrations alone are not one either.** They `ALTER` the base
  tables far more often than they create them: applied by themselves to an empty
  database they leave **26 of the 92 tables** the code names.
* **The aggregate files are the trap.** `supabase/migrations/_RUN_ALL_AT_ONCE.sql`
  and the `combined_*.sql` files restate statements the numbered files already
  carry. Applying them *as well* is not "more complete" — it is the same DDL twice,
  which is how a reproducible bootstrap stops being reproducible.

So the order is: the two base files, the policy files, then the numbered
migrations in name order — with the aggregate files and the placeholder left out
by name, each with its reason in :data:`EXCLUDED`.

## Deferring is the mechanism, and it is not a retry loop

`supabase/_COMPLETE_SETUP.sql` and the migrations are each written against the
schema as it stood *when they were written*, so a statement can name a table, a
column or a function that a later file creates. Dropping such a statement would
build a schema missing whatever it carried; aborting would make the order
impossible to get right by hand. So a statement whose dependency is absent is
**deferred to the next pass** and the run ends when a pass makes no progress.

The distinction that keeps this honest is the SQLSTATE, not the message:

* ``42P01`` (undefined table), ``42703`` (undefined column), ``42883`` (undefined
  function), ``3F000`` (undefined schema) and ``42P02`` (undefined parameter) are
  *"not yet"* — deferred.
* ``42710``/``42P07``/``42701``/``42723``/``42P06`` are *"already there"* — the
  file was written without `IF NOT EXISTS`, so a second run answers this way. It
  is counted, not failed: re-running is what makes this a bootstrap rather than a
  one-shot.
* **Everything else is a finding**, reported with the file, the code and the
  statement, and the run exits 1. A type conflict (`42804`) and a deliberate
  `RAISE` (`P0001`) are not things another pass will fix, and a bootstrap that
  shrugged at them would report a schema this repository cannot actually build.

## A target that is not empty is a different question, and `--audit` is the answer

Running the plan against a database that already holds another application's
tables — which is what the Sydney staging project is: a legacy Django schema, a
hand-written scaffold of `schools`, `profiles` and `exams`, and the ScanMeet
migrations on top — is not a bootstrap, it is a merge, and `CREATE TABLE IF NOT
EXISTS` turns a collision into a silent skip. `--audit` reads the target and
writes nothing: which declared relations are absent, which are present but short
of a declared column, and — with `pg_class.reltuples`, no scan and no lock — how
many rows the tables it would alter already hold. A relation that exists with
*more* columns than declared is not a finding: the migrations add columns with
`IF NOT EXISTS`, so that is the normal case.

## A repair migration is why the sweep exists

A statement can be correct and still fail because of the *shape* at the moment it
runs. `007`'s `UPDATE classes SET school_id = '<uuid>'` fails while
`classes.school_id` is `integer` — a type `003_submission_hidden_retracted.sql`
sets on a file about submission visibility, with `IF NOT EXISTS`, so `007`'s
`uuid` request is a silent no-op. The migration that repairs it
(`076_classes_school_id_uuid.sql`) sorts later, so it cannot have run yet.

Refusing those statements as final would leave eight RLS policies out of a schema
that reports itself complete; retrying for ever would hide a statement that can
never apply. So after the plan has run end to end, each refused statement gets
**one** more attempt, and what is still refused after that is the finding. The
run says how many it repaired that way, so the sweep is visible rather than an
undocumented second chance.

## What it refuses to do

Production is refused by default, three ways: by project ref
(`deploy/supabase_region.py`'s ``REF``), by the live domain, and — the one that
matters when parsing fails — **when no ref can be read at all and the host is not
this machine**, because silence is not evidence of safety. This tool is for an
empty staging or scratch database, and running it against the exam site is the one
mistake there is no second chance at. It prints the target **ref** and never the
URL, so neither a password in a pooler URL nor one in a `postgresql://` DSN
reaches a log.

Exit codes follow the rest of `deploy/`: 0 the schema converged and every table the
code names exists · 1 a finding (a statement was refused, or a table is still
missing) · 2 could not measure (no SQL, no URL, no driver).
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
from dataclasses import dataclass, field

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: Base files first, in the order they must run: `schema.sql` creates the exam
#: core, `_COMPLETE_SETUP.sql` the school and billing half, and several migrations
#: alter both.
BASE = ("supabase/schema.sql", "supabase/_COMPLETE_SETUP.sql")

#: Left out by name, with the reason attached to each. A file that is skipped has
#: to say why here, or the next reader adds it back and the count changes.
EXCLUDED = {
    "_run_all_at_once.sql":
        "an aggregate: it restates statements the numbered migrations carry, and "
        "applying both runs that DDL twice",
    "001_initial_schema.sql":
        "a two-line placeholder — 'Copy content from supabase/schema.sql' — so it "
        "creates nothing and must not be read as the base it names",
}

#: ``"not yet"``: the dependency this statement names does not exist on this pass.
DEFERRED = frozenset({"42P01", "42703", "42883", "3F000", "42P02"})

#: ``"already there"``: the file omitted `IF NOT EXISTS`, so a second run lands here.
TOLERATED = frozenset({"42710", "42P07", "42701", "42723", "42P06"})

#: How many passes before the run gives up on what is still deferred. A schema
#: layer can be deep, but it is not 12 files deep; a number this small is what
#: makes "no progress" a measurement rather than a timeout.
MAX_PASSES = 12

PLACEHOLDER = "[YOUR-PASSWORD]"


# ── statements, split for execution rather than for reading ───────────────────
#
# `apply_migration.sql_statements()` cannot be reused here: it *masks* a dollar
# body to " DO_BODY " so the objects inside it can be counted, and a masked
# statement is not SQL any server will run. This splitter keeps every byte and
# only decides where a statement ends — inside `$$`/`$tag$`, inside a quoted
# string, inside `--` and `/* */` a `;` is not a separator.

_DOLLAR = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$")


def statements(text: str) -> list[str]:
    """Split on `;`, respecting dollar bodies, quotes and comments."""
    out: list[str] = []
    buf: list[str] = []
    i, n = 0, len(text)
    tag: str | None = None
    while i < n:
        if tag:
            if text.startswith(tag, i):
                buf.append(tag)
                i += len(tag)
                tag = None
            else:
                buf.append(text[i])
                i += 1
            continue
        ch = text[i]
        if ch == "-" and text.startswith("--", i):
            nl = text.find("\n", i)
            i = n if nl < 0 else nl
            continue
        if ch == "/" and text.startswith("/*", i):
            close = text.find("*/", i)
            i = n if close < 0 else close + 2
            continue
        if ch == "'":
            j = i + 1
            while j < n:
                if text[j] == "'":
                    if j + 1 < n and text[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            buf.append(text[i:j + 1])
            i = j + 1
            continue
        match = _DOLLAR.match(text, i)
        if match:
            tag = match.group(0)
            buf.append(tag)
            i += len(tag)
            continue
        if ch == ";":
            piece = "".join(buf).strip()
            if piece:
                out.append(piece)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    piece = "".join(buf).strip()
    if piece:
        out.append(piece)
    return out


# ── the plan ─────────────────────────────────────────────────────────────────

@dataclass
class Step:
    source: str          # repo-relative path
    sql: str             # one statement, exactly as committed

    def head(self, width: int = 60) -> str:
        line = " ".join(self.sql.split())
        return line if len(line) <= width else line[:width - 3] + "..."


def _excluded(name: str) -> bool:
    low = name.lower()
    return low in EXCLUDED or low.startswith("combined_")


def plan(repo: pathlib.Path = ROOT) -> list[Step]:
    """Every statement to run, in the order this repository can actually run it."""
    files: list[pathlib.Path] = []
    for rel in BASE:
        path = repo / rel
        if path.is_file():
            files.append(path)
    policies = repo / "supabase" / "policies"
    if policies.is_dir():
        files.extend(sorted(policies.glob("*.sql")))
    migrations = repo / "supabase" / "migrations"
    if migrations.is_dir():
        files.extend(sorted(migrations.glob("*.sql")))

    steps: list[Step] = []
    for path in files:
        if _excluded(path.name):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        rel = path.relative_to(repo).as_posix()
        steps.extend(Step(rel, sql) for sql in statements(text))
    return steps


# ── applying it ──────────────────────────────────────────────────────────────

@dataclass
class Report:
    applied: int = 0
    tolerated: dict[str, int] = field(default_factory=dict)
    hard: list[tuple[Step, str, str]] = field(default_factory=list)   # step, code, message
    pending: list[Step] = field(default_factory=list)
    repaired: int = 0          # applied on the sweep that follows a repair migration
    passes: int = 0

    def tolerate(self, code: str) -> None:
        self.tolerated[code] = self.tolerated.get(code, 0) + 1

    @property
    def tolerated_total(self) -> int:
        return sum(self.tolerated.values())

    @property
    def converged(self) -> bool:
        return not self.pending and not self.hard


def _is_view_replace(sql: str) -> bool:
    head = " ".join(sql.split())[:40].upper()
    return head.startswith("CREATE OR REPLACE VIEW") or head.startswith("CREATE OR REPLACE MATERIALIZED VIEW")


def _run(conn, step: Step, report: Report) -> str:
    """One statement, one verdict: ``applied``, ``deferred``, ``tolerated`` or ``hard``."""
    cur = conn.cursor()
    try:
        cur.execute(step.sql)
        conn.commit()
        return "applied"
    except Exception as exc:                              # psycopg2.Error, and stubs
        conn.rollback()
        code = getattr(exc, "pgcode", None)
        message = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
        if code in DEFERRED:
            return "deferred"
        if code in TOLERATED:
            report.tolerate(code)
            return "tolerated"
        if code == "42P16" and _is_view_replace(step.sql):
            # `CREATE OR REPLACE VIEW` may add a column and may not remove one
            # (`cannot drop columns from view`). On a re-run the view is already
            # the *wider* definition a later file installed, which is the state
            # the release wants; refusing here would mean a bootstrap that can
            # only be run once, and dropping the view to replay a narrower
            # definition would take the later file's columns away until it ran.
            # Counted, so the re-run says what it skipped rather than implying
            # the statement changed nothing.
            report.tolerate(code)
            return "tolerated"
        if code == "23505" and step.sql.lstrip().upper().startswith("INSERT"):
            # A seed that inserts a fixed id the base file already inserted. The
            # row the statement wanted is there; failing the run would refuse a
            # schema whose only complaint is that it was built twice.
            report.tolerate(code)
            return "tolerated"
        report.hard.append((step, code or "-", message))
        return "hard"
    finally:
        cur.close()


def apply(conn, steps: list[Step], max_passes: int = MAX_PASSES) -> Report:
    """Run every step, deferring what is not there yet, then sweep what was refused.

    The sweep is what a repair migration is for. A statement can be correct and
    still fail because the column it reads is the wrong type *at the moment it
    runs* — `007`'s `UPDATE classes SET school_id = '<uuid>'` fails while
    `classes.school_id` is `integer`, and the migration that retypes it (`076`)
    has not run yet because it sorts later. Classifying that as final would leave
    eight RLS policies missing from a database nobody could tell was incomplete;
    retrying it forever would hide a statement that can never apply. So each
    refused statement gets **one** more attempt after the whole plan has run, and
    what is still refused after that is the finding.
    """
    report = Report()
    remaining = list(steps)
    while remaining and report.passes < max_passes:
        report.passes += 1
        deferred: list[Step] = []
        progressed = 0
        for step in remaining:
            verdict = _run(conn, step, report)
            if verdict == "deferred":
                deferred.append(step)
            elif verdict in ("applied", "tolerated"):
                if verdict == "applied":
                    report.applied += 1
                progressed += 1
        remaining = deferred
        if not progressed:
            break
    report.pending = remaining

    if report.hard and report.passes < max_passes:
        retry = [step for step, _, _ in report.hard]
        report.hard = []
        report.passes += 1
        for step in retry:
            verdict = _run(conn, step, report)
            if verdict == "applied":
                report.applied += 1
                report.repaired += 1
    return report


#: Names the SQL *declares* that this repository never creates, with the reason.
#: `activation_codes` is called what it is by the file that mentions it —
#: `20260608_fix_rls_policies.sql` says in a comment that it is a legacy name and
#: guards every statement about it in a DO block, because nothing creates it. A
#: bootstrap that reported it as missing would fail for a reason the repository
#: has already written down, and a list that stays quiet about it would be hiding
#: a name nothing builds. So it is reported on its own line, never as a pass.
LEGACY = {
    "activation_codes":
        "named as legacy by 20260608_fix_rls_policies.sql, which guards every "
        "statement about it in a DO block because nothing creates it",
}


def tables(cur) -> set[str]:
    """Every relation in `public`, views included.

    Two of the names the SQL declares are views (`active_school_years`,
    `class_student_counts`), and a check that read `pg_tables` alone would call a
    view a missing table — which is a measurement bug wearing the costume of a
    finding.
    """
    cur.execute(
        "select tablename from pg_tables where schemaname = 'public' "
        "union select viewname from pg_views where schemaname = 'public' "
        "union select matviewname from pg_matviews where schemaname = 'public'")
    return {row[0] for row in cur.fetchall()}


def declared_schema() -> dict[str, set[str]]:
    """Every table the repository's SQL says the schema has, with its columns.

    `schema_contract` already parses them out of the SQL — including the `ALTER
    TABLE ... ADD COLUMN` that most migrations are — so this does not keep a second
    idea of the list in step with it.
    """
    sys.path.insert(0, str(ROOT))
    from deploy import schema_contract
    return schema_contract.schema_from_migrations()


def declared() -> set[str]:
    """The table names alone, which is all a completeness verdict needs."""
    return set(declared_schema())


# ── the audit, which writes nothing ─────────────────────────────────────────

def live_columns(cur) -> dict[str, set[str]]:
    """Every relation in `public` and its columns, read, never written."""
    cur.execute(
        "select table_name, column_name from information_schema.columns "
        "where table_schema = 'public'")
    out: dict[str, set[str]] = {}
    for table, column in cur.fetchall():
        out.setdefault(table, set()).add(column)
    return out


def row_estimates(cur) -> dict[str, int]:
    """A cheap row count per relation, for the tables a bootstrap would alter.

    `reltuples` is the planner's own estimate and needs no scan, no lock and no
    ownership — which is the point on a database holding somebody else's data: the
    number exists to stop a reader from treating a populated table as scaffolding.
    """
    cur.execute(
        "select c.relname, greatest(c.reltuples, 0)::bigint from pg_class c "
        "join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'public'")
    return {name: int(count) for name, count in cur.fetchall()}


def audit(cur, declared_tables: dict[str, set[str]] | None = None) -> dict:
    """What an empty-database bootstrap would do to *this* database, without doing it.

    A target that is not empty is the case this exists for. Sydney already carries
    a legacy application's tables and a hand-written scaffold of `schools`,
    `profiles` and `exams`, so the question is never "is it empty" but "which of
    the declarations would collide with something that is already there and has
    rows in it". A relation that exists with *more* columns than declared is fine
    — the migrations add columns with `IF NOT EXISTS`. One that exists with
    *fewer*, or with a column of its own name and another type, is the dangerous
    case, and it is reported with its row estimate so nobody reads a populated
    table as scaffolding.
    """
    wanted = declared_tables if declared_tables is not None else declared_schema()
    live = live_columns(cur)
    rows = row_estimates(cur)
    # The same exemption the completeness verdict uses: a name this repository says
    # nothing creates is not a gap an audit can ask somebody to close.
    absent = {name: cols for name, cols in wanted.items()
              if name not in live and name not in LEGACY}
    legacy = sorted(name for name in wanted if name not in live and name in LEGACY)
    present = {name: cols for name, cols in wanted.items() if name in live}
    short = {name: sorted(cols - live[name]) for name, cols in present.items()
             if cols - live[name]}
    foreign = sorted(set(live) - set(wanted))
    return {
        "absent": absent,
        "legacy": legacy,
        "short": short,
        "rows": {name: rows.get(name, 0) for name in short},
        "foreign": foreign,
        "present": len(present),
        "declared": len(wanted),
    }


# ── target ───────────────────────────────────────────────────────────────────

def target_ref(url: str) -> str | None:
    from deploy import apply_migration
    return apply_migration.project_ref(url)


def load_url(repo: pathlib.Path) -> str:
    from deploy import apply_migration
    return apply_migration.load_migration_url(repo)


#: The live site's own name. A DSN never carries it, but an operator pastes one
#: into `--url` often enough that naming it costs nothing and closes the case
#: where the ref cannot be read out of the string.
LIVE_DOMAIN = "scangrade.web.id"
#: Hosts a bootstrap may write to without being asked: the box it runs on.
LOCAL_HOSTS = {"", "localhost", "127.0.0.1", "::1", "[::1]", "host.docker.internal"}


def _looks_local(url: str) -> bool:
    """A unix socket, a loopback host, or a socket path in the query."""
    from urllib.parse import urlsplit
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if (parts.hostname or "") in LOCAL_HOSTS:
        return True
    return "host=/" in (parts.query or "") or "host=%2F" in (parts.query or "").lower()


def refuse_production(url: str, allow_unidentified: bool = False) -> str | None:
    """Refuse the exam site by project ref, whatever hostname it arrives under.

    The default has to be *no*, because the mistake this tool can make is not
    recoverable: a bootstrap creates and alters tables, and the one database in
    this project where that is not a rehearsal is production. Three refusals, in
    order of how much they depend on parsing:

    * the URL names production's project ref;
    * the URL still carries the dashboard placeholder, so nothing real is in it;
    * **no ref can be read at all** and the host is not this machine — an
      unidentifiable remote target is refused unless `--allow-unidentified-target`
      says otherwise. Silence is not evidence of safety.
    """
    from deploy import supabase_region
    ref = target_ref(url)
    if ref == supabase_region.REF:
        return (f"refusing: this URL is production's project {ref}. A bootstrap "
                "belongs on an empty staging or scratch database; there is no "
                "second chance at the exam site.")
    if PLACEHOLDER in url:
        return (f"refusing: the URL still contains the dashboard placeholder "
                f"`{PLACEHOLDER}` — nothing was run.")
    if LIVE_DOMAIN in url.lower():
        return (f"refusing: the URL names {LIVE_DOMAIN}, which is the live site. "
                "A bootstrap never runs there.")
    if ref is None and not _looks_local(url) and not allow_unidentified:
        return ("refusing: no Supabase project ref can be read out of this URL and "
                "the host is not this machine, so the target cannot be shown to be "
                "anything but production. Pass --allow-unidentified-target only for a "
                "database you know by name to be scratch.")
    return None


def verify(cur) -> dict:
    """The shape of what was built: foreign keys, RLS coverage, and what is invalid.

    A relation that exists with the right name is not the claim. `NOT VALID` is how
    Postgres lets a constraint exist without being enforced, and a table with RLS
    switched off is a table whose policies apply to nobody — both are the kind of
    "the table is there" that a count of tables cannot tell apart from a schema.
    """
    cur.execute(
        "select count(*) from pg_constraint c join pg_namespace n "
        "on n.oid = c.connamespace where n.nspname = 'public' and c.contype = 'f'")
    foreign_keys = int(cur.fetchone()[0])
    cur.execute(
        "select count(*) from pg_constraint c join pg_namespace n "
        "on n.oid = c.connamespace where n.nspname = 'public' "
        "and c.contype = 'f' and not c.convalidated")
    not_valid = int(cur.fetchone()[0])
    cur.execute(
        "select count(*) filter (where c.relrowsecurity), count(*) "
        "from pg_class c join pg_namespace n on n.oid = c.relnamespace "
        "where n.nspname = 'public' and c.relkind = 'r'")
    with_rls, tables_total = (int(value) for value in cur.fetchone())
    cur.execute("select count(*) from pg_policies where schemaname = 'public'")
    policies = int(cur.fetchone()[0])
    return {"foreign_keys": foreign_keys, "not_valid": not_valid,
            "rls_tables": with_rls, "tables": tables_total, "policies": policies}


def _audit(url: str, ref: str, json_path: str | None) -> int:
    """The read-only answer: what is here, what is missing, what would collide."""
    from deploy import apply_migration

    conn = apply_migration.connect(url)
    try:
        cur = conn.cursor()
        try:
            reading = audit(cur)
        finally:
            cur.close()
    finally:
        conn.close()

    absent, short, rows = reading["absent"], reading["short"], reading["rows"]
    print(f"schema audit: target {ref} (read-only, nothing written)")
    print(f"schema audit: {reading['present']} of {reading['declared']} declared "
          f"relation(s) present, {len(absent)} absent, {len(short)} present but "
          f"missing declared column(s), {len(reading['foreign'])} relation(s) this "
          f"repository does not declare")
    for name in sorted(absent):
        print(f"  ABSENT {name}")
    for name in sorted(short):
        print(f"  SHORT {name}  ~{rows.get(name, 0)} row(s) here; would gain "
              f"{', '.join(short[name])[:120]}")
    for name in reading["legacy"]:
        print(f"  LEGACY {name}  -  {LEGACY[name]}")
    if reading["foreign"]:
        shown = ", ".join(reading["foreign"][:12])
        more = len(reading["foreign"]) - 12
        print(f"  UNTOUCHED (not declared, so no statement in this repository names "
              f"them, and this tool creates and drops nothing): {shown}"
              + (f" ... and {more} more" if more > 0 else ""))

    verdict = "PASS" if not absent and not short else "GAPS"
    print(f"schema audit: {verdict}  -  "
          + ("this target already has every relation and column the repository "
             "declares" if verdict == "PASS" else
             "an empty-database bootstrap would not be a no-op here (see above)"))

    if json_path:
        pathlib.Path(json_path).write_text(json.dumps({
            "target_ref": ref,
            "verdict": verdict,
            "absent": {name: sorted(cols) for name, cols in absent.items()},
            "legacy": reading["legacy"],
            "short": short,
            "estimated_rows": rows,
            "not_declared": reading["foreign"],
        }, indent=1), encoding="utf-8")
        print("wrote", json_path)

    return 0 if verdict == "PASS" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=str(ROOT))
    parser.add_argument("--url", default=None,
                        help="DIRECT_URL / DATABASE_URL for an empty database")
    parser.add_argument("--plan", action="store_true",
                        help="print the order and stop; touches no server")
    parser.add_argument("--audit", action="store_true",
                        help="read the target and report what a bootstrap would "
                             "find there; writes nothing")
    parser.add_argument("--json", default=None, metavar="PATH")
    parser.add_argument("--max-passes", type=int, default=MAX_PASSES)
    parser.add_argument("--allow-unidentified-target", action="store_true",
                        help="permit a remote target whose project ref cannot be "
                             "read; only for a database you know by name to be scratch")
    args = parser.parse_args(argv)

    repo = pathlib.Path(args.repo).resolve()
    steps = plan(repo)
    if not steps:
        print(f"schema bootstrap: cannot measure  -  no SQL under {repo / 'supabase'}",
              file=sys.stderr)
        return 2

    sources: list[str] = []
    for step in steps:
        if not sources or sources[-1] != step.source:
            sources.append(step.source)
    if args.plan:
        for source in sources:
            count = sum(1 for step in steps if step.source == source)
            print(f"  {count:4d} statement(s)  {source}")
        print(f"{len(steps)} statement(s) from {len(sources)} file(s), in this order.")
        for name, reason in EXCLUDED.items():
            print(f"  left out: {name}  -  {reason}")
        print("  left out: combined_*.sql  -  the same reason: an aggregate")
        return 0

    url = (args.url or "").strip() or None
    if not url:
        try:
            url = load_url(repo)
        except SystemExit as exc:
            print(f"schema bootstrap: cannot measure  -  {exc}", file=sys.stderr)
            return 2

    refusal = refuse_production(url, args.allow_unidentified_target)
    if refusal:
        print(f"schema bootstrap: {refusal}", file=sys.stderr)
        return 1

    try:
        from deploy import apply_migration
    except ImportError as exc:                            # pragma: no cover
        print(f"schema bootstrap: cannot measure  -  {exc}", file=sys.stderr)
        return 2

    ref = target_ref(url) or "(no supabase ref in the URL)"

    if args.audit:
        return _audit(url, ref, args.json)

    print(f"schema bootstrap: target {ref}. {len(steps)} statement(s) from "
          f"{len(sources)} file(s)")

    conn = apply_migration.connect(url)
    try:
        report = apply(conn, steps, args.max_passes)
        cur = conn.cursor()
        try:
            present = tables(cur)
            shape = verify(cur)
        finally:
            cur.close()
    finally:
        conn.close()

    wanted = declared()
    absent = wanted - present
    missing = sorted(name for name in absent if name not in LEGACY)
    legacy = sorted(name for name in absent if name in LEGACY)
    extra = sorted(present - wanted)

    print(f"schema bootstrap: {report.applied} applied "
          f"({report.repaired} on the repair sweep), "
          f"{report.tolerated_total} already there, {len(report.hard)} refused, "
          f"{len(report.pending)} still waiting, over {report.passes} pass(es)")
    for step, code, message in report.hard[:20]:
        print(f"  REFUSED {step.source}  {code}  {message}\n      {step.head()}")
    if len(report.hard) > 20:
        print(f"  ... and {len(report.hard) - 20} more refused")
    for step in report.pending[:20]:
        print(f"  WAITING {step.source}  {step.head()}")
    if len(report.pending) > 20:
        print(f"  ... and {len(report.pending) - 20} more waiting")
    print(f"schema bootstrap: {len(present)} table(s) present, {len(wanted)} the "
          f"SQL declares, {len(missing)} missing, {len(extra)} built by no file")
    for name in missing[:20]:
        print(f"  MISSING {name}")
    if len(missing) > 20:
        print(f"  ... and {len(missing) - 20} more missing")
    for name in legacy:
        print(f"  LEGACY {name}  -  {LEGACY[name]}")
    for name in extra[:20]:
        print(f"  NOT DECLARED {name}")

    print(f"schema bootstrap: {shape['foreign_keys']} foreign key(s), "
          f"{shape['not_valid']} NOT VALID, {shape['rls_tables']}/{shape['tables']} "
          f"table(s) with RLS on, {shape['policies']} polic(y/ies)")
    if shape["not_valid"]:
        print(f"  INVALID {shape['not_valid']} foreign key(s) exist without being "
              f"enforced — a constraint that is not validated is not a constraint")

    verdict = "PASS" if (report.converged and not missing
                          and not shape["not_valid"]) else "FAIL"
    print(f"schema bootstrap: {verdict}  -  "
          + ("every declared relation exists, every statement ran, and every "
             "foreign key is validated"
             if verdict == "PASS" else
             "the schema is not the one this repository describes (see above)"))

    if args.json:
        pathlib.Path(args.json).write_text(json.dumps({
            "target_ref": ref,
            "verdict": verdict,
            "applied": report.applied,
            "already_there": report.tolerated,
            "refused": [{"source": st.source, "code": c, "message": m,
                         "statement": st.head(200)} for st, c, m in report.hard],
            "repaired_on_sweep": report.repaired,
            "shape": shape,
            "waiting": [{"source": s.source, "statement": s.head(200)}
                        for s in report.pending],
            "present": sorted(present),
            "declared": sorted(wanted),
            "missing": missing,
            "legacy": {name: LEGACY[name] for name in legacy},
            "not_declared": extra,
        }, indent=1), encoding="utf-8")
        print("wrote", args.json)

    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
