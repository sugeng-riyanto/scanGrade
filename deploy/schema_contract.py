#!/usr/bin/env python3
"""Does the code, the SQL this repository carries, and the API agree about the data?

Three answers, and they are three ways to be wrong quietly.

**1. A name nothing creates.** PostgREST answers an unknown *table* with `PGRST205`
and an unknown *column* with `42703` — and a route that wraps its query in
`try/except` turns either into an empty page. Four live examples, found on the
first run of this check:

* the audit log selected `profiles.email`, a column that has never existed, so the
  whole request was refused and the page rendered an empty log while the table
  held rows;
* the proctoring panel read `student_classes`, a join table this database does not
  have, so the endpoint answered 500;
* the privacy settings wrote `system_settings`, a table no migration ever created,
  and answered `{"success": true}` anyway;
* the retention export selected `penalty` from `violation_logs` and ordered
  `notification_recipients` by `created_at`, either of which refuses the request —
  so a pupil's data export came back without their violations or their messages.

**2. A policy any caller can satisfy.** A `CREATE POLICY` with no `TO` clause applies
to `PUBLIC`, `anon` included — and `anon` is the key that ships in every page, so it
is not a secret. That is harmless when the expression itself asks for a session
(`auth.uid() = id`) and a hole when it does not. Measured against the live database
with that public key and no session: `exams` answered with the `answer_key` of every
published paper; `school_registration_requests` with the activation code of every
pending school; `notifications` with every staff message; `violation_logs` accepted
**writes**, so any caller could forge the record an anti-cheat penalty is computed
from. The two views answered as well, because a view without `security_invoker` runs
as its owner and every policy underneath is skipped. It is the *state* that is judged,
not the history: `DROP POLICY` and `CREATE POLICY` are applied in file order, so
migration 028 can close a hole 002 opened and this check stops reporting it.

**3. A role the database cannot hold.** `profiles.role` is held to a CHECK constraint,
and migration 007 moved the vocabulary from `student`/`teacher`/`admin` to
`murid`/`guru`/`admin_sekolah`/`super_admin`. A comparison against a legacy name is not
a typo anyone sees: the branch never runs, so whatever guard it held is not there.

## What the contract is made of

The contract is **the SQL this repository carries**, not a copy of the live schema:
it is reviewable in a diff, and a laptop with no credentials can still check it.

*All* of it, which is the second defect this check found in itself. It first read
only `supabase/migrations/*.sql` — and `001_initial_schema.sql` is a two-line stub
whose text is "Copy content from supabase/schema.sql", while the base tables
(`profiles`, `exams`, `submissions`, `classes`, `exam_access_codes`,
`violation_logs`, `analytics_cache`) live in `supabase/schema.sql` and the AI and
billing tables live in `supabase/_COMPLETE_SETUP.sql`. So the first honest run
reported **72 names the code uses that "no migration creates"** — every one of
them a real column of a real table, read as absent because the file that declares
it was never opened. A check that cries wolf on `submissions.score` is a check
nobody reads. The sources are listed in `SOURCES` below, and a table's columns are
the union over all of them, so an `ALTER TABLE` in a migration still adds to a
`CREATE TABLE` in the base file.

Modes:
    python deploy/schema_contract.py            # code, policies and roles (offline)
    python deploy/schema_contract.py --live     # also ask the database and the API
    python deploy/schema_contract.py --anon     # ask the API what it serves with no session
    python deploy/schema_contract.py --json out.json

`--anon` is the other direction and the one that found the answer keys: it points the
**public** key at every object PostgREST serves and asks for one row. Nothing in this
app needs to answer without a session — every page is rendered server-side with the
service key — so anything that does is reported. Run it after a migration, not as part
of the deploy.

`--live` is what found the drift in the other direction: four columns and two views
that exist in production and in no file here, added by hand in the SQL Editor, so a
database rebuilt from this repository would not have them — including
`school_settings.demo_settings`, which `get_demo_settings()` reads on every page. It
reports that as a failure and a migration not yet pasted as *pending*, because those
are opposite facts: one is a database ahead of the repository, the other is the
repository ahead of the database.

Exit codes, like the other gates: 0 pass · 1 a real disagreement (a name nothing
creates, a policy open to PUBLIC, a role the constraint cannot hold, or an object the
API serves that no file declares) · 2 could not measure (no SQL found, unreadable).
"""
from __future__ import annotations

import ast
import json
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
MIGRATIONS = ROOT / "supabase" / "migrations"

