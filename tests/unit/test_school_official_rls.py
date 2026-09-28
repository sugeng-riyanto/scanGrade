"""Kepala sekolah dan wakilnya membaca sekolahnya sendiri — di tingkat database.

Kedua peran ini lahir dengan dekorator (`school_official_required` di
`app/utils/auth.py`) dan sebuah test lintas sekolah yang menembak route-nya dengan
PostgREST tiruan. Yang belum ada adalah lapisan keduanya: **policy**. Backend
memakai service key, jadi policy memang bukan yang menegakkan akses — tetapi
justru karena itu ia satu-satunya tempat yang menjawab pertanyaan lain: *kalau
kunci publik yang ikut terkirim di setiap halaman dipakai tanpa sesi, apa yang
boleh dibaca?* Dan di sana kedua peran ini masih tidak terlihat sama sekali: tidak
satu pun policy menyebut `principal` atau `vice_principal`, sehingga satu-satunya
alasan seorang kepala sekolah tidak bisa membaca sekolah lain adalah kode Flask.
Satu route baru yang lupa memfilternya, dan tidak ada lapisan kedua untuk
menahannya.

Yang diuji di sini, dan mengapa berbentuk begini:

* **migrasinya ada dan berlingkup sekolah** — dibaca dari SQL-nya, bukan dari
  komentar yang mengklaimnya: setiap policy harus menyebut kedua peran (peran yang
  sama, dibaca dari `OFFICIAL_ROLES` milik aplikasi, sebab dekoratornya memakai
  tuple itu) *dan* membandingkan sekolah pemanggil dengan sekolah barisnya;
* **isolasi lintas sekolah dievaluasi, bukan diucapkan** — bentuk predikatnya
  dikenali dari SQL-nya lalu dijalankan terhadap dua sekolah tiruan, sehingga
  \"kepala sekolah A tidak melihat baris sekolah B\" adalah sesuatu yang terjadi di
  dalam test ini. Bentuk yang tidak dikenal akan menolak dibaca, bukan lolos diam;
* **hanya baca** — satu policy `FOR INSERT`/`UPDATE`/`DELETE`/`ALL` di berkas ini
  akan memberi peran pengawas wewenang tulis lewat API, dan itu kebalikan dari
  seluruh alasan perannya ada;
* **tidak lebih luas dari daftar yang disepakati** — `audit_logs` dan log mentah
  sengaja di luar jangkauan mereka; himpunan tabelnya dipin, jadi menambah tabel
  harus keputusan yang ditulis, bukan efek samping.

Berkas ini merah sebelum migrasinya ada — dan itu memang urutannya di repo ini.
"""
from __future__ import annotations

import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.utils.auth import OFFICIAL_ROLES  # noqa: E402

MIGRATION = ROOT / "supabase" / "migrations" / "039_school_official_rls.sql"
MATRIX_DOC = ROOT / "docs" / "SECURITY_RLS_MATRIX.md"

#: The tables the two officials must be able to read, and the reason each one is
#: here. The reason is part of the assertion message: a table added to the
#: migration without one is a table nobody argued for.
REQUIRED_TABLES = {
    "schools": "halaman pengawas membuka dengan nama, NPSN, kota sekolahnya",
    "profiles": "nama pada daftar guru dan murid hidup di tabel ini",
    "classes": "jumlah kelas dan daftarnya ada di halaman itu",
    "subjects": "mata pelajaran sekolahnya, untuk laporan",
    "teachers": "jumlah guru ada di halaman itu",
    "students": "jumlah murid ada di halaman itu",
    "teacher_assignments": "siapa mengajar apa, bahan laporan lintas kelas",
    "exams": "ujian sekolahnya, termasuk daftar ujian terbaru",
    "submissions": "kertas jawaban, untuk laporan yang agregat",
}

#: Two schools, and one row per school. `school-a` is the caller throughout the
#: cross-school evaluation below; `school-b` is the row they must not reach.
SCHOOL_A = "school-a"
SCHOOL_B = "school-b"

