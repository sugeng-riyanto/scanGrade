"""A left-behind `.git/index.lock` must never need a human at a console.

The box this was written from sat ten commits behind for three days, fetching
`origin/main` every two minutes and never merging, while its own status page said
"nothing refused before a merge" and "0 uncommitted files" — because a status read
and a status *write* disagree about a lock file:

    git status --porcelain   exit 0, empty output   (no reason to rewrite the index)
    git merge --ff-only      exit 1                 (cannot create index.lock)
    git reset --hard         exit 1                 (same)

So the dirty guard passed, the fetch reached GitHub, the merge could not happen,
and the one step that could have said so blamed the history. The runner now looks
for that lock first and answers it three ways, and each one is here:

  * nothing holds it         -> removed, and the release moves on the same tick;
  * a git holds it           -> left alone, refused with its own exit code and a
                                record naming the step, because a lock with a live
                                owner is somebody's in-flight operation;
  * the table cannot be read -> refused, because "we could not tell" is not "no
                                git is running", and removing the file on that
                                basis is the one way this could destroy work.

The middle answer is what the block is for, so it is tested by *running* it — the
holder scan reads `$PROC_ROOT`, and these tests point that at a process table they
wrote themselves. That is also what makes them runnable here: what a developer
machine's own `/proc` contains says nothing about what the VPS would do.
"""
from __future__ import annotations

import os
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

LOCK_START = "# lock-heal-logic:start"
LOCK_END = "# lock-heal-logic:end"

#: How many ways the heal can refuse. One gate key covers all three because the
#: page's sentence is about the lock; *which* of the three it was is the record's
#: own detail, shown verbatim.
REFUSALS = 3


def _norm(path) -> str:
    """A path the way bash will hand it back, on either platform."""
    return str(path).replace("\\", "/")


def _script() -> str:
    return RUNNER.read_text(encoding="utf-8")


def _block() -> str:
    return _script().split(LOCK_START, 1)[1].split(LOCK_END, 1)[0]


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


# ── the block on its own ─────────────────────────────────────────────────────

def test_the_block_lives_between_its_own_delimiters():
    """The delimiters are what let the rest of this file run it in isolation."""
    script = _script()
    assert LOCK_START in script and LOCK_END in script, (
        "the lock-healing block's delimiters are gone; they are also how the block "
        "is tested, the way Gate 0's and the quarantine's are")
    block = _block()
    for fn in ("lock_path() {", "process_table_readable() {", "lock_holders() {",
               "lock_heal() {"):
        assert fn in block, f"{fn} left the delimited block"


def test_the_process_table_is_the_real_one_by_default():
    """`PROC_ROOT` exists so a test can own the table. The box must not inherit
    whatever a test pointed it at."""
    assert 'PROC_ROOT="/proc"' in _block(), (
        "the holder scan is not pointed at the real process table, so on the VPS it "
        "would read whatever that variable happened to hold")


def test_the_holder_scan_needs_no_package():
    """One read per process, using the name the kernel records.

    `pgrep` is procps and `lsof` is a package; neither is a given on a minimal
    box, and a heal that quietly stops working because a tool is missing is the
    same silent stall in a new costume.
    """
    block = _block()
    assert '$PROC_ROOT"/[0-9]*' in block, "the scan does not walk the process table"
    assert "$pid/comm" in block, (
        "the scan reads something other than each process's own name")
    # The comments name the tools this avoids, so the check is of the code alone.
    code = "\n".join(line for line in block.splitlines()
                     if not line.strip().startswith("#"))
    for package in ("pgrep", "lsof", "pidof", "fuser", "ps aux", "ps -ef"):
        assert package not in code, (
            f"the heal runs {package!r}, which the box may not have — and a heal that "
            "stops working because a tool is missing is the same silent stall")


