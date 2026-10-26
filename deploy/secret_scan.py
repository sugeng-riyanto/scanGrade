#!/usr/bin/env python3
"""A literal that looks like a credential is refused at the commit, not at review.

`tests/unit/test_no_committed_secrets.py` answers one question — is a live
service-role key or a hard-coded Flask secret in this tree — and it answers it
well. It could not answer the question that actually cost a day: a **test
fixture** written as `scheme://someone:<password>@db.example.edu`. That is not a
key anyone can use and it was still a finding, because a scanner cannot tell a
fixture from a credential. It went up in a pull request, GitHub's own scanner failed the
check, and the literal had to be removed from the branch's **history** — a
follow-up commit does not clear a finding that lives in a commit already pushed.

The lesson is about *where* the check runs, not how strict it is. GitGuardian runs
on the pull request, which is after the branch exists; the pre-commit hook ran the
theme gate only for staged paths matching a filter, and `tests/` was not in that
filter. So a commit that added one test file was scanned by nothing at all.

This tool is what the hook runs, unconditionally, before that filter. It reads the
**index** — what is being committed — rather than the working tree, because a
half-staged file is exactly the case where the two disagree, and the committed
half is the one that ships.

## What it looks for

The shapes whose presence is the finding, so no entropy guess has to be made:

* a URL carrying userinfo — `scheme://<user>:<password>@host`. This is the one that
  shipped, and it is the commonest way a real credential ends up in a fixture, a
  README or a curl example;
* a private key block — the `BEGIN` line **and** a body under it. A header on its
  own is a string that grants nothing and is a fixture in this repo already;
* a token with a provider's own prefix (`sk-…`, `ghp_…`, `xoxb-…`, `AKIA…`,
  `sbp_…`, `AIza…`, `glpat-…`), which is a key by construction;
* a `Bearer <token>` of realistic length;
* a JWT whose payload says `service_role` and whose signature is long enough to be
  real — the same shape the tracked-file guard reads, and the reason a
  two-character fixture signature is not a finding here either.

## What is deliberately not a finding

The URL rule needs one carve-out or it is unusable: a pooler DSN whose role and
password are both the default `postgres` appears throughout this repository's own
tests, and every one of those is a well-known default that grants nothing. So a userinfo whose password is a
**documented placeholder** — `postgres`, `pass`, `pw`, `private`, `changeme`, …
— or the same word as the user, or a template expansion (`$DB_PASSWORD`,
`${PASS}`, `{{ password }}`) is not reported. That is the same reasoning the
tracked-file guard already applies to a two-character JWT signature: a value that
cannot be a credential is not a leak, and a check that fails on one gets deleted.

The line that cost the day — `someone:hunter2` — is neither a placeholder nor an
expansion, so it is reported by both this tool and the scanner that caught it late.

## The one allowance, and why it is not a list

The tool's own tests have to contain a credential-shaped literal, or they prove
nothing. That line carries `secret-scan:allow <reason>`, and a bare marker is
itself a finding: an exemption has to say what it is exempting and why, on the
line it exempts. There is no allow-file, because a file of paths is how a guard
quietly stops covering the directory that was added to it afterwards.

## What it prints, and what it must never print

`path:line` and the detector's name, with every match on the line replaced by
`<redacted>`. A tool that refuses a commit *because a secret exists* must not put
that secret into a terminal, a log or a pasted message — the report is the second
place it would leak.

Exit codes follow the other gates here: 0 clean · 1 a credential-shaped literal is
staged · 2 could not measure (not a git repository, or git will not answer), which
is a property of the box and refuses nothing on its own.
"""
from __future__ import annotations

import argparse
import base64
import json
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]

#: The marker an exempt line carries, and the shortest reason accepted after it.
#: A reason is required because `secret-scan:allow` alone is a suppression with
#: nobody's name on it, which is the shape every forgotten exemption starts as.
ALLOW = "secret-scan:allow"
MIN_REASON = 12

#: Passwords that are a published default or a fixture by convention. A URL
#: carrying one grants nothing, and refusing it would make the check fail on
#: sixteen lines of this repository's own tests — which is how a guard becomes
#: something people pass `--no-verify` to avoid.
PLACEHOLDERS = frozenset({
    "postgres", "password", "pass", "passwd", "pw", "pwd", "secret", "user",
    "username", "private", "hidden", "changeme", "redacted", "example", "demo",
    "test", "none", "xxx", "token", "apikey", "api_key", "key", "fake",
})

