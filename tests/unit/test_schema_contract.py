"""The code, the SQL this repository carries, and the API must agree.

Every failure this guards against is quiet. A `select` naming a column that does not
exist is refused by PostgREST with 42703 and a `try/except` renders an empty page — an
empty audit log, a roster with nobody on it, a privacy export without the violations.
And a policy written without a `TO` clause applies to `PUBLIC`, so with the anon key
that ships in every page an anonymous caller could read `exams.answer_key` for every
published paper. Both were live in this database when the check was written.

The tests below pin three separate things: that the readers read SQL correctly (a
checker that invents columns teaches its reader to ignore it), that the verdict is the
*state* after the files are applied rather than the *history* of what they once said
(028 closes holes 002 and 007 opened, and a check that cannot see a DROP reports them
for ever), and that the repository as it stands passes.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from deploy import schema_contract as sc  # noqa: E402


# ── the SQL readers ─────────────────────────────────────────────────────────

class TestReadingWhatTheSqlCreates:
    def test_a_create_table_gives_its_columns(self, tmp_path):
        (tmp_path / "001.sql").write_text(
            "CREATE TABLE IF NOT EXISTS profiles (\n"
            "    id UUID PRIMARY KEY,\n"
            "    full_name TEXT,\n"
            "    role TEXT NOT NULL\n"
            ");\n", encoding="utf-8")
        assert sc.schema_from_migrations(tmp_path)["profiles"] == {"id", "full_name", "role"}

    def test_a_table_level_constraint_is_not_a_column(self, tmp_path):
        (tmp_path / "001.sql").write_text(
            "CREATE TABLE t (\n  id UUID,\n  a TEXT,\n"
            "  PRIMARY KEY (id),\n  UNIQUE (a),\n  CONSTRAINT x CHECK (a <> '')\n);\n",
            encoding="utf-8")
        assert sc.schema_from_migrations(tmp_path)["t"] == {"id", "a"}

    def test_a_comma_inside_a_json_default_does_not_split_a_column(self, tmp_path):
        """The defect the live run found: four \"columns\" invented from one default."""
        (tmp_path / "001.sql").write_text(
            "CREATE TABLE teacher_ai_settings (\n"
            "  id UUID,\n"
            "  prompts JSONB DEFAULT '{\"feedback\": {\"range\": true, \"tone\": false}}',\n"
            "  school_id UUID\n"
            ");\n", encoding="utf-8")
        cols = sc.schema_from_migrations(tmp_path)["teacher_ai_settings"]
        assert cols == {"id", "prompts", "school_id"}, cols

    def test_a_hyphenated_table_name_is_read(self, tmp_path):
        (tmp_path / "001.sql").write_text(
            'CREATE TABLE IF NOT EXISTS "school-payment-demo" (\n  id BIGINT,\n  created_at TIMESTAMPTZ\n);\n',
            encoding="utf-8")
        assert "school-payment-demo" in sc.schema_from_migrations(tmp_path)

    def test_alter_adds_every_column_it_names(self, tmp_path):
        (tmp_path / "001.sql").write_text(
            "CREATE TABLE p (id UUID);\n"
            "ALTER TABLE p ADD COLUMN a TEXT, ADD COLUMN IF NOT EXISTS b INT;\n",
            encoding="utf-8")
        assert {"id", "a", "b"} <= sc.schema_from_migrations(tmp_path)["p"]

    def test_a_temporary_rename_column_does_not_survive_the_migration(self, tmp_path):
        """Migration 024 moves `pengumuman.school_id` through `school_id_new`."""
        (tmp_path / "024.sql").write_text(
            "ALTER TABLE pengumuman ADD COLUMN IF NOT EXISTS school_id_new UUID;\n"
            "UPDATE pengumuman SET school_id_new = NULL;\n"
            "ALTER TABLE pengumuman DROP COLUMN IF EXISTS school_id_new;\n",
            encoding="utf-8")
        assert "school_id_new" not in sc.schema_from_migrations(tmp_path)["pengumuman"]

    def test_an_alter_add_is_read_as_a_column(self, tmp_path):
        (tmp_path / "001.sql").write_text(
            "CREATE TABLE exams (id UUID);\nALTER TABLE exams ADD COLUMN tmp_page_new INT;\n",
            encoding="utf-8")
        assert "tmp_page_new" in sc.schema_from_migrations(tmp_path)["exams"]

    def test_a_view_with_a_column_list_is_read_like_a_table(self, tmp_path):
        (tmp_path / "001.sql").write_text(
            "CREATE OR REPLACE VIEW active_school_years\n"
            "    (id, school_id, name, is_active)\n"
            "AS SELECT id, school_id, name, is_active FROM school_years WHERE is_active;\n",
            encoding="utf-8")
        assert sc.schema_from_migrations(tmp_path)["active_school_years"] == {
            "id", "school_id", "name", "is_active"}

    def test_every_source_file_is_opened(self):
        """The 72-name lesson: the base tables are not in `migrations/`."""
        names = {p.name for p in sc.sql_files()}
        assert "schema.sql" in names, "the base tables live here"
        assert "_COMPLETE_SETUP.sql" in names, "the AI and billing tables live here"
        assert "profiles_policies.sql" in names or any(
            p.parent.name == "policies" for p in sc.sql_files())
        assert any(n.startswith("027") for n in names)


