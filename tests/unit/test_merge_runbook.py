"""The merge runbook re-run: its own classifier, against the parents it records.

`docs/deploy/QUESTION_TYPES_MERGE_RUNBOOK.md` is the record of one reconciliation —
**which side won each file** — and §4 ends with the recipe that produced its table:

    for f in $(git diff --name-only "$BASE" bca30d3); do
      if   git diff --quiet a2876cc 675b56f -- "$f"; then echo "main    $f"
      elif git diff --quiet bca30d3 675b56f -- "$f"; then echo "branch  $f"
      else                                                 echo "both    $f"; fi
    done

A record nobody re-runs is a paragraph claiming to be a measurement, and this page's
whole authority is that its labels were checked rather than remembered: it even says
so — one row `used to read main; it is not`. So this file re-runs that recipe against
the merge and parents the page records, and refuses a row whose label has drifted.

What is asserted, and why each half matters:

* **the recorded state is the merge it names** — the commit's two parents and the
  merge base, read out of `## The state this page describes` and compared with git.
  A page describing a different merge is not a record of this one.
* **every labelled row matches**: `main` is byte-identical to the main parent in the
  merge, `branch` to the branch parent, `both` differs from both. That is the
  runbook's `if/elif/else`, applied per row rather than per branch-changed path —
  which is what lets the `main` table also carry files the branch never touched.
* **a `main`/`branch` row may not name a path neither parent has.** This is the trap
  the runbook itself names at §2: `git diff --quiet A B -- path` answers *identical*
  for a path **absent on both sides**, so a typo in a `main` row would satisfy the
  byte comparison and the table would keep passing while pointing at nothing.
* **the tables cover every path the branch changed, once each.** The page claims the
  22 `both` paths are the rows it spells out and the 17 branch paths are "the
  question types and their suites"; a classified path with no row makes that
  sentence false, and a path in two tables makes it ambiguous.
* **the counts the page prints are the classifier's** — `9 main · 17 branch · 22 both
  out of the 48` is a number the page offers as evidence, so it is checked as one.

It is also a **gate**: `deploy/theme_gate.sh` runs this module (`python -m
tests.unit.test_merge_runbook`) on every commit that touches a page or the runbook, so
a label that drifts is refused before it is committed rather than by a suite nobody
runs on the box. The entry point is `gate()`, with the exit codes the other tools
here use — 0 agree, 1 a row says something the merge does not, 2 could not measure.

One case is deliberately **not** a refusal. This module reads three historical
commits, and a shallow clone — a CI checkout, or a box cloned with `--depth` — does
not have them; that is a property of the box, not of the release, and a gate that
refused it would take a good release down for the way it was cloned. So `is_shallow()`
is asked *before* anything is read, and a shallow clone exits **0** with the words
`were NOT re-run` on the line, which is also the phrase the gate withholds its success
claim on. A full clone whose recorded commits are missing is a different answer (2:
could not measure), and in the suite a missing ref fails with its name rather than
skipping — a skip would report the record as verified by a run that never read it.
"""
from __future__ import annotations

import shutil

import functools
import os
import re
import subprocess
from pathlib import Path

import pytest

from tests.unit.git_env import git

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "deploy" / "QUESTION_TYPES_MERGE_RUNBOOK.md"
GATE = ROOT / "deploy" / "theme_gate.sh"
#: Where the runbook sits inside a checkout, so a clone's copy of it is addressed
#: the same way the page addresses itself.
RUNBOOK_REL = "docs/deploy/QUESTION_TYPES_MERGE_RUNBOOK.md"

#: The section that titles each table, mapped to the label its rows assert. The label
#: is the **heading's** in every table, never the cell's: the heading is where the page
#: makes the claim ("main wins — the AI marking path") and a cell is a repetition of it.
#: Reading the label out of the cell instead made the heading unasserted — a table
#: retitled from `branch wins` to `main wins` kept passing on its cells alone, which is
#: exactly the drift a reader would act on. A row printing a side its heading does not
#: is a failure in its own right (`test_the_cell_and_the_heading_agree`).
SECTIONS = {
    "main": ("main wins", "main"),
    "branch": ("branch wins", "branch"),
    "both": ("both", "both"),
    "dropped": ("dropped", "dropped"),
    "generated": ("generated", "generated"),
}

