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

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

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


def _zstd(mean_square: float, df: float) -> float:
    """A mean square as a standardised t (Wilson–Hilferty cube-root approximation).

    This is the number Winsteps prints as a fit ZSTD: how far a mean square sits
    from 1.0 once its own spread is taken into account. Labeled as an approximation
    on the page rather than sold as Winsteps' own formula.
    """
    if df < 2 or mean_square <= 0:
        return 0.0
    third = 1.0 - 2.0 / (9.0 * df)
    return (mean_square ** (1.0 / 3.0) - third) / math.sqrt(2.0 / (9.0 * df))


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

    Both are anchored on 0 logits — the paper's average difficulty — which is what
    makes each item's `t = b_i / SE_i` a test against "as hard as this paper's
    average question". A student with a perfect or zero raw score has no finite
    maximum-likelihood estimate, so gets the conventional third-of-a-case correction
    and is flagged as extreme on the person table rather than quietly dropped.
    """
    people, items = len(rows), item_count
    if not people or not items:
        return [], [], [], []

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
         thetas: Sequence[float | None], index: int) -> tuple[float, float, float, float] | None:
    """Infit and outfit mean squares for one item, and their standardised values."""
    residuals: list[tuple[float, float]] = []     # (z², variance)
    for j, row in enumerate(rows):
        value = row[index]
        theta = thetas[j]
        if value is None or theta is None:
            continue
        expect = _logistic(theta - betas[index])
        variance = expect * (1.0 - expect)
        if variance <= 1e-12:
            continue
        residuals.append(((value - expect) ** 2, variance))
    if not residuals:
        return None
    info = sum(v for _, v in residuals)
    if info <= 0:
        return None
    outfit = sum(z2 / v for z2, v in residuals) / len(residuals)
    infit = sum(z2 for z2, _ in residuals) / info
    return infit, _zstd(infit, info), outfit, _zstd(outfit, len(residuals))


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
    flag: str = "ok"
    distractors: tuple[Distractor, ...] = ()


@dataclass(frozen=True)
class Person:
    name: str
    raw: int
    possible: int
    measure: float | None
    se: float | None
    score: float
    extreme: bool


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

        items.append(Item(
            index=i, qtype=qtype or "", kind=qt.question_kind(qtype), marks=marks,
            objective=objective, keyed=keyed,
            answered=answered, missing=len(rows) - answered,
            share=round(share, 4), pct=round(share * 100, 1),
            full=sum(1 for s in present if s >= 1.0),
            discrimination=round(discrimination, 3),
            point_biserial=round(point_biserial, 3),
            measure=round(measure, 3) if measure is not None else None,
            se=round(se, 3) if se else None,
            t=round(t_value, 2) if t_value is not None else None,
            p_value=round(p_side, 4) if p_side is not None else None,
            infit=round(fit[0], 3) if fit else None,
            infit_z=round(fit[1], 2) if fit else None,
            outfit=round(fit[2], 3) if fit else None,
            outfit_z=round(fit[3], 2) if fit else None,
            pt_measure=(round(_pearson(
                [shares[j] if shares[j] is not None else 0.0 for j in range(len(rows))],
                [(thetas[j] or 0.0) for j in range(len(rows))]), 3)
                if i in beta_by_item else None),
            # An essay is handed to `_flag` as if keyed: it cannot be *unkeyed*, so
            # its flag is its verdict on the classical columns instead.
            flag=_flag(share, answered, discrimination, point_biserial,
                       fit[2] if fit else None, keyed=keyed or not objective),
            distractors=distractors,
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
    person_mse = _mean([se * se for se in person_se if se]) if any(person_se) else None
    person_rel = person_sep = None
    if sd_theta and person_mse is not None and sd_theta ** 2 > 0:
        true_var = max(sd_theta ** 2 - person_mse, 0.0)
        person_rel = true_var / (sd_theta ** 2)
        # A separation needs an error to divide by. A set of papers that fit the
        # model exactly has none, and the ratio is then not "infinite reliability"
        # but unanswerable — reported as a dash rather than as 1.3e4.
        if person_mse > 1e-12:
            person_sep = math.sqrt(true_var / person_mse)

    finite_betas = [b for b in betas]
    item_mse = _mean([se * se for se in item_se if se]) if any(item_se) else None
    sd_beta = _sd(finite_betas) if len(finite_betas) > 1 else None
    item_rel = item_sep = None
    if sd_beta and item_mse and sd_beta ** 2 > 0:
        true_item_var = max(sd_beta ** 2 - item_mse, 0.0)
        item_rel = true_item_var / (sd_beta ** 2)
        if item_mse > 1e-12:
            item_sep = math.sqrt(true_item_var / item_mse)

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
    return tuple(notes)
