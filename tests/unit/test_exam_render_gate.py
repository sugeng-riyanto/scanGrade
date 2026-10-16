"""The pupil's exam page, rendered in a real browser on every release.

A blank exam page is invisible to every server-side gate: the route answers 200
with the whole paper, and the smoke test reads the right panels — yet the page is
white, because the failure is script *order* in the browser. On 2026-10-03
`/student/exams/<id>` shipped blank (a deferred helper Alpine started before, so
`examApp()` threw `ReferenceError: sgExamMedia is not defined`). Every gate passed.

`deploy/exam_render_gate.py` opens the demo exam as the demo pupil in a headless
browser and asks the rendered DOM. These tests are its guards: the exit contract,
the finding for a blank page, the could-not-measure for a redirect, and that the
deploy runner actually runs it. The live render is the gate's own job.

The second half of that reading is the **exception question's row**, and the
data in this file is measured rather than imagined: the two readings below were
taken from a real headless Chrome on 2026-10-11 against `/preview/paper` of an
exam with two exception questions — once from the deployed page (which rendered
**two** option rows for each of them, the plain row above the exception's) and
once after the template was fixed (one row each). The numbers in them are the
browser's: `w=118 h=40`, `font=14px/700`, `pad=8px 20px 8px 20px`,
`border=2px solid 12px`, one `span` child, no icon. A reading of that shape is what
`parity_problems` judges, and what a template read cannot see at all.
"""
from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GATE_PATH = ROOT / "deploy" / "exam_render_gate.py"
DEPLOY_SCRIPT = ROOT / "deploy" / "scangrade-deploy.sh"
INSTALLER = ROOT / "deploy" / "install-auto-deploy.sh"

EXAM_ID = "58285e5e-45dd-4f9c-a39e-f63f67856ba4"
EXAM_PATH = f"/student/exams/{EXAM_ID}"


