"""Does the armed SEB door gate actually refuse *and roll back* a bad release?

`deploy/arm-auto-deploy.sh` ends an armed box with "Prove it bites with the drill in
docs/AUTO_DEPLOY.md" — and that document contains no drill (0 occurrences of the
word). So this is the drill the repository's own technique allows, applied to Gate
4c: the **shipped** block and the **shipped** refusal path are sliced out of
`deploy/scangrade-deploy.sh` and *executed* against a scratch two-commit box, with a
stand-in `deploy/seb_door_gate.py` that reports a finding — the only way to make a bad
release on purpose in a checkout.

Three runs, and the two that do *not* roll back are the ones that make the first
mean something:

  armed,   gate exit 3 -> refused, quarantined, checkout back on the serving commit
  unarmed, gate exit 3 -> the finding is logged and the release is kept
  armed,   gate exit 2 -> "could not measure" keeps the release too

    python deploy/drill_seb_armed_rollback.py

It lives in `deploy/` and not in the gitignored `.freebuff/` scratch directory, where
it was written: a drill the runbook tells an operator to run is a path that has to
exist in a clean checkout, and `tests/unit/test_vps_runbook.py` refuses a page that
names one which does not (the runbook section 6 named `.freebuff/` until this file
moved, and the guard was right to refuse it — on the box, and on every laptop but the
one that wrote it, that command was unfindable).
"""
from __future__ import annotations

import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNNER = ROOT / "deploy" / "scangrade-deploy.sh"

sys.path.insert(0, str(ROOT))
from tests.unit.git_env import git_env  # noqa: E402  (the suite's own scrub)

#: The module the SEB block is measured on. Sliced by its own headers — the same way
#: `tests/unit/test_seb_door_gate.py` reads it — so the drill cannot pass by running
#: a copy of the block that drifted from the shipped one.
SEB_HEADER = "# \u2500\u2500 Gate 4c: is the SEB door *enforced* on the release being served?"
SEB_END = "# \u2500\u2500 Gate 5: do the numbers on the landing page still describe this box?"
FAILURE_HEADER = "# \u2500\u2500 Failure: put the previous release back \u2500"
QUARANTINE_START = "# quarantine-logic:start"
QUARANTINE_END = "# quarantine-logic:end"

HARNESS = r'''REPO="$1"; BEFORE="$2"; AFTER="$3"; AFTER_FULL="$4"; ENFORCE="$5"; RC="$6"

STATE_DIR="$REPO/state"
QUARANTINE_FILE="$STATE_DIR/quarantined"
REFUSALS_DIR="$STATE_DIR/refusals"
RELEASE_FILE="$STATE_DIR/release"
RELEASE_REQUEST="$STATE_DIR/requests/release"
REBASELINE_REQUEST="$STATE_DIR/requests/rebaseline"
REQUEST_DIR="$STATE_DIR/requests"
BRANCH="main"
SERVICE="scangrade"
SNAPSHOT=""
SMOKE_CONF="$REPO/etc/scangrade-smoke.conf"
SMOKE_BASE_URL="http://127.0.0.1:1"
SMOKE_MURID="siswa@drill.test:rahasia"
SMOKE_ENFORCE="true"
SEB_ENFORCE="$ENFORCE"
SG_DRILL_RC="$RC"
export SG_DRILL_RC SEB_ENFORCE
HEALTHY=1
FAIL_REASON=""
FAIL_DETAIL=""
log() { printf '    %s\n' "$*" >&2; }
as_owner() { "$@"; }
systemctl() { return 0; }
journalctl() { :; }
reload_app() { :; }
reload_worker() { return 0; }
probe_app() { return 0; }

@@QUARANTINE@@

@@SEB@@

if [ "$HEALTHY" = "1" ]; then
  echo "KEPT: $AFTER is still the release being served"
  exit 0
fi

@@TAIL@@
'''


