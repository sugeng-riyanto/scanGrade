#!/usr/bin/env python3
"""Carry one file into one or more checkouts, and prove each checkout still holds.

This repository is worked in sibling checkouts, and a change made in one is routinely
carried to the others. The carrying used to be a script per change: read the target,
assert the text it was about to replace was still there, write, then run the tests that
checkout owns. Every such script was slightly different, and the difference that
mattered was never the write — it was the assertion. A script that writes when its
anchor moved two lines is how a change lands somewhere it was never reviewed, and a
script whose anchor is `str.replace` on a missing needle writes the file back unchanged
and reports success.

So the refusal is the tool, and it is stated as the target's own bytes:

    python deploy/port_change.py app/services/foo.py \\
        --from ../seb4 --to ../key-order --to ../sgwt --base git:HEAD

The **declared base** is required and has three forms, because the checkouts this is
used across are not one repository at one revision:

    git:<rev>      that revision's bytes for this path *in each target checkout*; `HEAD`
                   therefore means "this target is unmodified since its own commit",
                   asked separately of every checkout rather than once for all of them
    sha256:<hex>   a digest, for a base whose bytes are not on this box
    <path>         a file's bytes — the shape a cross-checkout port needs, where no two
                   checkouts share a revision and the "before" copy is the artefact

There is deliberately **no default**. A base inferred from the target's current content
would make every write legal, which is the one thing this tool exists to prevent; a base
inferred from the source's own history would be a guess about which commit in another
branch corresponds to this one.

What happens to each target, in this order:

  1. **Refuse or admit.** The target's bytes are compared to the declared base, byte for
     byte — no newline folding, because a CRLF-only drift is a target somebody else has
     touched and the suite is about to run against it. A refusal states what the target
     holds instead (size and digest) and, when both sides' content is known, the first
     line that differs with both versions quoted.
  2. **Check the numbers the port would carry.** A Markdown line is the one kind of
     copy that arrives true and is false in the same commit: "(31 tests)" and "11/11
     injected defects caught" were measured in the checkout the sentence was written
     in, and both move with the code around them. The lines this port **adds** are read
     for those two shapes and every number is measured in the target itself — a test
     module is *collected* (no fixture runs) and a harness is *run* — and a claim that
     differs, or names a file this checkout does not have, or cannot be measured at
     all (a module that will not import, a harness that printed no score), refuses the
     write before it happens. `--claims auto` reads Markdown only, `off` says out loud
     that the run wrote without looking, and `--claim-limit` bounds the work per
     checkout.
  3. **Write, with a backup.** The base bytes are copied under the backup directory
     before anything is replaced, and the manifest entry is written as the copy is made,
     so a run killed between the two leaves a restorable file rather than a memory of
     one. The write is atomic (a temp file in the target's own directory, then
     `os.replace`) and keeps the original's mode bits — a ported `deploy/*.sh` that lost
     its executable bit is a release gate that cannot run.
  4. **Run that checkout's checks.** The suite defaults to the whole of that checkout's
     `tests/`, and the mutation harnesses default to the ones **whose `GUARDS` name this
     file**, read from the checkout's own `deploy/mutation_check.py --list` — the
     registry is the checkout's, not this tool's, so a worktree with no harness for the
     file reports "none" instead of running somebody else's. `--suite` and `--mutation`
     override either half, and `--no-suite` / `--no-mutation` are how a run says out loud
     that it did not look.
  5. **Report per checkout.** A table of base, write, suite, mutation and claims, then the
     refusals and side effects in full. Side effects are the paths the *checks* dirtied
     that this tool did not write (`git status` before against after), so a gate that
     rewrites a baseline during the run is visible rather than becoming tomorrow's
     unexplained diff.

Exit codes follow the rest of `deploy/`: **0** every target ported or already identical
and every check passed; **1** the port landed but a check found something (a red suite, a
mutation survivor, a stale harness anchor); **2** a target was refused, nothing could be
measured, or the checks could not run — never a silent zero for a run that did not
happen. A refusal on one checkout does not block the others: each is judged on its own
bytes, and the table says which.

Restore the whole run with the directory the report named:

    python deploy/port_change.py --restore ~/.scangrade-port/backups/20261017T101112-ab12

A restore refuses a file whose bytes changed after the port, because the alternative is
a tool that undoes work it never wrote; `--force` is the explicit answer to that.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import posixpath
import re
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKUP_ROOT = Path.home() / ".scangrade-port" / "backups"
TOOL = "port_change"

PASS, FINDING, UNMEASURED = 0, 1, 2


class Usage(Exception):
    """An argument this tool cannot act on, reported as exit 2 and nothing written."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def short(digest: str) -> str:
    return digest[:12]


def human_bytes(count: int) -> str:
    return f"{count:,} bytes"


def clip(text: str, width: int = 150) -> str:
    text = text.rstrip("\n")
    return text if len(text) <= width else text[: width - 3] + "..."


def last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return clip(lines[-1]) if lines else "no output"


def relposix(value: str, *, what: str) -> str:
    """A checkout-relative path, refusing the shapes that leave the checkout.

    A port is a statement about a file *inside* each checkout; an absolute path or one
    that climbs out with `..` would make the backup, the report and the base comparison
    describe three different files.
    """
    raw = value.replace("\\", "/")
    if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        raise Usage(f"{what} must be relative to the checkout, not {value!r}")
    clean = posixpath.normpath(raw)
    if clean in ("", "."):
        raise Usage(f"{what} names the checkout itself, not a file: {value!r}")
    if clean == ".." or clean.startswith("../"):
        raise Usage(f"{what} must not climb out of the checkout: {value!r}")
    return clean


