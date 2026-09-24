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
STUDENT = (ROOT / "app" / "routes" / "student.py").read_text(encoding="utf-8")
TOGGLE = (ROOT / "tests" / "unit" / "test_language_toggle.py").read_text(encoding="utf-8")
#: The page's markup without its comments, for the assertions that look for text.
#: This file's own prose quotes the label it replaced, so a scan over the raw source
#: finds the fix described rather than applied — the lesson every guard in this
#: repository has had to learn, twice.
PAGE = re.sub(r"\{#.*?#\}", " ", RAW, flags=re.S)


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
    def test_each_canvas_has_a_parent_with_a_height(self):
        """`maintainAspectRatio: false` with an unsized parent makes the canvas grow
        on every resize — measured elsewhere in this repository at 5 400-8 300px."""
        for ref in ("histoCanvas", "seriesCanvas", "pieCanvas"):
            block = PAGE.split('x-ref="%s"' % ref, 1)[0]
            parent = block.rsplit("<div", 1)[1]
            assert re.search(r'class="h-\d+', parent), (
                f"{ref} has no sized parent, so it will grow without bound")

    def test_the_charts_are_behind_the_same_switch_as_the_marks(self):
        """A chart is a loud way to show a score. A toggle that hides the column
        while an axis prints every mark is a switch that does not work."""
        section = PAGE.split('t(\'Ringkasan Visual\'', 1)[1]
        assert 'x-show="showScores"' in section.split("</section>", 1)[0]
        assert "if (!this.showScores) return;" in PAGE, \
            "a hidden canvas measures 0x0 and must not be drawn into"

    def test_the_pie_is_drawn_rather_than_charted(self):
        """Chart.js has no 3D pie. The slices are counts — a pie of *averages* would
        be a picture of a number that is not part of anything — and the caption says
        so on the page."""
        assert "drawPie3D" in PAGE
        assert "ctx.ellipse(" in PAGE, "the projection is gone"
        assert "shade(" in PAGE, "the wall has no depth without shading"
        assert "not an average" in PAGE and "bukan rata-rata" in PAGE
        assert "type: 'pie'" not in PAGE, (
            "a 2D pie came back while the caption still claims a count")

    def test_the_charts_are_rebuilt_when_the_language_changes(self):
        """A chart's axis labels are strings built in JavaScript, and `t()` cannot
        re-run inside a canvas — the watcher is what redraws them."""
        assert "$watch('lang'" in PAGE
        assert "$watch('showScores'" in PAGE
