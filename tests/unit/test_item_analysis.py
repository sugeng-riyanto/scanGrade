"""The numbers a paper's questions did, and the documents that carry them out.

Two kinds of assertion here, and both are needed:

* the arithmetic, against values that can be checked by hand;
* the **direction of the scale**, which is the defect this module actually shipped:
  the extreme-score correction used one sign for items and the other for papers, so
  a perfect paper was published at the bottom of the ability scale and a zero paper
  at the top. Every number below it was wrong too, because those thetas feed the
  item equations and the fit statistics. A test of "does it return a float" would
  have passed.
"""
from __future__ import annotations

import csv
import io
import math

import pytest

from app.services import analysis_report as report
from app.services import item_analysis as ia


# ── helpers ──────────────────────────────────────────────────────────────────

def exam(n: int = 4, key: dict | None = None, types: dict | None = None,
         weights: dict | None = None) -> dict:
    return {
        "id": "exam-1", "title": "Mid Semester 1", "total_questions": n,
        "question_types": types or {str(i): "mcq" for i in range(n)},
        "question_weights": weights or {str(i): 25.0 for i in range(n)},
        "answer_key": key if key is not None else {str(i): "A" for i in range(n)},
    }


def papers(rows: list[list], names: bool = True) -> list[dict]:
    """`1` right, `0` wrong, `None` left blank (the answer map omits it)."""
    out = []
    for j, row in enumerate(rows):
        answers = {}
        for i, value in enumerate(row):
            if value is None:
                continue
            answers[str(i)] = "A" if value else "B"
        out.append({"id": f"s{j}", "student_id": f"s{j}",
                    "student_name": f"Paper {j}" if names else "",
                    "answers": answers})
    return out


# ── the direction of the scale ───────────────────────────────────────────────

class TestTheFloorDoesNotBend:
    """What the two ends of the scale mean, stated as the app reads them."""

    def test_a_hard_item_is_positive_and_an_easy_one_negative(self):
        assert ia._difficulty_from(0.1) > 0 > ia._difficulty_from(0.9)

    def test_the_response_curve_rises_with_ability(self):
        assert ia._logistic(-1) < ia._logistic(0) < ia._logistic(1)

    def test_a_proportion_reads_back_through_the_difficulty_not_the_ability(self):
        # `_logit` is the *difficulty* transform, so negating it is what makes the
        # curve return the original proportion. Getting this backwards is the whole
        # defect, and this is the one line that pins it.
        assert ia._logistic(-ia._difficulty_from(0.3)) == pytest.approx(0.3)

    def test_an_extreme_score_is_nudged_off_the_boundary(self):
        assert ia._nudged(10, 10) == pytest.approx(9.7)
        assert ia._nudged(0, 10) == pytest.approx(0.3)
        assert ia._nudged(5, 10) == 5

    def test_a_perfect_paper_is_the_highest_on_the_scale(self):
        """The regression. A perfect paper and a zero paper take the correction
        branch; before the fix they came out inverted."""
        rows = [[1] * 10, [1] * 9 + [0], [1] * 5 + [0] * 5, [1] * 4 + [0] * 6,
                [1] + [0] * 9, [0] * 10]
        result = ia.analyse(exam(10), papers(rows))
        by_raw = {p.raw: p.measure for p in result.people}

        assert by_raw[10] > 0 > by_raw[0], "the perfect paper must be above zero"
        assert by_raw[10] > by_raw[9], "and above the paper that scored one less"
        assert by_raw[0] < by_raw[1], "and the zero paper below the one that scored 1"

    def test_ability_never_runs_backwards_however_skewed_the_class(self):
        # A near-Guttman class is the worst case for an extremum: everyone above
        # the middle is perfect and everyone below it is zero.
        rows = [[1] * 6 + [0] * 4 if j < 4 else [0] * 10 for j in range(8)]
        result = ia.analyse(exam(10), papers(rows))
        ordered = [p.measure for p in sorted(result.people, key=lambda p: p.raw)]
        assert ordered == sorted(ordered)


# ── the classical statistics ─────────────────────────────────────────────────

