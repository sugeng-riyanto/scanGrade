"""A box whose workers disagree is one `/health` should be able to name.

`build_info` freezes its reading at import, so on a box running three gevent
workers "the commit being served" is three answers, and `/health` is answered by
whichever worker the connection reached. Sampling is the only way that reading was
ever visible: five asks tallied by hand, or `deploy/box_report.py --samples 15`.
One worker in three answered `unreadable` for two days on this box while its
neighbours read the same checkout normally, and each individual sample looked like
a coincidence.

`app/utils/commit_readers.py` is the fix and this suite is its contract:

* a worker leaves its own reading at import, and any worker can count them — so
  **one request** names the box's blind workers and the commits its workers
  disagree on;
* a marker outlives a recycled worker, so **liveness and a process's start time**
  decide whether it is still a worker — a pid, alone, is not an identity;
* the Celery worker holds the same checkout and must **not** be counted here (it
  has its own gate and its own reload), which is why the role is recorded;
* nothing measured and nothing blind are **opposite** answers: a box with no ledger
  says `unmeasured`, never `uniform`;
* and the endpoint publishes a **projection** — the sha recall, a reason, a count —
  because `/health` is reachable without a session.

Nothing here talks to a real worker: the markers are written by hand and liveness
is injected, because the fact being tested is how the reading is *counted*, and a
suite cannot make one of its own processes blind to its own checkout.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import app as app_module  # noqa: E402
from app.utils import build_info, commit_readers  # noqa: E402

#: The app resolves its own reading through `import_safety.resolved`, and the route
#: asks for the workers of *that* checkout, so the markers here name the same
#: resolved path. A raw `str(ROOT)` can differ from the resolved one on a box with a
#: junction or a symlinked temp directory, and every marker would then be filtered
#: out as another checkout's.
REPO = str(build_info.CODE_ROOT)


def alive_for_everybody(pid, start=None):
    """Liveness for markers about processes that are not this one."""
    return True


def write_marker(directory, pid, *, commit="17665b2", available=True, reason=None,
                 role=commit_readers.HTTP_ROLE, repo=REPO, start=100, raw=None):
    """One worker's marker, exactly as `record` would leave it."""
    directory = pathlib.Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ("%d.json" % pid)
    if raw is not None:
        path.write_text(raw, encoding="utf-8")
        return path
    payload = {"pid": pid, "start": start, "role": role, "repo": repo,
               "loaded_at": "2026-10-19T09:00:00+00:00", "available": available,
               "reason_key": reason, "commit": commit if available else None}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def read_state(directory, **kwargs):
    kwargs.setdefault("repo", REPO)
    kwargs.setdefault("alive", alive_for_everybody)
    return commit_readers.state(directory=directory, **kwargs)


# ── the count ────────────────────────────────────────────────────────────────

