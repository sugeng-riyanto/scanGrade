"""A release that *landed* is not a release that is being *served*.

Everything else on `/super-admin/deploy-status` describes the arrangement: which
runner will deploy, what the checkout holds, how far it is behind `origin/main`.
All of it comes out of the box's files, and all of it can read perfectly well
while the process answering the page is still running last week's code — which is
exactly the shape the box was in for a day and a half: the checkout had fetched,
the journal said a release was refused, the site answered, and nothing on the page
could distinguish "the code being served is the code the box holds" from "the code
being served is four commits older".

So the app now reads its **own** commit, once per process, from the repository its
code was loaded out of, and the page places it against the checkout's `HEAD`:

* the same commit → the last release is loaded in this process;
* the checkout ahead → a release merged and this process never came up on it (the
  reload failed, or has not happened yet): the page says so in those words;
* the checkout behind → the box was rolled back under a running process;
* a commit this repository does not have → the box was reset, or the process was
  started somewhere else, and the honest answer is that it cannot be placed.

The readings are held to three rules, and each is a test below: a number is never
invented (`unknown` is not zero), the process's own commit is read from *where the
code lives* rather than from where the deploy looks (the two differ exactly when a
release has moved the checkout past the process), and the headline verdict names
the served code when it is older than the box — because that is a statement about
what is being served right now, not about what the next tick would do.
"""
from __future__ import annotations

import datetime as _dt
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.services import deploy_status_service as status  # noqa: E402
from app.utils import build_info  # noqa: E402

TEMPLATE = ROOT / "app" / "templates" / "super_admin" / "deploy_status.html"
ROUTE = ROOT / "app" / "routes" / "super_admin.py"
GIT = shutil.which("git")

needs_git = pytest.mark.skipif(GIT is None, reason="needs git to read a checkout")


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def _commit(repo: Path, message: str) -> str:
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty",
         "-m", message, cwd=repo)
    return _git("rev-parse", "HEAD", cwd=repo).stdout.strip()


def repo_with_commits(tmp_path: Path, messages=("first",)) -> Path:
    """A real checkout with real commits. Real git rather than a fake: the reading
    being tested is `rev-parse` / `rev-list`, so a mock would test the mock."""
    repo = tmp_path / "checkout"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    for message in messages:
        _commit(repo, message)
    return repo


def short_of(repo: Path, rev: str = "HEAD") -> str:
    return _git("rev-parse", "--short", rev, cwd=repo).stdout.strip()


def launcher_report(repo: Path, *, running, **kwargs) -> dict:
    """The whole page's report over a checkout whose runner is this repository's
    own rendered launcher — the arrangement that is otherwise *fresh*, so the only
    thing that can move the verdict is the code being served."""
    entrypoint = (ROOT / "deploy" / "entrypoint.sh").read_text(encoding="utf-8")
    rendered = entrypoint.replace(status.PLACEHOLDER, str(repo))
    installed = repo.parent / "bin"
    installed.mkdir(exist_ok=True)
    runner = installed / "scangrade-deploy"
    runner.write_text(rendered, encoding="utf-8")
    snapshot_runner = installed / "snap"
    snapshot_runner.write_text(rendered, encoding="utf-8")
    return status.report(repo=repo, runner=runner, snapshot_runner=snapshot_runner,
                         pause_file=repo.parent / "no-pause-flag", running=running,
                         **kwargs)


# ── the process's own commit ─────────────────────────────────────────────────