# ── the code reader ─────────────────────────────────────────────────────────

class TestReadingWhatTheCodeNames:
    def _refs(self, tmp_path, source):
        (tmp_path / "app").mkdir()
        (tmp_path / "app" / "route.py").write_text(source, encoding="utf-8")
        return sc.references(tmp_path)

    def test_a_select_records_each_column(self, tmp_path):
        refs = self._refs(tmp_path,
                          'def f():\n    return sb.table("exams").select("id, title, answer_key").execute()\n')
        assert {(r["name"], r["owner"]) for r in refs if r["kind"] == "column"} == {
            ("id", "exams"), ("title", "exams"), ("answer_key", "exams")}

    def test_an_embed_records_the_table_and_its_columns(self, tmp_path):
        refs = self._refs(tmp_path,
                          'def f():\n'
                          '    return sb.table("audit_logs").select("*, profiles!inner(full_name, phone)").execute()\n')
        assert ("profiles", None) in {(r["name"], r["owner"]) for r in refs}
        assert ("full_name", "profiles") in {(r["name"], r["owner"]) for r in refs}

    def test_a_select_finding_names_the_file_and_line(self, tmp_path):
        """A report that says \"select of profiles\" sends the reader through the tree."""
        refs = self._refs(tmp_path, 'X = 1\n\n\ndef f():\n    return sb.table("p").select("email").execute()\n')
        site = [r["where"] for r in refs if r["name"] == "email"][0]
        assert "app/route.py:5" in site, site

    def test_filters_order_and_writes_are_recorded(self, tmp_path):
        refs = self._refs(tmp_path,
                          'def f():\n'
                          '    return (sb.table("submissions").select("id").eq("exam_id", "x")\n'
                          '            .order("created_at").insert({"score": 1}).execute())\n')
        assert {(r["name"], r["where"].split()[0]) for r in refs} >= {
            ("id", "select"), ("exam_id", "filter"), ("created_at", "order"), ("score", "write")}

    def test_a_column_on_a_table_no_file_declares_is_not_a_finding(self, tmp_path):
        """Otherwise every embed into an undeclared table would be noise."""
        schema = {"exams": {"id"}}
        refs = [{"kind": "column", "name": "whatever", "owner": "mystery_table", "where": "x"}]
        assert sc.findings(schema, refs) == []

    def test_a_column_the_table_lacks_is_a_finding(self, tmp_path):
        schema = {"profiles": {"id", "full_name"}}
        refs = [{"kind": "column", "name": "email", "owner": "profiles", "where": "app/x.py:9"}]
        found = sc.findings(schema, refs)
        assert len(found) == 1 and found[0]["name"] == "email"


# ── who a policy lets in ────────────────────────────────────────────────────

def _policies(tmp_path, *files):
    for name, body in files:
        (tmp_path / name).write_text(body, encoding="utf-8")
    return sc.open_policies(tmp_path)