def first_difference(current: bytes, base: bytes) -> list[str]:
    """Where two versions part company, in lines, for a refusal worth reading."""
    left = current.splitlines()
    right = base.splitlines()
    index = 0
    for index, (mine, theirs) in enumerate(zip(left, right), start=1):
        if mine != theirs:
            break
    else:
        index = min(len(left), len(right)) + 1
    findings = [f"first difference at line {index}"]
    if index <= len(left):
        findings.append(f"  target line {index}: {clip(left[index - 1].decode('utf-8', 'replace'))}")
    else:
        findings.append(f"  target ends after line {len(left)}")
    if index <= len(right):
        findings.append(f"  base   line {index}: {clip(right[index - 1].decode('utf-8', 'replace'))}")
    else:
        findings.append(f"  base ends after line {len(right)}")
    return findings


@dataclass
class Base:
    """The declared base, resolved once; per-checkout forms resolve again later."""

    spec: str
    kind: str
    rev: str = ""
    content: bytes | None = None
    digest: str = ""

    @property
    def label(self) -> str:
        return self.spec


def parse_base(spec: str, cwd: Path) -> Base:
    if spec.startswith("git:"):
        rev = spec[len("git:"):].strip()
        if not rev:
            raise Usage("--base git: needs a revision, e.g. git:HEAD")
        return Base(spec=spec, kind="git", rev=rev)
    if spec.startswith("sha256:"):
        digest = spec[len("sha256:"):].strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise Usage("--base sha256: needs the full 64-character hex digest")
        return Base(spec=spec, kind="digest", digest=digest)
    path = Path(spec)
    if not path.is_absolute():
        path = cwd / path
    if not path.is_file():
        raise Usage(f"--base names no revision form and no such file: {spec!r} "
                    "(use git:<rev>, sha256:<hex>, or a path)")
    data = path.read_bytes()
    return Base(spec=spec, kind="file", content=data, digest=sha256(data))


def base_bytes_for(base: Base, checkout: Path, relpath: str) -> tuple[bytes | None, str]:
    """The base's content for one checkout, or why it could not be read."""
    if base.kind != "git":
        return base.content, ""
    try:
        proc = subprocess.run(["git", "-C", str(checkout), "show", f"{base.rev}:{relpath}"],
                              capture_output=True)
    except OSError as exc:
        return None, f"`git show` could not run: {exc.strerror or exc}"
    if proc.returncode != 0:
        reason = last_line(proc.stderr.decode("utf-8", "replace"))
        return None, f"`git show {base.rev}:{relpath}` failed in {checkout.name}: {reason}"
    return proc.stdout, ""


class Backup:
    """The copy of every byte this run replaced, and the manifest that finds it.

    The directory is created on the first copy and not before, so a refused or dry run
    leaves nothing behind that could later be mistaken for a restore point. The manifest
    is rewritten on every copy: a run that dies mid-port has a manifest that names what
    it had already replaced, which is the only moment the backup is worth anything.
    """

    def __init__(self, root: Path, stamp: str):
        self.dir = root / stamp
        self.entries: list[dict] = []
        self.header: dict = {}

    def started(self) -> bool:
        return bool(self.entries)

    def add(self, target: "Target", before: bytes) -> str:
        self.dir.mkdir(parents=True, exist_ok=True)
        index = len(self.entries) + 1
        folder = f"{index:02d}-{re.sub(r'[^A-Za-z0-9_.-]+', '-', target.checkout.name) or 'checkout'}"
        destination = self.dir / folder / target.relpath
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(before)
        entry = {
            "checkout": str(target.checkout),
            "path": str(target.path),
            "relpath": target.relpath,
            "backup": destination.relative_to(self.dir).as_posix(),
            "before_sha256": sha256(before),
            "after_sha256": "",
            "mode": target.mode(),
        }
        self.entries.append(entry)
        target.backup = entry["backup"]
        self.write()
        return entry["backup"]

    def write(self) -> None:
        if not self.entries:
            return
        manifest = dict(self.header)
        manifest["tool"] = TOOL
        manifest["targets"] = self.entries
        (self.dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


@dataclass
class Target:
    checkout: Path
    relpath: str
    state: str = "pending"
    base_state: str = ""
    reason: str = ""
    details: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    before_sha256: str = ""
    after_sha256: str = ""
    backup: str = ""
    suite: dict | None = None
    mutation: list[dict] = field(default_factory=list)
    claims: dict | None = None
    side_effects: list[str] = field(default_factory=list)

    @property
    def path(self) -> Path:
        return self.checkout / self.relpath

    def mode(self) -> int:
        try:
            return stat.S_IMODE(self.path.stat().st_mode)
        except OSError:
            return 0o644

    def refusal(self, reason: str, details: list[str] | None = None) -> None:
        self.state = "refused"
        self.reason = reason
        if details:
            self.details.extend(details)


def run(cmd: list[str], cwd: Path, timeout: float) -> tuple[int | None, str, float]:
    """One check, with the child's output made decodable rather than assumed.

    `mutation_check.py --list` separates a harness from its description with an em dash,
    and on a Windows box a piped child encodes that as cp1252 — one byte that is not
    UTF-8, so the listing would decode to a replacement character and the registry would
    silently read as empty (no harness discovered, nothing run, a green report). The
    child is asked for UTF-8 stdio instead, which leaves its file writes alone and makes
    the parse an observation rather than a guess. The decode below stays lossy because a
    check that prints something undecodable is still a check whose exit code means
    something.
    """
    started = time.monotonic()
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, timeout=timeout,
                              env=env)
    except OSError as exc:
        # A missing interpreter is the obvious case, and not the only one: a checkout
        # whose `.venv` was built under WSL hands over a POSIX shim that Windows answers
        # with "not a valid Win32 application", and an interpreter nobody can execute
        # must read as a check that could not run rather than as a crash in this tool.
        return None, f"cannot run {cmd[0]!r}: {exc.strerror or exc}", time.monotonic() - started
    except subprocess.TimeoutExpired:
        return None, f"timed out after {timeout:g}s", time.monotonic() - started
    text = (proc.stdout + proc.stderr).decode("utf-8", "replace")
    return proc.returncode, text, time.monotonic() - started


