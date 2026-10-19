"""Every deploy browser gate, run against a page that is deliberately broken.

Why this exists
---------------
A gate that cannot fail is worse than no gate: it reports OK, the release is kept,
and the page a teacher or a pupil uses is broken anyway. `deploy/touch_gate.py`,
`deploy/exam_render_gate.py` and the `deploy/browser_gate.py` run that drives them
all have their judging half tested — a reading with a short control becomes a
finding, a blank reading does not become a pass — and **none of those tests drives
a browser at a page that is actually broken**. A reading is a dict the test wrote;
the question this file answers is the one a dict cannot: when the measurement runs
for real, over HTTP, through Chrome, against a page that is broken in the way the
gate exists to catch, does the gate come back saying *this is broken* (exit 1) — or
does it come back saying *I could not look* (exit 2), or worse, *OK* (exit 0)?

The distinction is the whole reason the two codes are apart, and it is not
academic: exit 2 does not refuse a release, so a broken page that reads as "could
not measure" ships quietly. That is the failure mode this file is built to make
impossible to reach by accident.

What the pages are, and what they are not
-----------------------------------------
The pages here are **stand-ins**, served from a local `http.server` at the routes
each gate dials. They are not the app's pages and this file does not pretend
otherwise: `deploy/browser_gate.py` measures the release's own pages through
`deploy/fixture_app.py`, and the box measures the live app. What is under test here
is a gate's *verdict*, so a page only has to satisfy the contract that gate reads
off a page, which is small and exact:

  * a login form the gate's own `sign_in` accepts (a CSRF meta tag and a 302),
  * the path the gate navigates to and stays on,
  * the DOM the gate's `measure_expression`/`render_expression` reads, and
  * for the pupil's page, a script the browser can be made to throw on.

Each page comes in two variants that differ by exactly one thing — the control
that is 20px tall instead of 44, the question block that is missing, the script
that reads a symbol loaded later — so "the gate found this" is a statement about
that one difference and not about the harness.

Three answers, and the one that matters
---------------------------------------
Every broken-page test asserts **exit 1**, and says out loud what it is not:

  * not **2** — the gate could look, and did,
  * not **0** — a broken page must never be called clean,

and it asserts the sentence it printed names the defect, because a finding with the
wrong text is a finding someone will misread. Each gate also gets its **control**:
the same server, the same command, the intact page, exit 0. Without that half, a
gate that answered 1 to everything would pass this file.

Two pages are here that are *not* broken in a measurable way, and they are pinned
apart from the findings on purpose: a builder page with no `.sg-exam-builder` root
at all, and an exam page that redirects away. Both are honestly exit 2 — the gate
cannot tell a renamed scope from a page that is not the builder, and it cannot
measure a page it was not allowed to open — and the guard is that they stay 2
rather than drifting into either "finding" (which would send a reader to the wrong
problem) or "clean" (which would ship the page).

Where it runs
-------------
`.github/workflows/browser-gate.yml` already finds a browser and fails the job when
there is not one, so this file runs there with `SG_CHROME` set. On a machine with no
Chrome it skips, loudly and by name: the shell's own `SG_CHROME=""` means "there is
no browser here", which is the same switch the gates themselves honour.

The third part, and where its proof lives
-----------------------------------------
`deploy/browser_gate.py` has three parts. This file also simulates a release
without the optional SEB driver, both in process and in the CLI child, so that
absence remains unmeasured and named even when this checkout ships the driver.
The shipped handshake is measured by the browser-gate workflow against the app.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
BROWSER_GATE_PATH = DEPLOY / "browser_gate.py"
TOUCH_GATE_PATH = DEPLOY / "touch_gate.py"
RENDER_GATE_PATH = DEPLOY / "exam_render_gate.py"
SEB_GATE_PATH = DEPLOY / "seb_door_gate.py"

#: A pupil's paper id, in the shape `exam_render_gate.EXAM_LINK_RE` looks for.
EXAM_ID = "3f4c1b2a-9d8e-4f70-8a11-2b3c4d5e6f70"
EXAM_PATH = f"/student/exams/{EXAM_ID}"
BUILDER_PATH = "/teacher/exams/new"

#: The same `email:password` shape the deploy passes its gates. The accounts are
#: stand-ins — this server accepts any credential it is handed — and the domain is
#: reserved, so nothing here can sign anybody into anything real.
TEACHER = "guru@broken-page.test:fixture-password"
STUDENT = "murid@broken-page.test:fixture-password"

CSRF_TOKEN = "broken-page-harness-token"
SESSION_COOKIE = "broken-page-harness-session"

#: The sign-in page, in the shape `touch_gate.sign_in` reads: a CSRF meta tag, and a
#: POST that answers 302. Served as a whole page rather than assembled at the handler,
#: because the whole contract with that function is these two lines.
LOGIN_PAGE = ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
              f'<meta name="csrf-token" content="{CSRF_TOKEN}">'
              "<title>sign in</title></head><body>"
              "<form method='post' action='/auth/login-user'>"
              "<input type='email' name='email'><input type='password' name='password'>"
              "</form></body></html>")


def _load(path: Path, name: str):
    """Import a `deploy/` module by path — `deploy/` is not a package.

    The same helper `tests/unit/test_browser_gate.py` uses, and for the same
    reason: a gate is not importable as a module, and a test that reaches into
    `deploy/` by string path is a test that can name the file it is missing.
    """
    if not path.exists():
        pytest.fail(f"{path.relative_to(ROOT)} does not exist")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _locate_browser():
    """The browser the gates would use, through the gates' own locator.

    `locate_browser` is the single owner of that search (see `deploy/touch_gate.py`
    and the guard in `test_browser_gate.py` that forbids a second copy), and it
    treats an explicit empty `SG_CHROME` as "there is none here".
    """
    return _load(TOUCH_GATE_PATH, "sg_touch_gate_for_can_fail").locate_browser()


BROWSER = _locate_browser()

pytestmark = pytest.mark.skipif(
    BROWSER is None,
    reason=("no Chrome/Chromium on this machine, so no browser gate can be run against a "
            "broken page — set SG_CHROME to a binary, as the browser-gate workflow does"))


@pytest.fixture(scope="module")
def touch_gate():
    return _load(TOUCH_GATE_PATH, "sg_touch_gate_for_can_fail")


@pytest.fixture(scope="module")
def render_gate():
    return _load(RENDER_GATE_PATH, "sg_render_gate_for_can_fail")


@pytest.fixture(scope="module")
def wrapper():
    return _load(BROWSER_GATE_PATH, "sg_browser_gate_for_can_fail")


# ── the pages ────────────────────────────────────────────────────────────────
#
# One function per page, so the two variants of a page are visibly one edit apart.
# Sizes are written into the markup rather than left to the browser's defaults: a
# `<button>` with no style has whatever height Chrome's UA stylesheet gives it, and
# a harness that depended on that would be measuring the browser rather than the
# gate.


def _shell(inner: str, script: str = "") -> str:
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<title>broken-page harness</title></head><body>"
            f"{inner}{script}</body></html>")


def _control(height: int) -> str:
    """One builder control at `height`, 120px wide."""
    return (f"<button type='button' name='save' "
            f"style='width:120px;height:{height}px'>Simpan</button>")


def _text_input() -> str:
    return ("<input type='text' name='title' value='Ujian' "
            "style='width:240px;height:44px'>")


#: The builder as it is when the floor holds: every measured control at 44px.
BUILDER_INTACT = _shell(
    "<div class='sg-exam-builder'><form>" + _text_input() + _control(44) +
    "</form></div>")

#: The same page with exactly one control under the floor. 20px tall and full width,
#: so the axis that failed is unambiguous (the shipped measurement this gate exists
#: for was *42 controls under 40px* on this page).
BUILDER_SHORT_CONTROL = _shell(
    "<div class='sg-exam-builder'><form>" + _text_input() + _control(20) +
    "</form></div>")

#: The root renders, the form inside it does not. Every control is gone but the page
#: is still the builder — which is the state a template error inside the form leaves
#: behind, and the one a 44px measurement has to answer rather than count as zero
#: findings.
BUILDER_WITHOUT_CONTROLS = _shell(
    "<div class='sg-exam-builder'><p>the form did not render</p></div>")

#: The scope is renamed, so this is not the page the floor is measured on. Handled
#: separately from the two above: it is genuinely unmeasurable, not broken.
BUILDER_WITHOUT_THE_ROOT = _shell(
    "<div class='exam-builder'><form>" + _control(20) + "</form></div>")

#: The question-type blob the paper writes into its own page, shaped exactly as the
#: template writes it (`type: ({...})[i] || 'mcq'`). The gate reads the exception
#: question out of the *served document* before it opens the page, so without this
#: there is no answer to "how many exception questions should be on this page" and it
#: answers could-not-measure rather than clean. It is a function, never called: the
#: template's own copy of that line runs inside a loop that defines `i`, and a bare
#: `return` of it here would be the page error the fixtures below are made of.
_TYPE_BLOB = ('<script>function sgQType(i) { '
              'return {type: ({"0": "mcq", "1": "mcq_multi"})[i] || \'mcq\'}.type; '
              '}</script>')


def _opt_row(*, pressed: bool = False) -> str:
    """One question's option row: two letters at 118x40.

    `aria-pressed` is how the gate tells the exception question from the plain one,
    so the plain row must not carry it — and the two rows are built here rather than
    written twice because the gate then requires the exception's row to be the plain
    row beside it field by field. The marker is the only difference it may see.
    """
    mark = " aria-pressed='false'" if pressed else ""
    buttons = "".join(
        f"<button class='opt-btn'{mark} style='width:118px;height:40px'>{letter}</button>"
        for letter in ("A", "B"))
    return f"<div class='opt-row'>{buttons}</div>"


def _question(number: int, *, exception: bool = False) -> str:
    """One question block: its own number label, its one row, and the exception's badge."""
    badge = ("<span class='inline-flex'>Boleh lebih dari satu</span>"
             if exception else "")
    return (f"<div><span>Soal {number}:</span>{badge}"
            f"{_opt_row(pressed=exception)}</div>")