class TestPoliciesOpenToAnyone:
    def test_a_true_policy_with_no_to_clause_is_open(self, tmp_path):
        found = _policies(tmp_path, ("001.sql", 'CREATE POLICY "p" ON exams FOR SELECT USING (true);\n'))
        assert [f["name"] for f in found] == ["exams.p"]

    def test_the_measured_answer_key_policy_is_open(self, tmp_path):
        found = _policies(tmp_path, ("001.sql",
                                     'CREATE POLICY "exams_select_active" ON exams FOR SELECT\n'
                                     "    USING (status = 'active' AND is_published = TRUE);\n"))
        assert [f["name"] for f in found] == ["exams.exams_select_active"]

    def test_to_authenticated_closes_it(self, tmp_path):
        found = _policies(tmp_path, ("001.sql",
                                     'CREATE POLICY "p" ON exams FOR SELECT TO authenticated USING (true);\n'))
        assert found == []

    @pytest.mark.parametrize("predicate", [
        "auth.uid() = id", "public._is_role('guru')", "school_id = public._user_school_id()",
        "recipient_id = auth.uid()",
    ])
    def test_a_predicate_that_needs_a_session_closes_it(self, tmp_path, predicate):
        found = _policies(tmp_path, ("001.sql",
                                     f'CREATE POLICY "p" ON exams FOR SELECT USING ({predicate});\n'))
        assert found == [], f"{predicate} should count as needing a session"

    def test_a_later_drop_closes_it(self, tmp_path):
        """State, not history — otherwise 028 can never make this gate pass."""
        found = _policies(
            tmp_path,
            ("001.sql", 'CREATE POLICY "exams_select_active" ON exams FOR SELECT USING (true);\n'),
            ("028.sql", 'DROP POLICY IF EXISTS "exams_select_active" ON exams;\n'),
        )
        assert found == [], "the drop was ignored, so the gate reports a closed hole for ever"

    def test_drop_then_create_in_one_file_ends_with_the_policy_present(self, tmp_path):
        found = _policies(tmp_path, ("028.sql",
                                     'DROP POLICY IF EXISTS "p" ON exams;\n'
                                     'CREATE POLICY "p" ON exams FOR SELECT TO authenticated USING (true);\n'))
        assert found == []

    def test_drop_then_create_still_open_is_reported(self, tmp_path):
        found = _policies(tmp_path, ("028.sql",
                                     'DROP POLICY IF EXISTS "p" ON exams;\n'
                                     'CREATE POLICY "p" ON exams FOR SELECT USING (true);\n'))
        assert [f["name"] for f in found] == ["exams.p"]

    def test_a_recreated_policy_replaces_the_old_verdict(self, tmp_path):
        found = _policies(
            tmp_path,
            ("001.sql", 'CREATE POLICY "p" ON exams FOR SELECT USING (true);\n'),
            ("028.sql", 'DROP POLICY IF EXISTS "p" ON exams;\n'
                        'CREATE POLICY "p" ON exams FOR SELECT USING (false);\n'),
        )
        assert [f["name"] for f in found] == ["exams.p"], "the new body should be what is judged"


class TestViewsThatSkipRowLevelSecurity:
    def test_a_view_without_security_invoker_is_reported(self, tmp_path):
        found = _policies(tmp_path, ("001.sql", "CREATE VIEW v (a) AS SELECT a FROM t;\n"))
        assert [f["kind"] for f in found] == ["view"]

    def test_an_alter_view_in_a_later_file_closes_it(self, tmp_path):
        found = _policies(
            tmp_path,
            ("001.sql", "CREATE VIEW v (a) AS SELECT a FROM t;\n"),
            ("028.sql", "ALTER VIEW v SET (security_invoker = true);\n"),
        )
        assert found == []

    def test_a_view_that_is_never_altered_is_still_reported(self, tmp_path):
        found = _policies(
            tmp_path,
            ("001.sql", "CREATE VIEW v (a) AS SELECT a FROM t;\n"
                        "CREATE VIEW w (b) AS SELECT b FROM t;\n"),
            ("028.sql", "ALTER VIEW v SET (security_invoker = true);\n"),
        )
        assert [f["name"] for f in found] == ["w"]


# ── the role vocabulary is a database fact ──────────────────────────────────