class TestTheBoxIsCountedNotSampled:
    def test_one_blind_worker_in_three_is_a_mixed_box(self, tmp_path):
        """The reading that took two days and fifteen samples to see.

        A sample that lands on either healthy worker says the box is serving
        `17665b2`, and it is *right* — about that worker. The count is what makes
        the third one a property of the box.
        """
        d = tmp_path / "readers"
        write_marker(d, 11, commit="17665b2")
        write_marker(d, 12, commit="17665b2")
        write_marker(d, 13, available=False, reason="unreadable")
        state = read_state(d)
        assert state["key"] == commit_readers.MIXED
        assert state["seen"] == 3 and state["blind"] == 1
        assert state["commits"] == {"17665b2": 2}
        assert state["reasons"] == {"unreadable": 1}

    def test_workers_on_two_commits_are_mixed_too(self, tmp_path):
        """A reload that landed in some of them is the other half of 'mixed'.

        The workers are not blind — each names a commit — and the box is still not
        one release. This is the shape a graceful reload is caught in the middle of.
        """
        d = tmp_path / "readers"
        write_marker(d, 21, commit="17665b2")
        write_marker(d, 22, commit="17665b2")
        write_marker(d, 23, commit="8abe6fc")
        state = read_state(d)
        assert state["key"] == commit_readers.MIXED
        assert state["blind"] == 0
        assert state["commits"] == {"17665b2": 2, "8abe6fc": 1}

    def test_every_worker_naming_the_same_commit_is_uniform(self, tmp_path):
        d = tmp_path / "readers"
        for pid in (31, 32, 33):
            write_marker(d, pid, commit="17665b2")
        state = read_state(d)
        assert state["key"] == commit_readers.UNIFORM
        assert state["seen"] == 3 and state["blind"] == 0
        assert state["reasons"] == {}

    def test_a_dead_workers_marker_is_not_a_worker(self, tmp_path):
        """`max_requests` recycles workers, and a marker outlives the process.

        A dead worker's reading is not a worker: counting it would report a
        three-worker box as a four-worker one, and a blind worker that exited two
        days ago as blind today. The marker is removed as it is found, so the
        directory cannot grow for the life of the box.
        """
        d = tmp_path / "readers"
        write_marker(d, 41, commit="17665b2")
        write_marker(d, 42, available=False, reason="unreadable")
        state = commit_readers.state(directory=d, repo=REPO,
                                     alive=lambda pid, start=None: int(pid) != 42)
        assert state["seen"] == 1 and state["blind"] == 0
        assert state["key"] == commit_readers.UNIFORM
        assert not (d / "42.json").exists(), "a dead worker's marker was left behind"
        assert (d / "41.json").exists(), "a live worker's marker was removed"

    def test_a_dead_marker_is_dropped_whatever_its_role_or_checkout(self, tmp_path):
        """A retired worker's file is pruned before the filters that ignore it.

        Otherwise every process that ever started — a test run, a Celery worker, a
        checkout that was moved — leaves one file in the directory for the life of
        the box, and each of them costs a `stat` on every `/health`.
        """
        d = tmp_path / "readers"
        write_marker(d, 101, role=commit_readers.OTHER_ROLE)
        write_marker(d, 102, repo=str(tmp_path / "another-checkout"))
        write_marker(d, 103)
        state = commit_readers.state(directory=d, repo=REPO,
                                     alive=lambda pid, start=None: int(pid) == 103)
        assert state["seen"] == 1
        assert [p.name for p in d.iterdir()] == ["103.json"]

    def test_a_live_worker_whose_marker_cannot_be_read_is_skipped(self, tmp_path):
        """A file that is there and is not a marker is a worker we cannot account
        for — never a clean reading, and never a crash."""
        d = tmp_path / "readers"
        write_marker(d, 51, commit="17665b2")
        write_marker(d, 52, raw="{half a json")
        state = read_state(d)
        assert state["seen"] == 1 and state["blind"] == 0
        assert state["skipped"] == 1
        assert state["key"] == commit_readers.MIXED, (
            "a worker whose reading could not be read is not a uniform box")
        assert "could not be read" in state["detail"]

    def test_another_checkouts_workers_are_not_counted(self, tmp_path):
        """Two checkouts can share one temp directory; only one of them is this app."""
        d = tmp_path / "readers"
        write_marker(d, 61, repo=REPO)
        write_marker(d, 62, repo=str(tmp_path / "another-checkout"), available=False,
                     reason="unreadable")
        state = read_state(d)
        assert state["seen"] == 1 and state["blind"] == 0

    def test_the_celery_worker_is_not_one_of_the_apps_workers(self, tmp_path):
        """It imports the same app, holds the same checkout, and is reloaded on its
        own schedule — counting it would report a mixed box on every release while
        the app's own workers agree."""
        d = tmp_path / "readers"
        write_marker(d, 71)
        write_marker(d, 72, commit="8abe6fc", role=commit_readers.OTHER_ROLE)
        state = read_state(d)
        assert state["seen"] == 1
        assert state["commits"] == {"17665b2": 1}

    def test_expected_workers_come_from_the_gunicorn_environment(self, tmp_path,
                                                                monkeypatch):
        """`unregistered` is the other way a worker hides: it never wrote a marker,
        so its reading is unknown. Unknown is not healthy, and it is not blind
        either — the count says which."""
        d = tmp_path / "readers"
        write_marker(d, 81)
        write_marker(d, 82)
        monkeypatch.setenv(commit_readers.WORKER_COUNT_ENV, "4")
        state = read_state(d)
        assert state["expected"] == 4
        assert state["unregistered"] == 2, "two workers never recorded a reading"

    def test_an_unknown_worker_count_is_not_a_zero(self, tmp_path, monkeypatch):
        monkeypatch.delenv(commit_readers.WORKER_COUNT_ENV, raising=False)
        d = tmp_path / "readers"
        write_marker(d, 91)
        state = read_state(d)
        assert state["expected"] is None
        assert state["unregistered"] is None, "a count nobody gave was invented"

    def test_no_marker_at_all_is_unmeasured_not_uniform(self, tmp_path):
        """An empty ledger is the absence of a measurement, and the green of
        'every worker agrees' is a claim nothing supports."""
        d = tmp_path / "readers"
        d.mkdir()
        state = read_state(d)
        assert state["key"] == commit_readers.UNMEASURED
        assert state["seen"] is None and state["blind"] is None
        assert state["detail"] and str(d) in state["detail"]

    def test_nowhere_to_keep_markers_is_unmeasured_with_the_reason(self, monkeypatch):
        monkeypatch.setattr(commit_readers, "DEFAULT_DIR", "")
        monkeypatch.setenv(commit_readers.ENV_DIR, "")
        state = commit_readers.state(repo=REPO)
        assert state["key"] == commit_readers.UNMEASURED
        assert state["seen"] is None
        assert state["directory"] == ""
        assert state["detail"], "an unmeasured box must say why it could not measure"


