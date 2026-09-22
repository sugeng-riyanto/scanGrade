"""The item analysis as a teacher meets it: the page, its documents, and the door.

The page is rendered here rather than grepped, because the parts that fail quietly
are the ones a grep cannot see: a flag whose label lookup points at a key the
catalogue does not have (so the cell renders blank), a chart whose data never
reaches the attribute Alpine reads, a download link pointing at the wrong exam.

Two other contracts live here as well, both about *claims*:

* the routes are one door — every one of them funnels through `_analysis_of`, so
  there is a single place that checks who may open another teacher's exam;
* `/guide/skor` is a document that drifted once already (it still described the
  MCQ/Esai pool model after the app had moved to a per-type mark scheme), so its
  numbers are compared with the code that computes them.
"""
from __future__ import annotations

import contextlib
import json
import re
from pathlib import Path

import pytest

from app.services import analysis_frameworks as af

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "app" / "templates"
PAGE = (TEMPLATES / "teacher" / "analysis.html").read_text(encoding="utf-8")
GRADING = (TEMPLATES / "teacher" / "grading_queue.html").read_text(encoding="utf-8")
RESULTS = (TEMPLATES / "teacher" / "results.html").read_text(encoding="utf-8")
GUIDE = (TEMPLATES / "guide" / "skor.html").read_text(encoding="utf-8")
TEACHER = (ROOT / "app" / "routes" / "teacher.py").read_text(encoding="utf-8")


EXAM = {
    "id": "exam-1", "title": "Mid Semester 1", "subject": "Fisika",
    "total_questions": 4,
    "question_types": {"0": "mcq", "1": "mcq", "2": "true_false", "3": "essay_canvas"},
    "question_weights": {"0": 25.0, "1": 25.0, "2": 25.0, "3": 25.0},
    "answer_key": {"0": "A", "1": "C", "2": "true"},
}


def _submissions():
    rows = []
    for j in range(10):
        strong = j < 5
        rows.append({
            "student_name": f"Murid {j:02d}",
            "answers": {"0": "A" if strong else "B", "1": "C" if strong else "A",
                        "2": "true" if strong else "false", "3": {"text": "jawaban"}},
            "teacher_feedback": {"scores": {"3": 80 if strong else 40}},
            "final_score": 88 if strong else 42,
        })
    return rows


@pytest.fixture(scope="module")
def rendered(app):
    """The page, exactly as the route hands it to Jinja."""
    from app.routes.teacher import _chart_payload
    from app.services import item_analysis

    analysis = item_analysis.analyse(EXAM, _submissions())
    chart = _chart_payload(analysis)
    with _signed_in(app, "/teacher/analysis/exam-1"):
        html = app.jinja_env.get_template("teacher/analysis.html").render(
            exam=EXAM, analysis=analysis, chart=chart, framework=af.resolve(None))
    return {"html": html, "analysis": analysis, "chart": chart}


@contextlib.contextmanager
def _signed_in(app, path):
    from flask import g
    with app.test_request_context(path):
        g.user_id = "tea-1"
        g.user_name = "Guru Uji"
        g.user_email = "guru@example.test"
        g.user_role = "guru"
        g.tz_offset = 7
        g.show = {}
        yield


# ── it renders, and it renders the numbers ───────────────────────────────────

