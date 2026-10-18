#!/usr/bin/env python3
"""Which Supabase project this app talks to, and where its database is.

`AGENTS.md` recorded the ref as `roshkbkbzgfedowozfo` — a 19-character typo, one
letter away from `roshkbzgfzpfedowozfo` — and it was copied from there into a merge
runbook and the Windows dev script. Nothing failed for weeks, because nothing read
it: the app takes its URL from `.env`, and `.env` was right the whole time. The same
bullet carried a region no typo check could catch: `ap-southeast-1` (Singapore)
against a database in `ap-south-1` (Mumbai) — and that one was on a **UU PDP
disclosure**, where "where is personal data stored" is the sentence the page exists
to state.

So this is not a spell-check. It holds two recorded facts to three things: to each
other, to the one page that discloses them, and — when this checkout has a `.env` —
to the live configuration itself. It runs as a **tool** and not as a test case so
that the pre-commit hook, the VPS auto-deploy and CI run one implementation: the
old form was a pytest file, `pytest.skip`ped its `.env` half wherever there was no
`.env`, and `skip` is a pass to everything that reads an exit code. A guard whose
strongest half quietly declines to run is a guard that reports green about a ref
nobody compared with anything.

## The two halves, and what "not measured" means

**The record** — `REF_SITES` (a missing entry is a gap, so it is a list and not a
count), every `rosh…`-shaped token, every line that puts a region next to Supabase,
a pooler, Postgres or a database, the UU PDP page, the `AGENTS.md` bullet and the
capacity row. This half is static: it reads `git ls-files`, needs no network, and is
judged in **every** run — CI included.

**The published claim is one value, and it lives in configuration.** Two pages state
where the database is — the UU PDP disclosure (`§8`, to a reader deciding whether to
trust the app with a child's data) and the capacity page's machine row (beside the
figures a school checks against the evidence files) — so the same sentence existed
twice, and that is exactly how it drifted: the disclosure said Singapore, a
Cloudflare edge, while `DIRECT_URL`'s pooler said `ap-south-1`. Neither page may spell
the region now. Both render `app.config.database_region()`, which derives it from the
pooler host the app actually connects through and falls back to
`SUPABASE_REGION_DEFAULT`, and this tool holds that constant and its `REGION_PLACES`
entry to the record — so the record judges the source instead of competing with it.
A release that puts a region back into either page, or that moves the constant away
from the record, is a finding.

Two things are read apart on purpose. A ref *host* (`*.supabase.co`,
`postgres.<ref>`, `dashboard/project/<ref>`) and a *region claim* are checked in the
files that reach a real project, because a test may fabricate both
(`test_arm_auto_deploy.py`'s `abcdefghijklmnopqrst` is a fixture, not a claim, and
the guard's own suite puts a Singapore region code beside the word to prove the
region rule fires); the `rosh…` family is checked everywhere, since nothing has a reason to
write a second ref-shaped string of that shape — a fixture builds one instead of
quoting it. The old spelling is allowed in exactly one place — the line that records it
*as* the typo — so the record of the defect survives while a second mention of it
cannot hide behind the first.

**The configuration** — `SUPABASE_URL`'s host and `DIRECT_URL`'s pooler host out of
`.env`, compared with both records. It is the only half that can tell a *record*
that is self-consistent from a record that is self-consistently **wrong**, and it
exists only where `.env` does. So a run without `.env` does not skip it: it says,
in one line on stdout, `the configuration was NOT measured — no .env in this
checkout`, and the gate's success sentence then claims only the record. CI has no
`.env` by construction (a committed `.env` is a leaked key, and the secrets scan
refuses one), so CI proves the record and the *statement of absence*; the box, whose
`.env` is what the app actually serves from, proves both.

## Exit codes

    0  the record is one ref in one region, and the configuration either agrees with
       it or is named as not measured (a property of this checkout, not a finding)
    1  a real finding — a wrong ref, a second ref shape, a region beside Supabase
       that is not `ap-south-1`, a disclosure that moved, or a `.env` pointing at a
       different project
    2  could not measure — no tracked files, or no git in the tree. A box in that
       state is in no position to reject a release, and the caller is told so
       rather than handed a silent zero.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Measured 18 Oct 2026 from the app's own configuration and from the project's own
#: answers: `.env` carries it in `SUPABASE_URL` (four declarations),
#: `NEXT_PUBLIC_SUPABASE_URL`, `SUPABASE_JWKS_URL` and `DIRECT_URL`; the API replies
#: `sb-project-ref: roshkbzgfzpfedowozfo` to an unauthenticated `GET /rest/v1/`;
#: `super_admin/file_management.html` links three dashboard pages with it; and
#: `tests/unit/test_apply_migration.py` uses it. The old spelling resolves to
#: nothing at all (DNS does not answer), so it never named a second project.
REF = "roshkbzgfzpfedowozfo"
REF_OLD = "roshkbkbzgfedowozfo"

#: Where the database is, measured twice and independently of `.env`: `DIRECT_URL`'s
#: pooler host is `aws-1-ap-south-1.pooler.supabase.com`, whose CNAME resolves to
#: `pool-tcp-aps11-….elb.ap-south-1.amazonaws.com`; and `db.<ref>.supabase.co`
#: resolves to `2406:da1a:314:…`, inside AWS's own published `2406:da1a::/35` prefix
#: for ap-south-1 (`https://ip-ranges.amazonaws.com/ip-ranges.json`). Cloudflare
#: answers from Singapore (`CF-RAY: …-SIN`), which is the edge and not the database.
REGION = "ap-south-1"
SINGAPORE = ("Singapore", "Singapura")

#: What makes a region token a claim about the *database* rather than about a box or
#: an edge node. The box's own region is a fact about the VPS and is not judged here
#: — `capacity_service.py`'s `"region"` field says where the machine is, which is a
#: different question, while its `"database"` value is asserted by name.
CLAIM_WORDS = ("Supabase", "supabase", "pooler", "postgres", "Postgres", "database")

REGION_SHAPED = re.compile(r"\bap-[a-z]+-\d\b")
REF_HOST = re.compile(r"\b([a-z0-9]{18,24})\.supabase\.(?:co|in|net)\b")
REF_POOLER = re.compile(r"\bpostgres\.([a-z0-9]{18,24}):")
REF_DASHBOARD = re.compile(r"dashboard/project/([a-z0-9]{18,24})")
#: A pooler host says which region the *database* is in, however the row is worded.
POOLER_HOST = re.compile(r"aws-\d+-([a-z0-9-]+)\.pooler\.supabase\.com")

#: A ref-shaped string of this project's family: four letters, then the rest. The
#: typo was this shape and one character short, which is exactly what a length check
#: cannot see and this can.
REF_FAMILY = re.compile(r"\brosh[a-z0-9]{13,18}\b")

#: Where the old spelling may be written down at all: the page that records it as
#: the defect, and this tool, which has to name the string to recognise a second
#: sighting of it. Anywhere else is a copy that can hide behind the first — and the
#: whole reason the typo lasted weeks is that copies of it were not counted.
OLD_RECORD = "docs/features/SEB.md"
OLD_QUOTING = ("deploy/supabase_region.py", OLD_RECORD)

#: Where the ref is recorded, each for its own reason: a bullet an agent reads
#: first, the page that found the typo, the merge record's database section, the
#: three dashboard links, the link the dev script prints, and the constant the pooler
#: parser is tested with. A missing entry is a gap in the record, so this is a list
#: and not a count.
REF_SITES = {
    "AGENTS.md": "the bullet an agent reads first",
    "docs/features/SEB.md": "the page that found the typo",
    "docs/deploy/QUESTION_TYPES_MERGE_RUNBOOK.md": "the merge record's §6",
    "app/templates/super_admin/file_management.html": "three dashboard links",
    "start-dev.ps1": "the link the dev script prints",
    "tests/unit/test_apply_migration.py": "REF, for the pooler parser",
}

AGENTS = "AGENTS.md"
PRIVACY = "app/templates/compliance/privacy.html"
CAPACITY = "app/services/capacity_service.py"

#: The one place the published claim comes from. Both pages render
#: `app.config.database_region()`, so this file holds the value — and the two pages
#: are refused if they spell it themselves.
REGION_SOURCE = "app/config.py"

#: The template variable both renderings of the disclosure print. Named rather than
#: inferred: a page that renders something else has stopped publishing the claim.
CLAIM_VAR = "region_claim"
DEFAULT_DECL = re.compile(r'^SUPABASE_REGION_DEFAULT\s*=\s*"([^"]+)"', re.M)

#: The words that used to be written out by hand. The region code is the claim; the
#: city is what a literal carried beside it, and a page that keeps the city has kept
#: the second copy in a form the code check cannot see.
PLACE_WORDS = ("Mumbai",)

#: A scan that reads nothing cannot report agreement: this is the floor the region
#: half is held to, so a tree whose mentions all disappeared is a finding rather
#: than a clean run over an empty set.
MINIMUM_REGION_SITES = 6

#: The file the app's own configuration is read from. Gitignored on purpose: a
#: committed `.env` is a leaked service-role key, and the secrets scan refuses one.
ENV_FILE = ".env"

#: The variables git exports to the processes it spawns. The pre-commit hook *is* a
#: git process, so a plain `git ls-files` here would answer about the hook's own
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


class Config:
    """What `.env` said, if this checkout has one.

    Three states, because two would flatter a half-read file: `measured` means both
    halves were compared, `partly` means one was, `not measured` means nothing was.
    Only `measured` lets a caller claim the configuration agrees — a file carrying
    `SUPABASE_URL` and no `DIRECT_URL` says nothing about where the database is.
    """

    __slots__ = ("state", "reason", "url_ref", "pooler_region", "findings")

    def __init__(self) -> None:
        self.state = "not measured"      # measured | partly | not measured
        self.reason = ""                 # why nothing (or not everything) was read
        self.url_ref = ""
        self.pooler_region = ""
        self.findings: list[str] = []

    @property
    def measured(self) -> bool:
        return self.state == "measured"


class Report:
    """What a scan found: the record's counts, and every disagreement."""

    def __init__(self) -> None:
        self.findings: list[str] = []
        self.config = Config()
        self.ref_sites = 0        # recorded sites that carry the ref
        self.ref_tokens = 0       # ref-shaped strings read anywhere in the tree
        self.region_sites = 0     # region tokens read
        self.typo_sites = 0       # sightings of the old spelling in the record page

    def offenders(self) -> list[str]:
        return self.findings + self.config.findings


