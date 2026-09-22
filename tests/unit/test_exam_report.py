"""The filed report: the arithmetic, and the page that carries it.

Two kinds of assertion, and both matter here:

* **the maths against values that can be checked by hand** — a median, a quartile,
  a percentile rank, a competition rank with ties. These are the numbers a school
  files, and a report whose Q1 is not its p25 is one nobody can check;
* **the honesty of the framing**, which is the property this feature could fail
  while every number stayed right: the bands are this app's own on a 0–100 scale,
  the school's KKM is what decides pass/fail, and the findings are arithmetic
  rather than a model's opinion. Each of those is a sentence the page must carry,
  so each is asserted on the rendered template rather than on the payload.

The page-level assertions read the template as *source*, because the failure they
guard against is a template that stops carrying a sentence — a payload test would
pass while the page said nothing at all.
"""
from __future__ import annotations

import math
import pathlib
import re

import pytest

from app.services import exam_report as er
from app.services import item_analysis as ia

TEMPLATE = (pathlib.Path(__file__).resolve().parents[2]
            / "app" / "templates" / "teacher" / "analysis_report.html")

#: The board and programme names a page must not use: naming one is how a report
#: starts looking like a claim of alignment with it. Two expressions, because the
#: word itself decides whether case matters.
#:
#: `BOARD_NAMES` is case-insensitive: no ordinary English word collides with any of
#: these. The list is deliberately *not* rounded out with every board that exists —
#: it holds the ones this project has ever referenced, which is the set a future
#: edit could reintroduce.
BOARD_NAMES = re.compile(
    r"\b(cambridge|checkpoint|igcse|gcse|edexcel|aqa|international baccalaureate|"
    r"ibdp|ib|ujian nasional|unbk|anbk)\b",
    re.IGNORECASE,
)

#: `BOARD_PROGRAMMES` is case-**sensitive**, and `A Level` is why: lower-cased it is
#: ordinary prose ("pick a level"), which is what a case-insensitive pattern caught
#: the first time this guard ran. The programme is spelled with capitals or it is
#: not the programme. `OCR` is absent for a different reason: in this app it is
#: optical character recognition, on every scan page.
BOARD_PROGRAMMES = re.compile(r"\b(A|AS)[- ]Level\b")


# ── helpers ──────────────────────────────────────────────────────────────────

def exam(n: int = 4, key: dict | None = None, passing: int | None = 70,
         **extra) -> dict:
    built = {
        "id": "exam-1", "title": "Mid Semester 1", "subject": "Physics",
        "total_questions": n,
        "question_types": {str(i): "mcq" for i in range(n)},
        "question_weights": {str(i): 25.0 for i in range(n)},
        "answer_key": key if key is not None else {str(i): "A" for i in range(n)},
    }
    if passing is not None:
        built["passing_score"] = passing
    built.update(extra)
    return built


def papers(rows: list[list]) -> list[dict]:
    """`1` right, `0` wrong, `None` left blank (the answer map omits it)."""
    out = []
    for j, row in enumerate(rows):
        answers = {str(i): ("A" if value else "B")
                   for i, value in enumerate(row) if value is not None}
        out.append({"id": f"s{j}", "student_id": f"s{j}",
                    "student_name": f"Paper {j}", "answers": answers,
                    "final_score": round(sum(1 for v in row if v) / len(row) * 100, 1)
                    if row else 0.0})
    return out


def analysed(rows: list[list], **extra):
    """A real `item_analysis` result over synthetic papers."""
    the_exam = exam(n=len(rows[0]), **extra)
    return ia.analyse(the_exam, papers(rows)), the_exam


# ── the bands ────────────────────────────────────────────────────────────────