REF = re.compile(r"`([0-9a-f]{7,40})`")
COUNTS = re.compile(r"(\d+)\s+main\s*·\s*(\d+)\s+branch\s*·\s*(\d+)\s+both")
TABLE_ROW = re.compile(r"^\|(.*)\|\s*$")
DASHES = {"—", "–", "-", "--"}


class MergeRecordError(RuntimeError):
    """The recorded merge cannot be re-read in this clone."""


def _git(*args: str, root: Path | None = None) -> str:
    done = git(*args, cwd=str(root or ROOT))
    if done.returncode != 0:
        raise MergeRecordError(
            f"git {' '.join(args)} failed: {done.stderr.strip() or '(no output)'}")
    return done.stdout


def _lines(*args: str, root: Path | None = None) -> set[str]:
    return {line for line in _git(*args, root=root).splitlines() if line.strip()}


def recorded(doc: Path | None = None) -> dict[str, str]:
    """The four refs the page's state table records, from *this* document.

    The document is a parameter rather than a module constant because the gate has
    to be runnable against a fixture: a refusal nobody executes is a paragraph. The
    default is the page this suite is about, so every read below is unchanged.
    """
    return _recorded(Path(doc or DOC))


@functools.lru_cache(maxsize=4)
def _recorded(doc: Path) -> dict[str, str]:
    text = doc.read_text(encoding="utf-8")
    found = {}
    for line in text.splitlines():
        cells = _cells(line)
        if not cells or len(cells) < 2:
            continue
        if cells[0] == "merge commit":
            found["merge"] = REF.search(cells[1]).group(1)
        elif cells[0] == "parents":
            refs = REF.findall(cells[1])
            if len(refs) != 2:
                raise AssertionError(f"parents row does not name two refs: {cells[1]}")
            found["main"], found["branch"] = refs
        elif cells[0] == "merge base":
            found["base"] = REF.search(cells[1]).group(1)
    missing = {"merge", "main", "branch", "base"} - set(found)
    if missing:
        raise AssertionError(
            f"the state table no longer records {sorted(missing)} — the page cannot "
            "be re-run without the refs it describes")
    return found


def facts(doc: Path | None = None, root: Path | None = None) -> dict[str, set[str]]:
    """One read of each set the classifier compares, so a row is a lookup."""
    return _facts(Path(doc or DOC), Path(root or ROOT))


@functools.lru_cache(maxsize=4)
def _facts(doc: Path, root: Path) -> dict[str, set[str]]:
    refs = recorded(doc)
    # The page records short refs and `rev-list --parents` answers in full ones, so
    # every ref is resolved once and compared at full length.
    full = {}
    for name, ref in refs.items():
        try:
            full[name] = _git("rev-parse", f"{ref}^{{commit}}", root=root).strip()
        except MergeRecordError as exc:
            raise MergeRecordError(
                f"the {name} commit {ref} recorded by {DOC.name} is not in this "
                f"clone, so its table cannot be re-run ({exc})") from exc
    main, branch = full["main"], full["branch"]
    merge, base = full["merge"], full["base"]
    parents = _git("rev-list", "--parents", "-n", "1", merge, root=root).split()[1:]
    if parents != [main, branch]:
        raise MergeRecordError(
            f"{merge[:7]} has parents {[p[:7] for p in parents]}, but the page records "
            f"{[refs['main'], refs['branch']]}")
    if _git("merge-base", main, branch, root=root).strip() != base:
        raise MergeRecordError(
            f"the merge base of {refs['main']} and {refs['branch']} is not the "
            f"recorded {refs['base']}")
    return {
        "changed_main": _lines("diff", "--name-only", main, merge, root=root),
        "changed_branch": _lines("diff", "--name-only", branch, merge, root=root),
        "changed_by_branch": _lines("diff", "--name-only", base, branch, root=root),
        "in_main": _lines("ls-tree", "-r", "--name-only", main, root=root),
        "in_branch": _lines("ls-tree", "-r", "--name-only", branch, root=root),
        "in_merge": _lines("ls-tree", "-r", "--name-only", merge, root=root),
    }


