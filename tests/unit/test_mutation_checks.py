"""The mutation harnesses are part of the suite, so they are guarded like it.

A mutation check is the only thing in this repository that asks whether a *guard*
bites, and until now it lived in `.freebuff/` — which `.gitignore` excludes. So
"mutation-checked, 12/12 caught" was a sentence in a commit message that a reader of
the repository could not reproduce, and every new harness re-invented the loop. What
these tests hold is everything about that arrangement which is mechanical rather than
judgement:

* **the directory is the registry** — every `tests/mutation/mutate_*.py` is
  discovered, so adding a harness cannot forget to register it, and a harness cannot
  be a file nobody runs;
* **each one holds together before it is run** — a docstring that says what it
  guards, a non-empty `GUARDS` of files that exist, a non-empty `MUTATIONS` of
  four-part entries with unique labels, and — the failure that actually bit twice
  while these were being written — **every anchor still present in the file it
  names**. That check is what turns a twenty-minute run that ends in "not injected"
  into a one-second failure with the label and the file in it;
* **the contract is the same for every harness** — the shared shape is
  `tests/mutation/harness.py`, and a harness that does not use it is the next one to
  grow its own copy of the loop.

The slow half — actually running a harness, which is minutes per mutation — is
`deploy/mutation_check.py --run NAME`, deliberately **not** part of a test pass. It
is opt-in through `SCANGRADE_MUTATION=1`, which runs one named harness end to end, so
the permanent entry point is exercised by the suite without making a normal run wait
half an hour. That test asserts the score the harness prints, which is the only claim
a reader has to take on trust otherwise.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MUTATION_DIR = ROOT / "tests" / "mutation"
RUNNER = ROOT / "deploy" / "mutation_check.py"
HARNESS = MUTATION_DIR / "harness.py"

sys.path.insert(0, str(ROOT))

RUN = os.environ.get("SCANGRADE_MUTATION", "").strip()
OPT_IN = pytest.mark.skipif(
    not RUN,
    reason="a mutation run is minutes per harness; set SCANGRADE_MUTATION=<name> "
           "to run one (SCANGRADE_MUTATION=all for every one)")


def _runner(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(RUNNER), *args], cwd=str(ROOT),
                          capture_output=True, text=True, timeout=900)


def _describe() -> dict:
    """`--check`'s own reading, from the tool rather than a second implementation."""
    from deploy.mutation_check import describe_all
    return {report["name"]: report for report in describe_all()}


class TestTheDirectoryIsTheRegistry:
    def test_every_harness_on_disk_is_discovered(self):
        from deploy.mutation_check import discover

        on_disk = {path.stem for path in MUTATION_DIR.glob("mutate_*.py")}
        assert on_disk, "tests/mutation/ holds no harness at all"
        assert set(discover()) == on_disk, (
            "a harness on disk that discovery does not find is a file nobody runs")

    def test_the_membership_guard_has_one(self):
        """The guard with the most narrowings and the least visible failure mode."""
        names = _describe()
        assert "mutate_membership_guard" in names
        report = names["mutate_membership_guard"]
        assert report["total"] >= 10, "a handful of clauses is not a guard sweep"
        membership_guards = [g for g in report["guards"] if "membership" in g
                             or "member_" in g or "school_membership" in g]
        assert membership_guards, (
            "the membership harness must name the membership suites as its guards, "
            f"not {report['guards']}")

    def test_the_discovery_is_by_name_not_by_a_list_someone_maintains(self):
        """A registry file would be the same shape as the thing it replaced."""
        from deploy.mutation_check import discover

        source = (MUTATION_DIR / "mutate_gate2_reason.py").read_text(encoding="utf-8")
        assert "sys.exit(main(" in source or "raise SystemExit(main(" in source, (
            "a harness is a module with the shared shape, not a script with its own "
            "loop")
        assert set(discover()) >= {"mutate_gate2_reason", "mutate_membership_guard"}


