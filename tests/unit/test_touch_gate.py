"""The exam builder's finger floor, measured on a real browser on every release.

The floor itself is a stylesheet rule (`app/static/css/theme.css`, the
`@media (pointer: coarse)` block that names `.sg-exam-builder`), and it was put
there after a measurement: **42 controls under 40px** at every tablet width the
page was opened at, and **0** after the rule. That measurement was made by hand,
in headless Chrome, once. Two things follow from that and both are the reason this
file exists:

* A stylesheet rule can be edited, renamed, or dropped, and nothing on the box
  lays the page out to notice. The rule is a promise about *laid-out* geometry,
  so only a browser can keep it.
* So `deploy/touch_gate.py` drives a real headless browser at the documented
  tablet widths, measures every control the rule names, and refuses a release
  whose page is back under the floor. These tests are its guards: the floor and
  the widths are pinned, the gate's selector list is held equal to the stylesheet
  block it is supposed to match, the geometry rule is exercised on synthetic
  boxes, and the deploy runner is checked to actually run it.

What these tests cannot do is lay the page out. The live measurement is the
gate's own job (`deploy/touch_gate.py`), and it is what runs on every release.
"""

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GATE_PATH = ROOT / "deploy" / "touch_gate.py"
DEPLOY_SCRIPT = ROOT / "deploy" / "scangrade-deploy.sh"
THEME_CSS = ROOT / "app" / "static" / "css" / "theme.css"


