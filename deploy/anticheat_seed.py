"""The five sittings an anti-cheat dashboard has to be able to read.

Why a generator instead of a fixture of numbers
-----------------------------------------------
Every rule this dashboard will apply is a statement about a **distribution**: "this
absence is unusual for this class", "many students left at the same minute", "five
students is too few to compare". None of those can be checked against a fixture that
stores the summary numbers directly, because such a fixture would encode the answer
the dashboard is supposed to compute — and a test that only agrees with a fixture
tests nothing. So this module stores **events**, the same rows the exam page sends,
and the summary and the class baseline come out of the app's own arithmetic
(`attempt_summary.summarize` / `baseline_rows`). That is also the only way the data a
dashboard is *demonstrated* on and the data it is *tested* on can be guaranteed to be
the same shape.

The five scenarios exist because a dashboard that only ever sees ordinary sittings
looks correct while being useless, and one that only ever sees the worst case looks
paranoid:

* ``normal``           30 ordinary sittings — the reference the others are read
                      against. One of them predates the event pipeline, so its
                      connectivity metrics are *unmeasured* rather than zero.
* ``network_incident`` 28 sittings that all lose connection inside one four-minute
                      window. Every one of them looks individually odd and **not one
                      of them did anything**, which is the case a "worst 10%"
                      ranking gets wrong — the absent time inside that window is as
                      uniform as the class, so no per-student ranking can find it and
                      the dashboard needs a shared-disturbance rule instead.
* ``phone_blur``       7 handsets interrupted three to five times by their own
                      notifications, beside 19 classmates who are not. The first two
                      absences are short and forgiven; the rest are charged *on
                      sight*, with no length recorded — which is what makes
                      ``away_unknown_count`` real.
* ``tail``             3 sittings genuinely far out — a twelve-minute absence, gapped
                      sequence numbers, a moved device clock, a truncated event log —
                      among 21 ordinary ones, so the tail is visible without the
                      median moving with it.
* ``small_class``      5 sittings, so the minimum-n rule has a subject instead of a
                      branch nobody exercises.

Four properties are deliberate, and each one has a test:

**Determinism.** A shape that changes run to run is a flaky test, so the same seed
and day produce identical sessions. The per-student generator is seeded from
``sha256``, never ``hash()``, which is salted per process and would make the data
reproduce on the machine that wrote it and nowhere else.

**No real students.** Every name carries the marker and every address is under
``.invalid``, the TLD reserved for exactly this. The accounts the writer creates get
random passwords that are never printed: they exist to be *read on a dashboard*, not
to be logged in as.

**The NULL rule survives the seed.** A metric nobody measured must reach the
dashboard as NULL, not as zero, and the seed has to be able to produce both cases —
one sitting whose events were never recorded, beside twenty-nine that were.

**Dry by default.** ``ensure()`` plans and prints; ``--write`` is the flag that
touches the database. Five classes of synthetic students are ~113 accounts, and a
seeder that does that by accident is a seeder nobody runs twice.
"""
from __future__ import annotations

import argparse
import hashlib
import random
import sys
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
for _path in (HERE, REPO):        # the sibling specs, and the app itself
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import demo_exam_fixture as fixture               # noqa: E402  (deploy sibling)

#: Appended to every class name and exam title this module creates, so the demo rows
#: are recognisable in the database and removable without guessing.
SEED_MARK = "[seed-anticheat]"

#: The day the scenarios happen, when the caller does not name one. A Monday, and
#: fixed rather than "today" so a test asserting a distribution is not reading the
#: wall clock.
DEFAULT_DAY = date(2026, 9, 21)

#: The school's own clock (WIB), like every other timestamp in this app.
TZ = timezone(timedelta(hours=7))

#: When the paper opens. Every scenario uses the same hour so two classes are
#: comparable without a second variable.
FIRST_BELL = time(7, 30)

#: The grace the app grants before an absence is charged, and how many are forgiven
#: per sitting (`anti_cheat_service.AWAY_GRACE_SECONDS` / `_CHANCES`). Copied rather
#: than imported so the *plan* stays stdlib-only — and pinned by a test, because a
#: seed that grants a different grace would generate absences production charges for.
GRACE_SECONDS = 10
GRACE_CHANCES = 2

#: The event cap the summary reads (`attempt_summary.MAX_EVENTS`). The truncated
#: scenario emits more than this, which is the whole point of it.
SUMMARY_MAX_EVENTS = 1000

#: Synthetic identities. Both are asserted to be un-mailable and marker-bearing.
EMAIL_DOMAIN = "seed-anticheat.invalid"
NAME_PREFIX = "Murid Sintetis"

