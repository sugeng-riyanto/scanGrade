"""One splitter for every multi-feature commit, driven by markers, not by a script.

`deploy/stage_feature.py` replaces the per-commit `.freebuff/stage_<feature>.py`
wrappers: a spec names a feature and, per shared file, the rule that picks its
lines out of a diff that also carries other features'. Four rules, one per shape
the shared files have — whole hunks, added lines inside a merged hunk (recounted),
a rebuilt block, and a whole path — and no bespoke Python per commit.

What this file pins, in the order a run meets it:

1. **the split** — a diff becomes hunks, a hunk its changed and added lines;
2. **selection** — `hunks` keeps only hunks carrying a marker, `lines` keeps only
   marked added lines, `block` rebuilds `HEAD` plus the worktree region;
3. **refusal** — a marker that matches nothing, or that co-occurs with another
   feature's, stops the run instead of staging an empty patch (the failure the
   wrappers could not report), and `--allow-empty` cannot swallow a conflict;
4. **end to end, in a real checkout** — staging feature A leaves feature B in the
   working tree, and never touches the tree, only the index.
"""
from __future__ import annotations

import difflib
import importlib.util
import json
import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy" / "stage_feature.py"
GIT = shutil.which("git")

pytestmark = pytest.mark.skipif(GIT is None, reason="needs git to split a commit")


def _load():
    spec = importlib.util.spec_from_file_location("stage_feature", TOOL)
    module = importlib.util.module_from_spec(spec)
    sys.modules["stage_feature"] = module
    spec.loader.exec_module(module)
    return module


stage = _load()


# ── diffs, built rather than described ───────────────────────────────────────

def unified(head: list[str], work: list[str], path: str = "notes.md") -> str:
    return "".join(difflib.unified_diff(
        head, work, fromfile=f"a/{path}", tofile=f"b/{path}", n=3))


# ── a real checkout, one shared file ─────────────────────────────────────────

def _git(repo: pathlib.Path, *args: str):
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@example.com",
         "-c", "user.name=t", *args],
        capture_output=True, check=False)