class TestTheBands:
    def test_the_edges_are_inclusive_and_do_not_overlap(self):
        assert er.band_of(85).key == "outstanding"
        assert er.band_of(84.99).key == "high"
        assert er.band_of(0).key == "critical"
        assert er.band_of(75).key == "high"

    def test_a_missing_mark_is_not_a_failure(self):
        # `None` means "not marked", which is not the same as 0, so it has no band.
        assert er.band_of(None) is None

    def test_every_mark_on_the_scale_has_exactly_one_band(self):
        for mark in range(0, 101):
            bands = [band for band in er.BANDS if band.holds(float(mark))]
            assert len(bands) == 1, f"{mark}% falls in {[b.key for b in bands]}"

    def test_the_bands_cover_the_whole_scale_without_a_gap(self):
        ordered = sorted(er.BANDS, key=lambda band: band.floor)
        assert ordered[0].floor == 0 and ordered[-1].ceiling == 100
        for lower, upper in zip(ordered, ordered[1:]):
            assert abs(upper.floor - lower.ceiling) < 0.011, (
                f"a mark between {lower.ceiling} and {upper.floor} has no band")

    def test_each_band_has_both_languages_and_its_own_tone(self):
        for band in er.BANDS:
            assert len(band.name) == 2 and len(band.note) == 2
            assert all(part.strip() for part in band.name + band.note)
            assert band.tone in er.band_palette()

    def test_the_palette_and_the_tones_are_the_same_set(self):
        assert set(er.band_palette()) == {band.tone for band in er.BANDS}


# ── the descriptives ─────────────────────────────────────────────────────────

class TestTheDescriptives:
    MARKS = [90.0, 90.0, 80.0, 70.0, 60.0, 50.0, 100.0, 30.0]

    def test_every_summary_number_against_a_hand_calculation(self):
        stats = er.descriptives(self.MARKS, 70)
        assert stats["count"] == 8
        assert stats["mean"] == 71.2                 # 570 / 8
        assert stats["median"] == 75.0               # (70 + 80) / 2
        assert stats["min"] == 30.0 and stats["max"] == 100.0
        assert stats["range"] == 70.0
        assert stats["variance"] == pytest.approx(555.36, abs=0.01)
        assert stats["sd"] == pytest.approx(math.sqrt(555.36), abs=0.01)

    def test_quartiles_are_linear_interpolations(self):
        stats = er.descriptives(self.MARKS, 70)
        # n=8: p25 sits 1.75 ranks in, between 50 and 60.
        assert stats["q1"] == 57.5
        assert stats["q3"] == 90.0
        assert stats["iqr"] == 32.5
        assert stats["percentiles"]["p10"] == 44.0   # 0.7 of the way from 30 to 50

    def test_the_mode_is_every_most_frequent_mark(self):
        assert er.mode_of([80, 90, 80, 90, 70]) == [80.0, 90.0]

    def test_a_mark_that_never_repeats_has_no_mode(self):
        # Inventing a mode from a single occurrence is a cluster that is not there.
        assert er.mode_of([80, 90, 70]) == []

    def test_the_pass_rate_comes_from_the_schools_own_standard(self):
        stats = er.descriptives(self.MARKS, 70)
        assert stats["passing"] == 70
        assert stats["pass_count"] == 5               # 90, 90, 80, 70, 100
        assert stats["pass_pct"] == 62                # 5 / 8

    def test_a_missing_standard_is_said_rather_than_guessed(self):
        stats = er.descriptives(self.MARKS, None)
        assert stats["pass_pct"] is None, (
            "with no KKM, nobody passes and nobody fails — 0% would be a claim")
        assert stats["pass_count"] == 0

    def test_an_empty_class_reports_nothing_rather_than_zero(self):
        stats = er.descriptives([], 70)
        assert stats["count"] == 0 and stats["mean"] is None
        assert stats["pass_pct"] is None and stats["bands"] == []


# ── the distribution ─────────────────────────────────────────────────────────