def test_it_runs_twice_and_the_second_time_is_immediately_before_the_merge():
    """Both call sites, positionally, and neither is decoration.

    The first covers the guard below — which reads the index *through* the same
    lock, so a lock is what makes it report an unreadable checkout — and then the
    fetch. The second covers the gap a slow release opens: on a migration this run
    takes a data snapshot that can last minutes, and a lock appearing in between
    would land on the merge and be reported as a merge failure.
    """
    script = _script()
    calls = [m.start() for m in re.finditer(r"^lock_heal$", script, re.M)]
    assert len(calls) == 2, (
        f"{len(calls)} lock_heal call(s): one before the first read of the index, "
        "one immediately before the merge")
    first, second = calls
    # Anchored on the running code, not on a mention of it: the block's own comment
    # quotes the guard it exists for, and searching for the words found that first.
    dirty = script.index("status --porcelain 2>&1")
    snapshot = script.index('--keep "$BACKUP_KEEP"')
    merge = script.index('merge --ff-only --quiet "origin/$BRANCH" 2>&1')
    assert first < dirty, (
        "the heal runs after the dirty guard, so a lock that makes `git status` "
        "fail still reports an unreadable checkout every tick")
    assert snapshot < second < merge, (
        "the second heal does not sit between the slow part of a release and the "
        "merge it is there to unblock")
    # "Immediately before": the only code in between is the merge's own capture —
    # its empty assignment and the `if` that runs it. Anything else is a command
    # taking time, which reopens the window this call exists to close.
    between = script[second + len("lock_heal"):merge]
    the_capture = ("MERGE_OUT=", "if ! MERGE_OUT=")
    stray = [line.strip() for line in between.splitlines()
             if line.strip() and not line.strip().startswith("#")
             and not line.strip().startswith(the_capture)]
    assert not stray, (
        f"{stray} runs between the second heal and the merge, so the merge can "
        "still be refused by a lock created after the heal")


def test_a_merge_that_still_fails_names_who_holds_the_lock():
    """The journal line the three-day stall never had.

    Git's stderr says a lock file could not be created; it cannot say whether
    anybody owns it, and "not a fast-forward" was the guess that hid it. Both
    answers are printed, and only in the failure branch — a healthy box pays one
    `stat` for them.
    """
    script = _script()
    merge = script.index('merge --ff-only --quiet "origin/$BRANCH" 2>&1')
    branch = script[merge:script.index("PREFLIGHT_GATE=merge_refused", merge)]
    assert "lock_path" in branch and "lock_holders" in branch, (
        "a failed merge does not say whether the index was locked, so the journal "
        "is back to naming a cause git never mentioned")
    assert "no git process holds it" in branch, (
        "a lock with no owner — the one the next tick clears by itself — is not "
        "told apart from a live holder")


def test_every_answer_gets_its_own_record_and_exit_code():
    """Three states, one gate key, and an `exit` code that agrees with it.

    The page turns the gate into a sentence and `systemctl status` reports the
    code; a record naming one thing that then leaves through another is worse than
    no record, so the two are written together and checked together.
    """
    script = _script()
    pattern = re.compile(r"PREFLIGHT_GATE=(lock_refused) PREFLIGHT_EXIT=(\d+)")
    sites = [(m.group(1), m.group(2), m.end()) for m in pattern.finditer(script)]
    assert len(sites) == REFUSALS, (
        f"{len(sites)} lock_refused site(s): the live holder, the file that could "
        "not be removed, and the process table that could not be read")
    for gate, code, end in sites:
        found = re.search(r"^\s*exit (\d+)", script[end:end + 400], re.M)
        assert found, f"{gate} records a refusal and then does not leave"
        assert found.group(1) == code, (
            f"{gate} records exit {code} and leaves through {found.group(1)}")


def test_the_page_has_a_sentence_for_the_step_the_runner_records():
    assert "lock_refused" in status.PREFLIGHT_GATES, (
        "the runner records a step the status page classifies as unknown, so the "
        "card would name it without saying what it means")
    assert "lock_refused" not in status.PREFLIGHT_TRANSIENT, (
        "a locked checkout is reported as the world's fault rather than the box's: "
        "nothing is deploying, which is breakage, not a warning")


