"""What a class's answers say about the paper — item analysis and a Rasch calibration.

A teacher's screens show what each *student* scored. None of them showed what each
*question* did, and that is the other half of a marked paper: a question everybody
got right measured nothing, a question whose strong students did worse than its weak
ones is worth a second look, and a distractor nobody chose is a wasted option. The
whole point of an answer sheet is that it carries this information for free, and it
was sitting in `submissions.answers` unread.

Two families of numbers, and they answer different questions:

* **Classical item statistics** — difficulty (`p`, the share of the marks a question
  actually paid out), discrimination (`D`, upper 27% against lower 27%), and the
  point-biserial correlation with the rest of the paper. These need no model, they
  are what "anates" reports, and a teacher reads them in percentages.
* **A Rasch calibration** (the Winsteps family): every question's difficulty and
  every student's ability on one **logit** scale, with each item's standard error,
  its **t = measure / S.E.** against the paper's average, and its infit/outfit
  MNSQ — how much a question's answers surprise the model. `t = (M1 - M2) /
  sqrt(SE1² + SE2²)` with Welch–Satterthwaite degrees of freedom is Winsteps' own
  definition (www.winsteps.com/winman/t-statistics.htm), and it is what
  :func:`compare_measures` implements — including the two-sided probability, so the
  1.96 / 2.58 thresholds mean what that table says they mean.

Three rules this module borrows rather than restates, because a second copy is how
two numbers about one paper start disagreeing:

* a question's score share is the app's own grader (`question_types.part_factor`),
  so partial credit for a matching or ordering question counts here exactly as it
  counted on the paper;
* an essay's score share is the teacher's mark in `teacher_feedback.scores`, which
  is the only place a written answer is marked;
* the paper's marks come from `question_weights`, so "difficulty" is measured
  against what the question was worth and not against an equal split.

Honest limits, and they are stated on the page rather than hidden here:

* items are calibrated **dichotomously** — full credit against everything else. A
  part-credited matching question is a polytomous item and the Rasch model for it is
  the rating scale model, which this is not. The classical columns do use the part
  credit, so the two halves of the table answer slightly different questions;
* a question with no answer key has no data and is reported as such rather than
  scored wrong (the app's own auto-grader scores it wrong; this module refuses to
  pretend it measured something);
* a student who did not answer a question is *missing*, not wrong, and is left out
  of that item's statistics.
"""
from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from app.services import analysis_frameworks as af
from app.services import question_types as qt

#: The share of the class counted as the \"upper\" and \"lower\" group for the
#: discrimination index. 27% is the conventional figure: it is the point at which
#: the two groups differ most for a normally distributed trait.
GROUP_SHARE = 0.27

#: Below this many answered papers an item's numbers are not worth reading, and a
#: percentage with a denominator of three is worse than no percentage.
MIN_PAPERS = 1

#: Winsteps' own treatment of an extreme response pattern (a paper all right or all
#: wrong, an item every paper got right or none did): nudge the raw score by 0.3
#: score points and solve the *same* equation. Solving the same equation, rather
#: than mapping a proportion straight onto the scale, is what keeps the order — a
#: perfect paper must come out above the 9-out-of-10 paper, and the first version of
#: this module put it below, because 0.3 of a point and 0.03 of a proportion are not
#: the same nudge.
EXTREME_NUDGE = 0.3

#: The band a productive item's mean-square fit sits in. Above it the item measures
#: something other than the rest of the paper; below it the item is redundant.
FIT_LOW, FIT_HIGH = 0.5, 1.5

#: A choice question's five options, which is what the app's answer sheet prints and
#: what its student page renders. Listed rather than inferred so a distractor that
#: *nobody* chose still gets a line — an option nobody picks is a finding, and an
#: option missing from the table looks like it was never on the paper.
CHOICE_OPTIONS = ("A", "B", "C", "D", "E")

#: A true/false question's two, spelled as `question_types._as_bool_word` writes them.
BOOL_OPTIONS = ("true", "false")

#: Two-sided thresholds from the t table on the Winsteps page: p < .05 and p < .01.
T_05, T_01 = 1.96, 2.58


# ── small numerical helpers ──────────────────────────────────────────────────

def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _variance(values: Sequence[float], ddof: int = 1) -> float:
    if len(values) - ddof < 1:
        return 0.0
    centre = _mean(values)
    return sum((v - centre) ** 2 for v in values) / (len(values) - ddof)


def _sd(values: Sequence[float], ddof: int = 1) -> float:
    return math.sqrt(_variance(values, ddof))


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Pearson's r, and 0.0 when either side has no spread.

    A question every student got right has zero variance, so its correlation is
    undefined; 0.0 with the flag the caller sets is the honest reading, and a NaN
    leaking into a JSON payload or a chart is not.
    """
    if len(xs) != len(ys) or len(xs) < 3:
        return 0.0
    sx, sy = _sd(xs), _sd(ys)
    if sx == 0 or sy == 0:
        return 0.0
    mx, my = _mean(xs), _mean(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (len(xs) - 1)
    return cov / (sx * sy)


def _logit(p: float) -> float:
    """A **difficulty** on the logit scale — `ln((1-p)/p)`, so a hard item is positive.

    Not the textbook logit, and deliberately so: the model here is written
    `P(correct) = _logistic(theta - b)`, with `theta` an ability and `b` a difficulty
    on the same axis. Substituting a proportion for `P` with the paper anchored at
    zero logits gives `b = ln((1-p)/p)`, which is this function. It is therefore
    `_logistic`'s inverse *up to the sign of the difference*, i.e.
    `_logistic(_logit(p)) == 1 - p`. A caller who wants a *measure* rather than a
    proportion should use :func:`_difficulty_from`, so the direction is named at the
    call site instead of remembered. A paper that scored everything right is the
    *high* end of this scale and a question everyone got right is the *low* end, and
    mixing those two signs is how an earlier version of this module published its
    best student as its weakest — which no amount of care at the call site catches,
    because both branches look right on their own.
    """
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log((1.0 - p) / p)


def _logistic(x: float) -> float:
    """The Rasch response curve: `P(correct)` rises with `x` and stays in (0, 1)."""
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    grown = math.exp(x)
    return grown / (1.0 + grown)


def _difficulty_from(share: float) -> float:
    """An item's difficulty when every paper scored it the same. Hard is positive."""
    return _logit(share)


def _nudged(raw: float, length: int) -> float:
    """A raw score moved off the boundary by `EXTREME_NUDGE` points, or left alone.

    Used for both sides of the calibration. A paper's raw score and an item's raw
    score are the same kind of number here — a count of successes — so they get the
    same correction, and both then go through the equation their non-extreme
    neighbours use.
    """
    if raw <= 0:
        return EXTREME_NUDGE
    if raw >= length:
        return length - EXTREME_NUDGE
    return raw

