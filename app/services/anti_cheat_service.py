import time
from datetime import datetime
from flask import current_app


RATE_LIMIT_SECONDS = 2
TIMESTAMP_TOLERANCE = 900


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
        violation_count: Number of detected tab switches.
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
        last_time = recent.data[0]["created_at"]
        if isinstance(last_time, str):
            dt = datetime.fromisoformat(last_time[:19] if "T" in last_time else last_time)
            last_ts = dt.timestamp()
        else:
            last_ts = last_time.timestamp()
        if now - last_ts < RATE_LIMIT_SECONDS:
            current_app.logger.warning(f"Violation rejected for {user_id} exam {exam_id}: rate_limited")
            return {"valid": False, "reason": "rate_limited"}

    return {"valid": True}
