"""The analysis workbook, read back the way a spreadsheet opens it.

An `.xlsx` export is easy to get wrong in three ways that all *look* fine on the
screen and are useless in Excel, so each is a test here:

* **numbers stored as text.** `analysis_rows` formats every cell into a string,
  which is right for a print document and wrong for a spreadsheet: a column of
  text sorts `-1.94` above `0.5`, `AVERAGE` returns zero, and no chart can be
  drawn from it. The cells hold values.
* **a chart that is a picture of the data.** The drawings are native Excel charts
  anchored on the sheet whose columns they plot, so a teacher can retitle or
  re-range them and they follow a correction.
* **a colour with no key.** The item column is coloured by finding and Excel has
  no legend that can say what the colours mean, so the workbook carries filled
  swatches and the sentence for each.

Every assertion opens the produced bytes with `openpyxl`, because the thing being
tested is a file, not a function's return value.
"""
from __future__ import annotations

import io
import random

import pytest
from openpyxl import load_workbook

from app.services import analysis_report, item_analysis

OPTIONS = "ABCDE"

#: Excel refuses a sheet name over 31 characters or containing any of these.
FORBIDDEN = set(': \\ / ? * [ ]'.replace(" ", ""))


def _analysis(items: int = 12, students: int = 40, seed: int = 20260920,
              unkeyed: int = 1):
    rng = random.Random(seed)
    difficulty = ([0.85] * (items // 3) + [0.5] * (items // 3)
                  + [0.2] * (items - 2 * (items // 3)))
    exam = {
        "id": "0f0f0f0f-1111-2222-3333-444455556666",
        "title": "Ujian Akhir Semester Fisika",
        "total_questions": items,
        "question_types": {str(i): "mcq" for i in range(items)},
        "answer_key": {str(i): "A" for i in range(items - unkeyed)},
        "passing_score": 70,
    }
    submissions = []
    for student in range(students):
        ability = rng.gauss(0, 1)
        answers = {}
        for index in range(items - unkeyed):
            p = min(0.97, max(0.03, difficulty[index] + 0.15 * ability))
            answers[str(index)] = ("A" if rng.random() < p
                                   else rng.choice(OPTIONS[1:]))
        submissions.append({"student_id": f"s{student:03d}",
                            "student_name": f"Murid {student:03d}",
                            "answers": answers, "status": "graded"})
    return exam, item_analysis.analyse(exam, submissions)


@pytest.fixture(scope="module")
def book():
    exam, analysis = _analysis()
    return exam, analysis, load_workbook(io.BytesIO(
        analysis_report.analysis_xlsx(analysis, exam, lang="id")))


def _sheet(workbook, key: str, lang: str = "id"):
    return workbook[analysis_report._sheet(key, lang)]


def _charts(sheet):
    return list(getattr(sheet, "_charts", []))


class TestTheWorkbookOpens:
    def test_every_section_of_the_report_has_a_sheet(self, book):
        """In order, and only the sections that have something in them: an empty
        worksheet under a heading is a page a reader has to check before ignoring."""
        _exam, analysis, workbook = book
        # `framework` is written before anything else and unconditionally; only
        # the two sheets that depend on the exam's own kisi-kisi are left out
        # when there is nothing to say. The list went stale the moment the
        # framework sheet was added, and the suite kept reporting a sheet as
        # missing that the workbook had been writing all along.
        wanted = ["summary", "framework", "items", "options", "groups", "people", "key"]
        expected = []
        for key in wanted:
            if key == "options" and not any(i.distractors for i in analysis.items):
                continue
            if key == "groups" and not analysis.splits:
                continue
            expected.append(analysis_report._sheet(key, "id"))
        assert workbook.sheetnames == expected, (
            f"sheets are {workbook.sheetnames}, expected {expected}")

    @pytest.mark.parametrize("lang", ["id", "en"])
    def test_every_sheet_name_is_one_excel_accepts(self, lang):
        """A name Excel rejects makes the file refuse to open at all, and the
        label table is written for a *page* — one of its headings is 32
        characters, which is why the sheets have their own names."""
        for key, names in analysis_report.SHEET_NAMES.items():
            name = names[lang]
            assert len(name) <= 31, f"{key}: {name!r} is {len(name)} characters"
            assert not (FORBIDDEN & set(name)), f"{key}: {name!r} has a forbidden character"
            assert name.strip() == name and name, f"{key}: {name!r} is not a name"

    def test_the_reason_the_sheets_have_their_own_names_is_still_true(self):
        """Measured, not assumed: the label table is written for a page, and at
        least one of its headings is too long for a worksheet. If that ever stops
        being true this fails and the table can be simplified — and if it silently
        became true again, the sheets would have been named by truncation."""
        over = [key for key, value in analysis_report.labels("id").items()
                if isinstance(value, str) and len(value) > 31]
        assert over, (
            "no Indonesian label is over 31 characters any more, so the sheet "
            "names no longer need their own table")


class TestTheNumbersAreNumbers:
    def test_the_item_sheet_holds_values_not_text(self, book):
        """The whole reason this export is not a CSV with a different extension."""
        _exam, _analysis, workbook = book
        sheet = _sheet(workbook, "items")
        header = [cell.value for cell in sheet[1]]
        for column, name in enumerate(header, start=1):
            if column <= 2 or name in ("No", "Tipe", "Catatan butir"):
                continue
            values = [sheet.cell(row=row, column=column).value
                      for row in range(2, sheet.max_row + 1)]
            strings = [value for value in values
                       if isinstance(value, str) and value not in ("", None)]
            assert not strings, (
                f"column {name!r} holds text ({strings[:3]}), so it cannot be "
                f"sorted, averaged or charted")

    def test_a_missing_measure_is_an_empty_cell_not_a_zero(self, book):
        """An uncalibrated question has no difficulty. `0.00` is a measurement."""
        _exam, analysis, workbook = book
        sheet = _sheet(workbook, "items")
        column = analysis_report._ITEM_COLUMN["measure"]
        for row, item in enumerate(analysis.items, start=2):
            value = sheet.cell(row=row, column=column).value
            if item.measure is None:
                assert value in ("", None), (
                    f"question {item.index + 1} has no measure but the cell says "
                    f"{value!r}")
            else:
                assert isinstance(value, (int, float))

    def test_the_summary_sheet_keeps_its_numbers(self, book):
        _exam, analysis, workbook = book
        sheet = _sheet(workbook, "summary")
        found = {sheet.cell(row=row, column=1).value:
                 sheet.cell(row=row, column=2).value
                 for row in range(1, sheet.max_row + 1)}
        label = analysis_report.labels("id")["students"]
        assert found.get(label) == analysis.summary.students
        assert isinstance(found.get(label), int)


class TestTheChartsAreEditableExcelCharts:
    def test_the_item_sheet_carries_both_of_its_drawings(self, book):
        _exam, _analysis, workbook = book
        sheet = _sheet(workbook, "items")
        charts = _charts(sheet)
        titles = {chart.title is not None for chart in charts}
        assert len(charts) == 2, f"{len(charts)} charts on the item sheet"
        assert titles == {True}, "a chart was added with no title"

    def test_every_chart_sits_on_the_sheet_whose_data_it_plots(self, book):
        """Anchored, not floating: openpyxl keeps `_charts` per sheet, so a chart
        that actually belongs to a sheet is on it — and the reference it holds can
        be pointed at that sheet's own columns."""
        _exam, _analysis, workbook = book
        for key, expected in (("items", 2), ("options", 1), ("people", 1)):
            sheet = _sheet(workbook, key)
            assert len(_charts(sheet)) == expected, (
                f"{key}: {len(_charts(sheet))} charts, expected {expected}")

    def test_the_two_documents_call_the_same_axis_the_same_name(self, book):
        """The workbook and the PDF draw the same four charts, so a reader who
        opens both must not find two names for one axis.

        They did: the ability histogram's y axis was `legend_students`
        ("Jumlah murid") in the workbook and `chosen_count` ("Jumlah pemilih",
        the *option* chart's count) in the PDF. Asserted as agreement between the
        two documents rather than each against a constant, because a constant is
        what both would drift from.
        """
        exam, analysis, workbook = book
        sheet = _sheet(workbook, "people")
        t = analysis_report.labels("id")
        histogram = next(chart for chart in _charts(sheet)
                         if type(chart).__name__ == "BarChart")
        assert histogram.y_axis.title is not None, (
            "the ability histogram lost its y-axis title")
        specs = {chart.title: chart
                 for chart in analysis_report._chart_specs(analysis, "id")}
        pdf_label = specs[t["people_chart"]].y_label
        assert histogram.y_axis.title.tx.rich.p[0].r[0].t == pdf_label, (
            f"the workbook calls it {histogram.y_axis.title.tx.rich.p[0].r[0].t!r}"
            f" and the PDF {pdf_label!r}")
        assert t["chosen_count"] not in (pdf_label,
                                         histogram.y_axis.title.tx.rich.p[0].r[0].t)
        assert exam is not None  # the fixture is a whole exam, not a stub

    def test_the_item_map_plots_the_two_columns_the_label_names(self, book):
        """The chart's own reference, resolved to `A1`-style cells: a chart drawn
        from the wrong column is a picture of nothing and looks fine."""
        _exam, _analysis, workbook = book
        sheet = _sheet(workbook, "items")
        scatter = next(chart for chart in _charts(sheet)
                       if type(chart).__name__ == "ScatterChart")
        series = scatter.series[0]
        # `numRef.f` is the whole range as Excel writes it — "Items!$F$2:$F$13" —
        # so the column is checked in the reference itself rather than resolved.
        x_range = series.xVal.numRef.f
        y_range = series.yVal.numRef.f
        p_column = chr(64 + analysis_report._ITEM_COLUMN["pct"])
        d_column = chr(64 + analysis_report._ITEM_COLUMN["disc"])
        assert f"${p_column}$2:" in x_range, (
            f"the item map plots difficulty from {x_range}")
        assert f"${d_column}$2:" in y_range, (
            f"the item map plots discrimination from {y_range}")
        assert analysis_report._sheet("items", "id") in x_range, (
            "the chart is not read from the sheet it is anchored on")

    def test_the_difficulty_chart_is_a_column_per_question(self, book):
        _exam, analysis, workbook = book
        sheet = _sheet(workbook, "items")
        bars = [chart for chart in _charts(sheet)
                if type(chart).__name__ == "BarChart"]
        assert bars, "the difficulty chart is gone"
        chart = bars[0]
        categories = chart.series[0].cat.numRef.f
        no_column = chr(64 + analysis_report._ITEM_COLUMN["no"])
        assert f"${no_column}$2:" in categories, (
            f"the difficulty bars are labelled from {categories}")
        # Rows 2..n and not row 1: `titles_from_data=True` takes the header as the
        # series *name*, so the values start under it. An assertion written against
        # `$J$1` would fail on a chart that is perfectly right.
        rows = len(analysis.items) + 1
        measure_column = chr(64 + analysis_report._ITEM_COLUMN["measure"])
        assert f"${measure_column}$2:${measure_column}${rows}" \
            in chart.series[0].val.numRef.f, (
            f"the difficulty chart plots {chart.series[0].val.numRef.f} and the "
            f"item sheet has {rows} rows")


class TestTheColourKeyTravelsWithIt:
    def test_every_finding_has_a_filled_swatch_on_the_key_sheet(self, book):
        _exam, _analysis, workbook = book
        sheet = _sheet(workbook, "key")
        by_text = {}
        for row in range(1, sheet.max_row + 1):
            text = sheet.cell(row=row, column=2).value
            fill = sheet.cell(row=row, column=1).fill
            if text:
                by_text[text] = fill.fgColor.rgb
        for colour, text in analysis_report.legend_entries("id"):
            assert text in by_text, f"the key sheet has no {text!r}"
            assert by_text[text].lower().endswith(colour.lstrip("#").lower()), (
                f"{text!r} is filled {by_text[text]} and should be {colour}")

    def test_the_option_swatches_are_on_the_key_sheet_too(self, book):
        _exam, _analysis, workbook = book
        sheet = _sheet(workbook, "key")
        text = [str(sheet.cell(row=row, column=1).value)
                for row in range(1, sheet.max_row + 1)]
        joined = " ".join(text)
        assert analysis_report.labels("id")["legend_key"] in joined
        assert analysis_report.labels("id")["legend_distractor"] in joined

    def test_the_item_flag_cell_is_filled_with_the_palette_colour(self, book):
        """The column is coloured by finding, so a teacher can filter on colour —
        which only means anything if the colour is the palette's."""
        _exam, analysis, workbook = book
        sheet = _sheet(workbook, "items")
        column = analysis_report._ITEM_COLUMN["flag"]
        for row, item in enumerate(analysis.items, start=2):
            fill = sheet.cell(row=row, column=column).fill
            expected = analysis_report.tone_for(item.flag).lstrip("#").lower()
            assert str(fill.fgColor.rgb).lower().endswith(expected), (
                f"question {item.index + 1} is flagged {item.flag} and coloured "
                f"{fill.fgColor.rgb}")

    def test_the_explanation_of_the_columns_is_on_the_key_sheet(self, book):
        _exam, _analysis, workbook = book
        sheet = _sheet(workbook, "key")
        note = analysis_report.labels("id")["legend"]
        assert any(sheet.cell(row=row, column=1).value == note
                   for row in range(1, sheet.max_row + 1)), (
            "the workbook prints P, D, r and b with nothing saying what they are")

    def test_the_english_workbook_says_the_same_things_in_english(self):
        exam, analysis = _analysis()
        workbook = load_workbook(io.BytesIO(
            analysis_report.analysis_xlsx(analysis, exam, lang="en")))
        assert workbook.sheetnames[0] == analysis_report._sheet("summary", "en")
        sheet = workbook[analysis_report._sheet("key", "en")]
        texts = [sheet.cell(row=row, column=2).value
                 for row in range(1, sheet.max_row + 1)]
        assert analysis_report.labels("en")["legend_ok"] in texts