def _betacf(a: float, b: float, x: float, iterations: int = 300,
            eps: float = 3e-14) -> float:
    """Continued fraction for the incomplete beta function (Lentz's method)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    result = d
    for m in range(1, iterations + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        result *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        result *= delta
        if abs(delta - 1.0) < eps:
            break
    return result


def _betai(a: float, b: float, x: float) -> float:
    """The regularised incomplete beta function, `I_x(a, b)`."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
        + a * math.log(x) + b * math.log(1.0 - x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def student_t_two_sided_p(t: float, df: float) -> float:
    """The two-sided probability of |t| with `df` degrees of freedom.

    The table on the reference page, as arithmetic: at 1000 d.f. it gives .050 for
    t = 1.96 and .010 for 2.58, which is the row that page prints as \"1.96 / 2.58\".
    """
    if df <= 0:
        return 1.0
    x = df / (df + t * t)
    return _betai(df / 2.0, 0.5, x)


def compare_measures(m1: float, se1: float, n1: int,
                     m2: float, se2: float, n2: int) -> tuple[float, float, float]:
    """``(t, df, p)`` for two measures — Winsteps' t-statistics, in arithmetic.

        t = (M1 - M2) / sqrt(SE1² + SE2²)
        df = (EV1 + EV2)² / (EV1²/(N1-1) + EV2²/(N2-1))   [Welch–Satterthwaite]

    Checked against the worked example on that page (M1 = 1.62, SE1 = .38, N1 = 18;
    M2 = .76, SE2 = .16, N2 = 57): t = 2.09, and the page reports df = 24 where the
    formula gives 23.3 — Winsteps prints it rounded, and p = .049 either way.
    """
    ev1, ev2 = se1 * se1, se2 * se2
    spread = math.sqrt(ev1 + ev2)
    t = (m1 - m2) / spread if spread else 0.0
    denominator = 0.0
    if n1 > 1:
        denominator += ev1 * ev1 / (n1 - 1)
    if n2 > 1:
        denominator += ev2 * ev2 / (n2 - 1)
    if denominator <= 0:
        return t, float("inf"), student_t_two_sided_p(t, 100000.0)
    df = (ev1 + ev2) ** 2 / denominator
    return t, df, student_t_two_sided_p(t, df)


def _zstd(mean_square: float, variance: float) -> float:
    """A mean square as a unit-normal deviate (Wilson–Hilferty).

    This is Winsteps' ZSTD, and the argument that matters is the **variance of the
    mean square**, not the count of observations behind it: Winsteps' own help says
    "the degrees of freedom are 2/(qi*qi)", where `qi` is the model standard
    deviation of the mean square, and Rating Scale Analysis estimates that d.f. from
    the model distributions of the observations rather than from the sample size
    (Wilson & Hilferty 1931; Schulz, RMT 16:2 p. 879; RSA pp. 100-101).

    The distinction is not cosmetic. On data generated *from* the Rasch model — where
    a fit statistic should be standard normal — dividing by the observation count
    spreads ZSTD by a factor of 1.44 (measured, 60 replications: SD 1.44 against
    0.95), so a misfit flag at 2.0 fires on data that fit. Floored at 1.0, as
    Winsteps floors it, because the cube-root transform "goes crazy" below that.
    """
    if variance <= 0 or mean_square <= 0:
        return 0.0
    df = max(2.0 / variance, 1.0)
    third = 1.0 - 2.0 / (9.0 * df)
    return (mean_square ** (1.0 / 3.0) - third) / math.sqrt(2.0 / (9.0 * df))


def _observations(rows: list[list[float | None]], betas: Sequence[float],
                  thetas: Sequence[float | None], item: int | None = None,
                  person: int | None = None,
                  ) -> list[tuple[float, float, float]]:
    """`(standardized residual², model variance, model variance of that)`, per response.

    One observation is `(x - E)² / W` — the squared Pearson residual — with its
    information `W = P(1-P)`. The third number is what the fit *statistic's* own
    sampling distribution needs: the variance of a squared standardized residual,
    `Var(z²) = (1-2P)²/(P(1-P))`. It is zero at `P = .5` (where `z²` is 1 whatever
    happens) and grows as the response becomes predictable, which is why a test of
    middling questions has far more effective degrees of freedom than it has
    responses.

    `item` walks the rows; `person` walks the columns. The same arithmetic serves
    both, and Winsteps reports both.
    """
    pairs = ([(j, item) for j in range(len(rows))] if item is not None
             else [(person, i) for i in range(len(betas))])
    out: list[tuple[float, float, float]] = []
    for j, i in pairs:
        value = rows[j][i]
        theta = thetas[j]
        if value is None or theta is None:
            continue
        expect = _logistic(theta - betas[i])
        variance = expect * (1.0 - expect)
        if variance <= 1e-12:
            continue
        out.append((((value - expect) ** 2) / variance, variance,
                    ((1.0 - 2.0 * expect) ** 2) / variance))
    return out


@dataclass(frozen=True)
class Fit:
    """One item's or person's fit, in Winsteps' four numbers and their d.f."""

    infit: float
    infit_z: float
    outfit: float
    outfit_z: float
    #: The effective degrees of freedom behind each ZSTD: `2 / Var(mean square)`.
    infit_df: float
    outfit_df: float


def _fit_of(observations: Sequence[tuple[float, float, float]]) -> Fit | None:
    """Mean squares and ZSTDs from the observations, as Winsteps computes them.

        Outfit = sum(residual² / information) / count(residuals)
        Infit  = sum(residual² / information * information) / sum(information)

    — the first an ordinary mean of squared standardized residuals (outlier
    sensitive), the second weighted by each observation's information (pattern
    sensitive), exactly as Wright & Masters define them (RSA p. 100).
    """
    count = len(observations)
    info = sum(weight for _z, weight, _v in observations)
    if not count or info <= 0:
        return None
    outfit = sum(z2 for z2, _weight, _v in observations) / count
    infit = sum(z2 * weight for z2, weight, _v in observations) / info
    # Independent observations, so the variances add; the mean squares are sums
    # over the same terms, which is what carries the 1/count² and 1/info² through.
    var_outfit = sum(v for _z, _weight, v in observations) / (count * count)
    var_infit = sum(weight * weight * v for _z, weight, v in observations) / (info * info)
    return Fit(
        infit=infit, infit_z=_zstd(infit, var_infit),
        outfit=outfit, outfit_z=_zstd(outfit, var_outfit),
        infit_df=(2.0 / var_infit if var_infit > 0 else 0.0),
        outfit_df=(2.0 / var_outfit if var_outfit > 0 else 0.0),
    )


def _real_se(model_se: float, infit: float | None) -> float:
    """Winsteps' misfit-inflated standard error.

    "Real S.E. of an estimated measure = Model S.E. * Maximum [1.0, sqrt(INFIT
    mean-square)]" (Winsteps, *Standard errors: model and real*). Model S.E. is what
    the data say under the model; Real S.E. is the worst case, where the misfit is
    a real departure from the model rather than noise. Both are reported, because a
    reliability computed from the model errors flatters a misfitting paper.
    """
    if infit is None:
        return model_se
    return model_se * max(1.0, math.sqrt(max(infit, 0.0)))


# ── what the exam and its submissions give us ────────────────────────────────

@dataclass
class _Response:
    """One submission, reduced to what the analysis reads."""
    name: str
    score: float
    late: bool
    #: question index -> share of the question's marks (0..1), or None when the
    #: question carries no data for this paper at all.
    shares: dict[int, float | None] = field(default_factory=dict)
    #: question index -> the raw answer, for the option (distractor) counting.
    raw: dict[int, Any] = field(default_factory=dict)


def _essay_shares(feedback: Mapping[str, Any]) -> dict[str, float]:
    """A teacher's marks for the written questions, as `{index: 0..1}`.

    `teacher_feedback.scores` is where both the marking page and the queue write
    them, as a percentage per question. A question the teacher has not marked is
    simply absent, and stays missing here.
    """
    scores = (feedback or {}).get("scores") or {}
    shares: dict[str, float] = {}
    for key, value in scores.items():
        if value is None or value == "":
            continue
        try:
            shares[str(key)] = max(0.0, min(100.0, float(value))) / 100.0
        except (TypeError, ValueError):
            continue
    return shares


def _responses(exam: Mapping[str, Any],
               submissions: Iterable[Mapping[str, Any]]) -> list[_Response]:
    """Every submission as per-question score shares, by the app's own grader."""
    qtypes = exam.get("question_types") or {}
    key = exam.get("answer_key") or {}
    weights = exam.get("question_weights") or {}
    total = int(exam.get("total_questions") or 0)
    part = qt.partial_credit(weights)

    out: list[_Response] = []
    for sub in submissions:
        answers = sub.get("answers") or {}
        feedback = sub.get("teacher_feedback") or {}
        essay_marks = _essay_shares(feedback)
        row = _Response(
            name=sub.get("student_name") or sub.get("student_id") or "-",
            score=float(sub.get("final_score") if sub.get("final_score") is not None
                        else (sub.get("score") or 0)),
            late=bool(sub.get("submitted_late")),
        )
        for i in range(total):
            qi = str(i)
            qtype = qtypes.get(qi)
            answer = answers.get(qi)
            if not qt.is_objective(qtype):
                # A written answer is scored only where the teacher marked it.
                mark = essay_marks.get(qi)
                row.shares[i] = mark
                row.raw[i] = answer
                continue
            if not qt.key_has_answer(qtype, key.get(qi)):
                # No key: the auto-grader scores this wrong, and this module
                # refuses to measure a question against nothing.
                row.shares[i] = None
                row.raw[i] = answer
                continue
            if answer is None or answer == "" or answer == {} or answer == []:
                row.shares[i] = None
                row.raw[i] = answer
                continue
            if part:
                row.shares[i] = qt.part_factor(qtype, key.get(qi), answer)
            else:
                row.shares[i] = 1.0 if qt.grade_answer(qtype, key.get(qi), answer) else 0.0
            row.raw[i] = answer
        out.append(row)
    return out


def _binary(shares: Sequence[float | None]) -> list[float | None]:
    """Full credit as 1, anything else as 0, missing as missing."""
    return [None if s is None else (1.0 if s >= 1.0 else 0.0) for s in shares]


def _solve_monotone(target: float, function, increasing: bool,
                    low: float = -30.0, high: float = 30.0,
                    iterations: int = 90) -> float:
    """Bisection for `function(x) = target`, on a function that is monotone in x.

    Both equations of the calibration are monotone — a student's expected score rises
    with ability, an item's rises as the item gets easier — so bisection is exact to
    the iteration budget and, unlike Newton–Raphson, **cannot overshoot**. That is
    not a stylistic preference: the first version of this module used Newton, and on
    a class whose answers follow a near-perfect Guttman pattern (strong students right,
    weak students wrong, with four perfect or zero papers) it walked an item measure
    to 10^7 and a person measure to 10^6 — numbers that would have been published as
    "logits" on a teacher's screen.
    """
    for _ in range(iterations):
        middle = (low + high) / 2.0
        if (function(middle) < target) == increasing:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


def _calibrate(rows: list[list[float | None]], item_count: int,
               rounds: int = 3) -> tuple[list[float | None], list[float | None],
                                         list[float | None], list[float | None]]:
    """Joint Rasch calibration: item difficulties, person abilities, and their SEs.

    Alternating solution of the unconditional (UCON/JML) equations:

        b_i  solves  Σ_j P(x_ij = 1) = r_i     (the item's observed score)
        θ_j  solves  Σ_i P(x_ij = 1) = r_j

    Only the *differences* between a person's ability and an item's difficulty are
    estimable, so the origin of the scale is chosen rather than found, and Winsteps
    chooses the item mean (UCON). This does that explicitly: the shift is applied
    after every round. It is what makes `t = b_i / SE_i` a test against "as hard as
    this paper's average question" — without it the calibration stops wherever three
    rounds happened to leave it, and the anchor sentence on the page is untrue.
    Subtracting the same constant from both sides leaves every `theta - b` and
    therefore every fit statistic untouched. A student with a perfect or zero raw
    score has no finite maximum-likelihood estimate, so gets the conventional
    third-of-a-case correction and is flagged as extreme on the person table rather
    than quietly dropped.
    """
    people, items = len(rows), item_count
    if not people or not items:
        # Nothing to solve: nobody answered, or no question could be calibrated.
        # The shape is still the caller's — one slot per question and per paper —
        # with every slot saying "unknown". An empty list here indexed a question
        # that did not exist, which is a 500 on the analysis page; a filled list of
        # zeros would be worse, because `0.000 logits` is a measurement a reader
        # would quote and this is the case where there is no measurement at all.
        return ([None] * items, [None] * people,
                [None] * items, [None] * people)

    betas = [0.0] * items
    thetas: list[float | None] = [None] * people

    for _ in range(rounds):
        for j, row in enumerate(rows):
            seen = [i for i in range(items) if row[i] is not None]
            if not seen:
                thetas[j] = None
                continue
            raw = sum(row[i] for i in seen)
            length = len(seen)
            thetas[j] = _solve_monotone(
                _nudged(raw, length),
                lambda t, seen=seen: sum(_logistic(t - betas[i]) for i in seen),
                increasing=True)

        for i in range(items):
            pairs = [thetas[j] for j in range(people)
                     if rows[j][i] is not None and thetas[j] is not None]
            if not pairs:
                continue
            raw = sum(rows[j][i] for j in range(people)
                      if rows[j][i] is not None and thetas[j] is not None)
            length = len(pairs)
            # Decreasing in b: a harder item predicts fewer correct, so the root is
            # found with `increasing=False`. An extreme item takes the same road,
            # with its raw score nudged — so "everyone got it right" lands beyond
            # the easiest measured question instead of merely near it.
            betas[i] = _solve_monotone(
                _nudged(raw, length),
                lambda b, pairs=pairs: sum(_logistic(t - b) for t in pairs),
                increasing=False)

        # The UCON constraint, after each round. `_logistic(theta - b)` is
        # unchanged by shifting both, so this moves the origin and nothing else.
        shift = _mean(betas)
        betas = [value - shift for value in betas]
        thetas = [value - shift if value is not None else None for value in thetas]

    # Standard errors are 1/sqrt(information). An item or a person whose information
    # is zero — every paper at an extreme, so the model has no curvature to measure
    # — gets None rather than a division by zero, and the page shows a dash.
    item_se: list[float | None] = []
    for i in range(items):
        info = 0.0
        for j in range(people):
            if rows[j][i] is None or thetas[j] is None:
                continue
            p = _logistic(thetas[j] - betas[i])
            info += p * (1.0 - p)
        item_se.append(1.0 / math.sqrt(info) if info > 1e-12 else None)

    person_se: list[float | None] = []
    for j, row in enumerate(rows):
        info = 0.0
        for i in range(items):
            if row[i] is None or thetas[j] is None:
                continue
            p = _logistic(thetas[j] - betas[i])
            info += p * (1.0 - p)
        person_se.append(1.0 / math.sqrt(info) if info > 1e-12 else None)
    return betas, thetas, item_se, person_se


def _fit(rows: list[list[float | None]], betas: Sequence[float],
         thetas: Sequence[float | None], index: int) -> Fit | None:
    """One item's fit: infit and outfit mean squares and their ZSTDs."""
    return _fit_of(_observations(rows, betas, thetas, item=index))


def _person_fit(rows: list[list[float | None]], betas: Sequence[float],
                thetas: Sequence[float | None], index: int) -> Fit | None:
    """One person's fit — the same arithmetic with the axes exchanged.

    Winsteps' person table carries infit and outfit for the same reason the item
    table does: a paper whose answers do not go together is a paper the measures do
    not describe, and its Real S.E. is inflated by exactly this number.
    """
    return _fit_of(_observations(rows, betas, thetas, person=index))


def _option_labels(qtype: Any, key: Any) -> tuple[str, ...]:
    """The options a distractor table should list for this question.

    A matching question has no single option to count — its answer is a pairing, and
    counting "how many chose left 3" says nothing about which right was attached to
    it — so it gets no option table rather than a misleading one.
    """
    kind = qt.question_kind(qtype)
    if kind == qt.KIND_CHOICE:
        return CHOICE_OPTIONS
    if kind == qt.KIND_TRUE_FALSE:
        return BOOL_OPTIONS
    return ()


def _chosen(answer: Any) -> str:
    """What a student picked, as a comparable token (`unwrap`ed, case-folded)."""
    value = qt.unwrap(answer)
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value).strip().casefold()


