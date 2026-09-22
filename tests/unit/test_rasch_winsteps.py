"""The measurement model, held to the formulas Winsteps publishes.

The analysis page reports a logit scale, infit and outfit, a separation and a
reliability. Every one of those is a *definition* before it is a number, and a
definition can be checked: this file checks each against the source it claims —
Wright & Masters' Rating Scale Analysis for the fit statistics, Winsteps' own
documentation for the standard errors and the separation block, and the model
itself for whether a fit statistic is standardised at all.

Three of these tests are the reason the file exists:

* **the published Table 28.3 example.** Winsteps prints one worked summary block
  with its population SD and both RMSEs. Feeding those numbers to
  `item_analysis.separation` has to reproduce the separation, the reliability and
  the strata it prints beside them, or the arithmetic is not Winsteps'.
* **standardisation under the model.** Data generated *from* the Rasch model must
  give a fit ZSTD with mean 0 and SD 1. With the observation count as the degrees
  of freedom — which is what this module used before — the spread comes out at
  1.4x, so a misfit flag at 2.0 fires on data that fit.
* **specific objectivity.** The defining claim of Rasch measurement is that an
  item's difficulty does not depend on who sat the paper. Measured on two halves
  of one class split by ability: the logit difficulties agree, the classical
  p-values do not.
"""
from __future__ import annotations

import io
import math
import random

import pytest
from openpyxl import load_workbook

from app.services import analysis_frameworks as af
from app.services import analysis_report, item_analysis

#: The reading the separation block belongs to. Every route passes a framework
#: explicitly (`test_every_route_that_builds_a_document_hands_over_the_framework`),
#: so a test that asks a document for its Rasch block has to name it too — the
#: default is CTT, which publishes no separation and no logit scale.
_RASCH = af.resolve("rasch")

#: Winsteps Table 28.3's worked example (34 non-extreme kids, 18 items).
WINSTEP_POP_SD = 1.97
WINSTEP_RMSE_REAL = 1.18
WINSTEP_RMSE_MODEL = 1.01


def _simulated_item(seed: int, persons: int = 400, items: int = 30):
    """One item's fit on responses generated *from* the model, with known
    parameters, so no calibration stands between the model and the statistic."""
    rng = random.Random(seed)
    betas = [rng.gauss(0, 1) for _ in range(items)]
    betas = [value - sum(betas) / items for value in betas]
    thetas: list[float | None] = [rng.gauss(0, 1) for _ in range(persons)]
    rows = [[1.0 if rng.random() < item_analysis._logistic(theta - beta) else 0.0
             for beta in betas] for theta in thetas]
    return item_analysis._fit(rows, betas, thetas, 0), persons, rows


def _rasch_class(items: int = 30, students: int = 240, seed: int = 5150):
    """An exam and a class drawn from the Rasch model itself, difficulties spread
    evenly — the fixture specific objectivity is a claim about."""
    rng = random.Random(seed)
    difficulty = [-1.5 + 3.0 * index / (items - 1) for index in range(items)]
    exam = {
        "title": "Ujian Rasch murni",
        "total_questions": items,
        "question_types": {str(i): "mcq" for i in range(items)},
        "answer_key": {str(i): "A" for i in range(items)},
    }
    wrong = "BCDE"
    submissions = []
    for s in range(students):
        ability = rng.gauss(0, 1)
        answers = {}
        for index, beta in enumerate(difficulty):
            chance = item_analysis._logistic(ability - beta)
            answers[str(index)] = ("A" if rng.random() < chance
                                   else rng.choice(wrong))
        submissions.append({"student_id": f"r{s:03d}", "student_name": f"Murid {s:03d}",
                            "answers": answers, "status": "graded"})
    return exam, submissions


