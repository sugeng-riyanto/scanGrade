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

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "app" / "templates"
PAGE = (TEMPLATES / "teacher" / "analysis.html").read_text(encoding="utf-8")
GRADING = (TEMPLATES / "teacher" / "grading_queue.html").read_text(encoding="utf-8")
RESULTS = (TEMPLATES / "teacher" / "results.html").read_text(encoding="utf-8")
GUIDE = (TEMPLATES / "guide" / "skor.html").read_text(encoding="utf-8")
TEACHER = (ROOT / "app" / "routes" / "teacher.py").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app():
    from app import create_app
    return create_app("app.config.TestingConfig")


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
            exam=EXAM, analysis=analysis, chart=chart)
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
        # The three canvases: an item map, a difficulty chart and a person spread.
        assert html.count("<canvas") == 3
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
    def test_both_links_carry_this_exam_and_the_current_language(self, rendered):
        html = rendered["html"]
        assert "/teacher/analysis/exam-1/download.csv" in html
        assert "/teacher/analysis/exam-1/download.pdf" in html

    def test_the_language_parameter_follows_the_toggle(self):
        """The CSV and the PDF are rendered on the server, so Alpine cannot
        translate them. The page has to hand the reader's choice to the link, or
        an English reader files an Indonesian report — the same defect as a
        hardcoded label, one layer further out."""
        for kind in ("csv", "pdf"):
            assert re.search(
                rf":href=\"'/teacher/analysis/\{{\{{ exam\.id \}}\}}/download\.{kind}"
                r"\?lang=' \+ lang\"", PAGE), \
                f"the {kind} link does not carry the chosen language"
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


# ── responsiveness, asserted the way it fails ───────────────────────────────

class TestItFitsASmallScreen:
    def test_the_item_table_scrolls_instead_of_squeezing(self):
        """Sixteen columns cannot fit 320 px. A table that shrinks instead of
        scrolling is the layout that made the previous report unreadable, so the
        wrapper is asserted on the item table itself and not "somewhere on the
        page" — the banding and split tables have their own wrappers."""
        assert '<div class="overflow-x-auto">\n            <table class="w-full text-xs min-w-[820px]">' in PAGE, (
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
        assert set(PENALIZED_VIOLATION_TYPES) == {"tab_switch", "fullscreen_exit"}
        teacher_tab, student_tab = GUIDE.split("x-show=\"tab==='student'\"", 1)
        for name, tab in (("teacher", teacher_tab), ("student", student_tab)):
            assert "berpindah tab" in tab, \
                f"the {name} tab stopped naming what is charged"
            assert "keluar dari layar penuh" in tab, \
                f"the {name} tab names only half of what is charged"
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