#: Every SQL file that declares part of the schema. A table has no single home:
#: the base tables are in `schema.sql`, the billing/AI ones in
#: `_COMPLETE_SETUP.sql`, and each migration adds to whichever it means. Reading
#: only one directory is how 72 real columns came back as "nothing creates this".
SQL_SOURCES = (
    ROOT / "supabase" / "schema.sql",
    ROOT / "supabase" / "_COMPLETE_SETUP.sql",
    MIGRATIONS / "_RUN_ALL_AT_ONCE.sql",
    ROOT / "supabase" / "policies" / "profiles_policies.sql",
)

SCAN = ("app", "tools", "manage.py", "provision_loadtest.py", "wsgi.py")

#: Column names that are SQL clauses in a CREATE TABLE body rather than columns.
_NOT_A_COLUMN = re.compile(
    r"^(primary|unique|foreign|constraint|check|exclude|like|inherits|partition)\b", re.I)

FILTERS = ("eq", "neq", "gt", "gte", "lt", "lte", "like", "ilike", "is_", "in_",
           "contains", "contained_by", "overlaps", "text_search", "match", "not_")
ALIAS = re.compile(r"^(\w+)\s*:\s*(\w+)$")
HINT = re.compile(r"!\s*\(?\w*\)?")


# ── what the migrations create ──────────────────────────────────────────────

def split_sql_list(body: str) -> list[str]:
    """Comma-separated parts of a CREATE TABLE body, ignoring nested brackets.

    Braces count as well as parentheses, and that is not decoration: a column
    default holds JSON — `prompts JSONB DEFAULT '{"feedback": {"range": true}}'` —
    whose commas are at paren-depth zero, so splitting on parens alone turned the
    blob into "columns" named `{"feedback":` and `feedback":`. Four such names
    appeared in the report on the first honest run of `--live`, and a check that
    invents four columns teaches its reader to ignore it.
    """
    out, depth, cur = [], 0, ""
    for ch in body:
        if ch in "({[":
            depth += 1
        elif ch in ")}]":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    out.append(cur.strip())
    return [p for p in out if p]


def sql_files(directory: pathlib.Path | None = None) -> list[pathlib.Path]:
    """Every SQL file whose `CREATE`/`ALTER` the contract is made of, deduplicated.

    An explicit directory is used by the tests to point all of them at one place;
    the default is the repository's own list, migrations included in name order.
    """
    if directory is not None:
        # The same shape as a real `supabase/` directory — the base files, then the
        # migrations, then the policy files — because the *order* is the contract here:
        # a drop in `migrations/` has to come after the create in the base file, and a
        # checker that reads them the other way round reports closed holes for ever.
        #
        # Within one directory the base files come first and the numbered migrations
        # after, because `schema.sql` is not migration 007: plain `sorted()` puts
        # `007_multi_school…` before `schema.sql`, so the constraint the base file
        # carries would be read as the one still in force.
        def base_first(path: pathlib.Path) -> tuple[int, str]:
            return (1 if path.name[:1].isdigit() else 0, path.name)

        files = sorted(directory.glob("*.sql"), key=base_first)
        for sub in ("migrations", "policies"):
            if (directory / sub).is_dir():
                files += sorted((directory / sub).glob("*.sql"), key=base_first)
        return [p for p in files if p.is_file()]
    files = [p for p in SQL_SOURCES if p.is_file()]
    if MIGRATIONS.is_dir():
        files += sorted(MIGRATIONS.glob("*.sql"))
    seen: set[pathlib.Path] = set()
    out = []
    for path in files:
        key = path.resolve()
        if key not in seen:
            seen.add(key)
            out.append(path)
    return out


