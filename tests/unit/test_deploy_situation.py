"""Whether what is blocking a deploy will clear itself, or needs a person.

The runner already writes down *what* refused a release — a quarantine names the
commit and the gate, a pre-merge record names the step and the code, `last-stop`
names the step the last run stopped at. None of them answers the one question the
operator actually has: **do I have to do anything, or will it fix itself?** The
two look identical on a page. A fetch that could not reach GitHub and a gate that
rejected the code both leave "nothing is deploying" and a previous release still
serving, and the shape of the correct response is opposite: one is patience, the
other is work.

So the runner classifies each refusal itself, where it already knows the step and
the exit code, and writes the answer beside the records it keeps:

    line 1  how it resolves          self | human
    line 2  what refused             preflight | quarantine | dependencies | …
    line 3  the gate, when one is named (a pre-merge step, or `perf_gate`)
    line 4  the single action         wait | release | rebaseline | console
    line 5  when it was written (ISO)

The classification lives in the runner and not on the page for the same reason the
exit codes do: the page reads a record, it does not own the judgement. And the one
action is a *key*, not a sentence, so the page can offer exactly one button — the
release that retries a held commit, or the re-baseline that re-measures the box —
while a refusal the page cannot act on (`wait`, `console`) says so plainly instead
of offering a button that does nothing.

What these tests hold:

* **Every pre-merge refusal is classified, and the two sets partition the runner's
  own gate names** — a gate added without a disposition fails here rather than
  arriving as neither.
* **The disposition is the runner's, off the step it refused at**, not inferred
  from an exit code: `4` is both a box edit the runner heals and a checkout it
  cannot read, and those are opposite answers.
* **A quarantine offers the right single action**: re-baseline for the perf gate,
  release for every other, console for the ones no request can reach.
* **The page renders at most one button**, and the self-resolving case renders
  none — the whole point is that "wait" is not a button.
"""

import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.services import deploy_status_service as status  # noqa: E402

DEPLOY_SH = ROOT / "deploy" / "scangrade-deploy.sh"
TEMPLATE = ROOT / "app" / "templates" / "super_admin" / "deploy_status.html"
BASH = shutil.which("bash")

START = "# situation-logic:start"
END = "# situation-logic:end"

pytestmark = pytest.mark.skipif(BASH is None, reason="needs a bash to run the section")


def _block() -> str:
    script = DEPLOY_SH.read_text(encoding="utf-8")
    assert START in script and END in script, (
        "the section's delimiters are what let it be run on its own, the way the "
        "quarantine's, the preflight record's and the box-edits section's are")
    return script.split(START, 1)[1].split(END, 1)[0]


def _declared(script_text: str, name: str) -> set[str]:
    match = re.search(rf'^{name}=" ([^"]*) "', script_text, re.M)
    assert match, f"{name} must be one space-padded word list, in the runner"
    return set(match.group(1).split())


def _case_values(case: str) -> set[str]:
    """The gate names a runner case arm names, as `a|b|c)`."""
    return {part for part in re.split(r"[|)]", case.split(")")[0]) if part}


#: The smallest harness that can stand where the runner's exit trap does: it sets
#: the same globals *first* (the section computes `$SITUATION_FILE` from `$STATE_DIR`
#: at its top level), then asks the section to classify and record.
PRELUDE = """set -uo pipefail
STATE_DIR=__STATE__
PREFLIGHT_GATE=__GATE__
LAST_STOP_CODE=__CODE__
FAIL_REASON=__REASON__
RUN_STEP=__STEP__
"""

TAIL = """situation_classify
situation_write
printf 'DISPOSITION=%s\\n' "$SITUATION_DISPOSITION"
printf 'ACTION=%s\\n' "$SITUATION_ACTION"
"""


def _run(tmp_path: Path, *, gate: str = "", code: str = "0", reason: str = "",
         step: str = "start") -> subprocess.CompletedProcess:
    tmp_path.mkdir(parents=True, exist_ok=True)
    script = tmp_path / "harness.sh"
    # `shlex.quote`, because a `FAIL_REASON` is the runner's own prose — spaces and
    # parentheses and all — and an unquoted assignment turns it into a shell error
    # rather than the reason under test.
    prelude = (PRELUDE
               .replace("__STATE__", shlex.quote((tmp_path / "state").as_posix()))
               .replace("__GATE__", shlex.quote(gate))
               .replace("__CODE__", shlex.quote(code))
               .replace("__REASON__", shlex.quote(reason))
               .replace("__STEP__", shlex.quote(step)))
    script.write_text(prelude + _block() + "\n" + TAIL, encoding="utf-8",
                      newline="\n")
    return subprocess.run([BASH, str(script)], capture_output=True, text=True,
                          check=False)


