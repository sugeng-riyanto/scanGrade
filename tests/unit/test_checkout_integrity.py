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


# ── 3. the refusal ───────────────────────────────────────────────────────────

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


# ── 4. the journal and the page ──────────────────────────────────────────────

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