def _safe(text: str) -> str:
    """Text that can be printed on any console.

    `AGENTS.md` carries ✅ and an em dash, and a guard that dies while reporting is
    a guard that reports nothing. The file is unchanged; only the line the operator
    reads is transliterated.
    """
    return text.encode("ascii", "backslashreplace").decode("ascii")


def _where(rel: str, number: int, line: str) -> str:
    return f"{rel}:{number}: {_safe(line.strip()[:150])}"


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


def _texts(repo: Path, paths: list[str]) -> dict[str, str]:
    """Every tracked file as text. A binary one reads as noise and matches nothing.

    That is the honest cost of a scan with no file-type list: a PNG cannot carry a
    ref, so reading it is waste rather than risk, and a type list is one more thing
    that can silently stop covering the file that matters.
    """
    out: dict[str, str] = {}
    for rel in paths:
        try:
            out[rel] = (repo / rel).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
    return out


def _lines(text: str):
    return list(enumerate(text.splitlines(), 1))


def parse_env(path: Path) -> dict[str, str]:
    """A `.env` as a mapping. `KEY=value  # note` keeps the value, not the note.

    The same reader `app/config.py` uses in spirit: a `#` after a value is a
    comment there, and a stripped pair of quotes is not part of the value.
    """
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return values
    for line in text.splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


