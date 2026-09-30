"""The console's one word, held to what it promises.

`sgfix` exists because a stuck box ends in a noVNC session — a console where nothing
can be pasted and a long command has to be typed by hand — and because the deploy
runner's own heal cannot arrive: it travels in a release, and a release is what a
stuck box cannot fetch, since the runner's dirty check runs *before* its fetch.

So the lever has one property that matters more than the rest and is therefore the
first thing tested here: **it never takes something out of the checkout without
writing down where it went.** A recovery tool that loses a box's only copy of a fix
is worse than the console session it replaces. The record is written after a durable
copy exists and before the tree is touched, an entry that cannot be recorded is left
alone, and the run says so.

The shell is run, not read: the `recover-logic` block is extracted and executed
inside a small harness against a real git checkout in `tmp_path` — the same method
`tests/unit/test_box_edits.py` uses for the runner's heal. The rest of the suite holds
the wiring (the launcher name, the installer, the runner's launcher refresh, the arm
check) and the two ladders the script runs: migrations before the release, and a
quarantine lifted only when the schema gate earned it.
"""
from __future__ import annotations

import os
import pathlib
import shlex
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "AUTO_DEPLOY.md"
RECOVER = ROOT / "deploy" / "scangrade-recover.sh"
ENTRYPOINT = ROOT / "deploy" / "entrypoint.sh"
RUNNER = ROOT / "deploy" / "scangrade-deploy.sh"
INSTALLER = ROOT / "deploy" / "install-auto-deploy.sh"
ARM = ROOT / "deploy" / "arm-auto-deploy.sh"
MIGRATE = ROOT / "deploy" / "apply_migration.py"
BASH = shutil.which("bash")

START = "# recover-logic:start"
END = "# recover-logic:end"

pytestmark = pytest.mark.skipif(BASH is None, reason="needs a bash to run the block")


def _text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _logic_block() -> str:
    script = _text(RECOVER)
    assert START in script and END in script, (
        "the block's delimiters are what let it be run on its own, the way the "
        "quarantine's, the preflight record's, the lock heal's and the box-edit "
        "heal's are; keep them")
    return script.split(START, 1)[1].split(END, 1)[0]


def _git(repo: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@example.com",
         "-c", "user.name=t", *args],
        capture_output=True, text=True, check=False)