def test_the_card_says_the_box_heals_itself_without_a_root_step():
    """The whole reason this is in the runner rather than in a runbook.

    A card that only names the lock sends an operator to a console for a step the
    box takes care of by itself — so the sentence that says so is part of the
    feature, in both languages and in the same pair.
    """
    text = TEMPLATE.read_text(encoding="utf-8")
    at = text.index("tidak butuh langkah root")
    pair = text[at:at + 400]
    assert "no root step" in pair, (
        "the card says 'no root step needed' in Indonesian only, so half the "
        "readers are told to go and look for one")
    opened = text.rindex("t('", 0, at)
    assert at - opened < 200, (
        "the guidance sits outside any bilingual pair, so it does not follow the "
        "language toggle")


# ── and it behaves ───────────────────────────────────────────────────────────

def _harness(tmp_path: Path, *, procs: dict, lock: str = "file") -> tuple[str, Path, Path]:
    """The real block, given a process table and a checkout this test owns.

    `PROC_ROOT` is assigned *after* the block on purpose: the block sets its own
    default, so a harness that prefixed it would be testing the default instead of
    the rule. Every path is written with forward slashes — `git` and `[ -e ]` are
    handed the same string here, and a backslash path is the one form they read
    differently.
    """
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True, exist_ok=True)
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    proc = tmp_path / "proc"
    proc.mkdir(exist_ok=True)
    for pid, comm in procs.items():
        entry = proc / pid
        entry.mkdir(exist_ok=True)
        # Bytes, not text: on Windows `write_text` turns "\n" into "\r\n", and the
        # kernel's own `comm` is the name and nothing else — a trailing carriage
        # return is a name no `case` arm would ever match.
        (entry / "comm").write_bytes((comm + "\n").encode("utf-8"))
    target = repo / ".git" / "index.lock"
    if lock == "file":
        target.write_text("", encoding="utf-8")
    elif lock == "dir":          # `rm -f` refuses a directory, on every platform
        target.mkdir()

    program = (
        "set -uo pipefail\n"
        f'REPO="{_norm(repo)}"\n'
        f'STATE_DIR="{_norm(state)}"\n'
        f'PREFLIGHT_FILE="{_norm(state / "refused-before-merge")}"\n'
        'PREFLIGHT_GATE=""\n'
        'PREFLIGHT_EXIT=""\n'
        'PREFLIGHT_DETAIL=""\n'
        'as_owner() { "$@"; }\n'
        'log() { echo "$*"; }\n'
        'preflight_write() {\n'
        '  mkdir -p "$STATE_DIR" 2>/dev/null || true\n'
        '  { printf "%s\\n%s\\n%s\\n%s\\n" "$PREFLIGHT_GATE" "$(date -Is)" '
        '"$PREFLIGHT_EXIT" ""; printf "%s\\n" "$PREFLIGHT_DETAIL"; } '
        '> "$PREFLIGHT_FILE"\n'
        '}\n'
        + _block()
        + f'\nPROC_ROOT="{_norm(proc)}"\n'
    )
    return program, repo, state


def _run(program: str, body: str, *, ceiling: Path | None = None):
    """Run it with git's search for a repository stopped at this test's directory.

    Without that, `git -C <a directory with an unusable .git>` walks *upwards*, and
    on a machine whose temp directory happens to sit inside a repository it would
    answer with that repository's gitdir — so the tests would be reading the
    developer's checkout instead of the one they built.
    """
    env = dict(os.environ)
    if ceiling is not None:
        env["GIT_CEILING_DIRECTORIES"] = _norm(ceiling)
    env.pop("GIT_DIR", None)
    env.pop("GIT_INDEX_FILE", None)
    return subprocess.run([BASH, "-c", program + body], capture_output=True,
                          text=True, timeout=60, env=env)


@needs_bash
def test_a_lock_nobody_holds_is_removed_and_the_run_carries_on(tmp_path):
    program, repo, state = _harness(tmp_path, procs={"9999": "nginx"})
    run = _run(program, 'lock_heal; echo "rc=$?"', ceiling=tmp_path)
    assert run.returncode == 0, run.stderr
    assert "rc=0" in run.stdout, run.stdout
    assert "removed a stale index lock" in run.stdout, (
        "the box heals itself in silence, so nobody can tell it happened — and a "
        "run that repaired the checkout would look like one that refused nothing")
    assert not (repo / ".git" / "index.lock").exists(), (
        "the stale lock survived the one step that exists to clear it")
    assert not (state / "refused-before-merge").exists(), (
        "a healed lock was recorded as a refusal, so the page would report a stalled "
        "box while it is deploying")


