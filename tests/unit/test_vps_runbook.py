"""The VPS runbook is a page of literals, and every one is a fact in a script.

`docs/deploy/VPS_ARM_AND_UNQUARANTINE_RUNBOOK.md` is read at the worst moment this
system has: root, at a console, on a box that is already refusing. A drifted path or a
misattributed exit code there does not fail a build — it sends somebody down the wrong
branch of a diagnosis at night, on the machine that was about to tell them the answer.
So each class of literal the page promises is read back out of the page and checked
against the file that owns it:

* every `deploy/…`, `docs/…` and `.freebuff/…` path it names is in the tree;
* every box path (`/var/lib/scangrade-deploy/…`, `/etc/scangrade-…`,
  `/usr/local/bin/…`) is a name a shipped script writes — a renamed record fails here
  rather than sending an operator to a file that no longer exists;
* the record names the page hands an operator are the runner's own state names;
* every exit code in its **runner** table is an exit the runner takes, and every code in
  its **installer** table is the installer's. The page's whole §3 exists because a
  reader must tell three sets of digits apart — the runner's, the installer's and the
  gates' — so the numbers are pinned to the programs that use them and the tables may
  not lose rows;
* the gates' own codes are the constants the gates define, and the perf gate's
  `NOT ARMED` lines the page quotes are the lines the gate prints — a page that offered
  `--rebaseline` for a missing roster would be advice that cannot work;
* the flags it types are in the parsers of the tools that take them.

Nothing here reads prose for meaning; every assertion is about a literal an operator
would type or read, which is what makes it mutation-checkable: rename a record, drop an
exit, or move a gate's code in any one file and one of these fails.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = ROOT / "docs" / "deploy" / "VPS_ARM_AND_UNQUARANTINE_RUNBOOK.md"

RUNNER = ROOT / "deploy" / "scangrade-deploy.sh"
INSTALLER = ROOT / "deploy" / "install-auto-deploy.sh"
ARM = ROOT / "deploy" / "arm-auto-deploy.sh"
ENTRYPOINT = ROOT / "deploy" / "entrypoint.sh"
RECOVER = ROOT / "deploy" / "scangrade-recover.sh"
SERVED = ROOT / "deploy" / "served_commit_gate.py"
MIGRATE = ROOT / "deploy" / "apply_migration.py"

#: Every script that names a box path. The page is allowed to name a path because one
#: of these writes it; nothing else counts as evidence.
BOX_SOURCES = (RUNNER, INSTALLER, ARM, ENTRYPOINT, RECOVER)

def _records() -> tuple[tuple[str, Path, str], ...]:
    """What the runner (and the lever) keep for an operator, page path by assignment.

    Each entry is a *path the page names* paired with the *assignment that writes it*
    — not with the bare word, which is how a rename survives a guard: when
    `PREFLIGHT_FILE` was mutated to point elsewhere, `refused-before-merge` was still
    in the file inside the diff's own assignment and a substring check passed. This is
    what `deploy_status_service.UNRENDERED` already learned about `@REPO@`: read the
    assignment, because a file that merely mentions a name is not its writer.

    The path's writer is named too, and one of them is not the runner: the `recover/`
    records are the *lever's* (they exist only if `sgfix` ran), which is why the page
    lists them under `/usr/local/bin/sgfix` rather than under the tick.
    """
    return (
        ("/var/lib/scangrade-deploy/quarantined", RUNNER,
         'QUARANTINE_FILE="$STATE_DIR/quarantined"'),
        ("/var/lib/scangrade-deploy/refusals", RUNNER,
         'REFUSALS_DIR="$STATE_DIR/refusals"'),
        ("/var/lib/scangrade-deploy/refused-before-merge", RUNNER,
         'PREFLIGHT_FILE="$STATE_DIR/refused-before-merge"'),
        ("refused-before-merge.diff", RUNNER,
         'PREFLIGHT_DIFF_FILE="$STATE_DIR/refused-before-merge.diff"'),
        ("/var/lib/scangrade-deploy/unarmed", RUNNER,
         'UNARMED_FILE="$STATE_DIR/unarmed"'),
        ("/var/lib/scangrade-deploy/situation", RUNNER,
         'SITUATION_FILE="$STATE_DIR/situation"'),
        ("/var/lib/scangrade-deploy/last-stop", RUNNER,
         'LAST_STOP_FILE="$STATE_DIR/last-stop"'),
        ("/var/lib/scangrade-deploy/last-deploy", RUNNER,
         'printf \'%s\\n%s\\n%s\\n\' "$AFTER" "$(date -Is)" "$SNAPSHOT" > "$STATE_DIR/last-deploy"'),
        ("/var/lib/scangrade-deploy/requests/release", RUNNER,
         'RELEASE_REQUEST="$REQUEST_DIR/release"'),
        ("/var/lib/scangrade-deploy/requests/rebaseline", RUNNER,
         'REBASELINE_REQUEST="$REQUEST_DIR/rebaseline"'),
        ("/var/lib/scangrade-deploy/recover/", RECOVER,
         'RECOVER_DIR="$STATE_DIR/recover"'),
    )

RUNNER_TABLE_START = "**The exit code says which half.**"
RUNNER_TABLE_END = "The installer's own codes are a different set"
INSTALLER_TABLE_END = "**The gates have a third set"

#: The record's shape, which the page states: sha · when · gate · up to 6 lines of the
#: gate's own finding, each cut at 400 characters. Both numbers are the runner's.
QUARANTINE_LINES = "detail_lines=6"
QUARANTINE_WIDTH = "detail_width=400"


def page() -> str:
    assert RUNBOOK.exists(), f"{RUNBOOK.relative_to(ROOT)} is the page these tests guard"
    return RUNBOOK.read_text(encoding="utf-8")


def section(text: str, start: str, end: str) -> str:
    """The span between two literals, both asserted present.

    A slice that quietly became empty would pass every assertion made over it, which is
    the one failure mode a section-scoped guard has.
    """
    assert start in text, f"the page no longer says {start!r} — section boundary moved"
    assert end in text, f"the page no longer says {end!r} — section boundary moved"
    body = text.split(start, 1)[1].split(end, 1)[0]
    assert body.strip(), f"nothing between {start!r} and {end!r}"
    return body


def rows_in_table(body: str) -> list[str]:
    """The first cell of each markdown row whose value is a number, or `a / b`."""
    cells: list[str] = []
    for line in body.splitlines():
        match = re.match(r"\s*\|\s*(\d+(?:\s*/\s*\d+)?)\s*\|", line)
        if match:
            cells.append(match.group(1))
    return cells


def codes_in_table(body: str) -> list[int]:
    found: list[int] = []
    for cell in rows_in_table(body):
        found.extend(int(part) for part in re.findall(r"\d+", cell))
    return found


def exits_in(script: Path) -> set[int]:
    text = script.read_text(encoding="utf-8")
    return {int(m) for m in re.findall(r"\bexit\s+(\d+)\b", text)}


def test_the_page_exists_and_is_the_one_these_tests_read():
    text = page()
    assert text.startswith("# Runbook — arming the VPS, and releasing a held commit"), (
        "the page's heading is its contract with the operator: arming, and releasing")
    for needed in ("## 1. Arm it", "## 2. Un-quarantine a held release",
                   "## 3. The two refusals, told apart in one look"):
        assert needed in text, f"the page lost the step a console visit is for: {needed}"


def test_every_repo_path_the_page_names_exists():
    text = page()
    # The whole page, not just the backticked spans: most of the commands an operator
    # types live in fenced blocks, so reading only `` `…` `` would leave the path in
    # the command unguarded — which is exactly where a rename would hurt.
    # `(?<![\w/-])` is load-bearing: without the `-`, the tail of a box path matches —
    # `/var/lib/scangrade-deploy/requests/release` would be read as the repo path
    # `deploy/requests/release`, and the guard would refuse a page that is correct.
    named = set(re.findall(
        r"(?<![\w/-])((?:deploy|docs|app|tests|\.freebuff)/[A-Za-z0-9_./-]+)", text))
    named = {rel.rstrip(".,;:") for rel in named}
    assert len(named) >= 5, f"only {len(named)} repo paths extracted: {sorted(named)}"
    missing = sorted(rel for rel in named if not (ROOT / rel).exists())
    assert not missing, f"the runbook names paths that are not in the tree: {missing}"


def test_every_box_path_the_page_names_is_a_name_a_script_writes():
    text = page()
    sources = "\n".join(path.read_text(encoding="utf-8") for path in BOX_SOURCES)
    # A path on the box: a real root, then the components. Placeholders (`<stamp>`,
    # `NNN_x.sql`, a `*` glob, an ellipsis) are the page talking *about* a path rather
    # than naming one, and are skipped rather than asserted.
    pattern = re.compile(
        r"(?<![\w/])(/(?:var/lib/scangrade-deploy|etc|usr/local/bin|opt/scangrade)"
        r"[A-Za-z0-9_./-]*)")
    named = {m.rstrip(".,;:") for m in pattern.findall(text)
             if not any(bad in m for bad in ("<", ">", "*", "$", "NNN", "…"))}
    assert len(named) >= 12, f"only {len(named)} box paths extracted — extractor moved?"

    unknown = []
    for path in sorted(named):
        # A path *inside the checkout* — `/opt/scangrade/deploy/perf_gate.py` — is a
        # repo path wearing the box's prefix, and the file has to exist. Nothing else
        # would check it: the rest of this file reads repo paths by their `deploy/…`
        # form, and a lookbehind that let `deploy/` match inside a box path would read
        # every state file as a checkout path.
        tail = path.split("/opt/scangrade/", 1)[-1] if "/opt/scangrade/" in path else ""
        if tail.startswith(("deploy/", "docs/")) and not (ROOT / tail).exists():
            unknown.append(f"{path} ({tail} is not in the tree)")
            continue
        rest = path.split("/var/lib/scangrade-deploy", 1)[-1]
        rest = rest.split("/opt/scangrade", 1)[-1]
        rest = rest.split("/usr/local/bin", 1)[-1]
        rest = rest.split("/etc", 1)[-1]
        components = [c for c in rest.strip("/").split("/") if c]
        if not components:
            continue
        # Every component has to be a name some script writes. Checking the last one
        # alone would let `requests/release` pass on the word `release` anywhere.
        lost = [c for c in components if c not in sources]
        if lost:
            unknown.append(f"{path} (no script names {', '.join(lost)})")
    assert not unknown, "the runbook names box paths no shipped script writes: " + \
        "; ".join(unknown)


def test_the_records_the_page_hands_an_operator_are_the_runners_own():
    text = page()
    writers = {path: path.read_text(encoding="utf-8") for _, path, _ in _records()}
    lost = [f"{named} (page) / {written} ({path.name})"
            for named, path, written in _records()
            if named not in text or written not in writers[path]]
    assert not lost, (
        "a record the page sends an operator to is not both named by the page and "
        "assigned by the file that writes it: " + "; ".join(lost))


def test_the_quarantine_records_shape_is_the_shape_the_runner_writes():
    runner = RUNNER.read_text(encoding="utf-8")
    assert QUARANTINE_LINES in runner and QUARANTINE_WIDTH in runner, (
        "the record's cap is the runner's: `detail_lines` and `detail_width`")
    text = page()
    # Parsed out of the page and compared with the runner, rather than matched as a
    # phrase: the page states the shape in two places, and a guard that looked for the
    # words would be satisfied by the copy that had not changed.
    lines = re.search(r"up to (\d+) lines", text)
    width = re.search(r"(\d+) chars", text)
    assert lines and width, (
        "the page states the record's shape — it is the reason a gate cannot put a "
        "transcript on the status page")
    assert int(lines.group(1)) == 6, f"the page says {lines.group(1)} lines, the runner 6"
    assert int(width.group(1)) == 400, f"the page says {width.group(1)} chars, the runner 400"


def test_every_runner_exit_code_in_the_page_is_an_exit_the_runner_takes():
    body = section(page(), RUNNER_TABLE_START, RUNNER_TABLE_END)
    rows = rows_in_table(body)
    codes = codes_in_table(body)
    # A floor on *rows*, not on codes: the ⊆ direction below catches a wrong code but
    # not a missing one, and `7 / 8` on one row is two codes — so a count of codes
    # stays satisfied when a whole row (say the stale lock's `17`) is dropped.
    assert len(rows) >= 16, (
        f"the runner table has shrunk to {len(rows)} rows: dropping a row drops the "
        "diagnosis it carries, and nothing else on the page repeats it")
    assert len(set(codes)) == len(codes), f"a code is listed twice: {sorted(codes)}"
    available = exits_in(RUNNER)
    missing = sorted(set(codes) - available)
    assert not missing, (
        f"the page gives the runner exit codes it never takes: {missing} "
        f"(the runner takes {sorted(available)})")


def test_every_installer_exit_code_in_the_page_is_an_exit_the_installer_takes():
    body = section(page(), RUNNER_TABLE_END, INSTALLER_TABLE_END)
    codes = codes_in_table(body)
    assert len(codes) >= 5, f"the installer table has shrunk to {len(codes)} rows"
    available = exits_in(INSTALLER)
    missing = sorted(set(codes) - available)
    assert not missing, (
        f"the page gives the installer exit codes it never takes: {missing} "
        f"(the installer takes {sorted(available)})")


def test_the_gates_own_codes_are_the_constants_the_gates_define():
    import sys
    sys.path.insert(0, str(ROOT / "deploy"))
    import claims_gate
    import perf_gate
    import seb_door_gate

    # The vocabulary `perf_gate` imports rather than restating: 0 ok, 1 diverged,
    # 2 could not run, 4 not armed. The page's §3 lists exactly these, and calls them
    # the gates' own numbers precisely so a gate's `4` is not read as the runner's.
    assert (claims_gate.EXIT_OK, claims_gate.EXIT_DIVERGED,
            claims_gate.EXIT_CANNOT_RUN, claims_gate.EXIT_NOT_ARMED) == (0, 1, 2, 4)
    assert (perf_gate.EXIT_OK, perf_gate.EXIT_REGRESSED,
            perf_gate.EXIT_CANNOT_RUN, perf_gate.EXIT_NOT_ARMED) == (0, 1, 2, 4)
    assert (seb_door_gate.EXIT_OK, seb_door_gate.EXIT_UNMEASURED,
            seb_door_gate.EXIT_NOT_ENFORCED) == (0, 2, 3)

    text = page()
    for sentence in ("**0** OK", "**1** diverged", "**2** the box could not be measured",
                     "**4** not armed", "**3** not enforced"):
        assert sentence in text, (
            f"§3 tells the reader which numbers are the gates', and {sentence!r} is "
            "one of them")


def test_the_perf_lines_the_page_quotes_are_the_lines_the_gate_prints():
    text = page()
    gate = (ROOT / "deploy" / "perf_gate.py").read_text(encoding="utf-8")
    # The four `NOT ARMED` causes the §2(d) table asks a reader to tell apart, plus the
    # two other verdicts, each by the gate's own words. The page quotes those words, so
    # a reworded gate or a table that dropped a row fails here.
    for fragment in ("NOT ARMED", "harness", "is missing", "murid", "are needed",
                     "no --base URL", "REGRESSED", "CANNOT MEASURE"):
        assert fragment in gate, f"the perf gate no longer prints {fragment!r}"
    for fragment in ("NOT ARMED", "harness", "is missing", "are needed",
                     "no --base URL", "REGRESSED", "CANNOT MEASURE"):
        assert fragment in text, (
            f"§2(d) no longer quotes the gate's own words for it: {fragment!r}")

    # And the lift it recommends has to be the flag the gate parses and the runner
    # actually hands over — the assignment, not the word, because the word is also in
    # the runner's comments and its refusal text.
    assert '"--rebaseline", action="store_true"' in gate, (
        "the gate's parser no longer takes --rebaseline, which §2(d) tells an operator "
        "to ask for")
    assert "PERF_REBASELINE_ARGS=(--rebaseline)" in RUNNER.read_text(encoding="utf-8"), (
        "the runner no longer hands --rebaseline to the gate, so the lift §2(d) names "
        "would be a request nothing acts on")


def test_the_flags_the_page_types_are_in_the_parsers_that_take_them():
    text = page()
    expected = {
        SERVED: ("--base", "--commit", "--repo"),
        MIGRATE: ("--verify", "--repo", "--commit"),
        ARM: ("--check",),
        ROOT / "deploy" / "perf_gate.py": ("--rebaseline",),
    }
    for tool, flags in expected.items():
        body = tool.read_text(encoding="utf-8")
        lost = [flag for flag in flags if flag not in body]
        assert not lost, f"{tool.name} no longer takes {lost}"
        for flag in flags:
            assert flag in text, (
                f"the page no longer types {flag}, which {tool.name} still takes")


def test_the_programs_the_page_runs_are_in_the_tree():
    text = page()
    for program in (
        "deploy/arm-auto-deploy.sh",
        "deploy/install-auto-deploy.sh",
        "deploy/scangrade-deploy.sh",
        "deploy/served_commit_gate.py",
        "deploy/apply_migration.py",
        "deploy/scangrade-recover.sh",
    ):
        assert program in text, f"the page no longer runs {program}"
        assert (ROOT / program).exists(), f"the page runs {program}, which is not here"
    # The lever's own name, in the three files that install it, render it and answer
    # under it: a page that says `sgfix` while no script knows the word is a page that
    # sends an operator to a command that does not exist.
    for source in (ARM, ENTRYPOINT, RECOVER):
        assert "sgfix" in source.read_text(encoding="utf-8"), (
            f"{source.name} does not know the lever the page names")


def test_the_two_refusal_records_are_told_apart_by_the_page_and_kept_apart_by_the_runner():
    text = page()
    runner = RUNNER.read_text(encoding="utf-8")
    # The two files §3 asks an operator to read, and the reason they are different
    # facts: one is about a commit, the other about the box.
    assert "refused-before-merge" in text and "quarantined" in text
    for name in ("PREFLIGHT_FILE", "PREFLIGHT_DIFF_FILE", "QUARANTINE_FILE"):
        assert name in runner, f"the runner no longer names {name}"

    # §3's claim that a compile failure, a construction failure and a gate refusal are
    # all *post-merge* (and therefore their own quarantine), while a fetch or merge
    # failure is not: the runner's own steps have to keep that split.
    for step in ("RUN_STEP=\"dependencies\"", "RUN_STEP=\"construct\""):
        assert step in runner, f"the runner no longer reaches {step}"