def gate():
    """Import `deploy/exam_render_gate.py` without making `deploy/` a package."""
    if not GATE_PATH.exists():
        pytest.fail("deploy/exam_render_gate.py does not exist — the page has no browser")
    spec = importlib.util.spec_from_file_location("sg_exam_render_gate", GATE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


#: The question-type blob the paper writes into its own page, shaped exactly as the
#: template writes it (`type: ({...})[i] || 'mcq'`). Two of the three questions are
#: the exception.
SERVED_HTML = (
    '<script>return {type: ({"0": "mcq_multi", "1": "mcq", "2": "mcq_multi"})[i]'
    " || 'mcq'};</script>"
)


def _button(letter, pressed=False, w=118, h=40, kids="span", icons=0,
            pad="8px 20px 8px 20px", font="14px/700", border="2px solid 12px"):
    """One option button, with the values a real Chrome reported for it."""
    return {"letter": letter, "pressed": pressed, "w": w, "h": h, "font": font,
            "pad": pad, "border": border, "display": "block", "kids": kids,
            "icons": icons}


def _row(pressed=False, letters="ABCDE", **overrides):
    """A five-letter option row; `other` is the `x-for` template Alpine leaves in
    the row, which is what the browser reports for both rows on this page."""
    return {"buttons": [_button(letter, pressed=pressed, **overrides)
                        for letter in letters], "other": "template"}


def _block(label, rows, chips):
    return {"index": 1, "label": label, "chips": chips, "rows": rows}


#: The page as it actually rendered **before** the template fix, measured on the
#: deployed release: each exception question handed the pupil two rows, so the
#: question appeared twice — once as a row that replaces a letter, once as one that
#: adds one. The chips read `Terjawab` first because both rows carry their own.
DOUBLED_PAPER = {"blocks": [
    _block("Soal 1:", [_row(), _row(pressed=True)],
           ["Terjawab", "Boleh lebih dari satu", "Terjawab"]),
    _block("Soal 2:", [_row(), _row(pressed=True)],
           ["Terjawab", "Boleh lebih dari satu", "Terjawab"]),
    _block("Soal 3:", [_row()], ["Terjawab"]),
]}

#: The same page after the fix: one row per question, the badge on the exception and
#: on nothing else, and the exception's row byte-for-byte the plain row's.
FIXED_PAPER = {"blocks": [
    _block("Soal 1:", [_row(pressed=True)], ["Boleh lebih dari satu", "Terjawab"]),
    _block("Soal 2:", [_row(pressed=True)], ["Boleh lebih dari satu", "Terjawab"]),
    _block("Soal 3:", [_row()], ["Terjawab"]),
]}


class _FakeSession:
    class _Cookies:
        def get_dict(self):
            return {}

    class _Response:
        status_code = 200
        text = SERVED_HTML

    def get(self, url, **kwargs):
        return self._Response()

    cookies = _Cookies()


# ── the exit contract: pass, a finding, or nothing measurable ────


class TestTheExitContract:
    def test_without_a_browser_it_cannot_measure(self):
        """Exit 2 is "we could not look", and it must not look like a pass."""
        if not GATE_PATH.exists():
            pytest.fail("deploy/exam_render_gate.py does not exist")
        env = dict(os.environ)
        env["SG_CHROME"] = ""
        result = subprocess.run(
            [
                sys.executable,
                str(GATE_PATH),
                "--base-url",
                "http://127.0.0.1:9",
                "--student",
                "s@example.com:pw",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=120,
        )
        assert result.returncode == 2, (
            f"with no browser the gate answered {result.returncode}, not 2\n"
            f"{result.stdout}\n{result.stderr}"
        )
        assert "could not measure" in (result.stdout + result.stderr).lower()


# ── the browser is started where it can actually run ────────────


class TestTheBrowserStartsWhereItCanRun:
    def test_the_render_gate_launches_through_the_shared_helper(self):
        """A browser that never offers a debug target is a gate that never looks.

        The gates run as the checkout owner via `runuser`, whose home need not
        exist (on the VPS `scangrade`'s does not), and Chrome died before reaching
        its debug port because of it. The fix lives in `touch_gate.browser_launch`;
        the render gate must use it too, or it answers "could not measure" on
        every release while its tests stay green.
        """
        src = GATE_PATH.read_text(encoding="utf-8")
        assert "browser_launch(" in src, (
            "the render gate must start the browser with a writable HOME and, as "
            "root, no sandbox — or it can never measure the page"
        )
        assert "from touch_gate import" in src, (
            "browser_launch must come from the shared touch-gate module, so the two "
            "gates cannot drift apart on how a browser is started"
        )
        import_block = src.split("from touch_gate import", 1)[1].split("\n\n", 1)[0]
        assert "browser_launch" in import_block, (
            "the shared browser_launch helper is not imported from touch_gate"
        )


# ── the run contract: a blank page is 1, a rendered page is 0 ─────


class TestTheRunContract:
    """`run()` maps a rendered page to its exit code, without a browser."""

    def _run(self, monkeypatch, reading, events=(), exam_id=EXAM_ID, session=None):
        g = gate()

        async def fake_render(*a, **k):
            return reading, list(events), None

        monkeypatch.setattr(g, "locate_browser", lambda *a, **k: "/fake/chrome")
        monkeypatch.setattr(g, "sign_in",
                            lambda *a, **k: (session or _FakeSession(), None))
        monkeypatch.setattr(g, "demo_exam_id", lambda *a, **k: exam_id)
        monkeypatch.setattr(g, "_render", fake_render)
        return g, g.run("http://box", "s@example.com:pw")

    @staticmethod
    def _rendered(**overrides):
        """A reading of a page that rendered, with its answer rows measured."""
        reading = {"path": EXAM_PATH, "qbtns": 12, "answers": 12, "xcloak": 0,
                   "stageH": 664, "bodyText": "Ujian", "paper": FIXED_PAPER}
        reading.update(overrides)
        return reading

    def test_a_rendered_page_is_zero(self, monkeypatch):
        g, (code, lines, _) = self._run(monkeypatch, self._rendered())
        assert code == g.EXIT_OK, lines

    def test_the_ok_line_reports_what_was_measured(self, monkeypatch):
        """A gate that cannot say what it looked at reads as a pass either way."""
        g, (code, lines, payload) = self._run(monkeypatch, self._rendered())
        assert code == g.EXIT_OK, lines
        assert "2 exception question(s)" in " ".join(lines), lines
        assert payload["declared_exceptions"] == 2
        assert payload["paper"], "the rows are not carried into the record"

    def test_a_blank_page_is_a_finding_and_names_the_error(self, monkeypatch):
        """The exact regression this gate exists for: no questions, and the throw."""
        g, (code, lines, payload) = self._run(
            monkeypatch,
            {"path": EXAM_PATH, "qbtns": 0, "answers": 0, "xcloak": 0,
             "stageH": 34, "bodyText": "Ahmad"},
            events=[
                {
                    "method": "Runtime.exceptionThrown",
                    "params": {
                        "exceptionDetails": {
                            "exception": {
                                "description": "ReferenceError: sgExamMedia is not defined\n    at examApp"
                            }
                        }
                    },
                }
            ],
        )
        assert code == g.EXIT_FINDING, lines
        assert any("sgExamMedia" in line for line in lines), (
            "a finding must name the error an operator has to fix"
        )
        assert payload["problems"], "a finding must carry its reasons"

    def test_a_redirect_away_is_could_not_measure(self, monkeypatch):
        """A refused sitting is a page we did not get to look at, not a regression."""
        g, (code, lines, _) = self._run(
            monkeypatch,
            {"path": "/student/exams", "qbtns": 0, "answers": 0, "xcloak": 0,
             "stageH": 0, "bodyText": ""},
        )
        assert code == g.EXIT_CANNOT_MEASURE, lines

    def test_no_demo_exam_is_could_not_measure(self, monkeypatch):
        g, (code, lines, _) = self._run(
            monkeypatch,
            self._rendered(bodyText=""),
            exam_id=None,
        )
        assert code == g.EXIT_CANNOT_MEASURE, lines

    def test_the_page_that_shows_the_exception_twice_is_a_finding(self, monkeypatch):
        """The defect this half of the gate was written for, from the real page."""
        g, (code, lines, payload) = self._run(
            monkeypatch, self._rendered(paper=DOUBLED_PAPER))
        assert code == g.EXIT_FINDING, lines
        assert any("rendered 2 answer rows" in line for line in lines), lines
        assert any("Soal 1" in line for line in lines), (
            "the finding must name the question, or an operator has to hunt for it")
        assert payload["problems"], "a finding must carry its reasons"

    def test_a_served_paper_with_no_exception_is_could_not_measure(self, monkeypatch):
        """Stale demo data is a box fact: re-seeding is the remedy, not a rollback.

        The *data* is what is read here — the served document's own question types —
        because that is the fact the box controls. The page rendering no exception
        row while the data carries one is a finding, and is asserted above.
        """

        class _NoException(_FakeSession):
            class _Response:
                status_code = 200
                text = 'type: ({"0": "mcq", "1": "mcq"})[i] || \'mcq\''

            def get(self, url, **kwargs):
                return self._Response()

        g, (code, lines, payload) = self._run(
            monkeypatch,
            self._rendered(paper={"blocks": [
                _block("Soal 1:", [_row()], ["Terjawab"])]}),
            session=_NoException())
        assert code == g.EXIT_CANNOT_MEASURE, lines
        assert "manage.py demo-exam" in " ".join(lines), (
            "the remedy has to be in the message")
        assert payload["reason"] == "no-exception-in-the-paper"

    def test_the_data_and_the_render_disagreeing_is_a_finding(self, monkeypatch):
        """The paper says two exception questions and the page shows neither.

        This is the shape the vocabulary guard exists for: `qIsMulti` comparing
        against a name the data does not hold renders a plain row for an exception
        question, and only the two readings together can say so.
        """
        g, (code, lines, _) = self._run(
            monkeypatch,
            self._rendered(paper={"blocks": [
                _block("Soal 1:", [_row()], ["Terjawab"]),
                _block("Soal 2:", [_row()], ["Terjawab"])]}))
        assert code == g.EXIT_FINDING, lines
        assert any("rendered 0 of them" in line for line in lines), lines

    def test_a_served_paper_without_its_question_types_is_could_not_measure(
            self, monkeypatch):
        class _NoTypes(_FakeSession):
            class _Response:
                status_code = 200
                text = "<html>nothing to read here</html>"

            def get(self, url, **kwargs):
                return self._Response()

        g, (code, lines, _) = self._run(monkeypatch, self._rendered(),
                                        session=_NoTypes())
        assert code == g.EXIT_CANNOT_MEASURE, lines
        assert "question types" in " ".join(lines)

    def test_a_release_that_lost_the_fixtures_exception_is_a_finding(self, monkeypatch):
        """The fixture is this release's file: no exception on it, no subject here.

        A finding rather than a quiet skip, for the reason the theme gate refuses a
        deleted check — the exception's row would stop being measured by anything.
        """
        g = gate()
        stub = type("Spec", (), {"QUESTION_TYPES": {"0": "mcq", "1": "true_false"}})
        monkeypatch.setattr(g, "fixture_spec", lambda: stub)

        async def fake_render(*a, **k):
            return self._rendered(), [], None

        monkeypatch.setattr(g, "locate_browser", lambda *a, **k: "/fake/chrome")
        monkeypatch.setattr(g, "sign_in", lambda *a, **k: (_FakeSession(), None))
        monkeypatch.setattr(g, "demo_exam_id", lambda *a, **k: EXAM_ID)
        monkeypatch.setattr(g, "_render", fake_render)
        code, lines, payload = g.run("http://box", "s@example.com:pw")
        assert code == g.EXIT_FINDING, lines
        assert payload["reason"] == "fixture-declares-no-exception"
        assert any("demo fixture" in line for line in lines), lines


# ── reading the page errors out of the CDP stream ────────────────


class TestExceptionReading:
    def test_it_names_the_thrown_error(self):
        events = [
            {"method": "Page.loadEventFired", "params": {}},
            {
                "method": "Runtime.exceptionThrown",
                "params": {
                    "exceptionDetails": {
                        "exception": {"description": "ReferenceError: x is not defined\n stack"}
                    }
                },
            },
        ]
        out = gate()._exceptions(events)
        assert out and out[0].startswith("ReferenceError: x is not defined")

    def test_it_catches_console_errors_too(self):
        events = [
            {
                "method": "Runtime.consoleAPICalled",
                "params": {"type": "error", "args": [{"value": "boom"}]},
            }
        ]
        assert gate()._exceptions(events) == ["console.error: boom"]

    def test_a_clean_stream_is_empty(self):
        assert gate()._exceptions([{"method": "Page.loadEventFired", "params": {}}]) == []


# ── the deploy runs it on every release ──────────────────────────


def _render_case_arm(code, *, body_only=True):
    """One arm of the runner's `case "$RENDER_RC"`, read out of the runner.

    Scoped to the arm on purpose: the exit-1 arm already carries `RENDER_ENFORCE`,
    so a whole-file reading of the same question is satisfied while the exit-2 arm
    is unconditional again — the check and the defect would agree with each other.
    """
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    body = script.split('case "$RENDER_RC" in', 1)[1].split("\n  esac", 1)[0]
    # Split on the *labels* (`    *)`) and not on every four-space indent: an arm's
    # body is indented six spaces, so a plain split cuts it off from its own label.
    for chunk in re.split(r"\n    (?=\S+\))", body):
        if chunk.startswith(f"{code})"):
            arm = chunk.split("\n", 1)[1]
            # A `case` arm's `;;` terminates it rather than belonging to it, and
            # the executed half below splices the body into a plain script.
            stripped = arm.rstrip()
            if stripped.endswith(";;"):
                arm = stripped[:-2]
            return arm if body_only else chunk
    raise AssertionError(f"the render gate's case has no {code}) arm")


def _run_render_arm(enforce):
    """Run the real exit-2 arm under bash, with a stub `log`, and report the box.

    Written to a file rather than passed through `bash -c`: the arm's own copy
    carries an em dash, and this suite already learned (in `test_caller_armament`)
    that the Windows command line mangles exactly that, so a failure would show up
    as a comparison against damaged text rather than as behaviour.
    """
    script = (
        "log () { printf '%s\\n' \"$*\"; }\n"
        "HEALTHY=1\n"
        "BEFORE=before123\n"
        "AFTER=after456\n"
        "SMOKE_CONF=/etc/scangrade-smoke.conf\n"
        f"RENDER_ENFORCE={enforce}\n"
        "RENDER_RC=2\n"
        "RENDER_OUT='exam render gate: could not measure — no browser found'\n"
        + _render_case_arm(2)
        + "\nprintf 'HEALTHY=%s REASON=%s\\n' \"$HEALTHY\" \"${FAIL_REASON:-none}\"\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "arm.sh"
        path.write_text(script, encoding="utf-8")
        result = subprocess.run(["bash", str(path)], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=60)
    return result.stdout + result.stderr


class TestTheDeployRunsIt:
    def test_the_runner_invokes_the_gate(self):
        script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
        assert "deploy/exam_render_gate.py" in script, (
            "the render gate exists but no release runs it — the exam page can go "
            "blank silently again"
        )

    def test_the_runner_reads_a_finding_and_a_could_not_measure(self):
        script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
        block = script.split("exam render gate", 1)[-1]
        assert re.search(r"\bexam render gate\b", script), "no render gate block"
        assert "exit 2" in block, "the runner must name the could-not-measure exit"
        assert "RENDER_ENFORCE" in script, (
            "a finding must roll back only when the box is armed to enforce it, "
            "the way the smoke and touch gates are"
        )

    def test_a_regression_rolls_back_when_enforced(self):
        script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
        marker = 'if [ "${RENDER_ENFORCE:-false}" = "true" ]'
        assert marker in script, (
            "the finding arm must be gated on RENDER_ENFORCE, so a box that cannot "
            "run the gate cannot reject good releases"
        )

    def test_the_could_not_measure_arm_refuses_on_an_enforcing_box(self):
        """Exit 2 used to keep the release, and that is how the gate stopped guarding.

        Every cause of exit 2 — no browser, no credentials, no sittable demo exam, a
        sign-in that failed — means the page was never looked at, and the journal
        carries what keeping the release cost: `could not measure (exit 2)` followed
        by `DEPLOY OK`. On a box that armed the gate the release is unverified and
        must not be kept, through the same rollback path a blank page takes.

        Asserted against the **2) arm itself**, not the file: the exit-1 arm already
        says `RENDER_ENFORCE`, so a whole-file check would pass with this arm made
        unconditional again — which is the defect, not a detail of it.
        """
        arm = _render_case_arm(2, body_only=True)
        assert "RENDER_ENFORCE" in arm, (
            "the could-not-measure arm is not gated on the box's own enforcement "
            "switch, so it would either block a box with no browser or keep an "
            "unverified release"
        )
        assert "HEALTHY=0" in arm and "FAIL_REASON=" in arm, (
            "the could-not-measure arm names the switch but does not refuse: the "
            "shared rollback path is only entered through HEALTHY/FAIL_REASON"
        )
        assert "$RENDER_OUT" in arm, (
            "the refusal must carry the gate's own reading, or the journal says "
            "'unverified' without saying why"
        )

    def test_both_answers_of_that_arm_are_what_they_claim(self):
        """Run the real arm under bash, with the box armed and with it not.

        The source assertions above can be satisfied by a body that logs the right
        words and keeps the release anyway, so the two outcomes are measured: an
        enforcing box ends unhealthy with a reason, and a box that never armed the
        gate keeps the release and says so.
        """
        armed = _run_render_arm("true")
        assert "HEALTHY=0" in armed, (
            f"an enforcing box kept an unverified release\n{armed}")
        assert "could not measure" in armed, (
            f"the refusal does not say what could not be measured\n{armed}")
        assert "rolling back" in armed, armed

        unarmed = _run_render_arm("false")
        assert "HEALTHY=1" in unarmed, (
            f"a box that never armed the gate rolled a release back for a reading "
            f"it cannot take — every release would be refused\n{unarmed}")
        assert "could not measure" in unarmed, (
            f"the kept release must still say the page was not looked at\n{unarmed}")


# ── the installer arms it, on the same proving rule as the others ─


class TestTheInstallerArmsIt:
    """A gate nobody armed is a gate that reports and carries on. The installer is
    where the smoke, claims and performance gates are armed — after a real
    measurement passes — so the browser gates belong there too."""

    def test_the_installer_runs_both_browser_gates(self):
        text = INSTALLER.read_text(encoding="utf-8")
        assert "deploy/touch_gate.py" in text and "deploy/exam_render_gate.py" in text, (
            "the installer never measures the DOM gates, so it cannot arm them"
        )

    def test_it_arms_only_after_a_clean_measurement(self):
        """The `sed` that sets RENDER_ENFORCE=true must sit in the exit-0 branch of
        the render gate's council, not beside the refusal — a gate armed against a
        broken measurement rejects good releases."""
        text = INSTALLER.read_text(encoding="utf-8")
        arm = text.index('s/^RENDER_ENFORCE=.*/RENDER_ENFORCE=\"true\"/')
        # The nearest `case "$RENDER_RC"` above the arming line has a `0)` as its
        # first arm, and the arming line is after it.
        block = text[:arm]
        assert 'case "$RENDER_RC" in' in block, "RENDER_ENFORCE is set outside its council"
        assert 'RENDER_ENFORCE stays false' in text[arm:], (
            "the installer arms the gate with no branch that leaves it unarmed"
        )

    def test_it_says_how_to_get_a_browser(self):
        text = INSTALLER.read_text(encoding="utf-8")
        assert "no Chrome/Chromium found" in text and "apt-get install -y chromium" in text, (
            "a box with no browser is armed with a script that says nothing about it"
        )


# ── it polls for the page, it does not read it once ──────────────────────


class TestItPollsForThePageInsteadOfSleepingOnce:
    """A single fixed sleep read the page once, and under load read it blank.

    On 2026-10-04 the deploy quarantined a release on *"no question controls
    rendered"* while the very same gate run by hand answered `qbtns: 5,
    answers: 5`. The read simply happened too early: one sleep, no re-check, and
    a healthy release was rolled back for it. The gate must keep looking until
    the questions are in the DOM — or the budget runs out.
    """

    def test_the_render_waits_for_the_questions(self):
        src = GATE_PATH.read_text(encoding="utf-8")
        body = src.split("async def _render(", 1)[1]
        assert re.search(r"while True:", body), (
            "the render is not polled, so a fixed sleep is still the only wait — a "
            "slow render is reported as a blank exam")
        assert "qbtns" in body and "break" in body, (
            "the poll never stops on the questions appearing")

    def test_it_does_not_rely_on_a_fixed_settle(self):
        src = GATE_PATH.read_text(encoding="utf-8")
        assert "SETTLE_SECONDS" not in src, (
            "a fixed settle is still the readiness signal")

    def test_a_blank_page_still_exhausts_and_reports(self):
        """Polling must not become a pass: the finding path is unchanged.

        A real regression renders nothing, so it exhausts the budget and is still
        exit 1 — the strictness is the same, only the flake is gone.
        """
        src = GATE_PATH.read_text(encoding="utf-8")
        assert "RENDER_BUDGET" in src, "the poll has no bound, so a blank page hangs"
        assert "EXIT_FINDING" in src and "problems.append" in src

    def test_the_reading_survives_a_document_that_is_not_built(self):
        """The poll's first look can land before `document.body` exists.

        Reading `.innerText` off a null body threw `TypeError: Cannot read
        properties of null`, the gate answered "could not measure" (exit 2) — and a
        run that answers could-not-measure is a release in which nothing looked at
        the page, which the runner now refuses on an enforcing box. The read must
        be null-safe, or the gate reports its own crash as an unmeasured release.
        """
        src = GATE_PATH.read_text(encoding="utf-8")
        assert "d.body && d.body.innerText" in src, (
            "the reading dereferences document.body, which is null until the "
            "parser has built it")

    def test_the_rows_are_read_in_the_same_poll(self):
        """The option rows come from Alpine's own `x-if`/`x-for`, so a block being
        in the DOM is not yet a row in it: reading the rows once, outside the poll,
        is how a slow page reports "no answer row rendered".
        """
        src = GATE_PATH.read_text(encoding="utf-8")
        body = src.split("async def _render(", 1)[1]
        assert "read_once(c, value)" in body, (
            "the render does not read the rows through the one poll, so the reading "
            "depends on the page settling by luck")
        assert "readiness(value)" in body, "the poll has no readiness test"


class TestOnePollReadsThePageAndItsRows:
    """`read_once` and `readiness`, driven without a browser.

    Both exist so this decision is testable: a reading of the rows that is taken
    once (outside the poll), or thrown away after being taken, is a gate that
    reports a correct page as an unmeasured one — and no source assertion caught
    that, because the line that did the work was still in the file.
    """

    class _CDP:
        """A stand-in for `_CDP`: canned answers, keyed by the expression sent."""

        def __init__(self, page, paper):
            self.page, self.paper = page, paper
            self.sent = []

        async def send(self, method, **params):
            self.sent.append(params.get("expression", ""))
            if params.get("expression") == gate().paper_expression():
                return self.paper
            return self.page

    def _drive(self, page, paper):
        import asyncio

        value = {}
        cdp = self._CDP(page, paper)
        threw = asyncio.run(gate().read_once(cdp, value))
        return threw, value, cdp

    def test_it_adopts_the_page_and_the_rows(self):
        threw, value, cdp = self._drive(
            {"result": {"value": {"qbtns": 12, "answers": 12}}},
            {"result": {"value": {"blocks": [{"label": "Soal 1:", "rows": []}]}}},
        )
        assert threw is None
        assert value["qbtns"] == 12 and value["answers"] == 12
        assert value["paper"]["blocks"], (
            "the rows were read and not kept, so the run answers \"could not measure\" "
            "about a page it has just looked at")
        assert len(cdp.sent) == 2, "the rows are not read in the same pass as the page"

    def test_a_read_that_threw_is_not_a_verdict(self):
        threw, value, _ = self._drive({"exceptionDetails": {"text": "Execution context"}},
                                      {"result": {"value": {"blocks": []}}})
        assert threw, "a page read that threw was taken as a reading"
        assert "paper" not in value

    def test_a_row_reading_that_errors_is_named(self):
        threw, value, _ = self._drive(
            {"result": {"value": {"qbtns": 12, "answers": 12}}},
            {"result": {"value": {"error": "no .exam-answers on the page"}}})
        assert threw is None
        assert value["paper_error"] == "no .exam-answers on the page", (
            "an unreadable row reading must be distinguishable from an absent one")
        assert not value.get("paper")

    def test_readiness_needs_the_rows_too(self):
        """A page whose blocks are there and whose rows are not is not ready."""
        g = gate()
        assert g.readiness({"qbtns": 12, "answers": 12}) is False, (
            "the poll stops before the rows exist, so a slow page reads as one with "
            "no option row")
        assert g.readiness({"qbtns": 1, "answers": 1, "paper": {"blocks": []}}) is True
        assert g.readiness({"qbtns": 0, "answers": 1, "paper": {"blocks": []}}) is False


# ── the exception's row: what a lay-out says that a template cannot ────────


class TestTheExceptionRowIsMeasured:
    """`parity_problems` on readings taken from a real Chrome.

    The two papers at the top of this file are the same exam before and after the
    fix, and the assertions below are the ones that would have caught it: the
    pre-fix page renders two rows for one question, and nothing about reading the
    template shows that.
    """

    def test_the_shipped_defect_was_a_finding(self):
        problems = gate().parity_problems(DOUBLED_PAPER, 2)
        assert len([p for p in problems if "rendered 2 answer rows" in p]) == 2, problems
        assert any("Soal 1" in p for p in problems) and any("Soal 2" in p for p in problems)

    def test_the_fixed_page_is_clean(self):
        assert gate().parity_problems(FIXED_PAPER, 2) == []

    def test_the_count_has_to_match_the_papers_own_data(self):
        problems = gate().parity_problems(FIXED_PAPER, 1)
        assert any("data carries 1 exception question(s)" in p for p in problems), problems

    def test_a_badge_less_exception_is_a_finding(self):
        paper = {"blocks": [
            _block("Soal 1:", [_row(pressed=True)], ["Terjawab"]),
            _block("Soal 2:", [_row()], ["Terjawab"]),
        ]}
        problems = gate().parity_problems(paper, 1)
        assert any("carries 0 badge(s)" in p for p in problems), problems

    def test_a_badge_on_a_single_answer_question_is_a_finding(self):
        """The badge's *other* half: it marks the exception and nothing else."""
        paper = {"blocks": [
            _block("Soal 1:", [_row(pressed=True)], ["Boleh lebih dari satu", "Terjawab"]),
            _block("Soal 2:", [_row()], ["Boleh lebih dari satu", "Terjawab"]),
        ]}
        problems = gate().parity_problems(paper, 1)
        assert any("although its data is one answer" in p for p in problems), problems

    def test_the_old_checkbox_control_is_a_finding(self):
        """The row that first shipped had an icon inside each button."""
        paper = {"blocks": [
            _block("Soal 1:", [_row(pressed=True, kids="i,span", icons=1)],
                   ["Boleh lebih dari satu", "Terjawab"]),
            _block("Soal 2:", [_row()], ["Terjawab"]),
        ]}
        problems = gate().parity_problems(paper, 1)
        assert any("icon/control element(s)" in p for p in problems), problems
        assert any("kids" in p for p in problems), (
            "a different child list is the same finding, and must be named")

    def test_a_different_control_size_is_a_finding_when_both_are_on_screen(self):
        paper = {"blocks": [
            _block("Soal 1:", [_row(pressed=True, w=132, h=44)],
                   ["Boleh lebih dari satu", "Terjawab"]),
            _block("Soal 2:", [_row()], ["Terjawab"]),
        ]}
        problems = gate().parity_problems(paper, 1)
        assert any("differs from the single-answer row's in w, h" in p for p in problems), problems

    def test_a_hidden_row_is_not_a_size_finding(self):
        """The false positive this reading was corrected for.

        This paper lays out **one question at a time**, so every other row measures
        0x0 while its computed font, padding, border and children are intact. The
        first version compared those zeroes and reported five size findings per
        exception question on a page that was correct.
        """
        paper = {"blocks": [
            _block("Soal 1:", [_row(pressed=True, w=118, h=40)],
                   ["Boleh lebih dari satu", "Terjawab"]),
            _block("Soal 2:", [_row(w=0, h=0)], ["Terjawab"]),
        ]}
        assert gate().parity_problems(paper, 1) == [], (
            "a row that is not laid out has no size to compare, and comparing its "
            "zeroes reports a defect that is not there")

    def test_two_sizes_inside_one_row_is_a_finding(self):
        """On screen, the row's own buttons must agree about their size."""
        buttons = [_button(letter, pressed=True, w=118) for letter in "ABCD"]
        buttons.append(_button("E", pressed=True, w=90))
        paper = {"blocks": [
            _block("Soal 1:", [{"buttons": buttons, "other": "template"}],
                   ["Boleh lebih dari satu", "Terjawab"]),
            _block("Soal 2:", [_row()], ["Terjawab"]),
        ]}
        problems = gate().parity_problems(paper, 1)
        assert any("not one size" in p for p in problems), problems

    def test_the_two_rows_have_to_be_numbered_alike(self):
        paper = {"blocks": [
            _block("Question 1:", [_row(pressed=True)],
                   ["Boleh lebih dari satu", "Terjawab"]),
            _block("Soal 2:", [_row()], ["Terjawab"]),
        ]}
        problems = gate().parity_problems(paper, 1)
        assert any("number label does not read like" in p for p in problems), problems

    def test_a_page_with_no_option_row_at_all_is_a_finding(self):
        assert gate().parity_problems({"blocks": []}, 1), (
            "a page with no row is not a page whose row is fine")


# ── the data: what the paper says it carries ────────────────────────────────


class TestThePapersOwnData:
    def test_it_reads_the_types_out_of_the_served_document(self):
        assert gate().declared_exceptions(SERVED_HTML) == 2

    def test_a_document_without_the_types_is_not_a_zero(self):
        """``None`` and ``0`` are different answers, and the caller runs them
        differently: no exception on the paper is "could not measure", no question
        types in the document is also "could not measure" but for another reason.
        """
        assert gate().declared_exceptions("<html></html>") is None
        assert gate().declared_exceptions('<script>type: (not json)[i]</script>') is None
        assert gate().declared_exceptions('type: ({"0": "mcq"})[i]') == 0
        # A blob of another shape is a page this gate does not recognise — not a
        # paper with no exception question on it, which is what `0` would say.
        assert gate().declared_exceptions('type: (["A", "B"])[i]') is None
        assert gate().declared_exceptions('type: ("mcq_multi")[i]') is None

    def test_it_counts_the_stored_name_and_not_an_alias(self):
        assert gate().declared_exceptions('type: ({"0": "mcq_multi"})[i]') == 1
        assert gate().declared_exceptions('type: ({"0": "mcq"})[i]') == 0


class TestTheExceptionIsOneNameInThreePlaces:
    """The type on the fixture, in the app and in this gate are one string.

    A gate that looks for the wrong spelling answers "this paper has no exception
    question" about a paper with two — the same shape as the class of defect the
    template's own `SG_QT.multi` exists to prevent.
    """

    def test_the_gate_looks_for_the_type_the_app_stores(self):
        from app.services import question_types

        assert gate().MULTI_TYPE == question_types.MCQ_MULTI
        # `q_vocabulary` is the same object under the name the template calls
        # (app/__init__.py registers it as a Jinja global), which is what the page's
        # `SG_QT.multi` — and so `qIsMulti` — is built from.
        assert question_types.vocabulary()["multi"] == gate().MULTI_TYPE

    def test_the_fixture_declares_one_and_the_gate_can_see_it(self):
        spec = gate().fixture_spec()
        assert spec is not None, "the demo fixture is not loadable by the gate"
        assert gate().declares_exception(spec.QUESTION_TYPES) == 1, (
            "the fixture must carry exactly one exception question, or the gate has "
            "nothing measurable on the paper it opens")
        keys = [key for key, value in spec.ANSWER_KEY.items()
                if value == ["A", "D"]]
        assert keys == ["2"], (
            "the exception's key is a list of the letters that are right")
        assert str(spec.QUESTION_TYPES["2"]).lower() == gate().MULTI_TYPE


# ── the gate measures the paper the fixture describes ──────────────────────


class TestItOpensTheFixturePaper:
    """Not merely the first exam on the pupil's list.

    A pupil's list can legitimately offer a teacher's paper first, and one with no
    exception question on it would read as "could not measure" while a perfectly
    measurable paper sat below it.
    """

    class _Listing:
        status_code = 200

        def __init__(self, html):
            self.text = html

    def _session(self, *cards):
        html = "".join(
            f'<div class="card"><h3>{title}</h3>'
            f'<a href="/student/exams/{exam_id}">Mulai</a></div>'
            for exam_id, title in cards)

        class _S(_FakeSession):
            def get(self, url, **kwargs):
                return TestItOpensTheFixturePaper._Listing(html)

        return _S()

    def test_it_prefers_the_fixture_card(self):
        g = gate()
        spec = g.fixture_spec()
        other = "11111111-1111-1111-1111-111111111111"
        fixture = "22222222-2222-2222-2222-222222222222"
        session = self._session((other, "Ulangan Harian Biologi"), (fixture, spec.TITLE))
        assert g.demo_exam_id(session, "http://box") == fixture, (
            "the gate measured whichever paper came first, so a teacher's exam "
            "without the exception made the row unmeasurable")

    def test_it_still_opens_the_first_card_when_the_fixture_is_absent(self):
        g = gate()
        other = "11111111-1111-1111-1111-111111111111"
        session = self._session((other, "Ulangan Harian Biologi"))
        assert g.demo_exam_id(session, "http://box") == other

    def test_a_list_with_no_card_is_no_exam(self):
        session = self._session()
        assert gate().demo_exam_id(session, "http://box") is None


# ── the gate and the templates beat in step ────────────────────────────────


class TestTheGateAndTheTemplates:
    """What the gate asserts is what the template renders, held where it is written.

    The badge's two halves and the exception's exclusion are literals in two files;
    these are the assertions that fail when one of them moves without the other.
    """

    TEMPLATE = (ROOT / "app" / "templates" / "student" / "take_exam.html").read_text(
        encoding="utf-8")

    def test_the_badge_is_the_pair_the_template_renders(self):
        g = gate()
        lines = [line for line in self.TEMPLATE.splitlines() if g.BADGE_COPY[0] in line]
        assert len(lines) == 1, (
            f"expected exactly one line carrying the badge's Indonesian half, found "
            f"{len(lines)} — the gate's copy and the page's have drifted")
        assert f"t('{g.BADGE_COPY[0]}','{g.BADGE_COPY[1]}')" in lines[0], (
            "the badge's English half is not the one this gate looks for, so a "
            "translated page would read as a page with no badge")

    def test_the_plain_row_excludes_the_exception(self):
        """The fast half of the duplicate-row guard; the browser is the real one.

        Two sibling `x-if` templates whose conditions both match rendered the same
        question twice, and `qKind` is `'choice'` for the exception — so the plain
        row's condition has to name the exception it is not.
        """
        assert 'x-if="qKind(i) === \'choice\' && !qIsMulti(i)"' in self.TEMPLATE, (
            "the single-answer row does not exclude the exception, so an exception "
            "question renders both rows — one question, two answers")

    def test_the_pupil_door_declares_the_preview_flag_before_it_is_read(self):
        """The three page errors the pupil door carried, and why they mattered.

        `previewMode` was only ever *assigned*, in the preview's own branch, while
        the submit button reads it — so on a real sitting all three reads threw
        `ReferenceError`, the button's label and `:disabled` binding were left
        unbound, and the exception gate (which reads page errors) could not pass.
        """
        assert "previewMode: false" in self.TEMPLATE, (
            "the flag the template reads is not declared in the component's state, "
            "so the pupil's door throws on every load")
