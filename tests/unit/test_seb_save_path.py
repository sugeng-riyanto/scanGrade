"""The save paths read the Config Key — and never refuse over it.

Two questions, and they are different questions
-----------------------------------------------
1. **Does a real client send the header on an XHR at all?** Measured on
   9 October 2026 against Windows SEB **3.10.2.920 (x64)**: the probe page's own
   `fetch()` to `/probe-xhr` arrived carrying
   `X-SafeExamBrowser-ConfigKeyHash` = SHA256(of that XHR's own URL + the key this
   server computed), judged per URL, `deploy/seb_phase11_probe.py --report` exit
   **0**. So yes — a save path has a header to read, and the raw capture is the
   evidence (`docs/features/SEB_PHASE11.md`, Hasil → pertanyaan B).
2. **May a save path act on it?** No, and this file is where that is enforced.
   SEB for macOS/iOS runs on WKWebView, which cannot attach the Config Key to *any*
   request, so a save or a submit that ended a sitting over a missing header would
   throw away a completed paper for every iPad pupil — with their answers already in
   the row. The refusal belongs at the page door, which hands such a client to the
   JavaScript handshake instead.

What is pinned here, and why each part has a wrong version that passes without it
-------------------------------------------------------------------------------
* **Both routes call the observer.** A check nobody calls is this whole feature
  failing quietly: the panel looks right, and no line anywhere says what arrived.
* **Neither calls the refusing function** (`seb_service.verify`). That is the rule
  the *test name* claims: the only SEB call a save path makes is the observer, so a
  future edit that tightens these paths into a second door fails here rather than
  mid-exam on an iPad.
* **The observer runs before the route's first decision**, by index. After it, "the
  header was absent" is a fact about a save that was going to be allowed anyway.
* **The exam read carries both SEB columns.** PostgREST reports a column left out
  of a select as *absent*, never as an error — so a forgotten `require_seb` makes
  `gated()` answer `False` and the observation disappears with nothing in any log.
* **The routes are *run*, not only read.** "A gated paper is not interrupted when
  the header is absent" is a statement about a save landing and a paper being handed
  in, so both bodies are driven against a stand-in that really stores, for all three
  header states (absent, another paper's key, the right one). The last of those is
  the control: three refusals would also satisfy a door that turns everyone away.
"""
from __future__ import annotations

import ast
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.routes import api as apimod
from app.routes import student as studentmod
from app.services import seb_config_key as ck
from app.services import seb_service

ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "app" / "routes" / "api.py"
STUDENT = ROOT / "app" / "routes" / "student.py"

EXAM_ID = "exam-1"
PUPIL = "stu-1"
SCHOOL = "school-A"
CLASS = "class-7A"
KEY = "a" * 64

SYNC_BASE = "https://scangrade.web.id/api/student/sync-draft"
SUBMIT_BASE = f"https://scangrade.web.id/student/exams/{EXAM_ID}/submit"


def _source(path: Path) -> str:
    # `utf-8-sig`: `app/routes/api.py` opens with a BOM, and `ast.parse` refuses it
    # as a non-printable character — the file is read the way Python reads it.
    return path.read_text(encoding="utf-8-sig")


def _function_body(path: Path, name: str) -> str:
    """The source of one function, by parsing — never by searching for its name.

    A whole-file search for `observe_save_key(` is satisfied by the import line and
    by any comment, which is how a check for a call passes with nothing calling it.
    """
    tree = ast.parse(_source(path))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(_source(path), node) or ""
    raise AssertionError(f"no function named {name} in {path}")


def _seb_calls(body: str) -> set[str]:
    """Which `seb_service.<name>(` calls appear in a body — the inventory, not a grep."""
    return set(re.findall(r"seb_service\.(\w+)\(", body))


# ── 1. the observation exists on both save paths ─────────────────────────────

def test_the_draft_sync_path_observes_the_key():
    body = _function_body(API, "student_sync_draft")
    assert "observe_save_key(" in body, (
        "the draft sync never looks at the Config Key, so a sitting moved into an "
        "ordinary browser leaves no trace anywhere")


def test_the_submit_path_observes_the_key():
    body = _function_body(STUDENT, "submit_exam")
    assert "observe_save_key(" in body, (
        "handing the paper in is the one moment worth recording, and it does not")


