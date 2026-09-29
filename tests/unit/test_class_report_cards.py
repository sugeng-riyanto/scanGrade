"""A whole class's report cards, as the one document a school hands back.

Handing back thirty papers used to be thirty clicks: open a pupil's print page,
print, close, repeat. The sheet itself already existed and is already the sheet a
parent is handed, so what this feature adds is *the set* — one document, one
print, every paper in the class.

Two properties make that worth doing rather than looping the browser, and both are
what this suite holds:

* **The class document is the single sheet, repeated.** Not a second layout that
  resembles it — the same template, the same sheet markup, the same numbers,
  rendered once per paper. A separate class template would be a second copy of a
  document a school files, and two copies drift: the day one of them learns about a
  new column, the paper in the parent's hand and the paper in the file disagree.
  The first test here is that the single card still *is* that document, and the
  last is that there is no second template to drift from.
* **Its cost does not scale with the class.** `load_report_card` reads a paper in
  four round-trips: the submission, the pupil, the school, the teacher. Looping it
  over a class of thirty is 120 round-trips from one request, on a box that serves
  500 pupils with three workers — so the batch reads the same rows in a bounded
  handful, and a test counts them with a stand-in that records every read.

The ordering is a hand-back order (by name), never by mark: a stack of report
cards sorted by score is a stack that announces the ranking as it is passed down
the row.
"""
from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
CARD_TEMPLATE = ROOT / "app" / "templates" / "print" / "report_card.html"
RESULTS = ROOT / "app" / "templates" / "teacher" / "results.html"
ROUTES = ROOT / "app" / "routes" / "teacher.py"

EXAM = {
    "id": "ex-1", "title": "Ujian Matematika", "subject": "Matematika",
    "teacher_id": "guru-1", "school_id": "school-1", "total_questions": 2,
    "question_types": {"0": "mcq"}, "answer_key": {"0": "A"},
    "question_weights": {"0": 10}, "pdf_page_urls": [],
    "class_ids": ["kls-7a", "kls-7b"],
}


def _submission(sid, student_id, name, status="published", published=True,
                score=60.0, class_id="kls-7a"):
    return {
        "id": sid, "exam_id": "ex-1", "student_id": student_id,
        "answers": {"0": {"answer": "A"}}, "score": score, "max_score": 100,
        "violations": 0, "penalty": 0, "final_score": score, "status": status,
        "is_published": published, "started_at": None, "submitted_at": None,
        "graded_at": None, "teacher_feedback": {},
        "exams": dict(EXAM), "_class_id": class_id,
    }


class _Res:
    def __init__(self, data):
        self.data = data


class _Query:
    """A PostgREST stand-in that RECORDS every read, which is the point."""

    def __init__(self, fake, table):
        self.fake, self.table, self._eq, self._in = fake, table, [], None

    def select(self, *cols, **kw):
        return self

    def eq(self, col, val):
        self._eq.append((col, val))
        return self

    def in_(self, col, vals):
        self._in = (col, [str(v) for v in vals])
        return self

    def maybe_single(self):
        return self

    def order(self, *a, **k):
        return self

    def execute(self):
        rows = [dict(r) for r in self.fake.tables.get(self.table, [])]
        for col, val in self._eq:
            rows = [r for r in rows if r.get(col) == val]
        if self._in:
            col, vals = self._in
            rows = [r for r in rows if str(r.get(col)) in vals]
        self.fake.reads.append((self.table, tuple(self._eq), self._in))
        return _Res(rows)


class _Tables:
    def __init__(self, tables):
        self.tables = tables
        self.reads = []

    def table(self, name):
        return _Query(self, name)


def _world(papers, profiles, schools=None):
    """A stand-in holding one exam's papers, with a class per pupil."""
    tables = {
        "submissions": papers,
        "profiles": profiles,
        "schools": schools if schools is not None else [
            {"id": "school-1", "name": "SMP Negeri 1 Contoh", "address": "Jl. P 1",
             "npsn": "12345678", "city": "Bandung", "province": None,
             "logo_url": None},
        ],
    }
    return _Tables(tables)


def _paper_for(index, name, class_id="kls-7a", score=60.0, profile=True):
    sid, uid = f"sub-{index}", f"murid-{index}"
    profiles = []
    if profile:
        profiles.append({"id": uid, "full_name": name, "nisn": f"900{index}",
                         "nis": f"10{index}", "school_id": "school-1",
                         "class_id": class_id})
    return _submission(sid, uid, name, score=score, class_id=class_id), profiles


#: Names in alphabetical order with their marks in the *opposite* order: the worst
#: mark belongs to the first name. A fixture where the two orders agree cannot tell
#: an ordering by name from an ordering by mark — which is the mistake this suite
#: exists to catch, so it is not one to build into its own data.
ROSTER = (("Ahmad Pratama", 55.5), ("Bella Safira", 70.0), ("Rina Melati", 88.0))