# ── what a worker leaves behind, and what it must not ────────────────────────

class TestWhatIsRecorded:
    def test_one_marker_per_process_carrying_its_own_reading(self, tmp_path,
                                                             monkeypatch):
        monkeypatch.setenv(commit_readers.WORKER_COUNT_ENV, "3")
        target = commit_readers.record(
            {"available": True, "reason_key": None, "commit": "17665b2"},
            loaded_at="2026-10-19T09:00:00+00:00", repo=REPO, directory=tmp_path)
        assert target is not None
        assert [p.name for p in tmp_path.iterdir()] == ["%d.json" % os.getpid()]
        payload = json.loads(target.read_text(encoding="utf-8"))
        assert payload["pid"] == os.getpid()
        assert payload["role"] == commit_readers.HTTP_ROLE
        assert payload["repo"] == REPO
        assert payload["available"] is True and payload["commit"] == "17665b2"
        assert payload["loaded_at"] == "2026-10-19T09:00:00+00:00"

    def test_a_process_that_answers_no_requests_is_not_an_http_worker(self, tmp_path,
                                                                    monkeypatch):
        """`manage.py` and the Celery worker import the same app. Only a process
        gunicorn forked is one of the workers this count is about."""
        monkeypatch.delenv(commit_readers.WORKER_COUNT_ENV, raising=False)
        target = commit_readers.record({"available": True, "commit": "17665b2"},
                                       repo=REPO, directory=tmp_path)
        payload = json.loads(target.read_text(encoding="utf-8"))
        assert payload["role"] == commit_readers.OTHER_ROLE

    def test_a_blind_reading_publishes_no_commit(self, tmp_path):
        """`available: false` beside a sha is the shape that lets a blind worker
        read as a serving one."""
        target = commit_readers.record(
            {"available": False, "reason_key": "unreadable", "commit": "17665b2"},
            repo=REPO, directory=tmp_path)
        payload = json.loads(target.read_text(encoding="utf-8"))
        assert payload["available"] is False
        assert payload["commit"] is None
        assert payload["reason_key"] == "unreadable"

    def test_the_marker_carries_no_commit_message(self, tmp_path):
        """`/health` is public and publishes a sha, not a subject. The marker is on
        the box's own disk and keeps the same discipline."""
        target = commit_readers.record(
            {"available": True, "commit": "17665b2", "subject": "unreleased work",
             "detail": "a long git refusal"}, repo=REPO, directory=tmp_path)
        text = target.read_text(encoding="utf-8")
        assert "unreleased work" not in text
        assert "a long git refusal" not in text

    def test_a_place_that_cannot_be_written_is_a_return_not_a_raise(self, tmp_path):
        """This runs at import, in every process. A full disk, a read-only temp
        directory or a path that is a file must cost a count, never a worker."""
        blocked = tmp_path / "not-a-directory"
        blocked.write_text("a file where the directory should be", encoding="utf-8")
        assert commit_readers.record({"available": True, "commit": "17665b2"},
                                     repo=REPO, directory=blocked) is None
        assert blocked.is_file(), "the caller's file was replaced"

    def test_nowhere_to_write_is_a_return_not_a_raise(self, monkeypatch):
        monkeypatch.setattr(commit_readers, "DEFAULT_DIR", "")
        monkeypatch.setenv(commit_readers.ENV_DIR, "")
        assert commit_readers.record({"available": True, "commit": "x"},
                                     repo=REPO) is None

    def test_a_staged_write_that_fails_leaves_nothing_behind(self, tmp_path):
        """Markers are staged beside their target and renamed, so a reader that
        arrives mid-write sees the old file or the new one — never half of one — and
        a write that dies leaves no `.tmp` behind to be counted as an unreadable
        worker on every `/health` from then on."""
        assert commit_readers.record({"available": True, "commit": object()},
                                     repo=REPO, directory=tmp_path) is None
        assert list(tmp_path.iterdir()) == []

    def test_a_write_that_fails_is_a_return_not_a_raise(self, tmp_path, monkeypatch):
        monkeypatch.setattr(commit_readers, "_write_atomic",
                            lambda target, payload: (_ for _ in ()).throw(OSError("nope")))
        assert commit_readers.record({"available": True, "commit": "x"},
                                     repo=REPO, directory=tmp_path) is None
        assert list(tmp_path.iterdir()) == []

    def test_this_process_published_its_own_reading_at_import(self):
        """The whole count rests on the write happening at import — the moment the
        reading becomes true — and a worker that skipped it would be invisible."""
        directory = commit_readers.state_dir(None)
        assert directory is not None, commit_readers.TEMP_DIR_ERROR
        marker = pathlib.Path(directory) / ("%d.json" % os.getpid())
        assert marker.exists(), (
            "importing build_info did not leave this process's reading in %s, so "
            "/health cannot count it" % marker)
        payload = json.loads(marker.read_text(encoding="utf-8"))
        assert payload["repo"] == REPO
        assert payload["pid"] == os.getpid()


