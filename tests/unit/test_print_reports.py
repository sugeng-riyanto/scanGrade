"""A report card has to be a document, not a picture of the application.

Both screens involved already printed something, and what they printed was the
interface: the teacher's results page came out as a toolbar, a row of export
buttons and stat cards with the roster broken across page boundaries, and the
marking view printed its canvas tools and calculator. A school files a sheet —
who sat what, the marks, a place to sign.

The documents are checked for what a school needs and for what they must never
contain, and both print routes are held to the same access rule as the screens
they replace: a print view that is easier to open than the page would be a new
way to read another school's marks.

The Supabase stand-in below answers real filters, so a route that forgets to
scope its query fails here instead of passing against a mock that returns rows
regardless.
"""
import copy
import uuid
from pathlib import Path

import pytest

from app.utils import auth as authmod


# ── a Supabase stand-in that actually filters ────────────────────

class FakeResponse:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, rows):
        self._rows = rows
        self._filters = []
        self._mode = "many"

    def select(self, *a, **k):
        return self

    def eq(self, column, value):
        self._filters.append(("eq", column, value))
        return self

    def in_(self, column, values):
        self._filters.append(("in", column, list(values)))
        return self

    def single(self):
        self._mode = "single"
        return self

    def maybe_single(self):
        self._mode = "maybe"
        return self

    def _matches(self, row):
        for kind, column, value in self._filters:
            if kind == "eq" and str(row.get(column)) != str(value):
                return False
            if kind == "in" and str(row.get(column)) not in {str(v) for v in value}:
                return False
        return True

    def execute(self):
        rows = [r for r in self._rows if self._matches(r)]
        # Deep copies, because a real response is built fresh per query while
        # these rows are shared by every test. A caller that pops an embedded
        # row off its submission (load_report_card does) would otherwise edit
        # the fixture, and the next test would fail on data it thinks it owns.
        if self._mode == "single":
            if not rows:
                raise RuntimeError("no rows")   # postgrest raises here
            return FakeResponse(copy.deepcopy(rows[0]))
        if self._mode == "maybe":
            # postgrest returns None — not a response carrying None — when
            # nothing matches, which is the hazard row_or_none() exists for.
            return FakeResponse(copy.deepcopy(rows[0])) if rows else None
        return FakeResponse(copy.deepcopy(rows))


class FakeSupabase:
    def __init__(self, tables):
        self._tables = tables

    def table(self, name):
        return FakeQuery(list(self._tables.get(name, [])))


# ── fixtures ─────────────────────────────────────────────────────

PNG = "data:image/png;base64," + ("A" * 900)
OVERLAY = "data:image/png;base64," + ("B" * 1200)

EXAM = {
    "id": "exam-1", "title": "Ujian Matematika", "subject": "Matematika",
    "teacher_id": "guru-a", "school_id": "school-a", "total_questions": 3,
    "question_types": {"0": "mcq", "1": "essay", "2": "essay"},
    "answer_key": {"0": "A", "1": "essay", "2": "essay"},
    "question_weights": {"0": 30, "1": 35, "2": 35},
    "pdf_page_urls": ["/static/uploads/exams/exam-1/page_001.png"],
    "class_ids": ["c1"], "started_at": "2026-09-13T02:00:00+00:00",
}

ANSWERS = {
    "0": "A",
    "1": {"answer": "essay", "pages": {"0": {"canvas": PNG,
          "textBoxes": [{"text": "x = 2", "x_pct": 0.12, "y_pct": 0.2}]}}},
    "2": {"answer": "essay", "text": "Jawaban esai kedua"},
    "_nisn": "1234567890",
}

FEEDBACK = {
    "scores": {"1": 30}, "comments": {"1": "Langkah sudah benar"},
    "overlay_pages": {"1": {"0": {"canvas": OVERLAY, "textBoxes": []}}},
}


def _submission(sub_id, student_id, name, status, published, final):
    return {
        "id": sub_id, "exam_id": "exam-1", "student_id": student_id,
        "score": 20, "penalty": 5, "violations": 2, "final_score": final,
        "status": status, "is_published": published, "is_hidden": False,
        "submitted_at": "2026-09-13T03:15:00+00:00",
        "answers": dict(ANSWERS), "teacher_feedback": dict(FEEDBACK),
        "exams": dict(EXAM),
        # The embed and the flattened name, exactly as _exam_results leaves a row:
        # a direct render must feed the template what the route feeds it.
        "profiles": {"full_name": name},
        "student_name": name,
    }