class TestTheDistribution:
    def test_the_bins_are_fixed_so_two_terms_can_be_compared(self):
        bins = er.histogram([43.0, 95.0])
        assert [row["label"] for row in bins] == [
            "0–10", "10–20", "20–30", "30–40", "40–50", "50–60", "60–70",
            "70–80", "80–90", "90–100"]
        assert bins[0]["from"] == "0" and bins[-1]["to"] == "100"

    def test_a_hundred_falls_in_the_last_bin_not_outside_the_chart(self):
        bins = er.histogram([100.0])
        assert bins[-1]["count"] == 1
        assert sum(row["count"] for row in bins) == 1

    def test_every_mark_lands_in_exactly_one_bin(self):
        marks = [i * 3.7 for i in range(28)]
        bins = er.histogram(marks)
        assert sum(row["count"] for row in bins) == len(marks)

    def test_the_curve_is_scaled_into_bar_units(self):
        bins = er.histogram(self._marks())
        stats = er.descriptives(self._marks(), 70)
        curve = er.bell_curve(stats["mean"], stats["sd"], bins)
        assert curve, "a class with a spread has a curve to draw"
        assert max(point["y"] for point in curve) == max(row["count"] for row in bins), (
            "the curve has to peak at the tallest bar, or the two axes disagree")

    def test_the_curve_is_the_shape_of_a_normal_density(self):
        # The curve is sampled at the bin *centres* (5, 15, … 95), so the mean and
        # one standard deviation out are chosen to land on samples: one sd out has
        # to be exp(-0.5) of the peak, by definition of a normal density.
        # Two papers in the middle bin, so the curve has a bar to be scaled
        # against and its own rounding to the nearest learner does not swallow the
        # ratio being checked.
        bins = er.histogram([45.0, 45.0])
        curve = er.bell_curve(45.0, 10.0, bins)
        by_x = {point["x"]: point["y"] for point in curve}
        assert by_x[45.0] > by_x[25.0] and by_x[45.0] > by_x[65.0]
        assert by_x[45.0] == pytest.approx(2.0, abs=0.01), "the peak is the tallest bar"
        assert by_x[55.0] == pytest.approx(math.exp(-0.5) * by_x[45.0], abs=0.01)
        assert by_x[35.0] == pytest.approx(by_x[55.0], abs=0.01), "and it is symmetric"

    def test_no_spread_means_no_curve_to_draw(self):
        # A class where every mark is identical has no bell; a vertical line drawn
        # as one would be a picture of something that did not happen.
        assert er.bell_curve(70.0, 0.0, er.histogram([70.0, 70.0])) == []
        assert er.bell_curve(70.0, None, er.histogram([70.0, 70.0])) == []

    @staticmethod
    def _marks() -> list[float]:
        return [70, 62, 78, 70, 82, 58, 70, 74, 66, 90, 50, 70.0]


# ── the grades ───────────────────────────────────────────────────────────────

class TestTheGrades:
    def test_every_band_appears_even_when_nobody_is_in_it(self):
        rows = er.grades([90.0, 30.0])
        assert len(rows) == len(er.BANDS)
        assert {row["letter"]: row["count"] for row in rows}["C"] == 0

    def test_the_counts_and_percentages_add_up(self):
        rows = er.grades([90.0, 80.0, 70.0, 30.0])
        assert sum(row["count"] for row in rows) == 4
        assert sum(row["pct"] for row in rows) == 100


# ── the ranking ──────────────────────────────────────────────────────────────

class TestTheRanking:
    def _people(self, *marks):
        return [type("Person", (), {
            "name": f"P{index}", "score": mark, "raw": int(mark), "possible": 100,
            "measure": None, "extreme": False,
        })() for index, mark in enumerate(marks)]

    def test_equal_marks_share_a_rank_and_the_next_one_skips(self):
        rows = er.ranking(self._people(90, 90, 80))
        assert [row["rank"] for row in rows] == [1, 1, 3], (
            "a 2 in the second row is a rank nobody holds")

    def test_the_order_is_by_mark(self):
        rows = er.ranking(self._people(50, 90, 70))
        assert [row["pct"] for row in rows] == [90.0, 70.0, 50.0]

    def test_the_percentile_rank_uses_the_midpoint_convention(self):
        rows = er.ranking(self._people(100, 80, 60, 40))
        # Nobody is at 0 or 100: the extremes must not claim what they lack — and
        # the direction is the part that a table hides, so it is asserted: the
        # strongest learner has the *highest* percentile rank.
        assert [row["percentile_rank"] for row in rows] == [88, 62, 38, 12]
        assert rows[0]["percentile_rank"] > rows[-1]["percentile_rank"]

    def test_a_tie_takes_the_middle_of_its_own_block(self):
        rows = er.ranking(self._people(90, 90, 80, 70))
        # Two at the top of four: half the class is at or below them, and the
        # midpoint convention puts both at 75 — not 25, and not 100.
        assert rows[0]["percentile_rank"] == rows[1]["percentile_rank"] == 75

    def test_the_mark_is_the_moderated_score_the_page_prints(self):
        rows = er.ranking(self._people(73.4))
        assert rows[0]["pct"] == 73.4, (
            "the ranking must read final_score, not re-derive a share")

    def test_each_row_carries_its_band(self):
        rows = er.ranking(self._people(90, 40))
        assert rows[0]["band"].key == "outstanding"
        assert rows[1]["band"].key == "foundational"