@needs_bash
def test_a_second_call_costs_a_stat_and_says_nothing(tmp_path):
    """It runs twice per release, so the second one must not report a heal.

    A line printed on every tick for a heal that did not happen is how a journal
    stops being read — and `INDEX_LOCK` is cached precisely so the second call
    cannot disagree with the first about where the lock is.
    """
    program, repo, state = _harness(tmp_path, procs={"9999": "nginx"})
    run = _run(program, 'lock_heal; lock_heal; echo "rc=$?"', ceiling=tmp_path)
    assert run.returncode == 0, run.stderr
    assert "rc=0" in run.stdout, run.stdout
    assert run.stdout.count("removed a stale index lock") == 1, (
        f"the second call reported a heal it did not do:\n{run.stdout}")
    assert not (repo / ".git" / "index.lock").exists(), run.stdout
    assert not (state / "refused-before-merge").exists(), run.stdout


@needs_bash
def test_a_non_git_process_does_not_block_the_heal(tmp_path):
    """The scan is about git, not about \"something is running\"."""
    program, repo, _state = _harness(tmp_path, procs={"7": "postgres", "8": "python3"})
    run = _run(program, 'lock_heal; echo "rc=$?"', ceiling=tmp_path)
    assert "rc=0" in run.stdout, run.stdout
    assert not (repo / ".git" / "index.lock").exists(), run.stdout


@needs_bash
def test_a_lock_a_git_process_holds_is_left_alone_and_recorded(tmp_path):
    """The guard the whole step turns on: a live owner is not a stale file.

    Removing it would corrupt whatever that git is in the middle of. It is
    *recorded* because the alternative is the failure this file was written for: a
    box that looks as though it simply has no new commits.
    """
    program, repo, state = _harness(tmp_path, procs={"1234": "git"})
    run = _run(program, 'lock_heal; echo "the run carried on"', ceiling=tmp_path)
    assert run.returncode == 17, (
        f"the run left through {run.returncode} instead of the code its record names")
    assert "the run carried on" not in run.stdout, (
        "the run walked past a lock it would not clear, straight into the merge "
        "failure this step exists to get out of")
    assert (repo / ".git" / "index.lock").exists(), (
        "the heal removed a lock a running git owns")
    assert "leaving it alone" in run.stdout, (
        "the refusal does not say which way it went, so the journal reads as an "
        "unexplained stop")
    assert "1234 git" in run.stdout, (
        "the holder's pid and name are not printed, so the journal cannot be "
        "matched against anything")

    record = (state / "refused-before-merge").read_text(encoding="utf-8").splitlines()
    assert record[0] == "lock_refused", (
        "the record names a step the page has no sentence for")
    assert record[1], "the record must say when it refused"
    assert record[2] == "17", (
        "the recorded code is not the one the run leaves through, so the page and "
        "`systemctl status` would disagree about why it ended")
    assert "1234 git" in "\n".join(record[4:]), (
        "the record carries a paraphrase instead of what was actually found")


@needs_bash
def test_a_fetch_helper_counts_as_a_holder_too(tmp_path):
    """`comm` truncates at 15 characters, so `git-remote-https` arrives as
    `git-remote-http` — a name the bare `git` arm alone would miss."""
    program, repo, _state = _harness(tmp_path, procs={"55": "git-remote-http"})
    run = _run(program, 'lock_heal; echo "the run carried on"', ceiling=tmp_path)
    assert run.returncode == 17, run.stdout
    assert (repo / ".git" / "index.lock").exists(), run.stdout


@needs_bash
def test_a_lock_that_cannot_be_removed_is_refused_not_ignored(tmp_path):
    """`rm -f` returns non-zero when it cannot remove the file — an immutable bit,
    a read-only filesystem, a path that is not a file at all. A run that carried on
    would walk into the merge and report the lock as a merge failure."""
    program, repo, state = _harness(tmp_path, procs={"9999": "nginx"}, lock="dir")
    run = _run(program, 'lock_heal; echo "the run carried on"', ceiling=tmp_path)
    assert run.returncode == 17, run.stdout
    assert (repo / ".git" / "index.lock").is_dir(), "the path was not left as found"
    body = "\n".join(
        (state / "refused-before-merge").read_text(encoding="utf-8").splitlines()[4:])
    assert "could not remove" in body, (
        "the record does not say why the lock is still in the way")