def _class(items: int = 30, students: int = 200, seed: int = 20260920):
    """An exam and a class whose abilities and difficulties are both spread."""
    rng = random.Random(seed)
    difficulty = ([0.85] * (items // 3) + [0.5] * (items // 3)
                  + [0.2] * (items - 2 * (items // 3)))
    exam = {
        "title": "Ujian Rasch",
        "total_questions": items,
        "question_types": {str(i): "mcq" for i in range(items)},
        "answer_key": {str(i): "A" for i in range(items)},
    }
    submissions = []
    for s in range(students):
        ability = rng.gauss(0, 1)
        answers = {}
        for i in range(items):
            p = min(0.97, max(0.03, difficulty[i] + 0.15 * ability))
            answers[str(i)] = "A" if rng.random() < p else rng.choice("BCDE")
        submissions.append({"student_id": f"s{s:03d}", "student_name": f"Murid {s:03d}",
                            "answers": answers, "status": "graded"})
    return exam, submissions


# ── the separation block, against Winsteps' own worked example ───────────────

class TestTheSeparationBlock:
    def test_the_published_example_is_reproduced(self):
        """Winsteps' Table 28.3: P.SD 1.97, RMSE 1.18 REAL and 1.01 MODEL.

        Its printed block reads

            REAL  RMSE 1.18  TRUE SD 1.58  SEPARATION 1.34  KID RELIABILITY .64
            MODEL RMSE 1.01  TRUE SD 1.69  SEPARATION 1.67  KID RELIABILITY .74

        so every one of those four numbers per row is an assertion here, and the
        strata come from `(4G + 1)/3` on the same separations.
        """
        # 34 measures with a population SD of 1.97, and constant errors whose
        # root-mean-squares are the two RMSEs — the summary is computed from those
        # two facts in Winsteps, so it must be here as well.
        n = 34
        spread = [index - (n - 1) / 2 for index in range(n)]
        scale = WINSTEP_POP_SD / (sum(value * value for value in spread) / n) ** 0.5
        measures = [value * scale for value in spread]
        assert item_analysis._sd(measures, ddof=0) == pytest.approx(WINSTEP_POP_SD,
                                                                   abs=1e-9)
        block = item_analysis.separation(
            measures,
            [WINSTEP_RMSE_MODEL] * n,
            [WINSTEP_RMSE_REAL] * n,
        )
        assert block.true_sd_model == pytest.approx(1.69, abs=0.005)
        assert block.separation_model == pytest.approx(1.67, abs=0.005)
        assert block.reliability_model == pytest.approx(0.74, abs=0.005)
        # Winsteps prints 2.56; the block rounds the separation to 2 places and
        # derives strata from the unrounded ratio, so 2.57 is the same answer.
        assert block.strata_model == pytest.approx(2.57, abs=0.011)
        assert block.true_sd_real == pytest.approx(1.58, abs=0.005)
        assert block.separation_real == pytest.approx(1.34, abs=0.005)
        assert block.reliability_real == pytest.approx(0.64, abs=0.005)
        assert block.strata_real == pytest.approx(2.12, abs=0.011)

    def test_reliability_and_strata_are_defined_by_the_separation(self):
        """The two derived numbers, straight from the definitions: reliability is
        the separation squared over one plus the separation squared, and strata is
        `(4G + 1)/3` — the count of statistically distinct levels."""
        measures = [index * 0.5 for index in range(20)]
        block = item_analysis.separation(measures, [0.4] * 20, [0.6] * 20)
        for suffix in ("model", "real"):
            ratio = getattr(block, f"separation_{suffix}")
            assert getattr(block, f"reliability_{suffix}") == pytest.approx(
                ratio * ratio / (1.0 + ratio * ratio))
            assert getattr(block, f"strata_{suffix}") == pytest.approx(
                (4.0 * ratio + 1.0) / 3.0)

    def test_true_sd_is_the_observed_spread_with_the_error_taken_out(self):
        """`TRUE SD` is not a second opinion about the spread: it is the observed
        population SD minus the measurement error's variance, which is why a
        bigger error can only make it smaller."""
        measures = [index * 0.5 for index in range(20)]
        observed = item_analysis._sd(measures, ddof=0)
        quiet = item_analysis.separation(measures, [0.1] * 20, [0.1] * 20)
        noisy = item_analysis.separation(measures, [0.9] * 20, [0.9] * 20)
        assert quiet.true_sd_model == pytest.approx(
            math.sqrt(observed ** 2 - 0.1 ** 2))
        assert noisy.true_sd_model < quiet.true_sd_model
        assert noisy.reliability_model < quiet.reliability_model

    def test_a_class_with_no_measurement_error_has_no_separation(self):
        """Not "infinite reliability": there is no error to divide by, so the
        ratio is unanswerable and every field is empty rather than 1.3e4."""
        block = item_analysis.separation([0.0] * 20, [0.0] * 20, [0.0] * 20)
        assert block.separation_model is None
        assert block.reliability_model is None
        assert block.strata_model is None


# ── the scale, the fit, and the standard errors ──────────────────────────────

class TestTheModel:
    def test_the_scale_is_anchored_on_the_item_mean(self):
        """UCON: the origin is a choice, and Winsteps puts the item mean on it.

        Without the shift the calibration stops wherever the iteration left it —
        measured at -0.028 logits on this fixture — and the item table's
        `t = b/SE` is then a test against a question that is not the average one.

        The tolerance is a thousandth rather than zero because the *published*
        measure is rounded to three places, so thirty rounded items average within
        half a thousandth of the anchor; the unanchored mean is finer than that by
        a factor of thirty.
        """
        exam, submissions = _class()
        analysis = item_analysis.analyse(exam, submissions)
        betas = [item.measure for item in analysis.items if item.measure is not None]
        assert betas, "nothing was calibrated"
        assert sum(betas) / len(betas) == pytest.approx(0.0, abs=1e-3)

    def test_the_person_mean_is_left_free(self):
        """Anchoring the items does *not* centre the students: their mean is the
        class's ability in logits, which is a fact about the class."""
        exam, submissions = _class()
        analysis = item_analysis.analyse(exam, submissions)
        thetas = [p.measure for p in analysis.people if p.measure is not None]
        assert abs(sum(thetas) / len(thetas)) > 1e-6

    def test_infit_and_outfit_are_the_two_published_definitions(self):
        """Built from three observations whose arithmetic can be done by hand:

            outfit = sum(z²) / count        infit = sum(w·z²) / sum(w)

        The pair differs only in that weight, which is the whole point of having
        both: one is a plain mean, the other weights an observation by the
        information it carried.
        """
        observations = [(4.0, 0.25, 0.5), (1.0, 0.20, 0.3), (0.0, 0.05, 0.1)]
        fit = item_analysis._fit_of(observations)
        assert fit.outfit == pytest.approx((4.0 + 1.0 + 0.0) / 3)
        info = 0.25 + 0.20 + 0.05
        assert fit.infit == pytest.approx((4.0 * 0.25 + 1.0 * 0.20) / info)

    def test_the_fit_degrees_of_freedom_come_from_the_model_not_the_count(self):
        """`d.f. = 2/q²`, Winsteps' rule, where q² is the variance of the mean
        square itself. The count of responses is *not* that number: measured on
        this fixture's forty-question paper, 200 responses buy 118 degrees of
        freedom, and the difference changes a ZSTD's sign of significance.
        """
        observations = [(4.0, 0.25, 0.5), (1.0, 0.20, 0.3), (0.0, 0.05, 0.1)]
        fit = item_analysis._fit_of(observations)
        variance = (0.5 + 0.3 + 0.1) / 3 ** 2
        assert fit.outfit_df == pytest.approx(2.0 / variance)
        assert fit.outfit_df != pytest.approx(3.0)
        weighted = (0.25 ** 2 * 0.5 + 0.20 ** 2 * 0.3 + 0.05 ** 2 * 0.1)
        assert fit.infit_df == pytest.approx(2.0 / (weighted / (0.25 + 0.20 + 0.05) ** 2))

    def test_the_transform_is_wilson_hilferty(self):
        """The documented worked example for the transform itself: a mean square
        of .975 with an effective d.f. of 20 is reported as a normal deviate of
        .03 (Winsteps' `WHEXACT` page), and this is that arithmetic."""
        assert item_analysis._zstd(0.975, 2.0 / 20.0) == pytest.approx(0.026, abs=0.002)

    def test_a_divided_fit_is_less_strident_than_the_count_rule(self):
        """The same mean square, two rules, and the side the correction falls on.

        The transform is monotone in the degrees of freedom for a fixed mean square:
        more of them makes a given departure look more significant. So because a
        well-targeted test has *fewer* effective d.f. than responses, the model's
        ZSTD is the smaller — the count rule is the strident one, which is what it
        was doing to the flags.
        """
        fit, persons, _rows = _simulated_item(seed=11)
        assert fit.outfit_df < persons, (
            f"the model d.f. is {fit.outfit_df:.0f} against {persons} responses")
        count_rule = item_analysis._zstd(fit.outfit, 2.0 / persons)
        assert abs(fit.outfit_z) < abs(count_rule), (
            f"the model ZSTD {fit.outfit_z:.2f} is sterner than the count rule's "
            f"{count_rule:.2f}")

    def test_the_real_error_is_the_model_error_inflated_by_its_infit(self):
        """Winsteps: `Real S.E. = Model S.E. * Maximum [1.0, sqrt(INFIT MNSQ)]`.
        An item that overfits is not rewarded with a smaller error."""
        assert item_analysis._real_se(0.30, 4.0) == pytest.approx(0.60)
        assert item_analysis._real_se(0.30, 1.0) == pytest.approx(0.30)
        assert item_analysis._real_se(0.30, 0.25) == pytest.approx(0.30)
        assert item_analysis._real_se(0.30, None) == pytest.approx(0.30)

    def test_the_published_measures_reproduce_the_observed_scores(self):
        """The check that the table *is* the model that was fitted.

        A calibration claims that with these difficulties and these abilities the
        model predicts the marks that were actually made: the expected score of an
        item is the sum of `P(theta_j - b_i)` over the papers that answered it, and
        it has to equal the item's raw score. Measured at a couple of hundredths
        for both items and people, so a table whose numbers came from a *different*
        model — a slope, a wrong residual, a shift applied to one side only — can
        be told apart from one that came from this one. That distinction is the
        whole value of a logit table, and nothing else in the suite checks it.
        """
        exam, submissions = _class()
        analysis = item_analysis.analyse(exam, submissions)
        rows = item_analysis._responses(exam, submissions)
        abilities = {person.name: person.measure for person in analysis.people
                     if person.measure is not None and not person.extreme}
        difficulties = {item.index: item.measure for item in analysis.items
                        if item.measure is not None}
        assert len(abilities) > 100 and len(difficulties) > 20

        item_gap = 0.0
        checked = 0
        for item in analysis.items:
            if item.measure is None or item.full in (0, item.answered):
                continue
            pairs = [row for row in rows
                     if row.name in abilities
                     and row.shares.get(item.index) is not None]
            expected = sum(item_analysis._logistic(abilities[row.name] - item.measure)
                           for row in pairs)
            observed = sum(row.shares[item.index] for row in pairs)
            item_gap = max(item_gap, abs(expected - observed))
            checked += 1
        assert checked >= 20
        assert item_gap < 0.25, (
            f"an item's expected score is out by {item_gap:.2f} marks, so the "
            f"published measures are not the model that fits this data")

        person_gap = 0.0
        people_checked = 0
        for row in rows:
            if row.name not in abilities:
                continue
            seen = [index for index in difficulties
                    if row.shares.get(index) is not None]
            expected = sum(item_analysis._logistic(abilities[row.name]
                                                   - difficulties[index])
                           for index in seen)
            observed = sum(row.shares[index] for index in seen)
            person_gap = max(person_gap, abs(expected - observed))
            people_checked += 1
        assert people_checked > 100
        assert person_gap < 0.25, (
            f"a student's expected score is out by {person_gap:.2f} marks")

    def test_a_class_that_fits_has_mean_squares_near_one(self):
        """Mean squares average about 1.0 by construction, so a *systematic* drift
        is the signal: if every item overfit or underfit, the calibration or the
        residuals would be wrong rather than the paper."""
        exam, submissions = _class()
        analysis = item_analysis.analyse(exam, submissions)
        outfits = [item.outfit for item in analysis.items if item.outfit is not None]
        assert outfits
        assert sum(outfits) / len(outfits) == pytest.approx(1.0, abs=0.25)

    def test_a_guessing_item_is_caught_and_a_sound_one_is_not(self):
        """Fit statistics that never fire are decoration. One item answered at
        random — what a miskey or a guessed row looks like — has to be flagged,
        while the items around it are not."""
        items, students = 24, 240
        exam, submissions = _class(items=items, students=students)
        rng = random.Random(4242)
        for submission in submissions:
            submission["answers"]["5"] = rng.choice("ABCDE")
        analysis = item_analysis.analyse(exam, submissions)
        by_index = {item.index: item for item in analysis.items}
        broken = by_index[5]
        assert broken.outfit > 1.3, f"the random item's outfit is {broken.outfit}"
        assert broken.outfit_z > 2.0, f"its ZSTD is {broken.outfit_z}"
        quiet = [item for index, item in by_index.items() if index not in (5,)]
        flagged = [item.index for item in quiet
                   if item.outfit is not None and item.outfit_z > 2.0]
        assert len(flagged) <= 2, f"the flag fired on {flagged}, which are sound"


# ── the fit statistic's own distribution, by simulation ──────────────────────

class TestTheFitStatisticIsStandardised:
    """Under data generated *from* the model, ZSTD must be standard normal.

    This is the only test that can tell one degrees-of-freedom rule from another
    on evidence rather than on authority: the model is true, so a statistic that
    claims "the data misfit" must be wrong at the rate the normal deviate says.
    """

    @staticmethod
    def _zstd_for_item(seed: int, persons: int = 400, items: int = 30) -> float:
        """One replication: responses from known parameters, fit from the same."""
        rng = random.Random(seed)
        betas = [rng.gauss(0, 1) for _ in range(items)]
        betas = [value - sum(betas) / items for value in betas]
        thetas: list[float | None] = [rng.gauss(0, 1) for _ in range(persons)]
        rows = [[1.0 if rng.random() < item_analysis._logistic(theta - beta) else 0.0
                 for beta in betas] for theta in thetas]
        fit = item_analysis._fit(rows, betas, thetas, 0)
        assert fit is not None
        return fit.outfit_z

    @pytest.fixture(scope="class")
    def simulated(self):
        return [self._zstd_for_item(seed) for seed in range(120)]

    def test_the_mean_is_zero(self, simulated):
        assert sum(simulated) / len(simulated) == pytest.approx(0.0, abs=0.25)

    def test_the_spread_is_one(self, simulated):
        """The load-bearing assertion. With the response count as the d.f. this
        comes out near 1.4 — around 1,240 ZSTDs whose p-values are all wrong."""
        spread = item_analysis._sd(simulated)
        assert spread == pytest.approx(1.0, abs=0.25), (
            f"the ZSTD spread is {spread:.2f}, so the fit flag is miscalibrated")

    def test_the_same_sample_under_the_count_rule_is_wider(self, simulated):
        """"Not standard normal" is a claim about a specific wrong rule, so the
        rule is run over the *same* replications rather than merely asserted. The
        count-of-responses d.f. has no variance term: it assumes every response
        carries the same information, which on a paper of mixed difficulty it does
        not. Its spread is the ratio this test pins down."""
        rng = random.Random(11)
        betas = [rng.gauss(0, 1) for _ in range(30)]
        thetas: list[float | None] = [rng.gauss(0, 1) for _ in range(400)]
        rows = [[1.0 if rng.random() < item_analysis._logistic(theta - beta) else 0.0
                 for beta in betas] for theta in thetas]
        fit = item_analysis._fit(rows, betas, thetas, 0)
        count_rule = item_analysis._zstd(fit.outfit, 2.0 / len(rows))
        assert abs(count_rule) > abs(fit.outfit_z), (
            "the count rule came out no sterner than the model's, so it is not "
            "the rule this test means to reject")


# ── specific objectivity, the claim the whole model rests on ─────────────────

class TestSpecificObjectivity:
    def test_an_items_difficulty_barely_moves_between_ability_groups(self):
        """The defining property: a difficulty is a property of the *question*.

        One class is split at its median raw score and each half is calibrated on
        its own, so the two halves differ in ability by construction. The logit
        difficulties have to agree — and the bar is the estimation error itself,
        because "it moved, but by less than the noise in it" is the property: a
        difficulty that is a property of the question cannot also be a property of
        who sat it. The classical p-value, which *is* a group statistic, moves by
        far more, and both differences are measured so the gap between them is the
        evidence rather than either number alone.

        The fixture is drawn *from* the Rasch model, so the half-split is the only
        thing under test here.
        """
        exam, submissions = _rasch_class(items=30, students=240)
        scored = []
        for submission in submissions:
            correct = sum(1 for index in range(30)
                          if submission["answers"].get(str(index)) == "A")
            scored.append((correct, submission))
        scored.sort(key=lambda pair: pair[0])
        half = len(scored) // 2
        groups = {
            "low": [pair[1] for pair in scored[:half]],
            "high": [pair[1] for pair in scored[half:]],
        }
        fields: dict[str, dict[int, float]] = {}
        errors: dict[str, dict[int, float]] = {}
        p_values: dict[str, dict[int, float]] = {}
        answered: dict[str, dict[int, int]] = {}
        for name, group in groups.items():
            analysis = item_analysis.analyse(exam, group)
            fields[name] = {item.index: item.measure for item in analysis.items
                            if item.measure is not None}
            errors[name] = {item.index: (item.se or 0.0) for item in analysis.items}
            p_values[name] = {item.index: item.share for item in analysis.items}
            answered[name] = {item.index: max(item.answered, 1) for item in analysis.items}
        shared = sorted(set(fields["low"]) & set(fields["high"]))
        assert len(shared) >= 5, "too few items calibrated in both halves"

        def spread(table):
            gaps = [abs(table["low"][index] - table["high"][index])
                    for index in shared]
            return sum(gaps) / len(gaps)

        logit_gap = spread(fields)
        p_gap = spread(p_values)
        typical_error = sum((errors["low"][index] + errors["high"][index]) / 2
                            for index in shared) / len(shared)
        assert 0.1 < typical_error < 1.0, (
            f"the halves' standard errors are {typical_error:.2f} logits, so this "
            f"comparison is not measuring what it claims")
        # The bar is the error of the *difference*: two independent estimates,
        # each with its own standard error, so two times the variance.
        assert logit_gap < math.sqrt(2.0) * typical_error, (
            f"item difficulties moved {logit_gap:.2f} logits between ability "
            f"groups, against a standard error of {typical_error:.2f} — the "
            f"property is failing")
        # The p-value is the contrast, and it is judged in its own units: a
        # subgroup proportion's standard error is sqrt(p(1-p)/n), so a gap of
        # several of those is a real group effect rather than sampling noise.
        # (Comparing 0.29 logits against 0.32 probability would compare nothing:)
        # they are different scales, which is the whole reason the logit exists.
        p_error = sum(math.sqrt(p_values["low"][index]
                                * (1.0 - p_values["low"][index])
                                / answered["low"][index])
                      for index in shared) / len(shared)
        assert p_gap > 3 * p_error, (
            f"the classical p-value moved {p_gap:.3f} against its own standard "
            f"error of {p_error:.3f}, so the halves are not different enough for "
            f"this comparison to be evidence of anything")


# ── the numbers reach the page and the three documents ──────────────────────

class TestTheBlockIsReported:
    @pytest.fixture(scope="class")
    def built(self):
        exam, submissions = _class(items=12, students=60)
        return exam, item_analysis.analyse(exam, submissions)

    @pytest.mark.parametrize("lang", ["id", "en"])
    def test_the_csv_carries_the_four_lines(self, built, lang):
        exam, analysis = built
        text = analysis_report.analysis_csv(analysis, exam, lang=lang)
        t = analysis_report.labels(lang)
        for side in (t["side_people"], t["side_items"]):
            for row in (t["row_real"], t["row_model"]):
                assert f"{side} {row}" in text, f"the CSV has no {side} {row} line"

    @pytest.mark.parametrize("lang", ["id", "en"])
    def test_the_pdf_prints_the_block(self, built, lang):
        """The separation block belongs to the Rasch reading, and only to it.

        It used to be asked for without naming a framework, so the builder answered
        for the default one — CTT — which publishes no separation and no logit scale,
        and the block was correctly absent. The document under test is the Rasch one
        (`test_the_pdf_prints_only_the_blocks_the_framework_publishes` pins the other
        half: a classical report must NOT carry it), so the caller names it, the way
        every route does.
        """
        import fitz

        exam, analysis = built
        pdf = analysis_report.analysis_pdf(analysis, exam, lang=lang,
                                           framework=_RASCH)
        text = "".join(page.get_text() for page in fitz.open(stream=pdf,
                                                             filetype="pdf"))
        t = analysis_report.labels(lang)
        assert t["separation_block"] in text
        assert f"{t['side_people']} {t['row_real']}" in text
        assert t["strata"] in text

    def test_the_page_and_the_documents_print_the_same_lines(self, built):
        """One function decides which lines exist and what they say.

        The page re-renders the *labels* in the browser, because they follow the
        language toggle and the numbers do not — so the numbers it shows are the
        server's cells rather than a second implementation of them. This asserts the
        payload the page reads is that function's output, key by key.
        """
        from app.routes.teacher import _chart_payload

        exam, analysis = built
        payload = _chart_payload(analysis)
        assert payload["separation"] == analysis_report.separation_cells(
            analysis.summary)
        assert [line[0] for line in payload["separation"]] == ["people", "people",
                                                               "items", "items"]
        assert [line[1] for line in payload["separation"]] == ["real", "model",
                                                               "real", "model"]

    def test_a_line_with_no_separation_is_left_out(self):
        """Separation is the number the block exists for, and it has no value when
        there is nothing to divide. Four rows of dashes read as four measurements
        that all came out zero, so an unanswerable line is not printed at all — on
        the screen, in the CSV, in the PDF or in the workbook.
        """
        exam, submissions = _class(items=5, students=1)
        one_paper = item_analysis.analyse(exam, submissions)
        cells = analysis_report.separation_cells(one_paper.summary)
        assert [line[0] for line in cells] == ["items", "items"], (
            "a single paper cannot be separated from itself")
        assert all(line[4] is not None for line in cells)

        exam, nothing = _class(items=5, students=0)
        unmarked = item_analysis.analyse(exam, nothing)
        assert analysis_report.separation_cells(unmarked.summary) == []
        text = analysis_report.analysis_csv(unmarked, exam, lang="en")
        t = analysis_report.labels("en")
        assert t["separation_block"] in text
        assert f"{t['side_items']} {t['row_real']}" not in text, (
            "an empty line was printed as if it were a measurement")

    def test_the_workbook_holds_numbers_not_text(self, built):
        """A separation of "1.34" typed as text cannot be averaged or charted, and
        that is the defect this workbook exists to avoid."""
        exam, analysis = built
        book = load_workbook(io.BytesIO(analysis_report.analysis_xlsx(
            analysis, exam, lang="id")))
        sheet = book[analysis_report._sheet("summary", "id")]
        found = []
        for row in sheet.iter_rows():
            values = [cell.value for cell in row]
            if isinstance(values[0], str) and "REAL" in values[0]:
                found.extend(values[1:])
        numbers = [value for value in found if isinstance(value, (int, float))]
        assert len(numbers) >= 4, "the block was written as text"
        assert any(isinstance(value, float) for value in numbers)