def test_neither_save_path_can_refuse_over_the_header():
    """The whole point, as a rule: only the observer may be called from here.

    `seb_service.verify` is the refusing function, and it is deliberately reachable
    from exactly one place — the page door. A save path that called it would end a
    paper mid-sitting for a client whose platform cannot send the header at all.
    """
    for path, name in ((API, "student_sync_draft"), (STUDENT, "submit_exam")):
        calls = _seb_calls(_function_body(path, name))
        assert calls <= {"observe_save_key"}, (
            f"{name} calls {sorted(calls - {'observe_save_key'})} on a path that "
            "saves answers — refuse at the door, never here")


def test_the_observation_runs_before_the_route_decides_anything():
    """Before the first decision, by index, on both paths.

    After the decision, "the header was absent" becomes a fact about a request that
    was going to be allowed anyway, and the measurement quietly stops meaning what
    its name says.
    """
    sync = _function_body(API, "student_sync_draft")
    assert sync.index("observe_save_key(") < sync.index("exam_sitting_allowed("), (
        "the sync observes after deciding whether the pupil may sit")

    submit = _function_body(STUDENT, "submit_exam")
    assert submit.index("observe_save_key(") < submit.index(
        'jsonify({"error": "Exam is not available for submission"})'), (
        "the submit observes after the availability decision")


