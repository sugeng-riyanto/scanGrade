#!/usr/bin/env python3
"""Check the number claims in ``AGENTS.md`` against what this checkout can show.

Why this exists
---------------
``AGENTS.md`` is a log, and its most useful sentences carry numbers: "``N``
tests", "``N``/``M`` injected defects caught". Those numbers were true when they
were written, and nothing reads them afterwards. The failure this tool exists
for is not a lie in the log — it is a claim that *stopped* being true, in the
one direction nobody notices: a test file the claim says has 19 tests now
collects 17 because two were deleted with their feature, or a harness the log
says catches 16/16 declares 18 mutations and nobody has run the last two.

Two decisions make it cheap enough to run on every change, and they are the
whole design:

* **A test claim is checked by collection, not execution.** ``pytest
  --collect-only`` starts the interpreter and imports the modules — the same
  cost a test session pays before its first assertion — and answers the number
  of tests a file *has*. It does not run one. A claim worded as a pass count
  ("7 lulus") cannot be proven this way, so the tool verifies the necessary
  condition (a file cannot pass more tests than it collects) and says so
  instead of pretending. That is the difference between ``ok`` and
  ``consistent`` in the report.
* **A harness claim is checked by reading the harness, never by running it.**
  Every mutation harness in this repository declares its defects in one
  module-level ``MUTATIONS`` list and prints ``caught/len(MUTATIONS)``. The
  denominator is therefore a fact about the file, available from an AST parse;
  the numerator is a fact about a run. The tool verifies the denominator and
  the claim's shape, and never injects a defect — 16 harnesses at full cost is
  exactly what this must not do. ``--run-harness NAME`` exists for the one
  harness somebody actually wants to re-measure, and it is opt-in for that
  reason.

What "every number" means here
------------------------------
The tool does not claim to verify every digit in the file. It verifies every
number that is a *checkable claim about this checkout*, and it **counts and
names the rest** (dates, latency measurements, thresholds, prose counts) under
``unchecked`` so a green run cannot be mistaken for "all 6062 numbers are
true". The report's last line is that distinction.

History is not drift
--------------------
The log is append-only, so a file has many claims and all but the last are
history: "16/16" written in September is not wrong because the harness grew to
18 in October. Only the newest claim per file or harness is verified; earlier
ones are reported ``superseded`` and never fail the run. Without that rule the
tool would report the log's own honesty as drift, and the first person to see
64 findings would stop running it.

Exit codes
----------
0 — every current claim agrees with this checkout (or could not be checked and
    is named as such);
1 — at least one current claim is contradicted by the checkout;
2 — the check could not run (no ``AGENTS.md``, or collection failed for every
    file it was asked about), which is never a pass.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, NamedTuple

ROOT = Path(__file__).resolve().parents[1]
AGENTS = "AGENTS.md"
DEFAULT_TIMEOUT = 300

#: A test file and the count the log attaches to it: "`…/test_x.py` (19 tests —",
#: "`…/test_x.py` **7 lulus**". Three details are load-bearing, and each was
#: found by a wrong reading rather than chosen:
#:
#: * the gap is **four** characters at most (`**`, `` ` ``, `(`, a space) — a
#:   wider window reads the next clause's number as this file's count, which is
#:   how the first draft produced 63 "drifts" that were its own mistake;
#: * `+` is excluded from the gap, so "`…/test_x.py` (+4 uji: …)" is read as
#:   *four added*, not as a file with four tests;
#: * the word must end at punctuation, a closing bracket or the end of the line,
#:   which is what rejects "(8 uji mode termasuk …, 4 uji wiring)" — a count of
#:   one *mode*, not of the file.
TEST_CLAIM = re.compile(
    r"(?P<path>tests/unit/test_[A-Za-z0-9_]+\.py)"
    r"[^A-Za-z0-9+\n]{0,4}"
    r"(?P<number>\d+)\s+(?P<word>tests?|uji|lulus|passed)\b"
    r"(?=\s*(?:[,;:)—–]|\*\*|\)|$))"
)
HARNESS_REF = re.compile(r"(?P<path>\.freebuff/mutate_[A-Za-z0-9_]+\.py)")
SCORE = re.compile(r"(?P<caught>\d+)\s*/\s*(?P<total>\d+)")

#: Words that make a count a *pass* count rather than a test count. A pass
#: count is the one thing collection cannot answer, and saying so is the point.
PASS_WORDS = {"lulus", "passed"}

#: Words an entry uses when it retires a file. A "missing" finding on a line
#: that says the file was deleted is the tool being less informed than the text.
RETIRE_WORDS = re.compile(r"\b(?:dihapus|deleted|removed|digantikan|replaced)\b", re.I)

#: How far a score may sit from its harness on the same line. A log entry is one
#: long line and carries other fractions (durations, ratios); the cap is what
#: keeps "1/17" in the next clause from being read as a mutation score.
SCORE_REACH = 220

#: Numbers that are dates or clock times, not claims: `2026-10-09`,
#: `09/10/2026`, `03:44Z`. Removed before the unchecked tally so it measures
#: prose and measurements instead of the calendar.
DATE_SHAPES = (
    re.compile(r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b"),
    re.compile(r"\b\d{1,2}[-/]\d{1,2}[-/]\d{4}\b"),
    re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:UTC|Z)?\b"),
)
NUMBER = re.compile(r"\d+")


class Claim(NamedTuple):
    line: int
    target: str
    number: int
    word: str


class HarnessClaim(NamedTuple):
    line: int
    target: str
    caught: int
    total: int


class Finding(NamedTuple):
    status: str          # ok | grown | consistent | drift | missing |
                         # retired | unavailable | superseded | unverifiable
    target: str
    line: int
    said: str
    saw: str


class Report(NamedTuple):
    findings: list[Finding]
    checked: int
    unchecked: int
    unchecked_sample: list[str]

    @property
    def drift(self) -> list[Finding]:
        """Claims today's checkout contradicts — a loss, not a later addition.

        A suite that grew after the entry was written is not a false claim; it is
        an old sentence. A suite that *shrank* below what the log says is a claim
        about this checkout that is no longer true, and it is the direction that
        matters: tests disappeared and nothing said so. The same asymmetry holds
        for a harness: more declared mutations than the entry counted is growth;
        fewer is a harness that lost a case (or a number that was never its
        length).
        """
        return [f for f in self.findings if f.status in ("drift", "missing")]


# ── reading the log ──────────────────────────────────────────────────────────

def test_claims(text: str) -> list[Claim]:
    """Every test-count claim, in the order the log makes it."""
    claims = []
    for number, line in enumerate(text.splitlines(), start=1):
        for match in TEST_CLAIM.finditer(line):
            claims.append(Claim(number, match.group("path"),
                                int(match.group("number")), match.group("word")))
    return claims


def harness_claims(text: str) -> list[HarnessClaim]:
    """Every ``N/M`` a line attaches to a mutation harness.

    The score is paired with the nearest harness on its own line: the log writes
    "mutation-checked, **16/16** (`.freebuff/mutate_x.py`)" as often as the
    reverse, and a whole-log pairing would attach one entry's score to the next
    entry's harness.
    """
    claims = []
    for number, line in enumerate(text.splitlines(), start=1):
        refs = [(m.start(), m.group("path")) for m in HARNESS_REF.finditer(line)]
        if not refs:
            continue
        for score in SCORE.finditer(line):
            caught, total = int(score.group("caught")), int(score.group("total"))
            position, target = min(refs, key=lambda ref: abs(ref[0] - score.start()))
            distance = abs(position - score.start())
            # A fraction is a mutation score only when the entry says so nearby
            # ("**16/16 caught**", "**9/9** (`.freebuff/mutate_x.py`) — …").
            # Without this, every ratio in a long line ("9/7", "1/17", a
            # duration) becomes a claim about the harness the line also names.
            says_caught = re.match(r"\s*(?:injected\s+)?(?:defects?\s+)?caught\b",
                                   line[score.end():score.end() + 60]) is not None
            if distance > SCORE_REACH or (distance > 60 and not says_caught):
                continue
            claims.append(HarnessClaim(number, target, caught, total))
    return claims


def mutations_declared(path: Path) -> int | None:
    """How many defects a harness declares, without executing a single one.

    `MUTATIONS` is the harness's own contract — every one in this repository
    prints ``caught/len(MUTATIONS)`` — so its length is the denominator a claim
    is checked against. ``None`` means the file does not declare one in the
    shape this tool can read, and that is reported rather than guessed.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    except (OSError, SyntaxError):
        return None
    found: int | None = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "MUTATIONS" for t in node.targets):
            found = len(node.value.elts) if isinstance(node.value, (ast.List, ast.Tuple)) else None
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name) \
                and node.target.id == "MUTATIONS":
            # `MUTATIONS += [...]` means the literal above is not the whole set.
            # A length read from it would be too small and would read as drift.
            return None
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            func = node.value.func
            if isinstance(func, ast.Attribute) and func.attr in ("extend", "append") \
                    and isinstance(func.value, ast.Name) and func.value.id == "MUTATIONS":
                return None
    return found