def _cells(line: str) -> list[str]:
    """The cells of a table row, or [] when the line is not one."""
    match = TABLE_ROW.match(line.strip())
    if not match:
        return []
    cells = [c.strip() for c in match.group(1).split("|")]
    if not cells or all(set(c) <= set("-: ") for c in cells):
        return []                                    # the header separator
    return cells


def _paths(cell: str) -> list[str]:
    """Every path a cell names, expanding this page's shorthand.

    `docs/features/x.md`, `y.md` is one row about two files, and the second inherits
    the first's directory — the same convention the page uses in the tables and in
    its prose.
    """
    out: list[str] = []
    last_dir = ""
    for token in re.findall(r"`([^`]+)`", cell):
        token = token.strip()
        if not token or token.startswith("/"):
            continue
        if "/" in token:
            last_dir = token.rsplit("/", 1)[0]
        elif last_dir:
            token = f"{last_dir}/{token}"
        out.append(token)
    return out


def sections(doc: Path | None = None) -> list[tuple[str, str, str, str]]:
    """(kind, label, path, cell) for every row of every table that has one.

    The `cell` is the word the row itself prints in its `kept` column. It is not
    what the label is decided from — the heading is, in every table — but a cell
    that contradicts its heading is the same drift as a wrong heading, and it is
    what a reader of that row sees. A dash in the cell is neither: it is the row
    saying the branch never touched this file, so there is no side to assert.
    """
    rows: list[tuple[str, str, str, str]] = []
    kind = ""
    for line in Path(doc or DOC).read_text(encoding="utf-8").splitlines():
        heading = re.match(r"^#{2,4}\s+(.*)", line)
        if heading:
            title = heading.group(1).strip()
            kind = next((k for k, (needle, _) in SECTIONS.items()
                         if title.lower().startswith(needle)), "")
            continue
        if not kind:
            continue
        cells = _cells(line)
        if len(cells) < 2 or cells[0].lower() in ("file", "thing"):
            continue
        _, label = SECTIONS[kind]
        cell = cells[1].strip("`").lower()
        if cell in DASHES:
            # A dash is the absence of a claim, not a side: one row is main's own copy,
            # which the branch never touched, so the heading has nothing to say about it.
            label = "-"
        for path in _paths(cells[0]):
            rows.append((kind, label, path, cell))
    if not rows:
        raise AssertionError("no labelled per-file rows were found — the tables moved")
    return rows


ROWS = [(kind, label, path) for kind, label, path, _ in sections()]


def agrees(label: str, path: str, doc: Path | None = None,
           root: Path | None = None) -> tuple[bool, str]:
    """Whether a row's label is what the parents say, with the evidence either way."""
    seen = facts(doc, root)
    same_main = path not in seen["changed_main"]
    same_branch = path not in seen["changed_branch"]
    detail = (f"{path}: identical to main={same_main}, identical to branch={same_branch}, "
              f"present in the merge={path in seen['in_merge']}")
    if label == "main":
        return same_main and (path in seen["in_main"] or path in seen["in_branch"]), detail
    if label == "branch":
        return same_branch and (path in seen["in_main"] or path in seen["in_branch"]), detail
    if label == "both":
        return (not same_main and not same_branch), detail
    if label == "dropped":
        return path not in seen["in_merge"], detail
    if label == "generated":
        return not same_main, detail
    if label == "-":
        return path in seen["in_merge"] and not same_branch, detail
    return False, detail


