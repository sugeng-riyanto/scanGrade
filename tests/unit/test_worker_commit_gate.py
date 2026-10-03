"""The app is not the only process that has to be running this release.

The served-commit gate checks gunicorn. The Celery worker is the *other* process
holding the same checkout, it starts once and keeps its imported task modules in
memory for the life of the process, and it has no HTTP surface to be asked on. So a
release that reloads the app and leaves the worker on the previous revision is a
**half-deployed release** — and it is not theoretical: `page_index` was added to the
OMR task's signature and to its caller in one commit, the deploy restarted gunicorn
alone, and every scan then failed with

    process_omr_scan() got an unexpected keyword argument 'page_index'

— the caller new, the worker old, and the box reporting a successful release.

So the worker answers the same question the app answers, over the broker it already
uses: a `served_commit` control command registered in `app/celery_app.py`, returning
`build_info.snapshot()` — the commit whose code the worker loaded, resolved at import.

One thing is deliberately different from the app gate: **the worker has no HTTP
surface, so silence has to be read twice.** A worker that is *down* and a worker
*built before this reading* both answer nothing to the `served_commit` command, and
once they were indistinguishable — so a dead worker was reported as "could not
measure" and slipped past the deploy. They are told apart by a second question:
Celery's built-in `ping`, which every worker answers whatever code it loaded, because
it is not this reading at all. Nobody answering either question is a worker that is
not running — a release with no worker cannot process a single scan — so that
**refuses** (exit 4). An answered `ping` with an unanswered `served_commit` is a
worker built before the reading, which says nothing about *this* release and stays
"could not measure" (exit 2). A worker that **answers and names a different commit**
refuses as before (exit 3), the exact state that breaks scans.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

GATE_PY = ROOT / "deploy" / "worker_commit_gate.py"
RUNNER = ROOT / "deploy" / "scangrade-deploy.sh"
CELERY_APP = ROOT / "app" / "celery_app.py"
BUILD_INFO = ROOT / "app" / "utils" / "build_info.py"
SERVICE = ROOT / "app" / "services" / "deploy_status_service.py"
TEMPLATE = ROOT / "app" / "templates" / "super_admin" / "deploy_status.html"

MERGED = "a32585a1b2c3d4e5f60718293a4b5c6d7e8f9012"
PREVIOUS = "0b20b204f5e6d7c8b9a0123456789abcdef01234"



def _load_gate():
    spec = importlib.util.spec_from_file_location("worker_commit_gate", GATE_PY)
    assert spec and spec.loader, f"{GATE_PY} cannot be imported (does it exist?)"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gate():
    return _load_gate()


# ── the worker publishes what it runs ────────────────────────────────────────

def test_the_worker_registers_a_command_that_answers_with_its_own_commit():
    source = CELERY_APP.read_text(encoding="utf-8")
    assert "control_command" in source, (
        "the worker registers no control command, so it cannot be asked which "
        "commit it is running")
    assert "served_commit" in source, "the command has no name the gate can broadcast"
    assert "build_info" in source and "snapshot()" in source, (
        "the command does not answer with the process's own commit reading")
    # The reading must be the import-time one, never a fresh `read_commit`: a worker
    # asked *after* the checkout moved would name the merged commit while running the
    # code it loaded yesterday — the exact false negative this whole feature defeats.
    assert "read_commit(" not in source, (
        "the command reads the checkout at call time instead of answering with the "
        "commit whose code this process loaded")


def test_the_worker_carries_the_marker_the_gate_asks_about(gate):
    source = CELERY_APP.read_text(encoding="utf-8")
    assert gate.REPORTER_MARKER in source, (
        f"app/celery_app.py does not carry {gate.REPORTER_MARKER!r}, so the runner's "
        f"--reporter cannot tell whether this release ships the reading")


# ── reading one worker's answer ──────────────────────────────────────────────

def _reading(payload, gate):
    return gate.reading_of(payload)


def test_a_worker_that_named_its_commit_is_a_reading(gate):
    reading = _reading({"available": True, "reason_key": None, "commit": MERGED[:7],
                        "full_commit": MERGED, "pid": 10}, gate)
    assert reading.state == gate.READING_OK
    assert reading.commit == MERGED


def test_a_worker_that_cannot_place_itself_is_unnamed_not_a_mismatch(gate):
    reading = _reading({"available": False, "reason_key": "no_git"}, gate)
    assert reading.state == gate.READING_UNNAMED


def test_an_answer_without_the_block_is_absent(gate):
    reading = _reading({"ok": "something else"}, gate)
    assert reading.state == gate.READING_ABSENT


@pytest.mark.parametrize("payload", ["not a dict", 7, None, ["a", "b"]])
def test_an_answer_that_is_not_a_block_is_unreadable(payload, gate):
    assert _reading(payload, gate).state == gate.READING_UNREADABLE


def test_a_worker_that_errored_cannot_name_its_commit(gate):
    reading = _reading({"error": "KeyError: 'x'"}, gate)
    assert reading.state == gate.READING_UNREADABLE


def test_available_with_no_commit_is_unreadable_not_absent(gate):
    """`available: true` and nothing in it is a body this gate cannot read."""
    reading = _reading({"available": True, "commit": None, "full_commit": None}, gate)
    assert reading.state == gate.READING_UNREADABLE


# ── judging the answers ──────────────────────────────────────────────────────

def _ok(commit, gate):
    return gate.Reading(gate.READING_OK, commit, "named")


def test_judging_a_worker_on_the_merged_commit(gate):
    verdict, why = gate.judge([_ok(MERGED, gate)], MERGED, reporter_ships=True)
    assert verdict == gate.VERDICT_OK


def test_judging_a_worker_on_another_commit_is_a_mismatch(gate):
    verdict, why = gate.judge([_ok(PREVIOUS, gate)], MERGED, reporter_ships=True)
    assert verdict == gate.VERDICT_MISMATCH
    assert PREVIOUS[:7] in why and MERGED[:7] in why, why


def test_a_later_probe_naming_the_merged_commit_passes(gate):
    readings = [_ok(PREVIOUS, gate), _ok(MERGED, gate)]
    assert gate.judge(readings, MERGED, reporter_ships=True)[0] == gate.VERDICT_OK


def test_no_worker_answering_and_no_liveness_reading_is_never_a_refusal(gate):
    """Liveness not measured: an unconfirmed silence is not evidence."""
    verdict, why = gate.judge([], MERGED, reporter_ships=True, worker_alive=None)
    assert verdict == gate.VERDICT_UNMEASURED
    assert verdict != gate.VERDICT_MISMATCH


def test_a_worker_that_is_down_refuses_the_release(gate):
    """Nobody answered the commit question, and nobody answered `ping` either."""
    verdict, why = gate.judge([], MERGED, reporter_ships=True, worker_alive=False)
    assert verdict == gate.VERDICT_DOWN, why
    assert verdict != gate.VERDICT_UNMEASURED, (
        "a genuinely dead worker slipped past the release")


def test_a_worker_built_before_the_reading_is_not_a_down_worker(gate):
    """`ping` answered but `served_commit` did not: old code, not a dead process."""
    reading = gate.Reading(gate.READING_ABSENT, None, "a release from before the check")
    verdict, why = gate.judge([reading], MERGED, reporter_ships=True, worker_alive=True)
    assert verdict == gate.VERDICT_UNMEASURED, why


def test_a_dead_worker_is_never_read_as_a_commit_mismatch(gate):
    verdict, why = gate.judge([], MERGED, reporter_ships=True, worker_alive=False)
    assert verdict != gate.VERDICT_MISMATCH


def test_an_unnamed_worker_is_not_a_mismatch(gate):
    reading = gate.Reading(gate.READING_UNNAMED, None, "no git")
    assert gate.judge([reading], MERGED, reporter_ships=True)[0] == gate.VERDICT_UNMEASURED


def test_a_missing_commit_to_compare_against_is_not_a_refusal(gate):
    assert gate.judge([_ok(MERGED, gate)], "", reporter_ships=True)[0] == \
        gate.VERDICT_UNMEASURED


# ── the gate as a program ───────────────────────────────────────────────────

def _run_main(gate, monkeypatch, replies, *, argv, alive=None):
    monkeypatch.setattr(gate, "worker_replies", lambda repo, *, timeout: list(replies))
    # Liveness is its own broadcast; a test that only fixed the commit answers must
    # not fall through to a real broker. `None` is "not measured", the safe default.
    monkeypatch.setattr(
        gate, "workers_alive",
        lambda repo, *, timeout, attempts=None, gap=None: alive)
    monkeypatch.setattr(gate, "reporter_ships", lambda path: True)
    monkeypatch.setattr(sys, "argv", ["worker_commit_gate.py", *argv])
    return gate.main()


def test_the_gate_passes_when_the_worker_runs_the_merged_commit(gate, monkeypatch, capsys):
    rc = _run_main(gate, monkeypatch,
                   [{"available": True, "full_commit": MERGED, "commit": MERGED[:7]}],
                   argv=["--commit", MERGED, "--attempts", "1"])
    assert rc == gate.EXIT_OK
    assert "worker commit: OK" in capsys.readouterr().out


def test_the_gate_refuses_when_the_worker_runs_another_commit(gate, monkeypatch, capsys):
    rc = _run_main(gate, monkeypatch,
                   [{"available": True, "full_commit": PREVIOUS, "commit": PREVIOUS[:7]}],
                   argv=["--commit", MERGED, "--attempts", "1"])
    assert rc == gate.EXIT_REFUSED
    assert "REFUSED" in capsys.readouterr().out


def test_the_gate_does_not_refuse_a_worker_that_did_not_answer(gate, monkeypatch, capsys):
    rc = _run_main(gate, monkeypatch, [], argv=["--commit", MERGED, "--attempts", "1"],
                   alive=None)
    assert rc == gate.EXIT_UNMEASURED
    assert "CANNOT MEASURE" in capsys.readouterr().out


def test_the_gate_refuses_when_no_worker_is_alive(gate, monkeypatch, capsys):
    rc = _run_main(gate, monkeypatch, [], argv=["--commit", MERGED, "--attempts", "1"],
                   alive=False)
    assert rc == gate.EXIT_DOWN, (
        "a genuinely dead worker did not fail the release")
    assert "REFUSED" in capsys.readouterr().out


def test_the_gate_keeps_a_worker_built_before_the_reading(gate, monkeypatch, capsys):
    rc = _run_main(gate, monkeypatch, [], argv=["--commit", MERGED, "--attempts", "1"],
                   alive=True)
    assert rc == gate.EXIT_UNMEASURED, (
        "a worker built before the reading refused a release")
    assert "CANNOT MEASURE" in capsys.readouterr().out


def test_the_gate_keeps_a_release_when_liveness_could_not_be_read(gate, monkeypatch,
                                                                  capsys):
    rc = _run_main(gate, monkeypatch, [], argv=["--commit", MERGED, "--attempts", "1"],
                   alive=None)
    assert rc == gate.EXIT_UNMEASURED, "an unreadable liveness probe refused a release"


def test_the_down_code_is_neither_a_crash_nor_the_mismatch_code(gate):
    assert gate.EXIT_DOWN != 1, (
        "python exits 1 on an uncaught exception, so a crashed gate would be read as "
        "a refusal")
    assert gate.EXIT_DOWN != gate.EXIT_REFUSED, (
        "a dead worker and a mismatched worker are two different findings")


def test_the_refusal_code_is_not_the_one_a_crash_produces(gate):
    assert gate.EXIT_REFUSED != 1, (
        "python exits 1 on an uncaught exception, so a gate that crashed would be "
        "read by the runner as a gate that refused")


def test_a_crash_inside_the_gate_is_cannot_measure_not_refusal(gate, monkeypatch, capsys):
    def boom(repo, *, timeout):
        raise RuntimeError("the broker is on fire")
    monkeypatch.setattr(gate, "worker_replies", boom)
    monkeypatch.setattr(gate, "reporter_ships", lambda path: True)
    monkeypatch.setattr(sys, "argv",
                        ["worker_commit_gate.py", "--commit", MERGED, "--attempts", "1"])
    rc = gate.main()
    assert rc == gate.EXIT_UNMEASURED, "a crashed gate refused a release"
    assert "worker commit:" in capsys.readouterr().out


# ── the runner asks the worker, and acts on the answer ───────────────────────

def _gate_block() -> str:
    script = RUNNER.read_text(encoding="utf-8")
    assert "# worker-commit-gate:start" in script and "# worker-commit-gate:end" in script, (
        "the runner's worker-commit block has no delimiters, so it cannot be lifted "
        "out and tested on its own the way the other gates' are")
    return script.split("# worker-commit-gate:start", 1)[1].split(
        "# worker-commit-gate:end", 1)[0]


def test_the_runner_asks_the_worker_what_it_is_running():
    script = RUNNER.read_text(encoding="utf-8")
    assert "deploy/worker_commit_gate.py" in script, (
        "nothing in the runner compares the worker's commit with the merged one")
    block = _gate_block()
    assert re.search(r'--commit\s+"\$?AFTER_FULL', block), (
        "the gate is not told which commit this run merged")
    assert re.search(r'--reporter\s+"\$?REPO/app/celery_app\.py"', block), (
        "the gate is not told which file to ask whether the release ships the reading")
    assert re.search(r'--repo\s+"\$?REPO"', block), (
        "the gate is not told which checkout to import the worker's app from")


def test_the_worker_check_runs_after_the_reload_and_before_the_gates_it_protects():
    script = RUNNER.read_text(encoding="utf-8")
    at = script.index("# worker-commit-gate:start")
    assert script.index("reload_worker") < at, (
        "the worker is asked before it was restarted")
    assert script.index("# served-commit-gate:end") < at, (
        "the app is not confirmed as the code serving before the worker is asked")
    assert at < script.index('RUN_STEP="verify"'), (
        "the worker is asked after the smoke gate, so that gate can report on a "
        "release the worker is not running")


def test_a_worker_mismatch_is_refused_and_quarantined():
    block = _gate_block()
    assert re.search(r"HEALTHY=0", block), (
        "a worker mismatch does not mark the release unhealthy, so a half-deployed "
        "release reports success")
    reason = re.search(r'FAIL_REASON="([^"\n]*)"', block)
    assert reason, "the refusal names no gate"
    service = SERVICE.read_text(encoding="utf-8")
    keys = re.search(r"GATE_KEYS = frozenset\(\{(.*?)\}\)", service, re.S).group(1)
    slug = re.sub(r"[^a-z0-9]+", "_", reason.group(1).split("(", 1)[0].strip().lower())
    assert f'"{slug}"' in keys, (
        f"the runner records {reason.group(1)!r}, which the status page has no key for")


def test_the_page_and_the_runner_agree_on_the_worker_step_and_gate():
    service = SERVICE.read_text(encoding="utf-8")
    steps = re.search(r"RUN_STEPS = frozenset\(\{(.*?)\}\)", service, re.S).group(1)
    assert '"worker"' in steps, (
        "the runner names a `worker` step that the status page would show as unknown")
    template = TEMPLATE.read_text(encoding="utf-8")
    assert "ls.step_key == 'worker'" in template, (
        "the deploy-status page has no sentence for the worker step")
    assert '"worker_commit"' in service, "the worker gate has no key"
    assert "q.gate_key == 'worker_commit'" in template, (
        "the deploy-status page has no sentence for a worker-commit refusal")


# ── and the runner's own branching, run for real ─────────────────────────────

BASH = shutil.which("bash")

REFUSAL_LINES = ("worker commit: REFUSED — none of 3 worker(s) reported a32585a; "
                 "the worker reports 0b20b20")


def _runner_harness(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / ".venv" / "bin").mkdir(parents=True)
    (repo / "deploy").mkdir()
    (repo / "app").mkdir()
    (repo / "app" / "celery_app.py").write_text("# worker-commit:start\n", encoding="utf-8")
    (repo / ".venv" / "bin" / "python").write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" > "${WORKER_FAKE_ARGS}"\n'
        'cat "${WORKER_FAKE_OUT}"\n'
        'exit "${WORKER_FAKE_RC}"\n',
        encoding="utf-8", newline="\n")
    (repo / ".venv" / "bin" / "python").chmod(0o755)
    return repo


def _run_block(tmp_path: Path, repo: Path, rc: int, out: str):
    body = tmp_path / "worker-out.txt"
    body.write_text(out + ("\n" if out else ""), encoding="utf-8")
    program = (
        f'export WORKER_FAKE_ARGS="{tmp_path.as_posix()}/called-with.txt"\n'
        "set -uo pipefail\n"
        f'REPO="{repo.as_posix()}"\n'
        f'AFTER_FULL="{MERGED}"\n'
        f'BEFORE="{PREVIOUS[:7]}"\n'
        'WORKER_UNIT="scangrade-celery"\n'
        'HEALTHY=1\nFAIL_REASON=""\nFAIL_DETAIL=""\nRUN_STEP=""\n'
        'log() { echo "LOG: $*"; }\n'
        'as_owner() { "$@"; }\n'
        'systemctl() { return 0; }\n'          # `systemctl cat` finds the unit
        f'export WORKER_FAKE_OUT="{body.as_posix()}"\n'
        f'export WORKER_FAKE_RC={rc}\n'
        + _gate_block()
        + '\nprintf "AFTER healthy=%s reason=[%s] detail=[%s] step=[%s]\\n" '
          '"$HEALTHY" "$FAIL_REASON" "$FAIL_DETAIL" "$RUN_STEP"\n')
    script = tmp_path / "worker-block.sh"
    script.write_text(program, encoding="utf-8", newline="\n")
    return subprocess.run([BASH, str(script)], capture_output=True, text=True)


@pytest.mark.skipif(BASH is None, reason="needs a bash to run the runner's block")
class TestTheRunnerActsOnTheVerdict:
    def test_a_pass_leaves_the_release_healthy(self, tmp_path):
        run = _run_block(tmp_path, _runner_harness(tmp_path), 0,
                         "worker commit: OK — the worker is running a32585a")
        assert run.returncode == 0, run.stderr
        assert "reason=[]" in run.stdout and "healthy=1" in run.stdout, run.stdout

    def test_a_mismatch_marks_the_release_unhealthy_and_names_the_gate(self, tmp_path):
        run = _run_block(tmp_path, _runner_harness(tmp_path), 3, REFUSAL_LINES)
        assert run.returncode == 0, run.stderr
        assert "healthy=0" in run.stdout, (
            "a worker mismatch does not reach the shared rollback path, so the "
            "half-deployed release is kept: " + run.stdout)
        assert "worker commit (" in run.stdout, run.stdout
        assert "0b20b20" in run.stdout and "a32585a" in run.stdout, (
            "the refusal's own lines are not quoted into the record")
        assert "step=[worker]" in run.stdout, (
            "the refusal names no step, so the last-stop record cannot say where the "
            "run died")

    def test_a_down_worker_is_not_reported_as_the_mismatch_gate(self, tmp_path):
        run = _run_block(tmp_path, _runner_harness(tmp_path), 4,
                         "worker commit: REFUSED — no Celery worker answered")
        assert "worker commit (" not in run.stdout, (
            "a dead worker is reported under the mismatch reason, which tells the "
            "operator to look for a wrong commit rather than a worker that is down: "
            + run.stdout)

    def test_the_call_it_makes_names_the_merged_commit_the_reporter_and_the_repo(self, tmp_path):
        _run_block(tmp_path, _runner_harness(tmp_path), 0, "worker commit: OK")
        called = (tmp_path / "called-with.txt").read_text(encoding="utf-8")
        assert f"--commit {MERGED}" in called, called
        assert "--reporter" in called and "app/celery_app.py" in called, called
        assert "--repo" in called, called

    def test_a_could_not_measure_answer_keeps_the_release(self, tmp_path):
        run = _run_block(tmp_path, _runner_harness(tmp_path), 2,
                         "worker commit: CANNOT MEASURE — no worker answered")
        assert run.returncode == 0, run.stderr
        assert "healthy=1" in run.stdout and "reason=[]" in run.stdout, (
            "a worker that cannot be asked rolls the release back — a box property "
            "quarantining a commit: " + run.stdout)

    def test_a_down_worker_marks_the_release_unhealthy_and_names_the_gate(self,
                                                                          tmp_path):
        run = _run_block(tmp_path, _runner_harness(tmp_path), 4,
                         "worker commit: REFUSED — no Celery worker answered the "
                         "liveness probe: the worker is down")
        assert run.returncode == 0, run.stderr
        assert "healthy=0" in run.stdout, (
            "a genuinely dead worker did not reach the rollback path: " + run.stdout)
        assert "worker down (" in run.stdout, run.stdout
        assert "step=[worker]" in run.stdout, (
            "the refusal names no step, so the last-stop record cannot say where the "
            "run died")

    def test_a_gate_that_crashed_is_not_read_as_a_refusal(self, tmp_path):
        run = _run_block(tmp_path, _runner_harness(tmp_path), 1,
                         "Traceback (most recent call last):\nRuntimeError: boom")
        assert run.returncode == 0, run.stderr
        assert "healthy=1" in run.stdout and "reason=[]" in run.stdout, (
            "the gate crashed and the runner read it as a refusal: " + run.stdout)
        assert "RuntimeError: boom" in run.stdout, (
            "the crash is not quoted, so the journal says nothing about why")
