import time
from datetime import datetime, timezone
from flask import current_app


RATE_LIMIT_SECONDS = 2
TIMESTAMP_TOLERANCE = 900

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
PENALIZED_VIOLATION_TYPES = ("tab_switch", "fullscreen_exit")


def count_penalized_violations(supabase, user_id: str, exam_id: str) -> int:
    """How many violations count toward the penalty.

    Fails to 0 on a lookup error rather than inventing a penalty — but logs it,
    because silently reporting 0 is how an entire class can finish an exam with
    no penalty recorded and nobody notices.
    """
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
        current_app.logger.exception(
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
    if exam_settings.get("anti_cheat_enabled") is False:
        return {"penalty": 0, "warning": False, "auto_submit": False, "current_penalty_this_violation": 0}

    base = float(exam_settings.get("penalty_per_violation", 5))
    max_violations = int(exam_settings.get("max_violations", 5))
    auto_submit = bool(exam_settings.get("auto_submit_on_max", True))

    if violation_count <= 0:
        return {"penalty": 0, "warning": False, "auto_submit": False, "current_penalty_this_violation": 0}

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
    recent = (
        supabase.table("violation_logs")
        .select("created_at")
        .eq("user_id", user_id)
        .eq("exam_id", exam_id)
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