def _repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """A checkout shaped like the box's: one tracked file, committed."""
    repo = tmp_path / "repo"
    (repo / "app" / "routes").mkdir(parents=True)
    (repo / "app" / "routes" / "admin_sekolah.py").write_text(
        "the release's line\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], capture_output=True, text=True)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


#: The smallest harness that can stand where the script does. The block reads
#: `REPO`, `STATE_DIR`, `RECORD`, `HOLD` and the three marker constants, acts as the
#: checkout's owner through `gitdo`, and prints through `note`.
HARNESS = """set -uo pipefail
REPO=__REPO__
STATE_DIR=__STATE__
RECOVER_DIR="$STATE_DIR/recover"
RECORD=__RECORD__
HOLD=__HOLD__
GAPS_HEADER=__GAPS__
MISSING_MARK=__MISSING__
SCHEMA_GATE_WORD=__SCHEMA__
PERF_GATE_WORD=__PERF__
gitdo() { git -C "$REPO" "$@"; }
# The runner's real script defines this as `runuser -u <owner> -- env …`; here the
# block already runs as the checkout's owner, so it is the identity — and it must be
# defined, because `recover_make_reproducible` writes through it rather than through a
# plain redirect, so the box's file does not change hands (root writing into a repo
# owned by scangrade is how a recovery breaks the app it just restored).
as_owner() { "$@"; }
note() { printf '   %s\\n' "$*"; }
"""


def _run_logic(tmp_path: pathlib.Path, repo: pathlib.Path, state: pathlib.Path,
               tail: str, *, record: pathlib.Path | None = None,
               hold: pathlib.Path | None = None) -> subprocess.CompletedProcess:
    state.mkdir(parents=True, exist_ok=True)
    record = record if record is not None else state / "recover" / "r.txt"
    hold = hold if hold is not None else state / "recover" / "r"
    record.parent.mkdir(parents=True, exist_ok=True)
    header = (HARNESS
              .replace("__REPO__", shlex.quote(repo.as_posix()))
              .replace("__STATE__", shlex.quote(state.as_posix()))
              .replace("__RECORD__", shlex.quote(record.as_posix()))
              .replace("__HOLD__", shlex.quote(hold.as_posix()))
              .replace("__GAPS__", shlex.quote(_constant("GAPS_HEADER")))
              .replace("__MISSING__", shlex.quote(_constant("MISSING_MARK")))
              .replace("__SCHEMA__", shlex.quote(_constant("SCHEMA_GATE_WORD")))
              .replace("__PERF__", shlex.quote(_constant("PERF_GATE_WORD"))))
    script = tmp_path / "harness.sh"
    script.write_text(header + _logic_block() + tail, encoding="utf-8", newline="\n")
    return subprocess.run([BASH, str(script)], capture_output=True, text=True,
                          check=False)


def _constant(name: str) -> str:
    """One of the script's own constants, read out of it rather than restated here."""
    for line in _text(RECOVER).splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip().strip('"')
    raise AssertionError(f"{name} is not declared in deploy/scangrade-recover.sh")


# ── 1. nothing leaves the tree without a record ──────────────────────────────

def test_a_tracked_edit_is_copied_and_recorded_before_it_is_restored(tmp_path):
    repo, state = _repo(tmp_path), tmp_path / "state"
    target = repo / "app" / "routes" / "admin_sekolah.py"
    target.write_text("a box-local fix\n", encoding="utf-8")

    run = _run_logic(tmp_path, repo, state,
                     'git -C "$REPO" status --porcelain | recover_set_aside\n'
                     "printf 'RC %s\\n' \"$?\"\n")

    assert "RC 0" in run.stdout, run.stdout + run.stderr
    assert target.read_text(encoding="utf-8") == "the release's line\n", (
        "the tree was not restored, so the merge still has something to argue with")
    patch = state / "recover" / "r" / "files" / "app" / "routes" / "admin_sekolah.py.patch"
    assert patch.is_file() and "a box-local fix" in patch.read_text(encoding="utf-8"), (
        "the edit left the tree without a durable copy of itself")
    record = (state / "recover" / "r.txt").read_text(encoding="utf-8")
    assert "app/routes/admin_sekolah.py" in record, (
        "the record does not name what was moved — a recovery nobody can read back "
        "is indistinguishable from a checkout somebody broke by hand")
    assert _git(repo, "status", "--porcelain").stdout.strip() == "", (
        "the tree is not quiet afterwards, so the release would be refused again")


def test_a_path_head_does_not_have_is_copied_whole(tmp_path):
    """A file the release has never seen has no blob to diff, so the file *is* the
    record — which is the case a naive `git checkout` would have destroyed."""
    repo, state = _repo(tmp_path), tmp_path / "state"
    (repo / "tmp_teachers_30.xlsx").write_text("the only copy\n", encoding="utf-8")

    run = _run_logic(tmp_path, repo, state,
                     'git -C "$REPO" status --porcelain | recover_set_aside\n')

    assert run.returncode == 0, run.stdout + run.stderr
    assert not (repo / "tmp_teachers_30.xlsx").exists(), (
        "an untracked file still blocks the merge")
    kept = state / "recover" / "r" / "files" / "tmp_teachers_30.xlsx"
    assert kept.read_text(encoding="utf-8") == "the only copy\n"
    assert "MOVED" in (state / "recover" / "r.txt").read_text(encoding="utf-8")


def test_an_entry_that_cannot_be_recorded_is_left_exactly_where_it_was(tmp_path):
    """The property the whole lever stands on: no record, no move."""
    repo, state = _repo(tmp_path), tmp_path / "state"
    target = repo / "app" / "routes" / "admin_sekolah.py"
    target.write_text("a box-local fix\n", encoding="utf-8")
    # A *directory* where the record file should be: every append to it fails, which
    # is what a full or read-only state directory looks like from here.
    blocked = state / "recover" / "r.txt"
    blocked.parent.mkdir(parents=True, exist_ok=True)
    blocked.mkdir()

    run = _run_logic(tmp_path, repo, state,
                     'git -C "$REPO" status --porcelain | recover_set_aside\n'
                     "printf 'RC %s\\n' \"$?\"\n",
                     record=blocked)

    assert "RC 1" in run.stdout, run.stdout + run.stderr
    assert target.read_text(encoding="utf-8") == "a box-local fix\n", (
        "the edit was taken out of the tree even though it could not be written "
        "down — this is the one bug that loses a box's only copy of a fix")
    assert _git(repo, "status", "--porcelain").stdout.strip() != "", (
        "the entry disappeared from `git status` without a record")


def test_a_clean_tree_is_a_no_op(tmp_path):
    repo, state = _repo(tmp_path), tmp_path / "state"
    run = _run_logic(tmp_path, repo, state,
                     'git -C "$REPO" status --porcelain | recover_set_aside\n'
                     "printf 'RC %s\\n' \"$?\"\n")
    assert "RC 0" in run.stdout
    record = (state / "recover" / "r.txt").read_text(encoding="utf-8")
    assert "SET ASIDE 0 path(s)" in record


# ── 1b. the blob a checkout cannot reproduce, which is why it said NOT MOVED ──
#
# The case the box was actually stuck on: `git checkout HEAD -- <path>` writes through
# the filters, so a blob committed *around* them (a scripted commit's
# `hash-object --no-filters`) reads as modified however many times it is restored, and
# `git merge --ff-only` refuses for that reason. The lever reported a clean restore and
# the checkout did not move.

TARGET = "app/routes/admin_sekolah.py"


def _repo_with_the_rule(tmp_path: pathlib.Path) -> pathlib.Path:
    """The checkout, with the repo's own `*.py text eol=lf` committed.

    Without that rule a carriage return is harmless here and the difference that
    stranded the box cannot be reproduced at all — so the rule is part of the fixture
    rather than scenery.
    """
    repo = _repo(tmp_path)
    (repo / ".gitattributes").write_bytes(b"*.py text eol=lf\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "the attributes the real repo carries")
    return repo


def _plant_blob(repo: pathlib.Path, raw: bytes, rel: str = TARGET) -> bytes:
    """Commit *raw* as the path's blob, around the filters, and return the blob.

    `git add` normalises a CR away, so a blob like this can only reach the index the
    way it reached the box's: `hash-object --no-filters` then `update-index
    --cacheinfo`. Measured: `0afc68e` committed 108 carriage returns into a file whose
    attribute promises LF.
    """
    path = repo / rel
    path.write_bytes(raw)
    blob = subprocess.run(["git", "-C", str(repo), "hash-object", "-w", "--no-filters",
                           str(path)], capture_output=True, text=True).stdout.strip()
    assert blob, "git hash-object produced no blob"
    assert _git(repo, "update-index", "--cacheinfo", f"100644,{blob},{rel}").returncode == 0
    assert _git(repo, "commit", "-q", "-m", "the blob a box is stuck on").returncode == 0
    return subprocess.run(["git", "-C", str(repo), "cat-file", "blob", f"HEAD:{rel}"],
                          capture_output=True).stdout


def test_a_blob_a_checkout_cannot_reproduce_is_restored_from_head(tmp_path):
    """The box's real state, and the reason its own lever said NOT MOVED."""
    repo, state = _repo_with_the_rule(tmp_path), tmp_path / "state"
    target = repo / TARGET
    blob = _plant_blob(repo, b"the release's line\r\n")
    target.write_bytes(b"the box's hotfix\n")

    run = _run_logic(tmp_path, repo, state,
                     'git -C "$REPO" status --porcelain | recover_set_aside\n'
                     "printf 'RC %s\\n' \"$?\"\n")

    assert "RC 0" in run.stdout, run.stdout + run.stderr
    assert target.read_bytes() == blob, (
        "the worktree does not hold HEAD's own bytes, so the merge is refused for "
        "exactly the reason the box was stuck")
    assert _git(repo, "status", "--porcelain").stdout.strip() == "", (
        "the tree is still dirty, so the lever would report NOT MOVED again")
    record = (state / "recover" / "r.txt").read_text(encoding="utf-8")
    assert f"SET     {TARGET}" in record, record
    assert f"VERBATIM {TARGET}" in record, (
        f"the record does not say those bytes came from the commit: {record}")
    assert f"ATTRIBUTE {TARGET}" in record, (
        f"the record does not say a local override holds that path: {record}")
    # The one thing this must never trade away: the box's own version still exists.
    patch = state / "recover" / "r" / "files" / (TARGET + ".patch")
    assert "the box's hotfix" in patch.read_text(encoding="utf-8"), (
        "the lever replaced the box's bytes without keeping them")


def test_the_local_override_is_one_line_and_survives_a_second_heal(tmp_path):
    """A second tick must not grow the attributes file, and must still say what holds."""
    repo, state = _repo_with_the_rule(tmp_path), tmp_path / "state"
    target = repo / TARGET
    _plant_blob(repo, b"the release's line\r\n")
    target.write_bytes(b"the box's hotfix\n")
    tail = ('git -C "$REPO" status --porcelain | recover_set_aside\n'
            "printf 'RC %s\\n' \"$?\"\n")
    assert "RC 0" in _run_logic(tmp_path, repo, state, tail).stdout
    attributes = repo / ".git" / "info" / "attributes"
    first = attributes.read_text(encoding="utf-8")
    assert first.count("-text") == 1, f"the override is not one line: {first!r}"

    target.write_bytes(b"the box's second hotfix\n")
    assert "RC 0" in _run_logic(tmp_path, repo, state, tail).stdout
    assert attributes.read_text(encoding="utf-8") == first, (
        "the attributes file grew on a tick that had nothing to add")


def test_an_ordinary_edit_is_not_given_a_local_override(tmp_path):
    """The escalation is for the unreproducible class, not for every hand edit."""
    repo, state = _repo_with_the_rule(tmp_path), tmp_path / "state"
    (repo / TARGET).write_text("the box's hotfix\n", encoding="utf-8")

    run = _run_logic(tmp_path, repo, state,
                     'git -C "$REPO" status --porcelain | recover_set_aside\n'
                     "printf 'RC %s\\n' \"$?\"\n")

    assert "RC 0" in run.stdout, run.stdout + run.stderr
    assert not (repo / ".git" / "info" / "attributes").exists(), (
        "an ordinary restore wrote a local override, which changes how every later "
        "comparison reads that path")
    record = (state / "recover" / "r.txt").read_text(encoding="utf-8")
    assert "VERBATIM" not in record and "ATTRIBUTE" not in record, record


# ── 2. the reason comes from the runner's own records ────────────────────────

def _reason(tmp_path, state: pathlib.Path, porcelain: str = "") -> str:
    run = _run_logic(tmp_path, tmp_path / "repo", state,
                     f'PORCELAIN={shlex.quote(porcelain)}\n'
                     "recover_reason\n")
    return run.stdout.strip()


def test_the_reason_is_read_from_the_runners_own_files(tmp_path):
    state = tmp_path / "state"
    _repo(tmp_path)  # the checkout the harness points REPO at
    assert _reason(tmp_path, state) == "behind", "a healthy box has no excuse"

    state.mkdir(parents=True, exist_ok=True)
    (state / "refused-before-merge").write_text(
        "dirty_checkout\n2026-09-29T10:00:00+00:00\n4\nabc123\ndetail\n", encoding="utf-8")
    assert _reason(tmp_path, state) == "refused:dirty_checkout"

    # A quarantine outranks everything: it is the only record that says a *gate*
    # refused the commit, and therefore the only one that decides what may be lifted.
    (state / "quarantined").write_text(
        "abc123\n2026-09-29T10:00:00+00:00\nperf gate (p50 812 ms)\n", encoding="utf-8")
    assert _reason(tmp_path, state) == "quarantined:perf gate (p50 812 ms)"


def test_a_dirty_checkout_outranks_a_quarantine_it_explains(tmp_path):
    state = tmp_path / "state"
    state.mkdir(parents=True)
    reason = _reason(tmp_path, state, porcelain=" M app/routes/admin_sekolah.py")
    assert reason == "box-local edit", (
        "the checkout's own state is the thing a release trips over first")


# ── 3. the two ladders ───────────────────────────────────────────────────────

def test_only_the_files_the_verifier_names_are_treated_as_pending(tmp_path):
    """The table above the gap section lists every migration and their verdicts; only
    the gap section names files whose objects exist nowhere."""
    verify_output = "\n".join([
        "file                       schema      record                 sha256",
        "---------------------------------------------------------------------------",
        "039_school_official_rls.sql OUT         no record              1111",
        "040_user_activation_email.sql OUT       no record              2222",
        "",
        "=== declared objects that exist nowhere and nothing drops ===",
        "",
        "040_user_activation_email.sql",
        "    MISSING  column profiles.must_change_password",
        "",
        "041_invigilation.sql",
        "    MISSING  table public.invigilation_schedules",
        "",
        "2 file(s) declare objects the schema does not have.",
        "A file that was applied by hand still shows its objects, so this is a",
        "statement about the schema, not about how the file got there.",
    ])
    run = _run_logic(tmp_path, tmp_path / "repo", tmp_path / "state",
                     "recover_pending <<'VERIFY'\n" + verify_output + "\nVERIFY\n")
    pending = run.stdout.split()
    assert pending == ["040_user_activation_email.sql", "041_invigilation.sql"], (
        f"the pending list is {pending!r} — a file the table lists as OUT is not "
        f"evidence that its objects are absent")


def test_the_two_markers_are_the_verifiers_own_words():
    """A reader whose markers stopped matching the tool would report “nothing to
    apply” for every gap: the one way this script can be wrong and look right."""
    tool = _text(MIGRATE)
    for name in ("GAPS_HEADER", "MISSING_MARK"):
        literal = _constant(name)
        assert literal in tool, (
            f"{name} ({literal!r}) is not text deploy/apply_migration.py prints — "
            f"the reader has drifted from the tool it reads")


def test_only_the_schema_gate_earned_quarantine_may_be_lifted(tmp_path):
    state = tmp_path / "state"
    state.mkdir(parents=True)
    record = tmp_path / "quarantine"

    record.write_text("abc123\n2026-09-29T10:00:00+00:00\n"
                      "perf gate (p50 812 ms vs 374 ms)\n", encoding="utf-8")
    run = _run_logic(tmp_path, tmp_path / "repo", state,
                     f'recover_may_lift < {shlex.quote(record.as_posix())}\n'
                     "printf 'RC %s\\n' \"$?\"\n")
    assert "RC 1" in run.stdout, (
        "a perf refusal was treated as answerable by this lever — a recovery tool "
        "that can clear a gate it cannot fix is a bypass with a friendly name")

    record.write_text("abc123\n2026-09-29T10:00:00+00:00\n"
                      "schema gate (the release names objects no migration applied)\n",
                      encoding="utf-8")
    run = _run_logic(tmp_path, tmp_path / "repo", state,
                     f'recover_may_lift < {shlex.quote(record.as_posix())}\n'
                     "printf 'RC %s\\n' \"$?\"\n")
    assert "RC 0" in run.stdout, (
        "the schema gate names an action and this script performs exactly it")


# ── 3b. naming the gate that refused, not only that nothing moved ────────────
#
# The live box's own run, 2026-09-30: `sgfix` set aside `app/routes/admin_sekolah.py`,
# ran the release a second time, and printed only "the checkout did not move" — while
# the runner had already written `perf gate (p50 … vs …)` into its quarantine file.
# `NOT MOVED` reads as "nothing is happening"; the gate's name sends the operator to
# the re-baseline button. These hold the read that turns one into the other, and the
# staleness rule that keeps a first attempt's record from being quoted as a second's.

def test_a_refusal_after_a_retry_is_named_from_the_runners_own_record(tmp_path):
    repo, state = _repo(tmp_path), tmp_path / "state"
    state.mkdir(parents=True)
    reason = "perf gate (p50 1359 ms vs 374 ms, slower than the last release)"
    (state / "quarantined").write_text(
        f"abc123\n2026-09-30T11:36:07+00:00\n{reason}\n", encoding="utf-8")
    run = _run_logic(tmp_path, repo, state,
                     "\nrecover_name_refusal\nprintf 'RC %s\\n' \"$?\"\n")
    assert "RC 0" in run.stdout, run.stdout + run.stderr
    assert reason in run.stdout, (
        "a quarantine the runner wrote is not quoted — the operator gets no gate name")
    assert f"quarantine {reason}" in (state / "recover" / "r.txt").read_text(
        encoding="utf-8"), "the gate's name never reached the recovery record"


def test_a_preflight_from_the_first_attempt_is_not_quoted_as_the_seconds(tmp_path):
    """The first attempt refused at `dirty_checkout`; the second refused elsewhere.

    A reader that trusts any file it finds quotes the first attempt's key for the
    second, which names the wrong step and hides the gate that actually said no.
    """
    repo, state = _repo(tmp_path), tmp_path / "state"
    state.mkdir(parents=True)
    (state / "refused-before-merge").write_text(
        "dirty_checkout\n2026-09-30T11:30:00+00:00\n4\n\n", encoding="utf-8")
    run = _run_logic(tmp_path, repo, state,
                     "\nPREFLIGHT_BEFORE=\"$(cat \"$STATE_DIR/refused-before-merge\")\"\n"
                     "recover_name_refusal\nprintf 'RC %s\\n' \"$?\"\n")
    assert "RC 1" in run.stdout, run.stdout + run.stderr
    assert "dirty_checkout" not in run.stdout, (
        "the first attempt's refusal was quoted as the second's — it names a step "
        "that did not refuse again")
    record = state / "recover" / "r.txt"
    left = record.read_text(encoding="utf-8") if record.exists() else ""
    assert "dirty_checkout" not in left, (
        "the stale first-attempt refusal was written into the record as if this "
        "attempt had produced it")


def test_a_preflight_written_by_this_attempt_is_named(tmp_path):
    repo, state = _repo(tmp_path), tmp_path / "state"
    state.mkdir(parents=True)
    (state / "refused-before-merge").write_text(
        "merge_refused\n2026-09-30T11:36:07+00:00\n6\n\nnot a fast-forward\n",
        encoding="utf-8")
    run = _run_logic(tmp_path, repo, state,
                     "\nPREFLIGHT_BEFORE=\"an older attempt\"\n"
                     "recover_name_refusal\nprintf 'RC %s\\n' \"$?\"\n")
    assert "RC 0" in run.stdout, run.stdout + run.stderr
    assert "merge_refused" in run.stdout
    assert "preflight merge_refused" in (state / "recover" / "r.txt").read_text(
        encoding="utf-8")


def test_the_ladder_reads_the_refusal_after_the_second_release_too():
    """The retry is where the box's own run stopped, so the read has to be on it.

    The read now lives in `recover_try_again`, which every retry rung calls, so this
    holds both halves: the set-aside rung reaches the helper, and the helper reads the
    runner's record and names the gate.
    """
    source = _text(RECOVER)
    after_step5 = source[source.index("a release, on the restored tree"):]
    assert "recover_try_again" in after_step5, (
        "the second release does not go through the retry helper, so a gate that "
        "refuses after the set-aside is never named")
    helper = source.index("recover_try_again() {")
    body = source[helper:source.index("\n}", helper)]
    assert "recover_name_refusal" in body, (
        "the retry helper never reads the runner's refusal — it reports the move and "
        "nothing else")


def test_only_the_perf_gate_may_be_re_measured(tmp_path):
    """The schema gate is answered by *doing the migration*; the perf gate by asking
    it to measure the box again. A reader that answers anything else turns the lever
    into a bypass, so this pins both halves of the distinction."""
    state = tmp_path / "state"
    state.mkdir(parents=True)
    record = tmp_path / "quarantine"
    record.write_text("abc123\n2026-09-30T11:36:07+00:00\n"
                      "perf gate (p50 1359 ms vs 374 ms)\n", encoding="utf-8")
    run = _run_logic(tmp_path, tmp_path / "repo", state,
                     f'recover_may_rebaseline < {shlex.quote(record.as_posix())}\n'
                     "printf 'REBASELINE %s\\n' \"$?\"\n"
                     f'recover_may_lift < {shlex.quote(record.as_posix())}\n'
                     "printf 'LIFT %s\\n' \"$?\"\n")
    assert "REBASELINE 0" in run.stdout and "LIFT 1" in run.stdout, (
        "a perf refusal is named for a re-measurement and never for a lift, which is "
        "what keeps this from being a bypass:\n" + run.stdout + run.stderr)

    record.write_text("abc123\n2026-09-30T11:36:07+00:00\n"
                      "theme gate (exit 1)\n", encoding="utf-8")
    run = _run_logic(tmp_path, tmp_path / "repo", state,
                     f'recover_may_rebaseline < {shlex.quote(record.as_posix())}\n'
                     "printf 'REBASELINE %s\\n' \"$?\"\n")
    assert "REBASELINE 1" in run.stdout, (
        "a theme refusal was treated as re-measurable — only the perf gate names a "
        "measurement this lever can ask for")


def test_a_perf_quarantine_is_answered_by_re_measuring_not_by_a_lift(tmp_path):
    repo = _repo(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    reason = "perf gate (p50 1359 ms vs 374 ms, slower than the last release)"
    (state / "quarantined").write_text(
        f"abc123\n2026-09-30T11:36:07+00:00\n{reason}\n", encoding="utf-8")
    rebaseline = state / "rebaseline-request"
    release = state / "release-request"
    shim = _shim_dir(tmp_path, id=AS_ROOT, stat="echo root", journalctl="exit 0",
                     runuser="exit 0", curl="exit 0", systemctl="exit 0")
    env = _shimmed_env(tmp_path, shim)
    env["SG_REBASELINE_REQUEST"] = str(rebaseline)
    env["SG_RELEASE_FILE"] = str(release)
    run = subprocess.run([BASH, str(RECOVER)], capture_output=True, text=True,
                         cwd=str(repo), env=env)
    both = run.stdout + run.stderr
    assert rebaseline.exists(), (
        "a perf quarantine was not answered by a re-measurement request:\n" + both)
    assert not release.exists(), (
        "a perf quarantine was answered by a plain release request — a lift, not a "
        "re-measurement, which is the bypass this distinction exists to prevent")
    assert reason in both, both


def test_the_lever_names_the_gate_that_refused_the_second_release(tmp_path):
    """End to end, as the box runs it: a dirty tree, a first release that moves
    nothing, a successful set-aside, and a second release a gate quarantines."""
    repo = _repo(tmp_path)
    (repo / "app" / "routes" / "admin_sekolah.py").write_text(
        "a hand edit the release writes\n", encoding="utf-8")
    state = tmp_path / "state"
    state.mkdir()
    counter = (state / "count").as_posix()
    quarantine = (state / "quarantined").as_posix()
    reason = "perf gate (p50 1359 ms vs 374 ms, slower than the last release)"
    systemctl = ("case \"$1\" in\n"
                 "  start)\n"
                 f"    n=$(cat {shlex.quote(counter)} 2>/dev/null || echo 0)\n"
                 "    n=$((n + 1))\n"
                 f"    echo \"$n\" > {shlex.quote(counter)}\n"
                 "    if [ \"$n\" -ge 2 ]; then\n"
                 f"      printf '%s\\n%s\\n%s\\n' deadbeef 2026-09-30T11:36:07+00:00 "
                 f"{shlex.quote(reason)} > {shlex.quote(quarantine)}\n"
                 "    fi ;;\n"
                 "esac\n"
                 "exit 0")
    shim = _shim_dir(tmp_path, id=AS_ROOT, stat="echo root", journalctl="exit 0",
                     runuser="exit 0", curl="exit 0", systemctl=systemctl)
    run = subprocess.run([BASH, str(RECOVER)], capture_output=True, text=True,
                         cwd=str(repo), env=_shimmed_env(tmp_path, shim))
    both = run.stdout + run.stderr
    assert run.returncode == 4, both
    assert reason in both, (
        "the lever stopped at NOT MOVED without naming the gate that refused the "
        "second release, which is the whole complaint against it:\n" + both)
    records = list((state / "recover").glob("*.txt"))
    assert records, "the run wrote no recovery record"
    assert reason in records[0].read_text(encoding="utf-8"), (
        "the gate's name is not in the record the run leaves behind")


# ── 4. the order of the run, read from the script ────────────────────────────

def test_a_tracked_path_is_written_down_before_it_is_restored():
    """`recover_set_aside` runs three steps per tracked path: a durable copy, then
    the record, then the restore. Swapping the last two leaves a run that restored a
    path it never wrote down — a crash in that window keeps the change and loses the
    note that says why, which is the one thing the record exists for.

    Asserted on the order in the block rather than on the end state, because the end
    state is identical either way: the copy is made first in both.
    """
    block = _logic_block()
    recorded = block.index("SET     %s")
    restored = block.index("gitdo checkout HEAD --")
    assert recorded < restored, (
        "the path is restored before its record lands, so a run that dies between "
        "the two leaves a changed tree and no note saying what moved")


def test_migrations_are_applied_before_any_release_is_attempted():
    """The deploy's schema gate refuses a release whose database is behind its code,
    so applying the migrations afterwards would only earn that refusal."""
    source = _text(RECOVER)
    migrations = source.index("the migrations the live schema is missing")
    release = source.index("release_once\nAFTER=")
    assert migrations < release, (
        "the release runs before the migrations, so the schema gate would hold the "
        "very release this run is trying to land")


def test_every_migration_is_tried_before_it_is_committed():
    source = _text(RECOVER)
    trial = source.index('"$PY" "$MIGRATE" "$file" --repo')
    commit = source.index('"$PY" "$MIGRATE" "$file" --commit')
    assert trial < commit, (
        "a file is applied without its trial run — the trial is a rolled-back "
        "transaction, and it is the only rehearsal this box gets")
    assert "TRIAL FAILED" in source and "nothing was applied" in source, (
        "a failed trial must stop the run and say that nothing was applied")


def test_it_never_resets_a_branch_and_never_pushes():
    source = _text(RECOVER)
    for forbidden in ("git reset", "git push", "git checkout -f", "clean -fd"):
        assert forbidden not in source, (
            f"{forbidden!r} appears in the recovery lever — the checkout moves by "
            f"the runner's own fast-forward merge and by nothing else")
    assert "gitdo checkout HEAD --" in source, (
        "restoring one path to HEAD is how an edit is set aside; that is a different "
        "operation from moving the checkout")


def test_the_record_is_opened_before_anything_is_applied_or_set_aside():
    source = _text(RECOVER)
    opened = source.index('> "$RECORD" 2>/dev/null || die "cannot write the recovery record')
    first_apply = source.index("TRIAL FAILED")
    # The *call site*, not the definition above it: `recover_set_aside()` is declared
    # near the top of the file, so matching the bare name would compare the record
    # against a function body and pass no matter where the call moved to.
    set_aside = source.index("| recover_set_aside")
    assert opened < first_apply and opened < set_aside, (
        "the run can change the box before it has anywhere to write down why")


# ── 5. the wiring: one word, on the PATH ─────────────────────────────────────

def test_the_script_parses_and_takes_only_the_one_flag():
    assert subprocess.run([BASH, "-n", str(RECOVER)], capture_output=True,
                          check=False).returncode == 0
    helped = subprocess.run([BASH, str(RECOVER), "--help"], capture_output=True,
                            text=True, check=False)
    assert helped.returncode == 0 and "sgfix" in helped.stdout
    refused = subprocess.run([BASH, str(RECOVER), "--branch", "evil"],
                             capture_output=True, text=True, check=False)
    assert refused.returncode == 2, (
        "the lever accepted an argument that aims it — it is a thing root runs, not a "
        "thing anyone steers, the same rule the deploy runner holds")
    assert "--dry-run" in _text(RECOVER)


def test_the_launcher_name_is_the_short_one_and_points_at_the_script():
    entry = _text(ENTRYPOINT)
    assert "sgfix)" in entry, "the installed name is not the one-word one"
    assert 'TARGET="$REPO/deploy/scangrade-recover.sh"' in entry
    assert 'basename "$0"' in entry, "the launcher must still pick by its own name"


def test_the_gate_zero_block_does_not_apply_to_the_lever():
    """Gate 0 exists to stop a drifted *runner* from deploying. The lever applies no
    gates at all — it starts the unit, which is where the gates live — so requiring
    the block in it would refuse on a box that is working correctly."""
    entry = _text(ENTRYPOINT)
    guard = entry.split('if [ "$(basename "$0")" = "scangrade-deploy" ]; then', 1)
    assert len(guard) == 2
    assert "sgfix)" not in guard[1], (
        "the Gate 0 check was widened to the recovery lever")


def test_the_installer_installs_it_and_the_runner_refreshes_it():
    installer = _text(INSTALLER)
    assert 'RECOVER_BIN="/usr/local/bin/sgfix"' in installer
    assert 'install_launcher "$RECOVER_BIN"' in installer
    runner = _text(RUNNER)
    assert 'INSTALLED_RECOVER="$INSTALLED_BIN_DIR/sgfix"' in runner
    assert 'LAUNCHER_TARGET="$INSTALLED_RECOVER"' in runner, (
        "the runner never refreshes the lever, so a box that predates it would only "
        "get one by running the installer again")


def test_the_lever_also_comes_from_the_fetch_not_only_from_a_release():
    """The installer and the end-of-release refresh both need something the box that
    needs the lever does not have: a release that landed. The fetch is the one step
    that succeeds on a dirty, rolled-back or quarantined checkout, so the mark of the
    fix is that the runner reads the lever out of the *fetched* commit and installs it
    there — see `tests/unit/test_fetch_lever.py` for the behaviour."""
    runner = _text(RUNNER)
    block = runner.split("# fetch-lever-logic:start", 1)
    assert len(block) == 2, "the fetch-lever block is gone"
    body = block[1].split("# fetch-lever-logic:end", 1)[0]
    assert "materialise_lever_from_origin() {" in body
    assert 'origin/$BRANCH:deploy/scangrade-recover.sh' in body, (
        "the lever is not read out of the fetched commit")
    assert "\nmaterialise_lever_from_origin\n" in runner, (
        "the materialiser is never called, so the lever still arrives only with a "
        "release — which is the circle it exists to break")


def test_the_arm_check_reports_the_lever_without_refusing_releases_over_it():
    """A missing lever is worth saying and is not a reason to hold a release: being
    armed means the gates can run, and folding this in would refuse every release on
    every box installed before the lever existed — including the one that installs
    it."""
    arm = _text(ARM)
    assert 'RECOVER_BIN="${SG_RECOVER_BIN:-/usr/local/bin/sgfix}"' in arm
    block = arm.split("recover    :", 1)
    assert len(block) == 2, "the arm check does not report the lever at all"
    tail = block[1].split("\n\n", 1)[0]
    assert "armed=0" not in tail, (
        "a missing recovery lever now holds releases — the trap this check was "
        "written to avoid, one level down")


def test_the_documentation_names_the_word():
    doc = (ROOT / "docs" / "AUTO_DEPLOY.md").read_text(encoding="utf-8")
    assert "sgfix" in doc, "the lever is not documented anywhere an operator looks"


# ── 6. the lever on a box that cannot install it ─────────────────────────────
#
# `sgfix` is installed by the installer and refreshed by the runner, and both of those
# need something a stuck box does not have: a release that landed. What such a box
# *does* have is the commit it fetched back when it was well — so the way in is one
# line that reads this script out of `origin/$BRANCH` and runs it.
#
# The line has to answer two states, and they are different boxes:
#
# * the fetched ref **has** the lever — `git show` is enough, and the line must not
#   depend on reaching GitHub, because the box that needs it may not be able to;
# * the fetched ref **predates** the lever — a box that stalled before this file
#   existed. Then there is nothing to read, and the line has to fetch `origin` as the
#   deploy user and read again, or the only way out is a console session.
#
# And it must never run an *empty* file: a failed `git show > file` leaves the file
# truncated, so a line that runs it anyway exits 0 having done nothing — a silent
# no-op is the one outcome worse than an error. That is why the extract is checked
# before it is run, and why these are behavioural tests against real repositories
# rather than readings of the literal.


def _literal(name: str) -> str:
    """One of the script's own quoted constants, read out of it rather than restated."""
    for line in _text(RECOVER).splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip().strip('"')
    raise AssertionError(f"deploy/scangrade-recover.sh no longer defines {name}")


#: The line names the box's checkout and the file it writes; a test replaces those
#: two and nothing else, so the logic under test is the logic the console gets.
BOX_REPO_IN_LINE = "/opt/scangrade"
BOX_FILE_IN_LINE = "/tmp/sgfix.sh"

#: What the fetched lever says when it runs. Two versions, because the interesting
#: failure is running the *old* one — or the wrong file — and still looking recovered.
LEVER_NEW = "#!/usr/bin/env bash\necho LEVER-NEW-RAN\n"
LEVER_NEW_MARK = "LEVER-NEW-RAN"


def _seed_origin(tmp_path: pathlib.Path):
    """A bare origin and a checkout of it, whose fetched truth has no lever yet.

    That is the box's own first state after the lever landed: the checkout has a
    `main`, an `origin/main` pointing at it, and no `deploy/scangrade-recover.sh` in
    that commit.
    """
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "-c", "init.defaultBranch=main", "init", "-q", "--bare",
                    str(origin)], capture_output=True, text=True, check=True)
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "README").write_text("the release's line\n", encoding="utf-8")
    subprocess.run(["git", "-c", "init.defaultBranch=main", "init", "-q", str(seed)],
                   capture_output=True, text=True, check=True)
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "before the lever")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-q", "-u", "origin", "main")
    checkout = tmp_path / "checkout"
    subprocess.run(["git", "clone", "-q", str(origin), str(checkout)],
                   capture_output=True, text=True, check=True)
    return origin, seed, checkout