def gate():
    """Import `deploy/touch_gate.py` without making `deploy/` a package."""
    if not GATE_PATH.exists():
        pytest.fail("deploy/touch_gate.py does not exist — the floor has no browser")
    spec = importlib.util.spec_from_file_location("sg_touch_gate", GATE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def stylesheet_scope_selectors() -> set:
    """The selector tails the stylesheet names under `.sg-exam-builder`.

    Read from the stylesheet rather than restated, so the gate's own list and the
    rule it claims to measure cannot drift apart: rename a selector in one and
    this goes red.
    """
    css = THEME_CSS.read_text(encoding="utf-8")
    # Comments blanked — the block's own prose names the selectors too.
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return {
        m.strip() for m in re.findall(r"\.sg-exam-builder\s+([^,{}]+?)\s*(?=[,{])", css)
    }


# ── the floor, the widths and the page are pinned ────────────────


class TestTheFloorIsPinned:
    def test_the_floor_is_forty_four_pixels(self):
        assert gate().FLOOR == 44, (
            "the finger floor moved — every measured control on this page is a "
            "thumb, and the stylesheet's own rule is 44px in both axes"
        )

    def test_the_gate_measures_the_exam_builder(self):
        g = gate()
        assert (
            g.PAGE == "/teacher/exams/new"
        ), "the gate must measure the page a teacher writes a paper on"

    def test_the_gate_measures_the_documented_tablet_widths(self):
        g = gate()
        widths = set(g.WIDTHS)
        documented = {
            (820, 1180),
            (1180, 820),
            (800, 1280),
            (1280, 800),
            (1024, 768),
            (768, 1024),
        }
        assert documented <= widths, (
            "the gate dropped a width the floor was measured at; the stylesheet "
            f"comment names {sorted(documented)}"
        )

    def test_both_axes_are_measured(self):
        """`min-width` exists because some controls are icon-only."""
        g = gate()
        under = g.findings(
            [{"selector": "button", "w": 12, "h": 60, "display": "inline-block"}]
        )
        assert under and under[0]["axis"] == "width", (
            "a 12x60px control is under the floor on the narrow axis and must "
            "be a finding — a height-only check misses icon-only buttons"
        )


# ── the selector list is the stylesheet's list ───────────────────


class TestTheGateMeasuresWhatTheRuleNames:
    def test_the_selector_list_matches_the_stylesheet(self):
        g = gate()
        assert set(g.SELECTORS) == stylesheet_scope_selectors(), (
            "the gate's selector list and the stylesheet's `.sg-exam-builder` "
            "rule have drifted apart — the gate is measuring a different set of "
            "controls than the rule protects"
        )

    def test_the_gate_scopes_to_the_builder_root(self):
        assert gate().SCOPE == "sg-exam-builder", (
            "the rule is scoped to this class; the gate must read it off the "
            "page's own root element rather than the whole document"
        )


# ── the geometry rule, on synthetic boxes ────────────────────────


class TestTheGeometryRule:
    def test_a_control_at_the_floor_is_not_a_finding(self):
        g = gate()
        assert (
            g.findings(
                [{"selector": "button", "w": 44, "h": 44, "display": "inline-block"}]
            )
            == []
        )

    def test_a_short_control_is_a_finding(self):
        g = gate()
        under = g.findings(
            [{"selector": "select", "w": 200, "h": 30, "display": "inline-block"}]
        )
        assert len(under) == 1 and under[0]["axis"] == "height"

    def test_a_narrow_control_is_a_finding(self):
        g = gate()
        under = g.findings(
            [{"selector": "a[href]", "w": 20, "h": 60, "display": "block"}]
        )
        assert len(under) == 1 and under[0]["axis"] == "width"

    def test_an_inline_box_is_not_measured(self):
        """`min-height` does not apply to an inline box — the rule admits it.

        The stylesheet's own comment records this: the action card's block links
        are caught by `a[href]`, and an inline link cannot be, because a height
        floor simply does not apply to it. Flagging one would be a finding no
        release could ever fix.
        """
        g = gate()
        assert (
            g.findings([{"selector": "a[href]", "w": 40, "h": 18, "display": "inline"}])
            == []
        )

    def test_a_hidden_control_is_not_measured(self):
        g = gate()
        assert (
            g.findings([{"selector": "button", "w": 0, "h": 0, "display": "none"}])
            == []
        )

    def test_a_finding_names_the_selector_and_the_measurement(self):
        g = gate()
        finding = g.findings(
            [{"selector": "select", "w": 200, "h": 30, "display": "inline-block"}]
        )[0]
        assert finding["selector"] == "select"
        assert finding["w"] == 200 and finding["h"] == 30


# ── the browser is located the documented way ────────────────────


class TestFindingABrowser:
    def test_an_explicit_overrides_everything(self, tmp_path):
        fake = tmp_path / "chrome"
        fake.write_text("#!/bin/sh\n", encoding="utf-8")
        assert gate().locate_browser({"SG_CHROME": str(fake)}) == str(fake)

    def test_an_explicit_empty_means_none(self):
        """The `SG_CHROME=""` escape hatch: measure nothing, calmly."""
        assert gate().locate_browser({"SG_CHROME": ""}) is None

    def test_a_missing_explicit_path_is_none(self):
        assert gate().locate_browser({"SG_CHROME": "/nope/chrome"}) is None


# ── exit codes: pass, a finding, or nothing measurable ───────────


class TestTheExitContract:
    def test_without_a_browser_it_cannot_measure(self):
        """Exit 2 is "we could not look", and it must not look like a pass."""
        if not GATE_PATH.exists():
            pytest.fail("deploy/touch_gate.py does not exist")
        env = dict(os.environ)
        env["SG_CHROME"] = ""
        result = subprocess.run(
            [
                sys.executable,
                str(GATE_PATH),
                "--base-url",
                "http://127.0.0.1:9",
                "--teacher",
                "t@example.com:pw",
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


# ── the run contract: a finding is 1, a clean page is 0 ─────────


class _FakeSession:
    class _Cookies:
        def get_dict(self):
            return {}

    cookies = _Cookies()


class TestTheRunContract:
    """`run()` maps a measured page to its exit code, without a browser."""

    def _run(self, monkeypatch, readings):
        g = gate()

        async def fake_measure(*a, **k):
            return readings, None

        monkeypatch.setattr(g, "sign_in", lambda *a, **k: (_FakeSession(), None))
        monkeypatch.setattr(g, "_measure", fake_measure)
        monkeypatch.setattr(g, "locate_browser", lambda *a, **k: "/fake/chrome")
        return g, g.run("http://box", "t@example.com:pw")

    def test_a_clean_page_is_zero(self, monkeypatch):
        _, (code, lines, _) = self._run(
            monkeypatch,
            [
                {
                    "width": 820,
                    "height": 1180,
                    "controls": [
                        {
                            "selector": "button",
                            "w": 120,
                            "h": 44,
                            "display": "inline-block",
                            "name": "save",
                        }
                    ],
                }
            ],
        )
        assert code == gate().EXIT_OK, lines

    def test_a_short_control_is_one(self, monkeypatch):
        g, (code, lines, payload) = self._run(
            monkeypatch,
            [
                {
                    "width": 820,
                    "height": 1180,
                    "controls": [
                        {
                            "selector": "select",
                            "w": 200,
                            "h": 30,
                            "display": "inline-block",
                            "name": "question_types",
                        }
                    ],
                }
            ],
        )
        assert code == g.EXIT_FINDING, lines
        assert payload["findings"][0]["axis"] == "height"
        assert any(
            "question_types" in line for line in lines
        ), "a finding must name the control an operator has to fix"


# ── the deploy runs it on every release ──────────────────────────


class TestTheDeployRunsIt:
    def test_the_runner_invokes_the_gate(self):
        script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
        assert "deploy/touch_gate.py" in script, (
            "the touch gate exists but no release runs it — the floor can "
            "regress silently again"
        )

    def test_the_runner_reads_a_finding_and_a_could_not_measure(self):
        script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
        block = script.split("touch gate", 1)[-1]
        assert re.search(r"\btouch gate\b", script), "no touch gate block"
        assert "exit 2" in block, "the runner must name the could-not-measure exit"
        assert "TOUCH_ENFORCE" in script, (
            "a finding must roll back only when the box is armed to enforce it, "
            "the way the smoke and perf gates are"
        )

    def test_a_regression_rolls_back_when_enforced(self):
        script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
        marker = 'if [ "${TOUCH_ENFORCE:-false}" = "true" ]'
        assert marker in script, (
            "the finding arm must be gated on TOUCH_ENFORCE, so a box that "
            "cannot run the gate cannot reject good releases"
        )