# ── The record ────────────────────────────────────────────────────────────────


def _check_ref_sites(texts: dict[str, str], report: Report) -> None:
    for rel, reason in REF_SITES.items():
        if rel not in texts:
            report.findings.append(
                f"{rel} is gone — it is where {reason}, and it has to name {REF}")
            continue
        if REF not in texts[rel]:
            report.findings.append(
                f"{rel} is where {reason}, and it no longer names {REF}")
            continue
        report.ref_sites += 1


def _check_ref_contexts(texts: dict[str, str], report: Report) -> None:
    """Every ref written in a Supabase context is this ref, outside the tests.

    Only a host, a pooler URL or a dashboard link is read here, and deliberately
    not the words `Supabase project:`: this file contains that phrase as a string
    literal, and a rule that read it as a claim would refuse its own source. A
    ref-shaped string on such a line is caught by the family rule below, and a
    recorded file that stops naming the ref by `REF_SITES`.
    """
    for rel, text in texts.items():
        for number, line in _lines(text):
            tokens = []
            for pattern in (REF_HOST, REF_POOLER, REF_DASHBOARD):
                tokens.extend(m.group(1) for m in pattern.finditer(line))
            for token in tokens:
                if token != REF and not rel.startswith("tests/"):
                    report.findings.append(
                        f"{_where(rel, number, line)} — this reaches a project "
                        f"that is not {REF}")


