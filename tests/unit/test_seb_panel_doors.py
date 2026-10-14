"""The doors into the SEB panel — the pages that lead there, and the way back.

Why this file exists
--------------------
`test_seb_rbac_matrix.py` drives every SEB door as every role and proves the
*permission*: `decide` grants the admin sekolah, the head of school, their deputy and
the invigilator on duty, and the panel renders the reveal button and the file link for
each of them. What none of that can see is whether a human could **get** there. Until
this change the panel was reachable only by typing its URL for four roles out of five,
because the one link in the app sat on the owner's exam form — the permission was real
and had no door. This file is the other half of the earlier rule: a granted permission
with no door is a defect, and "the link exists in a template" is not the same claim as
"the link is on a page this role opens".

Two doors, and one of them is time-dependent
--------------------------------------------
``principal/invigilation.html`` — read by the school admin, the head of school and the
deputy — is the page those three roles already open *during* a sitting; it is where
the pupils' recovery codes are, which is precisely the moment somebody has to read an
exit password to a locked pupil. Every gated sitting on it links to the panel.

``teacher/invigilation.html`` is the supervisor's own duty list, and it is the harder
one, because an invigilator's access is a **window**: a link offered for tomorrow's
duty is a link to a 403, and a page that offers it is two implementations of one rule.
The row therefore carries ``seb_open``, computed by ``seb_access.in_duty_window`` —
the same function the decision applies — and a gated duty outside its window says so
in words instead of pointing at a refusal.

The window is asked of **every** sitting, not the first
-------------------------------------------------------
A paper sat twice in a day — two classes, two shifts — names the same supervisor
twice. When only the earliest sitting was asked, the teacher standing in the second
shift's room was refused a password the moment the first shift ended, and the page
(which knows about both rows) would have offered the link the door refused. Both the
door and the row now go through :func:`app.services.seb_access.covering_sitting`.

Rendered, not grepped
---------------------
Both pages are rendered through the app's own Jinja environment, with rows shaped the
way the service shapes them. A grep for ``seb.panel`` passes on a template whose
``{% if %}`` never fires, which is the exact defect being guarded.
"""
from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from app.services import invigilation, seb_access

ROOT = Path(__file__).resolve().parents[2]

SCHOOL = "sc-1"
EXAM_ID = "ex-1"
CLASS = "c-1"
OWNER = "t-owner"
INVIGILATOR = "t-invigilator"

NOW = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)

EXAM = {
    "id": EXAM_ID, "school_id": SCHOOL, "teacher_id": OWNER, "title": "Matematika",
    "require_seb": True, "seb_config_key": "k" * 8, "duration_minutes": 60,
    "end_at": None, "auto_submit_on_window_end": False,
}


# ── a postgrest stand-in: filters, and nothing else ─────────────────────────

class _Query:
    """Enough of postgrest for the reads these pages make — including its traps.

    `select` is honoured, not ignored: postgrest returns **only** the columns asked
    for, so a column dropped from a select arrives *absent* rather than as an error.
    That is the failure the codebase names twice in its own comments — "absent means
    blocked" for the SEB file's switches, and the 42703 that a select naming a
    column the schema does not have produced on the live box — and a fake that
    ignored the select could not see either. Flat lists only: the one nested select
    here (`profiles!inner(full_name)`) is not reached by the rows these tests seed.
    """

    def __init__(self, store, name):
        self._store, self._name, self._filters = store, name, []
        self._columns = None

    def select(self, *columns, **kwargs):
        spec = ",".join(str(c) for c in columns)
        if "(" not in spec:
            self._columns = [c.strip() for c in spec.split(",") if c.strip()]
        return self

    def eq(self, column, value):
        self._filters.append((column, value))
        return self

    def in_(self, column, values):
        self._filters.append((column, list(values)))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        rows = list(self._store.get(self._name, []))
        for column, value in self._filters:
            if isinstance(value, list):
                rows = [r for r in rows if r.get(column) in value]
            else:
                rows = [r for r in rows if r.get(column) == value]
        if self._columns is not None:
            rows = [{c: r[c] for c in self._columns if c in r} for r in rows]
        return SimpleNamespace(data=[dict(r) for r in rows])