#: The pupil's page as it is when it renders: a question control, the two questions the
#: served blob declares (one with the exception's row and badge), and that blob.
_EXAM_STAGE = ("<div class='exam-stage'>"
               "<button class='q-btn' type='button'>1</button>"
               "<div class='exam-answers'>"
               + _question(1) + _question(2, exception=True) +
               "</div>"
               "</div>"
               + _TYPE_BLOB)

EXAM_INTACT = _shell(_EXAM_STAGE)

#: The blank page of 2026-10-03: the route answers 200 with the whole paper and
#: nothing renders. No question control, no answer block — the finding this gate
#: exists for.
EXAM_BLANK = _shell("<div class='exam-stage'></div>")

#: The same shipped defect with the controls still on the page: a script that reads
#: a symbol another deferred script was supposed to define. The DOM renders, so this
#: is a page error rather than a blank page — and it is the reason the gate reads the
#: browser's own events and not only the DOM.
EXAM_THROWS = _shell(_EXAM_STAGE, "<script>sgExamMedia.start();</script>")

#: A sitting this pupil may not open: the page is not measurable, and the gate must
#: say so rather than judge a login form it was bounced onto.
EXAM_REDIRECTS = ("<!doctype html><html><body>"
                  "<p>please sign in</p></body></html>")


