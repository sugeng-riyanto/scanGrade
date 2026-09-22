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

    def test_a_copy_of_the_runner_that_mentions_the_placeholder_is_still_a_copy(self, tmp_path):
        """The reader keys on the placeholder's *assignment*, not the bare token.

        `deploy/scangrade-deploy.sh` names `@REPO@` itself while re-rendering the
        launcher from the checkout, and a bare-token test read a copy of the runner as
        an *unrendered launcher* — returning before the byte comparison this page
        exists to give, so a drifted copy was reported as a different problem.
        """
        repo = checkout(tmp_path)
        runner = (ROOT / "deploy/scangrade-deploy.sh").read_text(encoding="utf-8")
        assert status.PLACEHOLDER in runner, (
            "the runner no longer mentions the placeholder, so this fixture has stopped "
            "reproducing the shape that caught the defect — look for the token's new "
            "home before relaxing this test")
        state = state_of(tmp_path, repo, "scangrade-deploy", runner)
        assert state["kind"] == "copy", "a copy of the runner was read as a launcher"
        assert state["reason_key"] == "drifted"
        assert state["differs_from_checkout"] is True, (
            "the reading returned before comparing bytes, which is the defect")

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

#: What the alert card renders in the tests that are about something else. Armed
#: with one address, because the page's own default should be the state an operator
#: is meant to reach: a channel that has somewhere to go.
ALERTS_ARMED = {
    "armed": True, "to": ["ops@example.com"], "source": "setting",
    "source_detail": None, "interval_seconds": 6 * 3600, "min_commits": 5,
    "last": None, "state_dir": "/var/lib/scangrade-deploy/alerts",
    "state_error": None,
}


def render_status(app, report, alerts=None, testalert=None, released=None) -> str:
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
        return render_template("super_admin/deploy_status.html", status=report,
                               alerts=ALERTS_ARMED if alerts is None else alerts,
                               testalert=testalert, released=released)


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



def template_gate_keys() -> set[str]:
    """The gate names the quarantine card can say in either language.

    Digits are allowed because a gate may be numbered — `gate_0` is the runner's
    own name for the check that refuses an installed copy, and `[a-z_]+` would
    drop it silently, turning this guard into "every key I could parse has a
    sentence" while the missing one reads as *unknown* on the page.
    """
    return set(re.findall(r"q\.gate_key == '([a-z0-9_]+)'",
                          TEMPLATE.read_text(encoding="utf-8")))


def template_release_keys() -> set[str]:
    """The outcomes a one-click release can report."""
    return set(re.findall(r"released == '([a-z_]+)'",
                          TEMPLATE.read_text(encoding="utf-8")))


def template_quarantine_reason_keys() -> set[str]:
    """Why the quarantine record itself could not be read."""
    return set(re.findall(r"q\.reason_key == '([a-z_]+)'",
                          TEMPLATE.read_text(encoding="utf-8")))

# ── the release a gate is holding ────────────────────────────────────────────
#
# The quarantine is the deploy failure with no symptom on any other page: the
# previous release serves, nothing changes on the site, and the record is three
# lines in a file on the box. `/deploy-status` already answers "is the runner the
# checkout's own"; these hold the second question an operator asks when nothing has
# deployed for an hour — *which* commit is held, by *which* gate, since *when* —
# and the one-click answer to it.
#
# Two of them are relations rather than facts about one side, because the defect
# they exist for is drift *between* two sides: the page has to write where the
# runner looks, and every gate the runner can record has to be a gate the page can
# name in the reader's language. A test of either side alone would pass while the
# feature did nothing.

FAIL_REASON_LITERAL = re.compile(r'FAIL_REASON="([^"$]*)"')


def runner_fail_reasons() -> set[str]:
    """Every gate sentence `deploy/scangrade-deploy.sh` records.

    Literals only: the `$THEME_RC` in `theme gate (exit $THEME_RC)` is the runner's
    own variable, and `FAIL_REASON=""` is the reset before each gate rather than a
    reason — a `$` or a blank is a template, not something a gate ever writes.
    """
    text = RUNNER.read_text(encoding="utf-8")
    return {m.strip() for m in FAIL_REASON_LITERAL.findall(text) if m.strip()}


