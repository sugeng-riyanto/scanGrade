"""Stage one feature's share of the files other features also edited.

A commit is supposed to be one feature, but a handful of files carry several at
once — `AGENTS.md` appends every feature's journal entry to the same spot,
`docs/AUTO_DEPLOY.md` grows a subsection per change, and a route module can hold
two unrelated blocks. A plain `git add` on any of them drags the other features
into the commit, and `git add -p` cannot help when three entries were appended in
one hunk (git merged them, so there is no line-level choice to make by hand).

Until now each such commit grew its own `.freebuff/stage_<feature>.py`: a copy of
the split/recount/apply harness with that feature's markers hard-coded. Two
copies of "how a patch reaches the index" is how one of them quietly stops
applying, and every commit paid the same authoring cost again. This is that
harness, once, driven by a declarative spec instead of code.

The spec
========

A JSON file (default `.freebuff/stage_features.json`, override with `--spec`)
names each feature and, per shared file, the rule that picks its lines::

    {
      "features": {
        "branch-first": {
          "files": [
            {"path": "docs/AUTO_DEPLOY.md", "kind": "hunks",
             "markers": ["branch-first-logic"],
             "exclude": ["Two consequences worth stating"]},
            {"path": "AGENTS.md", "kind": "block",
             "start": "- **Kotak yang macet menolak *sebelum* ia fetch",
             "end":   "- **Empat hal dalam satu permintaan onboarding"}
          ]
        }
      }
    }

Four rule kinds, one per shape the shared files actually have:

* ``hunks`` — keep every hunk whose changed lines contain any marker. For a file
  where one feature's edit is whole hunks (a paragraph rewrite plus its new
  subsection) beside another's. ``exclude`` asserts a marker from *another*
  feature is absent, so a hunk that starts matching both fails loudly rather than
  committing the wrong paragraph under this feature's message.
* ``lines`` — inside each hunk, keep only the *added* lines that carry a marker
  and drop the rest, then let ``git apply --recount`` recompute the counts. For
  the appended-journal-entry shape, where git merged several features into one
  hunk and there is nothing to select by hunk.
* ``block`` — rebuild the file as ``HEAD <path>`` with the worktree's region
  between ``start`` and ``end`` spliced in at ``end``'s position. For one block
  that needs to land in the index while an adjacent block does not (`start` is
  usually a line that exists only in the worktree, `end` a line that already
  exists in ``HEAD`` — the anchor the region is inserted before).
* ``whole`` — stage the entire path, for a feature's new or renamed file that no
  other feature shares. A plain ``git add``, named in the spec so one feature's
  staging is one command.

Only the index is ever touched: the working tree is read, never written, and
nothing is committed. After this tool stages its feature, the leftovers stay
unstaged for the next feature (or a plain ``git add``).

Refusals, because a silent no-op is the failure mode
====================================================

A rule that matches nothing is refused (exit ``3``) rather than ignored: a stale
marker would otherwise stage an empty patch, print success, and leave the feature
half-committed with no signal. ``--allow-empty`` is the explicit override. A
``block`` whose ``end`` anchor is missing or not unique in ``HEAD`` is refused for
the same reason — the region has nowhere unambiguous to go.

    python deploy/stage_feature.py --list
    python deploy/stage_feature.py branch-first --dry-run
    python deploy/stage_feature.py branch-first
"""
from __future__ import annotations

import argparse
import difflib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SPEC = ROOT / ".freebuff" / "stage_features.json"

#: The rule kinds, and nothing else, so a typo in the spec is a refusal and not a
#: silently skipped file.
KINDS = ("hunks", "lines", "block", "whole")


class StageError(Exception):
    """A spec or a patch that must stop the run rather than stage nothing."""


class StageConflict(StageError):
    """A hunk that matched *another* feature's exclude marker.

    Never swallowed by ``--allow-empty``: an empty is "this rule has nothing", a
    conflict is "this rule is about to stage the wrong feature's lines".
    """


# ── git, read politely ───────────────────────────────────────────────────────

def run(args, repo: Path = ROOT):
    # UTF-8 with surrogateescape: the templates carry box-drawing characters, and
    # decoding git's bytes as the console codepage would turn a patch that matches
    # into one that does not. This round-trips the exact bytes back on write.
    return subprocess.run(args, cwd=repo, text=True, capture_output=True,
                          encoding="utf-8", errors="surrogateescape")


def diff_of(path: str, repo: Path = ROOT) -> str:
    return run(["git", "diff", "--no-color", "-U3", "--", path], repo).stdout