class TestTheProcessKnowsItsOwnCommit:
    def test_it_reads_the_commit_the_code_came_from(self, tmp_path):
        repo = repo_with_commits(tmp_path)
        snap = build_info.read_commit(repo)
        assert snap["available"] is True
        assert snap["commit"] == short_of(repo)
        assert snap["subject"] == "first"
        assert snap["committed_at"], "the reading has to carry its own date"

    def test_it_carries_the_full_sha_the_placement_needs(self, tmp_path):
        """`rev-list` cannot place an abbreviation, and a page that printed one
        while comparing a different one would be two readings wearing one sha."""
        repo = repo_with_commits(tmp_path)
        snap = build_info.read_commit(repo)
        assert snap["full_commit"] == _git("rev-parse", "HEAD", cwd=repo).stdout.strip()
        assert snap["full_commit"].startswith(snap["commit"])

    def test_a_path_that_is_not_a_checkout_is_a_reason_not_a_crash(self, tmp_path):
        snap = build_info.read_commit(tmp_path / "plain")
        assert snap["available"] is False
        assert snap["reason_key"] == "not_a_checkout"
        assert snap["commit"] is None, "unknown is not a sha"

    def test_it_is_read_once_for_the_process(self):
        """A git call per render would be a reading that drifts with the checkout,
        which is the one thing this must not do: what is being served is fixed
        until the process is replaced."""
        first = build_info.snapshot()
        assert build_info.snapshot() is first, "the snapshot is re-read per call"
        assert first["commit"] and first["loaded_at"] and first["pid"]

    def test_it_describes_where_this_code_lives_not_where_the_deploy_looks(
            self, monkeypatch, tmp_path):
        """`SCANGRADE_REPO` is the checkout the *deploy* will act on. A process
        that read its own commit from there would be reporting the checkout's HEAD
        under the name of the running code — which is precisely the pair this page
        exists to tell apart."""
        monkeypatch.setenv("SCANGRADE_REPO", str(tmp_path))
        snap = build_info.read_commit(ROOT)
        assert snap["repo"] == str(ROOT)
        assert build_info.snapshot()["repo"] == str(ROOT)
        #: Asked again here rather than only through `snapshot()`, because the
        #: snapshot's own reading is resolved at import — long before this test can
        #: point the variable anywhere. `read_own_commit` is the one call that still
        #: chooses a path at call time, so it is the one this guard has to make.
        assert build_info.read_own_commit()["repo"] == str(ROOT), (
            "the reading took its path from SCANGRADE_REPO, which names the checkout "
            "the deploy is about to touch rather than the one this code came from")

    def test_the_module_is_where_the_app_package_is(self):
        assert build_info.CODE_ROOT == ROOT, (
            "the reading has to come out of the repository this code was loaded from")

    def test_every_git_call_carries_the_no_locks_flag(self):
        """The same rule the checkout reader holds, and for the same reason: a
        status page must not be a writer on the checkout the deploy is about to
        use. An allowlist rather than a blocklist, so a new call site has to be
        argued for here instead of quietly joining the reader without it."""
        source = (ROOT / "app" / "utils" / "build_info.py").read_text(encoding="utf-8")
        calls = source.count("[git, ")
        assert calls >= 3, "no git call sites found — did the reader stop reading?"
        assert source.count('[git, "--no-optional-locks"') == calls, (
            "a git call was added without --no-optional-locks, so this page can "
            "take a lock on the checkout")


# ── where the running code sits ──────────────────────────────────────────────

