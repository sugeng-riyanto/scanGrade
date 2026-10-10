"""The second transport: the Config Key on a client that cannot send a header.

Why this exists at all, in one paragraph
----------------------------------------
SEB for macOS/iOS 3.0+ runs on WKWebView, and the project's own developer
documentation states that WKWebView cannot attach the Config Key to an HTTP request
"at all" — the way in for those clients is the Safe Exam Browser JavaScript API. A
gate that only reads the header therefore does not merely *degrade* on an iPad: it
refuses every iPad and every modern macOS client, on every gated paper, with a
message blaming the pupil's browser. That is the failure this file is about.

The properties, and the wrong version of each
--------------------------------------------
1. **It is the same verification, reached differently.** The documentation is
   explicit that the JavaScript value is "identical to the ones send in the HTTP
   request header". So the claim must land in the *one* comparison
   (`seb_config_key.header_matches`) — a second comparison function is a second
   thing to keep in step, and it is the sort of drift that only shows up on the
   platform nobody tests (which is exactly the platform this path serves).
2. **The value is bound to the page it was read on.** The client hashes *the URL of
   the page the script ran on*, which here is the handshake page, not the exam's
   `startURL`. Verifying against the exam's address would refuse every honest client
   while looking correct in review — so it is tested as behaviour, both ways.
3. **The claim cannot widen access.** It is honoured only for the exam it names,
   only for a few minutes, and only for a pupil the door had already admitted. Each
   of those is a test, because each of them is a way the fallback could become a
   bypass.
4. **A claim that was never made is not a claim.** An absent, malformed, expired or
   foreign claim all read as "no proof", never as "some proof" — the dangerous
   default for anything that unlocks a paper.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.routes import student as studentmod
from app.services import seb_config_key as ck
from app.services import seb_service

EXAM_ID = "exam-1"
PUPIL = "stu-1"
SCHOOL = "school-A"
CLASS = "class-7A"
KEY = "a" * 64


# ── a postgrest stand-in, and the world of one pupil on one paper ─────────────

class _Query:
    def __init__(self, store, name):
        self._store, self._name = store, name
        self._filters, self._one, self._pending = [], False, None

    def select(self, *a, **k):
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


class _Db:
    def __init__(self, exam):
        self._tables = {
            "exams": [exam] if exam else [],
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
        "pdf_page_urls": [], "require_seb": True, "seb_config_key": KEY,
    }
    row.update(over)
    return row


def _claim_value(app, *, url=None, key=KEY) -> str:
    """The value an honest client would report, computed over the page it is on."""
    if url is None:
        with app.test_request_context(f"/student/exams/{EXAM_ID}/seb-claim"):
            url = seb_service.claim_url(EXAM_ID)
    return ck.request_hash(url, key)


def _claim(exam_id=EXAM_ID, *, age=0, method=seb_service.JS_CLAIM_METHOD):
    return {"exam_id": exam_id, "method": method,
            "at": (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat()}


# ── 1. the same verification, reached by another transport ───────────────────

def test_the_js_value_verifies_through_the_one_comparison(app):
    """Whatever the client reports is judged by `header_matches`, not by a copy.

    A second comparison would have to be kept in step with the first by hand, and the
    only clients this path exists for are the ones nobody has on their desk.
    """
    url = "https://scangrade.web.id/student/exams/ex-1/seb-claim"
    assert ck.header_matches(url, ck.request_hash(url, KEY), KEY)
    assert not ck.header_matches(url, ck.request_hash(url, "b" * 64), KEY)


def test_the_claim_route_does_not_verify_the_key_itself():
    """Source rule: the route must *call* the comparison rather than inline one."""
    from pathlib import Path
    import ast

    source = Path(seb_service.__file__).resolve().parents[1] / "routes" / "seb.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    body = ""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "js_claim":
            body = ast.get_source_segment(source.read_text(encoding="utf-8"), node) or ""
    assert body, "no js_claim function found"
    assert "header_matches(" in body, (
        "the claim route does not use the one comparison the header path uses")
    assert "sha256" not in body, (
        "the claim route hashes something itself — a second implementation of the "
        "one rule, which is exactly the drift this path cannot afford")


# ── 2. bound to the page's own URL, not the exam's ───────────────────────────

def test_a_value_hashed_over_the_exam_url_is_refused(app, monkeypatch):
    """The subtle one: the client hashes the page it is on, and that page is this one."""
    db = _Db(_exam())
    with app.test_request_context(f"/student/exams/{EXAM_ID}"):
        exam_url = seb_service.start_url(EXAM_ID)
    out, stored = _call_claim(app, monkeypatch, db, method="POST",
                              payload={"config_key": ck.request_hash(exam_url, KEY)})
    assert out[1] == 403, "a value hashed over the exam URL was accepted"
    assert stored == {}


def test_a_value_hashed_over_the_claim_page_url_is_accepted(app, monkeypatch):
    out, stored = _call_claim(app, monkeypatch, _Db(_exam()), method="POST",
                              payload={"config_key": _claim_value(app)})
    assert out[1] == 200
    assert out[0].get_json()["ok"] is True
    assert stored.get("exam_id") == EXAM_ID


def test_only_one_module_builds_the_claim_url():
    """Two copies of this string is the failure this whole path is vulnerable to.

    Naming the *route* (`url_for('seb.js_claim')`) is how a page links to it and is
    fine; composing an absolute address out of `url_root` and that route — which is
    what the client's value is hashed *over* — is the thing that must exist once.
    """
    import re
    from pathlib import Path

    root = Path(seb_service.__file__).resolve().parents[2]
    pattern = re.compile(r"url_root.*url_for\(\s*[\"']seb\.js_claim")
    builders = [path.relative_to(root).as_posix()
                for path in (root / "app").rglob("*.py")
                if pattern.search(path.read_text(encoding="utf-8"))]
    assert builders == ["app/services/seb_service.py"], builders


# ── 3. the route's own behaviour ─────────────────────────────────────────────

def _call_claim(app, monkeypatch, db, *, method="GET", payload=None, uid=PUPIL):
    """Run the real route body; return `((response_or_html, status), stored claim)`.

    The route answers in three shapes — a rendered page, a `Response`, or the
    `(Response, status)` pair Flask takes for a refusal — and a harness that only
    understands the first two reports every refusal as `200`. That is exactly the
    bug this helper had, and it is the reason the status is normalised here rather
    than read off the return value at each call site.
    """
    from flask import g, session
    from app.routes import seb as sebmod

    monkeypatch.setattr(sebmod, "get_supabase", lambda: db)
    app.extensions["supabase"] = db
    kwargs = {"json": payload} if payload is not None else {}
    with app.test_request_context(f"/student/exams/{EXAM_ID}/seb-claim",
                                  method=method, **kwargs):
        g.user_id, g.user_role = uid, "murid"
        g.user_name, g.user_email = "Ahmad", "a@sekolah.test"
        g.user_school_id, g.user_class_id, g.user_status = SCHOOL, CLASS, "active"
        g.tz_offset, g.show = 7, {}
        out = sebmod.js_claim.__wrapped__(EXAM_ID)
        stored = dict(session.get(seb_service.JS_CLAIM_SESSION_KEY) or {})
    if isinstance(out, tuple):
        return (out[0], out[1]), stored
    if hasattr(out, "status_code"):
        return (out, out.status_code), stored
    return (out, 200), stored


def test_the_page_is_served_to_a_gated_paper(app, monkeypatch):
    (body, status), _ = _call_claim(app, monkeypatch, _Db(_exam()))
    assert status == 200
    code = _script_code(_page_script(body))
    assert "SafeExamBrowser" in code, "the page does not ask the client's own API"
    assert "updateKeys" in code, "the page never calls updateKeys"
    assert "/student/exams/exam-1/seb-claim" in body


def test_the_refusal_panel_offers_the_pupil_their_own_file(app, monkeypatch):
    """The one page that tells a pupil to open the file must hand them *their* file.

    Both states this panel shows are fixed by that file: an ordinary browser holds
    none, and a stale file carries a key this exam no longer has. The route behind
    the link is the access the RBAC matrix already grants a pupil
    (`decide(murid, download_file)`, recorded in `seb_access_log`) — it existed
    before this link did, and nothing in the app pointed at it, which made a
    granted permission a URL somebody had to guess. Asserted on the *rendered*
    page, because a link that lost its binding is a button that does nothing.
    """
    (body, status), _ = _call_claim(app, monkeypatch, _Db(_exam()))
    assert status == 200
    assert f"/student/exams/{EXAM_ID}/seb-file" in body, (
        "the handshake page does not offer the pupil's own .seb file")
    assert ':href="fileUrl"' in body, "the file link is not bound to its URL"
    assert "t('Unduh berkas .seb saya','Download my .seb file')" in body


def test_an_ungated_paper_has_nothing_to_claim(app, monkeypatch):
    (resp, status), _ = _call_claim(app, monkeypatch, _Db(_exam(require_seb=False)))
    assert status == 302
    assert resp.headers["Location"].endswith(f"/student/exams/{EXAM_ID}")


def test_a_missing_exam_is_a_404(app, monkeypatch):
    from werkzeug.exceptions import NotFound
    with pytest.raises(NotFound):
        _call_claim(app, monkeypatch, _Db(None))


def test_the_right_value_is_recorded_as_a_claim(app, monkeypatch):
    (out, status), stored = _call_claim(app, monkeypatch, _Db(_exam()), method="POST",
                                        payload={"config_key": _claim_value(app)})
    assert status == 200 and out.get_json() == {"ok": True, "reason": ""}
    assert stored["exam_id"] == EXAM_ID
    assert stored["method"] == seb_service.JS_CLAIM_METHOD
    assert seb_service.claim_valid(stored, EXAM_ID)


def test_a_wrong_value_is_refused_and_stores_nothing(app, monkeypatch):
    (out, status), stored = _call_claim(app, monkeypatch, _Db(_exam()), method="POST",
                                        payload={"config_key": "b" * 64})
    assert status == 403 and out.get_json()["ok"] is False
    assert stored == {}, "a refused claim still left a claim in the session"


def test_no_value_at_all_is_refused(app, monkeypatch):
    (out, status), stored = _call_claim(app, monkeypatch, _Db(_exam()), method="POST",
                                        payload={})
    assert status == 403
    assert stored == {}


def test_a_pupil_not_on_the_paper_cannot_claim(app, monkeypatch):
    """The claim must not be mintable for a paper that is not this pupil's."""
    from werkzeug.exceptions import Forbidden
    outsider = _exam(class_ids=["class-9Z"])
    with pytest.raises(Forbidden):
        _call_claim(app, monkeypatch, _Db(outsider), method="GET")
    (out, status), stored = _call_claim(app, monkeypatch, _Db(outsider),
                                        method="POST", payload={"config_key": "x"})
    assert status == 403
    assert out.get_json()["reason"] == "not_allowed"
    assert stored == {}


