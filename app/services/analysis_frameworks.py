"""The four ways a school reads one set of answers, named and kept apart.

One exam's answers can be read through more than one assessment framework, and
they answer *different questions*:

* **Classical test theory (CTT)** — how did this question behave on this class?
  Difficulty P, discrimination D, point-biserial r, KR-20. The numbers Anates,
  Iteman and every school's item-analysis form print.
* **Rasch / item response theory (IRT)** — what is the *measure*, on a scale
  that does not move with the class? Logits, item and person fit, separation and
  strata. The tables Winsteps prints, and the app already computes them.
* **Cognitive level (HOTS/MOTS/LOTS)** — is this paper measuring what the
  kisi-kisi said it would? Bloom's levels, grouped the way Indonesian schools
  group them: C1–C2 lower order, C3–C4 middle, C5–C6 higher order.
* **Criterion-referenced mastery (KKM)** — against the school's own minimum
  standard, who is tuntas and which questions the class has mastered. This is
  the framework a *school* is judged by, and the one no item statistic answers.

Why they get their own module rather than a `if framework == ...` in a route:
a framework is a promise about what a reader is looking at. Every panel, every
legend, all three documents and the shared copy have to make the same promise,
and the honest half of the promise is what each framework *cannot* answer — CTT
cannot say whether a question is HOTS, and a logit is not a mark out of 100.
So the identity (`name`, `question`, `measures`, `when`, `reference`) and the
panel list live here once, and the page and the documents read them from here.

Nothing in this module computes anything or touches the database: it is the
vocabulary. The numbers arrive from `item_analysis` (which does the arithmetic)
and are attached to the frameworks by the routes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

#: The default a report falls back to: the framework every school already reads,
#: and the only one whose numbers need no key to interpret.
DEFAULT = "ctt"


@dataclass(frozen=True)
class Framework:
    """One assessment framework, as the page, the documents and the legends
    describe it.

    ``question`` is the sentence a reader needs first — *what is this report
    answering* — and ``measures`` names what it publishes, in the order the page
    should present them. ``not_for`` is the honest half: the questions this
    framework does not answer, which is what stops a reader treating a logit as
    a mark.
    """

    key: str
    name: Mapping[str, str]
    question: Mapping[str, str]
    measures: tuple[str, ...]
    not_for: Mapping[str, str]
    when: Mapping[str, str]
    reference: Mapping[str, str]
    #: The panels the page shows for this framework. Every key must exist as a
    #: `data-panel` marker in `teacher/analysis.html`; a promise about a panel
    #: that does not render is a blank page with no error.
    panels: tuple[str, ...]

    def shows(self, panel: str) -> bool:
        return panel in self.panels

    def words(self, lang: str = "id") -> dict[str, str]:
        """The identity in one language, for a document that is already in it."""
        half = "en" if lang == "en" else "id"
        return {
            "key": self.key,
            "name": self.name[half],
            "question": self.question[half],
            "measures": " · ".join(self.measures),
            "not_for": self.not_for[half],
            "when": self.when[half],
            "reference": self.reference[half],
        }


#: The panels a framework can show. Named here rather than written as literals
#: in four places in the template and again in each document.
#: The blocks a framework can show on the analysis page. Every one of these is a
#: `data-panel` attribute in `teacher/analysis.html`, and the guard test checks
#: both directions: a promise about a block that does not render is a blank page
#: with no error, and a block nobody claims is a block nobody reads.
#:
#: The per-student table is not here on purpose. It exists in the three documents
#: and not on this page — the page answers "how did the questions behave", and
#: the roster answers "who", which is the results page's job — so it is not a
#: panel a framework can promise.
PANELS = (
    "summary",    # the metric cards: students, items, mean, alpha
    "separation",  # person and item reliability, separation, strata (Rasch)
    "items",      # the item table: P, D, r, b, fit, level, mastery
    "options",    # where the options landed, keyed option withheld when shared
    "splits",     # upper vs lower group, two-measure t
    "map",        # the item map: difficulty against discrimination
    "difficulty",  # the same difficulties on the logit scale (Rasch)
    "ability",    # the class's own ability distribution
    "cognitive",  # the HOTS/MOTS/LOTS mix against the kisi-kisi
    "mastery",    # criterion-referenced mastery against the school's KKM
    "notes",      # how the numbers were computed, and their limits
)

FRAMEWORKS: tuple[Framework, ...] = (
    Framework(
        key="ctt",
        name={"id": "Teori Tes Klasik (CTT)", "en": "Classical test theory (CTT)"},
        question={
            "id": "Bagaimana tiap soal berperilaku pada kelas ini?",
            "en": "How did each question behave on this class?",
        },
        measures=("P", "D", "r", "KR-20"),
        not_for={
            "id": "Tidak bisa memisahkan sulitnya soal dari pandainya kelas, dan "
                  "tidak tahu soal ini menuntut berpikir tingkat apa.",
            "en": "It cannot separate a question's difficulty from this class's "
                  "ability, and it says nothing about the level of thinking the "
                  "question demands.",
        },
        when={
            "id": "Paling umum dipakai sekolah, dan hasilnya mudah dibandingkan "
                  "antar kelas karena rumusnya sudah baku.",
            "en": "What most schools already use, and easy to compare across "
                  "classes because the formulas are standard.",
        },
        reference={"id": "Anates / Iteman; KR-20 (Kuder–Richardson)",
                   "en": "Anates / Iteman; KR-20 (Kuder–Richardson)"},
        # The item map is the *classical* plot — difficulty against discrimination,
        # both of which CTT publishes — so it belongs here too. What a classical
        # report does not show is the logit scale: `separation` and `difficulty`
        # are statements about a measure this framework does not use.
        panels=("summary", "items", "options", "splits", "map", "ability", "notes"),
    ),
    Framework(
        key="rasch",
        name={"id": "Rasch / Teori Respons Butir (IRT)",
              "en": "Rasch / item response theory (IRT)"},
        question={
            "id": "Berapa ukuran soal dan murid pada satu skala yang tidak "
                  "bergerak bersama kelas?",
            "en": "What is each question's and each student's measure on one "
                  "scale that does not move with the class?",
        },
        measures=("b (logit)", "SE", "Infit", "Outfit", "Separation", "Strata"),
        not_for={
            "id": "Logit bukan nilai: skala ini mengukur jarak antar soal, bukan "
                  "berapa nilai yang layak diberikan kepada murid.",
            "en": "A logit is not a mark: this scale measures the distance "
                  "between questions, not what a student deserves out of 100.",
        },
        when={
            "id": "Dipilih ketika sekolah ingin membandingkan soal lintas kelas "
                  "atau lintas tahun, dan ketika kualitas soal (fit) perlu diperiksa.",
            "en": "Chosen when a school wants to compare questions across classes "
                  "or years, and when the quality of the questions (fit) has to "
                  "be checked.",
        },
        reference={"id": "Rasch (1960); Winsteps — Wright & Masters",
                   "en": "Rasch (1960); Winsteps — Wright & Masters"},
        panels=("summary", "separation", "items", "map", "difficulty", "ability",
                "notes"),
    ),
    Framework(
        key="cognitive",
        name={"id": "Tingkat kognitif (HOTS/MOTS/LOTS)",
              "en": "Cognitive level (HOTS/MOTS/LOTS)"},
        question={
            "id": "Apakah soal yang keluar sesuai kisi-kisi: berapa banyak yang "
                  "menuntut berpikir tingkat tinggi?",
            "en": "Does the paper match its own kisi-kisi: how much of it demands "
                  "higher-order thinking?",
        },
        measures=("C1–C6 per soal", "Porsi LOTS/MOTS/HOTS"),
        not_for={
            "id": "Tidak mengukur mutu soal. Soal HOTS yang ditulis buruk tetap "
                  "soal buruk — nilainya ada di tabel butir, bukan di sini.",
            "en": "It does not measure question quality. A badly written HOTS "
                  "question is still a bad question — that verdict lives in the "
                  "item table, not here.",
        },
        when={
            "id": "Dipakai saat menyusun atau memeriksa kisi-kisi, dan saat "
                  "sekolah harus melaporkan porsi HOTS dalam penilaiannya.",
            "en": "Used when building or checking the kisi-kisi, and when a "
                  "school has to report the HOTS share of its assessment.",
        },
        reference={"id": "Taksonomi Bloom (revisi Anderson & Krathwohl, 2001)",
                   "en": "Bloom's taxonomy (Anderson & Krathwohl revision, 2001)"},
        panels=("cognitive", "items", "options", "notes"),
    ),
    Framework(
        key="mastery",
        name={"id": "Ketuntasan belajar (KKM)",
              "en": "Criterion-referenced mastery (KKM)"},
        question={
            "id": "Siapa yang sudah tuntas dan soal mana yang belum dikuasai "
                  "kelas, menurut standar sekolah sendiri?",
            "en": "Who has met the standard and which questions the class has "
                  "not mastered, by the school's own criterion?",
        },
        measures=("KKM", "Tuntas / belum tuntas", "Penguasaan per soal"),
        not_for={
            "id": "Bukan peringkat. Ketuntasan bergantung pada KKM yang dipilih "
                  "sekolah; mengubah KKM mengubah jawabannya tanpa satu jawaban "
                  "murid pun berubah.",
            "en": "It is not a ranking. Mastery depends on the KKM the school "
                  "chose; changing the KKM changes the answer without a single "
                  "student's answer changing.",
        },
        when={
            "id": "Dipakai untuk keputusan remedi dan pelaporan ketuntasan, dan "
                  "untuk memeriksa soal mana yang menahan kelas.",
            "en": "Used for remedial decisions and mastery reporting, and to see "
                  "which questions are holding the class back.",
        },
        reference={"id": "Penilaian acuan kriteria (criterion-referenced)",
                   "en": "Criterion-referenced assessment"},
        panels=("mastery", "items", "notes"),
    ),
)

BY_KEY: dict[str, Framework] = {framework.key: framework for framework in FRAMEWORKS}


def resolve(key: str | None) -> Framework:
    """The framework a request asked for, or the default one.

    A query string is text from the outside, so an unknown or missing key lands
    on the default rather than on an error: a report that 500s because somebody
    edited a URL is worse than a report that shows the framework most readers
    expected anyway.
    """
    return BY_KEY.get((key or "").strip().lower(), BY_KEY[DEFAULT])


# ── the cognitive vocabulary ─────────────────────────────────────────────────
#
# Six levels, three bands. The bands are the ones a kisi-kisi is written in —
# LOTS/MOTS/HOTS — and the mapping is the one Indonesian schools use, so it is
# stated once here and never re-derived: a level that maps to two bands would
# make the mix panel argue with itself.

@dataclass(frozen=True)
class Level:
    key: str
    band: str
    name: Mapping[str, str]
    verb: Mapping[str, str]


LEVELS: tuple[Level, ...] = (
    Level("c1", "lots", {"id": "C1 Mengingat", "en": "C1 Remembering"},
          {"id": "menyebutkan, mengenali", "en": "name, recognise"}),
    Level("c2", "lots", {"id": "C2 Memahami", "en": "C2 Understanding"},
          {"id": "menjelaskan, menafsirkan", "en": "explain, interpret"}),
    Level("c3", "mots", {"id": "C3 Menerapkan", "en": "C3 Applying"},
          {"id": "menerapkan pada kasus rutin", "en": "apply to a routine case"}),
    Level("c4", "mots", {"id": "C4 Menganalisis", "en": "C4 Analysing"},
          {"id": "membandingkan, menghubungkan", "en": "compare, connect"}),
    Level("c5", "hots", {"id": "C5 Mengevaluasi", "en": "C5 Evaluating"},
          {"id": "menilai, mengkritik dengan alasan", "en": "judge, justify"}),
    Level("c6", "hots", {"id": "C6 Mencipta", "en": "C6 Creating"},
          {"id": "merancang, menyusun", "en": "design, compose"}),
)

BANDS: tuple[str, ...] = ("lots", "mots", "hots")

BAND_NAMES: dict[str, Mapping[str, str]] = {
    "lots": {"id": "LOTS — tingkat rendah", "en": "LOTS — lower order"},
    "mots": {"id": "MOTS — tingkat menengah", "en": "MOTS — middle order"},
    "hots": {"id": "HOTS — tingkat tinggi", "en": "HOTS — higher order"},
}

BY_LEVEL: dict[str, Level] = {level.key: level for level in LEVELS}


def level(key: str | None) -> Level | None:
    """The level a stored key names, or ``None`` when nothing is set.

    ``None`` is a real state and must stay distinguishable from a mistake:
    "the teacher has not labelled this question yet" is a fact a report has to
    state, because guessing a level from the question *type* would invent the
    very thing the framework is asked to measure.

    The key comes out of a JSONB column a hand-edited exam can hold anything in,
    so anything that is not a string is unlabelled rather than an AttributeError
    on the page: a mislabelled question is a report to fix, not a 500.
    """
    if not isinstance(key, str):
        return None
    return BY_LEVEL.get(key.strip().lower())


def band_of(key: str | None) -> str | None:
    found = level(key)
    return found.band if found else None


#: Which panel a note is *about*.
#:
#: A note is a limit of the thing beside it, and the notes module raises them for
#: the whole analysis. Showing "some questions have no cognitive level, so the
#: shares count the labelled ones" on a CTT report — which draws no shares — tells
#: a teacher to fix something that page never measured, and it is the kind of
#: sentence that makes a reader stop reading the notes at all.
NOTE_PANELS: dict[str, str] = {
    "levels_partly_set": "cognitive",
    "kkm_missing": "mastery",
    "model_real": "separation",
    "scale_anchor": "items",
    "fit_dof": "items",
}

#: The panel a note falls back to. `summary` is on every framework, so an
#: unclassified note is never silently dropped — a limit nobody sees is worse
#: than a limit in the wrong place.
NOTE_DEFAULT_PANEL = "summary"


def notes_for(notes, framework) -> tuple[str, ...]:
    """The notes whose subject this framework actually shows."""
    return tuple(note for note in notes
                 if framework.shows(NOTE_PANELS.get(note, NOTE_DEFAULT_PANEL)))


def levels_payload() -> list[dict[str, object]]:
    """Every level, as the page's script and the forms need them."""
    return [
        {"key": item.key, "band": item.band,
         "name": dict(item.name), "verb": dict(item.verb)}
        for item in LEVELS
    ]