# ── asking the checkout ──────────────────────────────────────────────────────

class CollectionFailed(Exception):
    """The collector could not answer at all — an environment failure, not a claim."""


def collect_counts(root: Path, paths: list[str],
                   python: str | None = None) -> dict[str, int | str]:
    """Tests per file, from one ``--collect-only`` run for all of them.

    One process rather than one per file: collection pays the import cost of the
    app once, and 150 files × a pytest startup would be slower than the suite
    this tool exists to avoid running. ``-p no:cacheprovider`` keeps the check
    from writing anything into the checkout.
    """
    if not paths:
        return {}
    done = subprocess.run(
        [python or sys.executable, "-m", "pytest", "--collect-only", "-q",
         "-p", "no:cacheprovider", "--continue-on-collection-errors", *paths],
        cwd=str(root), capture_output=True, text=True, timeout=DEFAULT_TIMEOUT)
    counts: dict[str, int] = {path: 0 for path in paths}
    for line in done.stdout.splitlines():
        if "::" not in line:
            continue
        name = line.split("::", 1)[0].strip()
        if name in counts:
            counts[name] += 1
    # A file pytest could not even import is not a file with zero tests. Reading
    # the 0 would report drift for the environment's failure, which is the one
    # mistake a checker must not make: it sends somebody to the log when the
    # problem is the box.
    broken: dict[str, str] = {}
    for line in (done.stdout + "\n" + done.stderr).splitlines():
        match = re.match(r"ERROR\s+(\S+)", line.strip())
        if match and match.group(1) in counts:
            broken[match.group(1)] = line.strip()[:160]
    if done.returncode not in (0, 1, 5) and not any(counts.values()):
        raise CollectionFailed(
            f"pytest --collect-only exited {done.returncode}: "
            + (done.stderr or done.stdout).strip().splitlines()[-1][:300])
    return {path: broken.get(path, counts[path]) for path in paths}