def interpreter(checkout: Path, override: str | None) -> str:
    """This checkout's interpreter, in the layout **this** platform can execute.

    Both layouts exist on this box — a venv built under WSL has `bin/python`, which
    Windows refuses to spawn — so the platform picks the candidate instead of the first
    one that happens to be on disk, and a shim is never handed to `subprocess`.
    """
    if override:
        return override
    layouts = ((".venv/Scripts/python.exe",) if os.name == "nt" else (".venv/bin/python",))
    for rel in layouts:
        candidate = checkout / rel
        if candidate.is_file():
            return str(candidate)
    return sys.executable or "python"


def check_suite(target: Target, python: str, paths: list[str], extra: list[str],
                timeout: float) -> dict:
    """The checkout's suite, with pytest's own codes read rather than flattened.

    Only `0` is a pass. `5` (no tests collected) and `2`/`3`/`4` (interrupted, internal
    error, usage) are "could not measure" — a port verified against a run that collected
    nothing has been verified against nothing, and reporting that as green is the failure
    this whole file is about.
    """
    cmd = [python, "-m", "pytest", *paths, "-q", "-p", "no:cacheprovider",
           "--no-header", *extra]
    code, text, seconds = run(cmd, target.checkout, timeout)
    if code is None:
        state, summary = "unmeasured", clip(text)
    elif code == 0:
        state, summary = "pass", last_line(text)
    elif code == 1:
        state, summary = "fail", last_line(text)
    else:
        state, summary = "unmeasured", f"pytest exited {code}: {last_line(text)}"
    return {"kind": "suite", "state": state, "summary": summary,
            "seconds": round(seconds, 1), "cmd": cmd}


HARNESS_HEAD = re.compile(r"^(\S+)\s+(?:—|–|-)\s")
HARNESS_GUARDS = re.compile(r"guard file\(s\): (.+?)\s*$")
HARNESS_SCORE = re.compile(r"(\d+)\s*/\s*(\d+)\s+(?:injected defects|mutations)\s+caught")


def parse_harnesses(text: str) -> dict[str, list[str]]:
    """`--list` as a registry: harness name to the files it guards.

    `deploy/mutation_check.py` is the registry, and this reads its own listing rather
    than the harness modules, because loading another checkout's module here would run
    its imports against *this* checkout's `sys.path` — which is how a "read" turns into
    the wrong app being imported. The two lines parsed are that tool's own format, which
    is why a change there has to keep them: `tests/unit/test_port_change.py` holds a
    shim to the same shape, so a format change fails a test rather than silently
    discovering no harnesses.
    """
    found: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        head = HARNESS_HEAD.match(line)
        if head:
            current = head.group(1)
            found.setdefault(current, [])
            continue
        guards = HARNESS_GUARDS.search(line)
        if guards and current:
            found[current] = [item.strip() for item in guards.group(1).split(",")
                              if item.strip()]
    return found


def discover_harnesses(target: Target, python: str, tool: Path,
                       timeout: float) -> tuple[list[str], str, bool]:
    """(names, note, broken) — a registry that will not answer is not a registry."""
    code, text, _ = run([python, str(tool), "--list"], target.checkout, timeout)
    if code != 0:
        return [], f"`mutation_check.py --list` exited {code}: {last_line(text)}", True
    found = parse_harnesses(text)
    matching = [name for name, guards in sorted(found.items())
                if target.relpath in guards]
    return (matching, "" if matching else f"no harness in this checkout guards {target.relpath}",
            False)


def convention_harness(target: Target) -> Path | None:
    """`.freebuff/mutate_<stem>.py`, the naming a checkout without a registry uses.

    Older branches of this repository have no `tests/mutation/` registry and keep their
    harnesses in `.freebuff/`, where the name is the feature rather than the file. Only
    an exact stem match is accepted, because the point of that fallback is a harness for
    *this* file — anything looser would run a harness about something else and call the
    result a verification.
    """
    stem = Path(target.relpath).stem
    candidate = target.checkout / ".freebuff" / f"mutate_{stem}.py"
    return candidate if candidate.is_file() else None


def mutation_plan(target: Target, python: str, declared: list[str], timeout: float,
                  skip: bool) -> tuple[list[dict], str]:
    """Which harnesses to run, and why nothing ran when nothing did.

    Declared names win. Otherwise the checkout's own registry decides — the harnesses
    whose `GUARDS` name this file — and only when that registry is absent does the
    `.freebuff/` naming convention get a look. A checkout with neither is reported as
    having run none, which is the one thing a report must never leave implied.
    """
    if skip:
        return [], ""
    tool = target.checkout / "deploy" / "mutation_check.py"
    if declared:
        plan = []
        for name in declared:
            if tool.is_file():
                plan.append({"name": name, "cmd": [python, str(tool), "--run", name]})
            else:
                plan.append({"name": name, "cmd": None,
                             "note": "this checkout has no deploy/mutation_check.py"})
        return plan, ""
    note = ""
    if tool.is_file():
        names, note, broken = discover_harnesses(target, python, tool, timeout)
        if names:
            return [{"name": name, "cmd": [python, str(tool), "--run", name]}
                    for name in names], ""
        if broken:
            return [{"name": "discovery", "cmd": None, "note": note}], note
    else:
        note = "this checkout has no deploy/mutation_check.py"
    fallback = convention_harness(target)
    if fallback is not None:
        return ([{"name": fallback.name, "cmd": [python, str(fallback)]}],
                f"{fallback.name} found by naming convention ({note or 'no registry'})")
    return [], note


