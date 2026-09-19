"""Arming the box has to survive being done from a login that is not root.

`deploy/install-auto-deploy.sh` needs root: it writes the two launchers in
/usr/local/bin and the units under /etc/systemd/system, and run from a non-root
shell it prints a banner and exits 2 *before touching anything*. That refusal is
correct and it is silent in effect — the box keeps serving the old file, the
journal stays quiet, and "I ran the installer" remains true while nothing has
been armed.

`deploy/arm-auto-deploy.sh` is the answer to that: it says what the box is
running, elevates once when it must, keeps the installer's output in a log, and
says what is still missing afterwards. These tests drive the real script against
a scratch tree — the same way `test_gate_armament.py` drives the real gate — so
what is asserted is the script's decision, not this file's restatement of it.

The three states it must tell apart are the three the super-admin deploy-status
page reports: a launcher that renders from the checkout (armed), one that was
never rendered, and a *copy* of the deploy script — which deploys every release
while running no gate at all, the state this whole wrapper exists to make loud.

Mutation-checked, **10/10 injected defects caught**
(`.freebuff/mutate_arm_auto_deploy.py`): the copy state read as armed, the
unrendered state read as armed, a missing snapshot a missing conf and a missing
roster each read as armed, the no-terminal refusal removed, the elevation loop
guard removed, the roster validated only after the password prompt, the installer
run without keeping its log, and the roster counted by entries instead of by role.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
#: Overridable so `.freebuff/mutate_arm_auto_deploy.py` can run this file against
#: a mutated copy: a mutation harness that rewrites the real script in place can
#: leave it mutated after a crash, and this file's own subject is a shell script.
SCRIPT = Path(os.environ.get("ARM_SCRIPT") or (ROOT / "deploy" / "arm-auto-deploy.sh"))

# What `deploy/entrypoint.sh` renders: a launcher whose whole job is to exec the
# checkout's deploy script, so every gate in the repo is what runs.
LAUNCHER = """#!/usr/bin/env bash
REPO="/opt/scangrade"
case "$(basename "$0")" in
  scangrade-deploy) TARGET="$REPO/deploy/scangrade-deploy.sh" ;;