def _repo(tmp_path: pathlib.Path, shared: str) -> pathlib.Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], capture_output=True, check=False)
    _git(repo, "config", "core.autocrlf", "false")
    (repo / "NOTES.md").write_text(shared, encoding="utf-8", newline="\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _write(repo: pathlib.Path, text: str) -> None:
    (repo / "NOTES.md").write_text(text, encoding="utf-8", newline="\n")


def _spec(tmp_path: pathlib.Path, feature: str, rule: dict) -> str:
    path = tmp_path / "spec.json"
    path.write_text(json.dumps({"features": {feature: [rule]}}), encoding="utf-8")
    return str(path)


def _cached(repo: pathlib.Path) -> str:
    return _git(repo, "diff", "--cached").stdout.decode("utf-8", "replace")


def _unstaged(repo: pathlib.Path) -> str:
    return _git(repo, "diff").stdout.decode("utf-8", "replace")


# ── 1. the split ─────────────────────────────────────────────────────────────

class TestTheSplit:
    def test_no_diff_has_no_hunks(self):
        assert stage.split("") == ("", [])

    def test_the_header_is_kept_and_hunks_are_separate(self):
        head = [f"line {i}\n" for i in range(30)]
        work = list(head)
        work[1] = "line 1 A\n"
        work[27] = "line 27 B\n"
        header, hunks = stage.split(unified(head, work))
        assert header.startswith("--- a/notes.md")
        assert len(hunks) == 2
        assert all(h.startswith("@@") for h in hunks)

    def test_changed_lines_are_the_added_and_removed_ones(self):
        head = ["a\n", "b\n"]
        hunk = stage.split(unified(head, ["a\n", "B\n"]))[1][0]
        changed = stage.changed_lines(hunk)
        assert "-b" in changed and "+B" in changed
        assert not any(l.startswith(("+++", "---")) for l in changed)
        assert stage.added_lines(hunk) == ["+B"]


# ── 2. selecting each shape ──────────────────────────────────────────────────

class TestSelectHunks:
    HEAD = [f"line {i}\n" for i in range(30)]

    def _diff(self):
        work = list(self.HEAD)
        work[1] = "line 1 A-fix\n"
        work[27] = "line 27 B-fix\n"
        return unified(self.HEAD, work)

    def test_only_the_marked_hunk_is_taken(self):
        patch, matched = stage.select_hunks(self._diff(), ["A-fix"])
        assert matched == 1
        assert "A-fix" in patch
        assert "B-fix" not in patch

    def test_a_hunk_that_matches_another_feature_is_a_conflict(self):
        with pytest.raises(stage.StageConflict):
            stage.select_hunks(self._diff(), ["A-fix"], exclude=["A-fix"])

    def test_nothing_matching_is_refused_not_ignored(self):
        with pytest.raises(stage.StageError):
            stage.select_hunks(self._diff(), ["no-such-marker"])


class TestSelectLines:
    def _diff(self):
        head = ["- **old entry**\n", "\n", "body\n"]
        work = ["- **A entry**\n", "- **B entry**\n", "- **old entry**\n", "\n", "body\n"]
        return unified(head, work)

    def test_only_marked_added_lines_survive(self):
        patch, matched = stage.select_lines(self._diff(), ["A entry"])
        assert matched == 1
        assert "+- **A entry**" in patch
        assert "+- **B entry**" not in patch

    def test_the_context_and_removals_are_kept(self):
        patch, _ = stage.select_lines(self._diff(), ["A entry"])
        assert " - **old entry**" in patch or "- **old entry**" in patch
        assert "+- **old entry**" not in patch  # still a removal, not an addition

    def test_nothing_matching_is_refused(self):
        with pytest.raises(stage.StageError):
            stage.select_lines(self._diff(), ["no-such-marker"])


# ── 3. refusal and validation ────────────────────────────────────────────────

class TestSpecAndRefusal:
    def test_a_missing_spec_is_refused(self, tmp_path, capsys):
        rc = stage.main(["x", "--spec", str(tmp_path / "nope.json")])
        assert rc == 3 and "no spec" in capsys.readouterr().err

    def test_an_unknown_kind_is_refused(self, tmp_path, capsys):
        spec = _spec(tmp_path, "x", {"path": "NOTES.md", "kind": "magic"})
        assert stage.main(["x", "--spec", spec]) == 3

    def test_hunks_without_markers_is_refused(self, tmp_path):
        spec = _spec(tmp_path, "x", {"path": "NOTES.md", "kind": "hunks"})
        assert stage.main(["x", "--spec", spec]) == 3

    def test_block_without_anchors_is_refused(self, tmp_path):
        spec = _spec(tmp_path, "x", {"path": "NOTES.md", "kind": "block",
                                     "start": "a"})
        assert stage.main(["x", "--spec", spec]) == 3

    def test_unknown_feature_is_refused(self, tmp_path, capsys):
        spec = _spec(tmp_path, "x", {"path": "NOTES.md", "kind": "whole"})
        assert stage.main(["nope", "--spec", spec]) == 3
        assert "no feature" in capsys.readouterr().err

    def test_list_names_the_features(self, tmp_path, capsys):
        spec = _spec(tmp_path, "alpha", {"path": "NOTES.md", "kind": "whole"})
        assert stage.main(["--list", "--spec", spec]) == 0
        assert "alpha" in capsys.readouterr().out

    def test_allow_empty_does_not_swallow_a_conflict(self, tmp_path):
        repo = _repo(tmp_path, "".join(f"line {i}\n" for i in range(30)))
        work = (repo / "NOTES.md").read_text()
        _write(repo, work.replace("line 1\n", "line 1 A-fix\n"))
        # The marker and the exclude are the same word, so the hunk conflicts.
        spec = _spec(tmp_path, "a", {"path": "NOTES.md", "kind": "hunks",
                                     "markers": ["A-fix"], "exclude": ["A-fix"]})
        assert stage.main(["a", "--spec", spec, "--repo", str(repo),
                           "--allow-empty"]) == 3


# ── 4. end to end, in a real checkout ────────────────────────────────────────

class TestInARealCheckout:
    HEAD = "".join(f"line {i}\n" for i in range(30))

    def _two_hunks(self, tmp_path):
        repo = _repo(tmp_path, self.HEAD)
        work = list(self.HEAD.splitlines(keepends=True))
        work[1] = "line 1 A-fix\n"
        work[27] = "line 27 B-fix\n"
        _write(repo, "".join(work))
        return repo

    def test_hunks_stage_a_and_leave_b_in_the_tree(self, tmp_path):
        repo = self._two_hunks(tmp_path)
        spec = _spec(tmp_path, "a", {"path": "NOTES.md", "kind": "hunks",
                                     "markers": ["A-fix"]})
        assert stage.main(["a", "--spec", spec, "--repo", str(repo)]) == 0
        assert "A-fix" in _cached(repo)
        assert "B-fix" not in _cached(repo)
        assert "B-fix" in _unstaged(repo), "B was staged with A, or lost"
        assert "A-fix" not in _unstaged(repo), "A was not staged"
        # The working tree still holds both — only the index moved.
        assert "A-fix" in (repo / "NOTES.md").read_text()
        assert "B-fix" in (repo / "NOTES.md").read_text()

    def test_appended_entries_split_by_line(self, tmp_path):
        repo = _repo(tmp_path, "- **old entry**\n\nbody\n")
        _write(repo, "- **A entry**\n- **B entry**\n- **old entry**\n\nbody\n")
        spec = _spec(tmp_path, "a", {"path": "NOTES.md", "kind": "lines",
                                     "markers": ["A entry"]})
        assert stage.main(["a", "--spec", spec, "--repo", str(repo)]) == 0
        assert "A entry" in _cached(repo)
        assert "B entry" not in _cached(repo)
        assert "B entry" in _unstaged(repo)

    def test_a_block_lands_while_the_block_beside_it_does_not(self, tmp_path):
        repo = _repo(tmp_path, "intro\nANCHOR_END\ntail\n")
        _write(repo, "intro\nFEATURE A\nANCHOR_END\nFEATURE B\ntail\n")
        spec = _spec(tmp_path, "a", {"path": "NOTES.md", "kind": "block",
                                     "start": "FEATURE A", "end": "ANCHOR_END"})
        assert stage.main(["a", "--spec", spec, "--repo", str(repo)]) == 0
        assert "FEATURE A" in _cached(repo)
        assert "FEATURE B" not in _cached(repo)
        assert "FEATURE B" in _unstaged(repo)

    def test_whole_stages_an_untracked_file(self, tmp_path):
        repo = _repo(tmp_path, "intro\n")
        (repo / "NEW.md").write_text("brand new\n", encoding="utf-8", newline="\n")
        spec = _spec(tmp_path, "a", {"path": "NEW.md", "kind": "whole"})
        assert stage.main(["a", "--spec", spec, "--repo", str(repo)]) == 0
        assert "NEW.md" in _git(repo, "diff", "--cached", "--name-only").stdout.decode()

    def test_dry_run_changes_nothing(self, tmp_path, capsys):
        repo = self._two_hunks(tmp_path)
        spec = _spec(tmp_path, "a", {"path": "NOTES.md", "kind": "hunks",
                                     "markers": ["A-fix"]})
        before = _git(repo, "status", "--porcelain").stdout
        assert stage.main(["a", "--spec", spec, "--repo", str(repo), "--dry-run"]) == 0
        assert _cached(repo) == ""
        assert _git(repo, "status", "--porcelain").stdout == before
        assert "would stage" in capsys.readouterr().out

    def test_a_marker_that_matches_nothing_refuses(self, tmp_path, capsys):
        repo = self._two_hunks(tmp_path)
        spec = _spec(tmp_path, "a", {"path": "NOTES.md", "kind": "hunks",
                                     "markers": ["stale-marker"]})
        assert stage.main(["a", "--spec", spec, "--repo", str(repo)]) == 3
        assert _cached(repo) == ""
        assert "refused" in capsys.readouterr().err


# ── 5. the wrapper shape this replaced ───────────────────────────────────────

class TestItReplacedTheWrappers:
    def test_the_tool_needs_no_bespoke_script(self):
        """The whole point: the harness lives once, and a spec is data, not code."""
        source = TOOL.read_text(encoding="utf-8")
        assert "stage_features.json" in source
        assert "importlib" not in source and "stage_split" not in source, (
            "the tool imports a per-commit module instead of carrying the harness")
        # The four shapes the wrappers each re-implemented.
        for kind in ('"hunks"', '"lines"', '"block"', '"whole"'):
            assert kind in source, f"the {kind} rule is gone"