def _group_measure(values: Sequence[float]) -> tuple[float, float]:
    """A group's difficulty in logits and its standard error, on one convention.

    The score is corrected by a third of a case at both ends — the same extreme-score
    treatment the calibration uses — so a group that answered a question perfectly
    gets a large finite difficulty instead of an infinity, and the measure and its
    standard error come from that *same* probability. The first version measured the
    guarded probability but took the error from the raw one, which made t collapse to
    zero exactly where a question was behaving most oddly.
    """
    n = len(values)
    if n == 0:
        return 0.0, 0.0
    raw = sum(values)
    probability = (raw + 0.3) / (n + 0.6)
    return (_difficulty_from(probability),
            1.0 / math.sqrt(n * probability * (1.0 - probability)))


def _flag(share: float, answered: int, discrimination: float,
          point_biserial: float, outfit: float | None,
          keyed: bool = True) -> str:
    """One key per finding; the sentence lives in the template, in both languages."""
    if not keyed:
        # Checked first and named separately from `unscored`: "the key is missing"
        # and "nobody answered" are different instructions to the teacher, and the
        # page must not print the wrong one.
        return "unkeyed"
    if answered < MIN_PAPERS:
        return "unscored"
    if share <= 0.0:
        return "extreme_hard"
    if share >= 1.0:
        return "extreme_easy"
    if point_biserial < 0:
        return "negative"
    if discrimination < 0.2 or point_biserial < 0.2:
        return "weak"
    if outfit is not None and (outfit > FIT_HIGH or outfit < FIT_LOW):
        return "misfit"
    return "ok"


