"""The door: a gated paper is opened *through* `seb_service.verify`, at the route.

Why this file exists separately from `test_seb_service.py`
----------------------------------------------------------
That file proves the *decision* (`verify`) answers correctly. It cannot prove the
app ever asks. A correct predicate nobody calls is the shape of this whole feature
failing silently: `require_seb` reads ON on the builder, the `.seb` file downloads,
the panel looks right — and every pupil opens the paper in Chrome by pasting the
link. So the wiring is asserted here, and the two assertions that matter are about
*where* the call sits and *which URL* it hashes.

Three properties, each of which has a plausible wrong version
-------------------------------------------------------------
1. **The gate is inside `take_exam`, after the participant check.** Before
   `exam_sitting_allowed` would mean a stranger's refusal message is decided by SEB
   configuration, and a pupil not on the paper is told to install software for an
   exam that is not theirs.
2. **The URL hashed comes from `seb_service.start_url`, not `request.url`.** The
   config's `startURL` and the door's hash must be the same string; `request.url`
   carries whatever query string the pupil arrived with, so hashing it would refuse
   an honest client whose link was forwarded with `?src=wa` on it.
3. **There is exactly one definition of that URL.** If `app/routes/seb.py` grew a
   second copy of `url_root + url_for('student.take_exam')`, the file and the door
   would agree today and drift on the next edit — so the route delegates, and no
   module builds the string twice.
4. **The refusal is a *behaviour*, so it is also run.** Sections 1-3 read the code;
none of them can say what a pupil gets back. Section 4 drives the real route body
against a postgrest stand-in and asserts the two answers a school would notice
first: an ungated paper opens in whatever browser the pupil has, and a refused
client leaves **no sitting behind** (a refusal that quietly spent an attempt would
be worse than no gate at all).
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from types import SimpleNamespace

from app.routes import student as studentmod
from app.services import seb_config_key as ck
from app.services import seb_service

ROOT = Path(__file__).resolve().parents[2]
STUDENT = ROOT / "app" / "routes" / "student.py"
SEB_ROUTE = ROOT / "app" / "routes" / "seb.py"

EXAM_ID = "exam-1"
PUPIL = "stu-1"
SCHOOL = "school-A"
CLASS = "class-7A"
KEY = "a" * 64


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _function_body(path: Path, name: str) -> str:
    """The source of one function, by parsing — never by searching for its name.

    A whole-file search for `seb_service.verify(` is satisfied by the import line
    and by any comment, which is how a check for a call passes with nothing calling
    it. The body is what has to contain the call.
    """
    tree = ast.parse(_source(path))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(_source(path), node) or ""
    raise AssertionError(f"no function named {name} in {path}")


# ── 1. the gate is in the door, and in the right place ───────────────────────

def test_the_exam_door_asks_the_seb_decision():
    body = _function_body(STUDENT, "take_exam")
    assert "seb_service.gated(exam)" in body, "the door does not check `require_seb`"
    assert "seb_service.verify(" in body, "the door never asks the SEB decision"


def test_the_gate_sits_after_the_participant_check_and_before_the_render():
    """Order, by index — a stranger must not be refused *because* of SEB."""
    body = _function_body(STUDENT, "take_exam")
    allowed = body.index("exam_sitting_allowed(")
    gate = body.index("seb_service.gated(exam)")
    render = body.index("render_template(")
    assert allowed < gate < render, (
        "the SEB gate must run after `exam_sitting_allowed` (so only a participant "
        "is ever refused for SEB) and before the page is rendered")


def test_a_client_that_cannot_prove_itself_is_sent_to_the_handshake():
    """Where the refusal goes, and why it moved.

    The door used to flash a message and send the pupil to the exam list. It now hands
    them to the handshake page instead, because "no header" no longer means "not
    SEB": SEB for macOS/iOS runs on WKWebView, which cannot attach the Config Key to
    any request, so a header-only refusal would turn away every iPad on every gated
    paper. The handshake page asks the client's own JavaScript API, and the message a
    plain browser reads lives *there* — `test_seb_js_api.py` renders that page and
    asserts both halves of the copy and the guide link.

    What this file pins is the part that is the door's: it does not render the paper,
    and it does route the client to the handshake.
    """
    body = _function_body(STUDENT, "take_exam")
    gate = body.index("seb_service.gated(exam)")
    tail = body[gate:body.index("render_template(")]
    assert "redirect(" in tail, "a client that failed the check was not turned away"
    assert "seb.js_claim" in tail, "the refusal does not reach the handshake page"
    assert "seb_service.claim_valid(" in tail, (
        "the door never consults a claim, so the handshake can never succeed")


# ── 2. the URL hashed is the one the file carries ────────────────────────────

def test_the_door_hashes_the_same_url_the_config_starts_on():
    body = _function_body(STUDENT, "take_exam")
    gate = body.index("seb_service.verify(")
    call = body[gate:gate + 400]
    assert "seb_service.start_url(exam_id)" in call, (
        "the door hashes something other than the config's own startURL")
    assert "request.url" not in call, (
        "`request.url` carries the query string a forwarded link arrived with; "
        "hashing it refuses every honest client whose link was shared with one")


def test_a_bypassed_link_still_opens_and_a_foreign_url_does_not():
    """The decision, on the two URLs the door could have chosen.

    This is why the URL matters, stated as behaviour rather than as a comment: the
    canonical URL admits the client that was handed our file, while the *same
    request's* URL with a query string on it would not match that client's header.
    """
    key = "a" * 64
    exam = {"require_seb": True, "seb_config_key": key}
    canonical = "https://scangrade.web.id/student/exams/ex-1"
    forwarded = canonical + "?src=wa"

    # The client hashed its own address, which is the canonical one: SEB ignores
    # the fragment and never sees the query string the server's request carried.
    header = ck.request_hash(canonical, key)
    assert seb_service.verify(exam, canonical, header) is True
    # Same header, but a door that hashed `request.url` would refuse it — the
    # regression this assertion exists to keep out.
    assert seb_service.verify(exam, forwarded, header) is False


def test_the_end_to_end_file_hash_is_the_one_the_door_accepts():
    """Whatever the writer does to a config, the header its client sends matches.

    Built from the file's own settings rather than from a literal key, so a change
    to what goes into the file cannot leave the door checking an older idea of it.
    """
    from app.services import seb_crypto
    start = "https://scangrade.web.id/student/exams/ex-1"
    settings = seb_service.settings_for(
        None, start_url=start,
        quit_hash=seb_crypto.sha256_hex("quit-pass"),
        admin_hash=seb_crypto.sha256_hex("admin-pass"))
    key = ck.config_key(settings)
    # The settings round-trip through the file the pupil opens, and the key is the
    # same on both sides of it.
    assert ck.config_key(seb_service.decode_seb(seb_service.seb_file_bytes(settings))) == key
    exam = {"require_seb": True, "seb_config_key": key}
    assert seb_service.verify(exam, start, ck.request_hash(start, key)) is True
    assert seb_service.verify(exam, start, ck.request_hash(start, "b" * 64)) is False


# ── 3. one definition of the start URL, so file and door cannot drift ────────

def test_the_seb_route_delegates_the_start_url_rather_than_redefining_it():
    body = _function_body(SEB_ROUTE, "_start_url")
    assert "seb_service.start_url(" in body, (
        "the route builds the start URL itself; the door and the file can now drift")
    assert "url_for(" not in body, "a second copy of the URL construction is here"


def test_only_one_module_builds_the_exam_start_url():
    """`url_root` + `url_for('student.take_exam')` may appear in exactly one place."""
    pattern = re.compile(r"url_root.*url_for\(\s*[\"']student\.take_exam")
    builders = []
    for path in (ROOT / "app").rglob("*.py"):
        match = pattern.search(path.read_text(encoding="utf-8"))
        if match:
            builders.append(path.relative_to(ROOT).as_posix())
    assert builders == ["app/services/seb_service.py"], builders


def test_a_pupil_is_never_offered_the_quit_or_admin_password():
    """The brief's hardest rule, asserted at the access layer's own door too.

    `test_seb_access.py` covers the matrix; this is the one line that must also be
    true of the *route* a pupil can reach, because that is the endpoint a curious
    pupil probes by hand.
    """
    body = _function_body(ROOT / "app" / "routes" / "seb.py", "reveal_password")
    assert "ACCESS_VIEW_PASSWORD" in body
    # The reveal route is teacher-only by path and by role, and the decision runs
    # before the credential is read — so a pupil's request cannot reach `reveal`.
    gate = body.index("_verdict(")
    read = body.index("credentials_for(")
    assert gate < read, "the password is read before the decision that may refuse it"

    from app.services import seb_access
    refused = seb_access.decide(None, {"require_seb": True, "seb_config_key": "k"},
                                user_id="s-1", user_role="murid", user_school_id="sc-1",
                                access=seb_access.ACCESS_VIEW_PASSWORD)
    assert refused["ok"] is False and refused["reason"] == "role_not_covered", refused


# ── 4. the door, driven through the route itself ─────────────────────────────
#
# Why the route has to be *run* and not just read
# ----------------------------------------------
# "A gated paper is refused and an ungated one is admitted" is a statement about
# two different things happening, and every reading assertion above is equally
# satisfied by a door that refuses everyone: the call is there, the URL is right,
# the flash is written. What separates the two is the response — and, for the
# refusal, the *state* it leaves behind. So the body runs against a stand-in that
# really stores, the same shape `test_exam_countdown.py` uses and for the same
# reason. Nothing below touches the network: `get_supabase()` reads
# `current_app.extensions["supabase"]`, and the three services that would reach
# past the fake (page thumbnails, the recovery code, the media play count) are the
# ones that are irrelevant to the door's decision.

class _Query:
    """Enough of postgrest for this route: filters, `single`, insert, update."""

    def __init__(self, store, name):
        self._store, self._name = store, name
        self._filters, self._one, self._pending = [], False, None

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

    def limit(self, *a, **k):
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

    def update(self, patch):
        self._pending = ("update", dict(patch))
        return self

    def _matches(self):
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
        return out

    def execute(self):
        if self._pending:
            kind, payload = self._pending
            self._pending = None
            if kind == "insert":
                row = dict(payload)
                self._store.setdefault(self._name, []).append(row)
                return SimpleNamespace(data=[row], count=1)
            rows = self._matches()
            for row in rows:
                row.update(payload)
            return SimpleNamespace(data=rows, count=len(rows))
        rows = self._matches()
        if self._one:
            return SimpleNamespace(data=rows[0] if rows else None, count=len(rows))
        return SimpleNamespace(data=rows, count=len(rows))


class _FakeDb:
    def __init__(self, exam):
        self._tables = {
            "exams": [exam],
            "profiles": [{"id": PUPIL, "full_name": "Ahmad", "class_id": CLASS,
                          "school_id": SCHOOL, "role": "murid"}],
            "classes": [{"id": CLASS, "name": "7A", "grade_level": "7",
                         "school_id": SCHOOL}],
            "submissions": [],
        }

    def table(self, name):
        return _Query(self._tables, name)

    def rows(self, name):
        return self._tables.get(name, [])


def _exam(**over):
    row = {
        "id": EXAM_ID, "title": "Latihan Ujian", "subject": "Fisika",
        "school_id": SCHOOL, "class_ids": [CLASS], "teacher_id": "guru-1",
        "is_published": True, "status": "active",
        "duration_minutes": 60, "total_questions": 0,
        "question_types": {}, "answer_key": {}, "question_weights": {},
        "start_at": None, "end_at": None, "auto_submit_on_window_end": False,
        "pdf_page_urls": [],
        # The two columns this feature owns, at the values migration 062 defaults
        # them to — so the ungated case below is the *stored* case, not a fixture
        # that left the flag out.
        "require_seb": False, "seb_config_key": None,
    }
    row.update(over)
    return row


def _gated():
    return _exam(require_seb=True, seb_config_key=KEY)


def _right_header(app) -> str:
    """The header an honest client of *our* file sends, computed from the file."""
    with app.test_request_context(f"/student/exams/{EXAM_ID}"):
        start = seb_service.start_url(EXAM_ID)
    return ck.request_hash(start, KEY)


def _drive(app, monkeypatch, db, *, header=None):
    """Run the real `take_exam` body and report what the pupil got back."""
    from flask import g, get_flashed_messages
    from app.services import media_plays

    captured = {}

    def _capture(name, **kw):
        captured["template"], captured["ctx"] = name, kw
        return ""

    monkeypatch.setattr(studentmod, "render_template", _capture)
    monkeypatch.setattr(studentmod, "ensure_page_thumbs", lambda *a, **k: None)
    monkeypatch.setattr(studentmod, "issue_code", lambda *a, **k: "123456")
    monkeypatch.setattr(media_plays, "used_by_question", lambda *a, **k: {})
    app.extensions["supabase"] = db

    headers = {ck.CONFIG_KEY_HEADER: header} if header is not None else None
    with app.test_request_context(f"/student/exams/{EXAM_ID}", headers=headers):
        g.user_id, g.user_role = PUPIL, "murid"
        g.user_name, g.user_email = "Ahmad", "ahmad@sekolah.test"
        g.user_school_id, g.user_class_id, g.user_status = SCHOOL, CLASS, "active"
        g.tz_offset, g.show = 7, {}
        resp = studentmod.take_exam.__wrapped__(EXAM_ID)
        flashes = get_flashed_messages()
    return resp, captured.get("template"), flashes


class TestAnUngatedPaperIsUntouched:
    """The regression this whole feature leans on, asserted as behaviour."""

    def test_a_plain_browser_opens_a_paper_that_does_not_require_seb(self, app, monkeypatch):
        resp, template, flashes = _drive(app, monkeypatch, _FakeDb(_exam()))
        assert resp.status_code == 200, (
            "a paper nobody gated was refused — require_seb is off on this row")
        assert template == "student/take_exam.html"
        assert flashes == []

    def test_a_bogus_header_does_not_refuse_an_ungated_paper(self, app, monkeypatch):
        """`verify` returns True before it ever looks at the header: that ordering is
        the guarantee, and a client that sends a stale header from another paper
        must not lose access to a paper that never asked for one."""
        resp, template, _ = _drive(app, monkeypatch, _FakeDb(_exam()), header="f" * 64)
        assert resp.status_code == 200
        assert template == "student/take_exam.html"

    def test_the_ungated_path_still_opens_the_sitting(self, app, monkeypatch):
        """The control for the refusal assertion below: this fake *does* create a
        sitting when the route reaches `open_sitting`, so "no sitting" means the
        door turned the pupil away and not that the harness was inert."""
        db = _FakeDb(_exam())
        resp, _, _ = _drive(app, monkeypatch, db)
        assert resp.status_code == 200
        assert len(db.rows("submissions")) == 1


class TestAGatedPaperIsRefusedWithoutAMatchingHeader:
    def test_a_client_with_no_header_is_sent_to_the_handshake_page(self, app, monkeypatch):
        """Not to the exam list: the handshake page is where an iPad can still get in."""
        resp, template, _flashes = _drive(app, monkeypatch, _FakeDb(_gated()))
        assert resp.status_code == 302, "a plain browser was handed the paper"
        assert resp.headers["Location"].endswith(
            f"/student/exams/{EXAM_ID}/seb-claim"), resp.headers["Location"]
        assert template is None, "the page was rendered for a client that was refused"

    def test_a_header_hashed_with_another_paper_s_key_is_refused(self, app, monkeypatch):
        resp, template, _ = _drive(app, monkeypatch, _FakeDb(_gated()), header="b" * 64)
        assert resp.status_code == 302
        assert template is None

    def test_the_header_of_the_exam_s_own_file_is_admitted(self, app, monkeypatch):
        """The other side of the same door, so the refusals above cannot be a door
        that refuses everyone: a header built from the exam's stored key opens it."""
        resp, template, _ = _drive(app, monkeypatch, _FakeDb(_gated()),
                                   header=_right_header(app))
        assert resp.status_code == 200
        assert template == "student/take_exam.html"

    def test_a_gated_paper_whose_key_was_never_issued_fails_closed(self, app, monkeypatch):
        """`require_seb` on with no key is a half-written row — a toggle protecting
        nobody if it failed open, so it fails shut and the school reports it."""
        resp, template, _ = _drive(app, monkeypatch,
                                   _FakeDb(_exam(require_seb=True, seb_config_key=None)))
        assert resp.status_code == 302
        assert template is None

    def test_a_refused_client_does_not_spend_the_pupil_s_attempt(self, app, monkeypatch):
        """The gate sits before `open_sitting`, so a plain browser that gets turned
        away leaves nothing behind. A refusal that started a sitting would be worse
        than no gate: the pupil installs SEB, comes back, and is out of attempts."""
        db = _FakeDb(_gated())
        _drive(app, monkeypatch, db)
        assert db.rows("submissions") == []