def check_mutation(target: Target, entry: dict, timeout: float) -> dict:
    name, cmd = entry["name"], entry["cmd"]
    if cmd is None:
        return {"kind": "mutation", "name": name, "state": "unmeasured",
                "summary": entry.get("note", "no command"), "seconds": 0.0}
    code, text, seconds = run(cmd, target.checkout, timeout)
    score = HARNESS_SCORE.search(text)
    if code is None:
        state, summary = "unmeasured", clip(text)
    elif code == 0:
        state = "caught"
        summary = f"{score.group(1)}/{score.group(2)} caught" if score else "caught all"
    elif "not injected" in text:
        state, summary = "stale", "an anchor no longer matches - the harness did not inject"
    elif code == 1:
        state = "survivor"
        summary = f"{score.group(1)}/{score.group(2)} caught" if score else "a mutation survived"
    else:
        state, summary = "unmeasured", f"exited {code}: {last_line(text)}"
    return {"kind": "mutation", "name": name, "state": state, "summary": summary,
            "seconds": round(seconds, 1)}


#: The shapes this repository writes a measurement in, and the only ones read here.
#: A count with no subject to check it against is not a claim this tool can verify, and
#: guessing at prose produces refusals nobody can act on. The subject of a test **count**
#: is a module pytest would collect — under `tests/`, or named `test_*.py` — and not a
#: tool: `deploy/port_change.py` is not a suite, so a count written beside it collects
#: nothing and refusing the port over that would be a guard arguing with its own prose.
TEST_PATH = re.compile(
    r"(?<![\w./-])((?:tests/[\w./-]+|test_[\w]+)\.py)")
TEST_COUNT = re.compile(r"\((\d+)\s+tests?\b")
TEST_COUNT_BARE = re.compile(r"[`'\"]?\s*\((\d+)(?:\s*[—–-][^)]*)?\)")
MUTATION_PATH = re.compile(r"((?:\.freebuff|tests/mutation)/mutate_[\w./-]+\.py)")
MUTATION_CONFIRMED = re.compile(
    r"(\d+)\s*/\s*(\d+)\s*(?:injected defects caught|mutations caught|caught"
    r"|cacat yang disuntikkan tertangkap|tertangkap)", re.IGNORECASE)
HARNESS_TALLY = re.compile(r"(\d+)\s+caught,\s*(\d+)\s+survived(?:,\s*(\d+)\s+harness error)?")


def added_lines(current: bytes, new: bytes) -> list[str]:
    """The lines this port adds or changes, which are the lines it is claiming.

    The scope matters more than the parse. A bullet already in the target is not this
    run's sentence, and refusing a port over a number somebody else wrote is how a check
    becomes noise; only the lines the port brings are read. `current` is the admitted
    target's own bytes, so this is exact for every base form — a digest-only base still
    hands over the content the digest matched.
    """
    fresh = new.decode("utf-8", "replace").splitlines()
    old = current.decode("utf-8", "replace").splitlines()
    return [line[1:] for line in difflib.unified_diff(old, fresh, lineterm="", n=0)
            if line.startswith("+") and not line.startswith("+++")]


def _nearest(subjects: list[tuple[int, int, str]], at: int,
             reach: int = 220, follows: bool = False) -> str | None:
    """The subject a number is about, measured to its text rather than its start.

    Distance to the *span* is the property that matters: a count written after a path
    is closest to that path's end, while the next path's start can be nearer to the
    count than the previous path's start is — which is exactly how `a.py` (17), `b.py`
    (15) had 17 read against `b.py` when this measured starts.

    `follows` is the other half of the same reading: in the shapes this repository
    writes, a count **comes after** the module it counts, so a count with no preceding
    subject is not a claim about the path further down the line. That is what keeps an
    illustrative `(32 tests)` in a sentence about shapes from being read as a claim
    about whichever file the sentence mentions next. Under it the distance is simply
    `at - end` — every candidate ends before the count by construction — so there is no
    second branch here to disagree with this one.
    """
    if follows:
        subjects = [item for item in subjects if item[1] <= at]
    if not subjects:
        return None

    def distance(item: tuple[int, int, str]) -> int:
        start, end, _subject = item
        if follows:
            return at - end
        if start <= at <= end:
            return 0
        return min(abs(at - start), abs(at - end))

    best = min(subjects, key=distance)
    return best[2] if distance(best) <= reach else None


def claims_in(lines: list[str]) -> list[dict]:
    """The measurements a set of lines asserts, each with the file it is about.

    Two shapes, because they are the two this repository writes: a test module with the
    number of tests in it (`(31 tests)`, or `(31)` directly after the path), and a
    harness with its score (`11/11 injected defects caught`, or the Indonesian the
    refusal-clocking note uses). The number is bound to the **nearest** subject in the
    line rather than the next one, so a bullet naming three suites and their counts
    cannot have the second count read against the first path.
    """
    found: list[dict] = []
    seen: set[tuple] = set()
    for line in lines:
        if not line.strip():
            continue
        tests = [(match.start(1), match.end(1), match.group(1))
                 for match in TEST_PATH.finditer(line)
                 if not MUTATION_PATH.fullmatch(match.group(1))]
        counts: list[tuple[int, int]] = [(match.start(0), int(match.group(1)))
                                         for match in TEST_COUNT.finditer(line)]
        for _start, end, subject in tests:
            bare = TEST_COUNT_BARE.match(line, end)
            if bare:
                counts.append((bare.start(0), int(bare.group(1))))
        for at, count in counts:
            subject = _nearest(tests, at, follows=True)
            if subject is None:
                continue
            key = ("tests", subject, count)
            if key in seen:
                continue
            seen.add(key)
            found.append({"kind": "tests", "subject": subject, "claimed": count,
                          "line": clip(line, 120)})
        harnesses = [(match.start(1), match.end(1), match.group(1))
                     for match in MUTATION_PATH.finditer(line)]
        for match in MUTATION_CONFIRMED.finditer(line):
            subject = _nearest(harnesses, match.start(0))
            if subject is None:
                continue
            claimed = [int(match.group(1)), int(match.group(2))]
            key = ("score", subject, tuple(claimed))
            if key in seen:
                continue
            seen.add(key)
            found.append({"kind": "score", "subject": subject, "claimed": claimed,
                          "line": clip(line, 120)})
    return found


