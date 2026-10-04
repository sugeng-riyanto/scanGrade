"""A closed year is read-only — at every door, and the doors are enumerated.

The rule lives in `app/decorators/year_lock.py`, but a rule applied by hand to
forty routes is a rule that will be forgotten on the forty-first. So the main guard
here is a *sweep*: it reads every mutating route out of **every route module** and
requires each one either to carry `@open_year_required` or to appear in an explicit
exemption list with a reason. A new write route therefore fails this file until it
is classified.

The first version of this sweep read only `teacher.py`, so a grading write in
`api.py`, a whiteboard edit, or a vice principal's invigilation write could still
change a finished year. The doors named in `EXEMPT` are the rest of the app, each
with the reason it does not touch a year's papers or marks.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.decorators import year_lock
from app.services import academic_year

ROOT = Path(__file__).resolve().parents[2]
ROUTE_FILES = sorted((ROOT / "app" / "routes").glob("*.py"))

#: Mutating routes that do NOT touch a year's papers, marks or scores. Each one
#: needs a reason, because "it was easier" is not one — this list is the only way
#: a write route avoids the lock, so an unjustified entry is a hole. Keyed by
#: "<blueprint> <path>".
EXEMPT = {
    # ── teacher.py: routes that read or write something other than a year ──
    "teacher_bp /exams/parse-pdf": "reads an upload into a preview; writes no exam yet",
    "teacher_bp /exams/generate-key": "generates answer-key text for a form; writes nothing",
    "teacher_bp /exams/parse-pdf/markdown": "parses text in memory; writes nothing",
    "teacher_bp /exams/export-scan": "exports a file; writes nothing",
    "teacher_bp /reset-password": "the teacher's own account, not a year's data",
    "teacher_bp /profile/update": "the teacher's own account, not a year's data",
    "teacher_bp /assignments": "an assignment is per class-subject, not per year's marks",
    "teacher_bp /assignments/<assignment_id>": "same as /assignments",
    "teacher_bp /api/ai/test-demo": "a test call to the AI provider; writes nothing",
    "teacher_bp /api/grading-assist/bank":
        "the marker's own reusable comments, not a year's marks; the page opens "
        "only on a running year anyway",
    "teacher_bp /api/grading-assist/bank/apply":
        "counts one use of the marker's own comment",
    "teacher_bp /api/grading-assist/flag":
        "a 'come back to this one' note on one paper's question, not the mark "
        "itself; the paper is guarded by _guard_submission",
    "teacher_bp /api/grading-assist/annotations":
        "a mark drawn over a pupil's answer — a highlight, a strike-through or a "
        "comment pin — stored beside the answer and never editing it; the paper "
        "is guarded by _guard_submission",
    "teacher_bp /api/grading-assist/annotations/update":
        "changes one mark's note or colour, not the paper's marks",
    "teacher_bp /api/grading-assist/annotations/delete":
        "removes one mark, so a closed year's answers are unchanged by it",
    "teacher_bp /exams/<exam_id>/locked/<student_id>/unlock":
        "reopens a locked sitting for a pupil the teacher already holds — the "
        "human counterpart to the resume code; it changes no mark, is refused at "
        "or past the deadline, and is written to the activity log",
    "teacher_bp /ai-settings/add-key": "the teacher's own settings",
    "teacher_bp /ai-settings/<key_id>/toggle": "the teacher's own settings",
    "teacher_bp /ai-settings/<key_id>/delete": "the teacher's own settings",
    "teacher_bp /ai-settings/save-prompt": "the teacher's own settings",
    "teacher_bp /ai-settings/reset-prompt": "the teacher's own settings",
    "teacher_bp /subjects/new": "a school subject, not a year's marks",
    "teacher_bp /subjects/<subject_id>/delete": "a school subject, not a year's marks",
    "teacher_bp /analysis/<exam_id>/share": "a share link, not the year's data",
    "teacher_bp /analysis/<exam_id>/share/revoke": "a share link, not the year's data",
    "teacher_bp /analysis/<exam_id>/report/student/<student_id>/share":
        "a share link exposes an archived paper read-only; it does not change it",
    "teacher_bp /analysis/<exam_id>/report/student/<student_id>/share/revoke":
        "revoking that same read-only link",
    "teacher_bp /exams/new":
        "creates a NEW paper, which belongs to the running year; there is nothing "
        "to date yet, so no closed year can be its target",
    "teacher_bp /retake-requests/<request_id>/decide":
        "authorises a fresh sitting at a new year's door; a closed year's own rows "
        "are not touched",

    # ── exam.py: the API doors for a paper ──
    "exam_bp /": "creates a NEW paper; nothing to date yet",

    # ── api.py: grading helpers that only read, and the non-year endpoints ──
    "api_bp /scan/process": "reads a page and returns the marks it detects; writes nothing",
    "api_bp /scan/essay": "returns an OCR reading of an essay page; writes nothing",
    "api_bp /grade/vision-canvas": "returns an OCR reading of a canvas; writes nothing",
    "api_bp /scan/bulk": "returns readings for a batch of pages; writes nothing",
    "api_bp /grade/ai-suggest": "suggests marks from typed text in memory; writes nothing",
    "api_bp /pgk/simulate": "scores a PGK answer key in memory; writes nothing",
    "api_bp /ai/test-key": "tests a provider key; writes nothing",
    "api_bp /ai/test-key-raw": "tests a provider key; writes nothing",
    "api_bp /activation/redeem": "redeems a code against a school's subscription",
    "api_bp /students/import": "creates roster accounts from a spreadsheet",
    "api_bp /pengumuman": "an announcement, not a year's marks",
    "api_bp /pengumuman/<uuid:pengumuman_id>": "an announcement",
    "api_bp /pengumuman/<uuid:pengumuman_id>/mark-read": "an announcement's read receipt",
    "api_bp /percakapan": "a conversation",
    "api_bp /percakapan/<uuid:percakapan_id>": "a conversation",
    "api_bp /percakapan/<uuid:percakapan_id>/pesan": "a message in a conversation",
    "api_bp /pesan/<uuid:pesan_id>": "a message",
    "api_bp /account/delete-request": "the reader's own account lifecycle",
    "api_bp /account/delete-request/cancel": "the reader's own account lifecycle",
    "api_bp /admin/deletion-requests/process": "the account deletion queue",
    "api_bp /ui-preferences": "the reader's own interface preferences",

    # ── auth.py: sign-in, activation and credentials ──
    "auth_bp /register": "a signup, not a year's marks",
    "auth_bp /activate": "activates an account with a code",
    "auth_bp /login-user": "a sign-in",
    "auth_bp /forgot-password": "starts a password reset",
    "auth_bp /verify-reset-code": "verifies a reset code",
    "auth_bp /set-new-password": "sets an account password",
    "auth_bp /reset-password": "resets an account password",
    "auth_bp /reset-password-exchange": "exchanges a reset token",
    "auth_bp /change-password": "changes the reader's own password",
    "auth_bp /set-timezone": "the reader's own timezone",

    # ── student.py / students.py: the pupil's own account and roster ──
    "student_bp /reset-password": "the pupil's own account",
    "student_bp /profile/update": "the pupil's own account",
    "student_bp /settings/pdp-update": "the pupil's own privacy settings",
    "student_bp /api/recover-exam":
        "redeems a recovery code for the reader's own sitting; the sitting's own "
        "writes go through the locked submit/save doors",
    "student_bp /exams/<exam_id>/resume":
        "redeems a resume code for the reader's own locked sitting — the pupil "
        "who already owns it — and writes no mark; the sitting's own submit/save "
        "doors remain the ones that change answers, and it refuses at or past the "
        "deadline so a closed year cannot be reopened through it",
    "student_bp /import/csv": "creates roster accounts from a CSV",
    "student_bp /heartbeat/<exam_id>":
        "records one liveness ping on a running sitting's own row; it opens no "
        "paper and changes no mark, and a closed year's attempts are finalised",

    # ── admin.py: the legacy admin panel ──
    "admin_bp /teachers/<teacher_id>/delete": "an account and its roster row",
    "admin_bp /students/<student_id>/delete": "an account and its roster row",
    "admin_bp /classes/create": "a class row for the running year",
    "admin_bp /school": "the school's own profile",
    "admin_bp /students/import": "creates roster accounts",
    "admin_bp /teachers/import": "creates roster accounts",
    "admin_bp /registration-requests/<request_id>/approve": "approves a signup",
    "admin_bp /registration-requests/<request_id>/reject": "rejects a signup",
    "admin_bp /registration-requests/<request_id>/delete": "removes a signup request",
    "admin_bp /teachers/<teacher_id>/reset-password": "an account's credential",
    "admin_bp /students/<student_id>/reset-password": "an account's credential",

    # ── admin_sekolah.py: the school's roster, year and billing ──
    "admin_sekolah_bp /profile": "the school's own profile",
    "admin_sekolah_bp /import": "creates roster accounts from a spreadsheet",
    "admin_sekolah_bp /school-years":
        "creates or edits a year; a year is the *subject*, not its contents",
    "admin_sekolah_bp /school-years/<year_id>/toggle":
        "makes a year running or not; the subject itself",
    "admin_sekolah_bp /school-years/<year_id>/delete":
        "removes a year the admin created, not the marks inside it",
    "admin_sekolah_bp /school-years/close":
        "the close wizard itself; it runs its own plan/apply checks",
    "admin_sekolah_bp /classes/create": "a class row for the running year",
    "admin_sekolah_bp /subjects/create": "a school subject, not a year's marks",
    "admin_sekolah_bp /subjects/<subject_id>/edit": "a school subject",
    "admin_sekolah_bp /subjects/<subject_id>/delete": "a school subject",
    "admin_sekolah_bp /subjects/<subject_id>/mapping":
        "which classes a subject is offered to; a mapping, not a year's marks",
    "admin_sekolah_bp /subjects/<subject_id>/levels":
        "a pupil's basic/intermediate/advanced track; a label, not a year's marks",
    "admin_sekolah_bp /assessment-periods/save":
        "the school's assessment calendar; a period is the subject, not the marks "
        "inside a year",
    "admin_sekolah_bp /assessment-periods/<period_id>/delete":
        "removes a period the school admin created, not the marks inside a year",
    # KKM is year-scoped by *argument* (the running school year), not by a
    # resource id, so there is no id for the decorator to resolve. The service
    # asks the question itself: subject_kkm.set_kkm / clear_override call
    # _year_is_editable and refuse a closed year with reason `year_closed`
    # before any write.
    # The invigilator matrix: periods, exam rooms, and one duty per (date, slot,
    # room). None of it is a year's paper or mark — a duty names a room and a
    # teacher, not an exam — so there is no year resource for the decorator to
    # resolve. The rules that do apply are the two conflict constraints, enforced
    # in the database and checked first by the service.
    "admin_sekolah_bp /invigilation/matrix/periods":
        "a time slot the school scheduled, not a year's marks",
    "admin_sekolah_bp /invigilation/matrix/rooms":
        "an exam room, not a year's marks",
    "admin_sekolah_bp /invigilation/matrix/assign":
        "one invigilator in one room on one day; no exam or mark is touched",
    "admin_sekolah_bp /invigilation/matrix/clear":
        "empties one invigilation cell, not a year's marks",
    "admin_sekolah_bp /invigilation/matrix/upload":
        "parses a workbook into a preview; writes no row",
    "admin_sekolah_bp /invigilation/matrix/apply":
        "commits the confirmed invigilation rows through the same assign; no mark",
    "admin_sekolah_bp /subjects/<subject_id>/kkm":
        "subject_kkm refuses a closed year itself (year_closed), before writing",
    "admin_sekolah_bp /subjects/<subject_id>/kkm/<grade_level>/clear":
        "subject_kkm refuses a closed year itself (year_closed), before clearing",
    "principal_bp /vice-principal/assessment-periods/save":
        "the school's assessment calendar; a period is the subject, not the marks "
        "inside a year",
    "principal_bp /vice-principal/assessment-periods/<period_id>/delete":
        "removes a period the deputy created, not the marks inside a year",
    "admin_sekolah_bp /promote":
        "checked inside the view against the target year (its own test)",
    "admin_sekolah_bp /teachers/<teacher_id>/assignments":
        "an assignment is per class-subject, not a year's marks",
    "admin_sekolah_bp /teachers/create": "an account and its roster row",
    "admin_sekolah_bp /teachers/<teacher_id>/edit": "an account and its roster row",
    "admin_sekolah_bp /teachers/<teacher_id>/delete": "an account and its roster row",
    "admin_sekolah_bp /teachers/<teacher_id>/reset-password": "an account's credential",
    "admin_sekolah_bp /officials/create": "an account and its roster row",
    "admin_sekolah_bp /officials/<official_id>/edit": "an account and its roster row",
    "admin_sekolah_bp /officials/<official_id>/delete": "an account and its roster row",
    "admin_sekolah_bp /officials/<official_id>/reset-password": "an account's credential",
    "admin_sekolah_bp /students/create": "an account and its roster row",
    "admin_sekolah_bp /students/<student_id>/edit": "an account and its roster row",
    "admin_sekolah_bp /students/<student_id>/delete": "an account and its roster row",
    "admin_sekolah_bp /students/<student_id>/reset-password": "an account's credential",
    "admin_sekolah_bp /teachers/bulk-reset-password": "account credentials",
    "admin_sekolah_bp /teachers/bulk-delete": "accounts and roster rows",
    "admin_sekolah_bp /students/bulk-reset-password": "account credentials",
    "admin_sekolah_bp /students/bulk-delete": "accounts and roster rows",
    "admin_sekolah_bp /students/login-cards": "prints login cards",
    "admin_sekolah_bp /teachers/login-cards": "prints login cards",
    "admin_sekolah_bp /officials/login-cards": "prints login cards",
    "admin_sekolah_bp /emails/upload": "attaches account emails",
    "admin_sekolah_bp /users/<user_id>/reset-password": "an account's credential",
    "admin_sekolah_bp /subscription/subscribe": "a subscription purchase",

    # ── principal.py: nothing beyond the four locked invigilation writes ──

    # ── public.py / webhook.py / tools.py: no year data ──
    "public_bp /api/demo-request": "a prospective school's demo request",
    "webhook_bp /midtrans": "a payment provider's callback",
    "webhook_bp /fonnte": "a messaging provider's callback",
    "tools_bp /generate-answer-sheet": "prints a sheet; writes nothing",

    # ── super_admin.py: platform operations, not a school's year ──
    "super_bp /api/school/<school_id>/toggle-whiteboard": "a school-level feature flag",
    "super_bp /api/school/<school_id>/suspend": "a school-level suspension",
    "super_bp /api/school/<school_id>/extend-trial": "a school's trial window",
    "super_bp /api/school/<school_id>/reset-admin-pw": "an account's credential",
    # The school directory: a school row and the account that runs it. Neither is a
    # year's paper or mark, and neither has a year resource for the decorator to
    # resolve — a school belongs to no school year.
    "super_bp /schools/create": "a school and its admin account, not a year's data",
    "super_bp /schools/<school_id>/edit": "a school's name and NPSN, not a year's data",
    "super_bp /schools/<school_id>/status": "a school's active flag, not a year's data",
    "super_bp /schools/<school_id>/admin": "an admin account's address and jabatan",
    "super_bp /api/user/<user_id>/position": "an account's jabatan label, not a year's marks",
    "super_bp /deploy-status/release": "the runner's own release control",
    "super_bp /deploy-status/rebaseline": "the runner's own perf baseline",
    "super_bp /deploy-status/test-alert": "a test alert",
    "super_bp /reconcile-periods":
        "re-derives each exam's assessment-period tag against the running "
        "calendar; it writes no mark and touches no paper's scores",
    "super_bp /reset-demo-passwords": "the demo accounts' credentials",
    "super_bp /reset-demo-data": "the demo data",
    "super_bp /demo-settings": "the demo page's switches",
    "super_bp /midtrans": "platform payment configuration",
    "super_bp /plans/new": "a platform pricing plan",
    "super_bp /plans/<int:plan_id>/edit": "a platform pricing plan",
    "super_bp /plans/<int:plan_id>/delete": "a platform pricing plan",
    "super_bp /trial-settings": "platform trial configuration",
    "super_bp /payment-fee-settings": "platform fee configuration",
    "super_bp /pricing-settings": "platform pricing configuration",
    "super_bp /activation-codes/<school_id>/regenerate": "a subscription code",
    "super_bp /activation-codes/<school_id>/send-code": "sends a subscription code",
    "super_bp /activation-codes/generate": "a subscription code",
    "super_bp /activation-codes/<school_id>/activate-cash": "activates a cash payment",
    "super_bp /reset-school-data": "a platform reset tool for one school",
    "super_bp /whatsapp-settings": "platform messaging configuration",
    "super_bp /file-management":
        "a platform file inventory; it moves storage objects, not a year's rows",
    "super_bp /api/omr-test/batch": "a calibration harness; writes no marks",
    "super_bp /api/omr-test/single": "a calibration harness; writes no marks",
    "super_bp /api/omr-test/calibrate": "a calibration harness; writes no marks",
    "super_bp /api/feature-flags/<flag_id>/toggle": "a platform feature flag",
    "super_bp /api/user/<user_id>/reset-password": "an account's credential",
    "super_bp /api/user/<user_id>/update-email": "an account's email",
    "super_bp /api/user/<user_id>/suspend": "an account's status",
    "super_bp /api/privacy-settings/save": "platform privacy configuration",
    "super_bp /email-settings": "platform SMTP configuration",
    "super_bp /email-settings/test": "sends a test email",
}

MUTATING = ("POST", "PUT", "PATCH", "DELETE")
ROUTE_RE = re.compile(
    r'@(\w+)\.route\(\s*"([^"]+)"(?:[^)]*?methods=\[([^\]]*)\])?[^)]*\)\n'
    r'((?:@[^\n]*\n)*)def (\w+)\(')


def _routes():
    """[(blueprint, path, methods, decorators, function, source)] for every route."""
    out = []
    for path in ROUTE_FILES:
        src = path.read_text(encoding="utf-8-sig")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        bodies = {n.name: ast.get_source_segment(src, n) or ""
                  for n in tree.body if isinstance(n, ast.FunctionDef)}
        for match in ROUTE_RE.finditer(src):
            bp, rule, methods_raw, decos, name = match.groups()
            methods = {m.strip().strip('"') for m in (methods_raw or '"GET"').split(",")}
            out.append((bp, rule, methods, decos, name, bodies.get(name, "")))
    return out


def _writing():
    return [r for r in _routes() if r[2] & set(MUTATING)]


def test_the_sweep_covers_every_route_module_at_all():
    """A sweep that reads one file is the hole this file exists to close."""
    assert len(ROUTE_FILES) >= 15, f"only {len(ROUTE_FILES)} route modules"
    found = _writing()
    assert len(found) > 150, f"the sweep only found {len(found)} mutating routes"
    # The doors outside teacher.py are the ones this sweep was extended for.
    blueprints = {bp for bp, *_rest in found}
    assert {"api_bp", "whiteboard_teacher_bp", "principal_bp"} <= blueprints, (
        f"the sweep is not seeing the other blueprints: {sorted(blueprints)}")


def test_every_write_route_is_locked_or_excused_with_a_reason():
    unclassified = []
    for bp, rule, methods, decos, name, _src in _writing():
        if "@open_year_required" in decos:
            continue
        entry = f"{bp} {rule}"
        if entry in EXEMPT:
            assert EXEMPT[entry].strip(), f"{entry} is excused with no reason"
            continue
        unclassified.append(f"{entry} ({name})")
    assert not unclassified, (
        "these mutating routes can change a closed year's data without asking:\n  "
        + "\n  ".join(sorted(unclassified))
        + "\n\nAdd @open_year_required(...) or an entry in EXEMPT with a reason.")


def test_every_exemption_still_exists():
    """An exemption for a route that is gone hides the next route that takes it."""
    paths = {f"{bp} {rule}" for bp, rule, _m, _d, _n, _s in _writing()}
    stale = sorted(set(EXEMPT) - paths)
    assert not stale, f"EXEMPT names routes that no longer exist: {stale}"


def test_the_decorator_names_an_id_the_route_actually_carries():
    """`@open_year_required("exam_id")` on a route with no such id is dead code.

    The id may ride in the URL or in the body — the grading endpoints are
    body-only — so the check accepts either, and requires the key to be named.
    """
    wrong = []
    for bp, rule, methods, decos, name, src in _writing():
        if "@open_year_required" not in decos:
            continue
        keys = re.findall(r'open_year_required\(\s*"([^"]+)"', decos)
        assert keys, f"{bp} {rule} uses @open_year_required without naming a key"
        for key in keys:
            if f"<{key}>" in rule:
                continue
            if f'"{key}"' in src or f"'{key}'" in src:
                continue
            wrong.append(f"{bp} {rule} names {key} but neither the URL nor the "
                         f"view reads it")
    assert not wrong, "\n".join(wrong)


def test_every_file_that_locks_a_route_imports_the_decorator():
    for path in ROUTE_FILES:
        src = path.read_text(encoding="utf-8-sig")
        if "@open_year_required" in src:
            assert "from app.decorators.year_lock import open_year_required" in src, (
                f"{path.name} applies the lock without importing it")


def test_the_decorator_has_a_resolver_for_every_key_the_sweep_names():
    named = set()
    for _bp, _rule, _m, decos, _n, _s in _writing():
        named.update(re.findall(r'open_year_required\(\s*"([^"]+)"', decos))
    known = set(year_lock.RESOLVERS) | {"exam_id"}
    assert named <= known, f"no resolver for: {sorted(named - known)}"


# ── the rule itself ──────────────────────────────────────────────────────────

class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.filters = db, table, {}

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self.filters[col] = val
        return self

    def in_(self, col, vals):
        self.filters[col] = list(vals)
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        rows = self.db.tables.get(self.table, [])
        out = []
        for r in rows:
            ok = True
            for k, v in self.filters.items():
                if isinstance(v, list):
                    if r.get(k) not in v:
                        ok = False
                elif str(r.get(k)) != str(v):
                    ok = False
            if ok:
                out.append(r)
        return SimpleNamespace(data=out)


class _Db:
    def __init__(self, tables):
        self.tables = tables

    def table(self, name):
        return _Q(self, name)


def test_a_paper_carries_its_own_year():
    db = _Db({"exams": [{"id": "e-1", "school_year_id": "y-1", "class_ids": []}]})
    assert academic_year.year_of_exam(db, "e-1") == "y-1"


def test_an_old_paper_is_dated_by_its_class():
    db = _Db({"exams": [{"id": "e-1", "school_year_id": None, "class_ids": ["c-1"]}],
              "classes": [{"id": "c-1", "school_year_id": "y-1"}]})
    assert academic_year.year_of_exam(db, "e-1") == "y-1"


def test_a_paper_spanning_years_has_no_single_year():
    db = _Db({"exams": [{"id": "e-1", "school_year_id": None,
                         "class_ids": ["c-1", "c-2"]}],
              "classes": [{"id": "c-1", "school_year_id": "y-1"},
                          {"id": "c-2", "school_year_id": "y-2"}]})
    assert academic_year.year_of_exam(db, "e-1") is None


def test_a_submission_reaches_its_year_through_its_exam():
    db = _Db({"submissions": [{"id": "s-1", "exam_id": "e-1"}],
              "exams": [{"id": "e-1", "school_year_id": "y-1", "class_ids": []}]})
    assert academic_year.year_of_submission(db, "s-1") == "y-1"


def test_a_whiteboard_reaches_its_year_through_its_class():
    db = _Db({"whiteboards": [{"id": "w-1", "class_id": "c-1"}],
              "classes": [{"id": "c-1", "school_year_id": "y-1"}]})
    assert academic_year.year_of_whiteboard(db, "w-1") == "y-1"


def test_a_sitting_and_an_invigilators_duty_reach_the_paper_year():
    db = _Db({"invigilation_schedules": [{"id": "sch-1", "exam_id": "e-1"}],
              "invigilator_assignments": [{"id": "a-1", "schedule_id": "sch-1"}],
              "exams": [{"id": "e-1", "school_year_id": "y-1", "class_ids": []}]})
    assert academic_year.year_of_schedule(db, "sch-1") == "y-1"
    assert academic_year.year_of_invigilator_assignment(db, "a-1") == "y-1"


def test_a_retake_request_reaches_the_paper_year():
    db = _Db({"exam_retake_requests": [{"id": "r-1", "exam_id": "e-1"}],
              "exams": [{"id": "e-1", "school_year_id": "y-1", "class_ids": []}]})
    assert academic_year.year_of_retake_request(db, "r-1") == "y-1"


def test_only_a_closed_year_refuses_a_write():
    closed = _Db({"school_years": [{"id": "y-1", "status": "closed"}]})
    open_ = _Db({"school_years": [{"id": "y-1", "status": "active"}]})
    draft = _Db({"school_years": [{"id": "y-1", "status": "draft"}]})
    assert academic_year.write_refusal(closed, "y-1")
    assert academic_year.write_refusal(open_, "y-1") is None
    assert academic_year.write_refusal(draft, "y-1") is None


def test_an_unknown_year_does_not_refuse_every_write():
    """A read blip must not take the whole grading screen down."""
    assert academic_year.write_refusal(_Db({}), "y-missing") is None
    assert academic_year.write_refusal(_Db({}), None) is None


def test_a_closed_year_is_still_not_editable():
    closed = _Db({"school_years": [{"id": "y-1", "status": "closed"}]})
    assert academic_year.editable(closed, "y-1") is False


def test_the_message_is_stated_once():
    assert "ditutup" in year_lock.CLOSED_YEAR_MESSAGE


def test_promotion_into_a_closed_year_is_refused_too():
    """The school admin's promote door is the same rule, not a separate one."""
    src = (ROOT / "app" / "routes" / "admin_sekolah.py").read_text(encoding="utf-8")
    promote = src.split("def promote(")[1].split("\n# \u2500\u2500\u2500")[0]
    assert "academic_year.write_refusal(" in promote, (
        "promotion can still push pupils into a year whose marks are history")


