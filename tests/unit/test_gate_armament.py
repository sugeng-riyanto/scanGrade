"""A gate a release can delete is a gate that stops running — silently.

`deploy/theme_gate.sh` is a hard gate in the VPS auto-deploy: it runs before the
app is reloaded, and a non-zero exit rolls the checkout back. Two of its exit
codes exist because the reasons are different, and conflating them is how a gate
quietly stops working:

  * **2 — the box cannot answer.** No node, no `node_modules`, no SQL to read.
    Environmental. The deploy says so loudly and *does not* roll back, because a
    checker that breaks must not be able to take the site down.
  * **3 — the release removed the check.** A file the gate runs is missing or
    empty, or the named tests collected nothing. That is a property of the
    *release*, and it is worse than any defect the gate looks for: it disarms the
    gate for every release after it, and the first symptom is a page nobody can
    read shipping through a green deploy.

The check for exactly that used to be `[ ! -f ... ]` → `exit 2`, i.e. deleting
`tests/unit/test_dark_theme_contrast.py` made the deploy log "NOT
contrast-checked" and carry on. It is `-s` → `exit 3` now, and the deploy refuses
it with the rollback it uses for a real finding.

The first test runs the gate itself, from a copy of the script in a temporary
tree, so it is the real script deciding — not this file restating its logic. The
last class runs the deploy's own Gate-3 block, extracted from the shipped script,
against a scratch checkout — a guard that greps for `reset --hard` cannot tell a
working rollback from a `reset` aimed at the wrong commit.
`tests/unit/test_css_freshness.py` owns the other half (that the deploy's `-eq 2`
branch carries no `reset --hard` while its failure branch still does).

Mutation-checked, 7/7 injected defects caught: `-s` back to `-f`, both exit-3
arms downgraded to the exit-2 "carry on" code, the armament list trimmed to the
tools, the deploy's exit-3 branch removed, a rollback added to the branch that
must not have one, and the rollback aimed at the new commit instead of the one
that was serving. Two of those were caught only because a test was observed to
fail — `.freebuff/mutate_gate_armament.py` and
`.freebuff/prove_theme_gate_bites.py` carry the harnesses.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
GATE = DEPLOY / "theme_gate.sh"
DEPLOY_SCRIPT = DEPLOY / "scangrade-deploy.sh"
INSTALLER = DEPLOY / "install-auto-deploy.sh"
HOOK = DEPLOY / "git-hooks" / "pre-commit"

ARMAMENT_TOOLS = ("deploy/i18n_coverage.py", "deploy/css_freshness.py",
                  "deploy/schema_contract.py")

EXIT_FINDING, EXIT_CANNOT_RUN, EXIT_DISARMED = 1, 2, 3


def gate_tests() -> list:
    """The files the gate's own `TESTS=` line lists."""
    for line in GATE.read_text(encoding="utf-8").splitlines():
        if line.startswith("TESTS="):
            return line.split("=", 1)[1].strip().strip('"').split()
    raise AssertionError("deploy/theme_gate.sh no longer lists the checks it runs")


def armament_paths() -> list:
    """Every path the gate's own `ARMAMENT=` line names, `$TESTS` expanded."""
    for line in GATE.read_text(encoding="utf-8").splitlines():
        if line.startswith("ARMAMENT="):
            out = []
            for token in line.split("=", 1)[1].strip().strip('"').split():
                out.extend(gate_tests() if token == "$TESTS" else [token])
            return out
    raise AssertionError("deploy/theme_gate.sh no longer checks its own files")


