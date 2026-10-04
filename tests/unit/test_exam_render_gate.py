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
"""
from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
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


class _FakeSession:
    class _Cookies:
        def get_dict(self):
            return {}

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

    def _run(self, monkeypatch, reading, events=(), exam_id=EXAM_ID):
        g = gate()

        async def fake_render(*a, **k):
            return reading, list(events), None

        monkeypatch.setattr(g, "locate_browser", lambda *a, **k: "/fake/chrome")
        monkeypatch.setattr(g, "sign_in", lambda *a, **k: (_FakeSession(), None))
        monkeypatch.setattr(g, "demo_exam_id", lambda *a, **k: exam_id)
        monkeypatch.setattr(g, "_render", fake_render)
        return g, g.run("http://box", "s@example.com:pw")

    def test_a_rendered_page_is_zero(self, monkeypatch):
        g, (code, lines, _) = self._run(
            monkeypatch,
            {"path": EXAM_PATH, "qbtns": 12, "answers": 12, "xcloak": 0,
             "stageH": 664, "bodyText": "Ujian"},
        )
        assert code == g.EXIT_OK, lines

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
            {"path": EXAM_PATH, "qbtns": 12, "answers": 12, "xcloak": 0,
             "stageH": 664, "bodyText": ""},
            exam_id=None,
        )
        assert code == g.EXIT_CANNOT_MEASURE, lines


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