class TestTheRoleVocabulary:
    def test_the_last_check_constraint_wins(self, tmp_path):
        """`schema.sql` says student/teacher/admin; 007 replaces it with the four in use.
        A union would excuse a legacy name by finding it in the constraint it replaced."""
        (tmp_path / "schema.sql").write_text(
            "role TEXT CHECK (role IN ('student', 'teacher', 'admin'))", encoding="utf-8")
        (tmp_path / "007_multi.sql").write_text(
            "ALTER TABLE profiles ADD CONSTRAINT profiles_role_check\n"
            "    CHECK (role IN ('super_admin', 'admin_sekolah', 'guru', 'murid'));\n",
            encoding="utf-8")
        assert sc.role_vocabulary(tmp_path) == {"super_admin", "admin_sekolah", "guru", "murid"}

    def test_a_comparison_against_a_legacy_name_is_found(self, tmp_path):
        src = tmp_path / "app"
        src.mkdir()
        (src / "x.py").write_text('if user_role == "admin":\n    pass\n', encoding="utf-8")
        found = sc.role_mismatches({"guru", "murid"}, src)
        assert [f["name"] for f in found] == ["admin"]

    def test_a_comparison_against_a_live_name_is_not(self, tmp_path):
        src = tmp_path / "app"
        src.mkdir()
        (src / "x.py").write_text(
            'if user_role == "guru":\n    pass\n'
            'if role in ("admin_sekolah", "super_admin"):\n    pass\n'
            'if role != "murid":\n    pass\n', encoding="utf-8")
        assert sc.role_mismatches({"guru", "murid", "admin_sekolah", "super_admin"}, src) == []

    def test_this_repository_only_compares_against_roles_the_database_has(self):
        vocab = sc.role_vocabulary()
        assert vocab == {"super_admin", "admin_sekolah", "guru", "murid"}, vocab
        assert sc.role_mismatches(vocab) == []

    def test_without_a_constraint_there_is_nothing_to_judge(self, tmp_path):
        (tmp_path / "001.sql").write_text("CREATE TABLE t (id UUID);\n", encoding="utf-8")
        assert sc.role_vocabulary(tmp_path) == set()
        assert sc.role_mismatches(set(), tmp_path) == []


# ── the two directions of drift ─────────────────────────────────────────────

class TestStatementsThatCannotRun:
    """The one failure a name comparison is blind to: the file simply stops.

    `20260608_fix_rls_policies.sql` opened section 6 with
    `ALTER TABLE activation_codes …` and production has no such table — the app reads
    `registration_codes`. So its **first** statement failed and everything after it was
    never applied, including the `teacher_ai_keys.school_id` column and sixteen
    policies, while the file looked applied to anyone reading names. `001` had already
    met the same table and wrapped its own ALTER in `EXCEPTION WHEN undefined_table`:
    that guard is the difference between a migration that runs and one that cannot.
    """

    def test_a_statement_on_a_table_nothing_creates_is_reported(self, tmp_path):
        (tmp_path / "a.sql").write_text(
            "CREATE TABLE real_table (id UUID);\n"
            'CREATE POLICY "p" ON real_table FOR SELECT USING (true);\n', encoding="utf-8")
        (tmp_path / "b.sql").write_text(
            "ALTER TABLE ghost_table ENABLE ROW LEVEL SECURITY;\n", encoding="utf-8")
        found = sc.unrunnable(tmp_path)
        assert [f["name"] for f in found] == ["ghost_table"]
        assert found[0]["where"].startswith("b.sql:"), found[0]["where"]
        assert "ALTER TABLE ghost_table" in found[0]["statement"]

    def test_a_policy_on_a_table_nothing_creates_is_reported(self, tmp_path):
        (tmp_path / "a.sql").write_text(
            'CREATE POLICY "p" ON ghost_table FOR SELECT USING (true);\n', encoding="utf-8")
        assert [f["name"] for f in sc.unrunnable(tmp_path)] == ["ghost_table"]

    def test_the_guard_001_already_uses_makes_it_optional(self, tmp_path):
        (tmp_path / "a.sql").write_text(
            "DO $$ BEGIN\n"
            "  ALTER TABLE ghost_table ENABLE ROW LEVEL SECURITY;\n"
            "EXCEPTION WHEN undefined_table THEN NULL;\n"
            "END $$;\n", encoding="utf-8")
        assert sc.unrunnable(tmp_path) == []

    def test_a_table_another_file_creates_is_not_a_defect(self, tmp_path):
        (tmp_path / "b.sql").write_text(
            "ALTER TABLE late_table ADD COLUMN x INT;\n", encoding="utf-8")
        (tmp_path / "c.sql").write_text(
            "CREATE TABLE late_table (id UUID);\n", encoding="utf-8")
        assert sc.unrunnable(tmp_path) == []

    def test_a_table_in_someone_elses_schema_is_not_ours_to_create(self, tmp_path):
        (tmp_path / "a.sql").write_text(
            'CREATE POLICY "p" ON storage.objects FOR SELECT USING (true);\n',
            encoding="utf-8")
        assert sc.unrunnable(tmp_path) == []

    def test_a_table_named_only_in_a_comment_is_not_a_statement(self, tmp_path):
        (tmp_path / "a.sql").write_text(
            "-- ALTER TABLE ghost_table ENABLE ROW LEVEL SECURITY;\n", encoding="utf-8")
        assert sc.unrunnable(tmp_path) == []

    def test_a_guarded_table_is_optional_rather_than_pending(self, tmp_path):
        """The guard is the evidence. `--live` reads names and cannot tell a table
        waiting to be created from one a migration was written to tolerate, so it
        would report `activation_codes` as pending for ever."""
        (tmp_path / "b.sql").write_text(
            "DO $$ BEGIN\n  ALTER TABLE ghost_table ENABLE ROW LEVEL SECURITY;\n"
            "EXCEPTION WHEN undefined_table THEN NULL;\nEND $$;\n", encoding="utf-8")
        assert sc.optional_tables(tmp_path) == {"ghost_table"}

    def test_a_table_something_also_creates_is_not_optional(self, tmp_path):
        (tmp_path / "a.sql").write_text(
            "CREATE TABLE real_table (id UUID);\n"
            "DO $$ BEGIN\n  ALTER TABLE real_table ENABLE ROW LEVEL SECURITY;\n"
            "EXCEPTION WHEN undefined_table THEN NULL;\nEND $$;\n", encoding="utf-8")
        assert sc.optional_tables(tmp_path) == set()

    def test_this_repositorys_only_optional_table_is_the_legacy_one(self):
        assert sc.optional_tables() == {"activation_codes"}

    def test_live_reports_it_as_absent_by_design_not_as_pending(self, monkeypatch,
                                                                capsys):
        """The whole point: a `--live` run that is clean must say so."""
        monkeypatch.setattr(sc, "live_schema",
                            lambda: {t: set(c) for t, c in sc.schema_from_migrations().items()
                                     if t not in sc.optional_tables()})
        assert sc.main(["--live"]) == 0
        out = capsys.readouterr().out
        assert "pending migrations" not in out, out
        assert "absent by design" in out and "activation_codes" in out, out


