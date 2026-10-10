"""One writer for membership status — and a guard that keeps it that way.

Migration 063 added `closed` to `teacher_school_membership.status`, plus the four
stamps that make a closure accountable (`closed_by`/`closed_at`, and the two that
reopen it). The status is not one column: it is a *decision* with side effects, and
`closed` in particular is the one that removes a teacher's access to a school
entirely. So the question this file answers is not "does closing work" but **"is
there another road to this column"** — a route remembered from an earlier phase
that still writes `status` by hand would bypass the closure stamps, and a route
that wrote `inactive` while meaning closed would leave the school believing it had
offboarded somebody whose row says something weaker.

What is pinned, and why each part has a wrong version that passes without it
--------------------------------------------------------------------------------
* **The service is the only module that names the table.** This is the gate, not a
  style rule: a membership row cannot be written (or read) without naming
  `teacher_school_membership`, so one assertion covers every writer, present and
  future. It is deliberately stricter than "no route updates the status" — a reader
  in a route is the same drift, one step earlier.
* **The vocabulary is two-way equal to the migration's CHECK.** A value the
  database refuses must not exist in code (it would fail at runtime, mid-request),
  and a value the database accepts must not be invisible to the service (the next
  reader greps the service, finds three of four, and writes the fourth elsewhere).
* **Every status the service writes is one of its own constants.** A literal is how
  the vocabulary drifts: `"closed"` typed at a call site is a value the service does
  not know it can produce, and the guard cannot tell it from a typo.
* **The closure stamps are written in one place.** `close()`/`reopen()` carry both
  halves — the status *and* who/when — so a route that sets the status alone
  produces a row nobody can explain.
* **The writes are transitions, not assignments.** Both are scoped to one (school,
  user) pair and guarded on the state they move *from*, so a double press changes
  no stamp and a reopen can never be the way an ordinary revocation is undone. The
  fake below honours those filters the way Postgres does, so "refused" is an
  observation of the shipped code rather than a reading of it.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from app.services import school_membership as membership

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "app" / "services" / "school_membership.py"
MIGRATION = ROOT / "supabase" / "migrations" / "063_school_membership_requests.sql"
APP = ROOT / "app"

#: The four stamps migration 063 put on the membership row. `closed_at` is the one
#: an unrelated feature could plausibly reuse (a school year can be closed too), so
#: the scan for these is scoped to files that already talk about membership — see
#: `test_no_module_outside_the_service_names_the_closure_stamps`.
CLOSURE_COLUMNS = ("closed_by", "closed_at", "reopened_by", "reopened_at")

TESTED_EXTENSIONS = (".py", ".html", ".js")


def _read(path: Path) -> str:
    # `utf-8-sig`: a route file may open with a BOM and `ast.parse` refuses it.
    return path.read_text(encoding="utf-8-sig")


def _app_files() -> list[Path]:
    return sorted(p for p in APP.rglob("*")
                  if p.suffix in TESTED_EXTENSIONS and "__pycache__" not in p.parts)


# ── 1. one writer: the structural gate ──────────────────────────────────────

def test_only_the_service_names_the_membership_table():
    """Every road to this column runs through the service, by construction.

    A module that does not name the table cannot read a membership row, let alone
    write its status — so this single assertion is what makes "the service is the
    only writer" a property instead of a habit. The failure message names the file
    so the fix is obvious: call the service, do not re-implement it.
    """
    offenders = []
    for path in _app_files():
        if path == SERVICE:
            continue
        if "teacher_school_membership" in _read(path):
            offenders.append(path.relative_to(ROOT).as_posix())
    assert not offenders, (
        "these modules name `teacher_school_membership` directly, so they can change a "
        "membership status without the service that owns its vocabulary and its closure "
        "stamps:\n  " + "\n  ".join(offenders)
        + "\n\nRead or write through `app.services.school_membership` instead.")


def test_the_service_does_not_reach_the_table_from_a_route_file():
    """The other direction of the same rule, by directory rather than by name.

    A service imported into a route is the intended shape; a query built in a route
    is not. This pins that the routes directory carries no query of its own, so a
    route that *imports* the table name through a helper still fails here.
    """
    routes = [p for p in _app_files() if p.parts[-2:-1] == ("routes",)]
    offenders = [p.relative_to(ROOT).as_posix() for p in routes
                 if "teacher_school_membership" in _read(p)]
    assert not offenders, (
        "a route carries a membership query; routes decide *whether*, the service "
        "decides *what*:\n  " + "\n  ".join(offenders))


def test_no_module_outside_the_service_names_the_closure_stamps():
    """`closed_by` and friends belong to the one module that writes `closed`.

    Scoped to files that mention membership at all, deliberately: `closed_at` is a
    plausible name for an unrelated feature (a school year closes too), and a guard
    that fires on a school-year change is a guard its next reader deletes.
    """
    offenders = []
    for path in _app_files():
        if path == SERVICE:
            continue
        text = _read(path)
        if not re.search(r"member", text, re.I):
            continue
        for column in CLOSURE_COLUMNS:
            if column in text:
                offenders.append(f"{path.relative_to(ROOT).as_posix()}: {column}")
    assert not offenders, (
        "the closure stamps are written outside the service, so a closure can be "
        "recorded without the status that goes with it (or the reverse):\n  "
        + "\n  ".join(offenders))


# ── 2. one vocabulary, equal to the database's ──────────────────────────────

def _migration_vocabulary() -> set[str]:
    """The statuses migration 063's CHECK accepts, read from the SQL itself."""
    sql = MIGRATION.read_text(encoding="utf-8")
    match = re.search(
        r"(?is)ADD CONSTRAINT teacher_school_membership_status_check"
        r".*?CHECK\s*\(\s*status\s+IN\s*\(([^)]*)\)", sql)
    assert match, "the membership status CHECK is no longer in migration 063"
    return set(re.findall(r"'([^']+)'", match.group(1)))


