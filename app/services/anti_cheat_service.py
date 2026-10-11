import json
import time
from datetime import datetime, timezone
from flask import current_app

from app.utils.logger import get_logger

#: The read paths below log through this rather than `current_app.logger`, because
#: they have to degrade to an empty list wherever they are called from — including
#: the report assembly that runs outside a request (the printed sheet and the
#: exports build their rows from `_exam_results` with no request context at all).
#: `current_app` raises in exactly that case, which turns "the log could not be
#: read" into a stack trace on a page whose only sin was asking.
logger = get_logger("anti_cheat")


RATE_LIMIT_SECONDS = 2
TIMESTAMP_TOLERANCE = 900

# ── The second chance ────────────────────────────────────────────────────────
#
# An absence is not charged the moment it is seen. It starts a countdown: the
# paper blurs at once, and the student has this many seconds to come back — a
# phone call, a notification, a knock at the door — before the absence is
# recorded as a violation. Returning inside the countdown costs nothing at all.
#
# Two numbers, and they are decisions rather than tunables to taste.
#
# **The protection does not wait for either of them.** The blur is raised when
# the absence is seen, not when the countdown ends, so the grace can only ever
# forgive a penalty; it can never expose a question. That is what makes a grace
# safe to offer at all.
#
# **The countdown is bounded** (`AWAY_GRACE_CHANCES` per sitting). An unbounded
# grace would be a repeatable free window — look question 7 up for nine seconds,
# return, look up question 8 — and the deterrent is the whole product claim. One
# choice covers the accident the feature exists for; the second covers the day
# the accident happens twice. From the third absence the ladder is exactly what
# it was before this feature. The panel says how many are left, so the number is
# never a surprise, and `metadata.away_seconds` on the recorded row says how long
# the student was actually gone, which is the difference between a callback and a
# phone call that lasted four minutes.
AWAY_GRACE_SECONDS = 10
AWAY_GRACE_CHANCES = 2

# Leaving the exam counts, however it is left.
#
# `tab_switch` is switching tabs or apps. `fullscreen_exit` is leaving the
# required fullscreen state — pressing Esc, restoring the window down, or
# minimising it — which the exam page now blocks behind an overlay rather than
# only announcing. Both are the same act from the school's point of view
# (the assessment is no longer on the screen it was put on), so they share the
# one ladder: a student's first offence of either kind is the warning, and the
# penalty starts from the second.
#
# `fullscreen_exit` used to be recorded but not counted, because the page
# promised it carried no penalty. That promise is gone: fullscreen is mandatory
# during an assessed exam, and the page says so on its agreement screen. Rows
# written under the old policy are indistinguishable in the table, which is why
# the reason lives here rather than in a migration note.
# `focus_lost` is the third, and it is the one the away-blur exists for: the
# exam window is still *on* the screen, it is just no longer the window the
# student is attending to — a restored-down browser beside another application,
# or a click on the desktop. The paper is blurred in that state, and the blur
# used to be the whole of it: the window-blur handler logged a line to the
# console and recorded nothing, so a student could read the paper off a windowed
# exam for a whole sitting with no charge and, more to the point for the school,
# no record that it happened. It is charged on the same ladder as a tab switch,
# because from the school's point of view it is the same act: the assessment is
# no longer the thing being looked at. One absence is still one charge — the
# fullscreen overlay and the hidden-document path each own their own case, and
# the guards below keep one absence from being billed by two of them.
PENALIZED_VIOLATION_TYPES = ("tab_switch", "fullscreen_exit", "focus_lost")