#: The kinds this module emits, grouped by the vocabulary `attempt_summary` already
#: reads: the absences, the connectivity metrics it sums, and the per-question dwell
#: it builds from `question_index`. A test forbids any kind outside this set, so the
#: seed cannot invent an event the summary silently drops.
AWAY_KINDS = ("tab_switch", "focus_lost", "fullscreen_exit")
CONNECTIVITY_KINDS = ("went_offline", "sync_gap")
ANSWER_KINDS = ("answer_change",)
ALL_KINDS = AWAY_KINDS + CONNECTIVITY_KINDS + ANSWER_KINDS

#: Device classes the events carry. The dashboard is expected to compare a phone
#: against a phone rather than against a laptop, so the field has to be populated.
DEVICE_CLASSES = ("laptop", "phone", "tablet")

#: Where the shared outage happens, and how long it lasts.
#:
#: It has to be **inside** the sitting: an outage the class shares is only a
#: disturbance if the class is still sitting, and a window placed after the last
#: hand-in is a window nobody could have suffered. It was 130 minutes once — an
#: hour past every submission — and the whole suite stayed green, because every
#: other assertion asks what the events say and none asked when they happened. The
#: earliest a sitting ends is 55 minutes after the bell, so this sits well inside
#: all of them and `TestAnEventBelongsToTheSittingItDescribes` holds it there.
INCIDENT_AFTER_MINUTES = 28
INCIDENT_MINUTES = 4

#: How many handsets are interrupted in `phone_blur`.
HANDSETS = 7

#: The three far-out shapes in `tail`, in the order they are assigned.
TAIL_SHAPES = ("long_absence", "gapped_sequence", "moved_clock")


# ── the plan ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Scenario:
    """One class's sitting, described as the shape of behaviour it contains."""

    name: str
    label: str
    title: str
    students: int
    why: str
    small: bool = False


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        name="normal",
        label="Kelas 7A",
        title="Ulangan Harian 7A",
        students=30,
        why="The reference: ordinary sittings, nothing charged, nothing unusual — "
            "and one of them older than the event pipeline, so its connectivity "
            "metrics must read as unmeasured rather than zero.",
    ),
    Scenario(
        name="network_incident",
        label="Kelas 7B",
        title="Ulangan Harian 7B",
        students=28,
        why="Every student loses the connection inside one four-minute window. No "
            "student did anything, and a per-student ranking cannot say so.",
    ),
    Scenario(
        name="phone_blur",
        label="Kelas 8A",
        title="Ulangan Harian 8A",
        students=26,
        why="Seven handsets are interrupted repeatedly by their own notifications, "
            "beside nineteen classmates who are not.",
    ),
    Scenario(
        name="tail",
        label="Kelas 8B",
        title="Ulangan Harian 8B",
        students=24,
        why="Three sittings genuinely far out — a long absence, a lost sequence "
            "number, a moved clock, a truncated log — in an otherwise ordinary class.",
    ),
    Scenario(
        name="small_class",
        label="Kelas 9A",
        title="Ulangan Harian 9A",
        students=5,
        small=True,
        why="Five students: below the minimum a distribution can be read against.",
    ),
)

SCENARIO_NAMES = tuple(s.name for s in SCENARIOS)

EVENT_TABLE = "attempt_session_events"
VIOLATION_TABLE = "violation_logs"


@dataclass(frozen=True)
class Session:
    """One student's sitting, as the rows the app would have stored."""

    student_key: str
    display_name: str
    email: str
    scenario: str
    device_class: str
    started_at: datetime
    submitted_at: datetime
    #: `attempt_session_events`-shaped rows, numbered from 1.
    events: tuple[dict, ...] = ()
    #: `violation_logs`-shaped rows: the absences the ladder actually charged.
    violations: tuple[dict, ...] = ()
    #: False for a sitting older than the event pipeline: its events were never
    #: recorded at all, which is the case that must reach the dashboard as NULL.
    event_source: bool = True
    note: str = ""

    @property
    def sitting_ms(self) -> int:
        return int((self.submitted_at - self.started_at).total_seconds() * 1000)

    @property
    def sources(self) -> tuple[str, ...]:
        """Which tables contributed rows, in the shape `record_for_attempt` records.

        `violation_logs` is read on every attempt; the event table is read only when
        this release's pipeline stored anything for it. A summary whose sources lack
        the event table has *unmeasured* connectivity, not zero.
        """
        return (VIOLATION_TABLE,) + ((EVENT_TABLE,) if self.event_source else ())

    def offset_of(self, when: datetime) -> float:
        return (when - self.started_at).total_seconds()