def test_the_service_vocabulary_is_exactly_the_databases():
    """Equal both ways, and that is the whole assertion.

    A subset means a status the service will let you write and the database will
    refuse, at runtime, in the middle of a request. A superset means a status the
    database holds and the service cannot name — which is how the fourth value
    ends up typed at a call site.
    """
    assert set(membership.STATUSES) == _migration_vocabulary(), (
        f"the service says {sorted(membership.STATUSES)} and migration 063 says "
        f"{sorted(_migration_vocabulary())}")


def test_the_vocabulary_is_the_four_the_migration_declares():
    """Named outright, so a migration edited to drop a value fails here too."""
    assert set(membership.STATUSES) == {"active", "invited", "inactive", "closed"}
    assert membership.STATUS_CLOSED == "closed", (
        "offboarding is spelled `closed`; a rename would silently stop matching the "
        "migration that added it")
    assert len(set(membership.STATUSES)) == len(membership.STATUSES), (
        "a duplicated entry means two names for one state")


# ── 3. every write goes through a constant ──────────────────────────────────

def _status_values_in_service() -> list[tuple[int, str]]:
    """Every place the service sets or filters `status` to a **literal**.

    By AST, not by search: a search for `"status"` is satisfied by the column list
    in a `select()` string and by every docstring that mentions the word, which is
    how a check for a literal passes with the literals already removed.
    """
    tree = ast.parse(_read(SERVICE))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if (isinstance(key, ast.Constant) and key.value == "status"
                        and isinstance(value, ast.Constant)):
                    found.append((value.lineno, value.value))
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("eq", "neq", "in_") and len(node.args) > 1):
            first, second = node.args[0], node.args[1]
            if (isinstance(first, ast.Constant) and first.value == "status"
                    and isinstance(second, ast.Constant)):
                found.append((second.lineno, second.value))
    return found