class TestThePageRenders:
    def test_every_item_gets_exactly_one_row(self, rendered):
        rows = re.findall(r'data-item-row="(\d+)"', rendered["html"])
        assert rows == [str(item.index + 1)
                        for item in rendered["analysis"].items], \
            "one row per question, in order — not one per submission"

    def test_the_documented_numbers_are_on_the_page(self, rendered):
        """The page's P and D are the analysis's own, to the digit it rounds
        them to. Round twice at your peril: the CSV uses one precision and the
        screen another, and a teacher comparing them sees a discrepancy."""
        analysis, html = rendered["analysis"], rendered["html"]
        first = analysis.items[0]
        assert f">{round(first.pct, 1)}<" in html or f">{first.pct:.1f}<" in html
        assert f">{round(first.discrimination, 2)}<" in html
        assert f">{round(first.measure, 2)}<" in html

    def test_no_jinja_placeholder_survives(self, rendered):
        assert "{{" not in rendered["html"] and "{%" not in rendered["html"]
        assert "Undefined" not in rendered["html"]

    def test_the_charts_get_data_through_the_attribute_alpine_reads(self, rendered):
        html = rendered["html"]
        m = re.search(r'x-data="itemAnalysis\((.*?)\)"', html, re.S)
        assert m, "the component is not initialised with its payload"
        import html as html_mod
        data = json.loads(html_mod.unescape(m.group(1)))
        assert len(data["items"]) == len(rendered["analysis"].items)
        assert data["summary"]["students"] == 10
        # Only the drawings this framework publishes are rendered at all. CTT asks
        # how the questions behaved on this class, so it gets the item map and the
        # ability spread; the logit-scale chart belongs to the Rasch view, and the
        # framework tests pin which panels each one shows.
        assert data["framework"] == "ctt"
        assert html.count("<canvas") == 2
        assert 'x-ref="mapCanvas"' in html and 'x-ref="peopleCanvas"' in html
        assert 'x-ref="difficultyCanvas"' not in html
        # …and Alpine runs the component's own `init()`, so the element must not ask
        # for it again. It did, and the browser console said so: the second call
        # created a second Chart.js chart on a canvas the first one still owned.
        assert 'x-init="init()"' not in html, (
            "the charts component is initialized twice — Alpine already calls init()")

    def test_every_kind_a_question_can_be_gets_a_label(self, rendered):
        """`item.kind` is a `question_types.KIND_*` token; the catalogue is keyed
        by it. A token with no entry renders an empty type cell — and no other
        test would notice, because the table still has its row."""
        from app.services import analysis_report
        from app.services.question_types import (KIND_CHOICE, KIND_DRAG,
                                                 KIND_ESSAY, KIND_MATCH,
                                                 KIND_ORDER, KIND_TRUE_FALSE,
                                                 question_kind)
        for raw in ("mcq", "true_false", "match", "drag_drop", "order", "essay"):
            token = question_kind(raw)
            assert token in analysis_report.KIND_LABELS
        assert {KIND_CHOICE, KIND_TRUE_FALSE, KIND_MATCH, KIND_DRAG, KIND_ORDER,
                KIND_ESSAY} == set(analysis_report.KIND_LABELS)
        for token in sorted(analysis_report.KIND_LABELS):
            assert re.search(rf"^\s*{token}:\s*\{{ id:", PAGE, re.M), \
                f"the page has no label for the kind {token}"

    def test_every_finding_gets_a_label(self, rendered):
        from app.services import analysis_report
        for name in sorted(analysis_report.FLAG_LABELS):
            assert re.search(rf"^\s*{name}:\s*\{{ id:", PAGE, re.M), \
                f"the page has no label for the finding {name}"

    def test_every_note_the_module_can_raise_gets_a_sentence(self, rendered):
        from app.services import analysis_report
        for name in sorted(analysis_report.NOTE_LABELS):
            assert re.search(rf"^\s*{name}:\s*\{{ id:", PAGE, re.M), \
                f"the page never explains the note {name}"

    def test_each_row_shows_its_own_finding_in_the_readers_language(self, rendered):
        """Both halves at once: the lookup is keyed by *this* row's flag (so a
        row cannot silently show a neighbour's label), and it is indexed by
        `lang` (so the findings follow the toggle like everything else)."""
        html = rendered["html"]
        for item in rendered["analysis"].items:
            assert f"window.SG_ITEM_WORDS.flag['{item.flag}'][lang]" in html, \
                f"row {item.index + 1} does not label its own finding ({item.flag})"

    def test_the_flag_a_row_shows_is_a_finding_the_catalogue_knows(self, rendered):
        from app.services import analysis_report
        for item in rendered["analysis"].items:
            assert item.flag in analysis_report.FLAG_LABELS, (
                f"item {item.index + 1} is flagged {item.flag!r}, which the page "
                "cannot render")
        for note in rendered["analysis"].notes:
            assert note in analysis_report.NOTE_LABELS


# ── Winsteps' block on the screen ───────────────────────────────────────────

def _page_payload(html):
    """The payload Alpine is given, parsed out of the attribute it reads."""
    import html as html_mod
    match = re.search(r'x-data="itemAnalysis\((.*?)\)"', html, re.S)
    assert match, "the component is not initialised with its payload"
    return json.loads(html_mod.unescape(match.group(1)))


def _catalogue_entry(name):
    """One `{ id: '…', en: '…' }` entry from the page's JS catalogue.

    Matched on the key *and* the brace that opens its entry, so a longer name that
    merely starts with this one ("se" against "separation:") cannot stand in for a
    definition that is not there.
    """
    match = re.search(rf"(?<![\w]){re.escape(name)}\s*:\s*\{{", PAGE)
    assert match, f"the page's catalogue has no entry for {name}"
    return PAGE[match.end():match.end() + 600]


class TestTheSeparationBlock:
    """The four lines of the separation block, on the page that shows them.

    The numbers are measured on the server — `analysis_report.separation_cells` is
    the same function the PDF, the CSV and the workbook call — and the browser only
    puts words on them, because the labels follow the language toggle and the
    numbers do not. Both halves are asserted: the payload *is* that function's
    output, and the table reads the payload rather than the summary it used to
    re-derive the lines from.
    """

    def test_the_page_shows_what_the_documents_show(self, rendered):
        from app.services import analysis_report
        data = _page_payload(rendered["html"])
        assert data["separation"] == analysis_report.separation_cells(
            rendered["analysis"].summary)
        assert len(data["separation"]) == 4, (
            "both sides, both rows: ten marked papers can support all four lines")
        assert 'data.separation' in PAGE, (
            "the table no longer reads the server's cells")
        assert "s.person_stats" not in PAGE and "s.item_stats" not in PAGE, (
            "the page went back to deriving the block from the summary")

    def test_each_label_is_the_readers_own(self):
        """REAL and MODEL, students and items: four catalogue words, in pairs, so
        the block is bilingual like the rest of the page."""
        for key in ("peopleRow", "itemsRow", "realRow", "modelRow"):
            assert re.search(rf"^\s*{key}:\s*\{{ id:", PAGE, re.M), \
                f"the block has no label for {key}"

    def test_a_block_with_nothing_in_it_says_so(self, app):
        """        Papers came in, and nothing in them can be separated.

        One question, one marked paper: neither side has two measures, so the block
        has no lines at all. Four rows of dashes used to be printed in that state,
        which reads as four measurements that all came out zero — the table is
        hidden and the sentence that replaces it names what is missing.
        """
        from app.routes.teacher import _chart_payload
        from app.services import item_analysis

        exam, submissions = _class(items=1, students=1)
        analysis = item_analysis.analyse(exam, submissions)
        assert analysis.summary.answered_papers, "the empty state is a different page"
        data = _chart_payload(analysis)
        assert data["separation"] == [], "an unkeyed paper can separate nothing"
        # Rendered as the Rasch view, because that is the framework whose promise
        # the separation block is: a classical report does not show it at all.
        with _signed_in(app, "/teacher/analysis/exam-1"):
            html = app.jinja_env.get_template("teacher/analysis.html").render(
                exam=exam, analysis=analysis, chart=data,
                framework=af.resolve("rasch"))
        assert "cannot be computed for this exam yet" in html
        assert 'x-show="!separationRows().length"' in html
        assert "{{" not in html and "{%" not in html

    def test_an_exam_with_no_papers_is_not_a_crash(self, app):
        """Nobody submitted anything. The page's own empty state covers it, and it
        has to get that far: the calibration used to index a question that did not
        exist and answer 500 instead."""
        from app.routes.teacher import _chart_payload
        from app.services import item_analysis

        exam, submissions = _class(items=5, students=0)
        analysis = item_analysis.analyse(exam, submissions)
        assert [item.measure for item in analysis.items] == [None] * 5
        assert [person.measure for person in analysis.people] == []
        data = _chart_payload(analysis)
        assert data["separation"] == []
        with _signed_in(app, "/teacher/analysis/exam-1"):
            html = app.jinja_env.get_template("teacher/analysis.html").render(
                exam=exam, analysis=analysis, chart=data, framework=af.resolve(None))
        assert "No answers to analyse yet" in html


