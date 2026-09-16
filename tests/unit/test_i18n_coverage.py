"""The translation number, and the floor it may not fall below.

`deploy/i18n_coverage.py` answers "how much of this template actually switches
with the toggle" — `pairs / (pairs + leftovers)` — and refuses a release that
translates less than the last one that passed. The call is not where an answer is
proven, so what follows is:

* **the two readers agree.** The tool and the sweep in
  `tests/unit/test_language_toggle.py` read the same templates with the same
  vocabulary, and the tool *copies* that vocabulary because a deploy gate must
  not import the test suite. A copy drifts; an equality assertion cannot. Every
  template is compared string-for-string, so a divergence is a failure here
  rather than two tools quietly disagreeing about what "untranslated" means.
* **the metric is not gameable.** A pair is a numerator and never a denominator
  (counting a translated string as both is how a coverage number flatters
  itself), a template with nothing to translate is not 0%, and a comment is not
  copy — that last one was a real false positive: the text-node pass read a JS
  comment in base.html as an untranslated string.
* **the floors cover the tree**, so a new page's number cannot silently start at
  zero, and they are never *above* what the tree is today.
* **the exit codes are the contract**: 0 pass, 1 a page translates less, 2 the
  comparison could not be made.
* **the gate runs it.** Including the bug that made this file worth writing: the
  gate's own failure text told the reader to run `python audit_page_lang.py
  --templates`, and no such file exists in the repository.
"""
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
GATE = DEPLOY / "theme_gate.sh"
TEMPLATES = ROOT / "app" / "templates"

sys.path.insert(0, str(DEPLOY))
import i18n_coverage as cov  # noqa: E402


def _module_by_path(name, path):
    """Load a module by path, so no import mode has to be assumed."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def sweep():
    return _module_by_path("_sweep", ROOT / "tests" / "unit" / "test_language_toggle.py")


@pytest.fixture(scope="module")
def rows():
    return cov.scan()


def _leftover_chunks(path):
    """The tool's leftovers as raw strings (what the report would show)."""
    return cov.counts(path.read_text(encoding="utf-8", errors="replace"))[1]


# ── the two readers agree ────────────────────────────────────────────────────

class TestTheTwoReadersAgree:
    """A copied vocabulary is only safe if something forbids it from drifting."""

    def test_the_marker_vocabulary_is_the_same_set(self, sweep):
        assert cov.INDONESIAN_MARKERS == sweep.INDONESIAN_MARKERS, (
            "the tool's marker set and the sweep's have drifted. The tool copies "
            "the set on purpose (a deploy gate must not import the test suite), so "
            "this assertion is what keeps the copy honest:\n"
            f"  only in the tool: {sorted(cov.INDONESIAN_MARKERS - sweep.INDONESIAN_MARKERS)}\n"
            f"  only in the sweep: {sorted(sweep.INDONESIAN_MARKERS - cov.INDONESIAN_MARKERS)}"
        )

    def test_both_readers_find_the_same_leftovers(self, sweep, rows):
        """Every template, string for string — not a count, not a sample."""
        disagreements = []
        for rel in sorted(rows):
            path = TEMPLATES / rel
            mine = sorted(_leftover_chunks(path))
            theirs = sorted(
                c for c in sweep._visible_strings(
                    path.read_text(encoding="utf-8", errors="replace"))
                if sweep._markers_in(c)
            )
            if mine != theirs:
                disagreements.append(
                    f"{rel}:\n    tool only: {mine[:3]}\n    sweep only: {theirs[:3]}")
        assert not disagreements, (
            "the coverage tool and the language sweep disagree about what is "
            "untranslated, so one of them is wrong about the number this gate "
            "publishes:\n  " + "\n  ".join(disagreements[:6])
        )

    def test_a_script_comment_is_not_copy(self):
        """The false positive that made the readers disagree.

        `// Alpine's \\`t()\\` is a method on the body scope` — an apostrophe opens
        a literal, the JS comment runs to the next `<`, and the text-node pass
        reported base.html as carrying an untranslated string. Removing script
        bodies before that pass is what fixed it, and the script's own strings are
        still read (they are read *before* it).
        """
        sample = (
            "<script>\n"
            "// Alpine's `t()` is a method on the body scope, so isi semua\n"
            "// templates with `t('Simpan','Save')` messages\n"
            "this.msg = 'Hapus data murid';\n"
            "</script>\n"
        )
        _, leftovers = cov.counts(sample)
        assert leftovers == ["Hapus data murid"], (
            "a JS comment is being read as copy, or the script's own Indonesian "
            f"string is being missed: {leftovers}"
        )


