#!/usr/bin/env python3
"""Does the documentation still point at files this repository actually tracks?

A page is the only part of a repository nothing executes, so its references rot
silently. `docs/deploy/QUESTION_TYPES_MERGE_RUNBOOK.md` listed six files that a
merge had dropped and four more that were never written; `AGENTS.md` carried a
"Key Files (Notifications)" block naming four templates that no longer exist,
because the messaging UI moved to `shared/comms.html` and nothing noticed. Every
one of those pages opens fine, renders fine, and sends its reader to a dead path —
and the reader finds out at review, or after the edit, or never.

So this is the check the pre-commit hook runs: **a reference that does not resolve
is a refusal**, before the commit rather than at review.

## What counts as a reference

Two shapes, because they are two different assertions:

* a **markdown link** — `[text](path)` and its reference-style form
  `[label]: path`. A link is always an assertion that the target is there, so it
  is checked wherever it appears.
* a **path in an inline code span** — `` `deploy/theme_gate.sh` ``. Checked when
  the span *is* a path: its first segment is a directory this repository tracks,
  and it carries a known file extension. An extension-less span is resolved when
  it can be (`app/utils/cache` names `app/utils/cache.py`; a directory resolves to
  itself) and is **not** an assertion when it cannot — because this repo writes
  `feature/description` as a git branch template and `app/static/uploads/` as a
  directory the app creates at runtime, and a rule that failed on those would be
  deleted within a day.

Fenced blocks are skipped: a doc that shows a command is showing a command, not
naming this checkout's files (`/opt/scangrade`, `~/.ssh`). Headings, prose and
code share a line only in the table form below, which is read as text.

## The copy nothing executes

A page is not the only place a path is written and never run. A **docstring**, a
**template comment** and a **deploy script's own failure text** name tools too, and
they rot the same way: `app/services/exam_report.py` cited a page that was never
written (`docs/features/ANALYSIS.md`) for the item-analysis thresholds — in a
docstring nothing renders, while the tool itself stayed green.
A message that says "run `deploy/<gate>.py`" is read by a person at the worst
possible moment, so it is the worst place to find out the name moved.

So the same reading runs over the copy those files carry:

* `*.py` under `app/`, `deploy/` and the root, every line — a docstring, a `#`
  comment, and the failure text of a deploy tool (`print("run deploy/<tool>.py")`
  is a claim about this checkout in its own right, and it is the one that fails
  on a box rather than in review). A line that **opens with a command** is exempt:
  it is showing what somebody would type, exactly as a fenced block on a page is,
  and `supabase/migrations/<stamp>_<name>.sql` is the name *you* will write;
* `*.sh` and `*.ps1`, every line — a comment and a command that runs a tool are
  the same kind of claim (`bash deploy/<gate>.sh` must name a script that is
  there, and so must the `FAILED:` line above it);
* `{# … #}` and `<!-- … -->` bodies in the templates — comments and not the whole
  page, because a template's other paths are addresses (`/static/js/x.js`), which
  is what a browser fetches rather than what this checkout holds.

Those files add a third shape: a **bare path** (`deploy/theme_gate.sh`), because
comment copy and messages write it without backticks. It is a claim when its
first segment is a directory this repository tracks — the filter the code spans
already answer to — and it is resolved the same way.

Two things keep that from becoming the false-positive machine this guard exists
not to be. `tests/` is not read: a test builds fixture trees, and a docstring
there naming a path in *that* tree is not a claim about this one — the rule the
region guard already states about a fabricated host. And this copy gets one
exemption the pages do not need, because it has no backticks to mark the
difference: a reference whose own line says **for example**, `e.g.`, *such as*,
*illustrative*, *contoh* or *misalnya* is naming a shape rather than a file.

The limit: a bare path is only read when it carries a directory
(`deploy/<gate>.sh`). A bare filename (`schema_contract.py`, `requirements.txt`)
is not read, because this repository's prose is full of filenames and a rule that
read them would fail on `config.py` written in the sentence that means this one.
Quote those in backticks and they are checked like any other span.

## The heading a link names

`[x](<page>.md#the-cases)` is two claims, and only one of them was ever checked.
The page resolves, so the link looks right — while `## The cases` was renamed and
the reader lands at the top of a page that no longer says what the link promised. So
the fragment is resolved against the **headings** of the page it names, by
GitHub's slugger: lower-cased, punctuation dropped rather than replaced (which is
why `## Peran, Hak Akses & Sesi` is `#peran-hak-akses--sesi`), spaces to hyphens,
a repeated heading suffixed `-1`, and markup stripped because the anchor is the
rendered text. Any `<a id="…">` the page writes itself also counts. A same-page
`#fragment` is checked the same way — it is the one shape this tool never looked
at, because `normalise` used to read it as "no path".

Two limits are stated rather than guessed. A fragment on a target that is **not a
page** is not checked: `<file>.py#L42` is a line GitHub scrolls to, and
`<document>.html#anchor` is an address inside a document this tool does not parse. And a
fragment is judged only where the path resolved, so a link to a missing page is
one refusal and not two — the page is the first thing that has to exist.

## What is deliberately not checked

* `.freebuff/` — the scratch corner, gitignored except for two leftovers. A doc
  naming a probe that lives in *some* checkout is not asserting anything about
  this one.
* `docs/planning/` — a plan names the files it intends to write. That is the
  point of a plan.
* a reference on a line, or under a heading, that says the file is **gone** —
  `dropped`, `deleted`, `removed`, `superseded`, `retired`, `planned`, `gone`,
  or their Indonesian partners `dihapus`, `dibuang`, `diganti`, `ditinggalkan`.
  This repository's records name deleted files on purpose and say so in the same
  sentence; reading that sentence is what keeps the rule from punishing honesty.
  The runbook's whole `### dropped — branch-only, superseded by main` table is
  exempt by its heading, and it is the table that found this guard's reason to
  exist.
* `deploy/doc_links_gone.txt` — the exceptions no heading covers, one per line
  with the reason written down, which is how a deliberate mention of a file that
  is not there becomes a **recorded decision** instead of an invisible hole. The
  list is held in both directions: an entry whose path now resolves, or whose doc
  no longer names it, is a refusal too — an exemption nobody needs is how the
  next real reference is missed.

## What a refusal prints

One line per offender — `doc:line [kind] reference`, with the reason appended when
there is one (`no heading `#the-cases` on b.md`) — and **under it a window of the
page's own line**, cut at a word boundary a few words either side of the reference.
The number alone is a lookup; the sentence is the edit, and the reader who gets the
sentence can fix the reference without opening the file to find it. The window is
bounded because a line here is not a line anywhere else: `AGENTS.md`'s bullets run to
thousands of characters, so each side is capped and marked `...` where it was cut,
and the reference itself is shown where it sits rather than restated.

## Exit codes

    0  every reference resolves (or is exempt, by one of the rules above)
    1  a reference names something this checkout does not have, or the exception
       list has gone stale — both are defects in the release, and both name the
       doc and the line
    2  could not measure — no tracked documents, a doc that cannot be read, or no
       git in the tree. A box in that state is in no position to reject a release,
       and the caller is told so rather than handed a silent zero.
"""
from __future__ import annotations

