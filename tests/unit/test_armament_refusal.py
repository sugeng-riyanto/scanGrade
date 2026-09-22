"""An unarmed box cannot ship code, and the app is what makes that true.

The gap this closes is the one the runner cannot close for itself: an installed
**copy** of the deploy script so old that it predates Gate 0. Nothing inside such
a file can judge it, so it deploys happily with the gates from the day it was
installed while the site looks healthy. The one thing on that box guaranteed to be
the new commit is the app the copy is about to reload — so the copy's own
construct probe builds it, and the app refuses.

Three things make that work, and each one is guarded here:

* the probe is *identifiable* — `START_BACKGROUND_SCHEDULERS=false` is the marker
  the deploy sets and nothing else does, and every copy ever installed carries it
  because it dates from the script's first commit;
* the answer is the *existing* checker's own report, not a second opinion;
* the journal can name the cause — the marker the deploy greps for is the marker
  this prints, so a refusal never reads as a Python fault.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy" / "scangrade-deploy.sh"
CHECKER = ROOT / "deploy" / "arm-auto-deploy.sh"

from app.utils import armament  # noqa: E402  (after ROOT, for clarity)


# ── the marker that identifies the probe ─────────────────────────────────────

class TestTheProbeIsIdentifiable:
    def test_the_deploy_sets_the_marker_the_app_reads(self):
        """`START_BACKGROUND_SCHEDULERS=false` is set by the construct gate.

        It is the only signal that distinguishes the deploy's probe from the app
        about to serve, and it cannot be replaced by anything newer: a stale copy
        must still be refused, and a stale copy only knows the settings that
        existed when it was installed.
        """
        script = DEPLOY.read_text(encoding="utf-8")
        at = script.index("CONSTRUCT_OUT=$(as_owner env")
        call = script[at:script.index("\n' 2>&1)", at)]
        assert "START_BACKGROUND_SCHEDULERS=false" in call, (
            "the probe no longer marks itself, so the app cannot tell it from the "
            "app about to serve — and would refuse the site instead of the release")

    def test_the_config_reads_it_rather_than_guessing(self):
        """One derivation, in `app/config.py`, and it is the negation.

        The scheduler switch is off exactly when the scheduler-free construction
        happens, so deriving the probe from it means no caller has to remember to
        set two things — and a new probe path that sets one of them still works.
        """
        assert armament.__doc__, "the module's reasoning is part of the interface"
        from app.config import Config, TestingConfig

        assert Config.DEPLOY_PROBE == (not Config.START_BACKGROUND_SCHEDULERS)
        # Tests construct the app the same side-effect-free way the deploy's probe
        # does; if the test config derived the flag like production, every test
        # would be asking a question about a VPS.
        assert TestingConfig.DEPLOY_PROBE is False
        source = (ROOT / "app" / "config.py").read_text(encoding="utf-8")
        testing = source[source.index("class TestingConfig"):]
        assert "DEPLOY_PROBE = False" in testing.split("def ", 1)[0], (
            "the test config derives the probe, so the probe's question is asked "
            "on a laptop where the answer can only ever be 'unarmed'")


# ── the answer comes from the one checker that already exists ────────────────

class TestTheCheckerIsTheAuthority:
    def test_armed_is_none_and_says_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(armament, "CHECKER", _checker(tmp_path, 0, "ARMED"))
        assert armament.unarmed_reason() is None

    def test_unarmed_is_the_checker_s_own_report(self, tmp_path, monkeypatch):
        monkeypatch.setattr(armament, "CHECKER", _checker(tmp_path, 1, "NOT ARMED"))
        reason = armament.unarmed_reason()
        assert reason is not None and "NOT ARMED" in reason, (
            "the reason is a second opinion rather than the checker's own words")

    def test_a_checker_that_cannot_be_run_is_not_an_answer(self, tmp_path, monkeypatch):
        """"We could not tell" is not "it is fine".

        The whole module exists because the silent version of that confusion ships
        code no gate has looked at, so a checker that is absent, times out, or
        cannot be executed is reported as unarmed.
        """
        monkeypatch.setattr(armament, "CHECKER", tmp_path / "not-here.sh")
        assert "missing" in armament.unarmed_reason()

        monkeypatch.setattr(armament, "CHECKER", _checker(tmp_path, 1, "x"))

        def timeout(*a, **k):
            raise subprocess.TimeoutExpired(cmd="arm-auto-deploy.sh", timeout=1)

        monkeypatch.setattr(armament.subprocess, "run", timeout)
        assert "did not finish" in armament.unarmed_reason()

        def oserror(*a, **k):
            raise OSError("no bash here")

        monkeypatch.setattr(armament.subprocess, "run", oserror)
        assert "could not be run" in armament.unarmed_reason()

    def test_it_asks_the_checker_the_human_runs(self):
        """`--check` is read-only and is what an operator is told to run.

        A second implementation in Python would be a second opinion, and the first
        thing two opinions do is disagree — about the box, on the page that exists
        to explain why nothing is deploying.
        """
        assert CHECKER.exists()
        text = CHECKER.read_text(encoding="utf-8")
        assert "--check" in text
        assert armament.CHECKER == CHECKER, (
            "the app runs a different script from the one the operator is told to run")


def _checker(tmp_path: Path, code: int, text: str) -> Path:
    """A stand-in checker: the app only reads its exit code and its output."""
    path = tmp_path / f"checker-{code}.sh"
    path.write_text(f'#!/usr/bin/env bash\necho "{text}"\nexit {code}\n', encoding="utf-8")
    return path


# ── the refusal, and the journal that has to name it ─────────────────────────

class TestTheReleaseIsRefused:
    @pytest.fixture
    def probe(self, monkeypatch):
        """A construct that believes it is the deploy's probe."""
        from app.config import TestingConfig

        monkeypatch.setattr(TestingConfig, "DEPLOY_PROBE", True)
        return TestingConfig

    def test_an_unarmed_box_exits_nonzero_with_the_marker(self, probe, monkeypatch, capsys):
        from tests.conftest import build_app

        monkeypatch.setattr(armament, "unarmed_reason",
                            lambda *a, **k: "NOT ARMED\\n   runner : a COPY")
        with pytest.raises(SystemExit) as caught:
            build_app("testing")
        assert caught.value.code == 1, "a refusal has to be a failure, not a warning"
        out = capsys.readouterr().out
        assert armament.MARKER in out, (
            "without the marker the journal says 'app did not construct' and the "
            "next reader hunts for a Python fault that is not there")
        assert "NOT ARMED" in out, "the checker's own report is the evidence"
        assert "arm-auto-deploy.sh" in out, "a refusal with no remedy is a dead end"

    def test_an_armed_box_constructs(self, probe, monkeypatch):
        from tests.conftest import build_app

        monkeypatch.setattr(armament, "unarmed_reason", lambda *a, **k: None)
        assert build_app("testing") is not None

    def test_the_deploy_only_asks_when_it_is_a_probe(self, monkeypatch):
        """Gunicorn constructs the same app, and must not be refused.

        This is the half that keeps the refusal from taking the site down: the
        question is asked only under the marker the deploy sets, so the app that
        serves students never reaches it.
        """
        from tests.conftest import build_app

        def forbidden(*a, **k):
            raise AssertionError("the app about to serve asked whether the box is armed")

        monkeypatch.setattr(armament, "unarmed_reason", forbidden)
        assert build_app("testing") is not None

    def test_the_marker_the_app_prints_is_the_one_the_deploy_greps_for(self):
        """Two halves of one fact, in two languages, held together here.

        The app writes the marker to stdout; the deploy reads the probe's captured
        output and turns it into its own failure reason. If either spelling moves,
        the refusal still fails the release but says the wrong thing about why.
        """
        script = DEPLOY.read_text(encoding="utf-8")
        assert f"grep -q '{armament.MARKER}'" in script, (
            "the deploy no longer recognises the app's refusal, so it reports a "
            "Python construct fault instead of an unarmed runner")
        at = script.index(f"grep -q '{armament.MARKER}'")
        branch = script[at:script.index("\nfi\n", at)]
        assert "runner not armed" in branch, (
            "the branch names no gate, so the status page cannot say which one")
        assert "quarantine_write" in branch, (
            "a refusal that is not quarantined is retried every two minutes")