def schema_from_migrations(directory: pathlib.Path | None = None) -> dict[str, set[str]]:
    """{table: {column, …}} as the SQL files in this repository declare it."""
    tables: dict[str, set[str]] = {}
    files = sql_files(directory)
    if not files:
        return tables
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        text = re.sub(r"--[^\n]*", " ", text)          # comments hold example SQL
        for m in re.finditer(r"CREATE\s+(?:OR\s+REPLACE\s+)?TABLE\s+"
                             r"(?:IF\s+NOT\s+EXISTS\s+)?(?:public\.)?\"?([\w-]+)\"?\s*\(",
                             text, re.I):
            name = m.group(1)
            depth, i = 1, m.end()
            while i < len(text) and depth:
                if text[i] == "(":
                    depth += 1
                elif text[i] == ")":
                    depth -= 1
                i += 1
            cols = tables.setdefault(name, set())
            for part in split_sql_list(text[m.end(): i - 1]):
                head = part.strip().split()[0].strip('"') if part.strip() else ""
                if head and not _NOT_A_COLUMN.match(head):
                    cols.add(head)
        # A view is read exactly like a table, so the code can name its columns
        # and PostgREST serves it at `/rest/v1/<view>` all the same. Written with
        # an explicit column list — `CREATE VIEW v (a, b) AS` — the shape is
        # readable here; a view whose columns only exist inside its SELECT is not,
        # and is reported as *undeclared* rather than silently assumed.
        for m in re.finditer(r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:MATERIALIZED\s+)?VIEW\s+"
                             r"(?:IF\s+NOT\s+EXISTS\s+)?(?:public\.)?\"?(\w+)\"?\s*\(",
                             text, re.I):
            cols = tables.setdefault(m.group(1), set())
            depth, i = 1, m.end()
            while i < len(text) and depth:
                if text[i] == "(":
                    depth += 1
                elif text[i] == ")":
                    depth -= 1
                i += 1
            for part in split_sql_list(text[m.end(): i - 1]):
                head = part.strip().split()[0].strip('"') if part.strip() else ""
                if head and not _NOT_A_COLUMN.match(head):
                    cols.add(head)
        # One `ALTER TABLE` may add several columns, and every one of them is a
        # column the code may name: `ALTER TABLE profiles ADD COLUMN a …, ADD
        # COLUMN b …`. Reading only the first `ADD COLUMN` after the table name
        # left the rest unaccounted for and reported real columns as unknown.
        for m in re.finditer(r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:public\.)?\"?(\w+)\"?",
                             text, re.I):
            statement = text[m.end(): text.find(";", m.end()) if ";" in text[m.end():]
                               else len(text)]
            cols = tables.setdefault(m.group(1), set())
            # Ordered, because a rename is an add of one name and a drop of another:
            # migration 024 moves `pengumuman.school_id` through `school_id_new`, and
            # reading only the adds left that temporary column looking like part of
            # the schema — reported as a column the database is missing, when in fact
            # it was dropped the moment the migration finished.
            events = [(a.start(), "add", a.group(1)) for a in
                      re.finditer(r"ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?\"?([\w-]+)\"?",
                                  statement, re.I)]
            events += [(d.start(), "drop", d.group(1)) for d in
                       re.finditer(r"DROP\s+COLUMN\s+(?:IF\s+EXISTS\s+)?\"?([\w-]+)\"?",
                                   statement, re.I)]
            for _, action, column in sorted(events, key=lambda e: e[0]):
                if action == "add":
                    cols.add(column)
                else:
                    cols.discard(column)
    return tables


# ── what the code names ─────────────────────────────────────────────────────

def columns_of(select: str, table: str, out: list[dict], site: str = ""):
    """Record every column and embedded table a select string names.

    `site` is the file and line the select was written on. Without it a finding
    reads "select of profiles" and sends the reader looking through the tree;
    with it, the message names the one place to open — which is the difference
    between a report and a to-do.
    """
    if not select.strip() or select.strip() == "*":
        return
    tail = f" ({site})" if site else ""
    for part in split_sql_list(select):
        part = HINT.sub("", part).strip()
        if not part or part == "*" or part == "count" or part.endswith(".count"):
            continue
        if "(" in part:
            name = part.split("(", 1)[0].strip()
            alias = ALIAS.match(name)
            name = (alias.group(2) if alias else name).strip()
            out.append({"kind": "table", "name": name, "owner": None,
                        "where": f"embed in a select of {table}{tail}"})
            columns_of(part[part.index("(") + 1: part.rindex(")")], name, out, site)
            continue
        alias = ALIAS.match(part)
        out.append({"kind": "column", "name": (alias.group(2) if alias else part).strip(),
                    "owner": table, "where": f"select of {table}{tail}"})


def references(directory: pathlib.Path = ROOT) -> list[dict]:
    """Every table and column the Python code names, with where it named it."""
    out: list[dict] = []
    files: list[pathlib.Path] = []
    for entry in SCAN:
        p = directory / entry
        if p.is_dir():
            files.extend(sorted(p.rglob("*.py")))
        elif p.exists():
            files.append(p)
    for path in files:
        if any(part in (".venv", "__pycache__", "tests", "node_modules") for part in path.parts):
            continue
        where = path.relative_to(directory).as_posix()
        src = path.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"""\.table\(\s*["']([\w-]+)["']""", src):
            out.append({"kind": "table", "name": m.group(1), "owner": None,
                        "where": f"{where}:{src[:m.start()].count(chr(10)) + 1}"})
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            chain, cur = [], node
            while isinstance(cur, ast.Call) and isinstance(cur.func, ast.Attribute):
                chain.append((cur.func.attr, cur.args))
                cur = cur.func.value
            chain.reverse()
            table = None
            site = f"{where}:{getattr(node, 'lineno', 0)}"
            for attr, args in chain:
                const = args[0].value if args and isinstance(args[0], ast.Constant) else None
                if attr == "table" and isinstance(const, str):
                    table = const
                elif table and attr == "select" and isinstance(const, str):
                    columns_of(const, table, out, site)
                elif table and attr in FILTERS and isinstance(const, str):
                    out.append({"kind": "column", "name": const, "owner": table,
                                "where": f"filter on {table} ({where})"})
                elif table and attr == "order" and isinstance(const, str):
                    out.append({"kind": "column", "name": const.split(".")[0], "owner": table,
                                "where": f"order {table} ({where})"})
                elif table and attr in ("insert", "update", "upsert") and args \
                        and isinstance(args[0], ast.Dict):
                    for k in args[0].keys:
                        if isinstance(k, ast.Constant) and isinstance(k.value, str):
                            out.append({"kind": "column", "name": k.value, "owner": table,
                                        "where": f"write into {table} ({where})"})
    return out