# ── the analysis ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Distractor:
    """One option of a choice question: how many students picked it, and the key."""
    label: str
    count: int
    key: bool


@dataclass(frozen=True)
class Item:
    index: int
    qtype: str
    kind: str
    marks: float
    objective: bool
    keyed: bool                  # an objective item with a key to mark against
    answered: int
    missing: int
    share: float                 # mean credit, 0..1
    pct: float                   # share as a percentage — the classical difficulty
    full: int                    # papers awarded full credit
    discrimination: float        # D, upper 27% minus lower 27%
    point_biserial: float        # r against the rest of the paper
    measure: float | None = None # logits, dichotomous calibration
    se: float | None = None
    t: float | None = None       # measure / S.E. — the reference's t-test against 0
    p_value: float | None = None # its two-sided probability
    infit: float | None = None
    infit_z: float | None = None
    outfit: float | None = None
    outfit_z: float | None = None
    pt_measure: float | None = None
    #: The same standard error with its own misfit folded in (`_real_se`).
    se_real: float | None = None
    flag: str = "ok"
    distractors: tuple[Distractor, ...] = ()
    #: The cognitive level the teacher recorded in the kisi-kisi, as a key
    #: ("c1".."c6") and its band ("lots"/"mots"/"hots"). Empty means *not
    #: labelled* — never guessed from the question type, which would invent the
    #: very thing this framework exists to measure.
    level: str = ""
    band: str = ""
    #: Criterion-referenced verdict: has the class mastered this question, by the
    #: KKM's own standard? ``None`` means there is nothing to judge (nobody
    #: answered, or the exam carries no KKM).
    mastered: bool | None = None