def test_the_service_writes_no_status_literal():
    """The whole point of the constants: one place to change, one place to audit.

    A literal at a call site is a value the vocabulary test above cannot see, and
    the shape this repository has been bitten by before (`status='inactive'` written
    where `closed` was meant).
    """
    found = _status_values_in_service()
    assert not found, (
        "the service spells a status out instead of naming a constant, so the "
        "vocabulary above no longer describes what this module can write:\n  "
        + "\n  ".join(f"line {line}: {value!r}" for line, value in found))


def test_every_constant_the_service_uses_is_declared_once():
    """`STATUSES` is a tuple of the names, not a second list to keep in step."""
    for name, value in ((membership.STATUS_ACTIVE, "active"),
                        (membership.STATUS_INVITED, "invited"),
                        (membership.STATUS_INACTIVE, "inactive"),
                        (membership.STATUS_CLOSED, "closed")):
        assert name == value
        assert membership.STATUSES.count(name) == 1


# ── 4. the closure writes, driven ───────────────────────────────────────────
#
# The rules above are readings of the code. What a school notices is the *row*: a
# teacher who is really closed out, with a stamp that names who did it — and one
# who is not, when the press was repeated or aimed at the wrong state. So the
# queries run against a stand-in PostgREST that honours `eq`/`neq` the way the
# database does, and the assertions are about what the row ended up holding.

class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    """Enough PostgREST for these two writers: filters, one update, and a result."""

    def __init__(self, rows):
        self.rows = list(rows)
        #: `(kind, column, value)` in the order the service built them. Kept as a
        #: list, not a set, because the *order* is part of the guard: the scoping
        #: comes first and the transition guard last.
        self.filters: list[tuple[str, str, object]] = []
        self.payload: dict | None = None

    def select(self, *columns, **kwargs):
        return self

    def update(self, payload):
        self.payload = dict(payload)
        return self

    def eq(self, column, value):
        self.filters.append(("eq", column, value))
        return self

    def neq(self, column, value):
        self.filters.append(("neq", column, value))
        return self

    def limit(self, *a, **k):
        return self

    def _matches(self, row) -> bool:
        for kind, column, value in self.filters:
            hit = str(row.get(column)) == str(value)
            if kind == "eq" and not hit:
                return False
            if kind == "neq" and hit:
                return False
        return True

    def execute(self):
        rows = [row for row in self.rows if self._matches(row)]
        if self.payload is not None:
            for row in rows:
                row.update(self.payload)
        return _Result(rows)


class _Sb:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.tables: list[str] = []
        self.last: _Query | None = None

    def table(self, name):
        self.tables.append(name)
        self.last = _Query(self.rows)
        return self.last


def _row(**over) -> dict:
    row = {"id": "m-1", "user_id": "u-1", "school_id": "sc-1",
           "school_role": "guru", "status": membership.STATUS_ACTIVE}
    row.update(over)
    return row


class TestClosingAMembership:
    def test_it_writes_the_status_and_both_stamps(self):
        row = _row()
        membership.close(_Sb([row]), "sc-1", "u-1", by="admin-1")
        assert row["status"] == membership.STATUS_CLOSED
        assert row["closed_by"] == "admin-1", "a closure that names nobody is not a record"
        assert row["closed_at"], "a closure with no instant cannot be placed in a timeline"
        assert "T" in row["closed_at"], "the stamp is not an ISO instant"

    def test_it_is_scoped_to_the_pair_and_guarded_on_the_state(self):
        """A transition, not an assignment — asserted as the exact query it built."""
        db = _Sb([_row()])
        membership.close(db, "sc-1", "u-1", by="admin-1")
        assert db.tables == ["teacher_school_membership"]
        assert db.last.filters == [
            ("eq", "school_id", "sc-1"),
            ("eq", "user_id", "u-1"),
            ("neq", "status", membership.STATUS_CLOSED),
        ], ("a close must name the row it closes and skip a row already closed, or a "
            "second press rewrites who closed it and when")

    def test_a_second_press_changes_nothing(self):
        """The guard is exercised, not just named: the row keeps its first stamp."""
        row = _row(status=membership.STATUS_CLOSED, closed_by="admin-1",
                   closed_at="2026-01-01T00:00:00+00:00")
        written = membership.close(_Sb([row]), "sc-1", "u-1", by="admin-2")
        assert written == {} and row["closed_by"] == "admin-1"
        assert row["closed_at"] == "2026-01-01T00:00:00+00:00"

    def test_another_schools_row_is_not_touched(self):
        row = _row(school_id="sc-2")
        assert membership.close(_Sb([row]), "sc-1", "u-1", by="admin-1") == {}
        assert row["status"] == membership.STATUS_ACTIVE