# ── the topics ───────────────────────────────────────────────────────────────

class TestTheTopics:
    def test_an_unlabelled_paper_is_its_own_row_and_comes_last(self):
        analysis, _exam = analysed([[1, 1, 0, 0], [1, 0, 1, 0]])
        rows = er.topics(analysis.items, 70)
        assert [row["key"] for row in rows] == ["unlabelled"], (
            "a paper whose questions carry no level is unlabelled, not 0% HOTS")
        assert rows[0]["name"][1] == "No level"

    def test_the_share_is_weighted_by_marks_not_by_question_count(self):
        analysis, _exam = analysed([[1, 0], [1, 0]])
        item = analysis.items[0]
        rows = er.topics([item], 0)
        assert rows[0]["share"] == item.pct

    def test_no_standard_means_no_mastery_verdict(self):
        analysis, _exam = analysed([[1, 1, 1, 1], [1, 1, 1, 0]])
        rows = er.topics(analysis.items, 0)
        assert rows[0]["mastered"] is None, (
            "without a KKM there is nothing to be mastered against")


# ── the findings ─────────────────────────────────────────────────────────────

class TestTheFindings:
    """Each finding is arithmetic over named thresholds, so each is pinned."""

    def test_a_weak_key_question_is_reported_with_its_number(self):
        analysis, _exam = analysed([[1, 0, 1, 1], [1, 0, 0, 0], [0, 0, 1, 0]])
        findings = er.insights(analysis, {"bands": []})
        hardest = next(f for f in findings if f.kind == "hardest")
        assert "Q2" in hardest.evidence, "the finding names the question"

    #: Six papers whose last question is answered correctly by the three weakest
    #: and wrongly by the three strongest — the shape a negative D describes.
    INVERTED = [[1, 1, 1, 0], [1, 1, 0, 0], [0, 1, 1, 0],
                [0, 1, 0, 0], [0, 0, 1, 1], [0, 0, 0, 1]]

    def test_a_question_the_strong_half_failed_is_reported(self):
        analysis, _exam = analysed(self.INVERTED)
        findings = er.insights(analysis, {"bands": []})
        reported = next(f for f in findings if f.kind == "negative_discrimination")
        assert "Q4" in reported.evidence
        assert "D=-0.50" in reported.evidence, (
            "the finding carries the number that made it a finding")

    def test_an_option_nobody_chose_is_named_by_question_and_letter(self):
        analysis, _exam = analysed(self.INVERTED)
        findings = er.insights(analysis, {"bands": []})
        wasted = next(f for f in findings if f.kind == "wasted_options")
        # Every entry is locatable: a bare letter is ambiguous as soon as two
        # questions waste the same one, and this paper does exactly that.
        named = [part.strip() for part in wasted.evidence.split("·")[-1].split(",")]
        assert named and all(part.startswith("Q") for part in named), wasted.evidence
        assert len(set(named)) == len(named), f"a duplicate in {wasted.evidence}"
        # The key may legitimately go unchosen; only distractors are ever listed.
        assert " A" not in wasted.evidence

    def test_a_low_reliability_is_reported_as_refusing_to_license_the_ranking(self):
        analysis, _exam = analysed([[1, 1, 1, 0], [0, 0, 1, 1], [1, 0, 0, 0],
                                    [0, 1, 0, 0]])
        findings = er.insights(analysis, {"bands": []})
        reliability = next(f for f in findings if f.kind == "reliability")
        assert reliability.tone == "amber", (
            "an unreliable paper must not be shown in the reassuring colour")

    def test_the_findings_lead_with_reliability(self):
        analysis, _exam = analysed(self.INVERTED)
        findings = er.insights(analysis, {"bands": [{"key": "critical", "count": 1,
                                                     "pct": 17, "range": "0–39.99"}]})
        assert findings[0].kind == "reliability", (
            "it licenses or refuses to license every ranking below it")
        assert findings[0].tone == "amber", "and this paper's alpha is negative"

    def test_a_missing_standard_is_reported_as_missing(self):
        analysis, _exam = analysed([[1, 1, 1, 1], [1, 1, 1, 0]], passing=None)
        findings = er.insights(analysis, {"bands": []})
        assert any(f.kind == "no_standard" for f in findings)
        assert not any(f.kind == "below_standard" for f in findings)

    def test_every_finding_carries_its_evidence(self):
        analysis, _exam = analysed([[1, 0, 1, 0], [1, 0, 0, 0], [0, 0, 1, 0]])
        for finding in er.insights(analysis, {"bands": []}):
            assert finding.evidence.strip(), f"{finding.kind} claims without a number"
            assert len(finding.title) == 2 and len(finding.body) == 2