import argparse
import os
import posixpath
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]

#: The extensions a reference may carry. Anything else on the last segment is a
#: symbol or a derived artifact (`auth._resolved_school`, `…py.patch`) and is read
#: as the file it decorates — stated as a limit, because it means such a reference
#: is checked against its base path rather than in its own right.
KNOWN_EXT = frozenset({
    ".py", ".md", ".sh", ".ps1", ".html", ".css", ".js", ".sql", ".json",
    ".yml", ".yaml", ".txt", ".cfg", ".toml", ".ini", ".conf", ".csv", ".j2",
    ".service", ".timer",
})

#: Per-checkout scratch: a probe named here is not a claim about this tree.
SCRATCH = (".freebuff/",)

#: Pages whose job is to name files that do not exist yet.
PLANNING = ("docs/planning/",)

#: "This file is not here, on purpose" — the words this repo's records use when
#: they name something a merge dropped. Indonesian is included because most of the
#: long records are written in it, and a rule that only read English would fail on
#: the sentences that keep it honest.
GONE = (
    "dropped", "deleted", "removed", "superseded", "retired", "planned",
    "never written", "gone", "no longer", "dihapus", "dibuang", "diganti",
    "ditinggalkan", "tidak pernah ditulis",
)

#: How far from a reference the words above are still about *that* reference.
#: A line is not a sentence in this repository: `AGENTS.md`'s bullets run to
#: thousands of words, and reading the whole bullet exempted 470 real references
#: because a paragraph mentioning "removed" somewhere in it is not the sentence
#: that describes the path four hundred characters later. Measured before the
#: change: 470 of `AGENTS.md`'s references exempted, 9 genuinely broken ones left.
NEAR = 140

#: "This name is a shape, not a file." Comment copy gets this where a page does
#: not need it: a page has backticks, so a made-up path there is visibly made up,
#: while a docstring that shows a command — "(`app/services/<module>.py`)" —
#: is illustrating one. Same window as the gone words, and the same reason it is
#: bounded: the sentence has to be about *this* reference.
EXAMPLE = (
    "for example", "e.g.", "such as", "for instance", "illustrative",
    "hypothetical", "stand-in", "placeholder", "contoh", "misalnya",
)

#: A line that **opens with a command** is showing one — "python deploy/<tool>.py
#: supabase/migrations/<stamp>_<name>.sql" is a shape to copy, not a file this
#: checkout has (the migration is the one *you* will write). The same reading a
#: fenced block gets on a page, applied to the one place a docstring puts
#: commands.
COMMAND = re.compile(
    r"^\s*(?:\$|#\s*)?(?:python3?|py|bash|sh|zsh|pwsh|powershell|\./|\.\\|curl|"
    r"psql|npm|node|pytest|git|xcopy|copy|mv|cp|rm)\b")

#: The templates whose comments are read. `#` in HTML is nothing, so the shapes
#: are Jinja's `{# … #}` and `<!-- … -->`.
TEMPLATED = ("app/templates/",)

EXCEPTIONS = ROOT / "deploy" / "doc_links_gone.txt"