@needs_bash
def test_an_unreadable_process_table_is_not_read_as_no_git(tmp_path):
    """The one way this could destroy work: rounding \"I cannot see the table\" to
    \"nobody holds it\" and deleting a live owner's lock."""
    program, repo, state = _harness(tmp_path, procs={"9999": "nginx"})
    program = program.replace(f'PROC_ROOT="{_norm(tmp_path / "proc")}"',
                              f'PROC_ROOT="{_norm(tmp_path / "no-such-proc")}"')
    run = _run(program, 'lock_heal; echo "the run carried on"', ceiling=tmp_path)
    assert run.returncode == 17, run.stdout
    assert "the run carried on" not in run.stdout, (
        "the heal guessed, which is the answer that deletes a live owner's lock")
    assert (repo / ".git" / "index.lock").exists(), (
        "the heal removed a lock it could not judge")
    record = (state / "refused-before-merge").read_text(encoding="utf-8").splitlines()
    assert record[0] == "lock_refused"
    assert "no-such-proc" in "\n".join(record[4:]), (
        "the record does not say which table could not be read")


@needs_bash
def test_a_checkout_with_no_lock_is_not_disturbed(tmp_path):
    """The common case, and it must cost nothing: no record, no log, rc 0."""
    program, _repo, state = _harness(tmp_path, procs={"9999": "nginx"}, lock="none")
    run = _run(program, 'lock_heal; echo "rc=$?"', ceiling=tmp_path)
    assert "rc=0" in run.stdout, run.stdout
    assert "index lock" not in run.stdout, (
        "a checkout with no lock says something about locks, which is how a journal "
        "fills with lines nobody reads")
    assert not (state / "refused-before-merge").exists(), run.stdout


@needs_bash
def test_where_the_lock_is_asked_of_git_rather_than_assumed(tmp_path):
    """A linked worktree keeps its index in the worktree's own gitdir.

    `$REPO/.git/index.lock` is right for the ordinary checkout and wrong here — in
    a worktree `.git` is a *file* — so the path is read from git. The same read is
    what makes it correct under `GIT_INDEX_FILE`.
    """
    main = tmp_path / "main"
    main.mkdir()
    _git("init", "-q", ".", cwd=main)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty",
         "-m", "one", cwd=main)
    worktree = tmp_path / "linked"
    made = _git("worktree", "add", "-q", str(worktree), cwd=main)
    if made.returncode != 0:                 # a git that cannot link a worktree
        pytest.skip(made.stderr)

    program, _repo, _state = _harness(tmp_path, procs={"9999": "nginx"}, lock="none")
    program = program.replace(f'REPO="{_norm(tmp_path / "repo")}"',
                              f'REPO="{_norm(worktree)}"')
    run = _run(program, 'lock_path', ceiling=tmp_path)
    answer = run.stdout.strip()
    assert answer.endswith("/index.lock"), run.stdout
    assert "/worktrees/" in answer, (
        f"the lock path is {answer!r}: it points at the worktree's own `.git` instead "
        "of the gitdir git reported, where the index does not live")
    assert _norm(worktree / ".git" / "index.lock") != answer, (
        "in a linked worktree `.git` is a file, so a lock 'found' there is a lock "
        "that can never exist")


@needs_bash
def test_a_checkout_git_cannot_describe_still_gets_a_lock_path(tmp_path):
    """`rev-parse` failing must not leave the heal holding an empty path."""
    program, repo, _state = _harness(tmp_path, procs={"9999": "nginx"}, lock="none")
    run = _run(program, 'lock_path', ceiling=tmp_path)
    assert run.stdout.strip() == _norm(repo / ".git" / "index.lock"), (
        f"without a git answer the path is {run.stdout.strip()!r} instead of the "
        "checkout's own `.git`")