ROWS = {
    "profiles": [
        {"id": "murid-1", "full_name": "Ahmad Pratama", "nisn": "1234567890",
         "nis": "1001", "school_id": "school-a", "role": "murid", "status": "active"},
        {"id": "murid-2", "full_name": "Bella Safira", "nisn": "9876543210",
         "nis": "1002", "school_id": "school-a", "role": "murid", "status": "active"},
        {"id": "guru-a", "full_name": "LT Guru 01", "school_id": "school-a",
         "role": "guru", "status": "active"},
        {"id": "guru-b", "full_name": "Guru Sekolah Lain", "school_id": "school-b",
         "role": "guru", "status": "active"},
    ],
    "schools": [
        {"id": "school-a", "name": "SMP Negeri 1 Contoh", "address": "Jl. Pendidikan 1",
         "city": "Bandung", "province": "Jawa Barat", "npsn": "12345678", "logo_url": ""},
    ],
    "exams": [dict(EXAM)],
    "classes": [{"id": "c1", "name": "VII-A"}],
    "submissions": [
        _submission("sub-released", "murid-1", "Ahmad Pratama", "published", True, 55.5),
        _submission("sub-unreleased", "murid-1", "Ahmad Pratama", "submitted", False, 0.0),
        _submission("sub-other", "murid-2", "Bella Safira", "published", True, 88.0),
    ],
}


@pytest.fixture(autouse=True)
def _no_session_cache_replay():
    """The session cache is keyed on the token, so without this a session cached
    by an earlier test is replayed for a later, different fake user — which turns
    a permission test into a test of whichever user ran first."""
    from app.utils import kv_cache

    kv_cache._local.clear()
    yield
    kv_cache._local.clear()


@pytest.fixture
def app(app_session, monkeypatch):
    application = app_session
    application.config["RATELIMIT_ENABLED"] = False

    db = FakeSupabase(ROWS)
    # The route modules import get_supabase by value, so app.utils.auth alone
    # would not reach them and the view would open a real connection.
    for module in ("app.routes.student", "app.routes.teacher", "app.utils.auth"):
        monkeypatch.setattr(f"{module}.get_supabase", lambda: db, raising=False)
    # load_report_card reaches for the app-level client too.
    monkeypatch.setattr("app.services.report_card_service.get_supabase",
                        lambda: db, raising=False)

    yield application


def sign_in(app, user_id, role, school_id):
    """A logged-in test client, using the app's own session resolution.

    The cookie is unique per sign-in: reusing one token across tests lets the
    session cache serve the previous test's user.
    """
    authmod._session_for = lambda token, _u=user_id, _r=role, _s=school_id: {
        "user_id": _u, "email": f"{_u}@test", "name": _u, "role": _r,
        "school_id": _s, "status": "active",
    }
    client = app.test_client()
    client.set_cookie("access_token", f"tok-{user_id}-{uuid.uuid4().hex[:8]}")
    return client


@pytest.fixture(autouse=True)
def _restore_session():
    original = authmod._session_for
    yield
    authmod._session_for = original


def body_of(resp):
    return resp.get_data(as_text=True)


# ── the documents ────────────────────────────────────────────────