# ── the verdict ─────────────────────────────────────────────────────────────

def findings(schema: dict[str, set[str]], refs) -> list[dict]:
    """References no migration creates, one per name, with the first place it is named."""
    known = set(schema)
    problems: dict[tuple[str, str], dict] = {}
    for ref in refs:
        kind, name, owner = ref["kind"], ref["name"], ref["owner"]
        bad = False
        if kind == "table":
            bad = name not in known
        elif name:
            if owner:
                bad = owner in known and name not in schema[owner]
            else:
                bad = not any(name in cols for cols in schema.values())
        if bad:
            problems.setdefault((kind, name), ref)
    return [problems[k] for k in sorted(problems)]


# ── the other half: who the policy lets in ──────────────────────────────────

#: An expression that cannot be true without a session. A policy that mentions one of
#: these is scoped by *who is asking*; a policy that mentions none is scoped only by
#: the row, so `anon` — the public key, shipped in every page — gets the same answer
#: as a signed-in user.
SESSIONLESS = re.compile(r"auth\.uid\(\)|auth\.jwt\(\)|auth\.role\(\)|"
                         r"_is_role\s*\(|_user_school_id\s*\(", re.I)

POLICY = re.compile(r"CREATE\s+POLICY\s+\"([^\"]+)\"\s+ON\s+(?:public\.)?\"?([\w-]+)\"?\s*"
                    r"([^;]*);", re.I | re.S)
VIEW_CREATE = re.compile(r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:MATERIALIZED\s+)?VIEW\s+"
                         r"(?:public\.)?\"?([\w-]+)\"?", re.I)
VIEW_INVOKER = re.compile(r"ALTER\s+VIEW\s+(?:public\.)?\"?([\w-]+)\"?\s+SET\s*\([^)]*?"
                          r"security_invoker\s*=\s*(?:true|on)", re.I | re.S)
DROP_POLICY = re.compile(r"DROP\s+POLICY\s+(?:IF\s+EXISTS\s+)?\"([^\"]+)\"\s+ON\s+"
                         r"(?:public\.)?\"?([\w-]+)\"?", re.I)


def open_policies(directory: pathlib.Path | None = None) -> list[dict]:
    """Policies no session is needed to satisfy, and views that skip RLS.

    Both are the same defect seen from two sides. A policy written without a `TO`
    clause applies to `PUBLIC`; that is harmless when the expression itself asks for a
    session (`auth.uid() = id`), and a hole when it does not
    (`USING (status = 'active' AND is_published = TRUE)` handed the anonymous key every
    published exam's answer key). A view is the same question again: without
    `security_invoker` it runs as its owner and every policy underneath is skipped.

    State, not history. Every `DROP POLICY` and every `CREATE POLICY` is applied in
    file order, and the verdict is the policies that *survive* — otherwise migration
    028 could close a hole and the check would report it for ever, which is how a gate
    gets switched off. Drops and creates inside one file are ordered by position, so
    the `DROP POLICY IF EXISTS … ; CREATE POLICY …` pair every migration here writes
    ends with the policy present.

    The order is the order `sql_files()` returns: the base schema, the combined setup
    file that predates the numbered migrations, then the migrations by name. That is
    the order they were applied in, and the one an operator pasting them would follow.
    """
    problems: list[dict] = []
    state: dict[tuple[str, str], dict] = {}
    views: dict[str, str] = {}
    invoker: set[str] = set()
    for path in sql_files(directory):
        text = path.read_text(encoding="utf-8", errors="replace")
        stripped = re.sub(r"--[^\n]*", " ", text)
        where = path.name
        for m in VIEW_CREATE.finditer(stripped):
            views.setdefault(m.group(1), where)
        invoker.update(VIEW_INVOKER.findall(stripped))
        events: list[tuple[int, str, tuple[str, str], str, int]] = []
        for m in DROP_POLICY.finditer(stripped):
            events.append((m.start(), "drop", (m.group(2), m.group(1)), "",
                           stripped[:m.start()].count("\n") + 1))
        for m in POLICY.finditer(stripped):
            events.append((m.start(), "create", (m.group(2), m.group(1)), m.group(3),
                           stripped[:m.start()].count("\n") + 1))
        for _, action, key, body, line in sorted(events, key=lambda e: e[0]):
            if action == "drop":
                state.pop(key, None)
                continue
            open_to_public = not (re.search(r"\bTO\b", body, re.I)
                                  or SESSIONLESS.search(body))
            state[key] = {"kind": "policy", "name": f"{key[0]}.{key[1]}", "owner": key[0],
                          "where": f"{where}:{line}",
                          "body": " ".join(body.split())[:90]} if open_to_public else None
    problems.extend(v for v in state.values() if v)
    for name, where in sorted(views.items()):
        if name not in invoker:
            problems.append({"kind": "view", "name": name, "owner": None,
                             "where": where, "body": "no security_invoker"})
    return sorted(problems, key=lambda p: (p["kind"], p["name"]))