# ── the verdict ──────────────────────────────────────────────────────────────

def _retired(sentence: str, target: str) -> bool:
    """Whether the entry says *this* file was removed, not merely that something was.

    The whole line is the wrong window: a log entry is a paragraph, and words
    like "replaced" appear in it for reasons that have nothing to do with the
    file being counted. Measured, a whole-line search retired 44 files that are
    all still here. The window is the clause the path sits in.
    """
    at = sentence.find(target)
    if at < 0:
        return False
    # The clause the path sits in, not the paragraph: it ends at the first
    # semicolon or sentence break after the path. Otherwise "… masih ada; berkas
    # lain sudah digantikan" retires a file the same sentence says is still here.
    tail = sentence[at + len(target):at + len(target) + 200]
    for stop in (";", "\u3002", ". "):
        if stop in tail:
            tail = tail[:tail.index(stop)]
    head = sentence[max(0, at - 40):at]
    return RETIRE_WORDS.search(head + tail) is not None


def _latest(claims) -> dict[str, int]:
    """The index of the newest claim per target; everything earlier is history.

    Identity is the claim's position, not its line: one log line can carry two
    claims about the same file ("16/16 … after 9/7"), and a line number would
    call both of them current.
    """
    newest: dict[str, int] = {}
    for index, claim in enumerate(claims):
        newest[claim.target] = index
    return newest


