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


class TestTheChartsAreColourful:
    """Three views of one set of marks, and every one of them coloured — a five-hue
    ramp for the bands, a gradient for the line, eight hues for the pie. A chart whose
    colour carries nothing is a chart that has to be read twice: the ramp is the same
    red-under-60 / amber / blue / emerald rule the dashboard paints its subject bars
    with, so a colour means the same thing in both places."""

    def test_the_band_ramp_is_the_pages_own_rule(self):
        ramp = re.search(r"bandColors: \[([^\]]+)\]", CHARTS)
        assert ramp, "the band ramp is gone"
        colors = re.findall(r"#[0-9a-f]{6}", ramp.group(1))
        assert len(colors) == 5, f"five bands need five colours, found {colors}"
        #: The dashboard's own boundaries: fail / low / pass / good / very good.
        assert colors[0] == "#ef4444", "the failing band is no longer red"
        assert colors[3] == "#10b981", "the 80s band left the emerald the app uses"

    def test_every_bar_carries_its_band_colour(self):
        assert "backgroundColor: this.bandColors" in CHARTS, \
            "the histogram is one flat colour again"
        assert "color: this.bandColors[i]" in CHARTS, \
            "a band does not know its own colour"

    def test_the_line_is_a_gradient_and_its_points_are_banded(self):
        assert "createLinearGradient" in CHARTS, "the stroke is one flat colour"
        assert "pointBackgroundColor: this.series.map(p => this.bandColor(p.value))" in CHARTS, \
            "a point does not say which band its score is in"

    def test_the_point_colour_reads_the_same_edges_as_the_bars(self):
        """Two ways of asking "which band is this?" would let a 75 draw an amber bar
        and a blue dot. Both read one list of edges."""
        assert CHARTS.count("const edges = [0, 60, 70, 80, 90, 101];") == 2, \
            "the band edges are stated somewhere else as well"