class TestTheRecordedMerge:

    def test_the_parents_and_base_are_the_ones_git_has(self):
        """`facts()` raises if any of this is false; this names what it read."""
        refs = recorded()
        facts()
        assert _git("rev-parse", refs["merge"] + "^1").strip().startswith(refs["main"])
        assert _git("rev-parse", refs["merge"] + "^2").strip().startswith(refs["branch"])
        assert refs["base"] in _git("merge-base", refs["main"], refs["branch"])

    def test_the_classifier_reproduces_the_three_groups(self):
        """The recipe in §4, re-run: 9 main · 17 branch · 22 both, 48 paths."""
        sets = facts()
        counts = {"main": 0, "branch": 0, "both": 0}
        for path in sets["changed_by_branch"]:
            label = ("main" if path not in sets["changed_main"]
                     else "branch" if path not in sets["changed_branch"]
                     else "both")
            counts[label] += 1
        assert len(sets["changed_by_branch"]) == 48, sorted(sets["changed_by_branch"])
        assert counts == {"main": 9, "branch": 17, "both": 22}, counts

    def test_the_counts_the_page_prints_are_those_counts(self):
        """`9 main · 17 branch · 22 both out of the 48` is offered as evidence."""
        text = DOC.read_text(encoding="utf-8")
        match = COUNTS.search(text)
        assert match, "the page no longer prints the classifier's counts"
        assert tuple(int(n) for n in match.groups()) == (9, 17, 22)
        assert "48" in text.split(match.group(0), 1)[1][:80], \
            "the count line no longer says how many paths the branch changed"

    def test_a_missing_commit_is_named_not_skipped(self):
        with pytest.raises(MergeRecordError) as excinfo:
            _git("cat-file", "-e", "0000000^{commit}")
        assert "0000000" in str(excinfo.value)


class TestEveryRow:

    @pytest.mark.parametrize("kind,label,path", ROWS,
                             ids=[f"{k}:{p}" for k, _, p in ROWS])
    def test_the_label_matches_the_parents(self, kind, label, path):
        ok, detail = agrees(label, path)
        assert ok, (f"the `{kind}` table says {label!r} for a path the recorded "
                    f"parents do not agree with — {detail}")

    def test_every_path_the_branch_changed_has_exactly_one_row(self):
        """The page says the 22 `both` rows and the 17 branch rows *are* the 48."""
        named = [p for _, _, p in ROWS if p in facts()["changed_by_branch"]]
        twice = sorted({p for p in named if named.count(p) > 1})
        assert not twice, f"a path is in two tables: {twice}"
        missing = sorted(facts()["changed_by_branch"] - set(named))
        assert not missing, (
            f"the branch changed {len(missing)} path(s) no table names: {missing} — "
            "the page's count of rows would then be wrong")

    def test_no_declared_path_is_a_typo(self):
        """`git diff --quiet` is true for a path absent on both sides — so a row
        pointing at nothing would pass a byte comparison. It must exist first."""
        seen = facts()
        absent = sorted({p for _, _, p in ROWS
                         if p not in seen["in_main"] and p not in seen["in_branch"]
                         and p not in seen["in_merge"]})
        assert not absent, (
            f"these rows name a path neither parent nor the merge has (typo, or a "
            f"file the page invented): {absent}")