# ── the metric ───────────────────────────────────────────────────────────────

class TestTheMetricIsNotGameable:
    def test_a_pair_is_a_numerator_and_never_a_denominator(self):
        pairs, leftovers = cov.counts("<p x-text=\"t('Simpan','Save')\"></p>")
        assert (pairs, leftovers) == (1, [])
        assert cov.coverage(pairs, len(leftovers)) == 100.0

    def test_a_page_with_nothing_to_translate_is_not_zero_percent(self):
        """`Dashboard` is the same word in both languages; it needs no pair."""
        pairs, leftovers = cov.counts("<th>Dashboard</th>")
        assert (pairs, leftovers) == (0, [])
        assert cov.coverage(pairs, 0) == 100.0, (
            "a page whose copy is all shared vocabulary would be reported as 0% "
            "translated and fail a floor it cannot ever reach"
        )

    def test_an_untranslated_string_lowers_the_number(self, sweep):
        text = "<p x-text=\"t('Simpan','Save')\"></p><span>Hapus data murid</span>"
        pairs, leftovers = cov.counts(text)
        assert pairs == 1 and len(leftovers) == 1
        assert cov.coverage(pairs, len(leftovers)) == 50.0, (
            "one translated and one untranslated string must read 50%, not 100% "
            "and not 0%"
        )
        del sweep

    def test_data_is_not_copy(self):
        """An email is the same string in both languages, and its local part
        contains marker words (`guru_mtk_smp@…`)."""
        pairs, leftovers = cov.counts('<span x-text="\'guru_mtk_smp@scan-grade.app\'"></span>')
        assert pairs == 0 and leftovers == [], leftovers


# ── the floors ───────────────────────────────────────────────────────────────

class TestTheFloorsCoverTheTree:
    def test_a_keyed_catalogue_is_read_inside_an_attribute(self, rows):
        """`x-data="{ id: 'Simpan', en: 'Save' }"` — the pair shape has to be
        found inside an attribute, and the entry counts its two keys.

        Counting per *key* rather than per entry is the documented bias of this
        metric: 42 keyed literals exist tree-wide against ~1600 pairs, so it can
        move the overall percentage by well under a point, and the floors are
        compared with themselves rather than with a target. Grouping keys into
        entries would need a parser for a shape this app writes on one line.
        """
        assert cov.counts('<div x-data="{ id: \'Simpan\', en: \'Save\' }"></div>')[0] == 2, (
            "the keyed catalogue shape is not being read, so a page that stores its "
            "copy in `x-data` would score 0% while being fully translated"
        )
        assert cov.counts('<span>Just a label</span>')[0] == 0

    def test_every_gated_template_has_a_floor(self, rows):
        floors = cov.load_baseline()
        missing = sorted(r for r, v in rows.items()
                         if not v["standalone"] and r not in floors)
        assert not missing, (
            "these templates have no recorded floor, so the gate cannot tell "
            "whether they regressed and a new page's number starts at zero "
            "unnoticed. Record it deliberately:\n"
            "  python deploy/i18n_coverage.py --write-baseline\n  "
            + "\n  ".join(missing[:10])
        )

    def test_the_standalone_documents_are_the_designed_ones(self):
        """Four documents are Indonesian on purpose, and the property is real:
        they do not extend base.html, so they have no toggle to honour."""
        for rel in cov.STANDALONE:
            text = (TEMPLATES / rel).read_text(encoding="utf-8")
            assert 'extends "base.html"' not in text, (
                f"{rel} is exempt from the coverage floor because it is a "
                "standalone printed document, but it extends base.html — it has a "
                "toggle after all and the exemption is hiding it"
            )
            assert 'lang="id"' in text, (
                f"{rel} is exempt from the floor but does not declare `lang=\"id\"`, "
                "so it is neither translated nor honestly pinned"
            )

    def test_the_tree_is_at_or_above_its_floors(self, rows):
        """The floors are a ratchet, not a target: they must never be *above* the
        tree, or the gate fails on every release until someone translates."""
        floors = cov.load_baseline()
        regressed, _ = cov.check(rows, floors)
        assert not regressed, "the repository is below its own floors:\n  " + \
            "\n  ".join(regressed)

    def test_the_baseline_is_lf_wherever_it_is_written(self):
        """A committed artifact has to be the same bytes on both platforms.

        `Path.write_text` translates `\n` to `\r\n` on Windows by default, so the
        first baseline came out CRLF and every later `--write-baseline` on the
        other platform showed as a whole-file diff. Found by a mutation harness
        that could not match its own anchor.
        """
        assert b"\r\n" not in cov.BASELINE.read_bytes(), (
            "deploy/i18n_baseline.json has CRLF line endings; write it with "
            "`newline='\\n'` so the Windows checkout and the Linux deploy agree"
        )

    def test_the_floors_are_exactly_what_scan_sees(self, rows):
        """A floor is a recorded measurement, so `--write-baseline` round-trips."""
        floors = cov.load_baseline()
        for rel, floor in floors.items():
            if rel not in rows:
                continue
            assert floor["pairs"] == rows[rel]["pairs"], (
                f"{rel}: the committed floor says {floor['pairs']} pairs and the "
                f"template has {rows[rel]['pairs']} — the file is stale"
            )