class TestEveryHarnessHoldsTogether:
    @pytest.mark.parametrize("name,report", sorted(_describe().items()))
    def test_the_harness_loads_and_declares_itself(self, name, report):
        assert not report["error"], f"{name} will not load: {report['error']}"
        assert report["doc"], f"{name} has no docstring saying what it guards"
        assert report["guards"], f"{name} names no guard file"
        assert not report["missing_guards"], (
            f"{name} names guard file(s) that do not exist: {report['missing_guards']}")
        assert report["total"], f"{name} injects nothing"

    @pytest.mark.parametrize("name,report", sorted(_describe().items()))
    def test_every_anchor_still_matches(self, name, report):
        """The failure this tool exists to catch before a run rather than after."""
        stale = [(label, target) for label, state, target in report["mutations"]
                 if state != "ok"]
        assert not stale, (
            f"{name}: these anchors no longer match the source they target, so the "
            f"mutation would be reported as \"not injected\" after a full run:\n  "
            + "\n  ".join(f"{label}  ({target})" for label, target in stale))

    @pytest.mark.parametrize("name,report", sorted(_describe().items()))
    def test_the_labels_are_unique_and_the_targets_are_inside_the_checkout(
            self, name, report):
        labels = [label for label, _, _ in report["mutations"]]
        assert len(labels) == len(set(labels)), (
            f"{name} reuses a mutation label, which makes a survivor ambiguous")
        from deploy.mutation_check import load
        module = load(MUTATION_DIR / f"{name}.py")
        for label, relative, _, _ in module.MUTATIONS:
            assert not str(relative).startswith(("/", "..")), relative
            assert (ROOT / relative).is_file(), relative

    @pytest.mark.parametrize("name,report", sorted(_describe().items()))
    def test_the_shape_is_the_shared_one(self, name, report):
        """A harness with its own loop is the next one to disagree about restoring."""
        source = (MUTATION_DIR / f"{name}.py").read_text(encoding="utf-8")
        assert "from tests.mutation.harness import main" in source, (
            f"{name} does not use the shared harness")
        assert "subprocess" not in source, (
            f"{name} runs pytest itself instead of asking the shared harness to, "
            "which is how restoration and the CRLF translation drift apart")


class TestTheRunner:
    def test_check_passes_on_the_tree_and_says_what_it_read(self):
        done = _runner("--check")
        assert done.returncode == 0, done.stdout + done.stderr
        assert "harness(es) hold together" in done.stdout

    def test_list_names_every_harness_and_its_guards(self):
        done = _runner("--list")
        assert done.returncode == 0, done.stdout + done.stderr
        for name in _describe():
            assert name in done.stdout, name
        assert "guard file(s)" in done.stdout

    def test_an_unknown_harness_cannot_measure_rather_than_pass(self):
        """A run of nothing must never report the all-clear."""
        done = _runner("--run", "mutate_no_such_thing")
        assert done.returncode == 2, (
            "a name that does not exist has to be \"could not measure\", never a pass")
        assert "no harness named" in done.stdout

    def test_the_runner_is_discoverable_from_the_docs_it_lives_beside(self):
        """Anyone has to be able to find it, which is the whole point of tracking it."""
        for rel in ("docs/DEVELOPMENT.md", "AGENTS.md"):
            text = (ROOT / rel).read_text(encoding="utf-8")
            assert "deploy/mutation_check.py" in text, (
                f"{rel} does not tell a reader how to run a mutation check")

    def test_a_harness_that_will_not_load_is_a_measurement_failure(self, tmp_path,
                                                                  monkeypatch):
        from deploy import mutation_check

        broken = tmp_path / "mutate_broken.py"
        broken.write_text("this is not python(\n", encoding="utf-8")
        monkeypatch.setattr(mutation_check, "HARNESS_DIR", tmp_path)
        report = mutation_check.describe(broken)
        assert report["error"], "a file that will not parse is not a harness"
        assert not report.get("mutations"), (
            "an unloadable harness has no readings, rather than empty ones that read "
            "as a clean sweep")
        assert mutation_check.check() == 1, (
            "a harness that cannot be read is a failure of the check, not a pass")


class TestTheGuardedHalf:
    """A harness is a *suite* member in one more sense: its guards are real files."""

    @pytest.mark.parametrize("name,report", sorted(_describe().items()))
    def test_the_guard_files_are_tracked_by_git(self, name, report):
        """An untracked guard is a guard the box does not run either."""
        listed = subprocess.run(["git", "ls-files", *report["guards"]], cwd=str(ROOT),
                                capture_output=True, text=True).stdout.split()
        assert len(listed) == len(report["guards"]), (
            f"{name} names a guard file git does not track: "
            f"{sorted(set(report['guards']) - set(listed))}")

    def test_no_harness_lives_where_git_cannot_see_it(self):
        """`.freebuff/` is ignored, which is where these used to live."""
        ignored = subprocess.run(["git", "check-ignore", str(MUTATION_DIR)],
                                 cwd=str(ROOT), capture_output=True, text=True)
        assert ignored.returncode != 0, (
            "tests/mutation/ is ignored by git, so \"mutation-checked\" would go back "
            "to being a claim nobody can reproduce")


@OPT_IN
def test_a_named_harness_runs_green_end_to_end():
    """Opt-in: `SCANGRADE_MUTATION=mutate_gate2_reason pytest ...`.

    The slow half, run deliberately. It asserts the harness's own report — every
    injected defect caught — because that score is the one thing a reader of a commit
    message cannot otherwise check.
    """
    name = "all" if RUN == "all" else RUN
    args = ["--all"] if name == "all" else ["--run", name]
    done = _runner(*args)
    assert done.returncode == 0, done.stdout[-4000:] + done.stderr[-2000:]
    scores = re.findall(r"(\d+)/(\d+) injected defects caught", done.stdout)
    assert scores, "the harness printed no score: " + done.stdout[-2000:]
    for caught, total in scores:
        assert caught == total, (
            f"{name}: {caught}/{total} — a survivor is a hole in the guard, not a "
            "number to report as a pass")
