"""The shape every mutation harness has, so one of them is a file and not a ritual.

A mutation check writes down what a guard is *for*: for each entry it breaks exactly
one moving part of a claim and demands a red suite, and a survivor is a hole in the
guard rather than a curiosity. What was missing was that the harnesses lived in
`.freebuff/` — which `.gitignore` excludes — so "mutation-checked" was a claim no
reader of the repository could reproduce, and every new one re-invented the loop,
the interpreter lookup, and the CRLF translation of its own anchors.

So each harness is now three data structures and one call::

    from tests.mutation.harness import main
    GUARDS = ["tests/unit/test_x.py"]
    MUTATIONS = [(label, relative_path, anchor, replacement), ...]
    if __name__ == "__main__":
        raise SystemExit(main("mutate_x", GUARDS, MUTATIONS))

`deploy/mutation_check.py` is the entry point anyone runs — `--list`, `--check`,
`--run NAME` — and it reads `GUARDS` and `MUTATIONS` out of these modules without
executing them as scripts. `tests/unit/test_mutation_checks.py` runs `--check` on
every harness in the suite, so a stale anchor is caught in about a second instead of
after the twenty-minute run it would otherwise cost.

Two properties of the loop are deliberate:

* **the baseline is checked first**, and a red baseline refuses to report: a harness
  whose guards already fail would call every mutation "caught", which is the one
  wrong answer that looks exactly like a perfect score;
* **every file is restored from bytes read before the mutation**, so a harness that
  dies mid-run cannot leave a mutated source behind, and line endings are not
  rewritten by the round trip (the anchor is translated to the file's own style
  instead — a repo file committed LF and checked out CRLF otherwise reads as
  "anchor not found").
"""
from __future__ import annotations

import hashlib
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

__all__ = ["ROOT", "PY", "main", "read", "describe"]


def _find_root() -> Path:
    """The checkout, found by its own marker rather than by a fixed depth.

    A harness under `tests/mutation/` and a file under `.freebuff/` sit at different
    depths, and a hard-coded `parents[n]` is how a harness that moved reports "no
    such file" for every anchor at once.
    """
    here = Path(__file__).resolve()
    for candidate in here.parents:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise RuntimeError(f"no checkout above {here}")


ROOT = _find_root()


def python() -> str:
    """This checkout's interpreter, or a sibling's when run from a worktree."""
    local = ROOT / ".venv" / "Scripts" / "python.exe"
    if local.exists():
        return str(local)
    posix = ROOT / ".venv" / "bin" / "python"
    if posix.exists():
        return str(posix)
    for sibling in ("scanGrade", "main-merge", "school-switch"):
        for rel in ("Scripts/python.exe", "bin/python"):
            candidate = ROOT.parent / sibling / ".venv" / rel
            if candidate.exists():
                return str(candidate)
    return "python"


PY = python()


def read(relative: str) -> bytes:
    return (ROOT / relative).read_bytes()


def translate(anchor: str, text: str) -> str:
    """The anchor in the target file's own line-ending style."""
    return anchor.replace("\n", "\r\n") if "\r\n" in text else anchor


def _copy_for(relative: str) -> Path:
    """Where a killed run's pristine copy of one mutated file lives."""
    digest = hashlib.sha1(relative.encode("utf-8")).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / f"scangrade-mutation-{digest}.orig"


def recover(paths, *, root: Path | None = None) -> list[str]:
    """Put back any file a killed previous run left mutated, and name what moved.

    `finally` restores after every mutation and **does not run when the process is
    killed** - which is not hypothetical: a caller with a timeout killed a harness
    during its first mutation, the mutation stayed in the source, and the guards then
    went green over a rule that could no longer fire. That is the one failure a
    mutation harness cannot report, because every later mutation is then measured
    against a broken baseline. So the pristine bytes go to a scratch copy before the
    first mutation, and the next run puts them back before it reads a single anchor.

    The copy lives in the system temp dir on purpose: a file inside the checkout is a
    dirty tree, and a deploy refuses those.
    """
    root = root or ROOT
    restored = []
    for relative in dict.fromkeys(paths):
        copy = _copy_for(relative)
        if not copy.exists():
            continue
        saved = copy.read_bytes()
        target = root / relative
        if not target.is_file() or target.read_bytes() != saved:
            target.write_bytes(saved)
            restored.append(relative)
        copy.unlink()
    return restored


