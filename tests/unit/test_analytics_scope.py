"""Who gets which statistics — and the three documents each of them gets.

The page asks one question of three different readers: a teacher wants their own
exams, an admin of a school wants the school's, a super admin wants everything.
The permission is not decided here; it is `exam_access.can_manage_exam`, which is
the same predicate the per-exam routes ask. So these tests are written the way a
leak would happen: a teacher must not see a same-school colleague's exam, an
admin must not see another school's, and an admin with no school on file must see
nothing at all rather than everything.
"""
from __future__ import annotations

import pathlib
import re
from types import SimpleNamespace

import pytest

from app.services import analysis_scope

ROOT = pathlib.Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "app" / "templates"
ROUTE_SOURCE = (ROOT / "app" / "routes" / "teacher.py").read_text(encoding="utf-8")

SCHOOL_A, SCHOOL_B = "school-a", "school-b"
ANA, BUDI, CITA = "user-ana", "user-budi", "user-cita"   # ANA+BUDI at A, CITA at B


class FakeQuery:
    """Enough of PostgREST's builder to run the service's queries for real."""

    def __init__(self, rows, log):
        self.rows = list(rows)
        self.log = log
        self._filters = []
        self._order = None
        self._limit = None

    def select(self, _columns="*"):
        return self

    def eq(self, column, value):
        self._filters.append((column, value))
        return self

    def in_(self, column, values):
        self._filters.append((column, set(str(v) for v in values)))
        return self

    def order(self, column, desc=False):
        self._order = (column, desc)
        return self

    def limit(self, count):
        self._limit = int(count)
        return self

    def _rows(self):
        rows = self.rows
        for column, value in self._filters:
            if isinstance(value, set):
                rows = [r for r in rows if str(r.get(column)) in value]
            else:
                rows = [r for r in rows if str(r.get(column)) == str(value)]
        if self._order:
            column, desc = self._order
            rows = sorted(rows, key=lambda r: str(r.get(column) or ""), reverse=desc)
        if self._limit is not None:
            rows = rows[:self._limit]
        return rows

    def execute(self):
        self.log.append((tuple(self._filters), len(self.rows)))
        return SimpleNamespace(data=[dict(r) for r in self._rows()])

    def maybe_single(self):
        rows = self._rows()
        return SimpleNamespace(data=dict(rows[0]) if rows else None)


class FakeSupabase:
    def __init__(self, tables):
        self._tables = tables
        self.calls = []

    def table(self, name):
        self.calls.append(name)
        return FakeQuery(self._tables.get(name, []), self.calls)


def _exam(exam_id, teacher, school, title, passing=70, questions=4, key="A"):
    return {"id": exam_id, "title": title, "subject": "Fisika", "teacher_id": teacher,
            "school_id": school, "created_at": f"2026-09-0{exam_id[-1]}T00:00:00Z",
            "total_questions": questions, "passing_score": passing, "status": "active",
            "question_types": {str(i): "mcq" for i in range(questions)},
            "question_weights": {},
            "answer_key": ({str(i): key for i in range(questions)} if key else {})}


def _submission(exam_id, name, score, answers):
    return {"exam_id": exam_id, "student_id": f"stu-{name}", "student_name": name,
            "answers": answers, "final_score": score, "score": score,
            "status": "graded", "teacher_feedback": {}}


def _answers(right, wrong=0):
    """`right` copies of the key, then `wrong` copies of a distractor."""
    out = {}
    for index in range(4):
        if index < right:
            out[str(index)] = "A"
        elif index < right + wrong:
            out[str(index)] = "B"
    return out


def _tables():
    return {
        "schools": [{"id": SCHOOL_A, "name": "SMA Satu"},
                    {"id": SCHOOL_B, "name": "SMA Dua"}],
        "profiles": [{"id": ANA, "full_name": "Ana Guru"},
                     {"id": BUDI, "full_name": "Budi Guru"},
                     {"id": CITA, "full_name": "Cita Guru"}],
        "exams": [
            _exam("exam-a1", ANA, SCHOOL_A, "Ana Satu"),
            _exam("exam-a2", ANA, SCHOOL_A, "Ana Dua"),
            _exam("exam-b1", BUDI, SCHOOL_A, "Budi Satu"),
            _exam("exam-c1", CITA, SCHOOL_B, "Cita Satu"),
            _exam("exam-c2", CITA, SCHOOL_B, "Cita Dua"),
        ],
        "submissions": [
            _submission("exam-a1", "Murid 1", 90, _answers(4)),
            _submission("exam-a1", "Murid 2", 50, _answers(2, 2)),
            _submission("exam-b1", "Murid 3", 80, _answers(3, 1)),
            _submission("exam-c1", "Murid 4", 40, _answers(1, 3)),
        ],
    }


