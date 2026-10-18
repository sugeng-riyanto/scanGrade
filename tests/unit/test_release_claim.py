"""A box's *claim* about its last release, against the code that answers the page.

Two readings on `/super-admin/deploy-status` already describe what is being served,
and both of them compare it with the **checkout**: the served commit is placed
against `HEAD`, so "a release landed and this process never loaded it" is visible.
That answers `served == held`. It is not the same question as `served == released`,
and the two come apart in the states worth knowing about:

* the checkout moved outside the runner — a hand pull with a restart — so the code
  being served equals `HEAD`, no release on record put it there, and every reading
  that compares against `HEAD` is happy;
* a rollback whose reload did not take: the checkout went back, the runner recorded
  the commit it went back *to*, and the process is still on the refused one.

The runner has written a record of the release it landed all along — three lines,
the commit, when, and the recovery point taken before it — and until now the only
reader was an operator with a shell: `docs/AUTO_DEPLOY.md` tells you to `cat` it.
This suite holds the reading that puts it on the page, and the four rules the rest
of that page follows: a comparison is decided on the **full** sha, an unreadable
record is never reported as an absent one, "I could not check" is never reported as
"it matches", and the file this page reads is the file the runner writes.
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
from tests.unit.git_env import git_env  # noqa: E402

TEMPLATE = ROOT / "app" / "templates" / "super_admin" / "deploy_status.html"
RUNNER = ROOT / "deploy" / "scangrade-deploy.sh"
UNSTICK = ROOT / "deploy" / "unstick-deploy.sh"
GIT = shutil.which("git")

needs_git = pytest.mark.skipif(GIT is None, reason="needs git to read a checkout")


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          env=git_env())


def _commit(repo: Path, message: str) -> str:
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty",
         "-m", message, cwd=repo)
    return _git("rev-parse", "HEAD", cwd=repo).stdout.strip()


def repo_with_commits(tmp_path: Path, messages=("first",)) -> Path:
    repo = tmp_path / "checkout"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    for message in messages:
        _commit(repo, message)
    return repo


def full_of(repo: Path, rev: str = "HEAD") -> str:
    return _git("rev-parse", rev, cwd=repo).stdout.strip()


def write_record(path: Path, sha: str, *, when: str | None = None,
                 snapshot: str = "") -> Path:
    """The runner's own three lines, written the way it writes them."""
    stamp = when or _now().isoformat(timespec="seconds")
    path.write_text(f"{sha}\n{stamp}\n{snapshot}\n", encoding="utf-8", newline="\n")
    return path


# ── the record itself ────────────────────────────────────────────────────────