def test_the_claim_stores_no_key_material(app, monkeypatch):
    """A cookie is the one place a derived secret must never be parked.

    The session is signed, but it is still *held by the client*: what is stored is
    the fact that the proof happened, not the proof.
    """
    (value, _status), stored = _call_claim(app, monkeypatch, _Db(_exam()), method="POST",
                                           payload={"config_key": _claim_value(app)})
    blob = repr(stored)
    assert KEY not in blob
    assert _claim_value(app) not in blob
    assert "seb_config_key" not in blob
    assert set(stored) == {"exam_id", "method", "at"}, stored


def test_the_claim_post_is_csrf_protected(app):
    """It is a state-changing POST, so it answers to the app's global guard.

    Asserted through the real client so the guard is exercised rather than described:
    without a token the request is refused before the view is ever reached.
    """
    client = app.test_client()
    url = f"/student/exams/{EXAM_ID}/seb-claim"
    no_token = client.post(url, json={"config_key": "x"})
    assert no_token.status_code == 403
    assert "CSRF" in (no_token.get_json() or {}).get("error", "")

    with client.session_transaction() as sess:
        sess["_csrf_token"] = "t" * 64
    with_token = client.post(url, json={"_csrf_token": "t" * 64, "config_key": "x"})
    assert with_token.status_code != 403 or "CSRF" not in (
        with_token.get_json() or {}).get("error", ""), (
        "a request carrying the session's own token was still refused as CSRF")