class TestWhereTheRunningCodeSits:
    @needs_git
    def test_the_same_commit_reads_as_current(self, tmp_path):
        repo = repo_with_commits(tmp_path)
        state = status.checkout_state(repo, now=_now(),
                                      running=build_info.read_commit(repo))
        run = state["running"]
        assert run["key"] == status.RUNNING_CURRENT
        assert run["behind"] == 0 and run["ahead"] == 0

    @needs_git
    def test_a_release_that_landed_without_a_reload_reads_as_behind(self, tmp_path):
        """The shape this whole reading exists for: the merge happened, the reload
        did not, and every other figure on the page is happy about it."""
        repo = repo_with_commits(tmp_path)
        served = build_info.read_commit(repo)
        landed = _commit(repo, "the release that landed")
        state = status.checkout_state(repo, now=_now(), running=served)
        run = state["running"]
        assert run["key"] == status.RUNNING_BEHIND
        assert run["behind"] == 1
        assert run["commit"] != short_of(repo), "the page names what is served, not HEAD"
        assert run["commit"] == served["commit"]
        assert state["head"] == landed[:7], "and HEAD is the release that landed"

    @needs_git
    def test_a_rolled_back_checkout_reads_as_ahead(self, tmp_path):
        repo = repo_with_commits(tmp_path, messages=("first", "second"))
        served = build_info.read_commit(repo)
        _git("reset", "-q", "--hard", "HEAD~1", cwd=repo)
        run = status.checkout_state(repo, now=_now(), running=served)["running"]
        assert run["key"] == status.RUNNING_AHEAD
        assert run["ahead"] == 1

    @needs_git
    def test_a_commit_this_repo_never_had_reads_as_unknown(self, tmp_path):
        repo = repo_with_commits(tmp_path)
        invented = {**build_info.read_commit(repo), "full_commit": "0" * 40,
                    "commit": "0000000", "subject": "from somewhere else"}
        run = status.checkout_state(repo, now=_now(), running=invented)["running"]
        assert run["key"] == status.RUNNING_UNKNOWN
        assert run["behind"] is None and run["ahead"] is None, "unknown is not zero"

    @needs_git
    def test_a_process_that_could_not_read_itself_is_not_a_zero(self, tmp_path):
        repo = repo_with_commits(tmp_path)
        blind = {"available": False, "reason_key": "no_git", "detail": None,
                 "repo": str(repo), "commit": None, "full_commit": None,
                 "subject": None, "committed_at": None, "loaded_at": None, "pid": 7}
        run = status.checkout_state(repo, now=_now(), running=blind)["running"]
        assert run["key"] == status.RUNNING_UNREADABLE
        assert run["reason_key"] == "no_git", "the reason travels with the sentence"
        assert run["behind"] is None

    @needs_git
    def test_the_reading_survives_a_checkout_it_cannot_place_it_in(self, tmp_path):
        """A box whose checkout is unreadable can still say what code is being
        served — and that is the more useful half of the news."""
        plain = tmp_path / "plain"
        plain.mkdir()
        snap = build_info.read_commit(ROOT)
        state = status.checkout_state(plain, now=_now(), running=snap)
        assert state["available"] is False
        assert state["running"]["commit"] == snap["commit"]
        assert state["running"]["key"] == status.RUNNING_UNKNOWN

    @needs_git
    def test_the_reading_carries_how_long_this_code_has_been_serving(self, tmp_path):
        repo = repo_with_commits(tmp_path)
        snap = {**build_info.read_commit(repo),
                "loaded_at": (_now() - _dt.timedelta(seconds=90)).isoformat()}
        run = status.checkout_state(repo, now=_now(), running=snap)["running"]
        assert run["age_seconds"] is not None and 85 <= run["age_seconds"] <= 120

    @needs_git
    def test_reading_the_process_touches_nothing(self, tmp_path):
        """Same rule as the checkout reader: a status page is not a writer."""
        repo = repo_with_commits(tmp_path)
        index = repo / ".git" / "index"
        status.checkout_state(repo, now=_now(), running=build_info.read_commit(repo))
        before = index.stat().st_mtime_ns
        status.checkout_state(repo, now=_now(), running=build_info.read_commit(repo))
        assert index.stat().st_mtime_ns == before


# ── the verdict ──────────────────────────────────────────────────────────────

def _runner_ok() -> dict:
    return {"kind": "launcher", "reason_key": None, "detail": None,
            "gate0": status.GATE0_PASSES, "matches_this_commit": True}


class TestWhatItAddsUpTo:
    @needs_git
    def test_code_older_than_the_box_is_the_headline(self, tmp_path):
        """Every other reading here describes the arrangement — what will happen on
        the next tick. This one describes what is being served right now, and a
        release that merged and never loaded is a fault the arrangement cannot see."""
        repo = repo_with_commits(tmp_path)
        served = build_info.read_commit(repo)
        _commit(repo, "landed")
        checkout = status.checkout_state(repo, now=_now(), running=served)
        verdict = status.verdict(_runner_ok(), checkout, paused=False)
        assert verdict["level"] == status.WARN
        assert verdict["key"] == "running_behind"
        assert verdict["detail"] == "1"

    @needs_git
    def test_it_outranks_a_dirty_checkout_but_not_a_stop(self, tmp_path):
        """A stop is still a stop: `refused` and a heal the runner performed say
        the run ended, which is above any reading of what is currently served."""
        repo = repo_with_commits(tmp_path)
        served = build_info.read_commit(repo)
        _commit(repo, "landed")
        (repo / "scribble.txt").write_text("x\n", encoding="utf-8")
        dirty = status.checkout_state(repo, now=_now(), running=served)
        assert status.verdict(_runner_ok(), dirty, paused=False)["key"] == "running_behind"
        for stop in ("refused", "box_edits"):
            verdict = status.verdict(
                _runner_ok(), dirty, paused=False,
                preflight={"present": True, "gate_key": "smoke"} if stop == "refused" else None,
                box_edits={"present": True, "short": "x"} if stop == "box_edits" else None)
            assert verdict["key"] == stop, f"{stop} is a stop, not a reading"

    @needs_git
    def test_a_matching_process_changes_nothing(self, tmp_path):
        repo = repo_with_commits(tmp_path)
        checkout = status.checkout_state(repo, now=_now(),
                                        running=build_info.read_commit(repo))
        assert status.verdict(_runner_ok(), checkout, paused=False)["key"] == "fresh"

    @needs_git
    def test_an_unplaceable_commit_is_reported_but_is_not_the_headline(self, tmp_path):
        """It is worth a sentence on the card and it is not a release that failed to
        load, so it must not take the headline from a reading that answers one."""
        repo = repo_with_commits(tmp_path)
        invented = {**build_info.read_commit(repo), "full_commit": "0" * 40,
                    "commit": "0000000"}
        checkout = status.checkout_state(repo, now=_now(), running=invented)
        assert status.verdict(_runner_ok(), checkout, paused=False)["key"] == "fresh"


