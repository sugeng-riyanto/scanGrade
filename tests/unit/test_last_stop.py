"""The step a run stopped at, whatever stopped it — recorded by the runner itself.

Every other record on the status page is written by a branch somebody wrote for a
refusal they thought of: a dirty checkout, a fetch with no network, a merge that
could not happen, a release a gate judged and put back. Each of those is worth its
own card, and together they leave one hole — the run that stops somewhere nobody
wrote a branch for. A step added later, a command that exits non-zero with no
`if` around it, a `pipefail` in a helper nobody looked at twice. Those end the run
with one line in the middle of a 1 300-line journal, and from outside the box is
simply a box that is not deploying.

So the runner tracks the phase it is in and records any non-zero exit itself, from
its `EXIT` trap. Three properties are what make that worth anything, and each is
measured here:

  * it cannot be forgotten by a new failure path, because the trap is the writer
    and it is installed before the first exit in the script;
  * it says the *step*, not just the code, so "exit 7" becomes "it stopped
    installing dependencies" — which is the whole request;
  * it is only cleared by a run that reaches the end, so a tick that exits 0
    because it had nothing to do cannot erase the record of the tick that is
    still failing.

The page half is parity rather than prose: `RUN_STEPS` and `EXIT_CODES` in the
service are the vocabulary, and these tests prove the runner's own assignments,
the service's tables and the template's sentences all name the same things.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.services import deploy_status_service as status

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "deploy" / "scangrade-deploy.sh"
TEMPLATE = ROOT / "app" / "templates" / "super_admin" / "deploy_status.html"
BASH = shutil.which("bash")

needs_bash = pytest.mark.skipif(BASH is None, reason="needs bash to run the block")

START = "# last-stop-logic:start"
END = "# last-stop-logic:end"

#: A run that stopped part-way, with a commit under judgement.
LAST_STOP_TEXT = (
    "verify\n2026-09-23T11:14:02+00:00\n10\n" + "d" * 40 + "\n")


def _script() -> str:
    return RUNNER.read_text(encoding="utf-8")


def _block() -> str:
    return _script().split(START, 1)[1].split(END, 1)[0]


# ── the vocabulary, three ways ───────────────────────────────────────────────
#
# The runner assigns, the service names, the template speaks. None of the three
# may drift from the others, and each check below is mechanical rather than a
# reading of intent: a step a newer runner adds shows as itself on the page
# instead of as a guess, and a code the page has no row for is the one failure
# this whole card exists to prevent.

def runner_steps() -> set[str]:
    """Every step `deploy/scangrade-deploy.sh` can be in, by key."""
    return set(re.findall(r'^\s*RUN_STEP="([a-z_]+)"', _script(), re.M))


def runner_exit_codes() -> set[int]:
    """Every code `deploy/scangrade-deploy.sh` can end a run with."""
    return {int(c) for c in re.findall(r"^\s*exit (\d+)", _script(), re.M)}


def template_step_keys() -> set[str]:
    """The steps the last-stop card can say in either language."""
    return set(re.findall(r"ls\.step_key == '([a-z_0-9]+)'",
                          TEMPLATE.read_text(encoding="utf-8")))


def template_exit_codes() -> set[int]:
    """Every code the page's table has a meaning for."""
    return {int(c) for c in re.findall(r"code == (\d+)",
                                       TEMPLATE.read_text(encoding="utf-8"))}


def test_the_runner_assigns_exactly_the_steps_the_page_names():
    assert runner_steps() == status.RUN_STEPS, (
        "the runner and the page disagree about the phases a run has: "
        f"only in the runner {sorted(runner_steps() - status.RUN_STEPS)}, "
        f"only in the page {sorted(status.RUN_STEPS - runner_steps())}")


def test_every_step_has_a_sentence_in_both_languages():
    assert template_step_keys() == status.RUN_STEPS, (
        f"missing from the page: {sorted(status.RUN_STEPS - template_step_keys())}; "
        f"invented by the page: {sorted(template_step_keys() - status.RUN_STEPS)}")