# ── 4. the door: a fresh claim is a proof, and only that ─────────────────────

def _visit_exam(app, monkeypatch, db, *, header=None, claim=None):
    """Run the real exam door and report what the pupil got back."""
    from flask import g, session
    from app.services import media_plays

    captured = {}

    def _capture(name, **kw):
        captured["template"] = name
        return ""

    monkeypatch.setattr(studentmod, "render_template", _capture)
    monkeypatch.setattr(studentmod, "ensure_page_thumbs", lambda *a, **k: None)
    monkeypatch.setattr(studentmod, "issue_code", lambda *a, **k: "123456")
    monkeypatch.setattr(media_plays, "used_by_question", lambda *a, **k: {})
    app.extensions["supabase"] = db

    headers = {ck.CONFIG_KEY_HEADER: header} if header else None
    with app.test_request_context(f"/student/exams/{EXAM_ID}", headers=headers):
        g.user_id, g.user_role = PUPIL, "murid"
        g.user_name, g.user_email = "Ahmad", "a@sekolah.test"
        g.user_school_id, g.user_class_id, g.user_status = SCHOOL, CLASS, "active"
        g.tz_offset, g.show = 7, {}
        if claim is not None:
            session[seb_service.JS_CLAIM_SESSION_KEY] = claim
        resp = studentmod.take_exam.__wrapped__(EXAM_ID)
    return resp, captured.get("template")


