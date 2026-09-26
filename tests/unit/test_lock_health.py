"""Whether the shared store is actually sharing anything, held to what a page needs.

The draft lock (`app/routes/api.py`) rides one pooled Redis connection, and it falls
back to a table in the worker when that connection cannot be reached. The fallback
is the right trade — refusing to store a paper is the one outcome a cache outage
must never produce here — but until now the *only* trace was a `logger.warning`:
the site served normally, `/api/student/sync-draft` still answered `saved: true`,
and the fact that three gevent workers were no longer serialising against each
other existed nowhere a human looks.

Two things have to be true of the record that replaces it:

* **It has to survive the worker that saw it.** This app runs three gunicorn
  workers, so a per-process counter answers only when the page happens to be served
  by the worker that fell back — the same per-worker invisibility the sync throttle
  had, and the page's whole purpose is to be readable without a shell. So a
  fallback also leaves a marker file on the appliance, written once per worker per
  outage (never per lock — the sync path is hot), and the first successful shared
  lock deletes it.
* **It must never be able to break the sync path.** No state, an unreadable marker,
  a full disk, a read-only temp directory: every one of them means "nothing
  recorded", never an exception from inside a student's save.

And one property that is a deployment hazard rather than a nicety: the app must
not write inside its own checkout, because the deploy runner refuses a dirty one.
"""

from __future__ import annotations

import json
import os
import pathlib
from datetime import datetime

import pytest

from app.utils import lock_health

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: The same clock the tests move by hand: `state(now=...)` and `record_*` are all
#: wall-clock seconds, so an age can be asserted without sleeping.
T0 = 1_700_000_000.0


@pytest.fixture(autouse=True)
def _a_state_file_of_our_own(tmp_path, monkeypatch):
    """Never the appliance's own marker, and never a leftover from another test."""
    mark = tmp_path / "lock-fallback.json"
    monkeypatch.setenv("SCANGRADE_LOCK_STATE_FILE", str(mark))
    lock_health.reset()
    yield mark
    lock_health.reset()


def probed(*, connected=True, version="6.0.16", error=None):
    """A `get_redis_status()` answer, in its own shape."""
    out: dict = {"connected": connected}
    if version is not None:
        out["version"] = version
    if error is not None:
        out["error"] = error
    return out


def reading(path=None, *, probe=None, now=T0):
    return lock_health.state(path=path, probe=probe or (lambda: probed()), now=now)


# ── 1. what the worker counted ───────────────────────────────────────────────

class TestWhatTheWorkerSaw:

    def test_a_fallback_is_counted_and_its_first_and_last_are_kept(self):
        lock_health.record_fallback("RemoteProtocolError: Server disconnected")
        lock_health.record_fallback("TimeoutError: timed out")
        state = reading()

        assert state["worker_fallbacks"] == 2
        assert state["worker_first_at"] is not None
        assert state["worker_last_at"] is not None

    def test_shared_locks_are_counted_apart_from_fallbacks(self):
        """Not one counter: "how often did it work" is the other half of the answer."""
        lock_health.record_shared()
        lock_health.record_shared()
        lock_health.record_fallback("boom")
        lock_health.record_shared()

        state = reading()
        assert state["worker_ok"] == 3
        assert state["worker_fallbacks"] == 1

    def test_the_reason_kept_is_the_last_one(self):
        lock_health.record_fallback("first reason")
        lock_health.record_fallback("the reason it is still failing now")

        assert reading()["worker_reason"] == "the reason it is still failing now", (
            "the page has to show the reason the store is failing *now*, not the "
            "first one from hours ago")

    def test_a_huge_reason_is_trimmed(self):
        """An exception string can be a page of HTML; the card is not."""
        lock_health.record_fallback("x" * 5000)

        reason = reading()["worker_reason"]
        assert len(reason) <= 200 and reason.startswith("x"), len(reason)

    def test_the_reading_says_which_worker_answered(self):
        state = reading()

        assert state["worker"], "a reading with no worker cannot be attributed"
        assert str(state["worker"]) not in ("", "0")

    def test_reset_clears_the_counters(self):
        lock_health.record_fallback("boom")
        lock_health.reset()

        state = reading()
        assert state["worker_fallbacks"] == 0 and state["worker_last_at"] is None


# ── 2. the marker that outlives the worker ───────────────────────────────────