class TestTheReportCard:
    """One student's result, as the sheet a parent is handed."""

    def render(self, app, **overrides):
        context = {
            "submission": _submission("sub-released", "murid-1", "Ahmad Pratama",
                                      "published", True, 55.5),
            "exam": dict(EXAM), "released": True, "show_key": True,
            "student_name": "Ahmad Pratama", "student_nisn": "1234567890",
            "student_nis": "1001", "teacher_name": "LT Guru 01",
            "school": {"name": "SMP Negeri 1 Contoh", "address": "Jl. Pendidikan 1",
                       "city": "Bandung", "npsn": "12345678"},
            "printed_on": "13-09-2026 18:00 WIB",
        }
        context.update(overrides)
        # The embedded exam row would otherwise shadow the exam kwarg.
        context["submission"].pop("exams", None)
        return app.jinja_env.get_template("print/report_card.html").render(**context)

    def test_it_carries_the_result_a_school_records(self, app):
        html = self.render(app)
        for expected in ["Ahmad Pratama", "1234567890", "Ujian Matematika",
                         "Matematika", "LT Guru 01", "SMP Negeri 1 Contoh",
                         "Skor MCQ", "Penalti", "Pelanggaran", "Nilai Akhir"]:
            assert expected in html, f"{expected!r} is missing from the report card"
        assert ">55.5<" in html, "the final score itself"

    def test_it_carries_the_marked_answer_sheet(self, app):
        html = self.render(app)
        assert PNG in html, "the student's own writing"
        assert OVERLAY in html, "the teacher's annotation"
        assert "x = 2" in html, "a text box placed on the page"
        assert "Lembar Jawaban" in html
        assert "Langkah sudah benar" in html, "the per-question comment"
        assert "Skor 30" in html, "the per-question mark"

    def test_it_prints_paper_not_the_interface(self, app):
        """The defect this replaces: printing the screen printed the app."""
        html = self.render(app)
        for chrome in ["nav-item", "sidebar", "x-data", "bottom-nav", "csrf"]:
            assert chrome not in html, f"{chrome!r} belongs to the app, not the sheet"
        assert "@page { size: A4" in html
        assert "http://" not in html and "https://" not in html, "no remote assets"

    def test_the_answer_key_is_withheld_unless_it_may_be_shown(self, app):
        held = self.render(app, show_key=False,
                           submission={**_submission("s", "murid-1", "Ahmad", "draft", False, 0),
                                       "answers": {}})
        assert "Kunci" not in held
        assert "Kunci" in self.render(app)

    def test_a_teacher_printing_before_release_says_so(self, app):
        early = self.render(app, released=False,
                            submission=_submission("s", "murid-1", "Ahmad", "submitted", False, 0))
        assert "Hasil belum dirilis" in early
        assert "Kunci" in early, "the teacher still prints their own key"

    def test_a_stamp_says_when_and_in_which_timezone(self, app):
        from app.services.report_card_service import print_stamp
        from datetime import datetime, timezone

        # 11:00 UTC is 18:00 in WIB, where the school files the sheet.
        stamp = print_stamp(datetime(2026, 9, 13, 11, 0, tzinfo=timezone.utc))
        assert stamp == "13-09-2026 18:00 WIB"


class TestTheExamSheet:
    """One exam's results, as the sheet a school keeps."""

    ROSTER = [
        _submission("s1", "murid-2", "Bella Safira", "published", True, 88.0),
        _submission("s2", "murid-1", "Ahmad Pratama", "published", True, 55.5),
        _submission("s3", "murid-3", "Rina Melati", "submitted", False, 40.0),
    ]
    STATS = {"avg": 61.2, "max": 88.0, "min": 40.0, "count": 3,
             "passed": 1, "pass_rate": 33, "threshold": 70}

    def render(self, app, **overrides):
        context = {
            "exam": dict(EXAM), "roster": [dict(r) for r in self.ROSTER],
            "stats": dict(self.STATS), "class_names": ["VII-A"],
            "school": {"name": "SMP Negeri 1 Contoh", "city": "Bandung"},
            "teacher_name": "LT Guru 01", "printed_on": "13-09-2026 18:00 WIB",
            "pass_mark": 70,
        }
        context.update(overrides)
        return app.jinja_env.get_template("teacher/print_exam_report.html").render(**context)

    def test_it_lists_the_class_in_ranked_order(self, app):
        html = self.render(app)
        assert all(n in html for n in ["Bella Safira", "Ahmad Pratama", "Rina Melati"])
        assert html.index("Bella Safira") < html.index("Ahmad Pratama") < html.index("Rina Melati")
        assert "1234567890" in html, "the NISN a school matches on"

    def test_it_carries_the_statistics_that_were_on_screen(self, app):
        html = self.render(app)
        for value in [">61.2<", ">88.0<", ">40.0<", ">33%<"]:
            assert value in html, f"{value} is missing"
        assert "Ambang Tuntas" in html and "VII-A" in html

    def test_it_prints_paper_not_the_interface(self, app):
        html = self.render(app)
        for chrome in ["nav-item", "sidebar", "x-data", "Sale", "csrf"]:
            assert chrome not in html
        assert "@page { size: A4" in html
        assert "http://" not in html and "https://" not in html

    def test_it_has_somewhere_to_sign(self, app):
        html = self.render(app)
        assert "Kepala Sekolah" in html
        assert "Guru Mata Pelajaran" in html

    def test_an_exam_with_no_submissions_still_prints(self, app):
        html = self.render(app, roster=[], stats={"avg": 0, "max": 0, "min": 0,
                                                 "count": 0, "passed": 0,
                                                 "pass_rate": 0, "threshold": 70},
                           class_names=[], school={}, teacher_name="")
        assert "Belum ada nilai" in html
        assert "Kepala Sekolah" in html


