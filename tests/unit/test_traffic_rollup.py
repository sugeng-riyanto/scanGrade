"""The traffic meter: what it counts, what it refuses to count, and what it loses.

The properties pinned here are the ones a dashboard's credibility rests on, and
each of them is a place this could have been built the wrong way:

* **one row per key per batch.** The buffer is a dict keyed by
  (period, group, school), so two requests to the same page in the same hour in the
  same school are one row with `requests: 2`. That is not a detail: the SQL that
  receives the batch *refuses the whole statement* when one key appears twice, so a
  list-shaped buffer would not be slower, it would be a silent loss of every write.
* **a flush that fails loses, and does not double.** The buffer is emptied *before*
  the RPC. Losing one minute of aggregate statistics is a small error; counting each
  request twice is a wrong number forever.
* **the log is parsed, not guessed at.** A malformed line is counted as skipped and
  no row is invented from it, because an aggregate nobody can trust is worse than an
  aggregate with a hole in it.
* **a session count is the only number that comes from Redis**, and when Redis
  cannot be read it is *absent* rather than zero — a zero there reads as "nobody
  used the system today", the one answer this reading must never make up.
"""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

from app.services import traffic_rollup_service as traffic


class RpcRecorder:
    """A Supabase stand-in that remembers the RPCs it was asked to make."""

    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def rpc(self, name, params=None):
        self.calls.append((name, params))
        if self.fail:
            raise RuntimeError("database is having a bad afternoon")

        class _Done:
            data = 1
        done = _Done()
        done.execute = lambda: done
        return done


def _reset():
    traffic._HOUR.clear()
    traffic._DAY.clear()
    traffic._SESSION_DAYS.clear()
    traffic._SESSION_DAY_ORDER.clear()


@pytest.fixture(autouse=True)
def _clean_buffers():
    _reset()
    yield
    _reset()


# ── the pure readings ───────────────────────────────────────────────────────

class TestTheClassification:
    def test_a_path_lands_in_the_group_it_belongs_to(self):
        assert traffic.route_group("/student/dashboard") == "/student"
        assert traffic.route_group("/api/scan/save") == "/api/scan"
        assert traffic.route_group("/health") == "/health"

    def test_the_specific_group_wins_over_its_prefix(self):
        """`/api/scan` is checked before `/api`, so scanning is not folded into API.

        This is the whole reason the table is an ordered list rather than a dict:
        the specific group is a *prefix* of the general one, and a lookup that
        picked by key length would still be right by accident here and wrong for
        the next prefix added.
        """
        assert traffic.route_group("/api/scan") == "/api/scan"
        assert traffic.route_group("/api/zzz") == "/api"

    def test_an_unknown_path_is_a_named_group_and_not_a_dropped_request(self):
        assert traffic.route_group("/zzz") == traffic.OTHER_GROUP

    def test_the_device_split_is_coarse_and_never_a_fingerprint(self):
        assert traffic.device_class("Mozilla/5.0 (Linux; Android 13) Mobile") == "mobile"
        assert traffic.device_class("Mozilla/5.0 (Windows NT 10.0)") == "desktop"
        # A tool is `other`, not `desktop`: folding it in would inflate the number
        # the page uses to say "most of our pupils are on phones".
        assert traffic.device_class("curl/8.0") == "other"
        assert traffic.device_class("") == "other"

    def test_every_status_code_lands_in_one_of_four_classes(self):
        assert traffic.status_class(204) == "status_2xx"
        assert traffic.status_class(302) == "status_3xx"
        assert traffic.status_class(404) == "status_4xx"
        assert traffic.status_class(503) == "status_5xx"
        # 1xx and anything a proxy invented: the four classes must add up to the
        # request count, so there is no fifth bucket to hide in.
        assert traffic.status_class(100) == "status_5xx"
        assert traffic.status_class("nonsense") == "status_5xx"


