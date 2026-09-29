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

import pathlib
import shlex
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
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
gitdo() { git -C "$REPO" "$@"; }
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
              .replace("__SCHEMA__", shlex.quote(_constant("SCHEMA_GATE_WORD"))))
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