class _Db:
    """One school, one gated paper, one supervisor — and, when asked, a second shift."""

    def __init__(self, *, gated=True, sittings):
        self.tables = {
            "exams": [dict(EXAM, require_seb=gated)],
            "classes": [{"id": CLASS, "name": "7A", "school_id": SCHOOL}],
            "teachers": [],
            "invigilation_schedules": [
                {"id": f"sch-{n}", "school_id": SCHOOL, "exam_id": EXAM_ID,
                 "class_id": CLASS, "scheduled_at": at.isoformat(), "room": "R1",
                 "notes": None}
                for n, at in enumerate(sittings, start=1)],
            "invigilator_assignments": [
                {"id": f"ia-{n}", "school_id": SCHOOL, "schedule_id": f"sch-{n}",
                 "teacher_id": INVIGILATOR, "is_lead": False}
                for n in range(1, len(sittings) + 1)],
        }

    def table(self, name):
        return _Query(self.tables, name)


def _tasks(db, now=NOW):
    return invigilation.tasks_for_teacher(db, SCHOOL, INVIGILATOR, now=now)


def _schedules(db):
    return invigilation.list_schedules(db, SCHOOL)


# ── 1. the row carries the two facts the link needs ─────────────────────────

def test_a_duty_inside_its_window_is_open_and_named_as_gated():
    """The pair a supervisor's own row has to carry: gated, and open *now*."""
    tasks = _tasks(_Db(sittings=[NOW - timedelta(minutes=5)]))
    assert len(tasks) == 1, tasks
    assert tasks[0]["require_seb"] is True
    assert tasks[0]["seb_open"] is True


def test_a_duty_before_its_window_is_gated_but_not_open():
    """The sentence the page shows instead of a link comes from this one flag."""
    tasks = _tasks(_Db(sittings=[NOW + timedelta(hours=3)]))
    assert tasks[0]["require_seb"] is True
    assert tasks[0]["seb_open"] is False


def test_an_ungated_paper_is_never_open():
    """A paper nobody gated has no credential, so the panel refuses it — no link."""
    tasks = _tasks(_Db(gated=False, sittings=[NOW]))
    assert tasks[0]["require_seb"] is False
    assert tasks[0]["seb_open"] is False


def test_the_window_a_row_reports_is_the_doors_own_function():
    """Not a second opinion about the window: the value equals the door's answer.

    The row is asked about a window that is open and one that is not, and both
    answers are compared against `seb_access.in_duty_window` called the way the SEB
    decision calls it — the exam row, the sitting's own timestamp, the same clock.
    """
    for sitting, expected in ((NOW - timedelta(minutes=5), True),
                              (NOW + timedelta(hours=3), False)):
        db = _Db(sittings=[sitting])
        exam = db.tables["exams"][0]
        assert seb_access.in_duty_window(exam, sitting, NOW) is expected
        assert _tasks(db)[0]["seb_open"] is expected, sitting


def test_the_invigilation_service_owns_no_window_arithmetic_of_its_own():
    """A copy of the lead/tail arithmetic would be a second window to keep in step."""
    source = (ROOT / "app" / "services" / "invigilation.py").read_text(encoding="utf-8")
    assert "seb_access.in_duty_window(" in source
    assert "timedelta" not in source, (
        "the invigilation service is doing window arithmetic of its own")
    assert "DUTY_TAIL_MINUTES" not in source


def test_the_staff_schedule_carries_no_duty_window_to_be_inside():
    """The admin, the head of school and the deputy have no duty, so no flag.

    They may read a gated paper's password at any time, which is why their page needs
    `require_seb` alone — and why a `seb_open` there would be a fact about nobody.
    """
    rows = _schedules(_Db(sittings=[NOW - timedelta(minutes=5)]))
    assert rows[0]["require_seb"] is True
    assert "seb_open" not in rows[0], rows[0]


# ── 2. the second sitting of one paper ──────────────────────────────────────