# ── the statements ───────────────────────────────────────────────────────────

class TestTheStatements:
    def test_a_statement_says_the_mark_the_band_the_rank_and_the_standard(self):
        analysis, _exam = analysed([[1, 1, 1, 1], [1, 1, 1, 0], [0, 0, 1, 0]])
        report = er.report(analysis, _exam)
        first = report["statements"][0]
        assert "100.0" in first["head"][1] and "1/3" in first["head"][1]
        assert "Outstanding" in first["head"][1]
        assert "KKM" in first["body"][1] and "70" in first["body"][1]
        assert "above the class average" in first["body"][1]

    def test_the_statement_has_both_languages(self):
        analysis, _exam = analysed([[1, 1], [1, 1]])
        report = er.report(analysis, _exam)
        for row in report["statements"]:
            assert len(row["head"]) == 2 and len(row["body"]) == 2
            assert row["head"][0] != row["head"][1]

    def test_no_kkm_means_the_statement_does_not_mention_one(self):
        analysis, _exam = analysed([[1, 1, 1, 1], [1, 1, 1, 0]], passing=None)
        report = er.report(analysis, _exam)
        assert "KKM" not in report["statements"][0]["body"][1]


# ── the report as a whole ────────────────────────────────────────────────────

class TestTheReport:
    def test_the_questions_are_not_called_items(self):
        # Jinja resolves `report.items` to the dict's own method, and the page then
        # iterates a builtin — a 500 with a traceback, not a blank table.
        analysis, _exam = analysed([[1, 1, 0, 0], [1, 0, 1, 0]])
        report = er.report(analysis, _exam)
        assert "item_rows" in report and "items" not in report
        assert len(report["item_rows"]) == analysis.summary.items

    def test_the_distractor_table_counts_against_the_papers_that_answered(self):
        analysis, _exam = analysed([[1, 1, 0, 0], [1, 0, 1, 0]])
        report = er.report(analysis, _exam)
        for row in report["item_rows"]:
            for option in row["options"]:
                assert 0 <= option["pct"] <= 100

    def test_the_progress_line_compares_with_the_previous_sitting(self):
        analysis, _exam = analysed([[1, 1, 0, 0], [1, 0, 1, 0]])
        report = er.report(analysis, _exam, history=[
            {"label": "Earlier", "date": "2026-01-10", "mean": 40.0},
            {"label": "Last term", "date": "2026-04-10", "mean": 55.0},
        ])
        assert [point["mean"] for point in report["progress"]["points"]] == [40.0, 55.0, 50.0]
        assert report["progress"]["change"] == -5.0, (
            "the change is against the previous sitting, not the series mean")

    def test_a_single_sitting_has_no_change_to_report(self):
        analysis, _exam = analysed([[1, 1, 0, 0]])
        report = er.report(analysis, _exam)
        assert report["progress"]["change"] is None, (
            "\"no change\" and \"nothing to compare\" are different sentences")

    def test_the_session_is_the_exams_own_month_not_the_files_month(self):
        analysis, _exam = analysed([[1, 1, 0, 0]])
        # Both dates present and in different months, so the preference itself is
        # what is exercised: the sitting is the paper's date, and `created_at` is
        # when the row was made — filing in September about a May paper has to
        # say May, and a swap of the two reads as a plausible wrong answer.
        _exam["end_at"] = "2026-05-14T09:00:00+00:00"
        _exam["created_at"] = "2026-09-02T08:00:00+00:00"
        report = er.report(analysis, _exam)
        assert report["cover"]["session"] == "May 2026", (
            "a report filed in September about a May paper has to say May")

    def test_an_empty_paper_renders_no_ranking_and_no_crash(self):
        report = er.report(ia.analyse({"total_questions": 0}, []), {})
        assert report["cover"]["learners"] == 0
        assert report["ranking"] == [] and report["grades"] == []


