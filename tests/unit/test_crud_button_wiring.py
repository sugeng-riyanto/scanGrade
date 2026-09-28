"""The buttons on three pages that had no effect, held to the reason each was dead.

Reported: ``/admin-sekolah/promote``, ``/admin-sekolah/students`` and
``/super-admin/users/manage`` show working-looking CRUD controls whose actions
do nothing. Three unrelated causes, measured on the running box before this file
existed:

1. **every htmx write was refused before its handler ran.** The app enforces CSRF
   in a global ``before_request``, and ``base.html`` adds the token to forms and
   to ``window.fetch`` — but htmx drives its own ``XMLHttpRequest``, which neither
   reaches. Measured: ``POST`` of a reset-password with ``HX-Request`` and no
   token answered ``403 {"error": "CSRF token invalid"}``. So the per-row Reset PW
   and Delete on the student and teacher lists, the class delete, the feature-flag
   toggle and the teacher-dashboard assignment form were all refused, and the
   handlers that swapped the reply showed that error JSON in place of the button.
2. **the promote form sent a field the route does not read.** The radios were
   named ``target_type`` while ``promote()`` reads ``create_new == "1"``, so
   "Buat Kelas Baru" sent no ``create_new``, took the existing-class branch with an
   empty ``target_class_id``, and was refused with "Pilih atau buat kelas tujuan".
   Measured on the served page: ``target_type`` present, ``create_new`` absent.
3. **the super-admin reset answered 400 before it did anything.** The button sends
   ``Content-Type: application/json`` with no body, and ``request.get_json()``
   raises ``BadRequest`` on that — measured against Flask itself:
   ``empty body + json content-type -> 400``. The page's ``.then(r => r.json())``
   then threw on the HTML error page, so the promise never resolved.

What is asserted, and why each assertion is here:

* the htmx CSRF listener is **run**, not pattern-matched: a listener that sets the
  wrong header name passes any grep and still 403s every write;
* it is registered on ``document`` at parse time rather than inside
  ``DOMContentLoaded``, because a listener added on load is added after the page
  is ready but is still fine — what must not happen is registering it on an
  element, or never;
* the promote form sends ``create_new`` with the route's own ``1``/``0``, and the
  Alpine handle still hides and shows the right half;
* the student and teacher row actions go through ``fetch`` (which base.html does
  wrap) and drop their ``hx-post``, because the routes answer JSON and htmx would
  swap that JSON into the target;
* the reset writes the new password into the password modal the page already has;
* the super-admin reset route answers with a password for an empty JSON body.
"""
from __future__ import annotations

import contextlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.conftest import app_instance

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "app" / "templates" / "base.html"
PROMOTE = ROOT / "app" / "templates" / "admin_sekolah" / "promote.html"
STUDENTS = ROOT / "app" / "templates" / "admin_sekolah" / "students.html"
TEACHERS = ROOT / "app" / "templates" / "admin_sekolah" / "teachers.html"
ADMIN_ROUTES = ROOT / "app" / "routes" / "admin_sekolah.py"
SUPER_ROUTES = ROOT / "app" / "routes" / "super_admin.py"

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="needs node to run the listener")


def source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── 1. htmx writes carry the token ───────────────────────────────────────────

CSRF_LISTENER = re.compile(
    r"document\.addEventListener\(\s*'htmx:configRequest'\s*,\s*function\s*\(e\)\s*\{"
    r".*?\}\s*\)\s*;",
    re.S)


def _listener_statement() -> str:
    match = CSRF_LISTENER.search(source(BASE))
    assert match, (
        "base.html has no `htmx:configRequest` listener bound to `document`, so "
        "every hx-post on the site is refused 403 by the app's global CSRF hook "
        "before its handler runs")
    return match.group(0)


@needs_node
def test_the_htmx_listener_puts_the_token_on_every_request():
    """The registration statement itself is evaluated against a fake document,
    not grepped. Two defects pass every source check and 403 exactly like no
    listener: a header spelled one dash short, and a listener bound to an element
    — htmx dispatches on `document`, so `document.body.addEventListener` never
    hears it. Evaluating the real statement catches both, because the fake only
    hears what is bound to `document`."""
    statement = _listener_statement()
    script = (
        "let captured = null;\n"
        "globalThis.document = {\n"
        "  addEventListener: (name, fn) => { if (name === 'htmx:configRequest') captured = fn; },\n"
        "  querySelector: () => ({ getAttribute: () => 'TOKEN-abc123' }),\n"
        "};\n"
        + statement + "\n"
        "if (!captured) throw new Error('the listener was never registered on document');\n"
        "const e = { detail: { headers: {} } };\n"
        "captured(e);\n"
        "console.log(JSON.stringify(e.detail.headers));\n"
    )
    done = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == {"X-CSRF-Token": "TOKEN-abc123"}, (
        "the handler did not put the session token under `X-CSRF-Token`, which is "
        "the header the app reads")


# ── 2. the promote form sends what the route reads ───────────────────────────

def test_the_promote_form_sends_create_new_and_not_target_type():
    html = source(PROMOTE)
    assert 'name="create_new"' in html, (
        "the promote form sends no `create_new`, so choosing \"Buat Kelas Baru\" "
        "takes the existing-class branch with an empty target and is refused")
    assert 'name="target_type"' not in html, (
        "the form still sends `target_type`, which the route has never read")
    assert "create_new\") == \"1\"" in source(ADMIN_ROUTES), (
        "the route stopped reading `create_new`; the form and the route must agree")