def test_every_exit_code_the_runner_uses_has_a_row_in_the_table():
    assert runner_exit_codes() == set(status.EXIT_CODES), (
        "a code the runner can exit with has no reading: "
        f"{sorted(runner_exit_codes() - set(status.EXIT_CODES))}")


def test_the_page_has_a_meaning_for_every_row_it_renders():
    assert template_exit_codes() == set(status.EXIT_CODES), (
        f"the table renders a code with no sentence: "
        f"{sorted(set(status.EXIT_CODES) - template_exit_codes())}")


def test_every_tone_is_one_of_the_five_the_page_colours():
    assert set(status.EXIT_CODES.values()) == set(status.EXIT_TONES), (
        "a tone with no colour, or a colour with no code: "
        f"{sorted(set(status.EXIT_CODES.values()) ^ set(status.EXIT_TONES))}")


def test_the_trap_is_installed_before_the_first_exit():
    """The property that makes a new failure path unable to forget the record."""
    text = _script()
    trap_at = text.index("trap on_exit EXIT")
    first_exit = re.search(r"^\s*exit \d+", text, re.M).start()
    assert trap_at < first_exit, (
        "an exit before the trap runs unrecorded, which is exactly the hole this "
        "block fills")


def test_the_block_takes_no_positional_parameter():
    """The runner takes no arguments at any level; a step that could be passed a
    value could also name the wrong one."""
    for bad in ("$1", "${1}", "getopt", "shift"):
        assert bad not in _block(), f"the block reads {bad}"


def test_the_record_is_left_readable_by_the_app():
    """Root writes it and the app user reads it, so the mode is part of the record
    being usable at all — the same rule the pre-merge record follows."""
    assert 'chmod 0644 "$LAST_STOP_FILE"' in _block(), (
        "the record would keep root's umask, and the status page would read it as "
        "a stop it cannot name")


def test_the_block_lives_between_its_own_delimiters():
    script = _script()
    assert START in script and END in script, (
        "the last-stop block's delimiters are gone; they are also how the rest of "
        "this file runs it in isolation")


# ── the block on its own, run for real ───────────────────────────────────────

def _harness(state: Path, tail: str) -> str:
    return (
        "#!/usr/bin/env bash\n"
        "set -uo pipefail\n"
        f'STATE_DIR="{state.as_posix()}"\n'
        'AFTER_FULL=""\n'
        f"{_block()}\n"
        f"{tail}\n"
    )


def _run(tmp_path: Path, tail: str) -> tuple[int, str]:
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    script = tmp_path / "harness.sh"
    script.write_text(_harness(state, tail), encoding="utf-8", newline="\n")
    proc = subprocess.run([BASH, str(script)], capture_output=True, text=True)
    record = state / "last-stop"
    return proc.returncode, (record.read_text(encoding="utf-8") if record.exists() else "")


@needs_bash
def test_a_non_zero_exit_is_recorded_with_its_step_and_code(tmp_path):
    code, text = _run(tmp_path, 'RUN_STEP="verify"; AFTER_FULL="%s"; exit 10' % ("d" * 40))
    lines = text.splitlines()
    assert code == 10, "the trap must not swallow the code the caller chose"
    assert lines[0] == "verify"
    assert lines[2] == "10"
    assert lines[3] == "d" * 40
    assert len(lines) == 4, f"the record is positional: {lines!r}"


@needs_bash
def test_a_run_that_stopped_before_a_commit_invents_none(tmp_path):
    """The first steps run before `origin/main` is read, so line 4 is empty — and
    an invented sha would name a commit nobody judged."""
    code, text = _run(tmp_path, 'RUN_STEP="fetch"; exit 5')
    lines = text.splitlines()
    assert code == 5
    assert lines[0] == "fetch" and lines[2] == "5"
    assert lines[3] == "", f"a commit was invented: {lines!r}"