class TestTheParser:

    def test_a_row_in_the_both_table_inherits_both_whatever_its_cell_says(self):
        rows = [(k, label, p) for k, label, p in ROWS
                if k == "both" and label != "both"]
        assert rows == [], f"a `both` section row was not read as both: {rows}"

    def test_the_cell_and_the_heading_agree(self):
        """A row printing `main` in a `both` table is the drift a reader sees first.

        The label is read from the heading, so a differing cell would otherwise pass
        the classifier untouched: the assertion holds the two halves of the same row
        together, which is what makes the heading the thing that counts rather than a
        label quietly overridden per row.
        """
        disagree = [(kind, path, cell) for kind, label, path, cell in sections()
                    if cell in ("main", "branch", "both") and cell != label]
        assert not disagree, (
            "a row prints a side its own table does not: "
            f"{[(k, p, c) for k, p, c in disagree]}")

    def test_the_dropped_and_generated_tables_say_so_in_their_headings(self):
        assert {label for kind, label, _ in ROWS if kind == "dropped"} == {"dropped"}
        assert {label for kind, label, _ in ROWS if kind == "generated"} == {"generated"}

    def test_a_dash_in_the_main_table_is_not_a_claim(self):
        """One row is main's own copy, untouched by the branch: no side to assert."""
        assert [p for k, label, p in ROWS if k == "main" and label == "-"] == \
            ["tests/unit/test_ai_essay_grading.py"]

    def test_the_side_tables_claim_a_side(self):
        """Each side table is read as the side its heading names — a retitled heading
        moves every row in it — and only the dash row carries no side."""
        labels = {}
        for kind, label, _ in ROWS:
            labels.setdefault(kind, set()).add(label)
        assert labels["branch"] == {"branch"}
        assert labels["both"] == {"both"}
        assert labels["main"] == {"main", "-"}

    def test_shorthand_inherits_the_previous_directory(self):
        assert _paths("`app/templates/a.html`, `b.html`") == \
            ["app/templates/a.html", "app/templates/b.html"]
        assert _paths("`tests/unit/x.py`, `y.py`, `docs/z.md`") == \
            ["tests/unit/x.py", "tests/unit/y.py", "docs/z.md"]


# ══ The gate entry point ═════════════════════════════════════════════════════
#
# `deploy/theme_gate.sh` runs `python -m tests.unit.test_merge_runbook`. The three
# answers below are the contract: 0 the labels are what the parents say, 1 one of
# them is not, 2 this clone cannot answer. The fourth case is the shallow clone,
# which must be 0 — a box that cloned with `--depth` may not refuse a release for
# the way it was cloned — and it is asserted on a real shallow clone rather than by
# faking the git answer, because the thing being promised is what git says.


SHALLOW_NOTE = ("the labels were NOT re-run — this checkout is a shallow clone, so "
                "the merge and the refs it records are not here")


def is_shallow(root: Path | None = None) -> bool:
    """Whether this checkout was cloned without the history the runbook records.

    `git rev-parse --is-shallow-repository` (git 2.15+) is the repository's own
    answer. A git that cannot answer is treated as *not* shallow, so the read that
    follows reports what went wrong (`git ... failed`) rather than this predicate
    pretending to know.
    """
    try:
        return _git("rev-parse", "--is-shallow-repository", root=root).strip() == "true"
    except MergeRecordError:
        return False


def findings(doc: Path | None = None, root: Path | None = None) -> list[str]:
    """Every disagreement the gate refuses, one line each.

    The same classifier the suite asserts row by row, gathered instead of asserted:
    the gate prints what is wrong in one run, where the suite stops at the first
    failure. The state table losing its refs is a **finding** (the page changed);
    the commits being absent from the clone raises `MergeRecordError`, which the
    entry point answers as *could not measure*.
    """
    doc = Path(doc or DOC)
    root = Path(root or ROOT)
    try:
        rows = sections(doc)
    except AssertionError as exc:
        return [str(exc)]
    try:
        seen = facts(doc, root)
    except MergeRecordError:
        raise
    except AssertionError as exc:            # the state table lost a ref
        return [str(exc)]

    problems: list[str] = []
    for kind, label, path, cell in rows:
        if cell in ("main", "branch", "both") and cell != label:
            problems.append(
                f"{path}: the `{kind}` table's row prints {cell!r} but its heading "
                f"says {label!r}")
        ok, detail = agrees(label, path, doc, root)
        if not ok:
            problems.append(
                f"the `{kind}` table says {label!r} for a file the recorded parents "
                f"do not agree with — {detail}")

    named = [path for _, _, path, _ in rows if path in seen["changed_by_branch"]]
    twice = sorted({path for path in named if named.count(path) > 1})
    if twice:
        problems.append(f"a path is claimed by two tables: {twice}")
    missing = sorted(seen["changed_by_branch"] - set(named))
    if missing:
        problems.append(
            f"{len(missing)} path(s) the branch changed have no row, so the page's "
            f"count of rows is wrong: {missing}")
    absent = sorted({path for _, _, path, _ in rows
                     if path not in seen["in_main"]
                     and path not in seen["in_branch"]
                     and path not in seen["in_merge"]})
    if absent:
        problems.append(
            f"these rows name a path neither parent nor the merge has (a typo, or a "
            f"file the page invented): {absent}")
    return problems