class TestThePercentiles:
    def test_the_percentile_is_the_upper_bound_of_its_bucket(self):
        counts = [0] * traffic.DURATION_BUCKET_COUNT
        counts[traffic.bucket_index(80)] = 95     # 95 fast requests
        counts[traffic.bucket_index(400)] = 5     # 5 slow ones
        assert traffic.percentile_from_buckets(counts, 0.50) == 100
        assert traffic.percentile_from_buckets(counts, 0.95) == 100
        # The 99th is in the slow tail, and the answer is that bucket's bound.
        assert traffic.percentile_from_buckets(counts, 0.99) == 500

    def test_an_empty_histogram_has_no_percentile(self):
        assert traffic.percentile_from_buckets([0] * 4, 0.5) == 0.0
        assert traffic.percentile_from_buckets([], 0.95) == 0.0

    def test_a_value_equal_to_a_bound_is_in_that_bounds_bucket(self):
        """The off-by-one this module was written with, and what it cost.

        `bisect_right` files a value equal to a bound in the *next* bucket, so a
        5000 ms request was reported as 7500 and every percentile landing on a round
        number read one step slow — a dashboard that is consistently pessimistic by
        one bucket, with nothing to notice. `bisect_left` is the rule: bucket `i`
        means "at or below `DURATION_BOUNDS_MS[i]`".
        """
        assert traffic.bucket_index(5000) == traffic.DURATION_BOUNDS_MS.index(5000)
        assert traffic.bucket_index(101) == traffic.DURATION_BOUNDS_MS.index(150)

    def test_a_very_slow_request_lands_in_the_open_ended_bucket(self):
        """Above the last bound must still be counted somewhere, not dropped."""
        assert traffic.bucket_index(4 * 60 * 60 * 1000) == traffic.DURATION_BUCKET_COUNT - 1
        assert traffic.bucket_index(4 * 60 * 60 * 1000) != traffic.DURATION_BUCKET_COUNT

    def test_combining_rows_sums_the_histograms_first(self):
        """A percentile of percentiles understates the tail; this is why it is summed."""
        fast = {"duration_bucket_counts": [90] + [0] * (traffic.DURATION_BUCKET_COUNT - 1)}
        slow = {"duration_bucket_counts": [0] * traffic.DURATION_BUCKET_COUNT}
        slow["duration_bucket_counts"][traffic.bucket_index(5000)] = 10
        series = traffic.percentile_series([fast, slow])
        assert series["p50"] == 5           # the 50th of 100 is still the fast bucket
        assert series["p95"] == 5000        # ...and the 95th is in the slow half


# ── the buffer ──────────────────────────────────────────────────────────────

class TestTheAccumulator:
    def test_two_requests_on_one_route_are_one_row(self):
        traffic.note_request("/student/dashboard", 200, 30, "curl", "school-a")
        traffic.note_request("/student/dashboard", 500, 90, "curl", "school-a")
        recorder = RpcRecorder()
        outcome = traffic.flush(supabase=recorder)
        # One hourly row and one daily row, from two requests: the buffer is keyed
        # by (period, group, school), not appended to per request.
        assert outcome["rows"] == 2
        rows = list(_rows_of(recorder))[0]
        hourly = [r for r in rows if r["grain"] == "hour"]
        assert len(hourly) == 1
        assert hourly[0]["requests"] == 2
        assert hourly[0]["status_2xx"] == 1 and hourly[0]["status_5xx"] == 1

    def test_the_batch_carries_one_row_per_key_for_both_grains(self):
        traffic.note_request("/student/dashboard", 200, 30, "curl", "school-a")
        traffic.note_request("/student/dashboard", 200, 40, "curl", "school-a")
        traffic.note_request("/teacher/exams", 200, 10, "curl", "school-a")
        recorder = RpcRecorder()
        traffic.flush(supabase=recorder)
        name, params = recorder.calls[0]
        assert name == "traffic_rollup_add"
        keys = [(row["grain"], row["period_start"], row["route_group"], row["school_id"])
                for row in params["p_rows"]]
        assert len(keys) == len(set(keys)), "the SQL refuses a batch with a repeated key"
        hourly = [r for r in params["p_rows"] if r["grain"] == "hour"]
        student = [r for r in hourly if r["route_group"] == "/student"][0]
        assert student["requests"] == 2
        assert student["status_2xx"] == 2
        assert sum(student["duration_bucket_counts"]) == 2

    def test_the_counts_add_up_to_the_requests(self):
        traffic.note_request("/a", 200, 1, "curl")
        traffic.note_request("/a", 301, 2, "curl")
        traffic.note_request("/a", 404, 3, "curl")
        traffic.note_request("/a", 500, 4, "curl")
        recorder = RpcRecorder()
        traffic.flush(supabase=recorder)
        row = [r for r in recorder.calls[0][1]["p_rows"]
               if r["grain"] == "hour"][0]
        assert row["requests"] == 4
        assert (row["status_2xx"] + row["status_3xx"] + row["status_4xx"]
                + row["status_5xx"]) == row["requests"]
        assert (row["device_mobile"] + row["device_desktop"] + row["device_other"]
                ) == row["requests"]

    def test_the_buffer_is_empty_before_the_rpc_is_made(self):
        """The load-bearing ordering: a failed send loses a minute, never doubles it.

        A `flush` that sent first and cleared afterwards would, on a retry, write the
        same requests twice — and `traffic_rollup_add` *adds*, so the dashboard would
        report twice the traffic of a box that had one bad minute.
        """
        observed = {}

        class Observer(RpcRecorder):
            def rpc(self, name, params=None):
                observed["hour"] = dict(traffic._HOUR)
                observed["day"] = dict(traffic._DAY)
                return super().rpc(name, params)

        traffic.note_request("/student/dashboard", 200, 30, "curl", None)
        traffic.flush(supabase=Observer())
        assert observed["hour"] == {}, "the buffer must be cleared before the send"
        assert observed["day"] == {}

    def test_a_failed_flush_loses_the_batch_and_says_so(self):
        traffic.note_request("/student/dashboard", 200, 30, "curl", None)
        outcome = traffic.flush(supabase=RpcRecorder(fail=True))
        assert outcome["sent"] is False
        assert outcome["error"]
        assert traffic._HOUR == {}, "the failed batch is gone, not retried"
        # And the next flush is not a duplicate of it.
        again = traffic.flush(supabase=RpcRecorder())
        assert again["rows"] == 0

    def test_an_empty_buffer_makes_no_call_at_all(self):
        recorder = RpcRecorder()
        outcome = traffic.flush(supabase=recorder)
        assert recorder.calls == []
        assert outcome["sent"] is True and outcome["rows"] == 0

    def test_a_request_counts_once_per_route_and_the_school_is_attributed(self):
        traffic.note_request("/student/dashboard", 200, 30, "curl", "school-a")
        traffic.note_request("/student/dashboard", 200, 30, "curl", None)
        recorder = RpcRecorder()
        traffic.flush(supabase=recorder)
        rows = [r for r in recorder.calls[0][1]["p_rows"] if r["grain"] == "hour"]
        assert sorted(r["school_id"] for r in rows) == ["", "school-a"], (
            "a request with no session school is its own row, not merged into one")


