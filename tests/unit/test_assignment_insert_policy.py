"""INSERT on `teacher_assignments` is admin-only (migration 078).

The exam-build gate trusts every active row, so the old \"Teachers insert own
assignments\" policy was a self-grant: a signed-in teacher calling PostgREST
directly could attach themselves to any (class, subject) in their school and
then build papers for it. These tests pin the fix in all three places it must
hold: the migration, and both bootstrap lineages (numbered migrations and
`_COMPLETE_SETUP.sql`), plus the admin door that must survive the removal.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "supabase" / "migrations"
M078 = MIGRATIONS / "078_teacher_assignment_insert_admin_only.sql"
RUN_ALL = MIGRATIONS / "_RUN_ALL_AT_ONCE.sql"
COMPLETE_SETUP = ROOT / "supabase" / "_COMPLETE_SETUP.sql"


def test_078_drops_the_self_insert_in_both_lineages():
    sql = M078.read_text(encoding="utf-8")
    assert 'DROP POLICY IF EXISTS "Teachers insert own assignments"' in sql
    assert 'DROP POLICY IF EXISTS "ta_insert_own"' in sql


def test_078_grants_nothing_and_only_drops():
    """The fix narrows a door; a migration that grants here would reopen it."""
    sql = M078.read_text(encoding="utf-8")
    assert "CREATE POLICY" not in sql
    assert "FOR INSERT" not in sql
    assert not re.search(r"DROP\s+(TABLE|COLUMN)", sql), "the migration is destructive"
    assert not re.search(r"DELETE\s+FROM", sql, re.I), "the migration deletes rows"


def test_no_bootstrap_file_still_grants_teacher_self_insert():
    for path in (RUN_ALL, COMPLETE_SETUP):
        text = path.read_text(encoding="utf-8")
        assert "ta_insert_own" not in text, f"{path.name} still carries the self-insert policy"
        assert '"Teachers insert own assignments"' not in text, (
            f"{path.name} still carries the self-insert policy")


def test_admins_keep_an_insert_door_in_both_lineages():
    """Dropping the teacher policy must not lock the admin matrix out.

    Both remaining policies are FOR ALL without WITH CHECK, which is what makes
    them cover INSERT too (USING doubles as the check on write).
    """
    run_all = RUN_ALL.read_text(encoding="utf-8")
    assert '"Admin sekolah manage assignments" ON teacher_assignments' in run_all
    setup = COMPLETE_SETUP.read_text(encoding="utf-8")
    assert '"ta_admin_all" ON teacher_assignments' in setup
    assert "ta_admin_all" in setup and "FOR ALL" in setup