@dataclass(frozen=True)
class ClassPlan:
    """A class, the exam its students sat, and every sitting in it."""

    scenario: Scenario
    day: date
    sessions: tuple[Session, ...]
    #: The window a shared disturbance happens in, when the scenario has one. Kept
    #: as data rather than prose so a test can ask whether the dashboard's context
    #: rule could have found it.
    incident_window: tuple[datetime, datetime] | None = None
    meta: dict = field(default_factory=dict)

    @property
    def exam_title(self) -> str:
        return f"{self.scenario.title} {SEED_MARK}"

    @property
    def class_name(self) -> str:
        return f"{self.scenario.label} {SEED_MARK}"

    @property
    def size(self) -> int:
        return len(self.sessions)

    @property
    def grade_level(self) -> int:
        digits = "".join(c for c in self.scenario.label if c.isdigit())
        return int(digits[0]) if digits else 7


def is_seed_name(value) -> bool:
    """Is this row ours? The marker, not a prefix of a title."""
    return SEED_MARK in str(value or "")


# ── generating one sitting ───────────────────────────────────────────────────

def _rng(*parts) -> random.Random:
    """A generator seeded from the parts, stable across processes and versions."""
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _student_key(scenario: str, index: int) -> str:
    return f"{scenario}-{index:02d}"


def _identity(scenario: str, index: int) -> tuple[str, str]:
    slug = scenario.replace("_", "").title()
    return (f"{NAME_PREFIX} {slug}-{index:02d} {SEED_MARK}",
            f"seed+{scenario}-{index:02d}@{EMAIL_DOMAIN}")


class _EventLog:
    """A sitting's events, numbered the way the client numbers them.

    ``seq`` is the interesting field: it is what makes a gap visible, and a gap is
    information (an event was lost or edited) rather than a formatting detail. So the
    numbering lives here, with one method that can skip a number on purpose.
    """

    def __init__(self, started_at: datetime, device_class: str):
        self.started_at = started_at
        self.device_class = device_class
        self.rows: list[dict] = []
        self.seq = 0

    def add(self, kind: str, offset_s: float, duration_s: float | None = None,
            *, offline: bool = False, question: int | None = None,
            clock_shift_s: float = 0.0) -> None:
        """Add one event, ``offset_s`` seconds into the sitting."""
        self.seq += 1
        server_at = self.started_at + timedelta(seconds=offset_s)
        occurred_at = server_at + timedelta(seconds=clock_shift_s)
        self.rows.append({
            "seq": self.seq,
            "kind": kind,
            "occurred_at": occurred_at.isoformat(),
            "server_at": server_at.isoformat(),
            "duration_ms": int(round(duration_s * 1000)) if duration_s is not None else None,
            "offline": bool(offline),
            "device_class": self.device_class,
            "question_index": question,
            "meta": {"synthetic": True},
        })

    def absorb(self, rows) -> None:
        """Copy earlier rows in, keeping the numbering continuous."""
        for row in rows:
            self.rows.append(dict(row))
            self.seq = max(self.seq, int(row["seq"]))

    def lose_next_number(self) -> None:
        """Drop one sequence number, as a lost or edited event would."""
        self.seq += 1


def _violation(student_key: str, exam_key: str, when: datetime, kind: str,
               away_seconds: float | None = None, trigger: str = "synthetic") -> dict:
    """A charged absence, in the shape ``violation_logs`` stores."""
    meta: dict = {"synthetic": True, "trigger": trigger}
    if away_seconds is not None:
        meta["away_seconds"] = away_seconds
    return {
        "user_id": student_key,
        "exam_id": exam_key,
        "violation_type": kind,
        "created_at": when.isoformat(),
        "metadata": meta,
    }


def _ordinary_sitting(scenario: Scenario, index: int, day: date, seed) -> Session:
    """The baseline shape: a student who starts, works, and stops."""
    rng = _rng(scenario.name, index, seed, "ordinary")
    key = _student_key(scenario.name, index)
    name, email = _identity(scenario.name, index)
    device = "laptop" if scenario.small else rng.choices(
        DEVICE_CLASSES, weights=(0.7, 0.2, 0.1))[0]
    started = (datetime.combine(day, FIRST_BELL, tzinfo=TZ)
               + timedelta(seconds=rng.randint(0, 90)))
    sitting_s = rng.randint(55 * 60, 62 * 60)
    log = _EventLog(started, device)

    # A student who never leaves the page is the common case, and it has to be
    # generated: a seed where everybody left twice teaches a dashboard that "left
    # twice" is the norm.
    if rng.random() < 0.35:
        log.add("answer_change", rng.uniform(120, sitting_s - 60), question=rng.randint(0, 4))
    if rng.random() < 0.25:
        log.add("sync_gap", rng.uniform(300, sitting_s - 300), duration_s=rng.uniform(1, 4))
    if rng.random() < 0.30:
        log.add(rng.choice(AWAY_KINDS), rng.uniform(200, sitting_s - 200),
                duration_s=rng.uniform(2, GRACE_SECONDS - 1), question=rng.randint(0, 4))

    return Session(
        student_key=key, display_name=name, email=email, scenario=scenario.name,
        device_class=device, started_at=started,
        submitted_at=started + timedelta(seconds=sitting_s),
        events=tuple(log.rows), violations=(), note="ordinary sitting",
    )