def _class(items=5, students=0, keyed=True):
    """A small exam, so the empty states can be built without a database."""
    exam = dict(EXAM, total_questions=items,
                question_types={str(i): "mcq" for i in range(items)},
                answer_key=({str(i): "A" for i in range(items)} if keyed else {}))
    rows = []
    for j in range(students):
        rows.append({"student_name": f"Murid {j}",
                     "answers": {str(i): ("A" if j % 2 else "B")
                                 for i in range(items)},
                     "final_score": 50.0})
    return exam, rows


# ── one door for the permission check ───────────────────────────────────────

class TestTheDoor:
    """Three routes, one guard. The alternative — each route checking the exam
    itself — is how one of them ends up checking less."""

    def test_every_analysis_route_goes_through_the_same_guard(self):
        for view in ("exam_analysis", "exam_analysis_csv", "exam_analysis_pdf"):
            body = _view_body(view)
            assert "_analysis_of(" in body, f"{view} does not use the shared guard"
            assert "_guard_exam" not in body, (
                f"{view} reaches for the exam itself instead of the guarded loader")

    def test_the_shared_guard_is_the_one_that_checks_access(self):
        body = _view_body("_analysis_of")
        assert "_guard_exam(" in body
        assert "can_manage_exam" not in body, "the guard already decides this"

    def test_the_analysis_routes_require_a_teacher_or_admin(self):
        for view in ("exam_analysis", "exam_analysis_csv", "exam_analysis_pdf"):
            assert re.search(
                rf"@teacher_or_admin_required\ndef {view}\(", TEACHER), \
                f"{view} is reachable without a teacher session"

    def test_an_exam_with_no_weights_is_analysed_with_the_weights_it_was_marked_on(
            self, app, monkeypatch):
        """The grader falls back to the 70/30 default for an exam saved before
        schemes existed. Analysing it with an empty weight map would report every
        question as worth nothing — so this drives the loader and inspects what
        the analysis was actually handed, rather than grepping for a call."""
        import app.routes.teacher as teacher
        from app.services import item_analysis

        stored = {"id": "exam-1", "teacher_id": "tea-1", "school_id": "sch-1",
                  "title": "Lama", "total_questions": 4,
                  "question_types": {"0": "mcq", "1": "mcq", "2": "mcq", "3": "essay"},
                  "question_weights": None, "answer_key": {"0": "A"}}
        monkeypatch.setattr(teacher, "_guard_exam", lambda *a, **k: (dict(stored), None))
        monkeypatch.setattr(teacher, "_exam_results",
                            lambda *a, **k: ([], [], [], {}))
        handed = {}
        monkeypatch.setattr(teacher.item_analysis, "analyse",
                            lambda exam_row, subs: handed.update(exam_row) or None)

        with _signed_in(app, "/teacher/analysis/exam-1"):
            exam, analysis, err = teacher._analysis_of(object(), "exam-1")

        assert err is None and analysis is None
        weights = handed["question_weights"]
        assert weights, "the analysis was handed no weights at all"
        assert round(sum(float(v) for v in weights.values()), 2) == 100.0, \
            "the fallback weights do not add up to the paper's 100"
        assert item_analysis is not None

    def test_a_download_is_never_inline(self):
        """An inline CSV renders as a page of text in the browser; the whole
        point is a file."""
        for view in ("exam_analysis_csv", "exam_analysis_pdf"):
            body = _view_body(view)
            assert "as_attachment=True" in body, f"{view} serves the file inline"
            assert "download_name=" in body, f"{view} sends a file with no name"


def _view_body(name: str) -> str:
    """The source of one view function, by `def` to the next `def` at column 0."""
    m = re.search(rf"^def {re.escape(name)}\(.*?(?=^\S|\Z)", TEACHER, re.S | re.M)
    assert m, f"{name} not found in teacher.py"
    return m.group(0)


# ── the documents ────────────────────────────────────────────────────────────