class TestThePieAnswersThePointer:
    """The pie is the one canvas Chart.js does not own, so it is the one that had no
    tooltip, no cursor and no redraw — it was stale after every rotate and silent
    under every cursor, which on a phone is the only way to read a slice."""

    def test_the_pointer_lights_a_slice(self):
        assert '@pointermove="onPieMove($event)"' in CHARTS
        assert "onPieMove(evt)" in CHARTS
        assert "hoverSlice" in CHARTS

    def test_the_hit_test_and_the_drawing_share_one_geometry(self):
        """A hit test with its own ellipse arithmetic is how a slice starts answering
        for its neighbour — so there is exactly one place that computes it."""
        assert CHARTS.count("w / 2 - 8") == 1, \
            "the pie's ellipse is computed in two places"
        assert "ry: rx * 0.55" in CHARTS, \
            "`ry` is derived from `rx` again, so the two can drift"
        assert "this._pieGeom(box.w, box.h, depth)" in CHARTS, "the drawing left the shared geometry"
        assert "this._pieGeom(w, h)" in CHARTS, "the hit test left the shared geometry"

    def test_the_legend_is_an_input(self):
        assert '@mouseenter="hoverSlice = i; drawPie3D()"' in CHARTS, \
            "hovering a subject no longer lights its slice"
        assert "x-for=\"(slice, i) in pieSlices\"" in CHARTS, \
            "the legend has no index to point at"

    def test_the_hovered_slice_steps_out_and_is_named(self):
        assert "const out = this.hoverSlice === i ? 7 : 0;" in CHARTS
        assert "_pill(" in CHARTS, "the hovered slice is not named"

    def test_nothing_rewrites_the_pie_canvas_style(self):
        """Reported from the live site: the pie left its card the moment it was hovered.
        The canvas carried `:style="hoverSlice === null ? …"`, and an Alpine *string*
        style binding rewrites the whole style attribute — wiping the inline size
        `_pieCtx()` had set. Measured, the canvas then rendered at its attribute size
        (2x its css size at dpr 2) and overflowed the card. The cursor is set from
        `drawPie3D`, where the hover state lives, and no binding touches the canvas."""
        tag = re.search(r'<canvas x-ref="pieCanvas"[^>]*>', CHARTS, re.S)
        assert tag, "the pie canvas is gone"
        assert ":style=" not in tag.group(0), \
            "an Alpine :style binding wipes the inline size _pieCtx() sets"
        assert "canvas.style.cursor = this.hoverSlice === null ? 'default' : 'pointer';" in CHARTS, \
            "the cursor is no longer driven from the hover state"

    def test_the_pie_is_redrawn_on_resize_and_unregistered_after(self):
        assert "window.addEventListener('resize', this._onResize)" in CHARTS
        assert "destroy()" in CHARTS and "removeEventListener('resize'" in CHARTS, \
            "a detached canvas keeps being drawn into"

    def test_the_pie_canvas_is_sized_by_its_holder(self):
        """Measured in a real browser, and the reason this exists: with a percentage
        height the pie's own `height` attribute drove its layout, so every redraw grew
        it — a 224px holder holding a 1665px canvas, and a card as tall as the page.
        Chart.js writes an inline size for the canvases it manages; nothing does for
        this one, so it is sized here, in css pixels, with the backing store in device
        ones."""
        assert "_pieCtx()" in CHARTS
        assert "const w = holder.clientWidth, h = holder.clientHeight;" in CHARTS
        assert "canvas.style.width = w + 'px';" in CHARTS
        assert "canvas.style.height = h + 'px';" in CHARTS
        assert "ctx.setTransform(dpr, 0, 0, dpr, 0, 0);" in CHARTS, \
            "a 1x backing store is blurred on every phone"
        assert "this._ctx('pieCanvas')" not in CHARTS, \
            "the pie is back on the path that sets only the backing store, which is " \
            "what let its attribute drive its own layout"

    def test_every_canvas_is_sized_before_the_compact_decision_is_made(self):
        """`_small()` reads the canvas width, so a canvas that has not been laid out yet
        answers "narrow" and the axis titles are never drawn — measured at 1920px, where
        the titles were missing on a 573px chart."""
        assert CHARTS.count('class="w-full h-full"') == 2, \
            "the Chart.js canvases must be laid out from css, not from an attribute"


class TestTheChartsHoverAndServeEveryWidth:
    def test_the_chart_interaction_is_wired(self):
        assert "onHover: this._cursor" in CHARTS, "nothing sets the pointer cursor"
        assert "backgroundColor: 'rgba(15,23,42,.92)'" in CHARTS, \
            "the tooltip inherits whatever surface the card has"

    def test_the_card_and_its_three_views_respond_to_hover(self):
        assert "hover:shadow-xl hover:border-primary-200" in CHARTS, "the card does not lift"
        assert CHARTS.count("hover:border-primary-300 hover:bg-surface-50") == 3, \
            "one of the three views has no hover state"

    def test_three_columns_only_where_a_chart_is_wide_enough(self):
        """`lg` is where this page's sidebar appears, so a 3-up grid there leaves each
        chart about 220px — narrower than the phone's single one."""
        assert "md:grid-cols-2 xl:grid-cols-3" in CHARTS
        assert "lg:grid-cols-3" not in CHARTS, "three columns are back at the sidebar's width"

    def test_a_narrow_chart_drops_its_axis_titles_rather_than_squeezing(self):
        assert "_small(box)" in CHARTS
        assert CHARTS.count("title: { display: !small") == 4, \
            "an axis title is drawn at a width that cannot hold it"

    def test_the_other_dashboard_cards_lift_like_their_neighbours(self):
        """The stat cards and the exam/whiteboard cards already did; the two info cards
        and the mastery card sat still, which reads as "this one is not interactive"."""
        assert DASH.count("hover:shadow-xl hover:-translate-y-0.5 transition-all duration-300") >= 6, \
            "some dashboard cards still do not respond to the pointer"
