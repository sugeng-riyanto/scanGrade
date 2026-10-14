"""Who may read a SEB password, and who may download the file — one rule, four roles.

Fase 0 found the predicates already written, so this module invents none
----------------------------------------------------------------------
``app/utils/exam_access.py`` already answers the two questions this feature needs,
and answers them the way the school decided:

* :func:`can_manage_exam` — the paper's **owner**. It also returns ``True`` for
  ``super_admin`` and for an ``admin_sekolah`` of the same school.
* :func:`can_read_exam` — everything the owner may read, **plus** the head of
  school and their deputy, scoped to their own school. Its docstring is explicit
  that oversight is read-only and never becomes the power to act on a colleague's
  paper.

Fase 0 also established that ``principal`` and ``vice_principal`` **do** have a
school-wide academic dashboard and analytics of their own
(``app/routes/principal.py``), which is the condition the brief set for giving
them the same access as ``admin_sekolah``. They get it — through
``can_read_exam``, not through a list written here.

The one role deliberately **excluded** is ``super_admin``
---------------------------------------------------------
``can_manage_exam`` waves ``super_admin`` through, and this module does not: the
brief says a role outside the matrix gets nothing "kecuali ada kebutuhan
operasional yang jelas dilaporkan terpisah". A platform administrator has no
operational need to read one school's exam password, so refusing them is the
brief's default, and the refusal is recorded as ``role_not_covered`` so the
operational need — if it ever exists — shows up as data rather than as a guess
made here. This is the one place this module is *stricter* than the predicate it
borrows, and the difference is deliberate rather than an oversight.

The invigilator's window is the only genuinely new rule
------------------------------------------------------
Nothing in the app previously asked "is this teacher on duty **right now**".
``invigilation.invigilated_exam_ids`` answers the weaker question — "is this exam
one of theirs at all" — and is reused here so the page and the decision cannot
disagree about which exams are theirs. The window on top of it comes from the
sitting the duty names (``invigilation_schedules.scheduled_at``) widened by
:data:`DUTY_LEAD_MINUTES` before and :data:`DUTY_TAIL_MINUTES` after, and the
reason for both is concrete: a supervisor arrives before the paper starts, and a
pupil who needs letting out often needs it while the room is being cleared. Outside
that span the same teacher is refused — which is the whole point of the window, and
the assertion ``tests/unit/test_seb_access.py`` exists for.

Every sitting, not the first one
--------------------------------
A paper sat twice in a day — two classes, two shifts — names the same supervisor on
two sittings, and the first version of this window asked only the *earliest* of
them. So the teacher standing in the second shift's room was refused a password in
the middle of it, because a sitting that had finished hours earlier was the one the
window was computed from. :func:`covering_sitting` asks **each** of them with the
same arithmetic and keeps the one that covers ``now``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.utils.exam_access import can_manage_exam, can_read_exam
from app.utils.logger import get_logger

logger = get_logger("seb_access")

#: The two access types, matching the CHECK constraint in migration 062. Named
#: once: a second spelling would be a row the database refuses, at the moment a
#: teacher is trying to help a pupil.
ACCESS_VIEW_PASSWORD = "view_password"
ACCESS_DOWNLOAD_FILE = "download_file"
ACCESS_TYPES = (ACCESS_VIEW_PASSWORD, ACCESS_DOWNLOAD_FILE)

#: What gets written into ``seb_access_log.role_at_access``. Copied rather than
#: inferred at read time, because a role changes and an audit that shows today's
#: role beside last month's access is not an audit (migration 062 says so).
ROLE_OWNER = "owner_teacher"
ROLE_ADMIN = "admin_sekolah"
ROLE_PRINCIPAL = "principal"
ROLE_VICE_PRINCIPAL = "vice_principal"
ROLE_INVIGILATOR = "invigilator"
ROLE_STUDENT = "murid"

#: The staff roles whose authority comes from ``can_read_exam``, each reported as
#: itself in the log — "an admin read it" and "the head of school read it" are
#: different facts about a school.
SCHOOL_ROLES = {
    "admin_sekolah": ROLE_ADMIN,
    "principal": ROLE_PRINCIPAL,
    "vice_principal": ROLE_VICE_PRINCIPAL,
}

#: The duty window, in minutes. The lead covers a supervisor arriving before the
#: paper; the tail covers the room being cleared afterwards, which is when a locked
#: pupil is usually discovered.
DUTY_LEAD_MINUTES = 30
DUTY_TAIL_MINUTES = 60

#: What a duty is assumed to last when its exam declares no duration. Bounded, and
#: deliberately not enormous: an unbounded window is not a window.
DEFAULT_DUTY_MINUTES = 120

#: Every refusal, as a key. A page looks these up in one place, so a reason with no
#: sentence is a button that refuses silently — the failure mode the invigilation
#: service already names its reasons to avoid (`invigilation.REFUSALS`).
REASONS = (
    "ok",
    "not_gated",
    "no_credential",
    "role_not_covered",
    "not_owner",
    "outside_duty_window",
    "not_included",
    "no_school",
    "wrong_access_type",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_dt(value) -> datetime | None:
    """A UTC datetime from whatever Supabase handed back, or ``None``."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _rows(query) -> list[dict]:
    try:
        return query.execute().data or []
    except Exception as exc:                                     # noqa: BLE001
        logger.warning("seb_access: read failed: %s", exc)
        return []