def resolve_test(target: Target, subject: str) -> tuple[str | None, str]:
    """Where a claimed test module is here, or why it cannot be named.

    A bullet names its first suite in full and the rest by basename (`tests/unit/a.py`
    (17), `b.py` (15)), so a basename has to be resolvable — but only when it resolves
    to exactly one file. Two candidates means the claim cannot be attributed to either,
    and picking one would answer a question nobody asked with a number that happens to
    be handy.
    """
    if "/" in subject:
        if (target.checkout / subject).is_file():
            return subject, ""
        return None, "this checkout has no such test file"
    matches = sorted(path for path in target.checkout.glob(f"tests/**/{subject}")
                     if path.is_file())
    if len(matches) == 1:
        return matches[0].relative_to(target.checkout).as_posix(), ""
    if not matches:
        return None, "this checkout has no such test file"
    places = ", ".join(path.relative_to(target.checkout).as_posix()
                       for path in matches[:3])
    return None, f"{subject} is not one file here ({places}) so it cannot be attributed"


def collected_count(target: Target, python: str, subject: str,
                    timeout: float) -> tuple[int | None, str]:
    """How many tests the target's own copy of that module holds — collected, not run.

    Collection is the reading the claim is about ("27 tests" is how many the file has),
    and it is the cheap one: the module is imported and its test functions counted, and
    no fixture runs. pytest's exit codes are read rather than flattened: a path that is
    not there, or a module that will not import, is **unknown**, not zero.
    """
    resolved, note = resolve_test(target, subject)
    if resolved is None:
        return None, note
    code, text, _ = run([python, "-m", "pytest", resolved, "--collect-only", "-q",
                         "-p", "no:cacheprovider"], target.checkout, timeout)
    if code is None:
        return None, clip(text)
    summary = re.search(r"(\d+)\s+tests?\s+collected", text)
    if summary:
        return int(summary.group(1)), ""
    if "no tests collected" in text and re.search(r"\b\d+ error", text) is None:
        return 0, ""
    return None, f"pytest exited {code}: {last_line(text)}"


def harness_command(checkout: Path, python: str, subject: str) -> list[str] | None:
    """How this checkout runs the harness a bullet names, if it has it."""
    path = checkout / subject
    if not path.is_file():
        return None
    if subject.startswith("tests/mutation/"):
        tool = checkout / "deploy" / "mutation_check.py"
        if tool.is_file():
            return [python, str(tool), "--run", Path(subject).stem]
    return [python, str(path)]


def harness_score(target: Target, python: str, subject: str,
                  timeout: float) -> tuple[list[int] | None, str]:
    """The harness's own score there, in the two shapes a harness prints it."""
    command = harness_command(target.checkout, python, subject)
    if command is None:
        return None, "this checkout has no such harness"
    code, text, _ = run(command, target.checkout, timeout)
    if code is None:
        return None, clip(text)
    score = HARNESS_SCORE.search(text) or HARNESS_TALLY.search(text)
    if score is None:
        return None, f"exited {code}: {last_line(text)}"
    if score.re is HARNESS_SCORE:
        return [int(score.group(1)), int(score.group(2))], ""
    caught, survived = int(score.group(1)), int(score.group(2))
    errors = int(score.group(3) or 0)
    return [caught, caught + survived + errors], ""


def verify_claims(target: Target, python: str, claims: list[dict], limit: int,
                  timeout: float) -> dict:
    """Run this checkout's own measurement for every number the port would add."""
    items: list[dict] = []
    cache: dict[tuple, tuple] = {}
    held = broken = unknown = 0
    for index, claim in enumerate(claims):
        item = dict(claim)
        if limit and index >= limit:
            item.update(state="skipped", actual="", note=f"not checked (limit {limit})")
            items.append(item)
            unknown += 1
            continue
        key = (claim["kind"], claim["subject"])
        if key not in cache:
            if claim["kind"] == "tests":
                cache[key] = collected_count(target, python, claim["subject"], timeout)
            else:
                cache[key] = harness_score(target, python, claim["subject"], timeout)
        actual, note = cache[key]
        item["actual"] = "" if actual is None else actual
        item["note"] = note
        if actual is None:
            item["state"] = ("missing" if note.startswith("this checkout has no")
                             else "unmeasured")
        else:
            wanted = claim["claimed"] if isinstance(claim["claimed"], list) else [claim["claimed"]]
            got = actual if isinstance(actual, list) else [actual]
            item["state"] = "ok" if got == wanted else "differs"
        if item["state"] == "ok":
            held += 1
        elif item["state"] in ("differs", "missing"):
            broken += 1
        else:
            unknown += 1
        items.append(item)
    state = "fail" if broken else ("unmeasured" if unknown else "pass")
    summary = f"{held} of {len(items)} hold"
    if broken:
        summary += f", {broken} do not"
    if unknown:
        summary += f", {unknown} not checked"
    return {"kind": "claims", "state": state, "summary": summary, "items": items}


def describe_mutation(item: dict) -> str:
    """A harness result compact enough for the table; the score is the part that matters."""
    summary = item.get("summary") or ""
    if item["state"] in ("caught", "survivor") and re.match(r"\d+/\d+", summary):
        return f"{item['name']}: {item['state']} {summary.split(' ', 1)[0]}"
    return f"{item['name']}: {item['state']} ({summary})"


def describe_claim(item: dict) -> str:
    """One claim and the two numbers, in the words a refusal and a report share."""
    claimed = item["claimed"]
    actual = item.get("actual")
    if item["kind"] == "score":
        wanted = f"claims {claimed[0]}/{claimed[1]} caught"
        if item["state"] == "differs" and isinstance(actual, list) and len(actual) == 2:
            return f"{item['subject']} {wanted}, this checkout reports {actual[0]}/{actual[1]}"
        if item["state"] == "missing":
            return f"{item['subject']} {wanted}, and this checkout has no such harness"
        return f"{item['subject']} {wanted} - {item.get('note') or item['state']}"
    wanted = f"claims {claimed} tests"
    if item["state"] == "differs":
        return f"{item['subject']} {wanted}, this checkout collects {actual}"
    if item["state"] == "missing":
        return f"{item['subject']} {wanted}, and this checkout has no such test file"
    return f"{item['subject']} {wanted} - {item.get('note') or item['state']}"


