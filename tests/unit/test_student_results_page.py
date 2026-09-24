"""The student's results page: the pass line, the count, and the three charts.

This page was pinned to Indonesian and read its marks against a literal 70. Both are
the kind of defect the suite has to hold, because neither produces an error: a wrong
pass line paints a red 74 for a school whose standard is 75, and an untranslated
label looks exactly like a translated one to everything except a reader.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW = (ROOT / "app" / "templates" / "student" / "results.html").read_text(encoding="utf-8")
CHARTS_RAW = (ROOT / "app" / "templates" / "student" / "_visual_summary.html").read_text(encoding="utf-8")
DASH_RAW = (ROOT / "app" / "templates" / "student" / "dashboard.html").read_text(encoding="utf-8")
STUDENT = (ROOT / "app" / "routes" / "student.py").read_text(encoding="utf-8")
TOGGLE = (ROOT / "tests" / "unit" / "test_language_toggle.py").read_text(encoding="utf-8")
#: The page's markup without its comments, for the assertions that look for text.
#: This file's own prose quotes the label it replaced, so a scan over the raw source
#: finds the fix described rather than applied — the lesson every guard in this
#: repository has had to learn, twice.
PAGE = re.sub(r"\{#.*?#\}", " ", RAW, flags=re.S)
CHARTS = re.sub(r"\{#.*?#\}", " ", CHARTS_RAW, flags=re.S)
DASH = re.sub(r"\{#.*?#\}", " ", DASH_RAW, flags=re.S)


class TestThePassLineIsThePapers:
    """A mark is green or red against *that paper's* KKM. The page compared against
    a hard-coded 70, so a school whose standard is 75 saw a passing 74 painted red
    and a school whose standard is 65 saw a failing 68 painted green."""

    def test_the_route_asks_for_the_column_it_judges_by(self):
        """A column left out of a `select` reads as absent, which would make every
        paper fall back to the default — the defect this repository already carries a
        paragraph about for `answer_key`."""
        route = STUDENT.split("def results(", 1)[1]
        select = re.search(r"\.select\(\"([^\"]+)\"", route)
        assert select, "the results query could not be read"
        assert "passing_score" in select.group(1), (
            "the results page judges by a column it does not ask for")

    def test_the_page_compares_with_the_paper_not_a_number(self):
        assert "passed(s)" in PAGE, "the pass line lost its helper"
        assert "s.exam?.passing_score" in PAGE, (
            "the pass line is a literal again")
        assert not re.search(r">=\s*70\b", PAGE), "the hard-coded 70 is back"


class TestTheCountReadsLikeOne:
    def test_the_old_label_is_gone(self):
        """It read `submissions.length + ' submission'` — English, in an Indonesian
        screen, and never pluralised: "2 submission"."""
        assert "' submission'" not in PAGE
        assert "length + ' submission" not in PAGE

    def test_both_halves_are_literal_and_the_plural_is_carried(self):
        """The sweep only recognises `t('a','b')`, so a pair assembled by
        concatenation is a string that stops being translated — which is why the
        count sits outside the call and the plural is a second pair."""
        assert "'1 ' + t('ujian','exam')" in PAGE
        assert "shown + ' ' + t('ujian','exams')" in PAGE
        assert "{{ 'exam' if st.exam_count == 1 else 'exams' }}" in PAGE

    def test_the_page_is_on_the_translated_contract(self):
        """It used to pin itself with `content_lang = 'id'`, which is what let it
        drift out of the sweep. Dropping it from this list turns the sweep off for
        the page again."""
        assert '"student/results.html"' in TOGGLE
        assert "content_lang = 'id'" not in PAGE, "the pin is back"


class TestTheThreeCharts:
    """The three views live in `student/_visual_summary.html`, rendered by both student
    pages from that one file. These assertions follow the copy rather than the page — a
    chart is a chart wherever it is written — and the last one holds the *reason* it
    moved there: a second implementation is how two pages start drawing different
    pictures of the same marks."""

    def test_each_canvas_has_a_parent_with_a_height(self):
        """`maintainAspectRatio: false` with an unsized parent makes the canvas grow
        on every resize — measured elsewhere in this repository at 5 400-8 300px."""
        for ref in ("histoCanvas", "seriesCanvas", "pieCanvas"):
            block = CHARTS.split('x-ref="%s"' % ref, 1)[0]
            parent = block.rsplit("<div", 1)[1]
            assert re.search(r'class="h-\d+', parent), (
                f"{ref} has no sized parent, so it will grow without bound")

    def test_the_charts_are_behind_the_same_switch_as_the_marks(self):
        """A chart is a loud way to show a score. A toggle that hides the column
        while an axis prints every mark is a switch that does not work."""
        section = CHARTS.split("t('Ringkasan Visual'", 1)[1]
        assert 'x-show="showScores' in section.split("</section>", 1)[0]
        assert "if (!this.showScores) return;" in CHARTS, \
            "a hidden canvas measures 0x0 and must not be drawn into"

    def test_the_pie_is_drawn_rather_than_charted(self):
        """Chart.js has no 3D pie. The slices are counts — a pie of *averages* would
        be a picture of a number that is not part of anything — and the caption says
        so on the page."""
        assert "drawPie3D" in CHARTS
        assert "ctx.ellipse(" in CHARTS, "the projection is gone"
        assert "shade(" in CHARTS, "the wall has no depth without shading"
        assert "not an average" in CHARTS and "bukan rata-rata" in CHARTS
        assert "type: 'pie'" not in CHARTS, (
            "a 2D pie came back while the caption still claims a count")

    def test_both_student_pages_render_the_one_implementation(self):
        """The dashboard drew its own bars and the results page its own three charts —
        the same marks, two arrangements, and nothing that could tell them apart when
        they drifted."""
        assert "{% include 'student/_visual_summary.html' %}" in PAGE, \
            "the results page no longer renders the shared card"
        assert "{% include 'student/_visual_summary.html' %}" in DASH, \
            "the dashboard no longer renders the shared card"
        for page, name in ((PAGE, "results"), (DASH, "dashboard")):
            assert "<canvas" not in page, (
                f"the {name} page carries a canvas of its own again — two "
                f"implementations of one set of charts is how they disagree")
            assert "new Chart(" not in page, (
                f"the {name} page builds its own chart again")


class TestTheDashboardHidesEveryMarkItPrints:
    """The dashboard's switch hid the overall average while the subject list printed
    each subject's average *and* drew a bar for it, and a hand-rolled trend list printed
    every mark of the last five exams. One number hidden, seven printed, and no error
    anywhere — which is why the switch was worth making true rather than decorative."""

    def test_the_subject_averages_follow_the_switch(self):
        assert 'class="space-y-3" x-show="showScores"' in DASH, \
            "the subject averages print their marks regardless of the switch"
        assert "x-show=\"!showScores\"" in DASH, \
            "nothing says the list is hidden rather than empty"

    def test_the_hand_rolled_trend_is_gone(self):
        """A bar list rendered server-side, with a colour rule on a literal 70 and no
        relation to the switch. Its information is in the shared card now, over every
        released mark rather than five.

        The Python half reads *code*, not prose: the route explains the removal in a
        comment that names the field, and a guard that reads its own documentation is
        the mistake this file's header already warns about."""
        assert "score_trend" not in DASH, "the bar list is back in the template"
        used = [ln.strip() for ln in STUDENT.splitlines()
                if "score_trend" in ln and not ln.lstrip().startswith("#")]
        assert not used, f"the route computes the bar list again: {used[:2]}"

    def test_the_component_mixes_in_the_shared_charts(self):
        """The page has to *call* the factory, and call `initCharts()` from its own
        `init` — Alpine only calls the component's own `init`, so a mixin that
        declared one would never run."""
        assert 'x-data="studentDashboard()"' in DASH
        assert "Object.assign(sgVisualSummary(), {" in DASH
        assert "this.initCharts();" in DASH
        assert "initCharts() {" in CHARTS, "the factory cannot be started"

    def test_the_charts_are_rebuilt_when_the_language_changes(self):
        """A chart's axis labels are strings built in JavaScript, and `t()` cannot
        re-run inside a canvas — the watcher is what redraws them. It lives with the
        factory now, so both pages get it."""
        assert "$watch('lang'" in CHARTS
        assert "$watch('showScores'" in CHARTS