_SQL_COMMENT = re.compile(r"--[^\n]*")
_CREATE_POLICY = re.compile(
    r'CREATE\s+POLICY\s+"([^"]+)"\s+ON\s+(?:public\.)?"?([\w-]+)"?', re.I)
_DROP_POLICY = re.compile(
    r'DROP\s+POLICY\s+(?:IF\s+EXISTS\s+)?"([^"]+)"\s+ON\s+(?:public\.)?"?([\w-]+)"?', re.I)
_FOR_KIND = re.compile(r"\bFOR\s+(SELECT|INSERT|UPDATE|DELETE|ALL)\b", re.I)
_TO_CLAUSE = re.compile(r"\bTO\s+([\w, ]+?)\s+(?=USING|WITH\s+CHECK)", re.I)
_ROLE_CALL = re.compile(r"(?:public\.|auth\.)?_is_role\(\s*'(\w+)'\s*\)")
_OWN_COLUMN = re.compile(
    r"^\s*(?:[\w]+\.)?([\w]+)\s*=\s*(?:public\.|auth\.)?_user_school_id\(\)\s*$", re.I)
_VIA_FOREIGN = re.compile(
    r"^\s*EXISTS\s*\(\s*SELECT\s+1\s+FROM\s+(?:public\.)?([\w]+)\s+([\w]+)\s+WHERE\s+"
    r"\2\.id\s*=\s*([\w]+)\s+AND\s+\2\.school_id\s*=\s*"
    r"(?:public\.|auth\.)?_user_school_id\(\)\s*\)\s*$", re.I | re.S)


class UnmodelledSql(Exception):
    """A predicate this reader will not pretend to understand."""


def _strip_comments(sql: str) -> str:
    return _SQL_COMMENT.sub(" ", sql)


def _statements(sql: str) -> list[str]:
    """The file's statements, split on `;` at depth zero and outside quotes.

    A balanced-paren scan rather than a regex, because the predicate worth
    evaluating here is exactly the one with parens inside it
    (`EXISTS (SELECT 1 FROM exams e WHERE …)`), and a naive split would cut it in
    half and then judge the halves.
    """
    text = _strip_comments(sql)
    out: list[str] = []
    buf: list[str] = []
    depth = 0
    in_quote = False
    for ch in text:
        if ch == "'":
            in_quote = not in_quote
        elif not in_quote:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == ";" and depth == 0:
                out.append("".join(buf))
                buf = []
                continue
        buf.append(ch)
    out.append("".join(buf))
    return [s.strip() for s in out if s.strip()]


def _paren_group(text: str, keyword: str) -> str | None:
    """The parenthesised body after ``keyword``, matched by balanced parens."""
    m = re.search(rf"\b{keyword}\b\s*\(", text, re.I)
    if not m:
        return None
    start = m.end() - 1
    depth = 0
    in_quote = False
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "'":
            in_quote = not in_quote
        elif not in_quote:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return text[start + 1:i]
    raise UnmodelledSql(f"unbalanced parentheses after {keyword}")


def _conjuncts(predicate: str) -> list[str]:
    """Split a predicate on `AND` at depth zero, outside quotes."""
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    in_quote = False
    i = 0
    while i < len(predicate):
        ch = predicate[i]
        if ch == "'":
            in_quote = not in_quote
        elif not in_quote:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif depth == 0 and predicate[i:i + 3].upper() == "AND":
                parts.append("".join(buf))
                buf = []
                i += 3
                continue
        buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def read_policy(statement: str) -> dict:
    """One `CREATE POLICY` as a model: who it admits, to what, and by which column.

    Raises :class:`UnmodelledSql` for anything this reader cannot evaluate — the
    point being that a hand-written predicate which might not be school-scoped
    must not be able to pass the cross-school test by being unreadable.
    """
    head = _CREATE_POLICY.search(statement)
    if not head:
        raise UnmodelledSql(f"not a CREATE POLICY statement: {statement[:60]!r}")
    name, table = head.group(1), head.group(2)

    kind_match = _FOR_KIND.search(statement)
    kind = kind_match.group(1).upper() if kind_match else "ALL"

    to_match = _TO_CLAUSE.search(statement)
    to = [t.strip() for t in to_match.group(1).split(",")] if to_match else []

    body = _paren_group(statement, "USING")
    if body is None:
        raise UnmodelledSql(f"{name} has no USING clause")
    roles = _ROLE_CALL.findall(body)
    if not roles:
        raise UnmodelledSql(
            f"{name} names no role with _is_role(...) — the same policy would then "
            "apply to every role the database holds")

    shape = column = foreign = None
    for part in _conjuncts(body):
        if "_is_role(" in part:
            continue
        own = _OWN_COLUMN.match(part)
        if own:
            shape, column = "own_column", own.group(1)
            continue
        via = _VIA_FOREIGN.match(part)
        if via:
            shape, foreign, column = "via_foreign", via.group(1), via.group(3)
            continue
        raise UnmodelledSql(
            f"{name}: this reader does not model {part!r}, so it cannot show that "
            "the predicate is school-scoped")

    return {"name": name, "table": table, "kind": kind, "to": to, "roles": roles,
            "shape": shape, "column": column, "foreign": foreign, "body": body}