def _state(tmp_path: Path) -> Path:
    return tmp_path / "state" / "situation"


# ── 1. the classification, driven by the step that refused ──────────────────

class TestTheRunnerClassifies:
    def test_a_fetch_that_could_not_reach_github_resolves_itself(self, tmp_path):
        done = _run(tmp_path, gate="fetch_failed", code="5")
        assert done.returncode == 0, done.stderr
        assert "DISPOSITION=self" in done.stdout, done.stdout
        assert "ACTION=wait" in done.stdout, done.stdout
        fields = _state(tmp_path).read_text(encoding="utf-8").splitlines()
        assert fields[0] == "self" and fields[1] == "preflight"
        assert fields[2] == "fetch_failed" and fields[3] == "wait"

    @pytest.mark.parametrize("gate", ["snapshot_refused", "lock_refused",
                                      "merge_refused", "dirty_checkout"])
    def test_the_other_refusals_the_next_tick_can_clear_say_wait(self, tmp_path, gate):
        done = _run(tmp_path, gate=gate, code="4")
        assert done.returncode == 0, done.stderr
        assert "DISPOSITION=self" in done.stdout and "ACTION=wait" in done.stdout, (
            f"{gate} is a condition the runner retries by itself: {done.stdout}")

    @pytest.mark.parametrize("gate", ["not_root", "no_checkout", "no_virtualenv",
                                      "checkout_unreadable"])
    def test_a_static_pre_merge_refusal_says_a_person_is_needed(self, tmp_path, gate):
        done = _run(tmp_path, gate=gate, code="3")
        assert done.returncode == 0, done.stderr
        assert "DISPOSITION=human" in done.stdout, done.stdout
        assert "ACTION=console" in done.stdout, (
            f"{gate} cannot be cleared by any tick or request: {done.stdout}")

    def test_the_disposition_is_the_step_not_the_exit_code(self, tmp_path):
        """`4` is both the box edit the runner heals and the checkout it cannot read."""
        heal = _run(tmp_path / "a", gate="dirty_checkout", code="4")
        read = _run(tmp_path / "b", gate="checkout_unreadable", code="4")
        assert "DISPOSITION=self" in heal.stdout, heal.stdout
        assert "DISPOSITION=human" in read.stdout, read.stdout

    @pytest.mark.parametrize("code", ["8", "9", "13", "16", "18"])
    def test_a_gate_that_rejected_the_commit_offers_a_release(self, tmp_path, code):
        done = _run(tmp_path, code=code, reason="theme gate (exit 3)")
        assert done.returncode == 0, done.stderr
        assert "DISPOSITION=human" in done.stdout, done.stdout
        assert "ACTION=release" in done.stdout, (
            f"a quarantined commit is released by one request (exit {code}): {done.stdout}")

    def test_the_perf_gate_offers_a_re_baseline_instead(self, tmp_path):
        done = _run(tmp_path, code="10",
                    reason="perf gate (slower than the last release that passed)")
        assert "DISPOSITION=human" in done.stdout, done.stdout
        assert "ACTION=rebaseline" in done.stdout, (
            "retrying a release the box's drift held with the same yardstick "
            f"re-refuses it; the button has to re-measure: {done.stdout}")
        fields = _state(tmp_path).read_text(encoding="utf-8").splitlines()
        assert fields[2] == "perf_gate", fields

    def test_a_failed_dependency_install_resolves_itself(self, tmp_path):
        done = _run(tmp_path, code="7", reason="pip install (exit 7)")
        assert "DISPOSITION=self" in done.stdout and "ACTION=wait" in done.stdout, (
            f"a pip failure is usually the network: {done.stdout}")

    @pytest.mark.parametrize("code,source", [("15", "unarmed"), ("14", "runner_copy"),
                                             ("11", "unhealthy")])
    def test_nothing_a_request_can_do_offers_no_action(self, tmp_path, code, source):
        done = _run(tmp_path, code=code)
        assert "DISPOSITION=human" in done.stdout, done.stdout
        assert "ACTION=console" in done.stdout, (
            f"exit {code} needs an installer or a console, and must not offer a "
            f"button that cannot help: {done.stdout}")

    def test_an_unrecognised_exit_is_not_promised_an_automatic_fix(self, tmp_path):
        done = _run(tmp_path, code="99")
        assert "DISPOSITION=human" in done.stdout, (
            "the safe default is 'a person', never a fix the runner may not make")
        assert "ACTION=console" in done.stdout, done.stdout

    def test_clearing_removes_the_record(self, tmp_path):
        _run(tmp_path, gate="fetch_failed", code="5")
        assert _state(tmp_path).is_file()
        # `situation_clear` reads `$SITUATION_FILE`, which the section derives from
        # `$STATE_DIR`; run it against the same state directory the write used.
        script = tmp_path / "clear.sh"
        script.write_text(f'STATE_DIR={(tmp_path / "state").as_posix()}\n'
                          + _block() + "\nsituation_clear\n", encoding="utf-8",
                          newline="\n")
        done = subprocess.run([BASH, str(script)], capture_output=True, text=True,
                              check=False)
        assert done.returncode == 0, done.stderr
        assert not _state(tmp_path).exists(), "a cleared situation must not linger"