# ── who may print ────────────────────────────────────────────────

PRINT_URLS = [
    "/student/results/sub-released/print",
    "/teacher/results/print?exam_id=exam-1",
    "/teacher/submissions/sub-released/print",
]


class TestWhoMayPrint:
    def test_every_print_route_needs_a_login(self, app):
        client = app.test_client()
        for url in PRINT_URLS:
            resp = client.get(url)
            assert resp.status_code == 302, url
            assert "/auth/login" in resp.headers["Location"], url

    def test_a_student_prints_their_own_released_card(self, app):
        client = sign_in(app, "murid-1", "murid", "school-a")
        resp = client.get("/student/results/sub-released/print")
        assert resp.status_code == 200
        html = body_of(resp)
        assert "Ahmad Pratama" in html
        assert ">55.5<" in html

    def test_a_student_cannot_print_another_students_card(self, app):
        """The lookup is scoped by student, so this is a miss rather than a check."""
        client = sign_in(app, "murid-1", "murid", "school-a")
        resp = client.get("/student/results/sub-other/print")
        assert resp.status_code == 302
        assert "Bella" not in body_of(resp)

    def test_a_student_cannot_print_an_unreleased_result(self, app):
        """The same release rule the screen and the PDF obey."""
        client = sign_in(app, "murid-1", "murid", "school-a")
        resp = client.get("/student/results/sub-unreleased/print")
        assert resp.status_code == 302
        assert "/student/results" in resp.headers["Location"]
        assert "Kunci" not in body_of(resp)

    def test_a_missing_submission_is_not_a_crash(self, app):
        client = sign_in(app, "murid-1", "murid", "school-a")
        resp = client.get("/student/results/does-not-exist/print")
        assert resp.status_code == 302

    def test_the_owning_teacher_prints_a_card(self, app):
        client = sign_in(app, "guru-a", "guru", "school-a")
        resp = client.get("/teacher/submissions/sub-released/print")
        assert resp.status_code == 200, f"redirected to {resp.headers.get('Location')}"
        assert "Ahmad Pratama" in body_of(resp)

    def test_a_teacher_from_another_school_is_refused(self, app):
        """The route is school-scoped, not merely login-scoped.

        Asserted against a signed-in teacher, not an anonymous caller: an
        anonymous request would also be refused, and would prove nothing.
        """
        client = sign_in(app, "guru-b", "guru", "school-b")
        assert app.test_client().get("/teacher/submissions/sub-released/print").status_code == 302
        resp = client.get("/teacher/submissions/sub-released/print")
        assert resp.status_code == 302
        assert "Ahmad Pratama" not in body_of(resp)

    def test_the_exam_sheet_belongs_to_the_exam_owner(self, app):
        owner = sign_in(app, "guru-a", "guru", "school-a")
        resp = owner.get("/teacher/results/print?exam_id=exam-1")
        assert resp.status_code == 200
        html = body_of(resp)
        assert "Ujian Matematika" in html
        assert "VII-A" in html, "the class the sheet names"

        intruder = sign_in(app, "guru-b", "guru", "school-b")
        denied = intruder.get("/teacher/results/print?exam_id=exam-1")
        assert denied.status_code == 302
        assert "Ujian Matematika" not in body_of(denied)

    def test_the_exam_sheet_needs_an_exam(self, app):
        client = sign_in(app, "guru-a", "guru", "school-a")
        resp = client.get("/teacher/results/print")
        assert resp.status_code == 302
        assert "/teacher/results" in resp.headers["Location"]

    def test_a_missing_exam_is_handled(self, app):
        client = sign_in(app, "guru-a", "guru", "school-a")
        resp = client.get("/teacher/results/print?exam_id=nope")
        assert resp.status_code == 302