# ── the file as a whole ─────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def sql() -> str:
    assert MIGRATION.exists(), (
        f"{MIGRATION.name} does not exist: the two oversight roles have a decorator "
        "and no policy, so nothing but the Flask code keeps one school's principal "
        "out of another school's roster")
    return MIGRATION.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def policy_statements(sql: str) -> list[str]:
    return [s for s in _statements(sql) if _CREATE_POLICY.search(s)]


@pytest.fixture(scope="module")
def policies(policy_statements) -> list[dict]:
    return [read_policy(s) for s in policy_statements]


def test_the_file_has_policies_to_judge(policies):
    assert policies, "the migration carries no CREATE POLICY statement"


# ── it mirrors the decorator, not a second copy of the vocabulary ───────────

class TestItMirrorsTheDecorator:
    def test_it_names_exactly_the_two_official_roles(self, policies):
        """The tuple is read from the application, so the two cannot drift.

        `school_official_required` is `role_required(*OFFICIAL_ROLES)`. A migration
        that listed the roles by hand would keep working today and silently stop
        being the same set the day a third oversight role is added.
        """
        named = {role for p in policies for role in p["roles"]}
        assert named == set(OFFICIAL_ROLES), (
            f"the policies name {sorted(named)} and the shared decorator admits "
            f"{sorted(OFFICIAL_ROLES)}")

    def test_it_grants_nothing_to_the_other_roles(self, sql):
        """A policy that also admits, say, `guru` would be a second, quieter RBAC.

        Read from the stripped SQL so a role named only in a comment (\"not
        `admin_sekolah`\") is not mistaken for one that was granted.
        """
        named = set(_ROLE_CALL.findall(_strip_comments(sql)))
        others = named - set(OFFICIAL_ROLES)
        assert not others, (
            f"these roles are admitted by a policy meant for the two officials: "
            f"{sorted(others)}")

    def test_it_is_read_only(self, policies):
        """`principal` exists to watch. A write policy would hand that away."""
        writes = [(p["table"], p["name"], p["kind"]) for p in policies
                  if p["kind"] != "SELECT"]
        assert not writes, (
            "an oversight role gained a write policy at the database layer: "
            f"{writes}")

    def test_every_policy_names_the_caller_it_serves(self, policies):
        """A policy with no `TO` clause applies to `PUBLIC` — `anon` included.

        The first version of this test looped over the grantees it found and so
        asserted nothing at all about a policy that names none: it passed with the
        clause deleted, which is the shape of the whole failure it is meant to
        catch. The clause has to be *required*, not merely checked when present.
        """
        for p in policies:
            assert p["to"] == ["authenticated"], (
                f"{p['name']} grants its rows to {p['to'] or 'PUBLIC'} — a policy "
                "open to `anon` is one the public key that ships in every page can "
                "satisfy")


# ── the tables: the promise, and nothing beyond it ──────────────────────────