class TestClassicalStatistics:

    def test_pearson_knows_a_line_from_a_flat_line(self):
        assert ia._pearson([1, 2, 3, 4], [2, 4, 6, 8]) == pytest.approx(1.0)
        assert ia._pearson([1, 2, 3, 4], [8, 6, 4, 2]) == pytest.approx(-1.0)
        assert ia._pearson([1, 2, 3, 4], [5, 5, 5, 5]) == 0.0

    def test_a_question_everybody_aced_has_100_percent_and_no_discrimination(self):
        rows = [[1, 1], [1, 0], [1, 1], [1, 0]]
        result = ia.analyse(exam(2), papers(rows))
        item = result.items[0]
        assert item.pct == 100.0
        assert item.discrimination == 0.0
        assert item.flag == "extreme_easy"

    def test_an_inverted_question_is_flagged_and_measures_hard(self):
        """Strong students missing it and weak students getting it is the one
        pattern worth reading a paper for."""
        rows = []
        for j in range(12):
            strong = j < 6
            rows.append([1 if strong else 0, 1, 1 if j < 3 else 0,
                         0 if strong else 1])
        result = ia.analyse(exam(4), papers(rows))
        inverted = result.items[3]
        assert inverted.discrimination < 0
        assert inverted.point_biserial < 0
        assert inverted.flag in ("negative", "weak")
        assert inverted.measure > 0, "harder for the class that should find it easy"

    def test_the_difficulty_denominator_is_the_papers_that_answered(self):
        """A blank is missing, not wrong — and a page that divided by the whole
        class would report a lower difficulty for a question nobody reached."""
        rows = [[1, 1, 1], [1, 1, 1], [1, 1, 1], [None, None, 1]]
        result = ia.analyse(exam(3), papers(rows))
        assert result.items[0].answered == 3
        assert result.items[0].missing == 1
        assert result.items[0].share == pytest.approx(1.0)

    def test_every_question_is_worth_what_the_scheme_says(self):
        types = {"0": "mcq", "1": "mcq", "2": "match"}
        weights = {"0": 30.0, "1": 30.0, "2": 40.0}
        result = ia.analyse(exam(3, types=types, weights=weights), papers([[1] * 3]))
        assert [item.marks for item in result.items] == [30.0, 30.0, 40.0]

    def test_a_partly_right_matching_answer_counts_towards_its_difficulty(self):
        """The classical columns use the part credit the app actually paid; only
        the calibration is dichotomous. A page that used full credit for both
        would disagree with the mark the student can see."""
        exam_row = {
            "total_questions": 1,
            "question_types": {"0": "match"},
            "question_weights": {"0": 100.0, "_scheme": {"partial": True}},
            "answer_key": {"0": {"pairs": [["a", "1"], ["b", "2"], ["c", "3"]]}},
        }
        rows = [
            # three ways right — three quarters of the marks
            [{"pairs": [["a", "1"], ["b", "2"], ["c", "3"]]}],
            [{"pairs": [["a", "1"], ["b", "2"]]}],
        ]
        answers = [{"answers": {"0": row[0]}, "student_name": f"P{i}"}
                   for i, row in enumerate(rows)]
        result = ia.analyse(exam_row, answers)
        # One paper at 3 of 3 pairs, one at 2 of 3: the mean share is 5/6, and a
        # page that counted full credit only would report 1.0/2 = 0.5.
        assert result.items[0].share == pytest.approx(5 / 6, abs=1e-3)
        assert result.items[0].full == 1, "only one paper earned the whole mark"


# ── honesty about what could not be measured ─────────────────────────────────

