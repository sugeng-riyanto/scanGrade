"""The status page says whether this box is still sharing state — or only looks like it.

`/super-admin/deploy-status` exists to answer the questions an operator cannot
answer without a shell, and the shared store is now one of them: the draft lock
falls back to a single worker when Redis cannot be reached, and every page on the
site keeps answering normally while it does. The fallback's only trace used to be
a `logger.warning`.

What these tests hold about the page:

* **Every state has its own sentence, in both languages.** A shared store, a
  recorded fallback, an unreachable store, an unconfigured one and an un-askable
  one are five different findings with different remedies, and rendering one in
  another's words is worse than rendering none — it tells an operator the box is
  sharing state when it is not.
* **The reading is fresh, not cached behind the deploy report.** The report is
  cached for half a minute because it runs a dozen `git` calls; a thirty-second-old
  "the store answers" is exactly the wrong answer right after a restart, while the
  lock reading is one ping plus a counter.
* **The counts are this worker's own, and the marker is what another worker left.**
  A reader has to be able to tell the two apart.

The `locks` dicts below are the shape `app/utils/lock_health.state()` returns.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.services import deploy_status_service as status  # noqa: E402
from app.utils import lock_health  # noqa: E402

TEMPLATE = ROOT / "app" / "templates" / "super_admin" / "deploy_status.html"
ROUTE = ROOT / "app" / "routes" / "super_admin.py"


def reading(key: str, **over) -> dict:
    """A `lock_health.state()` reading, in the shape the card reads."""
    out = {
        "key": key,
        "measured_at": "2026-09-26T03:00:00+00:00",
        "reachable": key in (lock_health.SHARED, lock_health.RECORDED),
        "version": "6.0.16" if key in (lock_health.SHARED, lock_health.RECORDED) else None,
        "probe_error": None,
        "recorded": key in (lock_health.RECORDED, lock_health.UNREACHABLE),
        "worker": "4242",
        "state_file": "/tmp/scangrade-lock-fallback.json",
        "marker": {"present": False, "key": "absent", "at": None, "reason": None,
                   "worker": None, "age_seconds": None},
        "worker_ok": 12,
        "worker_fallbacks": 0,
        "worker_first_at": None,
        "worker_last_at": None,
        "worker_reason": None,
        "worker_cleared": 0,
        "worker_cleared_at": None,
        "worker_started_at": "2026-09-26T02:00:00+00:00",
    }
    out.update(over)
    return out


def report_for(tmp_path) -> dict:
    """A real report over paths that do not exist: the page's own shape, no git."""
    return status.report(repo=str(tmp_path), runner="/nonexistent",
                         snapshot_runner="/nonexistent",
                         pause_file=str(tmp_path / "no-pause"),
                         perf_history_file=str(tmp_path / "absent.jsonl"))


def render(app, locks: dict, tmp_path) -> str:
    """The page with a given lock reading, for the tests that read its copy."""
    from flask import g, render_template
    with app.test_request_context("/super-admin/deploy-status"):
        g.user_id = "a-super-admin"
        g.user_role = "super_admin"
        g.user_name = "Tester"
        g.user_email = "t@t"
        g.tz_offset = 7
        return render_template("super_admin/deploy_status.html",
                               status=report_for(tmp_path),
                               alerts={"interval_seconds": 21600}, locks=locks,
                               testalert=None, released=None)


# ── every state has its own words ────────────────────────────────────────────

class TestEveryStateHasItsOwnSentence:

    #: The key, and a phrase only that state's sentence uses.
    SENTENCES = {
        lock_health.SHARED: "nothing is on record",
        lock_health.RECORDED: "answers now",
        lock_health.UNREACHABLE: "does not answer",
        lock_health.UNCONFIGURED: "No shared store is configured",
        lock_health.UNKNOWN: "could not be asked",
    }

    @pytest.mark.parametrize("key,phrase", sorted(SENTENCES.items()))
    def test_the_state_renders_the_sentence_for_that_state(self, app, tmp_path, key, phrase):
        html = render(app, reading(key), tmp_path)

        assert phrase in html, f"{key!r} rendered without its own sentence"

    def test_a_fallback_is_not_rendered_in_the_words_of_a_healthy_store(self, app, tmp_path):
        """The one mix-up this card exists to prevent."""
        healthy = render(app, reading(lock_health.SHARED), tmp_path)
        fallback = render(app, reading(lock_health.RECORDED), tmp_path)

        assert "nothing is on record" in healthy
        assert "nothing is on record" not in fallback, (
            "a recorded fallback was rendered in the shared store's words, so the "
            "page told an operator the box is sharing state when it is not")

    def test_every_key_the_record_can_hold_has_a_branch_here(self):
        """A key with no branch falls through to the 'unknown' wording in silence."""
        keys = set(re.findall(r"LK\.key == '([a-z_]+)'",
                              TEMPLATE.read_text(encoding="utf-8")))

        assert keys == {lock_health.SHARED, lock_health.RECORDED,
                        lock_health.UNREACHABLE, lock_health.UNCONFIGURED}, (
            f"a state the record can hold has no sentence of its own: {sorted(keys)}")