# ── a statement that cannot run, which no name comparison can see ───────────

#: A table this repository actually *brings into being*. `ALTER TABLE` alone does not
#: count: that is how `activation_codes` looked like a table for as long as nothing
#: asked whether anything created it.
CREATES_TABLE = re.compile(r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:MATERIALIZED\s+)?"
                           r"(?:TABLE|VIEW)\s+(?:IF\s+NOT\s+EXISTS\s+)?"
                           r"(?:public\.)?\"?([\w-]+)\"?", re.I)
#: Statements that name a table and fail outright when it is not there. The schema
#: prefix is captured because `storage.objects` lives in Supabase's own schema, which
#: this repository does not create and must not be asked to.
NEEDS_TABLE = re.compile(r"(?:ALTER|DROP)\s+TABLE\s+(?:IF\s+EXISTS\s+)?"
                         r"(?:(?P<ts>[\w-]+)\.)?\"?(?P<t1>[\w-]+)\"?"
                         r"|(?:CREATE|DROP)\s+POLICY\s+(?:IF\s+EXISTS\s+)?\"[^\"]+\"\s+ON\s+"
                         r"(?:(?P<ps>[\w-]+)\.)?\"?(?P<t2>[\w-]+)\"?", re.I)
#: ``001_enable_rls_and_policies.sql`` wrote exactly this around its own ALTER, which
#: is how its author said out loud that the table may not exist. A statement inside
#: such a block is optional by construction; one outside it is mandatory. (`\$\$` is
#: followed by a space in every one of them, so a `\b` after it matches nothing — the
#: first version of this read a guarded statement as unguarded.)
GUARD = re.compile(r"DO\s+\$\$.*?EXCEPTION\s+WHEN\s+undefined_table.*?END\s*\$\$",
                   re.I | re.S)


def unrunnable(directory: pathlib.Path | None = None) -> list[dict]:
    """Statements that name a table nothing in this repository creates, unguarded.

    `20260608_fix_rls_policies.sql` began section 6 with
    ``ALTER TABLE activation_codes ENABLE ROW LEVEL SECURITY;`` and production does
    not have that table — the app reads `registration_codes`, and no file here has
    ever created the other one. So the **first** statement of the file failed and
    everything after it never ran: sections 1-5 and 7-9, including the
    `teacher_ai_keys.school_id` column and sixteen policies, were never applied, and
    nothing anywhere said so. `001_enable_rls_and_policies.sql` had already met the
    same table and wrapped its own ALTER in ``EXCEPTION WHEN undefined_table``, which
    is the difference between a migration that runs and one that cannot.

    This is the one failure mode a name comparison is blind to — every name resolves;
    the file simply stops. A guard counts only when it *wraps* the statement, so the
    exception handler has to be found in a ``DO $$`` block containing its offset.
    """
    created: set[str] = set()
    problems: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for path in sql_files(directory):
        text = path.read_text(encoding="utf-8", errors="replace")
        stripped = re.sub(r"--[^\n]*", " ", text)
        created.update(m.group(1) for m in CREATES_TABLE.finditer(stripped))
        guarded = [m.span() for m in GUARD.finditer(stripped)]
        for m in NEEDS_TABLE.finditer(stripped):
            # A statement in someone else's schema — `storage.objects` — is not this
            # repository's table to create, so it is not this check's business.
            if (m.group("ts") or m.group("ps")) not in (None, "public"):
                continue
            table = m.group("t1") or m.group("t2")
            if any(a <= m.start() < b for a, b in guarded):
                continue
            if (path.name, table) in seen:
                continue
            seen.add((path.name, table))
            problems.append({
                "kind": "statement", "name": table, "owner": None,
                "statement": " ".join(m.group(0).split())[:60],
                "where": f"{path.name}:{stripped[:m.start()].count(chr(10)) + 1}",
            })
    for problem in problems:
        if problem["name"] in created:
            problem["late"] = True
    # A table a *later* file creates is not a defect: migrations are applied in the
    # order `sql_files()` returns, but `CREATE TABLE` in a numbered migration after a
    # hand-written file is still a table this repository declares.
    return sorted([p for p in problems if not p.get("late")],
                  key=lambda p: (p["where"], p["name"]))