class TestTheMarker:

    def test_a_fallback_leaves_a_marker_a_healthy_worker_can_read(self, _a_state_file_of_our_own):
        lock_health.record_fallback("RemoteProtocolError: Server disconnected")

        written = json.loads(_a_state_file_of_our_own.read_text(encoding="utf-8"))
        assert written["reason"] == "RemoteProtocolError: Server disconnected"
        assert written["worker"]

        # The page, read as if by a *different* worker whose own counters are zero.
        lock_health.reset()
        state = reading()
        assert state["worker_fallbacks"] == 0, "this is the other worker's own count"
        assert state["marker"]["present"] is True
        assert state["marker"]["reason"] == "RemoteProtocolError: Server disconnected"

    def test_a_healthy_lock_writes_nothing_at_all(self, _a_state_file_of_our_own):
        lock_health.record_shared()
        lock_health.record_shared()

        assert not _a_state_file_of_our_own.exists(), (
            "the healthy path must not touch the filesystem — it runs on every sync")

    def test_the_marker_is_written_once_per_outage_not_per_lock(self, _a_state_file_of_our_own):
        """The sync path is hot: one write per worker per outage, not one per lock."""
        lock_health.record_fallback("first")
        first = _a_state_file_of_our_own.read_text(encoding="utf-8")
        for _ in range(50):
            lock_health.record_fallback("first")

        assert _a_state_file_of_our_own.read_text(encoding="utf-8") == first, (
            "the marker was rewritten on every fallback")
        assert reading()["worker_fallbacks"] == 51

    def test_a_marker_from_another_worker_is_not_overwritten(self, _a_state_file_of_our_own):
        """First writer wins: the *earliest* fallback of the outage is the evidence."""
        _a_state_file_of_our_own.write_text(json.dumps(
            {"at": "2026-09-26T02:00:00+00:00", "reason": "written by worker 1",
             "worker": "111"}), encoding="utf-8")

        lock_health.record_fallback("this worker's own reason")

        written = json.loads(_a_state_file_of_our_own.read_text(encoding="utf-8"))
        assert written["reason"] == "written by worker 1", (
            "a later worker's write buried the first record of the outage")

    def test_a_shared_lock_clears_the_marker(self, _a_state_file_of_our_own):
        lock_health.record_fallback("boom")
        assert _a_state_file_of_our_own.exists()

        lock_health.record_shared()

        assert not _a_state_file_of_our_own.exists(), (
            "the store is back, so the page must stop reporting an outage")
        assert reading()["worker_cleared"] == 1

    def test_the_marker_carries_its_own_age(self, _a_state_file_of_our_own):
        stamp = "2026-09-26T02:00:00+00:00"
        _a_state_file_of_our_own.write_text(json.dumps(
            {"at": stamp, "reason": "written by worker 1", "worker": "111"}),
            encoding="utf-8")
        at = datetime.fromisoformat(stamp).timestamp()

        state = lock_health.state(probe=lambda: probed(), now=at + 600)
        assert state["marker"]["at"] == stamp
        assert state["marker"]["age_seconds"] == pytest.approx(600, abs=1), (
            "an age is what turns 'it fell back' into 'it has been falling back for "
            "ten minutes'")


# ── 3. a record that cannot be read is not a clean bill ──────────────────────

class TestAnUnreadableRecord:

    def test_absent_is_not_unreadable(self, tmp_path):
        state = reading(tmp_path / "never-written.json")

        assert state["marker"]["present"] is False
        assert state["marker"]["key"] == "absent", (
            "'no outage has been recorded' and 'there is a record and this page "
            "cannot read it' have different remedies")

    def test_a_path_that_cannot_be_read_is_reported(self, tmp_path):
        directory = tmp_path / "a-directory"
        directory.mkdir()

        state = reading(directory)

        assert state["marker"]["key"] == "unreadable"
        assert state["marker"]["present"] is True, (
            "something is there; saying 'absent' claims the box is clean")

    def test_a_marker_that_is_not_json_is_reported(self, tmp_path):
        broken = tmp_path / "broken.json"
        broken.write_text("not json at all", encoding="utf-8")

        state = reading(broken)

        assert state["marker"]["key"] == "malformed"
        assert "not json at all" not in (state["marker"].get("reason") or ""), (
            "the page shows a reason, not an arbitrary file's contents")

    @pytest.mark.skipif(os.name == "nt", reason="Windows answers ENOTDIR with FileNotFoundError, "
                                              "so 'absent' and 'a file is in the way' are the "
                                              "same error there; production is not Windows")
    def test_a_path_component_that_is_a_file_is_not_absent(self, tmp_path):
        """The parent being a regular file is `NotADirectoryError`, not 'no record'."""
        blocker = tmp_path / "not-a-directory"
        blocker.write_text("", encoding="utf-8")

        state = reading(blocker / "marker.json")

        assert state["marker"]["key"] == "unreadable"
        assert state["marker"]["present"] is True

    def test_a_marker_missing_its_pieces_is_still_readable(self, tmp_path):
        """A newer writer may add fields; an older one may lack them."""
        thin = tmp_path / "thin.json"
        thin.write_text(json.dumps({"at": "2026-09-26T02:00:00+00:00"}), encoding="utf-8")

        state = reading(thin)

        assert state["marker"]["key"] == "ok"
        assert state["marker"]["reason"] is None