def runner_constant(name: str) -> str | None:
    """The value of a top-level assignment in the runner, e.g. `REQUEST_DIR`."""
    match = re.search(rf'^{name}="([^"\n]*)"', RUNNER.read_text(encoding="utf-8"), re.M)
    return match.group(1) if match else None


def as_path(value: str) -> str:
    """Compare paths by *path*, not by the separator this OS happens to use.

    The strings describe a Linux box; a test running on Windows renders the same
    `Path` with backslashes. Comparing the raw strings would fail on the shape of
    the test machine rather than on the thing the assertion is about.
    """
    return value.replace("\\", "/")


def resolved_runner_path(name: str) -> str | None:
    """A runner path with its own `$STATE_DIR` substituted.

    The runner spells its paths relative to that variable (`$STATE_DIR/requests`),
    so comparing the strings as written would fail on the spelling rather than on
    the path — and it is the *path* that has to agree with the page's default.
    """
    value = runner_constant(name)
    if value is None:
        return None
    known = {n: runner_constant(n)
             for n in ("STATE_DIR", "REQUEST_DIR", "RELEASE_REQUEST")}
    # Substitute until nothing moves: the runner spells one path in terms of
    # another (`RELEASE_REQUEST="$REQUEST_DIR/release"`, `REQUEST_DIR` in terms of
    # `$STATE_DIR`), so a single pass leaves `$STATE_DIR` sitting in the answer.
    for _ in range(len(known) + 1):
        before = value
        for var, raw in known.items():
            if raw:
                value = value.replace("${" + var + "}", raw).replace("$" + var, raw)
        if value == before:
            break
    return value


def runner_function(name: str) -> str:
    text = RUNNER.read_text(encoding="utf-8")
    start = text.index(f"{name}() {{")
    return text[start:text.index("\n}", start) + 2]


def held_report(tmp_path: Path, *, repo: Path | None = None, sha: str = "d" * 40,
                since: str = "2026-09-19T04:44:23+07:00",
                gate: str = "perf gate (slower than the last release that passed)"):
    """A box holding one commit, and nothing else that needs explaining."""
    record = tmp_path / "quarantined"
    record.write_text(f"{sha}\n{since}\n{gate}\n", encoding="utf-8")
    return status.report(repo=str(repo or tmp_path), runner="/nonexistent",
                         snapshot_runner="/nonexistent",
                         pause_file=str(tmp_path / "no-pause"),
                         quarantine_file=str(record),
                         request_dir=str(tmp_path / "requests"),
                         release_request=str(tmp_path / "requests" / "release"))


# ── the refusal that is about the box, not about a commit ───────────────────

def unarmed_report(tmp_path: Path, *, text: str | None = "2026-09-21T11:26:00+00:00\n"
                   "   runner     : a COPY of the deploy script\n"
                   "   claims     : MISSING (/etc/scangrade-claims.conf)\n",
                   path: Path | None = None) -> dict:
    """A box the deploy has refused for a box-side reason, and nothing else."""
    record = path or (tmp_path / "unarmed")
    # A caller that passes a path is supplying the file itself — a directory, in
    # the unreadable case — so only the default path is written here.
    if path is None and text is not None:
        record.write_text(text, encoding="utf-8")
    return status.report(repo=str(tmp_path), runner="/nonexistent",
                         snapshot_runner="/nonexistent",
                         pause_file=str(tmp_path / "no-pause"),
                         unarmed_file=str(record),
                         request_dir=str(tmp_path / "requests"))


