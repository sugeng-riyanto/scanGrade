"""The project this app talks to, and where its database is, are stated in prose.

`AGENTS.md` recorded the project ref with one letter missing — 19 characters where
the real one has 20 — and it was copied from there into a merge runbook and the
Windows dev script. Nothing failed for weeks, because nothing read it. The same
bullet carried a region no typo check could catch: `ap-southeast-1` against a
database in `ap-south-1`, on a **UU PDP disclosure**.

The guard is `deploy/supabase_region.py` now — a tool, not a test case — because the
pre-commit hook, the VPS auto-deploy and CI each ran a different thing before: the
hook did not run this file at all, and the file's `.env` half `pytest.skip`ped
wherever there was no `.env`, which is a pass to every caller that reads an exit
code. This file is the check on the check, and the thing it has to answer is what
"not measured" costs:

* **the record is judged everywhere.** Six recorded sites, one ref-shaped string,
  one region next to Supabase, the disclosure, the bullet and the capacity row — on
  a scratch tree, through the tool, as a verdict and an exit code.
* **the configuration is judged where it exists, and named where it does not.** A
  `.env` pointing at another project is a finding; no `.env` is a line saying so
  (`the configuration was NOT measured`), never a silent pass and never a skip. A
  half-read `.env` is `only partly measured`, because a file with `SUPABASE_URL` and
  no `DIRECT_URL` says nothing about where the database is.
* **the gate and the hook actually run it**, sliced from `deploy/theme_gate.sh` and
  executed the way the hook executes it, plus the pre-commit trigger and the CI
  workflow that make it run at all. A guard nobody invokes is a paragraph.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.unit.git_env import GIT_REPO_ENV, git

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
GATE = DEPLOY / "theme_gate.sh"
HOOK = DEPLOY / "git-hooks" / "pre-commit"
WORKFLOW = ROOT / ".github" / "workflows" / "region-guard.yml"

sys.path.insert(0, str(DEPLOY))
import supabase_region as sr  # noqa: E402

#: A ref of this project's shape that is not this project's ref — built, not
#: quoted, because the family scan reads every tracked file and a literal copy here
#: is exactly the second sighting it refuses. A fixture that needs the defect's
#: shape builds the shape.
WRONG_REF = "rosh" + "abcdefghijklmn"


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    """A scratch checkout git knows about — the tool reads `git ls-files`.

    A `.env` is written **after** `git add`, because a real one is gitignored: if it
    were tracked, the record half would read the same host and the configuration
    half would never be exercised on its own (a mutation proved exactly that — the
    `.env`-points-elsewhere test passed with the comparison removed).
    """
    git("init", "-q", cwd=str(tmp_path))
    tracked = {rel: text for rel, text in files.items() if rel != ".env"}
    for rel, text in tracked.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    git("add", "-A", cwd=str(tmp_path))
    for rel, text in files.items():
        if rel == ".env":
            (tmp_path / rel).write_text(text, encoding="utf-8")
    return tmp_path


def _clean_files() -> dict[str, str]:
    """A tree that should pass: the six sites, the source, exactly the floor.

    The region floor is hit exactly (`MINIMUM_REGION_SITES` mentions: the bullet and
    the row, the constant and three `REGION_PLACES` keys), so a fixture that loses
    one is a finding even before the rule it was meant to exercise — the floor is
    asserted by its own test below, against the rule directly.

    The two published claims **render** the value here, because that is the shape
    the release has: a page that spells it is the defect the rules below exist for,
    and a fixture that spelled it would pass a guard whose real subject is absent.
    """
    files = {rel: f"the project is {sr.REF}\n" for rel in sr.REF_SITES}
    files[sr.AGENTS] = (
        f"- Supabase project: `{sr.REF}` (region: {sr.REGION}, Mumbai)\n"
        f"| **Transfer Lintas Batas** (Pasal 27) | Supabase {sr.REGION}, Mumbai |\n"
    )
    files[sr.PRIVACY] = (
        "<p>Supabase berlokasi di <strong>{{ region_claim.label }}</strong>.</p>\n" * 2
    )
    files[sr.CAPACITY] = (
        "def machine():\n"
        "    from app.config import database_region\n"
        "    return {'database': f\"Supabase, {database_region()['short']}\"}\n"
        "MACHINE = {'region': 'ap-southeast-1 (Singapore)'}\n"
    )
    files[sr.REGION_SOURCE] = (
        f'SUPABASE_REGION_DEFAULT = "{sr.REGION}"\n'
        "REGION_PLACES = {\n"
        f'    "{sr.REGION}": ("Mumbai", "India"),\n'
        '    "ap-southeast-1": ("Singapore", "Singapore"),\n'
        '    "us-east-1": ("N. Virginia", "United States"),\n'
        "}\n"
    )
    return files


def _env(ref: str = sr.REF, region: str = sr.REGION) -> str:
    return (f"SUPABASE_URL=https://{ref}.supabase.co\n"
            f"DIRECT_URL=postgresql://postgres.{ref}:pw@aws-1-{region}"
            ".pooler.supabase.com:5432/postgres\n")


def _run(repo: Path, *extra: str) -> int:
    """The tool as the gate calls it, against a scratch tree."""
    return sr.main(["--repo", str(repo), *extra])


class TestTheRecordIsOneRef:

    def test_a_host_that_reaches_another_project_is_a_finding(self, tmp_path, capsys):
        files = _clean_files()
        files["app/routes/x.py"] = "URL = 'https://abcdefghijklmnopqrst.supabase.co'\n"
        assert _run(_repo(tmp_path, files)) == 1
        err = capsys.readouterr().err
        assert "app/routes/x.py:1" in err
        assert "abcdefghijklmnopqrst" in err

    def test_a_fabricated_host_in_a_test_is_a_fixture(self, tmp_path):
        files = _clean_files()
        files["tests/unit/test_x.py"] = "URL = 'https://abcdefghijklmnopqrst.supabase.co'\n"
        assert _run(_repo(tmp_path, files)) == 0

    def test_a_recorded_site_that_stops_naming_the_ref_is_a_finding(self, tmp_path, capsys):
        files = _clean_files()
        files["docs/features/SEB.md"] = "the project moved somewhere\n"
        assert _run(_repo(tmp_path, files)) == 1
        assert "docs/features/SEB.md" in capsys.readouterr().err

    def test_a_second_ref_shaped_string_is_a_finding(self, tmp_path, capsys):
        files = _clean_files()
        files["docs/x.md"] = f"see {WRONG_REF}\n"
        assert _run(_repo(tmp_path, files)) == 1
        assert WRONG_REF in capsys.readouterr().err

    def test_the_typo_survives_once_where_it_is_named_a_typo(self, tmp_path):
        files = _clean_files()
        files["docs/features/SEB.md"] = (
            f"{sr.REF} is right; {sr.REF_OLD} was the salah ketik.\n")
        assert _run(_repo(tmp_path, files)) == 0

    def test_a_second_typo_sighting_is_a_finding(self, tmp_path):
        files = _clean_files()
        files["docs/features/SEB.md"] = f"{sr.REF} and {sr.REF_OLD} salah ketik\n"
        files["docs/x.md"] = f"{sr.REF_OLD}\n"
        assert _run(_repo(tmp_path, files)) == 1

    def test_a_typo_quoted_without_saying_it_was_one_is_a_finding(self, tmp_path, capsys):
        files = _clean_files()
        files["docs/features/SEB.md"] = f"{sr.REF} replaces {sr.REF_OLD}\n"
        assert _run(_repo(tmp_path, files)) == 1
        assert "without saying it was one" in capsys.readouterr().err

    def test_a_ref_is_twenty_characters(self):
        # The typo was 19. Nothing about a 19-character string looks wrong until it
        # is compared with the API, so the length is stated where it can be seen.
        assert len(sr.REF) == 20, "a Supabase ref is 20 characters, by construction"

    def test_the_git_variables_match_the_suites(self):
        """`deploy/` may not import the suite, so the list is written twice."""
        assert sr.GIT_REPO_ENV == GIT_REPO_ENV


class TestTheRegionIsOneValue:

    def test_a_region_beside_supabase_that_is_not_the_databases_region(self, tmp_path, capsys):
        files = _clean_files()
        files["docs/x.md"] = "The Supabase database is in ap-southeast-1.\n"
        assert _run(_repo(tmp_path, files)) == 1
        assert "ap-southeast-1" in capsys.readouterr().err

    def test_the_boxes_own_region_is_not_the_databases(self, tmp_path):
        """`MACHINE["region"]` says where the VPS is; `database` is the claim."""
        assert _run(_repo(tmp_path, _clean_files())) == 0

    def test_prose_about_singapore_is_not_a_claim(self, tmp_path):
        """The claim is the region code; the words are checked on the disclosure.

        A global "Supabase + Singapore" rule read this repository's own record of
        the defect as the defect — `AGENTS.md` is one bullet per paragraph — and a
        guard that fails on the sentence describing the fix gets deleted.
        """
        files = _clean_files()
        files["docs/x.md"] = (
            f"the old record put the database in Singapore; {sr.REGION} is the fix\n")
        assert _run(_repo(tmp_path, files)) == 0

    def test_the_recorded_bullet_may_describe_the_correction(self, tmp_path):
        files = _clean_files()
        files[sr.AGENTS] = (
            f"- Supabase project: `{sr.REF}` (region: {sr.REGION}, Mumbai) — the "
            "old record put the database in Singapore\n"
            f"| **Transfer Lintas Batas** (Pasal 27) | Supabase {sr.REGION}, Mumbai |\n")
        assert _run(_repo(tmp_path, files)) == 0

    def test_a_scan_that_reads_nothing_is_a_finding(self):
        """The floor, tested on the rule: a tree whose mentions vanished is not clean."""
        report = sr.Report()
        sr._check_regions({"docs/a.md": f"Supabase {sr.REGION}\n"}, report)
        assert any(f"only {report.region_sites} region mention(s)" in f
                   for f in report.findings), report.findings

    def test_the_disclosure_renders_the_claim_twice(self, tmp_path, capsys):
        files = _clean_files()
        files[sr.PRIVACY] = "<p>Supabase is at {{ region_claim.label }}.</p>\n"
        assert _run(_repo(tmp_path, files)) == 1
        assert "it is stated twice" in capsys.readouterr().err

    def test_a_disclosure_that_spells_the_region_is_a_finding(self, tmp_path, capsys):
        """The literal is the second copy — the one that went stale last time."""
        files = _clean_files()
        files[sr.PRIVACY] = f"<p>Supabase is in {sr.REGION}.</p>\n" * 2
        assert _run(_repo(tmp_path, files)) == 1
        err = capsys.readouterr().err
        assert "spells" in err and "two pages that publish it" in err

    def test_a_disclosure_that_kept_the_city_by_hand_is_a_finding(self, tmp_path, capsys):
        """A place name survives a region change; the configured label cannot."""
        files = _clean_files()
        files[sr.PRIVACY] = (
            "<p>Supabase is in {{ region_claim.label }} (Mumbai).</p>\n" * 2)
        assert _run(_repo(tmp_path, files)) == 1
        assert "names Mumbai itself" in capsys.readouterr().err

    def test_a_disclosure_that_still_points_at_singapore_is_a_finding(self, tmp_path, capsys):
        files = _clean_files()
        files[sr.PRIVACY] = (
            "<p>Supabase is at {{ region_claim.label }}, Singapura earlier.</p>\n" * 2)
        assert _run(_repo(tmp_path, files)) == 1
        err = capsys.readouterr().err
        assert "points at Singapore" in err and "Cloudflare's edge" in err

    def test_a_capacity_row_that_spells_the_region_is_a_finding(self, tmp_path, capsys):
        files = _clean_files()
        files[sr.CAPACITY] = (
            "def machine():\n"
            "    return {'database': 'Supabase, " + sr.REGION + " (Mumbai)'}\n"
        )
        assert _run(_repo(tmp_path, files)) == 1
        err = capsys.readouterr().err
        assert "states the database's region itself" in err

    def test_a_capacity_row_that_does_not_read_the_claim_is_a_finding(self, tmp_path, capsys):
        files = _clean_files()
        files[sr.CAPACITY] = (
            "def machine():\n"
            "    return {'database': 'Supabase'}\n"
        )
        assert _run(_repo(tmp_path, files)) == 1
        assert "not read from configuration" in capsys.readouterr().err

    def test_the_claim_has_one_source_and_it_agrees_with_the_record(self, tmp_path):
        """The release's own reading: the constant is the record's value."""
        assert _run(_repo(tmp_path, _clean_files())) == 0

    def test_a_constant_that_moved_away_from_the_record_is_a_finding(
            self, tmp_path, capsys):
        files = _clean_files()
        files[sr.REGION_SOURCE] = files[sr.REGION_SOURCE].replace(
            f'SUPABASE_REGION_DEFAULT = "{sr.REGION}"',
            'SUPABASE_REGION_DEFAULT = "ap-southeast-1"')
        assert _run(_repo(tmp_path, files)) == 1
        err = capsys.readouterr().err
        assert "SUPABASE_REGION_DEFAULT is ap-southeast-1" in err
        assert "publish the wrong region" in err

    def test_a_source_without_the_constant_is_a_finding(self, tmp_path, capsys):
        files = _clean_files()
        files[sr.REGION_SOURCE] = 'REGION_PLACES = {"%s": ("Mumbai", "India")}\n' % sr.REGION
        assert _run(_repo(tmp_path, files)) == 1
        assert "no `SUPABASE_REGION_DEFAULT" in capsys.readouterr().err

    def test_a_source_that_cannot_name_the_place_is_a_finding(self, tmp_path, capsys):
        """A code with no city is a disclosure that stopped naming a place."""
        files = _clean_files()
        files[sr.REGION_SOURCE] = (
            f'SUPABASE_REGION_DEFAULT = "{sr.REGION}"\n'
            'REGION_PLACES = {"us-east-1": ("N. Virginia", "United States")}\n')
        assert _run(_repo(tmp_path, files)) == 1
        assert "REGION_PLACES has no" in capsys.readouterr().err

    def test_a_source_that_vanished_is_a_finding(self, tmp_path, capsys):
        files = _clean_files()
        del files[sr.REGION_SOURCE]
        assert _run(_repo(tmp_path, files)) == 1
        assert sr.REGION_SOURCE in capsys.readouterr().err

    def test_the_recorded_bullet_carries_both_facts(self, tmp_path, capsys):
        files = _clean_files()
        files[sr.AGENTS] = (
            f"- Supabase project: `{sr.REF}`\n"
            f"| **Transfer Lintas Batas** (Pasal 27) | Supabase {sr.REGION}, Mumbai |\n"
        )
        assert _run(_repo(tmp_path, files)) == 1
        assert "recorded bullet" in capsys.readouterr().err


class TestTheConfigurationIsMeasuredOrNamed:
    """The half that used to be a `pytest.skip` — and what happens without `.env`."""

    def test_no_env_is_named_and_not_passed_off_as_a_measurement(self, tmp_path, capsys):
        assert _run(_repo(tmp_path, _clean_files())) == 0
        out = capsys.readouterr().out
        assert "the configuration was NOT measured" in out
        assert "the configuration was measured" not in out
        assert "no .env in this checkout" in out

    def test_a_matching_env_is_measured(self, tmp_path, capsys):
        files = _clean_files()
        files[".env"] = _env()
        assert _run(_repo(tmp_path, files)) == 0
        out = capsys.readouterr().out
        assert "the configuration was measured" in out
        assert sr.REF in out and sr.REGION in out

    def test_an_env_talking_to_another_project_is_a_finding(self, tmp_path, capsys):
        files = _clean_files()
        files[".env"] = _env(ref="abcdefghijklmnopqrst")
        assert _run(_repo(tmp_path, files)) == 1
        assert "abcdefghijklmnopqrst" in capsys.readouterr().err

    def test_an_env_pooler_in_another_region_is_a_finding(self, tmp_path, capsys):
        files = _clean_files()
        files[".env"] = _env(region="ap-southeast-1")
        assert _run(_repo(tmp_path, files)) == 1
        err = capsys.readouterr().err
        assert "DIRECT_URL's pooler" in err and "ap-southeast-1" in err

    def test_a_half_read_env_is_not_a_measured_one(self, tmp_path, capsys):
        """`SUPABASE_URL` alone says nothing about where the database is."""
        files = _clean_files()
        files[".env"] = f"SUPABASE_URL=https://{sr.REF}.supabase.co\n"
        assert _run(_repo(tmp_path, files)) == 0
        out = capsys.readouterr().out
        assert "only partly measured" in out
        assert "the configuration was measured" not in out

    def test_a_url_that_is_not_a_supabase_host_is_a_finding(self, tmp_path, capsys):
        files = _clean_files()
        files[".env"] = f"SUPABASE_URL=https://example.test/{sr.REF}\n"
        assert _run(_repo(tmp_path, files)) == 1
        assert "not a Supabase host" in capsys.readouterr().err


class TestTheGateRunsIt:
    """The block that runs the tool, executed — not read."""

    def _block(self) -> str:
        text = GATE.read_text(encoding="utf-8")
        return text.split("# region:start", 1)[1].split("# region:end", 1)[0]

    def _run_block(self, repo: Path) -> subprocess.CompletedProcess:
        script = repo.parent / "region-block.sh"
        # Bytes, not `write_text`: Windows translates `\n` to `\r\n`, and bash
        # reads a CRLF script as `$'\r': command not found`.
        script.write_bytes(self._block().replace("\r\n", "\n").encode("utf-8"))
        env = dict(os.environ)
        env["PY"] = sys.executable
        env["REPO"] = str(repo)
        return subprocess.run(["bash", str(script)], capture_output=True,
                              text=True, encoding="utf-8", errors="replace", env=env)

    def test_a_wrong_ref_stops_the_release(self, tmp_path):
        files = _clean_files()
        files["docs/x.md"] = f"see {WRONG_REF}\n"
        files["deploy/supabase_region.py"] = (
            DEPLOY / "supabase_region.py").read_text(encoding="utf-8")
        done = self._run_block(_repo(tmp_path, files))
        assert done.returncode == 1, done.stderr
        assert "FAILED" in done.stderr
        assert WRONG_REF in done.stderr

    def test_a_clean_tree_lets_it_through(self, tmp_path):
        files = _clean_files()
        files["deploy/supabase_region.py"] = (
            DEPLOY / "supabase_region.py").read_text(encoding="utf-8")
        done = self._run_block(_repo(tmp_path, files))
        assert done.returncode == 0, done.stderr
        assert "recorded site(s)" in done.stdout

    def test_the_gate_arms_the_check(self):
        text = GATE.read_text(encoding="utf-8")
        armament = next(line for line in text.splitlines()
                        if line.startswith("ARMAMENT="))
        assert "deploy/supabase_region.py" in armament
        block = self._block()
        assert '"$REPO/deploy/supabase_region.py"' in block
        assert "REGION_CLAIM" in block
        assert "exit 1" in block
        assert "${REGION_CLAIM}" in text, "a run whose result nobody prints"
        # The success sentence may only claim the `.env` half when the tool said it
        # measured it — the phrase is the contract between the two files.
        assert "the configuration was measured" in block
        assert "the configuration was measured" in (
            DEPLOY / "supabase_region.py").read_text(encoding="utf-8")

    def test_the_pre_commit_hook_runs_the_gate_for_what_the_guard_reads(self):
        """The typo shipped in `AGENTS.md`; a hook that ignores `.md` never saw it."""
        text = HOOK.read_text(encoding="utf-8")
        line = next(l for l in text.splitlines() if "grep -qE" in l)
        pattern = line.split("grep -qE '", 1)[1].rsplit("'", 1)[0]
        compiled = re.compile(pattern)
        for rel in ("AGENTS.md", "docs/features/SEB.md", "start-dev.ps1",
                    "app/templates/super_admin/file_management.html"):
            assert compiled.search(rel), f"{rel} does not trigger the gate"

    def test_ci_runs_the_same_tool_and_the_same_suite(self):
        assert WORKFLOW.exists(), "CI has no region guard workflow"
        text = WORKFLOW.read_text(encoding="utf-8")
        assert "python deploy/supabase_region.py" in text
        assert "tests/unit/test_supabase_region.py" in text
        assert "the configuration was NOT measured" in text, (
            "CI has no .env, and the run must prove the absence is stated rather "
            "than skipped")


class TestTheRepository:

    def test_every_tracked_file_agrees(self):
        report = sr.scan(ROOT)
        assert report.offenders() == [], "\n".join(report.offenders())
        assert report.ref_sites == len(sr.REF_SITES)
        assert report.region_sites >= sr.MINIMUM_REGION_SITES

    def test_the_typo_is_still_recorded_where_it_was_found(self):
        """The record of the defect survives — and only there.

        A scratch tree may quote the old spelling nowhere; *this* tree has to keep
        the one line that says it was a typo, because the reason the guard exists is
        a fact a reader has to be able to find.
        """
        report = sr.scan(ROOT)
        assert report.typo_sites == 1, (
            f"{report.typo_sites} sighting(s) of the old spelling in "
            f"{sr.OLD_RECORD}; the record of the defect is one line")

    def test_the_configuration_half_is_measured_or_says_it_is_not(self):
        report = sr.scan(ROOT)
        if (ROOT / ".env").is_file():
            assert report.config.state in ("measured", "partly"), report.config.reason
            if report.config.measured:
                assert report.config.url_ref == sr.REF
                assert report.config.pooler_region == sr.REGION
        else:
            assert report.config.state == "not measured"
            assert report.config.reason, "an unmeasured half with no reason is a skip"

    def test_the_tool_prints_the_absence_phrase_the_gate_greps(self):
        """One phrase, two files: the gate's claim and this run's honesty."""
        text = (DEPLOY / "supabase_region.py").read_text(encoding="utf-8")
        gate = GATE.read_text(encoding="utf-8")
        assert "the configuration was NOT measured" in text
        assert "the configuration was measured" in gate
