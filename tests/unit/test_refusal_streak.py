"""A refusal the runner has already made is not made again.

The box this repository was being fixed for had been refused for a day: `M
app/routes/admin_sekolah.py`, `check out has local changes - NOT deploying`, exit 4,
every two minutes. A modern runner heals that on the *first* tick — the path overlaps
the release, so it is set aside and the merge proceeds. What is left is the shape the
heal itself cannot complete: the state directory cannot be created, or the disk that
holds it is full, or `git diff` cannot write. Then the same refusal repeats, tick after
tick, with the same paths, and every tick it repeats is a tick the box does not
release — a refusal that has become a loop, wearing the clothes of patience.

So the runner keeps count. `refusal-streak-logic` records what a refusal was about
(its paths, not its prose: the same sentence about different files is a different
problem) and how many ticks running it has been made. Past `REFUSAL_STREAK_MAX` the
box-local-edit heal stops re-attempting the shape that failed and uses the degraded
one instead — a fallback directory on another filesystem, whole files instead of a
diff — and the release proceeds.

Two properties are run here rather than read, because the whole point is what happens
across ticks and a single invocation cannot show it: the count is **per path set** (a
different edit is a different problem and starts again at one), and a count that
cannot be read is **zero rather than a crash** — this file is state left behind by an
older run, and the runner is the one process that must never fail to start because of
it.

Run: the block is extracted between its delimiters and executed against a real
`$STATE_DIR` in `tmp_path`, the way the quarantine's, the lock heal's and the
box-edit heal's are.
"""
from __future__ import annotations

import pathlib
import shlex
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNNER = ROOT / "deploy" / "scangrade-deploy.sh"
BASH = shutil.which("bash")

START = "# refusal-streak-logic:start"
END = "# refusal-streak-logic:end"

pytestmark = pytest.mark.skipif(BASH is None, reason="needs a bash to run the block")


def _text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _block() -> str:
    script = _text(RUNNER)
    assert START in script and END in script, (
        "the block's delimiters are what let it be run on its own, the way the "
        "quarantine's, the lock heal's and the box-edit heal's are; keep them")
    return script.split(START, 1)[1].split(END, 1)[0]


def _code() -> str:
    return "\n".join(ln for ln in _block().splitlines()
                     if ln.strip() and not ln.strip().startswith("#"))


def _constant(name: str) -> str:
    """One of the block's own constants, read out of it rather than restated here.

    Paths carry an `SG_*` override and thresholds do not — the script's own
    convention, so the fallback home is written `${SG_…:-/run/…}` and the threshold is
    a bare number. Both forms are resolved here, because a guard that saw the override
    syntax would be asserting about the *spelling* of a default rather than the
    default.
    """
    for line in _block().splitlines():
        line = line.strip()
        if line.startswith(f"{name}="):
            value = line.split("=", 1)[1].strip().strip('"')
            if value.startswith("${") and ":-" in value:
                value = value.split(":-", 1)[1].rstrip("}")
            return value
    raise AssertionError(f"{name} is not set in the block")


def _harness(tmp_path: pathlib.Path, tail: str, *,
             state: pathlib.Path | None = None) -> str:
    state = state if state is not None else tmp_path / "state"
    return (
        "set -uo pipefail\n"
        f"STATE_DIR={shlex.quote(state.as_posix())}\n"
        'log() { printf \'LOG %s\\n\' "$*"; }\n'
        + _block()
        + "\n"
        + tail
    )


def _run(tmp_path: pathlib.Path, tail: str, *,
         state: pathlib.Path | None = None) -> subprocess.CompletedProcess:
    script = tmp_path / "harness.sh"
    script.write_text(_harness(tmp_path, tail, state=state), encoding="utf-8",
                      newline="\n")
    return subprocess.run([BASH, str(script)], capture_output=True, text=True,
                          check=False)


#: One tick's worth of refusal, in the shape the runner makes it: the paths about to
#: be refused, then the record.
ONE = ("REFUSAL_PATHS=\"app/routes/admin_sekolah.py\"\n"
       "refusal_streak_record\n"
       "printf 'COUNT %s\\n' \"$REFUSAL_STREAK_COUNT\"\n")


def _counter(state: pathlib.Path) -> str:
    return (state / "refusal-streak").read_text(encoding="utf-8").strip()


def _paths(state: pathlib.Path) -> str:
    return (state / "refusal-streak.paths").read_text(encoding="utf-8")


# ── 1. the memory, across ticks ──────────────────────────────────────────────

