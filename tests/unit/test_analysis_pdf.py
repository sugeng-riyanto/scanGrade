"""The filed analysis: charts that are actually drawn, and a page that holds still.

The PDF is the document a school keeps, so it is checked as a *document*: the
page is opened with PyMuPDF and read the way a reader meets it — spans, their
boxes, and the shapes behind them. Grepping the template would pass every one of
the defects this file exists for:

* **the summary grid printed its markup.** reportlab renders a plain string
  literally, so `<font size=7 color='#666666'>Students</font><br/>1` arrived in
  the text layer as those characters — a broken-looking box of tags, and a last
  column pushed past the right margin by their length.
* **there were no charts at all.** An analysis is read as a shape first; the file
  was four tables and a legend.
* **`colWidths=[0.09, 0.22, …]`.** reportlab reads a bare number as *points* —
  only `"30%"` and `"*"` are relative — so the split table's columns collapsed
  into the same half-point and printed on top of each other.
* **sixteen full column names on a landscape A4** overlapped their neighbours,
  which is why the paper header is the screen's symbols plus one legend line.

Every assertion below is one of those.
"""
from __future__ import annotations

import random

import fitz
import pytest

from app.services import analysis_report, item_analysis

#: The colours the drawings use, and the only ones this file counts.
ACCENT = (0.76, 0.25, 0.05)   # #c2410c — bars and dots
MUTED = (0.39, 0.45, 0.55)    # #64748b — axes
KEY = (0.06, 0.46, 0.43)      # #0f766e — a keyed option

OPTIONS = "ABCDE"