# ── 2. the contract with the runner's own gate names ─────────────────────────

class TestTheClassificationCoversEveryRefusal:
    def test_the_two_sets_partition_the_runner_gate_names(self):
        text = DEPLOY_SH.read_text(encoding="utf-8")
        declared = _declared(text, "SITUATION_SELF_GATES") | _declared(
            text, "SITUATION_HUMAN_GATES")
        named = set(re.findall(r"PREFLIGHT_GATE=([A-Za-z0-9_]+)", text))
        assert named, "the call sites are what a gate name is read from"
        missing = named - declared
        assert not missing, (
            f"these pre-merge refusals have no disposition, so the page could not "
            f"say which kind is happening: {sorted(missing)}")
        extra = declared - named
        assert not extra, (
            f"these names are classified but the runner never refuses with them: "
            f"{sorted(extra)}")

    def test_the_two_sets_are_disjoint(self):
        text = DEPLOY_SH.read_text(encoding="utf-8")
        both = _declared(text, "SITUATION_SELF_GATES") & _declared(
            text, "SITUATION_HUMAN_GATES")
        assert not both, f"a gate cannot be both: {sorted(both)}"

    def test_the_runner_gate_vocabulary_is_the_service_s(self):
        text = DEPLOY_SH.read_text(encoding="utf-8")
        declared = _declared(text, "SITUATION_SELF_GATES") | _declared(
            text, "SITUATION_HUMAN_GATES")
        assert declared == set(status.PREFLIGHT_GATES), (
            "the page's own list of pre-merge steps and the runner's dispositions "
            "have to name the same steps, or one of them is wrong")


# ── 3. the record is written where the exit trap already looks ───────────────

class TestTheRunnerWiring:
    def test_the_exit_trap_records_it_and_a_finished_run_clears_it(self):
        text = DEPLOY_SH.read_text(encoding="utf-8")
        trap = text.split("on_exit() {", 1)[1].split("\n}", 1)[0]
        assert "last_stop_write" in trap, "the trap is where every non-zero exit lands"
        assert "situation_classify" in trap and "situation_write" in trap, (
            "the classification has to be written by the trap, where the step and "
            "the code are both still in scope")
        assert "situation_clear" in trap, (
            "a run that finishes must clear the record, or a cleared box keeps "
            "reading as one that is stuck")

    def test_a_merge_clears_a_pre_merge_situation(self):
        """At the merge site, not inside `preflight_forget`.

        Each block in this script is run on its own by its tests, so a call across
        two of them is a coupling neither block's harness can provide — the preflight
        suite runs `preflight_forget` with only the preflight block loaded. The clear
        therefore lives beside the one call site, where the whole script is in scope.
        """
        text = DEPLOY_SH.read_text(encoding="utf-8")
        forget = text.split("preflight_forget() {", 1)[1].split("\n}", 1)[0]
        assert "situation_clear" not in forget, (
            "`preflight_forget` runs alone in its own suite; it must not reach a "
            "function from another block")
        assert "\npreflight_forget\n" in text, "the merge call site must be a line of its own"
        after_call = text.split("\npreflight_forget\n", 1)[1][:400]
        assert "situation_clear" in after_call, (
            "a merge is the moment a pre-merge refusal stopped being true, and the "
            "call site is the only place with the whole script in scope")


# ── 4. the reader ────────────────────────────────────────────────────────────

def _now():
    return status._dt.datetime(2026, 9, 27, 5, 0, tzinfo=status._dt.timezone.utc)


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "situation"
    path.write_text(text, encoding="utf-8")
    return path


