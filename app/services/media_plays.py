"""How many times a question's media may be played — counted by the server.

A paper's audio, YouTube embed or uploaded video is material a candidate studies
while they answer, and a listening passage is often meant to be heard *once*. The
exam page drew a player with no limit and the allowance lived nowhere, so a pupil
could replay for the length of the sitting and a refresh reset whatever the page had
counted. This module is the allowance.

Three facts fix the design.

* **The limit is per question and the teacher sets it.** It travels inside the
  question's own media record (`question_audio[i]`, the JSON the builder already
  writes) as `plays`, so it cannot drift onto a different question. A record with no
  `plays` — every paper built before this existed — reads as one play; `0` is the
  "Tak terbatas / Unlimited" branch, the same word the duration field uses. A value
  outside the menu is clamped **down** to the default, never up: a hand-posted `999`
  must not become a bigger allowance.

* **The count lives on the server, per sitting.** Every charge is one `media_play`
  row in `attempt_session_events` (migration 035) — the same audit trail the lock
  gate and the liveness pings write — so a refresh, a second tab or another device
  cannot reset it, and a proctor can read the plays back later. `seq` is assigned by
  the one writer of that table, `attempt_status.record_event`, rather than by a second
  implementation here.

* **A write that cannot be recorded fails open.** If the event cannot be written the
  play is still allowed and the failure is logged; the audit trail must never cost a
  pupil their paper, the rule `attempt_status` and `submission_service` both follow.
  A pupil cannot make the write fail on purpose, so this is not a hole — it is the
  same shape as a heartbeat that could not be stamped.
"""
from __future__ import annotations

from app.utils.logger import get_logger

logger = get_logger("media_plays")

#: What a question that never chose a number allows.
DEFAULT_LIMIT = 1

#: The teacher's menu. `UNLIMITED` is the "Unlimited" branch; the rest are counts.
UNLIMITED = 0
LIMIT_CHOICES = (1, 2, 3, 5, 10, UNLIMITED)

#: The event kind one charge is recorded under. Distinct from the connection and
#: lifecycle kinds so the media count is a query rather than a subtraction.
EVENT_KIND = "media_play"

#: A pause — evidence for the timeline, never a charge. A pupil who pauses has not
#: spent a play, so this kind is written beside the count rather than into it.
PAUSE_KIND = "media_pause"

#: The moment the allowance runs out. Derived on the server from the charge that
#: spends the last play, so a page cannot invent one and cannot omit one.
LIMIT_KIND = "media_limit_reached"

#: Every kind this module writes. A reader-facing label exists for each; a new kind
#: that lands without one fails `tests/unit/test_media_timeline.py`.
KINDS = (EVENT_KIND, PAUSE_KIND, LIMIT_KIND)


def _normalise(value) -> int:
    """A number from the menu, or the default — never a larger allowance.

    An unknown value (a hand-posted `999`, a negative, a word) reads as the default
    rather than as itself, because the only reason to send one is to ask for more
    plays than the teacher granted.
    """
    try:
        number = int(value)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    return number if number in LIMIT_CHOICES else DEFAULT_LIMIT


def limit_for(media) -> int:
    """How many times this question's media may be played, total. 0 = unlimited.

    `media` is one question's record from `question_audio` — a dict for a modern
    paper, a bare string for the oldest ones (a pasted audio link). A string has no
    room for the setting and reads as the default, which is what those papers mean.
    """
    if isinstance(media, dict):
        return _normalise(media.get("plays"))
    return DEFAULT_LIMIT


def remaining(limit, used) -> int | None:
    """Plays left, or ``None`` when the question is unlimited."""
    limit = _normalise(limit)
    if limit == UNLIMITED:
        return None
    return max(0, limit - int(used or 0))


def used_by_question(supabase, attempt_id) -> dict:
    """``{question_index: plays}`` spent so far, from the sitting's own events.

    One read of the attempt's `media_play` rows. A row with no question index is not
    counted (there is no question to charge it to), and an unreadable table is an
    empty map rather than an error — a failed read must not 500 the exam page, the
    same rule `attempt_status.timeline` follows.
    """
    if not attempt_id:
        return {}
    try:
        rows = (
            supabase.table("attempt_session_events")
            .select("question_index")
            .eq("attempt_id", attempt_id).eq("kind", EVENT_KIND)
            .execute().data
            or []
        )
    except Exception:  # noqa: BLE001 — a count nobody can read is no count
        logger.warning("could not read media plays for attempt %s", attempt_id)
        return {}
    counts: dict = {}
    for row in rows:
        index = row.get("question_index")
        if index is None:
            continue
        counts[int(index)] = counts.get(int(index), 0) + 1
    return counts


def record_play(supabase, attempt_id, question_index, limit) -> dict:
    """Charge one play of one question's media — or refuse it.

    Returns ``{allowed, used, limit, remaining}``. The count is re-read here rather
    than trusted from the caller, so two tabs, two devices or a retried request all
    see the same number. A charge that would exceed the limit writes nothing and
    reports ``allowed: False``; the page turns that into a locked player.
    """
    limit = _normalise(limit)
    index = int(question_index)
    used = used_by_question(supabase, attempt_id).get(index, 0)

    if limit != UNLIMITED and used >= limit:
        return {"allowed": False, "used": used, "limit": limit, "remaining": 0}

    # Imported here so a test can stand in the one writer of the events table, and
    # so this module stays independent of the anti-cheat path at import time.
    from app.services import attempt_status

    charged = attempt_status.record_event(
        supabase, attempt_id, EVENT_KIND,
        question_index=index, meta={"limit": limit, "play": used + 1},
    )
    if not charged:
        # Fail open: the play stands and the count is one short. Refusing a pupil
        # their listening passage because a write hiccuped is worse than an
        # allowance that is briefly lenient.
        logger.warning("media play for attempt %s question %s was not recorded",
                       attempt_id, index)
        return {"allowed": True, "used": used + 1, "limit": limit,
                "remaining": remaining(limit, used + 1)}

    # The charge that spends the last allowance is the moment the player locks, so it
    # is recorded as its own transition — the one a proctor reads to see the media run
    # out. Derived here, not reported by the page: a pupil cannot invent the moment,
    # and it cannot go missing because a page forgot to send it.
    if limit != UNLIMITED and used + 1 >= limit:
        attempt_status.record_event(
            supabase, attempt_id, LIMIT_KIND,
            question_index=index, meta={"limit": limit},
        )

    return {"allowed": True, "used": used + 1, "limit": limit,
            "remaining": remaining(limit, used + 1)}


def record_pause(supabase, attempt_id, question_index) -> bool:
    """Record one pause of one question's media — evidence, never a charge.

    A pause is a client observation, so it is written exactly as reported and never
    turned into a lock or a verdict. It charges nothing: a pupil who fiddles with the
    player must not be able to spend their own allowance by pausing. Best-effort, like
    every other event — a failed write is a loss of evidence, not a failure of the exam.
    """
    if not attempt_id:
        return False
    try:
        index = int(question_index)
    except (TypeError, ValueError):
        return False

    # Imported here for the same reason `record_play` does: this module stays
    # independent of the events table at import time.
    from app.services import attempt_status

    return bool(attempt_status.record_event(
        supabase, attempt_id, PAUSE_KIND, question_index=index))



__all__ = [
    "DEFAULT_LIMIT", "UNLIMITED", "LIMIT_CHOICES",
    "EVENT_KIND", "PAUSE_KIND", "LIMIT_KIND", "KINDS",
    "limit_for", "remaining", "used_by_question", "record_play", "record_pause",
]