class _Pages(BaseHTTPRequestHandler):
    """The routes the gates dial, each page swappable while the server runs.

    One server on one address for a whole test, with `now()` replacing a page in
    place — the shape `tests/unit/test_perf_gate.py`'s health server uses, and the
    reason a test can go from the intact page to the broken one without the gate
    ever dialling a different host.
    """

    routes: dict = {}
    protocol_version = "HTTP/1.0"

    # ── the pages, by name, so a test says which page it is breaking ──
    def do_GET(self):  # noqa: N802 - http.server's interface
        path = self.path.split("?", 1)[0]
        if path == "/auth/login-user":
            self._send(200, LOGIN_PAGE)
        elif path == "/student/exams":
            # The link is quoted the way `exam_render_gate.EXAM_LINK_RE` looks for it —
            # double quotes, which is what the app's own templates write. A stand-in
            # that quoted it differently would make the gate answer "no sittable demo
            # exam", which is a gap in the harness rather than a fact about the gate.
            self._send(200, _shell(f'<a href="{EXAM_PATH}">Ujian</a>'))
        elif path == BUILDER_PATH:
            self._send(*self.routes.get("builder", (200, BUILDER_INTACT)))
        elif path == EXAM_PATH:
            self._send(*self.routes.get("exam", (200, EXAM_INTACT)))
        else:
            # Everything else, including the handshake page this branch has no door
            # for: a plain 404, which is what a route that was never added answers.
            self._send(404, _shell("<p>not here</p>"))

    def do_POST(self):  # noqa: N802 - http.server's interface
        if self.path.split("?", 1)[0] == "/auth/login-user":
            self._send(302, "", headers=[("Location", "/"),
                                         ("Set-Cookie",
                                          f"access_token={SESSION_COOKIE}; Path=/")])
        else:
            self._send(404, _shell("<p>not here</p>"))

    def _send(self, status, body, *, headers=()):
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def log_message(self, *args):  # noqa: D102 - a gate's run should be quiet
        pass