esac
exec bash "$TARGET" "$@"
"""

# What the 12 September install left behind: the deploy logic itself, copied, with
# no theme, claims, performance or quarantine block inside it.
COPY = """#!/usr/bin/env bash
set -uo pipefail
REPO="/opt/scangrade"
SERVICE="scangrade"
log() { echo "$*"; }
"""

UNRENDERED = """#!/usr/bin/env bash
REPO="@REPO@"
TARGET="$REPO/deploy/scangrade-deploy.sh"
"""

ROSTER = [
    {"email": "m1@siswa.scan-grade.app", "role": "murid"},
    {"email": "m2@siswa.scan-grade.app", "role": "murid"},
    {"email": "m3@siswa.scan-grade.app", "role": "murid"},
    {"email": "g1@scan-grade.app", "role": "guru"},
]

# The fake python the wrapper reaches for at $REPO/.venv/bin/python. A developer
# checkout's venv is a Windows executable, so the wrapper is handed a shim that
# forwards to the interpreter running the suite — the JSON handling under test is
# still real Python doing it.
def _python_shim(repo: Path) -> Path:
    target = ROOT / ".venv" / "Scripts" / "python.exe"
    if not target.exists():  # a Linux checkout installs a normal venv
        target = Path(sys.executable)
    shim = repo / ".venv" / "bin" / "python"
    shim.parent.mkdir(parents=True, exist_ok=True)
    shim.write_text(f'#!/usr/bin/env bash\nexec "{target}" "$@"\n', encoding="utf-8")
    shim.chmod(0o755)
    return shim


def scratch(tmp_path, *, runner=COPY, gates=True, snapshot=True, claims=True,
            perf=True, roster=ROSTER):
    """A tree shaped like the box, with only the paths under test populated."""
    repo = tmp_path / "repo"
    (repo / "deploy").mkdir(parents=True, exist_ok=True)

    ran = tmp_path / "installer-ran"
    body = f'#!/usr/bin/env bash\necho "fake installer"\ntouch "{ran}"\nexit 0\n'
    (repo / "deploy" / "install-auto-deploy.sh").write_text(body, encoding="utf-8")

    blocks = "".join(f"# {g}\n" for g in ("theme_gate", "claims_gate", "perf_gate",
                                           "quarantine")) if gates else ""
    (repo / "deploy" / "scangrade-deploy.sh").write_text(
        "#!/usr/bin/env bash\n" + blocks, encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "scangrade-deploy").write_text(runner, encoding="utf-8")
    if snapshot:
        (bin_dir / "scangrade-db-snapshot").write_text(LAUNCHER, encoding="utf-8")

    etc = tmp_path / "etc"
    etc.mkdir()
    if claims:
        (etc / "claims.conf").write_text('CLAIMS_ENFORCE="true"\n', encoding="utf-8")
    if perf:
        (etc / "perf.conf").write_text('PERF_ENFORCE="true"\n', encoding="utf-8")

    roster_src = tmp_path / "lt_roster.json"
    dst = repo / ".freebuff" / "lt_roster.json"
    if roster is not None:
        roster_src.write_text(json.dumps(roster), encoding="utf-8")
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(json.dumps(roster), encoding="utf-8")
    else:
        roster_src = tmp_path / "absent.json"

    _python_shim(repo)

    env = {
        **os.environ,
        "SG_REPO": str(repo),
        "SG_DEPLOY_BIN": str(bin_dir / "scangrade-deploy"),
        "SG_SNAPSHOT_BIN": str(bin_dir / "scangrade-db-snapshot"),
        "SG_CLAIMS_CONF": str(etc / "claims.conf"),
        "SG_PERF_CONF": str(etc / "perf.conf"),
        "SG_ROSTER_SRC": str(roster_src),
        "SG_LOG": str(tmp_path / "installer.log"),
    }
    # ROSTER_DST is deliberately not overridable: it is the path the installer and
    # both gates already agree on, and pointing it elsewhere would arm nothing.
    return repo, bin_dir, ran, env


def run(env, *args):
    return subprocess.run(["bash", str(SCRIPT), *args], env=env, cwd=str(SCRIPT.parent),
                          capture_output=True, text=True, stdin=subprocess.DEVNULL,
                          timeout=120)


def tree_state(path: Path) -> list:
    return sorted((str(p.relative_to(path)), p.stat().st_size)
                  for p in path.rglob("*") if p.is_file())


# ── the three states of the installed file ───────────────────────────────────

class TestItTellsTheThreeStatesApart:
    def test_a_copy_that_knows_no_gate_reads_as_unarmed(self, tmp_path):
        """The state the box was actually in: it deploys, and checks nothing."""
        _, _, _, env = scratch(tmp_path, runner=COPY)
        r = run(env, "--check")
        assert r.returncode == 1, r.stdout + r.stderr
        assert "a COPY of the deploy script" in r.stdout
        assert "no theme_gate, no claims_gate, no perf_gate, no quarantine" in r.stdout, \
            "the copy's blind spots are not named, so nothing points at what it cannot see"
        assert "NOT ARMED" in r.stdout

    def test_a_launcher_that_execs_the_checkout_reads_as_armed(self, tmp_path):
        _, _, _, env = scratch(tmp_path, runner=LAUNCHER)
        r = run(env, "--check")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "renders from the checkout" in r.stdout
        assert "ARMED" in r.stdout
        assert "NOT ARMED" not in r.stdout

    def test_a_launcher_never_rendered_is_its_own_answer(self, tmp_path):
        """Not the same failure as a copy: it cannot run at all, so no release
        has ever been deployed by it, and the remedy is the same one command."""
        _, _, _, env = scratch(tmp_path, runner=UNRENDERED)
        r = run(env, "--check")
        assert r.returncode == 1
        assert "never rendered" in r.stdout
        assert "cannot run at all" in r.stdout


# ── what "armed" is made of ──────────────────────────────────────────────────

class TestEveryMissingPieceIsNamed:
    @pytest.mark.parametrize("broken, expected", [
        ("snapshot", "snapshot   : missing"),
        ("claims", "claims     : MISSING"),
        ("perf", "perf       : MISSING"),
        ("roster", "cannot measure"),
    ])
    def test_one_missing_piece_is_enough_to_say_not_armed(self, tmp_path, broken, expected):
        kwargs = {"roster": None} if broken == "roster" else {broken: False}
        _, _, _, env = scratch(tmp_path, runner=LAUNCHER, **kwargs)
        r = run(env, "--check")
        assert r.returncode == 1, f"{broken} missing and --check still said armed"
        assert expected in r.stdout, r.stdout
        assert "NOT ARMED" in r.stdout

    def test_the_roster_is_counted_by_the_roles_the_gates_draw_from(self, tmp_path):
        """`roster_supply` in claims_gate.py is the authority on what the roster
        offers: a gate that needs 60 murid cannot be armed by 60 teachers."""
        _, _, _, env = scratch(tmp_path, runner=LAUNCHER)
        r = run(env, "--check")
        assert "3 murid / 1 guru" in r.stdout, r.stdout

    def test_it_says_the_one_command_that_arms_it(self, tmp_path):
        _, _, _, env = scratch(tmp_path, runner=COPY)
        r = run(env, "--check")
        assert "arm-auto-deploy.sh" in r.stdout
        assert "bash " in r.stdout


# ── --check must be free of consequences ─────────────────────────────────────

class TestCheckChangesNothing:
    def test_the_tree_is_byte_identical_afterwards(self, tmp_path):
        repo, bin_dir, ran, env = scratch(tmp_path, runner=COPY)
        before = tree_state(tmp_path)
        r = run(env, "--check")
        assert r.returncode == 1
        assert tree_state(tmp_path) == before, "--check wrote something"
        assert not ran.exists(), "--check ran the installer"

    def test_it_does_not_even_ask_for_a_password(self, tmp_path):
        _, _, _, env = scratch(tmp_path, runner=COPY, roster=None)
        r = run(env, "--check")
        assert "elevating" not in r.stdout


# ── refusing, instead of half-doing it ───────────────────────────────────────

class TestItRefusesRatherThanHalfDoingIt:
    def test_without_a_terminal_it_will_not_start_a_password_prompt(self, tmp_path):
        """A prompt nobody can answer hangs the run — the other way an attempt
        reads as done while nothing happened."""
        _, _, ran, env = scratch(tmp_path, runner=COPY)
        r = run(env)  # stdin is /dev/null
        assert r.returncode == 2
        assert "not a terminal" in r.stderr
        assert not ran.exists(), "the installer ran anyway"

    def test_it_does_not_loop_when_elevation_does_not_take(self, tmp_path):
        _, _, ran, env = scratch(tmp_path, runner=COPY)
        env["SG_ELEVATED"] = "1"
        r = run(env)
        assert r.returncode == 2
        assert "still uid" in r.stderr
        assert not ran.exists()

    def test_a_roster_the_gates_cannot_draw_from_is_refused_first(self, tmp_path):
        """Before the password prompt: a bad roster is worth reporting without
        making anyone retype anything, and it must not be installed."""
        repo, _, ran, env = scratch(tmp_path, runner=COPY, roster=None)
        bad = tmp_path / "bad.json"
        bad.write_text('{"not": "a list"}', encoding="utf-8")
        env["SG_ROSTER_SRC"] = str(bad)
        r = run(env)
        assert r.returncode == 3
        assert "not a non-empty JSON list" in r.stderr
        assert "elevating" not in r.stdout, "it asked for a password before validating"
        assert not (repo / ".freebuff" / "lt_roster.json").exists(), \
            "a roster nothing can draw from was installed anyway"
        assert not ran.exists()


# ── the file itself ──────────────────────────────────────────────────────────

class TestTheFileItself:
    def test_it_is_valid_bash(self):
        r = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr

    def test_it_has_lf_endings(self):
        """The Linux box always gets LF; a developer checkout can silently carry
        CRLF, and a launcher with CRLF fails Gate 0's byte comparison."""
        raw = SCRIPT.read_bytes()
        assert b"\r\n" not in raw, "deploy/arm-auto-deploy.sh has CRLF line endings"

    def test_it_runs_the_installer_and_keeps_its_output(self):
        text = SCRIPT.read_text(encoding="utf-8")
        assert 'bash "$INSTALLER" 2>&1 | tee "$LOG"' in text, \
            "the installer's output is not kept — the run cannot be read back"
        assert "rc=${PIPESTATUS[0]}" in text, "the installer's exit code is thrown away"
