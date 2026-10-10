"""The SEB access matrix, adversarially: every role, every scope, both directions.

What this file is guarding
--------------------------
The brief calls this matrix the most critical part of the feature, and the reason
is concrete: the object being protected is the password that unlocks an exam, so a
branch that is one predicate too wide hands a pupil — or a stranger — the way out
of a locked paper.

The two predicates are the app's own, unmocked
----------------------------------------------
``can_manage_exam`` and ``can_read_exam`` are imported from
``app/utils/exam_access.py`` and driven here with real dicts, because the whole
argument of the design is *reuse*: if this module quietly stopped calling them, the
tests that prove the matrix would start asserting the mock. Only the two lookups
that need a database (the target list and the invigilation assignment) are
substituted, and each substitution is narrow.

``super_admin`` is refused, and that is deliberate
--------------------------------------------------
``can_manage_exam`` waves ``super_admin`` through, so the only thing standing
between a platform administrator and a school's exam password is the role gate in
``decide``. It is asserted from both sides: the platform admin is refused, *and*
``can_manage_exam`` really does return ``True`` for them — so the test would notice
if the predicate were changed underneath and the refusal became accidental.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services import seb_access
from app.utils.exam_access import can_manage_exam

NOW = datetime(2026, 10, 9, 3, 0, tzinfo=timezone.utc)

SCHOOL = "sc-1"
OWNER = "u-owner"
EXAM = {
    "id": "ex-1",
    "school_id": SCHOOL,
    "teacher_id": OWNER,
    "require_seb": True,
    "seb_config_key": "a" * 64,
    "duration_minutes": 60,
}


@pytest.fixture(autouse=True)
def _no_invigilation(monkeypatch):
    """By default this teacher supervises nothing; a test that needs a duty opts in."""
    from app.services import invigilation
    monkeypatch.setattr(invigilation, "invigilated_exam_ids",
                        lambda *a, **k: set())
    monkeypatch.setattr(seb_access, "duty_start", lambda *a, **k: None)


def _decide(role, user="u-1", access=seb_access.ACCESS_VIEW_PASSWORD, exam=None,
            school=SCHOOL, now=NOW):
    return seb_access.decide(
        object(), exam if exam is not None else EXAM,
        user_id=user, user_role=role, user_school_id=school, access=access, now=now)


# ── the pupil: never a password, at any endpoint ────────────────────────────

def test_a_pupil_can_never_read_a_password(monkeypatch):
    from app.services import exam_targets
    monkeypatch.setattr(exam_targets, "student_is_included", lambda *a, **k: True)
    # The module's own password access type, and a plausible-looking variant of it:
    # both refused. An access type this module does not define is refused by name
    # *before* the role is considered, which is the stricter of the two orders — so
    # the two are asserted apart rather than collapsed.
    out = _decide("murid", user="u-pupil", access=seb_access.ACCESS_VIEW_PASSWORD)
    assert out["ok"] is False
    assert out["reason"] == "role_not_covered"
    assert _decide("murid", user="u-pupil",
                   access="password")["reason"] == "wrong_access_type"


def test_a_pupil_in_the_target_list_may_download_the_file(monkeypatch):
    from app.services import exam_targets
    monkeypatch.setattr(exam_targets, "student_is_included", lambda *a, **k: True)
    out = _decide("murid", user="u-pupil", access=seb_access.ACCESS_DOWNLOAD_FILE)
    assert out["ok"] is True
    assert out["role"] == seb_access.ROLE_STUDENT


def test_a_pupil_outside_the_target_list_is_refused(monkeypatch):
    from app.services import exam_targets
    monkeypatch.setattr(exam_targets, "student_is_included", lambda *a, **k: False)
    out = _decide("murid", user="u-stranger", access=seb_access.ACCESS_DOWNLOAD_FILE)
    assert out["ok"] is False
    assert out["reason"] == "not_included"


# ── the owner teacher ───────────────────────────────────────────────────────

def test_the_owner_teacher_may_view_and_download():
    for access in seb_access.ACCESS_TYPES:
        out = _decide("guru", user=OWNER, access=access)
        assert out["ok"] is True, access
        assert out["role"] == seb_access.ROLE_OWNER
        # Their own paper: nobody is being covered for.
        assert out["on_behalf_of"] is None


def test_a_same_school_teacher_who_owns_nothing_and_supervises_nothing_is_refused():
    """The colleague across the corridor, with no duty — the ordinary refusal."""
    out = _decide("guru", user="u-colleague")
    assert out["ok"] is False
    assert out["reason"] == "not_owner"


def test_a_teacher_from_another_school_is_refused():
    out = _decide("guru", user=OWNER, school="sc-other")
    assert out["ok"] is False


# ── the invigilator: only inside the window ─────────────────────────────────

DUTY_AT = NOW.isoformat()


def _with_duty(monkeypatch, scheduled_at):
    from app.services import invigilation
    monkeypatch.setattr(invigilation, "invigilated_exam_ids",
                        lambda sb, school, teacher: {str(EXAM["id"])})
    monkeypatch.setattr(seb_access, "duty_start", lambda *a, **k: scheduled_at)


def test_an_invigilator_inside_the_duty_window_may_view_and_download(monkeypatch):
    _with_duty(monkeypatch, DUTY_AT)
    for access in seb_access.ACCESS_TYPES:
        out = _decide("guru", user="u-invigilator", access=access)
        assert out["ok"] is True, access
        assert out["role"] == seb_access.ROLE_INVIGILATOR


def test_an_invigilator_before_the_window_is_refused(monkeypatch):
    """Arriving an hour early is not being on duty."""
    _with_duty(monkeypatch, (NOW + timedelta(hours=2)).isoformat())
    out = _decide("guru", user="u-invigilator")
    assert out["ok"] is False
    assert out["reason"] == "outside_duty_window"


def test_an_invigilator_after_the_window_is_refused(monkeypatch):
    """After the room is cleared the access closes itself, with no cron job."""
    _with_duty(monkeypatch, (NOW - timedelta(days=2)).isoformat())
    out = _decide("guru", user="u-invigilator")
    assert out["ok"] is False
    assert out["reason"] == "outside_duty_window"


def test_the_window_ends_at_the_exams_own_deadline_plus_the_tail(monkeypatch):
    """A 60-minute paper is not a 24-hour licence: the close comes from the clock."""
    _with_duty(monkeypatch, DUTY_AT)
    opens, closes = seb_access.duty_window(EXAM, DUTY_AT)
    assert opens == NOW - timedelta(minutes=seb_access.DUTY_LEAD_MINUTES)
    assert closes == NOW + timedelta(minutes=60 + seb_access.DUTY_TAIL_MINUTES)
    # ...and just past it, refused.
    assert seb_access.in_duty_window(EXAM, DUTY_AT, closes) is True
    assert seb_access.in_duty_window(EXAM, DUTY_AT,
                                     closes + timedelta(seconds=1)) is False


def test_a_duty_with_no_sitting_time_is_never_in_window():
    assert seb_access.in_duty_window(EXAM, None) is False
    assert seb_access.duty_window(EXAM, "") is None


# ── the school's staff ──────────────────────────────────────────────────────

def test_the_school_admin_may_view_and_download_on_the_owners_behalf():
    out = _decide("admin_sekolah", user="u-admin")
    assert out["ok"] is True
    assert out["role"] == seb_access.ROLE_ADMIN
    assert out["on_behalf_of"] == OWNER, "an admin covering for a teacher must say so"


def test_the_head_of_school_and_deputy_get_the_same_access_and_are_named_as_themselves():
    """Fase 0 found their dashboard exists, which is the brief's condition."""
    for role, label in (("principal", seb_access.ROLE_PRINCIPAL),
                        ("vice_principal", seb_access.ROLE_VICE_PRINCIPAL)):
        out = _decide(role, user="u-official")
        assert out["ok"] is True, role
        assert out["role"] == label