#: A URL with credentials in it. The match stops at the `@`, so the host stays
#: visible in the report — it is the part a reader needs to find the line, and it
#: is not the part that grants access.
_CREDENTIAL_URL = re.compile(
    r"[A-Za-z][A-Za-z0-9+.\-]*://([^\s:/@'\"<>]+:[^\s/@'\"<>]+)@")

#: A key block: the BEGIN line followed by a body of base64. A header with nothing
#: under it is a test string, not a key, and this repository already has one.
_PRIVATE_KEY_BLOCK = re.compile(
    r"(-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----)[\r\n]+[A-Za-z0-9+/=]{40,}")

#: Prefixes that are a key by construction, so matching one needs no entropy
#: argument. Deliberately a list of published shapes rather than a guess at what
#: looks random: a heuristic here fails on a base64 fixture and passes on a short
#: real key.
_PROVIDER_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"sk_live_[A-Za-z0-9]{16,}|rk_live_[A-Za-z0-9]{16,}|sk-[A-Za-z0-9]{20,}|"
    r"gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|"
    r"sbp_[0-9a-f]{40}|AIza[0-9A-Za-z_\-]{35}|glpat-[A-Za-z0-9_\-]{20,}"
    r")(?![A-Za-z0-9])")

#: A database role with its password, **outside** a URL: `postgres.abcdef:pw`.
#: The URL rule above needs a scheme, and this shape does not have one — it is how
#: a DSN gets quoted in a docstring, a commit message or a test's own assertion.
#: Narrowed to the `postgres` role on purpose: a bare `word:word` is a dict literal,
#: a type annotation and an Indonesian label far more often than it is a password,
#: and a rule that matches all of them is a rule nobody keeps switched on. This is
#: the exact shape GitHub's own scanner failed a pull request for.
_DSN_CREDENTIAL = re.compile(
    r"(?<![A-Za-z0-9_])((?:postgres(?:\.[A-Za-z0-9_]+)?):[^\s@/'\"`,;)\]]+)")

_BEARER = re.compile(r"(?<![A-Za-z0-9])Bearer\s+[A-Za-z0-9\-._~+/]{32,}=*")

#: A three-part JWT whose signature is long enough to be a real one. A short
#: stand-in grants nothing — `app/config.py` carries exactly such a fixture — so
#: the role claim is only read for a token that could actually be signed.
_JWT = re.compile(
    r"(?<![A-Za-z0-9])(eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{20,})(?![A-Za-z0-9])")

#: `(detector name, pattern)` for the per-line rules, in report order. The private
#: key is not here because its rule spans two lines.
#: The rules that need no per-match judgement — a token with a provider's own
#: prefix is a key by construction. The two `user:password` rules are handled in
#: :func:`_spans_by_line` because each has to be told a placeholder from a value.
DETECTORS = (
    ("provider-token", _PROVIDER_TOKEN),
    ("bearer-token", _BEARER),
)

#: What a template expansion looks like where a password would be. An expansion is
#: read at run time from the environment; the committed bytes carry no credential.
_EXPANSION = re.compile(
    r"^\$\{?[A-Za-z_][A-Za-z0-9_]*\}?$"
    r"|^\{[A-Za-z_][A-Za-z0-9_.]*\}$"      # an f-string field: `{password}`
    r"|^\{\{.*\}\}$"                      # a Jinja pair: `{{ password }}`
    r"|^%[s(].*$"
    r"|^<.*>$|^\[.*\]$")


def _role_of(token: str) -> str | None:
    """The `role` claim of a JWT payload, or None if it cannot be read."""
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part)).get("role")
    except Exception:
        return None


def is_placeholder_userinfo(userinfo: str) -> bool:
    """Is `user:password` a published default, a fixture or an expansion?

    Three shapes, each one a value that cannot open anything: the two halves are
    the same word (`postgres:postgres`, `user:user`), the password is in
    :data:`PLACEHOLDERS`, or it is a template expansion the commit does not
    contain. Everything else is reported, including `someone:hunter2`.
    """
    user, _, password = userinfo.partition(":")
    if not password:
        return True
    password = password.strip("'\"`")
    if password.isdigit():
        return True      # `postgres:5432` is a host and a port, not a password
    if _EXPANSION.match(password):
        return True
    lowered = password.lower()
    if lowered in PLACEHOLDERS:
        return True
    # A DSN's default is the role it connects as — `postgres:postgres`,
    # `scangrade:scangrade`. Real credentials are not the account's own name.
    return lowered == user.strip("'\"`").lower()