def head_text(path: str, repo: Path = ROOT) -> str:
    proc = run(["git", "show", f"HEAD:{path}"], repo)
    if proc.returncode != 0:
        raise StageError(f"{path}: no HEAD version to build a block against:\n"
                         f"{proc.stderr.strip()}")
    return proc.stdout.replace("\r\n", "\n")


def work_text(path: str, repo: Path = ROOT) -> str:
    file = repo / path
    if not file.exists():
        raise StageError(f"{path}: not in the working tree")
    return file.read_text(encoding="utf-8").replace("\r\n", "\n")


def tracked(path: str, repo: Path = ROOT) -> bool:
    return run(["git", "ls-files", "--error-unmatch", "--", path], repo).returncode == 0


# ── the split: a diff into hunks, a hunk into its changed lines ──────────────

def split(diff: str):
    """A unified diff as ``(header, [hunk, ...])``; ``([], [])`` when there is none."""
    lines = diff.splitlines(keepends=True)
    first = next((i for i, l in enumerate(lines) if l.startswith("@@")), None)
    if first is None:
        return "", []
    header = "".join(lines[:first])
    hunks, current = [], None
    for line in lines[first:]:
        if line.startswith("@@"):
            if current is not None:
                hunks.append("".join(current))
            current = [line]
        elif current is not None:
            current.append(line)
    if current:
        hunks.append("".join(current))
    return header, hunks


def changed_lines(hunk: str) -> list[str]:
    return [l for l in hunk.splitlines()
            if l.startswith(("+", "-")) and not l.startswith(("+++", "---"))]


def added_lines(hunk: str) -> list[str]:
    return [l for l in hunk.splitlines()
            if l.startswith("+") and not l.startswith("+++")]


# ── selecting each shape ─────────────────────────────────────────────────────

def select_hunks(diff: str, markers, exclude=()) -> tuple[str, int]:
    header, hunks = split(diff)
    chosen = []
    for hunk in hunks:
        body = changed_lines(hunk)
        if any(m in line for m in markers for line in body):
            both = [m for m in exclude if any(m in line for line in body)]
            if both:
                raise StageConflict(
                    "a hunk matched both this feature and another's marker "
                    f"({', '.join(both)}) — refusing rather than staging the "
                    f"wrong paragraph:\n{hunk.splitlines()[0]}")
            chosen.append(hunk)
    if not chosen:
        raise StageError("no hunk carried any of the markers " + repr(list(markers)))
    return header + "".join(chosen), len(chosen)


def select_lines(diff: str, markers) -> tuple[str, int]:
    """Keep only marked added lines per hunk; ``--recount`` fixes the counts."""
    header, hunks = split(diff)
    out, matched = [], 0
    for hunk in hunks:
        body = hunk.splitlines(keepends=True)
        head, rest = body[0], body[1:]
        keep, dropped = [], False
        for line in rest:
            if line.startswith("+") and not line.startswith("+++"):
                if any(m in line for m in markers):
                    keep.append(line)
                    matched += 1
                else:
                    dropped = True
            else:
                keep.append(line)
        out.append(head + "".join(keep) if dropped else hunk)
    if not matched:
        raise StageError("no added line carried any of the markers " + repr(list(markers)))
    return header + "".join(out), matched


def rebuild_block(path: str, start: str, end: str, repo: Path = ROOT) -> str:
    head = head_text(path, repo)
    work = work_text(path, repo)
    if start not in work or end not in work or work.index(start) > work.index(end):
        raise StageError(f"{path}: the block's `start`/`end` do not bound a "
                         f"region of the working tree")
    if head.count(end) != 1:
        raise StageError(f"{path}: the `end` anchor is not unique in HEAD, so the "
                         f"block has no unambiguous place to land")
    region = work[work.index(start):work.index(end)]
    rebuilt = head[:head.index(end)] + region + head[head.index(end):]
    return "".join(difflib.unified_diff(
        head.splitlines(keepends=True), rebuilt.splitlines(keepends=True),
        fromfile=f"a/{path}", tofile=f"b/{path}", n=3))


# ── getting a patch into the index ───────────────────────────────────────────