def _check_family(texts: dict[str, str], report: Report) -> None:
    """Anything shaped like this project's ref has to be this project's ref.

    The old spelling is the one exception, and the exception is a *file* list and
    not a licence: the page that records the defect, and this tool, which has to
    hold the string to recognise it. A copy anywhere else is refused, and the page
    that carries the record has to go on saying it was a typo — a quote without
    the word is indistinguishable from the defect it records.
    """
    old: list[tuple[str, int, str]] = []
    for rel, text in texts.items():
        for number, line in _lines(text):
            for match in REF_FAMILY.finditer(line):
                token = match.group(0)
                report.ref_tokens += 1
                if token == REF_OLD:
                    old.append((rel, number, line))
                elif token != REF:
                    report.findings.append(
                        f"{_where(rel, number, line)} — this looks like the "
                        f"project ref ({token}) and is not {REF}")
    for rel, number, line in old:
        if rel not in OLD_QUOTING:
            report.findings.append(
                f"{_where(rel, number, line)} — the old spelling is recorded in "
                f"{OLD_RECORD}, nowhere else")
        elif rel == OLD_RECORD:
            report.typo_sites += 1
            if "salah ketik" not in line:
                report.findings.append(
                    f"{_where(rel, number, line)} — quotes the typo without "
                    "saying it was one")


def _check_regions(texts: dict[str, str], report: Report) -> None:
    for rel, text in texts.items():
        # A fixture is not a claim, for the same reason a fabricated host in
        # `tests/` is not one: a test has to put `ap-southeast-1` beside the word
        # "Supabase" to prove this rule fires, and the guard's own suite does. The
        # records that could reach a real project are outside `tests/`.
        fixture = rel.startswith("tests/")
        for number, line in _lines(text):
            for match in REGION_SHAPED.finditer(line):
                report.region_sites += 1
                if fixture:
                    continue
                if match.group(0) != REGION and any(w in line for w in CLAIM_WORDS):
                    report.findings.append(
                        f"{_where(rel, number, line)} — a Supabase/database line "
                        f"that is not in {REGION}")
    # Singapore is checked by name on the disclosure (`_check_disclosure`), and
    # nowhere else. A global "Supabase" + "Singapore" rule read this repository's
    # own record of the defect — one `AGENTS.md` bullet, which is a paragraph — as
    # the defect, and a guard that fails on the sentence describing the fix is a
    # guard that gets deleted. The factual claim is the region code, and that is
    # judged everywhere.
    if report.region_sites < MINIMUM_REGION_SITES:
        report.findings.append(
            f"only {report.region_sites} region mention(s) were found; a scan "
            "that reads nothing cannot report agreement")


def _check_disclosure(texts: dict[str, str], report: Report) -> None:
    """The disclosure renders the configured claim — twice, and never the value."""
    text = texts.get(PRIVACY)
    if text is None:
        report.findings.append(f"{PRIVACY} is gone — the UU PDP disclosure lives there")
        return
    # Twice on purpose: the page renders the policy in two places.
    if text.count(CLAIM_VAR) != 2:
        report.findings.append(
            f"{PRIVACY}: the cross-border section renders `{CLAIM_VAR}` "
            f"{text.count(CLAIM_VAR)} time(s); it is stated twice")
    # The value is not in the page. A spelled region is the second copy that drifted
    # the first time, and it is invisible to a comparison made against the same page.
    spelled = REGION_SHAPED.search(text)
    if spelled:
        report.findings.append(
            f"{PRIVACY}: spells {spelled.group(0)} in the template — the "
            "disclosure must render the configured claim, or the two pages that "
            "publish it can disagree")
    kept = [w for w in PLACE_WORDS if w in text]
    if kept:
        report.findings.append(
            f"{PRIVACY}: names {kept[0]} itself — the city comes with the region "
            "code from configuration; a literal place survives a region change")
    if any(w in text for w in SINGAPORE):
        report.findings.append(
            f"{PRIVACY}: the disclosure still points at Singapore — the database "
            "is where the pooler host says it is, and Cloudflare's edge is not it")