def test_a_supervisor_on_the_second_shift_is_on_duty_there():
    """The defect this change removes: only the earliest sitting used to be asked.

    A 60-minute paper at 08:00 and at 13:30 names the same supervisor twice. At 13:30
    the first shift ended hours ago; asking it alone refused the teacher who was
    standing in the second shift's room.
    """
    first, second = (datetime(2026, 10, 14, 1, 0, tzinfo=timezone.utc),
                     datetime(2026, 10, 14, 6, 30, tzinfo=timezone.utc))
    db = _Db(sittings=[first, second])
    exam = db.tables["exams"][0]

    assert seb_access.covering_sitting(db, SCHOOL, INVIGILATOR, exam, second) == \
        second.isoformat(), "the covering sitting is not the one that covers now"
    assert seb_access.in_duty_window(exam, first, second) is False, (
        "the fixture is not measuring what it claims: the first shift is still open")


def test_the_row_the_supervisor_is_standing_in_is_the_open_one():
    """Page-side: the link lands on the shift that is happening, not the one that ran."""
    first, second = (datetime(2026, 10, 14, 1, 0, tzinfo=timezone.utc),
                     datetime(2026, 10, 14, 6, 30, tzinfo=timezone.utc))
    tasks = _tasks(_Db(sittings=[first, second]), now=second)
    opened = [t for t in tasks if t["seb_open"]]
    assert len(opened) == 1, tasks
    assert opened[0]["scheduled_at"] == second.isoformat()


def test_a_supervisor_between_the_two_shifts_is_outside_both():
    """Between shifts is outside the window, and that is not a defect."""
    first, second = (datetime(2026, 10, 14, 1, 0, tzinfo=timezone.utc),
                     datetime(2026, 10, 14, 6, 30, tzinfo=timezone.utc))
    between = datetime(2026, 10, 14, 4, 0, tzinfo=timezone.utc)
    tasks = _tasks(_Db(sittings=[first, second]), now=between)
    assert [t["seb_open"] for t in tasks] == [False, False], tasks


# ── 3. the two pages, rendered ──────────────────────────────────────────────

@contextlib.contextmanager
def _signed_in(app, path, role="guru"):
    """A request context carrying everything base.html reads."""
    from flask import g
    with app.test_request_context(path):
        g.user_id = INVIGILATOR
        g.user_name = "Pengawas Uji"
        g.user_email = "pengawas@example.test"
        g.user_role = role
        g.user_school_id = SCHOOL
        g.tz_offset = 7
        g.show = {}
        yield


def _task_row(*, gated=True, open_now=True, exam_id=EXAM_ID):
    return {"id": "sch-1", "school_id": SCHOOL, "exam_id": exam_id,
            "class_id": CLASS, "scheduled_at": NOW.isoformat(), "room": "R1",
            "notes": None, "exam_title": "Matematika", "class_name": "7A",
            "require_seb": gated, "seb_open": gated and open_now,
            "invigilators": []}


def _render_teacher_duties(app, rows):
    with _signed_in(app, "/teacher/invigilation"):
        return app.jinja_env.get_template("teacher/invigilation.html").render(
            tasks=rows, matrix_duties=[], requests=[], codes={}, timelines={})


def _render_staff_schedule(app, rows, *, role="admin_sekolah"):
    with _signed_in(app, f"/{role}/invigilation", role=role):
        return app.jinja_env.get_template("principal/invigilation.html").render(
            role=role, base=f"/{role}", invigilation_base=f"/{role}",
            retake_decide_base=f"/{role}", can_write=False, school=None,
            schedules=rows, requests=[], options={"teachers": []}, codes={},
            timelines={})


def test_the_supervisors_duty_list_links_to_the_panel_while_the_window_is_open(app):
    html = _render_teacher_duties(app, [_task_row()])
    assert f'href="/teacher/exams/{EXAM_ID}/seb"' in html, (
        "a supervisor on duty has the permission and no door to it")
    assert "Kata sandi SEB" in html


def test_the_supervisors_duty_list_says_why_when_the_window_is_closed(app):
    """A gated duty outside its window: a sentence, not a link to a 403."""
    html = _render_teacher_duties(app, [_task_row(open_now=False)])
    assert f'href="/teacher/exams/{EXAM_ID}/seb"' not in html
    assert "jendela tugas ini" in html, (
        "a gated duty outside its window rendered neither a link nor a reason")