def test_a_pupil_with_a_fresh_claim_opens_the_paper(app, monkeypatch):
    """The whole point: an iPad, which can never send the header, can sit the paper."""
    resp, template = _visit_exam(app, monkeypatch, _Db(_exam()), claim=_claim())
    assert resp.status_code == 200, "a claimed client was still refused"
    assert template == "student/take_exam.html"


def test_a_pupil_with_no_claim_is_sent_to_the_handshake(app, monkeypatch):
    resp, template = _visit_exam(app, monkeypatch, _Db(_exam()))
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith(f"/student/exams/{EXAM_ID}/seb-claim")
    assert template is None, "the paper was rendered without any proof"


def test_a_claim_for_another_paper_does_not_open_this_one(app, monkeypatch):
    resp, template = _visit_exam(app, monkeypatch, _Db(_exam()),
                                 claim=_claim(exam_id="exam-other"))
    assert resp.status_code == 302 and template is None


def test_an_expired_claim_does_not_open_the_paper(app, monkeypatch):
    resp, template = _visit_exam(app, monkeypatch, _Db(_exam()),
                                 claim=_claim(age=seb_service.JS_CLAIM_TTL + 1))
    assert resp.status_code == 302 and template is None


def test_a_claim_of_another_method_does_not_open_the_paper(app, monkeypatch):
    resp, template = _visit_exam(app, monkeypatch, _Db(_exam()),
                                 claim=_claim(method="trust-me"))
    assert resp.status_code == 302 and template is None


def test_the_header_still_opens_the_paper_on_its_own(app, monkeypatch):
    """The new path must not have become the only path."""
    with app.test_request_context(f"/student/exams/{EXAM_ID}"):
        right = ck.request_hash(seb_service.start_url(EXAM_ID), KEY)
    resp, template = _visit_exam(app, monkeypatch, _Db(_exam()), header=right)
    assert resp.status_code == 200 and template == "student/take_exam.html"


def test_a_claimed_client_still_opens_a_sitting_like_any_other(app, monkeypatch):
    """The control: a claim does not skip the rest of the door."""
    db = _Db(_exam())
    resp, _ = _visit_exam(app, monkeypatch, db, claim=_claim())
    assert resp.status_code == 200
    assert len(db.rows("submissions")) == 1


# ── 5. the freshness rule, at its boundaries ────────────────────────────────

@pytest.mark.parametrize("age,expected", [
    (0, True),
    (seb_service.JS_CLAIM_TTL - 1, True),
    (seb_service.JS_CLAIM_TTL, True),
    (seb_service.JS_CLAIM_TTL + 1, False),
    (86400, False),
    (-30, True),          # ordinary clock skew between two machines
    (-3600, False),       # a claim dating from the future is not fresher than now
])
def test_claim_freshness_at_the_edges(age, expected):
    assert seb_service.claim_valid(_claim(age=age), EXAM_ID) is expected