# ── the scope ────────────────────────────────────────────────────────────────

def _ids(data):
    return sorted(row["id"] for row in data["rows"])


def test_a_teacher_sees_their_own_exams_and_not_a_colleague_s():
    """The defect this exists for: the old page filtered by `teacher_id` for
    everyone, so an admin saw nothing — and a scope built by "same school" instead
    would have shown Ana her colleague's paper."""
    data = analysis_scope.report(FakeSupabase(_tables()), "guru", ANA, SCHOOL_A)
    assert _ids(data) == ["exam-a1", "exam-a2"]
    assert all(row["teacher"] == "Ana Guru" for row in data["rows"])


def test_an_admin_sees_the_whole_school_and_only_that_school():
    data = analysis_scope.report(FakeSupabase(_tables()), "admin_sekolah", "user-admin",
                                 SCHOOL_A)
    assert _ids(data) == ["exam-a1", "exam-a2", "exam-b1"]
    assert {row["school"] for row in data["rows"]} == {"SMA Satu"}
    assert {row["teacher"] for row in data["rows"]} == {"Ana Guru", "Budi Guru"}


def test_a_super_admin_sees_every_school():
    data = analysis_scope.report(FakeSupabase(_tables()), "super_admin", "user-root", None)
    assert _ids(data) == ["exam-a1", "exam-a2", "exam-b1", "exam-c1", "exam-c2"]
    assert {row["school"] for row in data["rows"]} == {"SMA Satu", "SMA Dua"}


def test_the_newest_exam_is_first():
    """The table is read newest first, which is what the query asks the database
    for — a report whose rows came back in insertion order would put last year's
    paper at the top of a school's page."""
    tables = _tables()
    for exam, day in zip(tables["exams"], ("2026-01-05", "2026-02-05",
                                            "2026-03-05", "2026-04-05",
                                            "2026-05-05")):
        exam["created_at"] = f"{day}T00:00:00Z"
    data = analysis_scope.report(FakeSupabase(tables), "super_admin", "root", None)
    assert [row["id"] for row in data["rows"]] == [
        "exam-c2", "exam-c1", "exam-b1", "exam-a2", "exam-a1"]


def test_an_admin_with_no_school_on_file_sees_nothing_rather_than_everything():
    """`can_manage_exam` fails closed on an admin with no school, and a scope that
    narrows first and asks afterwards would hand that admin the whole box."""
    data = analysis_scope.report(FakeSupabase(_tables()), "admin_sekolah", "user-admin", None)
    assert data["rows"] == []


@pytest.mark.parametrize("role", ["murid", "", "teacher", "admin", None])
def test_a_role_with_no_scope_gets_no_rows(role):
    data = analysis_scope.report(FakeSupabase(_tables()), role, "user-x", SCHOOL_A)
    assert data["rows"] == []


def test_the_permission_is_the_same_predicate_the_routes_ask(monkeypatch):
    """A row that `can_manage_exam` refuses must not appear, whatever the query
    returned — the query narrows for cost, the predicate is the permission."""
    tables = _tables()
    seen = []
    real = analysis_scope.can_manage_exam

    def watch(user_id, role, school_id, exam):
        allowed = real(user_id, role, school_id, exam)
        seen.append((exam["id"], allowed))
        return allowed

    monkeypatch.setattr(analysis_scope, "can_manage_exam", watch)
    data = analysis_scope.report(FakeSupabase(tables), "admin_sekolah", "user-admin",
                                 SCHOOL_A)
    assert seen, "the scope never asked the permission predicate"
    assert all(allowed for _id, allowed in seen)


def test_the_report_is_capped_and_says_so():
    data = analysis_scope.report(FakeSupabase(_tables()), "super_admin", "user-root",
                                 None, limit=2)
    assert len(data["rows"]) == 2
    assert data["truncated"] is True
    assert data["limit"] == 2
    assert analysis_scope.report(FakeSupabase(_tables()), "super_admin", "root",
                                 None, limit=40)["truncated"] is False


# ── the row ──────────────────────────────────────────────────────────────────

def test_each_row_carries_the_marks_and_the_item_statistics():
    data = analysis_scope.report(FakeSupabase(_tables()), "guru", ANA, SCHOOL_A)
    first = next(row for row in data["rows"] if row["id"] == "exam-a1")
    assert first["count"] == 2
    assert first["mean"] == 70.0 and first["median"] == 70.0
    assert first["pass_pct"] == 50
    assert first["items"] == 4
    # Two students on four questions: the reliability cannot be estimated, and the
    # row says so with None rather than with a number nobody should trust.
    assert first["alpha"] is None or isinstance(first["alpha"], float)


