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


def _rgb(hex_colour: str) -> tuple[float, float, float]:
    """`#059669` as the rounded triplet PyMuPDF reports for a fill.

    The palette is written once, as hex, and a page reports components — so the
    comparison has to be made in one of the two, and comparing a tuple to a string
    is a test that fails for the right reason and says the wrong one.
    """
    raw = hex_colour.lstrip("#")
    return tuple(round(int(raw[index:index + 2], 16) / 255, 2)
                 for index in (0, 2, 4))


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

    def translate(self, x, y):
        # The legend is drawn by the grid through a `translate`, so a recorder
        # without this cannot see the legend at all — which is how "the key is in
        # the document" would pass while the key was drawn off the page.
        self.texts.append(("translate", (x, y)))

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
        # Any ink the drawings use, not one of them: the item map and the logit
        # bars are coloured by *finding* now (the palette the key explains), so a
        # count that only saw `_ACCENT` would report a chart that dropped its
        # questions when it had merely re-coloured them.
        ink = {_rgb(colour) for colour in analysis_report.FLAG_TONES.values()}
        ink |= {ACCENT, KEY}
        filled = [shape for shape in _shapes(first) if shape[0] in ink]
        plotted = len([i for i in analysis.items if i.measure is not None])
        assert len(filled) >= plotted + len(analysis.person_bins), (
            f"{len(filled)} filled shapes for {plotted} calibrated questions and "
            f"{len(analysis.person_bins)} bins")
        assert any(shape[1] for shape in filled), (
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

    def test_a_line_of_text_never_lands_on_the_line_below_it(self, built):
        """The defect the same-line check above structurally cannot see.

        Grouping spans by their rounded top edge compares a line with itself, so
        two lines 3pt apart are never compared — and 3pt apart is exactly how the
        colour key came to be printed over the x-axis captions of the drawings
        above it. On the built file, `Logit` and `Baik — soal bekerja seperti
        seharusnya.` shared 4.6pt of height and `No. soal` shared 20pt of width
        with `Daya beda negatif atau menyimpang — soal ini merugikan murid kuat.`
        The key is painted upward from its own origin, so a two-row key started
        1pt *inside* the boxes; this is that, measured.

        Per **word**, and that is the point rather than a detail: the span
        extractor glues adjacent runs at one baseline into a single box — three
        question numbers printed side by side come back as one span twenty points
        wide — so a span-level rule reports collisions between numbers that never
        touch and cannot see the ones that do. Words carry their own boxes.
        """
        exam, analysis = built
        for lang in ("id", "en"):
            pdf = analysis_report.analysis_pdf(analysis, exam, lang=lang)
            for index, page in enumerate(_document(pdf)):
                words = [(word[0], word[1], word[2], word[3], word[4])
                         for word in page.get_text("words")]
                for position, a in enumerate(words):
                    for b in words[position + 1:]:
                        tall = min(a[3], b[3]) - max(a[1], b[1])
                        wide = min(a[2], b[2]) - max(a[0], b[0])
                        # 1.5pt, not 0: consecutive lines of a paragraph report
                        # bounding *boxes*, which overlap by about a point when the
                        # leading is tight without any ink touching. Everything
                        # measured above was 2.6pt or more.
                        assert tall < 1.5 or wide < 1.5, (
                            f"{lang} page {index + 1}: {a[4]!r} at "
                            f"{a[0]:.0f},{a[1]:.0f} and {b[4]!r} at "
                            f"{b[0]:.0f},{b[1]:.0f} share {tall:.1f}pt of height "
                            f"and {wide:.1f}pt of width")

    def test_the_paper_header_is_short_and_the_words_are_a_legend(self, built):
        """Sixteen full names on a landscape A4 do not fit; the symbols do, and
        the words they stand for are one line above the table."""
        exam, analysis = built
        pdf = analysis_report.analysis_pdf(analysis, exam, lang="id")
        # The page that holds the *table*: the drawings take the first page on a
        # full report, so the legend is looked for where its columns are, not on
        # page 1 — a test pinned to "page 1" measures pagination, not the legend.
        legend = analysis_report.labels("id")["legend"]
        holding = [page for page in _document(pdf) if legend in page.get_text()]
        assert holding, "the symbols under the item table are never explained"
        assert analysis_report.labels("id")["h_no"] in holding[0].get_text(), (
            "the legend is not on the page with the table it explains")
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

    def test_the_ability_histogram_counts_people_not_choices(self, built):
        """The same drawing was labelled two ways by two documents.

        `chosen_count` ("Jumlah pemilih") is the *option* chart's axis — how many
        students chose a distractor. On the ability histogram it labelled the
        students themselves as voters, while the workbook written from the same
        bins already said `legend_students`. A reader comparing the PDF with the
        spreadsheet found two names for one chart; this is the one place either
        can be checked without opening them.
        """
        exam, analysis = built
        t = analysis_report.labels("id")
        specs = {chart.title: chart
                 for chart in analysis_report._chart_specs(analysis, "id")}
        histogram = specs[t["people_chart"]]
        assert histogram.y_label == t["legend_students"], (
            f"the ability histogram's y axis says {histogram.y_label!r}")
        assert histogram.y_label != t["chosen_count"], (
            "the histogram is labelled with the option chart's counts")
        # The two documents are read against each other in
        # `test_analysis_workbook.py`, which is where the workbook is opened.

    def test_a_chart_with_nothing_to_draw_is_empty_not_zero(self):
        """`empty()` is what decides whether the grid is skipped: a chart whose
        values are all zero has data, one with no values does not."""
        assert analysis_report._Chart("bars", "t").empty()
        assert analysis_report._Chart("scatter", "t").empty()
        assert not analysis_report._Chart("bars", "t", values=[0.0]).empty()


# ── the key under the drawings ──────────────────────────────────────────────

class TestTheColourKey:
    """The drawings have colours that mean something, and the file is read alone.

    Everything here is about the reader who opens the PDF a term later: a red dot
    with nothing saying what red is is a decoration, and a key that lost its last
    entry to fit one line lies by omission.
    """

    def test_every_flag_the_analyser_can_raise_has_a_colour(self):
        """One palette, and no flag outside it — a new finding must not ship grey
        by accident and look like "not measurable"."""
        assert set(analysis_report.FLAG_LABELS) == set(analysis_report.FLAG_TONES), (
            "a finding flag has no colour in the palette, or a colour has no flag")
        assert analysis_report.tone_for("something_new") == analysis_report.FLAG_FALLBACK

    def test_the_palette_is_what_the_page_is_told(self):
        """The page, the PDF, the workbook and a copied image all read this one
        table; the literals that used to live in the Alpine component were the same
        information in a place no document could reach."""
        assert analysis_report.chart_palette() == analysis_report.FLAG_TONES
        palette = analysis_report.chart_palette()
        palette["ok"] = "#ffffff"
        assert analysis_report.FLAG_TONES["ok"] != "#ffffff", (
            "the palette handed to the page is an alias of the module's table, so "
            "editing it once would change the PDF for every school")

    def test_the_key_names_the_acts_not_the_statistics(self, built):
        _exam, analysis = built
        charts = [c for c in analysis_report._chart_specs(analysis, "id")
                  if not c.empty()]
        entries = analysis_report.chart_key("id", charts)
        colours = [colour for colour, _text in entries]
        assert analysis_report.tone_for("weak") in colours
        assert analysis_report.tone_for("unkeyed") in colours
        # A keyed option only appears when a chart actually stacks options.
        assert analysis_report._KEY in colours
        assert analysis_report._ACCENT in colours

    def test_a_key_with_no_stacked_chart_does_not_list_the_options(self, built):
        """The option swatches explain a drawing that is not there otherwise."""
        _exam, analysis = built
        charts = [c for c in analysis_report._chart_specs(analysis, "id")
                  if not c.empty() and c.kind != "stacked"]
        colours = [colour for colour, _text in analysis_report.chart_key("id", charts)]
        assert analysis_report._KEY not in colours
        assert analysis_report.tone_for("ok") in colours

    def test_the_key_is_in_the_document_and_says_what_red_is(self, built):
        exam, analysis = built
        pdf = analysis_report.analysis_pdf(analysis, exam, lang="en")
        text = " ".join(span for page in _document(pdf) for bbox, span in _spans(page))
        t = analysis_report.labels("en")
        for key in ("chart_legend", "legend_ok", "legend_weak", "legend_bad",
                    "legend_extreme", "legend_unmeasured"):
            assert t[key][:34] in text, f"the key is missing its {key} entry"

    def test_the_key_cannot_be_separated_from_the_drawings(self):
        """It rides inside the grid flowable.

        As its own flowable it was the last item of an already-full page: the key
        landed on page 2 under nothing while the drawings it explains stayed on
        page 1, which is how this was found — by opening the PDF.
        """
        chart = analysis_report._Chart("bars", "t", values=[1.0, 2.0])
        legend = analysis_report._ChartLegend([("#ff0000", "one")], heading="key")
        plain = analysis_report._ChartGrid([chart], "none", columns=1)
        with_key = analysis_report._ChartGrid([chart], "none", columns=1, legend=legend)
        recorder = RecordingCanvas()
        for grid in (plain, with_key):
            grid.canv = recorder
            grid.wrap(500, 500)
        assert with_key.height > plain.height, "the key takes no room in the grid"
        assert with_key.legend is legend

    def test_the_document_hands_the_key_to_the_grid_rather_than_appending_it(
            self, built, monkeypatch):
        """The mechanism, because the outcome can still hold by luck.

        A key appended as its own flowable lands on the drawings' page whenever
        there is room for it — so a same-page assertion passes on a smaller report
        and fails on a larger one, which is the worst kind of guard. Passing it
        *into* the grid is what makes the pairing structural.
        """
        from reportlab.platypus import SimpleDocTemplate

        exam, analysis = built
        captured: dict[str, list] = {}
        original = SimpleDocTemplate.build

        def spy(self, flowables, *args, **kwargs):
            captured["flow"] = list(flowables)
            return original(self, flowables, *args, **kwargs)

        monkeypatch.setattr(SimpleDocTemplate, "build", spy)
        analysis_report.analysis_pdf(analysis, exam, lang="id")
        flow = captured.get("flow") or []
        grids = [item for item in flow
                 if isinstance(item, analysis_report._ChartGrid)]
        assert grids, "the document has no chart grid"
        assert any(grid.legend is not None for grid in grids), (
            "the colour key was not handed to the grid")
        loose = [item for item in flow
                 if isinstance(item, analysis_report._ChartLegend)]
        assert not loose, (
            "the colour key is a flowable of its own, so it can be pushed onto the"
            " next page away from the drawings")

    def test_the_document_puts_the_key_on_the_page_with_the_drawings(self, built):
        """The property the flowable exists for, measured on the built file.

        The test above is about the class; this one is about the document, and
        without it `analysis_pdf` could append the key as its own flowable and
        every assertion about `_ChartGrid` would still pass.
        """
        exam, analysis = built
        pdf = analysis_report.analysis_pdf(analysis, exam, lang="id")
        t = analysis_report.labels("id")
        pages = _document(pdf)
        with_key = [number for number, page in enumerate(pages)
                    if t["chart_legend"] in page.get_text()]
        with_drawings = [number for number, page in enumerate(pages)
                         if t["item_map"] in page.get_text()]
        assert with_key, "the colour key is not in the document at all"
        assert with_drawings, "the charts are not in the document at all"
        assert set(with_key) == set(with_drawings), (
            f"the key is on page(s) {with_key} and the drawings on "
            f"{with_drawings}")

    def test_the_key_wraps_rather_than_losing_an_entry(self):
        """A legend that hides its last entry to fit one line is a legend that
        lies by omission, and the labels are sentences by design."""
        long_entries = [(analysis_report.tone_for(flag), "x" * 60)
                        for flag in ("ok", "weak", "negative", "extreme_easy",
                                     "unkeyed")]
        legend = analysis_report._ChartLegend(long_entries, heading="Keterangan")
        recorder = RecordingCanvas()
        legend.canv = recorder
        width, height = legend.wrap(220, 500)
        assert len(legend.rows) > 1, "the wide legend stayed on one line"
        assert height >= len(legend.rows) * 11 - 1
        legend.draw()
        # `drawString(x, y, text)`, so the text is the third argument — reading the
        # second compares labels against coordinates and passes whatever it finds.
        drawn = [args[2] for kind, args in recorder.texts
                 if kind in ("left", "centred", "right")]
        for _colour, text in long_entries:
            assert text in drawn, "an entry was dropped instead of wrapped"


class TestTheDrawingKeepsItsFrame:
    """The plot stays inside its box and out of the title band.

    The bars used to grow into the title from underneath and the two printed over
    each other, which is a defect a page image shows and a span list does not.
    """

    def test_a_bar_never_reaches_the_title(self):
        chart = analysis_report._Chart("bars", "A title", values=[1.0, 5.0, 3.0],
                                       labels=[1, 2, 3])
        grid, recorder = _drawn(chart)
        head = analysis_report._ChartGrid.HEAD
        ceiling = analysis_report._ChartGrid([chart], "none").box_height - head
        bars = [rect for rect in recorder.rects if rect[2] < 40 and rect[4]]
        assert bars, "no bars were drawn at all"
        for _x, y, _w, height, _colour in bars:
            assert y + height <= ceiling + 0.01, (
                f"a bar reaches {y + height:.1f} and the plot stops at {ceiling:.1f}")
        titles = [args[2] for kind, args in recorder.texts if kind == "left"]
        assert "A title" in titles
        grid_title_y = [args[1] for kind, args in recorder.texts
                        if kind == "left" and args[2] == "A title"][0]
        assert grid_title_y > max(y + height for _x, y, _w, height, _c in bars), (
            "the title is not above the tallest bar")

    def test_the_axis_caption_is_under_the_plot_not_over_it(self):
        """`Soal → logit` used to be drawn inside the plot area, across the bars."""
        chart = analysis_report._Chart("bars", "Difficulty", values=[1.0, 2.0],
                                       labels=[1, 2], x_label="Logit")
        _grid, recorder = _drawn(chart)
        captions = [args for kind, args in recorder.texts
                    if kind == "centred" and args[2] == "Logit"]
        assert captions, "the axis caption was not drawn"
        caption_y = captions[0][1]
        bars = [rect for rect in recorder.rects if rect[2] < 40 and rect[4]]
        assert min(y for _x, y, _w, _h, _c in bars) > caption_y, (
            "the axis caption sits inside the plot")

    def test_the_title_band_holds_the_note_it_was_given(self):
        """The four hints used to run together in one paragraph under the row of
        charts, where a reader could not match a sentence to a picture."""
        chart = analysis_report._Chart("bars", "Difficulty", values=[1.0], labels=[1],
                                       note="Zero is an average question.")
        _grid, recorder = _drawn(chart)
        notes = [args[2] for kind, args in recorder.texts
                 if kind == "right" and "average" in str(args[2])]
        assert notes, "the chart's note was not drawn in its own box"

    def test_the_item_table_opens_on_the_page_its_heading_is_on(self, built):
        """A heading stranded at the foot of one page with its table overleaf."""
        exam, analysis = built
        pdf = analysis_report.analysis_pdf(analysis, exam, lang="id")
        heading = analysis_report.labels("id")["items"]
        pages = _document(pdf)
        for number, page in enumerate(pages, start=1):
            text = " ".join(span for _bbox, span in _spans(page))
            if heading not in text:
                continue
            # The table's own header row has to be on that page too, and the
            # heading is not the last thing on it.
            assert analysis_report.labels("id")["h_no"] in text, (
                f"page {number} has the item heading without the table")
            return
        raise AssertionError("the item section is not in the document at all")

    def test_a_small_report_does_not_orphan_its_heading_either(self):
        """The case that broke: five questions and one paper.

        A full report fills the first page with drawings whatever the size of the
        class, so the heading has nowhere to be stranded. A *small* one is where
        there used to be room for the heading and none for the table.
        """
        exam, submissions = _class(items=3, students=6)
        analysis = item_analysis.analyse(exam, submissions)
        pdf = analysis_report.analysis_pdf(analysis, exam, lang="id")
        t = analysis_report.labels("id")
        pages = _document(pdf)
        for number, page in enumerate(pages, start=1):
            text = " ".join(span for _bbox, span in _spans(page))
            if t["items"] in text:
                assert t["h_no"] in text, (
                    f"page {number} has the item heading and not the table")
                return
        raise AssertionError("the item section is not in the document at all")

    def test_the_tables_begin_after_the_drawings_on_purpose(self, built, monkeypatch):
        """The rule, at the level of the flow: a page break stands between them.

        The two tests above check the *outcome*, and an outcome can hold by
        accident — the drawings happen to fill the page. This one checks the
        mechanism, so removing the break is a caught defect rather than a change
        that no fixture notices.
        """
        from reportlab.platypus import PageBreak, SimpleDocTemplate

        exam, analysis = built
        captured: dict[str, list] = {}
        original = SimpleDocTemplate.build

        def spy(self, flowables, *args, **kwargs):
            captured["flow"] = list(flowables)
            return original(self, flowables, *args, **kwargs)

        monkeypatch.setattr(SimpleDocTemplate, "build", spy)
        analysis_report.analysis_pdf(analysis, exam, lang="id")
        flow = captured.get("flow")
        assert flow, "the document was built without a flowable list"
        heading = analysis_report.labels("id")["items"]
        at = [index for index, item in enumerate(flow)
              if getattr(item, "text", None) == heading]
        assert at, "the item heading is not in the flow at all"
        assert any(isinstance(flow[index - 1], PageBreak) for index in at), (
            "the item table can begin at the foot of the drawings' page")
