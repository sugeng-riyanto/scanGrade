"""Subscription service — tier limits and usage enforcement."""

from datetime import datetime, timedelta, timezone
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

#: The status a paper carries while it is still being written. A draft is work a
#: teacher may abandon, so it does not spend the school's year — see `exam_quota`.
DRAFT_STATUS = "draft"

#: The keys that name a school in a payload or a query. `trial` is deliberately not one
#: of them: it is what a school has when it has bought nothing, and the defect
#: this module exists to stop was a paid subscription resolving into it.
PAID_TIERS = ("basic", "pro", "enterprise")

#: What a manual activation falls back to when nothing records which plan was
#: bought. The *lowest paid* tier, never `trial`: an operator who recorded a cash
#: payment is not looking at a free trial, and defaulting to one capped the school
#: at the trial's 5 exams/year.
DEFAULT_PAID_TIER = "basic"

#: What a cash/manual activation runs for when no plan records a length. The
#: online path has always defaulted to a year for "cash payment or plan not
#: specified"; the cash path left the end NULL instead, which made the
#: subscription open-ended and never expiring.
CASH_DEFAULT_DAYS = 365


def subscription_end(now, plan_days):
    """When a paid subscription ends: the plan's own length, or the default.

    One rule for both write paths, because they disagreed. ``plan_days is None``
    means *unknown* (a cash activation whose plan could not be read) and takes the
    documented one-year default; ``0`` means *Selamanya* and has no end at all.
    """
    days = CASH_DEFAULT_DAYS if plan_days is None else plan_days
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = CASH_DEFAULT_DAYS
    if days <= 0:
        return None
    return now + timedelta(days=days)


def tier_for_duration_days(days):
    """The tier a plan of this length grants.

    One band function for both the plan's length and (when the plan is gone) the
    span the subscription actually runs for: 30/90/120/180 days are the short
    plans (`basic`), a year to three years is `pro`, and anything past three years
    — including `0`, the "Selamanya" plan with no end at all — is `enterprise`.
    A missing length is the lowest paid tier, never `trial`.
    """
    if days is None:
        return DEFAULT_PAID_TIER
    try:
        days = int(days)
    except (TypeError, ValueError):
        return DEFAULT_PAID_TIER
    if days <= 0:
        return "enterprise" if days == 0 else DEFAULT_PAID_TIER
    if days <= 180:
        return "basic"
    if days <= 1095:
        return "pro"
    return "enterprise"


def tier_for_manual_activation():
    """The tier a cash/manual activation carries when no plan was recorded."""
    return DEFAULT_PAID_TIER