def _class(n=3, class_id="kls-7a"):
    papers, profiles = [], []
    for i, (name, score) in enumerate(ROSTER[:n], 1):
        paper, profs = _paper_for(i, name, class_id=class_id, score=score)
        papers.append(paper)
        profiles.extend(profs)
    profiles.append({"id": "guru-1", "full_name": "LT Guru 01", "nisn": None,
                     "nis": None, "school_id": "school-1", "class_id": None})
    return papers, profiles


# ── 1. the batch's cost is bounded, not per paper ────────────────────────────

def test_a_class_of_thirty_is_read_in_a_handful_of_queries():
    """The rule that makes this page possible at all: a paper per round-trip is
    120 round-trips for one class, on the box that is serving the exams."""
    from app.services.report_card_service import load_report_cards

    papers, profiles = [], [{"id": "guru-1", "full_name": "LT Guru 01",
                             "school_id": "school-1", "class_id": None}]
    for i in range(1, 31):
        paper, profs = _paper_for(i, f"Murid {i:02d}")
        papers.append(paper)
        profiles.extend(profs)
    fake = _world(papers, profiles)

    cards = load_report_cards(fake, "ex-1")

    assert len(cards) == 30, "every paper in the class"
    assert len(fake.reads) <= 4, (
        f"{len(fake.reads)} queries for 30 papers — the load is per paper, not per "
        f"class: {[r[0] for r in fake.reads]}")


def test_the_batch_reads_each_table_once():
    from app.services.report_card_service import load_report_cards

    papers, profiles = _class()
    fake = _world(papers, profiles)

    load_report_cards(fake, "ex-1")

    seen = [name for name, _, _ in fake.reads]
    assert seen.count("submissions") == 1, "the papers are read once, not per pupil"
    assert seen.count("profiles") == 1, "pupils and the teacher come from one read"
    assert seen.count("schools") == 1, "the letterhead is one school"


# ── 2. a hand-back order, and only the requested class ───────────────────────

def test_the_stack_is_ordered_by_name_not_by_mark():
    """A stack sorted by score announces the ranking as it is handed down the row."""
    from app.services.report_card_service import load_report_cards

    papers, profiles = _class()
    fake = _world(papers, profiles)

    cards = load_report_cards(fake, "ex-1")

    assert [c["student_name"] for c in cards] == [
        "Ahmad Pratama", "Bella Safira", "Rina Melati"]
    scores = [c["submission"]["final_score"] for c in cards]
    assert scores == [55.5, 70.0, 88.0], (
        "the marks came back in the order they were read, not the order the names "
        "are in — and the fixture's marks run the other way on purpose, so an "
        "ordering by mark cannot pass this")


def test_a_paper_whose_owner_has_no_profile_is_still_printed():
    """Dropping it would make the document shorter than the class with nothing
    saying so, which is how a missing paper reaches a parent as \"never sat\"."""
    from app.services.report_card_service import load_report_cards

    papers, profiles = [], [{"id": "guru-1", "full_name": "LT Guru 01",
                             "school_id": "school-1", "class_id": None}]
    for i, (name, has) in enumerate([("Ahmad Pratama", True),
                                     ("Tanpa Profil", False),
                                     ("Bella Safira", True)], 1):
        paper, profs = _paper_for(i, name, profile=has)
        papers.append(paper)
        profiles.extend(profs)
    fake = _world(papers, profiles)

    cards = load_report_cards(fake, "ex-1")

    assert [c["student_name"] for c in cards] == ["Ahmad Pratama", "Bella Safira", ""], (
        "a paper with no profile must be carried with no name — and it sorts last, "
        "because a stack handed down a row cannot open with the one paper nobody "
        "can be named for")
    assert len(cards) == 3, "the count the document prints is the count it carries"


def test_a_class_filter_keeps_exactly_that_class():
    from app.services.report_card_service import load_report_cards

    papers, profiles = [], [{"id": "guru-1", "full_name": "LT Guru 01",
                             "school_id": "school-1", "class_id": None}]
    for i, (name, kls) in enumerate([("Ahmad Pratama", "kls-7a"),
                                     ("Bella Safira", "kls-7b"),
                                     ("Rina Melati", "kls-7a")], 1):
        paper, profs = _paper_for(i, name, class_id=kls)
        papers.append(paper)
        profiles.extend(profs)
    fake = _world(papers, profiles)

    only_a = load_report_cards(fake, "ex-1", class_id="kls-7a")

    assert [c["student_name"] for c in only_a] == ["Ahmad Pratama", "Rina Melati"]


def test_a_class_nobody_is_in_returns_nothing_not_everything():
    """The dangerous failure: a filter that falls back to \"no filter\" prints
    another class's marks into the document a teacher is about to hand out."""
    from app.services.report_card_service import load_report_cards

    papers, profiles = _class()
    fake = _world(papers, profiles)

    assert load_report_cards(fake, "ex-1", class_id="kls-9z") == []


