import time
from flask import current_app


RATE_LIMIT_SECONDS = 2
TIMESTAMP_TOLERANCE = 300
BLUR_IGNORE_THRESHOLD_MS = 500
IOS_TOLERANCE = 1


def calculate_penalty(
    violation_count: int,
    exam_settings: dict,
) -> float:
    """Calculate final penalty based on exam configuration.

    Args:
        violation_count: Number of detected tab switches.
        exam_settings: Dict with keys: penalty_type, penalty_per_violation,
                      max_penalty_cap.

    Returns:
        float: Total penalty points to deduct from raw score.
    """
    penalty_type = exam_settings.get("penalty_type", "per_violation")
    per_violation = float(exam_settings.get("penalty_per_violation", 5))
    max_cap = float(exam_settings.get("max_penalty_cap", 100))

    if penalty_type == "flat":
        penalty = per_violation if violation_count > 0 else 0
    else:
        penalty = violation_count * per_violation

    return min(penalty, max_cap)


def validate_violation_log(user_id: str, exam_id: str, timestamp: float) -> dict:
    """Validate incoming violation before logging.

    Args:
        user_id: Student UUID.
        exam_id: Exam UUID.
        timestamp: Client-reported timestamp (unix epoch).

    Returns:
        dict with keys: valid (bool), reason (str | None).
    """
    now = time.time()
    if abs(now - timestamp) > TIMESTAMP_TOLERANCE:
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
            last_ts = time.mktime(
                time.strptime(last_time[:19], "%Y-%m-%dT%H:%M:%S")
            )
        else:
            last_ts = last_time.timestamp()
        if now - last_ts < RATE_LIMIT_SECONDS:
            return {"valid": False, "reason": "rate_limited"}

    return {"valid": True}