class TestTheUnarmedRefusal:
    def test_the_record_is_the_runner_s_own_file(self):
        """One path, spelled in two languages of the same fact.

        The runner writes `/var/lib/scangrade-deploy/unarmed` and this page reads
        it. Two spellings that drift means a card that is always blank on a box
        that always refuses — which is the failure it exists to show.
        """
        runner = RUNNER.read_text(encoding="utf-8")
        state_dir = re.search(r'^STATE_DIR="([^"]+)"', runner, re.M).group(1)
        written = re.search(r'^UNARMED_FILE="([^"]+)"', runner, re.M).group(1)
        # The runner may spell it with the variable it already has; what matters is
        # the path it resolves to, which is what the page is pointed at.
        assert written.replace("$STATE_DIR", state_dir) == status.DEFAULT_UNARMED_FILE, (
            f"the page reads {status.DEFAULT_UNARMED_FILE}, the runner writes {written}")

    def test_a_record_is_reported_with_its_own_report_and_age(self, tmp_path):
        report = unarmed_report(tmp_path)
        unarmed = report["unarmed"]
        assert unarmed["present"] is True
        assert unarmed["key"] == status.UNARMED_PRESENT
        assert unarmed["at"] == "2026-09-21T11:26:00+00:00"
        assert unarmed["age_seconds"] is not None and unarmed["age_seconds"] >= 0
        assert "a COPY of the deploy script" in unarmed["detail"], (
            "the checker's own words are the evidence; rewriting them here would be "
            "a second opinion")
        assert "claims     : MISSING" in unarmed["detail"], (
            "the multi-line report is kept as the checker wrote it")

    def test_no_record_is_its_own_answer_not_a_reason(self, tmp_path):
        """\"Nothing has refused\" has a sentence, so it is not a reason key."""
        report = unarmed_report(tmp_path, text=None)
        unarmed = report["unarmed"]
        assert unarmed["present"] is False
        assert unarmed["key"] == status.UNARMED_NONE
        assert unarmed["detail"] is None

    def test_a_record_that_cannot_be_read_is_not_reported_as_armed(self, tmp_path):
        """The trap this file already documents twice: a blank that is not a blank.

        `_read` distinguishes absent from unreadable, and folding the second into
        the first is how a box that refuses every release looks like a box with
        nothing to report.
        """
        unreadable = tmp_path / "a-directory"
        unreadable.mkdir()
        report = unarmed_report(tmp_path, path=unreadable)
        unarmed = report["unarmed"]
        assert unarmed["present"] is False
        assert unarmed["key"] == status.UNARMED_UNREADABLE
        assert unarmed["reason"], "the reason it could not be read has to travel"

    def test_a_timestamp_that_cannot_be_parsed_still_counts_as_a_refusal(self, tmp_path):
        """The record's *presence* is the fact; its date is a convenience."""
        report = unarmed_report(tmp_path, text="not a date\n   runner : a COPY\n")
        unarmed = report["unarmed"]
        assert unarmed["present"] is True
        assert unarmed["at"] == "not a date"
        assert unarmed["age_seconds"] is None
        assert unarmed["detail"] == "   runner : a COPY"

    def test_a_refusal_with_nothing_after_the_timestamp_is_still_a_refusal(self, tmp_path):
        """The checker may have said nothing; that is its silence, not this page's
        licence to report the ordinary answer."""
        report = unarmed_report(tmp_path, text="2026-09-21T11:26:00+00:00\n")
        assert report["unarmed"]["present"] is True
        assert report["unarmed"]["detail"] is None

    def test_a_refused_box_is_not_given_a_clean_verdict(self, tmp_path):
        """The arrangement can be perfect while nothing deploys.

        This is the whole point of folding it into the verdict: a runner that is
        the checkout's launcher, a checkout at `origin/main`, and a record saying
        every run is refused is a box that deploys nothing — and "Everything in
        order" is the opposite of what is happening.
        """
        report = unarmed_report(tmp_path)
        fresh_runner = {"kind": "launcher", "reason_key": None, "detail": None,
                        "gate0": status.GATE0_PASSES, "matches_this_commit": True}
        ok_checkout = {"available": True, "reason_key": None, "detail": None,
                       "behind": 0, "dirty": 0}
        assert status.verdict(fresh_runner, ok_checkout, paused=False)["key"] == "fresh"
        refused = status.verdict(fresh_runner, ok_checkout, paused=False,
                                 unarmed=report["unarmed"])
        assert refused["key"] == "unarmed"
        assert refused["level"] == status.BROKEN, (
            "a box that refuses every release is not a warning")
        assert refused["detail"] == "2026-09-21T11:26:00+00:00", (
            "how long this has been going on is the first question asked")

    def test_pausing_is_still_not_the_same_answer(self, tmp_path):
        """A frozen box is somebody's decision; a refused one is a fault."""
        _ = tmp_path
        assert status.UNARMED_NONE != status.UNARMED_PRESENT


