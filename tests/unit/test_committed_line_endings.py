"""A carriage return inside a *blob* is a checkout no box can ever make clean.

Measured on the deploy box, 2026-09-30, every two minutes for hours:

    dirty_checkout
    M app/routes/admin_sekolah.py

and the box was 12 commits behind, refusing at the guard that runs *before* the
fetch — so no push, no release and no lever read out of `origin/main` could reach
it. The lever's own heal ran (`app/routes/admin_sekolah.py is set aside` … `every
path was recorded in …`) and the file was dirty again on the next tick.

It was not a hand edit. Commit `0afc68e` — the exact commit that box was sitting
on — committed `app/routes/admin_sekolah.py` with **108 carriage returns** in its
84,502 bytes, while the same path has **0** at `ec6fc5c` and on `main`. The repo
carries `*.py text eol=lf`, and that attribute is what makes the state permanent:

* the **worktree** is read through the clean filter, which normalises CRLF → LF
  before comparing — so a CRLF worktree is *clean*, and this checkout has ~100 of
  them while `git status` is empty;
* the **blob** is compared as stored. A CR in it can therefore never match the
  normalised worktree, and nothing on the box moves it: `git checkout -f`,
  `git checkout HEAD -- <path>` (the lever's heal) and `git stash` all leave
  ` M <path>`, and `git merge --ff-only` answers `Your local changes to the
  following files would be overwritten by merge`. Reproduced end to end in a
  throwaway repository by the tests below.

How such a blob is born: a scripted commit that goes *around* the filters —
`git hash-object -w --no-filters <path>` then
`git update-index --cacheinfo 100644,<sha>,<path>`. `git add` would have
normalised the CRs away; neither of those two does. The remedy is the one the
failure message names, `git add --renormalize <path>` (measured: the blob's CR
count goes 3 → 0 and the scan goes empty).

The check therefore reads **blobs** — the index *and* `HEAD` — and not the working
tree: a CRLF worktree is normal for a Windows checkout, and failing on it would
fail this repo on the machine most of its commits are written on. A CR in a blob
is what strands a Linux box, and that is what is scanned.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.unit.git_env import git_env

ROOT = Path(__file__).resolve().parents[2]

#: The byte that strands a box. Passed to `git grep -e` as a literal pattern.
CR = "\r"

#: The commit whose blob did it, and the path, for the test that proves the sweep
#: would have named it. Skipped rather than deleted where a clone has no such object.
INCIDENT_COMMIT = "0afc68e"
INCIDENT_PATH = "app/routes/admin_sekolah.py"

#: Fixture bytes, written explicitly and never through `write_text` — that
#: translates `\n` to `\r\n` on Windows, and a fixture whose line endings depend on
#: the machine running it cannot measure line endings.
LF_BODY = b"a = 1\nb = 2\nc = 3\n"
CRLF_BODY = b"a = 1\r\nb = 2\r\nc = 3\r\n"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=False,
                          env=git_env())


def cr_blobs(repo: Path, rev: str | None = None) -> list[str]:
    """Tracked paths whose bytes carry a CR: the index, or one commit's tree.

    `-I` leaves binary blobs alone (git decides that from the bytes, not the name),
    and `-l` is enough — the question is *which* paths, because that is what a human
    has to renormalise. Against a rev, `git grep` prefixes each path with `<rev>:`;
    the prefix is stripped so callers compare paths rather than specifiers.
    """
    args = ["grep", "-l", "-I", "-e", CR]
    if rev is None:
        args.insert(1, "--cached")
    else:
        args.append(rev)
    done = _git(repo, *args)
    # 0 = matches, 1 = none. Anything else is a git that could not be asked, and
    # "could not measure" must not read as "clean" — the same rule as the gates.
    assert done.returncode in (0, 1), (
        f"git grep could not be asked ({done.returncode}): {done.stderr.strip()}")
    prefix = f"{rev}:" if rev else ""
    return sorted(line.strip()[len(prefix):] for line in done.stdout.splitlines()
                  if line.strip())


def committed_cr(repo: Path) -> list[str]:
    """Every path whose CR is in a blob: what the next commit carries, and what a
    checkout materialises. Both, because they can disagree — a bad commit fixed
    locally is still a bad commit — and either one strands a box."""
    return sorted(set(cr_blobs(repo)) | set(cr_blobs(repo, "HEAD")))


def _scratch_repo(tmp_path: Path) -> Path:
    """A repository as this one is configured: `*.py text eol=lf`, one file, committed."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "guard@scan-grade.app")
    _git(repo, "config", "user.name", "guard")
    (repo / ".gitattributes").write_bytes(b"*.py text eol=lf\n")
    (repo / "x.py").write_bytes(LF_BODY)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def _plant_cr_blob(repo: Path, name: str = "x.py") -> None:
    """Put a CR-carrying blob in the index without going through the filters.

    This is the shape that stranded the box, and it is deliberately *not* `git add`:
    the attributes normalise a CR away on add, so the only way a box ends up sitting
    on such a blob is a commit built around them.
    """
    path = repo / name
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    blob = _git(repo, "hash-object", "-w", "--no-filters", str(path)).stdout.strip()
    assert blob, "git hash-object produced no blob"
    done = _git(repo, "update-index", "--cacheinfo", f"100644,{blob},{name}")
    assert done.returncode == 0, f"update-index refused: {done.stderr.strip()}"