@dataclass(frozen=True)
class Person:
    name: str
    raw: int
    possible: int
    measure: float | None
    se: float | None
    score: float
    extreme: bool
    #: A paper's own fit, which is what makes its Real S.E. its own: the same
    #: columns as the item table, with the persons as the rows.
    infit: float | None = None
    infit_z: float | None = None
    outfit: float | None = None
    outfit_z: float | None = None
    se_real: float | None = None


@dataclass(frozen=True)
class Split:
    """One item's difficulty measured for two halves of the class.

    Not a DIF study — the halves are formed *by* total score, so a difference here
    says the question behaves differently for stronger and weaker students, which
    is exactly what a t of two measures is for.
    """
    index: int
    upper: float
    se_upper: float
    n_upper: int
    lower: float
    se_lower: float
    n_lower: int
    t: float
    df: float
    p: float


@dataclass(frozen=True)
class Separation:
    """One side of Winsteps' Table 28.3: how far apart the measures really are.

    Two rows, because they answer different questions and a school should quote the
    worse of them:

        REAL  RMSE 1.18  TRUE SD 1.58  SEPARATION 1.34  RELIABILITY .64  STRATA 2.12
        MODEL RMSE 1.01  TRUE SD 1.69  SEPARATION 1.67  RELIABILITY .74  STRATA 2.56

    MODEL takes the data at face value: the errors are the ones the model predicts.
    REAL assumes misfit is a departure from the model rather than noise, and inflates
    every standard error by its own infit (`_real_se`), so REAL is the conservative
    row. A separation of 2 means two statistically distinct levels of ability in the
    class; strata is how many the spread supports at a 95% cut.
    """

    rmse_model: float | None = None
    rmse_real: float | None = None
    true_sd_model: float | None = None
    true_sd_real: float | None = None
    separation_model: float | None = None
    separation_real: float | None = None
    reliability_model: float | None = None
    reliability_real: float | None = None
    strata_model: float | None = None
    strata_real: float | None = None


def _rounded(block: Separation) -> Separation:
    """A separation block, rounded for a document. Money-like, not machine-like."""
    return Separation(**{
        name: (round(value, 3) if name.startswith("reliability") else round(value, 2))
        for name, value in dataclasses.asdict(block).items()
        if value is not None
    })


def separation(measures: Sequence[float | None], model_se: Sequence[float | None],
               real_se: Sequence[float | None]) -> Separation:
    """Winsteps' separation block, from the measures and both sets of errors.

    `RMSE` is the root-mean-square of the standard errors — "the average conditional
    standard error of measurement for this sample"; `TRUE SD` is the observed
    *population* SD (Winsteps' P.SD, so ddof 0) with that error's variance taken out;
    `SEPARATION` is TRUE SD / RMSE; `RELIABILITY` is its square over one plus its
    square, which is the true variance over the observed variance; and `STRATA` is
    `(4G + 1)/3` — the number of statistically distinct levels the spread supports.

    Checked against the worked example in Winsteps' Table 28.3 (P.SD 1.97, RMSE 1.18
    real and 1.01 model, separation 1.34 and 1.67, reliability .64 and .74): every
    one of those comes back out of this function, which is why it takes plain
    numbers rather than an `Analysis`. A separation with no error to divide by — a
    class the model describes exactly — is unanswerable rather than infinite, and is
    reported as nothing at all.
    """
    values = [value for value in measures if value is not None]
    if len(values) < 2:
        return Separation()
    observed = _sd(values, ddof=0)
    block: dict[str, float] = {}
    for label, errors in (("model", [se for se in model_se if se]),
                          ("real", [se for se in real_se if se])):
        if not errors:
            continue
        rmse = math.sqrt(_mean([se * se for se in errors]))
        variance = observed * observed - rmse * rmse
        true_sd = math.sqrt(variance) if variance > 0 else 0.0
        block[f"rmse_{label}"] = rmse
        block[f"true_sd_{label}"] = true_sd
        if rmse <= 1e-12:
            continue
        ratio = true_sd / rmse
        block[f"separation_{label}"] = ratio
        block[f"reliability_{label}"] = ratio * ratio / (1.0 + ratio * ratio)
        block[f"strata_{label}"] = (4.0 * ratio + 1.0) / 3.0
    return Separation(**block)


@dataclass(frozen=True)
class Summary:
    students: int
    items: int
    objective: int
    essays: int
    answered_papers: int
    total_mean: float
    total_sd: float
    alpha: float | None
    kr20: float | None
    sem: float | None
    person_reliability: float | None
    person_separation: float | None
    item_reliability: float | None
    item_separation: float | None
    mean_measure: float | None
    sd_measure: float | None
    extremes: int
    #: Winsteps' separation block, one per side. `person_reliability` and
    #: `person_separation` above are this block's MODEL row, kept as flat fields
    #: because the page and the documents read them by name.
    person_stats: Separation = Separation()
    item_stats: Separation = Separation()


@dataclass(frozen=True)
class CognitiveMix:
    """How much of the paper asks for each level of thinking.

    Two bases are reported because a kisi-kisi is written in *marks* and a
    question list is read in *questions*: three HOTS questions worth one mark
    each beside ten one-mark LOTS questions are 23% of the paper by question and
    23% by mark, but a single five-mark HOTS question beside ten one-mark LOTS
    questions is 9% by question and 33% by mark. Quoting one basis without
    saying which is how the same paper "is" and "is not" HOTS-heavy depending on
    who counted.

    ``unset`` is the honest half and stays visible: a paper whose questions have
    no level recorded is not 0% HOTS, it is *unlabelled*, and the panel says so
    instead of drawing a bar at zero.
    """

    counts: Mapping[str, int] = field(default_factory=dict)      # band -> questions
    marks: Mapping[str, float] = field(default_factory=dict)     # band -> marks
    unset: int = 0
    unset_marks: float = 0.0
    total: int = 0
    total_marks: float = 0.0
    #: "marks" when the paper carries weights, "questions" when it does not.
    basis: str = "questions"

    @property
    def labelled(self) -> int:
        return self.total - self.unset

    def share(self, band: str) -> float | None:
        """The band's part of the paper as a percentage, on this mix's basis."""
        if self.basis == "marks":
            if not self.total_marks:
                return None
            return round(self.marks.get(band, 0.0) / self.total_marks * 100, 1)
        if not self.total:
            return None
        return round(self.counts.get(band, 0) / self.total * 100, 1)