def _check_bullet_and_row(texts: dict[str, str], report: Report) -> None:
    text = texts.get(AGENTS, "")
    bullets = [l for l in text.splitlines() if l.startswith("- Supabase project:")]
    if len(bullets) != 1:
        report.findings.append(
            f"{AGENTS}: {len(bullets)} `- Supabase project:` bullet(s); one records it")
    elif REF not in bullets[0] or REGION not in bullets[0]:
        report.findings.append(
            f"{AGENTS}: the recorded bullet does not carry {REF} in {REGION}: "
            f"{_safe(bullets[0][:150])}")
    rows = [l for l in text.splitlines() if "Transfer Lintas Batas" in l]
    if len(rows) != 1:
        report.findings.append(
            f"{AGENTS}: {len(rows)} `Transfer Lintas Batas` row(s); one records "
            "the cross-border disclosure")
    elif "Supabase" not in rows[0] or REGION not in rows[0]:
        report.findings.append(
            f"{AGENTS}: the cross-border row does not agree on {REGION}: "
            f"{_safe(rows[0][:150])}")

    source = texts.get(CAPACITY)
    if source is None:
        report.findings.append(
            f"{CAPACITY} is gone — the capacity page's machine row is read there")
        return
    if "database_region(" not in source:
        report.findings.append(
            f"{CAPACITY}: the database's row is not read from configuration "
            "(`database_region()`), so it is a second copy of the disclosure's claim")
    for number, line in _lines(source):
        if REGION in line or any(w in line for w in PLACE_WORDS):
            report.findings.append(
                f"{_where(CAPACITY, number, line)} — states the database's region "
                "itself; it comes from app.config")
    if "same region" in source:
        report.findings.append(
            f"{CAPACITY}: the box and the database are not in one region; say "
            "which one the database is in instead")


def _check_region_source(texts: dict[str, str], report: Report) -> None:
    """The single source, held to the record — not a second opinion beside it.

    The value is judged, never guessed: a constant that disagrees with `REGION` is a
    finding, and so is a `REGION_PLACES` entry that is missing, because a label
    without a city is a disclosure that quietly stopped naming a place.
    """
    source = texts.get(REGION_SOURCE)
    if source is None:
        report.findings.append(
            f"{REGION_SOURCE} is gone — both published claims read the region there")
        return
    declared = DEFAULT_DECL.search(source)
    if not declared:
        report.findings.append(
            f"{REGION_SOURCE}: no `SUPABASE_REGION_DEFAULT = \"…\"` — the published "
            "claim has no value to fall back on")
    elif declared.group(1) != REGION:
        report.findings.append(
            f"{REGION_SOURCE}: SUPABASE_REGION_DEFAULT is {declared.group(1)}; the "
            f"record says {REGION} — the disclosure and the capacity row would "
            "publish the wrong region")
    if f'"{REGION}":' not in source:
        report.findings.append(
            f"{REGION_SOURCE}: REGION_PLACES has no {REGION} entry — the published "
            "label would degrade to the bare code")


# ── The configuration ─────────────────────────────────────────────────────────