# ── the invigilator's window ────────────────────────────────────────────────

def duty_window(exam: dict, scheduled_at, now: datetime | None = None) -> tuple[datetime, datetime] | None:
    """``(opens, closes)`` for a sitting this teacher supervises, or ``None``.

    The close is the **exam's own deadline** when the exam has one, raised by the
    tail — asked of ``app.utils.exam_window.deadline`` rather than derived here, so
    a supervisor's access cannot outlast, or fall short of, the paper's own clock.
    ``scheduled_at`` stands in as the sitting's start because that is what the duty
    actually names; it is the same approximation ``invigilation_schedules`` makes
    by having one timestamp for a sitting.
    """
    start = _as_dt(scheduled_at)
    if start is None:
        return None
    opens = start - timedelta(minutes=DUTY_LEAD_MINUTES)
    closes = None
    try:
        from app.utils import exam_window
        closes = _as_dt(exam_window.deadline(exam or {}, start))
    except Exception:                                            # noqa: BLE001
        closes = None
    if closes is None:
        try:
            minutes = int((exam or {}).get("duration_minutes") or DEFAULT_DUTY_MINUTES)
        except (TypeError, ValueError):
            minutes = DEFAULT_DUTY_MINUTES
        closes = start + timedelta(minutes=max(1, minutes))
    if closes < start:
        # A deadline before the sitting named is a misconfiguration, and a window
        # that closes before it opens would silently grant nothing. Fall back to
        # the sitting's own length rather than refusing every invigilator.
        closes = start + timedelta(minutes=DEFAULT_DUTY_MINUTES)
    return opens, closes + timedelta(minutes=DUTY_TAIL_MINUTES)


def in_duty_window(exam: dict, scheduled_at, now: datetime | None = None) -> bool:
    """Whether ``now`` falls inside the supervised sitting — inclusive both ends."""
    span = duty_window(exam, scheduled_at, now)
    if span is None:
        return False
    opens, closes = span
    return opens <= (now or _now()) <= closes


def duty_sittings(supabase, school_id: str, teacher_id: str,
                  exam_id: str) -> list[str]:
    """Every sitting of this exam that names this teacher, earliest first.

    A list rather than a single stamp, because "the sitting I am on duty for" is not
    a function of the exam: the same paper can be sat by two classes at two times,
    with the same person supervising both.
    """
    schedules = _rows(supabase.table("invigilation_schedules")
                      .select("id, scheduled_at")
                      .eq("school_id", school_id).eq("exam_id", exam_id))
    if not schedules:
        return []
    by_id = {str(row.get("id")): row.get("scheduled_at") for row in schedules}
    mine = _rows(supabase.table("invigilator_assignments")
                 .select("schedule_id")
                 .eq("school_id", school_id).eq("teacher_id", teacher_id))
    stamps = {str(by_id[str(row.get("schedule_id"))]) for row in mine
              if str(row.get("schedule_id")) in by_id and by_id[str(row.get("schedule_id"))]}
    return sorted(stamps)


