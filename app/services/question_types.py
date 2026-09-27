"""What kind of question is this, and is the answer right?

The app grew up with two kinds — `mcq` and the essays — and that distinction was
written into the code as literal tests, `qtype == "mcq"` and
`v not in ("essay", "essay_text", "essay_canvas")`, in **seven** places: two
weighted scoring loops, three count-based ones (the sync route, the scan route and
the Celery OMR task), the weight fallback, and every report template. Each copy
also carried its own comparison for "is this answer correct".

Two kinds survive that. A third kind of *objective* question does not: the moment
`true_false`, `match` or `drag_drop` exists as a question type, every one of those
seven places decides on its own that the new type is an essay — it is not `"mcq"`,
so it lands in the essay pool, earns no points from the auto-grader, is excluded
from `mcq_count`, and is counted wrong in the pupil's result. Silently, in seven
different ways, with the marks already published.

So the vocabulary lives here, once:

    objective (auto-graded)   mcq · true_false · match · drag_drop
    essay (teacher marks it)  essay · essay_text · essay_canvas

and so does the one comparison that turns a key and a student's answer into
right or wrong. Callers ask `is_objective(qtype)` instead of comparing to a
string, and `grade_answer(qtype, key, answer)` instead of writing the rule again.
`tests/unit/test_objective_types.py` fails if a scoring site reintroduces the
literal comparison.

The `mcq` branch is deliberately a *copy* of the rule that shipped, including its
quirks, because this module arrived after real marks were recorded: a "bonus"
question is right when anything was written at all, a list key accepts any member
of the list, and equality is exact. Nothing here changes what an existing exam
scores.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

# ── the vocabulary ───────────────────────────────────────────────────────────

# A question type is a *kind of question*, not a flavour of multiple choice. Each
# name below is priced on its own in the mark scheme, offered on its own in the
# builder, and graded by its own rule. `OBJECTIVE_TYPES` is a statement about who
# marks it — the app, not the teacher — and **not** a statement that these five are
# one thing that shares a weight: the 70/30 pool model that read it that way is kept
# only to reproduce papers marked before the scheme existed, and the scheme gives
# every type its own row.
MCQ = "mcq"
TRUE_FALSE = "true_false"
COMPLEX_MULTIPLE_CHOICE = "complex_multiple_choice"
MATCH = "match"
DRAG_DROP = "drag_drop"
ORDER = "order"

ESSAY = "essay"
ESSAY_TEXT = "essay_text"
ESSAY_CANVAS = "essay_canvas"

OBJECTIVE_TYPES = (MCQ, TRUE_FALSE, COMPLEX_MULTIPLE_CHOICE, MATCH, DRAG_DROP, ORDER)
ESSAY_TYPES = (ESSAY, ESSAY_TEXT, ESSAY_CANVAS)

#: The strings the answer key uses to mean "a teacher marks this one". The scanner
#: and the count-based graders have tested against exactly this tuple since the
#: first release, so it is also what `counts_as_objective_key()` mirrors.
ESSAY_MARKERS = (ESSAY, ESSAY_TEXT, ESSAY_CANVAS)

#: What the builder writes into `answer_key` for a question nobody auto-grades.
DEFAULT_ESSAY_MARKER = ESSAY

#: What a question type falls back to when the stored value is missing or a name
#: this version does not know. `mcq` is the app's own default for an unknown
#: index, and keeping it means an exam written before the new types existed reads
#: exactly as it did.
DEFAULT_TYPE = MCQ

# ── the shape of a key, named once ───────────────────────────────────────────
#
# An MCQ key is a letter, a list of letters, or the word `bonus`. The three new
# objective types need more than a letter, so their keys are objects with named
# fields rather than positional ones — `{"pairs": [...]}` reads the same in the
# builder, the answer-key page, the grader and a report. Every reader goes through
# the accessors below so that a key written by an older version, or by hand in the
# Supabase console, is still read rather than silently graded wrong.

#: Matching: `{"pairs": [{"left": …, "right": …}, …], "extra": [right, …]}`
#: Complex multiple choice: the statements a pupil judges, the categories they
#: judge them with, and the correct category for each statement. Everything the
#: question *is* lives in its key, exactly as a matching question's two columns and
#: its pairing do — which is what lets one function (`public_options`) hand a pupil
#: the material to answer with and never the answers.
PGK_STATEMENTS = "statements"
PGK_CATEGORIES = "categories"
PGK_KEY = "key"

#: Matching: `{"pairs": [{"left": …, "right": …}, …], "extra": [right, …]}`
MATCH_PAIRS = "pairs"
#: Drag and drop *and* ordering: `{"order": [item, …], "extra": [item, …]}`. The
#: two types answer the same kind of question — a sequence — so they share the
#: field and the grader; what differs is the affordance a student is given, and
#: that a drag & drop question is dragged into while an ordering question is
#: ranked. Sharing the shape is also what keeps every drag & drop question already
#: stored grading exactly as it did.
DRAG_ORDER = "order"
#: Both: right-hand or bank entries that belong to no correct answer.
EXTRA = "extra"
_EXTRA_ALIASES = ("extra", "distractors", "extras")

# ── how a complex multiple choice question is marked ────────────────────────
#
# Three modes, and the teacher picks one per question. The names are a closed
# vocabulary rather than free text for the reason the question types themselves
# are: a mode nothing answers to is a marking rule that silently does nothing, and
# the SQL CHECK beside this list (migration 037) enforces the same three strings so
# a row edited in the console cannot store a fourth.

#: The default, and the one the Kemendikbud AKM guidance recommends: a short
#: two-category question is marked 1/0, and anything else 2/1/0. Chosen as the
#: default because guessing *every* statement of a two-option question right is a
#: 0.5**n event — far below the odds a proportional rule walks into.
SCORING_AKM = "akm_standard"
#: `max(0, (right - wrong) / total)`: more granular, and less resistant to guessing.
#: The builder warns about exactly that when a teacher picks it.
SCORING_PROPORTIONAL = "proportional"
#: Full marks when every statement is right and nothing otherwise, at any length.
SCORING_ALL_OR_NOTHING = "all_or_nothing"

SCORING_MODES = (SCORING_AKM, SCORING_PROPORTIONAL, SCORING_ALL_OR_NOTHING)
DEFAULT_SCORING_MODE = SCORING_AKM

#: The band AKM marks 1/0 rather than 2/1/0: three to five statements, judged with
#: exactly two categories. Outside it — longer, or more than two categories — the
#: question earns the 2/1/0 ladder instead. Both halves are asserted over the
#: boundary in `tests/unit/test_pgk_scoring.py`.
AKM_BINARY_MIN, AKM_BINARY_MAX = 3, 5
#: The most wrong statements that still earn the middle rung of the 2/1/0 ladder.
AKM_PARTIAL_WRONG = 2


def canonical_type(raw: Any) -> str:
    """The stored type name, or `mcq` for anything absent or unrecognised."""
    if not isinstance(raw, str):
        return DEFAULT_TYPE
    value = raw.strip().lower()
    return value if value in OBJECTIVE_TYPES or value in ESSAY_TYPES else DEFAULT_TYPE


def is_objective(raw: Any) -> bool:
    """True when the app can mark this question without a teacher."""
    return canonical_type(raw) in OBJECTIVE_TYPES


def is_essay(raw: Any) -> bool:
    return not is_objective(raw)


def pool(raw: Any) -> str:
    """Which weight pool this question belongs to: `objective` or `essay`."""
    return "objective" if is_objective(raw) else "essay"


def essay_marker(raw: Any = None) -> str:
    """The `answer_key` value that means "not auto-graded" for this question."""
    value = canonical_type(raw)
    return value if value in ESSAY_TYPES else DEFAULT_ESSAY_MARKER


def is_essay_marker(value: Any) -> bool:
    """Is this `answer_key` value the marker for a teacher-marked question?"""
    return value in ESSAY_MARKERS


def counts_as_objective_key(value: Any) -> bool:
    """Does this key value belong in the auto-graded count?

    Mirrors the test the app has always made — `v not in (essay markers)` — which
    means a `None` value counts. That is preserved on purpose: this helper was
    extracted after marks were published, and the count feeds `max_score` and the
    percentage a pupil is shown, so tightening it here would quietly change
    recorded scores.
    """
    return not is_essay_marker(value)


def objective_key_count(key: Mapping[str, Any] | None) -> int:
    """How many questions in this key the app auto-grades."""
    return sum(1 for value in (key or {}).values() if counts_as_objective_key(value))


# ── reading an answer whatever shape it arrived in ──────────────────────────

_TRUE = {"true", "t", "1", "yes", "y", "benar", "b"}
_FALSE = {"false", "f", "0", "no", "n", "salah", "s"}


def unwrap(answer: Any) -> Any:
    """The answer itself, from either a bare value or `{"answer": ...}`.

    Online the student page stores a bare value, offline it syncs a wrapper with
    the drawings beside it, and the routes have always unwrapped the wrapper
    before grading. This is that step, in one place.
    """
    if isinstance(answer, dict) and "answer" in answer:
        return answer.get("answer")
    return answer


def _as_bool_word(value: Any) -> str | None:
    """`true` / `false` in any of the forms a pupil or a key might produce."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and value in (0, 1):
        return "true" if int(value) == 1 else "false"
    if isinstance(value, str):
        word = value.strip().lower()
        if word in _TRUE:
            return "true"
        if word in _FALSE:
            return "false"
    return None