def _as_dt(value):
    """A timestamp the driver may hand over as a string or an object."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _span_days(row):
    """How long the subscription runs for, from its own start and end.

    ``None`` when either end is missing (an open-ended activation), so the caller
    can tell "runs for a year" from "runs for ever".
    """
    start = row.get("subscription_start") or row.get("trial_start")
    end = row.get("subscription_end") or row.get("trial_end")
    if not start or not end:
        return None
    try:
        return max((_as_dt(end) - _as_dt(start)).days, 0)
    except (ValueError, TypeError):
        return None


def resolve_tier(subscription, plan=None, load_plan=None):
    """The tier a subscription entitles the school to.

    Ordered from the most literal statement of what was bought to the most
    inferred, and **never from a hardcoded id**: the row's own recorded paid tier,
    then the plan it names — resolved by relation to `subscription_plans`, either
    as the `plan` row the caller already holds or through `load_plan(plan_id)` —
    then the span it runs for, and finally the lowest paid tier for an *active*
    row with none of those. A trial row is the only row that may resolve to
    `trial`. A bare `plan_id` with no row and no loader is not evidence: an id
    whose meaning lives only in this file is how a plan edited on `/plans`
    silently mispriced every school on it.
    """
    if not subscription:
        return "trial"
    # The lifecycle first: a row still *in trial* is a trial whatever a stale
    # `tier` column says. Only a row past that is asked what it bought.
    status = (subscription.get("status") or "").lower()
    if status in ("trial", "trial_expired"):
        return "trial"
    stored = subscription.get("tier")
    if stored in PAID_TIERS:
        return stored
    plan_id = subscription.get("plan_id")
    if plan is None and plan_id is not None and load_plan is not None:
        plan = load_plan(plan_id)
    if plan and plan.get("duration_days") is not None:
        return tier_for_duration_days(plan.get("duration_days"))
    span = _span_days(subscription)
    if span is not None:
        return tier_for_duration_days(span)
    if status == "active":
        return DEFAULT_PAID_TIER
    return "trial"


def _load_plan_row(supabase, plan_id):
    """The plan a subscription names, or ``None`` when it names none."""
    if plan_id is None:
        return None
    try:
        res = supabase.table("subscription_plans") \
            .select("id, duration_days") \
            .eq("id", plan_id) \
            .limit(1) \
            .execute()
        return res.data[0] if res.data else None
    except Exception as e:
        logger.warning("plan lookup for plan_id=%s failed: %s", plan_id, e)
        return None


def get_tier_for_school(school_id):
    """Determine the active tier for a school from what it actually bought."""
    if not school_id:
        return None
    supabase = get_supabase()
    try:
        sub = supabase.table("school_subscriptions") \
            .select("status, plan_id, tier, subscription_start, subscription_end, trial_end") \
            .eq("school_id", school_id) \
            .order("created_at", desc=True) \
            .limit(1) \
            .execute()
        if not sub.data:
            return "trial"
        s = sub.data[0]
        if s.get("status") == "expired":
            return None
        if s.get("subscription_end") and _as_dt(s["subscription_end"]) < datetime.now(timezone.utc):
            return None
        # The stored `tier` is read first; only a row that does not already say
        # what it bought pays for a lookup of the plan it names.
        return resolve_tier(
            s, load_plan=lambda pid: _load_plan_row(supabase, pid))
    except Exception as e:
        logger.warning("get_tier_for_school error: %s", e)
        return "trial"


def exam_quota(school_id, tier=None):
    """This school's exam allowance for the year, and how much of it is spent.

    What counts: rows on `exams` for this *school*, created since 1 January (UTC),
    that are real papers. A draft is not one (a teacher may abandon it) and a
    template is a shape rather than a paper, so neither spends the school's year.
    Every teacher's rows count — the plan is the school's, not one teacher's.

    Returns ``{"tier", "limit", "used", "remaining", "unlimited"}``; ``limit`` and
    ``remaining`` are ``None`` for a plan with no exam allowance at all. One reader,
    because the number the page shows and the number the refusal is made of have to
    be the same number.
    """
    quota = {"tier": tier or get_tier_for_school(school_id), "limit": None, "used": 0,
             "remaining": None, "unlimited": True}
    quota["limit"] = TIER_LIMITS.get(quota["tier"] or "", {}).get("exams_per_year")
    quota["unlimited"] = quota["limit"] is None
    # Spent nothing yet, and the page reads a full quota rather than a half-built one:
    # a count that fails below leaves these two as they are, so an unreachable database
    # shows the allowance instead of a refusal or a blank.
    quota["remaining"] = None if quota["unlimited"] else quota["limit"]
    if not school_id:
        return quota
    year_start = datetime(datetime.now().year, 1, 1, tzinfo=timezone.utc).isoformat()
    try:
        rows = get_supabase().table("exams") \
            .select("id, is_template") \
            .eq("school_id", school_id) \
            .neq("status", DRAFT_STATUS) \
            .gte("created_at", year_start) \
            .execute().data or []
    except Exception as e:
        logger.warning("exam quota count error: %s", e)
        return quota
    # Counted in Python rather than with a filter on `is_template`: a row written
    # before that column existed carries NULL, and a filter for `false` reads those
    # as spend — the school would be charged for papers it cannot find.
    quota["used"] = sum(1 for row in rows if not row.get("is_template"))
    if not quota["unlimited"]:
        quota["remaining"] = max(0, quota["limit"] - quota["used"])
    return quota


def quota_message(quota):
    """The refusal: the allowance, and the way past it."""
    return (f"Kuota ujian tahun ini ({quota['limit']}) telah terpenuhi. "
            f"Upgrade paket untuk batas tak terbatas.")


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
        # The allowance is the *school's* spend this year, measured over real papers
        # only, and it is asked when a paper is minted — saving, editing or publishing
        # a paper that already exists is that teacher's own work being written down.
        quota = exam_quota(school_id, tier=tier)
        if quota["unlimited"]:
            return True, ""
        if quota["used"] >= quota["limit"]:
            return False, quota_message(quota)

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
            # How many are about to be added: a bulk import must be judged on the
            # rows it carries, or a school one student under its cap imports a
            # whole year-group past it.
            incoming = int(extra or 0)
            if usage + incoming > max_students:
                remaining = max(0, max_students - usage)
                return False, (
                    f"Kuota siswa paket ini ({max_students}) tidak cukup — "
                    f"tersisa {remaining}. Upgrade paket untuk menambah siswa.")
        except Exception as e:
            logger.warning("check_feature_limit student count error: %s", e)

    return True, ""