def covering_sitting(supabase, school_id: str, teacher_id: str, exam: dict,
                     now: datetime | None = None) -> str | None:
    """The sitting of this exam whose duty window covers ``now``, or ``None``.

    *Any* of them, and that word is the fix: with only the earliest asked, a
    supervisor whose paper is sat twice in a day — two classes, two shifts — was
    outside the window for the second one from the moment the first one ended, while
    standing in the room. Every sitting is judged with the same
    :func:`in_duty_window` the rest of the module uses, so this cannot become a
    second opinion about the window.

    ``None`` here means *assigned but not on duty now*; whether the teacher is on
    this paper **at all** is a different question, answered by
    ``invigilation.invigilated_exam_ids`` in :func:`_is_my_duty`. The two get
    different sentences on the page, and only one of them is worth waiting for.
    """
    for stamp in duty_sittings(supabase, school_id, teacher_id,
                               str((exam or {}).get("id"))):
        if in_duty_window(exam, stamp, now):
            return stamp
    return None


def _is_my_duty(supabase, exam: dict, user_id: str,
                now: datetime | None = None) -> tuple[str, str | None]:
    """``("invigilator"|"none", scheduled_at)`` — is this exam one of theirs today.

    The *set* of exams comes from ``invigilation.invigilated_exam_ids`` — the same
    function ``/teacher/invigilation`` is built from — so the page a teacher sees
    and the decision a route takes cannot disagree about which papers are theirs.
    """
    school_id = exam.get("school_id")
    if not school_id:
        return "none", None
    try:
        from app.services import invigilation
        if str(exam.get("id")) not in invigilation.invigilated_exam_ids(
                supabase, str(school_id), str(user_id)):
            return "none", None
    except Exception as exc:                                     # noqa: BLE001
        logger.warning("seb_access: invigilation lookup failed: %s", exc)
        return "none", None
    return "invigilator", covering_sitting(supabase, str(school_id), str(user_id),
                                           exam, now)


# ── the pupil's one access: the file, and the rule that decides it ──────────

def _pupil_file(supabase, exam: dict, user_id: str) -> dict:
    """``decide``'s answer for a pupil asking for the exam's own ``.seb`` file.

    The rule is ``exam_sitting_allowed`` and not ``exam_targets.student_is_included``,
    and the difference is the defect this function was written to remove:
    ``student_is_included`` answers *one* of the app's participation questions — "is
    this pupil on the paper's per-pupil list" — and that list only exists when
    ``target_mode`` is ``students``. Read on its own it is ``False`` for every pupil
    of an ordinary class-assigned paper, because there is no target row to find: so
    the handshake page's own *Unduh berkas .seb saya* button answered **403
    ``not_included`` to every pupil in the school**, on every paper the school had
    not switched to a per-pupil list. The one file the feature is built to hand out
    was unreachable exactly where it is offered.

    ``exam_sitting_allowed`` is the app's participation rule — published, active,
    the pupil's own school, the pupil's own class, and *then* the target list when
    the paper has one — and it is already what the exam door and the handshake route
    apply. Asking it here is what makes the three pupil doors one rule rather than
    three versions of it, in either direction: a class paper is no longer refused to
    its own pupils, and a ``target_mode='students'`` paper can no longer be downloaded
    by a pupil the sitting itself would refuse.

    ``exam_id`` rather than the row's own id is what the rule is handed, exactly as
    the two sibling doors hand it, because it is also the id the per-pupil lookup is
    scoped by.
    """
    if not exam.get("require_seb"):
        # A paper nobody gated has no file to offer, and handing one over would say
        # otherwise. The toggle and the file are one thing: migration 062 is where
        # that is written down.
        return {"ok": False, "role": None, "reason": "not_gated", "on_behalf_of": None}
    if not (exam.get("seb_config_key") or ""):
        return {"ok": False, "role": None, "reason": "no_credential",
                "on_behalf_of": None}
    from app.utils.exam_access import exam_sitting_allowed
    allowed, _why = exam_sitting_allowed(supabase, exam, exam.get("id"), user_id)
    if not allowed:
        # "Not on this paper" is the whole answer a pupil needs, and the only one
        # the route gives them: `_refuse` hides the reason from a `/student/` path.
        # The reasons a paper is not theirs — another class, another school, excluded
        # by the teacher, not published yet — are all that one sentence.
        return {"ok": False, "role": None, "reason": "not_included",
                "on_behalf_of": None}
    return {"ok": True, "role": ROLE_STUDENT, "reason": "ok", "on_behalf_of": None}


