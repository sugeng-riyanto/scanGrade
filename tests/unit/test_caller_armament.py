"""One checker, two callers: a box whose armament depends on who asks is armed by luck.

`deploy/arm-auto-deploy.sh --check` answers "is this box armed to check a release"
by reading files, and **which files it can read depends on the caller**. The gate
confs are mode 0600 root:root, and the deploy's construct probe runs the same
checker through `as_owner` — so two callers, one file, two readings is a state that
exists on real boxes. It already cost one release: the app's reading took a present
`SMOKE_MURID` for missing, reported a fully armed box unarmed, and refused every
tick; the console, running the same file as root in the same minute, said ARMED.

The runner now takes both readings at the preflight — as the caller the timer runs
as, and as the user that owns the checkout — and a disagreement is its own refusal
with both answers in the record. The properties guarded here are the ones that make
that a check rather than a coin toss:

* **the comparison is of the verdict, not of the report.** The two reports differ on
  every correctly-configured box, because a root-only conf legitimately reads
  "present — enforcement on" to root and "present (cannot read it)" to the service
  user. Both callers are armed; refusing that would stop every release on every box.
  The verdict each caller reached is what is compared, and a test holds the
  false-positive case as well as the disagreement;
* **a disagreement refuses.** It is the one finding about a *box* the runner cannot
  resolve by picking a side, so it is refused as unarmed and both reports are left
  where the status page reads them;
* **"we could not take the second reading" is not a disagreement.** A missing
  `runuser`, a user that cannot execute the checker, a hand-run as someone who is not
  root: the run is judged as root and says so, because a box whose only fault is the
  tool it would use to ask a second time has no disagreement to report;
* **it is the same question twice** — one file, one flag — and it is asked before
  anything is fetched, so a release is never staged by a box that cannot say it is
  ready.

Mutation-checked: `.freebuff/mutate_caller_armament.py`.
"""
from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from tests.unit.test_auto_deploy import (BASH, DEPLOY_SH, _preflight_block,
                                         _run_preflight)

pytestmark = pytest.mark.skipif(BASH is None, reason="needs a bash to run the preflight")

#: The harness's `as_owner`, for a checker whose answer depends on the caller. In
#: production the difference is the uid; here both runs are the same process, so this
#: is what makes one file answer twice — which is the shape under test, not the uid.
OWNER_AWARE = 'as_owner() { SG_TEST_CALLER=owner "$@"; }\n'
#: An `as_owner` that takes no reading at all (no `runuser`, a user that cannot run it).
NO_SECOND_READING = "as_owner() { return 1; }\n"

ARMED = "ARMED — every gate has what it needs."
#: The checker's own unarmed verdict, in its own shape: `report_state` prints the
#: verdict and then the remedy, so the verdict is **not** the last line of an unarmed
#: report. The stub keeps that shape, because a reader that took the last line instead
#: of the verdict would pass against a report that always ended in one.
NOT_ARMED = "NOT ARMED — see the lines above. Arm it with:"
REMEDY = "    bash /opt/scangrade/deploy/arm-auto-deploy.sh"
#: The two readings of one conf, as they happen on a real box: root reads the value,
#: the service user can only establish that it is there. Both are armed.
ROOT_READS_CONF = "   claims     : present — enforcement on"
OWNER_CANNOT_READ_CONF = (
    "   claims     : present (/etc/scangrade-claims.conf — mode 0600 root:root, so "
    "this caller (service-user) cannot read it)")


def _two_reader_checker(repo: Path, *, root: list, root_rc: int,
                        owner: list, owner_rc: int) -> None:
    """A checker that answers per caller, in the two reports the runner must read."""
    def emit(lines):
        return "".join(f"  printf '%s\\n' {shlex.quote(line)}\n" for line in lines)

    (repo / "deploy").mkdir(parents=True, exist_ok=True)
    (repo / "deploy" / "arm-auto-deploy.sh").write_text(
        "#!/usr/bin/env bash\n"
        'echo "   started as : $(id -un)"\n'
        'if [ "${SG_TEST_CALLER:-}" = "owner" ]; then\n'
        + emit(owner)
        + f"  exit {owner_rc}\n"
        "fi\n"
        + emit(root)
        + f"exit {root_rc}\n",
        encoding="utf-8")


def _run(tmp_path: Path, *, root: list, root_rc: int, owner: list, owner_rc: int,
         as_owner: str = OWNER_AWARE):
    """The preflight, over a box whose checker answers per caller."""
    repo = tmp_path / "repo"
    _two_reader_checker(repo, root=root, root_rc=root_rc,
                        owner=owner, owner_rc=owner_rc)
    state = tmp_path / "state"
    return _run_preflight(repo, state, as_owner=as_owner), state