@dataclass(frozen=True)
class Mastery:
    """Criterion-referenced mastery against the school's own KKM.

    Two questions, both about the standard rather than about the spread: who is
    tuntas, and which questions the class has mastered. A question is mastered
    when the share of the papers that earned full credit on it reaches the KKM
    — the school's own number, so changing the KKM moves the verdict and nothing
    else has to be argued about.

    ``configured`` is False when the exam carries no KKM (0 or absent): a report
    that treats a missing standard as "everything passes" is worse than one that
    says the standard is missing.
    """

    kkm: int = 0
    configured: bool = False
    passed: int = 0
    failed: int = 0
    mean: float | None = None
    lowest: float | None = None
    items_mastered: int = 0
    items_measured: int = 0

    @property
    def threshold(self) -> float:
        """The share of the class that must get a question fully right."""
        return self.kkm / 100.0

    @property
    def gap(self) -> float | None:
        """How far the class's mean sits from the standard, in marks."""
        return None if self.mean is None else round(self.mean - self.kkm, 2)

    @property
    def pass_rate(self) -> float | None:
        total = self.passed + self.failed
        return round(self.passed / total * 100, 1) if total else None


@dataclass(frozen=True)
class Analysis:
    code: str
    title: str
    summary: Summary
    items: tuple[Item, ...]
    people: tuple[Person, ...]
    splits: tuple[Split, ...]
    notes: tuple[str, ...]
    #: ``(bin centre, count)`` of the ability distribution, for the chart.
    person_bins: tuple[tuple[float, int], ...] = ()
    #: The two frameworks that need their own block. CTT and Rasch are already
    #: the summary, the item table and the splits above; these two are the ones
    #: this app never published, so they arrive as named blocks.
    cognitive: CognitiveMix = field(default_factory=CognitiveMix)
    mastery: Mastery = field(default_factory=Mastery)


def _alpha(columns: list[list[float]]) -> float | None:
    """Cronbach's alpha over the item columns that carry data.

    The same number as KR-20 when every column is 0/1, which is why one function
    answers both and the caller only differs in what it names the answer.
    """
    if len(columns) < 2 or not columns or not columns[0]:
        return None
    k = len(columns)
    variances = [_variance(col) for col in columns]
    totals = [sum(col[i] for col in columns) for i in range(len(columns[0]))]
    total_variance = _variance(totals)
    if total_variance <= 0:
        return None
    return (k / (k - 1.0)) * (1.0 - sum(variances) / total_variance)


def _bins(values: Sequence[float], count: int = 12) -> tuple[tuple[float, int], ...]:
    """A histogram as ``(centre, n)`` pairs, so a chart needs no binning code."""
    if not values:
        return ()
    low, high = min(values), max(values)
    if high - low < 1e-9:
        return ((round(low, 3), len(values)),)
    width = (high - low) / count
    counts = [0] * count
    for value in values:
        slot = int((value - low) / width)
        counts[min(slot, count - 1)] += 1
    return tuple((round(low + width * (i + 0.5), 3), n) for i, n in enumerate(counts))