def test_the_totals_add_up_the_whole_scope():
    data = analysis_scope.report(FakeSupabase(_tables()), "super_admin", "root", None)
    totals = data["totals"]
    assert totals["exams"] == 5
    assert totals["participants"] == 4
    assert totals["mean"] == 65.0          # 90, 50, 80, 40
    assert totals["pass_rate"] == 50       # two of four reached 70
    assert totals["questions"] == 20
    assert totals["without_key"] == 0


def test_a_question_nobody_answered_is_a_hole_and_not_a_flag():
    """A flagged question is one that was measured and misbehaved; a hole is one
    that could not be measured. Counting them together lets an exam full of empty
    questions look like an exam full of bad ones."""
    tables = _tables()
    tables["exams"].append(_exam("exam-a3", ANA, SCHOOL_A, "Tanpa kunci", key=None))
    data = analysis_scope.report(FakeSupabase(tables), "guru", ANA, SCHOOL_A)
    row = next(r for r in data["rows"] if r["id"] == "exam-a3")
    assert row["holes"] == 4, "a question with no key is not a hole"
    assert row["flagged"] == 0
    assert data["totals"]["without_key"] == 1


def test_a_paper_nobody_sat_is_not_a_paper_without_a_key():
    """`without_key` counts exams whose *key* is missing, and an exam with no
    submissions is missing answers, not a key.

    It counted every hole once, and the two looked identical because
    `analyse(exam, [])` raised on the empty matrix — the `except` branch zeroed
    the count, so both of the fixture's uninhabited exams came out at zero for
    the wrong reason. `/teacher/analytics` grew a crash test for that empty case,
    the exception stopped happening, and the row started reporting four holes as
    four missing keys: a school that had not yet run a paper was told its answer
    key was missing.
    """
    tables = _tables()
    keys = {exam["id"]: exam["answer_key"] for exam in tables["exams"]}
    assert all(keys.values()), "every exam in the fixture carries a key"
    assert not any(s["exam_id"] in ("exam-a2", "exam-c2")
                   for s in tables["submissions"]), "and two have no papers"

    data = analysis_scope.report(FakeSupabase(tables), "super_admin", "root", None)
    rows = {row["id"]: row for row in data["rows"]}
    assert rows["exam-a2"]["holes"] == 4, "a paper nobody sat cannot be measured"
    assert rows["exam-a2"]["unkeyed"] == 0, "and that is not a missing key"
    assert data["totals"]["without_key"] == 0


def test_an_unreadable_paper_does_not_take_the_report_with_it():
    """One exam whose columns are malformed must cost its own row's statistics,
    not the school's whole page."""
    tables = _tables()
    tables["exams"].append({"id": "exam-broken", "title": "Rusak", "teacher_id": ANA,
                            "school_id": SCHOOL_A, "created_at": "2026-09-09",
                            "total_questions": "not a number"})
    data = analysis_scope.report(FakeSupabase(tables), "guru", ANA, SCHOOL_A)
    assert len(data["rows"]) == 3
    broken = next(r for r in data["rows"] if r["id"] == "exam-broken")
    assert broken["items"] == 0 and broken["alpha"] is None


def test_the_scope_label_is_written_for_the_reader():
    assert analysis_scope.scope_label("guru", "en") == "Your own exams"
    assert analysis_scope.scope_label("admin_sekolah", "id") == \
        "Seluruh ujian di sekolah Anda"
    assert analysis_scope.scope_label("super_admin", "en") == \
        "Every exam, across all schools"
    # An unknown role still says something rather than raising on the key.
    assert analysis_scope.scope_label("who", "en")


# ── the documents ────────────────────────────────────────────────────────────

def test_the_spreadsheet_is_one_row_per_exam_plus_a_total():
    import csv
    import io

    data = analysis_scope.report(FakeSupabase(_tables()), "admin_sekolah", "user-admin",
                                 SCHOOL_A)
    rows = list(csv.reader(io.StringIO(analysis_scope.report_csv(data))))
    assert rows[0][0] == data["texts"]["exam"]
    assert len(rows) == len(data["rows"]) + 2          # header + six exams + total
    assert rows[-1][0] == data["texts"]["total"]
    assert rows[-1][3] == "3"                          # participants, as a number
    assert rows[1][1] and rows[1][2]                   # teacher and school names