def test_the_two_radio_values_are_the_ones_the_route_compares():
    html = source(PROMOTE)
    values = re.findall(r'type="radio"\s+name="create_new"\s+value="([^"]+)"', html)
    assert values == ["0", "1"], (
        f"the radios carry {values}; the route compares against \"1\"")
    assert "x-show=\"targetType === '1'\"" in html and "x-show=\"targetType === '0'\"" in html, (
        "the Alpine handle no longer shows the new-class half for value 1")


# ── 3. the row actions go through fetch ──────────────────────────────────────

class TestTheRowActionsPostThroughFetch:
    @pytest.mark.parametrize("name,path", [("students", STUDENTS), ("teachers", TEACHERS)])
    def test_no_hx_post_is_left_on_the_reset_and_delete_buttons(self, name, path):
        html = source(path)
        stragglers = re.findall(
            r'<button[^>]*hx-post="[^"]*/(?:reset-password|delete)"', html)
        assert not stragglers, (
            f"{name}.html still drives reset/delete through htmx ({len(stragglers)} "
            "button(s)), which the app refuses without a CSRF header and whose JSON "
            "reply htmx would swap into the target")

    @pytest.mark.parametrize("name,path", [("students", STUDENTS), ("teachers", TEACHERS)])
    def test_the_reset_calls_the_route_with_fetch(self, name, path):
        html = source(path)
        assert re.search(r"/reset-password',\{method:'POST'\}", html), (
            f"{name}.html does not POST to the reset route through fetch")

    @pytest.mark.parametrize("name,path", [("students", STUDENTS), ("teachers", TEACHERS)])
    def test_the_new_password_lands_in_the_page_modal(self, name, path):
        """The modal exists on both pages for the create flow; a reset reuses it, so
        the admin can read the password instead of a JSON fragment."""
        html = source(path)
        assert "generatedPassword=d.password" in html, (
            f"{name}.html does not put the reset password anywhere visible")
        assert "showPassword=true" in html, (
            f"{name}.html never opens the password modal after a reset")

    @pytest.mark.parametrize("name,path", [("students", STUDENTS), ("teachers", TEACHERS)])
    def test_the_modal_title_is_not_stuck_on_created(self, name, path):
        """It said "Berhasil Dibuat!" for a reset too. The title is now a field with
        the old copy as its default, so a reset can say what happened."""
        html = source(path)
        assert "pwTitle" in html and "pwNote" in html, (
            f"{name}.html has no field to say the reset succeeded")


# ── 4. the super-admin reset tolerates an empty JSON body ────────────────────

class _FakeAuthAdmin:
    def __init__(self):
        self.updated = []

    def update_user_by_id(self, user_id, payload):
        self.updated.append((user_id, payload))
        return type("U", (), {"user": {"id": user_id}})()


class _FakeAuth:
    def __init__(self):
        self.admin = _FakeAuthAdmin()


class _FakeSupabase:
    def __init__(self):
        self.auth = _FakeAuth()


@contextlib.contextmanager
def _unauthenticated_post(path, data=None):
    """The route body alone, under a request.

    The role guard is peeled with ``.__wrapped__`` on purpose: this test is about
    how the request *body* is parsed, and signing in would drag the token, the
    session cookie and the idle clock into a question they do not bear on. What
    stays real is the part that broke — ``request.get_json()`` against the exact
    headers the page sends.
    """
    with app_instance().test_request_context(
            path, method="POST",
            headers={"Content-Type": "application/json"}, data=data):
        yield


def test_the_super_admin_reset_accepts_an_empty_json_body(monkeypatch):
    """The button sends a JSON content type and no body. `get_json()` raises on
    that, so the route answered 400 before doing anything and the page's promise
    never resolved."""
    from app.routes import super_admin as mod

    fake = _FakeSupabase()
    monkeypatch.setattr(mod, "get_supabase", lambda: fake)
    body = mod.api_user_reset_password.__wrapped__

    with _unauthenticated_post("/super-admin/api/user/u-1/reset-password"):
        resp = body("u-1")

    assert resp.status_code == 200, (
        f"an empty JSON body answered {resp.status_code}; the reset button sends "
        "exactly that")
    payload = resp.get_json()
    assert payload["success"] is True and payload["password"], (
        "no password came back, so the modal has nothing to show")
    assert fake.auth.admin.updated == [("u-1", {"password": payload["password"]})]


def test_the_super_admin_reset_still_honours_a_supplied_password(monkeypatch):
    from app.routes import super_admin as mod

    fake = _FakeSupabase()
    monkeypatch.setattr(mod, "get_supabase", lambda: fake)
    body = mod.api_user_reset_password.__wrapped__

    with _unauthenticated_post("/super-admin/api/user/u-1/reset-password",
                               data=json.dumps({"password": "Chosen-123"})):
        resp = body("u-1")

    assert resp.get_json()["password"] == "Chosen-123", (
        "a caller who names the password must get the one they named")


def test_the_super_admin_email_change_tolerates_an_empty_body(monkeypatch):
    """Same shape of request, same 400 — and here the answer has to be the
    validation message rather than a parse failure, or the page alerts the wrong
    thing."""
    from app.routes import super_admin as mod

    monkeypatch.setattr(mod, "get_supabase", lambda: _FakeSupabase())
    body = mod.api_user_update_email.__wrapped__

    with _unauthenticated_post("/super-admin/api/user/u-1/update-email"):
        resp = body("u-1")

    # This route answers a (body, status) tuple rather than a Response; both
    # shapes are in the file, so the test unpacks rather than assumes.
    response, status = resp if isinstance(resp, tuple) else (resp, resp.status_code)
    assert status == 400 and response.get_json()["error"] == "Email tidak valid", (
        "an empty body is not a valid email — but it must be *this* refusal, not a "
        "JSON parse error")