class _Harness:
    """The running stand-in server: the address, and the page it is serving now."""

    def __init__(self, httpd):
        self._httpd = httpd
        self.base_url = f"http://127.0.0.1:{httpd.server_address[1]}"
        #: What each page was last set to, so a test can prove the page the gate read
        #: is the page it set (`served` is the harness's own record, and the tests
        #: that matter also ask the server over HTTP).
        self.served: dict[str, str] = {}

    def now(self, *, builder=None, exam=None) -> None:
        if builder is not None:
            _Pages.routes["builder"] = (200, builder)
            self.served["builder"] = builder
        if exam is not None:
            _Pages.routes["exam"] = (200, exam)
            self.served["exam"] = exam

    def redirect_away(self) -> None:
        """The exam page answers 302 to the sign-in page instead of the paper."""
        _Pages.routes["exam"] = (302, EXAM_REDIRECTS,
                                 [("Location", "/auth/login-user")])
        self.served["exam"] = "redirect"

    def fetch(self, path: str) -> str:
        """One plain HTTP read of what the server is serving — no browser."""
        import requests

        answer = requests.get(f"{self.base_url}{path}", timeout=10,
                              allow_redirects=False)
        return answer.text

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


@pytest.fixture(scope="module")
def pages():
    """The stand-in pages, serving the intact variants to begin with."""
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Pages)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True,
                     name="sg-broken-pages").start()
    harness = _Harness(httpd)
    harness.now(builder=BUILDER_INTACT, exam=EXAM_INTACT)
    try:
        yield harness
    finally:
        harness.stop()


# ── the three answers, asserted together ─────────────────────────────────────


def _assert_finding(code, lines, *, gate, needle):
    """The gate looked, and what it found is the defect — not a gap, not a pass."""
    text = "\n".join(lines)
    assert code == 1, (
        f"{gate} answered {code} for a page broken in the dimension it measures, and the "
        f"only answers that matter here are 1 (a finding), 2 (could not measure) and 0 "
        f"(clean):\n{text}")
    assert code != 2, f"{gate} could not measure a page it was pointed straight at:\n{text}"
    assert code != 0, f"{gate} called a broken page clean:\n{text}"
    assert "could not measure" not in text.lower(), (
        f"{gate} reported a broken page as a measurement gap, and exit 2 does not stop a "
        f"release:\n{text}")
    assert needle.lower() in text.lower(), (
        f"{gate} found something but did not name it ({needle!r}):\n{text}")


def _assert_clean(code, lines, *, gate):
    """The control: the same server with the intact page is a pass.

    Without this half a gate that answered "finding" to everything would satisfy
    every test above, and this file would be proving nothing at all.
    """
    text = "\n".join(lines)
    assert code == 0, f"{gate} did not call the intact page clean (exit {code}):\n{text}"


def _assert_cannot_measure(code, lines, *, gate, needle):
    """A page that is genuinely unmeasurable says so — it is never a finding or a pass."""
    text = "\n".join(lines)
    assert code == 2, (
        f"{gate} answered {code} for a page it cannot measure, and 2 is the only honest "
        f"answer:\n{text}")
    assert "could not measure" in text.lower(), text
    assert needle.lower() in text.lower(), text


# ── the exam builder's finger floor ──────────────────────────────────────────