# ── the decorator, actually refusing ────────────────────────────────────────

def _decorated(monkeypatch, year_id, refusal, key="exam_id"):
    from app.services import academic_year as ay
    from app.utils import auth as auth_mod

    monkeypatch.setattr(ay, "year_of_exam", lambda sb, i: year_id)
    monkeypatch.setattr(ay, "year_of_submission", lambda sb, i: year_id)
    monkeypatch.setattr(ay, "year_of_whiteboard", lambda sb, i: year_id)
    monkeypatch.setattr(ay, "write_refusal", lambda sb, y: refusal)
    monkeypatch.setattr(auth_mod, "get_supabase", lambda: object())

    @year_lock.open_year_required(key)
    def view(**kwargs):
        return "wrote"

    return view


def test_a_closed_year_answers_403_to_an_api_write(app, monkeypatch):
    view = _decorated(monkeypatch, "y-closed", "closed")
    with app.test_request_context("/api/grade-question/e-1/0/save", method="POST",
                                  headers={"Accept": "application/json"}):
        resp = view(exam_id="e-1")
    body, status = (resp[0], resp[1]) if isinstance(resp, tuple) else (resp, 200)
    assert status == 403, f"a closed year answered {status}"


def test_an_open_year_lets_the_write_through(app, monkeypatch):
    view = _decorated(monkeypatch, "y-open", None)
    with app.test_request_context("/api/grade-question/e-1/0/save", method="POST",
                                  headers={"Accept": "application/json"}):
        assert view(exam_id="e-1") == "wrote"


