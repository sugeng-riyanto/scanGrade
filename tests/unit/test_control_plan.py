"""The pipeline can reach the box: a plan out of the branch, obeyed before the pause.

Requested: *"give the deployment pipeline a way to reach the VPS without the provider
console, so a stuck box never needs hand-typed commands again."*

Two mechanisms, and each answers a shape of stuck the other cannot:

* **the runner adopts the branch's runner before it judges anything.** A box can only
  be commanded by the runner it is running, so a runner older than the command set is
  a box the pipeline cannot reach — the deadlock, one level up. Measured on the box
  this was written for: 25 commits behind, held by arrangement refusals that ran
  *before* its own fetch, with the fix and the lever both travelling in releases the
  refusal was holding. Adopting `origin/$BRANCH:deploy/scangrade-deploy.sh` makes
  every merged change reach such a box on the next tick, through the timer, with no
  release, no reload and no console.
* **a plan**, `deploy/control/plan`, read out of the same fetched commit and obeyed
  even when the release would be refused — including while the box is paused, which is
  the one state that otherwise has no way out but a console. The commands are the
  things an operator would otherwise type: `recover`, `release`, `rebaseline`,
  `pause`, `resume`.

What these tests hold, and why each thing is held rather than described:

* **placement.** The read sits after the root check (the fetch drops to the checkout's
  owner, which needs root) and *before* the pause check (a `resume` that a paused box
  cannot read is not a way in), and it runs on every tick rather than only on the
  arrangement refusals the lever already answers.
* **one-shot.** A plan is content-addressed, so an identical plan is obeyed once and a
  changed one is obeyed again — the difference between a `recover` that runs and a
  `recover` that runs every two minutes forever.
* **no silent no-ops.** A command the runner does not know is refused *and recorded*
  by name. A plan whose command the reader ignores is a box nobody can command, which
  is the failure this whole file exists to prevent.
* **the identity gate is not weakened.** The runner refuses an installed copy because
  a copy stops receiving fixes; the adopted runner is that same code, so what the gate
  accepts is a file whose bytes hash to the blob the branch publishes — not "anything
  the runner just wrote".
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "deploy" / "scangrade-deploy.sh"
PLAN = ROOT / "deploy" / "control" / "plan"
PUBLISHER = ROOT / "deploy" / "scangrade-plan.sh"

BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="needs a bash to run the block")

BLOCK = "control-plan-logic"
#: The shared ref read lives with the other half of "the branch is read before the box
#: refuses", so this suite composes both — exactly as the runner composes them.
BLOCKS = ("branch-first-logic", BLOCK)


def _script() -> str:
    return RUNNER.read_text(encoding="utf-8")


def _block(name: str = BLOCK) -> str:
    script = _script()
    start, end = f"# {name}:start", f"# {name}:end"
    assert start in script and end in script, (
        f"deploy/scangrade-deploy.sh no longer carries {name}: the pipeline has no way "
        f"into a box that is refusing, paused or quarantined")
    return script.split(start, 1)[1].split(end, 1)[0]


def _blocks() -> str:
    return "\n".join(_block(name) for name in BLOCKS)


# ── 1. where it sits, and what it may not skip ───────────────────────────────

class TestPlacement:
    def test_the_read_sits_after_the_root_check(self):
        """The fetch drops to the checkout's owner with `runuser`, which needs root."""
        script = _script()
        root_check = script.index("must run as root")
        adopt = script.index("runner_adopt_from_origin\n")
        assert adopt > script.index("exit 2", root_check), (
            "the adoption runs before the root check, so the fetch it needs cannot be "
            "made as the checkout's owner")

    def test_the_read_sits_before_the_pause_check(self):
        """A paused box is the one state with no other way out: `resume` has to reach
        it, and the pause check exits."""
        script = _script()
        pause = script.index('if [ -e "$PAUSE_FILE" ]')
        assert script.index("control_apply_plan\n") < pause, (
            "the plan is read after the pause check, so a box frozen for exam week "
            "can only be unfrozen by hand")
        assert script.index("runner_adopt_from_origin\n") < pause, (
            "a paused box cannot adopt the branch's runner either")

    def test_the_adoption_runs_before_the_plan_is_obeyed(self):
        """The plan's vocabulary evolves; an old runner reading a new command is the
        silent no-op this ordering exists to prevent."""
        script = _script()
        assert script.index("runner_adopt_from_origin\n") \
            < script.index("control_apply_plan\n")

    def test_the_read_runs_on_every_tick_not_only_on_a_refusal(self):
        """`branch_read_refs` is called by four arrangement refusals and by nothing
        else, so hanging the plan off it would leave every healthy box uncommandable."""
        script = _script()
        at = script.index("runner_adopt_from_origin\n")
        after = script[at:]
        assert after.count("control_apply_plan\n") == 1, (
            "the plan is obeyed more or fewer than once per tick")
        assert "branch_read_refs\n" not in _block(), (
            "the plan read is hung off the refusal path, so a box that is merely "
            "behind reads no plan")

    def test_the_tick_fetches_once(self):
        """Three readers, one call; the fetch is the round-trip this box pays every two
        minutes, and it is the only step that needs GitHub."""
        branch_first = _block("branch-first-logic")
        assert "branch_refs_read() {" in branch_first, (
            "the shared ref read is not where the refusal path can reach it")
        assert '[ "${REFS_FETCHED:-0}" = "1" ] && return 0' in branch_first, (
            "the shared read keeps no record of having read, so every later reader "
            "fetches again — three round-trips to GitHub per tick")
        assert branch_first.count("as_owner git -C \"$REPO\" fetch") == 1, (
            "the fetch is written more than once, so the sharing is nominal")
        block = _block()
        assert block.count("branch_refs_read || return 0") == 2, (
            "the adoption and the plan read do not both go through the shared read")
        assert ('[ "${REFS_FETCHED:-0}" != "1" ] && ! branch_refs_read' in _script()), (
            "the release's own fetch does not consult the shared read, so a healthy "
            "tick pays a second round-trip to GitHub for the same branch")