class TestTheDocumentsAreOffered:
    def test_every_document_carries_this_exam_and_the_current_language(self, rendered):
        html = rendered["html"]
        assert "/teacher/analysis/exam-1/download.csv" in html
        assert "/teacher/analysis/exam-1/download.xlsx" in html
        assert "/teacher/analysis/exam-1/download.pdf" in html

    def test_the_language_parameter_follows_the_toggle(self):
        """The CSV, the workbook and the PDF are rendered on the server, so Alpine
        cannot translate them. The page has to hand the reader's choice to the
        link, or an English reader files an Indonesian report — the same defect as
        a hardcoded label, one layer further out."""
        for kind in ("csv", "xlsx", "pdf"):
            link = ":href=\"'{{ base }}/download." + kind + "?lang=' + lang"
            assert link in PAGE, f"the {kind} link does not carry the chosen language"
        # And the framework travels with it, for the same reason the language does:
        # the files are built on the server, so a document that did not carry the
        # reader's choice would name one framework while the page showed another.
        for kind in ("csv", "xlsx", "pdf"):
            link = ":href=\"'{{ base }}/download." + kind + "?lang=' + lang + '&framework='"
            assert link in PAGE, f"the {kind} link does not carry the chosen framework"
        # And the base differs by reader, which is the point of putting it in the
        # route: the teacher's copy links to the authenticated download, a shared
        # copy to its own redacted one, so the file a stranger can fetch is the
        # file with no names and no key in it.
        #
        # Which copy is rendered is the *route's* answer (`public_view`), not the
        # session's: asking whether the reader is signed in meant a teacher who
        # opened somebody's share link got the teacher's copy of a shared report,
        # key marker and all. The fallback is still the teacher's copy, which is
        # the only reader that reaches this block without the flag.
        assert "body(public_view|default(false, true), "
        assert "download_base|default('/teacher/analysis/'" in PAGE
        assert "body(true, download_base|default('/r/'" in PAGE
        assert "lang=id\"" not in PAGE, "a download link is pinned to Indonesian"

    def test_the_csv_carries_every_item_the_page_shows(self, rendered):
        from app.services import analysis_report
        text = analysis_report.analysis_csv(rendered["analysis"], EXAM, "en")
        for item in rendered["analysis"].items:
            assert f"{item.index + 1}," in text or f",{item.index + 1}," in text

    def test_the_pdf_builds_from_the_same_object(self, rendered):
        from app.services import analysis_report
        pdf = analysis_report.analysis_pdf(rendered["analysis"], EXAM, lang="en")
        assert pdf.startswith(b"%PDF-") and len(pdf) > 1500

    def test_the_workbook_route_is_one_door_like_the_others(self):
        """Guarded, and funnelled through `_analysis_of` — the one place that
        decides whether this caller may open this exam. A new document route is
        the easiest way to add a second door without noticing."""
        block = TEACHER.split('@teacher_bp.route("/analysis/<exam_id>/download.xlsx")',
                              1)
        assert len(block) == 2, "the workbook route is gone"
        body = block[1].split("@teacher_bp.route", 1)[0]
        assert "@teacher_or_admin_required" in body
        assert "_analysis_of(" in body, "the workbook route checks access by hand"
        assert "redirect_to=\"/teacher/results\"" in body
        assert ("application/vnd.openxmlformats-officedocument"
                ".spreadsheetml.sheet") in body, (
            "the workbook is not sent as a workbook, so a browser opens it as text")


# ── responsiveness, asserted the way it fails ───────────────────────────────

class TestItFitsASmallScreen:
    def test_the_item_table_scrolls_instead_of_squeezing(self):
        """Sixteen columns cannot fit 320 px. A table that shrinks instead of
        scrolling is the layout that made the previous report unreadable, so the
        wrapper is asserted on the item table itself and not "somewhere on the
        page" — the banding and split tables have their own wrappers."""
        # Matched by shape rather than by the whole tag: the table carries a
        # `data-item-table` hook for the copy control now, and a test that pins the
        # attribute list fails on the next attribute instead of on the layout.
        assert re.search(r'<div class="overflow-x-auto">\s*<table[^>]*'
                         r'min-w-\[820px\]', PAGE), (
            "the item table is not inside a horizontal scroller with a minimum "
            "width, so it will squash on a phone")

    def test_the_summary_grid_starts_at_two_columns_not_four(self):
        """Four cards across a 320 px screen is four unreadable columns."""
        assert "grid grid-cols-2 lg:grid-cols-4" in PAGE

    def test_the_page_stacks_its_columns_below_the_breakpoint(self):
        assert "grid-cols-1 xl:grid-cols-2" in PAGE, \
            "the chart row does not fall back to one column"

    def test_the_filter_row_wraps_rather_than_overflowing(self):
        assert "flex-wrap" in PAGE

    def test_the_grading_queue_is_one_column_below_lg(self):
        """The defect: a `w-72` queue beside a `flex-1` panel at every width put
        the marking panel at roughly 100 px on a phone."""
        assert "flex flex-col lg:flex-row" in GRADING, \
            "the grading split pane does not stack"
        assert re.search(r"w-full lg:w-72", GRADING), \
            "the student queue keeps a fixed width on a phone"
        assert "min-w-[15rem]" in GRADING, \
            "the student list has no minimum width, so it collapses horizontally"

    def test_the_score_presets_wrap(self):
        body = re.search(r"Quick score presets.*?</div>", GRADING, re.S).group(0)
        assert "flex-wrap" in body, "six preset buttons squeeze on a small phone"


# ── the link a teacher actually clicks ──────────────────────────────────────

def test_the_results_page_offers_the_analysis():
    assert 'href="/teacher/analysis/{{ exam_id }}"' in RESULTS
    assert "/teacher/analysis/" in RESULTS