def _incident_sitting(scenario: Scenario, index: int, day: date, seed,
                      window: tuple[datetime, datetime]) -> Session:
    """A student whose whole class lost the connection at the same minute.

    The outage is placed inside the shared window and nowhere else, which is what
    makes the scenario testable: every student's only connectivity loss is inside it,
    so the class is *uniform* and yet each member looks unusual on their own.
    """
    session = _ordinary_sitting(scenario, index, day, seed)
    rng = _rng(scenario.name, index, seed, "incident")
    start, end = window
    span_s = (end - start).total_seconds()
    offset = session.offset_of(start)
    log = _EventLog(session.started_at, session.device_class)
    log.absorb(session.events)
    outage_s = rng.uniform(90, min(210, span_s))
    at = offset + rng.uniform(0, max(1.0, span_s - outage_s))
    log.add("went_offline", at, duration_s=outage_s, offline=True)
    log.add("sync_gap", at + outage_s, duration_s=rng.uniform(1, 5))
    return replace(
        session,
        submitted_at=session.submitted_at + timedelta(seconds=outage_s),
        events=tuple(log.rows),
        note="offline inside the class's own four-minute window",
    )


def _phone_sitting(scenario: Scenario, index: int, day: date, seed) -> Session:
    """A handset interrupted by its own notifications, three to five times.

    The first two absences are short and forgiven (`GRACE_CHANCES`); the rest are
    charged *on sight* — recorded with no length at all, exactly as the ladder writes
    them. That is why this scenario is the one that carries
    ``away_unknown_count``: the interesting fact is not how long the third absence
    lasted, it is that nobody measured it.
    """
    session = _ordinary_sitting(scenario, index, day, seed)
    rng = _rng(scenario.name, index, seed, "phone")
    log = _EventLog(session.started_at, "phone")
    log.absorb(session.events)

    count = rng.randint(3, 5)
    offsets = sorted(rng.uniform(180, session.sitting_ms / 1000 - 120)
                     for _ in range(count))
    violations = []
    forgiven = 0
    for position, offset in enumerate(offsets):
        if position < GRACE_CHANCES:
            log.add("focus_lost", offset, duration_s=rng.uniform(2, GRACE_SECONDS - 1),
                    question=rng.randint(0, 4))
            forgiven += 1
        else:
            log.add("focus_lost", offset, question=rng.randint(0, 4))
            violations.append(_violation(
                session.student_key, session.scenario,
                session.started_at + timedelta(seconds=offset),
                kind="focus_lost", trigger="notification"))
    return replace(
        session, device_class="phone", events=tuple(log.rows),
        violations=tuple(violations),
        note=f"{count} short interruptions ({forgiven} forgiven, "
             f"{count - forgiven} charged on sight)",
    )


def _tail_sitting(scenario: Scenario, index: int, day: date, seed,
                  shape: str) -> Session:
    """One of the three sessions that are genuinely far out."""
    session = _ordinary_sitting(scenario, index, day, seed)
    rng = _rng(scenario.name, index, seed, shape)
    log = _EventLog(session.started_at, session.device_class)
    log.absorb(session.events)
    violations: list[dict] = []

    if shape == "long_absence":
        # Twelve minutes away, charged, with its length recorded — the difference
        # between a callback and a phone call that lasted.
        at = rng.uniform(400, 1200)
        log.add("tab_switch", at, duration_s=720, question=rng.randint(0, 4))
        violations.append(_violation(
            session.student_key, session.scenario,
            session.started_at + timedelta(seconds=at),
            kind="tab_switch", away_seconds=720, trigger="away"))
        log.add("focus_lost", at + 900, duration_s=300)
        violations.append(_violation(
            session.student_key, session.scenario,
            session.started_at + timedelta(seconds=at + 900),
            kind="focus_lost", away_seconds=300, trigger="away"))
        note = "a twelve-minute absence, and a second long one"
    elif shape == "gapped_sequence":
        # The numbers are the evidence: 3 is missing, so an event was lost or edited.
        log.add("answer_change", 200, question=1)
        log.add("fullscreen_exit", 420, duration_s=14)
        log.lose_next_number()
        log.add("answer_change", 640, question=2)
        log.add("answer_change", 900, question=3)
        note = "one sequence number missing between two events"
    elif shape == "moved_clock":
        # The device's clock is five minutes behind ours. Both readings are kept, so
        # the difference is the evidence a reader sees as `clock_suspect`.
        log.add("focus_lost", 500, duration_s=6, clock_shift_s=-300)
        log.add("answer_change", 700, question=4, clock_shift_s=-300)
        note = "the device clock is five minutes behind the server's"
    else:                                   # pragma: no cover - guarded by the caller
        note = "tail"

    return replace(
        session,
        submitted_at=session.submitted_at + timedelta(seconds=rng.uniform(60, 300)),
        events=tuple(log.rows), violations=tuple(violations), note=note,
    )