def test_only_the_deploy_marks_a_probe():
    """Exactly one caller sets the marker, which is what makes the refusal safe.

    A second setter — an ops script, another gate, a helper — would make "this is
    a probe" true of something that is not, and the app would refuse work that
    should have gone out. Set anywhere but the deploy's construct gate, this
    switch stops meaning "a release is being judged" and starts meaning "someone
    wanted the schedulers off".
    """
    marker = re.compile(r"START_BACKGROUND_SCHEDULERS\s*=\s*false", re.I)
    setters: dict[str, list[str]] = {}
    for path in sorted(list(ROOT.glob("*.sh")) + list(ROOT.glob("deploy/*.sh"))
                       + list(ROOT.glob("tools/*.sh"))):
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if marker.search(line) and not line.lstrip().startswith("#"):
                setters.setdefault(path.relative_to(ROOT).as_posix(), []).append(line.strip())
    assert list(setters) == ["deploy/scangrade-deploy.sh"], (
        f"the probe marker is set by something other than the construct gate: {setters}")

    # And nothing Python-side turns it on for a construction it is running.
    for path in list(ROOT.glob("*.py")) + list(ROOT.glob("app/**/*.py")) \
            + list(ROOT.glob("tools/*.py")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert 'environ["START_BACKGROUND_SCHEDULERS"]' not in text, (
            f"{path.relative_to(ROOT)} sets the probe marker from Python")


if __name__ == "__main__":                                   # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