def claims_wanted(mode: str, relpath: str) -> bool:
    """Whether a port of this file is a sentence whose numbers are worth checking."""
    if mode == "off":
        return False
    if mode == "on":
        return True
    name = Path(relpath).name.lower()
    return name.endswith(".md") or name == "agents"


def git_dirt(checkout: Path) -> set[str] | None:
    """The checkout's dirty paths, or None when it is not a git worktree."""
    try:
        proc = subprocess.run(["git", "-C", str(checkout), "status", "--porcelain"],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace")
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return {line.rstrip() for line in proc.stdout.splitlines() if line.strip()}


def side_effects(before: set[str] | None, after: set[str] | None, relpath: str) -> list[str]:
    if before is None or after is None:
        return []
    return sorted(line for line in (after - before) if relpath not in line)


def write_port(target: Target, new: bytes) -> None:
    """Replace the file atomically, keeping its mode, and verify what landed."""
    before = target.path.read_bytes()
    mode = target.mode()
    handle, staged = tempfile.mkstemp(prefix=".port-", dir=str(target.path.parent))
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(new)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(staged, mode)
        os.replace(staged, target.path)
    except BaseException:
        Path(staged).unlink(missing_ok=True)
        raise
    landed = target.path.read_bytes()
    if landed != new:
        raise OSError(f"wrote {human_bytes(len(new))} but read back {human_bytes(len(landed))}")


def refused_table(targets: list[Target]) -> int:
    return sum(1 for target in targets if target.state == "refused")


def verdict(targets: list[Target], dry_run: bool) -> int:
    if any(target.state in ("refused", "failed") for target in targets):
        return UNMEASURED
    if dry_run:
        return PASS
    checks = [check for target in targets for check in
              ([target.suite] if target.suite else []) + target.mutation]
    if any(check["state"] == "unmeasured" for check in checks):
        return UNMEASURED
    if any(check["state"] in ("fail", "survivor", "stale") for check in checks):
        return FINDING
    return PASS


def verdict_word(code: int, targets: list[Target], dry_run: bool) -> str:
    if dry_run:
        refused = refused_table(targets)
        if refused:
            return f"dry run - {refused} of {len(targets)} target(s) would be refused"
        return "dry run - every target matches the declared base, nothing written or run"
    if code == UNMEASURED:
        if refused_table(targets):
            return f"refused ({refused_table(targets)} of {len(targets)} target(s))"
        return "not fully checked"
    if code == FINDING:
        return "ported, but a check found something"
    written = sum(1 for target in targets if target.state == "written")
    already = sum(1 for target in targets if target.state == "already")
    return f"ported ({written} written, {already} already identical)"


def render(report: dict) -> None:
    targets: list[Target] = report["_targets"]
    print(f"port_change: {report['file']} -> {len(targets)} checkout(s)")
    print(f"  source  {report['source']}  {human_bytes(report['source_bytes'])}  "
          f"sha256 {short(report['source_sha256'])}")
    base_line = f"  base    {report['base']} (declared)"
    if report.get("base_bytes") is not None:
        base_line += (f"  {human_bytes(report['base_bytes'])}  "
                      f"sha256 {short(report['base_sha256'])}")
    print(base_line)
    if report.get("backup"):
        print(f"  backup  {report['backup']}")
    print()

    def cell(text: str, width: int) -> str:
        text = clip(text, width)
        return text.ljust(width)

    widths = {
        "checkout": max([len("checkout")] + [len(t.checkout.name) for t in targets]) + 2,
        "base": 11,
        "write": 13,
        "suite": 34,
        "mutation": 34,
    }
    print(cell("checkout", widths["checkout"]) + cell("base", widths["base"])
          + cell("write", widths["write"]) + cell("suite", widths["suite"])
          + cell("mutation", widths["mutation"]) + "claims")
    for target in targets:
        base_state = target.base_state or "-"
        write_state = {"written": "written", "already": "identical",
                       "refused": "refused", "failed": "FAILED",
                       "admitted": "would write",
                       "pending": "-"}.get(target.state, target.state)
        suite, mutation = "-", "-"
        if target.state in ("written", "already"):
            if target.suite:
                suite = (f"{target.suite['state']} [{target.suite['seconds']:g}s] "
                         f"{target.suite['summary']}")
            else:
                suite = "not run"
            if target.mutation:
                mutation = "; ".join(describe_mutation(item) for item in target.mutation)
            elif not report["no_mutation"]:
                mutation = "none declared"
        elif report["dry_run"] and target.state == "admitted":
            suite, mutation = "- (dry run)", "- (dry run)"
        claims_cell = target.claims["summary"] if target.claims else "-"
        print(cell(target.checkout.name, widths["checkout"]) + cell(base_state, widths["base"])
              + cell(write_state, widths["write"]) + cell(suite, widths["suite"])
              + cell(mutation, widths["mutation"]) + claims_cell)
        if target.reason:
            print(f"  {target.checkout.name}: refused - {target.reason}")
        for line in target.details:
            print(f"    {line}")
        for line in target.warnings:
            print(f"    warning: {line}")
        for line in target.side_effects:
            print(f"    side effect: {line.strip()}")
        for item in (target.claims or {}).get("items", []):
            if item["state"] != "ok":
                print(f"    claim: {describe_claim(item)}")
    print()
    print(f"port_change: {report['verdict']}")


def apply(args) -> int:
    cwd = Path.cwd()
    relpath = relposix(args.file, what="the file")
    source_rel = relposix(args.source_path or args.file, what="--source-path")
    source_checkout = Path(args.source).expanduser()
    if not source_checkout.is_absolute():
        source_checkout = (cwd / source_checkout).resolve()
    else:
        source_checkout = source_checkout.resolve()
    if not source_checkout.is_dir():
        raise Usage(f"--from is not a checkout: {source_checkout}")

    source_file = Path(args.new).expanduser() if args.new else source_checkout / source_rel
    if not source_file.is_absolute():
        source_file = (cwd / source_file).resolve()
    if not source_file.is_file():
        raise Usage(f"no file to port: {source_file}")
    new = source_file.read_bytes()

    base = parse_base(args.base, cwd)
    if base.content is not None:
        if sha256(new) == base.digest:
            raise Usage("the declared base already holds this content - there is nothing "
                        "to port (declare the state before the change, not after it)")

    targets: list[Target] = []
    for raw in args.targets:
        checkout = Path(raw).expanduser()
        checkout = (cwd / checkout).resolve() if not checkout.is_absolute() else checkout.resolve()
        target = Target(checkout=checkout, relpath=relpath)
        targets.append(target)
        if not checkout.is_dir():
            target.refusal(f"no such checkout: {checkout}")
            continue
        if not target.path.is_file():
            target.base_state = "missing"
            target.refusal(f"no such file in this checkout: {relpath}")
            continue
        current = target.path.read_bytes()
        target.before_sha256 = sha256(current)
        if current == new:
            # A target that already holds exactly what would be written is the re-run
            # case, and it is not a drift: nothing is at risk in writing nothing. The
            # base is deliberately not consulted here — a re-run happens after the port
            # landed, so the target is necessarily past the declared base — and the
            # check is put first because a byte-identical file cannot be a surprise.
            target.state = "already"
            target.base_state = "already"
            continue
        target.base_state = "match"
        base_bytes, error = base_bytes_for(base, checkout, relpath)
        if error:
            target.base_state = "unreadable"
            target.refusal(error)
            continue
        if base_bytes is not None:
            if current != base_bytes:
                target.base_state = "differs"
                target.refusal(
                    f"target is {human_bytes(len(current))} sha256 {short(sha256(current))}, "
                    f"declared base is {human_bytes(len(base_bytes))} "
                    f"sha256 {short(sha256(base_bytes))}",
                    first_difference(current, base_bytes))
                continue
        elif target.before_sha256 != base.digest:
            target.base_state = "differs"
            target.refusal(
                f"target is sha256 {short(target.before_sha256)}, declared base is "
                f"sha256 {short(base.digest)} - the bytes behind that digest are not here, "
                "so only the digest can be compared")
            continue
        if claims_wanted(args.claims, relpath):
            claims = claims_in(added_lines(current, new))
            if claims:
                if args.dry_run:
                    target.claims = {"kind": "claims", "state": "unchecked",
                                     "summary": "not checked (dry run)", "items": []}
                else:
                    target.claims = verify_claims(
                        target, interpreter(checkout, args.python), claims,
                        args.claim_limit, args.timeout)
                    if target.claims["state"] == "fail":
                        broken = next(item for item in target.claims["items"]
                                      if item["state"] in ("differs", "missing"))
                        target.refusal(
                            "a line this port would add claims a number this checkout "
                            "does not report: " + describe_claim(broken))
                        continue
                    if target.claims["state"] == "unmeasured":
                        stuck = next(item for item in target.claims["items"]
                                     if item["state"] in ("unmeasured", "skipped"))
                        target.refusal(
                            "a number in the lines this port would add could not be "
                            f"measured here ({describe_claim(stuck)}) - pass --claims off "
                            "to write it without the check")
                        continue
        target.state = "admitted"
        if b"\r\n" in current and b"\r\n" not in new:
            target.warnings.append("line ending changes: base was CRLF, the ported bytes are LF")
        elif b"\r\n" not in current and b"\r\n" in new:
            target.warnings.append("line ending changes: base was LF, the ported bytes are CRLF")

    admitted = [target for target in targets if target.state in ("admitted", "already")]

    if args.dry_run:
        report = build_report(args, relpath, source_file, new, base, targets, None, dry_run=True)
        emit(args, report)
        return verdict(targets, dry_run=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + f"-{os.getpid():x}"
    backup = Backup(Path(args.backup_dir).expanduser(), stamp)
    backup.header = {
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": str(source_file),
        "file": relpath,
        "source_sha256": sha256(new),
        "base": base.label,
    }

    dirt_before: list[set[str] | None] = []
    for target in admitted:
        dirt_before.append(git_dirt(target.checkout))
        if target.state == "already":
            continue
        try:
            backup.add(target, target.path.read_bytes())
            write_port(target, new)
        except OSError as exc:
            target.state = "failed"
            target.reason = f"could not write: {exc}"
            continue
        entry = backup.entries[-1]
        entry["after_sha256"] = sha256(new)
        target.after_sha256 = entry["after_sha256"]
        target.state = "written"

    backup.write()

    for index, target in enumerate(admitted):
        if target.state == "already":
            target.after_sha256 = target.before_sha256
        if target.state not in ("written", "already"):
            continue
        python = interpreter(target.checkout, args.python)
        if not args.no_suite:
            target.suite = check_suite(
                target, python,
                args.suite or ["tests"],
                args.pytest_arg,
                args.timeout)
        plan, note = mutation_plan(target, python, args.mutation, args.timeout,
                                   args.no_mutation)
        if note:
            target.warnings.append(f"mutation: {note}")
        for entry in plan:
            target.mutation.append(check_mutation(target, entry, args.timeout))
        target.side_effects = side_effects(dirt_before[index], git_dirt(target.checkout),
                                           target.relpath)

    report = build_report(args, relpath, source_file, new, base, targets,
                          str(backup.dir) if backup.started() else "", dry_run=False)
    emit(args, report)
    return report["code"]


def build_report(args, relpath, source_file, new, base, targets, backup_dir,
                 *, dry_run: bool) -> dict:
    base_bytes = base.content if base.kind == "file" else None
    code = verdict(targets, dry_run)
    report = {
        "file": relpath,
        "source": str(source_file),
        "source_bytes": len(new),
        "source_sha256": sha256(new),
        "base": base.label,
        "base_bytes": len(base_bytes) if base_bytes is not None else None,
        "base_sha256": base.digest,
        "backup": backup_dir,
        "dry_run": dry_run,
        "no_mutation": bool(args.no_mutation),
        "targets": [
            {
                "checkout": str(target.checkout),
                "path": str(target.path),
                "relpath": target.relpath,
                "state": target.state,
                "base_state": target.base_state,
                "reason": target.reason,
                "details": target.details,
                "warnings": target.warnings,
                "before_sha256": target.before_sha256,
                "after_sha256": target.after_sha256,
                "backup": target.backup,
                "suite": target.suite,
                "mutation": target.mutation,
                "claims": target.claims,
                "side_effects": target.side_effects,
            }
            for target in targets
        ],
        "code": code,
        "verdict": verdict_word(code, targets, dry_run),
        "_targets": targets,
    }
    return report


def emit(args, report: dict) -> None:
    if args.json:
        payload = {key: value for key, value in report.items() if not key.startswith("_")}
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        render(report)


def restore(args) -> int:
    directory = Path(args.restore).expanduser()
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        raise Usage(f"no manifest at {manifest_path} - that is not a port backup")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise Usage(f"the manifest is not JSON: {exc}")
    entries = manifest.get("targets") or []
    if not entries:
        print(f"restore: {manifest_path} names nothing to restore")
        return UNMEASURED
    print(f"restore: {manifest.get('file', '?')} from {manifest.get('base', '?')}")
    refused = 0
    for entry in entries:
        path = Path(entry["path"])
        source = directory / entry["backup"]
        if not source.is_file():
            print(f"  refused {entry['relpath']}: the backup is missing ({source})")
            refused += 1
            continue
        if not path.is_file():
            print(f"  refused {entry['relpath']}: {path} is gone")
            refused += 1
            continue
        current = sha256(path.read_bytes())
        if current != entry.get("after_sha256") and not args.force:
            print(f"  refused {entry['relpath']}: changed after the port "
                  f"(sha256 {short(current)}, ported {short(entry.get('after_sha256') or '')}) "
                  "- pass --force to overwrite it")
            refused += 1
            continue
        path.write_bytes(source.read_bytes())
        try:
            os.chmod(path, entry.get("mode") or 0o644)
        except OSError:
            pass
        print(f"  restored {entry['relpath']}  sha256 {short(entry.get('before_sha256') or '')}")
    if refused:
        print(f"restore: {refused} file(s) refused, nothing else undone")
        return FINDING
    print(f"restore: {len(entries)} file(s) back at the declared base")
    return PASS


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="port_change.py", description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file", nargs="?", help="the file to port, relative to each checkout")
    parser.add_argument("--from", dest="source", metavar="CHECKOUT",
                        help="the checkout the content comes from")
    parser.add_argument("--to", dest="targets", action="append", default=[],
                        metavar="CHECKOUT", help="a checkout to port into (repeatable)")
    parser.add_argument("--base", metavar="SPEC",
                        help="the declared base: git:<rev>, sha256:<hex>, or a file's path")
    parser.add_argument("--source-path", help="the file's path inside the source checkout")
    parser.add_argument("--new", help="a file whose bytes are the new content")
    parser.add_argument("--suite", action="append", default=[], metavar="PATH",
                        help="pytest path(s) inside each checkout (default: tests)")
    parser.add_argument("--pytest-arg", action="append", default=[], metavar="ARG",
                        help="an extra argument for pytest (repeatable)")
    parser.add_argument("--no-suite", action="store_true", help="do not run the suite")
    parser.add_argument("--mutation", action="append", default=[], metavar="NAME",
                        help="a harness to run (default: the ones guarding this file)")
    parser.add_argument("--no-mutation", action="store_true",
                        help="do not run any mutation harness")
    parser.add_argument("--claims", choices=("auto", "on", "off"), default="auto",
                        help="check the numbers a Markdown line claims against this "
                             "checkout's own measurement (default: auto)")
    parser.add_argument("--claim-limit", type=int, default=25, metavar="N",
                        help="claims to verify per checkout, 0 for no limit (default: 25)")
    parser.add_argument("--python", help="the interpreter each checkout's checks run under")
    parser.add_argument("--timeout", type=float, default=900.0,
                        help="seconds per check (default: 900)")
    parser.add_argument("--backup-dir", default=str(BACKUP_ROOT),
                        help=f"where the backups go (default: {BACKUP_ROOT})")
    parser.add_argument("--dry-run", action="store_true",
                        help="report which targets would be refused; write and run nothing")
    parser.add_argument("--json", action="store_true", help="the report as one JSON object")
    parser.add_argument("--restore", metavar="DIR", help="put a backup back")
    parser.add_argument("--force", action="store_true",
                        help="restore over a file that changed after the port")
    args = parser.parse_args(argv)

    try:
        if args.restore:
            if args.file:
                raise Usage("--restore takes the backup directory, not a file")
            return restore(args)
        missing = [flag for flag, value in
                   (("a file", args.file), ("--from", args.source), ("--base", args.base))
                   if not value]
        if not args.targets:
            missing.append("--to")
        if args.claim_limit < 0:
            raise Usage("--claim-limit cannot be negative (0 means no limit)")
        if missing:
            raise Usage("porting needs " + ", ".join(missing) +
                        " - the base is never inferred from the target, because a base "
                        "taken from the file being changed refuses nothing")
        return apply(args)
    except Usage as exc:
        print(f"port_change: {exc}", file=sys.stderr)
        return UNMEASURED


if __name__ == "__main__":
    sys.exit(main())