def _publish_lever(seed: pathlib.Path, body: str) -> None:
    """Put the lever on the origin's `main`, after the checkout has already fetched."""
    (seed / "deploy").mkdir(exist_ok=True)
    (seed / "deploy" / "scangrade-recover.sh").write_text(body, encoding="utf-8")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "the lever")
    _git(seed, "push", "-q", "origin", "main")


def _line_as_this_box_would_run_it(checkout: pathlib.Path,
                                   extract_to: pathlib.Path) -> str:
    """`GET_LEVER` with this box's two paths swapped for the test's own.

    Only the paths change — the checkout the constant names, and the file it writes —
    because those are the parts that are about *this* box. `runuser` goes too, since
    the test is already one user. Every substitution is asserted, so an edit to the
    literal cannot quietly turn these into tests of nothing.
    """
    line = _literal("GET_LEVER")
    assert line.count(BOX_REPO_IN_LINE) == 3, (
        "the line no longer names the checkout three times (read, fetch, read again): "
        + line)
    assert line.count(BOX_FILE_IN_LINE) == 3, line
    command = (line.replace(BOX_REPO_IN_LINE, checkout.as_posix())
                   .replace(BOX_FILE_IN_LINE, extract_to.as_posix())
                   .replace("runuser -u scangrade -- ", ""))
    assert BOX_REPO_IN_LINE not in command and "runuser" not in command, command
    return command