def verify(root: Path, text: str,
           collect: Callable[[Path, list[str]], dict[str, int | str]] | None = None,
           only: str | None = None) -> Report:
    """Compare the log's current claims with this checkout.

    ``collect`` is injected so the tests can drive every shape without starting
    pytest; the default is :func:`collect_counts`.
    """
    findings: list[Finding] = []
    tests = test_claims(text) if only in (None, "tests") else []
    harnesses = harness_claims(text) if only in (None, "harnesses") else []

    latest_test = _latest(tests)
    latest_harness = _latest(harnesses)
    wanted = {c.target for index, c in enumerate(tests)
              if latest_test[c.target] == index and (root / c.target).is_file()}
    counts: dict[str, int | str] = {}
    failure = ""
    if wanted:
        try:
            counts = (collect or (lambda r, p: collect_counts(r, p)))(root, sorted(wanted))
        except CollectionFailed as exc:                       # the environment, not a claim
            failure = str(exc)

    for index, claim in enumerate(tests):
        if latest_test[claim.target] != index:
            findings.append(Finding("superseded", claim.target, claim.line,
                                    f"{claim.number} {claim.word}", "an older entry"))
            continue
        if not (root / claim.target).is_file():
            # The log says when it retires something — "`test_x.py` (17 uji)
            # dihapus … digantikan berkas baru" — and a file the entry itself
            # says was removed is not drift. Without this, the tool's first
            # "missing" finding was a file the sentence had already buried.
            sentence = text.splitlines()[claim.line - 1]
            retired = _retired(sentence, claim.target)
            findings.append(Finding("retired" if retired else "missing",
                                    claim.target, claim.line,
                                    f"{claim.number} {claim.word}",
                                    "the entry says this file was removed" if retired
                                    else "the file is not in this checkout"))
            continue
        if failure:
            findings.append(Finding("unverifiable", claim.target, claim.line,
                                    f"{claim.number} {claim.word}", failure))
            continue
        if claim.target not in counts:
            findings.append(Finding("unverifiable", claim.target, claim.line,
                                    f"{claim.number} {claim.word}",
                                    "collection did not answer for this file"))
            continue
        seen = counts[claim.target]
        if isinstance(seen, str):
            findings.append(Finding("unverifiable", claim.target, claim.line,
                                    f"{claim.number} {claim.word}", seen))
            continue
        if claim.word in PASS_WORDS:
            # A pass count cannot be proven without running the file, and the
            # cheap direction is still a real refusal: more passes than tests is
            # impossible whatever the run does.
            status = "drift" if seen < claim.number else "consistent"
            detail = f"{seen} collected" + ("" if status == "drift" else
                                             "; passing needs a run")
        elif seen > claim.number:
            # Growth after the entry is history, not a false sentence. It is
            # still shown: a reader asking "what does the log say about this
            # file" gets the gap rather than silence.
            status, detail = "grown", f"{seen} collected (suite grew after the entry)"
        else:
            status = "ok" if seen == claim.number else "drift"
            detail = f"{seen} collected (tests were lost)" if status == "drift" \
                else f"{seen} collected"
        findings.append(Finding(status, claim.target, claim.line,
                                f"{claim.number} {claim.word}", detail))

    for index, claim in enumerate(harnesses):
        if latest_harness[claim.target] != index:
            findings.append(Finding("superseded", claim.target, claim.line,
                                    f"{claim.caught}/{claim.total}", "an older entry"))
            continue
        path = root / claim.target
        if not path.is_file():
            sentence = text.splitlines()[claim.line - 1]
            retired = _retired(sentence, claim.target)
            # `.freebuff/` is gitignored, so a fresh clone legitimately has none
            # of these. Naming it is the honest answer; failing on it would make
            # the check unusable outside the machine that ran the harness.
            findings.append(Finding("retired" if retired else "unavailable",
                                    claim.target, claim.line,
                                    f"{claim.caught}/{claim.total}",
                                    "the entry says this harness was removed" if retired
                                    else "harness not in this checkout (.freebuff is ignored)"))
            continue
        declared = mutations_declared(path)
        if declared is None:
            findings.append(Finding("unverifiable", claim.target, claim.line,
                                    f"{claim.caught}/{claim.total}",
                                    "no MUTATIONS list this tool can read"))
            continue
        if claim.caught > claim.total:
            findings.append(Finding("drift", claim.target, claim.line,
                                    f"{claim.caught}/{claim.total}",
                                    "more caught than injection is not a score"))
        elif declared > claim.total:
            findings.append(Finding("grown", claim.target, claim.line,
                                    f"{claim.caught}/{claim.total}",
                                    f"the harness now declares {declared} mutations"))
        elif declared < claim.total:
            findings.append(Finding("drift", claim.target, claim.line,
                                    f"{claim.caught}/{claim.total}",
                                    f"the harness declares only {declared} mutations"))
        else:
            findings.append(Finding("consistent", claim.target, claim.line,
                                    f"{claim.caught}/{claim.total}",
                                    f"{declared} declared; the catch count needs a run"))

    checked = sum(1 for f in findings if f.status != "superseded")
    unchecked, sample = _unchecked(text, tests, harnesses)
    return Report(findings, checked, unchecked, sample)