# ── 4. the key the page headlines ────────────────────────────────────────────

class TestWhatItAddsUpTo:

    def test_a_store_that_answers_with_nothing_on_record_is_shared(self):
        state = reading()

        assert state["key"] == lock_health.SHARED
        assert state["reachable"] is True
        assert state["version"] == "6.0.16"

    def test_a_store_that_is_not_configured_is_its_own_state(self):
        """On a production box this is a misconfiguration, not a dev convenience."""
        state = reading(probe=lambda: probed(connected=False, version=None,
                                             error="not_configured"))

        assert state["key"] == lock_health.UNCONFIGURED

    def test_a_store_that_does_not_answer_is_unreachable(self):
        state = reading(probe=lambda: probed(connected=False, version=None,
                                             error="ping_failed"))

        assert state["key"] == lock_health.UNREACHABLE

    def test_a_store_that_answers_after_a_fallback_is_recorded(self):
        """The amber case: it works now, and it did not — that is the whole warning."""
        lock_health.record_fallback("boom")

        state = reading()
        assert state["key"] == lock_health.RECORDED
        assert state["reachable"] is True
        assert state["worker_fallbacks"] == 1

    def test_a_worker_that_only_saw_a_marker_reads_as_recorded_too(self, _a_state_file_of_our_own):
        _a_state_file_of_our_own.write_text(json.dumps(
            {"at": "2026-09-26T02:00:00+00:00", "reason": "other worker", "worker": "9"}),
            encoding="utf-8")

        state = reading()

        assert state["key"] == lock_health.RECORDED
        assert state["worker_fallbacks"] == 0, "this worker's own count stays its own"

    def test_a_probe_that_raises_is_unknown_not_shared(self):
        """The failure of the *check* must never read as a healthy store."""
        def explode():
            raise RuntimeError("the probe itself is broken")

        state = reading(probe=explode)

        assert state["key"] == lock_health.UNKNOWN
        assert state["reachable"] is None

    def test_the_reading_carries_its_own_instant(self):
        assert reading(now=T0)["measured_at"], "a reading without a time cannot be aged"


# ── 5. it cannot break the sync path, and it cannot dirty the checkout ───────

class TestItCannotBreakAnything:

    def test_a_state_file_that_cannot_be_written_still_counts_and_never_raises(
            self, tmp_path, monkeypatch):
        """A full disk or a read-only /tmp must not reach the student's save.

        The parent is a *file*, so neither the directory nor the marker can be
        created: the one shape that stays unwritable however the test runs as root.
        """
        blocker = tmp_path / "not-a-directory"
        blocker.write_text("", encoding="utf-8")
        monkeypatch.setenv("SCANGRADE_LOCK_STATE_FILE", str(blocker / "marker.json"))

        lock_health.record_fallback("boom")

        assert reading()["worker_fallbacks"] == 1, (
            "a state file that cannot be written lost the count as well")


    def test_the_state_file_never_lives_inside_the_checkout(self):
        """The deploy runner refuses a dirty checkout; this app must not dirty it.

        The marker is written by the app process, so a default path under the repo
        would leave an untracked file behind on the box that deploys it.
        """
        default = pathlib.Path(lock_health.DEFAULT_STATE_FILE).resolve()

        assert ROOT not in default.parents and default != ROOT, (
            f"the lock marker would be written into the checkout at {default}")