def _restore_then_die(copies, signum, _frame) -> None:
    for relative, copy in copies:
        if copy.exists():
            (ROOT / relative).write_bytes(copy.read_bytes())
            copy.unlink()
    raise SystemExit(128 + signum)


def describe(guards, mutations, *, root: Path | None = None) -> dict:
    """What a harness claims, and whether each anchor still matches its target.

    Read-only and cheap — no pytest, no writes — because this is what the suite runs
    on every check. The three answers it can give are kept apart: a missing target
    file, an anchor that no longer matches, and a well-formed entry.
    """
    root = root or ROOT
    missing_guards = [g for g in guards if not (root / g).is_file()]
    findings = []
    for entry in mutations:
        label, relative, anchor, replacement = entry[:4]
        target = root / relative
        if not target.is_file():
            findings.append((label, "missing", f"no such file: {relative}"))
            continue
        text = target.read_text(encoding="utf-8")
        if translate(anchor, text) not in text:
            findings.append((label, "stale", relative))
        else:
            findings.append((label, "ok", relative))
    return {
        "guards": list(guards),
        "missing_guards": missing_guards,
        "mutations": findings,
        "anchored": sum(1 for _, state, _ in findings if state == "ok"),
        "total": len(findings),
    }


def _guards_pass(guards) -> bool:
    proc = subprocess.run([PY, "-m", "pytest", *guards, "-q", "-x", "-p",
                           "no:cacheprovider", "--no-header"],
                          cwd=str(ROOT), capture_output=True, text=True)
    return proc.returncode == 0


def main(name: str, guards, mutations, *, guards_pass=None) -> int:
    """Run one harness: baseline first, then every mutation, then the score.

    Returns 0 when every injected defect was caught, 1 on a survivor or an anchor
    that no longer matches, 2 when the baseline is red or could not be measured —
    the same three-way contract the other tools in `deploy/` use.
    """
    restored = recover([entry[1] for entry in mutations])
    if restored:
        print(f"{name}: restored after a killed previous run: {chr(44).join(restored)}")
    check = describe(guards, mutations)
    if check["missing_guards"]:
        print(f"{name}: a guard file this harness names does not exist: "
              f"{', '.join(check['missing_guards'])}")
        return 2
    print(f"{name}: {check['total']} mutation(s), {len(guards)} guard file(s)")

    runner = guards_pass or _guards_pass
    if not runner(guards):
        print(f"{name}: BASELINE IS RED — refusing to report (fix the guard first)")
        return 2

    copies = []
    for relative in dict.fromkeys(entry[1] for entry in mutations):
        copy = _copy_for(relative)
        if not copy.exists():
            copy.write_bytes((ROOT / relative).read_bytes())
        copies.append((relative, copy))
    for _name in ("SIGTERM", "SIGINT", "SIGBREAK"):
        if hasattr(signal, _name):
            try:
                signal.signal(getattr(signal, _name),
                              lambda s, f: _restore_then_die(copies, s, f))
            except (OSError, ValueError):
                pass

    survivors, unmatched, caught = [], [], 0
    for label, relative, anchor, replacement in mutations:
        path = ROOT / relative
        original = path.read_bytes()
        text = original.decode("utf-8")
        probe_old = translate(anchor, text)
        probe_new = translate(replacement, text)
        if probe_old not in text:
            print(f"  [no match] {label}  ({relative})")
            unmatched.append(label)
            continue
        path.write_bytes(text.replace(probe_old, probe_new, 1).encode("utf-8"))
        try:
            detected = not runner(guards)
        finally:
            path.write_bytes(original)
        if detected:
            caught += 1
        else:
            survivors.append(label)
        print("  {} {}  ({})".format("caught " if detected else "SURVIVED", label,
                                     relative))
        sys.stdout.flush()

    for _relative, copy in copies:
        copy.unlink(missing_ok=True)
    print(f"\n{name}: {caught}/{len(mutations)} injected defects caught")
    for label in survivors:
        print("  survivor:", label, "— the guard still passed")
    for label in unmatched:
        print("  not injected:", label, "— the anchor no longer matches the source")
    return 1 if (survivors or unmatched) else 0