# ── a disagreement is refused, and both readings are kept ────────────────────

class TestADisagreementIsRefused:
    def test_armed_for_root_and_unarmed_for_the_service_user(self, tmp_path):
        """The direction that cost a release: the app refuses what root calls armed."""
        run, state = _run(tmp_path, root=["   runner     : renders from the checkout", ARMED],
                          root_rc=0,
                          owner=["   runner     : renders from the checkout", NOT_ARMED,
                                 REMEDY],
                          owner_rc=1)
        assert run.returncode == 15, run.stdout + run.stderr
        assert "REACHED" not in run.stdout, "it refused and carried on anyway"
        assert "TWO READINGS" in run.stdout
        assert ARMED in run.stdout and NOT_ARMED in run.stdout, (
            "the refusal does not name both readings, so the reader cannot see which "
            "caller said what")
        assert "service-user" in run.stdout, (
            "the refusal does not name the second caller")

    def test_unarmed_for_root_and_armed_for_the_service_user(self, tmp_path):
        """The mirror image. It is still two answers about one box, so it is refused
        the same way — and it is the direction that says the runner's own reading is
        the pessimistic one, which is worth naming rather than rounding to 'unarmed'."""
        run, _ = _run(tmp_path, root=["   runner     : a COPY of the deploy script",
                                     NOT_ARMED, REMEDY],
                      root_rc=1,
                      owner=["   runner     : renders from the checkout", ARMED],
                      owner_rc=0)
        assert run.returncode == 15, run.stdout + run.stderr
        assert "TWO READINGS" in run.stdout
        assert ARMED in run.stdout and NOT_ARMED in run.stdout

    def test_the_record_carries_both_reports_and_says_which_caller_took_which(self, tmp_path):
        """The status page shows this file as it stands; 'which of the two said so' is
        the first question anyone asks it."""
        run, state = _run(tmp_path, root=["   runner     : renders from the checkout", ARMED],
                          root_rc=0,
                          owner=["   runner     : a COPY of the deploy script", NOT_ARMED,
                                 REMEDY],
                          owner_rc=1)
        assert run.returncode == 15
        body = (state / "unarmed").read_text(encoding="utf-8")
        assert body.splitlines()[0].startswith("20"), (
            "the first line is the time it refused, which is what the card ages")
        assert ARMED in body and NOT_ARMED in body, (
            "the record carries one reading, so a disagreement reads as agreement")
        assert body.index("--- as ") < body.index("--- as service-user ---")
        assert "the two readings disagree" in body
        assert "renders from the checkout" in body and "a COPY of the deploy script" in body

    def test_the_status_page_reads_the_record_the_runner_wrote(self, tmp_path):
        """The two ends of one file, checked together: a shape the runner writes and
        the page cannot read shows up as an operational page nobody can act on.

        The runner writes `date -Is` then the reports; `unarmed_state` reads the first
        line as the moment it refused and the rest as the detail, printed as it
        stands. So the assertions are about *that* split and not about the wording.
        """
        import datetime as _dt

        from app.services.deploy_status_service import unarmed_state

        run, state = _run(tmp_path, root=["   runner     : renders from the checkout", ARMED],
                          root_rc=0,
                          owner=["   runner     : a COPY of the deploy script", NOT_ARMED,
                                 REMEDY],
                          owner_rc=1)
        assert run.returncode == 15
        read = unarmed_state(state / "unarmed", now=_dt.datetime.now(_dt.timezone.utc))
        assert read["present"] is True and read["at"], (
            "the page cannot tell the moment the box refused from the report")
        assert ARMED in read["detail"] and NOT_ARMED in read["detail"], (
            "the page shows one reading, so a disagreement looks like agreement")
        assert "the two readings disagree" in read["detail"]

    def test_a_disagreement_is_a_refusal_to_deploy_not_a_rollback(self, tmp_path):
        """Nothing has been fetched when this is decided, and the box is what is
        wrong — so it is not a fact about a commit and it is not quarantined."""
        block = _preflight_block()
        assert "ARMAMENT_DISAGREE" in block, (
            "the block can no longer tell a disagreement apart from a plain refusal")
        assert "quarantine_write" not in block
        disagree = block.index('if [ "$ARMAMENT_DISAGREE" = "1" ]; then')
        assert disagree < block.index("exit 15"), (
            "the disagreement is named after the refusal it is supposed to explain")


# ── the false positive this must not have ────────────────────────────────────