# ── the guide has to describe the app that exists ───────────────────────────

class TestTheScoringGuideIsTrue:
    """`/guide/skor` described the MCQ/Esai pool model for a release after the
    app had moved to a per-type scheme. These are the assertions that make that
    a failing test instead of a page nobody re-reads."""

    def test_it_no_longer_teaches_the_old_pool_formula(self):
        assert "Skor MCQ" not in GUIDE, "the guide still pools marks as MCQ"
        assert "%MCQ" not in GUIDE and "%Esai" not in GUIDE
        assert "Nilai Akhir = max(0, min(Skor MCQ + Skor Esai, 100) &minus; Penalti)" \
            not in GUIDE

    def test_it_states_the_formula_the_code_uses(self):
        assert "max(0, min(Poin Terkumpul, 100) &minus; Penalti)" in GUIDE

    def test_it_names_every_question_type_the_scheme_prices(self):
        for label in ("Pilihan ganda", "Benar/Salah", "Menjodohkan",
                      "Seret &amp; lepas", "Mengurutkan", "Esai"):
            assert label in GUIDE, f"the scheme table has no row for {label}"

    def test_it_says_part_credit_exists_for_the_types_that_have_it(self):
        from app.services.question_types import PARTIAL_TYPES, MATCH, ORDER
        assert MATCH in PARTIAL_TYPES and ORDER in PARTIAL_TYPES
        assert "Nilai sebagian" in GUIDE

    def test_the_guide_follows_the_language_toggle(self):
        """It used to be pinned Indonesian. A school that runs the app in English
        still marks papers, so the page has to switch — and a page that both
        switches and declares its document language is a screen reader reading
        English with an Indonesian voice, which the coverage gate refuses."""
        # The *declaration*, not the word: the template's header comment explains
        # why it no longer carries one, and a test that cannot tell a comment from
        # a `{% set %}` is a test that deletes the explanation.
        assert "{% set content_lang" not in GUIDE, (
            "the guide declares a document language while its copy switches")
        assert "t('Pilihan ganda','Multiple choice')" in GUIDE, (
            "the scheme table's type names are not paired")
        assert "'Final mark = max(0, min(points earned, 100)" in GUIDE, (
            "the formula the page teaches has no English side")
        # The tab state is internal, and an Indonesian identifier is still an
        # Indonesian word to the sweep that reads Alpine expressions — it would
        # have to be excused one at a time until the sweep stopped meaning
        # anything. `teacher` says the same thing.
        assert "tab: 'teacher'" in GUIDE and "tab='student'" in GUIDE
        assert "'guru'" not in GUIDE and "'murid'" not in GUIDE, (
            "an Indonesian identifier survived in the tab state")

    def test_the_penalty_table_is_computed_not_copied(self):
        """The page has no penalty numbers of its own. The table is built from
        `calculate_graduated_penalty`, so there is no second copy to drift."""
        assert "data-penalty-row" in GUIDE
        assert "{% for row in penalty_schedule" in GUIDE
        assert "&minus;30" not in GUIDE, ("the schedule is typed into the page "
                                         "again, which is how it drifted before")

    def test_the_settings_the_guide_documents_are_the_service_defaults(self):
        """A guide may not describe a setting the code does not have. With only
        `anti_cheat_enabled` set, the service uses its own defaults — and the
        page's schedule has to be that one."""
        from app.routes.guide import penalty_schedule
        from app.services.anti_cheat_service import calculate_graduated_penalty

        defaults = [{ "n": n,
                      "cut": calculate_graduated_penalty(n, {"anti_cheat_enabled": True})["current_penalty_this_violation"],
                      "total": calculate_graduated_penalty(n, {"anti_cheat_enabled": True})["penalty"],
                      "submits": bool(calculate_graduated_penalty(n, {"anti_cheat_enabled": True}).get("auto_submit")) }
                    for n in range(1, 6)]
        documented = penalty_schedule()
        assert [{k: row[k] for k in ("n", "cut", "total")} for row in documented] == \
               [{k: row[k] for k in ("n", "cut", "total")} for row in defaults]
        assert [row["total"] for row in documented] == [0, 5, 15, 30, 45]

    def test_the_rendered_table_prints_what_the_function_computes(self, app):
        """Rendered, not grepped: a hand-typed `&minus;25` in one cell is exactly
        the defect, and a text scan of the template would pass it."""
        import re as _re
        from app.routes.guide import penalty_schedule
        with _signed_in(app, "/guide/skor"):
            html = app.jinja_env.get_template("guide/skor.html").render(
                penalty_schedule=penalty_schedule(), charged_violations=2)
        rows = _re.findall(r'data-penalty-row="(\d+)".*?data-penalty-total="([\d.]+)"',
                           html, _re.S)
        assert rows, "the penalty table did not render"
        expected = [(str(r["n"]), str(r["total"])) for r in penalty_schedule()]
        # Both tables render the same schedule; each must agree with the code.
        assert rows[:len(expected)] == expected
        assert rows[len(expected):] == expected

    def test_it_names_only_the_acts_that_are_actually_charged(self):
        """Both tabs, and the charging policy's own name: the teacher's copy of
        this sentence and the student's are separate paragraphs, so one page
        saying it while the other says something else is the failure."""
        from app.services.anti_cheat_service import PENALIZED_VIOLATION_TYPES
        assert set(PENALIZED_VIOLATION_TYPES) == {
            "tab_switch", "fullscreen_exit", "focus_lost",
        }
        teacher_tab, student_tab = GUIDE.split("x-show=\"tab==='student'\"", 1)
        for name, tab in (("teacher", teacher_tab), ("student", student_tab)):
            assert "berpindah tab" in tab, \
                f"the {name} tab stopped naming what is charged"
            assert "keluar dari layar penuh" in tab, \
                f"the {name} tab names only part of what is charged"
            assert "meninggalkan jendela ujian" in tab, \
                f"the {name} tab does not name the act the away-blur records"
            # And what is *not* charged, or every recorded event reads as a
            # penalty — a student who right-clicked would ask why they lost marks.
            assert "tidak mengurangi nilai" in tab, \
                f"the {name} tab does not say what is not charged"

    def test_it_says_an_old_exam_keeps_its_old_scoring(self):
        """The retroactivity rule: a release must not silently rescore a paper."""
        assert "Ujian lama tidak berubah" in GUIDE
        assert "70%" in GUIDE and "30%" in GUIDE

    def test_a_missing_key_is_described_as_unscored(self):
        assert "kunci" in GUIDE.lower()
        assert "Soal tanpa kunci tidak dinilai" in GUIDE