def analyse(exam: Mapping[str, Any],
            submissions: Iterable[Mapping[str, Any]]) -> Analysis:
    """The whole analysis of one exam, from the rows both screens already load."""
    total_questions = int(exam.get("total_questions") or 0)
    qtypes = exam.get("question_types") or {}
    weights = exam.get("question_weights") or {}
    key = exam.get("answer_key") or {}
    # The kisi-kisi's own labels, and the school's own standard. Both are what
    # the teacher recorded; neither is inferred from anything.
    levels = exam.get("question_cognitive") or {}
    if not isinstance(levels, Mapping):
        levels = {}
    kkm = _kkm(exam.get("passing_score"))
    responses = list(submissions)
    rows = _responses(exam, responses)

    # The matrix the calibration works on: objective items only, dichotomous.
    binary_columns: list[list[float | None]] = []
    matrix: list[list[float | None]] = [[] for _ in rows]
    item_index: list[int] = []
    for i in range(total_questions):
        qi = str(i)
        if not qt.is_objective(qtypes.get(qi)):
            continue
        if not qt.key_has_answer(qtypes.get(qi), key.get(qi)):
            continue
        item_index.append(i)
        column = _binary([row.shares.get(i) for row in rows])
        binary_columns.append(column)
        for j, value in enumerate(column):
            matrix[j].append(value)

    betas, thetas, item_se, person_se = _calibrate(matrix, len(item_index))
    fit_by_item = {
        i: _fit(matrix, betas, thetas, position)
        for position, i in enumerate(item_index)
    }
    beta_by_item = {i: betas[position] for position, i in enumerate(item_index)}
    se_by_item = {i: item_se[position] for position, i in enumerate(item_index)}
    real_se_by_item = {
        i: (_real_se(item_se[position], fit_by_item[i].infit)
            if item_se[position] and fit_by_item[i] else item_se[position])
        for position, i in enumerate(item_index)
    }
    # Person fit, and the Real S.E. that follows from it. Winsteps' person table
    # has the same four fit numbers as its item table, and the summary block needs
    # them: a class whose answers already misfit cannot claim a clean reliability.
    person_fit = {j: _person_fit(matrix, betas, thetas, j) for j in range(len(rows))}
    person_real_se = [
        _real_se(person_se[j], person_fit[j].infit if person_fit[j] else None)
        if person_se[j] else None
        for j in range(len(rows))
    ]

    # Totals for the discrimination groups and the correlations.
    totals = [sum(s for s in row.shares.values() if s is not None) for row in rows]
    ordered = sorted(range(len(rows)), key=lambda j: totals[j], reverse=True)
    group = max(1, int(round(len(rows) * GROUP_SHARE))) if rows else 0

    items: list[Item] = []
    for i in range(total_questions):
        qi = str(i)
        qtype = qtypes.get(qi)
        shares = [row.shares.get(i) for row in rows]
        present = [s for s in shares if s is not None]
        answered = len(present)
        share = _mean(present)
        objective = qt.is_objective(qtype)
        marks = float(weights.get(qi, 0) or 0)

        # D: the mean credit of the top 27% against the bottom 27%. Read off the
        # shares, so a part-credited question is judged on the marks it paid.
        def _group_mean(indices: Sequence[int]) -> float:
            values = [shares[j] for j in indices if shares[j] is not None]
            return _mean(values)

        upper = _group_mean(ordered[:group]) if group else 0.0
        lower = _group_mean(ordered[-group:]) if group else 0.0
        discrimination = upper - lower if group else 0.0

        # Point-biserial: this question against the rest of the paper's marks.
        rest = [totals[j] - (shares[j] or 0.0) for j in range(len(rows))]
        paired = [(shares[j], rest[j]) for j in range(len(rows)) if shares[j] is not None]
        point_biserial = _pearson([p[0] for p in paired], [p[1] for p in paired])

        fit = fit_by_item.get(i)
        measure = beta_by_item.get(i)
        se = se_by_item.get(i)
        t_value = p_side = None
        if measure is not None and se:
            t_value = measure / se
            p_side = student_t_two_sided_p(t_value, max(len(present) - 1, 1))

        distractors: tuple[Distractor, ...] = ()
        labels = _option_labels(qtype, key.get(qi))
        if labels:
            correct = _chosen(qt.normalise_key(qtype, key.get(qi)))
            counts: dict[str, int] = {}
            for row in rows:
                picked = _chosen(row.raw.get(i))
                if picked:
                    counts[picked] = counts.get(picked, 0) + 1
            distractors = tuple(
                Distractor(label=label, count=counts.get(label.casefold(), 0),
                           key=label.casefold() == correct)
                for label in labels
            )
        else:
            options = qt.public_options(qtype, key.get(qi))
            if options and options.get("chips"):
                # Drag & drop and ordering share one chip bank, where how many
                # students *picked* a chip is a real distractor question: a chip in
                # the bank that nobody chose was never a plausible answer.
                picked: dict[str, int] = {}
                for row in rows:
                    value = qt.unwrap(row.raw.get(i))
                    for chip in (value if isinstance(value, list) else [value]):
                        if isinstance(chip, str) and chip:
                            picked[chip] = picked.get(chip, 0) + 1
                key_chips = set(qt.drag_order(key.get(qi)))
                distractors = tuple(
                    Distractor(label=chip, count=picked.get(chip, 0),
                               key=chip in key_chips)
                    for chip in options["chips"]
                )

        # "Keyed" means the auto-grader can mark it. An essay is not keyed and
        # never will be — its mark is the teacher's — so the two must not share a
        # word, or the page tells a teacher their essay question has no answer key.
        keyed = bool(objective and qt.key_has_answer(qtype, key.get(qi)))
        # Papers awarded full credit on this question, out of the papers that
        # answered it — the count a criterion-referenced verdict is made of.
        full_credit = sum(1 for s in present if s >= 1.0)
        level_key = af.level(levels.get(qi))
        mastered = (None if not answered or not kkm
                    else full_credit / answered >= kkm / 100.0)

        items.append(Item(
            index=i, qtype=qtype or "", kind=qt.question_kind(qtype), marks=marks,
            objective=objective, keyed=keyed,
            answered=answered, missing=len(rows) - answered,
            share=round(share, 4), pct=round(share * 100, 1),
            full=full_credit,
            discrimination=round(discrimination, 3),
            point_biserial=round(point_biserial, 3),
            measure=round(measure, 3) if measure is not None else None,
            se=round(se, 3) if se else None,
            t=round(t_value, 2) if t_value is not None else None,
            p_value=round(p_side, 4) if p_side is not None else None,
            infit=round(fit.infit, 3) if fit else None,
            infit_z=round(fit.infit_z, 2) if fit else None,
            outfit=round(fit.outfit, 3) if fit else None,
            outfit_z=round(fit.outfit_z, 2) if fit else None,
            se_real=(round(real_se_by_item[i], 3) if real_se_by_item.get(i) else None),
            pt_measure=(round(_pearson(
                [shares[j] if shares[j] is not None else 0.0 for j in range(len(rows))],
                [(thetas[j] or 0.0) for j in range(len(rows))]), 3)
                if beta_by_item.get(i) is not None else None),
            # An essay is handed to `_flag` as if keyed: it cannot be *unkeyed*, so
            # its flag is its verdict on the classical columns instead.
            flag=_flag(share, answered, discrimination, point_biserial,
                       fit.outfit if fit else None, keyed=keyed or not objective),
            distractors=distractors,
            level=level_key.key if level_key else "",
            band=level_key.band if level_key else "",
            mastered=mastered,
        ))

    # Reliability over every question that carries data. An essay's teacher marks
    # belong in the paper's reliability as much as a bubble does, so this is
    # Cronbach's alpha; KR-20 is the same arithmetic *required* to be all-or-nothing,
    # which is why it is reported only for a paper where every question is.
    all_columns: list[list[float]] = []
    dichotomous = True
    for i in range(total_questions):
        column = [row.shares.get(i) for row in rows]
        if not any(v is not None for v in column):
            continue
        filled = [v if v is not None else 0.0 for v in column]
        if any(v not in (0.0, 1.0) for v in filled):
            dichotomous = False
        all_columns.append(filled)
    alpha = _alpha(all_columns)
    kr20 = (alpha if dichotomous and len(all_columns) == total_questions
            and total_questions > 1 else None)

    finite_thetas = [th for th in thetas if th is not None]
    mean_theta = _mean(finite_thetas) if finite_thetas else None
    sd_theta = _sd(finite_thetas) if len(finite_thetas) > 1 else None
    # Winsteps' separation block replaces the hand-rolled reliability that used to
    # live here: same arithmetic, but with the REAL row as well as the MODEL one,
    # and with `strata` — how many statistically distinct levels the spread
    # supports, which is the number a school can actually act on.
    person_stats = separation(thetas, person_se, person_real_se)
    item_stats = separation(betas, item_se, [real_se_by_item[i] for i in item_index])
    person_rel = person_stats.reliability_model
    person_sep = person_stats.separation_model
    item_rel = item_stats.reliability_model
    item_sep = item_stats.separation_model

    total_values = [row.score for row in rows]

    # The reference's two-measure t, applied to the two halves of the class.
    splits: list[Split] = []
    if group >= 2 and len(rows) >= 6:
        for position, i in enumerate(item_index):
            upper_rows = [matrix[j][position] for j in ordered[:group]
                          if matrix[j][position] is not None]
            lower_rows = [matrix[j][position] for j in ordered[-group:]
                          if matrix[j][position] is not None]
            if len(upper_rows) < 2 or len(lower_rows) < 2:
                continue
            up, se_up = _group_measure(upper_rows)
            low, se_low = _group_measure(lower_rows)
            t_value, df, p_side = compare_measures(up, se_up, len(upper_rows),
                                                  low, se_low, len(lower_rows))
            splits.append(Split(
                index=i, upper=round(up, 3), se_upper=round(se_up, 3), n_upper=len(upper_rows),
                lower=round(low, 3), se_lower=round(se_low, 3),
                n_lower=len(lower_rows), t=round(t_value, 2),
                df=round(df, 1) if df != float("inf") else 100000.0, p=round(p_side, 4),
            ))

    people = tuple(
        Person(
            name=row.name,
            raw=sum(1 for v in matrix[j] if v == 1.0),
            possible=sum(1 for v in matrix[j] if v is not None),
            measure=round(thetas[j], 3) if thetas[j] is not None else None,
            se=round(person_se[j], 3) if person_se[j] else None,
            se_real=(round(person_real_se[j], 3) if person_real_se[j] else None),
            infit=round(person_fit[j].infit, 3) if person_fit[j] else None,
            infit_z=round(person_fit[j].infit_z, 2) if person_fit[j] else None,
            outfit=round(person_fit[j].outfit, 3) if person_fit[j] else None,
            outfit_z=round(person_fit[j].outfit_z, 2) if person_fit[j] else None,
            score=row.score,
            extreme=(sum(1 for v in matrix[j] if v is not None) > 0
                     and (sum(1 for v in matrix[j] if v == 1.0) in (0, sum(1 for v in matrix[j] if v is not None)))),
        )
        for j, row in enumerate(rows)
    )

    summary = Summary(
        students=len(rows),
        items=total_questions,
        objective=sum(1 for it in items if it.objective),
        essays=sum(1 for it in items if not it.objective),
        answered_papers=sum(1 for row in rows if any(v is not None for v in row.shares.values())),
        total_mean=round(_mean(total_values), 2),
        total_sd=round(_sd(total_values), 2),
        alpha=round(alpha, 3) if alpha is not None else None,
        kr20=round(kr20, 3) if kr20 is not None else None,
        sem=round(_sd(total_values) * math.sqrt(max(1 - alpha, 0)), 2) if alpha is not None else None,
        person_reliability=round(person_rel, 3) if person_rel is not None else None,
        person_separation=round(person_sep, 2) if person_sep is not None else None,
        item_reliability=round(item_rel, 3) if item_rel is not None else None,
        item_separation=round(item_sep, 2) if item_sep is not None else None,
        mean_measure=round(mean_theta, 3) if mean_theta is not None else None,
        sd_measure=round(sd_theta, 3) if sd_theta is not None else None,
        extremes=sum(1 for p in people if p.extreme),
        person_stats=_rounded(person_stats),
        item_stats=_rounded(item_stats),
    )

    notes = _notes(exam, items)
    return Analysis(
        code=str(exam.get("id") or ""),
        title=str(exam.get("title") or ""),
        summary=summary,
        items=tuple(items),
        people=people,
        splits=tuple(splits),
        notes=notes,
        person_bins=_bins([p.measure for p in people if p.measure is not None]),
        cognitive=_mix(items),
        mastery=_mastery(people, items, kkm),
    )