def _as_pairs(value: Any) -> frozenset[tuple[str, str]] | None:
    """A matching answer as an order-insensitive set of (left, right) pairs.

    Three shapes arrive here and all three mean the same answer: the page's own
    list of `{left, right}` objects, the `{left: right}` map it keeps while the
    pupil is answering, and the *named* shape the key itself is stored in —
    `{"pairs": [...]}`, which is what the offline queue replays and what a teacher
    who retypes a key in the console produces. That last one used to fall into the
    mapping branch and become the single pair `("pairs", "[…]")`: an answer that
    was right, marked wrong, with nothing anywhere saying why.
    """
    if isinstance(value, Mapping) and MATCH_PAIRS in value:
        value = value.get(MATCH_PAIRS)
    if isinstance(value, Mapping):
        return frozenset((str(k).strip(), str(v).strip()) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        pairs = []
        for item in value:
            if isinstance(item, Mapping):
                left = item.get("left", item.get("kiri", item.get("from")))
                right = item.get("right", item.get("kanan", item.get("to")))
                if left is None or right is None:
                    return None
                pairs.append((str(left).strip(), str(right).strip()))
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                pairs.append((str(item[0]).strip(), str(item[1]).strip()))
            else:
                return None
        return frozenset(pairs)
    return None


def _as_sequence(value: Any) -> tuple[str, ...] | None:
    """A drag-and-drop answer as the ordered list of the chips that were placed.

    A bare string is refused rather than exploded into characters: `"ab"` is not
    two placements, and treating it as one would mark a nonsense answer right.
    The named shape the key is stored in — `{"order": [...]}` — is read as the
    answer too, for the same reason `_as_pairs` reads `{"pairs": [...]}`.
    """
    if isinstance(value, Mapping) and DRAG_ORDER in value:
        value = value.get(DRAG_ORDER)
    if isinstance(value, str):
        return None
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value)
    return None


# ── reading a reasoned key ──────────────────────────────────────────────────

def match_pairs(key: Any) -> tuple[tuple[str, str], ...]:
    """The correct (left, right) pairs, in the order the teacher wrote them.

    Accepts the stored shape (`{"pairs": [...]}`), a bare list of pairs, and a
    plain object mapping left to right — the last because a key pasted by hand
    into the Supabase console is usually written that way.
    """
    if isinstance(key, Mapping):
        # The stored shape names its list; a key written by hand — or by the
        # answer-key page — is usually the mapping itself, left to right. Both are
        # read, because a key stored in two shapes is a key graded two ways.
        if MATCH_PAIRS in key:
            raw: Any = key.get(MATCH_PAIRS)
        elif any(name in key for name in _EXTRA_ALIASES):
            raw = None                   # `{"order": …}`-shaped: not a pair map
        else:
            raw = key
    else:
        raw = key
    if isinstance(raw, Mapping):
        return tuple(
            (str(k).strip(), str(v).strip()) for k, v in raw.items()
            if str(k).strip() and str(v).strip()
        )
    if not isinstance(raw, (list, tuple)):
        return ()
    pairs: list[tuple[str, str]] = []
    for item in raw:
        if isinstance(item, Mapping):
            left = item.get("left", item.get("kiri", item.get("from")))
            right = item.get("right", item.get("kanan", item.get("to")))
            if left is None or right is None:
                continue
            pairs.append((str(left).strip(), str(right).strip()))
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            pairs.append((str(item[0]).strip(), str(item[1]).strip()))
    return tuple(p for p in pairs if p[0] and p[1])


def match_rights(key: Any) -> tuple[str, ...]:
    """Every right-hand value a student may choose, deduplicated.

    Sorted, and that is deliberate: the paper's own right column is what a student
    reads, so this list only has to be *stable* across reloads and must not imply
    the pairing. Alphabetical gives both, with no randomness in a cached page.
    """
    rights = [right for _, right in match_pairs(key)]
    rights.extend(_extra_chips(key))
    return tuple(sorted({r for r in rights if r}))