def slice_runner() -> tuple[str, str, str]:
    text = RUNNER.read_text(encoding="utf-8")
    quarantine = text[text.index(QUARANTINE_START):
                      text.index(QUARANTINE_END) + len(QUARANTINE_END)]
    seb = text[text.index(SEB_HEADER):text.index(SEB_END)]
    tail = text[text.index(FAILURE_HEADER):]
    return quarantine, seb, tail


STAND_IN_GATE = '''\
import os, sys
rc = int(os.environ.get("SG_DRILL_RC", "3"))
if rc == 3:
    print("seb door: FAILED \u2014 a plain browser opened a gated paper (drill stand-in)")
    print("seb door: 1 finding")
else:
    print("seb door: NOT MEASURED \u2014 no browser to drive (drill stand-in)")
    print("seb door: the client half could not be taken, so the page's script was not")
sys.exit(rc)
'''


def scratch_box(root: pathlib.Path) -> tuple[pathlib.Path, str, str]:
    """Two commits: the one serving (`before`) and the bad release (`after`)."""
    # (the scratch box is rebuilt per case, so no case can inherit another's state)
    repo = root / "box"
    (repo / "deploy").mkdir(parents=True)
    (repo / "etc").mkdir(parents=True)
    (repo / ".venv" / "bin").mkdir(parents=True)

    def git(*args, check=True):
        return subprocess.run(["git", *args], cwd=repo, check=check, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", env=git_env())

    git("init", "-q")
    (repo / "app.py").write_text("release = 'serving'\n", encoding="utf-8")
    git("add", ".")
    git("-c", "user.email=drill@test", "-c", "user.name=drill", "-c", "commit.gpgsign=false",
        "commit", "-q", "-m", "the release that was serving")
    before = git("rev-parse", "HEAD").stdout.strip()

    (repo / "app.py").write_text("release = 'candidate'\n", encoding="utf-8")
    git("add", ".")
    git("-c", "user.email=drill@test", "-c", "user.name=drill", "-c", "commit.gpgsign=false",
        "commit", "-q", "-m", "the bad release")
    after = git("rev-parse", "HEAD").stdout.strip()

    # The stand-in gate is *untracked*: it is the thing that makes the release bad,
    # and a rollback must not be able to delete it.
    (repo / "deploy" / "seb_door_gate.py").write_text(STAND_IN_GATE, encoding="utf-8")
    shim = repo / ".venv" / "bin" / "python"
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    shim.chmod(0o755)
    (repo / "etc" / "scangrade-smoke.conf").write_text(
        'SMOKE_BASE_URL="http://127.0.0.1:1"\nSMOKE_ENFORCE="true"\n', encoding="utf-8")
    (repo / "state" / "requests").mkdir(parents=True, exist_ok=True)
    return repo, before, after


def run_case(root: pathlib.Path, enforce: str, rc: int, mutate: str = "") -> dict:
    quarantine, seb, tail = slice_runner()
    if mutate:
        # The drill's own bite check: a block that cannot refuse must show up as a
        # release kept, or the three runs above are describing something else.
        assert mutate in seb, f"the mutation {mutate!r} is not in the shipped block"
        seb = seb.replace(mutate, "", 1)
    repo, before, after = scratch_box(root)
    script = root / "harness.sh"
    script.write_text(HARNESS.replace("@@QUARANTINE@@", quarantine)
                             .replace("@@SEB@@", seb)
                             .replace("@@TAIL@@", tail), encoding="utf-8")
    proc = subprocess.run(["bash", str(script), str(repo), before, after, after, enforce, str(rc)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env={**git_env(), "PYTHONIOENCODING": "utf-8"})
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
                          text=True, env=git_env()).stdout.strip()
    record = repo / "state" / "quarantined"
    return {
        "exit": proc.returncode,
        "head": head,
        "before": before,
        "after": after,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "record": record.read_text(encoding="utf-8") if record.exists() else "",
        "record_path": record,
    }


def check(label: str, got: dict, *, exit_code: int, rolled_back: bool,
          journal: tuple[str, ...] = (), record: tuple[str, ...] = ()) -> bool:
    ok = True
    if got["exit"] != exit_code:
        ok = False
        print(f"  FAIL  {label}: exit {got['exit']}, expected {exit_code}")
    moved = got["head"] == got["before"]
    if moved != rolled_back:
        ok = False
        print(f"  FAIL  {label}: checkout is {'back on' if moved else 'still on'} "
              f"{got['head'][:7]}, expected "
              f"{'a rollback' if rolled_back else 'the release kept'}")
    for needle in journal:
        if needle not in got["stderr"]:
            ok = False
            print(f"  FAIL  {label}: the journal never says {needle!r}")
    for needle in record:
        if needle not in got["record"]:
            ok = False
            print(f"  FAIL  {label}: the quarantine record never says {needle!r}")
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}: exit {got['exit']}, HEAD "
          f"{got['head'][:7]} ({'rolled back' if moved else 'untouched'}), "
          f"record {'written' if got['record'] else 'absent'}")
    return ok