def run_gate_in(tmp_path: Path, *, missing=(), empty=()) -> subprocess.CompletedProcess:
    """The gate, executed from a tree where only the armament is placed.

    The script derives its repository from its own location, so copying it into a
    scratch `deploy/` makes it read that tree — the only way to hand it a release
    with a check gone without touching this checkout.

    Everything the gate says it needs is created, so that a refusal can only come
    from the entry under test rather than from the scratch tree being bare. The
    placeholders are not real checks: a placeholder holds no tests, which is the
    point when the question is whether *presence* is being tested.
    """
    target = tmp_path / "deploy" / "theme_gate.sh"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(GATE, target)

    for rel in armament_paths():
        if rel in missing:
            continue
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("" if rel in empty else "# placeholder\n", encoding="utf-8")

    return subprocess.run(["bash", str(target)], cwd=tmp_path,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def gate_block() -> str:
    """Gate 3 of the deploy script: the part that reads the gate's exit code."""
    src = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    block = src.split("# ── Gate 3: is this release readable?", 1)[1]
    return block.split("# ── Reload", 1)[0]


# ── the gate refuses a release that removed its own checks ───────

class TestTheReleaseCannotDisarmTheGate:
    def test_a_missing_check_is_three_and_not_two(self, tmp_path):
        """The defect this file exists for: 2 means "carry on", 3 means "refuse"."""
        result = run_gate_in(tmp_path, missing=gate_tests())
        assert result.returncode == EXIT_DISARMED, (
            "the gate answered %d for a release with every check file missing — "
            "exit 2 is the code the deploy reads as 'this box cannot judge' and "
            "sails past, which is how deleting a checker would disarm the gate\n%s"
            % (result.returncode, result.stderr[-800:]))
        assert "DISARMED" in result.stderr

    def test_it_names_the_file_it_refused_on(self, tmp_path):
        result = run_gate_in(tmp_path, missing=gate_tests())
        assert Path(gate_tests()[0]).name in result.stderr, (
            "a refusal nobody can act on is a journal line with no fix in it")

    def test_an_emptied_check_file_is_the_same_release_as_a_deleted_one(self, tmp_path):
        """`-s`, not `-f` — the one test here that tells the two apart.

        Existence is not presence: `> tests/unit/test_dark_theme_contrast.py`
        truncates the file instead of deleting it, and `-f` calls that a check
        that is still there. Every other file the gate needs is provided, so a
        refusal has to come from this one entry — and the *file name* is asserted,
        not just the word DISARMED, because the exit-5 branch prints that word too
        and would otherwise let a `-f` mutant pass this test.
        """
        first = gate_tests()[0]
        result = run_gate_in(tmp_path, empty=[first])

        assert result.returncode == EXIT_DISARMED, result.stderr[-800:]
        assert Path(first).name in result.stderr, (
            "the gate refused, but not on the emptied file: "
            + result.stderr[-400:])

    def test_a_run_that_collects_nothing_is_disarmed_too(self):
        """pytest exit 5 is the arm that covers a file full of comments.

        Asserted on the source rather than executed, and the reason is the
        ordering inside the script: the coverage tool runs *before* the exit-5
        arm, so a scratch tree with empty checks leaves the gate at exit 1 (the
        tool cannot compare anything) and never reaches this branch. What is
        checked here is the mapping the branch makes.
        """
        src = GATE.read_text(encoding="utf-8")
        arm = src.split('if [ "$RC" -eq 5 ]', 1)
        assert len(arm) == 2, "the gate no longer handles 'nothing collected'"
        # On a line boundary: the branch's own message says "the files exist", and
        # a bare `split("fi")` truncates inside the word `files` — measured, by
        # this test failing on its own prose.
        body = arm[1].split("\nfi", 1)[0]
        assert f"exit {EXIT_DISARMED}" in body, (
            "a run that collected nothing must be refused, not warned about — "
            "`grep -q passed` and every exit-code reader call it green\n" + body)
        assert f"exit {EXIT_CANNOT_RUN}" not in body, (
            "exit 2 is 'this box cannot answer', and the deploy continues on it")

    def test_every_named_check_is_in_the_armament_list(self):
        """The list and the checks cannot drift: a new check must be covered."""
        src = GATE.read_text(encoding="utf-8")
        armament = [ln for ln in src.splitlines() if ln.startswith("ARMAMENT=")]
        assert armament, "the gate no longer checks its own files up front"
        line = armament[0]
        assert "$TESTS" in line, (
            "the armament list stopped including the test files, so emptying one "
            "would only be caught by pytest's own exit code")
        for tool in ARMAMENT_TOOLS:
            assert tool in line, f"{tool} is run by the gate but not guarded"


# ── the deploy reads 3 the way it reads 1 ────────────────────────

class TestTheDeployRefusesADisarmedRelease:
    def test_the_disarm_arm_rolls_back(self):
        block = gate_block()
        arm = block.split('if [ "$THEME_RC" -eq 3 ]', 1)
        assert len(arm) == 2, "the deploy does not distinguish exit 3 at all"
        assert "DISARMED" in arm[1][:400], (
            "the journal must say the gate was disarmed rather than report it as "
            "an unreadable page — the two are fixed in different places")
        # It lands in the branch that resets, not in the exit-2 branch.
        assert "reset --hard" in arm[1], (
            "a release that removed the checks must be rolled back like any other "
            "refusal, or the disarmed state becomes the new normal")

    def test_the_exit_two_branch_still_does_not_roll_back(self):
        """Restated here because it is the half that a third branch endangers."""
        block = gate_block()
        after = block.split('elif [ "$THEME_RC" -eq 2 ]', 1)[1]
        assert "reset --hard" not in after.split("else", 1)[0], (
            "adding the exit-3 arm must not put a rollback in the 'could not run' "
            "branch: a box without node cannot judge a release")

    def test_the_gate_documents_the_three_codes(self):
        src = GATE.read_text(encoding="utf-8")
        for code in (EXIT_FINDING, EXIT_CANNOT_RUN, EXIT_DISARMED):
            assert f"  {code}  " in src or f"   {code}  " in src, (
                f"the gate's header no longer explains exit {code}, so the next "
                "reader has to guess which failure rolls back")


# ── the other two readers ────────────────────────────────────────

class TestTheOtherCallersRefuseItToo:
    def test_the_installer_refuses_to_arm_a_disarmed_box(self):
        src = INSTALLER.read_text(encoding="utf-8")
        assert '"$rc" -eq 3' in src, (
            "installing the runner on a checkout whose gate is disarmed arms a box "
            "that refuses every future release")
        arm = src.split('"$rc" -eq 3', 1)[1]
        assert "DISARMED" in arm[:400]
        assert "exit 7" in arm[:600], "the installer has to fail, not warn"

    def test_the_installer_still_treats_cannot_run_as_its_own_case(self):
        src = INSTALLER.read_text(encoding="utf-8")
        assert '"$rc" -eq 2' in src, (
            "a box that cannot run the gate is a different installation failure "
            "with a different fix (pip install -r requirements.txt)")

    def test_the_hook_refuses_every_non_zero_and_names_no_single_cause(self):
        """`git commit --no-verify` is the only bypass, and it is Git's own.

        The hook has no carve-out for exit 2 either — locally, "cannot run" is
        the author's problem to fix, unlike on an unattended box.
        """
        src = HOOK.read_text(encoding="utf-8")
        assert "theme_gate.sh" in src
        assert "-eq 2" not in src and "-eq 3" not in src, (
            "the hook must block on any non-zero code; a carve-out turns a broken "
            "checker into a permanently disabled gate")
        assert "--no-verify" in src


# ── and the rollback is executed, not just read ──────────────────

HARNESS = '''#!/usr/bin/env bash
# The deploy's own Gate-3 block, lifted verbatim from deploy/scangrade-deploy.sh
# and run against a scratch checkout. `as_owner` is the one stub: on the box it
# drops privileges to the checkout's owner, and there is no such user here.
set -uo pipefail
REPO="$1"; BEFORE="$2"; THEME_RC="$3"; THEME_OUT="(the gate's own output)"
log() { printf '    %s\n' "$*" >&2; }
as_owner() { "$@"; }
@@BLOCK@@
echo "FELL THROUGH WITHOUT ROLLING BACK"
exit 0
'''


def theme_exit_block() -> str:
    """Gate 3 from `if [ "$THEME_RC" -eq 0 ]` to its closing `fi`."""
    block = gate_block()
    start = block.index('if [ "$THEME_RC" -eq 0 ]')
    tail = block[start:]
    # Ends at the first `fi` that closes it, on its own line at column 0.
    end = tail.index("\nfi\n") + len("\nfi")
    return tail[:end]


def make_scratch_repo(tmp_path: Path):
    """Two commits; `before` is the first, and HEAD is the second."""
    repo = tmp_path / "scratch"
    repo.mkdir()

    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, check=True,
                              capture_output=True, text=True)

    git("init", "-q")
    (repo / "file.txt").write_text("one\n", encoding="utf-8")
    git("add", ".")
    git("-c", "user.email=gate@test", "-c", "user.name=gate",
        "-c", "commit.gpgsign=false", "commit", "-q", "-m", "one")
    before = git("rev-parse", "--short", "HEAD").stdout.strip()
    (repo / "file.txt").write_text("two\n", encoding="utf-8")
    git("add", ".")
    git("-c", "user.email=gate@test", "-c", "user.name=gate",
        "-c", "commit.gpgsign=false", "commit", "-q", "-m", "two")
    return repo, before