class TestTheTables:
    def test_the_tables_the_officials_own_route_reads_are_covered(self, policies):
        """Derived from the code, so a new query in that route demands a policy.

        `principal.py` is the only page these two roles can reach. Whatever it
        reads is read with the service key today; the policy is what keeps it
        school-scoped for any other caller on the same row.
        """
        import deploy.schema_contract as sc  # noqa: E402

        route = "app/routes/principal.py:"
        read = {ref["name"] for ref in sc.references()
                if ref["kind"] == "table" and ref["where"].startswith(route)}
        assert read, "principal.py names no table — the reader would prove nothing"
        covered = {p["table"] for p in policies}
        assert read <= covered, (
            f"the officials' own page reads {sorted(read - covered)} and no policy "
            "of theirs covers it")

    def test_the_agreed_tables_are_all_there(self, policies):
        covered = {p["table"] for p in policies}
        missing = set(REQUIRED_TABLES) - covered
        assert not missing, (
            "these tables are in the agreed read scope and have no policy: "
            f"{sorted(missing)}")

    def test_it_grants_nothing_beyond_the_agreed_tables(self, policies):
        """`audit_logs` is the one that matters: a raw log is not an oversight view."""
        extra = {p["table"] for p in policies} - set(REQUIRED_TABLES)
        assert not extra, (
            f"the migration widens the officials' reach to {sorted(extra)}; adding a "
            "table here has to be a deliberate edit with a reason")

    def test_every_table_it_touches_exists(self, policies):
        """A policy on a table nothing creates fails at apply time and takes the
        rest of the file with it."""
        import deploy.schema_contract as sc  # noqa: E402

        schema = sc.schema_from_migrations()
        unknown = {p["table"] for p in policies if p["table"] not in schema}
        assert not unknown, f"no SQL file here creates {sorted(unknown)}"


# ── isolation across schools, evaluated ────────────────────────────────────

def _row_for(policy: dict, school: str) -> dict:
    if policy["shape"] == "own_column":
        return {policy["column"]: school}
    if policy["shape"] == "via_foreign":
        return {policy["column"]: f"exam-{school}"}
    raise AssertionError(f"{policy['name']} has shape {policy['shape']!r}")


#: The foreign rows the `EXISTS` shape needs: one exam per school, each in its own.
FOREIGN_ROWS = {
    "exams": [{"id": f"exam-{SCHOOL_A}", "school_id": SCHOOL_A},
              {"id": f"exam-{SCHOOL_B}", "school_id": SCHOOL_B}],
}


def _visible(policy: dict, role: str, caller_school: str | None, row: dict) -> bool:
    """The predicate, run: would the database hand this row to this caller?

    Driven by the parsed policy rather than by a re-statement of it, so the thing
    the assertions describe is the thing the migration says.
    """
    if role not in policy["roles"]:
        return False
    if caller_school is None:
        return False
    if policy["shape"] == "own_column":
        return row.get(policy["column"]) == caller_school
    if policy["shape"] == "via_foreign":
        wanted = row.get(policy["column"])
        return any(foreign.get("id") == wanted
                   and foreign.get("school_id") == caller_school
                   for foreign in FOREIGN_ROWS[policy["foreign"]])
    raise AssertionError(f"{policy['name']} has shape {policy['shape']!r}")