def _truncated_sitting(scenario: Scenario, index: int, day: date, seed) -> Session:
    """A sitting with more events than the summary reads, so `truncated` is real."""
    session = _ordinary_sitting(scenario, index, day, seed)
    log = _EventLog(session.started_at, session.device_class)
    log.absorb(session.events)
    sitting_s = int(session.sitting_ms / 1000)
    for step in range((SUMMARY_MAX_EVENTS + 200) // 2):
        at = (step % sitting_s) + 1.0
        log.add("answer_change", at, question=step % 5)
        log.add("focus_lost", at + 0.5, duration_s=3)
    return replace(
        session, events=tuple(log.rows),
        note=f"{len(log.rows)} events — more than the summary reads "
             f"({SUMMARY_MAX_EVENTS}), so it must be flagged as truncated",
    )


def _pre_pipeline_sitting(scenario: Scenario, index: int, day: date, seed) -> Session:
    """A sitting older than the event pipeline: no events were ever recorded.

    This is the case the NULL rule exists for. Its summary can say what
    ``violation_logs`` charged and nothing at all about connectivity, and the
    difference must survive all the way to the dashboard.
    """
    session = _ordinary_sitting(scenario, index, day, seed)
    one_short_absence = _violation(
        session.student_key, session.scenario,
        session.started_at + timedelta(seconds=900),
        kind="focus_lost", away_seconds=4, trigger="away")
    return replace(
        session, events=(), violations=(one_short_absence,), event_source=False,
        note="recorded before the event pipeline existed — connectivity is "
             "unmeasured, not zero",
    )


# ── the five plans ───────────────────────────────────────────────────────────

def plan(name: str, *, seed=DEFAULT_DAY, day: date = DEFAULT_DAY) -> ClassPlan:
    """One scenario's whole class, deterministically."""
    scenario = next((s for s in SCENARIOS if s.name == name), None)
    if scenario is None:
        raise ValueError(f"unknown scenario {name!r} (have: {', '.join(SCENARIO_NAMES)})")

    window = None
    sessions: list[Session] = []

    if scenario.name == "network_incident":
        start = (datetime.combine(day, FIRST_BELL, tzinfo=TZ)
                 + timedelta(minutes=INCIDENT_AFTER_MINUTES))
        window = (start, start + timedelta(minutes=INCIDENT_MINUTES))
        sessions = [_incident_sitting(scenario, i, day, seed, window)
                    for i in range(scenario.students)]
    elif scenario.name == "phone_blur":
        for index in range(scenario.students):
            if index < HANDSETS:
                sessions.append(_phone_sitting(scenario, index, day, seed))
            else:
                # The scenario's claim is "every handset in this class is the one
                # being interrupted", so a classmate is never generated on a phone:
                # a random handset among the quiet students would make the pattern
                # unreadable and the scenario's own name false.
                classmate = _ordinary_sitting(scenario, index, day, seed)
                sessions.append(replace(
                    classmate, device_class="tablet" if index % 3 == 0 else "laptop"))
    elif scenario.name == "tail":
        for index in range(scenario.students):
            if index < len(TAIL_SHAPES):
                sessions.append(_tail_sitting(scenario, index, day, seed,
                                              TAIL_SHAPES[index]))
            elif index == len(TAIL_SHAPES):
                sessions.append(_truncated_sitting(scenario, index, day, seed))
            else:
                sessions.append(_ordinary_sitting(scenario, index, day, seed))
    else:
        sessions = [_ordinary_sitting(scenario, i, day, seed)
                    for i in range(scenario.students)]
        # The last one is older than the pipeline, so one class carries both the
        # measured and the unmeasured case.
        sessions[-1] = _pre_pipeline_sitting(scenario, scenario.students - 1, day, seed)

    return ClassPlan(scenario=scenario, day=day, sessions=tuple(sessions),
                     incident_window=window)


def plans(*, seed=DEFAULT_DAY, day: date = DEFAULT_DAY) -> list[ClassPlan]:
    """All five scenarios, in the order they are documented."""
    return [plan(name, seed=seed, day=day) for name in SCENARIO_NAMES]


# ── measuring the plan with the app's own arithmetic ─────────────────────────

def summary_for(session: Session, *, attempt_id: str | None = None) -> dict:
    """One sitting's summary, measured by the code production measures with.

    The event read is capped the way `record_for_attempt` caps it — violations
    whole, then at most ``MAX_EVENTS + 1`` events — because that cap is visible in
    the result: a sitting with 1200 events reports ``events_seen = 1001`` from the
    database, and a plan measured from all 1200 would disagree with it about exactly
    the sittings that overflow.
    """
    from app.services import attempt_summary as summarizer

    events = list(session.violations) + list(session.events)[:summarizer.MAX_EVENTS + 1]
    payload = summarizer.summarize(events, list(session.sources),
                                   sitting_ms=session.sitting_ms)
    payload.update({
        "attempt_id": attempt_id or f"seed:{session.student_key}",
        "exam_id": f"seed:{session.scenario}",
        "student_id": f"seed:{session.student_key}",
    })
    return payload


def summaries_for(plan_: ClassPlan) -> list[dict]:
    """Every sitting's summary in one class."""
    return [summary_for(session) for session in plan_.sessions]


def baseline_for(plan_: ClassPlan) -> list[dict]:
    """The class baseline, from the app's own percentile convention."""
    from app.services import attempt_summary as summarizer

    return summarizer.baseline_rows(summaries_for(plan_),
                                    exam_id=f"seed:{plan_.scenario.name}",
                                    class_id=f"seed:{plan_.scenario.name}")


def incident_share(plan_: ClassPlan, window_s: int | None = None) -> float:
    """What share of the class has an event inside the shared window.

    The number the shared-disturbance rule is a statement about, so both the tests
    and the dry run read it from one place.
    """
    if not plan_.incident_window:
        return 0.0
    start, end = plan_.incident_window
    if window_s is not None:
        end = start + timedelta(seconds=window_s)
    inside = 0
    for session in plan_.sessions:
        for row in session.events:
            when = datetime.fromisoformat(row["server_at"])
            if start <= when <= end:
                inside += 1
                break
    return inside / max(1, plan_.size)


def busiest_minute(plan_: ClassPlan, bucket_s: int = 60) -> tuple[float, float]:
    """The fullest minute: (share of the class present, its p50 absence count).

    Answers "is there a moment this whole class shares?", which is what separates the
    network incident from the handset interruptions.
    """
    buckets: dict[int, set[str]] = {}
    for session in plan_.sessions:
        for row in session.events:
            when = datetime.fromisoformat(row["server_at"])
            slot = int(when.timestamp() // bucket_s)
            buckets.setdefault(slot, set()).add(session.student_key)
    if not buckets:
        return 0.0, 0.0
    slot = max(buckets, key=lambda s: len(buckets[s]))
    return len(buckets[slot]) / max(1, plan_.size), float(slot)


# ── the demo write ───────────────────────────────────────────────────────────

def _find_one(supabase, table: str, column: str, value, school_id: str):
    rows = (supabase.table(table).select("id," + column)
            .eq("school_id", school_id).execute().data or [])
    for row in rows:
        if row.get(column) == value:
            return row["id"]
    return None


def _class_id(supabase, school_id: str, plan_: ClassPlan, dry_run: bool) -> str | None:
    found = _find_one(supabase, "classes", "name", plan_.class_name, school_id)
    if found or dry_run:
        return found
    created = supabase.table("classes").insert({
        "name": plan_.class_name, "school_id": school_id,
        "grade_level": str(plan_.grade_level),
    }).execute().data or []
    return created[0]["id"] if created else None


def _exam_id(supabase, school_id: str, plan_: ClassPlan, class_id: str | None,
             dry_run: bool) -> str | None:
    found = _find_one(supabase, "exams", "title", plan_.exam_title, school_id)
    if found or dry_run:
        return found
    teacher_id = None
    for row in (supabase.table("teacher_assignments").select("teacher_id")
                .eq("school_id", school_id).limit(1).execute().data or []):
        teacher_id = row.get("teacher_id")
    if not teacher_id:
        gurus = (supabase.table("profiles").select("id")
                 .eq("school_id", school_id).eq("role", "guru").limit(1).execute().data or [])
        teacher_id = gurus[0]["id"] if gurus else None
    if not teacher_id:
        return None
    payload = {
        "teacher_id": teacher_id, "school_id": school_id,
        "title": plan_.exam_title, "subject": fixture.SUBJECT,
        "duration_minutes": 60, "total_questions": 5, "passing_score": 70,
        "question_types": fixture.QUESTION_TYPES, "answer_key": fixture.ANSWER_KEY,
        "question_weights": fixture.QUESTION_WEIGHTS,
        "status": "active", "is_published": True, "publish_mode": "auto",
        "class_ids": [class_id] if class_id else [],
        "start_at": None, "end_at": None,
        # The same anti-cheat spec the deploy fixture uses: one definition of what an
        # armed exam is, in the module that already owns it.
        **fixture.ANTI_CHEAT,
    }
    created = supabase.table("exams").insert(payload).execute().data or []
    return created[0]["id"] if created else None


def _write_session(supabase, school_id: str, exam_id: str, class_id: str | None,
                   session: Session, say) -> str | None:
    """One student's account, attempt, events and summary. Returns the attempt id."""
    from app.services import attempt_summary as summarizer

    uid = None
    try:
        created = supabase.auth.admin.create_user({
            "email": session.email,
            "password": hashlib.sha256(session.email.encode()).hexdigest()[:24],
            "user_metadata": {"role": "murid", "full_name": session.display_name},
            "email_confirm": True,
        })
        uid = created.user.id
    except Exception as error:                                  # noqa: BLE001
        if "already" not in str(error).lower():
            say(f"   ⚠️  {session.email}: {str(error)[:70]}")
            return None
        try:
            for user in supabase.auth.admin.list_users():
                if getattr(user, "email", None) == session.email:
                    uid = user.id
                    break
        except Exception:                                       # noqa: BLE001
            return None
    if not uid:
        return None

    # The account's password is random by construction and never printed: these
    # students exist to be read on a dashboard, not to be logged in as.
    supabase.table("profiles").upsert({
        "id": uid, "full_name": session.display_name, "role": "murid",
        "school_id": school_id, "class_id": class_id,
    }).execute()
    supabase.table("students").upsert({
        "id": uid, "school_id": school_id, "nisn": "",
    }).execute()

    attempt = supabase.table("submissions").upsert({
        "exam_id": exam_id, "student_id": uid, "status": "submitted",
        "answers": {}, "started_at": session.started_at.isoformat(),
        "submitted_at": session.submitted_at.isoformat(),
    }, on_conflict="exam_id,student_id").execute().data or []
    attempt_id = attempt[0]["id"] if attempt else None
    if not attempt_id:
        say(f"   ⚠️  {session.email}: the attempt could not be written")
        return None

    # Idempotent on re-run: this sitting's own rows are replaced, never appended.
    supabase.table(EVENT_TABLE).delete().eq("attempt_id", attempt_id).execute()
    supabase.table(VIOLATION_TABLE).delete().eq("exam_id", exam_id).eq(
        "user_id", uid).execute()
    if session.violations:
        supabase.table(VIOLATION_TABLE).insert([
            {**row, "exam_id": exam_id, "user_id": uid} for row in session.violations
        ]).execute()
    if session.events:
        supabase.table(EVENT_TABLE).insert([
            {**row, "attempt_id": attempt_id} for row in session.events
        ]).execute()

    if session.event_source:
        # Measured by production's own reader, from the rows that are now in the
        # database — not from the plan. The demo data and the data the tests check
        # are therefore the same measurement, by construction.
        summarizer.record_for_attempt(supabase, attempt_id, exam_id, uid,
                                      school_id=school_id, sitting_ms=session.sitting_ms)
    else:
        # A sitting older than the event pipeline. Its summary is the row a release
        # *without* that pipeline would have stored: measured from `violation_logs`
        # alone, so connectivity stays unmeasured instead of reading as zero. The
        # reader below it cannot produce this state today — it names the event table
        # as a source whenever the read succeeds, even when nothing came back — and
        # a dashboard still has to be able to read a row like this one.
        payload = summarizer.summarize(list(session.violations), [VIOLATION_TABLE],
                                       sitting_ms=session.sitting_ms)
        payload.update({"attempt_id": attempt_id, "exam_id": exam_id,
                        "student_id": uid, "school_id": school_id})
        supabase.table("attempt_summary").upsert(
            payload, on_conflict="attempt_id").execute()
    return attempt_id


def ensure(supabase, school_id: str, *, seed=DEFAULT_DAY, day: date = DEFAULT_DAY,
           dry_run: bool = True, say=print) -> dict:
    """Write the five scenarios into one school's demo data.

    Dry by default, idempotent by name, and scoped to the school it is given: the
    classes and exams are found by their marker-stamped names and written over, and
    every sitting's own events and summary are replaced rather than appended to.
    """
    made = {"classes": 0, "exams": 0, "sessions": 0, "skipped": 0, "dry_run": dry_run}
    for plan_ in plans(seed=seed, day=day):
        say(f"\n── {plan_.scenario.label} ({plan_.size} sittings) {plan_.scenario.why}")
        class_id = _class_id(supabase, school_id, plan_, dry_run)
        exam_id = _exam_id(supabase, school_id, plan_, class_id, dry_run)
        if dry_run:
            say(f"   would write class {plan_.class_name!r} and exam "
                f"{plan_.exam_title!r}")
            for session in plan_.sessions:
                say(f"   · {session.student_key:12} {session.device_class:6} "
                    f"{len(session.events):5} event(s), {len(session.violations)} "
                    f"charged — {session.note}")
            made["classes"] += 1
            made["exams"] += 1
            made["sessions"] += plan_.size
            continue

        if not (class_id and exam_id):
            say("   ⚠️  no class or no exam could be written — is this school empty?")
            made["skipped"] += plan_.size
            continue
        for session in plan_.sessions:
            if _write_session(supabase, school_id, exam_id, class_id, session, say):
                made["sessions"] += 1
            else:
                made["skipped"] += 1
        # The baseline is derived, so it is produced by the same reader the dashboard
        # will call rather than by this module's own arithmetic.
        from app.services import attempt_summary as summarizer
        summarizer.baseline_for(supabase, exam_id, class_id)
        made["classes"] += 1
        made["exams"] += 1
        say(f"   ✅ {plan_.size} sitting(s) written and measured")

    say(f"\n{'DRY RUN — nothing was written' if dry_run else 'written'}: "
        f"{made['classes']} class(es), {made['exams']} exam(s), "
        f"{made['sessions']} sitting(s)"
        + (f", {made['skipped']} skipped" if made["skipped"] else ""))
    return made


def clear(supabase, school_id: str, *, say=print) -> dict:
    """Remove this module's demo rows, by marker. Nothing a teacher made is touched.

    The accounts are deleted (profiles and attempts cascade with them); the classes
    and exams go with them, since they own the rows that point at them.
    """
    removed = {"exams": 0, "classes": 0, "accounts": 0}

    for row in (supabase.table("exams").select("id,title")
                .eq("school_id", school_id).execute().data or []):
        if is_seed_name(row.get("title")):
            supabase.table("exams").delete().eq("id", row["id"]).execute()
            removed["exams"] += 1
    for row in (supabase.table("classes").select("id,name")
                .eq("school_id", school_id).execute().data or []):
        if is_seed_name(row.get("name")):
            supabase.table("classes").delete().eq("id", row["id"]).execute()
            removed["classes"] += 1

    # One listing for the whole run: `list_users` is paginated and slow, and asking
    # it once per sitting would make clearing five classes a minute of round-trips.
    wanted = {session.email for plan_ in plans() for session in plan_.sessions}
    try:
        for user in (supabase.auth.admin.list_users() or []):
            if getattr(user, "email", None) in wanted:
                supabase.auth.admin.delete_user(user.id)
                removed["accounts"] += 1
    except Exception as error:                                  # noqa: BLE001
        say(f"   ⚠️  the synthetic accounts could not be listed: {str(error)[:70]}")

    say(f"removed: {removed}")
    return removed


# ── the command line ─────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--day", default=DEFAULT_DAY.isoformat(),
                        help="the school day the sittings happen on")
    parser.add_argument("--incident-share", action="store_true",
                        help="print the shared-window and busiest-minute readings")
    args = parser.parse_args(argv)

    day = date.fromisoformat(args.day)
    for plan_ in plans(day=day):
        summaries = summaries_for(plan_)
        out_of = [s for s in summaries if s["sync_gap_count"] is None]
        print(f"{plan_.scenario.name:17} n={plan_.size:3} "
              f"events={sum(len(s.events) for s in plan_.sessions):6} "
              f"charged={sum(len(s.violations) for s in plan_.sessions):3} "
              f"unmeasured={len(out_of)}")
        if args.incident_share:
            share, _ = busiest_minute(plan_)
            print(f"   shared window: {incident_share(plan_):.0%} of the class · "
                  f"busiest minute: {share:.0%} of the class")
    return 0


if __name__ == "__main__":
    sys.exit(main())
