import time
from flask import current_app


RATE_LIMIT_SECONDS = 2
TIMESTAMP_TOLERANCE = 300


def validate_violation_log(user_id: str, exam_id: str, timestamp: float) -> dict:
    if abs(time.time() - timestamp) > TIMESTAMP_TOLERANCE:
        return {"valid": False, "reason": "timestamp_out_of_range"}

    supabase = current_app.extensions["supabase"]
    recent = supabase.table("violation_logs") \
        .select("created_at") \
        .eq("user_id", user_id) \
        .eq("exam_id", exam_id) \
        .order("created_at", desc=True) \
        .limit(1) \
        .execute()

    if recent.data:
        last_time = recent.data[0]["created_at"]
        if isinstance(last_time, str):
            last_ts = time.mktime(time.strptime(last_time[:19], "%Y-%m-%dT%H:%M:%S"))
        else:
            last_ts = last_time.timestamp()
        if time.time() - last_ts < RATE_LIMIT_SECONDS:
            return {"valid": False, "reason": "rate_limited"}

    return {"valid": True}