class TestTheBoxsRecordOfItsOwnRelease:
    def test_the_three_lines_are_the_commit_the_time_and_the_recovery_point(
            self, tmp_path):
        sha = "a" * 40
        record = write_record(tmp_path / "last-deploy", sha, snapshot="/var/snap/1")
        state = status.last_release_state(record, tmp_path, now=_now())
        # No `running` was handed in, so the record is read and the comparison it
        # exists for is not made — which is `unknown`, never `live`.
        assert state["key"] == status.CLAIM_UNKNOWN
        assert state["present"] is True
        assert state["sha"] == sha and state["short"] == sha[:7]
        assert state["released_at"] and state["snapshot"] == "/var/snap/1"

    def test_a_record_with_no_recovery_point_is_not_a_record_with_an_empty_one(
            self, tmp_path):
        record = write_record(tmp_path / "last-deploy", "b" * 40)
        state = status.last_release_state(record, tmp_path, now=_now())
        assert state["snapshot"] is None, (
            "a release that shipped no migrations must not read as one whose "
            "recovery point is the empty string")

    def test_the_reading_carries_its_own_age(self, tmp_path):
        when = (_now() - _dt.timedelta(seconds=90)).isoformat(timespec="seconds")
        record = write_record(tmp_path / "last-deploy", "c" * 40, when=when)
        state = status.last_release_state(record, tmp_path, now=_now())
        assert state["age_seconds"] is not None
        assert 85 <= state["age_seconds"] <= 120
        assert state["released_at"] == when

    def test_no_record_is_absent_and_not_a_fault(self, tmp_path):
        """`unstick-deploy.sh` deletes this file deliberately, so \"no release is on
        record\" is an ordinary answer the page has a sentence for."""
        state = status.last_release_state(tmp_path / "never-written", tmp_path,
                                          now=_now())
        assert state["key"] == status.CLAIM_ABSENT
        assert state["present"] is False and state["sha"] is None
        assert state["reason"] is None, "absent is not a reason"

    def test_a_first_line_that_is_not_a_commit_is_malformed_not_absent(self, tmp_path):
        """A half-written file and a file that is not there are different findings:
        one means the runner was interrupted mid-write, the other means it has never
        written. Folding them together hides the first."""
        record = tmp_path / "last-deploy"
        record.write_text("not-a-sha\n2026-01-01T00:00:00+00:00\n", encoding="utf-8")
        state = status.last_release_state(record, tmp_path, now=_now())
        assert state["key"] == status.CLAIM_MALFORMED
        assert state["present"] is False and state["reason"] == "not-a-sha"

    def test_a_record_that_cannot_be_read_is_reported_rather_than_absent(self, tmp_path):
        directory = tmp_path / "last-deploy"
        directory.mkdir()
        state = status.last_release_state(directory, tmp_path, now=_now())
        assert state["key"] == status.CLAIM_UNREADABLE
        assert state["reason"], "the reason travels with the answer"

    @needs_git
    def test_the_commit_is_named_by_a_subject_when_the_checkout_has_it(self, tmp_path):
        repo = repo_with_commits(tmp_path, messages=("the release that was recorded",))
        record = write_record(tmp_path / "last-deploy", full_of(repo))
        state = status.last_release_state(record, repo, now=_now())
        assert state["subject"] == "the release that was recorded"

    @needs_git
    def test_a_record_naming_a_commit_this_checkout_never_had_still_answers(self, tmp_path):
        """The answer is sha equality, which needs no history: a record the checkout
        cannot resolve is still a definite answer to *is that what is served?* — it is
        the grep-ability of the subject that is lost, not the verdict."""
        repo = repo_with_commits(tmp_path, messages=("first",))
        record = write_record(tmp_path / "last-deploy", "d" * 40)
        served = {**build_info.read_commit(repo), "full_commit": "e" * 40,
                  "commit": "eeeeeee"}
        state = status.last_release_state(record, repo, now=_now(), running=served)
        assert state["subject"] is None, "no history holds that commit"
        assert state["key"] == status.CLAIM_NOT_SERVED


# ── the comparison ───────────────────────────────────────────────────────────

class TestTheClaimAgainstWhatIsServed:
    @needs_git
    def test_the_recorded_release_being_served_is_live(self, tmp_path):
        repo = repo_with_commits(tmp_path, messages=("first", "the release"))
        record = write_record(tmp_path / "last-deploy", full_of(repo))
        state = status.last_release_state(record, repo, now=_now(),
                                          running=build_info.read_commit(repo))
        assert state["key"] == status.CLAIM_LIVE and state["served"] is True

    @needs_git
    def test_serving_something_else_is_not_served(self, tmp_path):
        repo = repo_with_commits(tmp_path, messages=("first", "second"))
        served = build_info.read_commit(repo)
        record = write_record(tmp_path / "last-deploy", full_of(repo, "HEAD~1"))
        state = status.last_release_state(record, repo, now=_now(), running=served)
        assert state["key"] == status.CLAIM_NOT_SERVED
        assert state["served"] is False
        assert state["served_commit"] == served["commit"], (
            "the page names the code that is actually being served")

    def test_the_comparison_is_decided_on_the_full_sha(self, tmp_path):
        """Two commits can share an abbreviation, and the page prints the short one.
        Comparing what it prints would call them the same on exactly the collision
        where the answer matters — so the abbreviation is display only."""
        record_sha = "abc1234" + "0" * 33
        other_sha = "abc1234" + "f" * 33
        record = write_record(tmp_path / "last-deploy", record_sha)
        running = {"available": True, "reason_key": None, "detail": None,
                   "repo": str(tmp_path), "commit": record_sha[:7],
                   "full_commit": other_sha, "subject": "other",
                   "committed_at": None, "loaded_at": None, "pid": 1}
        state = status.last_release_state(record, tmp_path, now=_now(), running=running)
        assert state["key"] == status.CLAIM_NOT_SERVED, (
            "the answer was taken from the seven characters the page displays")

    def test_a_process_that_cannot_read_itself_is_unknown_and_never_live(self, tmp_path):
        record = write_record(tmp_path / "last-deploy", "f" * 40)
        blind = {"available": False, "reason_key": "no_git", "detail": None,
                 "repo": str(tmp_path), "commit": None, "full_commit": None,
                 "subject": None, "committed_at": None, "loaded_at": None, "pid": 7}
        state = status.last_release_state(record, tmp_path, now=_now(), running=blind)
        assert state["key"] == status.CLAIM_UNKNOWN
        assert state["served"] is None, "\"could not check\" is not \"it matches\""
        assert state["detail"] == "no_git"