class TestTheReader:
    def test_an_absent_record_is_none_rather_than_an_error(self, tmp_path):
        state = status.situation_state(tmp_path / "missing", now=_now())
        assert state["key"] == status.SITUATION_NONE
        assert state["disposition_key"] is None and state["action_key"] is None

    def test_it_reads_the_five_fields(self, tmp_path):
        path = _write(tmp_path, "\n".join(
            ["self", "preflight", "fetch_failed", "wait", "2026-09-27T04:00:00+00:00"])
            + "\n")
        state = status.situation_state(path, now=_now())
        assert state["key"] == status.SITUATION_PRESENT
        assert state["disposition_key"] == "self"
        assert state["disposition"] == "self"
        assert state["source_key"] == "preflight"
        assert state["gate"] == "fetch_failed"
        assert state["action_key"] == "wait"
        assert state["button"] is None, "a self-resolving refusal offers no button"

    def test_a_release_offers_the_release_button(self, tmp_path):
        path = _write(tmp_path, "\n".join(
            ["human", "quarantine", "", "release", "2026-09-27T04:00:00+00:00"]) + "\n")
        state = status.situation_state(path, now=_now(), held=True)
        assert state["button"] == "release"

    def test_a_re_baseline_offers_the_re_baseline_button(self, tmp_path):
        path = _write(tmp_path, "\n".join(
            ["human", "quarantine", "perf_gate", "rebaseline",
             "2026-09-27T04:00:00+00:00"]) + "\n")
        state = status.situation_state(path, now=_now(), held=True)
        assert state["button"] == "rebaseline"

    def test_a_console_action_offers_no_button(self, tmp_path):
        path = _write(tmp_path, "\n".join(
            ["human", "unarmed", "", "console", "2026-09-27T04:00:00+00:00"]) + "\n")
        state = status.situation_state(path, now=_now(), held=True)
        assert state["action_key"] == "console" and state["button"] is None

    def test_an_unreadable_record_is_reported_not_absent(self, tmp_path):
        state = status.situation_state(tmp_path / "missing-dir" / "situation",
                                       now=_now())
        assert state["key"] in (status.SITUATION_NONE, status.SITUATION_UNREADABLE)

    @pytest.mark.parametrize("text", [
        "wat\npreflight\n\nwait\n2026-09-27T04:00:00+00:00\n",
        "self\npreflight\n\nfly\n2026-09-27T04:00:00+00:00\n",
        "\n",
    ])
    def test_a_record_it_cannot_trust_is_malformed(self, tmp_path, text):
        path = _write(tmp_path, text)
        state = status.situation_state(path, now=_now())
        assert state["key"] == status.SITUATION_MALFORMED, (
            f"a value outside the vocabulary must not be guessed at: {text!r}")

    def test_the_report_carries_it(self, tmp_path):
        path = _write(tmp_path, "\n".join(
            ["human", "quarantine", "perf_gate", "rebaseline",
             "2026-09-27T04:00:00+00:00"]) + "\n")
        quarantine = tmp_path / "quarantined"
        quarantine.write_text(
            "\n".join(["a" * 40, "2026-09-27T04:00:00+00:00",
                       "perf gate (slower than the last release that passed)"]) + "\n",
            encoding="utf-8")
        report = status.report(repo="/nonexistent", runner="/nonexistent",
                               snapshot_runner="/nonexistent",
                               pause_file="/nonexistent", quarantine_file=quarantine,
                               unarmed_file="/nonexistent", preflight_file="/nonexistent",
                               last_stop_file="/nonexistent", situation_file=path,
                               now=_now())
        assert report["situation"]["key"] == status.SITUATION_PRESENT
        assert report["situation"]["button"] == "rebaseline"

    def test_a_button_needs_a_commit_actually_held(self, tmp_path):
        """A release button with nothing held would write a request that is spent
        on the next tick's release — the standing override the runner refuses."""
        path = _write(tmp_path, "\n".join(
            ["human", "quarantine", "", "release", "2026-09-27T04:00:00+00:00"]) + "\n")
        report = status.report(repo="/nonexistent", runner="/nonexistent",
                               snapshot_runner="/nonexistent",
                               pause_file="/nonexistent",
                               quarantine_file="/nonexistent",
                               unarmed_file="/nonexistent", preflight_file="/nonexistent",
                               last_stop_file="/nonexistent", situation_file=path,
                               now=_now())
        assert report["situation"]["action_key"] == "release"
        assert report["situation"]["button"] is None, (
            "no request may be offered when no commit is held for it to release")