class TestTheTouchGateCanFail:
    """`deploy/touch_gate.py`, against a builder page under the 44px floor.

    One width rather than all six: the gate is asked about the same page at every
    width, and a finding is a finding at the first one. The floor, the expression
    and the judging are the shipped ones — `widths` is the gate's own parameter.
    """

    def _run(self, touch_gate, pages):
        return touch_gate.run(pages.base_url, TEACHER, browser=BROWSER,
                              widths=(touch_gate.WIDTHS[0],))

    def test_a_control_under_the_floor_is_a_finding(self, touch_gate, pages):
        pages.now(builder=BUILDER_SHORT_CONTROL)
        code, lines, payload = self._run(touch_gate, pages)
        _assert_finding(code, lines, gate="touch gate", needle="44px finger floor")
        assert payload["findings"], payload
        assert all(item["axis"] == "height" for item in payload["findings"]), payload
        assert payload["measured"] >= 2, (
            "the page carries controls that hold the floor as well as the broken one, so "
            f"a run that measured {payload['measured']} of them is not the page: {payload}")

    def test_the_same_page_at_the_floor_is_clean(self, touch_gate, pages):
        pages.now(builder=BUILDER_INTACT)
        code, lines, payload = self._run(touch_gate, pages)
        _assert_clean(code, lines, gate="touch gate")
        assert payload["findings"] == [], payload
        assert payload["measured"] >= 2, (
            f"the intact page measured {payload['measured']} control(s) — a clean answer "
            f"about a page with nothing on it proves nothing")

    def test_the_page_the_gate_measured_is_the_page_that_was_broken(self, touch_gate,
                                                                    pages):
        """The harness's own premise, without a browser in the way."""
        pages.now(builder=BUILDER_SHORT_CONTROL)
        assert self._run(touch_gate, pages)[0] == touch_gate.EXIT_FINDING
        assert pages.fetch(BUILDER_PATH) == BUILDER_SHORT_CONTROL, (
            "the gate dials /teacher/exams/new, so that is the page the harness must "
            "serve; a break served at another address is a break nobody measured")

    def test_a_builder_whose_form_did_not_render_is_not_a_pass(self, touch_gate, pages):
        """The root is there and every control in it is gone — the page is the builder,
        so this is a broken release and not a page this gate cannot judge.

        This case is the reason the file exists in this shape: the gate answered
        **0** here until this harness served the page, because zero findings and zero
        controls look the same to a rule that only ever counts what it found.
        """
        pages.now(builder=BUILDER_WITHOUT_CONTROLS)
        code, lines, payload = self._run(touch_gate, pages)
        _assert_finding(code, lines, gate="touch gate", needle="no controls in it")
        assert payload["measured"] == 0, payload

    def test_a_page_that_is_not_the_builder_cannot_be_measured(self, touch_gate, pages):
        """The other direction, pinned so the two cannot drift into each other: a page
        whose scope was renamed is not a page the floor applies to, and the gate says
        it could not measure rather than inventing a finding about it."""
        pages.now(builder=BUILDER_WITHOUT_THE_ROOT)
        code, lines, _ = self._run(touch_gate, pages)
        _assert_cannot_measure(code, lines, gate="touch gate",
                               needle="not the exam builder")


# ── the pupil's exam page ────────────────────────────────────────────────────


class TestTheRenderGateCanFail:
    """`deploy/exam_render_gate.py`, against a paper that does not appear.

    The budget is shortened for the two cases that have to wait for it. The
    strictness is not: a page with no question control never grows one, so the
    verdict is the same at three seconds as at twenty-five — what changes is only
    how long a suite spends watching a page that is not going to render.
    """

    @pytest.fixture
    def impatient(self, render_gate, monkeypatch):
        monkeypatch.setattr(render_gate, "RENDER_BUDGET", 3.0)
        monkeypatch.setattr(render_gate, "POLL_EVERY", 0.25)
        return render_gate

    def _run(self, render_gate, pages):
        return render_gate.run(pages.base_url, STUDENT, browser=BROWSER)

    def test_a_blank_exam_page_is_a_finding(self, impatient, pages):
        pages.now(exam=EXAM_BLANK)
        code, lines, payload = self._run(impatient, pages)
        _assert_finding(code, lines, gate="exam render gate",
                        needle="no question controls rendered")
        assert "no answer blocks rendered" in "\n".join(lines), payload

    def test_a_page_error_is_a_finding(self, render_gate, pages):
        """The shipped 2026-10-03 defect: the DOM renders and the script that builds
        it throws, so only the browser knows."""
        pages.now(exam=EXAM_THROWS)
        code, lines, payload = self._run(render_gate, pages)
        _assert_finding(code, lines, gate="exam render gate", needle="page error")
        assert "sgExamMedia" in "\n".join(lines), (
            f"the finding does not carry the browser's own error text: {payload}")

    def test_the_rendered_page_is_clean(self, render_gate, pages):
        pages.now(exam=EXAM_INTACT)
        code, lines, payload = self._run(render_gate, pages)
        _assert_clean(code, lines, gate="exam render gate")
        assert payload["problems"] == [], payload
        assert payload["reading"]["qbtns"] >= 1 and payload["reading"]["answers"] >= 1, (
            f"the control page must actually carry a question and an answer row: {payload}")

    def test_a_page_that_redirects_away_cannot_be_measured(self, impatient, pages):
        """A sitting the pupil may not open is not a rendering regression. The gate
        must not read the sign-in page it was bounced onto as a blank exam — and it
        must not call the redirect a finding either."""
        pages.redirect_away()
        code, lines, _ = self._run(impatient, pages)
        _assert_cannot_measure(code, lines, gate="exam render gate",
                               needle="redirected away")


