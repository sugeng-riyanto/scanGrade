"""The filed report: the same numbers, arranged the way a school keeps them.

Why a second report exists
--------------------------
`/teacher/analysis/<exam_id>` answers *"what did this paper do?"* for a teacher who
is already looking at the item table: difficulty, discrimination, fit, distractors,
and every figure next to the row it came from.

A school that **files** a result wants a different document. It wants a cover that
says which exam, which class, which session; an executive summary someone can read
in ten seconds; a score distribution and a grade distribution with the bands named;
topic and item tables with the weak rows already picked out; a ranking with
percentiles; and a statement of achievement per learner. That is a different
*arrangement* of the same numbers, not a second measurement — every figure below
comes from `item_analysis`, and none of it is computed a second way. Where a number
has to be derived (a median, a percentile rank, a grade band) it is derived here,
once, so the page, the printout and any later file cannot disagree.

What this report does not claim
-------------------------------
It is laid out like the assessment report a school receives from an external
examination board, and it is **not** one. `BANDS` below is this app's own six-band
vocabulary on the app's own 0–100 scale; an external board reports on that board's
own scale against that board's own grade boundaries, neither of which is
reproducible from a class's raw marks. Two consequences are load-bearing and are
stated on the page rather than left to the reader:

* **The bands are ours, and no board is named.** `Band.note` describes what a mark
  in that band shows, in this app's words. A report that printed some board's
  descriptors next to our marks would be inventing a standard nobody measured
  against — and naming the board would read as a claim of alignment with it, which
  is why the page describes the *shape* of such a report and never its author.
* **The school's own standard is separate and stays visible.** `Mastery.kkm` is the
  KKM the school set; it decides tuntas/belum tuntas and it is the number the report
  quotes for "pass", not a band floor. `descriptives["passing"]` is that KKM, and
  where the exam carries none the report says the standard is missing instead of
  treating every mark as passing.

The same rule applies to the insight card: it is arithmetic over this exam's own
statistics — a finding names the item, the number and the threshold that made it a
finding — and no language model writes a word of it.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence

from app.services import analysis_frameworks
from app.services import question_types as qt

@dataclass(frozen=True)
class Band:
    """One achievement band: the range, the letter, the words, and its tone key.

    `tone` is a *key*, never a colour. The page maps it to compiled Tailwind
    utilities so the theme gate can check the contrast in both themes, and the
    charts read the hexes from `band_palette()` — the same split the item analysis
    already uses for its finding flags.
    """

    key: str
    letter: str
    floor: float
    ceiling: float
    #: Both languages travel: the page's toggle is client-side and the server has
    #: already rendered the numbers.
    name: tuple[str, str]
    note: tuple[str, str]
    tone: str

    @property
    def label(self) -> str:
        """The range as a reader writes it: `85–100`, `40–54`."""
        return f"{_trim(self.floor)}–{_trim(self.ceiling)}"

    def holds(self, mark: float) -> bool:
        """Whether a mark falls in this band."""
        return self.floor <= mark <= self.ceiling


#: The six bands, high to low. See the module docstring: these are this app's
#: words on this app's scale, and the page says so.
BANDS: tuple[Band, ...] = (
    Band("outstanding", "A", 85, 100,
         ("Istimewa", "Outstanding"),
         ("Menguasai seluruh materi yang diujikan dan siap ke tahap berikutnya.",
          "Has command of all the material tested and is ready for the next stage."),
         "emerald"),
    Band("high", "B", 75, 84.99,
         ("Tinggi", "High"),
         ("Menguasai sebagian besar materi; yang tersisa adalah butir tertentu.",
          "Has most of the material; what remains is specific questions."),
         "teal"),
    Band("good", "C", 65, 74.99,
         ("Baik", "Good"),
         ("Menguasai materi inti; perlu latihan pada topik yang teridentifikasi.",
          "Has the core material; needs practice on the topics identified."),
         "sky"),
    Band("developing", "D", 55, 64.99,
         ("Berkembang", "Developing"),
         ("Menguasai sebagian materi; beberapa konsep dasar perlu diulang.",
          "Has part of the material; some foundational ideas need revisiting."),
         "amber"),
    Band("foundational", "E", 40, 54.99,
         ("Dasar", "Foundational"),
         ("Memahami dasar-dasarnya saja; perlu pendampingan menyeluruh.",
          "Has the basics only; needs support across the whole paper."),
         "orange"),
    Band("critical", "F", 0, 39.99,
         ("Perlu Pendampingan", "Needs Support"),
         ("Belum menguasai materi dasar; perlu program perbaikan terarah.",
          "Has not yet secured the basics; needs a targeted intervention."),
         "rose"),
)

#: The band a mark belongs to. `None` marks are not 0 — an unmarked paper is not a
#: failure, so the caller decides and this returns `None` for anything not a mark.
def band_of(mark: float | None) -> Band | None:
    for band in BANDS:
        if mark is not None and band.holds(float(mark)):
            return band
    return None


def band_palette() -> dict[str, str]:
    """The band colours for the canvas, keyed by `Band.tone`.

    Canvas is the one surface the stylesheet cannot reach, so these hexes are the
    one place a colour is written twice. They are chosen to read on a white card,
    which is what every chart here is drawn on, in either theme.
    """
    return {
        "emerald": "#059669", "teal": "#0d9488", "sky": "#0284c7",
        "amber": "#d97706", "orange": "#ea580c", "rose": "#e11d48",
    }


def _trim(value: float) -> str:
    """`85.0 -> "85"`, `84.99 -> "84.99"` — a range a reader would write."""
    return f"{value:g}"


# ── the descriptives ─────────────────────────────────────────────────────────


def mode_of(marks: Sequence[float]) -> list[float]:
    """Every most-frequent mark, sorted, or `[]`.

    *Every* one, not one: a class where 80 and 90 each occur four times has two
    modes, and printing one of them is a silent editorial choice. Rounded to one
    decimal first, because 79.9999 and 80.0 are the same mark to a reader and two
    values to `Counter`.
    """
    if not marks:
        return []
    counts: dict[float, int] = {}
    for mark in marks:
        key = round(float(mark), 1)
        counts[key] = counts.get(key, 0) + 1
    top = max(counts.values())
    if top <= 1:
        # No mark repeats: there is no mode. Saying "the mode is 12.0" because
        # every value occurred once invents a cluster that does not exist.
        return []
    return sorted(key for key, count in counts.items() if count == top)


def percentiles(marks: Sequence[float]) -> dict[str, float | None]:
    """p10/p25/p50/p75/p90 by linear interpolation between ranks.

    The same method as `statistics.quantiles` with `n=100`, spelled out so the
    page's quartiles and its percentiles cannot come from two different
    conventions — a report whose Q1 is not its p25 is a report nobody can check.
    """
    ordered = sorted(float(mark) for mark in marks)
    return {f"p{q}": _percentile(ordered, q) for q in (10, 25, 50, 75, 90)}


def _percentile(ordered: Sequence[float], q: int) -> float | None:
    """One percentile of an already-sorted list, interpolating, rounded to 0.1."""
    if not ordered:
        return None
    if len(ordered) == 1:
        return round(ordered[0], 1)
    position = (len(ordered) - 1) * q / 100.0
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return round(ordered[int(position)], 1)
    weight = position - low
    return round(ordered[low] * (1 - weight) + ordered[high] * weight, 1)


def descriptives(marks: Sequence[float], passing: float | None) -> dict[str, Any]:
    """The executive summary's numbers, all of them from the one list of marks.

    `variance` is the *sample* variance (`ddof=1`), matching `total_sd` in
    `item_analysis` and `stdev` in `analysis_scope`: the class is a sample of the
    cohort the exam is meant to measure, and a population variance would quietly
    report a spread slightly smaller than every other page in this app.
    """
    values = [float(mark) for mark in marks]
    if not values:
        return {
            "count": 0, "mean": None, "median": None, "modes": [], "sd": None,
            "variance": None, "min": None, "max": None, "range": None,
            "q1": None, "q2": None, "q3": None, "iqr": None, "percentiles": {},
            "passing": passing, "pass_count": 0, "pass_pct": None,
            "bands": [], "spread": None,
        }
    ordered = sorted(values)
    quart = percentiles(values)
    sd = round(statistics.stdev(values), 2) if len(values) > 1 else None
    variance = round(statistics.variance(values), 2) if len(values) > 1 else None
    passed = ([mark for mark in values if mark >= passing]
              if passing is not None else [])
    ceiling = max(values)
    return {
        "count": len(values),
        "mean": round(sum(values) / len(values), 1),
        "median": quart["p50"],
        "modes": mode_of(values),
        "sd": sd,
        "variance": variance,
        "min": round(ordered[0], 1),
        "max": round(ceiling, 1),
        "range": round(ceiling - ordered[0], 1),
        "q1": quart["p25"], "q2": quart["p50"], "q3": quart["p75"],
        "iqr": (round(quart["p75"] - quart["p25"], 1)
                if quart["p75"] is not None and quart["p25"] is not None else None),
        "percentiles": quart,
        "passing": passing,
        "pass_count": len(passed),
        # None, not 0, when the exam carries no KKM: "nobody passed" and "there is
        # no standard to pass" are different sentences and only one is true.
        "pass_pct": (round(len(passed) / len(values) * 100) if passing is not None
                     else None),
        "bands": grades(values),
        "spread": round(ceiling - ordered[0], 1),
    }


def grades(marks: Sequence[float]) -> list[dict[str, Any]]:
    """The band distribution: every band, including the empty ones.

    A band with nobody in it is information — a class with no F is what a head of
    department is looking for — so the row stays with a count of 0 rather than
    being dropped from the table.
    """
    values = [float(mark) for mark in marks]
    total = len(values)
    rows: list[dict[str, Any]] = []
    for band in BANDS:
        count = sum(1 for mark in values if band.holds(mark))
        rows.append({
            "key": band.key, "letter": band.letter, "range": band.label,
            "floor": band.floor, "ceiling": band.ceiling,
            "name": band.name, "note": band.note, "tone": band.tone,
            "count": count,
            "pct": round(count / total * 100) if total else 0,
        })
    return rows


# ── the distribution ─────────────────────────────────────────────────────────


def histogram(marks: Sequence[float], width: float = 10.0,
              low: float = 0.0, high: float = 100.0) -> list[dict[str, Any]]:
    """Fixed-width bins from 0 to 100, inclusive at the top.

    Fixed edges rather than `item_analysis._bins`'s data-driven ones: a histogram
    whose first bin starts at 43 because that is the class's worst mark cannot be
    compared with last term's, which is the one thing a filed distribution chart
    is for. The last bin takes 100, which would otherwise fall in a 101st bin.
    """
    if not marks:
        return []
    edges = [low + width * step for step in range(int((high - low) / width))]
    rows = []
    for start in edges:
        end = start + width
        members = [float(m) for m in marks if start <= m < end]
        if end >= high:
            members += [float(m) for m in marks if m >= high]
        rows.append({
            "from": _trim(start), "to": _trim(end),
            "label": f"{_trim(start)}–{_trim(end)}",
            "count": len(members),
            "pct": round(len(members) / len(marks) * 100),
        })
    return rows


def bell_curve(mean: float | None, sd: float | None, bins: Sequence[Mapping[str, Any]],
               low: float = 0.0, high: float = 100.0) -> list[dict[str, float]]:
    """A normal curve sampled at each bin's midpoint and **scaled to the bars**.

    A density curve plotted on a count axis is off by n, which is how a bell curve
    ends up flat along the x-axis beside a histogram at the same scale. Scaling by
    (bin count × bin width × peak height) puts the curve in the same units as the
    bars, so the two are directly readable against each other — and where the curve
    sits above the bars, the class really does have fewer extreme marks than a
    normal distribution would.

    `sd` of 0 (every mark identical) has no curve: a vertical line drawn as a
    "bell" would be a drawing of something that did not happen.
    """
    if mean is None or not sd or len(bins) < 2:
        return []
    points: list[dict[str, float]] = []
    for row in bins:
        centre = (float(row["from"]) + float(row["to"])) / 2
        if centre < low or centre > high:
            continue
        z = (centre - mean) / sd
        points.append({"x": round(centre, 2),
                       "y": round(math.exp(-0.5 * z * z), 6)})
    if not points:
        return []
    # Solve the scale so the curve's own peak matches the tallest bar's count:
    # the curve is a *shape* to compare against the bars, and its absolute height
    # is only meaningful in bar units.
    peak = max(point["y"] for point in points)
    tallest = max((int(row["count"]) for row in bins), default=0)
    scale = (tallest / peak) if peak > 0 else 0.0
    return [{"x": point["x"], "y": round(point["y"] * scale, 2)} for point in points]


# ── the ranking ──────────────────────────────────────────────────────────────

def ranking(people: Sequence[Any]) -> list[dict[str, Any]]:
    """Learners ordered by mark, with a percentile rank and a competition rank.

    Two deliberate choices, both of which a school checks first:

    * **Competition ranking.** Equal marks share a rank and the next mark skips:
      90, 90, 80 ranks 1, 1, 3. A 2 in the second row would be a rank nobody
      holds, and "third of 30" is a claim a parent can check against the table.
    * **Percentile rank is `(below + 0.5 × equal) / n`.** The midpoint convention,
      because the endpoints must not claim what they do not have: with the
      "at or below" formula the weakest learner is at the 3rd percentile of three
      and the strongest is always at 100, which reads as a perfect score.

    The mark is the person's own `score`, which `item_analysis` takes from the
    submission's `final_score` — the moderated mark, already 0–100. It is read
    here and nowhere else derived, so the ranking and the executive summary are
    the same list of numbers; a second reading of `final_score` in this module
    would be a second chance to disagree about a total.
    """
    rows: list[dict[str, Any]] = []
    for person in people:
        pct = round(float(person.score), 1)
        rows.append({
            "id": getattr(person, "student_id", None),
            "name": person.name,
            "raw": person.raw,
            "possible": person.possible,
            "pct": round(pct, 1),
            "measure": person.measure,
            "extreme": person.extreme,
            "band": band_of(pct),
        })
    rows.sort(key=lambda row: (-row["pct"], row["name"]))
    total = len(rows)
    index = 0
    while index < total:
        tied = 1
        while (index + tied < total
               and rows[index + tied]["pct"] == rows[index]["pct"]):
            tied += 1
        rank = index + 1
        for offset in range(tied):
            row = rows[index + offset]
            row["rank"] = rank
            row["tied"] = tied > 1
            # `index` counts the papers ranked *above* this one, so the number of
            # papers below it is what is left over. Passing `index` straight in
            # ranked the strongest learner at the bottom of the class, which is
            # the kind of defect that reads as plausible in the middle of a table.
            row["percentile_rank"] = _percentile_rank(total - index - tied, tied, total)
        index += tied
    return rows


def _percentile_rank(below: int, equal: int, total: int) -> int:
    """`(below + 0.5 × equal) / n`, as a whole number."""
    if total <= 0:
        return 0
    return round((below + 0.5 * equal) / total * 100)


# ── topics ───────────────────────────────────────────────────────────────────

def level_names() -> dict[str, tuple[str, str]]:
    """Level key → its `(id, en)` name, plus the unlabelled row's own name.

    `(id, en)` tuples rather than the framework's own mappings: one macro renders
    every pair on the page, and a mapping here would need a second one. The single
    place a level is *named* — the class table, an individual table and a question
    row all read it, so none of them can call C4 something the builder does not.
    """
    names = {level.key: (level.name["id"], level.name["en"])
             for level in analysis_frameworks.LEVELS}
    names["unlabelled"] = ("Tanpa level", "No level")
    return names


def _level_table(items: Sequence[Any], credit, kkm: float = 0.0) -> list[dict[str, Any]]:
    """One row per cognitive level, from a per-question credit function.

    `credit(item)` answers ``(earned, possible, full, answered)`` for whoever the
    table is about — the class, or one learner. The grouping, the level names and
    the ordering live here, once, so the class page and an individual page cannot
    disagree about which questions a level contains or what that level is called.
    """
    names = level_names()
    unlabelled = names["unlabelled"]
    groups: dict[str, dict[str, Any]] = {}
    for item in items:
        key = item.level or ""
        row = groups.setdefault(key, {
            "key": key or "unlabelled",
            # The level's own name, from the one place this app names levels — a
            # second vocabulary written into this module is how a report ends up
            # calling C4 "Analysing" while the builder calls it "Menganalisis".
            "name": names.get(key, unlabelled),
            "band": getattr(item, "band", "") or "",
            "marks": 0.0, "possible": 0.0, "questions": 0, "full": 0, "answered": 0,
        })
        earned, possible, full, answered = credit(item)
        row["marks"] += earned
        row["possible"] += possible
        row["questions"] += 1
        row["full"] += full
        row["answered"] += answered
    rows = []
    for row in groups.values():
        share = round(row["marks"] / row["possible"] * 100, 1) if row["possible"] else None
        rows.append({
            **row,
            "marks": round(row["marks"], 2),
            "possible": round(row["possible"], 2),
            "share": share,
            "mastered": (None if not row["answered"] or not kkm or share is None
                         else share >= kkm),
        })
    # Labelled levels first in their kisi-kisi order, then the unlabelled row: a
    # topic table that opened with "unlabelled" would read as the paper's main
    # strand being the one nobody described.
    rows.sort(key=lambda row: (row["key"] == "unlabelled", row["key"]))
    return rows


def topics(items: Sequence[Any], kkm: float = 0.0) -> list[dict[str, Any]]:
    """The paper grouped by the cognitive level the teacher recorded.

    This is the honest reading of "topic analysis" for this app: it has no
    syllabus-strand column, and grouping a paper by a topic nobody labelled would
    be inventing the taxonomy. What it *does* have is the kisi-kisi level per
    question, which is a strand of the paper in the sense a teacher means — the
    material a set of questions was written to reach.

    Unlabelled questions are their own row, last, and never folded into a level:
    a paper whose questions carry no level is *unlabelled*, which is a different
    statement from "no HOTS questions".
    """
    return _level_table(
        items,
        lambda item: (float(item.pct) * float(item.marks) / 100.0,
                      float(item.marks), int(item.full), int(item.answered)),
        kkm)


# ── the insights (the "insight card") ────────────────────────────────────────

@dataclass(frozen=True)
class Finding:
    """One insight: what was seen, the number that made it a finding, and the act.

    `evidence` is not decoration. Every finding here is arithmetic over this
    exam's own statistics, and a claim whose evidence is not on the page cannot be
    checked by the teacher reading it — which is how a report starts lying without
    anyone editing it.
    """

    kind: str
    tone: str
    title: tuple[str, str]
    body: tuple[str, str]
    evidence: str


def insights(analysis: Any, stats: Mapping[str, Any]) -> list[Finding]:
    """Findings a teacher can act on, strongest first.

    The thresholds are conventions and each is stated in the finding itself
    (`docs/features/ANALYSIS.md` records the sources):
    difficulty 0.30–0.85, discrimination ≥ 0.20, reliability ≥ 0.70, and a
    distractor nobody chose. A finding that fires only when the number crosses a
    named line is a finding a teacher can argue with; "the AI thinks" is not.
    """
    summary = analysis.summary
    items = list(analysis.items)
    findings: list[Finding] = []

    # Reliability first: it licenses — or refuses to license — every ranking
    # below it, so it is the one finding that has to be read before the others.
    reliability = summary.kr20 if summary.kr20 is not None else summary.alpha
    if reliability is not None:
        strong = reliability >= 0.7
        findings.append(Finding(
            "reliability", "emerald" if strong else "amber",
            ("Keandalan skor", "Score reliability"),
            (("Alpha/KR-20 {value:.2f} — ranking di halaman ini cukup andal untuk "
              "dibandingkan antar murid." if strong else
              "Alpha/KR-20 {value:.2f} di bawah 0.70 — urutan peringkat di halaman "
              "ini masih terlalu banyak dipengaruhi tebakan untuk dipakai "
              "memutuskan apapun tentang seorang murid.").format(value=reliability),
             ("Alpha/KR-20 {value:.2f} — the ranking on this page is dependable "
              "enough to compare learners by." if strong else
              "Alpha/KR-20 {value:.2f} is under 0.70 — the order on this page is "
              "still too influenced by guessing to decide anything about a "
              "learner.").format(value=reliability)),
            f"KR-20 {reliability:.2f} · alpha {summary.alpha:.2f}"
            if summary.alpha is not None else f"KR-20 {reliability:.2f}",
        ))

    answered = [item for item in items if item.answered]
    hardest = min(answered, key=lambda item: item.pct, default=None)
    if hardest is not None and hardest.pct < 30:
        findings.append(Finding(
            "hardest", "rose",
            ("Butir tersulit", "Hardest question"),
            (f"Soal {hardest.index + 1} hanya dijawab benar oleh {hardest.pct:.0f}% "
             "kelas. Periksa apakah konsep ini diajarkan sebelum ujian atau apakah "
             "soalnya sendiri ambigu.",
             f"Question {hardest.index + 1} was answered correctly by only "
             f"{hardest.pct:.0f}% of the class. Check whether the concept was "
             "taught before the exam, or whether the question itself is unclear."),
            f"Q{hardest.index + 1} · p={hardest.pct:.0f}%",
        ))

    negative = [item for item in answered if item.discrimination < 0]
    if negative:
        worst = min(negative, key=lambda item: item.discrimination)
        findings.append(Finding(
            "negative_discrimination", "rose",
            ("Butir yang menyesatkan", "Question that misbehaves"),
            (f"Soal {worst.index + 1} dijawab lebih baik oleh murid lemah daripada "
             f"murid kuat (D={worst.discrimination:.2f}). Biasanya soal ini "
             "menjebak, atau kuncinya salah.",
             f"Question {worst.index + 1} was answered better by the weaker half "
             f"than the stronger half (D={worst.discrimination:.2f}). Usually the "
             "question is a trick, or the key is wrong."),
            f"Q{worst.index + 1} · D={worst.discrimination:.2f}",
        ))

    wasted = _unused_distractors(answered)
    if wasted:
        item = wasted[0][0]
        labels = _named_options(wasted[:4])
        findings.append(Finding(
            "wasted_options", "amber",
            ("Opsi yang tidak dipakai", "Options nobody chose"),
            (f"{len(wasted)} opsi tidak dipilih satu murid pun ({labels}). Opsi itu "
             "tidak menambah daya beda soal, jadi bisa diganti pengecoh yang lebih "
             "masuk akal pada revisi berikutnya.",
             f"{len(wasted)} options were chosen by nobody ({labels}). They add no "
             "discriminating power, so a more plausible distractor can take their "
             "place next time the paper is revised."),
            f"Q{item.index + 1} · {labels}",
        ))

    critical = next((row for row in stats.get("bands", [])
                     if row["key"] == "critical"), None)
    if critical and critical["count"]:
        findings.append(Finding(
            "intervention", "amber",
            ("Perlu program perbaikan", "Intervention needed"),
            (f"{critical['count']} murid ({critical['pct']}%) berada di rentang "
             f"{critical['range']} — belum menguasai materi dasar. Kelompok ini "
             "yang paling terbantu oleh pengajaran ulang sebelum materi berikutnya.",
             f"{critical['count']} learners ({critical['pct']}%) are in the "
             f"{critical['range']} band — the basics are not yet secure. This group "
             "gains most from re-teaching before the next unit."),
            f"{critical['count']} learners in {critical['range']}",
        ))

    mastery = getattr(analysis, "mastery", None)
    if mastery is not None and mastery.configured and mastery.failed:
        findings.append(Finding(
            "below_standard", "sky",
            ("Di bawah KKM", "Below the standard"),
            (f"{mastery.failed} dari {mastery.passed + mastery.failed} murid belum "
             f"mencapai KKM {mastery.kkm}. Rata-rata kelas {mastery.mean} — "
             "selisihnya dengan KKM adalah besarnya jarak yang harus ditutup.",
             f"{mastery.failed} of {mastery.passed + mastery.failed} learners are "
             f"below the school's KKM of {mastery.kkm}. The class mean is "
             f"{mastery.mean} — the gap to the standard is the distance to close."),
            f"KKM {mastery.kkm} · mean {mastery.mean}",
        ))
    elif mastery is not None and not mastery.configured:
        findings.append(Finding(
            "no_standard", "amber",
            ("KKM belum diisi", "No standard set"),
            ("Ujian ini tidak menyimpan KKM, jadi halaman ini tidak menyatakan "
             "siapa pun lulus. Isi KKM di pengaturan ujian supaya laporan bisa "
             "menjawab pertanyaan itu.",
             "This exam stores no KKM, so this page states no pass or fail for "
             "anyone. Set it on the exam to let the report answer that question."),
            "passing_score not set",
        ))
    return findings


def _unused_distractors(items: Sequence[Any]) -> list[tuple[Any, str]]:
    """`(item, option label)` for every plausible option nobody chose.

    A `key` option is never listed: the correct answer may legitimately go
    unchosen by a strong class, and calling that a wasted option would tell a
    teacher to rewrite the answer.
    """
    wasted: list[tuple[Any, str]] = []
    for item in items:
        for option in getattr(item, "distractors", ()) or ():
            if not option.key and not option.count:
                wasted.append((item, option.label))
    return wasted


def _named_options(wasted: Sequence[tuple[Any, str]]) -> str:
    """The wasted options as "Q3 C, Q7 B" — the question *and* the letter.

    Without the question a bare `C, D, E, C` is ambiguous the moment two questions
    waste the same letter, which is the common case: an option letter is not an
    identity, and a teacher cannot act on a list they cannot locate.
    """
    seen: list[str] = []
    for item, label in wasted:
        name = f"Q{item.index + 1} {label}"
        if name not in seen:
            seen.append(name)
    return ", ".join(seen)


# ── statements of achievement ────────────────────────────────────────────────

def statement(row: Mapping[str, Any], total: int, class_mean: float | None,
              kkm: float | None) -> dict[str, tuple[str, str]]:
    """The paragraph a school files for one learner.

    It says four things and no more: where the mark sits, what the band means in
    words, how it compares with the class, and — only when a KKM exists — whether
    the standard was met. Praise beyond the numbers is the one thing a generated
    statement must not add, because nobody signed it.
    """
    band = row["band"] or BANDS[-1]
    rank = f"{row['rank']}/{total}"
    comparison = ""
    if class_mean is not None:
        gap = round(row["pct"] - class_mean, 1)
        comparison = ("di atas rata-rata kelas" if gap > 0 else
                      "di bawah rata-rata kelas" if gap < 0 else
                      "tepat pada rata-rata kelas")
        comparison_en = ("above the class average" if gap > 0 else
                         "below the class average" if gap < 0 else
                         "exactly at the class average")
        gap_text = f"{gap:+.1f}"
    else:
        comparison_en, gap_text = "", ""

    standard = ""
    standard_en = ""
    if kkm is not None:
        met = row["pct"] >= kkm
        standard = ("Mencapai KKM" if met else "Belum mencapai KKM") + f" {_trim(kkm)}"
        standard_en = ("Meets the school's KKM of " if met else
                       "Has not yet met the school's KKM of ") + _trim(kkm)

    head_id = (f"Mencapai {row['pct']:.1f} dari 100 pada {band.name[0]} "
               f"(peringkat {rank}).")
    head_en = (f"Attained {row['pct']:.1f} out of 100 in the "
               f"{band.name[1]} band (rank {rank}).")
    tail_id = " ".join(part for part in (
        band.note[0],
        (f"Nilai ini {comparison} ({gap_text} poin)." if comparison else ""),
        (standard + "." if standard else "")) if part)
    tail_en = " ".join(part for part in (
        band.note[1],
        (f"This mark is {comparison_en} ({gap_text} points)." if comparison_en else ""),
        (standard_en + "." if standard_en else "")) if part)
    return {"head": (head_id, head_en), "body": (tail_id, tail_en)}


# ── the report ───────────────────────────────────────────────────────────────

def report(analysis: Any, exam: Mapping[str, Any] | None = None, *,
           history: Sequence[Mapping[str, Any]] = (),
           generated: datetime | None = None) -> dict[str, Any]:
    """Everything the page renders, of one exam, in one dict.

    Sized for one *class*: the ranking is a class sitting an exam, not a school's
    cohort, which is why nothing here is deferred to a background job. `history`
    is the earlier sittings a trend line needs — `{"label", "date", "mean"}` — and
    an empty history renders the honest empty state rather than a flat line.
    """
    exam = exam or {}
    marks = [float(row["pct"]) for row in ranking(analysis.people)]
    kkm = _kkm(exam.get("passing_score")) or None
    stats = descriptives(marks, kkm)
    bins = histogram(marks)
    rows = ranking(analysis.people)
    total = len(rows)
    strand_rows = topics(analysis.items, kkm or 0.0)

    return {
        "cover": {
            "school": str(exam.get("school_name") or ""),
            "subject": str(exam.get("subject") or ""),
            "title": str(exam.get("title") or analysis.title or ""),
            "exam_code": analysis.code,
            "class_name": str(exam.get("class_name") or ""),
            "teacher": str(exam.get("teacher_name") or ""),
            "session": _session_of(exam),
            "generated": (generated or datetime.now().astimezone()),
            "learners": total,
            "questions": analysis.summary.items,
        },
        "descriptives": stats,
        "histogram": bins,
        "curve": bell_curve(stats["mean"], stats["sd"], bins),
        "grades": stats["bands"],
        "bands": [{
            "key": band.key, "letter": band.letter, "range": band.label,
            "name": band.name, "note": band.note, "tone": band.tone,
            "palette": band_palette()[band.tone],
        } for band in BANDS],
        "palette": band_palette(),
        "ranking": rows,
        "topics": strand_rows,
        # Not `items`: Jinja resolves `report.items` to the dict's own method, and
        # the page then iterates a builtin. The name is a page-level contract, so
        # it is chosen to have no method behind it.
        "item_rows": [{
            "no": item.index + 1,
            "kind": item.kind,
            "pct": item.pct,
            "disc": item.discrimination,
            "point_biserial": item.point_biserial,
            "flag": item.flag,
            "marks": item.marks,
            "answered": item.answered,
            "full": item.full,
            "level": item.level,
            "band": item.band,
            "mastered": item.mastered,
            # The distractor table, as counts and shares. The share is computed
            # against the papers that answered, not the class: a question three
            # learners never reached has an unchoosable option for the other ten.
            "options": [{
                "label": option.label, "count": option.count, "key": option.key,
                "pct": (round(option.count / item.answered * 100)
                        if item.answered else 0),
            } for option in (getattr(item, "distractors", ()) or ())],
        } for item in analysis.items],
        "reliability": {
            "alpha": analysis.summary.alpha,
            "kr20": analysis.summary.kr20,
            "sem": analysis.summary.sem,
            "person_reliability": analysis.summary.person_reliability,
            "item_reliability": analysis.summary.item_reliability,
            "people_measured": sum(1 for person in analysis.people
                                   if person.measure is not None),
        },
        "insights": insights(analysis, stats),
        # The id travels with the statement so the page has a second door into that
        # learner's own report: a school reads the statements as a list of children,
        # and "which one is this" is asked of a paragraph, not of a table row. It is
        # `None` for a paper that could not name a profile, and the page then prints
        # the paragraph without a link rather than one that 404s.
        "statements": [
            {"id": row["id"], "name": row["name"], "rank": row["rank"],
             "pct": row["pct"],
             **statement(row, total, stats["mean"], kkm)}
            for row in rows
        ],
        "progress": _progress(history, stats["mean"]),
        "mastery": {
            "kkm": kkm,
            "configured": bool(kkm),
            "passed": getattr(analysis.mastery, "passed", 0),
            "failed": getattr(analysis.mastery, "failed", 0),
            "mean": getattr(analysis.mastery, "mean", None),
            "gap": getattr(analysis.mastery, "gap", None),
        },
    }


# ── one learner ──────────────────────────────────────────────────────────────

#: What one question came to for one learner. Six outcomes rather than
#: right/wrong, because "wrong" is four different problems to the person reading
#: this page: a blank is not an attempt, an essay the teacher has not marked is
#: not a zero, and a question with no key was never markable at all. Every one of
#: those is a different sentence in a parent meeting.
ANSWER_STATES = {
    "full": ("Benar penuh", "Full credit"),
    "part": ("Sebagian benar", "Partial credit"),
    "none": ("Salah", "Incorrect"),
    "blank": ("Tidak dijawab", "Not answered"),
    "unmarked": ("Belum dikoreksi", "Not yet marked"),
    "unkeyed": ("Kunci belum diisi", "No answer key"),
}

#: The states that *count* for a learner: the question was markable and the
#: learner's paper was in a position to earn something. `unmarked` and `unkeyed`
#: are excluded from a learner's level shares, because a denominator that includes
#: a question nobody could have scored reports a weakness that is not theirs.
COUNTED_STATES = ("full", "part", "none", "blank")


def _state_of(qtype: Any, key: Any, answer: Any, share: float | None) -> str:
    """Why a question paid what it paid, for one learner.

    Mirrors `item_analysis._responses` exactly, using the same predicates the
    grader uses (`is_objective`, `key_has_answer`, `has_answer`), so the reason
    this page gives and the credit the statistics were built from cannot disagree.
    The share stays authoritative — this only names the case.
    """
    if not qt.is_objective(qtype):
        return "unmarked" if share is None else (
            "full" if share >= 1.0 else ("part" if share > 0 else "none"))
    if not qt.key_has_answer(qtype, key):
        return "unkeyed"
    if not qt.has_answer(qtype, answer):
        return "blank"
    if share is None:
        return "blank"
    return "full" if share >= 1.0 else ("part" if share > 0 else "none")


def learner(analysis: Any, exam: Mapping[str, Any] | None = None,
            student_id: str | None = None, *, with_key: bool = True,
            generated: datetime | None = None) -> dict[str, Any] | None:
    """One learner's own report, or ``None`` for a learner who is not in it.

    Addressed by **id**, never by name: two learners in one class can share a full
    name, and a page that picked the first match would confidently describe the
    wrong child. A paper whose submission carried no id gets no individual report,
    which is the honest outcome rather than a guess.

    Every credit here is the learner's own `shares` from `item_analysis` — the
    same numbers the item statistics were computed from — so this page cannot
    grade the sheet a second way. What it adds is the *reason* a question paid
    what it paid, the level breakdown for this learner beside the class's own, and
    what to do next.

    ``with_key=False`` drops the answer key from the payload entirely. A shared
    link renders that copy: the key is what one learner's page teaches the next
    learner who opens it, and an exam can still be open for the rest of the class.
    """
    exam = exam or {}
    sid = str(student_id) if student_id else ""
    if not sid:
        return None
    people = list(analysis.people)
    person = next((p for p in people if getattr(p, "student_id", None) == sid), None)
    if person is None:
        return None

    rows = ranking(people)
    row = next((r for r in rows if r["id"] == sid), None)
    if row is None:  # pragma: no cover - ranking() is built from the same people
        return None
    marks = [float(r["pct"]) for r in rows]
    kkm = _kkm(exam.get("passing_score")) or None
    stats = descriptives(marks, kkm)
    class_mean = stats["mean"]
    qtypes = exam.get("question_types") or {}
    keys = exam.get("answer_key") or {}

    names = level_names()
    questions: list[dict[str, Any]] = []
    for index, item in enumerate(analysis.items):
        qtype = qtypes.get(str(index))
        key = keys.get(str(index))
        share = person.shares[index] if index < len(person.shares) else None
        answer = person.answers[index] if index < len(person.answers) else None
        state = _state_of(qtype, key, answer, share)
        questions.append({
            "no": item.index + 1,
            "kind": qt.kind_label(qtype),
            "level_name": names.get(item.level or "unlabelled", names["unlabelled"]),
            "objective": bool(qt.is_objective(qtype)),
            "level": item.level or "",
            "band": getattr(item, "band", "") or "",
            "marks": round(float(item.marks), 2),
            "state": state,
            "state_name": ANSWER_STATES[state],
            "share": None if share is None else round(float(share), 3),
            # What this question *contributed to the total*, which is the one thing
            # the reader can check: a question left unanswered paid nothing, so it
            # prints 0, and a question nobody could score prints `None` because it
            # is not in the denominator either. Showing `—` for a blank while the
            # totals counted it as zero is how the column stops adding up to the
            # number beside it, and a family that adds the column up is doing the
            # one verification this page invites.
            "earned": (round(float(share or 0.0) * float(item.marks), 2)
                       if state in COUNTED_STATES else None),
            "answered": (qt.describe_answer(qtype, answer) if answer is not None else ""),
            "key": (qt.describe_answer(qtype, key) if with_key else ""),
            "class_pct": item.pct,
            "class_full": item.full,
            "flag": getattr(item, "flag", "") or "",
        })

    counts = {state: sum(1 for q in questions if q["state"] == state)
              for state in ANSWER_STATES}
    lost = sum(1 for q in questions
               if q["state"] in ("part", "none", "blank"))
    # Both totals are sums of the numbers the marks column *prints*, not of the
    # unrounded per-question marks they were rounded from. The difference is one
    # hundredth on a paper whose questions are 16.666… each, and it is the one
    # verification this page invites — a reader who adds the column up and finds a
    # different number in the card above has been told two answers by one page.
    credited = round(sum((q["earned"] or 0.0) for q in questions
                         if q["state"] in COUNTED_STATES), 2)
    possible = round(sum(q["marks"] for q in questions
                         if q["state"] in COUNTED_STATES), 2)

    def credit_of(item):
        """This learner's `(earned, possible, full, answered)` for one question.

        Read off the row above rather than recomputed: the level table, the totals
        card and the marks column then describe one number three ways instead of
        three numbers that are nearly the same. A blank counts against the learner
        here, which is the opposite of the class table — the class's share is a
        mean over the papers that answered, and a parent reading their own child's
        page is owed the denominator their child actually faced.
        """
        row = questions[item.index]
        if row["state"] not in COUNTED_STATES:
            return 0.0, 0.0, 0, 0
        return (row["earned"] or 0.0, row["marks"],
                1 if row["state"] == "full" else 0, 1)

    levels = _level_table(analysis.items, credit_of, kkm or 0.0)
    class_levels = {r["key"]: r for r in topics(analysis.items, kkm or 0.0)}
    for level in levels:
        peer = class_levels.get(level["key"], {})
        level["class_share"] = peer.get("share")
        level["class_full"] = peer.get("full")

    return {
        "who": {**row, "measure": row.get("measure"),
                "band_detail": band_of(float(row["pct"])),
                "student_id": sid},
        "class": {
            "learners": len(rows),
            "mean": class_mean,
            "median": stats["median"],
            "sd": stats["sd"],
            "kkm": kkm,
            "configured": bool(kkm),
        },
        "gap": _gap(row["pct"], class_mean),
        "questions": questions,
        "levels": levels,
        "states": dict(ANSWER_STATES),
        "counts": {**counts, "lost": lost, "questions": len(questions)},
        # One denominator, not two. `Person.raw/possible` counts *items* under the
        # dichotomous calibration; showing it beside a marks total would give the
        # page two numbers that do not add up to each other, which is exactly how
        # a reader stops trusting both.
        "totals": {"credited": credited, "possible": possible},
        "strengths": _strengths(levels, questions),
        "weaknesses": _weaknesses(levels, questions, row["pct"]),
        "remediation": _remediation(levels, questions, kkm),
        "statement": statement(row, len(rows), class_mean, kkm),
        "generated": (generated or datetime.now().astimezone()),
        "with_key": bool(with_key),
    }


def _gap(score: float, class_mean: float | None) -> dict[str, Any]:
    """This learner against the class mean, in points, and never as a rank claim."""
    if class_mean is None:
        return {"points": None, "text": None}
    gap = round(score - class_mean, 1)
    if abs(gap) < 0.05:
        text = ("tepat pada rata-rata kelas", "exactly at the class average")
    elif gap > 0:
        text = (f"{gap:+.1f} poin di atas rata-rata kelas",
                f"{gap:+.1f} points above the class average")
    else:
        text = (f"{gap:+.1f} poin di bawah rata-rata kelas",
                f"{gap:+.1f} points below the class average")
    return {"points": gap, "text": text}


def _strengths(levels: Sequence[Mapping[str, Any]],
               questions: Sequence[Mapping[str, Any]]) -> list[tuple[str, str]]:
    """What this learner did well, each line carrying the number that shows it."""
    out: list[tuple[str, str]] = []
    # Labelled levels only: "the strongest level" is a claim about the paper's
    # kisi-kisi, and the unlabelled row is the absence of one. A class whose paper
    # was never labelled gets no level sentence at all rather than "Level Tanpa
    # level is the strongest", which says nothing in the voice of a finding.
    scored = [level for level in levels
              if level.get("share") is not None and level["key"] != "unlabelled"]
    if scored:
        best = max(scored, key=lambda level: level["share"])
        out.append((
            f"Level {best['name'][0]} adalah yang terkuat: {best['share']:.0f}% "
            f"dari {_trim(best['possible'])} markah pada level ini.",
            f"{best['name'][1]} is the strongest level: {best['share']:.0f}% of the "
            f"{_trim(best['possible'])} marks in it."))
    full = [q["no"] for q in questions if q["state"] == "full"]
    if full:
        shown = ", ".join(f"Q{no}" for no in full[:8])
        more = "" if len(full) <= 8 else f" (+{len(full) - 8})"
        out.append((
            f"Benar penuh pada {len(full)} soal ({shown}{more}).",
            f"Full credit on {len(full)} question(s) ({shown}{more})."))
    if not out:
        # An empty list on a page about a learner reads as a defect in the page.
        out.append((
            "Belum ada satu pun soal yang diberi nilai penuh pada ujian ini.",
            "No question on this exam was awarded full credit yet."))
    return out


def _weaknesses(levels: Sequence[Mapping[str, Any]],
                questions: Sequence[Mapping[str, Any]],
                score: float) -> list[tuple[str, str]]:
    """What to work on, split by what the class managed and what it did not.

    The two are different findings and the page keeps them apart: a question the
    class found easy and this learner missed is a gap in *this* learner; a question
    the whole class found hard is a gap in the teaching, and telling a family it is
    their child's problem would be false.
    """
    out: list[tuple[str, str]] = []
    scored = [level for level in levels
              if level.get("share") is not None and level["key"] != "unlabelled"]
    if scored:
        worst = min(scored, key=lambda level: level["share"])
        peer = worst.get("class_share")
        against = (f" (kelas {peer:.0f}%)" if peer is not None else "")
        against_en = (f" (class {peer:.0f}%)" if peer is not None else "")
        out.append((
            f"Level {worst['name'][0]} paling lemah: {worst['share']:.0f}%"
            f"{against}.",
            f"{worst['name'][1]} is the weakest level: {worst['share']:.0f}%"
            f"{against_en}."))
    missed = [q for q in questions
              if q["state"] in ("part", "none", "blank") and q["class_pct"] >= 60]
    if missed:
        shown = ", ".join(f"Q{q['no']} ({q['class_pct']:.0f}% kelas)" for q in missed[:6])
        out.append((
            f"{len(missed)} soal yang sebagian besar kelas bisa, belum dikuasai: "
            f"{shown}.",
            f"{len(missed)} question(s) most of the class managed were not secured: "
            f"{shown}."))
    if not out:
        lost = [q for q in questions if q["state"] in ("part", "none", "blank")]
        unattempted = sum(1 for q in questions if q["state"] == "blank")
        if not lost:
            out.append((
                "Tidak ada markah yang hilang pada soal yang sudah bisa dinilai.",
                "No marks were lost on the questions that could be scored."))
        else:
            # Every question this learner missed was missed by the class too. That
            # is a different finding from a personal gap, and calling it one would
            # be the page taking the class's problem out on a child.
            note = (f", {unattempted} di antaranya tidak dijawab" if unattempted else "")
            note_en = (f", {unattempted} of them left unanswered" if unattempted else "")
            out.append((
                f"{len(lost)} soal kehilangan markah{note}, dan tidak satu pun "
                "termasuk yang mudah bagi kelas — kelemahannya ada pada level "
                "kelas, bukan pada murid ini.",
                f"{len(lost)} question(s) lost marks{note_en}, and none of them was "
                "easy for the class — the weakness is at class level, not this "
                "learner's."))
    return out


def _remediation(levels: Sequence[Mapping[str, Any]],
                 questions: Sequence[Mapping[str, Any]],
                 kkm: float | None) -> list[tuple[str, str]]:
    """What to do next, derived from this page's own numbers and nothing else."""
    out: list[tuple[str, str]] = []
    missed = [q for q in questions
              if q["state"] in ("part", "none", "blank") and q["class_pct"] >= 60]
    if missed:
        nos = ", ".join(f"{q['no']}" for q in missed[:6])
        out.append((
            f"Kerjakan ulang soal {nos} bersama guru: kelas rata-rata sudah bisa, "
            "jadi konsepnya sudah diajarkan — yang perlu diperbaiki cara "
            "mengerjakannya.",
            f"Re-work question(s) {nos} with the teacher: the class average already "
            "reaches them, so the teaching landed and what needs work is the "
            "approach."))
    hard = [q for q in questions
            if q["state"] in ("part", "none") and q["class_pct"] < 60]
    if hard:
        nos = ", ".join(f"{q['no']}" for q in hard[:6])
        out.append((
            f"Soal {nos} sulit bagi seluruh kelas; itu bahan ajar ulang untuk "
            "kelas, bukan kekurangan pribadi.",
            f"Question(s) {nos} were hard for the whole class; that is material to "
            "re-teach to everyone, not a personal shortfall."))
    scored = [level for level in levels
              if level.get("share") is not None and level["key"] != "unlabelled"]
    if kkm and scored:
        under = [level for level in scored if level["share"] < kkm]
        if under:
            names = ", ".join(level["name"][0] for level in under[:3])
            names_en = ", ".join(level["name"][1] for level in under[:3])
            out.append((
                f"Level di bawah KKM {_trim(kkm)}: {names}. Latihan tambahan di "
                "level itu paling cepat menaikkan nilai.",
                f"Levels below the school's KKM of {_trim(kkm)}: {names_en}. Extra "
                "practice there raises the mark fastest."))
    if not out:
        out.append((
            "Tidak ada kelemahan yang menonjol pada ujian ini.",
            "No pronounced weakness on this exam."))
    return out