def gate(doc: Path | None = None, root: Path | None = None) -> int:
    """0 the labels agree (or were not re-run, on a shallow clone) · 1 a label drifted
    · 2 could not measure. Prints its verdict, because the gate is a place a person
    reads."""
    doc = Path(doc or DOC)
    root = Path(root or ROOT)
    if is_shallow(root):
        print(f"merge runbook: {SHALLOW_NOTE}. That is a property of this box, not "
              f"of the release, so nothing is refused.")
        return 0
    try:
        problems = findings(doc, root)
    except MergeRecordError as exc:
        print(f"merge runbook: COULD NOT RE-RUN — {exc}")
        return 2
    if problems:
        print(f"merge runbook: {len(problems)} label(s) disagree with the merge they "
              f"describe ({doc.name}):")
        for problem in problems:
            print(f"    {problem}")
        return 1
    print(f"merge runbook: {len(sections(doc))} labelled row(s) agree with the merge "
          f"recorded in {doc.name}")
    return 0


# ══ The gate, against checkouts rather than this one ═════════════════════════
#
# `gate()` is driven through fixture repositories, because "a broken label exits 1"
# is a claim about code and not about the page that happens to be correct today. A
# fixture merge is built with the same shape the real one has — a main side, a
# branch side, a file both touched — and the doc that records it is generated with
# the hashes Git actually made.


def _gitq(*args: str, cwd: Path) -> str:
    """A git command that must succeed, returning its trimmed output."""
    return git(*args, cwd=cwd, check=True).stdout.strip()


def _runbook_doc(merge: str, main: str, branch: str, base: str,
                 main_label: str = "main") -> str:
    """The page, in the shape the reader parses: a state table and three tables."""
    return (
        "# Question types merge runbook\n\n"
        "## The state this page describes\n\n"
        "| thing | value |\n|---|---|\n"
        f"| merge commit | `{merge}` |\n"
        f"| parents | `{main}`, `{branch}` |\n"
        f"| merge base | `{base}` |\n\n"
        "## main wins — the page main owns\n\n"
        "| file | kept |\n|---|---|\n"
        f"| `app/main_only.py` | `{main_label}` |\n\n"
        "## branch wins — the question types\n\n"
        "| file | kept |\n|---|---|\n"
        "| `app/branch_only.py` | `branch` |\n\n"
        "## both — the shared table\n\n"
        "| file | kept |\n|---|---|\n"
        "| `both.txt` | `both` |\n"
    )