# ── the verdict ──────────────────────────────────────────────────────────────

def _runner_ok() -> dict:
    return {"kind": "launcher", "reason_key": None, "detail": None,
            "gate0": status.GATE0_PASSES, "matches_this_commit": True}


@needs_git
def served_state(repo: Path, *, running, record_sha: str) -> tuple[dict, dict]:
    """The checkout reading and the claim reading for one repo, as `report` builds
    them: the claim is compared with the served commit *as the checkout placed it*."""
    checkout = status.checkout_state(repo, now=_now(), running=running)
    record = write_record(repo.parent / "last-deploy", record_sha)
    return checkout, status.last_release_state(record, repo, now=_now(),
                                               running=checkout["running"])


class TestWhatItAddsUpTo:
    @needs_git
    def test_code_no_release_recorded_is_the_headline(self, tmp_path):
        """The state nothing else on the page can see: the box serves a commit it
        holds (`running` reads as current, the tree is clean, nothing is behind) and
        that commit is not the release it recorded — a pull and a restart outside the
        runner, which serves code no gate ever judged."""
        repo = repo_with_commits(tmp_path, messages=("first", "second"))
        served = build_info.read_commit(repo)
        checkout, claim = served_state(repo, running=served,
                                       record_sha=full_of(repo, "HEAD~1"))
        assert checkout["running"]["key"] == status.RUNNING_CURRENT, "the checkout agrees"
        assert claim["key"] == status.CLAIM_NOT_SERVED
        verdict = status.verdict(_runner_ok(), checkout, paused=False,
                                 last_release=claim)
        assert verdict["level"] == status.WARN
        assert verdict["key"] == "release_not_served"

    @needs_git
    def test_a_release_that_landed_and_never_loaded_keeps_its_own_headline(self, tmp_path):
        """Both readings fire here, and the checkout one names the mechanism: the
        claim comparison must not take the headline from it."""
        repo = repo_with_commits(tmp_path, messages=("first",))
        served = build_info.read_commit(repo)
        landed = _commit(repo, "the release that landed")
        checkout, claim = served_state(repo, running=served, record_sha=landed)
        assert checkout["running"]["key"] == status.RUNNING_BEHIND
        assert claim["key"] == status.CLAIM_NOT_SERVED
        verdict = status.verdict(_runner_ok(), checkout, paused=False,
                                 last_release=claim)
        assert verdict["key"] == "running_behind"

    @needs_git
    def test_it_outranks_a_dirty_checkout_but_not_a_refusal(self, tmp_path):
        repo = repo_with_commits(tmp_path, messages=("first", "second"))
        served = build_info.read_commit(repo)
        (repo / "scribble.txt").write_text("x\n", encoding="utf-8")
        checkout, claim = served_state(repo, running=served,
                                       record_sha=full_of(repo, "HEAD~1"))
        assert status.verdict(_runner_ok(), checkout, paused=False,
                              last_release=claim)["key"] == "release_not_served"
        refused = status.verdict(_runner_ok(), checkout, paused=False,
                                 preflight={"present": True, "gate_key": "smoke"},
                                 last_release=claim)
        assert refused["key"] == "refused", "a refusal is a stop, not a reading"

    @needs_git
    def test_a_matching_record_changes_nothing(self, tmp_path):
        repo = repo_with_commits(tmp_path)
        served = build_info.read_commit(repo)
        checkout, claim = served_state(repo, running=served, record_sha=full_of(repo))
        assert claim["key"] == status.CLAIM_LIVE
        assert status.verdict(_runner_ok(), checkout, paused=False,
                              last_release=claim)["key"] == "fresh"

    @needs_git
    def test_no_record_at_all_is_not_a_warning(self, tmp_path):
        """A box that has never landed a release (or whose record `unstick-deploy.sh`
        cleared) has nothing to disagree with."""
        repo = repo_with_commits(tmp_path)
        served = build_info.read_commit(repo)
        checkout = status.checkout_state(repo, now=_now(), running=served)
        claim = status.last_release_state(repo.parent / "never", repo, now=_now(),
                                          running=checkout["running"])
        assert status.verdict(_runner_ok(), checkout, paused=False,
                              last_release=claim)["key"] == "fresh"