def test_the_supervisors_duty_list_offers_nothing_for_an_ungated_paper(app):
    html = _render_teacher_duties(app, [_task_row(gated=False, open_now=True)])
    assert f'href="/teacher/exams/{EXAM_ID}/seb"' not in html
    assert "jendela tugas ini" not in html, (
        "an ungated paper was explained as though a window had closed on it")


def test_the_staff_schedule_links_to_the_panel_for_a_gated_sitting(app):
    for role in ("admin_sekolah", "principal", "vice_principal"):
        html = _render_staff_schedule(app, [_task_row(gated=True)], role=role)
        assert f'href="/teacher/exams/{EXAM_ID}/seb"' in html, role


def test_the_staff_schedule_never_links_to_a_paper_nobody_gated(app):
    """The panel refuses an ungated paper; a link to it is a link that cannot work."""
    html = _render_staff_schedule(app, [_task_row(gated=False)])
    assert f'href="/teacher/exams/{EXAM_ID}/seb"' not in html


def test_the_link_names_the_exam_of_its_own_row(app):
    """Two sittings of two papers: each link points at its own, not at the first."""
    rows = [_task_row(exam_id="exam-a"), _task_row(exam_id="exam-b")]
    html = _render_teacher_duties(app, rows)
    assert 'href="/teacher/exams/exam-a/seb"' in html
    assert 'href="/teacher/exams/exam-b/seb"' in html


# ── 4. the panel's way back ─────────────────────────────────────────────────

PANEL_BACK = {
    "guru": "/teacher/exams",
    "admin_sekolah": "/admin-sekolah/invigilation",
    "principal": "/principal/invigilation",
    "vice_principal": "/vice-principal/invigilation",
}


def _render_panel(app, back_url, *, role="guru"):
    with _signed_in(app, f"/teacher/exams/{EXAM_ID}/seb", role=role):
        return app.jinja_env.get_template("teacher/seb_panel.html").render(
            exam=EXAM, verdict={"ok": True, "role": "owner_teacher"}, credential=None,
            has_credential=False, file_is_current=False, can_manage=True,
            back_url=back_url, confirm_field="confirm_no_android",
            mobile_risk={"android": 0, "known": 0}, access_log=[], refusals=[],
            refusal_tally={"refused_pupils": 0, "refused_attempts": 0,
                           "refused_last_at": None})


def test_the_panel_back_button_renders_the_destination_it_was_handed(app):
    for url in PANEL_BACK.values():
        assert f'href="{url}"' in _render_panel(app, url)


def test_every_role_goes_back_to_a_page_that_will_answer_it(app):
    """Each endpoint the route's table names resolves — and to the page it claims.

    `teacher.my_exams` is a *teacher-or-admin* door, so the head of school and their
    deputy sent there get a 403: the same dead end the doors above were added to
    remove, one click further in. This drives the app's own URL map, so a renamed
    endpoint fails here rather than at render time on the box.
    """
    from app.routes.seb import PANEL_BACK_ENDPOINTS
    with app.test_request_context("/teacher/exams/ex-1/seb"):
        from flask import url_for
        for role, endpoint in PANEL_BACK_ENDPOINTS.items():
            assert url_for(endpoint) == PANEL_BACK[role], role
        assert url_for("teacher.my_exams") == PANEL_BACK["guru"]


def test_the_back_table_covers_exactly_the_roles_the_panel_serves(app):
    """A staff role missing from the table is a role sent to the teacher's own door."""
    from app.routes.seb import PANEL_BACK_ENDPOINTS
    from app.services import seb_access
    assert set(PANEL_BACK_ENDPOINTS) == set(seb_access.SCHOOL_ROLES), (
        "the panel's Back table and the roles the matrix grants must not drift")


def test_the_back_url_is_chosen_from_the_session_role(app):
    with _signed_in(app, f"/teacher/exams/{EXAM_ID}/seb", role="principal"):
        from app.routes.seb import _back_url
        assert _back_url() == PANEL_BACK["principal"]
    with _signed_in(app, f"/teacher/exams/{EXAM_ID}/seb", role="guru"):
        from app.routes.seb import _back_url
        assert _back_url() == PANEL_BACK["guru"]