class TestThePageNamesTheUnarmedRefusal:
    def test_every_unarmed_reading_has_a_sentence_in_both_languages(self):
        """All three answers, and a separate one for each.

        `present` is the flag; the other two are compared by key. If the card
        folded `unreadable` into "nothing has refused", a box whose record this
        process cannot read would be described as armed — on the one page an
        operator has for the question.
        """
        text = TEMPLATE.read_text(encoding="utf-8")
        by_key = set(re.findall(r"u\.key == '([a-z_]+)'", text))
        if "u.present" in text:
            by_key |= {status.UNARMED_PRESENT}
        assert by_key == status.UNARMED_KEYS, (
            f"these states have no sentence on the page: "
            f"{sorted(status.UNARMED_KEYS - by_key)}")

    def test_the_card_is_painted_by_the_state_it_names(self):
        """A card that always looks calm is a card an operator skims past.

        The palette is the same one the runner card uses — rose for a refusal,
        surface for the ordinary answer — and it is chosen from the reading, not
        hard-coded. The sentence is in a language half the readers skim; the colour
        is what makes them stop and read it.
        """
        text = TEMPLATE.read_text(encoding="utf-8")
        block = text[text.index("{% set u = status.unarmed %}"):
                     text.index("{% set q = status.quarantine %}")]
        assert re.search(r"\{%\s*set UL = .*u\.key.*%\}", block), (
            "the unarmed card's palette does not depend on the state it is showing")
        assert "bg-rose-50" in block and "bg-surface-50" in block, (
            "the card has no palette of its own to choose from")

    def test_the_refusal_and_its_report_are_on_the_page(self, app, tmp_path):
        report = unarmed_report(tmp_path)
        html = render_status(app, report)
        assert "Release Refused" in html
        assert "a COPY of the deploy script" in html
        assert "claims     : MISSING" in html
        assert report["unarmed"]["at"] in html

    def test_the_cold_state_says_so_rather_than_showing_a_blank(self, app, tmp_path):
        html = render_status(app, unarmed_report(tmp_path, text=None))
        assert "No Release Refused for a Box-side Reason" in html
        assert "Nothing has been refused for a box-side reason" in html

    def test_an_unreadable_record_is_not_dressed_as_the_ordinary_answer(self, app, tmp_path):
        """Its own heading, its own sentence, and the captured error."""
        unreadable = tmp_path / "a-directory"
        unreadable.mkdir()
        html = render_status(app, unarmed_report(tmp_path, path=unreadable))
        assert "Refusal Record Unreadable" in html
        assert "There is a refusal on record" in html
        assert "No Release Refused for a Box-side Reason" not in html, (
            "a record this process cannot read is not evidence that nothing refused")


class _Unreachable(type(Path())):
    """A path that exists as far as the script is concerned and denies us anyway.

    This is what the VPS actually served: asking about anything under
    `/var/lib/scangrade-deploy` raised EACCES for the service user, because the
    directory was root:root 0750. A test cannot `chmod` that portably — Windows
    ignores the mode bits — so the calls the branches depend on are stood in for,
    each with the error it really raises. Both are needed because the two readers
    do not go through the same syscall: `_dir_writable` asks `stat()`, and `_read`
    opens the file.
    """

    def stat(self, *, follow_symlinks=True):  # noqa: ARG002
        raise PermissionError(13, "Permission denied")

    def read_text(self, encoding=None, errors=None):  # noqa: ARG002
        raise PermissionError(13, "Permission denied")