# ── the one decision ────────────────────────────────────────────────────────

def decide(supabase, exam: dict, *, user_id: str, user_role: str,
           user_school_id: str | None, access: str,
           now: datetime | None = None) -> dict:
    """``{ok, role, reason, on_behalf_of}`` for one request.

    The order is the rule, and it is written so that the **strictest** branch is
    reached first:

    1. an access type this module does not define is refused, not defaulted;
    2. a pupil may never read a password — checked before anything else that could
       grant a pupil something, at any endpoint (this is the assertion the brief
       calls the most critical one). A pupil's one access is the file, and that is
       judged by ``exam_sitting_allowed`` — the app's own participation rule, the
       same one the exam door and the handshake page apply — never by the per-pupil
       target list read on its own. See :func:`_pupil_file`;
    3. a gated exam with no credential has nothing to give, and says so rather than
       looking like a permission failure;
    4. the owner teacher;
    5. the school's admin and its two officials, via ``can_read_exam`` — and when
       the reader is not the paper's own teacher, ``on_behalf_of`` names that
       teacher, which is the brief's "akses atas nama guru X";
    6. a teacher who supervises this exam **inside their duty window** — any sitting
       of theirs that covers now, so a paper sat twice in a day does not close the
       second sitting's access;
    7. everyone else, including ``super_admin``, refused as ``role_not_covered``.
    """
    exam = exam or {}
    if access not in ACCESS_TYPES:
        return {"ok": False, "role": None, "reason": "wrong_access_type",
                "on_behalf_of": None}
    if user_role == "murid":
        # No password, ever, at any endpoint. A pupil's only access is the file,
        # and it is checked below where the file is what was asked for.
        if access != ACCESS_DOWNLOAD_FILE:
            return {"ok": False, "role": None, "reason": "role_not_covered",
                    "on_behalf_of": None}
        return _pupil_file(supabase, exam, user_id)

    if not exam.get("require_seb"):
        return {"ok": False, "role": None, "reason": "not_gated", "on_behalf_of": None}
    if not (exam.get("seb_config_key") or ""):
        return {"ok": False, "role": None, "reason": "no_credential",
                "on_behalf_of": None}

    owner = bool(exam.get("teacher_id")) and str(exam.get("teacher_id")) == str(user_id)

    if user_role == "guru":
        if owner and can_manage_exam(user_id, user_role, user_school_id, exam):
            return {"ok": True, "role": ROLE_OWNER, "reason": "ok", "on_behalf_of": None}
        if not user_school_id:
            return {"ok": False, "role": None, "reason": "no_school", "on_behalf_of": None}
        state, scheduled = _is_my_duty(supabase, exam, user_id, now)
        if state != "invigilator":
            return {"ok": False, "role": None, "reason": "not_owner", "on_behalf_of": None}
        if scheduled is None:
            # Assigned, but no sitting of theirs covers now — the window itself is
            # already applied, by `covering_sitting`, and it is the *only* place that
            # asks. A second check here would be a second opinion about the window.
            return {"ok": False, "role": None, "reason": "outside_duty_window",
                    "on_behalf_of": None}
        return {"ok": True, "role": ROLE_INVIGILATOR, "reason": "ok",
                "on_behalf_of": None}

    label = SCHOOL_ROLES.get(user_role)
    if label and can_read_exam(user_id, user_role, user_school_id, exam):
        # Not the paper's own teacher: the brief asks for the access to be recorded
        # as being on that teacher's behalf, so the log answers "who was covering".
        return {"ok": True, "role": label, "reason": "ok",
                "on_behalf_of": None if owner else (exam.get("teacher_id") or None)}

    return {"ok": False, "role": None, "reason": "role_not_covered", "on_behalf_of": None}