# ── A rotated screen is not a pupil who left ─────────────────────────────────
#
# Reported from a tablet lab: a pupil who turned the screen was walked down the
# same ladder as a pupil who left the exam. Rotating *does* drop the fullscreen
# state on several tablet browsers — it is a side effect of the transition, not
# an act — and the page charged it as `fullscreen_exit` because nothing in the
# detection knew an orientation change had just happened.
#
# So the page now watches the orientation itself, and a fullscreen loss that
# lands inside this window is treated as the transition's rather than the
# pupil's: the overlay still goes up at once (the protection never waits — the
# same rule the away-grace above rests on), but nothing is charged. What happens
# when the window closes is the part that stops this being a free window:
#
#   * **it came back on its own** — every browser that can re-enter without a
#     fresh gesture — and nothing happened at all;
#   * **it did not, on a platform that holds fullscreen across a rotation** — the
#     absence outlived its explanation, so it is charged as the real exit it is;
#   * **it did not, on a platform whose fullscreen a rotation *drops* and which
#     cannot re-enter without a fresh gesture** (iPadOS Safari) — charging there
#     would bill a pupil for their device. It is recorded instead, as
#     `orientation_shift`: present in the teacher's record, absent from the
#     ladder.
#
# The window is deliberately short. It has to cover a device's rotation
# animation and the browser's own `fullscreenchange`, and anything longer would
# be a window in which a pupil could keep the paper on screen by rocking the
# tablet.
ROTATION_GRACE_SECONDS = 1.5

#: A rotation the page saw while the paper was out of fullscreen. Recorded, never
#: counted: `_as_event` marks any kind outside PENALIZED_VIOLATION_TYPES as
#: `charged: False`, so it appears in the teacher's record with its own label and
#: adds nothing to the ladder. Kept as a name rather than a literal because three
#: files spell it — the page sends it, the report labels it, the tests pin it.
ORIENTATION_SHIFT = "orientation_shift"

#: How a recorded kind reads to a teacher, as an (Indonesian, English) pair.
#: One table, so the pinned per-paper page and the bilingual results list cannot
#: describe the same event two ways.
KIND_LABELS = {
    "tab_switch": ("Berpindah tab atau aplikasi", "Switched tab or app"),
    "fullscreen_exit": ("Keluar dari layar penuh", "Left fullscreen"),
    "focus_lost": ("Jendela ujian ditinggalkan", "Left the exam window"),
    ORIENTATION_SHIFT: ("Layar diputar (bukan pelanggaran)",
                        "Screen rotated (not a violation)"),
}


# ── The school's own switch, read in one place ───────────────────────────────
#
# `exams.anti_cheat_enabled` decides whether a paper counts violations at all, and
# four callers need that answer: the door that writes a row, the ladder that prices
# it, the count that feeds both the ladder and the sitting's stored penalty, and the
# resume-code lock that trips at the threshold. Each of them used to ask for itself
# — `is not False` spelled out in the route, in the ladder and in `resume_code` —
# which is how the log was the one half that wrote a row for a paper the school had
# switched off while the ladder charged nothing for it. The answer is here now, so
# there is one place to read and one place to change.
#
# The comparison is `is not False` on purpose, and it is not the same as truthiness:
# a row that could not be read, a column a migration has not added yet, or a null is
# *not* a school asking for silence. Only an explicit `false` switches a paper off.
#: Why a violation this paper does not count is refused. A named reason rather than
#: a sentence, because the exam page matches on it to stay quiet.
DISABLED_REASON = "anti_cheat_disabled"

#: What the ladder answers for a paper the school switched off: nothing charged, no
#: warning, no auto-submit. A value rather than four zeros in two places.
NONE_CHARGED = {"penalty": 0.0, "warning": False, "auto_submit": False,
                "current_penalty_this_violation": 0.0}


def enabled(exam_settings: dict | None) -> bool:
    """Whether this paper counts anti-cheat violations at all.

    The one reader of `anti_cheat_enabled`. A paper that is switched off records
    nothing, charges nothing, counts nothing and never locks — and every one of
    those four answers comes from here, so they cannot disagree.
    """
    return (exam_settings or {}).get("anti_cheat_enabled") is not False


def refusal(exam_settings: dict | None) -> dict | None:
    """The answer a violation gets from a paper that does not count it.

    ``None`` means *record this one*; anything else is the whole refusal, shaped the
    way the log endpoint answers (``{"logged": False, "reason": ...}``) so a caller
    relays it rather than building a second shape of its own. The decision belongs
    to the server and not the page: a hand-crafted POST from a switched-off paper is
    refused exactly as the page's own would be.
    """
    if enabled(exam_settings):
        return None
    return {"logged": False, "reason": DISABLED_REASON}