class TestADirectoryThatExistsButCannotBeReached:
    def test_a_missing_directory_is_its_own_answer(self, tmp_path):
        """`None` means the installer has not run; the page says exactly that."""
        assert status._dir_writable(tmp_path / "never-created") is None

    def test_a_directory_this_process_may_not_reach_is_not_missing(self, tmp_path):
        """The distinction that was wrong, and it named the wrong remedy.

        `stat()` raising anything other than FileNotFoundError means the directory
        is there and we are not allowed to see it. Reporting that as `None` told
        the operator "the installer has never been run on this server" — on a box
        where it had been run, which is how a permission problem stays invisible.
        """
        assert status._dir_writable(_Unreachable(str(tmp_path / "requests"))) is False

    def test_a_regular_file_where_the_directory_belongs_is_not_writable(self, tmp_path):
        """Not a directory ⇒ no request can be dropped in it, on any platform."""
        blocker = tmp_path / "requests"
        blocker.write_text("not a directory", encoding="utf-8")
        assert status._dir_writable(blocker) is False

    def test_the_card_shows_why_it_could_not_read_the_record(self, app, tmp_path):
        """A diagnosis without its cause leaves an operator with no next step.

        The service captured the OSError all along and the card dropped it, so the
        page said "the quarantine record could not be read" and stopped there — no
        path, no errno, nothing to look at. This is the one surface an operator
        without a shell has.
        """
        report = status.report(repo=str(tmp_path), runner="/nonexistent",
                               snapshot_runner="/nonexistent",
                               pause_file=str(tmp_path / "no-pause"),
                               quarantine_file=str(tmp_path / "var-lib" / "quarantined"))
        # `report()` takes paths as strings, so the reading is taken from the
        # unreachable path directly and dropped in — the same function the route
        # calls, with the path it cannot stat.
        report["quarantine"] = status.quarantine_state(
            _Unreachable(str(tmp_path / "var-lib" / "quarantined")),
            Path(str(tmp_path)), now=status._dt.datetime.now(status._dt.timezone.utc))
        assert report["quarantine"]["reason_key"] == "unreadable"
        html = render_status(app, report)
        assert "The quarantine record could not be read" in html, (
            "the unreadable branch is no longer the one the page renders")
        assert "Permission denied" in html, (
            "the captured error is not on the page, so the sentinel this branch "
            "exists to give is missing: a browser-only operator cannot find out "
            "which directory refused them")


class TestTheHeldRelease:
    def test_the_record_is_read_from_the_runner_s_own_file(self, tmp_path):
        """Three lines, written by the runner: sha, time, gate.

        Read rather than re-derived, so the page cannot disagree with the box about
        which commit is held.
        """
        report = held_report(tmp_path)
        held = report["quarantine"]
        assert held["held"] is True
        assert held["sha"] == "d" * 40
        assert held["short"] == "d" * 7, "the page shows a prefix; the record holds the sha"
        assert held["refused_at"] == "2026-09-19T04:44:23+07:00"
        assert held["gate"] == "perf gate (slower than the last release that passed)"
        assert held["gate_key"] == "perf_gate"
        assert held["age_seconds"] is not None and held["age_seconds"] >= 0

    def test_no_record_is_nothing_held_and_not_a_reason(self, tmp_path):
        """\"Nothing is held\" has its own sentence, so it is not a reason key."""
        report = status.report(repo=str(tmp_path), runner="/nonexistent",
                               snapshot_runner="/nonexistent",
                               pause_file=str(tmp_path / "no-pause"),
                               quarantine_file=str(tmp_path / "absent"),
                               request_dir=str(tmp_path / "requests"))
        held = report["quarantine"]
        assert held["held"] is False and held["reason_key"] is None

    def test_a_record_that_cannot_be_read_is_not_reported_as_nothing_held(self, tmp_path):
        """A blank here reads as \"no release is stuck\", which is the one wrong
        answer that looks exactly like the right one."""
        unreadable = tmp_path / "a-directory"
        unreadable.mkdir()
        report = status.report(repo=str(tmp_path), runner="/nonexistent",
                               snapshot_runner="/nonexistent",
                               pause_file=str(tmp_path / "no-pause"),
                               quarantine_file=str(unreadable),
                               request_dir=str(tmp_path / "requests"))
        held = report["quarantine"]
        assert held["held"] is False
        assert held["reason_key"] == "unreadable"
        assert held["detail"], "the reason it could not be read has to travel"

    def test_a_malformed_record_is_reported_rather_than_ignored(self, tmp_path):
        record = tmp_path / "quarantined"
        record.write_text("nothing like a sha\n", encoding="utf-8")
        report = status.report(repo=str(tmp_path), runner="/nonexistent",
                               snapshot_runner="/nonexistent",
                               pause_file=str(tmp_path / "no-pause"),
                               quarantine_file=str(record),
                               request_dir=str(tmp_path / "requests"))
        assert report["quarantine"]["reason_key"] == "malformed"
        assert report["quarantine"]["held"] is False

    @needs_git
    def test_the_held_commit_s_subject_comes_from_the_checkout(self, tmp_path):
        """So the operator recognises the release they are releasing."""
        repo = checkout(tmp_path)
        sha = _git("rev-parse", "HEAD", cwd=repo).stdout.strip()
        report = held_report(tmp_path, repo=repo, sha=sha)
        assert report["quarantine"]["subject"], (
            "a sha with no subject makes the operator open a shell to know what "
            "they are releasing")

    def test_a_commit_the_checkout_does_not_know_is_still_reported(self, tmp_path):
        """The rollback moves HEAD, so the commit is in the object database rather
        than on a branch — and a subject that cannot be found is an absent field,
        never an absent *record*."""
        report = held_report(tmp_path, sha="9" * 40)
        assert report["quarantine"]["held"] is True
        assert report["quarantine"]["subject"] is None