def _check_config(repo: Path, report: Report) -> None:
    """`.env` against both records — or the sentence saying it was not read."""
    config = report.config
    path = repo / ENV_FILE
    if not path.is_file():
        config.state = "not measured"
        config.reason = f"no {ENV_FILE} in this checkout"
        return

    values = parse_env(path)
    unread: list[str] = []

    url = values.get("SUPABASE_URL", "")
    if not url:
        unread.append(f"{ENV_FILE} carries no SUPABASE_URL")
    else:
        found = REF_HOST.search(url)
        if not found:
            config.findings.append(
                f"{ENV_FILE}: SUPABASE_URL is not a Supabase host — {url!r}")
        else:
            config.url_ref = found.group(1)
            if config.url_ref != REF:
                config.findings.append(
                    f"{ENV_FILE}: SUPABASE_URL talks to {config.url_ref}; the "
                    f"tree records {REF}")

    direct = values.get("DIRECT_URL", "")
    if not direct:
        unread.append(f"{ENV_FILE} carries no DIRECT_URL pooler host")
    else:
        found = POOLER_HOST.search(direct)
        if not found:
            unread.append(f"{ENV_FILE}'s DIRECT_URL carries no pooler host")
        else:
            config.pooler_region = found.group(1)
            if config.pooler_region != REGION:
                config.findings.append(
                    f"{ENV_FILE}: DIRECT_URL's pooler is in "
                    f"{config.pooler_region}; the tree records {REGION}")

    if config.url_ref and config.pooler_region:
        config.state = "measured"
    elif config.url_ref or config.pooler_region:
        config.state = "partly"
        config.reason = "; ".join(unread) or "one half could not be read"
    else:
        config.state = "not measured"
        config.reason = "; ".join(unread) or f"{ENV_FILE} carries neither value"


def scan(repo: Path | str = ROOT) -> Report:
    """The one reading: the record, then the configuration where there is one."""
    repo = Path(repo)
    paths = tracked(repo)
    if not paths:
        raise Measure(f"no tracked files in {repo}")
    texts = _texts(repo, paths)
    if not texts:
        raise Measure(f"no tracked file in {repo} could be read")

    report = Report()
    _check_ref_sites(texts, report)
    _check_ref_contexts(texts, report)
    _check_family(texts, report)
    _check_regions(texts, report)
    _check_disclosure(texts, report)
    _check_bullet_and_row(texts, report)
    _check_region_source(texts, report)
    _check_config(repo, report)
    return report


# ── What the caller sees ─────────────────────────────────────────────────────


def summarise(report: Report) -> str:
    return (f"supabase region: {report.ref_sites} recorded site(s) name {REF}, "
            f"{report.region_sites} region mention(s) name {REGION}, "
            f"{report.ref_tokens} ref-shaped string(s) read, and both published "
            f"claims render {REGION_SOURCE}'s {REGION} rather than spelling it")


def config_line(config: Config) -> str:
    """One line, and its first words decide what a caller may claim.

    `the configuration was measured` is matched by the gate's success clause (and
    `deploy/theme_gate.sh`'s test pins the phrase), because a sentence saying the
    `.env` agrees must not be printed by a run that read no `.env`.
    """
    if config.state == "measured":
        return ("supabase region: the configuration was measured — this "
                f"checkout's {ENV_FILE} says SUPABASE_URL is {config.url_ref} and "
                f"DIRECT_URL's pooler is in {config.pooler_region}")
    if config.state == "partly":
        read = []
        if config.url_ref:
            read.append(f"SUPABASE_URL is {config.url_ref}")
        if config.pooler_region:
            read.append(f"DIRECT_URL's pooler is in {config.pooler_region}")
        return ("supabase region: the configuration was only partly measured — "
                f"this checkout's {ENV_FILE} says " + " and ".join(read)
                + f", and {config.reason}")
    return ("supabase region: the configuration was NOT measured — "
            f"{config.reason}; the recorded ref is confirmed by nothing live here")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Hold the recorded Supabase ref and region to the tree and to .env.")
    parser.add_argument("--repo", default=str(ROOT),
                        help="the checkout to read (default: this one)")
    args = parser.parse_args(argv)

    # A console that cannot encode an em dash must not be the reason a refusal is
    # unreadable, nor the reason a release looks refused.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, ValueError):  # pragma: no cover - older streams
            pass

    try:
        report = scan(Path(args.repo).resolve())
    except Measure as exc:
        print(f"supabase region: could not measure — {_safe(str(exc))}", file=sys.stderr)
        return 2

    print(summarise(report))
    print(config_line(report.config))
    if report.offenders():
        print("supabase region: FAILED — this release records one project ref and "
              "one region, and something in it does not agree.", file=sys.stderr)
        for line in report.offenders():
            print(f"    {line}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