def test_a_paper_with_no_year_is_not_refused(app, monkeypatch):
    """An exam written before the column existed must still be gradeable."""
    view = _decorated(monkeypatch, None, None)
    with app.test_request_context("/api/grade-question/e-1/0/save", method="POST",
                                  headers={"Accept": "application/json"}):
        assert view(exam_id="e-1") == "wrote"


def test_a_body_only_id_is_found_in_the_json_payload(app, monkeypatch):
    """The grading endpoints carry the paper in the body, not the URL."""
    view = _decorated(monkeypatch, "y-closed", "closed")
    with app.test_request_context("/api/scan/save", method="POST",
                                  json={"exam_id": "e-body"},
                                  headers={"Accept": "application/json"}):
        resp = view()
    status = resp[1] if isinstance(resp, tuple) else 200
    assert status == 403, "a body-only exam id was not resolved, so no lock applied"


def test_a_whiteboard_write_resolves_its_class_year(app, monkeypatch):
    view = _decorated(monkeypatch, "y-closed", "closed", key="whiteboard_id")
    with app.test_request_context("/api/whiteboard/w-1", method="PUT",
                                  json={}, headers={"Accept": "application/json"}):
        resp = view(whiteboard_id="w-1")
    status = resp[1] if isinstance(resp, tuple) else 200
    assert status == 403


def test_a_form_write_into_a_closed_year_is_redirected_with_a_message(app, monkeypatch):
    view = _decorated(monkeypatch, "y-closed", "closed")
    with app.test_request_context("/teacher/exams/e-1/recalculate", method="POST"):
        resp = view(exam_id="e-1")
    assert getattr(resp, "status_code", None) in (301, 302), resp
