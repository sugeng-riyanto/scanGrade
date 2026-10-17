"""The traffic and error reading on `/super-admin/dashboard`.

What this file is guarding, in one sentence: **the page that reports the load must
not be part of it.** Every panel is computed in Python from two reads of the
pre-computed rollup tables, and the tests below pin that in the three ways it can
be lost —

* `build_report` issues exactly two table reads, whatever the range or how many
  panels are asked for. "Just one more query" per panel is how a seventeen-query
  page gets built one panel at a time, and this box is 1 vCPU.
* the panel template holds no query at all: it is rendered from the dict the route
  already built, so a panel cannot smuggle in a read of its own.
* the route itself is super-admin only, through the app's shared role door rather
  than a hand-rolled check — the same list `test_deploy_status.py` pins for the
  neighbouring page.

The other half is privacy, and it is a property of the data rather than a promise:
the rows the report is built from carry counts, a route *group*, a coarse device
class and a school id. There is no student id, no IP, no cookie and no raw
user-agent for this page to leak, and `TestNothingIsPerPerson` reads the module's
own column usage to say so.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.routes import super_admin as mod
from app.services import cache_maintenance_service as sweep
from app.services import traffic_rollup_service as traffic

ROOT = Path(__file__).resolve().parents[2]
PANEL = ROOT / "app" / "templates" / "super_admin" / "_traffic_panel.html"
DASHBOARD = ROOT / "app" / "templates" / "super_admin" / "dashboard.html"

DAY = 24 * 3600


def day(offset: int) -> str:
    """A UTC midnight a few days ago, as the rollup tables store it."""
    stamp = (dt.datetime.now(dt.timezone.utc).replace(hour=0, minute=0, second=0,
                                                      microsecond=0)
             - dt.timedelta(days=offset))
    return stamp.isoformat()


def hour(offset: int, hh: int) -> str:
    stamp = (dt.datetime.now(dt.timezone.utc).replace(hour=hh, minute=0, second=0,
                                                      microsecond=0)
             - dt.timedelta(days=offset))
    return stamp.isoformat()


def buckets(fast: int, slow: int = 0) -> list:
    counts = [0] * traffic.DURATION_BUCKET_COUNT
    counts[traffic.bucket_index(80)] = fast
    counts[traffic.bucket_index(3000)] = slow
    return counts


class FakeSupabase:
    """`table(...).select(...).gte(...).order(...).execute()`, and a read log.

    Deliberately not a query engine: the rollup windows are applied by the database
    in production, and re-implementing them here would test the fake. What matters
    for every assertion below is *how many reads the render asks for* and what the
    rows say, and both are answerable without filtering.
    """

    def __init__(self, daily=(), hourly=()):
        self.tables = {"traffic_rollup_daily": list(daily),
                       "traffic_rollup_hourly": list(hourly),
                       "schools": []}
        self.reads = []

    def table(self, name):
        self.reads.append(name)
        rows = self.tables.get(name, [])
        recorder = self

        class Query:
            def select(self, *a, **k):
                return self

            def gte(self, *a, **k):
                return self

            def order(self, *a, **k):
                return self

            def execute(self):
                return SimpleNamespace(data=rows, count=len(rows))

        del recorder
        return Query()


def daily_row(offset, group="/student", requests=100, errors=0, sessions=0,
              mobile=None, school="school-a"):
    return {
        "period_start": day(offset),
        "route_group": group,
        "school_id": school if school else None,
        "requests": requests,
        "status_2xx": requests - errors,
        "status_3xx": 0,
        "status_4xx": 0,
        "status_5xx": errors,
        "device_mobile": requests if mobile is None else mobile,
        "device_desktop": 0,
        "device_other": 0,
        "duration_bucket_counts": buckets(requests - errors, errors),
        "duration_sum_ms": requests * 40.0,
        "duration_max_ms": 3200,
        "unique_sessions": sessions,
    }


def session_row(offset, sessions, school=""):
    row = daily_row(offset, group=traffic.SESSION_GROUP, requests=0, sessions=sessions)
    row["school_id"] = school or None
    row["duration_bucket_counts"] = []
    return row


# ── the reading ─────────────────────────────────────────────────────────────

class TestTheReadBudget:
    def test_the_whole_report_is_two_reads(self):
        """Two, and no more: one daily, one hourly. Asserted as an equality."""
        fake = FakeSupabase([daily_row(1)], [])
        traffic.build_report(fake, days=30, hourly_days=7)
        assert fake.reads == ["traffic_rollup_daily", "traffic_rollup_hourly"], (
            "a panel that fetches its own rows is the page becoming the load it "
            "measures; every panel is computed from these two")

    def test_a_longer_range_costs_the_same_two_reads(self):
        fake = FakeSupabase([daily_row(n) for n in range(1, 40)], [])
        traffic.build_report(fake, days=90, hourly_days=30)
        assert len(fake.reads) == 2

    def test_the_panel_template_holds_no_query(self):
        """It renders what it is handed. A `.table(` here would be a third read."""
        body = PANEL.read_text(encoding="utf-8")
        assert ".table(" not in body
        assert "supabase" not in body.lower()
        assert "{% include 'super_admin/_traffic_panel.html' %}" in \
            DASHBOARD.read_text(encoding="utf-8"), (
            "the dashboard must include the panel rather than inline a copy of it, "
            "or the two drift and the test above guards nothing")


class TestTheReport:
    def test_the_daily_series_adds_the_route_groups_up(self):
        fake = FakeSupabase([daily_row(2, "/student", requests=10, errors=1),
                             daily_row(2, "/teacher", requests=5, errors=0),
                             daily_row(1, "/student", requests=7)], [])
        report = traffic.build_report(fake)
        # Oldest first: the two days, with the route groups already added within each.
        assert [entry["requests"] for entry in report["series"]] == [15, 7]
        assert [entry["errors"] for entry in report["series"]] == [1, 0]
        assert report["totals"]["requests"] == 22

    def test_the_session_row_is_the_platform_count_and_not_a_sum(self):
        """A pupil is one person, not one per school they appear under.

        The `*` group carries one row per (day, school) *and* one platform row. The
        page's number is the platform one — summing the school rows would count a
        pupil once per school and, on a day whose platform write failed, taking only
        that row would lose the day entirely. `max` over the day's rows is right in
        both cases.
        """
        fake = FakeSupabase([
            daily_row(1, "/student", requests=900, school="school-a"),
            session_row(1, 40, school=""),
            session_row(1, 30, school="school-a"),
            session_row(1, 25, school="school-b"),
        ], [])
        report = traffic.build_report(fake)
        assert report["series"][0]["unique_sessions"] == 40, (
            "30 + 25 would be 55 people; the platform row says 40")
        assert report["totals"]["sessions"] == 40

    def test_a_group_with_no_traffic_has_no_percentile_rather_than_a_zero(self):
        fake = FakeSupabase([daily_row(1, "/teacher", requests=4)], [])
        report = traffic.build_report(fake)
        group = report["groups"][0]
        assert group["requests"] == 4
        # 4 requests of 80 ms: every percentile is in the fast bucket, whose bound
        # is reported. The point is that it is a *bound*, not an invention.
        assert group["p95"] >= 80

    def test_the_per_school_rows_are_keyed_by_school_and_skip_the_session_group(self):
        fake = FakeSupabase([
            session_row(1, 12, school="school-a"),
            daily_row(1, "/student", requests=50, school="school-a"),
            daily_row(1, "/student", requests=20, school="school-b"),
        ], [])
        report = traffic.build_report(fake)
        by_id = {row["school_id"]: row for row in report["schools"]}
        assert by_id["school-a"]["requests"] == 50
        assert by_id["school-a"]["unique_sessions"] == 12
        assert by_id["school-b"]["requests"] == 20

    def test_the_heatmap_is_seven_by_twenty_four_and_finds_the_quiet_hour(self):
        # Every hour has traffic except the one we are asking about, because the
        # quietest hour of an empty night is always midnight and that proves nothing.
        hourly = [{"period_start": hour(1, h), "requests": 2 if h == 3 else 900}
                  for h in range(24)]
        fake = FakeSupabase([], hourly)
        report = traffic.build_report(fake)
        assert len(report["heat"]) == 7 and len(report["heat"][0]) == 24
        stamp = dt.datetime.fromisoformat(hour(1, 3).replace("Z", "+00:00"))
        assert report["heat"][stamp.weekday()][3] == 2
        assert report["quiet_window"]["hour"] == 3, (
            "the quiet hour is a column sum over the week, not the emptiest cell — "
            "a per-cell minimum is always Monday 00:00 and answers nothing")
        assert report["quiet_window"]["requests"] == 2

    def test_an_empty_window_reads_as_empty_and_not_as_a_failure(self):
        report = traffic.build_report(FakeSupabase())
        assert report["series"] == [] and report["groups"] == []
        assert report["totals"]["requests"] == 0
        assert report["first_day"] is None


class TestTheErrorTrend:
    def test_a_route_whose_error_rate_doubled_is_named_with_both_numbers(self):
        rows = []
        for offset in range(20, 10, -1):      # the earlier half: 200 requests, 2 err
            rows.append(daily_row(offset, "/student", requests=100, errors=1))
        for offset in range(10, 0, -1):       # the later half: 200 requests, 20 err
            rows.append(daily_row(offset, "/student", requests=100, errors=10))
        report = traffic.build_report(FakeSupabase(rows, []))
        rising = traffic.rising_groups(report)
        assert [row["group"] for row in rising] == ["/student"]
        # Both numbers are the *same window's* halves. Comparing the whole-window
        # rate against a half-window one would show a rise on a page that had not
        # got worse, which is the failure mode of every "vs baseline" card.
        assert rising[0]["baseline_error_rate"] == pytest.approx(0.01)
        assert rising[0]["error_rate"] == pytest.approx(0.10)

    def test_a_quiet_route_does_not_top_the_list_on_one_error(self):
        """A ratio over a handful of requests is noise, and saying so is the job."""
        rows = [daily_row(offset, "/guide", requests=12, errors=1) for offset in range(1, 9)]
        report = traffic.build_report(FakeSupabase(rows, []))
        assert traffic.rising_groups(report) == []

    def test_a_route_that_did_not_get_worse_is_not_named(self):
        rows = [daily_row(offset, "/student", requests=100, errors=2)
                for offset in range(1, 9)]
        report = traffic.build_report(FakeSupabase(rows, []))
        assert traffic.rising_groups(report) == []


# ── the error half reuses the tracker that is already installed ─────────────

class TestTheSentryLink:
    def test_it_is_derived_from_the_dsn_when_nothing_else_is_set(self, app):
        with app.app_context():
            app.config["SENTRY_DSN"] = "https://abc123@o4242.ingest.sentry.io/777"
            app.config["SENTRY_ISSUES_URL"] = ""
            assert mod.sentry_issues_url() == \
                "https://sentry.io/organizations/o4242/issues/?project=777"

    def test_an_explicit_url_wins_because_a_self_hosted_one_cannot_be_derived(
            self, app):
        with app.app_context():
            app.config["SENTRY_DSN"] = "https://abc123@o4242.ingest.sentry.io/777"
            app.config["SENTRY_ISSUES_URL"] = "https://sentry.sekolah.id/issues"
            assert mod.sentry_issues_url() == "https://sentry.sekolah.id/issues"

    def test_no_dsn_means_no_link_rather_than_a_link_that_404s(self, app):
        with app.app_context():
            app.config["SENTRY_DSN"] = ""
            app.config["SENTRY_ISSUES_URL"] = ""
            assert mod.sentry_issues_url() is None

    def test_a_dsn_this_derivation_cannot_read_produces_no_link(self, app):
        with app.app_context():
            app.config["SENTRY_ISSUES_URL"] = ""
            app.config["SENTRY_DSN"] = "https://key@not-sentry.example/1"
            assert mod.sentry_issues_url() is None


class TestTheHealthReading:
    def test_it_reads_the_same_inputs_health_does_without_an_http_call(self, app):
        from app import served_commit
        with app.app_context():
            health = mod.platform_health()
            assert health["commit"] == served_commit()
            assert "redis" in health and "workers" in health
        source = (ROOT / "app" / "routes" / "super_admin.py").read_text(encoding="utf-8")
        block = source.split("def platform_health(", 1)[1].split("\ndef ", 1)[0]
        assert "requests.get" not in block and "urlopen" not in block, (
            "a request to the app's own /health would need its port and scheme, and "
            "would be the box asking itself something it already knows")


# ── access ──────────────────────────────────────────────────────────────────

class TestTheDoor:
    def test_the_route_is_super_admin_only(self, app):
        response = app.test_client().get("/super-admin/dashboard")
        assert response.status_code in (301, 302), response.status_code
        from app.utils.auth import LOGIN_URL
        assert response.headers.get("Location", "").split("?")[0] == LOGIN_URL

    def test_it_uses_the_shared_role_door_rather_than_its_own_check(self):
        source = (ROOT / "app" / "routes" / "super_admin.py").read_text(encoding="utf-8")
        block = source.split('@super_bp.route("/dashboard")', 1)[1].split("\ndef ", 1)[0]
        assert "@_sa_required" in block
        assert "role_required(\"super_admin\")" in source


# ── the page ────────────────────────────────────────────────────────────────

def render_panel(app, **context):
    return app.jinja_env.get_template("super_admin/_traffic_panel.html").render(**context)


#: The record `platform_health()` actually hands this panel: `served_commit()`'s
#: dict, not a sha. The fixture used to model `commit` as `"a" * 40`, and that string
#: is why the panel's `(health.commit or '—')[:7]` shipped and answered 500 on the box
#: (`TypeError: unhashable type: 'slice'`) while every test in this file passed: the
#: fixture was the one shape the route never produces.
SERVED = {"available": True, "reason_key": None, "commit": "abc1234",
          "full_commit": "a" * 40, "loaded_at": None}


def context_for(report):
    return {"analytics": report, "school_names": {"school-a": "SMP Tujuan"},
            "health": {"commit": SERVED, "workers": "3", "redis": True,
                       "page_cache": 0},
            "cache_sweep": {"at": "2026-10-11T20:05:00+00:00", "ran": True},
            "worst_page": {"roundtrips": 12, "roundtrips_endpoint": "/student/dashboard"}}


class TestThePanel:
    def test_it_renders_the_numbers_it_was_handed(self, app):
        report = traffic.build_report(
            FakeSupabase([daily_row(1, "/student", requests=1234, errors=7)],
                         [{"period_start": hour(1, 9), "requests": 1234}]))
        report["rising"] = []
        report["last_flush"] = {"at": "2026-10-14T22:00:00+00:00"}
        report["sentry_url"] = "https://sentry.io/organizations/o1/issues/?project=2"
        body = render_panel(app, **context_for(report))
        assert "1234" in body, "the request total is on the page"
        assert "SMP Tujuan" in body, "the per-school row names the school"
        assert "/student" in body, "the route group is named"
        assert "sentry.io/organizations/o1" in body, "the Sentry link is rendered"
        assert "12" in body, "the worst page's query count is rendered"

    def test_it_renders_the_deferred_sweep_as_a_reason_and_not_an_error(self, app):
        report = traffic.build_report(FakeSupabase())
        report["rising"], report["last_flush"], report["sentry_url"] = [], {}, None
        context = context_for(report)
        context["cache_sweep"] = {"at": "2026-10-11T20:05:00+00:00", "ran": False,
                                  "reason": "a sitting is in progress"}
        body = render_panel(app, **context)
        assert "a sitting is in progress" in body

    def test_it_says_so_rather_than_showing_a_link_when_there_is_no_tracker(self, app):
        report = traffic.build_report(FakeSupabase())
        report["rising"], report["last_flush"] = [], {}
        report["sentry_url"] = None
        body = render_panel(app, **context_for(report))
        assert "SENTRY_DSN" in body
        assert "sentry.io" not in body

    def test_the_commit_it_shows_is_the_one_the_route_hands_it(self, app):
        """Rendered from the producer's own shape, the box's health card does not raise."""
        report = traffic.build_report(FakeSupabase())
        report["rising"], report["last_flush"], report["sentry_url"] = [], {}, None
        body = render_panel(app, **context_for(report))
        assert "abc1234" in body, "the short commit `served_commit()` names is on the page"
        assert "a" * 40 not in body, "the full sha is not what this card shows"

    def test_the_health_fixture_is_the_shape_the_route_produces(self):
        """The two shapes are read against each other, so the fixture cannot drift again.

        This is the guard the old fixture needed: the panel renders `health["commit"]`,
        which `platform_health()` fills with `served_commit()`'s record. A fixture that
        models it as a string passes every render and proves nothing about the page the
        box serves, so the keys the route writes are compared with the keys here, and the
        record's own keys with the ones `served_commit()` returns.
        """
        from app import served_commit
        from app.routes.super_admin import platform_health

        health = platform_health()
        assert set(health) == set(context_for(None)["health"]), (
            "the route writes a key this fixture does not model")
        assert isinstance(health["commit"], dict), (
            "`served_commit()` is a record, and the page reads its `commit` field")
        assert set(health["commit"]) == set(served_commit()), (
            "the card shows a record whose fields this fixture does not carry")

    def test_an_empty_window_does_not_break_the_page(self, app):
        report = traffic.build_report(FakeSupabase())
        report["rising"], report["last_flush"], report["sentry_url"] = [], {}, None
        body = render_panel(app, **context_for(report))
        assert "Requests" in body and "5xx" in body

    def test_every_panel_label_is_offered_in_both_languages(self, app):
        """A page a super admin reads must follow the toggle like every other page."""
        body = PANEL.read_text(encoding="utf-8")
        assert "t('Permintaan','Requests')" in body
        assert "x-text=\"t(" in body

    def test_the_charts_are_built_after_chart_js_exists(self):
        """A chart created inline would throw, and the panel would be a blank canvas.

        `base.html` loads Chart.js as a classic script at the *end* of the body,
        after `{% block scripts %}` — so the block runs first and `Chart` is not
        defined yet. The listener is what makes the order safe.
        """
        body = DASHBOARD.read_text(encoding="utf-8")
        block = body.split("{% block scripts %}", 1)[1]
        assert "DOMContentLoaded" in block, (
            "Chart.js is not defined when this block runs")
        assert block.index("DOMContentLoaded") < block.index("new Chart(")
        assert "sgApplyChartTheme(" not in block, (
            "the theme is base.html's job; calling it here would be a second palette")


class TestTheCacheSweepCard:
    def test_the_sweep_reads_its_window_from_the_same_constants_the_card_names(self):
        """A page that promises an hour it does not count with is how this reads."""
        assert sweep.in_window(dt.datetime(2026, 10, 18, 3, 30, tzinfo=dt.timezone.utc),
                               weekday=6, hour=10) is True, (
            "WIB is UTC+7, so 03:30 UTC is the 10:00 window")
        assert sweep.in_window(dt.datetime(2026, 10, 18, 3, 30, tzinfo=dt.timezone.utc),
                               weekday=6, hour=3) is False