# ── the record ─────────────────────────────────────────────────────────────

def record(supabase, exam: dict, *, user_id: str, role: str, access: str,
           on_behalf_of: str | None = None, ip: str | None = None,
           user_agent: str | None = None) -> bool:
    """Write one ``seb_access_log`` row, and one line in the school's audit log.

    Both, deliberately. ``seb_access_log`` is the table this feature is *about* —
    it knows about roles and about reading on someone's behalf — while
    ``audit_logs`` is where the school already looks. Writing only the first would
    hide the access from the page a head of school opens; writing only the second
    would lose the columns the SEB report needs.

    Best-effort by design and never fatal: a logging failure must not become a
    teacher who cannot help a pupil. The failure is logged instead, which is the
    same contract ``audit_service.log_activity`` already has.
    """
    entry = {
        "school_id": exam.get("school_id"),
        "exam_id": exam.get("id"),
        "user_id": user_id,
        "role_at_access": role,
        "access_type": access,
        "on_behalf_of": on_behalf_of,
        "ip_address": ip,
        "user_agent": (user_agent or "")[:500],
        "created_at": _now().isoformat(),
    }
    ok = bool(_rows(supabase.table("seb_access_log").insert(entry)))
    if not ok:
        logger.warning("seb_access: could not record %s for exam %s by %s",
                       access, exam.get("id"), user_id)
    try:
        from app.services import audit_service
        audit_service.log_activity(
            audit_service.ACTION_EXPORT if access == ACCESS_DOWNLOAD_FILE
            else audit_service.ACTION_UPDATE,
            "seb_credential", exam.get("id"),
            new_data={"access_type": access, "role_at_access": role,
                      "on_behalf_of": on_behalf_of},
            user_id=user_id, ip_address=ip, user_agent=user_agent)
    except Exception as exc:                                     # noqa: BLE001
        logger.warning("seb_access: audit_service write failed: %s", exc)
    return ok


def log_for_exam(supabase, school_id: str, exam_id: str, limit: int = 50) -> list[dict]:
    """Who read or downloaded this exam's SEB material, newest first.

    Scoped by school in the query itself, because the backend uses the service key
    and walks through RLS — the scope has to be a filter here, not an assumption.
    """
    return _rows(supabase.table("seb_access_log")
                 .select("id, user_id, role_at_access, access_type, on_behalf_of, "
                         "ip_address, created_at, profiles!left(full_name)")
                 .eq("school_id", school_id).eq("exam_id", exam_id)
                 .order("created_at", desc=True).limit(limit))


__all__ = [
    "ACCESS_DOWNLOAD_FILE", "ACCESS_TYPES", "ACCESS_VIEW_PASSWORD",
    "DEFAULT_DUTY_MINUTES", "DUTY_LEAD_MINUTES", "DUTY_TAIL_MINUTES", "REASONS",
    "ROLE_ADMIN", "ROLE_INVIGILATOR", "ROLE_OWNER", "ROLE_PRINCIPAL",
    "ROLE_STUDENT", "ROLE_VICE_PRINCIPAL", "SCHOOL_ROLES", "covering_sitting",
    "decide", "duty_sittings", "duty_window", "in_duty_window", "log_for_exam",
    "record",
]
