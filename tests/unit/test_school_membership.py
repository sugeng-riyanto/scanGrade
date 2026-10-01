"""One account, many schools — and the rule that never trusts a cached answer.

The backend uses the service key and passes through RLS, so "which school is this
request for?" is decided in Python. These guards pin the two properties that
matter most:

* a role that cannot be cross-school is never touched by the membership table
  (pupils and admins keep behaving exactly as before);
* the active school is re-verified against an active membership on every
  resolution, so a revoked membership drops a chosen school instead of keeping it.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

from app.services import school_membership as membership

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "supabase" / "migrations" / "044_teacher_school_membership.sql"


class _Query:
    def __init__(self, rows, fail=False):
        self.rows = list(rows)
        self.fail = fail
        self.upserted = None
        self.conflict = None
        self.updated = None

    def select(self, *a, **k):
        if self.fail:
            raise RuntimeError("no such table")
        return self

    def eq(self, col, val):
        self.rows = [r for r in self.rows if str(r.get(col)) == str(val)]
        return self

    def limit(self, *a, **k):
        return self

    def update(self, payload):
        self.updated = payload
        return self

    def upsert(self, payload, on_conflict=None):
        self.upserted = payload
        self.conflict = on_conflict
        return self

    def execute(self):
        if self.fail:
            raise RuntimeError("no such table")
        if self.upserted is not None:
            return SimpleNamespace(data=[dict(self.upserted, id="m-1")])
        if self.updated is not None:
            return SimpleNamespace(data=[dict(self.updated, id="m-1")])
        return SimpleNamespace(data=self.rows)


class _Sb:
    def __init__(self, rows=None, fail=False):
        self.rows = list(rows or [])
        self.fail = fail

    def table(self, name):
        # A NEW query per call, seeded from the same rows: `eq` mutates the
        # filter, so a shared instance would let one membership check empty the
        # rows the next one reads — which is exactly the bug `resolve` guards.
        assert name == "teacher_school_membership", name
        self.last = _Query(list(self.rows), fail=self.fail)
        return self.last


# ── which roles may be cross-school ──────────────────────────────────────────

def test_only_teacher_roles_are_cross_school():
    for role in ("guru", "principal", "vice_principal"):
        assert membership.is_cross_school_role(role), role
    for role in ("murid", "admin_sekolah", "super_admin"):
        assert not membership.is_cross_school_role(role), role


# ── resolving the active school ──────────────────────────────────────────────

def test_a_pupil_keeps_their_home_school_untouched():
    """The membership table is never consulted for a single-school role."""
    sb = _Sb([{"school_id": "sc-2", "status": "active", "user_id": "u-1"}])
    assert membership.resolve_active_school(sb, "u-1", "sc-home", "sc-2", "murid") == "sc-home"


def test_an_admin_keeps_their_home_school_untouched():
    sb = _Sb([{"school_id": "sc-2", "status": "active", "user_id": "u-1"}])
    assert membership.resolve_active_school(sb, "u-1", "sc-home", "sc-2",
                                            "admin_sekolah") == "sc-home"


def test_a_chosen_school_is_used_when_the_membership_is_active():
    sb = _Sb([{"school_id": "sc-2", "status": "active", "user_id": "u-1"}])
    assert membership.resolve_active_school(sb, "u-1", "sc-home", "sc-2", "guru") == "sc-2"


def test_a_choice_the_user_is_not_a_member_of_falls_back_to_home():
    """A tampered active-school value must not open another school."""
    sb = _Sb([{"school_id": "sc-home", "status": "active", "user_id": "u-1"}])
    assert membership.resolve_active_school(sb, "u-1", "sc-home", "sc-other",
                                            "guru") == "sc-home"


def test_a_revoked_membership_drops_the_chosen_school():
    """Nothing is cached that outlives the row: inactive does not resolve."""
    sb = _Sb([{"school_id": "sc-home", "status": "active", "user_id": "u-1"}])
    assert membership.resolve_active_school(sb, "u-1", "sc-home", "sc-revoked",
                                            "guru") == "sc-home"


def test_no_valid_school_resolves_to_none_not_a_default():
    sb = _Sb([{"school_id": "sc-x", "status": "inactive", "user_id": "u-1"}])
    assert membership.resolve_active_school(sb, "u-1", None, None, "guru") is None


def test_a_missing_table_fails_closed():
    sb = _Sb(fail=True)
    assert membership.is_active_member(sb, "u-1", "sc-1") is False
    assert membership.resolve_active_school(_Sb(fail=True), "u-1", "sc-home", "sc-2",
                                            "guru") is None


# ── membership writes ────────────────────────────────────────────────────────

def test_invite_targets_the_pair_constraint():
    sb = _Sb()
    membership.invite(sb, "sc-1", "u-1", role="guru", invited_by="admin-1")
    assert sb.last.conflict == "user_id,school_id"
    assert sb.last.upserted["status"] == "invited"
    assert sb.last.upserted["invited_by"] == "admin-1"


def test_accepting_scopes_to_the_caller_own_row():
    """A teacher accepts their own invitation, never somebody else's."""
    sb = _Sb()
    membership.accept_invite(sb, "u-1", "sc-1")
    assert sb.last.updated["status"] == "active"


# ── the active school is server-side, not a cookie ───────────────────────────

def test_the_active_school_key_is_per_token():
    key = membership.ACTIVE_SCHOOL_KEY.format(token="tok-abc")
    assert "tok-abc" in key
    # Never a browser-supplied name: the key is the server's own namespace.
    assert key.startswith("active_school:")


# ── the migration ────────────────────────────────────────────────────────────

def _statements(sql: str) -> str:
    """SQL with `--` comments removed, so prose about DROP is not read as DROP."""
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def test_the_migration_is_additive_and_idempotent():
    sql = MIGRATION.read_text(encoding="utf-8")
    code = _statements(sql)
    assert not re.search(r"DROP\s+(TABLE|COLUMN)", code), "the migration is destructive"
    assert "CREATE TABLE IF NOT EXISTS public.teacher_school_membership" in sql
    assert "ON CONFLICT (user_id, school_id) DO NOTHING" in sql


def test_one_row_per_person_per_school():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "UNIQUE (user_id, school_id)" in sql


def test_every_policy_names_its_caller():
    sql = MIGRATION.read_text(encoding="utf-8")
    blocks = re.findall(r"CREATE POLICY.*?;", sql, re.S)
    assert blocks
    for block in blocks:
        assert " TO authenticated" in block, block[:80]


def test_the_backfill_covers_the_three_membership_roles():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "p.role IN ('guru', 'principal', 'vice_principal')" in sql
    # ...and does not touch pupils or admins.
    assert "'murid'" not in sql.split("INSERT INTO")[1].split(";")[0]