def test_an_admin_of_another_school_is_refused():
    out = _decide("admin_sekolah", user="u-admin", school="sc-other")
    assert out["ok"] is False
    assert out["reason"] == "role_not_covered"


def test_an_admin_with_no_school_on_file_is_refused():
    out = _decide("admin_sekolah", user="u-admin", school=None)
    assert out["ok"] is False


def test_the_owner_reading_their_own_paper_is_not_on_anyone_behalf():
    out = _decide("admin_sekolah", user=OWNER)
    assert out["ok"] is True
    assert out["on_behalf_of"] is None


# ── the platform administrator: refused on purpose ──────────────────────────

def test_super_admin_is_refused_even_though_the_predicate_would_allow_it():
    out = _decide("super_admin", user="u-root")
    assert out["ok"] is False
    assert out["reason"] == "role_not_covered"
    # The refusal is a *decision*, not an accident of the shared predicate:
    assert can_manage_exam("u-root", "super_admin", None, EXAM) is True


def test_an_unknown_role_is_refused():
    out = _decide("kurator", user="u-x")
    assert out["ok"] is False
    assert out["reason"] == "role_not_covered"


# ── the exam's own state ────────────────────────────────────────────────────

def test_an_exam_that_does_not_require_seb_has_nothing_to_give():
    out = _decide("admin_sekolah", user="u-admin", exam=dict(EXAM, require_seb=False))
    assert out["ok"] is False
    assert out["reason"] == "not_gated"


def test_a_gated_exam_with_no_issued_config_key_refuses_rather_than_showing_nothing():
    out = _decide("admin_sekolah", user="u-admin", exam=dict(EXAM, seb_config_key=None))
    assert out["ok"] is False
    assert out["reason"] == "no_credential"


def test_an_undefined_access_type_is_refused_rather_than_defaulted():
    out = _decide("admin_sekolah", user="u-admin", access="view_password_please")
    assert out["ok"] is False
    assert out["reason"] == "wrong_access_type"


# ── every refusal has a sentence ────────────────────────────────────────────

def test_every_reason_this_module_can_return_is_declared():
    """A reason the page has no string for is a silent refusal."""
    declared = set(seb_access.REASONS)
    for reason in ("ok", "not_gated", "no_credential", "role_not_covered",
                   "not_owner", "outside_duty_window", "not_included", "no_school",
                   "wrong_access_type"):
        assert reason in declared, reason


def test_the_audit_rows_role_vocabulary_matches_the_check_constraint():
    """`seb_access_log.access_type` has a CHECK; a typo would be a refused insert."""
    import re
    from pathlib import Path
    sql = (Path(__file__).resolve().parents[2] / "supabase" / "migrations"
           / "062_seb_and_environment_signals.sql").read_text(encoding="utf-8")
    allowed = re.findall(r"'(view_password|download_file)'", sql)
    assert set(allowed) == set(seb_access.ACCESS_TYPES)