def _outer(spans: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    """Drop a match that sits inside a larger one: the larger span decides."""
    kept: list[tuple[int, int, str]] = []
    for span in sorted(spans, key=lambda s: (s[0], -s[1])):
        if kept and span[1] <= kept[-1][1]:
            continue
        kept.append(span)
    return kept


def _spans_by_line(text: str) -> dict[int, list[tuple[int, int, str]]]:
    """Every credential-shaped span in this text, keyed by 1-based line number.

    Positions are offsets **within that line**, which is what the excerpt needs.
    """
    lines = text.splitlines()
    spans: dict[int, list[tuple[int, int, str]]] = {n: [] for n in range(1, len(lines) + 1)}
    for number, line in enumerate(lines, start=1):
        # The URL rule first, and its matches kept even when their userinfo is a
        # placeholder: a role-and-password pair *inside* one is that URL's
        # business, and judging it twice is how a fill-me-in password inside a
        # DSN became a finding through the second rule after the first cleared it.
        urls = [(m.start(), m.end(), m.group(1))
                for m in _CREDENTIAL_URL.finditer(line)]
        for start, end, userinfo in urls:
            if not is_placeholder_userinfo(userinfo):
                spans[number].append((start, end, "credential-url"))
        for match in _DSN_CREDENTIAL.finditer(line):
            if any(s <= match.start() and match.end() <= e for s, e, _ in urls):
                continue
            if is_placeholder_userinfo(match.group(1)):
                continue
            spans[number].append((match.start(), match.end(), "dsn-credential"))
        for name, pattern in DETECTORS:
            for match in pattern.finditer(line):
                spans[number].append((match.start(), match.end(), name))
        for match in _JWT.finditer(line):
            if _role_of(match.group(1)) == "service_role":
                spans[number].append((match.start(), match.end(), "service-role-jwt"))
    for match in _PRIVATE_KEY_BLOCK.finditer(text):
        number = text.count("\n", 0, match.start()) + 1
        start = match.start() - (text.rfind("\n", 0, match.start()) + 1)
        # Report the BEGIN line: the body is 60 lines of base64 nobody needs
        # printed, and the marker is the whole fix.
        spans.setdefault(number, []).append((start, start + len(match.group(1)), "private-key"))
    return {n: _outer(found) for n, found in spans.items() if found}


def _reason(line: str) -> str | None:
    """The reason given after the allowance marker, if there is one."""
    at = line.find(ALLOW)
    if at < 0:
        return None
    rest = line[at + len(ALLOW):].lstrip(":").strip()
    # A trailing comment, quote or bracket is the line's, not the reason's.
    rest = rest.strip(" \t\"'`*/#>")
    return rest or None


def _excerpt(line: str, spans: list[tuple[int, int, str]]) -> str:
    """The line with every credential-shaped span replaced by `<redacted>`.

    Redaction is applied to *all* matches rather than only the one being reported:
    a line with two credentials reports the first, and printing the line as it is
    would leak the second into the very message that refuses the commit.
    """
    out, cursor = [], 0
    for start, end, _ in sorted(spans):
        if start < cursor:
            continue
        out.append(line[cursor:start])
        out.append("<redacted>")
        cursor = end
    out.append(line[cursor:])
    text = "".join(out).strip()
    return text if len(text) <= 160 else text[:157] + "..."


class Finding:
    """One refused line: where it is, what shape it is, and the redacted text."""

    __slots__ = ("path", "line_no", "detector", "excerpt", "why")

    def __init__(self, path, line_no, detector, excerpt, why=""):
        self.path = path
        self.line_no = line_no
        self.detector = detector
        self.excerpt = excerpt
        self.why = why

    def render(self) -> str:
        text = f"  {self.path}:{self.line_no}  {self.detector}"
        if self.excerpt:
            text += f"\n      {self.excerpt}"
        if self.why:
            text += f"\n      {self.why}"
        return text


def scan_text(path: str, text: str) -> list[Finding]:
    """Every refusal in this text, including an allowance that gives no reason."""
    lines = text.splitlines()
    findings: list[Finding] = []
    for number, spans in sorted(_spans_by_line(text).items()):
        line = lines[number - 1]
        if ALLOW in line:
            reason = _reason(line)
            if reason is not None and len(reason) >= MIN_REASON:
                # An exemption with a written reason is the one case the line is
                # not read: the marker is the author saying "this one is a fixture,
                # and here is why".
                continue
            findings.append(Finding(
                path, number, "bare-allowance", _excerpt(line, spans),
                f"`{ALLOW}` needs a reason of at least {MIN_REASON} characters — an "
                f"exemption nobody has to explain is one nobody reviews"))
            continue
        findings.append(Finding(path, number, sorted(spans)[0][2], _excerpt(line, spans)))
    # A marker on a line with no finding is not reported: it is an exemption that
    # has gone stale, which is a thing to remove but not a reason to refuse.
    return findings


def _git(args: list[str], root: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(root), capture_output=True)


def _decode(blob: bytes) -> str | None:
    """The text of a blob, or None for one that is not text.

    A NUL byte is git's own test for binary, and scanning a PNG for a URL would
    only produce noise that teaches a reader to ignore this report.
    """
    if b"\0" in blob:
        return None
    return blob.decode("utf-8", errors="ignore")


def staged(root: pathlib.Path) -> list[tuple[str, str]]:
    """The staged content of every added or modified path — the index, not the disk."""
    listed = _git(["diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"], root)
    if listed.returncode != 0:
        raise RuntimeError("git will not list the staged paths: "
                           + listed.stderr.decode("utf-8", errors="replace").strip())
    files = []
    for rel in listed.stdout.decode("utf-8", errors="replace").split("\0"):
        rel = rel.strip()
        if not rel:
            continue
        # `git show :path` reads the blob in the index — what this commit will
        # contain — which is the only version the check is about.
        blob = _git(["show", f":{rel}"], root)
        if blob.returncode != 0:
            raise RuntimeError(f"git will not read the staged {rel}: "
                               + blob.stderr.decode("utf-8", errors="replace").strip())
        text = _decode(blob.stdout)
        if text is not None:
            files.append((rel, text))
    return files


def tracked(root: pathlib.Path) -> list[tuple[str, str]]:
    """Every tracked text file, read from the checkout.

    Read through `git ls-files` rather than by walking the disk: a scratch file in
    a working tree is not something a clone of this release has, so judging it
    would make the answer depend on whose laptop ran it.
    """
    listed = _git(["ls-files", "-z"], root)
    if listed.returncode != 0:
        raise RuntimeError("git will not list the tracked files: "
                           + listed.stderr.decode("utf-8", errors="replace").strip())
    files = []
    for rel in listed.stdout.decode("utf-8", errors="replace").split("\0"):
        rel = rel.strip()
        if not rel:
            continue
        try:
            blob = (root / rel).read_bytes()
        except OSError:
            continue
        text = _decode(blob)
        if text is not None:
            files.append((rel, text))
    return files


def from_paths(root: pathlib.Path, paths: list[str]) -> list[tuple[str, str]]:
    """Named files, read from the checkout — for a caller that already knows them."""
    files = []
    for rel in paths:
        try:
            blob = (root / rel).read_bytes()
        except OSError as exc:
            raise RuntimeError(f"cannot read {rel}: {exc}") from exc
        text = _decode(blob)
        if text is not None:
            files.append((rel, text))
    return files


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Refuse a credential-shaped literal before it is committed.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--staged", action="store_true", default=True,
                      help="the staged content of the index (default)")
    mode.add_argument("--tracked", action="store_true",
                      help="every tracked text file, for the whole-tree check")
    mode.add_argument("--paths", nargs="+", metavar="PATH",
                      help="named files, read from the checkout")
    parser.add_argument("--root", default=str(REPO),
                        help="the repository to read (default: this checkout)")
    args = parser.parse_args(argv)
    root = pathlib.Path(args.root).resolve()

    try:
        if args.paths:
            files = from_paths(root, args.paths)
            subject = f"{len(files)} named file(s)"
        elif args.tracked:
            files = tracked(root)
            subject = f"{len(files)} tracked file(s)"
        else:
            files = staged(root)
            subject = f"{len(files)} staged file(s)"
    except RuntimeError as exc:
        print(f"secret-scan: NOT MEASURED — {exc}", file=sys.stderr)
        return 2

    findings: list[Finding] = []
    for rel, text in files:
        findings.extend(scan_text(rel, text))

    if findings:
        print(f"secret-scan: {len(findings)} credential-shaped literal(s) in "
              f"{subject}:", file=sys.stderr)
        for finding in findings:
            print(finding.render(), file=sys.stderr)
        print("\n  Take the value out of the file: build the string from parts when it "
              "is a fixture, and keep a real credential in the box's .env, which is "
              "not committed.\n  If the line really is a fixture, say so on that line "
              f"with `{ALLOW} <why>` — and note that removing it from a later commit "
              "does not remove it from the history that was already pushed.",
              file=sys.stderr)
        return 1

    print(f"secret-scan: {subject} carry no credential-shaped literal.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