# ── the exit codes ───────────────────────────────────────────────────────────

class TestTheExitCodesAreTheContract:
    def _baseline(self, tmp_path, mutate):
        floors = json.loads(cov.BASELINE.read_text(encoding="utf-8"))
        mutate(floors["pages"])
        path = tmp_path / "baseline.json"
        path.write_text(json.dumps(floors), encoding="utf-8")
        return path

    def test_the_tree_passes(self, capsys):
        assert cov.main([]) == 0
        assert "i18n coverage: OK" in capsys.readouterr().out

    def test_a_page_that_lost_bilingual_copy_fails(self, tmp_path, monkeypatch, capsys):
        page = "teacher/_results_table.html"

        def mutate(pages):
            pages[page]["pairs"] += 5

        monkeypatch.setattr(cov, "BASELINE", self._baseline(tmp_path, mutate))
        assert cov.main([]) == 1
        err = capsys.readouterr().err
        assert "FAILED" in err and page in err, err

    def test_a_page_whose_coverage_fell_fails(self, tmp_path, monkeypatch, capsys):
        """The second floor: pairs unchanged, but coverage below the floor.

        The page is chosen by looking for one that still has leftovers, so this
        test cannot start passing because the app finished its translations.
        """
        rows = cov.scan()
        candidates = [r for r, v in rows.items()
                      if v["leftovers"] > 0 and not v["standalone"]]
        assert candidates, (
            "no gated template has a leftover string any more, so this floor has "
            "nothing to protect — the metric has become a constant"
        )
        page = sorted(candidates)[0]

        def mutate(pages):
            pages[page]["coverage"] = 100.0

        monkeypatch.setattr(cov, "BASELINE", self._baseline(tmp_path, mutate))
        assert cov.main([]) == 1
        err = capsys.readouterr().err
        assert page in err and "coverage fell" in err, err

    def test_a_newly_frozen_page_fails(self, tmp_path, monkeypatch, capsys):
        """A translated page pinned to Indonesian has just stopped switching."""
        frozen = [r for r, v in cov.scan().items() if v["frozen"]]
        assert frozen, "no template is frozen any more; this guard needs a new one"

        def mutate(pages):
            pages[frozen[0]]["frozen"] = False

        monkeypatch.setattr(cov, "BASELINE", self._baseline(tmp_path, mutate))
        assert cov.main([]) == 1
        err = capsys.readouterr().err
        assert "freezes them" in err and frozen[0] in err, err

    def test_no_baseline_cannot_measure_and_does_not_pass(
            self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(cov, "BASELINE", tmp_path / "absent.json")
        assert cov.main([]) == 2
        assert "no baseline" in capsys.readouterr().err

    def test_a_template_with_no_floor_cannot_measure(
            self, tmp_path, monkeypatch, capsys):
        def mutate(pages):
            pages.pop("teacher/_results_table.html")

        monkeypatch.setattr(cov, "BASELINE", self._baseline(tmp_path, mutate))
        assert cov.main([]) == 2
        err = capsys.readouterr().err
        assert "no floor recorded" in err and "--write-baseline" in err

    def test_a_floor_for_a_deleted_template_cannot_measure(
            self, tmp_path, monkeypatch, capsys):
        def mutate(pages):
            pages["gone/away.html"] = {"pairs": 0, "leftovers": 0, "coverage": 100.0}

        monkeypatch.setattr(cov, "BASELINE", self._baseline(tmp_path, mutate))
        assert cov.main([]) == 2
        assert "no longer exists" in capsys.readouterr().err

    def test_report_only_never_fails_a_build(self, tmp_path, monkeypatch, capsys):
        """The table is also what a person runs to see where to work next."""
        monkeypatch.setattr(cov, "BASELINE", tmp_path / "absent.json")
        assert cov.main(["--report-only"]) == 0
        assert "i18n coverage:" in capsys.readouterr().out


# ── the gate runs it ─────────────────────────────────────────────────────────

class TestTheGateRunsIt:
    def test_the_readability_gate_runs_the_tool(self):
        """The *invocation*, not a mention of the file.

        `'deploy/i18n_coverage.py' in src` is not this check: the gate's failure
        text names the same path, so deleting the run left the assertion passing
        — the mutation was MISSED until the pattern was pinned to the command.
        """
        src = GATE.read_text(encoding="utf-8")
        invocation = re.search(
            r'COVERAGE=\$\("\$PY"\s+"\$REPO/deploy/i18n_coverage\.py"', src)
        assert invocation, (
            "the gate does not *run* the coverage tool, so the number it publishes "
            "is not checked on any release. Naming the file in the failure text is "
            "not running it."
        )
        assert "COV_RC=$?" in src and 'if [ "$COV_RC" -ne 0 ]' in src, (
            "the gate runs the tool but ignores its verdict — a gate that cannot "
            "fail is a report"
        )
        ok = src.find("theme gate: OK")
        assert ok > invocation.start(), (
            "the coverage check runs *after* the gate has already printed OK, so it "
            "can no longer fail the release"
        )

    def test_both_places_that_must_gate_the_release_run_the_gate(self):
        hook = (DEPLOY / "git-hooks" / "pre-commit").read_text(encoding="utf-8")
        deploy = (DEPLOY / "scangrade-deploy.sh").read_text(encoding="utf-8")
        assert "theme_gate.sh" in hook, "the pre-commit hook stopped running the gate"
        assert "theme_gate.sh" in deploy, (
            "the auto-deploy stopped running the gate, so the checks only run on "
            "the developer's machine"
        )

    def test_every_tool_the_gate_names_exists(self):
        """The bug this file was written for.

        `deploy/theme_gate.sh` told the reader to run
        `python audit_page_lang.py --templates`, and there is no such file in the
        repository: the gate pointed at a tool somebody had deleted, so the
        instruction was a dead end exactly when it was needed.
        """
        src = GATE.read_text(encoding="utf-8")
        named = set(re.findall(r"\bdeploy/[\w./-]+\.py\b", src))
        named |= {m for m in re.findall(r"\bpython3?\s+([\w./-]+\.py)\b", src)}
        assert named, "the gate names no tool at all; this check has gone blind"
        missing = sorted(n for n in named if not (ROOT / n).is_file())
        assert not missing, (
            "the gate's own text tells the reader to run a file that does not "
            f"exist: {missing}"
        )

    def test_the_tool_needs_nothing_but_the_filesystem(self):
        """It runs as a hard gate before a release, on the box, with no database
        and no app context — an import of flask/app/requests would make it a
        different, slower, failing thing."""
        src = (DEPLOY / "i18n_coverage.py").read_text(encoding="utf-8")
        forbidden = re.findall(r"^\s*(?:import|from)\s+(requests|flask|supabase|app)\b",
                               src, re.M)
        assert not forbidden, f"the coverage tool imports {forbidden}"