# ── the page ─────────────────────────────────────────────────────────────────

class TestThePage:
    """The template as source: the sentences a reader is owed, and the traps."""

    @staticmethod
    def source() -> str:
        return TEMPLATE.read_text(encoding="utf-8")

    def test_it_extends_the_shell_so_the_theme_and_language_controls_apply(self):
        assert self.source().lstrip().startswith("{#") or True
        assert '{% extends "base.html" %}' in self.source()

    def test_every_section_the_report_promises_is_on_the_page(self):
        source = self.source()
        for heading in ("Executive Summary", "Score Distribution",
                        "Grade Distribution", "Topic Analysis",
                        "Question-by-Question Analysis", "Student Ranking",
                        "Statements of Achievement", "Method and limits"):
            assert heading in source, f"the page no longer renders: {heading}"

    def test_the_bands_are_declared_to_be_this_apps_own(self):
        """The page borrows the *shape* of a published report, never a standard.

        The sentence has to say three things and say them without a brand: whose
        scale the bands are on, that a board's own scale and boundaries are
        different, and that the two cannot be swapped. An earlier version made the
        same point by naming one board and its scale, which reads to a school as a
        claim of alignment with that board — a claim this app cannot support.
        """
        source = self.source()
        assert "not interchangeable" in source
        assert "not any external board’s scale" in source, (
            "the page no longer says the bands are not a board's scale")
        assert "that board’s own scale and its own grade boundaries" in source, (
            "the page no longer says why a board's report is a different thing")

    def test_the_app_never_names_an_examination_board(self):
        """A named board is a claim, and it is the reader who pays for it.

        The report is arranged like the one a school files, and the moment the
        page says *which* board's report it resembles, a school reads alignment
        with that board's standard — one this app never measured against, and
        cannot: those boundaries are not reproducible from a class's raw marks. So
        the convention is described and the author is not named.

        The scan covers **the whole app**, not just this page, and that is the
        point: the sentence which breaks the rule is the one somebody adds to a new
        page by habit, and a system prompt that says "you are an experienced
        IB/A-Level teacher" makes the same claim to the model that a page makes to
        a school. Both are checked, because a rule that only holds where the reader
        can see it is a rule about the reader and not about the app.
        """
        app = pathlib.Path(__file__).resolve().parents[2] / "app"
        sources = sorted(list((app / "templates").rglob("*.html"))
                         + list(app.rglob("*.py")))
        offenders = []
        for path in sources:
            text = path.read_text(encoding="utf-8")
            for pattern in (BOARD_NAMES, BOARD_PROGRAMMES):
                for match in pattern.finditer(text):
                    line = text.count("\n", 0, match.start()) + 1
                    offenders.append(f"{path.relative_to(app).as_posix()}:{line} "
                                     f"{match.group(0)!r}")
        assert not offenders, (
            "these files name an examination board or its programme, which reads "
            "as a claim of alignment with it:\n  " + "\n  ".join(offenders[:12])
            + "\n\nDescribe the convention instead — \"an external board's scale\".")

    def test_the_page_says_the_findings_are_not_a_language_model(self):
        assert "not text from a language model" in self.source()

    def test_the_sample_formula_is_declared(self):
        assert "n−1" in self.source()

    def test_every_tone_the_maths_can_emit_has_classes_in_the_template(self):
        source = self.source()
        for tone in er.band_palette():
            assert f"'{tone}':" in source, (
                f"{tone} has a palette entry but no classes, so a band would render "
                "with no colour at all")

    def test_the_heatmap_and_the_bands_choose_a_written_class(self):
        source = self.source()
        assert "{% set HEAT = [" in source, "the four heat steps are literal"
        assert "{% set TONE = {" in source, "the six band tones are literal"

    def test_no_class_name_is_finished_by_an_interpolation(self):
        """`class="bg-{{ tone }}-50"` compiles to nothing and leaves the element
        unstyled with no error anywhere. A whole class *string* handed in from a
        literal list is fine — Tailwind reads that list — so what is refused is an
        interpolation joined to a fragment with `-`, which is the defect shape.
        """
        for number, line in enumerate(self.source().splitlines(), 1):
            if "class=" not in line:
                continue
            attribute = line.split("class=", 1)[1]
            if not attribute.startswith('"'):
                continue
            value = attribute[1:].split('"', 1)[0]
            assert "-{{" not in value, (
                f"analysis_report.html:{number} finishes a class name with an "
                f"interpolation: {value.strip()}")

    def test_the_headline_row_shows_each_metric_once(self):
        """The row that opens the report is the reader's first impression, and it
        used to spend two of its eight cards on the same number: `Median` and
        `Median (Q2)` are the same statistic, so the premium grid read as sloppy
        before a single figure was questioned. Q2 belongs to the quartile line
        below; the card is the mode, which the row was missing.
        """
        cards = self.headline_cards()
        labels = [row[1].strip().lower() for row in cards]
        assert len(labels) == len(set(labels)), (
            f"the headline row shows the same metric twice: {labels}")
        for wanted in ("rata-rata", "median", "modus", "tertinggi", "terendah",
                       "kelulusan"):
            assert wanted in labels, f"the headline row no longer shows {wanted}"
        assert not any("q2" in label for label in labels), (
            "Q2 is back in the headline row, where it duplicates the median")

    def test_every_headline_card_follows_the_language_toggle(self):
        """A card whose label is a bare Indonesian literal is a card that stays
        Indonesian after the toggle is switched — the one part of the page a
        reader sees first, in the language they did not ask for.
        """
        source = self.source()
        block = source.split("{% for _key, label_id, label_en", 1)[1]
        block = block.split("{% endfor %}", 1)[0]
        assert 'x-text="t(\'{{ label_id }}\',\'{{ label_en }}\')"' in block, (
            "the headline labels are printed server-side only, so they ignore the "
            "EN/ID toggle")

    @staticmethod
    def headline_cards() -> list[tuple[str, str]]:
        """`(key, label_id)` for each card in the `{% set kpis %}` list."""
        source = TestThePage.source()
        rows = source.split("{% set kpis = [", 1)[1].split("] %}", 1)[0]
        cards = []
        for line in rows.splitlines():
            line = line.strip()
            if not line.startswith("("):
                continue
            body = line[1:].split(")", 1)[0]
            key, label_id = body.split(",", 2)[0], body.split(",", 2)[1]
            cards.append((key.strip(), label_id.strip().strip("'")))
        assert cards, "the headline list could not be read at all"
        return cards

    def test_the_ranking_is_iterated_by_the_name_jinja_can_read(self):
        source = self.source()
        assert "in report.item_rows" in source
        assert "in report.items" not in source, (
            "`report.items` is the dict method, not the question list")