def drag_order(key: Any) -> tuple[str, ...]:
    """The correct order of the items — for drag & drop and for ordering alike.

    Accepts the stored shape or a bare list. Named for the drag & drop type because
    it was written for it first; an ordering question is the same sequence behind a
    different control, so a second accessor would be a second answer to the same
    question.
    """
    raw = key.get(DRAG_ORDER) if isinstance(key, Mapping) else key
    sequence = _as_sequence(raw)
    return tuple(s for s in (sequence or ()) if s)


def drag_bank(key: Any) -> tuple[str, ...]:
    """Every chip the student may drag — the answer plus any distractors, sorted.

    Sorted for the same reason as `match_rights`: the bank must be legible and
    stable, and its order must not be the answer.

    A word that the answer uses twice appears twice, because the bank is the
    student's whole supply: de-duplicating it would leave a question whose correct
    sequence repeats a word impossible to answer.
    """
    chips = [c for c in drag_order(key) if c]
    chips.extend(_extra_chips(key))
    return tuple(sorted(chips))


def _extra_chips(key: Any) -> list[str]:
    """Distractors beside the correct answer: entries that match nothing."""
    if not isinstance(key, Mapping):
        return []
    for name in _EXTRA_ALIASES:
        raw = key.get(name)
        if isinstance(raw, str):
            return [raw.strip()] if raw.strip() else []
        if isinstance(raw, (list, tuple)):
            return [str(item).strip() for item in raw if str(item).strip()]
    return []


# ── what a pupil is allowed to see ──────────────────────────────────────────

def pgk_statements(key: Any) -> tuple[str, ...]:
    """The statements a pupil judges, in the order the teacher wrote them."""
    raw = key.get(PGK_STATEMENTS) if isinstance(key, Mapping) else None
    if isinstance(raw, (list, tuple)):
        return tuple(text for text in (str(s).strip() for s in raw) if text)
    return ()


def pgk_categories(key: Any) -> tuple[str, ...]:
    """The labels a pupil judges *with* — `Benar`/`Salah`, `Ya`/`Tidak`, or their own."""
    raw = key.get(PGK_CATEGORIES) if isinstance(key, Mapping) else None
    if isinstance(raw, (list, tuple)):
        return tuple(text for text in (str(c).strip() for c in raw) if text)
    return ()


def pgk_key(key: Any) -> tuple[int, ...]:
    """The correct category *index* for each statement.

    Read from the three shapes a real key arrives in, because a key stored two ways
    is a key graded two ways: the builder's own list of indexes, the category
    *names* a teacher retyping a key in the Supabase console would produce, and the
    booleans a two-category form posts. Anything unreadable returns `()`, which
    every caller reads as "no answer key here" rather than as a guess — the same
    choice `match_pairs` makes.
    """
    categories = pgk_categories(key)
    raw = key.get(PGK_KEY) if isinstance(key, Mapping) else key
    if isinstance(raw, str) or not isinstance(raw, (list, tuple)):
        return ()
    out: list[int] = []
    for item in raw:
        # `bool` before `int`: `True == 1` in Python, and a two-category key posted
        # as booleans would otherwise be read as the *second* category being right.
        if isinstance(item, bool):
            out.append(0 if item else 1)
            continue
        if isinstance(item, (int, float)):
            out.append(int(item))
            continue
        if isinstance(item, str):
            text = item.strip()
            if text in categories:
                out.append(categories.index(text))
                continue
            if text.isdigit():
                out.append(int(text))
                continue
        return ()
    return tuple(out)


def _as_pgk_answer(answer: Any, width: int) -> tuple[int | None, ...] | None:
    """A pupil's judgements as category indexes, `None` where a row was left blank.

    `None` and not "wrong": a blank row is not a choice. The two are treated alike
    by every mode — a blank is not correct — but keeping them apart is what lets
    `has_answer` tell an untouched question from a wrong one.
    """
    value = unwrap(answer)
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        return None
    if len(value) != width:
        return None
    out: list[int | None] = []
    for item in value:
        if item is None or item == "":
            out.append(None)
            continue
        if isinstance(item, bool):
            out.append(0 if item else 1)
            continue
        if isinstance(item, (int, float)):
            out.append(int(item))
            continue
        if isinstance(item, str) and item.strip().isdigit():
            out.append(int(item.strip()))
            continue
        return None
    return tuple(out)


def canonical_scoring_mode(raw: Any = None) -> str:
    """A stored marking mode, or the default for anything absent or unrecognised."""
    if not isinstance(raw, str):
        return DEFAULT_SCORING_MODE
    value = raw.strip().lower()
    return value if value in SCORING_MODES else DEFAULT_SCORING_MODE


def scoring_mode(question_scoring: Mapping[str, Any] | None, index: Any = 0) -> str:
    """The marking mode stored for one question, by question *index*.

    `question_scoring` is `{"3": "proportional"}` beside `question_types` and
    `question_weights`, keyed the same way. A map that names no mode for this
    question means the teacher never chose one, and the default is what the builder
    offers — so a question with no entry behaves as if AKM had been picked.
    """
    modes = question_scoring if isinstance(question_scoring, Mapping) else {}
    return canonical_scoring_mode(modes.get(str(index)))


def pgk_score(mode: Any, key: Any, answer: Any) -> float:
    """The share of a complex multiple choice question this answer earns, `[0, 1]`.

    Total, like every other reader here: a key that does not describe the same
    number of statements, or an answer that is not one judgement per statement,
    earns nothing rather than raising. The share is what the question's *points*
    are multiplied by, which is what turns the AKM 2/1/0 into 10/5/0 on a question
    worth ten — the conversion the brief asks for, applied once, here.
    """
    statements = pgk_statements(key)
    wanted = pgk_key(key)
    if not statements or len(wanted) != len(statements):
        return 0.0
    chosen = _as_pgk_answer(answer, len(wanted))
    if chosen is None:
        return 0.0

    total = len(wanted)
    right = sum(1 for want, got in zip(wanted, chosen) if got is not None and got == want)
    wrong = total - right

    kind = canonical_scoring_mode(mode)
    if kind == SCORING_PROPORTIONAL:
        return max(0.0, (right - wrong) / total)
    if kind == SCORING_ALL_OR_NOTHING:
        return 1.0 if wrong == 0 else 0.0

    # AKM. Its binary band is a statement about the *question* — how many
    # judgements, against how many categories — and not about the answer, so a
    # question outside the band earns the ladder whatever the pupil did.
    if pgk_akm_band(key) == AKM_BINARY_BAND:
        return 1.0 if wrong == 0 else 0.0
    if wrong == 0:
        return 1.0
    if wrong <= AKM_PARTIAL_WRONG:
        return 0.5
    return 0.0