# ── 5. the page ──────────────────────────────────────────────────────────────

class TestThePage:
    def _html(self, app, tmp_path, text, *, held=True):
        path = _write(tmp_path, text)
        quarantine = tmp_path / "quarantined"
        if held:
            quarantine.write_text(
                "\n".join(["a" * 40, "2026-09-27T04:00:00+00:00",
                           "perf gate (slower than the last release that passed)"])
                + "\n", encoding="utf-8")
        report = status.report(repo="/nonexistent", runner="/nonexistent",
                               snapshot_runner="/nonexistent",
                               pause_file="/nonexistent",
                               quarantine_file=quarantine,
                               unarmed_file="/nonexistent",
                               preflight_file="/nonexistent",
                               last_stop_file="/nonexistent", situation_file=path,
                               now=_now())
        from flask import g, render_template
        with app.test_request_context("/super-admin/deploy-status"):
            g.user_id = "sa"
            g.user_role = "super_admin"
            g.user_name = "T"
            g.user_email = "t@t"
            g.tz_offset = 7
            return render_template("super_admin/deploy_status.html", status=report,
                                   alerts=None, testalert=None, released=None)

    def test_a_self_resolving_refusal_offers_no_button(self, app, tmp_path):
        html = self._html(app, tmp_path, "\n".join(
            ["self", "preflight", "fetch_failed", "wait",
             "2026-09-27T04:00:00+00:00"]) + "\n")
        assert "deploy-status/release" not in html, (
            "waiting is not a button: a self-resolving refusal must not invite a click")
        assert "deploy-status/rebaseline" not in html, html[:200]

    def test_a_held_commit_renders_exactly_one_action(self, app, tmp_path):
        html = self._html(app, tmp_path, "\n".join(
            ["human", "quarantine", "", "release",
             "2026-09-27T04:00:00+00:00"]) + "\n")
        assert html.count('action="/super-admin/deploy-status/release"') == 1, (
            "the human case is the one place the page may offer an action, and it "
            "offers exactly one")

    def test_the_perf_refusal_offers_the_re_baseline_not_the_release(self, app, tmp_path):
        html = self._html(app, tmp_path, "\n".join(
            ["human", "quarantine", "perf_gate", "rebaseline",
             "2026-09-27T04:00:00+00:00"]) + "\n")
        assert html.count('action="/super-admin/deploy-status/rebaseline"') == 1, (
            "one button, and it is the one that re-measures the box")
        assert 'action="/super-admin/deploy-status/release"' not in html, html[:200]

    def test_the_quarantine_card_stops_offering_its_own_forms(self):
        """One place to press, and it is the situation card.

        Rendered, this is hard to pin: the quarantine card only shows its request
        forms when the request directory is writable, which a test's scratch tree is
        not — so its buttons are absent for the wrong reason and a mutation that
        removes the suppression stays green (measured: the mutator's 7th defect
        survived). The property is in the template's structure, so it is read there.
        """
        text = TEMPLATE.read_text(encoding="utf-8")
        assert "status.situation is not defined or status.situation.key != 'present'" in text, (
            "with the situation card up, the quarantine card must not offer its own "
            "request forms: two buttons that do one thing is the confusion this "
            "card exists to remove")

    def test_a_console_action_offers_nothing_to_click(self, app, tmp_path):
        html = self._html(app, tmp_path, "\n".join(
            ["human", "unarmed", "", "console", "2026-09-27T04:00:00+00:00"]) + "\n",
            held=False)
        assert "deploy-status/release" not in html and \
            "deploy-status/rebaseline" not in html, (
            "arming the box is not a request a page can write")


# ── 6. the vocabulary lives in one place ─────────────────────────────────────

class TestTheVocabulary:
    def test_every_disposition_and_action_has_a_sentence_source(self):
        assert status.SITUATION_DISPOSITIONS == frozenset({"self", "human"})
        assert status.SITUATION_ACTIONS == frozenset(
            {"wait", "release", "rebaseline", "console"})
        text = TEMPLATE.read_text(encoding="utf-8")
        for word in sorted(status.SITUATION_DISPOSITIONS):
            assert f"situation.disposition_key == '{word}'" in text, (
                f"the page has no sentence for the `{word}` disposition, so it would "
                f"render as a blank line")
        for word in sorted(status.SITUATION_ACTIONS):
            assert f"action_key == '{word}'" in text or word == "wait", (
                f"the page has no sentence for the `{word}` action")