def _class(items: int = 40, students: int = 200, seed: int = 20260920,
           mix: bool = True, unkeyed: int = 2):
    """An exam and a class, with a plausible spread and real distractors.

    Hard, middling and easy questions, and an ability term, so the item map has
    something in each corner: a chart of forty identical dots does not prove a
    chart was drawn. The last `unkeyed` questions have no answer key — a real
    state (`teacher/dashboard` warns about it) and the one that proves the charts
    leave a question *out* rather than plotting it at zero.
    """
    rng = random.Random(seed)
    difficulty = ([0.85] * (items // 4) + [0.5] * (items // 2) + [0.2] * (items // 4)
                  if mix else [0.5] * items)
    exam = {
        "title": "Ujian Akhir Semester Fisika",
        "total_questions": items,
        "question_types": {str(i): "mcq" for i in range(items)},
        "answer_key": {str(i): "A" for i in range(items - unkeyed)},
    }
    submissions = []
    for s in range(students):
        ability = rng.gauss(0, 1)
        answers = {}
        for i in range(items - unkeyed):
            p = min(0.97, max(0.03, difficulty[i] + 0.15 * ability))
            if rng.random() < p:
                answers[str(i)] = "A"
            elif i % 4 == 0:
                answers[str(i)] = rng.choice("BC")
            else:
                answers[str(i)] = rng.choice(OPTIONS[1:])
        submissions.append({"student_id": f"s{s:03d}", "student_name": f"Murid {s:03d}",
                            "answers": answers, "status": "graded"})
    return exam, submissions


@pytest.fixture(scope="module")
def built():
    exam, submissions = _class()
    return exam, item_analysis.analyse(exam, submissions)


def _document(pdf: bytes):
    return fitz.open(stream=pdf, filetype="pdf")


def _spans(page):
    return [(s["bbox"], s["text"]) for b in page.get_text("dict")["blocks"]
            for line in b.get("lines", []) for s in line["spans"]]


def _shapes(page):
    """Every filled shape, as (colour, is_curve)."""
    out = []
    for drawing in page.get_drawings():
        fill = drawing.get("fill")
        if fill is None:
            continue
        out.append((tuple(round(c, 2) for c in fill),
                    any(item[0] == "c" for item in drawing["items"])))
    return out


def _strokes(page):
    """Every *outline*, as colour — the axes and ticks are lines, not fills."""
    out = []
    for drawing in page.get_drawings():
        if drawing.get("fill") is not None:
            continue
        colour = drawing.get("color") or drawing.get("stroke")
        if colour is None:
            continue
        out.append(tuple(round(c, 2) for c in colour))
    return out


def _margin(page):
    return 12 * 72 / 25.4


class RecordingCanvas:
    """A canvas that answers like reportlab's and remembers what it was asked.

    The stacked chart's whole claim is *how tall* each slice is, and a slice of
    the right height in a page image is not something a test can measure: at A4
    landscape a two-point error is under a pixel. So the drawing calls are the
    thing that gets measured, and they are the real ones — the same `_ChartGrid`
    the document uses, drawing onto this instead of onto paper.
    """

    def __init__(self):
        self.rects: list[tuple[float, float, float, float, object]] = []
        self.lines: list[tuple] = []
        self.texts: list[tuple] = []
        self.font_size = 7.0
        self._fill = None

    def saveState(self):
        pass

    def restoreState(self):
        pass

    def setStrokeColor(self, *_args, **_kwargs):
        pass

    def setFillColor(self, colour):
        self._fill = colour

    def setLineWidth(self, *_args, **_kwargs):
        pass

    def setFont(self, _name, size, *_args):
        self.font_size = size

    def rect(self, x, y, width, height, stroke=0, fill=0, **_kwargs):
        self.rects.append((x, y, width, height, self._fill))

    def line(self, *args):
        self.lines.append(args)

    #: Recorded with the call that drew them: an axis tick and a column's number
    #: are both "just text" on a page, and a test that cannot tell them apart
    #: measures the ticks and calls it a label collision.
    def drawString(self, *args):
        self.texts.append(("left", args))

    def drawRightString(self, *args):
        self.texts.append(("right", args))

    def drawCentredString(self, *args):
        self.texts.append(("centred", args))

    def circle(self, *_args, **_kwargs):
        pass

    def stringWidth(self, text, _font, size):
        # Helvetica's average advance, near enough for a label-collision rule.
        return len(str(text)) * size * 0.5


def _drawn(chart, width: float = 500.0):
    """Run the real chart grid against a canvas that records instead of inks."""
    grid = analysis_report._ChartGrid([chart], "nothing to draw", columns=1)
    recorder = RecordingCanvas()
    grid.canv = recorder
    grid.wrap(width, width)
    grid.draw()
    return grid, recorder


# ── the drawing is a drawing ────────────────────────────────────────────────

class TestTheChartsAreDrawn:
    def test_every_chart_the_page_shows_is_in_the_file(self, built):
        exam, analysis = built
        pdf = analysis_report.analysis_pdf(analysis, exam, lang="id")
        text = "".join(page.get_text() for page in _document(pdf))
        for title in ("Peta butir soal", "Kesulitan soal (logit)",
                      "Sebaran kemampuan murid", "Sebaran pilihan jawaban"):
            assert title in text, f"the filed report has no {title} chart"

    def test_there_is_a_bar_or_dot_per_measurement(self, built):
        """Counted, not looked at: every calibrated question and every occupied
        logit bin must put a filled shape on the paper. A chart that quietly drops
        the ones it cannot place is how \"the graphs are missing\" starts."""
        exam, analysis = built
        pdf = analysis_report.analysis_pdf(analysis, exam, lang="id")
        first = _document(pdf)[0]
        accent = [shape for shape in _shapes(first) if shape[0] == ACCENT]
        plotted = len([i for i in analysis.items if i.measure is not None])
        assert len(accent) >= plotted + len(analysis.person_bins), (
            f"{len(accent)} filled shapes for {plotted} calibrated questions and "
            f"{len(analysis.person_bins)} bins")
        assert any(shape[1] for shape in accent), (
            "nothing round was drawn, so the item map plotted no question")

    def test_the_keyed_option_is_flagged_in_its_own_colour(self, built):
        """A distractor distribution is read next to its key: without the second
        colour the chart says which options were chosen and not which was right."""
        exam, analysis = built
        pdf = analysis_report.analysis_pdf(analysis, exam, lang="id")
        first = _document(pdf)[0]
        assert any(shape[0] == KEY for shape in _shapes(first)), (
            "the distractor chart marks no key")

    def test_the_axes_are_inked(self, built):
        """A tick mark is two points of ink; a *baseline* says what the bars are
        measured from. Counting strokes alone passed while the baseline was gone,
        because the ticks still drew."""
        exam, analysis = built
        pdf = analysis_report.analysis_pdf(analysis, exam, lang="en")
        first = _document(pdf)[0]
        strokes = [drawing for drawing in first.get_drawings()
                   if drawing.get("fill") is None]
        assert any(tuple(round(c, 2) for c in (d.get("color") or ())) == MUTED
                   for d in strokes), "the axes are not inked at all"
        assert any(d["rect"].width > 150 for d in strokes), (
            "no baseline was drawn, so the bars have no zero to sit on")

    def test_a_class_with_nothing_to_draw_says_so(self):
        """An exam nobody has sat draws no chart, and an empty frame reads as a
        chart that failed to load."""
        exam = {"title": "Kosong", "total_questions": 0, "question_types": {},
                "answer_key": {}}
        empty = item_analysis.analyse(exam, [])
        pdf = analysis_report.analysis_pdf(empty, exam, lang="id")
        text = "".join(page.get_text() for page in _document(pdf))
        assert analysis_report.labels("id")["no_chart"] in text

    def test_both_languages_get_the_same_picture(self, built):
        exam, analysis = built
        shapes = {}
        for lang in ("id", "en"):
            pdf = analysis_report.analysis_pdf(analysis, exam, lang=lang)
            first = _document(pdf)[0]
            shapes[lang] = len([s for s in _shapes(first) if s[0] == ACCENT])
            text = "".join(page.get_text() for page in _document(pdf))
            assert analysis_report.labels(lang)["item_map"] in text
        assert shapes["id"] == shapes["en"], (
            "the English report draws a different chart from the Indonesian one")


# ── the page holds still ────────────────────────────────────────────────────

class TestThePageIsReadable:
    def test_no_markup_reaches_the_text_layer(self, built):
        """The summary grid's own defect: markup printed as text."""
        exam, analysis = built
        pdf = analysis_report.analysis_pdf(analysis, exam, lang="id")
        leaked = [text for page in _document(pdf) for _box, text in _spans(page)
                  if "<" in text or "</" in text]
        assert not leaked, f"reportlab markup printed literally: {leaked[:3]}"

    def test_the_summary_grid_shows_values_not_only_labels(self, built):
        exam, analysis = built
        pdf = analysis_report.analysis_pdf(analysis, exam, lang="id")
        text = _document(pdf)[0].get_text()
        for label in ("Jumlah murid", "KR-20", "Rata-rata logit murid"):
            assert label in text, f"the summary grid lost {label}"
        # The *values*, formatted the way the grid formats them. Only the fields
        # this paper actually has: a blank cell is a measurement that does not
        # exist, and the service says so with None rather than with a zero.
        summary = analysis.summary
        present = [(name, value) for name, value in (
            ("students", summary.students), ("items", summary.items),
            ("alpha", summary.alpha), ("kr20", summary.kr20),
            ("total_mean", summary.total_mean), ("sem", summary.sem),
            ("item_reliability", summary.item_reliability)) if value is not None]
        assert present, "the summary has no numbers to show at all"
        for name, value in present:
            assert analysis_report._num(value, 3) in text, (
                f"the summary grid shows the label for {name} but not its value")

    def test_nothing_crosses_the_page_margin(self, built):
        """Both wide tables used to: the item table sized its own columns, and a
        long flag label or a wide number pushed the last one off the paper."""
        exam, analysis = built
        for lang in ("id", "en"):
            pdf = analysis_report.analysis_pdf(analysis, exam, school="SMA Uji",
                                               teacher="Guru Uji", lang=lang)
            for index, page in enumerate(_document(pdf)):
                limit = page.rect.width - _margin(page)
                over = [text for box, text in _spans(page) if box[2] > limit + 1]
                assert not over, f"{lang} page {index + 1} runs past the margin: {over[:3]}"
                before = [text for box, text in _spans(page) if box[0] < _margin(page) - 1]
                assert not before, f"{lang} page {index + 1} starts before it: {before[:3]}"

    def test_no_two_pieces_of_text_print_on_top_of_each_other(self, built):
        """The fractional `colWidths` defect, and the item-map labels: both
        printed text where text already was."""
        exam, analysis = built
        for lang in ("id", "en"):
            pdf = analysis_report.analysis_pdf(analysis, exam, lang=lang)
            for index, page in enumerate(_document(pdf)):
                lines: dict[float, list] = {}
                for box, text in _spans(page):
                    lines.setdefault(round(box[1]), []).append((box, text))
                for y, row in lines.items():
                    row.sort(key=lambda item: item[0][0])
                    for (a, text_a), (b, text_b) in zip(row, row[1:]):
                        # Adjacent cells *touch*, which is what a table is; only a
                        # real overlap counts, and the tolerance is the kerning
                        # between two runs that reportlab placed side by side.
                        assert b[0] >= a[2] - 0.8, (
                            f"{lang} page {index + 1} line {y}: {text_a[-16:]!r} and "
                            f"{text_b[:16]!r} overlap by {a[2] - b[0]:.1f}pt")

    def test_the_paper_header_is_short_and_the_words_are_a_legend(self, built):
        """Sixteen full names on a landscape A4 do not fit; the symbols do, and
        the words they stand for are one line above the table."""
        exam, analysis = built
        pdf = analysis_report.analysis_pdf(analysis, exam, lang="id")
        text = _document(pdf)[0].get_text()
        assert analysis_report.labels("id")["legend"] in text
        # The long names are the CSV's header, where a column can be as wide as
        # it likes — the paper uses the symbols.
        csv = analysis_report.analysis_csv(analysis, exam, "id")
        assert "Tingkat kesulitan (P)" in csv

    def test_a_big_paper_still_fits(self):
        """Two hundred questions and a three-digit item count widen every column:
        the widths are chosen rather than computed, so this is the case where a
        table sized by its own longest cell starts to leave the page."""
        exam, submissions = _class(items=200, students=30)
        analysis = item_analysis.analyse(exam, submissions)
        pdf = analysis_report.analysis_pdf(analysis, exam, lang="id")
        document = _document(pdf)
        for index, page in enumerate(document):
            limit = page.rect.width - _margin(page)
            over = [text for box, text in _spans(page) if box[2] > limit + 1]
            assert not over, f"page {index + 1} runs past the margin: {over[:3]}"

    def test_every_item_and_every_student_is_in_the_file(self, built):
        """A chart is a summary, not a substitute: the numbers behind it have to
        be in the document, or the shape cannot be checked."""
        exam, analysis = built
        pdf = analysis_report.analysis_pdf(analysis, exam, lang="id")
        text = "".join(page.get_text() for page in _document(pdf))
        assert analysis.people[-1].name in text or analysis.people[-1].name[-4:] in text
        assert str(analysis.items[-1].index + 1) in text
        assert analysis_report.labels("id")["people"] in text


# ── what the drawings are made of ───────────────────────────────────────────

class TestTheChartSpecs:
    def test_a_question_with_no_calibration_is_left_off_the_logit_chart(self, built):
        """An unkeyed question has no difficulty, and a chart that plots it at
        zero says the class got it wrong — which nobody has even answered."""
        exam, analysis = built
        unkeyed = [i for i in analysis.items if i.flag == "unkeyed"]
        assert unkeyed, "the fixture must contain questions with no key"
        specs = {chart.title: chart for chart in analysis_report._chart_specs(analysis, "id")}
        calibrated = [i for i in analysis.items if i.measure is not None]
        assert len(calibrated) == len(analysis.items) - len(unkeyed)
        assert len(specs["Kesulitan soal (logit)"].values) == len(calibrated)
        assert len(specs["Peta butir soal"].points) == len(
            [i for i in analysis.items if i.pct is not None and i.discrimination is not None])

    def test_the_histogram_uses_the_same_bins_the_page_shows(self, built):
        exam, analysis = built
        specs = {chart.title: chart for chart in analysis_report._chart_specs(analysis, "id")}
        assert len(specs["Sebaran kemampuan murid"].values) == len(analysis.person_bins)
        assert sum(specs["Sebaran kemampuan murid"].values) == sum(
            count for _centre, count in analysis.person_bins)

    def test_the_distractor_chart_is_one_column_per_question(self, built):
        """Two hundred options drawn as two hundred bars side by side are two
        hundred hairlines of the same width, and every question looks identical.
        One column per question is what makes the shares comparable."""
        exam, analysis = built
        specs = {chart.title: chart for chart in analysis_report._chart_specs(analysis, "id")}
        chart = specs["Sebaran pilihan jawaban"]
        every = [item for item in analysis.items if item.distractors]
        assert chart.kind == "stacked", (
            "the option distribution is drawn as one bar per option again")
        # One column per question that *has* options — including the ones nobody
        # answered, whose column is zero-height. The axis is numbered by question,
        # so dropping a question would slide a later number under an earlier
        # column; an empty column reads as "nobody", which is what happened.
        assert len(chart.columns) == len(every), (
            f"{len(chart.columns)} columns for {len(every)} questions with options")
        assert chart.labels == [item.index + 1 for item in every], (
            "the columns are not numbered by question, so a column cannot be found")
        for item, column, flags in zip(every, chart.columns, chart.column_keys):
            assert column == [option.count for option in item.distractors], (
                f"question {item.index + 1} stacks something other than its counts")
            assert flags == [bool(option.key) for option in item.distractors], (
                f"question {item.index + 1} marks the wrong option as its key")
        unkeyed = [item.index + 1 for item in every
                   if not sum(d.count for d in item.distractors)]
        assert unkeyed, "this fixture no longer has an unanswered question"
        assert all(not any(column) for item, column
                   in zip(every, chart.columns)
                   if item.index + 1 in unkeyed), (
            "a question nobody answered draws a bar, so an empty slot looks like "
            "a chart that failed to load")

    def test_a_key_with_a_hairline_share_is_still_a_slice(self):
        """Every question carries its own key flags, not one list for the whole
        chart: a matching question and a four-option choice do not have the same
        options in the same order, so a shared list marks the wrong slice."""
        chart = analysis_report._Chart(
            "stacked", "t", columns=[[8, 2], [1, 1, 6]], labels=[1, 2],
            column_keys=[[True, False], [False, False, True]])
        assert not chart.empty()
        assert chart.column_keys[0] == [True, False]
        assert chart.column_keys[1][2] is True

    def test_the_marked_slice_is_where_that_question_s_key_sits(self):
        """The key of a question is not always its first option.

        The fixture above keys every question to "A", so it would pass even if
        the chart marked the bottom slice of every column — the shape of the
        defect this guards is a key that sits third, or on an option another
        question does not even have.
        """
        exam = {"title": "Kunci di tengah", "total_questions": 2,
                "question_types": {"0": "mcq", "1": "mcq"},
                "answer_key": {"0": "C", "1": "B"}}
        answers = [{"student_id": f"s{i}", "student_name": f"M{i}",
                    "answers": {"0": "C" if i < 6 else "A",
                                "1": "B" if i < 4 else "D"},
                    "status": "graded"} for i in range(8)]
        analysis = item_analysis.analyse(exam, answers)
        chart = next(c for c in analysis_report._chart_specs(analysis, "id")
                     if c.kind == "stacked")
        assert chart.column_keys[0][2] is True, (
            "question 1's key is option C and not the bottom of its column")
        assert chart.column_keys[1][1] is True, (
            "question 2's key is option B and not the bottom of its column")

        _grid, recorder = _drawn(chart)
        slices = [r for r in recorder.rects
                  if r[4] in (analysis_report._KEY, analysis_report._ACCENT)
                  and r[2] < 30]
        by_column: dict = {}
        for x, y, width, height, colour in slices:
            by_column.setdefault(round(x, 1), []).append((y, height, colour))
        columns = [by_column[k] for k in sorted(by_column)]
        assert len(columns) == 2
        # Bottom-up, in option order, and only where something was chosen:
        # question 1 is answered A twice and C six times, with B, D and E chosen
        # by nobody, so its key is the *second* slice and not the third.
        # The heights are on the chart's own scale, so what is checked is the
        # colour order and the ratio between the slices: 2 and 6 answers.
        expected = [
            [(analysis_report._ACCENT, 1 / 3), (analysis_report._KEY, 3.0)],
            [(analysis_report._KEY, 1.0), (analysis_report._ACCENT, 1.0)],
        ]
        for number, (column, want) in enumerate(zip(columns, expected), start=1):
            column.sort()                        # bottom-up
            heights = [height for _y, height, _colour in column]
            assert [colour for _y, _h, colour in column] == [w[0] for w in want], (
                f"question {number} painted {[c for _y, _h, c in column]}")
            assert abs(heights[0] / heights[1] - want[0][1]) < 0.02, (
                f"question {number}: slices are {heights[0] / heights[1]:.2f} to "
                f"one, not {want[0][1]:.2f}")

    def test_the_question_numbers_under_the_columns_do_not_touch(self):
        """Forty questions in half a page is 8.7pt a column; two-digit numbers
        printed under every one of them run together into a row of unreadable
        digits, which is worse than a sparse axis."""
        exam, submissions = _class(items=40, students=30)
        analysis = item_analysis.analyse(exam, submissions)
        chart = next(c for c in analysis_report._chart_specs(analysis, "id")
                     if c.kind == "stacked")
        _grid, recorder = _drawn(chart, width=350.0)
        numbers = sorted(args[0] for method, args in recorder.texts
                         if method == "centred" and str(args[2]).rstrip(".").isdigit())
        assert len(numbers) < len(chart.columns), (
            "every column is numbered, so the numbers overlap")
        assert len(numbers) > 3, "the axis is not numbered at all"
        gaps = [b - a for a, b in zip(numbers, numbers[1:])]
        assert min(gaps) >= 10.0, (
            f"two question numbers are {min(gaps):.1f}pt apart and overlap")

    def test_the_slices_are_as_tall_as_the_counts_they_come_from(self, built):
        """The one thing a reader takes from this chart is *how much* of a
        column is the key — so the key's slice is measured against the column's
        full height. A stack whose slices all start at the zero line has the same
        total and is not a stack; measuring their sum would pass it."""
        exam, analysis = built
        chart = next(c for c in analysis_report._chart_specs(analysis, "id")
                     if c.kind == "stacked")
        grid, recorder = _drawn(chart)
        slices = [r for r in recorder.rects
                  if r[4] in (analysis_report._KEY, analysis_report._ACCENT)
                  and r[2] < 30]
        by_column = {}
        for x, y, width, height, colour in slices:
            by_column.setdefault(round(x, 1), []).append((y, height, colour))
        answered = [item for item in analysis.items
                    if item.distractors and sum(d.count for d in item.distractors)]
        assert len(by_column) == len(answered), (
            f"{len(by_column)} columns drawn for {len(answered)} questions with answers")
        for item, parts in zip(answered, [by_column[k]
                                          for k in sorted(by_column)]):
            parts.sort()
            total = sum(option.count for option in item.distractors)
            key = sum(option.count for option in item.distractors if option.key)
            # The column's height is the *extent* of what was drawn, not the sum of
            # the slices: slices that each start at the zero line sum to the same
            # total as a stack and look nothing like one.
            height = (max(y + h for y, h, _c in parts)
                      - min(y for y, _h, _c in parts))
            key_height = max(h for _y, h, c in parts
                             if c == analysis_report._KEY)
            expected = len([o for o in item.distractors if o.count])
            assert len(parts) == expected, (
                f"question {item.index + 1} drew {len(parts)} slices for "
                f"{expected} chosen options")
            assert abs(key_height / height - key / total) < 0.005, (
                f"question {item.index + 1}: the key slice is "
                f"{key_height / height:.3f} of the column but {key / total:.3f} of "
                f"the answers")

    def test_the_histogram_keeps_the_bins_nobody_is_in(self):
        """A gap in a distribution is information. Dropping the empty bins closes
        it up, so a class with two groups looks like a single smooth spread — and
        the bars no longer sit where their labels say they do."""
        class Stub:
            items: list = []
            person_bins = [(-1.0, 0), (-0.5, 3), (0.0, 0), (0.5, 2)]

        specs = {chart.title: chart
                 for chart in analysis_report._chart_specs(Stub(), "id")}
        histogram = specs[analysis_report.labels("id")["people_chart"]]
        assert histogram.values == [0, 3, 0, 2], (
            "the histogram dropped a range nobody scored in")
        assert len(histogram.labels) == len(Stub.person_bins)

    def test_a_chart_with_nothing_to_draw_is_empty_not_zero(self):
        """`empty()` is what decides whether the grid is skipped: a chart whose
        values are all zero has data, one with no values does not."""
        assert analysis_report._Chart("bars", "t").empty()
        assert analysis_report._Chart("scatter", "t").empty()
        assert not analysis_report._Chart("bars", "t", values=[0.0]).empty()