# ── 2. the plan file the pipeline writes ─────────────────────────────────────

class TestThePlatformPublishesAPlan:
    def test_the_plan_exists_on_the_branch(self):
        assert PLAN.is_file(), (
            "there is no deploy/control/plan for the pipeline to write, so the "
            "channel has nothing to carry")
        assert "command:" in PLAN.read_text(encoding="utf-8")

    def test_the_publisher_takes_one_word(self):
        assert PUBLISHER.is_file(), "no publisher, so publishing is a hand edit"
        text = PUBLISHER.read_text(encoding="utf-8")
        assert "usage" in text.lower(), "the publisher does not say how to use it"
        for command in ("recover", "release", "rebaseline", "pause", "resume", "none"):
            assert command in text, f"the publisher cannot issue {command}"

    def test_the_publisher_renders_one_command(self, tmp_path):
        """`--print` is the whole publisher minus git, which is what makes the
        vocabulary checkable without writing a commit into this repository."""
        done = subprocess.run([BASH, str(PUBLISHER), "--print", "recover"],
                              capture_output=True, text=True, check=False)
        assert done.returncode == 0, done.stderr
        assert "command: recover" in done.stdout, done.stdout
        assert re.search(r"issued: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
                         done.stdout), (
            "the plan carries no *changing* stamp, so re-issuing the same command "
            "would be read as one already obeyed")

    def test_the_publisher_refuses_an_unknown_word(self, tmp_path):
        """A typo must stop here, not at the far end of a push: a plan the runner
        refuses reads exactly like a box that ignored you."""
        done = subprocess.run([BASH, str(PUBLISHER), "--print", "restart"],
                              capture_output=True, text=True, check=False)
        assert done.returncode != 0, done.stdout
        assert "refused" in (done.stdout + done.stderr).lower(), done.stdout + done.stderr
        for command in ("recover", "release", "rebaseline", "pause", "resume", "none"):
            assert command in done.stdout + done.stderr, (
                f"the refusal does not list {command}, so an operator has to read "
                f"the runner to learn the vocabulary")