#: Which of AKM's two rungs a question falls on. Named rather than a bare bool so
#: the page can explain *why* one wrong statement costs everything at five and
#: half at six, and so a third band has somewhere to be added.
AKM_BINARY_BAND = "binary"
AKM_LADDER_BAND = "ladder"


#: The answer patterns the builder's simulator shows, in the order a teacher
#: reads them: everything right, then one, two, or every statement wrong. Named
#: here rather than in the template so the page and any future export agree.
PGK_SIMULATION_PATTERNS = ("all_right", "one_wrong", "two_wrong", "all_wrong")


def pgk_akm_band(key: Any) -> str:
    """AKM's binary band or its 2/1/0 ladder, for one question.

    The band is a property of the *question* — a 3-5 statement question judged
    with exactly two categories — and not of the answer, which is why it is read
    from the key alone and used by both the grader and the builder's simulator.
    """
    total = len(pgk_key(key))
    if AKM_BINARY_MIN <= total <= AKM_BINARY_MAX and len(pgk_categories(key)) == 2:
        return AKM_BINARY_BAND
    return AKM_LADDER_BAND


def _pgk_answer_wrong(wanted: Sequence[int], count: int, categories: int) -> list[int]:
    """The first `count` judgements deliberately wrong, the rest right.

    "Wrong" is any category other than the key's, so a question with more than two
    categories exercises a genuinely different judgement rather than a coin flip
    between two — which is what makes the simulator's rows worth reading.
    """
    out: list[int] = []
    for i, want in enumerate(wanted):
        out.append((want + 1) % categories if i < count else want)
    return out


def pgk_simulate(key: Any, mode: Any = None) -> list[dict[str, Any]]:
    """The canonical answer patterns, scored — for the builder's simulator.

    Each row is produced by **the grader itself** (`pgk_score`), never by a second
    arithmetic: a simulator that agrees with the page rather than with the marking
    engine is worse than no simulator, because a teacher would check their rule
    against a number that is not the number their pupils get.

    Patterns whose wrong-count is indistinguishable from another's are folded (at
    two statements "two wrong" and "every statement wrong" are the same answer),
    and a key that cannot be read simulates nothing rather than inventing a rule.
    """
    wanted = pgk_key(key)
    category_names = pgk_categories(key)
    categories = len(category_names)
    if not wanted or not categories:
        return []
    # The statement *text* does not enter the arithmetic — only how many there are
    # — so a simulator asked about a half-typed question (blank statements, or none
    # at all) still answers instead of reporting every pattern as zero. The count
    # comes from the key, which is what `pgk_score` checks against.
    statements = pgk_statements(key)
    if len(statements) != len(wanted):
        statements = tuple(f"statement {i + 1}" for i in range(len(wanted)))
    key = {PGK_STATEMENTS: list(statements),
           PGK_CATEGORIES: list(category_names),
           PGK_KEY: list(wanted)}
    total = len(wanted)
    counts: list[int] = []
    for count in (0, 1, 2, total):
        if 0 <= count <= total and count not in counts:
            counts.append(count)
    label = {0: "all_right", 1: "one_wrong", 2: "two_wrong"}
    out: list[dict[str, Any]] = []
    for count in counts:
        answer = _pgk_answer_wrong(wanted, count, categories)
        out.append({
            "pattern": label.get(count, "all_wrong"),
            "wrong": count,
            "right": total - count,
            "score": round(pgk_score(mode, key, answer), 4),
        })
    return out


def public_options(qtype: Any, key: Any) -> dict[str, list[str]] | None:
    """The part of a keyed question a *student* may receive, and nothing more.

    A student page needs the material to answer with — the matching columns, the
    chip bank — and must never receive the pairing itself. The two are separated
    here rather than in the route so there is exactly one place that decides what
    is safe to send, and `tests/unit/test_objective_types.py` fails if a route
    stops using it.

    `None` means "nothing extra": an MCQ shows its five bubbles, a true/false
    shows its two buttons, and an essay shows its canvas, all without a key.
    """
    kind = question_kind(qtype)
    if kind == KIND_MATCH:
        lefts = [left for left, _ in match_pairs(key)]
        rights = list(match_rights(key))
        if not lefts or not rights:
            return None
        return {"lefts": lefts, "rights": rights}
    if kind in (KIND_DRAG, KIND_ORDER):
        chips = list(drag_bank(key))
        return {"chips": chips} if chips else None
    if kind == KIND_PGK:
        # The statements and the categories, and pointedly not `key` — which sits
        # in the same object, one field away. A question whose statements and whose
        # answers travel together is exactly why this function exists rather than a
        # branch in a route.
        statements = list(pgk_statements(key))
        categories = list(pgk_categories(key))
        if not statements or not categories:
            return None
        return {"statements": statements, "categories": categories}
    return None


# ── naming a question for display ───────────────────────────────────────────

KIND_CHOICE = "choice"
KIND_TRUE_FALSE = "truefalse"
KIND_PGK = "pgk"
KIND_MATCH = "match"
KIND_DRAG = "dragdrop"
KIND_ORDER = "ordering"
KIND_ESSAY = "essay"

_KIND_BY_TYPE: dict[str, str] = {
    MCQ: KIND_CHOICE,
    TRUE_FALSE: KIND_TRUE_FALSE,
    COMPLEX_MULTIPLE_CHOICE: KIND_PGK,
    MATCH: KIND_MATCH,
    DRAG_DROP: KIND_DRAG,
    ORDER: KIND_ORDER,
    ESSAY: KIND_ESSAY,
    ESSAY_TEXT: KIND_ESSAY,
    ESSAY_CANVAS: KIND_ESSAY,
}


#: The types a teacher may *create* in the builder, in the order the buttons
#: appear, each with its bilingual name. `essay_text` is deliberately absent: a
#: typed essay is a legacy form the app still reads and grades, and offering it
#: beside the canvas one invites a question the pupil cannot draw on.
PICKER_TYPES: tuple[str, ...] = (
    MCQ, TRUE_FALSE, COMPLEX_MULTIPLE_CHOICE, MATCH, DRAG_DROP, ORDER, ESSAY_CANVAS,
)

#: `complex_multiple_choice` joined this tuple **with its editor**, which is the
#: property that tuple stands for: the values a teacher may *create*. The builder
#: draws an editor per kind (statements, categories, one key per statement, and the
#: AKM simulator), so the entry now has something under it. Adding the type here
#: while withholding the mode picker is deliberate — only AKM is offered until its
#: boundary behaviour is proven, and the other two modes stay defined and graded in
#: `SCORING_MODES` so the vocabulary the SQL CHECK mirrors does not move.