class TestComparingWithTheApi:
    def test_declared_but_not_served_is_pending_not_a_failure(self):
        pending, undeclared = sc.live_differences(
            {"a": {"id"}, "b": {"id"}}, {"a": {"id", "extra"}})
        assert pending == ["table b"] and undeclared == ["a.extra"]

    def test_a_column_the_api_serves_and_no_file_creates_is_undeclared(self):
        pending, undeclared = sc.live_differences({"a": {"id"}}, {"a": {"id", "consent_at"}})
        assert pending == [] and undeclared == ["a.consent_at"]

    def test_nothing_missing_in_either_direction(self):
        assert sc.live_differences({"a": {"id"}}, {"a": {"id"}}) == ([], [])


# ── the repository as it stands ─────────────────────────────────────────────

class TestThisRepositoryPasses:
    def test_no_table_or_column_the_code_names_is_unaccounted_for(self):
        found = sc.findings(sc.schema_from_migrations(), sc.references())
        assert found == [], (
            "these are named by the code and created by no SQL file here; at runtime "
            f"PostgREST refuses the request and a `try/except` renders an empty page: {found}")

    def test_no_policy_needs_no_session_and_no_view_skips_rls(self):
        found = sc.open_policies()
        assert found == [], (
            "the anon key is public, so a policy these describe is readable (or "
            f"writable) by anyone: {[(f['kind'], f['name']) for f in found]}")

    def test_the_answer_key_policy_is_gone(self):
        names = {f["name"] for f in sc.open_policies()}
        assert "exams.exams_select_active" not in names
        sql = "\n".join(p.read_text(encoding="utf-8") for p in sc.sql_files())
        assert 'DROP POLICY IF EXISTS "exams_select_active" ON exams;' in sql, (
            "the drop is what closes it; the check reads state, so removing the drop "
            "brings the answer key back for the anonymous caller")

    def test_the_two_views_run_as_the_caller(self):
        sql = "\n".join(p.read_text(encoding="utf-8") for p in sc.sql_files())
        for view in ("active_school_years", "class_student_counts"):
            assert f"ALTER VIEW {view} SET (security_invoker = true);" in sql, view

    def test_the_only_optional_table_is_named_as_such(self):
        assert sc.optional_tables() == {"activation_codes"}, (
            "a second guarded table has to be a deliberate edit here, or the `--live` "
            "report stops distinguishing 'waiting' from 'optional'")

    def test_no_migration_stops_at_a_table_that_does_not_exist(self):
        found = sc.unrunnable()
        assert found == [], (
            "a statement that fails takes the rest of its file with it, so every "
            f"migration after that line is silently unapplied: {found}")

    def test_a_profiles_email_select_cannot_come_back(self):
        """`profiles` has no `email`; addresses live in `auth.users`."""
        assert "email" not in sc.schema_from_migrations().get("profiles", set())
        sql = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "app").rglob("*.py"))
        for line in sql.splitlines():
            if "profiles!inner(" in line or "profiles!" in line:
                assert "email" not in line, (
                    f"a profiles embed names a column that does not exist: {line.strip()}")


