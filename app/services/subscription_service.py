"""Subscription service — tier limits and usage enforcement."""

from datetime import datetime, timezone
from app.utils.auth import get_supabase
from app.utils.logger import get_logger

logger = get_logger("subscription")


# Tier limits: which features each subscription tier allows and their quotas
TIER_LIMITS = {
    "trial": {"exams_per_year": 5, "ai_grading": False, "students_per_school": 100},
    "basic": {"exams_per_year": 10, "ai_grading": False, "students_per_school": 500},
    "pro": {"exams_per_year": None, "ai_grading": True, "students_per_school": None},
    "enterprise": {"exams_per_year": None, "ai_grading": True, "students_per_school": None},
}


def get_tier_for_school(school_id):
    """Determine the active tier for a school based on their subscription."""
    if not school_id:
        return None
    supabase = get_supabase()
    try:
        sub = supabase.table("school_subscriptions") \
            .select("status, plan_id, subscription_end") \
            .eq("school_id", school_id) \
            .order("created_at", desc=True) \
            .limit(1) \
            .execute()
        if not sub.data:
            return "trial"
        s = sub.data[0]
        if s.get("status") == "expired":
            return None
        if s.get("subscription_end") and datetime.fromisoformat(s["subscription_end"].replace("Z", "+00:00")) < datetime.now(timezone.utc):
            return None
        # Map plan_id to tier name
        plan_id = s.get("plan_id")
        return _plan_id_to_tier(plan_id) or "trial"
    except Exception as e:
        logger.warning("get_tier_for_school error: %s", e)
        return "trial"


def _plan_id_to_tier(plan_id):
    """Map subscription_plan id to a tier name."""
    if plan_id is None:
        return "trial"
    plans_map = {1: "trial", 2: "basic", 3: "basic", 4: "basic",
                 5: "pro", 6: "pro", 7: "pro", 8: "enterprise", 9: "enterprise", 10: "enterprise"}
    return plans_map.get(int(plan_id), "trial")


def check_feature_limit(school_id, feature, extra=None):
    """Check if a school has exceeded their plan's feature limit.
    Returns (allowed: bool, message: str).
    """
    tier = get_tier_for_school(school_id)
    if tier is None:
        return False, "Langganan telah berakhir. Perpanjang untuk melanjutkan."

    limits = TIER_LIMITS.get(tier, {})
    supabase = get_supabase()

    if feature == "create_exam":
        max_exams = limits.get("exams_per_year")
        if max_exams is None:
            return True, ""
        year_start = datetime(datetime.now().year, 1, 1, tzinfo=timezone.utc).isoformat()
        try:
            count = supabase.table("exams") \
                .select("id", count="exact") \
                .eq("school_id", school_id) \
                .gte("created_at", year_start) \
                .execute()
            usage = count.count or 0
            if usage >= max_exams:
                return False, f"Kuota ujian tahun ini ({max_exams}) telah terpenuhi. Upgrade paket untuk batas tak terbatas."
        except Exception as e:
            logger.warning("check_feature_limit exam count error: %s", e)

    elif feature == "ai_grading":
        if not limits.get("ai_grading"):
            return False, "Fitur koreksi AI tidak tersedia di paket saat ini. Upgrade ke Pro atau Enterprise."

    elif feature == "add_student":
        max_students = limits.get("students_per_school")
        if max_students is None:
            return True, ""
        try:
            count = supabase.table("students") \
                .select("id", count="exact") \
                .eq("school_id", school_id) \
                .eq("status", "active") \
                .execute()
            usage = count.count or 0
            if usage >= max_students:
                return False, f"Kuota siswa ({max_students}) telah terpenuhi. Upgrade paket untuk menambah siswa."
        except Exception as e:
            logger.warning("check_feature_limit student count error: %s", e)

    return True, ""