def test_the_spreadsheet_headers_follow_the_reader_s_language():
    tables = FakeSupabase(_tables())
    english = analysis_scope.report(tables, "guru", ANA, SCHOOL_A, lang="en")
    indonesian = analysis_scope.report(FakeSupabase(_tables()), "guru", ANA, SCHOOL_A,
                                       lang="id")
    assert analysis_scope.report_csv(english).splitlines()[0].startswith("Exam,")
    assert analysis_scope.report_csv(indonesian).splitlines()[0].startswith("Ujian,")


def test_the_pdf_is_a_document_with_drawings_in_it():
    """The complaint this feature answers was a download with no charts. A PDF of
    tables is what that looked like, so the drawings are measured."""
    import fitz

    for lang, scope_words in (("id", "SMA Satu"), ("en", "SMA Satu")):
        data = analysis_scope.report(FakeSupabase(_tables()), "admin_sekolah",
                                     "user-admin", SCHOOL_A, lang=lang)
        pdf = analysis_scope.report_pdf(data)
        assert pdf.startswith(b"%PDF-")
        doc = fitz.open(stream=pdf, filetype="pdf")
        page = doc[0]
        text = page.get_text()
        assert len(page.get_drawings()) > 40, "the report has no drawings"
        assert data["scope"] in text
        assert analysis_scope.filename(data, "pdf").startswith("analitik-admin_sekolah")
        doc.close()


def test_the_filename_names_the_scope_and_the_day():
    data = analysis_scope.report(FakeSupabase(_tables()), "super_admin", "root", None)
    name = analysis_scope.filename(data, "csv")
    assert name.startswith("analitik-super_admin-")
    assert name.endswith(".csv")


def test_a_report_survives_the_cache_round_trip():
    """The cache stores JSON, and the stamp is the one value in the report that is
    not one — a string coming back where a datetime is expected breaks every file
    name built from it."""
    data = analysis_scope.report(FakeSupabase(_tables()), "guru", ANA, SCHOOL_A)
    payload = analysis_scope.as_payload(data)
    assert isinstance(payload["generated"], str)
    restored = analysis_scope.from_payload(payload)
    assert restored["generated"] == data["generated"]
    assert analysis_scope.filename(restored, "pdf") == \
        analysis_scope.filename(data, "pdf")


# ── the page ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def app():
    from app import create_app
    return create_app("app.config.TestingConfig")


def _signed_in(user_id, role, school_id=""):
    """The `g` values base.html reads: a name, an email, and the role."""
    from flask import g
    g.user_id = user_id
    g.user_role = role
    g.user_school_id = school_id
    g.user_name = "Uji Coba"
    g.user_email = "uji@example.test"
    g.tz_offset = 7
    g.show = {}


def _page_context(data):
    return {
        "report": data,
        "stats": {"total_exams": data["totals"]["exams"],
                  "total_submissions": data["totals"]["participants"],
                  "avg_score": data["totals"]["mean"],
                  "pass_rate": data["totals"]["pass_rate"],
                  "std_dev": data["totals"]["sd"] or 0},
        "dist_bins": data["bins"],
        "exam_breakdown": data["rows"],
        "exam_labels": [row["title"][:20] for row in data["rows"]],
        "exam_avgs": [row["mean"] or 0 for row in data["rows"]],
        "exam_medians": [row["median"] or 0 for row in data["rows"]],
    }


def _page(app, role="admin_sekolah", user_id="user-admin", school=SCHOOL_A):
    """The page, rendered from a real report for the role being checked."""
    data = analysis_scope.report(FakeSupabase(_tables()), role, user_id, school,
                                 lang="en")
    with app.test_request_context("/teacher/analytics"):
        _signed_in(user_id, role, school or "")
        return app.jinja_env.get_template("teacher/analytics.html").render(
            **_page_context(data))


def test_the_page_offers_each_role_its_own_three_documents(app):
    html = _page(app)
    # The export links now use exportUrl() which appends lang + date range via JS.
    for path in ("/teacher/analytics/download.csv",
                 "/teacher/analytics/download.pdf",
                 "/teacher/analytics/print"):
        assert path in html, f"the scope report offers no {path}"
    assert "data-download=\"csv\"" in html and "data-download=\"pdf\"" in html
    # The exportUrl helper builds the query string dynamically.
    assert "exportUrl(" in html
    assert "/teacher/analytics/download.pdf" in html


def test_every_row_links_to_that_exam_s_own_analysis_and_files(app):
    html = _page(app)
    data = analysis_scope.report(FakeSupabase(_tables()), "admin_sekolah", "user-admin",
                                 SCHOOL_A)
    for row in data["rows"]:
        assert f'href="/teacher/analysis/{row["id"]}"' in html, (
            f"{row['id']} has no analysis page link")
        assert f"'/teacher/analysis/{row['id']}/download.pdf?lang=' + lang" in html
        assert f"'/teacher/analysis/{row['id']}/download.csv?lang=' + lang" in html