def _shim_dir(tmp_path: pathlib.Path, **commands: str) -> pathlib.Path:
    """A PATH directory holding one stand-in per command.

    The script decides through `id`, `stat`, `journalctl` and, when it is not root,
    `sudo` — so running it is the only way to see those decisions, and a shim is a
    one-line script executed through its shebang exactly as the box would.
    """
    bin_dir = tmp_path / "shim"
    bin_dir.mkdir(exist_ok=True)
    for name, body in commands.items():
        path = bin_dir / name
        path.write_text("#!/usr/bin/env bash\n" + body + "\n", encoding="utf-8",
                        newline="\n")
        path.chmod(0o755)
    return bin_dir


def _shimmed_env(tmp_path: pathlib.Path, shim: pathlib.Path) -> dict:
    env = dict(os.environ)
    env["PATH"] = str(shim) + os.pathsep + env.get("PATH", "")
    env["SG_REPO"] = str(tmp_path / "repo")
    env["SG_STATE_DIR"] = str(tmp_path / "state")
    return env


#: `id -u` / `id -un`. The box's own answer is what the script branches on, so these
#: are the two values the whole file turns on: root, and an ordinary deploy user.
AS_ROOT = 'case "$1" in -u) echo 0;; -un) echo root;; *) echo 0;; esac'
AS_DEPLOY = 'case "$1" in -u) echo 1000;; -un) echo deploy;; *) echo 1000;; esac'
HOP = 'echo "SUDO-HOP $*"; exit 0'


