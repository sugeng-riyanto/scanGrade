"""Run the suite on a checkout whose own dirt would refuse to construct.

The problem this closes
-----------------------
`app/utils/checkout_integrity.py` refuses to construct the app when a checkout
holds more than `SCAN_LIMIT` (**64**) modified tracked paths. That refusal is
right for a box that serves: each path is measured in two git calls, so the scan
is bounded on a 1-vCPU box, and a tree with more uncommitted files than the bound
is not one a release merges over either — it says so instead of passing quietly.

It is wrong for a laptop where four features are in flight at once. The suite
cannot construct the app at all, so the very change that would prove "green"
cannot be measured — and until now the workaround was a hand-typed
`git worktree add` followed by a `cp -f` of the handful of files one feature
touched. That is different every time and easy to get subtly wrong: a new test
file left behind (so the guard quietly does not run), a deletion not carried, a
template copied but not its partial.

What this does
--------------
It materialises the **working tree** — tracked edits and untracked files alike —
as one commit in a linked scratch worktree. The tree the suite then measures is
clean by construction, so the cap is not reached. Nothing in the source checkout
is written: the edit is read, never applied. The scratch worktree lives where
this checkout's other scratch worktrees already live, under the gitignored
`.freebuff/`, so it does not itself become dirty paths.

Why it copies rather than stashes
---------------------------------
`git stash create` is the tempting one-liner and it is the wrong tool: it records
tracked modifications only, so an untracked test file — the exact file that would
run the new guard — is silently absent from the tree the suite then passes in.
This enumerates what a status would report (`git diff --no-renames --name-only -z
HEAD` for tracked paths, `git ls-files -z --others --exclude-standard` for new
ones), copies each, removes the ones the worktree deleted, and commits. Only
paths that differ are touched: the worktree already holds HEAD's own checkout, so
the copy is of the delta, not of the repository.

What travels, and what does not
-------------------------------
Tracked files (from the worktree's own checkout of HEAD) plus the tracked delta
plus un-ignored untracked files. Ignored files stay behind — `.env`, `.venv`,
`__pycache__`, the build output — because they are not the code under review, and
the suite already runs without them. `--run` uses this checkout's own interpreter
(its `.venv` when one is present), so the dependencies under test are still the
ones in this checkout.

One command
-----------
    python deploy/dirty_snapshot.py --run

snapshots the tree into `.freebuff/snapshot` and runs `pytest tests -q` there,
forwarding pytest's exit code. Without `--run` it only snapshots and prints the
path and the commit it made. `--clean` removes the scratch worktree and exits.

The refusal, because a snapshot that is not clean is worthless
--------------------------------------------------------------
After the commit the tool re-reads `git status --porcelain
--untracked-files=no` **in the snapshot** and refuses (exit 2) if anything is
still dirty — a fresh snapshot exists to be reproducible, and one that is not
would fail the very check it was made for, later and more confusingly. Exit codes
follow the other gates: 0 ok · 2 the snapshot could not be made or is not clean ·
pytest's own code when `--run` is used.
"""
from __future__ import annotations

import argparse
import stat
import os
import pathlib
import shlex
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: Where the scratch worktree lives. Inside `.freebuff/`, which `.gitignore`
#: already excludes and where this checkout's other scratch worktrees sit — so a
#: snapshot is never itself a dirty path in the tree it was taken from.
DEFAULT_DIR = pathlib.Path(".freebuff") / "snapshot"

#: A commit and a full `git add -A` over a large tree; slow, but not open-ended.
GIT_TIMEOUT = 300

#: The commit the snapshot makes is not meant to be authored by the person who
#: ran the tool — it is a machine capture, and it must not depend on (or read)
#: the local `user.name`/`user.email`, which may be unset on a fresh box.
COMMIT_NAME = "dirty-snapshot"
COMMIT_EMAIL = "dirty-snapshot@localhost"

#: pytest's arguments when `--run` is given and none are named.
DEFAULT_PYTEST = "tests -q"

#: The variables git sets to point a hook (and everything it spawns) at the
#: repository the hook is for. Cleared for every git this tool runs, so it
#: resolves the repository from its own `cwd` — `tests/unit/git_env.py` holds the
#: same list for the suite, and a linked worktree exporting an absolute
#: `GIT_DIR`/`GIT_INDEX_FILE` is exactly how a spawn ends up writing the wrong
#: index. Defined here rather than imported from the suite: deploy code does not
#: depend on tests, and this tool is also run from a hook.
GIT_REPO_ENV = (
    "GIT_DIR",
    "GIT_INDEX_FILE",
    "GIT_WORK_TREE",
    "GIT_PREFIX",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)