def test_the_first_refusal_is_a_first_refusal(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    ran = _run(tmp_path, ONE + "if refusal_streak_exhausted; then echo LOOP; else echo FRESH; fi\n",
               state=state)
    assert ran.returncode == 0, ran.stderr
    assert "COUNT 1" in ran.stdout, ran.stdout
    assert "FRESH" in ran.stdout, "one refusal was already treated as a loop"
    assert _counter(state) == "1"
    assert _paths(state) == "app/routes/admin_sekolah.py\n", (
        "the streak does not say which paths it is about, so it cannot tell one "
        "refusal from another")


def test_the_same_paths_three_ticks_running_are_a_loop(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    ran = _run(tmp_path, ONE * 3 + "if refusal_streak_exhausted; then echo LOOP; else echo FRESH; fi\n",
               state=state)
    assert ran.returncode == 0, ran.stderr
    assert "COUNT 3" in ran.stdout, ran.stdout
    assert "LOOP" in ran.stdout, (
        "three ticks of the same refusal is a box that does not release, and the "
        "runner still called it patience")


def test_a_different_path_set_starts_the_count_again(tmp_path):
    """The same sentence about different files is a different problem."""
    state = tmp_path / "state"
    state.mkdir()
    tail = ("REFUSAL_PATHS=\"app/routes/admin_sekolah.py\"\nrefusal_streak_record\n"
            "REFUSAL_PATHS=\"app/routes/admin_sekolah.py\"\nrefusal_streak_record\n"
            "REFUSAL_PATHS=\"app/models/exam.py\"\nrefusal_streak_record\n"
            "printf 'COUNT %s\\n' \"$REFUSAL_STREAK_COUNT\"\n"
            "if refusal_streak_exhausted; then echo LOOP; else echo FRESH; fi\n")
    ran = _run(tmp_path, tail, state=state)
    assert ran.returncode == 0, ran.stderr
    assert "COUNT 1" in ran.stdout, ran.stdout
    assert "FRESH" in ran.stdout, "an unrelated edit inherited the other one's count"
    assert _paths(state) == "app/models/exam.py\n"


def test_a_count_it_cannot_read_is_zero_rather_than_a_crash(tmp_path):
    """This file is state left behind by an older run of a script that may since have
    changed its format, and the runner is one process that must never fail to start
    because of it."""
    state = tmp_path / "state"
    (state / "set-aside").mkdir(parents=True)          # the dir exists, the file is junk
    (state / "refusal-streak").write_text("not a number\n", encoding="utf-8")
    (state / "refusal-streak.paths").write_text(
        "app/routes/admin_sekolah.py\n", encoding="utf-8")

    ran = _run(tmp_path, ONE + "if refusal_streak_exhausted; then echo LOOP; else echo FRESH; fi\n",
               state=state)
    assert ran.returncode == 0, ran.stderr
    assert "COUNT 1" in ran.stdout, (
        f"a corrupt count was not treated as zero: {ran.stdout} {ran.stderr}")


def test_a_state_directory_that_cannot_be_written_does_not_stop_the_runner(tmp_path):
    """Recording the streak is bookkeeping. A box whose state directory is the very
    thing that is broken must still refuse for the real reason, not die on this."""
    state = tmp_path / "state"
    state.parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "state").write_text("in the way\n", encoding="utf-8")   # a file, not a dir
    ran = _run(tmp_path, ONE + "if refusal_streak_exhausted; then echo LOOP; else echo FRESH; fi\n",
               state=state)
    assert ran.returncode == 0, f"the streak recording took the runner down: {ran.stderr}"
    assert "FRESH" in ran.stdout


def test_a_completed_pass_forgets_the_refusal(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    ran = _run(tmp_path, ONE * 3 + "refusal_streak_clear\n"
               "if refusal_streak_exhausted; then echo LOOP; else echo FRESH; fi\n",
               state=state)
    assert ran.returncode == 0, ran.stderr
    assert "FRESH" in ran.stdout, "a cleared streak still read as a loop"
    assert not (state / "refusal-streak").exists()
    assert not (state / "refusal-streak.paths").exists(), (
        "clearing left the paths behind, so the next refusal would inherit a count it "
        "has nothing to do with")


# ── 2. the source guards ─────────────────────────────────────────────────────

def test_the_block_sets_its_own_constants_and_takes_no_arguments():
    block = _block()
    for name in ("REFUSAL_STREAK_MAX", "REFUSAL_STREAK_FILE", "REFUSAL_STREAK_PATHS",
                 "REFUSAL_FALLBACK_DIR"):
        assert f"{name}=" in block, f"{name} is set outside the block, so a harness " \
                                   "that runs the block alone cannot stand where the " \
                                   "runner does"
    for positional in ("$1", "${1"):
        assert positional not in block, (
            "the streak block takes a positional parameter; this script takes none at "
            "all")
        assert positional not in _code()


def test_the_fallback_home_is_a_different_filesystem_from_the_preferred_one():
    """The failure the fallback exists for is `/var` being full or read-only, so a
    fallback *inside* `/var/lib/scangrade-deploy` would share its fate and be worth
    nothing."""
    fallback = _constant("REFUSAL_FALLBACK_DIR")
    assert fallback.startswith("/run"), (
        f"the fallback home is {fallback!r}; it has to be a different filesystem — "
        "/run is tmpfs, which is the one a full /var does not take with it")
    assert _constant("REFUSAL_STREAK_MAX").isdigit(), "the threshold is not a number"
    assert int(_constant("REFUSAL_STREAK_MAX")) >= 2, (
        "a threshold of one would degrade on the first refusal — before anything has "
        "been shown to repeat")


def test_the_count_is_kept_per_path_set_and_not_per_tick():
    code = _code()
    assert "cmp -s -" in code, (
        "the streak never compares the paths, so every refusal inherits the previous "
        "one's count and an unrelated edit becomes a loop")
    assert "REFUSAL_STREAK_PATHS" in code