class TestTheLeverCanBeReadOutOfTheFetchedCommit:
    def test_a_piped_run_reaches_its_own_end(self, tmp_path):
        """Nothing in the script reads its own stdin.

        If anything did, `git show … | bash` would execute the script as far as that
        read and stop — the tail would simply be gone, and the box would be left with
        a recovery that ran, exited, and changed nothing.
        """
        repo = _repo(tmp_path)
        # `runuser` is only asked for its existence here: with the owner reading as
        # root, `as_owner` is the direct branch and never calls it.
        shim = _shim_dir(tmp_path, id=AS_ROOT, stat='echo root', journalctl="exit 0",
                         runuser="exit 0")
        # Bytes in and out: the script is UTF-8 (em dashes in its own output), and a
        # Windows locale would refuse to encode stdin — piped input is exactly what
        # this test is about, so it must go in as the bytes the box would receive.
        run = subprocess.run([BASH, "-s", "--", "--dry-run"],
                             input=_text(RECOVER).encode("utf-8"),
                             capture_output=True, cwd=str(repo),
                             env=_shimmed_env(tmp_path, shim))
        out = run.stdout.decode("utf-8", "replace")
        assert run.returncode == 0, run.stderr.decode("utf-8", "replace")
        assert "why this box is stuck" in out
        assert "dry run — nothing was changed" in out, (
            "the script did not reach its own end when it was piped in: something in "
            "it consumed stdin, so `git show … | bash` stops halfway and reports "
            "nothing wrong\n" + out[-2000:])

    def test_without_root_it_refuses_and_names_the_line_that_works(self, tmp_path):
        """A piped script has nothing to re-run as root, so it must not try.

        Measured on this box: `cat x | bash` and `bash -s < x` both leave `$0` as the
        shell's own path and `BASH_SOURCE` unset, while `bash x.sh` sets both to the
        file. A hop keyed on `$0` therefore either runs whatever `bash` means in the
        current directory or hands sudo the shell binary — and either way the
        recovery silently does not happen, with an error about the wrong thing.
        """
        # Named here because it is the property, not the symptom: the shim below is
        # what the hop would find if it ran.
        source = _text(RECOVER)
        assert '${BASH_SOURCE[0]:-}' in source and '[ -f "${BASH_SOURCE[0]}" ]' in source, (
            "the hop is not guarded by whether this script came from a file")
        shim = _shim_dir(tmp_path, id=AS_DEPLOY, sudo=HOP)
        run = subprocess.run([BASH, "-s"], input=_text(RECOVER).encode("utf-8"),
                             capture_output=True, cwd=str(tmp_path),
                             env=_shimmed_env(tmp_path, shim))
        both = (run.stdout + run.stderr).decode("utf-8", "replace")
        assert run.returncode != 0, "a piped run without root must not claim to recover"
        assert "SUDO-HOP" not in both, (
            "the sudo hop ran on a piped script, where `$0` is 'bash'")
        assert _literal("GET_LEVER") in both, (
            "the refusal has to name the line that does work, not only decline")

    def test_the_refusal_prints_that_line_and_nothing_after_it(self, tmp_path):
        """The exit code is a decision about the refusal, not part of the sentence.

        `die` takes the code as a second argument, and a message printer that joins
        *all* of its arguments leaves it at the end of the instruction — so the one
        thing on this page that is copied by hand reads `… | bash' 1`, which is a
        different command. Typed on a noVNC console with no clipboard, that is the
        difference between a recovery and a shell syntax error.
        """
        shim = _shim_dir(tmp_path, id=AS_DEPLOY, sudo=HOP)
        run = subprocess.run([BASH, "-s"], input=_text(RECOVER).encode("utf-8"),
                             capture_output=True, cwd=str(tmp_path),
                             env=_shimmed_env(tmp_path, shim))
        both = (run.stdout + run.stderr).decode("utf-8", "replace")
        command = "sudo bash -c '" + _literal("GET_LEVER") + "'"
        printed = [line.strip() for line in both.splitlines() if "show origin/" in line]
        assert printed == [command], (
            "the line an operator copies is not exactly the line that works:\n"
            + "\n".join(repr(line) for line in printed))

    def test_with_a_file_it_still_hops(self, tmp_path):
        """The installed launcher execs a file, and that path has to keep working:
        the guard above may not turn the one word into a refusal."""
        copy = tmp_path / "sgfix.sh"
        copy.write_text(_text(RECOVER), encoding="utf-8", newline="\n")
        shim = _shim_dir(tmp_path, id=AS_DEPLOY, sudo=HOP)
        run = subprocess.run([BASH, str(copy)], capture_output=True, text=True,
                             cwd=str(tmp_path), env=_shimmed_env(tmp_path, shim))
        assert "SUDO-HOP" in run.stdout, (
            "the sudo hop stopped working for the installed launcher:\n"
            + run.stdout + run.stderr)
        assert str(copy) in run.stdout

    def test_the_hop_is_guarded_before_it_runs(self):
        """A source rule, because this failure is invisible in a passing run: the
        `BASH_SOURCE` test has to be ahead of the exec, in the same branch."""
        script = _text(RECOVER)
        assert script.index('[ -f "${BASH_SOURCE[0]}" ]') < script.index("exec sudo -E bash"), (
            "the file test moved after the exec, which is the same as not having it")
        assert 'exec sudo -E bash "$0"' not in script, (
            "the hop re-runs `$0`, which on a piped script is the shell itself")

    def test_the_line_is_one_string_in_every_place_it_is_written_down(self):
        """The operator types it, the refusal prints it, the docs carry it.

        Three places, one string: a console handed a command that is nearly the one
        that works is worse than a console handed none.
        """
        lever = _literal("GET_LEVER")
        command = "sudo bash -c '" + lever + "'"
        assert "show origin/main:deploy/scangrade-recover.sh" in lever
        assert command in _text(RECOVER), (
            "the script's own header no longer shows the line an operator types")
        assert command in _text(DOC), (
            "docs/AUTO_DEPLOY.md does not carry the line the refusal prints, so the "
            "documented way back from a rolled-back checkout is a different command")

    def test_the_line_carries_the_fetch_for_a_ref_that_has_no_lever(self):
        """The case the docs used to hand to a console session, in the line itself.

        A box that stalled before `deploy/scangrade-recover.sh` existed has a fetched
        ref with no lever in it, so `git show` fails and the old line ran nothing.
        The line fetches `origin` when — and only when — that read came up empty.
        """
        lever = _literal("GET_LEVER")
        assert "fetch -q origin" in lever, (
            "the line has no fallback, so a fetched ref older than the lever still ends "
            "in nothing running and a console session")
        assert lever.index("show origin/main:deploy/scangrade-recover.sh") < \
            lever.index("fetch -q origin"), (
            "the line fetches before it looks, which hangs a box with no network in "
            "the one case that did not need a fetch")
        doc = _text(DOC)
        assert "older than the lever" in doc, (
            "the docs no longer name the case: a fetched ref from before this script "
            "existed")
        assert "cannot reach GitHub" in doc, (
            "the docs keep a console recipe without saying when it is the only thing "
            "left, so an operator cannot tell it apart from the case the line answers")

    def test_the_line_fetches_when_the_fetched_commit_has_no_lever(self, tmp_path):
        """Behavioural, against real repositories: the pre-lever box is answered."""
        _origin, seed, checkout = _seed_origin(tmp_path)
        out = tmp_path / "sgfix.sh"
        line = _line_as_this_box_would_run_it(checkout, out)
        stale = subprocess.run(["git", "-C", str(checkout), "show",
                                "origin/main:deploy/scangrade-recover.sh"],
                               capture_output=True, text=True, check=False)
        assert stale.returncode != 0, (
            "the harness is not in the pre-lever state the test is about")
        # The lever appears on the origin *after* this box last fetched — which is
        # every box's first state after the lever landed.
        _publish_lever(seed, LEVER_NEW)
        run = subprocess.run([BASH, "-c", line], capture_output=True, text=True,
                             cwd=str(tmp_path), check=False)
        both = run.stdout + run.stderr
        assert LEVER_NEW_MARK in both, (
            "the line did not fetch: a fetched ref older than the lever still ends in "
            "nothing running\n" + both)
        assert run.returncode == 0, both

    def test_the_line_runs_a_lever_it_already_has_without_the_network(self, tmp_path):
        """The fetch is a fallback, not a step: a box that cannot reach GitHub must
        still be recovered by the lever it already fetched."""
        _origin, seed, checkout = _seed_origin(tmp_path)
        _publish_lever(seed, LEVER_NEW)
        _git(checkout, "fetch", "-q", "origin")   # this box is up to date
        _git(checkout, "remote", "set-url", "origin", str(tmp_path / "gone"))
        out = tmp_path / "sgfix.sh"
        run = subprocess.run([BASH, "-c", _line_as_this_box_would_run_it(checkout, out)],
                             capture_output=True, text=True, cwd=str(tmp_path), check=False)
        both = run.stdout + run.stderr
        assert LEVER_NEW_MARK in both, (
            "the line fetched unconditionally, so an unreachable origin stopped the "
            "lever this box already had from running\n" + both)

    def test_the_line_never_runs_an_empty_file(self, tmp_path):
        """Neither read nor fetch worked: the run must fail, not succeed emptily.

        `git show x > f` truncates `f` *before* git runs, so a line that runs it
        anyway executes an empty script, exits 0, and reports a recovery that changed
        nothing. That is the failure this asserts against.
        """
        _origin, _seed, checkout = _seed_origin(tmp_path)
        _git(checkout, "remote", "set-url", "origin", str(tmp_path / "gone"))
        out = tmp_path / "sgfix.sh"
        run = subprocess.run([BASH, "-c", _line_as_this_box_would_run_it(checkout, out)],
                             capture_output=True, text=True, cwd=str(tmp_path), check=False)
        both = run.stdout + run.stderr
        assert LEVER_NEW_MARK not in both
        assert run.returncode != 0, (
            "a line that neither read nor fetched a lever exited 0, which is "
            "indistinguishable from a recovery that ran\n" + both)
        assert not out.exists() or not out.read_text(encoding="utf-8").strip(), (
            "something was written to the file the line runs")

    def test_the_line_runs_what_it_read_and_not_what_was_there_before(self, tmp_path):
        """A leftover file from an earlier attempt must not be what runs."""
        _origin, seed, checkout = _seed_origin(tmp_path)
        _publish_lever(seed, LEVER_NEW)
        out = tmp_path / "sgfix.sh"
        out.write_text("echo STALE-FROM-LAST-TIME\n", encoding="utf-8")
        run = subprocess.run([BASH, "-c", _line_as_this_box_would_run_it(checkout, out)],
                             capture_output=True, text=True, cwd=str(tmp_path), check=False)
        both = run.stdout + run.stderr
        assert "STALE-FROM-LAST-TIME" not in both, (
            "the line ran a file left over from an earlier attempt: " + both)
        assert LEVER_NEW_MARK in both, both