# ── the role vocabulary, which is also a database fact ──────────────────────

ROLE_CHECK = re.compile(r"CHECK\s*\(\s*role\s+IN\s*\(([^)]*)\)\s*\)", re.I)
#: `user_role == 'x'`, `role != 'x'`, `role in ('x', 'y')` — the shapes this app
#: writes. Deliberately blind to a parameter called `role` that is not a database role
#: (a named argument, `role=`), because a checker that guesses gets switched off.
ROLE_LITERAL = re.compile(
    r"\b(?:user_role|role)\s*(==|!=|in)\s*(\([^)]*\)|'[\w_]*'|\"[\w_]*\")")


def role_vocabulary(directory: pathlib.Path | None = None) -> set[str]:
    """The role names the repository's own CHECK constraint allows — last one wins.

    `schema.sql` still carries the original `('student', 'teacher', 'admin')` and
    migration 007 replaces it with `('super_admin', 'admin_sekolah', 'guru', 'murid')`.
    Reading files in the order they were applied and keeping the last constraint is
    what makes the answer "the vocabulary in use now" rather than a union that would
    excuse a legacy name by finding it in the schema it replaced.
    """
    vocab: set[str] = set()
    for path in sql_files(directory):
        text = re.sub(r"--[^\n]*", " ", path.read_text(encoding="utf-8", errors="replace"))
        for m in ROLE_CHECK.finditer(text):
            names = {n.strip().strip("'\"") for n in m.group(1).split(",")}
            if names:
                vocab = {n for n in names if n}
    return vocab