#: How much of the line to keep on each side of a reference. A **window** and not
#: the line: `AGENTS.md` alone is 1.2 MB of very long bullets, and a refusal that
#: printed the whole line per offender would be a wall nobody reads — which is the
#: state this replaces, where the reader looked the line number up by hand.
CONTEXT = 64

#: Punctuation this repository's pages actually write, folded to ASCII. The report
#: is ASCII on purpose (`_safe`) — an unencodable reference once crashed the guard
#: on a cp1252 console, and a guard that dies while reporting reports nothing — but
#: an em dash is in every second sentence of `AGENTS.md`, and `\u2014` inside a
#: snippet is a snippet nobody can read. Anything unmapped still goes through
#: `_safe`, so the escape hatch is where it always was.
FOLD = {
    "\u2014": "-", "\u2013": "-", "\u2018": "'", "\u2019": "'", "\u201c": '"',
    "\u201d": '"', "\u2026": "...", "\u2192": "->", "\u00d7": "x", "\u2264": "<=",
    "\u2265": ">=", "\u2713": "ok", "\u21c4": "<->", "\u00a0": " ", "\u2022": "*",
}

LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
LINK_DEF = re.compile(r"^\s*\[[^\]]+\]:\s+(\S+)")
CODE = re.compile(r"`([^`]+)`")

#: The text of a link, kept when a heading is slugged: the anchor is the
#: **rendered** text, so `[the gate](<page>.md)` in a heading slugs as `the-gate`.
LINK_TEXT = re.compile(r"\[([^\]]*)\]\([^)]*\)")
MARKUP = re.compile(r"[`*~]")

#: A heading's own anchors, written into the page: `<a id="...">`.
EXPLICIT_ANCHOR = re.compile(
    r"<(?:a|div|span|section)\b[^>]*\b(?:id|name)\s*=\s*[\"']([^\"']+)[\"']")

#: `page.py#L42` is a line, not a heading, and GitHub renders it as one.
LINE_ANCHOR = re.compile(r"^L\d+$", re.I)

#: A path written without backticks — how a docstring, a comment and a shell
#: message name a file. It must carry a directory: `deploy/<gate>.sh` is a claim,
#: a bare filename (`config.py`) is how this repository's prose means *this* file,
#: and reading those would refuse sentences that are right. The first segment is
#: still filtered against the directories git tracks, so `supabase.com/docs` in a
#: URL and `12/05/2026` in a date are not candidates at all.
BARE = re.compile(
    r"(?<![\w/.\-])((?:[\w.\-]+/)+[\w.\-]+\.[A-Za-z0-9]+)(?![\w/\-])")
FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
#: Characters that make a token something other than a path. The bracketed pair
#: and the backslash are here because reading a Python file's **code** means meeting
#: a regex: `.table\\(\\s*[\"']([\\w-]+)[\"']` carries a `](` that the link shape
#: reads as a destination, and `[\\w-]+` is a pattern rather than a file.
BAD_CHARS = set("*?<>{}|$`…\u2026\x00[]^\\")


#: The variables git exports to the processes it spawns. The pre-commit hook *is*
#: a git process, so a plain `git ls-files` here would answer about the hook's own
#: index whatever `-C` said — and the tests drive this tool against scratch trees,
#: where that answer is the wrong repository entirely. These are
#: `tests/unit/git_env.py`'s list, written out again because `deploy/` may not
#: import the suite; the test file asserts the two agree.
GIT_REPO_ENV = (
    "GIT_DIR",
    "GIT_INDEX_FILE",
    "GIT_WORK_TREE",
    "GIT_PREFIX",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)


class Measure(Exception):
    """The question could not be asked — a property of the box, not the release."""


class Ref:
    """One reference, where it was written, and what became of it."""

    __slots__ = ("doc", "line", "kind", "raw", "path", "state", "why", "context")

    def __init__(self, doc: str, line: int, kind: str, raw: str) -> None:
        self.doc = doc
        self.line = line
        self.kind = kind
        self.raw = raw
        self.path = ""
        self.context = ""
        self.state = "missing"      # resolved | missing | exempt | noted
        self.why = ""

    def __repr__(self) -> str:  # pragma: no cover - debugging only
        return f"<Ref {self.doc}:{self.line} {self.state} {self.raw!r}>"


class Report:
    """What a scan found. `missing` and `stale` are the two refusals."""

    def __init__(self) -> None:
        self.refs: list[Ref] = []
        self.docs: list[str] = []
        #: The files whose comment copy was read: a summary that counted only the
        #: pages would claim a scan this run may not have made.
        self.sources: list[str] = []
        self.missing: list[Ref] = []
        self.stale: list[tuple[str, str, str]] = []   # (where, path, why)
        self.exempt = 0

    @property
    def checked(self) -> int:
        return sum(1 for r in self.refs if r.state in ("resolved", "missing"))

    def offenders(self) -> int:
        return len(self.missing) + len(self.stale)


def _safe(text: str) -> str:
    """Text that can be printed on any console.

    Measured on this box: an offender carrying a U+2026 (this repo abbreviates a
    migration filename with one) crashed `print` on a cp1252 Windows console, and a
    guard that dies while reporting is a guard that reports nothing. The real
    offender is unchanged in the file; only the line the operator reads is
    transliterated.
    """
    return text.encode("ascii", "backslashreplace").decode("ascii")