def _rows_of(recorder):
    for call in recorder.calls:
        if call[0] == "traffic_rollup_add":
            yield call[1]["p_rows"]


class TestTheSessionCount:
    def test_no_redis_means_no_session_row_rather_than_a_zero(self, monkeypatch):
        """The one number that must never be invented.

        A zero here reads as "nobody used the system today". `SCARD` is absent when
        Redis is absent, and the batch simply carries no session row — the page then
        has nothing to show, which is the truth.
        """
        monkeypatch.setattr(traffic, "_session_rows", lambda: [])
        traffic.note_request("/student/dashboard", 200, 30, "curl", None)
        recorder = RpcRecorder()
        traffic.flush(supabase=recorder)
        assert [name for name, _ in recorder.calls] == ["traffic_rollup_add"]


# ── the existing raw source ─────────────────────────────────────────────────

LOG_LINE = ('10.0.0.1 - - [14/Oct/2026:22:28:37 +0000] "GET /student/dashboard HTTP/1.1" '
            '200 15234 "-" "Mozilla/5.0 (Linux; Android 13; SM-A536E) AppleWebKit/537.36" 12345')


class TestTheAccessLogBackfill:
    def test_a_real_line_is_parsed_into_a_request(self):
        parsed = traffic.parse_access_log_line(LOG_LINE)
        assert parsed["path"] == "/student/dashboard"
        assert parsed["status"] == 200
        # `%(D)s` is microseconds, so 12345 is 12.345 ms — not 12 seconds.
        assert parsed["duration_ms"] == pytest.approx(12.345)
        assert parsed["at"] == dt.datetime(2026, 10, 14, 22, 28, 37, tzinfo=dt.timezone.utc)
        assert traffic.device_class(parsed["user_agent"]) == "mobile"

    def test_a_line_the_format_cannot_describe_is_refused_not_guessed(self):
        for bad in ("", "not a log line", LOG_LINE.replace(" 200 ", " ?? "),
                    LOG_LINE.replace('"GET /student/dashboard HTTP/1.1"', '"-"'),
                    LOG_LINE.replace("[14/Oct/2026:22:28:37 +0000]", "[not a date]")):
            assert traffic.parse_access_log_line(bad) is None, bad

    def test_the_backfill_files_the_hour_the_line_happened_in(self, tmp_path):
        """Not the hour it was read in — a backfill of yesterday must not be today."""
        path = tmp_path / "access.log"
        path.write_text(LOG_LINE + "\n", encoding="utf-8")
        recorder = RpcRecorder()
        out = traffic.rollup_from_access_log(str(path), supabase=recorder)
        assert out["lines"] == 1 and out["skipped"] == 0
        row = recorder.calls[0][1]["p_rows"][0]
        assert row["period_start"].startswith("2026-10-14T22:00:00")
        # The log has no session, so the row is platform-wide rather than guessed at.
        assert row["school_id"] == ""

    def test_an_unreadable_log_reports_rather_than_raises(self, tmp_path):
        out = traffic.rollup_from_access_log(str(tmp_path / "nope.log"))
        assert out["error"] and out["lines"] == 0