def _progress(history: Sequence[Mapping[str, Any]],
              current: float | None) -> dict[str, Any]:
    """Earlier sittings plus this one, and the change between the last two.

    `change` compares with the *previous* sitting, not with the series mean: a
    teacher asking "did the class improve" is asking about the two papers in
    front of them. It is `None` when there is nothing to compare against, which is
    a different statement from "no change" and is drawn as one.
    """
    points = [{
        "label": str(row.get("label") or ""),
        "date": str(row.get("date") or ""),
        "mean": (None if row.get("mean") is None else round(float(row["mean"]), 1)),
    } for row in history]
    if current is not None:
        points.append({"label": "", "date": "", "mean": round(current, 1)})
    scored = [point for point in points if point["mean"] is not None]
    change = None
    if len(scored) >= 2:
        change = round(scored[-1]["mean"] - scored[-2]["mean"], 1)
    return {"points": points, "change": change, "compared": len(scored) - 1}


def _session_of(exam: Mapping[str, Any]) -> str:
    """The sitting, as a school writes it: month and year of the exam's own date.

    An exam may be scheduled, so its own `end_at`/`created_at` is preferred over
    the moment the report is rendered — a report filed in September about a May
    paper has to say May.
    """
    raw = exam.get("end_at") or exam.get("created_at") or ""
    try:
        moment = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return str(raw)[:10]
    return moment.strftime("%B %Y")


def _kkm(value: Any) -> int:
    """The school's standard, or 0 for "not set".

    The same reading as `item_analysis._kkm`, on purpose: a page that called a
    mark passing at 70 while the mastery panel used 75 would be reporting two
    different schools.
    """
    try:
        kkm = int(float(value))
    except (TypeError, ValueError):
        return 0
    return kkm if 0 < kkm <= 100 else 0