@needs_bash
def test_a_quiet_tick_does_not_erase_the_record_of_the_failing_one(tmp_path):
    """Exit 0 is also what a tick with nothing to do exits with. Treating that as
    a recovery would delete the evidence every other minute."""
    _, text = _run(tmp_path, 'RUN_STEP="verify"; exit 7')
    assert text.startswith("verify")
    code, after = _run(tmp_path, 'RUN_STEP="fetch"; exit 0')
    assert code == 0
    assert after.startswith("verify"), (
        "a tick that had nothing to do erased the step the box is stuck on")


@needs_bash
def test_only_a_run_that_reaches_the_end_clears_it(tmp_path):
    _, text = _run(tmp_path, 'RUN_STEP="verify"; exit 7')
    assert text, "nothing was written to clear"
    code, after = _run(tmp_path, 'RUN_STEP="done"; exit 0')
    assert code == 0
    assert after == "", "a finished run left the record of a stop behind"


@needs_bash
def test_the_record_is_replaced_rather_than_appended(tmp_path):
    """This file is read as four positional lines, so a second stop must not push
    the first one down into the body."""
    _run(tmp_path, 'RUN_STEP="verify"; exit 10')
    _, text = _run(tmp_path, 'RUN_STEP="merge"; exit 6')
    lines = text.splitlines()
    assert lines[0] == "merge" and lines[2] == "6" and len(lines) == 4, lines


@needs_bash
def test_a_stop_nobody_explained_is_still_recorded(tmp_path):
    """The hole this block exists for: no `PREFLIGHT_GATE`, no `FAIL_REASON`, no
    branch — just a command that returned non-zero in a phase."""
    code, text = _run(tmp_path, 'RUN_STEP="snapshot"; exit 12')
    assert code == 12
    assert text.splitlines()[:1] == ["snapshot"]


# ── the reading, and the page ────────────────────────────────────────────────

def last_stop_report(tmp_path: Path, *, text: str | None = LAST_STOP_TEXT,
                     path: Path | None = None) -> dict:
    """A box whose last run that ended non-zero stopped at a recorded step."""
    record = path or (tmp_path / "last-stop")
    if path is None and text is not None:
        record.write_text(text, encoding="utf-8")
    return status.report(repo=str(tmp_path), runner="/nonexistent",
                         snapshot_runner="/nonexistent",
                         pause_file=str(tmp_path / "no-pause"),
                         last_stop_file=str(record),
                         request_dir=str(tmp_path / "requests"))


def render_status(app, report) -> str:
    from flask import g, render_template
    with app.test_request_context("/super-admin/deploy-status"):
        g.user_id = "a-super-admin"
        g.user_role = "super_admin"
        g.user_name = "Tester"
        g.user_email = "t@t"
        g.tz_offset = 7
        return render_template("super_admin/deploy_status.html", status=report,
                               alerts={}, testalert=None, released=None)