class TestWhatIsNotMeasured:
    """Three different reasons an item has no number, and the page has to say
    which one: no key, nobody answered, or a teacher still has to mark it."""

    def test_a_missing_key_is_not_a_question_nobody_answered(self):
        result = ia.analyse(exam(3, key={"0": "A"}), papers([[1, 0, 0]] * 4))
        assert [item.keyed for item in result.items] == [True, False, False]
        assert [item.flag for item in result.items][1:] == ["unkeyed", "unkeyed"]
        assert "unkeyed_items" in result.notes
        assert "unanswered_items" not in result.notes

    def test_an_essay_is_teacher_marked_rather_than_unkeyed(self):
        types = {"0": "mcq", "1": "essay_canvas"}
        key = {"0": "A"}
        rows = [{"answers": {"0": "A", "1": {"text": "x"}},
                 "teacher_feedback": {"scores": {"1": 80}}, "student_name": f"P{i}"}
                for i in range(4)]
        result = ia.analyse(exam(2, key=key, types=types), rows)
        essay = result.items[1]
        assert essay.objective is False
        assert essay.flag != "unkeyed"
        assert "essays_teacher_marked" in result.notes
        assert "unkeyed_items" not in result.notes

    def test_an_unmarked_essay_is_missing_rather_than_zero(self):
        types = {"0": "mcq", "1": "essay_canvas"}
        rows = [{"answers": {"0": "A", "1": {"text": "x"}}, "student_name": f"P{i}"}
                for i in range(3)]
        result = ia.analyse(exam(2, key={"0": "A"}, types=types), rows)
        assert result.items[1].answered == 0
        assert result.items[1].share == 0.0
        assert "missing_not_wrong" in result.notes

    def test_the_notes_name_the_corrections_that_were_applied(self):
        result = ia.analyse(exam(2), papers([[1, 1], [0, 1]]))
        assert "dichotomous" in result.notes
        # Item 1 was answered right by both papers, so it took the correction.
        assert [i.flag for i in result.items][1] == "extreme_easy"
        assert "extreme_items" in result.notes


# ── nothing published is a NaN or an infinity ────────────────────────────────

def test_no_number_that_reaches_a_page_is_a_nan_or_an_infinity():
    """A JSON payload with NaN in it is not valid JSON, and an infinity rendered
    on a chart stretches the axis into a single spike."""
    rows = [[1] * 8, [1] * 8, [0] * 8, [0] * 8, [1] * 4 + [0] * 4]
    result = ia.analyse(exam(8), papers(rows))
    numbers = []
    for item in result.items:
        numbers += [item.share, item.pct, item.discrimination, item.point_biserial,
                    item.measure, item.se, item.t, item.p_value, item.infit,
                    item.outfit, item.infit_z, item.outfit_z, item.pt_measure]
    for person in result.people:
        numbers += [person.measure, person.se, person.score]
    for split in result.splits:
        numbers += [split.upper, split.lower, split.se_upper, split.se_lower,
                    split.t, split.df, split.p]
    for value in numbers:
        assert value is None or math.isfinite(value), value if value is not None else ""


def test_a_scale_that_cannot_be_measured_reports_a_dash_not_a_dividend_by_zero():
    """One paper and one item: there is no spread, so reliability is not 0 — it is
    unanswerable, and `None` is how the page renders that."""
    result = ia.analyse(exam(2), papers([[1, 0]]))
    assert result.summary.alpha is None or math.isfinite(result.summary.alpha)
    assert result.summary.person_reliability is None
    assert all(math.isfinite(i.pct) for i in result.items)


def test_an_exam_with_no_questions_is_an_empty_analysis_not_a_crash():
    result = ia.analyse({"total_questions": 0}, [])
    assert result.items == ()
    assert result.people == ()
    assert result.summary.students == 0


# ── the documents ────────────────────────────────────────────────────────────

def _note_carrying():
    """An exam whose analysis raises several notes at once: an unkeyed question,
    a teacher-marked essay, and papers that left questions blank."""
    exam_row = exam(3, key={"0": "A"},
                    types={"0": "mcq", "1": "mcq", "2": "essay_canvas"})
    rows = [{"student_name": f"P{i}", "answers": {"0": "A" if i else ""}}
            for i in range(4)]
    return ia.analyse(exam_row, rows), exam_row


def _sample():
    exam_row = exam(4, types={"0": "mcq", "1": "mcq", "2": "true_false",
                              "3": "essay_canvas"})
    exam_row["title"] = "Mid Semester 1 / Fisika"
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
    return ia.analyse(exam_row, rows), exam_row