# ── the copy board ──────────────────────────────────────────────────────────
#
# Charts are the one part of this page a teacher cannot type into a report. Each
# one copies as an image *with its colour key*, and the item table copies as cells
# — the two things Word and Excel respectively need. The failure that matters is
# silent: a button that does nothing looks exactly like a button that worked, so
# what is checked here is that the controls exist, that they address the drawings
# that are on the page, and that the key they compose comes from the palette the
# server sends rather than from a second copy kept in the template.

class TestTheCopyBoard:
    #: The canvases the page draws, and the key each copy control names. A control
    #: for a chart that is not there composes nothing and reports success.
    CHART_REFS = ("map", "difficulty", "people")

    def test_the_page_draws_the_charts_the_copy_controls_address(self, rendered):
        """The drawn canvases and the copy controls are the *same* set, whatever
        framework the reader chose — which is why this is read off the render
        rather than from a list in the test."""
        html = rendered["html"]
        refs = set(re.findall(r'x-ref="(\w+)Canvas"', html))
        controls = set(re.findall(r"copyChart\('(\w+)'\)", html))
        assert refs, "the page draws no chart at all"
        assert refs == controls, (refs, controls)

    def test_every_chart_has_a_copy_control_under_the_framework_that_draws_it(
            self, app):
        """Rasch is the view that shows all three drawings, so it is the one that
        can prove each of them is copyable."""
        from app.routes.teacher import _chart_payload
        from app.services import item_analysis

        analysis = item_analysis.analyse(EXAM, _submissions())
        chart = _chart_payload(analysis, framework=af.resolve("rasch"))
        with _signed_in(app, "/teacher/analysis/exam-1"):
            html = app.jinja_env.get_template("teacher/analysis.html").render(
                exam=EXAM, analysis=analysis, chart=chart,
                framework=af.resolve("rasch"))
        for ref in self.CHART_REFS:
            assert f'x-ref="{ref}Canvas"' in html, f"the page has no {ref} canvas"
            assert f"copyChart('{ref}')" in html, f"the {ref} chart cannot be copied"

    def test_the_board_offers_the_table_and_the_whole_figure(self, rendered):
        html = rendered["html"]
        assert "copyAllCharts()" in html, "there is no way to copy every chart at once"
        assert "copyTable()" in html, "there is no way to copy the item table"

    def test_the_table_the_copy_control_reads_is_marked(self, rendered):
        """`copyTable` reads the DOM, so it needs a hook that is not a CSS class —
        restyling the table must not silently unhook the button.

        Matched on the *element*, not on the string: `[data-item-table]` appears in
        the selector that reads the hook as well, so a plain substring check passes
        with the attribute removed from the table and still present in the code
        looking for it.
        """
        on_the_table = re.compile(r"<table[^>]*data-item-table")
        assert on_the_table.search(PAGE), "the template's table has no copy hook"
        assert on_the_table.search(rendered["html"]), (
            "the rendered item table has no hook for the copy control")

    #: The legend keys each drawing names, both its shape lines and the statistics
    #: that are in it. A drawing with no legend copies as a picture of a shape.
    def test_every_column_the_table_shows_has_a_definition(self, rendered):
        """The table's `data-col` names its definition, so a column whose key has
        none cannot be copied: the paste would carry a bare letter and two teachers
        would read it two ways. Both languages, because the legend follows the
        toggle and the review of the page is not always in Indonesian."""
        columns = re.findall(r'data-col="(\w+)"', rendered["html"])
        assert len(columns) >= 10, "the item table lost its column keys"
        for key in columns:
            entry = _catalogue_entry(key)
            assert "id:" in entry and "en:" in entry, (
                f"the definition of {key!r} is not bilingual")
            # And a short name for it: the label comes from the catalogue, not from
            # the table's own headers, because those are filled by `x-text` and the
            # legend can otherwise read them before they have landed.
            label = re.search(rf"{key}:\s*\{{[^}}]*\}}", PAGE, re.S)
            assert label, f"the legend has no short name for {key!r}"
        assert "this.words('glossaryLabel'" in PAGE

    def test_every_drawing_carries_its_own_legend(self):
        """Each chart's own lines and its own statistics, not one caption for all
        three: a reader pasting the item map needs to be told what a dot is."""
        for key in self.CHART_REFS:
            chunks = re.findall(rf"\b{key}:\s*\[([^\]]*)\]", PAGE)
            names = [name for chunk in chunks
                     for name in re.findall(r"'(\w+)'", chunk)]
            assert len(names) >= 3, (
                f"the {key} chart names {names}, which is not a legend")
            for name in names:
                entry = _catalogue_entry(name)
                assert "id:" in entry and "en:" in entry, (
                    f"the {key} chart's line {name!r} is not bilingual")

    def test_the_key_composed_into_a_copied_chart_is_the_server_palette(self):
        """One palette. The five `rgba(...)` literals that used to be here were the
        same information kept where the PDF and the workbook could not reach it."""
        assert "this.tones[flag]" in PAGE
        for literal in ("rgba(220,38,38", "rgba(217,119,6", "rgba(99,102,241",
                        "rgba(120,113,108", "rgba(5,150,105"):
            assert literal not in PAGE, (
                f"the template keeps its own copy of the palette ({literal})")

    def test_a_copied_table_carries_its_own_legend(self, rendered):
        """The numbers first, then what each column is — one paste, both halves.
        The legend is read from the table's own headings, so it is in the reader's
        language along with them."""
        assert "this.tableLegend()" in PAGE, "the item table travels with no legend"
        assert "figureTitle()" in PAGE, "the paste does not say which exam it is"
        assert 'data-copy="table"' in rendered["html"]
        assert "'text/plain'" in PAGE and "'text/html'" in PAGE, (
            "Excel takes tabs and Word takes a table, and both are needed")

    def test_the_summary_can_be_copied_with_its_meaning(self, rendered):
        """The cards and Winsteps' block, with the definition of each statistic:
        these are the numbers a school quotes into a report."""
        assert "copySummary()" in rendered["html"]
        assert 'data-copy="summary"' in rendered["html"]
        assert "this.summaryLegend()" in PAGE
        for name in ("stat_rmse", "stat_tsd", "stat_sep", "stat_strata",
                     "stat_rel"):
            entry = _catalogue_entry(name)
            assert "id:" in entry and "en:" in entry, (
                f"{name} is not defined in both languages")

    def test_the_legend_follows_the_language_toggle(self):
        """Every line of every legend is a pair read through the catalogue — no
        Indonesian sentence hard-coded into the canvas code, which is where the
        page's copy would otherwise escape the sweep."""
        for call in ("this.words('chartLine'", "this.words('glossary'",
                     "this.words('figure'"):
            assert call in PAGE, f"the copy board does not read {call}"
        # The heading exists, in the catalogue, once — and the drawing code reads
        # it rather than spelling it out.
        assert PAGE.count("Keterangan warna:") == 1, (
            "a legend heading is hard-coded outside the catalogue")
        assert "this.words('figure', 'legend')" in PAGE

    def test_the_page_prints_the_same_legends_the_paste_carries(self, rendered):
        """One function, two destinations. A teacher reads the legend on the page
        and pastes the same sentences into a report; two lists of sentences would
        be two chances to disagree, and the page's copy is the one nobody notices
        going stale."""
        html = rendered["html"]
        assert html.count("<details") >= 3, (
            "a legend that is only in the paste is a legend nobody reads")
        for call in ("chartLegend()", "tableLegend()", "summaryLegend()"):
            assert f"in {call}" in html, f"the page does not list {call}"
        for heading in ("What each column means",
                        "What the summary and separation numbers mean",
                        "How to read the charts above"):
            assert heading in html, f"no on-page legend for {heading!r}"

    def test_the_provenance_travels_with_the_figure(self, rendered):
        """A pasted chart with no exam name is a chart nobody can check, and the
        copy board cannot read the page's own header."""
        data = _page_payload(rendered["html"])
        assert data["meta"]["title"] == EXAM["title"]
        assert data["meta"]["students"] == 10
        assert data["meta"]["items"] == EXAM["total_questions"]
        assert '"meta": {' in TEACHER
        assert "meta.title" in PAGE and "meta.students" in PAGE

    def test_the_page_is_told_the_palette_the_documents_use(self, rendered):
        from app.services import analysis_report
        assert rendered["chart"]["tones"] == analysis_report.FLAG_TONES
        # And it survives the trip through the attribute Alpine reads, which is
        # where a payload silently becomes `{}`.
        import html as html_mod
        match = re.search(r'x-data="itemAnalysis\((.*?)\)"', rendered["html"], re.S)
        data = json.loads(html_mod.unescape(match.group(1)))
        assert data["tones"] == analysis_report.FLAG_TONES

    def test_the_copied_legend_covers_the_acts_and_not_the_internals(self):
        """Five swatches for the four acts a teacher takes plus \"not measurable\".

        A swatch per internal flag would be eight, and two of them would be the
        same colour — a key that lists the same red twice teaches a reader to skip
        it.
        """
        legend = PAGE.split("legend() {", 1)[1].split("},\n", 1)[0]
        for flag in ("'ok'", "'weak'", "'negative', 'misfit'",
                     "'extreme_easy', 'extreme_hard'", "'unkeyed', 'unscored'"):
            assert flag in legend, f"the copied key has no group for {flag}"
        assert "this.tone(" in legend, "the swatches do not use the palette"

    def test_a_refused_clipboard_is_not_a_button_that_does_nothing(self):
        """`clipboard.write` needs a secure context and a browser may refuse it.
        The fallback saves the PNG and the note says which happened."""
        assert "navigator.clipboard.write" in PAGE
        assert "canvas.toDataURL" in PAGE, (
            "a refused clipboard leaves the reader with nothing")
        for pair in ("'Tersalin', 'Copied'",
                     "'Grafik diunduh sebagai PNG', 'Chart downloaded as a PNG'",
                     "'Tabel tersalin', 'Table copied'"):
            assert pair in PAGE, f"the copy feedback is not bilingual ({pair})"

    def test_the_table_is_copied_as_cells_and_not_as_one_column(self):
        """`text/plain` as tabs for Excel and `text/html` as a table for Word: a
        plain-text paste into Word arrives as one column of tabs."""
        assert "'text/plain': new Blob([tsv]" in PAGE
        assert "'text/html': new Blob([html]" in PAGE
        assert "cell.innerText" in PAGE, (
            "the copy reads the template's markup instead of what is on the page")

    def test_the_copy_control_is_offered_in_both_languages_and_on_a_phone(self):
        assert "t('Salin semua grafik','Copy all charts')" in PAGE
        assert "t('Salin tabel','Copy table')" in PAGE
        # The chart controls are in the card header, which stacks on a phone; a
        # fixed-width toolbar here would push the chart off the screen.
        assert "flex items-center gap-2" in PAGE.split('copyChart(', 1)[0][-600:]