# ── the wiring ───────────────────────────────────────────────────────────────

def template_claim_keys() -> set[str]:
    """The claim keys the template has a sentence for."""
    text = TEMPLATE.read_text(encoding="utf-8")
    return set(re.findall(r"status\.last_release\.key == '([a-z_]+)'", text))


class TestTheWiring:
    def test_every_claim_key_has_a_sentence_in_both_languages(self):
        """The template branches on these strings, so a constant renamed in Python has
        to fail here rather than render an empty row on the box."""
        assert template_claim_keys() == status.CLAIM_KEYS, (
            f"no sentence: {sorted(status.CLAIM_KEYS - template_claim_keys())}; "
            f"invented: {sorted(template_claim_keys() - status.CLAIM_KEYS)}")

    def test_the_headline_has_a_sentence(self):
        text = TEMPLATE.read_text(encoding="utf-8")
        assert "v.key == 'release_not_served'" in text, (
            "the verdict key has no sentence, so a box serving code no release "
            "recorded would fall through to 'cannot be decided'")
        assert "release_not_served" in status.REASON_KEYS, (
            "the key is not in the vocabulary every verdict key is checked against")

    def test_the_file_the_page_reads_is_the_file_the_runner_writes(self):
        """Two readers of one path, the same rule the perf card follows: the runner's
        own `STATE_DIR` names it, and the page's default has to be that same path —
        a page reading a file nobody writes would report 'no release on record' on a
        box that has been deploying for months."""
        runner = RUNNER.read_text(encoding="utf-8")
        declared = re.search(r'^STATE_DIR="([^"]+)"', runner, re.M)
        assert declared, "the runner no longer declares its state directory"
        assert status.DEFAULT_STATE_DIR == declared.group(1), (
            "the page and the runner disagree about where the box keeps its records")
        assert status.DEFAULT_LAST_RELEASE_FILE == declared.group(1) + "/last-deploy"
        writes = re.findall(
            r"printf '%s\\n%s\\n%s\\n' \"(\$\w+)\" \"\$\(date -Is\)\" \"(\$\w+)\" "
            r"> \"\$STATE_DIR/last-deploy\"", runner)
        assert sorted(v[0] for v in writes) == ["$AFTER", "$BEFORE"], (
            "the record is written on the healthy path and on the rollback path "
            f"(naming the commit each went with), found {writes}")
        assert all(v[1] == "$SNAPSHOT" for v in writes), (
            "the third line is the recovery point, which is what the page prints")

    def test_the_page_knows_the_record_can_be_absent_on_purpose(self):
        """`unstick-deploy.sh` deletes it, so `absent` is a state an operator can
        reach deliberately — which is why the page has a sentence for it rather than
        treating a missing file as an error."""
        unstick = UNSTICK.read_text(encoding="utf-8")
        assert "last-deploy" in unstick, (
            "nothing clears this record, so `absent` would be a state with no way "
            "to reach it (or unstick stopped clearing it and the page's sentence is "
            "now about a file that is always there)")

    def test_the_report_reads_the_record_by_itself(self, tmp_path, monkeypatch):
        """The route must not have to remember it: a second caller — a download, a
        future JSON view — would otherwise ship without the reading."""
        record = write_record(tmp_path / "last-deploy", "9" * 40)
        monkeypatch.setenv("SCANGRADE_LAST_RELEASE_FILE", str(record))
        report = status.report(repo="/nonexistent", runner="/nonexistent",
                               snapshot_runner="/nonexistent", pause_file="/nonexistent")
        assert report["last_release"]["sha"] == "9" * 40
        assert report["last_release"]["path"] == str(record)