# ── the wrapper the deploy runs ──────────────────────────────────────────────


class TestTheWrapperCanFail:
    """`deploy/browser_gate.py`, through its command line, as the deploy runs it.

    The two gates above are proved on their own; what is asked here is the question
    the deploy actually asks — one command, several measurements — because the
    wrapper has its own way to lose a finding: a part that answers *unmeasured*
    would make the run exit 2, and a run that exits 2 keeps the release.
    """

    def _cli(self, pages, *, only="touch,render", extra=(), missing_seb=False):
        command = [sys.executable, str(BROWSER_GATE_PATH),
                   "--base-url", pages.base_url, "--teacher", TEACHER,
                   "--student", STUDENT, "--only", only, *extra]
        if missing_seb:
            # Simulate a release without the optional driver in the child process
            # too; whether this checkout ships it must not disable this regression.
            bootstrap = (
                "import sys; "
                f"sys.path.insert(0, {str(DEPLOY)!r}); "
                "import browser_gate; browser_gate._seb_module = lambda: None; "
                "sys.exit(browser_gate.main())"
            )
            command = [sys.executable, "-c", bootstrap, *command[2:]]
        done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=600)
        return done

    def test_a_broken_page_fails_the_run_and_names_the_part(self, pages):
        pages.now(builder=BUILDER_SHORT_CONTROL, exam=EXAM_BLANK)
        done = self._cli(pages)
        text = done.stdout + done.stderr
        _assert_finding(done.returncode, text.splitlines(), gate="browser gate",
                        needle="browser gate: FAILED")
        assert "touch gate: FAILED" in text, text
        assert "exam render gate: FAILED" in text, text
        assert "2 part(s) measured" in text, (
            f"the run must report both parts as measured:\n{text}")

    def test_the_same_command_on_the_intact_pages_is_zero(self, pages):
        pages.now(builder=BUILDER_INTACT, exam=EXAM_INTACT)
        done = self._cli(pages)
        text = done.stdout + done.stderr
        assert done.returncode == 0, (
            f"the same command, the same server, the intact pages: exit {done.returncode}\n"
            f"{text}")
        assert "browser gate: OK" in text, text

    def test_the_reading_it_keeps_says_finding_not_unmeasured(self, pages, tmp_path):
        """`--json-out` is what CI reads to say which part failed, so the file itself
        has to carry the finding — a run that failed with a reading full of
        `unmeasured` sends the reader to the wrong problem."""
        pages.now(builder=BUILDER_SHORT_CONTROL, exam=EXAM_BLANK)
        out = tmp_path / "reading.json"
        done = self._cli(pages, extra=("--json-out", str(out)))
        assert done.returncode == 1, done.stdout + done.stderr
        reading = json.loads(out.read_text(encoding="utf-8"))
        states = {name: part["state"] for name, part in reading["parts"].items()}
        assert states.get("touch") == "finding", states
        assert states.get("render") == "finding", states
        assert "unmeasured" not in states.values(), states

    def test_a_part_this_branch_cannot_drive_is_never_a_pass(self, wrapper, pages, monkeypatch):
        """The handshake half, simulating a release without its optional driver.

        It cannot be driven, and the wrapper's answer to that has to be *unmeasured
        and named* — never a pass, because a part folded into a pass is a part
        nobody will ever notice is missing, and never a finding, because a missing
        driver is not a regression in the release.
        """
        monkeypatch.setattr(wrapper, "_seb_module", lambda: None)
        part = wrapper.seb_part(pages.base_url, STUDENT, EXAM_ID)
        assert part["state"] == wrapper.STATE_UNMEASURED, part
        assert part["state"] not in (wrapper.STATE_OK, wrapper.STATE_FINDING), part
        assert "seb_door_gate.py" in "\n".join(part["lines"]), part

        # ...and through the command line, so the exit code the deploy reads is 2
        # rather than the 0 a "not shipped" part would give.
        done = self._cli(pages, only="seb", extra=("--seb-exam", EXAM_ID), missing_seb=True)
        text = done.stdout + done.stderr
        assert done.returncode == 2, (
            f"a part that cannot be driven answered {done.returncode}:\n{text}")
        assert "seb_door_gate.py" in text, text