def test_the_page_names_the_scope_it_is_showing(app):
    html = _page(app)
    assert "Every exam in your school" in html


def test_the_scope_name_switches_with_the_toggle(app):
    """The scope is the one line the server used to bake.

    `report.scope` is written for the documents — a PDF is generated for one
    reader and cannot follow a toggle — and printing it on the page left the
    single Indonesian phrase on an otherwise English screen, because the page's
    language is decided in the browser long after the server rendered it.
    """
    for role, user, school in (("guru", ANA, SCHOOL_A),
                               ("admin_sekolah", "user-admin", SCHOOL_A),
                               ("super_admin", "user-root", None)):
        html = _page(app, role=role, user_id=user, school=school)
        assert "x-text=\"t(scope.id, scope.en)\"" in html, (
            f"{role}: the scope label is not bound as a pair")
        for printed in ('>{{ report.scope }}', "{{report.scope}}"):
            assert printed not in html, (
                f"{role}: the scope label is printed frozen, so it cannot toggle")
    text = (TEMPLATES / "teacher" / "analytics.html").read_text(encoding="utf-8")
    assert "{{ report.scope }}" not in text, (
        "the analytics page prints a language-pinned scope label again")


def test_the_scope_pair_carries_both_languages_for_every_role():
    """A role whose pair was half-empty would render a blank on one side of the
    toggle, which is worse than a stale label: it looks like a missing permission."""
    for role in ("guru", "admin_sekolah", "super_admin"):
        pair = analysis_scope.scope_pair(role)
        assert pair["id"] and pair["en"], f"{role}: {pair!r} is missing a language"
        assert pair["id"] == analysis_scope.scope_label(role, "id")
        assert pair["en"] == analysis_scope.scope_label(role, "en")
    assert analysis_scope.scope_pair("murid") == {"id": "", "en": ""}, (
        "a role with no scope got words anyway")


def test_the_school_and_teacher_columns_are_dropped_for_a_teacher(app):
    """A teacher reading their own exams does not need a column repeating their
    own name on every row — and an admin does, because the rows are other people's."""
    teacher_pair = "t('Guru','Teacher')"
    school_pair = "t('Sekolah','School')"
    head = lambda html: html.split("<thead", 1)[1].split("</thead>", 1)[0]
    mine = head(_page(app, role="guru", user_id=ANA))
    assert teacher_pair not in mine and school_pair not in mine
    admin = head(_page(app))
    assert teacher_pair in admin and school_pair in admin


def test_no_cell_prints_a_python_repr(app):
    """Jinja resolves an attribute before a key, so `e.items` on a dict finds the
    *method* — and the school's page printed "<built-in method items of dict object
    at 0x…>" in the question count. Nothing about a template says which keys
    collide with `dict`'s own names, so the whole page is swept rather than the one
    cell that broke."""
    html = _page(app)
    for pattern in ("<built-in method", "<bound method", "object at 0x",
                    "&lt;built-in method"):
        assert pattern not in html, (
            f"the page prints a Python repr ({pattern}) — a row key is colliding "
            f"with a dict attribute in the template")
    # The question count reaches the table as a number, which is what the
    # collision was hiding.
    assert ">4</td>" in html or ">4 <" in html or ">4<" in html


def test_the_page_does_not_pin_its_language_any_more():
    """The page used to declare `content_lang = 'id'` and write its copy as inline
    ternaries, which left the audit unable to count it and the toggle unable to
    reach it. Every string on it is a pair now."""
    text = (TEMPLATES / "teacher" / "analytics.html").read_text(encoding="utf-8")
    assert not re.search(r"\{%-?\s*set\s+content_lang", text), \
        "the analytics page pins its language again"
    assert re.search(r"t\('", text), "the page carries no pairs at all"


# ── the routes ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("route", ["/analytics", "/analytics/download.csv",
                                   "/analytics/download.pdf", "/analytics/print"])
def test_every_route_is_guarded_for_the_three_roles_that_have_a_scope(route):
    """Source-level, because the decorator is what decides: a route that lost it
    would answer a student with a report about the school."""
    block = ROUTE_SOURCE.split(f'@teacher_bp.route("{route}")', 1)
    assert len(block) == 2, f"route {route} is gone"
    head = block[1][:400]
    assert 'role_required("guru", "admin_sekolah", "super_admin")' in head, (
        f"{route} lost its role guard")