def _observer_call(path: Path, name: str) -> str:
    """The `observe_save_key(...)` call itself, by AST — arguments included."""
    tree = ast.parse(_source(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            for call in ast.walk(node):
                if (isinstance(call, ast.Call)
                        and getattr(call.func, "attr", "") == "observe_save_key"):
                    return ast.unparse(call)
    raise AssertionError(f"{name} in {path} never calls observe_save_key")


def test_the_url_handed_to_the_observer_is_the_requests_own():
    """Not the exam's page URL: a client hashes the address it is fetching.

    Measured, not assumed — see the file docstring. The route that passed
    `seb_service.start_url(exam_id)` instead would record `mismatch` for every
    honest client, which is worse than recording nothing: the log would say the
    client was not SEB while it was.
    """
    for path, name in ((API, "student_sync_draft"), (STUDENT, "submit_exam")):
        call = _observer_call(path, name)
        assert "request.url" in call, call
        assert "start_url" not in call, (
            f"{name} hashes the exam's page URL, not the request's own address")


def test_the_two_routes_read_the_columns_the_observation_needs():
    """`require_seb` and `seb_config_key` must be in the select they make.

    PostgREST reports a column left out of a select as *missing*, never as an error,
    so a dropped `require_seb` makes `gated()` answer `False` and the observation
    vanishes — with no failure anywhere to say so.
    """
    for path, name in ((API, "student_sync_draft"), (STUDENT, "submit_exam")):
        body = _function_body(path, name)
        select = re.search(r"select\((.*?)\)\s*\.", body, re.S)
        assert select, f"{name} has no exam read to check"
        assert "require_seb" in select.group(1) and "seb_config_key" in select.group(1), (
            f"{name} reads the exam without both SEB columns, so the observation "
            "silently sees nothing")


# ── 2. what the observation says ─────────────────────────────────────────────

def _gated(**over) -> dict:
    row = {"require_seb": True, "seb_config_key": KEY}
    row.update(over)
    return row


def test_an_ungated_paper_is_not_observed_at_all():
    """Most papers are not gated; nothing here may cost them anything or fill a log."""
    assert seb_service.key_state({"require_seb": False}, SYNC_BASE, None) is None
    assert seb_service.key_state({"require_seb": False}, SYNC_BASE, "b" * 64) is None


def test_a_gated_paper_with_no_header_reports_absent():
    assert seb_service.key_state(_gated(), SYNC_BASE, None) == seb_service.KEY_ABSENT
    assert seb_service.key_state(_gated(), SYNC_BASE, "") == seb_service.KEY_ABSENT


def test_a_gated_paper_with_another_papers_key_reports_mismatch():
    assert seb_service.key_state(
        _gated(), SYNC_BASE, ck.request_hash(SYNC_BASE, "b" * 64)) == seb_service.KEY_MISMATCH


def test_a_gated_paper_with_its_own_key_reports_match():
    assert seb_service.key_state(
        _gated(), SYNC_BASE, ck.request_hash(SYNC_BASE, KEY)) == seb_service.KEY_MATCH


def test_the_url_judged_is_the_clients_own_address_not_the_exams():
    """A client hashes the address it is fetching; a save is not the exam page.

    Measured, not assumed: in the probe run the favicon request carried the hash of
    the favicon and the XHR the hash of the XHR. A save path that hashed
    `start_url` would report `mismatch` for every honest client — which is the
    finding this assertion keeps out of the log.
    """
    own = ck.request_hash(SYNC_BASE, KEY)
    exam_page = ck.request_hash(f"https://scangrade.web.id/student/exams/{EXAM_ID}", KEY)
    assert seb_service.key_state(_gated(), SYNC_BASE, own) == seb_service.KEY_MATCH
    assert seb_service.key_state(_gated(), SYNC_BASE, exam_page) == seb_service.KEY_MISMATCH


def test_a_gated_paper_whose_key_was_never_issued_is_reported_not_judged():
    """`require_seb` on with no key is a half-written row.

    The door fails closed on it, and here it can only be *reported*: a save path has
    no door to close, so the state says the two sides disagree rather than pretending
    the client matched.
    """
    state = seb_service.key_state(_gated(seb_config_key=None), SYNC_BASE, "c" * 64)
    assert state == seb_service.KEY_MISMATCH


def test_the_observation_returns_the_state_and_writes_one_line(caplog):
    # At the root's level, not at a named logger's: the line is written through
    # `app.utils.logger.get_logger`, whose name is not the module's own.
    with caplog.at_level("INFO"):
        state = seb_service.observe_save_key(
            _gated(), EXAM_ID, SYNC_BASE, None, "sync-draft")
    assert state == seb_service.KEY_ABSENT
    assert any("SEB header on XHR: absent (sync-draft, exam exam-1)" in r.getMessage()
               for r in caplog.records), [r.getMessage() for r in caplog.records]


def test_an_ungated_paper_writes_no_line(caplog):
    with caplog.at_level("INFO"):
        assert seb_service.observe_save_key(
            {"require_seb": False}, EXAM_ID, SYNC_BASE, "d" * 64, "submit") is None
    assert not [r for r in caplog.records if "SEB header on XHR" in r.getMessage()]


# ── 3. the save paths, driven: a gated paper is never interrupted ────────────
#
# The rule above is a reading of the code. What a school notices is the *response*:
# a sync that stored the answers and a paper that was handed in. So both bodies run
# against a stand-in PostgREST that really stores, the same shape
# `test_sync_authority.py` and `test_seb_door.py` use.

class _Result:
    def __init__(self, data):
        self.data = data


class _Table:
    """Enough PostgREST for these two routes: filters, single, insert, update."""

    def __init__(self, rows):
        self.rows = rows
        self._filters, self._one, self._pending, self._limit = [], False, None, None

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

    def order(self, *a, **k):
        return self

    def limit(self, count=None, *a, **k):
        self._limit = count
        return self

    def single(self):
        self._one = True
        return self

    def maybe_single(self):
        # postgrest answers a miss with a row-shaped `None`, never an empty list:
        # `row_or_none()` and every `.get()` above it depend on that shape.
        self._one = True
        return self

    def insert(self, payload):
        self._pending = ("insert", dict(payload))
        return self

    def update(self, payload):
        self._pending = ("update", dict(payload))
        return self

    def _match(self):
        return [row for row in self.rows
                if all(row.get(c) == v for c, v in self._filters)]

    def execute(self):
        if self._pending:
            kind, payload = self._pending
            self._pending = None
            if kind == "insert":
                row = dict(payload)
                row.setdefault("id", f"row-{len(self.rows) + 1}")
                self.rows.append(row)
                self._filters = []
                return _Result([row])
            rows = self._match()
            self._filters = []
            for row in rows:
                row.update(payload)
            return _Result(rows)
        rows = self._match()
        self._filters = []
        if self._limit:
            rows = rows[: self._limit]
        return _Result(rows[0] if (self._one and rows) else (rows if not self._one else None))


class _FakeSupabase:
    def __init__(self, exam, submissions=None):
        self.store = {
            "exams": [exam],
            "profiles": [{"id": PUPIL, "full_name": "Ahmad", "class_id": CLASS,
                          "school_id": SCHOOL, "role": "murid"}],
            "classes": [{"id": CLASS, "name": "7A", "grade_level": "7", "school_id": SCHOOL}],
            "submissions": list(submissions or []),
        }
        self.calls = []

    def table(self, name):
        self.calls.append(name)
        return _Table(self.store.setdefault(name, []))


def _exam(**over) -> dict:
    row = {
        "id": EXAM_ID, "title": "Latihan Ujian", "subject": "Fisika",
        "school_id": SCHOOL, "class_ids": [CLASS], "teacher_id": "guru-1",
        "is_published": True, "status": "active", "publish_mode": "manual",
        "duration_minutes": 60, "total_questions": 1,
        "question_types": {}, "answer_key": {"0": "A"}, "question_weights": {},
        "question_scoring": None, "question_pages": {},
        "start_at": None, "end_at": None, "auto_submit_on_window_end": False,
        "max_attempts": 1,
        # The two columns this feature owns, at the values migration 062 defaults
        # them to — so the ungated control below is the *stored* case, not a
        # fixture that left the flag out.
        "require_seb": False, "seb_config_key": None,
    }
    row.update(over)
    return row


def _sitting(**over) -> dict:
    """A sitting open right now, so the deadline is not what these tests measure.

    `started_at` five minutes ago and `updated_at` an hour ago: inside the duration,
    and past the fair-use interval the sync's own guard reads (`_check_rate_limit`
    fails open on an old stamp, and refuses a stamp from seconds ago).
    """
    now = datetime.now(timezone.utc)
    row = {
        "id": "sub-1", "exam_id": EXAM_ID, "student_id": PUPIL, "status": "draft",
        "answers": {},
        "started_at": (now - timedelta(minutes=5)).isoformat(),
        "updated_at": (now - timedelta(hours=1)).isoformat(),
    }
    row.update(over)
    return row


def _gated_exam(**over) -> dict:
    return _exam(require_seb=True, seb_config_key=KEY, **over)


def _run_sync(app, db, headers, base_url=SYNC_BASE):
    from flask import g

    app.extensions["supabase"] = db
    body = {"exam_id": EXAM_ID, "answers": {"0": "A"}, "light": True}
    with app.test_request_context(base_url.rstrip("/") + "/", method="POST",
                                 json=body, headers=headers):
        g.user_id, g.user_role = PUPIL, "murid"
        g.user_school_id, g.user_class_id = SCHOOL, CLASS
        out = apimod.student_sync_draft.__wrapped__()
    resp = out[0] if isinstance(out, tuple) else out
    status = out[1] if isinstance(out, tuple) else resp.status_code
    return resp.get_json(), status


def _run_submit(app, db, headers, base_url=SUBMIT_BASE, monkeypatch=None):
    from flask import g

    app.extensions["supabase"] = db
    # The two services that would reach past the fake and say nothing about the
    # header: the audit trail, and the subscription gate (which asks Midtrans).
    if monkeypatch is not None:
        monkeypatch.setattr(studentmod, "log_activity", lambda *a, **k: None)
        import app.utils.auth as auth_mod
        monkeypatch.setattr(auth_mod, "check_subscription_write",
                            lambda *a, **k: (True, None))
    with app.test_request_context(base_url, method="POST",
                                 json={"answers": {"0": "A"}}, headers=headers):
        g.user_id, g.user_role = PUPIL, "murid"
        g.user_school_id, g.user_class_id = SCHOOL, CLASS
        out = studentmod.submit_exam.__wrapped__(EXAM_ID)
    resp, status = (out[0], out[1]) if isinstance(out, tuple) else (out, out.status_code)
    return resp.get_json(), status


def _no_header():
    return None


def _wrong_header(base_url):
    return {ck.CONFIG_KEY_HEADER: ck.request_hash(base_url, "b" * 64)}


def _right_header(base_url):
    return {ck.CONFIG_KEY_HEADER: ck.request_hash(base_url, KEY)}


class TestAGatedPaperIsNotInterruptedByASave:
    """The sync path, on all three header states. Each one stores the answers."""

    def _sync(self, app, header, exam=None):
        db = _FakeSupabase(exam or _gated_exam(), [_sitting()])
        body, status = _run_sync(app, db, header)
        return db, body, status

    def test_a_sync_with_no_header_is_still_stored(self, app):
        db, body, status = self._sync(app, _no_header())
        assert status == 200, f"a gated paper's save was refused: {body}"
        assert body.get("saved") is True and not body.get("denied")
        assert db.store["submissions"][0]["answers"].get("0") == "A", (
            "the answers were not stored, so the sitting is interrupted after all")

    def test_a_sync_with_another_papers_key_is_still_stored(self, app):
        db, body, status = self._sync(app, _wrong_header(SYNC_BASE))
        assert status == 200 and body.get("saved") is True, body
        assert db.store["submissions"][0]["answers"].get("0") == "A"

    def test_a_sync_with_the_right_key_is_still_stored(self, app):
        """The control: the two above must not pass because the fake is inert."""
        db, body, status = self._sync(app, _right_header(SYNC_BASE))
        assert status == 200 and body.get("saved") is True, body
        assert db.store["submissions"][0]["answers"].get("0") == "A"

    def test_an_ungated_paper_is_not_observed_at_all(self, app, caplog):
        """The regression every other test here leans on: nothing gated, nothing said."""
        with caplog.at_level("INFO"):
            db, body, status = self._sync(app, _no_header(), exam=_exam())
        assert status == 200 and body.get("saved") is True
        assert not [r for r in caplog.records if "SEB header on XHR" in r.getMessage()]

    def test_the_save_records_what_it_saw(self, app, caplog):
        """`absent` on a gated paper is the measurement the whole phase asked for."""
        with caplog.at_level("INFO"):
            _run_sync(app, _FakeSupabase(_gated_exam(), [_sitting()]), _no_header())
        assert any("SEB header on XHR: absent (sync-draft, exam exam-1)" in r.getMessage()
                   for r in caplog.records), [r.getMessage() for r in caplog.records]


class TestAGatedPaperIsNotInterruptedByASubmit:
    """The submit path, the same three states. Every one of them hands the paper in."""

    def _submit(self, app, header, monkeypatch, exam=None):
        db = _FakeSupabase(exam or _gated_exam(), [_sitting()])
        body, status = _run_submit(app, db, header, monkeypatch=monkeypatch)
        return db, body, status

    def test_a_submit_with_no_header_still_hands_the_paper_in(self, app, monkeypatch):
        db, body, status = self._submit(app, _no_header(), monkeypatch)
        assert status == 200, f"a gated paper's submit was refused: {body}"
        assert body.get("success") is True, body
        assert db.store["submissions"][0]["status"] == "submitted", (
            "the paper was not handed in, so the sitting is interrupted after all")

    def test_a_submit_with_another_papers_key_still_hands_the_paper_in(self, app, monkeypatch):
        db, body, status = self._submit(app, _wrong_header(SUBMIT_BASE), monkeypatch)
        assert status == 200 and body.get("success") is True, body
        assert db.store["submissions"][0]["status"] == "submitted"

    def test_a_submit_with_the_right_key_still_hands_the_paper_in(self, app, monkeypatch):
        """The control: this is what an honest client's request looks like."""
        db, body, status = self._submit(app, _right_header(SUBMIT_BASE), monkeypatch)
        assert status == 200 and body.get("success") is True, body
        assert db.store["submissions"][0]["status"] == "submitted"

    def test_an_ungated_paper_is_admitted_with_a_stale_header_too(self, app, monkeypatch):
        """A header from another paper must not cost a pupil a paper nobody gated."""
        db, body, status = self._submit(app, _wrong_header(SUBMIT_BASE), monkeypatch,
                                        exam=_exam())
        assert status == 200 and body.get("success") is True, body
        assert db.store["submissions"][0]["status"] == "submitted"


def test_the_fake_reports_a_missing_row_the_way_postgrest_does():
    """The harness's own contract, so a green suite cannot be an inert fake.

    `maybe_single()` answers a miss with `None` — which is what every
    `row_or_none()` in these routes is written against — while a `single()` on a
    missing row is the shape the routes treat as an error.
    """
    table = _Table([])
    assert table.select("*").eq("id", "nope").maybe_single().execute().data is None
    assert _Table([{"id": "x"}]).select("*").eq("id", "x").limit(1).execute().data == [{"id": "x"}]