class TestReopeningAMembership:
    def test_it_puts_the_membership_back_and_names_the_actor(self):
        row = _row(status=membership.STATUS_CLOSED, closed_by="admin-1",
                   closed_at="2026-01-01T00:00:00+00:00")
        membership.reopen(_Sb([row]), "sc-1", "u-1", by="admin-1")
        assert row["status"] == membership.STATUS_ACTIVE
        assert row["reopened_by"] == "admin-1" and row["reopened_at"]

    def test_it_keeps_the_closure_as_history(self):
        """Erasing the closure would make "was this teacher ever removed?" unanswerable."""
        row = _row(status=membership.STATUS_CLOSED, closed_by="admin-1")
        membership.reopen(_Sb([row]), "sc-1", "u-1", by="admin-1")
        assert row["closed_by"] == "admin-1", (
            "reopening overwrote the record that a closure happened")

    def test_it_only_reverses_a_closure_never_a_revocation(self):
        """`inactive` comes back through the invite flow, not through this door."""
        row = _row(status=membership.STATUS_INACTIVE)
        assert membership.reopen(_Sb([row]), "sc-1", "u-1", by="admin-1") == {}
        assert row["status"] == membership.STATUS_INACTIVE, (
            "reopen turned an ordinary revocation into access")

    def test_the_query_is_the_close_query_mirrored(self):
        db = _Sb([_row(status=membership.STATUS_CLOSED)])
        membership.reopen(db, "sc-1", "u-1", by="admin-1")
        assert db.last.filters == [
            ("eq", "school_id", "sc-1"),
            ("eq", "user_id", "u-1"),
            ("eq", "status", membership.STATUS_CLOSED),
        ]


# ── 5. what `closed` means for access, through the readers that already exist ─

class TestClosedMeansNoAccess:
    def test_the_active_read_excludes_it(self):
        """No new read path: the filter was already `status = active`."""
        db = _Sb([_row(school_id="sc-1", status=membership.STATUS_ACTIVE),
                  _row(school_id="sc-2", status=membership.STATUS_CLOSED)])
        assert membership.member_school_ids(db, "u-1") == {"sc-1"}
        assert membership.is_active_member(db, "u-1", "sc-2") is False
        assert membership.is_active_member(db, "u-1", "sc-1") is True

    def test_a_closed_membership_cannot_be_the_active_school(self):
        """The resolution drops a closed school exactly as it drops a revoked one.

        The home school is still an active membership and the closed one was chosen
        in this session: the answer must be the home school, and `None` when a
        teacher has nothing left that is active — never the closed school.
        """
        db = _Sb([_row(school_id="sc-home", status=membership.STATUS_ACTIVE),
                  _row(school_id="sc-2", status=membership.STATUS_CLOSED)])
        assert membership.resolve_active_school(
            db, "u-1", "sc-home", "sc-2", "guru") == "sc-home"
        assert membership.resolve_active_school(
            db, "u-1", "sc-2", "sc-2", "guru") is None, (
            "a closed school was offered as the active one")