_TYPE_LABELS: dict[str, tuple[str, str]] = {
    MCQ: ("Pilihan Ganda", "Multiple choice"),
    TRUE_FALSE: ("Benar / Salah", "True / False"),
    COMPLEX_MULTIPLE_CHOICE: ("Pilihan Ganda Kompleks", "Complex multiple choice"),
    MATCH: ("Menjodohkan", "Matching"),
    DRAG_DROP: ("Tarik & Letakkan", "Drag & drop"),
    ORDER: ("Mengurutkan", "Ordering"),
    ESSAY_CANVAS: ("Esai", "Essay"),
    ESSAY_TEXT: ("Esai (ketik)", "Essay (typed)"),
    ESSAY: ("Esai", "Essay"),
}


def vocabulary() -> dict[str, Any]:
    """The type names, for a page that has to decide *before* asking the server.

    Both the exam builder and the student's exam page classify a question in
    JavaScript — one to draw an editor, the other to draw the answer control — and
    a second hand-written list in JavaScript is exactly how the two drift apart.
    They read this instead, so the names exist once in the whole app.

    `picker` is the builder's own list: the values it may create and the words it
    shows for them. A JavaScript array of `{v:'true_false', …}` in the template
    would be that second list again — the exam builder's type `<select>` would go
    on offering a name the grader no longer answers to.
    """
    return {
        "kinds": dict(_KIND_BY_TYPE),
        "objective": list(OBJECTIVE_TYPES),
        "essay": list(ESSAY_TYPES),
        "default": DEFAULT_TYPE,
        "labels": {
            KIND_CHOICE: ["Pilihan Ganda", "Multiple choice"],
            KIND_TRUE_FALSE: ["Benar / Salah", "True / False"],
            KIND_PGK: ["Pilihan Ganda Kompleks", "Complex multiple choice"],
            KIND_MATCH: ["Menjodohkan", "Matching"],
            KIND_DRAG: ["Tarik & Letakkan", "Drag & drop"],
            KIND_ORDER: ["Mengurutkan", "Ordering"],
            KIND_ESSAY: ["Esai", "Essay"],
        },
        "picker": [
            {"v": t, "kind": _KIND_BY_TYPE[t], "id": _TYPE_LABELS[t][0], "en": _TYPE_LABELS[t][1]}
            for t in PICKER_TYPES
        ],
    }


#: A kind's name in English. One map, because a scheme table, a spreadsheet export
#: and a report all name the same kinds, and six hand-written lists is how one of
#: them ends up calling a matching question "Essay".
_KIND_LABELS: dict[str, str] = {
    KIND_CHOICE: "Multiple choice",
    KIND_TRUE_FALSE: "True / False",
    KIND_PGK: "Complex multiple choice",
    KIND_MATCH: "Matching",
    KIND_DRAG: "Drag & drop",
    KIND_ORDER: "Ordering",
    KIND_ESSAY: "Essay",
}


def kind_label(raw: Any) -> str:
    """This question's kind, named for a reader."""
    return _KIND_LABELS[question_kind(raw)]


def question_kind(raw: Any) -> str:
    """How a page should *render* this question.

    Templates switch on this instead of on `== 'mcq'`, so adding a type is one
    line here rather than a hunt through every page for the branch that decided
    "not mcq, so it must be an essay".
    """
    return _KIND_BY_TYPE[canonical_type(raw)]


def _pgk_choices_text(categories: Sequence[str], answer: Any) -> str:
    """What a pupil judged, as words when the categories are known.

    `categories` come from the *key*, so a caller holding the key beside the answer
    gets `1=Benar; 2=Salah` and a caller without one gets the positions (`1; 2`) —
    honest about which judgement was made, and never a label invented for it.
    """
    if isinstance(answer, str) or not isinstance(answer, (list, tuple)):
        return ""
    picked = _as_pgk_answer(answer, len(answer))
    if picked is None:
        return ""
    out: list[str] = []
    for i, choice in enumerate(picked):
        if choice is None:
            continue
        if 0 <= choice < len(categories):
            out.append(f"{i + 1}={categories[choice]}")
        else:
            out.append(str(i + 1))
    return "; ".join(out)


def describe_answer(qtype: Any, key: Any) -> str:
    """The correct answer as a teacher reads it, or `""` when there is none.

    Rendered by the grading page, both report templates and the PDF, so the same
    key never appears as `{'left': 'x'}` in one place and `x` in another.
    """
    kind = question_kind(qtype)
    if kind == KIND_TRUE_FALSE:
        word = _as_bool_word(key)
        return word.capitalize() if word else ""
    if kind == KIND_MATCH:
        return "; ".join(f"{left} \u2192 {right}" for left, right in match_pairs(key))
    if kind in (KIND_DRAG, KIND_ORDER):
        return " \u2192 ".join(drag_order(key))
    if kind == KIND_PGK:
        categories = pgk_categories(key)
        out = []
        for i, choice in enumerate(pgk_key(key)):
            label = categories[choice] if 0 <= choice < len(categories) else str(choice)
            out.append(f"{i + 1}={label}")
        return "; ".join(out)
    if kind == KIND_ESSAY:
        return ""
    if key == "bonus":
        return "bonus"
    if isinstance(key, (list, tuple, set)):
        return ", ".join(str(v) for v in key)
    return "" if key is None else str(key)


#: The field a student's drawing lives under, inside an answer's `pages` map:
#: `{"pages": {"<page index>": {"canvas": "data:image/png;base64,…", "textBoxes": […]}}}`.
#: Index-keyed, like every other reader of this shape — see `page_label`.
ANSWER_PAGES = "pages"
ANSWER_CANVAS = "canvas"
ANSWER_TEXT_BOXES = "textBoxes"


def answer_pages(answer: Any) -> dict[str, Any]:
    """The pages a student's answer carries, keyed by page index.

    `{}` for an answer that is a plain value — a choice, a boolean, a sequence —
    which is what makes it safe to ask of every question. A single page kept as
    `{"canvas": …}` carries no `pages` map and is left alone rather than misread.
    """
    if not isinstance(answer, Mapping):
        return {}
    pages = answer.get(ANSWER_PAGES)
    if isinstance(pages, Mapping):
        return {str(page): content for page, content in pages.items()}
    return {}


def _page_index(page: Any) -> int:
    try:
        return int(page)
    except (TypeError, ValueError):
        return 10 ** 6                      # an unreadable key sorts last, not first


def page_label(page: Any) -> str:
    """The page as a reader counts them.

    The map is **index**-keyed — `take_exam.html` writes `canvasData[i][page - 1]`
    and the teacher's overlay the same — so page `"0"` is the paper's page 1. A
    label printing the raw key would send a family to a page the paper has not got.
    """
    try:
        return str(int(page) + 1)
    except (TypeError, ValueError):
        return str(page)