# ── the page ─────────────────────────────────────────────────────────────────

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


def launcher_report(repo: Path, *, running, record_sha: str | None) -> dict:
    """The whole page's report over a checkout whose runner is this repository's own
    rendered launcher — the arrangement that is otherwise *fresh*, so the only thing
    that can move the verdict is the claim.

    `record_sha` of `None` writes no record at all, which is the state
    `unstick-deploy.sh` leaves behind.
    """
    entrypoint = (ROOT / "deploy" / "entrypoint.sh").read_text(encoding="utf-8")
    rendered = entrypoint.replace(status.PLACEHOLDER, str(repo))
    installed = repo.parent / "bin"
    installed.mkdir(exist_ok=True)
    runner = installed / "scangrade-deploy"
    runner.write_text(rendered, encoding="utf-8")
    snapshot_runner = installed / "snap"
    snapshot_runner.write_text(rendered, encoding="utf-8")
    record = repo.parent / "last-deploy"
    if record_sha is not None:
        write_record(record, record_sha,
                     snapshot="/var/lib/scangrade-deploy/snapshots/1")
    return status.report(repo=repo, runner=runner, snapshot_runner=snapshot_runner,
                         pause_file=repo.parent / "no-pause-flag", running=running,
                         last_release_file=record)


class TestThePageShowsIt:
    @needs_git
    def test_the_page_names_the_recorded_release_and_says_it_is_serving(self, app,
                                                                       tmp_path):
        repo = repo_with_commits(tmp_path, messages=("first", "the release"))
        served = build_info.read_commit(repo)
        html = render_status(app, launcher_report(repo, running=served,
                                                  record_sha=full_of(repo)))
        assert served["commit"] in html, "the recorded commit is not on the page"
        assert "the release" in html, "nor is what that commit was"
        assert "the release the box recorded is the code being served" in html
        assert "/var/lib/scangrade-deploy/snapshots/1" in html, (
            "the recovery point the runner wrote is not shown")

    @needs_git
    def test_a_served_commit_no_release_recorded_names_both(self, app, tmp_path):
        """The finding is a disagreement between two shas, so the page has to carry
        both: the one the box says it released and the one it is actually serving.

        The verdict is asserted here rather than only through `verdict()` called by
        hand, because this is the one place that proves the *report* passed the
        reading to it: a route that read the record and never handed it over would
        leave the card saying "different" under a headline saying everything is fine.
        """
        repo = repo_with_commits(tmp_path, messages=("first", "second"))
        served = build_info.read_commit(repo)
        recorded = full_of(repo, "HEAD~1")
        report = launcher_report(repo, running=served, record_sha=recorded)
        assert report["verdict"]["key"] == "release_not_served"
        html = render_status(app, report)
        assert recorded[:7] in html, "the recorded release is not named"
        assert served["commit"] in html, "the served commit is not named"
        assert "what is being served is not the release the box recorded" in html
        assert "The code answering this page is not the release this box last recorded" \
            in html, "the headline has no sentence for it"

    @needs_git
    def test_a_box_with_no_record_says_so_rather_than_showing_a_commit(self, app, tmp_path):
        """`unstick-deploy.sh` clears the record, so "nothing is on record" is a state
        an operator reaches on purpose — and it must not read as a disagreement."""
        repo = repo_with_commits(tmp_path)
        served = build_info.read_commit(repo)
        report = launcher_report(repo, running=served, record_sha=None)
        assert report["last_release"]["key"] == status.CLAIM_ABSENT
        assert report["verdict"]["key"] == "fresh", "nothing to disagree with"
        html = render_status(app, report)
        assert "no release is on record, so there is nothing to compare yet" in html
        assert "t('tidak ada','none')" in html, (
            "the record row has to say there is nothing to show rather than leave "
            "the row blank")
        assert "/var/lib/scangrade-deploy/snapshots/1" not in html, (
            "a box with no record must not print a recovery point either")
