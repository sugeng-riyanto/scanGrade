"""The SEB surface, role by role: every door, driven, both directions.

Why this file exists
--------------------
`test_seb_access.py` pins the *predicate* — nine refusals and four grants, each
with the reason it answers. What it cannot see is the door: a route that never
calls the predicate, calls it with the wrong access type, or hands the predicate a
column the select never asked for is green there and broken in production. This
repository has paid for that shape twice already, so the rule is written down: **a
granted permission with no working door is a defect, and only running the door can
show it.**

The defect this file found is the one it is named for. `decide` answered a pupil's
request for their own `.seb` file with `exam_targets.student_is_included` — the
*per-pupil list* predicate, which only exists for a `target_mode='students'` paper.
Read on its own it is `False` for every pupil of an ordinary class-assigned paper,
so `/student/exams/<id>/seb-file` — the route behind the handshake page's own
*Unduh berkas .seb saya* button — answered **403 to every pupil in the school**,
on every paper the school had not switched to a per-pupil list. The link was
offered by a page whose own test asserts the link exists, and the file behind it
was unreachable.

The doors, and what a role gets from each
-----------------------------------------
======  ======================  ==========================================
door    audience                what it hands over
======  ======================  ==========================================
panel   staff                   the state, the switch, and (if granted) the keys
enable  the paper's owner       mints the credential and turns the requirement on
disable the paper's owner       turns it off; the credential survives
reissue the paper's owner       new passwords, new Config Key
password staff, inside scope    one plaintext password, per reveal, audited
file    staff + the pupil       the ``.seb`` file (hashes only, never a password)
======  ======================  ==========================================

Everything below runs the *route body* against the app's own seam
(`app.extensions["supabase"]`), so `_load_exam`, `exam_sitting_allowed` and
`invigilated_exam_ids` are the real ones. The substitution is a PostgREST stand-in
that really stores, plus one thing a test cannot have: the box's clock, supplied to
`seb_access.decide` where the route passes none — the duty window is a property of
the *decision*, and `now` is its documented argument.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.routes import seb as sebmod
from app.services import seb_access, seb_config_key, seb_crypto, seb_service

SCHOOL = "sc-target"
OTHER_SCHOOL = "sc-elsewhere"
OWNER = "u-owner"
COLLEAGUE = "u-colleague"
INVIGILATOR = "u-invigilator"
ADMIN = "u-admin"
PRINCIPAL = "u-principal"
VICE = "u-vice"
PUPIL = "u-pupil"
OUTSIDER = "u-outsider"
EXCLUDED = "u-excluded"
ROOT = "u-root"

EXAM_ID = "ex-1"
SCHEDULE = "sch-1"
CLASS = "class-7A"
OTHER_CLASS = "class-7B"

QUIT_PASSWORD = "BukaSekarang7"
ADMIN_PASSWORD = "PengawasKunci9"

NOW = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)

ROLES = ("guru", "admin_sekolah", "principal", "vice_principal", "murid",
         "super_admin")


# ── a postgrest stand-in, small enough to read ──────────────────────────────

class _Query:
    def __init__(self, store, name):
        self._store, self._name = store, name
        self._filters, self._one, self._pending = [], False, None
        self._order, self._limit = None, None

    def select(self, *columns, **kwargs):
        return self

    def eq(self, column, value):
        self._filters.append((column, value))
        return self

    def in_(self, column, values):
        self._filters.append((column, list(values)))
        return self

    def neq(self, *a, **k):
        return self

    def is_(self, *a, **k):
        return self

    def order(self, column, desc=False):
        self._order = (column, desc)
        return self

    def limit(self, count):
        self._limit = count
        return self

    def single(self):
        self._one = True
        return self

    def maybe_single(self):
        # postgrest answers a miss with a row-shaped `None`, never an empty list.
        self._one = True
        return self

    def insert(self, payload):
        self._pending = ("insert", dict(payload))
        return self

    def upsert(self, payload, **kwargs):
        self._pending = ("upsert", dict(payload))
        return self

    def update(self, patch):
        self._pending = ("update", dict(patch))
        return self

    def _matched(self):
        out = []
        for row in self._store.setdefault(self._name, []):
            hit = True
            for column, want in self._filters:
                if isinstance(want, list):
                    hit = hit and row.get(column) in want
                else:
                    hit = hit and row.get(column) == want
            if hit:
                out.append(row)
        if self._order:
            column, desc = self._order
            out.sort(key=lambda r: str(r.get(column) or ""), reverse=bool(desc))
        if self._limit:
            out = out[: self._limit]
        return out

    def execute(self):
        if self._pending:
            kind, payload = self._pending
            self._pending = None
            rows = self._matched()
            if kind == "insert":
                self._store.setdefault(self._name, []).append(dict(payload))
                return SimpleNamespace(data=[dict(payload)], count=1)
            if kind == "upsert":
                if rows:
                    rows[0].update(payload)
                    return SimpleNamespace(data=[rows[0]], count=1)
                self._store.setdefault(self._name, []).append(dict(payload))
                return SimpleNamespace(data=[dict(payload)], count=1)
            for row in rows:
                row.update(payload)
            return SimpleNamespace(data=rows, count=len(rows))
        rows = self._matched()
        if self._one:
            return SimpleNamespace(data=rows[0] if rows else None, count=len(rows))
        return SimpleNamespace(data=rows, count=len(rows))


class _Db:
    """One school, one gated paper, and the people the matrix names."""

    def __init__(self, exam, *, credentials=None):
        self.tables = {
            "exams": [exam],
            "profiles": [
                {"id": OWNER, "full_name": "Guru Pemilik", "role": "guru",
                 "school_id": SCHOOL, "class_id": None},
                {"id": COLLEAGUE, "full_name": "Guru Lain", "role": "guru",
                 "school_id": SCHOOL, "class_id": None},
                {"id": INVIGILATOR, "full_name": "Pengawas", "role": "guru",
                 "school_id": SCHOOL, "class_id": None},
                {"id": ADMIN, "full_name": "Admin Sekolah", "role": "admin_sekolah",
                 "school_id": SCHOOL, "class_id": None},
                {"id": PRINCIPAL, "full_name": "Kepala Sekolah", "role": "principal",
                 "school_id": SCHOOL, "class_id": None},
                {"id": VICE, "full_name": "Wakil Kepala", "role": "vice_principal",
                 "school_id": SCHOOL, "class_id": None},
                {"id": PUPIL, "full_name": "Ahmad", "role": "murid",
                 "school_id": SCHOOL, "class_id": CLASS},
                {"id": OUTSIDER, "full_name": "Budi", "role": "murid",
                 "school_id": SCHOOL, "class_id": OTHER_CLASS},
                {"id": EXCLUDED, "full_name": "Citra", "role": "murid",
                 "school_id": SCHOOL, "class_id": CLASS},
                {"id": ROOT, "full_name": "Super Admin", "role": "super_admin",
                 "school_id": None, "class_id": None},
            ],
            "classes": [{"id": CLASS, "name": "7A", "school_id": SCHOOL},
                        {"id": OTHER_CLASS, "name": "7B", "school_id": SCHOOL}],
            "submissions": [],
            "exam_seb_credential": list(credentials or []),
            "exam_target_student": [],
            "seb_access_log": [],
            "seb_door_refusal": [],
            "audit_logs": [],
            "exam_access_codes": [],
            "invigilation_schedules": [],
            "invigilator_assignments": [],
        }

    def table(self, name):
        return _Query(self.tables, name)

    def rows(self, name):
        return self.tables.get(name, [])


def _exam_row(*, require_seb=True, credential=None, target_mode="class",
              teacher_id=OWNER, key=True):
    """The row `_load_exam` selects, with the key its own settings produce.

    ``key=False`` is the half-issued state migration 062 leaves room for: the
    requirement is on and the key is missing, which is the state nobody should ever
    reach through the panel and which the doors must refuse rather than fall back on.
    """
    row = {"id": EXAM_ID, "school_id": SCHOOL, "teacher_id": teacher_id,
           "title": "Latihan Ujian", "require_seb": require_seb,
           "seb_config_key": None, "duration_minutes": 60, "start_at": None,
           "end_at": None, "auto_submit_on_window_end": False, "max_violations": 3,
           "lock_pending_resume": False, "target_mode": target_mode,
           "class_ids": [CLASS], "is_published": True, "status": "active",
           "block_right_click": True, "block_screenshot": True}
    if require_seb and key:
        row["seb_config_key"] = _key_for(row, credential)
    return row


def _key_for(exam, credential):
    settings = seb_service.settings_for(
        exam, start_url=seb_service.start_url(EXAM_ID),
        quit_hash=(credential or {}).get("quit_password_hash", ""),
        admin_hash=(credential or {}).get("admin_password_hash", ""))
    return seb_config_key.config_key(settings)


def _credential(app):
    """A stored credential, encrypted with the app's own root secret.

    The reveal path is driven for real — AES-GCM under `SECRET_KEY`, derived exactly
    as the box derives it — so the door is measured handing a password back, not a
    decision about one.
    """
    secret = app.config["SECRET_KEY"]
    return {"exam_id": EXAM_ID,
            "quit_password_hash": seb_crypto.sha256_hex(QUIT_PASSWORD),
            "admin_password_hash": seb_crypto.sha256_hex(ADMIN_PASSWORD),
            "quit_password_enc": seb_crypto.encrypt(QUIT_PASSWORD, secret=secret),
            "admin_password_enc": seb_crypto.encrypt(ADMIN_PASSWORD, secret=secret)}


# ── driving one door as one role ────────────────────────────────────────────

def peel(view):
    """The undecorated body, so `g` can be populated by hand (the repo's idiom)."""
    while hasattr(view, "__wrapped__"):
        view = view.__wrapped__
    return view


VIEWS = {
    "panel": ("panel", "GET"),
    "password": ("reveal_password", "POST"),
    "file": ("download_file", "GET"),
    "student_file": ("student_file", "GET"),
    "enable": ("enable", "POST"),
    "disable": ("disable", "POST"),
    "reissue": ("reissue", "POST"),
    "claim": ("js_claim", "GET"),
    "refusal": ("refusal_report", "POST"),
    "environment": ("environment", "POST"),
    "guide": ("guide", "GET"),
    "test_file": ("test_config", "GET"),
    "verified": ("test_verified", "GET"),
}

#: The doors whose route reads no exam from the path: the three public ones and
#: the signal endpoint, which takes the exam in its payload because it is a POST
#: from a page that already knows which paper it is sitting.
NO_EXAM_ID = ("guide", "test_file", "verified", "environment")


def run(app, monkeypatch, *, role="guru", user=OWNER, school=SCHOOL, door="panel",
        require_seb=True, target_mode="class", targets=(), duty=None,
        exam=None, credential=True, key=True, method=None, data=None, now=NOW):
    """Run one door body as one role; return the response and the world it wrote.

    ``monkeypatch`` is the fixture, used for the one reading a test cannot have —
    the clock the duty window is judged against. Everything else is real: the app's
    `get_supabase()`, the route, the services it calls.
    """
    name, verb = VIEWS[door]
    verb = method or verb

    seen = {}

    def _capture(template_name, **kwargs):
        seen["template"] = template_name
        seen["passed"] = {k: v for k, v in kwargs.items()
                          if k in ("has_credential", "can_manage",
                                   "file_is_current", "verdict", "back_url")}
        return SimpleNamespace(name=template_name)

    # The duty window is a property of the *decision* and `now` is its own argument;
    # the route passes none, so the clock is supplied here and nothing else is
    # touched. `decide` is the seam, not the rule: the window arithmetic, the
    # assignment lookup and the predicate all run.
    if duty is not None or now is not NOW:
        real_decide = seb_access.decide
        monkeypatch.setattr(
            seb_access, "decide",
            lambda *a, **k: real_decide(*a, **{**k, "now": now}))

    monkeypatch.setattr(sebmod, "render_template", _capture)
    payload = dict(data or {})
    if door == "password":
        payload.setdefault("kind", "quit")
    if door == "enable":
        payload.setdefault(sebmod.CONFIRM_FIELD, "1")
    if door == "refusal":
        payload.setdefault("reason", "no_client")
    if door == "environment":
        payload.setdefault("exam_id", EXAM_ID)
    if door == "claim":
        payload.setdefault("config_key", "b" * 64)

    from flask import g

    with app.test_request_context(f"/seb-probe/{door}", method=verb, data=payload):
        # The world is built *inside* the request, because the paper's key is
        # computed from the URL the request arrived on — the same reason the file
        # and the door cannot disagree about it.
        # A paper nobody gated has no credential: the two are one thing (migration
        # 062), and a test that seeded one anyway would be measuring a state the
        # app cannot reach.
        held = _credential(app) if (credential and require_seb and key) else None
        row = exam if exam is not None else _exam_row(
            require_seb=require_seb, credential=held, target_mode=target_mode, key=key)
        db = _Db(row, credentials=[held] if held else [])
        for student_id in targets:
            db.tables["exam_target_student"].append(
                {"exam_id": EXAM_ID, "student_id": student_id,
                 "status": "included"})
        if duty is not None:
            db.tables["invigilation_schedules"].append(
                {"id": SCHEDULE, "school_id": SCHOOL, "exam_id": EXAM_ID,
                 "scheduled_at": duty.isoformat()})
            db.tables["invigilator_assignments"].append(
                {"id": "ia-1", "school_id": SCHOOL, "schedule_id": SCHEDULE,
                 "teacher_id": INVIGILATOR})
        app.extensions["supabase"] = db
        g.user_id, g.user_role, g.user_school_id = user, role, school
        g.user_email, g.user_name, g.tz_offset = "who@sekolah.test", "Who", 7
        view = peel(getattr(sebmod, name))
        try:
            result = view() if door in NO_EXAM_ID else view(EXAM_ID)
        except Exception as exc:                                 # noqa: BLE001
            raised = {"code": getattr(exc, "code", None),
                      "description": getattr(exc, "description", "")}
            return {"status": raised["code"], "raised": raised, "db": db,
                    "exam": db.rows("exams")[0], "seen": seen}

    if isinstance(result, tuple):
        response, status = result[0], result[1]
    elif hasattr(result, "status_code"):
        response, status = result, result.status_code
    else:
        # `render_template` was stood in for, so the answer *is* the template name.
        response, status = result, "template"
    body = ""
    if hasattr(response, "get_data"):
        try:
            body = response.get_data(as_text=True)
        except Exception:                                        # noqa: BLE001
            body = ""
    return {"status": status, "body": body, "response": response, "db": db,
            "exam": db.rows("exams")[0], "seen": seen}


def status_of(result):
    return result["status"]


def body_bytes(result) -> bytes:
    """The bytes a `send_file` door handed over.

    A streamed response is in direct-passthrough mode and refuses `.data`; the body
    is the wrapper it is holding, which is what the pupil's downloader receives.
    """
    response = result["response"]
    if getattr(response, "direct_passthrough", False):
        return b"".join(response.response)
    return response.get_data()


# ── the pupil: the file, and never a password ───────────────────────────────

class TestThePupilsOwnFile:
    """The regression this file was written for, both ways round."""

    def test_a_pupil_of_an_ordinary_class_paper_gets_the_file(self, app, monkeypatch):
        """A class-assigned paper has no per-pupil rows at all — and that is the
        normal paper. The door used to read the per-pupil list on its own, so this
        answer was 403 for every pupil in the school, behind a link the handshake
        page offers and asserts."""
        out = run(app, monkeypatch, role="murid", user=PUPIL, door="student_file")
        assert status_of(out) == 200, out
        assert body_bytes(out)[:2] == b"\x1f\x8b", "not a .seb file"

    def test_the_file_is_the_one_the_stored_key_belongs_to(self, app, monkeypatch):
        out = run(app, monkeypatch, role="murid", user=PUPIL, door="student_file")
        settings = seb_service.decode_seb(body_bytes(out))
        assert seb_config_key.config_key(settings) == out["exam"]["seb_config_key"]

    def test_a_pupil_of_another_class_gets_nothing(self, app, monkeypatch):
        out = run(app, monkeypatch, role="murid", user=OUTSIDER, door="student_file")
        assert status_of(out) == 403, out

    def test_a_pupil_the_target_list_excludes_gets_nothing(self, app, monkeypatch):
        out = run(app, monkeypatch, role="murid", user=EXCLUDED, door="student_file",
                  target_mode="students", targets=[PUPIL])
        assert status_of(out) == 403, out

    def test_a_pupil_the_target_list_includes_gets_it(self, app, monkeypatch):
        out = run(app, monkeypatch, role="murid", user=EXCLUDED, door="student_file",
                  target_mode="students", targets=[EXCLUDED])
        assert status_of(out) == 200, out

    def test_an_ungated_paper_has_no_file_to_hand_over(self, app, monkeypatch):
        out = run(app, monkeypatch, role="murid", user=PUPIL, door="student_file",
                  require_seb=False)
        assert status_of(out) == 403, out

    def test_a_gated_paper_with_no_key_is_refused_by_the_decision(self, app,
                                                                 monkeypatch):
        """A half-issued requirement is a refusal, not a file and not a 409 from a
        route that had already decided to hand one over."""
        out = run(app, monkeypatch, role="murid", user=PUPIL, door="student_file",
                  key=False)
        assert status_of(out) == 403, out

    def test_the_pupil_is_refused_a_password_at_every_door(self, app, monkeypatch):
        """The brief's hardest rule, asserted at both endpoints a pupil can reach."""
        for door, user in (("password", PUPIL), ("password", OUTSIDER)):
            out = run(app, monkeypatch, role="murid", user=user, door=door)
            assert out["status"] == 403, door
            assert "password" not in out["response"].get_json()

    def test_the_pupil_cannot_reach_the_setup_writes(self, app, monkeypatch):
        for door in ("enable", "disable", "reissue"):
            out = run(app, monkeypatch, role="murid", user=PUPIL, door=door)
            assert status_of(out) == 403, door

    def test_the_pupil_never_gets_the_panel(self, app, monkeypatch):
        out = run(app, monkeypatch, role="murid", user=PUPIL, door="panel")
        assert status_of(out) == 403


# ── the owner: the whole surface, including the setup ───────────────────────

class TestTheOwnersDoors:
    def test_the_owner_may_read_the_password_and_download_the_file(self, app,
                                                                  monkeypatch):
        for door in ("password", "file", "student_file"):
            out = run(app, monkeypatch, role="guru", user=OWNER, door=door)
            assert status_of(out) == 200, door
        out = run(app, monkeypatch, role="guru", user=OWNER, door="password")
        assert out["response"].get_json()["password"] == QUIT_PASSWORD

    def test_the_owner_may_open_the_panel(self, app, monkeypatch):
        out = run(app, monkeypatch, role="guru", user=OWNER, door="panel")
        assert status_of(out) == "template"
        assert out["seen"]["template"] == "teacher/seb_panel.html"
        assert out["seen"]["passed"]["can_manage"] is True

    def test_the_owner_may_open_the_panel_while_the_requirement_is_off(self, app,
                                                                      monkeypatch):
        """The page *is* the switch: refusing the owner there made the feature
        unreachable, which is the dead end this page exists to remove."""
        out = run(app, monkeypatch, role="guru", user=OWNER, door="panel",
                  require_seb=False)
        assert status_of(out) == "template"
        assert out["seen"]["passed"]["can_manage"] is True
        assert out["seen"]["passed"]["has_credential"] is False

    def test_the_owner_sets_the_paper_up(self, app, monkeypatch):
        """enable writes the requirement *and* the key, from one action."""
        out = run(app, monkeypatch, role="guru", user=OWNER, door="enable",
                  require_seb=False)
        assert status_of(out) == 302, out
        exam = out["exam"]
        assert exam["require_seb"] is True
        assert exam["seb_config_key"], "the toggle went on with no key to check"
        credential = out["db"].rows("exam_seb_credential")
        assert len(credential) == 1, credential
        assert credential[0]["quit_password_hash"]
        # The generated file and the stored key are one thing: the settings the
        # download would rebuild hash to the key the door will check. The URL is the
        # one a request arrives on, so the recomputation happens in a request.
        with app.test_request_context(f"/student/exams/{EXAM_ID}"):
            settings = seb_service.settings_for(
                exam, start_url=seb_service.start_url(EXAM_ID),
                quit_hash=credential[0]["quit_password_hash"],
                admin_hash=credential[0]["admin_password_hash"])
            assert seb_config_key.config_key(settings) == exam["seb_config_key"]

    def test_the_owner_turns_it_off_and_the_credential_survives(self, app,
                                                               monkeypatch):
        out = run(app, monkeypatch, role="guru", user=OWNER, door="disable")
        assert status_of(out) == 302
        assert out["exam"]["require_seb"] is False
        assert len(out["db"].rows("exam_seb_credential")) == 1, (
            "turning the requirement off destroyed the credential, so turning it "
            "back on would re-issue and invalidate every file already handed out")

    def test_a_re_issue_mints_a_new_key(self, app, monkeypatch):
        before = run(app, monkeypatch, role="guru", user=OWNER, door="panel")
        old_key = before["exam"]["seb_config_key"]
        out = run(app, monkeypatch, role="guru", user=OWNER, door="reissue")
        assert status_of(out) == 302
        assert out["exam"]["seb_config_key"] != old_key

    def test_a_colleague_with_no_duty_gets_nothing(self, app, monkeypatch):
        for door in ("panel", "password", "file"):
            out = run(app, monkeypatch, role="guru", user=COLLEAGUE, door=door)
            assert status_of(out) == 403, door
        out = run(app, monkeypatch, role="guru", user=COLLEAGUE, door="password")
        assert out["response"].get_json()["reason"] == "not_owner"

    def test_the_toggle_refuses_a_post_without_the_confirmation(self, app,
                                                               monkeypatch):
        out = run(app, monkeypatch, role="guru", user=OWNER, door="enable",
                  require_seb=False, data={sebmod.CONFIRM_FIELD: ""})
        assert status_of(out) == 302, out
        assert out["exam"]["require_seb"] is False, (
            "the requirement went on without the confirmation the server checks")
        assert not out["db"].rows("exam_seb_credential")


# ── the invigilator: a window, and only inside it ───────────────────────────

class TestTheInvigilatorsWindow:
    def test_on_duty_may_read_the_password_and_download_the_file(self, app,
                                                                monkeypatch):
        for door in ("password", "file"):
            out = run(app, monkeypatch, role="guru", user=INVIGILATOR, door=door,
                      duty=NOW - timedelta(minutes=5))
            assert status_of(out) == 200, door

    def test_on_duty_may_open_the_panel_and_is_named_as_an_invigilator(self, app,
                                                                       monkeypatch):
        out = run(app, monkeypatch, role="guru", user=INVIGILATOR, door="panel",
                  duty=NOW - timedelta(minutes=5))
        assert status_of(out) == "template"
        passed = out["seen"]["passed"]
        assert passed["verdict"]["role"] == seb_access.ROLE_INVIGILATOR
        assert passed["can_manage"] is False, (
            "an invigilator reads a password to help a pupil; turning the "
            "requirement off is the owner's")

    def test_before_the_window_the_supervisor_gets_nothing(self, app, monkeypatch):
        out = run(app, monkeypatch, role="guru", user=INVIGILATOR, door="password",
                  duty=NOW + timedelta(hours=3))
        assert out["status"] == 403
        assert out["response"].get_json()["reason"] == "outside_duty_window"

    def test_after_the_window_the_supervisor_gets_nothing(self, app, monkeypatch):
        out = run(app, monkeypatch, role="guru", user=INVIGILATOR, door="file",
                  duty=NOW - timedelta(days=2))
        assert status_of(out) == 403
        assert out["raised"]["description"] == "outside_duty_window"

    def test_on_duty_never_gets_the_setup_writes(self, app, monkeypatch):
        for door in ("enable", "disable", "reissue"):
            out = run(app, monkeypatch, role="guru", user=INVIGILATOR, door=door,
                      duty=NOW - timedelta(minutes=5))
            assert status_of(out) == 403, door


# ── the school's staff: the owner's password, on the owner's behalf ─────────

class TestTheSchoolsStaff:
    @pytest.mark.parametrize("role,user", [
        ("admin_sekolah", ADMIN), ("principal", PRINCIPAL),
        ("vice_principal", VICE),
    ])
    def test_the_schools_staff_may_read_and_download(self, app, monkeypatch, role,
                                                     user):
        for door in ("panel", "password", "file"):
            out = run(app, monkeypatch, role=role, user=user, door=door)
            assert status_of(out) in (200, "template"), (role, door, out)

    @pytest.mark.parametrize("role,user", [
        ("admin_sekolah", ADMIN), ("principal", PRINCIPAL),
        ("vice_principal", VICE),
    ])
    def test_the_reading_is_recorded_as_covering_for_the_owner(self, app,
                                                               monkeypatch, role,
                                                               user):
        out = run(app, monkeypatch, role=role, user=user, door="password")
        assert out["response"].get_json()["on_behalf_of"] == OWNER, out
        rows = out["db"].rows("seb_access_log")
        assert len(rows) == 1, rows
        assert rows[0]["role_at_access"] == seb_access.SCHOOL_ROLES[role]
        assert rows[0]["access_type"] == seb_access.ACCESS_VIEW_PASSWORD
        assert rows[0]["on_behalf_of"] == OWNER

    def test_the_schools_admin_may_flip_the_switch(self, app, monkeypatch):
        """The admin manages every other property of this paper, so SEB follows.

        `can_manage_exam` is the predicate the builder's own edit door applies
        (`teacher._guard_exam`), and it admits the admin of the paper's school: the
        admin may change the paper's anti-cheat switches, its answer key and its
        published results. The SEB requirement is a property of that same row
        (`exams.require_seb`, migration 062), so reading it as a separate resource
        the admin cannot touch was the one switch out of reach.
        """
        out = run(app, monkeypatch, role="admin_sekolah", user=ADMIN, door="disable")
        assert status_of(out) == 302, out
        assert out["exam"]["require_seb"] is False

        out = run(app, monkeypatch, role="admin_sekolah", user=ADMIN, door="enable",
                  require_seb=False)
        assert status_of(out) == 302, out
        assert out["exam"]["require_seb"] is True
        assert out["exam"]["seb_config_key"], "the admin turned it on with no key"
        assert out["db"].rows("exam_seb_credential"), "no password was issued"

    def test_the_admin_may_re_issue_and_the_panel_offers_the_switch(self, app,
                                                                   monkeypatch):
        before = run(app, monkeypatch, role="admin_sekolah", user=ADMIN, door="panel")
        assert before["seen"]["passed"]["can_manage"] is True, (
            "the panel refuses to show a switch the door now accepts")
        old_key = before["exam"]["seb_config_key"]
        out = run(app, monkeypatch, role="admin_sekolah", user=ADMIN, door="reissue")
        assert status_of(out) == 302, out
        assert out["exam"]["seb_config_key"] != old_key

    def test_an_admin_of_another_school_may_not_flip_the_switch(self, app,
                                                               monkeypatch):
        for door in ("enable", "disable", "reissue"):
            out = run(app, monkeypatch, role="admin_sekolah", user=ADMIN,
                      school=OTHER_SCHOOL, door=door)
            assert status_of(out) == 403, door

    @pytest.mark.parametrize("role,user", [
        ("principal", PRINCIPAL), ("vice_principal", VICE),
    ])
    def test_an_official_who_does_not_own_the_paper_stays_read_only(self, app,
                                                                   monkeypatch, role,
                                                                   user):
        """Oversight is a read: a paper they did not build is not theirs to gate."""
        for door in ("enable", "disable", "reissue"):
            out = run(app, monkeypatch, role=role, user=user, door=door)
            assert status_of(out) == 403, (role, door)
        out = run(app, monkeypatch, role=role, user=user, door="panel")
        assert status_of(out) == "template", out
        assert out["seen"]["passed"]["can_manage"] is False, role

    @pytest.mark.parametrize("role,user", [
        ("principal", PRINCIPAL), ("vice_principal", VICE),
    ])
    def test_an_official_who_owns_the_paper_may_flip_the_switch(self, app,
                                                               monkeypatch, role, user):
        """An official who teaches owns those papers — `OWN_PAPER_ROLES` says so,
        and the builder already lets them edit and publish them. Their SEB toggle
        is the same fact, not a second kind of permission."""
        # The row's key is computed from the URL the request arrives on, so the
        # paper is built inside the same request context the doors run in.
        cred = _credential(app)

        def owned(require_seb=True):
            with app.test_request_context(f"/student/exams/{EXAM_ID}"):
                return _exam_row(require_seb=require_seb, credential=cred,
                                 teacher_id=user)

        out = run(app, monkeypatch, role=role, user=user, door="disable",
                  exam=owned())
        assert status_of(out) == 302, out
        assert out["exam"]["require_seb"] is False

        out = run(app, monkeypatch, role=role, user=user, door="enable",
                  exam=owned(require_seb=False), require_seb=False)
        assert status_of(out) == 302, out
        assert out["exam"]["require_seb"] is True
        assert out["exam"]["seb_config_key"]

        panel = run(app, monkeypatch, role=role, user=user, door="panel",
                    exam=owned())
        assert panel["seen"]["passed"]["can_manage"] is True, role

    def test_another_schools_admin_gets_nothing(self, app, monkeypatch):
        for door in ("panel", "password", "file"):
            out = run(app, monkeypatch, role="admin_sekolah", user=ADMIN,
                      school=OTHER_SCHOOL, door=door)
            assert status_of(out) == 403, door


# ── nobody else, including the platform administrator ──────────────────────

class TestNobodyElse:
    def test_super_admin_is_refused_everywhere(self, app, monkeypatch):
        for door in ("panel", "password", "file", "student_file", "enable",
                     "disable", "reissue"):
            out = run(app, monkeypatch, role="super_admin", user=ROOT, door=door)
            assert status_of(out) == 403, door

    def test_the_refusal_is_a_decision_and_not_an_accident(self):
        """`can_manage_exam` waves them through; this module does not, and the
        difference is written down so widening one predicate cannot silently widen
        the other."""
        from app.utils.exam_access import can_manage_exam
        exam = {"id": EXAM_ID, "school_id": SCHOOL, "teacher_id": OWNER,
                "require_seb": True, "seb_config_key": "k"}
        assert can_manage_exam(ROOT, "super_admin", None, exam) is True

    def test_an_unknown_role_is_refused_everywhere(self, app, monkeypatch):
        for door in ("panel", "password", "file"):
            out = run(app, monkeypatch, role="kurator", user="u-x", door=door)
            assert status_of(out) == 403, door


# ── the panel's way back, per role ──────────────────────────────────────────

class TestTheWayBack:
    """The door *in* is only half of it: the button out must not be a 403.

    `teacher.my_exams` is a teacher-or-admin endpoint, so a head of school or their
    deputy sent there is refused — the same dead end as a permission with no door,
    one click further in. The panel hands the template the destination its own route
    computed from `g.user_role`; this drives the route, so a table that stops
    matching the roles the matrix grants fails here rather than on the box.
    """

    @pytest.mark.parametrize("role,user,ends_with", [
        ("guru", OWNER, "/teacher/exams"),
        ("admin_sekolah", ADMIN, "/admin-sekolah/invigilation"),
        ("principal", PRINCIPAL, "/principal/invigilation"),
        ("vice_principal", VICE, "/vice-principal/invigilation"),
    ])
    def test_each_role_goes_back_to_the_page_it_came_from(self, app, monkeypatch,
                                                          role, user, ends_with):
        out = run(app, monkeypatch, role=role, user=user, door="panel")
        assert status_of(out) == "template", out
        back = out["seen"]["passed"]["back_url"]
        assert back.endswith(ends_with), (role, back)

    def test_the_panel_back_destination_is_the_role_the_session_names(self, app):
        """An unknown role still gets a page that answers — the teacher's own list."""
        from app.routes.seb import _back_url
        with app.test_request_context("/teacher/exams/ex-1/seb"):
            from flask import g
            g.user_role = "kurator"
            assert _back_url().endswith("/teacher/exams")


# ── every grant is recorded, and a refusal is not a release ─────────────────

class TestTheRecord:
    def test_a_granted_read_writes_a_row_and_an_audit_line(self, app, monkeypatch):
        out = run(app, monkeypatch, role="guru", user=OWNER, door="file")
        rows = out["db"].rows("seb_access_log")
        assert len(rows) == 1
        assert rows[0]["access_type"] == seb_access.ACCESS_DOWNLOAD_FILE
        assert rows[0]["role_at_access"] == seb_access.ROLE_OWNER
        assert rows[0]["user_id"] == OWNER
        assert rows[0]["school_id"] == SCHOOL

    def test_a_refusal_writes_nothing(self, app, monkeypatch):
        out = run(app, monkeypatch, role="guru", user=COLLEAGUE, door="password")
        assert out["status"] == 403
        assert out["db"].rows("seb_access_log") == []

    def test_the_record_is_written_before_the_password_leaves(self, app, monkeypatch):
        """A disclosure that happened cannot be one the log missed."""
        out = run(app, monkeypatch, role="admin_sekolah", user=ADMIN, door="password")
        assert out["response"].get_json()["ok"] is True
        assert len(out["db"].rows("seb_access_log")) == 1


# ── the pupil's other two doors, and why they are a pupil's ─────────────────

class TestTheHandshakeAndTheReportingDoors:
    """The handshake page and the report it sends are the pupil's, not the staff's.

    Both ask `exam_sitting_allowed` — the same participation rule the exam door
    applies — so a teacher cannot mint a claim, and a pupil of another class cannot
    file a report against a paper that is not theirs.
    """

    def test_the_handshake_is_served_to_a_pupil_of_the_paper(self, app, monkeypatch):
        out = run(app, monkeypatch, role="murid", user=PUPIL, door="claim")
        assert status_of(out) == "template", out
        assert out["seen"]["template"] == sebmod.JS_CLAIM_TEMPLATE

    def test_a_teacher_is_not_the_audience_of_the_handshake(self, app, monkeypatch):
        out = run(app, monkeypatch, role="guru", user=OWNER, door="claim")
        assert status_of(out) == 403, out

    def test_a_pupil_of_another_class_cannot_reach_the_handshake(self, app,
                                                                monkeypatch):
        out = run(app, monkeypatch, role="murid", user=OUTSIDER, door="claim")
        assert status_of(out) == 403, out

    def test_a_pupil_may_report_a_refusal_and_a_teacher_may_not(self, app,
                                                               monkeypatch):
        pupil = run(app, monkeypatch, role="murid", user=PUPIL, door="refusal")
        assert pupil["status"] == 200, pupil
        assert pupil["response"].get_json()["ok"] is True
        teacher = run(app, monkeypatch, role="guru", user=OWNER, door="refusal")
        assert status_of(teacher) == 403, teacher
        assert not teacher["db"].rows("seb_door_refusal"), (
            "a teacher's report left a refusal row against a pupil's paper")

    def test_an_ungated_paper_has_no_handshake_at_all(self, app, monkeypatch):
        out = run(app, monkeypatch, role="murid", user=PUPIL, door="claim",
                  require_seb=False)
        assert status_of(out) == 302, out

    def test_the_environment_door_belongs_to_the_pupil_sitting_the_paper(self, app,
                                                                        monkeypatch):
        teacher = run(app, monkeypatch, role="guru", user=OWNER, door="environment")
        assert teacher["status"] == 403
        assert teacher["response"].get_json()["reason"] == "not_a_pupil"


# ── the public doors, reachable by nobody in particular ─────────────────────

class TestThePublicDoors:
    @pytest.mark.parametrize("door", ["guide", "test_file", "verified"])
    def test_the_public_surface_needs_no_role_and_no_session(self, app, monkeypatch,
                                                             door):
        out = run(app, monkeypatch, role=None, user=None, school=None, door=door)
        assert status_of(out) in (200, "template"), (door, out)