def answer_drawings(answer: Any) -> list[tuple[int, str, str]]:
    """`(page index, page label, data URL)` for each page that carries a drawing.

    The index is returned beside the label because the *page image* of the exam is
    addressed by the same index — `pdf_page_urls[index]` — so a surface that wants
    to lay the drawing over the paper it was made on does not have to turn a label
    back into an index and hope.
    """
    out: list[tuple[int, str, str]] = []
    pages = answer_pages(answer)
    ordered = sorted(pages, key=_page_index)
    for position, page in enumerate(ordered):
        content = pages[page]
        if not isinstance(content, Mapping):
            continue
        drawing = content.get(ANSWER_CANVAS)
        if isinstance(drawing, str) and drawing.startswith("data:"):
            index = _page_index(page)
            out.append((index if index < 10 ** 6 else position,
                        page_label(page), drawing))
    return out


def describe_attempt(qtype: Any, answer: Any, key: Any = None) -> str:
    """What a student wrote, as words — never a Python repr of it.

    `describe_answer` describes a **key**: the shapes the builder writes, which
    never include a drawing. Handed a submission it fell through to `str(value)`,
    so one table cell of the learner's report — and the matching cell of the CSV,
    XLSX and PDF built from the same row — carried the whole stored dict, base64
    and all: a cell of about a megabyte that no reader can use, on a report a
    family is meant to read.

    A drawing is stored under the page it was made on, so the honest text is which
    pages carry work. The wording belongs to the surface — a sentence written here
    would be English on an Indonesian report — so this returns page numbers and
    each surface says what they are.
    """
    pages = answer_pages(answer)
    if pages:
        drawn: list[str] = []
        written: list[str] = []
        for page in sorted(pages, key=_page_index):
            content = pages[page]
            if not isinstance(content, Mapping):
                continue
            label = page_label(page)
            if content.get(ANSWER_CANVAS):
                drawn.append(label)
            elif content.get(ANSWER_TEXT_BOXES):
                written.append(label)
        return ", ".join([*drawn, *written])

    value = unwrap(answer)
    if question_kind(qtype) == KIND_PGK:
        # The categories live in the *key*, one object away from the answer, so this
        # is the one type whose attempt cannot be described from its answer alone.
        # `key` is optional rather than required because the reports that render a
        # learner's own paper have the key to hand and the ones that do not are
        # still better off with positions than with a Python repr of the list.
        return _pgk_choices_text(pgk_categories(key), value)
    text = describe_answer(qtype, answer)
    if isinstance(value, Mapping) and text.startswith(("{", "[")):
        # A structured answer whose shape this version does not know — an older
        # one, or one written by hand. `describe_answer` only ever produces a repr
        # by falling through to `str(value)`, and a repr is precisely what must not
        # reach a report: an empty cell is honest, a megabyte of base64 in a
        # family's table is not. A *string* answer that happens to start with a
        # brace is left alone — it is text somebody wrote.
        return ""
    return text


def normalise_key(qtype: Any, key: Any) -> Any:
    """A key as the app stores it, whatever a form posted.

    The builder posts the same JSON shape this returns, but the answer-key page
    and a hand-edited row may not, and a key that is *stored* in two shapes is a
    key that is graded in two ways. Canonicalising on the way in is what makes
    the accessors above sufficient rather than defensive.
    """
    kind = question_kind(qtype)
    if kind == KIND_TRUE_FALSE:
        return _as_bool_word(key) or ""
    if kind == KIND_MATCH:
        pairs = [{"left": left, "right": right} for left, right in match_pairs(key)]
        extra = _extra_chips(key)
        out: dict[str, Any] = {MATCH_PAIRS: pairs}
        if extra:
            out[EXTRA] = extra
        return out
    if kind in (KIND_DRAG, KIND_ORDER):
        out = {DRAG_ORDER: list(drag_order(key))}
        extra = _extra_chips(key)
        if extra:
            out[EXTRA] = extra
        return out
    if kind == KIND_PGK:
        return {
            PGK_CATEGORIES: list(pgk_categories(key)),
            PGK_STATEMENTS: list(pgk_statements(key)),
            PGK_KEY: list(pgk_key(key)),
        }
    if kind == KIND_ESSAY:
        return essay_marker(qtype)
    return key


def key_has_answer(qtype: Any, value: Any) -> bool:
    """Does this stored key actually contain an answer?

    The teacher dashboard's "answer key not set yet" card asks this per question,
    and it used to ask it only of letters: a `match` or `drag_drop` key is an
    object, so the old test fell through to "no key" and would have flagged every
    matching question permanently — the same failure mode as the trimmed `select()`
    that made that card wrong in the first place.
    """
    if value is None:
        return False
    kind = question_kind(qtype)
    if kind == KIND_TRUE_FALSE:
        return _as_bool_word(value) is not None
    if kind == KIND_MATCH:
        return bool(match_pairs(value))
    if kind in (KIND_DRAG, KIND_ORDER):
        return bool(drag_order(value))
    if kind == KIND_PGK:
        # All three parts, and the statements must match the judgements one for one:
        # a key shorter than its statements is not a key, it is half a question.
        statements = pgk_statements(value)
        return bool(statements) and len(statements) == len(pgk_key(value)) \
            and bool(pgk_categories(value))
    if kind == KIND_ESSAY:
        return False                     # a teacher marks it; there is no key
    if isinstance(value, (list, tuple, set)):
        return any(v not in (None, "") for v in value)
    # A choice question whose "key" is an essay marker has no key: the string is
    # the placeholder the builder writes for the questions it does not auto-mark,
    # and reading it as an answer is how the dashboard's card went wrong.
    if is_essay_marker(value):
        return False
    return str(value).strip() != ""


def has_answer(qtype: Any, answer: Any) -> bool:
    """Did the student actually answer this one?

    Used for the "answered" dot on the student's page, for the sync route's
    "is there new work on this page" test, and for the progress counter — three
    places that each had their own idea of what an empty answer looks like. A
    matching answer with one pair filled in is *not* empty, which is the case a
    naive truthiness test got wrong.
    """
    value = unwrap(answer)
    kind = question_kind(qtype)
    if kind == KIND_TRUE_FALSE:
        return _as_bool_word(value) is not None
    if kind == KIND_MATCH:
        return bool(_as_pairs(value))
    if kind in (KIND_DRAG, KIND_ORDER):
        return bool([s for s in (_as_sequence(value) or ()) if s])
    if kind == KIND_PGK:
        # One judgement is an answer; a blank grid is not.
        if isinstance(value, str) or not isinstance(value, (list, tuple)):
            return False
        return any(item is not None and item != "" for item in value)
    if kind == KIND_ESSAY:
        if isinstance(answer, Mapping):
            text = answer.get("text")
            if isinstance(text, str) and text.strip():
                return True
            pages = answer.get("pages") or answer.get("canvas")
            if isinstance(pages, Mapping) and pages:
                return True
            if isinstance(pages, str) and pages.strip():
                return True
        if isinstance(value, str) and value.strip():
            return True
        return bool(value)
    return value is not None and str(value).strip() != ""


