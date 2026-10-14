#!/usr/bin/env python3
"""Run the mutation harnesses — the checks that ask whether the guards *bite*.

A green suite says the tests pass. It does not say they would fail if the rule they
describe were broken, and for this codebase that gap has cost real defects: the
membership guard's clauses are all narrowings whose removal keeps the happy path
working, and the deploy page's cards are read by tests that grep the copy they were
written beside. A mutation harness closes the gap by breaking one moving part at a
time and demanding a red suite; a survivor is a hole in the guard.

Every harness lives in `tests/mutation/` and is tracked, so "mutation-checked" is a
claim any reader can reproduce rather than a sentence in a commit message:

    python deploy/mutation_check.py --list      what harnesses exist, and what they guard
    python deploy/mutation_check.py --check     every anchor still matches (seconds)
    python deploy/mutation_check.py --run NAME  run one harness (minutes)
    python deploy/mutation_check.py --all       run every harness

`--check` is the half the suite runs on every test pass
(`tests/unit/test_mutation_checks.py`): it reads each harness's `GUARDS` and
`MUTATIONS` and asks whether each anchor is still present in the file it names. That
is the failure this tools exists to catch early — an anchor that a later edit moved
would otherwise be discovered twenty minutes into a run that reports "not injected"
for a mutation nobody had seen break.

Exit codes follow the other tools in this directory: 0 pass, 1 a survivor or a stale
anchor, 2 could not measure (a harness that will not load, or no harness by that
name) — never a silent zero for a run that did not happen.
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HARNESS_DIR = ROOT / "tests" / "mutation"
PREFIX = "mutate_"

# The harnesses import the suite (`tests.mutation.harness`) and the suite imports
# the app, so the checkout has to be importable before anything here reads a
# harness. Done at module scope rather than inside the loader: the first reader is
# this file's own `from tests.mutation...`, which runs before any loader would.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def discover(directory: Path | None = None) -> dict:
    """Every harness on disk, by name, sorted — the directory is the registry."""
    directory = directory or HARNESS_DIR
    found = {}
    for path in sorted(directory.glob(f"{PREFIX}*.py")):
        found[path.stem] = path
    return found


def load(path: Path):
    """A harness module, imported without running it as a script.



    Imported rather than parsed because a harness builds its anchors out of string
    constants (`chr(10)` for a newline, a shared template path), and an AST reader
    would have to re-implement that to answer a question the module already answers
    by existing. Nothing here calls `main()`: the module is loaded with a name that
    is not `__main__`, which is what keeps `--check` cheap.
    """
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    name = f"scangrade_mutation_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def describe(path: Path) -> dict:
    """What one harness claims, or why it could not be read."""
    from tests.mutation.harness import describe as describe_anchors

    try:
        module = load(path)
    except Exception as exc:  # a harness that will not load cannot be trusted
        return {"name": path.stem, "error": f"{type(exc).__name__}: {exc}"}
    guards = list(getattr(module, "GUARDS", []) or [])
    mutations = list(getattr(module, "MUTATIONS", []) or [])
    report = describe_anchors(guards, mutations)
    report.update({
        "name": path.stem,
        "path": str(path.relative_to(ROOT)),
        "doc": (module.__doc__ or "").strip().splitlines()[0] if module.__doc__ else "",
        "error": None,
    })
    return report


def describe_all() -> list:
    return [describe(path) for path in discover().values()]


def check() -> int:
    """Read-only: does every harness still hold together? Returns an exit code."""
    reports = describe_all()
    if not reports:
        print("no harnesses found — the directory is the registry, and it is empty")
        return 2
    bad = 0
    for report in reports:
        if report["error"]:
            print(f"  FAIL  {report['name']}: {report['error']}")
            bad += 1
            continue
        stale = [label for label, state, _ in report["mutations"] if state != "ok"]
        missing = report["missing_guards"]
        if missing:
            print(f"  FAIL  {report['name']}: no such guard file: {', '.join(missing)}")
            bad += 1
        elif stale:
            print(f"  FAIL  {report['name']}: {len(stale)} anchor(s) no longer match:")
            for label, state, target in report["mutations"]:
                if state != "ok":
                    print(f"          [{state}] {label}  ({target})")
            bad += 1
        else:
            print(f"  ok    {report['name']}: {report['anchored']}/{report['total']} "
                  f"anchor(s) match, {len(report['guards'])} guard file(s)")
    print(f"mutation check: {len(reports) - bad}/{len(reports)} harness(es) hold together")
    return 1 if bad else 0


def list_harnesses() -> int:
    reports = describe_all()
    if not reports:
        print("no harnesses found under tests/mutation/")
        return 2
    for report in reports:
        if report["error"]:
            print(f"{report['name']} — COULD NOT LOAD: {report['error']}")
            continue
        print(f"{report['name']} — {report['doc']}")
        print(f"    {report['total']} mutation(s) against {len(report['guards'])} "
              f"guard file(s): {', '.join(report['guards'])}")
    return 0


def run_one(name: str) -> int:
    """The harness's own `main()`, in this process: it prints its own score."""
    from tests.mutation.harness import main as harness_main

    path = discover().get(name)
    if path is None:
        print(f"no harness named {name!r} — try --list")
        return 2
    module = load(path)
    guards = list(getattr(module, "GUARDS", []) or [])
    mutations = list(getattr(module, "MUTATIONS", []) or [])
    return harness_main(name, guards, mutations)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="mutation_check.py", description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--list", action="store_true",
                       help="the harnesses that exist and what each one guards")
    group.add_argument("--check", action="store_true",
                       help="every anchor still matches — seconds, no pytest")
    group.add_argument("--run", metavar="NAME", help="run one harness (slow)")
    group.add_argument("--all", action="store_true", help="run every harness (slow)")
    args = parser.parse_args(argv)

    if args.list:
        return list_harnesses()
    if args.run:
        return run_one(args.run)
    if args.all:
        worst = 0
        for name in discover():
            code = run_one(name)
            worst = max(worst, code)
        print(f"\nmutation check: {len(discover())} harness(es), worst result {worst}")
        return worst
    return check()


if __name__ == "__main__":
    sys.exit(main())