def role_mismatches(vocab: set[str], app: pathlib.Path | None = None) -> list[dict]:
    """Roles the code compares against that the database will never contain.

    The live database holds exactly `guru`, `murid`, `admin_sekolah` and
    `super_admin`; `teacher`, `student` and `admin` were migrated away by 007. A
    comparison against one of those is not a typo the user sees — it is a branch that
    never runs, so the guard it was written for is not there.
    """
    if not vocab:
        return []
    root = app or (ROOT / "app")
    out: list[dict] = []
    for path in sorted(root.rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in ROLE_LITERAL.finditer(text):
            for name in re.findall(r"['\"]([\w_]+)['\"]", m.group(2)):
                if name not in vocab:
                    # Relative to the tree being scanned, which is not always this
                    # repository — the tests point the scan at a temporary copy.
                    try:
                        shown = path.relative_to(root.parent).as_posix()
                    except ValueError:
                        shown = path.as_posix()
                    out.append({
                        "kind": "role", "name": name, "owner": None,
                        "where": f"{shown}:{text[:m.start()].count(chr(10)) + 1}",
                        "body": " ".join(m.group(0).split())[:70]})
    seen = set()
    return [p for p in out if not (p["where"] in seen or seen.add(p["where"]))]


def live_schema() -> dict[str, set[str]] | None:
    """{table: {column, …}} as PostgREST serves it, or None without credentials.

    This is the API's own description of the database — the same document a client
    sees at `/rest/v1/` — so comparing the code with it is comparing the code with
    **the database as the API exposes it**, which is the only version of it any
    query here can reach.
    """
    url = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_SECRET_KEY") or ""
    if not (url and key):
        return None
    try:
        import requests
    except ImportError:                                # pragma: no cover
        return None
    spec = requests.get(f"{url}/rest/v1/",
                        headers={"apikey": key, "Authorization": f"Bearer {key}"},
                        timeout=60).json()
    defs = spec.get("definitions") or spec.get("components", {}).get("schemas") or {}
    return {name: set((body.get("properties") or {}).keys()) for name, body in defs.items()}


def live_differences(schema: dict[str, set[str]], live: dict[str, set[str]]) -> tuple[list, list]:
    """(declared but not in the API, in the API but no SQL declares it).

    The first list is a migration waiting to be pasted. The second is the honest
    one: production has an object no file here creates, which is how four columns
    and two views were found — they were added by hand in the SQL Editor and a
    database rebuilt from this repository would not have them.
    """
    pending, undeclared = [], []
    for table in sorted(schema):
        if table not in live:
            pending.append(f"table {table}")
            continue
        pending.extend(f"{table}.{col}" for col in sorted(schema[table] - live[table]))
    for table in sorted(live):
        if table not in schema:
            undeclared.append(f"table {table}")
            continue
        undeclared.extend(f"{table}.{col}" for col in sorted(live[table] - schema[table]))
    return pending, undeclared


#: Objects a caller with no session is *meant* to read. Empty on purpose: every page
#: in this app is rendered server-side with the service key, so nothing in the API
#: needs to be readable with the public key. Adding a name here is a deliberate
#: statement that the data is public, and `tests/unit/test_schema_contract.py` pins
#: the list so it cannot grow by accident.
PUBLIC_BY_DESIGN: frozenset[str] = frozenset()


#: The keys a browser can hold. Both are in circulation: the legacy JWT anon key and
#: the newer publishable key, which Supabase documents as safe to embed in a page.
#: Whatever is embedded is public, so both are probed rather than the one this
#: repository happens to read.
PUBLIC_KEYS = ("SUPABASE_ANON_KEY", "SUPABASE_PUBLISHABLE_KEY")


def probe_anon(timeout: int = 45) -> tuple[list[tuple[str, str, str]], set[str], str] | None:
    """Ask the API for one row of every object, with a public key and no session.

    This is the measurement that found the answer keys. It is not a static check and it
    is not part of the deploy gate — it touches every table — but it is the only thing
    that can answer "is this policy open?" from outside, so it is here to be run after
    a migration.

    The catalogue of objects comes from the **service** key, because the API's own
    description is service-only: asked with the anon key, `/rest/v1/` answers 401
    "Only the `service_role` API key can be used for this endpoint" — which is how the
    first version of this probe reported a confident `0 of 0 objects` and called the
    database clean.

    **Reads only, on purpose.** The same measurement has a write half — `POST {}` with
    `Prefer: return=minimal` — and it was taken by hand once: an anonymous caller
    reached `violation_logs`, `audit_logs`, `classes` and `schools`, all four answering
    400/23502 (a not-null violation) rather than 401 the way a refused policy answers,
    which is to say the insert *passed* row-level security and failed on the payload.
    That is worse than the read leak, because a forged violation is the record the
    penalty is computed from. It is not automated here because a probe cannot know
    which tables have every column defaulted, and on one of those `POST {}` **creates a
    row** in a customer's production database.

    Returns `([(object, key name, count)], {all objects seen}, error)` or None without
    credentials.
    """
    url = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
    keys = [(name, os.environ[name]) for name in PUBLIC_KEYS if os.environ.get(name)]
    if not url or not keys:
        return None
    try:
        import requests
    except ImportError:                                # pragma: no cover
        return None
    catalogue = live_schema()
    if not catalogue:
        return [], set(), "could not read the API description with the service key"
    leaked: list[tuple[str, str, str]] = []
    for key_name, key in keys:
        headers = {"apikey": key, "Authorization": f"Bearer {key}"}
        for name in sorted(catalogue):
            path = name if re.fullmatch(r"[a-z_][\w]*", name) else f'"{name}"'
            try:
                r = requests.get(f"{url}/rest/v1/{path}",
                                 headers={**headers, "Prefer": "count=exact"},
                                 params={"select": "*", "limit": "1"}, timeout=timeout)
            except Exception:                          # noqa: BLE001
                continue
            if r.status_code >= 400:
                continue
            try:
                rows = r.json()
            except ValueError:
                continue
            if not rows:
                continue
            count = (r.headers.get("content-range") or "/*").split("/")[-1]
            leaked.append((name, key_name, count))
    return leaked, set(catalogue), ""


def main(argv: list[str]) -> int:
    # A developer's own `.env` is what makes `--live` answerable locally; the offline
    # verdict never depends on it, and a missing file is not an error.
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:                                # pragma: no cover
        pass
    schema = schema_from_migrations()
    if not schema:
        print(f"schema contract: cannot measure  -  no SQL found in {MIGRATIONS}")
        return 2
    refs = references()
    problems = findings(schema, refs)
    open_pols = open_policies()
    vocab = role_vocabulary()
    role_problems = role_mismatches(vocab)
    unrunnable_stmts = unrunnable()
    print(f"schema contract: {len(schema)} table(s) in the migrations, "
          f"{len(refs)} reference(s) in the code, "
          f"{len(open_pols)} policy/view open to PUBLIC, "
          f"{len(unrunnable_stmts)} statement(s) on a table nothing creates, "
          f"{len(vocab)} role(s) in the CHECK constraint")

    if "--json" in argv:
        path = pathlib.Path(argv[argv.index("--json") + 1])
        path.write_text(json.dumps({"tables": {k: sorted(v) for k, v in schema.items()},
                                    "problems": problems}, indent=1), encoding="utf-8")
        print("wrote", path)

    if problems or open_pols or role_problems or unrunnable_stmts:
        for ref in problems[:60]:
            print(f"  {ref['kind']} `{ref['name']}`  -  {ref['where']}")
        if len(problems) > 60:
            print(f"  ... and {len(problems) - 60} more")
        for ref in open_pols[:60]:
            print(f"  {ref['kind']} `{ref['name']}` needs no session  -  {ref['where']}")
            if ref["body"]:
                print(f"      {ref['body']}")
        if len(open_pols) > 60:
            print(f"  ... and {len(open_pols) - 60} more")
        for ref in role_problems:
            print(f"  {ref['kind']} '{ref['name']}' is not in ({', '.join(sorted(vocab))}) "
                  f"- {ref['where']}  [{ref['body']}]")
        for ref in unrunnable_stmts:
            print(f"  {ref['statement']}  -  {ref['where']}: nothing in this repository "
                  f"creates `{ref['name']}`")
        if problems:
            print(f"\nschema contract: FAILED  -  {len(problems)} name(s) no SQL in this "
                  "repository creates. At runtime PostgREST refuses the request "
                  "(PGRST205 / 42703) and a `try/except` turns that into an empty page; "
                  "add the migration, or stop naming it.")
        if open_pols:
            print(f"schema contract: FAILED - {len(open_pols)} policy/view any caller "
                  "can satisfy without a session. The anon key is public, so this is "
                  "readable (or writable) by anyone; scope it with `TO authenticated` "
                  "and a predicate that names the caller.")
        if role_problems:
            print(f"schema contract: FAILED - {len(role_problems)} role(s) compared "
                  "against a name the database cannot hold. The branch never runs, so "
                  "the guard written around it is not there.")
        if unrunnable_stmts:
            print(f"schema contract: FAILED - {len(unrunnable_stmts)} statement(s) name "
                  "a table no file here creates. This is the failure a name comparison "
                  "cannot see: every name resolves and the migration still stops at "
                  "that line, so nothing after it is ever applied. Write the migration "
                  "that creates the table, or wrap the statement in "
                  "`DO $$ … EXCEPTION WHEN undefined_table … END $$` the way "
                  "001_enable_rls_and_policies.sql does.")
        return 1

    if "--anon" in argv:
        probed = probe_anon()
        if probed is None:
            print("schema contract: cannot measure - set SUPABASE_URL and "
                  "SUPABASE_ANON_KEY to ask the API what it serves without a session")
            return 2
        leaked, all_objects, error = probed
        if error:
            print(f"schema contract: cannot measure - {error}")
            return 2
        unexpected = [row for row in leaked if row[0] not in PUBLIC_BY_DESIGN]
        print(f"public key, no session: {len(unexpected)} answer(s) from "
              f"{len(all_objects)} object(s) x {len(PUBLIC_KEYS)} key(s)")
        for name, key_name, count in unexpected:
            print(f"  {name:34s} count={count:>6s}  via {key_name}")
        if PUBLIC_BY_DESIGN:
            print(f"public by design, not counted: {', '.join(sorted(PUBLIC_BY_DESIGN))}")
        if unexpected:
            print("\nschema contract: FAILED - the anon key is public (it ships in every "
                  "page), so a row these objects return is a row anyone can read. A "
                  "policy with no `TO` clause applies to PUBLIC; scope it with `TO "
                  "authenticated` and a predicate that names the caller.")
            return 1
        print("schema contract: OK - no object answers without a session")
        return 0

    if "--live" in argv:
        live = live_schema()
        if live is None:
            print("schema contract: OK (offline)  -  set SUPABASE_URL and "
                  "SUPABASE_SERVICE_KEY to also ask the database")
            return 0
        pending, undeclared = live_differences(schema, live)
        print(f"the API serves {len(live)} object(s)")
        if pending:
            print(f"declared but not in the API yet (pending migrations): {len(pending)}")
            for name in pending[:40]:
                print(f"  - {name}")
            if len(pending) > 40:
                print(f"  ... and {len(pending) - 40} more")
        if undeclared:
            print(f"in the API but no SQL in this repository declares it: {len(undeclared)}")
            for name in undeclared[:40]:
                print(f"  + {name}")
            if len(undeclared) > 40:
                print(f"  ... and {len(undeclared) - 40} more")
            print("\nschema contract: FAILED  -  the database has objects no file here creates. "
                  "Either write the migration that declares them, or stop serving them; "
                  "a database rebuilt from this repository must match the one in use.")
            return 1
    print("schema contract: OK  -  every table and column the code names is created by a "
          "migration, and every policy names the caller")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main(sys.argv[1:]))