class TestTheShippedRollbackActuallyRollsBack:
    """The block above is read for its shape; this runs it.

    A release is refused by moving the checkout back to the commit that was
    serving, so the thing worth executing is the move itself — a guard that only
    greps for `reset --hard` cannot tell a working rollback from a `reset` aimed
    at the new commit.
    """

    def run_block(self, tmp_path, theme_rc):
        repo, before = make_scratch_repo(tmp_path)
        script = tmp_path / "harness.sh"
        script.write_text(HARNESS.replace("@@BLOCK@@", theme_exit_block()),
                          encoding="utf-8")
        proc = subprocess.run(["bash", str(script), str(repo), before, str(theme_rc)],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace")
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=repo,
                              capture_output=True, text=True).stdout.strip()
        return proc, head, before

    @pytest.mark.parametrize("theme_rc", [EXIT_FINDING, EXIT_DISARMED])
    def test_a_refusal_puts_the_previous_release_back(self, tmp_path, theme_rc):
        proc, head, before = self.run_block(tmp_path, theme_rc)
        assert proc.returncode == 13, (
            f"exit {theme_rc} left the script with {proc.returncode}; the deploy has "
            "to end on its rollback code so the journal shows what happened")
        assert head == before, (
            f"exit {theme_rc} did not move the checkout back: HEAD is {head}, the "
            "release that was serving is " + before)

    def test_exit_two_leaves_the_release_alone(self, tmp_path):
        """A checker that cannot run must not be able to take the site down."""
        proc, head, before = self.run_block(tmp_path, EXIT_CANNOT_RUN)
        assert proc.returncode == 0, proc.stderr[-600:]
        assert head != before, "the exit-2 branch rolled back a healthy release"
        assert "FELL THROUGH" in proc.stdout

    def test_a_pass_leaves_the_release_alone(self, tmp_path):
        proc, head, before = self.run_block(tmp_path, 0)
        assert proc.returncode == 0
        assert head != before