# ── every table and drawing says what it means ──────────────────────────────

class TestTheMeaningIsOnThePage:
    """The meaning travels with the thing it explains, on the screen as well.

    A legend that exists only inside a copy buffer is a legend the reader of the
    page never sees; a table whose columns are defined only in the paste is a
    table two teachers read two ways. So each block that publishes numbers — the
    summary cards, the separation table, the item table, the upper-and-lower
    table, the option spread — has to carry its own sentences *on the page*, and
    those sentences have to be the pairs the language toggle switches.
    """

    #: The block -> the legend it must carry, and the binding that lets the
    #: reader open it. The binding is asserted rather than the function's name,
    #: because the name is also the word that *defines* it: a legend deleted from
    #: the page would still be "there" as a method nobody calls.
    LEGENDS = (
        ("Arti angka pada ringkasan dan pemisahan",
         'x-for="(line, index) in summaryLegend()"'),
        ("Arti setiap kolom", 'x-for="line in tableLegend()"'),
        ("Arti tabel kelompok atas dan bawah", 'x-for="line in splitLegend()"'),
        ("Cara membaca grafik di atas",
         'x-for="(line, index) in chartLegend()"'),
    )

    @staticmethod
    def _section(name):
        """One catalogue section of the page's own JS, as the page writes it."""
        body = PAGE.split(f"    {name}: {{", 1)[1]
        return body.split("\n    },", 1)[0]

    def test_each_table_and_drawing_has_its_own_legend(self, rendered):
        html = rendered["html"]
        for heading, binding in self.LEGENDS:
            assert heading in html, f"no on-page legend for {heading!r}"
            assert html.count(binding) == 1, (
                f"{heading!r} is not the one block bound to {binding}")

    def test_each_legend_names_what_its_table_shows(self):
        """The upper-and-lower table's columns and its legend are one list: a
        column whose definition is missing renders as a bare letter beside a
        number the reader has to interpret."""
        block = PAGE.split("splitLegend() {", 1)[1].split("        },", 1)[0]
        keys = re.findall(r"'([a-z]+)'", block)
        assert keys[:5] == ["upper", "lower", "t", "df", "p"], keys
        for key in keys[:5]:
            for section in ("splitLabel", "split"):
                # Scoped to the section's own body: a definition that moved to
                # another section, or that lost its pair, is not a definition
                # where this legend reads.
                entry = re.search(
                    rf"^\s*{key}:\s*\{{(.*?)\}}\s*,?$",
                    self._section(section), re.M | re.S)
                assert entry, f"{section} has no line for {key!r}"
                assert "en:" in entry.group(1), (
                    f"{section}.{key} is not bilingual")
        # The headings the table prints and the labels the legend prints are the
        # same words, so the reader can match a column to its sentence.
        for label in ("Atas", "Bawah"):
            assert label in PAGE, f"the split legend lost its label {label!r}"

    def test_a_card_of_one_paper_says_one(self):
        """The hint under a card is the meaning of the number above it, so it has
        to agree with it: an exam with one paper answered printed "1 Papers with
        data", and the copied summary — which reads the same `hint` — copied it."""
        assert "s.answered_papers === 1" in PAGE, (
            'the students card prints "1 Papers with data" again')
        assert "'paperOne' : 'papers'" in PAGE.replace("\n", " ")
        entry = _catalogue_entry("paperOne")
        assert "id:" in entry and "en:" in entry, (
            "the singular is not bilingual")

    def test_the_option_spread_carries_its_colour_key(self, rendered):
        """Two colours and one length, said in words: the bars encode a finding
        and the finding is why the block is on the page at all."""
        for call in (
            "t('Hijau dan ikon kunci: pilihan yang menjadi kunci jawaban soal "
            "ini.','Green with a key icon: the option this question is keyed to.')",
            "t('Abu-abu: pengecoh, bukan kunci.','Grey: a distractor, not the key.')",
            "t('Panjang batang: berapa kertas memilih pilihan itu.','Bar length: "
            "how many papers chose that option.')",
        ):
            assert call in PAGE, f"the option key is missing {call}"
        html = rendered["html"]
        assert "Green with a key icon" in html and "Bar length" in html
        # The swatches use the same utilities the bars do, so a legend cannot
        # promise a colour the chart does not paint.
        for utility in ("bg-emerald-400", "bg-surface-300"):
            assert PAGE.count(utility) >= 2, (
                f"{utility} is used once: the key and the bars drifted apart")