def test_a_stored_claim_is_read_as_what_it_is():
    """Anything that is not one of our claims reads as "no proof"."""
    for junk in (None, "", 0, "js_api", [], {"exam_id": EXAM_ID},
                 {"exam_id": EXAM_ID, "method": seb_service.JS_CLAIM_METHOD},
                 {"exam_id": EXAM_ID, "method": seb_service.JS_CLAIM_METHOD, "at": "never"}):
        assert seb_service.claim_valid(junk, EXAM_ID) is False, junk


def test_the_window_stays_short():
    """A guard against the "fix" that makes a page-load proof last a working day."""
    assert 0 < seb_service.JS_CLAIM_TTL <= 600, seb_service.JS_CLAIM_TTL


# ── 6. the page itself ──────────────────────────────────────────────────────

def _render_page(app):
    """The template with the context its route passes — all four names, not three.

    A context that lags the route is not a smaller test, it is a different template:
    `{{ file_url|tojson }}` on a missing name raises while the page is being built,
    so every test here failed at once when the page gained the pupil's own file link
    — which is the loud shape, and the reason the template carries no `|default`.
    """
    from flask import g, render_template
    with app.test_request_context(f"/student/exams/{EXAM_ID}/seb-claim"):
        g.user_id, g.user_role = PUPIL, "murid"
        g.user_name, g.user_email = "Ahmad", "a@sekolah.test"
        g.user_school_id, g.user_class_id, g.user_status = SCHOOL, CLASS, "active"
        g.tz_offset, g.show = 7, {}
        return render_template("student/seb_claim.html", exam={"id": EXAM_ID},
                               exam_url=f"/student/exams/{EXAM_ID}",
                               guide_url="/panduan/seb",
                               file_url=f"/student/exams/{EXAM_ID}/seb-file")


def _page_script(html: str) -> str:
    """The handshake page's own `<script>`, found by what it defines.

    **Not** ``html.split("<script>")[-1]``. The base layout emits scripts *after* the
    page's own block — the privacy-info fetch is one of them — so "the last script"
    is somebody else's, and a guard reading it passes while the script it meant to
    judge goes unexamined. That is not hypothetical: this helper replaced exactly that
    expression, and the first mutation run walked straight through both guards that
    used it. A block is located by the function it defines, and if no block defines
    it the helper says so rather than returning an empty string that asserts nothing.
    """
    for chunk in html.split("<script>")[1:]:
        body = chunk.split("</script>")[0]
        if "function sebClaim()" in body:
            return body
    raise AssertionError("the handshake page's own script is not in the page")


def _script_code(script: str) -> str:
    """The script's *code*, with its comments removed.

    A JS comment is not something a pupil reads, and this block's own header comment
    names ``security.configKey`` — so an assertion that the page *reads* that value
    would be satisfied by prose. The repository has written this trap down once
    already (the i18n sweep read a JS comment as untranslated copy); this is the same
    fix at a smaller scale.
    """
    import re
    return re.sub(r"/\*.*?\*/", "", re.sub(r"//[^\n]*", "", script), flags=re.S)


def test_the_page_renders_and_asks_the_clients_own_api(app):
    html = _render_page(app)
    code = _script_code(_page_script(html))
    assert "window.SafeExamBrowser" in code
    assert "security.updateKeys(" in code, "the page never calls the client's updateKeys"
    assert "security.configKey" in code
    assert "/student/exams/exam-1/seb-claim" in html, (
        "the page does not know where to send the claim")
    assert "/student/exams/exam-1" in html, "the page does not know where to go next"


def test_the_page_tells_a_pupil_in_a_plain_browser_what_to_open(app):
    """The refusal the door used to flash now lives here, and it must survive."""
    html = _render_page(app)
    assert "Safe Exam Browser" in html
    assert "berkas .seb dari sekolah Anda" in html      # Indonesian half
    assert "the .seb file from your school" in html     # English half
    assert "/panduan/seb" in html, "the page does not point at the guide"