def _as_event(row: dict) -> dict:
    """One stored violation row as the report needs it.

    ``at`` is left as the server's ISO stamp — the template-localising `tz` filter
    turns it into the school's clock, and a timestamp recomputed in the browser
    would be the one field a student could move.
    """
    meta = row.get("metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except ValueError:
            meta = {}
    kind = row.get("violation_type") or "unknown"
    return {
        "user_id": row.get("user_id"),
        "kind": kind,
        # A kind this release does not know about is still shown — recording an
        # event the report silently drops is how a log stops being evidence — but
        # it is not charged, because the ladder only knows the kinds it lists.
        "charged": kind in PENALIZED_VIOLATION_TYPES,
        "label": KIND_LABELS.get(kind, (kind, kind)),
        # The student's own reading of what took the screen away, kept as a hint.
        "trigger": meta.get("trigger"),
        # How long the absence actually lasted, when the page timed one. It is the
        # only trace the second chance leaves in the record, and it is what tells a
        # teacher the difference between a callback charged on sight and a student
        # who was gone for four minutes. `None` is meaningful: this absence was
        # recorded the moment it was seen — the fullscreen panel, or a paper out of
        # second chances — and the report says nothing about a length rather than
        # claiming zero seconds.
        "away_seconds": meta.get("away_seconds"),
        "at": row.get("created_at"),
    }


def _rows(query) -> list[dict]:
    """The query's rows as events, oldest first. The caller owns the fallback."""
    return [_as_event(row) for row in (query.order("created_at").execute().data or [])]


EVENT_COLUMNS = "user_id, violation_type, created_at, metadata"


def events_for_exam(supabase, exam_id: str) -> list[dict]:
    """Every anti-cheat event one exam recorded, oldest first.

    One query for the whole sitting: the results list asks about every student at
    once, and a per-student lookup there would be a round-trip per row. A log that
    cannot be read is an empty list and a logged exception, never an exception
    thrown at a page: a report that 500s because its evidence is missing is worse
    than a report that says it has no evidence.
    """
    try:
        return _rows(supabase.table("violation_logs").select(EVENT_COLUMNS)
                     .eq("exam_id", exam_id))
    except Exception:
        logger.exception("Could not read the anti-cheat log for exam %s", exam_id)
        return []


def events_for_student(supabase, exam_id: str, user_id: str) -> list[dict]:
    """The events one student's paper carries, oldest first."""
    try:
        return _rows(supabase.table("violation_logs").select(EVENT_COLUMNS)
                     .eq("exam_id", exam_id).eq("user_id", user_id))
    except Exception:
        logger.exception("Could not read the anti-cheat log for %s", user_id)
        return []


def unmonitored_report(supabase, *, school_id: str | None = None,
                       exam_ids: list[str] | None = None) -> dict:
    """The violations already recorded on papers whose anti-cheat is switched off.

    A read, and only a read: it selects and counts, and it writes nothing — no score,
    no penalty, and no row is created, changed or deleted. The restraint is the point
    of the report. A row in `violation_logs` was evidence when it was taken, and the
    school switching the paper off afterwards does not make that evidence wrong;
    deleting it would destroy the only record that a pupil was charged under a policy
    the school has since changed. What a school needs is the list, so that what to do
    about marks already awarded is a decision for people rather than for a script.

    Returns four views of one read: ``papers`` (every switched-off paper found, with
    how many rows it holds — a switched-off paper with none is a fact about the
    assessment, not a finding about a pupil), ``pupils`` (one line per pupil and
    paper — the unit a teacher acts on), ``rows`` (the events themselves, labelled)
    and ``total``, so
    "how big is this" is answered before anyone scrolls. Nothing is charged here and
    nothing is un-charged: the ladder is not consulted, because an audit that moved
    a mark would be the opposite of an audit.
    """
    empty = {"papers": [], "pupils": [], "rows": [], "total": 0}
    try:
        exams_query = supabase.table("exams").select("id, title, teacher_id, school_id")
        if school_id:
            exams_query = exams_query.eq("school_id", school_id)
        if exam_ids:
            exams_query = exams_query.in_("id", list(exam_ids))
        # `is false`, never "falsy": a column a migration has not added, or a null,
        # is not a school asking for silence — the same reading `enabled` makes.
        papers = exams_query.eq("anti_cheat_enabled", False).execute().data or []
    except Exception:
        logger.exception("Could not read the switched-off papers")
        return dict(empty)
    if not papers:
        return dict(empty)
    by_id = {paper["id"]: paper for paper in papers}
    try:
        # `exam_id` is named explicitly: `EVENT_COLUMNS` leaves it out because the
        # per-exam readers already know which exam they asked for, and this one asks
        # for several at once.
        raw = (supabase.table("violation_logs")
               .select("exam_id, " + EVENT_COLUMNS)
               .in_("exam_id", list(by_id)).order("created_at").execute().data or [])
    except Exception:
        logger.exception("Could not read the log for %d switched-off paper(s)", len(by_id))
        raw = []

    rows = []
    for row in raw:
        event = _as_event(row)
        event["exam_id"] = row.get("exam_id")
        event["exam_title"] = (by_id.get(row.get("exam_id")) or {}).get("title")
        rows.append(event)

    # The pupils' names, in one query rather than one per row.
    names: dict[str, str] = {}
    user_ids = sorted({r.get("user_id") for r in rows if r.get("user_id")})
    if user_ids:
        try:
            found = (supabase.table("profiles").select("id, full_name")
                     .in_("id", user_ids).execute().data or [])
            names = {p["id"]: p.get("full_name") for p in found}
        except Exception:
            logger.exception("Could not read the pupil names for the audit")

    grouped: dict[tuple, dict] = {}
    for row in rows:
        key = (row.get("exam_id"), row.get("user_id"))
        line = grouped.setdefault(key, {
            "exam_id": row.get("exam_id"), "exam_title": row.get("exam_title"),
            "user_id": row.get("user_id"), "name": names.get(row.get("user_id")),
            "recorded": 0, "charged": 0, "kinds": [],
            "first_at": row.get("at"), "last_at": row.get("at"),
        })
        line["recorded"] += 1
        if row.get("charged"):
            line["charged"] += 1
        if row.get("kind") not in line["kinds"]:
            line["kinds"].append(row.get("kind"))
        line["last_at"] = row.get("at")

    return {
        "papers": [{"id": paper["id"], "title": paper.get("title"),
                    "recorded": sum(1 for r in rows if r.get("exam_id") == paper["id"])}
                   for paper in papers],
        "pupils": [grouped[key] for key in sorted(
            grouped, key=lambda k: (str(k[0]), str(k[1])))],
        "rows": rows,
        "total": len(rows),
    }


def leaving_summary(events: list[dict]) -> dict:
    """What the results list says about one student in one line.

    The count and the *last* moment, and only charged events: a teacher reading
    the list wants to know how many times the paper left the screen and when it
    happened most recently, which is the question they would otherwise open the
    paper to answer.
    """
    charged = [event for event in events if event["charged"]]
    return {
        "away_count": len(charged),
        "away_last_at": charged[-1]["at"] if charged else None,
        "away_last_kind": charged[-1]["kind"] if charged else None,
    }


def count_penalized_violations(supabase, user_id: str, exam_id: str,
                               exam_settings: dict | None = None) -> int:
    """How many violations count toward the penalty.

    Fails to 0 on a lookup error rather than inventing a penalty — but logs it,
    because silently reporting 0 is how an entire class can finish an exam with
    no penalty recorded and nobody notices.

    The count is what gets *stored* on the sitting and shown to the pupil, so it
    asks the switch like everything else: a paper the school switched off has
    nothing charged, and a count read before the switch was flipped must not keep
    a penalty alive in the results. Pass ``exam_settings`` wherever the exam row is
    already in hand (it is, at every caller that charges); omitting it counts the
    log as it stands, which is what an audit of recorded rows wants.
    """
    if exam_settings is not None and not enabled(exam_settings):
        return 0
    try:
        res = (
            supabase.table("violation_logs")
            .select("id", count="exact")
            .eq("user_id", user_id)
            .eq("exam_id", exam_id)
            .in_("violation_type", list(PENALIZED_VIOLATION_TYPES))
            .execute()
        )
        return int(res.count or 0)
    except Exception:
        # The module logger, not `current_app.logger`: the count is a *reader*, and a
        # reader that raises while reporting its own failure turns "the count could
        # not be read" into a stack trace at whichever caller had no request context
        # — the same lesson `events_for_exam` above is written to.
        logger.exception(
            "Could not count penalized violations for user=%s exam=%s", user_id, exam_id
        )
        return 0


def calculate_graduated_penalty(
    violation_count: int,
    exam_settings: dict,
) -> dict:
    """Calculate graduated penalty based on violation count and exam config.

    Graduated scale:
        Violation 1 → WARNING (0 points)
        Violation 2 → penalty_per_violation
        Violation 3 → penalty_per_violation * 2
        Violation 4+ → penalty_per_violation * 3

    Args:
        violation_count: Charged violations, per PENALIZED_VIOLATION_TYPES — a tab
            switch or leaving fullscreen, both counted by
            count_penalized_violations().
        exam_settings: Dict with anti_cheat settings from exam record.

    Returns:
        dict with keys: penalty (float), warning (bool), auto_submit (bool),
                        current_penalty_this_violation (float)
    """
    if not enabled(exam_settings):
        return dict(NONE_CHARGED)

    base = float(exam_settings.get("penalty_per_violation", 5))
    max_violations = int(exam_settings.get("max_violations", 5))
    auto_submit = bool(exam_settings.get("auto_submit_on_max", True))

    if violation_count <= 0:
        return dict(NONE_CHARGED)

    total = 0.0
    for v in range(1, violation_count + 1):
        if v == 1:
            total += 0
        elif v == 2:
            total += base
        elif v == 3:
            total += base * 2
        else:
            total += base * 3

    current_penalty = 0
    if violation_count == 1:
        current_penalty = 0
    elif violation_count == 2:
        current_penalty = base
    elif violation_count == 3:
        current_penalty = base * 2
    else:
        current_penalty = base * 3

    should_auto_submit = auto_submit and max_violations > 0 and violation_count >= max_violations

    is_warning = violation_count == 1

    return {
        "penalty": round(min(total, 100), 2),
        "warning": is_warning,
        "auto_submit": should_auto_submit,
        "current_penalty_this_violation": round(current_penalty, 2),
    }


def _as_utc_epoch(value):
    """A stored timestamp as a POSIX epoch, or ``None`` if it cannot be read.

    The offset has to survive parsing. This used to truncate the string to 19
    characters, which strips a trailing `+00:00` and leaves a *naive* datetime
    that ``.timestamp()`` then reads as local time — on a WIB server that placed
    the previous event seven hours in the past, so ``now - last_ts`` was always
    far above the debounce window and the window rejected nothing at all.
    Measured on the real app: one Esc press produced seven violation rows and
    -75 points, which is enough to auto-submit an exam nobody left.
    """
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            current_app.logger.warning("Unparseable violation timestamp: %r", value)
            return None
    if value.tzinfo is None:
        # The app stores UTC everywhere, so a column without an offset is UTC.
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def validate_violation_log(user_id: str, exam_id: str, timestamp: float) -> dict:
    now = time.time()
    if abs(now - timestamp) > TIMESTAMP_TOLERANCE:
        current_app.logger.warning(f"Violation rejected for {user_id} exam {exam_id}: timestamp_out_of_range (server={now}, client={timestamp})")
        return {"valid": False, "reason": "timestamp_out_of_range"}

    supabase = current_app.extensions["supabase"]
    # Only a *charged* row counts for the debounce. The window exists so one act
    # cannot be billed several times over, and an informational row is not an act:
    # a rotation recorded a tenth of a second earlier would otherwise shield the
    # next real exit from the ladder entirely, which is a bypass the rotation fix
    # would have introduced.
    recent = (
        supabase.table("violation_logs")
        .select("created_at")
        .eq("user_id", user_id)
        .eq("exam_id", exam_id)
        .in_("violation_type", list(PENALIZED_VIOLATION_TYPES))
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )

    if recent.data:
        last_ts = _as_utc_epoch(recent.data[0]["created_at"])
        if last_ts is not None and now - last_ts < RATE_LIMIT_SECONDS:
            current_app.logger.warning(f"Violation rejected for {user_id} exam {exam_id}: rate_limited")
            return {"valid": False, "reason": "rate_limited"}

    return {"valid": True}