def test_the_requested_class_must_be_one_the_exam_names():
    """A route-level rule, written as a function so it is testable without a
    client: the exam's own `class_ids` is the only thing that may widen a request."""
    from app.services.report_card_service import select_class

    exam = dict(EXAM)
    assert select_class(exam, None) == (None, None), "no request means the whole sitting"
    assert select_class(exam, "kls-7a") == ("kls-7a", None)
    assert select_class(exam, "kls-9z")[1], (
        "a class the exam is not assigned to must be refused, not silently ignored")


# ── 3. one document, one sheet per paper ─────────────────────────────────────

def _render(app, cards, **overrides):
    context = {"cards": cards, "printed_on": "13-09-2026 18:00 WIB", "show_key": True}
    context.update(overrides)
    return app.jinja_env.get_template("print/report_card.html").render(**context)


def _cards():
    from app.services.report_card_service import load_report_cards

    papers, profiles = _class()
    return load_report_cards(_world(papers, profiles), "ex-1")


def test_a_class_renders_one_sheet_per_paper(app):
    html = _render(app, _cards())

    assert html.count("Laporan Hasil Ujian") == 3, "one document header per paper"
    assert html.count('class="sign"') == 3, "every paper carries its own signature block"
    for name in ("Ahmad Pratama", "Bella Safira", "Rina Melati"):
        assert name in html, f"{name} is missing from the class set"


def test_every_paper_after_the_first_starts_on_a_fresh_sheet(app):
    """A card carries its own marked pages, so it can run to several sheets — the
    break belongs *between* papers, never inside one."""
    html = _render(app, _cards())

    assert re.search(r"\.sheet\s*\+\s*\.sheet\s*\{[^}]*break-before:\s*page", html), (
        "nothing stops one pupil's card starting halfway down the previous one's "
        "last page")
    assert "page-break-before: always" in html, (
        "the older property too: the browser that prints this is often not current")


def test_the_document_says_how_many_papers_it_carries(app):
    """So a set that is short says so on its face, rather than looking complete."""
    html = _render(app, _cards())
    assert re.search(r">\s*3\s*kertas", html) or "3 kertas" in html, html[:2000]


def test_one_paper_is_still_the_document_it_always_was(app):
    html = _render(app, _cards()[:1])

    assert "Laporan Hasil Ujian" in html
    assert "Ahmad Pratama" in html
    assert "Dokumen siap dicetak" in html, (
        "the single card lost the toolbar sentence it had before this feature")
    assert "kertas" not in html.split("<body")[1], (
        "a count of one is not a set: the page a family is handed should not mention "
        "how many other children were in the sitting")


def test_a_paper_that_is_not_released_still_says_so_inside_the_set(app):
    """Mixed states happen — a teacher hands back a marked paper while a resit is
    still open — and the note is per paper, not per document."""
    cards = _cards()
    cards[1]["submission"]["is_published"] = False
    cards[1]["submission"]["status"] = "graded"
    cards[1]["released"] = False

    html = _render(app, cards)

    assert "Hasil belum dirilis" in html
    assert html.count("Hasil belum dirilis") == 1, (
        "the note belongs to the paper it is true of, not to the document")


# ── 4. the single card and the class set are the same document ───────────────

def test_there_is_no_second_template_to_drift_from(app):
    """The strongest form of \"the file and the home copy cannot disagree\": one
    template renders both, so there is nothing to keep in step."""
    assert not (CARD_TEMPLATE.parent / "report_cards.html").exists(), (
        "a second class document exists — it will drift from the single card")
    source = ROUTES.read_text(encoding="utf-8")
    assert source.count('render_template("print/report_card.html"') == 2, (
        "the class route and the single-card route must render the same template")


def test_the_single_card_route_still_hands_the_template_one_card():
    source = ROUTES.read_text(encoding="utf-8")
    block = source.split("def print_submission_card", 1)[1].split("\ndef ", 1)[0]
    assert "cards=[card]" in block, (
        "the single-card route no longer passes a list, so it renders an empty document")


def test_the_class_document_goes_through_the_screen_page_s_guard():
    """It carries every pupil's name and mark, so it cannot be the route that is
    easier to reach than the results list."""
    source = ROUTES.read_text(encoding="utf-8")
    block = source.split("def results_report_cards", 1)[1].split("\ndef ", 1)[0]
    assert "_guard_exam(" in block, (
        "the class document is not behind the exam guard the screen page uses")
    assert "class_id" in block, "the requested class is not read from the query string"


def test_the_results_row_offers_the_one_click(app):
    html = RESULTS.read_text(encoding="utf-8")
    assert "/teacher/results/report-cards?exam_id=" in html, (
        "there is no way to ask for the class set from the page a teacher is on")
    link = html.split("/teacher/results/report-cards", 1)[1].split("</a>", 1)[0]
    assert 'target="_blank"' in link, (
        "opening the class set in the tab you are on loses the results list")
    assert "{{ exam_id }}" in link, "the link does not name the exam"