class TestTheGateIsNamed:
    def test_every_gate_the_runner_records_is_one_the_page_can_name(self):
        """The relation that makes the card trustworthy.

        A gate added to the runner reads as \"unknown\" here — the runner's own
        words still shown, but the one sentence an operator skims is missing. This
        is what fails first.
        """
        reasons = runner_fail_reasons()
        assert reasons, "no FAIL_REASON literals found — did the runner change shape?"
        unknown = {r for r in reasons if status.gate_key(r) == status.GATE_UNKNOWN}
        assert not unknown, (
            "these gates have no name on the page, so the card would say \"unknown\": "
            f"{sorted(unknown)}")

    def test_the_runner_s_own_default_is_the_unknown_bucket(self):
        """`${FAIL_REASON:-unknown gate}` is what a future gate gets if it forgets
        to name itself; it must not read as a gate nobody has heard of."""
        assert status.gate_key("unknown gate") == status.GATE_UNKNOWN
        assert status.gate_key("") == status.GATE_UNKNOWN
        assert status.gate_key(None) == status.GATE_UNKNOWN

    def test_a_gate_the_page_does_not_know_still_shows_the_runner_s_words(self, tmp_path):
        """Classifying is for the sentence, never for the evidence."""
        report = held_report(tmp_path, gate="a gate from a newer runner (exit 3)")
        held = report["quarantine"]
        assert held["gate_key"] == status.GATE_UNKNOWN
        assert held["gate"] == "a gate from a newer runner (exit 3)"