class TestTheSpreadsheet:

    def test_both_languages_produce_a_parseable_file(self):
        analysis, exam_row = _sample()
        for lang in ("id", "en"):
            parsed = list(csv.reader(io.StringIO(
                report.analysis_csv(analysis, exam_row, lang))))
            assert parsed[0][0] == report.labels(lang)["title"]
            assert any("Murid 00" in cell for row in parsed for cell in row)
            assert any(row and row[0] == report.labels(lang)["items"] for row in parsed)

    def test_the_item_table_is_the_header_plus_one_row_each(self):
        analysis, exam_row = _sample()
        parsed = list(csv.reader(io.StringIO(
            report.analysis_csv(analysis, exam_row, "en"))))
        start = next(i for i, r in enumerate(parsed) if r and r[0] == "Item statistics")
        rows = parsed[start + 1:start + 2 + len(analysis.items)]
        assert len(rows) == len(analysis.items) + 1
        assert len(rows[0]) == len(report.analysis_rows(analysis, "en")[0])

    def test_the_numbers_in_the_file_are_the_numbers_on_the_screen(self):
        analysis, exam_row = _sample()
        parsed = list(csv.reader(io.StringIO(
            report.analysis_csv(analysis, exam_row, "en"))))
        start = next(i for i, r in enumerate(parsed) if r and r[0] == "Item statistics")
        first = parsed[start + 2]
        assert float(first[5]) == round(analysis.items[0].pct, 1)
        assert float(first[7]) == round(analysis.items[0].discrimination, 3)

    def test_a_number_that_does_not_exist_is_an_empty_cell_not_a_zero(self):
        """An item nobody could be measured on has no difficulty; `0.00` in a
        spreadsheet is a measurement, and a teacher will average it."""
        analysis, exam_row = _sample()
        parsed = list(csv.reader(io.StringIO(
            report.analysis_csv(analysis, exam_row, "en"))))
        start = next(i for i, r in enumerate(parsed) if r and r[0] == "Item statistics")
        essay = parsed[start + 2 + 3]
        assert essay[9] == "", "the essay is off the logit scale"
        assert essay[15] != report.FLAG_LABELS["unkeyed"]["en"]

    def test_the_notes_travel_with_the_numbers(self):
        """The limits are half the value of the table: without them a teacher
        averages a question nobody could be measured on."""
        analysis, exam_row = _note_carrying()
        parsed = list(csv.reader(io.StringIO(
            report.analysis_csv(analysis, exam_row, "en"))))
        assert any(r and r[0] == report.labels("en")["notes"] for r in parsed), \
            "the CSV has no notes section"
        text = " ".join(cell for row in parsed for cell in row)
        for note in analysis.notes:
            assert report.NOTE_LABELS[note]["en"] in text, \
                f"the note {note} never reached the spreadsheet"

    def test_selected_options_are_counted_and_the_rest_still_listed(self):
        analysis, exam_row = _sample()
        parsed = list(csv.reader(io.StringIO(
            report.analysis_csv(analysis, exam_row, "en"))))
        start = next(i for i, r in enumerate(parsed) if r and r[0] == "Option distribution")
        options = parsed[start + 2:start + 7]
        assert len(options) == 5, "A..E, including the ones nobody chose"
        assert [o[1] for o in options] == ["A", "B", "C", "D", "E"]
        assert sum(int(o[2]) for o in options) == analysis.items[0].answered


class TestTheReport:

    def test_both_languages_produce_a_pdf(self):
        analysis, exam_row = _sample()
        for lang in ("id", "en"):
            pdf = report.analysis_pdf(analysis, exam_row, school="SMA 1",
                                      teacher="Bu Ani", lang=lang)
            assert pdf[:5] == b"%PDF-"
            assert pdf.rstrip()[-5:] == b"%%EOF"
            assert len(pdf) > 1500

    def test_an_empty_analysis_still_builds(self):
        assert report.analysis_pdf(ia.analyse({"total_questions": 0}, {}),
                                   {}).startswith(b"%PDF-")

    def test_a_filename_survives_a_title_with_punctuation(self):
        name = report.filename(
            ia.analyse({"total_questions": 0}, {}),
            {"title": "Exam / 2: Bab 3 (Fisika)", "code": "x y"}, "csv")
        assert name.endswith(".csv")
        assert " " not in name and "/" not in name and ":" not in name

    def test_every_label_the_documents_use_exists_in_both_languages(self):
        """A missing translation would surface as an Indonesian word inside an
        English report. Both tables are checked against each other here."""
        assert set(report._LABELS["id"]) == set(report._LABELS["en"])
        for table in (report.KIND_LABELS, report.FLAG_LABELS, report.NOTE_LABELS):
            for key, pair in table.items():
                assert pair.get("id") and pair.get("en"), f"{key} is half-translated"

    def test_an_essay_carries_a_label_in_both_languages(self):
        analysis, exam_row = _sample()
        assert report._pair(report.KIND_LABELS, "essay", "en") == "Essay"
        assert report._pair(report.KIND_LABELS, "essay", "id") == "Esai"
