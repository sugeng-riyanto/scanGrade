"""A runner that cannot deploy has no symptom, so the page has to show it.

`/super-admin/deploy-status` exists because the one state that matters — an
installed *copy* of the deploy runner that has drifted from the checkout — makes
Gate 0 refuse every release with exit 14 while the site keeps serving happily.
Nothing on any page changes, no error is logged where anyone looks, and the only
way to notice was to SSH in.

So the checks here are about three things:

1. **The readings are right**, including the ones that are easy to get backwards:
   a copy that still matches the checkout *does* deploy today, and a launcher that
   differs from what this commit renders is not yet broken but is not current.
2. **The page and Gate 0 cannot disagree.** The strongest test in this file runs
   the real `runner-identity` block from `deploy/scangrade-deploy.sh` over the same
   two files the service judges, and asserts the exit codes line up: identical copy
   → 0, drifted copy → 14.
3. **Nothing is invented.** A part that cannot be measured reports a reason key and
   no number, a zero is never used for "unknown", and every key the service can emit
   has a sentence in the template — in both languages, since the copy lives there
   rather than in Python where the toggle cannot reach it.
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app import create_app  # noqa: E402
from app.services import deploy_status_service as status  # noqa: E402

RUNNER = ROOT / "deploy" / "scangrade-deploy.sh"
ENTRYPOINT = ROOT / "deploy" / "entrypoint.sh"
TEMPLATE = ROOT / "app" / "templates" / "super_admin" / "deploy_status.html"
BASH = shutil.which("bash")
GIT = shutil.which("git")

needs_bash = pytest.mark.skipif(BASH is None, reason="needs bash to run Gate 0")
needs_git = pytest.mark.skipif(GIT is None, reason="needs git to read a checkout")

IDENTITY_START = "# runner-identity:start"
IDENTITY_END = "# runner-identity:end"


# ── fixtures ─────────────────────────────────────────────────────────────────

def _render(text: str, repo: Path) -> str:
    return text.replace(status.PLACEHOLDER, str(repo))


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def checkout(tmp_path: Path, *, commit: bool = True) -> Path:
    """A real git checkout holding the real deploy files.

    Real git rather than a fake: the whole point of the checkout half is that the
    numbers come out of `rev-parse` / `rev-list` / `status`, so a mocked git would
    be testing the mock.
    """
    repo = tmp_path / "checkout"
    (repo / "deploy").mkdir(parents=True)
    for name in ("entrypoint.sh", "scangrade-deploy.sh", "scangrade-db-snapshot.sh"):
        shutil.copyfile(ROOT / "deploy" / name, repo / "deploy" / name)
    (repo / "app.py").write_text("print('hi')\n", encoding="utf-8")
    if commit and GIT:
        _git("init", "-q", "-b", "main", cwd=repo)
        _git("-c", "user.email=t@t", "-c", "user.name=t", "add", "-A", cwd=repo)
        _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "first",
             cwd=repo)
    return repo


def install(tmp_path: Path, name: str, text: str) -> Path:
    """Write text where a runner is expected. Note this is *not* byte-faithful on
    Windows (`write_text` turns `\n` into `\r\n`); use `install_from` when the test
    means "the same file"."""
    path = tmp_path / "bin" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def install_from(tmp_path: Path, name: str, source: Path) -> Path:
    """A byte-for-byte copy — what `cmp -s` and Gate 0 are talking about."""
    path = tmp_path / "bin" / name
    path.parent.mkdir(exist_ok=True)
    shutil.copyfile(source, path)
    return path


def launcher_for(repo: Path) -> str:
    return _render(ENTRYPOINT.read_text(encoding="utf-8"), repo)


def strip_gate0(text: str) -> str:
    """The same script as it was *before* Gate 0 existed."""
    return text.split(IDENTITY_START, 1)[0] + text.split(IDENTITY_END, 1)[1]


def checkout_whose_runner_predates_gate_0(tmp_path: Path) -> tuple[Path, str]:
    """A checkout one commit *after* an installed copy that predates Gate 0.

    This is the shape production was in on 2026-09-17, and the reason this file
    grew a second failure mode: the runner was the revision from before the
    identity check existed, so it had no check in it to refuse anything while
    releases went out all week with the deploy logic of 45 commits ago. The page
    said "Gate 0 refuses it, so no release can deploy" — wrong, and wrong in the
    direction that hides the problem.
    """
    repo = tmp_path / "checkout"
    (repo / "deploy").mkdir(parents=True)
    old_text = strip_gate0((ROOT / "deploy/scangrade-deploy.sh").read_text(encoding="utf-8"))
    assert IDENTITY_START not in old_text
    (repo / "deploy/scangrade-deploy.sh").write_bytes(old_text.encode("utf-8"))
    for name in ("entrypoint.sh", "scangrade-db-snapshot.sh"):
        shutil.copyfile(ROOT / "deploy" / name, repo / "deploy" / name)
    _git("init", "-q", "-b", "main", cwd=repo)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "add", "-A", cwd=repo)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm",
         "before gate 0", cwd=repo)
    old_short = _git("rev-parse", "--short", "HEAD", cwd=repo).stdout.strip()
    shutil.copyfile(ROOT / "deploy/scangrade-deploy.sh",
                    repo / "deploy/scangrade-deploy.sh")
    _git("-c", "user.email=t@t", "-c", "user.name=t", "add", "-A", cwd=repo)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm",
         "gate 0", cwd=repo)
    return repo, old_short


def ahead_of_head(repo: Path) -> str:
    """Put one commit on `origin/main` that HEAD does not have, and return its sha."""
    _git("checkout", "-q", "-b", "upstream", cwd=repo)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty",
         "-m", "second", cwd=repo)
    sha = _git("rev-parse", "HEAD", cwd=repo).stdout.strip()
    _git("checkout", "-q", "main", cwd=repo)
    _git("update-ref", "refs/remotes/origin/main", sha, cwd=repo)
    return sha


def state_of(tmp_path: Path, repo: Path, name: str, text: str) -> dict:
    return status.runner_state(install(tmp_path, name, text), repo,
                               expect=_render((repo / "deploy/entrypoint.sh")
                                              .read_text(encoding="utf-8"), repo))


# ── the installed runner ─────────────────────────────────────────────────────

class TestWhatIsInstalled:
    def test_a_rendered_launcher_is_the_arrangement(self, tmp_path):
        repo = checkout(tmp_path)
        state = state_of(tmp_path, repo, "scangrade-deploy", launcher_for(repo))
        assert state["kind"] == "launcher"
        assert state["gate0"] == "passes"
        assert state["matches_this_commit"] is True
        assert state["reason_key"] is None
        assert state["rendered_repo"] == str(repo)
        assert status.PLACEHOLDER not in launcher_for(repo), "the fixture must be rendered"

    def test_a_launcher_rendered_from_an_older_entrypoint_is_stale(self, tmp_path):
        """It still runs the checkout, so nothing is broken — but it is not what
        this commit renders, and whatever changed is not in effect until the
        installer runs once more."""
        repo = checkout(tmp_path)
        repo.joinpath("deploy/entrypoint.sh").write_text(
            (ROOT / "deploy/entrypoint.sh").read_text(encoding="utf-8")
            + "\n# a fix landed after the install\n", encoding="utf-8")
        state = state_of(tmp_path, repo, "scangrade-deploy", launcher_for(repo))
        assert state["kind"] == "launcher"
        assert state["gate0"] == "passes", "Gate 0 never compares the launcher"
        assert state["matches_this_commit"] is False
        assert state["reason_key"] == "launcher_stale"

    def test_a_launcher_for_another_checkout_is_reported(self, tmp_path):
        repo = checkout(tmp_path)
        elsewhere = tmp_path / "somewhere-else"
        state = state_of(tmp_path, repo, "scangrade-deploy", launcher_for(elsewhere))
        assert state["kind"] == "launcher"
        assert state["reason_key"] == "other_path"
        assert state["detail"] == str(elsewhere)

    def test_an_unrendered_file_is_reported(self, tmp_path):
        repo = checkout(tmp_path)
        state = state_of(tmp_path, repo, "scangrade-deploy",
                         ENTRYPOINT.read_text(encoding="utf-8"))
        assert state["kind"] == "copy"
        assert state["reason_key"] == "unrendered"

    def test_a_launcher_with_no_repo_line_is_reported(self, tmp_path):
        """A launcher-shaped file whose REPO= was stripped cannot find anything."""
        repo = checkout(tmp_path)
        text = re.sub(r'^REPO=.*$', "", launcher_for(repo), flags=re.M)
        state = state_of(tmp_path, repo, "scangrade-deploy", text)
        assert state["kind"] == "launcher"
        assert state["reason_key"] == "no_repo_line"

    def test_a_copy_that_still_matches_works_today(self, tmp_path):
        """The old arrangement, installed before the launcher existed. Gate 0 lets
        it through, so it is a warning and not a breakage — a box that installed an
        older version must not stop deploying on the first tick. The copy is made
        byte-for-byte because that is what `cmp -s` compares."""
        repo = checkout(tmp_path)
        copy = install_from(tmp_path, "scangrade-deploy",
                            repo / "deploy/scangrade-deploy.sh")
        state = status.runner_state(copy, repo)
        assert state["kind"] == "copy"
        assert state["differs_from_checkout"] is False
        assert state["gate0"] == "passes (the copy still matches)"

    def test_a_copy_that_has_drifted_is_refused(self, tmp_path):
        repo = checkout(tmp_path)
        state = state_of(tmp_path, repo, "scangrade-deploy",
                         (repo / "deploy/scangrade-deploy.sh")
                         .read_text(encoding="utf-8") + "\n# drift\n")
        assert state["kind"] == "copy"
        assert state["differs_from_checkout"] is True
        assert state["gate0"] == "refuses (exit 14)"
        assert state["reason_key"] == "drifted"

    def test_the_snapshot_launcher_is_compared_against_its_own_file(self, tmp_path):
        """`scangrade-db-snapshot` was the one that was silently broken as a copy;
        it has to be judged against scangrade-db-snapshot.sh, not the deploy."""
        repo = checkout(tmp_path)
        snapshot = install_from(tmp_path, "scangrade-db-snapshot",
                                repo / "deploy/scangrade-db-snapshot.sh")
        state = status.runner_state(snapshot, repo,
                                    expect=launcher_for(repo),
                                    copy_of="scangrade-db-snapshot.sh")
        assert state["kind"] == "copy"
        assert state["gate0"] == "passes (the copy still matches)"

    def test_a_stale_copy_is_named_and_counted(self, tmp_path):
        """"A copy" is not an answer to "which fixes are missing" — the commit and
        the distance are, and they are what the box could not tell anyone before."""
        repo, _ = checkout_whose_runner_predates_gate_0(tmp_path)
        # The blob itself, not git's text-mode stdout: the point of this reading is
        # the bytes, so the fixture has to hand the reader the same ones.
        blob = subprocess.run(
            ["git", "cat-file", "blob", "HEAD~1:deploy/scangrade-deploy.sh"],
            cwd=repo, capture_output=True).stdout
        assert blob
        source = tmp_path / "installed-snapshot"
        source.write_bytes(blob)
        copy = install_from(tmp_path, "scangrade-deploy", source)

        state = status.runner_state(copy, repo, expect=launcher_for(repo))

        assert state["kind"] == "copy"
        assert state["has_identity_check"] is False
        assert state["gate0"] == status.GATE0_CANNOT, (
            "this copy has no Gate 0 in it, so it cannot refuse anything")
        assert state["origin_key"] == status.ORIGIN_NAMED
        assert state["origin_short"] == _git("rev-parse", "--short", "HEAD~1",
                                            cwd=repo).stdout.strip()
        assert state["origin_subject"] == "before gate 0"
        assert state["origin_behind"] == 1
        assert state["origin_stale_files"] == 1, "one file under deploy/ moved on"

    def test_a_copy_of_this_commit_says_so_rather_than_naming_history(self, tmp_path):
        repo = checkout(tmp_path)
        copy = install_from(tmp_path, "scangrade-deploy",
                            repo / "deploy/scangrade-deploy.sh")
        state = status.runner_state(copy, repo)
        assert state["origin_key"] == status.ORIGIN_CURRENT
        assert state["origin_behind"] == 0
        assert state["origin_stale_files"] == 0

    def test_bytes_no_commit_ever_held_are_reported_as_unmatched(self, tmp_path):
        """A hand-edited install is not a commit, and the page must not invent one.
        The distance stays unknown rather than becoming a zero."""
        repo = checkout(tmp_path)
        state = state_of(tmp_path, repo, "scangrade-deploy",
                         (repo / "deploy/scangrade-deploy.sh").read_text(encoding="utf-8")
                         + "\n# edited on the box by hand\n")
        assert state["origin_key"] == status.ORIGIN_UNMATCHED
        assert state["origin_commit"] is None
        assert state["origin_behind"] is None, "unknown is not zero"

    def test_a_stale_launcher_is_named_and_counted(self, tmp_path):
        """The launcher half of the same question: which commit rendered it."""
        repo = checkout(tmp_path)
        (repo / "deploy/entrypoint.sh").write_text(
            (ROOT / "deploy/entrypoint.sh").read_text(encoding="utf-8")
            + "\n# a fix landed after the install\n", encoding="utf-8")
        _git("-c", "user.email=t@t", "-c", "user.name=t", "add", "-A", cwd=repo)
        _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm",
             "entrypoint grows", cwd=repo)
        old_short = _git("rev-parse", "--short", "HEAD~1", cwd=repo).stdout.strip()
        old_render = _render(
            _git("show", "HEAD~1:deploy/entrypoint.sh", cwd=repo).stdout, repo)
        launcher = install(tmp_path, "scangrade-deploy", old_render)

        state = status.runner_state(launcher, repo, expect=launcher_for(repo))

        assert state["kind"] == "launcher"
        assert state["gate0"] == status.GATE0_PASSES, "Gate 0 never judges a launcher"
        assert state["origin_key"] == status.ORIGIN_NAMED
        assert state["origin_short"] == old_short
        assert state["origin_behind"] == 1

    def test_nothing_installed_is_unknown_not_zero(self, tmp_path):
        repo = checkout(tmp_path)
        state = status.runner_state(tmp_path / "bin" / "nothing", repo)
        assert state["kind"] == status.UNKNOWN
        assert state["reason_key"] == "absent"
        assert state["exists"] is False
        assert state["sha256"] is None and state["gate0"] == status.UNKNOWN

    def test_an_unreadable_path_is_reported_rather_than_guessed(self, tmp_path):
        repo = checkout(tmp_path)
        path = tmp_path / "bin" / "a-directory"
        path.mkdir(parents=True)
        state = status.runner_state(path, repo)
        assert state["kind"] == status.UNKNOWN
        assert state["reason_key"] == "unreadable"
        assert state["detail"], "an unreadable file has to say what went wrong"

    def test_no_english_prose_is_built_here(self):
        """The sentence a reader sees is the template's job — a reason spelled out
        in Python is copy the toggle cannot reach and the i18n sweep cannot see."""
        source = Path(status.__file__).read_text(encoding="utf-8")
        for phrase in ("is missing", "nothing is installed", "so it was installed",
                       "not this checkout", "no release can deploy"):
            assert phrase not in source, f"prose belongs in the template: {phrase!r}"


# ── the checkout ─────────────────────────────────────────────────────────────

class TestWhereTheCheckoutIs:
    @needs_git
    def test_behind_is_counted_against_the_remote_ref(self, tmp_path):
        repo = checkout(tmp_path)
        status.checkout_state(repo, now=status._dt.datetime.now(status._dt.timezone.utc))
        ahead_of_head(repo)
        state = status.checkout_state(
            repo, now=status._dt.datetime.now(status._dt.timezone.utc))
        assert state["available"] is True
        assert state["behind"] == 1, "one commit on origin/main that HEAD does not have"
        assert state["ahead"] == 0
        assert state["branch"] == "main"
        assert state["head"] and state["origin"]
        assert state["head_subject"] == "first"

    @needs_git
    def test_up_to_date_reads_as_zero_behind(self, tmp_path):
        repo = checkout(tmp_path)
        head = _git("rev-parse", "HEAD", cwd=repo).stdout.strip()
        _git("update-ref", "refs/remotes/origin/main", head, cwd=repo)
        state = status.checkout_state(
            repo, now=status._dt.datetime.now(status._dt.timezone.utc))
        assert state["behind"] == 0 and state["ahead"] == 0

    @needs_git
    def test_the_reading_carries_its_own_age(self, tmp_path):
        """The number is only as good as the ref it came from, so the age travels
        with it — a stale figure must not read as a live one."""
        repo = checkout(tmp_path)
        head = _git("rev-parse", "HEAD", cwd=repo).stdout.strip()
        _git("update-ref", "refs/remotes/origin/main", head, cwd=repo)
        now = status._dt.datetime.now(status._dt.timezone.utc)
        state = status.checkout_state(repo, now=now)
        assert state["origin_updated_at"], (
            "a packed or loose ref has to yield a timestamp, or the figure has no age")
        assert state["origin_age_seconds"] is not None
        assert state["origin_age_seconds"] >= 0

    @needs_git
    def test_a_dirty_checkout_is_counted(self, tmp_path):
        repo = checkout(tmp_path)
        (repo / "scribble.txt").write_text("uncommitted\n", encoding="utf-8")
        state = status.checkout_state(
            repo, now=status._dt.datetime.now(status._dt.timezone.utc))
        assert state["dirty"] == 1

    @needs_git
    def test_reading_the_checkout_does_not_change_it(self, tmp_path):
        """A status page must not be a writer — not even the index. `git status`
        would refresh and rewrite `.git/index` without `--no-optional-locks`, on
        the checkout the deploy is about to use.

        The measurement is taken around the reader alone: a `git status` in the
        test itself legitimately refreshes the index, which is how the first
        version of this test failed on its own setup rather than on the reader.
        """
        repo = checkout(tmp_path)
        index = repo / ".git" / "index"
        now = status._dt.datetime.now(status._dt.timezone.utc)
        status.checkout_state(repo, now=now)          # settle the index first
        before = index.stat().st_mtime_ns
        status.checkout_state(repo, now=now)
        after = index.stat().st_mtime_ns
        assert after == before, "the reader touched .git/index"

        # An allowlist rather than a blocklist, so a *new* call site has to be
        # argued for here instead of quietly joining the reader.
        source = Path(status.__file__).read_text(encoding="utf-8")
        assert source.count("--no-optional-locks") >= 2, "every git call must carry it"
        calls = set(re.findall(
            r'_git_(?:out|raw)\(git, repo,\s*"([a-z-]+)"', source))
        assert calls, "no git call sites found — did the reader stop reading?"
        assert calls <= {"rev-parse", "rev-list", "log", "status", "for-each-ref",
                         "diff", "cat-file"}, (
            f"these read nothing: {sorted(calls)}")

    def test_a_path_that_is_not_a_checkout_says_so(self, tmp_path):
        not_a_repo = tmp_path / "plain"
        not_a_repo.mkdir()
        state = status.checkout_state(
            not_a_repo, now=status._dt.datetime.now(status._dt.timezone.utc))
        assert state["available"] is False
        assert state["reason_key"] == "not_a_checkout"
        assert state["behind"] is None, "unknown is not zero"

    @needs_git
    def test_a_detached_head_is_reported_as_such(self, tmp_path):
        repo = checkout(tmp_path)
        _git("checkout", "-q", "--detach", cwd=repo)
        state = status.checkout_state(
            repo, now=status._dt.datetime.now(status._dt.timezone.utc))
        assert state["detached"] is True and state["branch"] is None


# ── the verdict ──────────────────────────────────────────────────────────────

class TestWhatItAddsUpTo:
    def _report(self, tmp_path, *, installed=None, dirty=False, ahead=False,
                paused=False):
        repo = checkout(tmp_path)
        if dirty:
            (repo / "scribble.txt").write_text("x\n", encoding="utf-8")
        if ahead:
            ahead_of_head(repo)
        text = launcher_for(repo) if installed is None else installed
        runner = install(tmp_path, "scangrade-deploy", text)
        pause_file = tmp_path / "pause-flag"
        if paused:
            pause_file.write_text("", encoding="utf-8")
        return status.report(repo=repo, runner=runner,
                             snapshot_runner=install(tmp_path, "snap", launcher_for(repo)),
                             pause_file=pause_file)

    @needs_git
    def test_fresh_when_everything_agrees(self, tmp_path):
        report = self._report(tmp_path)
        assert report["verdict"]["level"] == status.FRESH
        assert report["verdict"]["key"] == "fresh"

    @needs_git
    def test_a_drifted_copy_outranks_commits_behind(self, tmp_path):
        """Nothing deploys at all in this state, however much is waiting, so it is
        the headline rather than a footnote."""
        report = self._report(
            tmp_path, ahead=True,
            installed=(ROOT / "deploy/scangrade-deploy.sh").read_text(encoding="utf-8"))
        verdict = report["verdict"]
        assert verdict["level"] == status.BROKEN
        assert verdict["key"] == "copy_drifted"
        assert verdict["behind"] == 1, "the waiting work is still reported"

    @needs_git
    def test_behind_is_the_headline_when_the_runner_is_right(self, tmp_path):
        report = self._report(tmp_path, ahead=True)
        assert report["verdict"]["level"] == status.WARN
        assert report["verdict"]["key"] == "behind"
        assert report["verdict"]["detail"] == "1"

    @needs_git
    def test_a_dirty_checkout_is_reported_as_its_own_reason(self, tmp_path):
        report = self._report(tmp_path, dirty=True)
        assert report["verdict"]["key"] == "dirty", (
            "the deploy refuses a dirty checkout, so 'nothing deployed' has to be "
            "answerable from the page")

    @needs_git
    def test_a_paused_box_is_a_frozen_schedule_not_a_broken_runner(self, tmp_path):
        report = self._report(tmp_path, paused=True)
        assert report["verdict"]["key"] == "paused"
        assert report["paused"] is True
        assert report["runner"]["gate0"] == "passes", "the runner is still fine"

    @needs_git
    def test_the_verdict_reports_absence_rather_than_a_pass(self, tmp_path):
        repo = checkout(tmp_path)
        report = status.report(
            repo=repo, runner=tmp_path / "bin" / "never-installed",
            snapshot_runner=tmp_path / "bin" / "never-installed-either",
            pause_file=tmp_path / "no-pause-flag")
        assert report["verdict"]["level"] == status.UNKNOWN
        assert report["verdict"]["key"] == "absent"
        assert report["runner"]["gate0"] == status.UNKNOWN, "unknown is not a pass"

    @needs_git
    def test_an_empty_installed_file_is_broken_not_a_pass(self, tmp_path):
        """It carries no Gate 0 either, so nothing refuses it — and it is still not
        a pass. The important half is "not fresh": an unknown is never a pass."""
        report = self._report(tmp_path, installed="")
        verdict = report["verdict"]
        assert verdict["level"] == status.BROKEN
        assert verdict["key"] == "copy_predates_gate"

    @needs_git
    def test_a_copy_without_gate_0_is_broken_for_the_other_reason(self, tmp_path):
        """Production's shape, and the defect this page shipped with: nothing
        refuses an old copy, so releases keep going out with the deploy logic of
        the commit it came from. The page said the opposite."""
        repo, _ = checkout_whose_runner_predates_gate_0(tmp_path)
        source = tmp_path / "installed-snapshot"
        source.write_bytes(subprocess.run(
            ["git", "cat-file", "blob", "HEAD~1:deploy/scangrade-deploy.sh"],
            cwd=repo, capture_output=True).stdout)
        report = status.report(
            repo=repo, runner=install_from(tmp_path, "scangrade-deploy", source),
            snapshot_runner=install(tmp_path, "snap", launcher_for(repo)),
            pause_file=tmp_path / "no-pause-flag")
        verdict = report["verdict"]
        assert verdict["level"] == status.BROKEN
        assert verdict["key"] == "copy_predates_gate"
        assert verdict["runner_behind"] == 1, "the distance travels with the verdict"
        assert report["runner"]["has_identity_check"] is False
        assert report["runner"]["origin_short"]

    @needs_git
    def test_the_two_copy_failures_are_never_collapsed_into_one(self, tmp_path):
        """One copy is refused; the other is not refused and cannot be. Reporting
        the second as the first is how a box went a week with no gates and a page
        that said nothing could deploy."""
        plain = {"available": True, "reason_key": None, "detail": None,
                 "behind": 0, "dirty": 0}
        refused = status.verdict(
            {"kind": "copy", "gate0": status.GATE0_REFUSES, "detail": "deploy/x",
             "reason_key": "drifted", "origin_behind": 1}, plain, paused=False)
        unnoticed = status.verdict(
            {"kind": "copy", "gate0": status.GATE0_CANNOT, "detail": "deploy/x",
             "reason_key": "drifted", "origin_behind": 45}, plain, paused=False)
        assert refused["key"] == "copy_drifted"
        assert unnoticed["key"] == "copy_predates_gate"
        assert refused["level"] == unnoticed["level"] == status.BROKEN, (
            "both are failures; they differ in what is stopping the releases")


# ── the page and Gate 0 cannot disagree ──────────────────────────────────────

@needs_bash
class TestThePageAndGate0Agree:
    """The page is a second opinion on the same comparison Gate 0 makes.

    Two readers of one fact drift apart silently, so this drives the *real*
    `runner-identity` block out of the deploy script over the same files the
    service judges, and asserts the exit code and the page's verdict line up.
    """

    def _gate0(self, repo: Path, running: Path) -> subprocess.CompletedProcess:
        script = RUNNER.read_text(encoding="utf-8")
        block = script.split(IDENTITY_START, 1)[1].split(IDENTITY_END, 1)[0]
        harness = (f'set -uo pipefail\nREPO="{repo}"\nlog() {{ echo "$*"; }}\n'
                   f"{block}\necho REACHED_END\n")
        return subprocess.run([BASH, "-c", harness, str(running)],
                              capture_output=True, text=True)

    def test_an_identical_copy_passes_both_readers(self, tmp_path):
        repo = checkout(tmp_path)
        copy = install_from(tmp_path, "scangrade-deploy",
                            repo / "deploy/scangrade-deploy.sh")

        gate = self._gate0(repo, copy)
        state = status.runner_state(copy, repo)

        assert gate.returncode == 0 and "REACHED_END" in gate.stdout, gate
        assert state["gate0"] == "passes (the copy still matches)"

    def test_a_drifted_copy_is_refused_by_both(self, tmp_path):
        repo = checkout(tmp_path)
        copy = install_from(tmp_path, "scangrade-deploy",
                            repo / "deploy/scangrade-deploy.sh")
        with copy.open("ab") as handle:
            handle.write(b"\n# drift\n")

        gate = self._gate0(repo, copy)
        state = status.runner_state(copy, repo)

        assert gate.returncode == 14, gate
        assert "install-auto-deploy.sh" in gate.stdout
        assert state["gate0"] == "refuses (exit 14)"
        assert status.verdict(state, {"available": True, "behind": 0, "dirty": 0},
                              paused=False)["level"] == status.BROKEN

    def test_a_line_ending_difference_is_a_difference(self, tmp_path):
        """`cmp -s` does not normalise, so neither may the page.

        A copy whose text reads the same character-for-character but whose bytes
        differ is refused by Gate 0. Comparing as text called that a match — two
        answers to one question — which is what this pins, in the direction the
        gate actually decides.
        """
        repo = checkout(tmp_path)
        copy = install_from(tmp_path, "scangrade-deploy",
                            repo / "deploy/scangrade-deploy.sh")
        text = copy.read_text(encoding="utf-8")
        copy.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))

        gate = self._gate0(repo, copy)
        state = status.runner_state(copy, repo)

        assert gate.returncode == 14, gate
        assert state["differs_from_checkout"] is True, (
            "the page called it a match while Gate 0 refused it")

    def test_the_checkout_itself_passes_both_readers(self, tmp_path):
        repo = checkout(tmp_path)
        gate = self._gate0(repo, repo / "deploy/scangrade-deploy.sh")
        assert gate.returncode == 0 and "REACHED_END" in gate.stdout, gate
        # …and the launcher the installer would write is *not* a copy of the runner,
        # which is why the page judges it by its rendered REPO rather than by bytes.
        launcher = install(tmp_path, "scangrade-deploy", launcher_for(repo))
        assert status.runner_state(launcher, repo)["kind"] == "launcher"

    def test_a_launcher_passes_gate_0_through_the_file_it_execs(self, tmp_path):
        """Gate 0 sees a launcher as SELF != REPO_RUNNER — which is exactly why it
        also compares bytes — so the launcher's own text must never be what Gate 0
        measures. Running the harness with the launcher would compare the launcher
        to the runner and refuse a correct install, so the installed launcher is
        checked by what it *execs*, and this pins that difference."""
        repo = checkout(tmp_path)
        launcher = install(tmp_path, "scangrade-deploy", launcher_for(repo))
        gate = self._gate0(repo, launcher)
        assert gate.returncode == 14, (
            "Gate 0 refuses the launcher's own file — so nothing may rely on Gate 0 "
            "to judge an installed launcher, which is what the service does instead")
        state = status.runner_state(launcher, repo, expect=launcher_for(repo))
        assert state["kind"] == "launcher" and state["gate0"] == "passes"


# ── the vocabulary ───────────────────────────────────────────────────────────

def template_keys() -> set[str]:
    """The reason keys the template has a sentence for."""
    text = TEMPLATE.read_text(encoding="utf-8")
    return set(re.findall(r"v\.key == '([a-z_]+)'", text))


def template_origin_keys() -> set[str]:
    """The provenance keys the template has a sentence for."""
    text = TEMPLATE.read_text(encoding="utf-8")
    return set(re.findall(r"status\.runner\.origin_key == '([a-z_]+)'", text))


def template_gate0_literals() -> set[str]:
    text = TEMPLATE.read_text(encoding="utf-8")
    return set(re.findall(r"status\.runner\.gate0 == '([^']+)'", text))


class TestTheVocabularyIsWired:
    def test_every_gate0_verdict_has_a_sentence_in_both_languages(self):
        """The template branches on these strings, so a constant renamed in Python
        has to fail here rather than render as an empty row on the box."""
        service = {status.GATE0_PASSES, status.GATE0_COPY_MATCHES,
                   status.GATE0_REFUSES, status.GATE0_CANNOT}
        assert service == template_gate0_literals(), (
            f"service-only: {sorted(service - template_gate0_literals())}; "
            f"template-only: {sorted(template_gate0_literals() - service)}")

    def test_every_provenance_key_has_a_sentence(self):
        assert template_origin_keys() == status.ORIGIN_KEYS, (
            f"missing from the page: {sorted(status.ORIGIN_KEYS - template_origin_keys())}; "
            f"invented by the page: {sorted(template_origin_keys() - status.ORIGIN_KEYS)}")

    def test_the_template_only_names_keys_the_service_has(self):
        unknown = template_keys() - status.REASON_KEYS
        assert not unknown, f"the template invents keys: {sorted(unknown)}"

    def test_the_service_only_emits_keys_the_template_has(self):
        assert status.REASON_KEYS >= template_keys(), (
            "a key with no sentence ships as a blank line: "
            f"{sorted(status.REASON_KEYS - template_keys())}")

    def test_every_verdict_this_can_reach_has_a_sentence(self, tmp_path):
        """Walk the reachable states and prove each one lands on a rendered
        sentence — a new branch that falls through to nothing is the defect this
        catches, not a key that exists in the abstract."""
        empty_checkout = {"available": False, "reason_key": "not_a_checkout",
                          "detail": None, "behind": None, "dirty": None}
        ok_checkout = {"available": True, "reason_key": None, "detail": None,
                       "behind": 0, "dirty": 0}
        runners = [
            {"kind": status.UNKNOWN, "reason_key": "absent", "detail": None,
             "gate0": status.UNKNOWN, "matches_this_commit": None},
            {"kind": "launcher", "reason_key": None, "detail": None,
             "gate0": "passes", "matches_this_commit": True},
            {"kind": "launcher", "reason_key": "launcher_stale", "detail": None,
             "gate0": "passes", "matches_this_commit": False},
            {"kind": "launcher", "reason_key": "other_path", "detail": "/elsewhere",
             "gate0": status.UNKNOWN, "matches_this_commit": None},
            {"kind": "copy", "reason_key": None, "detail": None,
             "gate0": "passes (the copy still matches)", "matches_this_commit": True},
            {"kind": "copy", "reason_key": "drifted", "detail": "deploy/x.sh",
             "gate0": "refuses (exit 14)", "matches_this_commit": False},
            {"kind": "copy", "reason_key": "unrendered", "detail": None,
             "gate0": status.UNKNOWN, "matches_this_commit": None},
        ]
        checkouts = [ok_checkout, empty_checkout,
                     {**ok_checkout, "behind": 3},
                     {**ok_checkout, "dirty": 2}]
        seen = set()
        for runner in runners:
            for checkout_state in checkouts:
                for paused in (False, True):
                    key = status.verdict(runner, checkout_state,
                                         paused=paused)["key"]
                    seen.add(key)
                    assert key in template_keys(), (
                        f"verdict key {key!r} has no sentence in the template")
        assert {"fresh", "behind", "dirty", "paused", "copy_drifted"} <= seen, seen


# ── the page ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def app():
    return create_app("testing")


def render_status(app, report) -> str:
    """The template with a given report — for the tests that read its copy.

    `g.user_id` is what base.html branches on to render the signed-in layout; with
    only a role set, the *content* block is never invoked and a test that greps the
    result is grepping the chrome. That is how the first version of these three
    tests "passed" their assertions about the page's sentences.
    """
    from flask import g, render_template
    with app.test_request_context("/super-admin/deploy-status"):
        g.user_id = "a-super-admin"
        g.user_role = "super_admin"
        g.user_name = "Tester"
        g.user_email = "t@t"
        g.tz_offset = 7
        return render_template("super_admin/deploy_status.html", status=report)


class TestThePage:
    def test_it_is_super_admin_only(self, app):
        client = app.test_client()
        response = client.get("/super-admin/deploy-status")
        assert response.status_code in (301, 302), response.status_code
        assert "/auth/login" in response.headers.get("Location", "")

    def test_it_is_reachable_from_the_navigation(self):
        nav = (ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8")
        assert 'href="/super-admin/deploy-status"' in nav, (
            "a page nobody can reach is a page nobody reads")

    def test_the_route_guards_before_it_reads(self):
        source = (ROOT / "app" / "routes" / "super_admin.py").read_text(encoding="utf-8")
        block = source.split("def deploy_status(", 1)[0]
        assert "@_sa_required" in block.split("@super_bp.route")[-1] or \
            "@_sa_required" in block.rsplit("def ", 1)[-1] + "@_sa_required", \
            "the route must carry the super-admin guard"
        assert re.search(r'@super_bp\.route\("/deploy-status"\)\s*\n@_sa_required',
                         source), "the decorator order put the guard first"

    def test_it_renders_both_languages(self, app):
        report = status.report(repo="/nonexistent", runner="/nonexistent",
                               snapshot_runner="/nonexistent",
                               pause_file="/nonexistent")
        html = render_status(app, report)
        # The unknown state must still render sentences rather than an empty box.
        assert html.count("t('") >= 20, (
            "the copy is pairs, not one language; the unknown branch alone renders "
            f"this many: {html.count(chr(116) + chr(39))}")
        assert "@REPO@" not in html
        assert "Deploy Runner Status" in html, (
            "the content block did not render — base.html gates it on g.user_id, and "
            "a test that misses that is testing the chrome")

    @needs_git
    def test_a_copy_that_predates_gate_0_does_not_claim_a_refusal(self, app, tmp_path):
        """The regression this whole change exists for.

        Production's runner was older than Gate 0: no check in it could refuse
        anything, and releases were going out with 45 commits of gate work missing.
        The page must not tell that box that nothing can deploy — that is the wrong
        story about the wrong failure, and it points at the wrong fix.
        """
        repo, _ = checkout_whose_runner_predates_gate_0(tmp_path)
        source = tmp_path / "installed-snapshot"
        source.write_bytes(subprocess.run(
            ["git", "cat-file", "blob", "HEAD~1:deploy/scangrade-deploy.sh"],
            cwd=repo, capture_output=True).stdout)
        report = status.report(
            repo=repo, runner=install_from(tmp_path, "scangrade-deploy", source),
            snapshot_runner=install(tmp_path, "snap", launcher_for(repo)),
            pause_file=tmp_path / "no-pause-flag")
        html = render_status(app, report)

        assert "no release can deploy" not in html, (
            "the page claimed a refusal that this copy cannot make")
        assert "nothing refuses it" in html
        assert "Every gate added since is not in effect" in html

    @needs_git
    def test_a_copy_that_carries_gate_0_still_claims_the_refusal(self, app, tmp_path):
        """The other direction: the sentence is not deleted, it is earned."""
        repo, _ = checkout_whose_runner_predates_gate_0(tmp_path)
        report = status.report(
            repo=repo,
            runner=install(tmp_path, "scangrade-deploy",
                           (repo / "deploy/scangrade-deploy.sh")
                           .read_text(encoding="utf-8") + "\n# drift\n"),
            snapshot_runner=install(tmp_path, "snap", launcher_for(repo)),
            pause_file=tmp_path / "no-pause-flag")
        assert report["verdict"]["key"] == "copy_drifted"
        assert report["runner"]["has_identity_check"] is True
        html = render_status(app, report)
        assert "no release can deploy" in html
        assert "nothing refuses it" not in html

    def test_the_stale_runner_number_is_the_first_thing_on_the_page(self, app, tmp_path):
        """The number that answers "which fixes are missing" is the runner's own
        distance, not the checkout's — a box can be perfectly up to date and still
        deploying with week-old logic."""
        report = status.report(repo="/nonexistent", runner="/nonexistent",
                               snapshot_runner="/nonexistent",
                               pause_file="/nonexistent")
        html = render_status(app, report)
        behind = html.find("Behind (runner)")
        checkout_behind = html.find("Commits Behind")
        assert behind != -1 and checkout_behind != -1
        assert behind < checkout_behind, "the runner's distance comes first"

    def test_the_template_writes_every_sentence_in_both_languages(self):
        """The reason a key is a key: the copy lives here, where the toggle and the
        i18n sweep can both see it."""
        text = TEMPLATE.read_text(encoding="utf-8")
        assert text.count("x-text=\"t('") >= 20
        assert "{% set content_lang" not in text, (
            "a page that can switch must not pin its own document language")