class TestTheReleaseRequest:
    def test_the_page_writes_where_the_runner_looks(self):
        """The whole feature is one path agreeing with another.

        The runner spells its paths relative to `$STATE_DIR` and the page has them
        absolute, so the comparison substitutes rather than matching the text: the
        *path* is what has to agree, and the state dir is checked first because a
        substitution from the wrong base would agree by accident.
        """
        assert runner_constant("STATE_DIR") == status.DEFAULT_STATE_DIR, (
            "the runner keeps its state somewhere else than the page looks")
        assert as_path(resolved_runner_path("REQUEST_DIR") or "") == \
            status.DEFAULT_REQUEST_DIR, (
            "the runner's request directory and the page's have drifted apart, so "
            "the button would write a file nothing reads")
        assert as_path(resolved_runner_path("RELEASE_REQUEST") or "") == \
            status.DEFAULT_REQUEST_DIR + "/release"

    def test_the_page_s_defaults_land_on_the_runner_s_paths(self, tmp_path):
        """The same relation from the page's side, through `report()`."""
        # No path overrides here on purpose: the assertion *is* about the defaults.
        # Reading an absent `/var/lib/...` is harmless wherever the suite runs.
        report = status.report(repo=str(tmp_path), runner="/nonexistent",
                               snapshot_runner="/nonexistent",
                               pause_file=str(tmp_path / "no-pause"))
        assert as_path(report["request_path"]) == \
            status.DEFAULT_REQUEST_DIR + "/release"
        assert as_path(report["quarantine_file"]) == \
            status.DEFAULT_STATE_DIR + "/quarantined"

    def test_nothing_is_written_when_nothing_is_held(self, tmp_path):
        """A request that outlived its quarantine would release the *next* refusal.

        That is the standing override the runner's own design refuses to have, so
        the answer is \"there is nothing to release\" — not a file waiting for
        something to release.
        """
        requests = tmp_path / "requests"
        requests.mkdir()
        request = requests / "release"
        result = status.request_release(
            request_file=str(request),
            quarantine_file=str(tmp_path / "no-record"))
        assert result["key"] == status.RELEASE_NOTHING_HELD
        assert result["written"] is False
        assert not request.exists(), (
            "a request was left behind with nothing held — it would release the "
            "next commit a gate refuses, and nobody asked for that")

    def test_a_missing_request_dir_is_its_own_answer(self, tmp_path):
        """`dir_missing` is not `not_writable`: one needs the installer run once,
        the other needs its permissions looked at."""
        record = tmp_path / "quarantined"
        record.write_text("e" * 40 + "\n2026-09-19T04:44:23+07:00\ntheme gate (exit 3)\n",
                          encoding="utf-8")
        result = status.request_release(
            request_file=str(tmp_path / "not-there" / "release"),
            quarantine_file=str(record))
        assert result["key"] == status.RELEASE_DIR_MISSING
        assert result["written"] is False
        assert (tmp_path / "not-there").exists() is False, (
            "the app must not create the runner's state directory itself")

    def test_a_write_that_is_refused_is_its_own_answer(self, tmp_path, monkeypatch):
        record = tmp_path / "quarantined"
        record.write_text("f" * 40 + "\n2026-09-19T04:44:23+07:00\ntheme gate (exit 3)\n",
                          encoding="utf-8")
        requests = tmp_path / "requests"
        requests.mkdir()

        def refuse(self, *args, **kwargs):
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(type(requests), "write_text", refuse)
        result = status.request_release(request_file=str(requests / "release"),
                                        quarantine_file=str(record))
        assert result["key"] == status.RELEASE_NOT_WRITABLE
        assert result["written"] is False
        assert result["detail"], "why it was refused has to travel with the answer"

    def test_a_written_request_names_the_held_commit(self, tmp_path):
        record = tmp_path / "quarantined"
        sha = "c" * 40
        record.write_text(f"{sha}\n2026-09-19T04:44:23+07:00\nsmoke test (exit 1)\n",
                          encoding="utf-8")
        requests = tmp_path / "requests"
        requests.mkdir()
        result = status.request_release(request_file=str(requests / "release"),
                                        quarantine_file=str(record))
        assert result["key"] == status.RELEASE_WRITTEN
        assert result["written"] is True and result["held"] == sha
        body = (requests / "release").read_text(encoding="utf-8")
        assert sha in body, "for a reader coming along later, the file names the commit"

    def test_the_runner_reads_only_the_existence_of_the_request(self):
        """One bit wide, on purpose.

        This is the only channel a web request has to a script that runs as root;
        if the runner read the file's contents it would be a way to put something
        into root's hands from a page.
        """
        block = runner_function("quarantine_honour_release")
        assert "RELEASE_REQUEST" in block, "the runner never looks at the request"
        for reader in (r"\bcat\b", r"\bread\b", r"\$\(<", r"< *\""):
            assert not re.search(reader, block), (
                "the honour function reads the request's contents; only its "
                f"existence may matter (matched {reader!r})")

    def test_the_installer_creates_the_directory_the_app_writes_to(self):
        """Ownership is the permission model here: the app runs as the service user,
        so that user has to own the directory it writes into.

        And the identity now comes from the *unit* rather than from the checkout's
        owner, because the group half is what the app reads its state through: with
        the group left out, `/var/lib/scangrade-deploy` was root:root 0750 and the
        quarantine card could not read the record it displays. See
        `test_auto_deploy.TestTheServiceUserCanReachItsStateDirectory`.
        """
        installer = (ROOT / "deploy" / "install-auto-deploy.sh").read_text(encoding="utf-8")
        assert "mkdir -p /var/lib/scangrade-deploy/requests" in installer
        assert ('chown "$SERVICE_USER":"$SERVICE_GROUP" '
                '/var/lib/scangrade-deploy/requests') in installer, (
            "the request directory is not given to the identity the unit runs as, so "
            "the button writes nowhere or the wrong place")
        assert "chmod 0750 /var/lib/scangrade-deploy/requests" in installer, (
            "a world-writable request directory would let any local user ask for a "
            "release")