class TestTheLastStopRecord:
    def test_the_record_reads_back_whole(self, tmp_path):
        state = last_stop_report(tmp_path)["last_stop"]
        assert state["present"] is True
        assert state["key"] == status.LAST_STOP_PRESENT
        assert state["step"] == "verify"
        assert state["step_key"] == "verify", "a step the page knows is kept as itself"
        assert state["exit_code"] == 10
        assert state["tone"] == "rolled_back"
        assert state["commit"] == "d" * 40 and state["short"] == "ddddddd"
        assert isinstance(state["age_seconds"], int)
        assert state["at"] == "2026-09-23T11:14:02+00:00"

    def test_a_step_from_a_newer_runner_is_shown_as_itself(self, tmp_path):
        """A page that hid an unknown step would leave the operator with a code and
        no phase — the state this card exists to end."""
        state = last_stop_report(
            tmp_path, text="quantum_gate\n2026-09-23T11:14:02+00:00\n8\n\n")["last_stop"]
        assert state["present"] is True
        assert state["step"] == "quantum_gate"
        assert state["step_key"] == status.RUN_STEP_UNKNOWN

    def test_a_code_with_no_row_is_left_without_a_reading(self, tmp_path):
        """Better a code and no sentence than a sentence about the wrong code."""
        state = last_stop_report(
            tmp_path, text="verify\n2026-09-23T11:14:02+00:00\n99\n\n")["last_stop"]
        assert state["present"] is True
        assert state["exit_code"] == 99
        assert state["tone"] is None

    def test_the_cold_state_does_not_claim_a_clean_run(self, tmp_path):
        state = last_stop_report(tmp_path, text=None)["last_stop"]
        assert state["present"] is False
        assert state["key"] == status.LAST_STOP_NONE

    def test_a_record_that_is_not_a_record_is_not_read_as_absent(self, tmp_path):
        state = last_stop_report(tmp_path, text="!!!\n\n\n\n")["last_stop"]
        assert state["key"] == status.LAST_STOP_MALFORMED, (
            "an unreadable header must not read as a box that is fine")

    def test_a_record_that_cannot_be_read_is_not_read_as_absent(self, tmp_path):
        path = tmp_path / "a-directory-instead"
        path.mkdir()
        state = last_stop_report(tmp_path, path=path)["last_stop"]
        assert state["key"] == status.LAST_STOP_UNREADABLE
        assert state["reason"], "the reason the page could not read it is not carried"

    def test_the_default_lands_on_the_runner_s_own_path(self, tmp_path):
        report = status.report(repo=str(tmp_path), runner="/nonexistent",
                               snapshot_runner="/nonexistent",
                               pause_file=str(tmp_path / "no-pause"))
        assert report["last_stop_file"].replace("\\", "/") == \
            status.DEFAULT_STATE_DIR + "/last-stop", (
            "the page reads a different file from the one the runner writes")


class TestThePageNamesTheStepThatStopped:
    def test_the_step_and_the_code_are_both_rendered(self, app, tmp_path):
        report = last_stop_report(tmp_path)
        html = render_status(app, report)
        assert "The Last Run Stopped Here" in html
        assert "it reloaded, then failed the verification after it" in html, (
            "the code's meaning is not shown, so the reader has a number and no "
            "sense of what it means")
        assert "signing in as each role" in html, (
            "the step is not named in the reader's language")
        assert report["last_stop"]["short"] in html
        assert report["last_stop"]["at"] in html

    def test_the_table_renders_every_code_the_runner_can_use(self, app, tmp_path):
        html = render_status(app, last_stop_report(tmp_path, text=None))
        for code in status.EXIT_CODES:
            assert f">{code}<" in html, f"exit {code} has no row in the table"

    def test_a_code_with_no_row_is_not_given_a_reading(self, app, tmp_path):
        html = render_status(app, last_stop_report(
            tmp_path, text="verify\n2026-09-23T11:14:02+00:00\n99\n\n"))
        assert "a code with no row in the table below" in html, (
            "an unknown code got a tone it does not have")

    def test_the_cold_state_says_what_clears_it(self, app, tmp_path):
        """The blank must not read as \"nothing has ever stopped\": only a run that
        reaches the end removes the record, and that is what the sentence says."""
        html = render_status(app, last_stop_report(tmp_path, text=None))
        assert "No Run Is Stopping" in html
        assert "removed only by a run that reaches the end" in html

    def test_a_record_it_cannot_read_is_not_rendered_as_a_clean_state(self, app, tmp_path):
        path = tmp_path / "a-directory-instead"
        path.mkdir()
        html = render_status(app, last_stop_report(tmp_path, path=path))
        assert "Last-Stop Record Unreadable" in html
        assert "No Run Is Stopping" not in html