def printable(text: str) -> str:
    """ASCII, with this repository's own punctuation kept legible.

    The snippet is prose from a page, so it carries em dashes, arrows and curly
    quotes; escaped they read as `\u2014`, which is a snippet nobody can act on. The
    handful this repository writes are folded to their ASCII twins first, and
    anything else still goes through `_safe`.
    """
    return _safe("".join(FOLD.get(char, char) for char in text))


def _clip(text: str, limit: int, *, from_end: bool) -> str:
    """`text` cut to `limit` characters at a word boundary (no ellipsis)."""
    if len(text) <= limit:
        return text
    if from_end:
        cut = text[-limit:]
        space = cut.find(" ")
        return cut[space + 1:] if space != -1 else cut
    cut = text[:limit]
    space = cut.rfind(" ")
    return cut[:space] if space > 0 else cut


def context_for(line: str, start: int, end: int) -> str:
    """A few words either side of a reference, on one line.

    The reference is shown **where it sits** between them rather than restated: a
    reader who sees `deploy/<gate>.py` in the middle of the sentence that names it
    has the edit in front of them, and one who sees only a line number has a
    lookup.
    """
    def squash(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    before, after = squash(line[:start]), squash(line[end:])
    head = ("..." + _clip(before, CONTEXT, from_end=True)
            if len(before) > CONTEXT else before)
    tail = (_clip(after, CONTEXT, from_end=False) + "..."
            if len(after) > CONTEXT else after)
    return " ".join(part for part in (head, squash(line[start:end]), tail) if part)


def tracked(repo: Path) -> list[str]:
    """The files git tracks under `repo`, as repository-relative paths."""
    env = {k: v for k, v in os.environ.items() if k not in GIT_REPO_ENV}
    try:
        done = subprocess.run(
            ["git", "-C", str(repo), "ls-files"],
            capture_output=True, text=True, check=True, env=env,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise Measure(f"no tracked-file list in {repo}: {exc}") from exc
    return [line.strip() for line in done.stdout.splitlines() if line.strip()]


def top_level(paths: list[str]) -> set[str]:
    """The directories at the root of the tree git tracks.

    From git and not from the filesystem on purpose: `node_modules/` exists on a
    developer's box and in no clone, so a set read off the disk would make the
    verdict depend on who asks — the one failure this repository keeps finding.
    """
    return {p.split("/")[0] for p in paths if "/" in p}


def normalise(raw: str) -> str:
    """A span or a link destination as a path, or "" when it is not one.

    Percent-encoding is decoded **last**, after the whitespace rule: for example
    `docs/name%20with%20space.md` is a link to a file with a space in its name, and
    decoding it first made the space look like prose rather than a path.
    """
    text = raw.strip().strip("`'\"<>")
    # A fragment or a query is not part of the path: `#L42`, `?tab=2`.
    text = text.split("#", 1)[0].split("?", 1)[0]
    # pytest node ids and the two ways this repo points at a symbol.
    text = text.split("::", 1)[0]
    text = re.sub(r":\d+(?:[-–]\d+)?$", "", text)      # `file.py:437`
    text = re.sub(r":[\w_]+\(\)?$", "", text)          # `file.py:func()`
    text = re.sub(r"\(\)$", "", text)                  # `module.func()` → `module.func`
    if not text or text.endswith("/"):
        return ""
    if any(ch in BAD_CHARS for ch in text):
        return ""
    if " " in text or text.startswith(("/", "~")):
        return ""
    if SCHEME.match(text) or re.match(r"^[A-Za-z]:[\\/]", text):
        return ""
    return unquote(text)


def slug(heading: str) -> str:
    """The fragment a browser scrolls to for this heading, as GitHub makes it.

    Lower-cased; every character that is not a letter, a digit, `-`, `_` or a space
    is **dropped rather than replaced** — which is why `## Peran, Hak Akses & Sesi`
    is `#peran-hak-akses--sesi`, the comma and the ampersand leaving their two
    spaces behind as a double hyphen. Markup goes first, because the anchor is the
    rendered text: a heading that wraps a filename in backticks and a word in
    asterisks slugs as the two words and their `and`.
    """
    text = LINK_TEXT.sub(r"\1", heading)
    text = MARKUP.sub("", text)
    kept = "".join(ch for ch in text.strip().lower()
                   if ch.isalnum() or ch in "-_ ")
    return kept.replace(" ", "-")


def anchors_in(text: str) -> set[str]:
    """Every fragment a page can be linked to.

    Headings outside fenced blocks (a `# comment` in a shell sample is a comment,
    not a heading — the same rule the page reader already uses), a repeated heading
    suffixed `-1`, `-2`… the way GitHub numbers them, and any anchor the page writes
    into its own HTML. An anchor nobody declared is the one a renamed heading leaves
    behind, which is the whole point of the index.
    """
    out: set[str] = set()
    seen: dict[str, int] = {}
    fenced = False
    for line in text.splitlines():
        if FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        match = HEADING.match(line)
        if not match:
            continue
        base = slug(match.group(2))
        count = seen.get(base, 0)
        seen[base] = count + 1
        out.add(base if not count else f"{base}-{count}")
    out.update(match.group(1) for match in EXPLICIT_ANCHOR.finditer(text))
    return out


def page_named(repo: Path, doc_rel: str, path: str,
               index: tuple[set[str], set[str]]) -> str | None:
    """The tracked **file** a link names, or None when it names a directory.

    A different question from `resolve`, which answers "does this exist": a
    directory exists and has no headings, so a fragment on one is checked against
    nothing rather than refused.
    """
    files, _dirs = index
    for candidate in (posixpath.normpath(posixpath.join(posixpath.dirname(doc_rel), path)),
                      posixpath.normpath(path)):
        if candidate in files:
            return candidate
    return None


def first_segment(path: str) -> str:
    """The first segment that names a directory in the tree.

    `../deploy/<gate>.py` and `./deploy/<gate>.py` are how a page links to a sibling area,
    and reading the leading `..` as the segment made both of them invisible to the
    rule that decides what a path is — the commonest shape of link in this
    repository's own docs.
    """
    rest = path
    while rest.startswith("../"):
        rest = rest[3:]
    if rest.startswith("./"):
        rest = rest[2:]
    return rest.split("/")[0]


def extension_of(path: str) -> str:
    """The last segment's extension, lower-cased, or ""."""
    last = path.rsplit("/", 1)[-1]
    if "." not in last.lstrip("."):
        return ""
    return "." + last.rsplit(".", 1)[-1].lower()


def symbol_base(path: str) -> str:
    """`deploy/schema_contract.references` → `deploy/schema_contract`.

    A suffix that is not a known extension is a symbol or an artifact (`…py.patch`),
    and the reference is really to the file it decorates. Its own spelling is not
    checked — that is the limit named in the module docstring.
    """
    if extension_of(path) in KNOWN_EXT:
        return path
    last = path.rsplit("/", 1)[-1]
    if "." in last:
        return path[: path.rfind(".")]
    return path


def is_assertion(path: str) -> bool:
    """Whether an inline-code span is a claim that a file is there.

    A known extension is one. So is a symbol or an artifact suffix on a file that
    has one (`auth._resolved_school` → `auth.py`, `…admin_sekolah.py.patch` →
    `…admin_sekolah.py`). A suffix that is data (`.pdf`), an intent file a tool
    writes at runtime (`.intent`), or nothing at all (`feature/description`, a git
    branch template) is not — and neither is an extension-less module name, which
    this repository writes in prose (`app/utils/cache`). That last limit is real:
    a *misspelled* module name is not caught, and it is stated rather than papered
    over, because the alternative — treating every dotted word as a path — is the
    false-positive machine this guard exists not to be.
    """
    if extension_of(path) in KNOWN_EXT:
        return True
    return extension_of(symbol_base(path)) in KNOWN_EXT


def index_of(paths: list[str]) -> tuple[set[str], set[str]]:
    """The files this repository tracks, and every directory that holds one.

    Resolution asks the **repository**, not the disk, and that is the honest
    question first. The disk answers a different one — it says yes for
    `node_modules/`, for a leftover in `app/static/`, for anything a developer
    happens to have — while a page that points there is still broken on the box,
    whose checkout is a clone. It is also the cheap one: measured on this tree, a
    run took **486 ms** with a `Path.exists()` per candidate and **327 ms** with two
    set lookups, and the index itself is built once in 0.8 ms (`tracked()` 20 ms,
    the 69 documents read and matched 278 ms). The remainder is not resolution at
    all — `AGENTS.md` alone is 1.2 MB and both patterns run over every line of it —
    which is worth knowing before optimising the wrong half.
    """
    files = set(paths)
    dirs: set[str] = set()
    for rel in paths:
        parts = rel.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            dirs.add("/".join(parts[:i]))
    return files, dirs


def resolve(repo: Path, doc_rel: str, path: str, literal: bool = False,
            index: tuple[set[str], set[str]] | None = None) -> bool:
    """Whether `path`, as written in `doc_rel`, names something the repo tracks.

    A **link** is resolved verbatim: its destination is a path, and a destination
    ending in `.pdf` is a claim that the PDF is there, not a symbol on a module.
    An inline-code span may name a symbol or a derived artifact, so it is resolved
    through its base (`deploy/schema_contract.references` → `deploy/schema_contract`).
    """
    files, dirs = index if index is not None else index_of(tracked(repo))
    base = path if literal else symbol_base(path)
    for candidate in (posixpath.normpath(posixpath.join(posixpath.dirname(doc_rel), base)),
                      posixpath.normpath(base)):
        if candidate in files or candidate in dirs:
            return True
        if not literal and not extension_of(base) and candidate + ".py" in files:
            return True
    return False


def _heading_missing(repo: Path, rel: str, path: str, fragment: str, kind: str,
                     local: bool, index: tuple[set[str], set[str]],
                     anchors: dict[str, set[str]]) -> str:
    """Why the fragment a link carries is not a heading on the page, or "".

    Only a **link** carries one, and only a page has headings: `<file>.py#L42` is a
    line GitHub scrolls to, and `<document>.html#anchor` is an address inside a
    document this tool does not parse. Both are named as limits, not guessed at.
    """
    if kind != "link" or not fragment or LINE_ANCHOR.match(fragment):
        return ""
    page = rel if local else (page_named(repo, rel, path, index) if path else None)
    if not page or not page.endswith(".md") or page not in anchors:
        return ""
    if fragment in anchors[page]:
        return ""
    where = page if page != rel else "this page"
    return (f"no heading `#{fragment}` on {where} — the section it named was "
            "renamed or removed")


def load_exceptions(path: Path) -> dict[tuple[str, str], str]:
    """The recorded exceptions: (doc, path) → why.

    A malformed record is a defect in the release (exit 1), because the file is
    checked in and a reader has to be able to trust every line of it. A file that
    cannot be read at all is a property of the box (exit 2).
    """
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise Measure(f"cannot read {path}: {exc}") from exc
    out: dict[tuple[str, str], str] = {}
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 3 or not all(p.strip() for p in parts[:3]):
            raise ValueError(
                f"{path.name}:{number}: expected `<doc>\\t<path>\\t<reason>`")
        out[(parts[0].strip(), parts[1].strip())] = parts[2].strip()
    return out


def _gone(text: str) -> bool:
    low = text.lower()
    return any(word in low for word in GONE)


def _example(text: str) -> bool:
    """Whether the copy says this name is a shape rather than a file."""
    low = text.lower()
    return any(word in low for word in EXAMPLE)


def shape_of(rel: str) -> str:
    """What kind of copy `rel` carries, or "" when nothing is read from it.

    `tests/` answers "" on purpose: a test builds fixture trees, and a docstring
    there naming a path in *one of them* is not a claim about this checkout. The
    scratch corner is not read for the reason the pages are not — a probe in some
    checkout is not a file a clone has.
    """
    if rel.startswith(("tests/",) + SCRATCH):
        return ""
    if rel.endswith(".py"):
        return "python"
    if rel.endswith(".html") and rel.startswith(TEMPLATED):
        return "template"
    if rel.endswith((".sh", ".ps1")):
        return "shell"
    return ""


def _template_copy(source: str) -> list[tuple[int, str, bool]]:
    """Jinja and HTML comment bodies, with the line each one falls on.

    Comments and not the whole template: a template's other paths are addresses
    (`/static/js/x.js`), which are what the browser fetches rather than what this
    checkout holds, and reading 149 templates' HTML would add one false positive
    per `src=` for no coverage. The comment is where a template explains itself.
    """
    out: list[tuple[int, str, bool]] = []
    consumed, line = 0, 1
    for match in re.finditer(r"\{#(.*?)#\}|<!--(.*?)-->", source, re.S):
        line += source.count("\n", consumed, match.start())
        consumed = match.start()
        body = match.group(1) if match.group(1) is not None else match.group(2)
        for offset, text in enumerate(body.splitlines()):
            out.append((line + offset, text, False))
    return out


def _every_line(source: str) -> list[tuple[int, str, bool]]:
    """Every line of a shell script: nothing on it is merely prose.

    A comment and a command that runs a tool are both claims here — the `bash
    deploy/<gate>.sh` line is a use of a file, and the `FAILED:` message above it
    names the same one — and neither is rendered or fails until it runs, on a box,
    at the worst moment. No line is a snippet: a shell script *is* the command
    list, so "it starts with a command" says nothing about a message inside it.
    """
    return [(number, line, False)
            for number, line in enumerate(source.splitlines(), 1)]


def _python_copy(source: str) -> list[tuple[int, str, bool]]:
    """Every line of a Python file — comment, docstring or failure string.

    Not just the comments, because the copy is not the only place a tool is named:
    `print("run deploy/<tool>.py")` is a claim about this checkout in its own
    right, and it is the kind that fails on a box rather than in review. A line
    that **opens with a command** is the exception: it is showing what somebody
    would type, the way a fenced block on a page is, and
    `supabase/migrations/<stamp>_<name>.sql` is the name *you* will write.
    """
    return [(number, line, bool(COMMAND.match(line)))
            for number, line in enumerate(source.splitlines(), 1)]


COPY = {
    "python": _python_copy,
    "shell": _every_line,
    "template": _template_copy,
}


def _copy_lines(rel: str, text: str) -> list[tuple[int, str, bool]]:
    """The copy in one file, as (line number, line, is-a-snippet)."""
    shape = shape_of(rel)
    if not shape:
        return []
    return COPY[shape](text)


def _page_lines(text: str):
    """(number, line, snippet, gone-section) for every line a page is judged on.

    Fenced blocks are skipped and headings are state: the nearest heading and its
    ancestors decide whether the copy under it says a file is gone, which is what
    makes the runbook's whole `### dropped — branch-only, superseded by main`
    table exempt by its own heading.
    """
    headings: list[tuple[int, str]] = []
    fenced = False
    for number, line in enumerate(text.splitlines(), 1):
        if FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        match = HEADING.match(line)
        if match:
            level = len(match.group(1))
            headings = [h for h in headings if h[0] < level]
            headings.append((level, match.group(2)))
            continue
        yield number, line, False, any(_gone(body) for _, body in headings)


def _line_refs(line: str, markdown: bool) -> list[tuple[str, str, int, int]]:
    """The reference shapes on one line, as (kind, raw, start, end).

    A page has two shapes and comment copy has a third. The bare shape is read
    **off** a page only: `[x](y)` and `` `y` `` are how a page marks a path, so a
    bare `deploy/<gate>.sh` there is prose that happens to look like a path.
    """
    found = [("link", m.group(1), m.start(), m.end()) for m in LINK.finditer(line)]
    if markdown:
        definition = LINK_DEF.match(line)
        if definition:
            found.append(("link", definition.group(1),
                          definition.start(1), definition.end(1)))
    spans = [m for m in CODE.finditer(line)]
    found.extend(("path", m.group(1), m.start(), m.end()) for m in spans)
    if not markdown:
        for match in BARE.finditer(line):
            # A path inside backticks is already read as a span; reading it twice
            # would count one claim as two and, worse, print both.
            if any(m.start(1) <= match.start(1) and match.end(1) <= m.end(1)
                   for m in spans):
                continue
            found.append(("path", match.group(1), match.start(1), match.end(1)))
    return found


def scan(repo: Path | str = ROOT, docs: list[str] | None = None,
         known_dirs: set[str] | None = None,
         exceptions: dict[tuple[str, str], str] | None = None) -> Report:
    """Every reference in every page and in every file's copy, resolved.

    One reading for both halves: a page's line and a docstring's line are the same
    claim with the same rules, and the only differences are the shapes (a page has
    links and backticks, this copy also writes paths bare) and two exemptions it
    needs (a line that opens with a command is showing one, and a line that says
    "for example" is naming a shape).
    """
    repo = Path(repo)
    paths = tracked(repo) if docs is None else list(docs)
    known_dirs = top_level(paths) if known_dirs is None else set(known_dirs)
    index = index_of(paths)
    exceptions = {} if exceptions is None else dict(exceptions)
    report = Report()
    report.docs = sorted(p for p in paths if p.endswith(".md"))
    report.sources = sorted(p for p in paths if shape_of(p))
    if not report.docs:
        raise Measure(f"no tracked markdown under {repo}")

    # The pages are read before they are judged, because a link's fragment is
    # checked against **another** page's headings: `[x](<page>.md#the-cases)` on page
    # `a` is a claim about `b`, and `b` may come later in the list. One read, then one
    # index, and the loop below sees the same text either way.
    texts: dict[str, str] = {}
    for rel in report.docs:
        try:
            texts[rel] = (repo / rel).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise Measure(f"cannot read {rel}: {exc}") from exc
    anchors = {rel: anchors_in(text) for rel, text in texts.items()}

    named: set[tuple[str, str]] = set()
    for rel in report.docs + report.sources:
        if rel in texts:
            text = texts[rel]
        else:
            try:
                text = (repo / rel).read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                raise Measure(f"cannot read {rel}: {exc}") from exc
        page = rel.endswith(".md")
        lines = (_page_lines(text) if page
                 else ((number, line, snippet, False)
                       for number, line, snippet in _copy_lines(rel, text)))
        previous = ""
        for number, line, snippet, section_gone in lines:
            for kind, raw, start, end in _line_refs(line, markdown=page):
                path = normalise(raw)
                # A link may carry a fragment: `[x](<page>.md#the-cases)`, or `[x](#top)`
                # on the page itself. The path half is resolved exactly as before;
                # the fragment is a claim about a **heading**, which is the part a
                # rename breaks silently — the page still opens, at the top.
                target_name, _, fragment = raw.partition("#") if kind == "link" \
                    else (raw, "", "")
                fragment = unquote(fragment)
                local = kind == "link" and not target_name and bool(fragment)
                if not path and not local:
                    continue
                # A **link** is resolved wherever it points, without the
                # top-level-directory filter a code span needs: a span has to be
                # recognised as a path before it can be judged, while a link has
                # already said so — and `[x](<page>.md#cases)` next to the page was
                # the commonest way to write one, which that filter made invisible
                # until the fragment needed it.
                if path and kind != "link" and first_segment(path) not in known_dirs:
                    continue
                resolvable = local or resolve(repo, rel, path,
                                              literal=(kind == "link"), index=index)
                if kind == "link" and path and not (
                        is_assertion(path) or "/" in path or resolvable):
                    # A destination that is neither a known extension, nor a
                    # directory, nor anything this repository has is a **shape**:
                    # `[x](<path>)` is how prose draws a link, and `COPY[shape](text)`
                    # in a source file is a subscript followed by a call that the
                    # link pattern reads as one.
                    continue
                if kind == "path" and not is_assertion(path) and not resolvable:
                    # A data file, a runtime artifact, or a module name: this repo
                    # writes all three in prose and none of them is a claim. One
                    # that *does* resolve is still read (and counted) — the module
                    # names this repo writes in prose are `app/utils/cache`
                    # shaped, and `cache.py` is the file they mean.
                    continue
                # The words **around** the reference, never inside it: a file is
                # allowed to be called `test_gone.py` without the guard reading its
                # own name as the sentence that says it was deleted.
                # Comment copy wraps where a page does not: a message built in a
                # docstring or a `print(...)` continues on the next line, so the
                # sentence that says "e.g." about this path can be one line up.
                # Pages keep one line — `AGENTS.md`'s bullets are already the
                # measurement that a wider window exempts real references.
                above = "" if page else previous[-NEAR:] + " "
                near = (above + line[max(0, start - NEAR): start] + " "
                        + line[end:end + NEAR])
                ref = Ref(rel, number, kind, raw)
                ref.path = path or f"#{fragment}"
                named.add((rel, ref.path))
                if rel.startswith(PLANNING) or path.startswith(SCRATCH):
                    ref.state, ref.why = "exempt", "scratch or planning page"
                elif snippet:
                    ref.state, ref.why = "exempt", "a snippet shows a command"
                elif section_gone or _gone(near):
                    ref.state, ref.why = "exempt", "the copy says it is gone"
                elif not page and _example(near):
                    ref.state, ref.why = "exempt", "the copy says it is an example"
                elif (rel, ref.path) in exceptions:
                    ref.state, ref.why = "exempt", exceptions[(rel, ref.path)]
                elif resolvable and (heading := _heading_missing(
                        repo, rel, path, fragment, kind, local, index, anchors)):
                    # The page is there and the anchor is not: the reader lands at
                    # the top of a page that no longer says what the link promised.
                    ref.state = "missing"
                    ref.why = heading
                    ref.context = context_for(line, start, end)
                    report.missing.append(ref)
                elif resolvable:
                    ref.state = "resolved"
                else:
                    ref.state = "missing"
                    # Only where it will be printed: a resolved reference has no
                    # refusal to make one-line-reading-easier for, and this scan
                    # reads 1500 of them on every commit.
                    ref.context = context_for(line, start, end)
                    report.missing.append(ref)
                report.refs.append(ref)
            previous = line

    for (doc, path), reason in exceptions.items():
        where = f"{doc}:{path}"
        if (doc, path) not in named:
            report.stale.append((where, path, f"no longer named by {doc} — {reason}"))
        elif resolve(repo, doc, path, index=index):
            report.stale.append((where, path, "the path is tracked again — drop the exception"))
    report.exempt = sum(1 for r in report.refs if r.state == "exempt")
    return report


def summarise(report: Report) -> str:
    """What was read, not only what was found.

    The count of files is here because a run that read the pages and none of the
    comment copy would otherwise print the same first number and look complete.
    """
    return (f"doc links: {len(report.docs)} document(s) and "
            f"{len(report.sources)} file(s) of comment copy, {report.checked} "
            f"reference(s) checked, {report.exempt} exempt, "
            f"{len(report.missing)} unresolved")


def print_offenders(report: Report) -> None:
    """The detail goes with the refusal, on the same stream as the verdict."""
    for ref in report.missing:
        label = "link" if ref.kind == "link" else "path"
        verdict = f"  — {ref.why}" if ref.why else ""
        print(f"    {_safe(ref.doc)}:{ref.line}  [{label}]  {_safe(ref.raw)}{_safe(verdict)}",
              file=sys.stderr)
        if ref.context:
            # The page's own words, so the fix is one edit away rather than a line
            # number to go and look up.
            print(f"        {printable(ref.context)}", file=sys.stderr)
    for where, path, why in report.stale:
        print(f"    {_safe(where)}  — {_safe(why)}", file=sys.stderr)


def show(repo: Path, doc: str) -> int:
    """Every reference in one document, with the verdict on each."""
    report = scan(repo)
    rows = [r for r in report.refs if r.doc == doc]
    if not rows:
        print(f"doc links: no references in {doc}")
        return 0
    for ref in rows:
        mark = {"resolved": "ok", "missing": "MISSING", "exempt": "exempt"}[ref.state]
        note = f"  ({ref.why})" if ref.why else ""
        print(f"  {_safe(ref.doc)}:{ref.line}  {mark:<8} {_safe(ref.path)}{note}")
    return 0 if not report.missing else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check that documented links and paths resolve.")
    parser.add_argument("--repo", default=str(ROOT),
                        help="the checkout to read (default: this one)")
    parser.add_argument("--show", metavar="DOC",
                        help="print every reference in one document, with its verdict")
    parser.add_argument("--exceptions", default=str(EXCEPTIONS),
                        help="the recorded exceptions (default: deploy/doc_links_gone.txt)")
    args = parser.parse_args(argv)

    # A console that cannot encode a reference must not be the reason a release
    # looks refused, and must not be the reason a refusal is unreadable.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, ValueError):  # pragma: no cover - older streams
            pass

    repo = Path(args.repo).resolve()
    try:
        if args.show:
            return show(repo, args.show)
        exceptions = load_exceptions(Path(args.exceptions).resolve())
        report = scan(repo, exceptions=exceptions)
    except Measure as exc:
        print(f"doc links: could not measure — {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"doc links: FAILED — {exc}", file=sys.stderr)
        return 1

    print(summarise(report))
    if report.offenders():
        print("doc links: FAILED — a page, a docstring or a gate's own message "
              "names a path this repository does not track.", file=sys.stderr)
        print_offenders(report)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
