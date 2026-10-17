"""Guards for the harness in `tests/mutation/mutate_port_change.py`, not for the tool.

These live in their own file because the harness runs `test_port_change.py` once per
mutation: an anchor assertion in *that* file would fail under every injection — the
mutation necessarily moves the line it anchors on — and would turn all sixteen into
false catches, which is precisely the failure mode the harness exists to expose.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "tests" / "mutation" / "mutate_port_change.py"


def load_harness():
    spec = importlib.util.spec_from_file_location("mutate_port_change", HARNESS)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_every_anchor_in_the_mutation_harness_still_bites_the_shipped_tool():
    """A harness whose anchor moved reports `stale` only when somebody runs it."""
    harness = load_harness()
    text = (ROOT / harness.TARGET).read_text(encoding="utf-8")

    stale = [label for label, _path, old, _new in harness.MUTATIONS if old not in text]

    assert stale == [], f"the guard moved away from its mutation: {stale}"
    assert len(harness.MUTATIONS) >= 16, "a mutation was dropped instead of repaired"


def test_a_run_killed_mid_mutation_is_repaired_by_the_next(tmp_path, monkeypatch, capsys):
    """`finally` cannot run under SIGKILL, so the copy beside the tool is the repair.

    Observed, not assumed: killing this harness during its first mutation (the whole
    suite per mutation makes it long enough for a caller's timeout to do it) left
    `if False:` in the refusal, and the suite then passed over it. The next run has to
    put the tool back before it reads anything, or a stranded mutation becomes the
    baseline every later score is measured against.
    """
    harness = load_harness()
    (tmp_path / "deploy").mkdir()
    target = tmp_path / harness.TARGET
    target.write_text("mutated\n", encoding="utf-8")
    recovery = tmp_path / "scangrade-port-change.orig"
    recovery.write_text("pristine\n", encoding="utf-8")
    monkeypatch.setattr(harness, "ROOT", tmp_path)
    monkeypatch.setattr(harness, "RECOVERY", recovery)

    harness.recover()

    assert target.read_text(encoding="utf-8") == "pristine\n", "the stranded mutation stayed"
    assert not recovery.exists(), "the copy outlived the repair"
    assert "restored" in capsys.readouterr().out

    harness.recover()
    assert target.read_text(encoding="utf-8") == "pristine\n"