# ── the page ─────────────────────────────────────────────────────────────────

def template_running_keys() -> set[str]:
    """The placement keys the template has a sentence for."""
    text = TEMPLATE.read_text(encoding="utf-8")
    return set(re.findall(r"status\.checkout\.running\.key == '([a-z_]+)'", text))


def render_status(app, report) -> str:
    from flask import g, render_template
    with app.test_request_context("/super-admin/deploy-status"):
        g.user_id = "a-super-admin"
        g.user_role = "super_admin"
        g.user_name = "Tester"
        g.user_email = "t@t"
        g.tz_offset = 7
        return render_template("super_admin/deploy_status.html", status=report,
                               alerts=None, testalert=None, released=None)


class TestThePageShowsIt:
    def test_every_placement_has_a_sentence_in_both_languages(self):
        """The template branches on these strings, so a constant renamed in Python
        has to fail here rather than render as an empty row on the box."""
        assert template_running_keys() == status.RUNNING_KEYS, (
            f"no sentence: {sorted(status.RUNNING_KEYS - template_running_keys())}; "
            f"invented: {sorted(template_running_keys() - status.RUNNING_KEYS)}")

    def test_the_headline_has_a_sentence(self):
        assert "v.key == 'running_behind'" in TEMPLATE.read_text(encoding="utf-8"), (
            "the verdict key has no sentence, so the page would fall through to "
            "'cannot be decided' while the box serves older code")

    def test_the_route_does_not_have_to_remember_it(self):
        """The one input that comes from *this process* rather than from the box's
        files is read by the report itself, so a second caller — a download, a
        future JSON view — cannot quietly ship without it."""
        report = status.report(repo="/nonexistent", runner="/nonexistent",
                               snapshot_runner="/nonexistent", pause_file="/nonexistent")
        assert report["checkout"]["running"]["commit"] == build_info.snapshot()["commit"]

    @needs_git
    def test_the_page_names_the_commit_beside_the_checkout_gap(self, app, tmp_path):
        repo = repo_with_commits(tmp_path)
        served = build_info.read_commit(repo)
        _commit(repo, "the release that landed")
        html = render_status(app, launcher_report(repo, running=served))
        assert served["commit"] in html, "the served commit is not on the page"
        assert served["subject"] in html, "nor is what that commit was"
        assert "belum dimuat proses ini" in html, (
            "the page does not say that a release landed and was never loaded")
        assert 't(\'Kode yang menjawab halaman ini\'' in html

    @needs_git
    def test_a_process_that_cannot_read_itself_reports_no_sha(self, app, tmp_path):
        repo = repo_with_commits(tmp_path)
        blind = {"available": False, "reason_key": "no_git", "detail": None,
                 "repo": str(repo), "commit": None, "full_commit": None,
                 "subject": None, "committed_at": None, "loaded_at": None, "pid": 7}
        html = render_status(app, launcher_report(repo, running=blind))
        assert "tidak bisa membaca commit-nya sendiri" in html, (
            "a process with no reading of itself must say so rather than show a sha")
        assert "tidak bisa dinilai" in html, (
            "the placement has no sentence for a process that could not read itself")