def force_rmtree(path: pathlib.Path) -> None:
    """Remove a scratch box, read-only files included.

    `git reset --hard` leaves `.git/objects/pack` read-only, and on Windows
    `shutil.rmtree` cannot unlink those — so the first version of this drill left
    its two boxes in TEMP. Clearing the attribute and retrying is the whole fix.
    """
    def on_error(func, target, _exc):
        try:
            pathlib.Path(target).chmod(stat.S_IWRITE)
            func(target)
        except OSError:
            pass

    shutil.rmtree(path, onerror=on_error)
    if path.exists():  # a second pass, because one attribute clears one file
        shutil.rmtree(path, onerror=on_error)


def main() -> int:
    print("the drill the arm script promises, applied to Gate 4c")
    print(f"  runner          : {RUNNER.relative_to(ROOT)}")
    print("  block           : the shipped Gate 4c + the shipped refusal path, executed")
    print("  the bad release : a stand-in deploy/seb_door_gate.py reporting a finding")
    print()
    root = pathlib.Path(tempfile.mkdtemp(prefix="sg-seb-drill-"))
    try:
        armed = run_case(root / "armed", "true", 3)
        ok = check("armed + a finding", armed, exit_code=10, rolled_back=True,
                   journal=("did not pass verification", "QUARANTINED",
                            "seb door gate (the SEB toggle is not enforced",
                            "ROLLED BACK to"),
                   record=("seb door gate (the SEB toggle is not enforced on the served release)",
                           "a plain browser opened a gated paper"))
        unarmed = run_case(root / "unarmed", "false", 3)
        ok &= check("unarmed + a finding", unarmed, exit_code=0, rolled_back=False,
                    journal=("SEB_ENFORCE is not 'true' — keeping the release",))
        blind = run_case(root / "blind", "true", 2)
        ok &= check("armed + could not measure", blind, exit_code=0, rolled_back=False,
                    journal=("could not measure (exit 2)",))
        # Mutating the *shipped block* it just ran: with the refusal removed, the same
        # stand-in finding has to leave the release alone. Without this, a drill that
        # rolled back for some other reason would read as proof.
        bitten = run_case(root / "bitten", "true", 3, mutate="        HEALTHY=0\n")
        ok &= check("armed + a finding, with the block's refusal deleted", bitten,
                    exit_code=0, rolled_back=False)

        print()
        print("  the armed run's journal, as the box would write it:")
        for line in armed["stderr"].splitlines():
            if line.strip():
                print(f"    {line}")
        print()
        print("  the quarantine record it left (the status page reads this):")
        for line in armed["record"].splitlines():
            print(f"    {line}")
        print()
        print("  the unarmed run's verdict line:")
        for line in unarmed["stderr"].splitlines():
            if "SEB_ENFORCE" in line or "seb door" in line or "KEPT" in line:
                print(f"    {line}")
        print(f"    {unarmed['stdout'].strip()}")
        print()
        print("the drill says: the gate refuses the release and the box puts the "
              "previous one back" if ok else "the drill found a defect")
        return 0 if ok else 1
    finally:
        force_rmtree(root)
        print(f"  scratch boxes removed: {not root.exists()} ({root})")


if __name__ == "__main__":
    sys.exit(main())