def test_the_page_does_not_assemble_its_copy_in_javascript(app):
    """Copy frozen inside a script never follows the language toggle.

    The script is allowed to set a *word* (`checking`, `refused`, `error`); every
    sentence a pupil reads comes from the template as a pair.
    """
    html = _render_page(app)
    script = _page_script(html)
    for phrase in ("Memeriksa", "Ujian ini hanya bisa", "berkas .seb dari sekolah",
                   "Tidak bisa menghubungi server", "Coba lagi"):
        assert phrase not in script, f"copy is written inside the script: {phrase!r}"


# ── 7. the handshake script, executed ───────────────────────────────────────
#
# Everything above reads the page. Reading is not enough for the page that *is* the
# iPad's only way in: a script with a syntax error, a `fetch` that never fires, or a
# missing CSRF header all look identical to a text assertion — the page serves, the
# copy is right, and every WKWebView client is stuck forever. So the real script is
# sliced out of the **rendered** page (Jinja has already run, so the URLs are the
# ones a client gets) and executed under node, with the three environments that
# matter: no JavaScript API at all (an ordinary browser), the API already populated
# (newer SEB), and `configKey` only filled in by `updateKeys` (SEB 3.0 macOS/iOS).

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="needs node to run the handshake")

DRIVER = r"""
const fs = require('fs');
const mode = process.argv[2];
const script = fs.readFileSync(process.argv[3], 'utf8');
// What the client's own API reports. A real SEB reports `security.configKey`,
// which is the Config Key hashed with the URL of the page the script ran on; the
// caller hands the value in so the chain tests can report an honest one, another
// key's, and one from before a re-issue.
const reported = process.argv[4] || 'SEB-KEY';
const seen = { fetches: [], replaced: null, updateKeysCalls: 0 };
global.document = { querySelector: () => ({ getAttribute: () => 'CSRF-TOKEN' }) };
global.fetch = (url, options) => {
  seen.fetches.push({ url, method: options && options.method, body: options && options.body,
                      headers: options && options.headers });
  const answer = (mode === 'server-refuses')
    ? { ok: false, json: () => Promise.resolve({ ok: false, reason: 'config_key_mismatch' }) }
    : { ok: true, json: () => Promise.resolve({ ok: true, reason: '' }) };
  return Promise.resolve(answer);
};
global.window = { location: { replace: (u) => { seen.replaced = u; } } };
if (mode !== 'no-api') {
  const security = {};
  if (mode === 'update-keys') {
    security.updateKeys = (cb) => { seen.updateKeysCalls += 1; security.configKey = 'FROM-CALLBACK'; cb(); };
  } else {
    security.configKey = reported;
  }
  window.SafeExamBrowser = { security: security };
}
(0, eval)(script);
const vm = sebClaim();
vm.start();
setTimeout(() => console.log(JSON.stringify({ state: vm.state, seen: seen,
                                              claimUrl: vm.claimUrl, examUrl: vm.examUrl })), 30);
"""