class TestThePageNamesTheQuarantine:
    def test_every_gate_name_has_a_sentence_in_both_languages(self):
        assert template_gate_keys() == status.GATE_KEYS, (
            f"missing from the page: {sorted(status.GATE_KEYS - template_gate_keys())}; "
            f"invented by the page: {sorted(template_gate_keys() - status.GATE_KEYS)}")

    def test_every_release_answer_has_a_sentence_in_both_languages(self):
        assert template_release_keys() == status.RELEASE_KEYS, (
            f"missing from the page: {sorted(status.RELEASE_KEYS - template_release_keys())}; "
            f"invented by the page: {sorted(template_release_keys() - status.RELEASE_KEYS)}")

    def test_every_quarantine_reading_has_a_sentence(self):
        assert template_quarantine_reason_keys() == status.QUARANTINE_REASON_KEYS

    def test_the_button_is_a_real_post_form_the_csrf_injection_will_find(self, app, tmp_path):
        """`base.html` injects the token into `form[method="POST"]`, and the route
        is POST-only, so the button has to be a form rather than a fetch."""
        report = held_report(tmp_path)
        report["request_dir_writable"] = True
        html = render_status(app, report)
        assert re.search(r'<form method="POST" action="/super-admin/deploy-status/release"',
                         html), "no POST form for the release"
        assert "Release once (one attempt)" in html

    def test_the_button_is_withheld_when_the_request_cannot_be_written(self, app, tmp_path):
        """The page knows the answer already, so it does not offer a control that
        cannot work — it says what to do instead, and both remedies are named."""
        for writable, expected in ((None, "install-auto-deploy.sh"),
                                   (False, "not writable by the app process")):
            report = held_report(tmp_path)
            report["request_dir_writable"] = writable
            html = render_status(app, report)
            assert "deploy-status/release" not in html, (
                f"the release button was offered with writable={writable!r}")
            assert expected in html, expected

    def test_a_pending_request_does_not_offer_a_second_one(self, app, tmp_path):
        report = held_report(tmp_path)
        report["request_dir_writable"] = True
        report["request_pending"] = True
        html = render_status(app, report)
        assert "deploy-status/release" not in html
        assert "already waiting" in html

    def test_the_held_commit_gate_and_time_are_on_the_page(self, app, tmp_path):
        report = held_report(tmp_path)
        html = render_status(app, report)
        assert report["quarantine"]["short"] in html
        assert "perf gate (slower than the last release that passed)" in html, (
            "the runner's own words are the evidence for the sentence beside them")
        assert report["quarantine"]["refused_at"] in html
        assert "the performance gate" in html, "the gate name is not in the reader's language"

    def test_the_cold_state_says_so_rather_than_showing_a_blank(self, app, tmp_path):
        report = status.report(repo=str(tmp_path), runner="/nonexistent",
                               snapshot_runner="/nonexistent",
                               pause_file=str(tmp_path / "no-pause"),
                               quarantine_file=str(tmp_path / "absent"),
                               request_dir=str(tmp_path / "requests"))
        html = render_status(app, report)
        assert "No Held Release" in html
        assert "No commit is held" in html
        assert "deploy-status/release" not in html


class TestTheReleaseRoute:
    _SOURCE = (ROOT / "app" / "routes" / "super_admin.py").read_text(encoding="utf-8")

    def test_it_is_a_post_and_super_admin_only(self):
        assert re.search(
            r'@super_bp\.route\("/deploy-status/release", methods=\["POST"\]\)\s*\n@_sa_required',
            self._SOURCE), (
            "the release route is not a guarded POST — a GET would let a link or a "
            "prefetch release a quarantine")

    def test_it_answers_with_a_key_rather_than_a_sentence(self):
        assert re.search(r"\?released=\{result\['key'\]\}", self._SOURCE), (
            "the outcome has to travel as a key; a sentence built here is copy the "
            "language toggle and the i18n sweep cannot reach")

    def test_it_drops_the_cached_report_before_redirecting(self):
        """Otherwise the operator lands on a page showing the reading from *before*
        the click, which reads as \"the button did nothing\"."""
        block = self._SOURCE.split("def deploy_status_release", 1)[1].split("\n@", 1)[0]
        invalidate_at = block.index('invalidate("deploy_status:report")')
        redirect_at = block.index("return redirect(")
        assert invalidate_at < redirect_at, "the stale report is still cached"

    def test_it_is_audited(self):
        block = self._SOURCE.split("def deploy_status_release", 1)[1].split("\n@", 1)[0]
        assert "log_activity(" in block, (
            "releasing a quarantined release is a consequential act on the box and "
            "leaves no trace otherwise")