# ── the grading ─────────────────────────────────────────────────────────────

def grade_answer(qtype: Any, key: Any, answer: Any) -> bool:
    """Is this answer right? Never raises — a malformed answer is simply wrong.

    `answer` may be a bare value or a `{"answer": ...}` wrapper; the wrapper is
    unwrapped here so every caller does not have to remember to.
    """
    kind = canonical_type(qtype)
    value = unwrap(answer)

    if kind == TRUE_FALSE:
        want = _as_bool_word(key)
        got = _as_bool_word(value)
        return want is not None and got is not None and want == got

    if kind == MATCH:
        # Order-insensitive on purpose: a matching question asks *which goes with
        # which*, and a student who answers the right pairings in a different order
        # has answered the question.
        want = frozenset(match_pairs(key))
        got = _as_pairs(value)
        return bool(want) and want == got

    if kind in (DRAG_DROP, ORDER):
        want = drag_order(key)
        got = _as_sequence(value)
        # Order-sensitive for both: a drag & drop question and an ordering question
        # each ask for a sequence, and the sequence is the answer either way.
        return bool(want) and tuple(got or ()) == want

    if kind == COMPLEX_MULTIPLE_CHOICE:
        # "Right" means every statement, under every one of the three modes — each of
        # them awards full credit only when nothing is wrong. Expressed through the
        # one all-or-nothing rule rather than a fourth comparison, so a page that
        # asks "is this right" cannot disagree with the marks.
        return pgk_score(SCORING_ALL_OR_NOTHING, key, answer) >= 1.0

    # mcq, and anything that reaches here without a type of its own: the rule that
    # has always shipped. `str(answer).strip()` is only for the bonus case, where
    # the question asks whether anything at all was written.
    if key == "bonus":
        return value is not None and bool(str(value).strip())
    if isinstance(key, (list, tuple, set)):
        return value in key
    return value == key


def is_correct(qtype: Any, key: Any, answer: Any) -> bool:
    """Alias kept for readability at call sites that count marks."""
    return grade_answer(qtype, key, answer)


# ── the marks ───────────────────────────────────────────────────────────────

#: The reserved key inside `question_weights` that carries an exam's mark scheme.
#:
#: `question_weights` is `{"0": 1.4, "1": 3.2, …}` — points, looked up by question
#: index — so a key called `_scheme` can never be mistaken for a question's marks:
#: every reader asks for a numeric index. It rides *with* the weights on purpose,
#: because the scheme decides how those weights are awarded, and the two travelling
#: together is what makes every existing scoring site scheme-aware without a
#: signature change. An exam whose weights carry no scheme scores exactly as it did
#: before this key existed, which is what keeps already-published marks frozen.
SCHEME_KEY = "_scheme"

#: The types that can earn part of their marks. The other objective kinds have two
#: or five possible answers, so a "part" of one is not a meaningful quantity.
#: Ordering and drag & drop earn their share of the positions they got right; a
#: matching question its share of the pairs. Ordering is in this tuple for the
#: reason a teacher expects: arranging four items with three in the right place is
#: three quarters of the question, and the old all-or-nothing rule called it zero.
#:
#: Complex multiple choice is in it for the same reason and one more: its share is
#: not "how many of the parts were right" but the *teacher's chosen rule* over how
#: many were — a mode, not a proportion. That is why it is read through
#: `pgk_score` rather than through the pair-and-position arithmetic below.
PARTIAL_TYPES = (COMPLEX_MULTIPLE_CHOICE, MATCH, DRAG_DROP, ORDER)