def _kkm(value: Any) -> int:
    """The school's minimum standard as a whole number 0..100.

    Anything unreadable reads as *not configured* (0) rather than as a default:
    the mastery report is a statement about the school's own criterion, and
    substituting 70 for a value nobody chose would put words in its mouth.
    """
    try:
        kkm = int(float(value))
    except (TypeError, ValueError):
        return 0
    return kkm if 0 < kkm <= 100 else 0


def _mix(items: Sequence[Item]) -> CognitiveMix:
    """The HOTS/MOTS/LOTS mix of a paper, by marks when it has them."""
    counts = {band: 0 for band in af.BANDS}
    marks = {band: 0.0 for band in af.BANDS}
    unset = 0
    unset_marks = 0.0
    total_marks = 0.0
    for item in items:
        total_marks += item.marks
        if not item.band:
            unset += 1
            unset_marks += item.marks
            continue
        counts[item.band] += 1
        marks[item.band] += item.marks
    # Marks are the basis only when the paper actually carries weights; an exam
    # saved before weights existed would otherwise report every band as 0.0%.
    basis = "marks" if total_marks > 0 else "questions"
    return CognitiveMix(
        counts=counts, marks={band: round(value, 2) for band, value in marks.items()},
        unset=unset, unset_marks=round(unset_marks, 2), total=len(items),
        total_marks=round(total_marks, 2), basis=basis)


def _mastery(people: Sequence[Person], items: Sequence[Item], kkm: int) -> Mastery:
    """Who is tuntas and which questions the class has mastered, by the KKM."""
    scores = [person.score for person in people]
    measured = [item for item in items if item.mastered is not None]
    return Mastery(
        kkm=kkm,
        configured=kkm > 0,
        passed=sum(1 for score in scores if score >= kkm) if kkm else 0,
        failed=sum(1 for score in scores if score < kkm) if kkm else 0,
        mean=round(_mean(scores), 2) if scores else None,
        lowest=round(min(scores), 2) if scores else None,
        items_mastered=sum(1 for item in measured if item.mastered),
        items_measured=len(measured),
    )


def _notes(exam: Mapping[str, Any], items: Sequence[Item]) -> tuple[str, ...]:
    """Which rules applied, as keys the page turns into sentences.

    Keys, not prose: this runs on the server, and copy written in Python is copy the
    language toggle cannot reach and the i18n sweep cannot see. The page owns the
    sentences, in both languages.
    """
    notes = ["dichotomous"]
    if qt.partial_credit(exam.get("question_weights") or {}):
        notes.append("partial_credit")
    if any(not item.objective for item in items):
        notes.append("essays_teacher_marked")
    if any(item.answered == 0 and item.keyed for item in items):
        notes.append("unanswered_items")
    if any(item.objective and not item.keyed for item in items):
        # Not the same finding as "nobody answered": the question cannot be marked
        # at all, and the teacher's dashboard already warns about exactly this.
        notes.append("unkeyed_items")
    if any(item.missing for item in items):
        notes.append("missing_not_wrong")
    if any(item.flag == "extreme_easy" or item.flag == "extreme_hard" for item in items):
        notes.append("extreme_items")
    if not _kkm(exam.get("passing_score")) and any(item.answered for item in items):
        # Mastery is reported against a standard the teacher chose; papers that
        # came in beside no KKM cannot be read as "everything passed", and the
        # panel says so instead of printing a verdict nobody set.
        notes.append("kkm_missing")
    if any(item.band for item in items) and any(not item.band for item in items):
        notes.append("levels_partly_set")
    if any(item.measure is not None for item in items):
        # The measurement model, said where the numbers are. A school checking a
        # logit or a fit t against Winsteps needs to know which block is which, what
        # the fit t is standardised by, and what the scale is anchored on — none of
        # which can be inferred from the columns.
        notes.append("scale_anchor")
        notes.append("fit_dof")
        notes.append("model_real")
    return tuple(notes)