def apply_cached(patch: str, label: str, repo: Path = ROOT) -> None:
    # The patch goes to `git apply` on **stdin**, not through a scratch file: a
    # shared path under `.freebuff/` was how one run's patch could be read by the
    # next, and a tool that stages a commit has no business writing files anyway.
    if not patch.strip():
        raise StageError(f"{label}: the patch is empty")
    # Bytes, not text: a text-mode pipe translates each `\n` to the platform's
    # line ending on write, and on Windows that hands `git apply` a CRLF patch —
    # "corrupt patch" for a patch that is byte-for-byte correct.
    blob = patch.encode("utf-8", "surrogateescape")
    last = ""
    for flags in (["--recount"], []):
        proc = subprocess.run(
            ["git", "apply", "--cached", *flags, "-"], cwd=repo,
            input=blob, capture_output=True)
        if proc.returncode == 0:
            print(f"staged {label}")
            return
        last = (proc.stderr or proc.stdout).decode("utf-8", "replace")
    raise StageError(f"git apply failed for {label}:\n{last.strip()}")


def stage_whole(path: str, repo: Path = ROOT) -> None:
    proc = run(["git", "add", "--", path], repo)
    if proc.returncode != 0:
        raise StageError(f"git add failed for {path}:\n{(proc.stderr or proc.stdout).strip()}")
    print(f"staged {path} (whole)")


# ── the spec, and one feature of it ──────────────────────────────────────────

def load_spec(path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise StageError(f"no spec at {path} — write one, or pass --spec")
    except json.JSONDecodeError as exc:
        raise StageError(f"{path} is not valid JSON: {exc}")
    features = data.get("features")
    if not isinstance(features, dict) or not features:
        raise StageError(f"{path}: `features` must name at least one feature")
    return features


def validate(feature: str, rules: dict) -> list[dict]:
    if not isinstance(rules, list) or not rules:
        raise StageError(f"feature `{feature}` must list at least one file rule")
    for rule in rules:
        path = rule.get("path")
        kind = rule.get("kind")
        if not path or not isinstance(path, str):
            raise StageError(f"feature `{feature}`: a file rule has no `path`")
        if kind not in KINDS:
            raise StageError(f"{path}: `kind` must be one of {KINDS}, not {kind!r}")
        if kind in ("hunks", "lines") and not rule.get("markers"):
            raise StageError(f"{path}: `{kind}` needs a non-empty `markers` list")
        if kind == "block" and not (rule.get("start") and rule.get("end")):
            raise StageError(f"{path}: `block` needs both `start` and `end`")
    return rules


def stage_rule(rule: dict, feature: str, repo: Path, dry_run: bool, allow_empty: bool) -> None:
    path, kind = rule["path"], rule["kind"]
    if kind == "whole":
        if not dry_run:
            stage_whole(path, repo)
        else:
            print(f"would stage {path} (whole)")
        return

    if kind == "block":
        patch = rebuild_block(path, rule["start"], rule["end"], repo)
        detail = "block"
        if not patch.strip() and not allow_empty:
            raise StageError(f"{path}: nothing between the block anchors to stage")
    else:
        diff = diff_of(path, repo)
        try:
            if kind == "hunks":
                patch, matched = select_hunks(diff, rule["markers"], rule.get("exclude", ()))
                detail = f"{matched} hunk(s)"
            else:
                patch, matched = select_lines(diff, rule["markers"])
                detail = f"{matched} added line(s)"
        except StageConflict:
            raise
        except StageError:
            if not allow_empty:
                raise
            print(f"skip {path}: nothing matched the {kind} rule for `{feature}`")
            return

    if dry_run:
        print(f"would stage {path} ({kind}: {detail})")
        return
    apply_cached(patch, f"{path} ({detail})", repo)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="stage_feature", description="Stage one feature's share of shared files.")
    parser.add_argument("feature", nargs="?",
                        help="the feature named in the spec to stage")
    parser.add_argument("--spec", default=str(DEFAULT_SPEC),
                        help=f"the marker spec (default {DEFAULT_SPEC})")
    parser.add_argument("--repo", default=str(ROOT), help="the checkout to stage in")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be staged; change nothing")
    parser.add_argument("--allow-empty", action="store_true",
                        help="do not refuse a rule that matches nothing")
    parser.add_argument("--list", action="store_true",
                        help="list the features in the spec and exit")
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    try:
        features = load_spec(args.spec)
        if args.list:
            for name in features:
                print(name)
            return 0
        if not args.feature:
            parser.error("a feature name is required (or --list)")
        if args.feature not in features:
            raise StageError(
                f"no feature `{args.feature}` in the spec; have: {', '.join(features)}")
        for rule in validate(args.feature, features[args.feature]):
            stage_rule(rule, args.feature, repo, args.dry_run, args.allow_empty)
    except StageError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