class SnapshotError(RuntimeError):
    """The snapshot cannot be made, or was made and is still dirty."""


# ── git ──────────────────────────────────────────────────────────────────────

def _git(args: list[str], *, cwd: pathlib.Path = ROOT, check: bool = True,
         timeout: int = GIT_TIMEOUT) -> subprocess.CompletedProcess:
    """Run git with the repository-selection variables scrubbed from the env."""
    env = dict(os.environ)
    for name in GIT_REPO_ENV:
        env.pop(name, None)
    try:
        done = subprocess.run(["git", *args], cwd=str(cwd), env=env,
                              capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SnapshotError(f"could not run git {' '.join(args)}: {exc}") from exc
    if check and done.returncode != 0:
        raise SnapshotError(
            f"git {' '.join(args)} failed ({done.returncode}): "
            f"{(done.stderr or done.stdout or '').strip()}")
    return done


def _require_checkout(root: pathlib.Path) -> None:
    """A path with no `.git` has no commit to snapshot *from* — refuse it."""
    if shutil.which("git") is None:
        raise SnapshotError("no git on PATH, so there is no worktree to make")
    if not (root / ".git").exists():
        raise SnapshotError(f"{root} is not a git checkout (no .git)")
    head = _git(["rev-parse", "--verify", "HEAD"], cwd=root, check=False)
    if head.returncode != 0:
        raise SnapshotError(f"{root} has no HEAD to snapshot from")


def _paths_to_copy(root: pathlib.Path) -> tuple[list[str], list[str]]:
    """(tracked deltas, untracked files), read **before** the snapshot exists.

    `git diff HEAD` is the whole delta against HEAD — staged and unstaged
    together — and `--no-renames` turns a rename into a delete plus an add so
    both halves are named and neither is missed. Untracked files come from
    `--exclude-standard`, which is what keeps a gitignored `.venv` (or the
    destination itself) out of the copy. Enumerated before the worktree is
    created so a destination inside the source cannot appear in its own listing.
    """
    raw = _git(["diff", "--no-renames", "--name-only", "-z", "HEAD"],
               cwd=root).stdout
    tracked = [p for p in raw.split("\0") if p]
    raw = _git(["ls-files", "--others", "--exclude-standard", "-z"],
               cwd=root).stdout
    untracked = [p for p in raw.split("\0") if p]
    return tracked, untracked


def _remove(path: pathlib.Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _place(src_root: pathlib.Path, dest_root: pathlib.Path, rel: str) -> None:
    """Copy one source path into the snapshot, or delete the one it replaced."""
    src = src_root / rel
    dest = dest_root / rel
    if not src.exists() and not src.is_symlink():
        if dest.exists() or dest.is_symlink():        # the worktree deleted it
            _remove(dest)
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.is_symlink():
        if dest.exists() or dest.is_symlink():
            _remove(dest)
        os.symlink(os.readlink(src), dest)
    elif src.is_dir():
        shutil.copytree(src, dest, dirs_exist_ok=True, symlinks=True)
    else:
        if dest.is_dir():                             # a file replaced a dir
            shutil.rmtree(dest)
        shutil.copy2(src, dest)


# ── the scratch worktree ─────────────────────────────────────────────────────

def _worktree_paths(root: pathlib.Path) -> list[pathlib.Path]:
    """Every worktree git currently has registered for this repository."""
    out = _git(["worktree", "list", "--porcelain"], cwd=root, check=False).stdout
    return [pathlib.Path(line[len("worktree "):].strip())
            for line in out.splitlines() if line.startswith("worktree ")]


def _is_our_worktree(root: pathlib.Path, dest: pathlib.Path) -> bool:
    return any(p.resolve() == dest.resolve() for p in _worktree_paths(root))


#: The dependency tree the gate needs and `--exclude-standard` leaves behind.
#:
#: `deploy/theme_gate.sh` runs the Tailwind CLI to prove the committed stylesheet is
#: the one these templates produce, and it cannot do that without `node_modules` — a
#: snapshot without one reports "could not measure", which is not a pass, so the
#: snapshot would silently stop checking the half of the gate that catches an
#: unbuilt class. It is *linked*, never copied: it is hundreds of megabytes, and the
#: suite under test is the checkout's own code.
LINKED_DIRS = ("node_modules",)


def _unlink_without_following(link: pathlib.Path) -> None:
    """Remove a junction or symlink and **nothing behind it**.

    This is the one function here that has already cost somebody something.
    `shutil.rmtree` on a Windows junction walks *into* the target and deletes the
    real directory's contents: cleaning a snapshot whose `node_modules` was a
    junction emptied the checkout's own `node_modules` — 74 packages, restored with
    `npm install`. A reparse point is a name, so it goes by unlinking the name, and
    `rmdir` is the fallback because a junction answers to it. Neither can follow.
    """
    for attempt in (os.unlink, os.rmdir):
        try:
            attempt(link)
            return
        except OSError:
            continue


def _is_junction(path: pathlib.Path) -> bool:
    """Is this a Windows directory junction?

    `os.path.islink` answers **no** for one — a junction is a reparse point of a
    different kind — which is exactly why `rmtree` walked through the one that
    emptied a checkout's `node_modules`.
    """
    checker = getattr(os.path, "isjunction", None)
    if checker is not None:                                     # Python 3.12+
        return bool(checker(path))
    if os.name != "nt":                                        # pragma: no cover
        return False
    try:
        return bool(os.stat(path, follow_symlinks=False).st_file_attributes
                    & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except OSError:                                             # pragma: no cover
        return False


def _make_dir_link(target: pathlib.Path, link: pathlib.Path) -> None:
    """Point `link` at the directory `target`, in this platform's spelling.

    A junction on Windows because creating a *symlink* there needs a privilege an
    ordinary shell does not have, and `cmd mklink /J` does what `npm install` and
    every IDE on the platform do. Raises rather than returning a code, so a caller
    cannot go on believing it linked something.
    """
    if os.name == "nt":
        done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                              capture_output=True, text=True)
        if done.returncode != 0:
            raise OSError(done.stdout + done.stderr)
    else:
        os.symlink(target, link)


def _link_ignored_dirs(root: pathlib.Path, dest: pathlib.Path, log=print) -> list[str]:
    """Link the gitignored directories the gates need into the snapshot.

    Only when the source has one: a checkout that never ran `npm install` gets a
    snapshot that says so honestly (the gate reports "could not measure") rather
    than a link to nothing.
    """
    made = []
    for name in LINKED_DIRS:
        src = root / name
        link = dest / name
        if not src.is_dir() or link.exists() or link.is_symlink():
            continue
        try:
            _make_dir_link(src, link)
        except OSError as exc:
            log(f"could not link {name} into the snapshot: {exc}")
            continue
        made.append(name)
    return made


def remove_snapshot(root: pathlib.Path, dest: pathlib.Path) -> None:
    """Drop the scratch worktree and its registration, whatever shape it is in.

    The links come out **first**, by name, before git is asked to remove anything.
    Measured: `git worktree remove --force` deletes untracked files itself, and a
    junction is an untracked directory to it — so unlinking afterwards was too late
    and the checkout's `node_modules` went with the snapshot anyway. Both halves of
    this function have now been the thing that emptied it.
    """
    for name in LINKED_DIRS:
        link = dest / name
        if link.is_symlink() or link.exists():
            _unlink_without_following(link)
    if _is_our_worktree(root, dest):
        _git(["worktree", "remove", "--force", str(dest)], cwd=root, check=False)
    # `worktree remove` can leave the directory behind (a locked file on Windows,
    # a nested worktree). It only ever lives under the configured scratch path,
    # so clearing it is ours to do — but only after git has had its say.
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    # A registration whose directory is gone is stale; prune it so the next add
    # does not refuse over it.
    _git(["worktree", "prune"], cwd=root, check=False)


def snapshot(root: pathlib.Path = ROOT, dest: pathlib.Path | str = DEFAULT_DIR, *,
             replace: bool = False, log=print) -> tuple[pathlib.Path, str | None]:
    """Copy the dirty working tree into a fresh, committed scratch worktree.

    Returns (snapshot path, the commit it made). The commit is None when the
    source was already clean, in which case the worktree is simply HEAD. The
    source checkout is only ever read.
    """
    root = pathlib.Path(root).resolve()
    dest = pathlib.Path(dest)
    dest = (dest if dest.is_absolute() else root / dest).resolve()
    if dest == root or dest in root.parents:
        raise SnapshotError(
            f"{dest} is the checkout or a directory above it, so a snapshot "
            f"there would clear the tree it is meant to copy")

    _require_checkout(root)

    # Enumerated before anything is created, so a destination nested in the
    # source cannot turn up in its own listing.
    tracked, untracked = _paths_to_copy(root)

    registered = _is_our_worktree(root, dest)
    if dest.exists() and not (registered or replace):
        raise SnapshotError(
            f"{dest} already exists and is not this repository's scratch "
            f"worktree — pass --replace to clear it, or --clean to remove it")
    if dest.exists():
        log(f"replacing the snapshot at {dest}")
        remove_snapshot(root, dest)

    _git(["worktree", "add", "--detach", "--quiet", str(dest), "HEAD"], cwd=root)
    for rel in tracked:
        _place(root, dest, rel)
    for rel in untracked:
        _place(root, dest, rel)

    _git(["add", "-A"], cwd=dest)
    staged = _git(["diff", "--cached", "--quiet"], cwd=dest, check=False)
    commit: str | None = None
    if staged.returncode != 0:
        _git(["-c", f"user.name={COMMIT_NAME}", "-c", f"user.email={COMMIT_EMAIL}",
              "commit", "--quiet", "--no-verify", "-m",
              f"snapshot of the working tree at {root.name}"], cwd=dest)
        commit = _git(["rev-parse", "--short", "HEAD"], cwd=dest).stdout.strip()

    leftovers = _git(["status", "--porcelain", "--untracked-files=no"],
                     cwd=dest, check=False).stdout.strip()
    if leftovers:
        raise SnapshotError(
            f"the snapshot at {dest} is still dirty after committing, so it "
            f"cannot stand in for a clean checkout:\n"
            + "\n".join(f"    {line}" for line in leftovers.splitlines()))
    # After the clean check, deliberately: the links are gitignored, and a link is
    # not a dirty path — but creating them before the check would put something in
    # the tree that `git status` is being asked about.
    _link_ignored_dirs(root, dest, log=log)
    return dest, commit


# ── running the suite ────────────────────────────────────────────────────────

def runner_python(root: pathlib.Path = ROOT) -> str:
    """This checkout's interpreter — its `.venv` when one is present.

    The snapshot carries no `.venv` (it is gitignored), so the suite has to be
    run by the checkout's own interpreter rather than one the snapshot lacks.
    """
    for rel in ("Scripts/python.exe", "bin/python"):
        candidate = root / ".venv" / rel
        if candidate.exists():
            return str(candidate)
    return sys.executable


def _run(root: pathlib.Path, dest: pathlib.Path, pytest_args: str) -> int:
    args = shlex.split(pytest_args) or ["tests", "-q"]
    cmd = [runner_python(root), "-m", "pytest", *args]
    env = dict(os.environ)
    for name in GIT_REPO_ENV:
        env.pop(name, None)
    # The snapshot's own root first, so `app` and `tests` resolve to the copy.
    env["PYTHONPATH"] = os.pathsep.join(
        [str(dest), env["PYTHONPATH"]]) if env.get("PYTHONPATH") else str(dest)
    print(f"$ cd {dest} && {shlex.join(cmd)}")
    try:
        return subprocess.call(cmd, cwd=str(dest), env=env)
    except KeyboardInterrupt:                          # pragma: no cover
        return 130


# ── the command ──────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dirty_snapshot.py",
        description="Snapshot the working tree into a committed scratch worktree "
                    "so the suite can run over the dirty-file cap.")
    parser.add_argument("--dir", default=str(DEFAULT_DIR),
                        help=f"where the scratch worktree lives (default {DEFAULT_DIR})")
    parser.add_argument("--replace", action="store_true",
                        help="clear an existing snapshot directory before a fresh one")
    parser.add_argument("--clean", action="store_true",
                        help="remove the scratch worktree and exit")
    parser.add_argument("--run", action="store_true",
                        help="run the suite in the snapshot after making it")
    parser.add_argument("--pytest", default=DEFAULT_PYTEST,
                        help=f"arguments for the suite when --run is used "
                             f"(default '{DEFAULT_PYTEST}')")
    args = parser.parse_args(argv)

    root = ROOT
    dest = pathlib.Path(args.dir)
    dest = (dest if dest.is_absolute() else root / dest).resolve()

    try:
        if args.clean:
            remove_snapshot(root, dest)
            print(f"removed {dest}")
            return 0
        path, commit = snapshot(root, dest, replace=args.replace)
    except SnapshotError as exc:
        print(f"dirty_snapshot: {exc}", file=sys.stderr)
        return 2

    where = f"at {commit}" if commit else "clean, nothing to commit — the tree is HEAD"
    # Flushed, so this line is not buried behind the suite's own output when the
    # whole command is piped (`... --run --pytest ... | tail`).
    print(f"snapshot {path} {where}", flush=True)
    if args.run:
        return _run(root, path, args.pytest)
    print(f"to run the suite:  cd {path} && "
          f"{runner_python(root)} -m pytest {DEFAULT_PYTEST}")
    return 0


if __name__ == "__main__":                             # pragma: no cover
    sys.exit(main())