# ── 1. the guard itself ──────────────────────────────────────────────────────

def test_the_checkout_commits_no_carriage_return():
    """A blob with a CR is a file every Linux box reads as modified, forever."""
    found = committed_cr(ROOT)
    assert found == [], (
        "these tracked paths carry a carriage return in a blob:\n  "
        + "\n  ".join(found)
        + "\nthe attributes say `text eol=lf`, so the worktree is normalised before "
          "it is compared and the blob is not: the file reads as modified on every "
          "Linux checkout, no `git checkout HEAD -- <path>` can clear it, and "
          "`git merge --ff-only` refuses the release. The deploy box lost hours to "
          "exactly this. Fix each one with `git add --renormalize <path>` and commit; "
          "never let a script build a blob with `hash-object --no-filters`.")


# ── 2. the check reads blobs, which is the whole point ───────────────────────

def test_it_reads_the_blob_and_not_the_worktree(tmp_path):
    """The box's shape: a worktree that looks clean, a blob that can never match."""
    repo = _scratch_repo(tmp_path)
    _plant_cr_blob(repo)
    # Whatever the blob holds, leave the worktree's *bytes* free of CRs — so anything
    # that scans files on disk reports nothing, and only a blob reader can see it.
    (repo / "x.py").write_bytes(LF_BODY)

    assert cr_blobs(repo) == ["x.py"]


def test_a_carriage_return_in_the_worktree_is_not_the_defect(tmp_path):
    """A CRLF checkout is normal here; failing on it would fail the writing machine."""
    repo = _scratch_repo(tmp_path)
    (repo / "x.py").write_bytes(CRLF_BODY)

    assert cr_blobs(repo) == []


def test_a_carriage_return_already_committed_is_still_found(tmp_path):
    """Fixing the index does not un-commit it: `HEAD` is scanned too."""
    repo = _scratch_repo(tmp_path)
    _plant_cr_blob(repo)
    _git(repo, "commit", "-q", "-m", "a blob built around the filters")
    _git(repo, "add", "--renormalize", "x.py")     # the index is clean now …
    assert cr_blobs(repo) == []                    # … and the commit is not

    assert committed_cr(repo) == ["x.py"]


# ── 3. the remedy the message names, performed ───────────────────────────────

def test_the_remedy_the_message_names_actually_clears_it(tmp_path):
    repo = _scratch_repo(tmp_path)
    _plant_cr_blob(repo)
    assert cr_blobs(repo) == ["x.py"]

    done = _git(repo, "add", "--renormalize", "x.py")
    assert done.returncode == 0, f"git add --renormalize refused: {done.stderr.strip()}"
    assert cr_blobs(repo) == []


# ── 4. and it would have named the commit that did it ────────────────────────

def test_it_names_the_commit_that_caused_the_incident():
    """The sweep, pointed at the commit the box sat on, must name the file.

    Skipped where the object is absent (a shallow clone), because a guard that cannot
    be asked must not be read as one that passed.
    """
    if _git(ROOT, "cat-file", "-e", f"{INCIDENT_COMMIT}^{{commit}}").returncode != 0:
        pytest.skip(f"{INCIDENT_COMMIT} is not in this clone")

    assert INCIDENT_PATH in cr_blobs(ROOT, INCIDENT_COMMIT)


def test_it_refuses_to_answer_clean_when_it_cannot_be_asked(tmp_path):
    """`git` failing is not `git` finding nothing."""
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()

    with pytest.raises(AssertionError, match="could not be asked"):
        cr_blobs(not_a_repo)