def _run_handshake(app, tmp_path: Path, mode: str, value: str = "SEB-KEY") -> dict:
    """Run the page's own script under node; return what it did.

    The script comes from the rendered page, so this measures the bytes a client is
    served — not the template's raw text, which still carries Jinja and would not
    even parse.
    """
    script = tmp_path / "seb_claim.js"
    script.write_text(_page_script(_render_page(app)), encoding="utf-8")
    driver = tmp_path / "driver.js"
    driver.write_text(DRIVER, encoding="utf-8")
    done = subprocess.run([NODE or "node", str(driver), mode, str(script), value],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


@needs_node
def test_an_ordinary_browser_is_told_to_open_the_file_and_never_posts_a_claim(app, tmp_path):
    """The most common reader: a pupil who clicked the link instead of the file.

    "Never posts a claim" is the assertion, and it is narrower than "sends nothing"
    on purpose. This page is the only witness to this half of the door — a browser
    with no SEB API never reaches the server any other way — so it reports the refusal
    it saw, which is what lets the teacher's panel say how many pupils were turned
    away and when. What it must **not** do is post to the claim endpoint: a claim
    from a client with nothing to prove is the bypass this page exists to prevent, and
    a count of fetches would call reporting it the same defect.
    """
    out = _run_handshake(app, tmp_path, "no-api")
    assert out["state"] == "refused"
    fetches = out["seen"]["fetches"]
    assert [f["url"] for f in fetches] == [f"/student/exams/{EXAM_ID}/seb-claim/refused"], (
        f"a page with no client API posted something else: {fetches}")
    assert json.loads(fetches[0]["body"]) == {"reason": "no_client"}
    assert fetches[0]["method"] == "POST"
    assert out["seen"]["replaced"] is None, "an unproven client was sent to the paper"


@needs_node
def test_the_script_posts_the_key_the_client_reports_and_then_opens_the_paper(app, tmp_path):
    """The whole point of the page: prove, then go to the exam."""
    out = _run_handshake(app, tmp_path, "present")
    fetches = out["seen"]["fetches"]
    assert len(fetches) == 1, fetches
    assert fetches[0]["url"] == out["claimUrl"] == "/student/exams/exam-1/seb-claim"
    assert fetches[0]["method"] == "POST"
    assert json.loads(fetches[0]["body"]) == {"config_key": "SEB-KEY"}
    # The route is CSRF-protected, so a claim without the token is refused — and the
    # refusal is a 403 the page would read as "not a claim" on a real iPad.
    assert fetches[0]["headers"]["X-CSRF-Token"] == "CSRF-TOKEN"
    assert out["seen"]["replaced"] == out["examUrl"] == "/student/exams/exam-1"


@needs_node
def test_the_key_is_read_only_after_updateKeys_on_the_clients_that_need_it(app, tmp_path):
    """SEB 3.0 macOS/iOS populates `configKey` inside the callback. Reading it
    before the callback posts an empty string, and the pupil sees a refusal."""
    out = _run_handshake(app, tmp_path, "update-keys")
    assert out["seen"]["updateKeysCalls"] == 1
    assert json.loads(out["seen"]["fetches"][0]["body"]) == {"config_key": "FROM-CALLBACK"}
    assert out["seen"]["replaced"] == out["examUrl"]


@needs_node
def test_a_refused_claim_never_opens_the_paper(app, tmp_path):
    """The client side of the door: a 403 must leave the pupil on the page that
    tells them what to do, not on a paper the server would bounce anyway."""
    out = _run_handshake(app, tmp_path, "server-refuses")
    assert out["state"] == "refused"
    assert out["seen"]["replaced"] is None, "a refused claim still navigated to the exam"
    # And the page does not report it a second time: the claim route already wrote the
    # refusal, so a second row would overstate how many pupils were turned away.
    assert [f["url"] for f in out["seen"]["fetches"]] == [f"/student/exams/{EXAM_ID}/seb-claim"], (
        "the mismatch was reported twice")


def test_the_handshake_script_parses_as_javascript(app, tmp_path):
    """A syntax error on this page strands every iPad, and no text assertion sees it."""
    script = tmp_path / "seb_claim.js"
    script.write_text(_page_script(_render_page(app)), encoding="utf-8")
    done = subprocess.run([NODE or "node", "--check", str(script)],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr


# ── 8. the chain: the page's own POST, the real route, the real door ────────
#
# The sections above prove each half with the other stubbed: the script posts a
# good-looking body to a fake `fetch`, and the route is called with a value a test
# computed itself. Neither says the *page* and the *server* agree — and if they do
# not, every client is refused forever while both halves look individually correct.
#
# So these tests join them: the shipped script runs under node, the request it
# actually produced is fed to the real route, and the session the route answers with
# is handed to the real exam door. What is asserted is the whole chain — admitted
# with a value the client's own API would report for this paper, refused when that
# value is another key's or one from before a re-issue, and the paper opened with
# **no header at all**, which is the only way an iPad ever sits a gated paper.


def _claim_page_url(app) -> str:
    """The address the browser is on when the script runs — what the value covers."""
    with app.test_request_context(f"/student/exams/{EXAM_ID}/seb-claim"):
        return seb_service.claim_url(EXAM_ID)


def _reported(app, key: str) -> str:
    """The value a real SEB would report, computed from the documentation by hand.

    Deliberately **not** `ck.request_hash`: the point of this oracle is to say that
    the server's expectation is the documented rule (SHA256 of the page's absolute
    URL followed by the Config Key) rather than whatever the server's own helper
    happens to do. A helper that agreed with itself while disagreeing with a client
    would pass every other test in this file.
    """
    import hashlib

    return hashlib.sha256((_claim_page_url(app) + key).encode("utf-8")).hexdigest()


def _run_chain(app, monkeypatch, tmp_path, *, reported: str, db=None):
    """The shipped page's request, through the real route, then the real door.

    Returns (the route's verdict, the session claim it stored, the door's answer).
    """
    db = db or _Db(_exam())
    post = _run_handshake(app, tmp_path, "present", reported)["seen"]["fetches"][0]
    assert post["method"] == "POST", post
    (resp, status), stored = _call_claim(app, monkeypatch, db, method="POST",
                                         payload=json.loads(post["body"]))
    door, template = _visit_exam(app, monkeypatch, db, claim=stored)
    return (resp, status, stored), (door, template)


def test_the_documented_rule_is_what_the_server_expects(app):
    """The oracle itself, cross-checked against the app's own implementation.

    Without this the chain below could pass with both sides wrong in the same way.
    """
    assert _reported(app, KEY) == ck.request_hash(_claim_page_url(app), KEY)


@needs_node
def test_the_page_s_own_request_is_admitted_and_opens_the_paper(app, monkeypatch, tmp_path):
    """The whole answer to "can an iPad sit a gated paper": yes, and here is the chain."""
    db = _Db(_exam())
    (resp, status, stored), (door, template) = _run_chain(
        app, monkeypatch, tmp_path, reported=_reported(app, KEY), db=db)
    assert status == 200, resp.get_json()
    assert resp.get_json()["ok"] is True
    assert seb_service.claim_valid(stored, EXAM_ID)
    assert door.status_code == 200, "the door refused a client the handshake admitted"
    assert template == "student/take_exam.html"
    assert len(db.rows("submissions")) == 1, "the sitting was not opened"


@needs_node
def test_the_page_s_request_with_another_key_is_refused_and_leaves_the_paper_shut(
        app, monkeypatch, tmp_path):
    """A wrong value is not a proof, and the door says so on the very next load."""
    (resp, status, stored), (door, template) = _run_chain(
        app, monkeypatch, tmp_path, reported=_reported(app, "b" * 64))
    assert status == 403 and resp.get_json()["ok"] is False
    assert stored == {}, "a refused claim was left in the session"
    assert door.status_code == 302 and template is None
    assert door.headers["Location"].endswith(f"/student/exams/{EXAM_ID}/seb-claim")


@needs_node
def test_the_page_s_request_from_before_a_re_issue_is_refused(app, monkeypatch, tmp_path):
    """The stale file a pupil is still holding: honest for the key that was stored
    when it was issued, and dead the moment the key is re-issued."""
    old = "c" * 64
    reissued = _exam(seb_config_key="d" * 64)
    # The client reports the value its *old* file generates; the exam now stores the
    # key the re-issue wrote.
    (resp, status, stored), (door, template) = _run_chain(
        app, monkeypatch, tmp_path, reported=_reported(app, old), db=_Db(reissued))
    assert status == 403 and resp.get_json()["ok"] is False
    assert stored == {}
    assert door.status_code == 302 and template is None, (
        "a value from before the re-issue opened the paper")


@needs_node
def test_the_reissued_key_is_the_one_the_page_s_chain_needs(app, monkeypatch, tmp_path):
    """The control for the stale case: the *new* value is admitted on the same exam
    row, so the refusal above is about the key and not about the paper."""
    db = _Db(_exam(seb_config_key="d" * 64))
    (resp, status, stored), (door, template) = _run_chain(
        app, monkeypatch, tmp_path, reported=_reported(app, "d" * 64), db=db)
    assert status == 200 and resp.get_json()["ok"] is True
    assert door.status_code == 200 and template == "student/take_exam.html"