class TestIsolationAcrossSchools:
    def test_the_same_school_is_visible_to_both_officials(self, policies):
        assert policies, "nothing to evaluate"
        for p in policies:
            row = _row_for(p, SCHOOL_A)
            for role in OFFICIAL_ROLES:
                assert _visible(p, role, SCHOOL_A, row), (
                    f"{p['name']} hides {p['table']} from {role} in its own school")

    def test_another_schools_row_is_not_visible_to_either(self, policies):
        for p in policies:
            row = _row_for(p, SCHOOL_B)
            for role in OFFICIAL_ROLES:
                assert not _visible(p, role, SCHOOL_A, row), (
                    f"{p['name']}: a {role} of {SCHOOL_A} could reach a "
                    f"{p['table']} row of {SCHOOL_B}")

    def test_a_caller_with_no_school_sees_nothing(self, policies):
        """`_user_school_id()` is NULL for a profile with no school, and
        `column = NULL` is never true — but the row model should say so out loud."""
        for p in policies:
            row = _row_for(p, SCHOOL_A)
            for role in OFFICIAL_ROLES:
                assert not _visible(p, role, None, row), (
                    f"{p['name']} admits a caller with no school")

    def test_the_school_scope_is_not_the_only_thing_holding_the_line(self, policies):
        """A same-school caller of another role must stay outside these policies.

        Otherwise the migration would be quietly widening RBAC: `admin_sekolah`
        reads this table through its own policy, and `guru` through another one —
        neither of them through this.
        """
        for p in policies:
            row = _row_for(p, SCHOOL_A)
            for role in ("super_admin", "admin_sekolah", "guru", "murid"):
                assert not _visible(p, role, SCHOOL_A, row), (
                    f"{p['name']} admits {role}, which has a policy of its own")

    def test_the_reader_refuses_a_shape_it_does_not_model(self):
        """The property that keeps the test above honest.

        If an unreadable predicate were skipped instead of refused, the next
        hand-written policy would pass the cross-school test by being invisible to
        it — which is the failure mode this whole file exists to prevent.
        """
        with pytest.raises(UnmodelledSql):
            read_policy('CREATE POLICY "p" ON exams FOR SELECT USING (true);')
        with pytest.raises(UnmodelledSql):
            read_policy('CREATE POLICY "p" ON exams FOR SELECT USING '
                        "(public._is_role('principal') AND teacher_id = auth.uid());")
        # ...and the two shapes that are modelled read cleanly.
        own = read_policy('CREATE POLICY "q" ON exams FOR SELECT TO authenticated '
                          "USING (public._is_role('principal') "
                          "AND school_id = public._user_school_id());")
        assert own["shape"] == "own_column" and own["column"] == "school_id"


# ── it is a migration this repository can survive ──────────────────────────

class TestItIsSafeToApply:
    def test_every_create_is_preceded_by_its_drop(self, sql):
        dropped = {m.group(1) for m in _DROP_POLICY.finditer(_strip_comments(sql))}
        created = {m.group(1) for m in _CREATE_POLICY.finditer(_strip_comments(sql))}
        assert created, "no policy to be idempotent about"
        assert created <= dropped, (
            f"re-running the file fails on a duplicate policy name: "
            f"{sorted(created - dropped)}")

    def test_it_removes_no_row_and_no_table(self, sql):
        body = _strip_comments(sql)
        assert not re.search(r"\bDROP\s+(TABLE|COLUMN|SCHEMA)\b", body, re.I), (
            "the migration must be additive: real rows live in these tables")
        assert not re.search(r"\bDELETE\s+FROM\b", body, re.I)
        assert not re.search(r"\bTRUNCATE\b", body, re.I)

    def test_it_does_not_own_its_own_transaction(self, sql):
        """`apply_migration.py` owns the transaction, and a dry run rolls it back.

        A file that commits by itself makes the dry run a lie, and the tool
        refuses it for that reason.
        """
        body = _strip_comments(sql)
        assert not re.search(r"\b(BEGIN|COMMIT|ROLLBACK)\s*;", body, re.I)

    def test_the_schema_contract_sees_nothing_open_in_it(self):
        import deploy.schema_contract as sc  # noqa: E402

        open_names = {f["name"] for f in sc.open_policies()}
        for table in REQUIRED_TABLES:
            assert f"{table}.{table}_select_school_official" not in open_names, (
                "the schema contract reads this policy as satisfiable without a "
                "session, which is the hole it exists to close")


# ── the documents stop claiming the gap is still there ─────────────────────

class TestTheDocumentationFollows:
    def test_the_matrix_names_the_migration(self):
        doc = MATRIX_DOC.read_text(encoding="utf-8")
        assert "039_school_official_rls.sql" in doc, (
            "docs/SECURITY_RLS_MATRIX.md still describes the two oversight roles as "
            "having no policy of their own")

    def test_the_matrix_no_longer_calls_them_roles_without_a_policy(self):
        doc = MATRIX_DOC.read_text(encoding="utf-8")
        assert "Without A Policy Yet" not in doc, (
            "the heading is now false: the policies exist")