# ── the screens have to offer the documents ──────────────────────

class TestTheScreensOfferTheDocument:
    """A print route nobody links to is a route nobody uses.

    Both screens used to print themselves. Each now carries a control that
    points at its document, so undoing the feature takes a deliberate edit
    rather than a forgotten button.
    """

    TEMPLATES = Path(__file__).resolve().parents[2] / "app" / "templates"

    def read(self, relative):
        return (self.TEMPLATES / relative).read_text(encoding="utf-8")

    def test_the_student_result_offers_the_report_card(self):
        page = self.read("student/result_detail.html")
        assert "/student/results/{{ submission.id }}/print" in page

    def test_the_corrected_answer_offers_the_report_card(self):
        page = self.read("teacher/grade_detail.html")
        assert "/teacher/submissions/{{ submission.id }}/print" in page
        assert "window.print()" not in page, "printing that page prints the marking tools"

    def test_the_exam_results_offer_the_exam_sheet(self):
        page = self.read("teacher/results.html")
        assert "/teacher/results/print?exam_id={{ exam_id }}" in page


# ── the numbers the sheet prints are the numbers the screen shows ──

# Three different students, for the statistics.
THREE_STUDENTS = {**ROWS, "submissions": [
    _submission("s1", "murid-2", "Bella Safira", "published", True, 88.0),
    _submission("s2", "murid-1", "Ahmad Pratama", "published", True, 55.5),
    _submission("s3", "murid-3", "Rina Melati", "submitted", False, 40.0),
]}


class TestTheSharedAssembly:
    """One exam's results are assembled once, so two renderings cannot disagree."""

    def test_stats_match_the_roster_they_describe(self, app):
        from app.routes.teacher import _exam_results

        subs, scan, online, stats = _exam_results(FakeSupabase(THREE_STUDENTS), "exam-1")
        assert stats["count"] == len(subs) == 3
        assert stats["max"] == max(s["final_score"] for s in subs) == 88.0
        assert stats["min"] == min(s["final_score"] for s in subs) == 40.0
        assert stats["avg"] == 61.2
        assert stats["passed"] == 1, ">= the 70 pass mark"
        assert stats["pass_rate"] == 33
        assert stats["threshold"] == 70
        assert len(scan) + len(online) == len(subs)

    def test_a_resubmission_is_counted_once(self, app):
        """A student who sits the exam twice is one line on the sheet.

        The row that survives is the latest, so a retake does not look like two
        students and does not double-count in the statistics.
        """
        from app.routes.teacher import _exam_results

        older = {**_submission("s-old", "murid-1", "Ahmad Pratama", "submitted", False, 40.0),
                 "submitted_at": "2026-09-13T03:00:00+00:00"}
        newer = {**_submission("s-new", "murid-1", "Ahmad Pratama", "published", True, 80.0),
                 "submitted_at": "2026-09-13T04:00:00+00:00"}
        rows = {**ROWS, "submissions": [older, newer]}

        subs, _, _, stats = _exam_results(FakeSupabase(rows), "exam-1")
        assert len(subs) == 1 and stats["count"] == 1
        assert subs[0]["id"] == "s-new", "the latest attempt is the one that counts"

    def test_json_answers_come_back_as_data(self, app):
        """A row whose answers are still text would print a dash instead of a NISN."""
        import json
        from app.routes.teacher import _exam_results

        rows = {**ROWS, "submissions": [
            {**ROWS["submissions"][0], "answers": json.dumps(ANSWERS)},
        ]}
        subs, _, _, _ = _exam_results(FakeSupabase(rows), "exam-1")
        assert isinstance(subs[0]["answers"], dict)
        assert subs[0]["answers"]["_nisn"] == "1234567890"