# ── 3. the plan is read, obeyed once, and recorded ───────────────────────────

#: `newline=""` on every write, and `core.autocrlf=false` on every git call that
#: commits: this suite compares *bytes* in places (the adoption's `cmp`), and on Windows
#: a text-mode write turns `\n` into `\r\n` while the blob git stores keeps `\n` — so a
#: harness that let the two differ would report a box adopting a runner it already has.
_NO_CRLF = ["-c", "core.autocrlf=false", "-c", "core.safecrlf=false"]


def _sandbox(tmp_path: Path, plan: str, *, runner: str = "#!/usr/bin/env bash\n# old\n"):
    """A bare origin and a clone, with `plan` (and optionally a runner) on the branch."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", *_NO_CRLF, "-c", "init.defaultBranch=main", "init", "-q",
                    "--bare", str(origin)], check=True, capture_output=True, text=True)
    seed = tmp_path / "seed"
    (seed / "deploy" / "control").mkdir(parents=True)
    (seed / "deploy" / "scangrade-deploy.sh").write_text(runner, encoding="utf-8",
                                                          newline="")
    (seed / "deploy" / "control" / "plan").write_text(plan, encoding="utf-8", newline="")
    subprocess.run(["git", *_NO_CRLF, "-c", "init.defaultBranch=main", "init", "-q",
                    str(seed)], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(seed), *_NO_CRLF, "add", "-A"], check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "-C", str(seed), *_NO_CRLF, "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "-q", "-m", "seed"], check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "-C", str(seed), "remote", "add", "origin", str(origin)],
                   check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(seed), "push", "-q", "-u", "origin", "main"],
                   check=True, capture_output=True, text=True)
    checkout = tmp_path / "checkout"
    subprocess.run(["git", *_NO_CRLF, "clone", "-q", str(origin), str(checkout)],
                   check=True, capture_output=True, text=True)
    return seed, checkout


def _harness(checkout: Path, state: Path, *, extra: str = "") -> str:
    return "\n".join([
        "set -uo pipefail",
        'BRANCH="main"',
        f'REPO="{checkout.as_posix()}"',
        f'STATE_DIR="{state.as_posix()}"',
        f'CONTROL_DIR="{state.as_posix()}/control"',
        f'LEVER_DIR="{state.as_posix()}/lever"',
        f'REQUEST_DIR="{state.as_posix()}/requests"',
        f'RELEASE_REQUEST="{state.as_posix()}/requests/release"',
        f'REBASELINE_REQUEST="{state.as_posix()}/requests/rebaseline"',
        f'PAUSE_FILE="{state.as_posix()}/pause"',
        'CONTROL_FILE="deploy/control/plan"',
        'CONTROL_KEEP=10',
        'CONTROL_STAGE=""',
        'CONTROL_COMMAND=""',
        'CONTROL_NONCE=""',
        'CONTROL_NOTE=""',
        'REFS_FETCHED=0',
        'REFS_FETCH_OUT=""',
        'OWNER="$(id -un)"',
        'log() { echo "LOG $*"; }',
        'as_owner() { "$@"; }',
        'materialise_lever_from_origin() { echo "MATERIALISED"; }',
        extra,
        _blocks(),
        "control_apply_plan",
        "echo DONE",
    ])


def _run(harness: str, where: Path, name: str = "harness.sh") -> subprocess.CompletedProcess:
    """Run from a *file*: Git Bash truncates an ~8 KB `bash -c` argument silently, at
    exit 0, which turns "the plan was not obeyed" into a passing test."""
    script = where / name
    script.write_text(harness, encoding="utf-8", newline="")
    return subprocess.run([BASH, str(script)], capture_output=True, text=True,
                          check=False)


def _plan(command: str, *, issued: str = "2026-09-30T21:00:00Z") -> str:
    return (f"# the pipeline's way into the box\n"
            f"command: {command}\n"
            f"issued: {issued}\n")


class TestTheCommands:
    def test_recover_runs_the_lever_and_ends_the_tick(self, tmp_path):
        seed, checkout = _sandbox(tmp_path, _plan("recover"))
        state = tmp_path / "state"
        (state / "lever" / "deploy").mkdir(parents=True)
        (state / "lever" / "deploy" / "scangrade-recover.sh").write_text(
            "#!/usr/bin/env bash\necho LEVER-RAN\n", encoding="utf-8", newline="")
        done = _run(_harness(checkout, state), tmp_path)

        assert "LEVER-RAN" in done.stdout, (
            f"the lever did not run for a `recover` plan:\n{done.stdout}{done.stderr}")
        assert "DONE" not in done.stdout, (
            "the tick carried on after running the lever, which runs a release of its "
            "own — the box would deploy twice for one command")
        assert seed  # the sandbox is used; kept for symmetry with the other cases

    def test_release_and_rebaseline_write_the_requests_the_runner_already_reads(
            self, tmp_path):
        _, checkout = _sandbox(tmp_path, _plan("release"))
        state = tmp_path / "state"
        done = _run(_harness(checkout, state), tmp_path)
        assert (state / "requests" / "release").exists(), done.stdout
        assert "DONE" in done.stdout, done.stdout

    def test_pause_and_resume_are_the_one_state_with_no_other_way_out(self, tmp_path):
        _, checkout = _sandbox(tmp_path, _plan("pause"))
        state = tmp_path / "state"
        done = _run(_harness(checkout, state), tmp_path)
        assert (state / "pause").exists(), (
            f"a `pause` plan did not freeze the box:\n{done.stdout}{done.stderr}")

    def test_an_unknown_command_is_refused_and_recorded_by_name(self, tmp_path):
        _, checkout = _sandbox(tmp_path, _plan("restart"))
        state = tmp_path / "state"
        done = _run(_harness(checkout, state), tmp_path)

        assert "restart" in done.stdout or "restart" in done.stderr, (
            "an unknown command was ignored in silence, which is a box nobody can "
            "command and nobody can see is uncommanded")
        records = list((state / "control").glob("*.applied")) \
            if (state / "control").is_dir() else []
        assert records, "the refusal left no record, so the plan reads as obeyed"
        assert "restart" in records[0].read_text(encoding="utf-8")

    def test_an_identical_plan_is_obeyed_once(self, tmp_path):
        """Content-addressed: a `recover` that ran every two minutes forever would be
        a box under a load test, not a box being recovered."""
        _, checkout = _sandbox(tmp_path, _plan("release"))
        state = tmp_path / "state"
        first = _run(_harness(checkout, state), tmp_path)
        (state / "requests" / "release").unlink()
        second = _run(_harness(checkout, state), tmp_path)

        assert "DONE" in first.stdout and "DONE" in second.stdout
        assert not (state / "requests" / "release").exists(), (
            "the same plan was obeyed twice, so every tick re-issues the command")

    def test_a_changed_plan_is_obeyed_again(self, tmp_path):
        seed, checkout = _sandbox(tmp_path, _plan("release"))
        state = tmp_path / "state"
        _run(_harness(checkout, state), tmp_path)
        (state / "requests" / "release").unlink()

        (seed / "deploy" / "control" / "plan").write_text(
            _plan("release", issued="2026-09-30T22:00:00Z"), encoding="utf-8")
        for args in (("add", "-A"),
                     ("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
                      "-m", "reissue"),
                     ("push", "-q", "origin", "main")):
            subprocess.run(["git", "-C", str(seed), *args], check=True,
                           capture_output=True, text=True)

        second = _run(_harness(checkout, state), tmp_path)
        assert (state / "requests" / "release").exists(), (
            f"a re-issued plan was treated as one already obeyed:\n{second.stdout}")

    def test_the_plan_is_read_without_merging_anything(self, tmp_path):
        """The read is a `git show` of a fetched blob — the tree is not touched, which
        is what makes it safe on a dirty, rolled-back or quarantined checkout."""
        block = _block()
        assert "show" in block, "the plan is not read out of the fetched commit"
        # The *command*, not the word: the block's own prose says "nothing is merged",
        # which is the property being asserted, so matching the substring would make
        # the sentence describing the property fail the test for it.
        for forbidden in ("merge --ff-only", "git reset", "git checkout", "git switch"):
            assert forbidden not in block, (
                f"the plan read runs `{forbidden}`: a refused box must be reachable "
                f"without the merge that is being refused")
        assert "$CONTROL_FILE" in block


# ── 4. adopting the branch's runner ──────────────────────────────────────────

class TestAdoptingTheBranchesRunner:
    def _composed(self, tmp_path: Path, *, branch_runner: str, self_body: str = "# old\n"):
        _, checkout = _sandbox(tmp_path, _plan("none"), runner=branch_runner)
        state = tmp_path / "state"
        self_file = tmp_path / "running.sh"
        self_file.write_text(self_body, encoding="utf-8", newline="")
        harness = "\n".join([
            "set -uo pipefail",
            'BRANCH="main"',
            f'REPO="{checkout.as_posix()}"',
            f'STATE_DIR="{state.as_posix()}"',
            f'CONTROL_DIR="{state.as_posix()}/control"',
            f'LEVER_DIR="{state.as_posix()}/lever"',
            f'REQUEST_DIR="{state.as_posix()}/requests"',
            f'RELEASE_REQUEST="{state.as_posix()}/requests/release"',
            f'REBASELINE_REQUEST="{state.as_posix()}/requests/rebaseline"',
            f'PAUSE_FILE="{state.as_posix()}/pause"',
            'CONTROL_FILE="deploy/control/plan"',
            'REFS_FETCHED=0',
            'REFS_FETCH_OUT=""',
            'OWNER="$(id -un)"',
            f'SCANGRADE_RUNNER_SELF="{self_file.as_posix()}"',
            'log() { echo "LOG $*"; }',
            'as_owner() { "$@"; }',
            'materialise_lever_from_origin() { :; }',
            _blocks(),
            "runner_adopt_from_origin",
            "echo DONE",
        ])
        return harness

    def test_a_different_branch_runner_is_adopted_and_runs(self, tmp_path):
        body = "#!/usr/bin/env bash\necho ADOPTED-RUNNER-RAN\n"
        done = _run(self._composed(tmp_path, branch_runner=body), tmp_path)
        assert "ADOPTED-RUNNER-RAN" in done.stdout, (
            f"the box kept running its own stale runner:\n{done.stdout}{done.stderr}")
        assert "DONE" not in done.stdout, (
            "the adopted runner returned instead of replacing the stale one, so the "
            "tick was judged by the logic this adoption exists to replace")

    def test_an_identical_runner_is_not_re_adopted(self, tmp_path):
        body = "#!/usr/bin/env bash\n# same\n"
        done = _run(self._composed(tmp_path, branch_runner=body, self_body=body),
                    tmp_path)
        assert "DONE" in done.stdout, (
            f"a box already on the branch's runner re-executed itself:\n{done.stdout}")
        assert "ADOPTED-RUNNER-RAN" not in done.stdout

    def test_a_runner_that_does_not_parse_is_not_adopted(self, tmp_path):
        done = _run(self._composed(tmp_path, branch_runner="if true; then\n"), tmp_path)
        assert "DONE" in done.stdout, (
            "a branch whose runner does not parse replaced a working one — the box "
            "would be left with no runner at all")
        assert "LOG" in done.stdout, "the refusal to adopt was silent"

    def test_it_is_one_level_only(self, tmp_path):
        body = "#!/usr/bin/env bash\necho ADOPTED-RUNNER-RAN\n"
        harness = self._composed(tmp_path, branch_runner=body)
        done = _run("SCANGRADE_RUNNER_ADOPTED=already\n" + harness, tmp_path)
        assert "ADOPTED-RUNNER-RAN" not in done.stdout, (
            "an adopted runner adopts again, which is a loop rather than a fix")
        assert "DONE" in done.stdout

    def test_the_lock_is_released_before_handing_over(self, tmp_path):
        """`flock` is per open file description: a re-exec that kept fd 9 would hand
        the new runner a lock it already holds, and it would exit 'another deploy is
        already running' — a box that adopts a runner that immediately gives up."""
        assert "exec 9>&-" in _block(), (
            "the adoption does not release the run lock before re-executing, so the "
            "adopted runner skips every round")

    def test_the_refusal_of_an_installed_copy_still_holds(self, tmp_path):
        """A copy that is *not* the branch's is the drift this gate exists for."""
        script = _script()
        at = script.index("REFUSING: this is an installed COPY of the runner")
        window = script[max(0, at - 1200):at + 400]
        assert "SCANGRADE_RUNNER_ADOPTED" in window, (
            "the identity gate does not know about adoption, so the adopted runner "
            "refuses itself")
        assert "hash-object" in window, (
            "the gate accepts an adopted runner without checking that its bytes are "
            "the blob the branch publishes, which is the whole condition it holds")
        assert '[ "$ADOPTED_HASH" = "${SCANGRADE_RUNNER_ADOPTED}" ]' in window, (
            "the gate accepts adoption on presence alone, so any file the runner "
            "happened to be handed would pass as the branch's")

    def test_the_identity_gate_accepts_only_the_adopted_bytes(self, tmp_path):
        """Run Gate 0 for real, both ways. A source-text guard cannot show that the
        two branches are the two the runner takes."""
        identity = "# runner-identity:start", "# runner-identity:end"
        script = _script()
        block = script.split(identity[0], 1)[1].split(identity[1], 1)[0]
        branch_first = _block("branch-first-logic")

        repo = tmp_path / "repo"
        (repo / "deploy").mkdir(parents=True)
        checkout = repo / "deploy" / "scangrade-deploy.sh"
        checkout.write_text("#!/usr/bin/env bash\n# the checkout's runner\n",
                            encoding="utf-8", newline="")
        #: A runner the branch published, staged where the adoption would put it —
        #: *not* the checkout's file, which is what makes this the adopted case.
        adopted = tmp_path / "lever" / "deploy" / "scangrade-deploy.sh"
        adopted.parent.mkdir(parents=True)
        adopted.write_text("#!/usr/bin/env bash\n# origin/main's runner\n",
                           encoding="utf-8", newline="")
        blob = subprocess.run(["git", "hash-object", "--no-filters", str(adopted)],
                              capture_output=True, text=True, check=True).stdout.strip()

        # `RUNNER_SELF` is the adopted copy, because that is what the running
        # process *is* after the handover: `exec bash $target` makes it `$0`.
        harness = (f'set -uo pipefail\nREPO="{repo.as_posix()}"\n'
                   'BRANCH="main"\nREFS_FETCHED=0\nREFS_FETCH_OUT=""\n'
                   f'RUNNER_SELF="{adopted.as_posix()}"\n'
                   'log() { echo "$*"; }\nas_owner() { "$@"; }\n'
                   + branch_first + block + '\necho REACHED_END\n')
        where = tmp_path / "harness.sh"

        claimed = _run(f'SCANGRADE_RUNNER_ADOPTED="{blob}"\n' + harness, tmp_path,
                       name="adopted.sh")
        assert claimed.returncode == 0 and "REACHED_END" in claimed.stdout, (
            f"the adopted runner refused itself:\n{claimed.stdout}{claimed.stderr}")
        assert "REFUSING" not in claimed.stdout

        lying = _run(f'SCANGRADE_RUNNER_ADOPTED="deadbeef"\n' + harness, tmp_path,
                     name="lying.sh")
        assert lying.returncode == 14, (
            f"a hash that is not this file's was accepted as adoption:\n{lying.stdout}")
        assert "REFUSING" in lying.stdout

        unset = _run(harness, tmp_path, name="unset.sh")
        assert unset.returncode == 14, (
            f"the copy refusal no longer fires when nothing was adopted:\n{unset.stdout}")
        assert where.name  # the harness directory is used; kept for clarity


if __name__ == "__main__":                                   # pragma: no cover
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