def bands_payload() -> list[dict[str, object]]:
    """The three bands with their names and the levels under each, both languages.

    The levels travel with the band because that is what a legend has to say:
    *which* C-levels a band covers, not just that "HOTS" exists. Building that
    sentence in the page from a list written in the page is how the legend ends
    up naming a level the vocabulary dropped.
    """
    return [{
        "key": band,
        "name": dict(BAND_NAMES[band]),
        "levels": [{"key": level.key, "name": dict(level.name),
                    "verb": dict(level.verb)}
                   for level in LEVELS if level.band == band],
    } for band in BANDS]


def catalogue(lang: str = "id") -> list[dict[str, str]]:
    """The four frameworks in one language, for a document's front page."""
    return [framework.words(lang) for framework in FRAMEWORKS]


def frameworks_payload() -> list[dict[str, object]]:
    """The four frameworks with *both* languages, for a page whose toggle is live.

    `catalogue` is for a document, which is already in one language; this is for
    the selector on the analysis page, where the reader can switch the toggle
    without a round trip. A server-rendered list with one language baked in is how
    a page ends up offering "Cognitive level" under an Indonesian heading.
    """
    return [{
        "key": framework.key,
        "name": dict(framework.name),
        "question": dict(framework.question),
        "measures": list(framework.measures),
        "not_for": dict(framework.not_for),
        "when": dict(framework.when),
        "reference": dict(framework.reference),
        "panels": list(framework.panels),
    } for framework in FRAMEWORKS]


def panels_for(key: str | None) -> tuple[str, ...]:
    """The panels a framework key names, for a caller that has only the key."""
    return resolve(key).panels
