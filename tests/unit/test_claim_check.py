"""Guards for the check that reads AGENTS.md's numbers back to this checkout.

The tool is cheap on purpose — collection instead of execution, an AST read of a
harness instead of a mutation run — and every property that makes it cheap is
also a way it could become useless while still printing a green line:

* **A number it read wrongly is worse than no check.** The first draft matched
  any number within 120 characters of a test path and reported 63 "drifts" from
  66 files; none were real. The shapes below pin what is and is not a claim,
  because each of those 63 came from one of them.
* **History is not drift.** The log is append-only: a file cited in September
  and again in October has two true numbers and only the latest describes the
  file today. Verifying an older one would report the log's honesty as a defect.
* **Growth is not drift either; loss is.** A suite that gained tests after the
  entry was written is an old sentence, not a false one. A suite that now
  collects fewer than the log says is a contradiction about this checkout — the
  direction in which coverage disappeared and nothing said so.
* **A pass count cannot be proven without a run**, so the tool says
  `consistent` and names what it did not do. That word has to mean exactly that.
* **No harness is executed.** Sixteen mutation runs at full cost is the thing
  this tool exists to avoid; the only subprocess it starts by default is
  `pytest --collect-only`.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"


def _load():
    spec = importlib.util.spec_from_file_location("claim_check", DEPLOY / "claim_check.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["claim_check"] = module
    spec.loader.exec_module(module)
    return module


cc = _load()

ROUTE = "tests/unit/test_thing.py"


def _collector(answers):
    """A stand-in for pytest collection, so no test here starts one."""
    def collect(root, paths):
        return {path: answers.get(path, 0) for path in paths}
    return collect


def _repo(tmp_path: Path, *files: str) -> Path:
    for name in files:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# fixture\n", encoding="utf-8")
    return tmp_path


class TestWhatCountsAsATestClaim:
    def test_the_parenthesised_total_is_the_file_count(self):
        claims = cc.test_claims(f"Guards: `{ROUTE}` (19 tests — the reader, the "
                                f"failure path, and two renders).\n")
        assert [(c.target, c.number, c.word) for c in claims] == [(ROUTE, 19, "tests")]

    def test_a_bold_pass_count_is_a_claim(self):
        claims = cc.test_claims(f"`{ROUTE}` **7 lulus**; 398 uji tetangga hijau.\n")
        assert [(c.number, c.word) for c in claims] == [(7, "lulus")]

    def test_an_added_count_is_not_a_total(self):
        """`(+4 uji: …)` says four tests were added, not that the file has four."""
        assert cc.test_claims(f"`{ROUTE}` (+4 uji: path, writer, answer, refusal)\n") == []

    def test_a_count_of_one_mode_is_not_the_files_count(self):
        """Measured shape: `(8 uji mode …, 4 uji wiring)` — two subsets, no total."""
        assert cc.test_claims(f"`{ROUTE}` (8 uji mode termasuk arah sebaliknya, "
                              "4 uji wiring termasuk blok)\n") == []

    def test_a_number_far_from_the_path_is_not_its_count(self):
        text = f"`{ROUTE}` gagal bila keduanya menyimpang; 87 uji lain hijau.\n"
        assert cc.test_claims(text) == []


class TestHistory:
    def test_only_the_newest_claim_is_verified(self, tmp_path):
        text = (f"`{ROUTE}` (7 tests — first pass)\n"
                f"`{ROUTE}` (9 tests — after the second door)\n")
        repo = _repo(tmp_path, ROUTE)
        report = cc.verify(repo, text, collect=_collector({ROUTE: 9}))
        superseded = [f for f in report.findings if f.status == "superseded"]
        verified = [f for f in report.findings if f.status != "superseded"]
        assert len(superseded) == 1 and superseded[0].said == "7 tests"
        assert [(f.status, f.said) for f in verified] == [("ok", "9 tests")]

    def test_an_older_claim_never_fails_the_run(self, tmp_path):
        text = (f"`{ROUTE}` (99 tests)\n"
                f"`{ROUTE}` (5 tests)\n")
        repo = _repo(tmp_path, ROUTE)
        report = cc.verify(repo, text, collect=_collector({ROUTE: 5}))
        assert report.drift == []


class TestGrowthAndLoss:
    def test_more_collected_than_claimed_is_growth_not_drift(self, tmp_path):
        repo = _repo(tmp_path, ROUTE)
        report = cc.verify(repo, f"`{ROUTE}` (19 tests)\n",
                           collect=_collector({ROUTE: 22}))
        assert [(f.status, f.saw) for f in report.findings] == [
            ("grown", "22 collected (suite grew after the entry)")]
        assert report.drift == []

    def test_fewer_collected_than_claimed_is_drift(self, tmp_path):
        repo = _repo(tmp_path, ROUTE)
        report = cc.verify(repo, f"`{ROUTE}` (19 tests)\n",
                           collect=_collector({ROUTE: 17}))
        assert [f.status for f in report.findings] == ["drift"]
        assert "lost" in report.findings[0].saw
        assert len(report.drift) == 1

    def test_a_pass_count_that_matches_collection_is_not_proof(self, tmp_path):
        repo = _repo(tmp_path, ROUTE)
        report = cc.verify(repo, f"`{ROUTE}` (7 lulus)\n",
                           collect=_collector({ROUTE: 7}))
        finding = report.findings[0]
        assert finding.status == "consistent"
        assert "passing needs a run" in finding.saw, (
            "the report claims a pass count was verified when only the total was")

    def test_more_passes_than_tests_is_a_contradiction(self, tmp_path):
        repo = _repo(tmp_path, ROUTE)
        report = cc.verify(repo, f"`{ROUTE}` (7 lulus)\n",
                           collect=_collector({ROUTE: 5}))
        assert [f.status for f in report.findings] == ["drift"]


class TestFilesTheLogRetires:
    def test_a_missing_file_is_named(self, tmp_path):
        report = cc.verify(tmp_path, f"`{ROUTE}` (17 uji)\n",
                           collect=_collector({ROUTE: 17}))
        assert [f.status for f in report.findings] == ["missing"]

    def test_a_file_the_entry_says_was_deleted_is_retired_not_missing(self, tmp_path):
        """Measured: the log says `(17 uji) dihapus … digantikan berkas baru`;
        reporting that as drift is the tool being less informed than the text."""
        text = f"`{ROUTE}` (17 uji) dihapus dengan alasan yang sama.\n"
        report = cc.verify(tmp_path, text, collect=_collector({ROUTE: 17}))
        assert [f.status for f in report.findings] == ["retired"]
        assert report.drift == []

    def test_a_deletion_elsewhere_in_the_entry_does_not_retire_this_file(self, tmp_path):
        text = (f"`{ROUTE}` (17 uji) masih ada; berkas lain sudah digantikan "
                "berkas baru dan tidak lagi dipakai.\n")
        report = cc.verify(tmp_path, text, collect=_collector({ROUTE: 17}))
        assert [f.status for f in report.findings] == ["missing"]


class TestHarnessScores:
    def _harness(self, repo: Path, count: int, name="mutate_thing.py") -> str:
        target = f".freebuff/{name}"
        path = repo / target
        path.parent.mkdir(parents=True, exist_ok=True)
        cases = ",\n    ".join(f"({i}, 'case {i}')" for i in range(count))
        path.write_text(f"MUTATIONS = [\n    {cases},\n]\n", encoding="utf-8")
        return target

    def test_the_declared_mutations_are_counted_without_injection(self, tmp_path):
        target = self._harness(tmp_path, 4)
        assert cc.mutations_declared(tmp_path / target) == 4
        report = cc.verify(tmp_path, f"mutation-checked, **4/4 caught** (`{target}`)\n")
        assert [f.status for f in report.findings] == ["consistent"]
        assert "4 declared" in report.findings[0].saw

    def test_a_harness_that_grew_is_grown_not_drift(self, tmp_path):
        target = self._harness(tmp_path, 6)
        report = cc.verify(tmp_path, f"**4/4 caught** (`{target}`)\n")
        assert [f.status for f in report.findings] == ["grown"]
        assert report.drift == []

    def test_a_harness_that_shrank_is_drift(self, tmp_path):
        target = self._harness(tmp_path, 4)
        report = cc.verify(tmp_path, f"**6/6 caught** (`{target}`)\n")
        assert [f.status for f in report.findings] == ["drift"]
        assert "only 4" in report.findings[0].saw

    def test_an_absent_harness_is_unavailable_and_not_a_failure(self, tmp_path):
        target = ".freebuff/mutate_not_here.py"
        report = cc.verify(tmp_path, f"**9/9 caught** (`{target}`)\n")
        assert [f.status for f in report.findings] == ["unavailable"]
        assert report.drift == []

    def test_a_fraction_far_from_the_harness_is_not_its_score(self, tmp_path):
        target = self._harness(tmp_path, 4)
        padding = "x" * 400
        report = cc.verify(tmp_path, f"**4/4 caught** (`{target}`) {padding} 9/7\n")
        claims = [f.said for f in report.findings]
        assert claims == ["4/4"], claims

    def test_more_caught_than_injected_is_not_a_score(self, tmp_path):
        target = self._harness(tmp_path, 4)
        report = cc.verify(tmp_path, f"**9/7 caught** (`{target}`)\n")
        assert [f.status for f in report.findings] == ["drift"]


class TestItStaysCheap:
    def test_the_only_default_subprocess_is_a_collected_run(self):
        source = (DEPLOY / "claim_check.py").read_text(encoding="utf-8")
        calls = [line.strip() for line in source.splitlines()
                 if "subprocess.run(" in line]
        assert len(calls) == 2, calls
        assert "--collect-only" in source
        assert '"--run-harness"' in source or "\"--run-harness\"" in source, (
            "the only opt-in execution path is not named")

    def test_reading_a_harness_uses_ast_and_never_imports_it(self):
        source = (DEPLOY / "claim_check.py").read_text(encoding="utf-8")
        body = source[source.index("def mutations_declared"):]
        body = body[:body.index("\n# ──")]
        assert "ast.parse" in body
        assert "exec_module" not in body and "import_module" not in body


class TestTheReport:
    def test_unchecked_numbers_are_counted_and_sampled(self, tmp_path):
        repo = _repo(tmp_path, ROUTE)
        text = (f"`{ROUTE}` (19 tests)\n"
                "400 concurrent students, p50 374 ms, 36 KB lighter.\n")
        report = cc.verify(repo, text, collect=_collector({ROUTE: 19}))
        assert report.unchecked >= 3
        assert any("AGENTS.md:2" in sample for sample in report.unchecked_sample)

    def test_a_dates_digits_are_not_counted_as_prose(self, tmp_path):
        repo = _repo(tmp_path, ROUTE)
        text = (f"`{ROUTE}` (19 tests)\n"
                "Applied 2026-10-09 at 03:44Z.\n")
        report = cc.verify(repo, text, collect=_collector({ROUTE: 19}))
        assert report.unchecked == 0, report.unchecked_sample

    def test_the_default_report_leads_with_drift(self, tmp_path):
        repo = _repo(tmp_path, ROUTE)
        report = cc.verify(repo, f"`{ROUTE}` (19 tests)\n",
                           collect=_collector({ROUTE: 17}))
        rendered = cc.render(report)
        assert rendered.splitlines()[0].startswith("DRIFT (1)")
        assert "harnesses: read, never executed" in rendered


class TestTheCli:
    def test_no_log_is_exit_two(self, tmp_path):
        assert cc.main(["--repo", str(tmp_path)]) == 2

    def test_a_drift_is_exit_one(self, tmp_path, monkeypatch):
        repo = _repo(tmp_path, ROUTE)
        (repo / "AGENTS.md").write_text(f"`{ROUTE}` (19 tests)\n", encoding="utf-8")
        monkeypatch.setattr(cc, "collect_counts", lambda root, paths: {ROUTE: 17})
        assert cc.main(["--repo", str(repo)]) == 1

    def test_an_agreeing_log_is_exit_zero(self, tmp_path, monkeypatch):
        repo = _repo(tmp_path, ROUTE)
        (repo / "AGENTS.md").write_text(f"`{ROUTE}` (19 tests)\n", encoding="utf-8")
        monkeypatch.setattr(cc, "collect_counts", lambda root, paths: {ROUTE: 19})
        assert cc.main(["--repo", str(repo)]) == 0

    def test_a_json_report_carries_the_findings(self, tmp_path, monkeypatch, capsys):
        repo = _repo(tmp_path, ROUTE)
        (repo / "AGENTS.md").write_text(f"`{ROUTE}` (19 tests)\n", encoding="utf-8")
        monkeypatch.setattr(cc, "collect_counts", lambda root, paths: {ROUTE: 19})
        assert cc.main(["--repo", str(repo), "--json"]) == 0
        import json
        payload = json.loads(capsys.readouterr().out)
        assert payload["checked"] == 1
        assert payload["findings"][0]["status"] == "ok"


def test_a_harness_that_extends_its_list_is_unverifiable_rather_than_undercounted(
        tmp_path):
    """`MUTATIONS += [...]` makes the literal a prefix, not the whole contract.

    A length read from the prefix would be smaller than the claim and would
    report drift for a harness that did nothing wrong; the honest answer is that
    this reader cannot count it.
    """
    target = ".freebuff/mutate_extended.py"
    path = tmp_path / target
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("MUTATIONS = [(1, 'a')]\nMUTATIONS += [(2, 'b')]\n",
                    encoding="utf-8")
    assert cc.mutations_declared(path) is None
    report = cc.verify(tmp_path, f"**2/2 caught** (`{target}`)\n")
    assert [f.status for f in report.findings] == ["unverifiable"]


def test_a_file_collection_could_not_import_is_unverifiable_not_zero(tmp_path):
    """The 0 pytest never printed is not a file with no tests.

    A checker that reads a broken environment as drift sends somebody to edit
    the log while the box cannot import a module — the one wrong direction.
    """
    repo = _repo(tmp_path, ROUTE)
    report = cc.verify(repo, f"`{ROUTE}` (19 tests)\n",
                       collect=lambda root, paths: {ROUTE: "ERROR tests/unit/x.py"})
    assert [f.status for f in report.findings] == ["unverifiable"]
    assert report.drift == []
