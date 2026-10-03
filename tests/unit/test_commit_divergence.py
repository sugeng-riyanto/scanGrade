"""How far off a process is, and what an operator should do about it.

A served/worker/process commit refusal today names the two shas and stops there:
"the worker reports bbbbbbb". An operator reading the quarantine card still has to
answer the only question that decides their next move — *did the process merely miss
the reload, or is the box itself on the wrong history?* Those are two different
remedies: **restart the unit** (it holds an older release that is still on the merged
commit's line), or **re-baseline the box** (it holds code the merged commit never
had, so the checkout is wrong).

`deploy/commit_divergence.py` answers it from the commits themselves, and each gate
appends the sentence to its refusal so the quarantine record carries the number and
the action instead of the reader working it out.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def div():
    return _load("commit_divergence", DEPLOY / "commit_divergence.py")


MERGED = "a" * 40
BEHIND = "b" * 40
AHEAD = "c" * 40


def _runner(*, ancestor_pairs, counts):
    """A fake git: `merge-base --is-ancestor a b` is true for the pairs given, and
    `rev-list --count x..y` returns the mapped number."""
    def run(args):
        if args[:2] == ["merge-base", "--is-ancestor"]:
            a, b = args[2], args[3]
            return (0 if (a, b) in ancestor_pairs else 1), ""
        if args[0] == "rev-list":
            spec = args[-1]
            return 0, str(counts.get(spec, 0))
        return 128, "unknown"
    return run


def test_the_same_commit_is_same(div):
    assert div.of(".", MERGED, MERGED, run=_runner(ancestor_pairs=set(), counts={}))["shape"] == "same"


def test_a_served_commit_on_the_merged_line_is_behind(div):
    info = div.of(".", BEHIND, MERGED,
                  run=_runner(ancestor_pairs={(BEHIND, MERGED)},
                              counts={BEHIND + ".." + MERGED: 4}))
    assert info["shape"] == "behind"
    assert info["distance"] == 4


def test_a_served_commit_the_merge_never_had_is_diverged(div):
    info = div.of(".", AHEAD, MERGED, run=_runner(ancestor_pairs=set(), counts={}))
    assert info["shape"] == "diverged"


def test_a_commit_ahead_of_the_merge_is_ahead(div):
    info = div.of(".", AHEAD, MERGED,
                  run=_runner(ancestor_pairs={(MERGED, AHEAD)},
                              counts={MERGED + ".." + AHEAD: 2}))
    assert info["shape"] == "ahead"
    assert info["distance"] == 2


def test_git_that_cannot_answer_is_unknown(div):
    info = div.of(".", BEHIND, MERGED, run=lambda args: (128, "boom"))
    assert info["shape"] == "unknown"
    assert info["distance"] is None


# ── the sentence an operator reads ───────────────────────────────────────────

def test_behind_says_the_number_and_to_restart_the_unit(div):
    run = _runner(ancestor_pairs={(BEHIND, MERGED)}, counts={BEHIND + ".." + MERGED: 3})
    text = div.describe(".", BEHIND, MERGED, unit="scangrade-celery", run=run)
    assert "3" in text and "behind" in text
    assert "restart scangrade-celery" in text
    assert BEHIND[:7] in text and MERGED[:7] in text


def test_diverged_says_to_rebaseline_the_box(div):
    text = div.describe(".", AHEAD, MERGED, unit="scangrade-celery",
                        run=_runner(ancestor_pairs=set(), counts={}))
    assert "re-baseline" in text
    assert AHEAD[:7] in text and MERGED[:7] in text


def test_ahead_says_to_rebaseline_the_box(div):
    run = _runner(ancestor_pairs={(MERGED, AHEAD)}, counts={MERGED + ".." + AHEAD: 5})
    text = div.describe(".", AHEAD, MERGED, unit="scangrade", run=run)
    assert "re-baseline" in text and "ahead" in text


def test_unknown_says_it_could_not_measure(div):
    text = div.describe(".", BEHIND, MERGED, unit="scangrade",
                        run=lambda args: (128, "boom"))
    assert "could not" in text.lower()


def test_annotate_appends_without_losing_the_original(div):
    run = _runner(ancestor_pairs={(BEHIND, MERGED)}, counts={BEHIND + ".." + MERGED: 1})
    out = div.annotate("the worker reports bbbbbbb", ".", BEHIND, MERGED,
                       unit="scangrade-celery", run=run)
    assert out.startswith("the worker reports bbbbbbb")
    assert "restart scangrade-celery" in out


# ── the wiring: every gate that can quarantine names it ──────────────────────

def test_the_worker_gate_appends_the_divergence():
    gate = (DEPLOY / "worker_commit_gate.py").read_text(encoding="utf-8")
    assert "commit_divergence" in gate and "annotate" in gate, (
        "the worker gate is the one that quarantines a stale worker; its detail must "
        "name the distance and the remedy")
    assert '"--unit"' in gate


def test_the_served_gate_appends_the_divergence():
    gate = (DEPLOY / "served_commit_gate.py").read_text(encoding="utf-8")
    assert "commit_divergence" in gate and "annotate" in gate
    assert '"--unit"' in gate


def test_the_process_gate_appends_the_divergence():
    gate = (DEPLOY / "process_commit_gate.py").read_text(encoding="utf-8")
    assert "commit_divergence" in gate and "annotate" in gate


def test_the_runner_tells_each_gate_which_unit_it_is_asking():
    script = (DEPLOY / "scangrade-deploy.sh").read_text(encoding="utf-8-sig")
    assert "--unit \"$SERVICE\"" in script and "--unit \"$WORKER_UNIT\"" in script, (
        "the record names the unit, so the runner has to tell the gate which one")