# ── the check bites ─────────────────────────────────────────────────────────

class TestInjectedDefectsAreCaught:
    """Each fix is removed in a copy of the real tree and the verdict must change.

    Without this the suite proves only that the check passes, not that it can fail —
    and a check that cannot fail is a check nobody reads.
    """

    def _tree(self, tmp_path):
        dest = tmp_path / "supabase"
        shutil.copytree(ROOT / "supabase", dest)
        return dest

    def _run(self, tree):
        return sc.open_policies(tree), sc.schema_from_migrations(tree)

    def test_removing_the_answer_key_drop_brings_the_finding_back(self, tmp_path):
        tree = self._tree(tmp_path)
        m28 = tree / "migrations" / "028_rbac_least_privilege.sql"
        m28.write_text(m28.read_text(encoding="utf-8").replace(
            'DROP POLICY IF EXISTS "exams_select_active" ON exams;\n', ""), encoding="utf-8")
        open_pols, _ = self._run(tree)
        assert "exams.exams_select_active" in {f["name"] for f in open_pols}

    def test_removing_the_view_invoker_switch_brings_the_finding_back(self, tmp_path):
        tree = self._tree(tmp_path)
        m28 = tree / "migrations" / "028_rbac_least_privilege.sql"
        m28.write_text(m28.read_text(encoding="utf-8").replace(
            "ALTER VIEW active_school_years SET (security_invoker = true);\n", ""), encoding="utf-8")
        open_pols, _ = self._run(tree)
        assert "active_school_years" in {f["name"] for f in open_pols}

    def test_unguarding_the_legacy_table_statement_brings_the_finding_back(self, tmp_path):
        """Exactly the defect the file shipped with, put back verbatim."""
        tree = self._tree(tmp_path)
        m = tree / "migrations" / "20260608_fix_rls_policies.sql"
        m.write_text(m.read_text(encoding="utf-8").replace(
            "DO $$ BEGIN\n  ALTER TABLE activation_codes ENABLE ROW LEVEL SECURITY;",
            "ALTER TABLE activation_codes ENABLE ROW LEVEL SECURITY;\n"
            "DO $$ BEGIN\n  ALTER TABLE activation_codes ENABLE ROW LEVEL SECURITY;"),
            encoding="utf-8")
        assert [f["name"] for f in sc.unrunnable(tree)] == ["activation_codes"]

    def test_removing_the_drift_migration_brings_the_columns_back(self, tmp_path):
        tree = self._tree(tmp_path)
        (tree / "migrations" / "027_schema_contract_drift.sql").unlink()
        _, schema = self._run(tree)
        assert "consent_at" not in schema.get("profiles", set())
        assert "demo_settings" not in schema.get("school_settings", set())

    def test_a_wide_open_policy_added_to_a_migration_is_reported(self, tmp_path):
        tree = self._tree(tmp_path)
        (tree / "migrations" / "099_regression.sql").write_text(
            'CREATE POLICY "submissions_select_all" ON submissions FOR SELECT USING (true);\n',
            encoding="utf-8")
        open_pols, _ = self._run(tree)
        assert "submissions.submissions_select_all" in {f["name"] for f in open_pols}

    def test_reading_only_the_migrations_brings_the_72_names_back(self, monkeypatch, tmp_path):
        """The first honest run of this check reported 72 real columns as missing,
        because the base tables live in `schema.sql` and the AI tables in
        `_COMPLETE_SETUP.sql` — neither of them in `migrations/`."""
        monkeypatch.setattr(sc, "SQL_SOURCES", (sc.MIGRATIONS / "_RUN_ALL_AT_ONCE.sql",))
        found = sc.findings(sc.schema_from_migrations(), sc.references())
        assert any(f["name"] == "answer_key" and f["owner"] == "exams" for f in found), (
            "with only the migrations read, `exams.answer_key` should look absent — the "
            f"injection that reproduces the original defect; found {len(found)} names")

    def test_dropping_a_source_file_is_visible_in_the_verdict(self, monkeypatch, tmp_path):
        tree = self._tree(tmp_path)
        (tree / "schema.sql").unlink()
        real = sc.SQL_SOURCES
        monkeypatch.setattr(sc, "SQL_SOURCES", tuple(
            p for p in real if p.name != "schema.sql"))
        _, schema = self._run(tree)
        assert "full_name" not in schema.get("profiles", set()), (
            "schema.sql declares the base columns; without it they must read as absent")