# ── the numbers ──────────────────────────────────────────────────────────────

class TestWhatTheNumbersSay:

    def test_the_two_counts_are_shown_apart(self, app, tmp_path):
        html = render(app, reading(lock_health.RECORDED, worker_ok=12,
                                   worker_fallbacks=7), tmp_path)

        assert ">12<" in html and ">7<" in html, (
            "the page cannot show how many locks went to the store and how many did "
            "not, which is the whole reading")

    def test_the_reason_the_store_failed_is_shown(self, app, tmp_path):
        html = render(app, reading(lock_health.UNREACHABLE,
                                   worker_reason="RemoteProtocolError: Server disconnected"),
                      tmp_path)

        assert "Server disconnected" in html

    def test_the_window_of_the_outage_is_shown(self, app, tmp_path):
        html = render(app, reading(lock_health.RECORDED,
                                   worker_first_at="2026-09-26T02:10:00+00:00",
                                   worker_last_at="2026-09-26T02:59:00+00:00"),
                      tmp_path)

        assert "2026-09-26T02:10:00+00:00" in html
        assert "2026-09-26T02:59:00+00:00" in html, (
            "'it fell back' without 'and it still is' is the difference between a "
            "blip and an outage")

    def test_a_quiet_worker_does_not_show_an_outage_window(self, app, tmp_path):
        html = render(app, reading(lock_health.SHARED), tmp_path)

        assert "First fallback (this worker)" not in html, (
            "a store with nothing on record showed outage rows")


# ── the marker, and the worker it came from ──────────────────────────────────

class TestTheMarkerOnTheAppliance:

    def test_a_marker_written_by_another_worker_is_shown_with_its_writer(self, app, tmp_path):
        html = render(app, reading(lock_health.RECORDED, worker_fallbacks=0, marker={
            "present": True, "key": "ok", "at": "2026-09-26T02:10:00+00:00",
            "reason": "other worker", "worker": "111", "age_seconds": 600}), tmp_path)

        assert "111" in html and "2026-09-26T02:10:00+00:00" in html
        assert "600" in html, "the age is what makes the marker actionable"
        assert "other worker" in html, (
            "the marker's reason is the *first* cause of the outage, which is not the "
            "same fact as the reason the store is failing now")

    def test_an_unreadable_marker_is_not_rendered_as_absent(self, app, tmp_path):
        html = render(app, reading(lock_health.SHARED, marker={
            "present": True, "key": "unreadable", "at": None,
            "reason": "Permission denied", "worker": None, "age_seconds": None}),
            tmp_path)

        assert "Present and unreadable" in html, (
            "a marker that cannot be read was rendered as 'None', which is a clean "
            "bill for a box whose record is broken")

    def test_a_marker_that_is_not_a_record_is_reported(self, app, tmp_path):
        html = render(app, reading(lock_health.SHARED, marker={
            "present": True, "key": "malformed", "at": None,
            "reason": "Expecting value: line 1", "worker": None,
            "age_seconds": None}), tmp_path)

        assert "Present and not a record" in html

    def test_a_probe_error_is_shown_next_to_the_state(self, app, tmp_path):
        """'Not answering' with no reason is not actionable; the probe knows why."""
        html = render(app, reading(lock_health.UNREACHABLE, probe_error="ping_failed"),
                      tmp_path)

        assert "ping_failed" in html

    def test_the_path_is_shown_so_it_can_be_looked_at_by_hand(self, app, tmp_path):
        html = render(app, reading(lock_health.SHARED), tmp_path)

        assert "/tmp/scangrade-lock-fallback.json" in html


# ── the reading has to be fresh, and the page has to survive it being absent ──

class TestHowTheRouteFeedsIt:

    def test_the_route_reads_the_record_freshly(self):
        """Not inside the cached report: the report is thirty seconds of git calls.

        A cached "the store answers" is exactly the wrong thing to show right after
        a restart, and the lock reading costs one ping plus a counter.
        """
        source = ROUTE.read_text(encoding="utf-8")
        body = source[source.index("def deploy_status():"):]
        body = body[:body.index("@super_bp.route", 1)]

        assert "locks=lock_health.state()" in body, (
            "the page no longer reads the lock record")
        assert "ttl(" not in body.split("locks=lock_health.state()")[1], (
            "the lock reading was put behind the report's cache")

    def test_the_page_renders_when_the_reading_is_missing(self, app, tmp_path):
        """Undefined must not be a 500: an older route, or a template rendered alone."""
        from flask import g, render_template
        with app.test_request_context("/super-admin/deploy-status"):
            g.user_id = "x"
            g.user_role = "super_admin"
            g.user_name = "T"
            g.user_email = "t@t"
            g.tz_offset = 7
            html = render_template("super_admin/deploy_status.html",
                                   status=report_for(tmp_path),
                                   alerts={}, testalert=None, released=None)

        assert "could not be asked" in html, (
            "a missing reading rendered as something other than 'unknown'")
