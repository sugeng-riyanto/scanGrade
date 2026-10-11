"""A checkout that cannot reproduce its own commit must not serve.

This is the third shape of one strand, and the only one nothing refused.

The box that reported `dirty_checkout` every two minutes for hours was not holding
a hand edit: commit `0afc68e` had written **108 carriage returns** into the *blob*
`app/routes/admin_sekolah.py`, a path `.gitattributes` promises is `eol=lf`. Every
checkout of that commit therefore smudges the blob down to LF, `git status` reads
the path as modified for ever, and `git merge --ff-only` refuses over it — so the
release can neither land nor be explained, while the site keeps answering `200`.

Two other layers now know about that state: the runner writes HEAD's own bytes in
verbatim and reads that one path without the filter, and the deploy-status page
says *which kind* of dirty it is holding. Neither is a gate: a box can be serving
code whose own commit it cannot reproduce, and until this check nothing in the app
that is actually answering had an opinion about it.

So the app asks at construction, and refuses on positive evidence:

* **the rule is the one the page already measures** (`dirty_kinds_state`) — a
  second implementation would be a second opinion, and the page and the refusal
  would eventually disagree about which state the box is in;
* **only a measured blob refuses.** A hand edit, an untracked file, a path whose
  attribute *asks* for CRLF, a directory that is not a checkout, a box with no git
  — none of them is evidence of this defect, and a site is not taken down because
  git happened to be busy. This is the one place this module is deliberately more
  forgiving than the armament checker, and the reason is that it runs on the path
  that serves students;
* **the journal can name the cause** — the marker the deploy greps for is the
  marker this prints, and the gate it quarantines under has a sentence on the
  page, so a refusal never reads as "app did not construct".
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.utils import checkout_integrity  # noqa: E402
from tests.unit.git_env import git_env  # noqa: E402

DEPLOY = ROOT / "deploy" / "scangrade-deploy.sh"
TEMPLATE = ROOT / "app" / "templates" / "super_admin" / "deploy_status.html"
GIT = shutil.which("git")

pytestmark = pytest.mark.skipif(GIT is None, reason="needs git to build a checkout")


def _git(repo: Path, *args: str, data: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@example.com",
         "-c", "user.name=t", *args],
        input=data, capture_output=True, check=False, env=git_env())


def _repo(tmp_path: Path) -> Path:
    """A checkout whose attributes normalise Python to LF — this project's own rule."""
    repo = tmp_path / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / ".gitattributes").write_text("*.py text eol=lf\n", encoding="utf-8")
    (repo / "app" / "clean.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], capture_output=True, check=False,
                   env=git_env())
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _wide_dirty(repo: Path, files: int) -> None:
    """Commit `files` paths and then modify every one of them.

    At the **real** `SCAN_LIMIT` rather than at a small `limit=`, because the question
    this class is about is whether a real working checkout can still be verified: the
    cap is what a laptop mid-feature reaches, and a fixture built from a smaller
    number would prove the waiver works on a tree the developer never has.
    """
    for i in range(files):
        (repo / "app" / f"wide_{i:03d}.py").write_text("w = 0\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "a wide tree")
    for i in range(files):
        (repo / "app" / f"wide_{i:03d}.py").write_text("w = 1\n", encoding="utf-8")


@pytest.fixture(scope="module")
def wide_repo(tmp_path_factory) -> Path:
    """One wide, dirty checkout for the whole module: measured, never written to.

    Built once because the fixture is 70 files and each read is two git calls per
    path — and because every test below is about the same tree, so a fresh one per
    test would be the same measurement paid three times.
    """
    repo = _repo(tmp_path_factory.mktemp("wide"))
    _wide_dirty(repo, checkout_integrity.SCAN_LIMIT + 6)
    return repo


def _unreproducible_path(repo: Path, rel: str = "app/cr.py") -> None:
    """Commit a blob that bypasses the filters, then leave the filtered file in place.

    The commit holds bytes the attribute would never produce, so the worktree git
    checks out of it can never compare equal — the exact shape `0afc68e` was in.
    """
    blob = b"x = 1\r\n\r\ny = 2\r\n"
    written = _git(repo, "hash-object", "-w", "--no-filters", "--stdin", data=blob)
    sha = written.stdout.decode().strip()
    assert written.returncode == 0 and sha, written.stderr
    _git(repo, "update-index", "--add", "--cacheinfo", f"100644,{sha},{rel}")
    _git(repo, "commit", "-q", "-m", "a blob the filters cannot reproduce")
    (repo / rel).write_text("x = 1\n\ny = 2\n", encoding="utf-8")


# ── 1. what is not this question ─────────────────────────────────────────────

class TestWhatIsNotThisQuestion:
    def test_a_clean_checkout_has_nothing_to_say(self, tmp_path):
        assert checkout_integrity.unreproducible_reason(_repo(tmp_path)) is None

    def test_a_hand_edit_is_not_a_blob(self, tmp_path):
        """The box's edit is replaceable, and the runner sets it aside as a patch."""
        repo = _repo(tmp_path)
        (repo / "app" / "clean.py").write_text("x = 2\n", encoding="utf-8")
        assert checkout_integrity.unreproducible_reason(repo) is None

    def test_an_untracked_file_is_not_a_blob(self, tmp_path):
        repo = _repo(tmp_path)
        (repo / "app" / "fresh.py").write_text("brand = new\n", encoding="utf-8")
        assert checkout_integrity.unreproducible_reason(repo) is None

    def test_a_directory_that_is_not_a_checkout_is_not_a_question(self, tmp_path):
        """There is no commit to reproduce *from*, so nothing can be unreproducible."""
        assert checkout_integrity.unreproducible_reason(tmp_path / "nowhere") is None

    def test_untracked_files_do_not_spend_the_scan_budget(self, tmp_path):
        """A scratch file has no blob to disagree with, so it is not measured.

        `git status --untracked-files=no` is what keeps a developer's scratch from
        pushing a real path out of the measured window and making a checkout this
        answer for look like one it cannot.
        """
        repo = _repo(tmp_path)
        for i in range(5):
            (repo / "app" / f"scratch_{i}.py").write_text("s = 1\n", encoding="utf-8")
        assert checkout_integrity.unreproducible_reason(repo, limit=2) is None

    def test_a_path_whose_attribute_asks_for_crlf_is_fine(self, tmp_path):
        """`crlf` is what some attributes *ask* for; only the promise broken is a defect."""
        repo = _repo(tmp_path)
        (repo / ".gitattributes").write_text("*.py text eol=lf\n*.md text eol=crlf\n",
                                             encoding="utf-8")
        (repo / "notes.md").write_text("hello\r\nworld\r\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "a crlf document, as its attribute asks")
        assert checkout_integrity.unreproducible_reason(repo) is None


# ── 2. the blob, and the proof that it is one ────────────────────────────────

class TestAnUnreproducibleBlob:
    def test_it_names_the_path(self, tmp_path):
        repo = _repo(tmp_path)
        _unreproducible_path(repo)
        reason = checkout_integrity.unreproducible_reason(repo)
        assert reason and "app/cr.py" in reason, (
            "a refusal that does not name the file sends the reader to the journal")

    def test_a_checkout_cannot_clear_it(self, tmp_path):
        """The fixture really is the defect: the ordinary remedy does nothing here."""
        repo = _repo(tmp_path)
        _unreproducible_path(repo)
        assert _git(repo, "checkout", "HEAD", "--", "app/cr.py").returncode == 0
        _, out = checkout_integrity._modified_paths(GIT, repo)
        assert out == ["app/cr.py"], (
            "the fixture is not reproducing the defect: a restore cleared it")

    def test_it_says_what_to_do(self, tmp_path):
        repo = _repo(tmp_path)
        _unreproducible_path(repo)
        reason = checkout_integrity.unreproducible_reason(repo)
        assert "filter" in reason and ("verbatim" in reason or "scangrade-recover" in reason), (
            "a refusal with no remedy is a dead end — and the remedy is not a restore")

    def test_only_the_blob_is_named_when_both_are_present(self, tmp_path):
        repo = _repo(tmp_path)
        (repo / "app" / "clean.py").write_text("x = 2\n", encoding="utf-8")
        _unreproducible_path(repo)
        reason = checkout_integrity.unreproducible_reason(repo)
        assert "app/cr.py" in reason and "app/clean.py" not in reason, (
            "the hand edit is replaceable and must not be reported as the defect")

    def test_more_modified_paths_than_the_scan_refuses(self, tmp_path):
        """A cap that passed quietly would be "we could not tell" read as "it is fine".

        Each measured path is two git calls on a 1-vCPU box, so the scan is bounded
        — and a checkout with more uncommitted tracked files than the bound is not
        one a release merges over either. The cap says so rather than reporting the
        paths it happened to reach.
        """
        repo = _repo(tmp_path)
        for i in range(3):
            (repo / "app" / f"edit_{i}.py").write_text("v = 0\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "three files")
        for i in range(3):
            (repo / "app" / f"edit_{i}.py").write_text("v = 1\n", encoding="utf-8")
        reason = checkout_integrity.unreproducible_reason(repo, limit=2)
        assert reason and "more than 2" in reason, reason
        assert checkout_integrity.unreproducible_reason(repo, limit=3) is None, (
            "the cap refuses a tree it could not measure, not a tree it measured")

    def test_the_reading_is_the_page_s_own(self):
        """One rule, one place: the app must not grow a second classifier."""
        source = (ROOT / "app" / "utils" / "checkout_integrity.py").read_text(encoding="utf-8")
        assert "dirty_kinds_state" in source, (
            "the startup check re-implements the blob rule instead of reusing the "
            "one the deploy-status page already measures")


# ── 3. the cap: the one refusal a laptop may answer for itself ───────────────

class TestTheCapIsNotAMeasurement:
    """Two refusals, and only one of them is evidence. This is the other one.

    `SCAN_LIMIT` bounds the scan, so a checkout past it is one the app cannot *rule
    out* — and a checkout with more files in flight than the bound is exactly the
    laptop where four features are being verified, which is how the suite stopped
    being runnable in place and the workaround became a hand-typed linked worktree
    plus a `cp -f` of the files the feature touched (silently wrong whenever a new
    test file was left behind: the guard then never ran and the suite still said
    green). The waiver exists, and these are its three properties: it is asked for
    explicitly, it says when it was used, and it does not reach the measured refusal.
    """

    def test_the_cap_refuses_at_the_real_bound(self, wide_repo):
        reason = checkout_integrity.unreproducible_reason(wide_repo)
        assert reason and f"more than {checkout_integrity.SCAN_LIMIT}" in reason, (
            reason or "a tree past the scan bound was not refused")

    def test_the_refusal_names_the_way_out(self, wide_repo):
        """The reader who hits this is mid-feature: the remedy goes where the wall is."""
        reason = checkout_integrity.unreproducible_reason(wide_repo)
        assert checkout_integrity.ALLOW_DIRTY_ENV in reason, (
            "the cap refusal does not name the development permission it can be "
            "answered with, so the developer's next move is a worktree again")

    def test_the_allowance_waives_the_cap_and_says_so(self, wide_repo, capsys):
        assert checkout_integrity.unreproducible_reason(
            wide_repo, allow_unmeasured=True) is None
        err = capsys.readouterr().err
        assert checkout_integrity.ALLOWED_MARKER in err, (
            "a permitted run that says nothing is a green suite nobody can attribute")
        assert checkout_integrity.ALLOW_DIRTY_ENV in err
        assert checkout_integrity.MARKER not in err, (
            "the waiver printed the marker the deploy reads as a refusal — that "
            "quarantines a release for the opposite of the reason it happened")

    def test_a_measured_blob_is_never_waived(self, tmp_path):
        repo = _repo(tmp_path)
        _unreproducible_path(repo)
        reason = checkout_integrity.unreproducible_reason(repo, allow_unmeasured=True)
        assert reason and "app/cr.py" in reason, (
            "the allowance reached the one refusal that is a measurement")

    def test_the_measurement_wins_inside_a_truncated_tree(self, tmp_path):
        """Both refusals at once — the ordering is what keeps the waiver honest."""
        repo = _repo(tmp_path)
        _wide_dirty(repo, checkout_integrity.SCAN_LIMIT + 6)
        _unreproducible_path(repo)
        reason = checkout_integrity.unreproducible_reason(repo, allow_unmeasured=True)
        assert reason and "app/cr.py" in reason, (
            "a waived run reported a tree as answered while a blob in it was measured")


class TestTheAllowanceIsDevelopmentOnly:
    """Permission needs two independent conditions, and neither is decoration."""

    CONFIG = {"IS_PRODUCTION": False, "DEPLOY_PROBE": False}

    def _allow(self, words, config=None):
        environ = {}
        if words is not None:
            environ[checkout_integrity.ALLOW_DIRTY_ENV] = words
        return checkout_integrity.dev_allowance(
            self.CONFIG if config is None else config, environ=environ)

    def test_the_variable_is_what_asks(self):
        assert self._allow(None) is False, "the allowance defaulted to on"
        for word in ("1", "true", "TRUE", " yes ", "on"):
            assert self._allow(word) is True, f"{word!r} did not ask for it"

    def test_a_zero_is_not_a_yes(self):
        """A script that forwards its environment says no by exporting nothing/0."""
        for word in ("", " ", "0", "false", "no", "off", "2", "onward"):
            assert self._allow(word) is False, f"{word!r} was read as permission"

    def test_no_config_is_not_permission(self):
        """"I could not tell where I am" must answer no — the direction is the point."""
        environ = {checkout_integrity.ALLOW_DIRTY_ENV: "1"}
        assert checkout_integrity.dev_allowance(None, environ=environ) is False
        assert checkout_integrity.dev_allowance(object(), environ=environ) is False
        assert checkout_integrity.dev_allowance(lambda key: False, environ=environ) is False

    def test_a_box_that_serves_never_grants_it(self):
        assert self._allow("1", {"IS_PRODUCTION": True}) is False
        assert self._allow("1", {"IS_PRODUCTION": True, "DEPLOY_PROBE": False}) is False

    def test_the_probes_own_construction_never_grants_it(self):
        """The probe is the release's construction: a release is not a developer."""
        assert self._allow("1", {"IS_PRODUCTION": False, "DEPLOY_PROBE": True}) is False

    def test_the_production_flag_is_a_class_attribute_not_an_env_read(self):
        """A `.env` on the box must not be able to grant this — same as payments."""
        from app.config import Config, DevelopmentConfig, ProductionConfig, TestingConfig

        assert ProductionConfig.IS_PRODUCTION is True
        assert Config.IS_PRODUCTION is False
        assert DevelopmentConfig.IS_PRODUCTION is False
        assert TestingConfig.IS_PRODUCTION is False
        source = (ROOT / "app" / "config.py").read_text(encoding="utf-8")
        assert re.search(r"^    IS_PRODUCTION = False$", source, re.M), (
            "`Config.IS_PRODUCTION` is not the plain class attribute any more — an "
            "environment read here would let a shell profile on the box grant the "
            "allowance to whatever it deploys next")
        assert re.search(r"^    IS_PRODUCTION = True$", source, re.M)

    def test_the_app_asks_the_allowance_of_its_own_config(self):
        """The wiring, read where it lives: a dead permission is not a permission."""
        source = (ROOT / "app" / "__init__.py").read_text(encoding="utf-8")
        assert "dev_allowance(app.config)" in source, (
            "`create_app` does not ask the allowance, so the variable changes nothing "
            "and the suite still cannot run in a dirty checkout")
        at = source.index("unreproducible_reason(")
        assert "allow_unmeasured=" in source[at:at + 400]


class TestTheSuiteRunsInADirtyCheckout:
    """Executed, not read: a wide dirty tree constructs with the variable, and not without."""

    def test_a_wide_dirty_checkout_refuses_without_the_variable(self, wide_repo, monkeypatch):
        from tests.conftest import build_app

        monkeypatch.setattr(checkout_integrity, "REPO_ROOT", wide_repo)
        monkeypatch.delenv(checkout_integrity.ALLOW_DIRTY_ENV, raising=False)
        with pytest.raises(SystemExit):
            build_app("testing")

    def test_the_variable_lets_it_construct_in_place(self, wide_repo, monkeypatch):
        from tests.conftest import build_app

        monkeypatch.setattr(checkout_integrity, "REPO_ROOT", wide_repo)
        monkeypatch.setenv(checkout_integrity.ALLOW_DIRTY_ENV, "1")
        assert build_app("testing") is not None

    def test_the_same_variable_does_not_open_a_box_that_serves(self, wide_repo, monkeypatch):
        """The end-to-end half of the two-condition rule: production, dirty, refused."""
        from app.config import ProductionConfig

        from tests.conftest import build_app

        monkeypatch.setattr(checkout_integrity, "REPO_ROOT", wide_repo)
        monkeypatch.setenv(checkout_integrity.ALLOW_DIRTY_ENV, "1")
        # A constructible production config: the placeholder secret and the missing
        # credentials would otherwise refuse *before* the question this test asks.
        for name, value in (("SECRET_KEY", "a-real-looking-secret"),
                            ("SUPABASE_URL", "http://127.0.0.1:54321"),
                            ("SUPABASE_SERVICE_KEY", "service-key")):
            monkeypatch.setattr(ProductionConfig, name, value)
        with pytest.raises(SystemExit) as caught:
            build_app("app.config.ProductionConfig")
        assert caught.value.code == 1


# ── 4. the refusal ───────────────────────────────────────────────────────────

class TestTheRefusal:
    def test_it_refuses_to_serve_with_the_marker(self, tmp_path, monkeypatch, capsys):
        from tests.conftest import build_app

        repo = _repo(tmp_path)
        _unreproducible_path(repo)
        monkeypatch.setattr(checkout_integrity, "REPO_ROOT", repo)
        with pytest.raises(SystemExit) as caught:
            build_app("testing")
        assert caught.value.code == 1, "a refusal has to be a failure, not a warning"
        out = capsys.readouterr().out
        assert checkout_integrity.MARKER in out, (
            "without the marker the journal says 'app did not construct' and the "
            "next reader hunts for a Python fault that is not there")
        assert "app/cr.py" in out, "the refusal has to name the file it is about"

    def test_a_clean_checkout_serves(self, tmp_path, monkeypatch):
        from tests.conftest import build_app

        monkeypatch.setattr(checkout_integrity, "REPO_ROOT", _repo(tmp_path))
        assert build_app("testing") is not None

    def test_it_is_not_asked_only_of_the_probe(self, tmp_path, monkeypatch):
        """Unlike the armament question, this one is asked of the app about to serve.

        A release refused at the probe keeps the old commit up; the same state on
        the serving process is code nobody can reconcile with its own commit, and
        that is the state this exists to make loud.
        """
        from tests.conftest import build_app

        repo = _repo(tmp_path)
        _unreproducible_path(repo)
        monkeypatch.setattr(checkout_integrity, "REPO_ROOT", repo)
        from app.config import TestingConfig

        assert TestingConfig.DEPLOY_PROBE is False
        with pytest.raises(SystemExit):
            build_app("testing")


# ── 5. the journal and the page ──────────────────────────────────────────────

class TestTheJournalNamesIt:
    def test_the_deploy_recognises_the_marker(self):
        script = DEPLOY.read_text(encoding="utf-8")
        branch = _construct_branch(script)
        assert "quarantine_write" in branch, (
            "a refusal that is not quarantined is retried every two minutes")
        assert "FAIL_REASON=" in branch, "the branch names no gate"

    def test_the_gate_it_names_has_a_sentence_on_the_page(self):
        from app.services import deploy_status_service as status

        branch = _construct_branch(DEPLOY.read_text(encoding="utf-8"))
        reason = branch.split('FAIL_REASON="', 1)[1].split('"', 1)[0]
        key = status.gate_key(reason)
        assert key != status.GATE_UNKNOWN, f"'{reason}' lands on no gate key"
        assert key in status.GATE_KEYS
        assert f"q.gate_key == '{key}'" in TEMPLATE.read_text(encoding="utf-8"), (
            "the page would render the gate this refusal quarantines under as a blank")


# ── 6. the gate the suite runs through ──────────────────────────────────────

GATE = ROOT / "deploy" / "theme_gate.sh"


def _gate_constants() -> dict:
    """The two markers the gate matches, read off its own assignments."""
    names = ("CONSTRUCT_MARKER", "ALLOW_DIRTY_VAR")
    found = {}
    for line in GATE.read_text(encoding="utf-8").splitlines():
        for name in names:
            if line.startswith(f"{name}="):
                found[name] = line.split("=", 1)[1].strip().strip('"')
    assert set(found) == set(names), (
        "deploy/theme_gate.sh no longer names the app's refusal markers, so the "
        "block that reads them matches nothing — and a gate that matches nothing "
        "falls through to blaming the release for a checkout")
    return found


def _gate_refusal_block(*, code_only: bool = False) -> str:
    """The block between its delimiters, optionally with the prose taken out.

    `code_only` is what the "never promotes it" assertion reads: the block's own
    header explains that a development checkout may export the variable, so a
    sentence-level search for the word would fail on the explanation.
    """
    src = GATE.read_text(encoding="utf-8")
    at = src.index("# checkout_refusal:start")
    block = src[at:src.index("# checkout_refusal:end", at)]
    if not code_only:
        return block
    return "\n".join(ln for ln in block.splitlines()
                     if not ln.lstrip().startswith("#"))


def _run_refusal_block(tmp_path: Path, output: str) -> subprocess.CompletedProcess:
    """The shipped block, run against a captured pytest output.

    Executed rather than read, for the reason `tests/unit/test_gate_armament.py`
    gives about the deploy's rollback: the block's whole job is to pick one of two
    exit codes out of a wall of text, and no source assertion can tell which one it
    picks. The marker assignments are lifted from the gate itself, so the harness
    tests the strings the gate would match for real.
    """
    constants = _gate_constants()
    harness = tmp_path / "harness.sh"
    harness.write_text(
        "set -uo pipefail\n"
        f"CONSTRUCT_MARKER={constants['CONSTRUCT_MARKER']}\n"
        f"ALLOW_DIRTY_VAR={constants['ALLOW_DIRTY_VAR']}\n"
        'OUTPUT=$(cat "$1")\n'
        + _gate_refusal_block()
        + '\necho "FELL THROUGH"\nexit 0\n', encoding="utf-8")
    captured = tmp_path / "output.txt"
    captured.write_text(output, encoding="utf-8")
    return subprocess.run(["bash", str(harness), str(captured)], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def _as_pytest_output(reason: str) -> str:
    """A refusal as the gate really sees it: pytest's capture of the app's output.

    Shaped the way `create_app` prints it — the marker, the reason, and the
    sentence about serving — because that whole block is what pytest captures, and
    a fixture that dropped the marker line would be testing a gate that never sees
    a refusal at all.
    """
    return ("E           SystemExit: 1\n"
            "---------------------------- Captured stdout setup ------" + "-" * 22 + "\n"
            f"{checkout_integrity.MARKER}: refusing to serve a checkout its own "
            f"commit cannot reproduce.\n{reason}\n"
            "The code this process would serve is not the code of the commit it "
            "would report, so it does not serve at all.\n"
            "=========================== short test summary info ============================\n"
            "ERROR tests/unit/test_dark_theme_contrast.py::test_x\n"
            "226 errors in 845.09s (0:14:05)\n")


class TestTheGateDoesNotBlameTheRelease:
    """The suite this gate runs is settled by one app, so this refusal reaches it.

    Measured: in a checkout with more than `SCAN_LIMIT` modified tracked paths the
    gate answered **exit 1** — "this release would ship an invisible or unreadable
    element" — with 226 `error at setup` lines underneath and not one check having
    run. That is a false entry in the ledger in both directions: the release is
    blamed for a property of the checkout, and a run that measured nothing is
    recorded as a finding rather than as "could not be measured". The state is not
    exotic — it is what the laptop this was written on looks like mid-feature,
    which is why the workaround had been a temporary worktree.

    The three outputs below are the module's **own** refusal sentences, so a
    rewording that dropped the remedy (or the file name) fails here rather than
    silently changing what the gate says.
    """

    def test_the_two_markers_are_the_modules_own(self):
        constants = _gate_constants()
        assert constants["CONSTRUCT_MARKER"] == checkout_integrity.MARKER, (
            "the gate greps for a marker the app does not print, so every refusal "
            "falls through to the exit-1 branch")
        assert constants["ALLOW_DIRTY_VAR"] == checkout_integrity.ALLOW_DIRTY_ENV

    def test_the_cap_is_exit_two_and_says_the_release_was_not_the_problem(
            self, wide_repo, tmp_path):
        reason = checkout_integrity.unreproducible_reason(wide_repo)
        assert reason and checkout_integrity.ALLOW_DIRTY_ENV in reason
        result = _run_refusal_block(tmp_path, _as_pytest_output(reason))
        assert result.returncode == 2, (
            "a checkout the app will not construct in answered %d; exit 1 is "
            "'a real finding in the release' and is a sentence nobody can act on\n%s"
            % (result.returncode, result.stderr))
        assert "NOT CHECKED" in result.stderr
        assert "would ship an invisible" not in result.stderr, (
            "the gate still points the reader at the release\n" + result.stderr)

    def test_the_cap_names_the_remedy_as_a_command(self, wide_repo, tmp_path):
        """The refusal names the variable; the gate has to say how to use it."""
        result = _run_refusal_block(tmp_path, _as_pytest_output(
            checkout_integrity.unreproducible_reason(wide_repo)))
        assert f"{checkout_integrity.ALLOW_DIRTY_ENV}=1" in result.stderr, (
            "the gate knows which permission answers this and does not print it")
        assert "deploy/theme_gate.sh" in result.stderr, (
            "the remedy has to be the command that was refused, or the reader has "
            "to reconstruct it")

    def test_a_measured_blob_is_still_a_finding(self, tmp_path):
        """The other refusal is about the release, and it keeps exit 1.

        A path storing bytes no checkout of HEAD can reproduce is the defect this
        module exists for; it must not be absorbed by the case above just because
        the two share a marker.
        """
        repo = _repo(tmp_path)
        _unreproducible_path(repo)
        reason = checkout_integrity.unreproducible_reason(repo)
        assert reason and "app/cr.py" in reason
        result = _run_refusal_block(tmp_path, _as_pytest_output(reason))
        assert result.returncode == 1, (
            "a blob no checkout can reproduce was answered %d — it is a property "
            "of the release and has to be refused as one\n%s"
            % (result.returncode, result.stderr))
        assert "app/cr.py" in result.stderr, (
            "the refusal has to reach the reader with the file it names")
        assert checkout_integrity.ALLOW_DIRTY_ENV not in result.stderr, (
            "the blob case offered the development permission, which cannot clear "
            "a measured blob")

    def test_a_run_that_was_not_refused_falls_through(self, tmp_path):
        """A real failure — and a green run — must reach the gate's own verdict."""
        for output in ("8 passed in 12.4s\n",
                       "FAILED tests/unit/test_dark_theme_contrast.py::test_x - AssertionError\n"
                       "1 failed, 7 passed in 12.4s\n"):
            result = _run_refusal_block(tmp_path, output)
            assert result.returncode == 0 and "FELL THROUGH" in result.stdout, (
                "the block decided a run that is not this refusal\n" + output)

    def test_the_gate_never_promotes_the_permission_itself(self):
        """Explicit means the checkout asks, not the gate.

        A gate that exported it would waive the cap for every run it makes —
        including the deploy's, where the module refuses the permission on purpose.
        """
        src = GATE.read_text(encoding="utf-8")
        assert "export" not in _gate_refusal_block(code_only=True), (
            "the refusal block exports something, so the refusal is no longer "
            "something a checkout asks for")
        for forbidden in (f"export {checkout_integrity.ALLOW_DIRTY_ENV}",
                          f"env {checkout_integrity.ALLOW_DIRTY_ENV}"):
            assert forbidden not in src, (
                f"the gate sets {forbidden} for the run it judges")

    def test_the_block_runs_before_the_other_reports(self):
        """A refused run must not print a coverage table and a dark-mode lecture."""
        src = GATE.read_text(encoding="utf-8")
        assert src.index("# checkout_refusal:start") < src.index("COVERAGE=$("), (
            "the refusal is decided after the tools have reported, so the reader "
            "gets a page of numbers about a run that measured nothing")
        assert src.index("# checkout_refusal:start") < src.index(
            "theme gate: FAILED — this release would ship an invisible"), (
            "the refusal is printed after the release-blaming help text, which is "
            "the false accusation this block exists to prevent")


# ── 7. the deploy's own reading of the marker ───────────────────────────────


def _construct_branch(script: str) -> str:
    """The part of the construct refusal that answers *this* marker, to `exit 9`.

    Cut by the `exit 9` *line* rather than the words: the branch beside this one
    names its own code in a sentence (`app did not construct (exit 9)`), so a
    substring search walks off the end of the block and lands on the wrong half.
    """
    assert f"grep -q '{checkout_integrity.MARKER}'" in script, (
        "the deploy no longer recognises the app's refusal, so it reports a "
        "Python construct fault instead of an unreproducible checkout")
    at = script.index(f"grep -q '{checkout_integrity.MARKER}'")
    end = re.search(r"^[ \t]*exit 9", script[at:], re.M)
    assert end, "the construct refusal no longer exits 9"
    return script[at:at + end.start()]


if __name__ == "__main__":                                   # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