# ── the live probe ──────────────────────────────────────────────────────────

class TestAskingTheApiWithAPublicKey:
    def test_every_key_a_browser_can_hold_is_probed(self):
        """The legacy anon JWT and the newer publishable key are both in circulation,
        and both answered the same eight objects — probing one would have missed
        whichever page embeds the other."""
        assert set(sc.PUBLIC_KEYS) == {"SUPABASE_ANON_KEY", "SUPABASE_PUBLISHABLE_KEY"}

    def test_nothing_is_public_by_design_yet(self):
        """Every page is rendered server-side with the service key, so no object needs
        to answer without a session. A name added here is a claim that data is public,
        and this pin is what makes that a deliberate edit."""
        assert sc.PUBLIC_BY_DESIGN == frozenset()

    def test_without_credentials_it_says_so_instead_of_passing(self, monkeypatch):
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
        monkeypatch.delenv("SUPABASE_PUBLISHABLE_KEY", raising=False)
        assert sc.probe_anon() is None, "an unanswerable probe must not read as clean"

    def test_the_catalogue_does_not_come_from_the_public_key(self):
        """Asked with the anon key, `/rest/v1/` answers 401 "Only the `service_role`
        API key can be used for this endpoint" — the first version read it with the
        anon key, got no definitions at all, and reported `0 of 0 objects` as OK."""
        source = pathlib.Path(sc.__file__).read_text(encoding="utf-8")
        body = source[source.index("def probe_anon"):]
        body = body[:body.index("def main")]
        assert "live_schema()" in body, "the catalogue must come from the service key"
        assert "requests" in body

    def test_the_probe_never_writes(self):
        source = pathlib.Path(sc.__file__).read_text(encoding="utf-8")
        body = source[source.index("def probe_anon"):]
        body = body[:body.index("def main")]
        assert "requests.post" not in body, (
            "a POST probe cannot know which tables default every column, and on one of "
            "those it creates a row in production")


# ── wiring ──────────────────────────────────────────────────────────────────

class TestTheGateRunsIt:
    def test_its_output_survives_a_console_that_is_not_utf8(self):
        """The VPS gate runs this with no PYTHONIOENCODING, so an em dash in a
        *printed* string is a UnicodeEncodeError on a cp1252 box — the tool would
        die and the gate would read that as a finding and refuse the release."""
        env = {**os.environ, "PYTHONIOENCODING": "ascii"}
        proc = subprocess.run(
            [sys.executable, str(ROOT / "deploy" / "schema_contract.py")],
            capture_output=True, env=env, cwd=str(ROOT), timeout=300)
        assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
        assert b"Traceback" not in proc.stderr, proc.stderr[-2000:]

    def test_the_hook_fires_on_python_and_sql_too(self):
        hook = (ROOT / "deploy" / "git-hooks" / "pre-commit").read_text(encoding="utf-8")
        assert "app/.*\\.py" in hook and "supabase/.*\\.sql" in hook, (
            "the schema contract reads both, so a commit touching either must run the gate")

    def test_the_theme_gate_runs_the_contract_and_stops_on_a_finding(self):
        gate = (ROOT / "deploy" / "theme_gate.sh").read_text(encoding="utf-8")
        assert "deploy/schema_contract.py" in gate
        assert "SCHEMA_RC" in gate
        assert 'if [ "$SCHEMA_RC" -eq 1 ]' in gate, "exit 1 must refuse the release"
        assert "exit 1" in gate
        index = gate.index("SCHEMA_OUT=")
        tail = gate[index:]
        assert 'if [ "$SCHEMA_RC" -eq 1 ]' in tail[:400], "the verdict must be read right there"