def scheme_in(weights: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    """The mark scheme stored beside these weights, or `None`.

    `None` is the important answer: it means "this exam was marked before schemes
    existed", and every caller must then use the all-or-nothing rule rather than a
    default scheme — otherwise saving an old exam in the builder would silently
    re-score work already returned to students.
    """
    scheme = (weights or {}).get(SCHEME_KEY)
    return scheme if isinstance(scheme, Mapping) else None


def partial_credit(weights: Mapping[str, Any] | None) -> bool:
    """Does this exam award part-marks for a partly-right matching or drag & drop
    answer? Only an exam carrying a scheme can say yes."""
    scheme = scheme_in(weights)
    return bool(scheme and scheme.get("partial"))


def partial_applies(raw_type: Any, weights: Mapping[str, Any] | None = None) -> bool:
    """Does this question earn a *share*, rather than all-or-nothing?

    Two ways to qualify, and they are different questions. A paper carrying a scheme
    with `partial` set asks for part credit across its multi-part types; a complex
    multiple choice question asks for it because its marking mode *is* a part rule,
    chosen by the teacher per question. The second does not depend on the first:
    without this, a paper with no scheme would grade a PGK with `grade_answer`,
    silently ignoring the mode and paying a 2/1/0 answer zero.

    Exists as a function because `item_analysis` has to make the same call, and it
    must not decide it by naming the type itself — that is the seven-copies defect
    this module was written to end.
    """
    return partial_credit(weights) or canonical_type(raw_type) == COMPLEX_MULTIPLE_CHOICE


def part_factor(qtype: Any, key: Any, answer: Any, mode: Any = None) -> float:
    """What share of a question's marks this answer earns, in `[0, 1]`.

    All-or-nothing for the types where "partly right" is not a quantity — a wrong
    letter is wrong, and so is a wrong true/false. For matching it is the share of
    the key's pairs the student actually got: a four-pair question answered three
    ways right earns three quarters of it, where the old rule gave it nothing. For
    ordering and drag & drop it is the share of positions holding the right item —
    four items in the right order with one out of place is three quarters.

    The two shapes are compared against the *key's* own items, never against the
    student's, so a student who pairs one left twice (once correctly, once with a
    distractor) cannot earn the mark twice, and a distractor in the bank never
    becomes a missing mark.
    """
    kind = canonical_type(qtype)
    if kind == COMPLEX_MULTIPLE_CHOICE:
        # Before the `PARTIAL_TYPES` test rather than inside it: the branches below
        # count pairs and positions, neither of which a PGK has, and falling into
        # them would read the question as a sequence and award it zero.
        return pgk_score(mode, key, answer)
    if kind not in PARTIAL_TYPES:
        return 1.0 if grade_answer(kind, key, answer) else 0.0

    value = unwrap(answer)
    if kind == MATCH:
        want = match_pairs(key)
        if not want:
            return 0.0
        got = _as_pairs(value) or frozenset()
        right = sum(1 for pair in want if pair in got)
        return right / len(want)

    want_sequence = drag_order(key)
    if not want_sequence:
        return 0.0
    got_sequence = _as_sequence(value) or ()
    right = sum(
        1 for i, chip in enumerate(want_sequence)
        if i < len(got_sequence) and got_sequence[i] == chip
    )
    return right / len(want_sequence)


def earned_points(
    question_types: Mapping[str, Any] | None,
    answer_key: Mapping[str, Any] | None,
    answers: Mapping[str, Any] | None,
    weights: Mapping[str, Any] | None,
    total_questions: int,
    question_scoring: Mapping[str, Any] | None = None,
) -> tuple[float, int]:
    """Weighted marks earned, and how many objective questions were graded.

    This is the loop that used to be written out in full in three routes. It
    returns `(earned, graded_count)`, where `graded_count` counts only the
    questions that actually had a key and a weight — which is what the scan route
    reports as "correct out of".
    """
    qtypes = question_types or {}
    key = answer_key or {}
    given = answers or {}
    weight_of = weights or {}
    scoring = question_scoring or {}

    earned = 0.0
    graded = 0
    for i in range(total_questions or 0):
        qi = str(i)
        qtype = qtypes.get(qi, DEFAULT_TYPE)
        key_value = key.get(qi)
        weight = float(weight_of.get(qi, 0) or 0)
        if not is_objective(qtype) or not key_value or weight <= 0:
            continue
        graded += 1
        # Asked per question and not once per paper: whether a question earns a
        # share depends on the type as well as on the paper's scheme. For the types
        # that have never had part credit nothing changes — with no scheme this is
        # still the expression that has marked every paper so far, so a wrong answer
        # stays worth exactly zero.
        if partial_applies(qtype, weight_of):
            earned += weight * part_factor(qtype, key_value, given.get(qi),
                                          scoring_mode(scoring, i))
        elif grade_answer(qtype, key_value, given.get(qi)):
            earned += weight
    return round(earned, 2), graded


# ── the one divisor for a partly keyed paper ────────────────────────────────

@dataclass(frozen=True)
class ObjectiveResult:
    """The auto-graded half of one submission, and what it was measured against.

    `score` is a percentage of the **paper's** objective questions, so it cannot
    reach 100 while any of them has no key. `unkeyed` names the ones that do not,
    which is what lets a caller say *why* the ceiling is where it is.
    """
    score: float
    correct: int
    out_of: int
    keyed: int
    unkeyed: list[int] = field(default_factory=list)

    @property
    def ceiling(self) -> float:
        """The best this submission could have scored against this key."""
        return round((self.keyed / self.out_of) * 100, 2) if self.out_of else 0.0


def objective_result(
    question_types: Mapping[str, Any] | None,
    answer_key: Mapping[str, Any] | None,
    answers: Mapping[str, Any] | None,
    total_questions: int,
) -> ObjectiveResult:
    """The auto-graded score of one submission, by the one rule the app uses.

    Why this function exists
    ------------------------
    Six places computed this score, in four different ways, and two of them
    divided by the number of answers the **key** happens to contain. So a teacher
    who had keyed 2 of 10 MCQ and saved one student's sheet got

        correct 2 / keyed 2  =  100%

    — a perfect mark out of a paper that was two-tenths marked — while the same
    student's online submission was written as the *weighted* objective marks and
    the teacher's own "recalculate scores" wrote `2 / 10 = 20%`. Three different
    numbers for one sitting, chosen by which button was pressed.

    The rule here is the paper's own count as the denominator and the key as the
    only way to earn a mark:

        score = correct / (objective questions on the paper) * 100

    An objective question with no key is therefore scored wrong, which is the safe
    direction and the honest one — it is what `_needs_answer_key` already told the
    teacher happens when the whole key is missing. A partly filled key now
    *depresses the ceiling* instead of inflating the score, and `ceiling` says by
    how much so a page can put a number on the warning.

    Only objective questions are counted; an essay is a teacher's mark and is
    added to the final score by the caller, as it always was.
    """
    qtypes = question_types or {}
    key = answer_key or {}
    given = answers or {}

    out_of = 0
    keyed = 0
    correct = 0
    unkeyed: list[int] = []
    for i in range(total_questions or 0):
        qi = str(i)
        qtype = qtypes.get(qi, DEFAULT_TYPE)
        if not is_objective(qtype):
            continue
        out_of += 1
        value = key.get(qi)
        if not key_has_answer(qtype, value):
            unkeyed.append(i)
            continue
        keyed += 1
        if grade_answer(qtype, value, given.get(qi)):
            correct += 1

    return ObjectiveResult(
        score=round((correct / out_of) * 100, 2) if out_of else 0.0,
        correct=correct,
        out_of=out_of,
        keyed=keyed,
        unkeyed=unkeyed,
    )


def default_weights(
    question_types: Mapping[str, Any] | None,
    total_questions: int,
    objective_pct: float = 70.0,
    essay_pct: float = 30.0,
) -> dict[str, float]:
    """The 70/30 split, distributed equally inside each pool, totalling exactly 100.

    Used when an exam carries no `question_weights` at all. The pools are decided
    by `pool()`, so a true/false question shares the objective budget with the
    MCQs rather than being counted as an essay.

    Kept for the exams that have no stored weights at all; a scheme, when there is
    one, decides the shares instead (see `app/services/mark_scheme.py`).

    **Exactly 100**, and that is a fix rather than a tidy-up. Dividing each pool
    independently and rounding — the obvious version, and this function's first one
    — gave three essays 23.33 points each and a paper that could never reach 100: a
    perfect legacy paper scored 99.99, and `min(earned, 100)` does not repair a
    number that is *below* the cap. `mark_scheme.normalise_to_100` re-bases the whole
    paper in tenths, so the pools keep their 70/30 ratio and the parts add up to the
    paper's own total. It is imported inside the function because `mark_scheme`
    imports this module.
    """
    from app.services import mark_scheme

    qtypes = question_types or {}
    objective = [i for i in range(total_questions or 0) if is_objective(qtypes.get(str(i), DEFAULT_TYPE))]
    essay = [i for i in range(total_questions or 0) if not is_objective(qtypes.get(str(i), DEFAULT_TYPE))]

    if not objective and not essay:
        return {}
    if not objective:
        objective_pct, essay_pct = 0.0, 100.0
    elif not essay:
        objective_pct, essay_pct = 100.0, 0.0

    order = objective + essay
    raw = [objective_pct / len(objective)] * len(objective) if objective else []
    raw += [essay_pct / len(essay)] * len(essay) if essay else []
    return {str(i): point for i, point in zip(order, mark_scheme.normalise_to_100(raw))}