class TestTheDeployInvokesThemAsProcesses:
    """The two gates the *runner* calls directly, called the way it calls them.

    `deploy/scangrade-deploy.sh` does not pass flags to these two: it puts the conf
    in the environment (`TOUCH_BASE_URL`/`TOUCH_TEACHER`, `RENDER_BASE_URL`/
    `RENDER_STUDENT`) and runs the file as a process, reading the exit code and the
    `FAILED` lines. That wiring is where a "could not measure" would hide — an
    unread conf is exit 2, not a finding — so it is driven here rather than assumed
    from the in-process calls above. `SG_CHROME` is what the workflow exports for
    the same reason; the box relies on the browser being on `PATH`.
    """

    def _cli(self, script, conf):
        env = dict(os.environ)
        env["SG_CHROME"] = BROWSER
        env.update(conf)
        return subprocess.run([sys.executable, str(script)], capture_output=True,
                              text=True, encoding="utf-8", errors="replace", env=env,
                              timeout=600)

    def test_the_touch_gate_as_the_runner_calls_it(self, pages):
        pages.now(builder=BUILDER_SHORT_CONTROL)
        done = self._cli(TOUCH_GATE_PATH, {"TOUCH_BASE_URL": pages.base_url,
                                           "TOUCH_TEACHER": TEACHER})
        _assert_finding(done.returncode, (done.stdout + done.stderr).splitlines(),
                        gate="touch gate", needle="44px finger floor")

    def test_the_render_gate_as_the_runner_calls_it(self, pages):
        pages.now(exam=EXAM_THROWS)
        done = self._cli(RENDER_GATE_PATH, {"RENDER_BASE_URL": pages.base_url,
                                            "RENDER_STUDENT": STUDENT})
        _assert_finding(done.returncode, (done.stdout + done.stderr).splitlines(),
                        gate="exam render gate", needle="page error")

    def test_the_same_invocations_on_the_intact_pages_are_zero(self, pages):
        pages.now(builder=BUILDER_INTACT, exam=EXAM_INTACT)
        for script, conf in (
            (TOUCH_GATE_PATH, {"TOUCH_BASE_URL": pages.base_url,
                               "TOUCH_TEACHER": TEACHER}),
            (RENDER_GATE_PATH, {"RENDER_BASE_URL": pages.base_url,
                                "RENDER_STUDENT": STUDENT}),
        ):
            done = self._cli(script, conf)
            _assert_clean(done.returncode, (done.stdout + done.stderr).splitlines(),
                          gate=script.name)


class TestTheHarnessItself:
    """What this file must not become: a stub that answers everything, or a control
    that cannot tell the two variants apart."""

    def test_the_pages_differ_by_exactly_what_is_measured(self, pages):
        """The intact and broken builder differ in one control's height, and the two
        pupil's pages in the one thing the render gate reads."""
        assert BUILDER_INTACT != BUILDER_SHORT_CONTROL
        assert "height:44px" in BUILDER_INTACT and "height:20px" in BUILDER_SHORT_CONTROL
        assert "q-btn" in EXAM_INTACT and "exam-answers" in EXAM_INTACT
        assert "q-btn" not in EXAM_BLANK and "exam-answers" not in EXAM_BLANK

    def test_the_login_the_gates_sign_in_with_is_the_app_s_own_shape(self, pages):
        """`touch_gate.sign_in` reads a CSRF meta tag off the form page and treats a
        302 from the POST as a signed-in session. Both halves are served, so the
        gates reach their pages the way they do on the box."""
        body = pages.fetch("/auth/login-user")
        assert f'name="csrf-token" content="{CSRF_TOKEN}"' in body
        assert "/auth/login-user" in body