def _unchecked(text: str, tests: list[Claim],
               harnesses: list[HarnessClaim]) -> tuple[int, list[str]]:
    """Numbers this run did not check, counted rather than hidden.

    Every digit that is not part of a date/time and not inside a span this tool
    read is tallied, with a short sample of the lines that carry them. The
    number is not a defect — most are measurements and thresholds that no suite
    can re-derive offline — but a report that showed only "12 claims checked"
    would read as "the file has 12 numbers", and that is the overclaim.
    """
    clean = text
    for shape in DATE_SHAPES:
        clean = shape.sub(" ", clean)
    total = len(NUMBER.findall(clean))

    # Spans already accounted for: the claim's own number, once per claim.
    accounted = len(tests) + len(harnesses)

    lines = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not NUMBER.search(line):
            continue
        if TEST_CLAIM.search(line) or (HARNESS_REF.search(line) and SCORE.search(line)):
            continue
        snippet = line.strip()
        lines.append(f"AGENTS.md:{number}: {snippet[:110]}")
    return max(0, total - accounted), lines[:10]


# ── reporting ────────────────────────────────────────────────────────────────

def render(report: Report, verbose: bool = False, ran: int = 0) -> str:
    """The attention list by default; everything, including history, on ``--all``.

    A checkout whose log is honest will carry hundreds of ``unavailable`` and
    ``grown`` findings, and a report that prints them all is one nobody reads.
    The default shows what a person has to decide about (a claim the checkout
    contradicts) and *counts* the rest, which is still a complete statement of
    what was and was not checked.
    """
    counts: dict[str, int] = {}
    for finding in report.findings:
        counts[finding.status] = counts.get(finding.status, 0) + 1

    out = []
    interesting = {"drift", "missing"} if not verbose else (
        set(counts) - {"superseded"})
    for status in ("drift", "missing", "retired", "unverifiable", "grown",
                   "consistent", "ok", "unavailable"):
        if status not in interesting:
            continue
        rows = [f for f in report.findings if f.status == status]
        if not rows:
            continue
        out.append(f"{status.upper()} ({len(rows)})")
        for finding in rows:
            out.append(f"  {finding.target}:{finding.line}  said {finding.said} — "
                       f"{finding.saw}")
        out.append("")
    if verbose:
        rows = [f for f in report.findings if f.status == "superseded"]
        if rows:
            out.append(f"SUPERSEDED ({len(rows)}) — older entries, not verified")
            for finding in rows:
                out.append(f"  {finding.target}:{finding.line}  said {finding.said}")
            out.append("")

    summary = ", ".join(f"{name} {count}" for name, count in sorted(counts.items()))
    out.append(f"claims: {report.checked} checked ({summary or 'none'})")
    out.append("harnesses: read, never executed — `--run-harness NAME` is the opt-in"
               if not ran else
               f"harnesses: read; {ran} run on request (`--run-harness`)")
    out.append(f"not machine-checked: {report.unchecked} number(s) — dates removed; "
               "measurements, thresholds and prose counts are not suite claims")
    if verbose:
        for sample in report.unchecked_sample:
            out.append(f"    {sample}")
    drift = report.drift
    if drift:
        out.append(f"DRIFT: {len(drift)} claim(s) this checkout contradicts")
    elif counts.get("unverifiable") or counts.get("unavailable"):
        out.append("OK: no contradiction found; the unavailable and unverifiable "
                   "claims were not checked")
    else:
        out.append("OK: every current number claim agrees with this checkout")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--repo", default=str(ROOT), help="checkout to check")
    parser.add_argument("--agents", default=AGENTS, help="the log to read")
    parser.add_argument("--only", choices=("tests", "harnesses"),
                        help="check one claim family")
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    parser.add_argument("--all", action="store_true",
                        help="print every finding, including grown suites and the "
                             "history the newest claim supersedes")
    parser.add_argument("--run-harness", metavar="NAME", action="append", default=[],
                        help="opt in to running one harness (NAME matches its file "
                             "stem); off by default because a run injects defects")
    args = parser.parse_args(argv)

    root = Path(args.repo)
    agents = root / args.agents
    if not agents.is_file():
        print(f"claim check: no {agents} — nothing to read", file=sys.stderr)
        return 2
    text = agents.read_text(encoding="utf-8-sig")

    if args.run_harness:
        # An opt-in deep check, and the only path in this tool that executes a
        # harness. Everything above it reads.
        for name in args.run_harness:
            target = next((c.target for c in harness_claims(text)
                           if Path(c.target).stem == name), None)
            if target is None:
                print(f"claim check: no harness named {name!r} in {args.agents}",
                      file=sys.stderr)
                return 2
            path = root / target
            if not path.is_file():
                print(f"claim check: {target} is not in this checkout", file=sys.stderr)
                return 2
            done = subprocess.run([sys.executable, str(path)], cwd=str(root),
                                  capture_output=True, text=True, timeout=DEFAULT_TIMEOUT)
            print(f"{target}: exit {done.returncode}")
            print((done.stdout or done.stderr).strip().splitlines()[-1]
                  if (done.stdout or done.stderr).strip() else "(no output)")

    report = verify(root, text, only=args.only)
    if args.json:
        print(json.dumps({
            "checked": report.checked,
            "unchecked": report.unchecked,
            "unchecked_sample": report.unchecked_sample,
            "drift": len(report.drift),
            "findings": [f._asdict() for f in report.findings],
        }, indent=2))
    else:
        print(render(report, verbose=args.all, ran=len(args.run_harness)))

    if report.drift:
        return 1
    checked_any = any(f.status in ("ok", "consistent", "grown")
                      for f in report.findings)
    if report.findings and not checked_any and not args.only:
        # Nothing verifiable at all is an environment failure, not a pass.
        return 2
    return 0


if __name__ == "__main__":
    try:
        code = main()
        # The flush is inside the guard: a pipe closed by the reader (``--json |
        # head``) fails here, not in ``print``, and an uncaught flush error makes
        # the interpreter exit 120 — a number nobody can interpret as a finding.
        sys.stdout.flush()
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        code = 0
    raise SystemExit(code)