class TestTwoReportsThatDifferAreNotADisagreement:
    def test_the_same_verdict_from_two_callers_carries_on(self, tmp_path):
        """A root-only conf reads differently to the two callers and *both are armed*.

        This is every correctly-configured box: the checker's three-state conf read
        exists so that "this caller cannot read it" is not "it is missing". Comparing
        the reports would refuse here, on every tick, for ever.
        """
        run, state = _run(tmp_path,
                          root=["   browser    : present (/usr/bin/chromium)", ROOT_READS_CONF, ARMED],
                          root_rc=0,
                          owner=["   browser    : present (/usr/bin/chromium)",
                                 OWNER_CANNOT_READ_CONF, ARMED],
                          owner_rc=0)
        assert run.returncode == 0, run.stdout + run.stderr
        assert "REACHED" in run.stdout, (
            "two readings that reach the same verdict were treated as a disagreement")
        assert not (state / "unarmed").exists()

    def test_the_armed_run_says_both_readings_were_taken(self, tmp_path):
        """Observable in the journal between two ticks: the comparison happened."""
        run, _ = _run(tmp_path, root=["   browser    : present", ARMED], root_rc=0,
                      owner=["   browser    : present", ARMED], owner_rc=0)
        assert run.returncode == 0
        assert "checked as root and as service-user" in run.stdout, (
            "an armed box does not say the second reading was taken, so a comparison "
            "that quietly stopped cannot be noticed from the journal either")


# ── a reading that could not be taken is not a disagreement ─────────────────

class TestAMissingSecondReadingIsSaidNotInvented:
    def test_an_armed_box_is_not_refused_because_the_second_reading_failed(self, tmp_path):
        run, state = _run(tmp_path, root=["   runner     : renders from the checkout", ARMED],
                          root_rc=0, owner=[], owner_rc=1, as_owner=NO_SECOND_READING)
        assert run.returncode == 0, run.stdout + run.stderr
        assert "REACHED" in run.stdout, (
            "a box whose only fault is the tool it would use to ask a second time was "
            "refused by a question it already had an answer to")
        assert "the second reading could not be taken" in run.stdout, (
            "the missing half is not named, so the journal reads as agreement")
        assert not (state / "unarmed").exists()

    def test_an_unarmed_box_is_still_refused_and_the_record_says_one_reading_only(self, tmp_path):
        run, state = _run(tmp_path, root=["   roster     : MISSING", NOT_ARMED, REMEDY],
                          root_rc=1, owner=[], owner_rc=1, as_owner=NO_SECOND_READING)
        assert run.returncode == 15, run.stdout + run.stderr
        assert "UNARMED" in run.stdout
        body = (state / "unarmed").read_text(encoding="utf-8")
        assert "MISSING" in body and NOT_ARMED in body, (
            "the record dropped the reading the refusal stands on")
        assert "could not be taken" in body, (
            "the record carries one reading and does not say the other is missing, so "
            "it reads as two callers agreeing")
        assert "--- as service-user ---" not in body

    def test_an_unsupported_second_caller_is_not_read_as_agreement(self, tmp_path):
        """An `as_owner` that echoes the arguments but runs nothing useful (a shell
        with no `runuser`) must not look like a caller that agreed."""
        run, _ = _run(tmp_path, root=["   runner     : renders from the checkout", ARMED],
                      root_rc=0, owner=["   runner     : renders from the checkout", ARMED],
                      owner_rc=0, as_owner="as_owner() { : ; }\n")
        assert run.returncode == 0
        assert "checked as root and as" not in run.stdout, (
            "a second reading that produced no verdict was reported as taken")


# ── the wiring a later edit can quietly drop ─────────────────────────────────

class TestItIsTheSameQuestionAskedTwice:
    def test_both_readings_run_the_same_file_with_the_same_flag(self):
        """Two different questions whose answers agree is not a comparison."""
        block = _preflight_block()
        assert 'ARMAMENT_OUT=$(bash "$checker" --check 2>&1)' in block, (
            "the runner's own reading no longer runs the checker's own --check")
        assert 'ARMAMENT_OWNER_OUT=$(as_owner bash "$checker" --check 2>&1)' in block, (
            "the second reading is not the same file and flag as the first, so the two "
            "answers are not comparable")

    def test_both_verdicts_come_from_one_reader(self):
        """Two extractions is how one of them starts reporting a different kind of
        line, and then the comparison is between two spellings rather than two
        callers."""
        block = _preflight_block()
        assert "armament_verdict() {" in block, (
            "the verdict reader is gone from the preflight")
        assert block.count("$(armament_verdict ") == 2, (
            "the two verdicts are not both read through the one helper")

    def test_the_comparison_runs_before_anything_is_fetched(self):
        """A release is never staged by a box that cannot say whether it is armed."""
        script = DEPLOY_SH.read_text(encoding="utf-8")
        assert script.index('as_owner bash "$checker" --check') < \
            script.index("AFTER=$(as_owner git"), (
                "the second reading is taken after the release fetch")


if __name__ == "__main__":                                   # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