# ── the pid is not an identity ────────────────────────────────────────────────

class TestAPidIsNotAnIdentity:
    def test_a_recycled_pid_is_not_the_same_worker(self, monkeypatch):
        """Linux reuses pids. Without the start time, a marker left by a worker that
        exited an hour ago would be counted as a live worker serving its commit."""
        if os.name != "posix":
            pytest.skip("the liveness probe is POSIX-only by design; the platform "
                        "rule has its own test")
        monkeypatch.setattr(commit_readers.os, "kill", lambda pid, sig: None)
        monkeypatch.setattr(commit_readers, "_proc_start", lambda pid: 4242)
        assert commit_readers._process_alive(999, 1111) is False, (
            "a pid whose start time has changed is a different process")
        assert commit_readers._process_alive(999, 4242) is True

    def test_a_platform_without_the_probe_is_never_asked(self, monkeypatch):
        """`os.kill(pid, 0)` is not a probe on Windows — it *terminates* the
        process. The platform check therefore comes before the probe, so nothing
        can reach it there, and the answer is 'cannot say' rather than a guess."""
        def forbidden(pid, sig):  # pragma: no cover - it must never be called
            raise AssertionError("os.kill was used to probe a pid on this platform")

        monkeypatch.setattr(commit_readers.os, "name", "nt")
        monkeypatch.setattr(commit_readers.os, "kill", forbidden)
        assert commit_readers._process_alive(999) is None

    def test_this_process_is_always_alive(self):
        assert commit_readers._process_alive(os.getpid()) is True


# ── what the endpoint publishes ──────────────────────────────────────────────

class TestHealthNamesTheBox:
    def test_one_request_counts_the_workers_it_did_not_reach(self, app, tmp_path,
                                                            monkeypatch):
        """The whole point: two healthy workers and one blind one, seen once."""
        monkeypatch.setenv(commit_readers.ENV_DIR, str(tmp_path))
        monkeypatch.setattr(commit_readers, "_process_alive", alive_for_everybody)
        write_marker(tmp_path, 11, commit="17665b2")
        write_marker(tmp_path, 12, commit="17665b2")
        write_marker(tmp_path, 13, available=False, reason="unreadable")

        response = app.test_client().get("/health")
        body = response.get_json()
        assert response.status_code == 200
        readers = body["commit_readers"]
        assert readers["key"] == "mixed"
        assert readers["seen"] == 3
        assert readers["blind"] == 1
        assert readers["reasons"] == {"unreadable": 1}
        assert readers["commits"] == {"17665b2": 2}

    def test_a_mixed_box_keeps_serving(self, app, tmp_path, monkeypatch):
        """A blind worker needs a restart, not an outage. Taking the box out of
        rotation for a bookkeeping fault is a worse failure than the one it reports,
        and the deploy's own probe reads this status."""
        monkeypatch.setenv(commit_readers.ENV_DIR, str(tmp_path))
        monkeypatch.setattr(commit_readers, "_process_alive", alive_for_everybody)
        write_marker(tmp_path, 21, available=False, reason="unreadable")
        body = app.test_client().get("/health").get_json()
        assert body["status"] == "ok"
        assert body["commit_readers"]["key"] == "mixed"
        assert body["commit"], "the commit block the deploy compares must survive"

    def test_an_empty_ledger_is_unmeasured_not_healthy(self, app, tmp_path,
                                                       monkeypatch):
        monkeypatch.setenv(commit_readers.ENV_DIR, str(tmp_path / "never-written"))
        readers = app.test_client().get("/health").get_json()["commit_readers"]
        assert readers["key"] == "unmeasured"
        assert readers["seen"] is None
        assert readers["detail"]

    def test_the_endpoint_publishes_a_projection(self, app):
        """`/health` is reachable without a session, so the block is an explicit
        projection: a count and a sha, not the marker directory or a git refusal."""
        readers = app.test_client().get("/health").get_json()["commit_readers"]
        assert set(readers) == set(app_module.COMMIT_READERS_FIELDS)
        assert "directory" not in readers

    def test_a_reading_that_raises_is_not_a_500(self, app, monkeypatch):
        def explode(*args, **kwargs):
            raise RuntimeError("the ledger is on fire")

        monkeypatch.setattr(commit_readers, "state", explode)
        response = app.test_client().get("/health")
        assert response.status_code == 200
        readers = response.get_json()["commit_readers"]
        assert readers["key"] == "unmeasured"
        assert "RuntimeError" in readers["detail"]