def _fixture_repo(tmp_path: Path, main_label: str = "main") -> tuple[Path, Path]:
    """A merge with one file per side, a shared file, and the page that records it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _gitq("init", "-q", cwd=repo)
    _gitq("checkout", "-q", "-b", "main", cwd=repo)
    _gitq("config", "user.email", "gate@example.test", cwd=repo)
    _gitq("config", "user.name", "Gate", cwd=repo)

    def commit(message: str, files: dict[str, str]) -> str:
        for name, text in files.items():
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        _gitq("add", "-A", cwd=repo)
        _gitq("commit", "-q", "-m", message, cwd=repo)
        return _gitq("rev-parse", "HEAD", cwd=repo)

    base = commit("base", {"both.txt": "base\n"})
    _gitq("checkout", "-q", "-b", "feature", cwd=repo)
    branch = commit("branch", {"app/branch_only.py": "b\n", "both.txt": "branch\n"})
    _gitq("checkout", "-q", "main", cwd=repo)
    main = commit("main", {"app/main_only.py": "m\n", "both.txt": "main\n"})
    # The shared file is changed on both sides, which is what a `both` row means —
    # and a real merge of that shape stops on the conflict and resolves it, so the
    # fixture does too rather than inventing a merge no git would produce.
    merged = git("merge", "--no-ff", "-m", "merge", "feature", cwd=repo)
    if merged.returncode != 0:
        (repo / "both.txt").write_text("resolved\n", encoding="utf-8")
        _gitq("add", "both.txt", cwd=repo)
        _gitq("commit", "-q", "--no-edit", cwd=repo)
    merge = _gitq("rev-parse", "HEAD", cwd=repo)
    parents = _gitq("rev-list", "--parents", "-n", "1", merge, cwd=repo).split()
    assert len(parents) == 3, f"the fixture is not a merge commit: {parents}"
    doc = repo / RUNBOOK_REL
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(_runbook_doc(merge, main, branch, base, main_label), encoding="utf-8")
    commit("record this merge", {})
    return repo, doc


class TestTheGateOnAFixture:

    def test_a_record_that_agrees_passes(self, tmp_path, capsys):
        repo, doc = _fixture_repo(tmp_path)
        assert gate(doc, repo) == 0
        assert "labelled row(s) agree" in capsys.readouterr().out

    def test_a_relabelled_row_is_exit_one_and_names_the_row(self, tmp_path, capsys):
        """The failure the gate exists for: the merge did not change, the page did."""
        repo, doc = _fixture_repo(tmp_path, main_label="branch")
        assert gate(doc, repo) == 1
        out = capsys.readouterr().out
        assert "app/main_only.py" in out
        assert "the `main` table" in out, (
            "the gate must name the table the row is in, not only the path")
        assert "prints 'branch'" in out

    def test_a_full_clone_of_the_same_fixture_still_measures(self, tmp_path):
        """The control for the shallow case below: the fixture is readable when the
        history is there, so a shallow pass cannot be a fixture that cannot be read."""
        repo, _ = _fixture_repo(tmp_path)
        clone = tmp_path / "clone"
        _gitq("clone", "-q", repo.as_uri(), str(clone), cwd=tmp_path)
        assert not is_shallow(clone)
        assert gate(clone / RUNBOOK_REL, clone) == 0

    def test_a_shallow_clone_passes_and_says_it_did_not_compare(self, tmp_path, capsys):
        """A box cloned with `--depth` may not refuse a release for the way it was
        cloned — and the guard is not cosmetic: the check *would* fail here."""
        repo, _ = _fixture_repo(tmp_path)
        shallow = tmp_path / "shallow"
        _gitq("clone", "-q", "--depth", "1", repo.as_uri(), str(shallow), cwd=tmp_path)
        assert is_shallow(shallow)
        doc = shallow / RUNBOOK_REL
        with pytest.raises(MergeRecordError):
            findings(doc, shallow)
        assert gate(doc, shallow) == 0
        assert "were NOT re-run" in capsys.readouterr().out

    def test_a_full_clone_missing_the_recorded_commit_could_not_measure(self, tmp_path, capsys):
        repo, doc = _fixture_repo(tmp_path)
        merge = recorded(doc)["merge"]
        broken = tmp_path / "broken.md"
        broken.write_text(doc.read_text(encoding="utf-8").replace(merge, "1111111"),
                          encoding="utf-8")
        assert gate(broken, repo) == 2
        assert "COULD NOT RE-RUN" in capsys.readouterr().out

    def test_a_page_with_no_tables_is_a_finding_not_a_pass(self, tmp_path, capsys):
        """The worst shape is the quietest: the record still renders, the tables are
        gone, and every path a reader would follow is no longer named."""
        repo, doc = _fixture_repo(tmp_path)
        doc.write_text("# runbook\n\nthe tables moved to another page\n", encoding="utf-8")
        assert gate(doc, repo) == 1
        assert "no labelled per-file rows" in capsys.readouterr().out

    def test_the_real_tree_agrees_with_its_own_record(self):
        if is_shallow():
            pytest.skip("this checkout is shallow; the gate says so and passes")
        assert gate() == 0


class TestTheGateBlock:
    """The block that runs the tool, executed — not read.

    The block's own job is the relay: a finding is exit 1, a measurement failure is
    said out loud and not refused, and the success *claim* is withheld when the tool
    says it did not compare. A fake interpreter stands in for the tool so each of
    those four answers is driven through the real bash.
    """

    def _block(self) -> str:
        text = GATE.read_text(encoding="utf-8")
        return text.split("# runbook:start", 1)[1].split("# runbook:end", 1)[0]

    def _run(self, tmp_path: Path, rc: int, out: str) -> subprocess.CompletedProcess:
        wrapper = tmp_path / "fake-python.sh"
        wrapper.write_bytes(b"#!/usr/bin/env bash\n"
                            b"printf '%s\\n' \"$FAKE_OUT\"\n"
                            b"exit \"$FAKE_RC\"\n")
        wrapper.chmod(0o755)
        script = tmp_path / "runbook-block.sh"
        # Bytes, not `write_text`: Windows writes CRLF, and bash reads `\r` as part
        # of the command name.
        source = "set -uo pipefail\n" + self._block() + '\nprintf "CLAIM=%s\\n" "$RUNBOOK_CLAIM"\n'
        script.write_bytes(source.replace("\r\n", "\n").encode("utf-8"))
        env = dict(os.environ)
        env.update(PY=str(wrapper), FAKE_OUT=out, FAKE_RC=str(rc))
        return subprocess.run([shutil.which("bash") or "bash", str(script)], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=env)

    def test_a_finding_stops_the_commit(self, tmp_path):
        done = self._run(tmp_path, 1, "merge runbook: 1 label(s) disagree")
        assert done.returncode == 1
        assert "FAILED" in done.stderr
        assert "1 label(s) disagree" in done.stderr

    def test_a_measurement_failure_is_reported_and_not_refused(self, tmp_path):
        done = self._run(tmp_path, 2, "merge runbook: COULD NOT RE-RUN — no such ref")
        assert done.returncode == 0, done.stderr
        assert "NOT re-run" in done.stderr
        assert "CLAIM=" in done.stdout
        assert done.stdout.strip().endswith("CLAIM="), "nothing may be claimed"

    def test_a_shallow_pass_withholds_the_claim(self, tmp_path):
        """Exit 0, but the sentence must not say the labels were checked."""
        done = self._run(tmp_path, 0, "merge runbook: the labels were NOT re-run — shallow")
        assert done.returncode == 0, done.stderr
        assert done.stdout.strip().endswith("CLAIM="), done.stdout

    def test_a_measured_pass_carries_the_claim(self, tmp_path):
        done = self._run(tmp_path, 0, "merge runbook: 62 labelled row(s) agree")
        assert done.returncode == 0, done.stderr
        assert "still names the side the recorded merge gives its file" in done.stdout


class TestTheGateWiring:

    def test_the_tool_is_armament_so_deleting_it_is_exit_three(self):
        armament = GATE.read_text(encoding="utf-8").split("ARMAMENT=", 1)[1].splitlines()[0]
        assert "tests/unit/test_merge_runbook.py" in armament

    def test_the_ok_sentence_claims_it(self):
        text = GATE.read_text(encoding="utf-8")
        assert "${RUNBOOK_CLAIM}" in text
        assert "question-types merge runbook" in text

    def test_the_block_is_fenced_once_each_way(self):
        text = GATE.read_text(encoding="utf-8")
        assert text.count("# runbook:start") == 1
        assert text.count("# runbook:end") == 1
        assert text.index("# runbook:start") < text.index("# runbook:end")

    def test_the_withheld_phrase_is_the_tool_s_own(self):
        """The block matches a phrase the tool prints; a reword on either side would
        make the gate claim a comparison nobody made, silently."""
        assert "were NOT re-run" in SHALLOW_NOTE
        assert '*"were NOT re-run"*' in GATE.read_text(encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(gate())
