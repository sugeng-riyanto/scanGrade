"""Mutation check for `deploy/port_change.py` — the refusals a port depends on.

Sixteen entries, each breaking one clause the tool exists for: the base
comparison, the backup, the exit code a finding reaches, the discovery of a
harness, and every reading of a number a ported sentence carries. Each is
demanded to turn the guards red, because a refusal that still writes is the one
defect that looks exactly like a working tool.

The file is data first (`GUARDS` and `MUTATIONS`, read by
`deploy/mutation_check.py`), and a runnable harness second, so the same file
serves a checkout that has the registry and one that predates it.

Run:  python deploy/mutation_check.py --run mutate_port_change   (~6 minutes)
      python deploy/mutation_check.py --check                    (anchors only)
"""
from __future__ import annotations

import signal
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

GUARDS = ["tests/unit/test_port_change.py"]

MUTATIONS = [
    ('the base comparison folds CRLF',
     'deploy/port_change.py',
     '            if current != base_bytes:',
     '            if current.replace(b"\\r\\n", b"\\n") != base_bytes.replace(b"\\r\\n", b"\\n"):'),
    ('a drifted target is written anyway',
     'deploy/port_change.py',
     '                    first_difference(current, base_bytes))\n                continue',
     '                    first_difference(current, base_bytes))\n                pass'),
    ('the backup copy is empty',
     'deploy/port_change.py',
     '        destination.write_bytes(before)',
     '        destination.write_bytes(b"")'),
    ('a red suite is reported as a pass',
     'deploy/port_change.py',
     '    elif code == 1:\n        state, summary = "fail", last_line(text)',
     '    elif code == 1:\n        state, summary = "pass", last_line(text)'),
    ('a suite that could not run is reported as a pass',
     'deploy/port_change.py',
     '        state, summary = "unmeasured", f"pytest exited {code}: {last_line(text)}"',
     '        state, summary = "pass", f"pytest exited {code}: {last_line(text)}"'),
    ('a survivor is reported as caught',
     'deploy/port_change.py',
     '    elif code == 1:\n        state = "survivor"',
     '    elif code == 1:\n        state = "caught"'),
    ('a refusal does not reach the exit code',
     'deploy/port_change.py',
     '    if any(target.state in ("refused", "failed") for target in targets):\n        return UNMEASURED',
     '    if any(target.state in ("refused", "failed") for target in targets):\n        return PASS'),
    ('restore overwrites a file changed after the port',
     'deploy/port_change.py',
     '        if current != entry.get("after_sha256") and not args.force:',
     '        if False and not args.force:'),
    ('a missing base is inferred',
     'deploy/port_change.py',
     '        if missing:\n            raise Usage("porting needs " + ", ".join(missing) +',
     '        if False:\n            raise Usage("porting needs " + ", ".join(missing) +'),
    ('an already-identical target is rewritten',
     'deploy/port_change.py',
     '        if current == new:',
     '        if False:'),
    ('a claim that does not hold is written anyway',
     'deploy/port_change.py',
     '                    if target.claims["state"] == "fail":',
     '                    if False:'),
    ('the claims are read from the whole file, not the added lines',
     'deploy/port_change.py',
     '            claims = claims_in(added_lines(current, new))',
     '            claims = claims_in(new.decode("utf-8", "replace").splitlines())'),
    ('a claim that could not be measured is written anyway',
     'deploy/port_change.py',
     '                    if target.claims["state"] == "unmeasured":',
     '                    if False:'),
    ('a tool is read as the subject of a test count',
     'deploy/port_change.py',
     '    r"(?<![\\w./-])((?:tests/[\\w./-]+|test_[\\w]+)\\.py)")',
     '    r"(?<![\\w./-])((?:tests|deploy)/[\\w./-]+\\.py|test_[\\w]+\\.py)")'),
    ('a count is read against a path that follows it',
     'deploy/port_change.py',
     '            subject = _nearest(tests, at, follows=True)',
     '            subject = _nearest(tests, at)'),
    ('a count is bound to the first path on the line rather than the nearest',
     'deploy/port_change.py',
     '    best = min(subjects, key=distance)',
     '    best = subjects[0]'),
]


RECOVERY = Path(tempfile.gettempdir()) / "scangrade-port-change.orig"




TARGET = "deploy/port_change.py"


def _target() -> Path:
    """The one file this harness mutates."""
    return ROOT / TARGET


def recover() -> None:
    """Undo a mutation a killed run could not restore.

    `finally` restores after every mutation, but it does not run when the process is
    killed — and this harness is slow enough (a whole suite per mutation) that a caller
    with a timeout will kill it. That is not hypothetical: a 300-second timeout did,
    and left `if False:` in the claim refusal, after which the suite went green over a
    check that could never fire. So the pristine bytes are written to a scratch copy
    before the first mutation and put back before the next run reads anything.
    """
    if not RECOVERY.exists():
        return
    saved = RECOVERY.read_bytes()
    if _target().read_bytes() != saved:
        _target().write_bytes(saved)
        print(f"restored {_target()} from {RECOVERY} "
              "(a previous run was killed mid-mutation)")
    RECOVERY.unlink()


def _restore_and_die(signum, _frame):
    recover()
    raise SystemExit(128 + signum)


def _local_main() -> int:
    """The registry's loop, for a checkout that predates `tests/mutation/harness.py`."""
    recover()
    original = _target().read_bytes()
    text = original.decode("utf-8")
    for label, relative, old, _new in MUTATIONS:
        probe = ROOT / relative
        if old not in probe.read_text(encoding="utf-8"):
            print(f"  [no match] {label}  ({relative})")
            return 2
    if subprocess.run([sys.executable, "-m", "pytest", *GUARDS, "-q", "-p",
                       "no:cacheprovider", "--no-header"], cwd=str(ROOT)).returncode != 0:
        print("mutate_port_change: BASELINE IS RED — refusing to report")
        return 2
    caught, survivors = 0, []
    RECOVERY.write_bytes(original)
    try:
        for label, relative, old, new in MUTATIONS:
            path = ROOT / relative
            try:
                path.write_bytes(text.replace(old, new, 1).encode("utf-8"))
                detected = subprocess.run(
                    [sys.executable, "-m", "pytest", *GUARDS, "-q", "-p",
                     "no:cacheprovider", "--no-header"],
                    cwd=str(ROOT), capture_output=True).returncode != 0
            finally:
                path.write_bytes(original)
            if detected:
                caught += 1
            else:
                survivors.append(label)
            print(f"  {'caught ' if detected else 'SURVIVED'} {label}  ({relative})")
            sys.stdout.flush()
    finally:
        RECOVERY.unlink(missing_ok=True)
    print(f"\nmutate_port_change: {caught}/{len(MUTATIONS)} injected defects caught")
    for label in survivors:
        print("  survivor:", label, "— the guard still passed")
    return 0 if not survivors else 1


if __name__ == "__main__":
    for _name in ("SIGTERM", "SIGINT", "SIGBREAK"):
        if hasattr(signal, _name):
            try:
                signal.signal(getattr(signal, _name), _restore_and_die)
            except (OSError, ValueError):
                pass
    try:
        from tests.mutation.harness import main as harness_main
    except ImportError:
        raise SystemExit(_local_main())
    raise SystemExit(harness_main("mutate_port_change", GUARDS, MUTATIONS))
