"""The performance gate: a release must not be slower than the last good one.

`claims_gate.py` holds the deployment to the number printed on the landing page.
That check cannot see a release that costs 40% of every page's response time while
staying inside the published bound — and five of those in a row are a box that no
longer does what it did, each one passing on its own.

This gate asks the other question, so these tests hold it to it:

1. the comparison is a *ratio against the previous passing release*, not against
   an absolute, because the baseline is the same box on the same load;
2. the baseline is written only when a release passes — otherwise a bad release
   becomes the yardstick and the regression stops being visible;
3. a changed reference load or a box that is already busy is "could not measure",
   never a verdict, because exit 2 is deliberately not a rollback;
4. the deploy treats "slower" differently from "could not tell", and only a
   confirmed regression with PERF_ENFORCE=true rolls the release back.

The end-to-end tests run the real `main()` against a local HTTP server and a fake
harness, so the accept/reject/baseline path is exercised rather than described.
"""
import importlib.util
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GATE_PATH = ROOT / "deploy" / "perf_gate.py"
CLAIMS_PATH = ROOT / "deploy" / "claims_gate.py"
DEPLOY = ROOT / "deploy" / "scangrade-deploy.sh"
INSTALLER = ROOT / "deploy" / "install-auto-deploy.sh"


def _load(path: Path, name: str):
    """Import a deploy script without making deploy/ a package."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gate = _load(GATE_PATH, "perf_gate")
claims = _load(CLAIMS_PATH, "claims_gate")

# (conf variable, gate source, the one switch the deploy reads itself) per gate.
GATES = {
    "perf": ("PERF_CONF", GATE_PATH.read_text(encoding="utf-8"), "PERF_ENFORCE"),
    "claims": ("CLAIMS_CONF", CLAIMS_PATH.read_text(encoding="utf-8"), "CLAIMS_ENFORCE"),
}


# ── fixtures ─────────────────────────────────────────────────────────────────

def summary(p50: float, p95: float, sessions: int = 20, error_pct: float = 0.0,
            fivexx: int = 0, logins: int | None = None,
            endpoint: str = "GET /student/dashboard",
            payload: float | None = None, queries: float | None = None,
            asked: float | None = None, rows: float | None = None) -> dict:
    """A harness summary in the shape the real one writes.

    `payload` and `queries` are opt-in: left out, the summary is one an older harness
    would have written, which is its own case to test. `asked` is the queries the
    render *issued* (as opposed to the attempts the database served) and `rows` is how
    much data it read; both are also opt-in, for the same reason.
    """
    ok = sessions if logins is None else logins
    row = {"n": 300, "p50": p50, "p95": p95}
    if payload is not None:
        row["bytes_p50"] = payload
    if queries is not None:
        row["roundtrips_p50"] = queries
    if asked is not None:
        row["queries_p50"] = asked
    if rows is not None:
        row["rows_p50"] = rows
    return {
        "sessions_launched": sessions,
        "logins_ok": ok,
        "logins_failed": sessions - ok,
        "identity_checked": sessions,
        "identity_ok": sessions,
        "requests_total": 500,
        "throughput_rps": 25.0,
        "error_rate_pct": error_pct,
        "server_errors_5xx": fivexx,
        "rate_limited_429": 0,
        "transport_errors": 0,
        "per_endpoint": {endpoint: row},
    }


def baseline_from(p50: float, p95: float, commit: str = "abc1234",
                  payload: float | None = None, queries: float | None = None,
                  asked: float | None = None, rows: float | None = None,
                  floor_bytes: float | None = None,
                  floor_queries: float | None = None, **extra) -> dict:
    record = {
        "commit": commit,
        "shape": gate.shape_of(20, 2, 20.0, "https://example.test"),
        "latency": {"p50_ms": p50, "p95_ms": p95, "endpoints": ["GET /student/dashboard"],
                    "samples": 300},
        "error_pct": extra.pop("error_pct", 0.0),
        "server_errors_5xx": extra.pop("fivexx", 0),
    }
    if payload is not None or queries is not None:
        cost = {"bytes": float(payload or 0),
                "bytes_endpoint": "GET /student/dashboard",
                "roundtrips": float(queries or 0),
                "roundtrips_endpoint": "GET /student/dashboard"}
        if rows is not None:
            cost["bytes_rows"] = float(rows)
            cost["roundtrips_rows"] = float(rows)
        if asked is not None:
            cost["roundtrips_queries"] = float(asked)
        if floor_bytes is not None:
            cost["floor_bytes"] = float(floor_bytes)
        if floor_queries is not None:
            cost["floor_queries"] = float(floor_queries)
        record["page_cost"] = cost
    record.update(extra)
    return record


def two_pages(slow_p50: float, slow_p95: float, slow_rows: float, *,
              fast_p50: float = 80, fast_p95: float = 120,
              fast_rows: float | None = None) -> dict:
    """A run with two signed-in pages, so a floor below the worst one exists.

    `latency_floor` reads the *smallest* measured page as the fixed cost a page
    carries whatever the data — the same estimate the byte and query floors use — so
    a test about the latency normalization needs at least two pages to have one.
    """
    out = summary(slow_p50, slow_p95)
    rows = slow_rows if fast_rows is None else fast_rows
    out["per_endpoint"] = {
        "GET /student/dashboard": {"n": 100, "p50": fast_p50, "p95": fast_p95,
                                    "rows_p50": rows},
        "GET /teacher/results": {"n": 100, "p50": slow_p50, "p95": slow_p95,
                                  "rows_p50": slow_rows},
    }
    return out


def baseline_with_latency(*, p50: float, p95: float, floor_p50: float,
                          floor_p95: float, p50_rows: float,
                          commit: str = "abc1234") -> dict:
    """A baseline whose latency block carries the floor and the slow page's rows."""
    record = baseline_from(p50, p95, commit=commit)
    record["latency"].update({"floor_p50_ms": floor_p50, "floor_p95_ms": floor_p95,
                              "p50_rows": p50_rows})
    return record


# ── 1. the comparison is a ratio against the last passing release ────────────

class TestTheComparison:
    def test_an_unchanged_release_is_not_a_regression(self):
        reasons = gate.regression(baseline_from(200, 400), summary(205, 420))
        assert reasons == []

    def test_a_slower_p50_is_a_regression_and_names_the_baseline(self):
        reasons = gate.regression(baseline_from(200, 400), summary(340, 420))
        assert len(reasons) == 1
        assert "p50 340 ms" in reasons[0] and "200 ms" in reasons[0]
        assert "abc1234" in reasons[0], "the reason must say which release it is slower than"
        assert "1.70x" in reasons[0], "the ratio is the whole argument"

    def test_a_slower_p95_is_a_regression(self):
        reasons = gate.regression(baseline_from(200, 400), summary(210, 900))
        assert len(reasons) == 1 and "p95" in reasons[0]

    def test_the_slack_is_a_ratio_not_an_absolute(self):
        """The baseline is this box on this load, so only the change matters: the
        same 40% is a pass from 100 ms and from 1000 ms."""
        small = gate.regression(baseline_from(100, 1000), summary(140, 1300))
        assert small == [], f"140 ms against 100 ms is 1.4x, inside the 1.5x slack: {small}"
        large = gate.regression(baseline_from(1000, 10000), summary(1400, 13000))
        assert large == [], f"the same ratio from a slower baseline must pass: {large}"

    def test_server_errors_are_a_regression_even_when_the_pages_are_fast(self):
        reasons = gate.regression(baseline_from(200, 400), summary(150, 300, fivexx=7))
        assert any("7 server error" in r for r in reasons), (
            "a release that answers 500s produced no page at all, whatever the latency of "
            f"the pages it did produce: {reasons}")

    def test_an_error_rate_above_the_baseline_is_a_regression(self):
        reasons = gate.regression(baseline_from(200, 400), summary(200, 400, error_pct=2.4))
        assert any("error rate" in r for r in reasons), reasons

    def test_nothing_is_claimed_when_no_page_load_was_recorded(self):
        empty = summary(0, 0)
        empty["per_endpoint"] = {}
        assert gate.regression(baseline_from(200, 400), empty) == []


# ── 1b. what a page costs, which latency cannot see ──────────────────────────

class TestTheCostOfAPage:
    """A page that stays just as fast while costing more is still a regression.

    Response time is a symptom. A release can add three queries to the student
    dashboard, or land a 200 KB script on it, and still answer inside the latency
    slack on a box this quiet — and then be the page that falls over at 500
    concurrent students. So each signed-in page also reports what it *cost*: the
    bytes it sent, and the Supabase round-trips the render spent (the app's own
    `X-Supabase-Roundtrips` header).

    These hold the rule, the grace that keeps ordinary data growth from being
    mistaken for a leak, and the self-arming that lets the rule arrive without
    rejecting its first release.
    """

    def test_an_unchanged_page_is_not_a_regression(self):
        reasons = gate.regression(baseline_from(200, 400, payload=100_000, queries=6),
                                  summary(205, 420, payload=100_000, queries=6))
        assert reasons == []

    def test_a_heavier_page_is_a_regression_and_names_the_endpoint(self):
        reasons = gate.regression(baseline_from(200, 400, payload=100_000, queries=6),
                                  summary(200, 400, payload=300_000, queries=6))
        assert len(reasons) == 1, reasons
        assert "293.0 KB" in reasons[0] and "97.7 KB" in reasons[0], reasons[0]
        assert "GET /student/dashboard" in reasons[0], "the page that grew must be named"
        assert "abc1234" in reasons[0], "and the release it grew against"

    def test_a_page_that_spends_more_queries_is_a_regression(self):
        reasons = gate.regression(baseline_from(200, 400, payload=100_000, queries=6),
                                  summary(200, 400, payload=100_000, queries=11))
        assert len(reasons) == 1, reasons
        assert "Supabase queries" in reasons[0]
        assert "11" in reasons[0] and "6" in reasons[0], reasons[0]
        assert "N+1" in reasons[0], (
            "the message has to name the shape of the defect, or whoever reads the "
            f"journal does not know what to grep for: {reasons[0]}")

    def test_an_n_plus_one_is_caught_rather_than_waved_through(self):
        """6 queries becoming 8 is the defect, not a rounding difference.

        The count is structural: it does not grow because the data did, it grows
        when somebody moves a query inside a loop. So the slack has to be tight
        enough to see it, and this is the assertion that says so.
        """
        reasons = gate.regression(baseline_from(200, 400, payload=100_000, queries=6),
                                  summary(200, 400, payload=100_000, queries=8))
        assert reasons and "Supabase queries" in reasons[0], reasons

    def test_growth_that_is_not_the_release_is_absorbed(self):
        """A longer list is not a leak: 3 KiB and one more query both pass."""
        reasons = gate.regression(baseline_from(200, 400, payload=100_000, queries=6),
                                  summary(200, 400, payload=103_000, queries=7))
        assert reasons == [], reasons

    def test_the_grace_and_the_ratio_both_have_to_be_cleared(self):
        """Two guards for two worries, and neither one alone is a refusal.

        A tiny page can multiply many times over without being worth a rollback, and
        a large one can grow well past its grace while staying a modest multiple.
        """
        tiny = gate.regression(baseline_from(200, 400, payload=1_000, queries=6),
                               summary(200, 400, payload=9_000, queries=6))
        assert tiny == [], f"9 KB against 1 KB is 9x, but inside the 8 KiB grace: {tiny}"
        large = gate.regression(baseline_from(200, 400, payload=100_000, queries=6),
                                summary(200, 400, payload=118_000, queries=6))
        assert large == [], f"118 KB against 100 KB is inside the 1.25x ratio: {large}"

    def test_a_baseline_written_before_the_rule_existed_is_not_a_regression(self):
        """Self-arming: the rule arrived without a baseline that knows about it.

        A baseline with no `page_cost` was written by the gate before this axis
        existed. Reading its silence as zero bytes would refuse every release from
        the moment the rule landed until somebody re-baselined by hand.
        """
        reasons = gate.regression(baseline_from(200, 400),
                                  summary(200, 400, payload=900_000, queries=99))
        assert reasons == [], reasons

    def test_a_run_that_reports_no_cost_is_not_scored_against_one(self):
        """The other direction: a harness too old to report costs.

        `main()` refuses to *pass* this quietly — that is the end-to-end case below —
        but the comparison itself has nothing to compare and must say so by silence,
        not by inventing a number.
        """
        reasons = gate.regression(baseline_from(200, 400, payload=100_000, queries=6),
                                  summary(200, 400))
        assert reasons == [], reasons

    def test_the_worst_page_on_each_axis_is_found_separately(self):
        """The byte-heavy page and the query-heavy page are often different pages.

        Judging both from whichever endpoint happens to be the biggest would let the
        other one grow unseen.
        """
        measured = summary(200, 400, payload=10_000, queries=2)
        measured["per_endpoint"] = {
            "GET /student/dashboard": {"n": 10, "p50": 200, "p95": 400,
                                      "bytes_p50": 500_000, "roundtrips_p50": 3},
            "GET /teacher/exams": {"n": 10, "p50": 200, "p95": 400,
                                   "bytes_p50": 20_000, "roundtrips_p50": 40},
        }
        cost = gate.page_cost(measured)
        assert (cost.bytes, cost.by_bytes) == (500_000.0, "GET /student/dashboard")
        assert (cost.roundtrips, cost.by_roundtrips) == (40.0, "GET /teacher/exams")

    def test_a_page_the_run_never_loaded_costs_nothing_to_compare(self):
        measured = summary(200, 400, payload=100_000, queries=6)
        measured["per_endpoint"] = {"GET /health": {"n": 10, "p50": 5, "p95": 9,
                                                   "bytes_p50": 2, "roundtrips_p50": 0}}
        assert gate.page_cost(measured) is None, (
            "the gate is about signed-in pages, not every endpoint the run touched")

    def test_the_record_keeps_the_cost_and_which_page_it_came_from(self):
        measured = summary(200, 400, payload=100_000, queries=6)
        record = gate.baseline_record("deadbee", gate.shape_of(20, 2, 20.0, "https://x.test"),
                                      measured, claims.claim_latency(measured))
        assert record["page_cost"]["bytes"] == 100_000
        assert record["page_cost"]["roundtrips"] == 6
        assert record["page_cost"]["bytes_endpoint"] == "GET /student/dashboard"

    def test_the_floor_the_growth_is_measured_from_is_the_smallest_page(self):
        """The intercept of the cost model, measured rather than assumed.

        A render is a shared layout plus the data it drew, and the layout does not
        grow with the roster — which is why the growth normalization has to hold
        that part constant, byte for byte. The smallest signed-in page the run
        loaded is the cheapest honest estimate of it. Taking the *largest* instead
        would put the intercept above every page, so nothing would ever look
        growth-explained and the normalization would be decoration.
        """
        measured = summary(200, 400, payload=100_000, queries=6, asked=6, rows=50)
        measured["per_endpoint"]["GET /student/settings"] = {
            "n": 10, "p50": 90, "p95": 150, "bytes_p50": 9_000,
            "roundtrips_p50": 1, "queries_p50": 1, "rows_p50": 4,
        }
        record = gate.baseline_record("deadbee",
                                      gate.shape_of(20, 2, 20.0, "https://x.test"),
                                      measured, claims.claim_latency(measured))
        assert record["page_cost"]["floor_bytes"] == 9_000, (
            "the part of a page that does not scale with rows is the cheapest page "
            "this run loaded")
        assert record["page_cost"]["floor_queries"] == 1


# ── 1c. the growth the data explains, and the growth it does not ─────────────

class TestWhatTheDataExplains:
    """A page can cost more because the release got heavier or because the school
    got bigger, and the gate used to be able to tell only one of them apart.

    Bytes are deterministic given (code, data), which is measurable rather than
    argued: on production, a day apart on the *same* release, every endpoint's page
    bytes were identical to the byte — while `/teacher/dashboard` went from 1
    round-trip to 3. So a page that grew is not evidence of anything on its own,
    and the only way to separate the two causes is to ask how much data the page
    read. `rows` is that number; where it is missing, the old absolute rule stands.

    A cost increase the data explains is not this release's doing and must not roll
    it back. A cost increase the data does *not* explain is the defect this axis
    exists for, and the normalization must not become a way to smuggle one past.
    """

    #: 40 KB of shared layout, 5 KB of data at 50 rows, 3 queries issued (floor 1).
    TEN_X = dict(payload=45_000, queries=3, asked=3, rows=50,
                 floor_bytes=40_000, floor_queries=1)

    def test_a_page_that_grew_because_it_read_more_rows_is_not_a_regression(self):
        """Ten times the rows, and the page is ten times its data section larger."""
        base = baseline_from(200, 400, **self.TEN_X)
        now = summary(205, 420, payload=90_000, queries=21, asked=21, rows=500)
        assert gate.regression(base, now) == [], (
            "the page read 10x the rows and grew exactly as much as that explains; "
            "refusing this is refusing the release for the school's data")

    def test_a_heavier_render_at_the_same_row_count_is_still_a_regression(self):
        base = baseline_from(200, 400, **self.TEN_X)
        now = summary(205, 420, payload=70_000, queries=3, asked=3, rows=50)
        reasons = gate.regression(base, now)
        assert reasons and "grew" in reasons[0], reasons

    def test_growth_the_rows_cannot_explain_is_still_a_regression(self):
        """Twice the rows, but the page grew past what twice the data accounts for."""
        base = baseline_from(200, 400, **self.TEN_X)
        now = summary(205, 420, payload=120_000, queries=3, asked=3, rows=100)
        reasons = gate.regression(base, now)
        assert reasons and "117.2 KB" in reasons[0] and "43.9 KB" in reasons[0], reasons
        assert "the data accounts for 4.9 KB" in reasons[0], (
            "the message has to say what the data accounted for, or the reader cannot "
            f"tell a heavier release from a bigger school: {reasons[0]}")

    def test_a_fixed_query_added_to_a_page_that_also_grew_is_still_refused(self):
        """The whole reason the data's own share is *added*, not multiplied.

        A page whose data grows fourfold may spend four times the queries that scale
        with rows; the queries that do *not* scale with rows are still the release's,
        and here there is one of them too many. Adding the data's share as the absolute
        amount it is justifies 10 queries (the fourfold rows, the baseline's slack and
        grace); the render issued 11. Multiplying the allowance by the growth instead
        would have justified 12 and swallowed the extra query — which is the defect
        this test exists to refuse, and why the margin is deliberately wider than the
        two numbers that used to sit either side of it.
        """
        base = baseline_from(200, 400, **self.TEN_X)
        # Bytes are exactly what four times the rows explains — 40 KB of layout plus
        # 4 x 5 KB of data — so this test is about the query axis alone.
        now = summary(205, 420, payload=60_000, queries=11, asked=11, rows=200)
        reasons = gate.regression(base, now)
        assert reasons and "queries" in reasons[0], reasons

    def test_the_explanation_never_tightens_the_rule(self):
        """A dataset that shrank does not license refusing an unchanged page."""
        base = baseline_from(200, 400, **self.TEN_X)
        now = summary(205, 420, payload=45_000, queries=3, asked=3, rows=50)
        assert gate.regression(base, now) == []
        fewer_rows = summary(205, 420, payload=45_000, queries=3, asked=3, rows=5)
        assert gate.regression(base, fewer_rows) == [], (
            "the data's share may explain growth; it may never make a page that did "
            "not change look heavier than the data says it should have been")

    def test_a_baseline_without_rows_falls_back_to_the_absolute_rule(self):
        """Every baseline written before this existed is one of these.

        The old rule is the safe one here: it may refuse a release the data would
        have explained, and it cannot let a heavier one through.
        """
        base = baseline_from(200, 400, payload=45_000, queries=3)
        reasons = gate.regression(base, summary(205, 420, payload=90_000, queries=21,
                                                asked=21, rows=500))
        assert reasons, "without rows there is nothing to explain the growth with"

    def test_a_run_that_reports_rows_but_no_queries_is_not_scored_on_bytes_alone(self):
        """Half a measurement is not a measurement gap; the axes are independent."""
        base = baseline_from(200, 400, payload=45_000, queries=3, asked=3, rows=50,
                             floor_bytes=40_000, floor_queries=1)
        now = summary(205, 420, payload=90_000, rows=500)
        assert gate.regression(base, now) == [], (
            "bytes are explained by rows even when the run reported no query count")


# ── 1c-bis. the same normalization for the clock ─────────────────────────────

class TestLatencyAndTheData:
    """Bytes and queries were normalized against the rows the pages read; latency was
    not, so a school that had simply grown read as a release that had slowed down.

    The reasoning is the same as the cost axes and so is the floor: a page is a fixed
    cost plus the work it does with the data, and the smallest measured page is the
    cheapest honest estimate of the fixed part. Holding that floor constant is what
    keeps the layout from being blamed for the roster — scaling a page's *whole*
    latency by the row growth would make a heavier release hide behind a bigger
    school, which is the mistake the byte and query axes already avoid.
    """

    #: A slow page (~300 ms) beside a fast one (~100 ms): the fixed cost is ~100 ms
    #: and the slow page's data section is ~200 ms at 10 rows.
    BASE = dict(p50=300, p95=450, floor_p50=100, floor_p95=150, p50_rows=10)

    def test_a_busier_school_is_not_read_as_a_slower_release(self):
        """Four times the rows grows the data section fourfold, and the page is under it.

        Without the normalization the same measurement is 700/300 = 2.33x and refused;
        what the baseline page would cost on today's data is 100 + 200*4 = 900 ms, so
        this is the release the gate exists to stop refusing.
        """
        base = baseline_with_latency(**self.BASE)
        assert gate.regression(base, two_pages(700, 1000, slow_rows=40)) == [], (
            "a page that reads four times the rows and answers inside what that data "
            "explains is the school growing, not the release slowing")

    def test_a_slower_page_at_the_same_row_count_is_still_a_regression(self):
        base = baseline_with_latency(**self.BASE)
        reasons = gate.regression(base, two_pages(700, 1000, slow_rows=10))
        assert reasons and "p50 700 ms" in reasons[0] and "300 ms" in reasons[0], reasons

    def test_growth_the_rows_cannot_explain_is_still_a_regression(self):
        """Twice the rows, but the page slowed past what twice the data accounts for."""
        base = baseline_with_latency(**self.BASE)
        reasons = gate.regression(base, two_pages(1500, 2200, slow_rows=20))
        assert reasons and any("p50" in r for r in reasons), reasons
        assert any("the data accounts for" in r for r in reasons), (
            "the message has to say what the data accounted for, or a reader cannot tell "
            f"a slower release from a bigger school: {reasons}")

    def test_the_floor_is_held_constant_so_the_layout_is_not_blamed_for_the_roster(self):
        """With no spread between the floor and the worst page there is no data section
        to grow, so the normalization adds nothing.

        A page whose whole cost *is* its data section is exactly the case where scaling
        the total would multiply the allowance by the roster; holding the floor constant
        means four times the rows buys no allowance at all here.
        """
        base = baseline_with_latency(p50=300, p95=450, floor_p50=300, floor_p95=450,
                                     p50_rows=10)
        reasons = gate.regression(base, two_pages(700, 1000, slow_rows=40))
        p50 = [r for r in reasons if "p50" in r]
        assert p50, f"four times the rows bought an allowance where the floor equals the page: {reasons}"
        assert "the data accounts for" not in p50[0], (
            "there is no data section to grow when the floor is the page itself, so no "
            f"milliseconds may be attributed to it: {p50[0]}")

    def test_a_baseline_without_a_floor_falls_back_to_the_absolute_rule(self):
        base = baseline_from(300, 450, commit="abc1234")
        assert gate.regression(base, two_pages(700, 1000, slow_rows=40)), (
            "without a floor there is nothing to explain the growth with")

    def test_a_baseline_without_rows_falls_back_to_the_absolute_rule(self):
        record = baseline_from(300, 450)
        record["latency"].update({"floor_p50_ms": 100, "floor_p95_ms": 150})
        assert gate.regression(record, two_pages(700, 1000, slow_rows=40))

    def test_shrinking_rows_never_tighten_the_rule(self):
        base = baseline_with_latency(**self.BASE)
        assert gate.regression(base, two_pages(250, 400, slow_rows=2)) == [], (
            "the data's share may explain growth; it may never make a page that did not "
            "change look slower than the data says it should have been")

    def test_the_baseline_records_the_latency_floor_and_the_slow_page_rows(self):
        measured = two_pages(300, 450, slow_rows=10)
        record = gate.baseline_record("deadbee", gate.shape_of(20, 2, 20.0, "https://x.test"),
                                      measured, claims.claim_latency(measured))
        assert record["latency"]["p50_ms"] == 300
        assert record["latency"]["floor_p50_ms"] == 80
        assert record["latency"]["floor_p95_ms"] == 120
        assert record["latency"]["p50_rows"] == 10


# ── 1d. retries are the box, not the release ─────────────────────────────────

class TestRetriesAreTheBox:
    """`X-Supabase-Roundtrips` counts *attempts*: a retried read costs two.

    That was deliberate — "a release that makes retries routine pays the database
    twice" — but it made the number answer two questions at once. The measurement
    above is the whole argument: identical code, byte-identical pages, and
    `/teacher/dashboard` reporting 1 round-trip one day and 3 the next. Scored as a
    ratio, that is a regression on a release that changed nothing.

    So attempts are reported and never scored; what a render *issued* is what a
    release changes, and only that number is compared.
    """

    def test_round_trips_that_were_retried_do_not_refuse_the_release(self):
        base = baseline_from(200, 400, payload=57_570, queries=1, asked=1, rows=12,
                             floor_bytes=45_285, floor_queries=1)
        now = summary(205, 420, payload=57_570, queries=3, asked=1, rows=12)
        assert gate.regression(base, now) == [], (
            "one query, served three times because the transport retried it, is the "
            "measured production case and must not read as a slower release")

    def test_and_the_notes_say_so_in_words(self):
        base = baseline_from(200, 400, payload=57_570, queries=1, asked=1, rows=12,
                             floor_bytes=45_285, floor_queries=1)
        now = summary(205, 420, payload=57_570, queries=3, asked=1, rows=12)
        notes = " ".join(gate.explanations(base, now))
        assert "retr" in notes.lower() and "box" in notes.lower(), notes

    def test_more_queries_than_the_render_issued_are_still_the_release(self):
        base = baseline_from(200, 400, payload=57_570, queries=1, asked=1, rows=12,
                             floor_bytes=45_285, floor_queries=1)
        now = summary(205, 420, payload=57_570, queries=9, asked=9, rows=12)
        reasons = gate.regression(base, now)
        assert reasons and "queries" in reasons[0], reasons

    def test_a_growing_dataset_is_reported_as_such(self):
        base = baseline_from(200, 400, **TestWhatTheDataExplains.TEN_X)
        now = summary(205, 420, payload=90_000, queries=21, asked=21, rows=500)
        notes = " ".join(gate.explanations(base, now))
        assert "10" in notes and "row" in notes.lower(), notes

    def test_nothing_is_explained_when_nothing_moved(self):
        base = baseline_from(200, 400, **TestWhatTheDataExplains.TEN_X)
        now = summary(205, 420, payload=45_000, queries=3, asked=3, rows=50)
        assert gate.explanations(base, now) == [], (
            "the gate's output stays quiet on a release that changed nothing")


# ── 1e. the box's baseline can go stale ──────────────────────────────────────
#
# A baseline is a description of *this box* on the day it was measured, and it is
# rewritten on every release that passes — so it ages exactly when releases stop
# passing. Leave a stalled box alone for a fortnight and the description can stop
# being true without any release doing anything: the host gets busier, a neighbour
# appears, a kernel or an nginx version moves. The comparison still runs and still
# diverges, but it is weighing today's box against a description of a box that no
# longer exists — and the release it refuses is charged for drift it did not cause.
#
# The remedy is to recognize the staleness and re-baseline on the box as it is now.
# That is only safe to do without an operator when the release cannot be the cause:
# a release that changed nothing the gate measures is serving the very same page
# bytes the baseline measured, so the difference is necessarily the box. When the
# release *did* change a measured page it stays the suspect and is compared as
# before, however old the baseline is.

class TestAStaleBaseline:
    def _now(self):
        return gate.datetime(2026, 9, 26, tzinfo=gate.timezone.utc)

    def test_age_is_read_from_the_record(self):
        base = baseline_from(200, 400, measured_at="2026-09-12T00:00:00+00:00")
        assert gate.baseline_age_seconds(base, now=self._now()) == pytest.approx(14 * 86400)

    def test_a_baseline_with_no_timestamp_is_not_called_stale(self):
        """Not measuring the age is not evidence that it is old.

        The same rule the gate follows everywhere: a part that could not be read says
        so and takes the conservative branch. Treating a missing timestamp as stale
        would hand every pre-timestamp baseline an exemption nobody measured.
        """
        base = baseline_from(200, 400)
        assert gate.baseline_age_seconds(base, now=self._now()) is None
        assert gate.baseline_stale_reason(base, gate.DEFAULT_BASELINE_MAX_AGE_S,
                                          now=self._now()) is None

    def test_a_baseline_that_cannot_be_parsed_is_not_called_stale(self):
        base = baseline_from(200, 400, measured_at="not a date")
        assert gate.baseline_age_seconds(base, now=self._now()) is None

    def test_an_old_baseline_says_how_old_and_names_the_commit(self):
        base = baseline_from(200, 400, commit="deadbeef",
                             measured_at="2026-09-01T00:00:00+00:00")
        why = gate.baseline_stale_reason(base, 14 * 86400, now=self._now())
        assert why and "25" in why and "deadbeef" in why, why

    def test_a_recent_baseline_is_not_stale(self):
        base = baseline_from(200, 400, measured_at="2026-09-25T00:00:00+00:00")
        assert gate.baseline_stale_reason(base, 14 * 86400, now=self._now()) is None

    def test_the_window_can_be_disabled(self):
        base = baseline_from(200, 400, measured_at="2020-01-01T00:00:00+00:00")
        assert gate.baseline_stale_reason(base, 0, now=self._now()) is None, (
            "a zero window must mean 'never stale', not 'always stale'")

    def test_only_a_path_that_can_move_a_measured_page_counts(self):
        moved = gate.measured_surface_changed(
            ["docs/AUTO_DEPLOY.md", "tests/unit/test_x.py", "AGENTS.md",
             "app/routes/teacher.py"])
        assert moved == ["app/routes/teacher.py"], moved

    def test_inert_when_every_changed_path_is_outside_the_surface(self, monkeypatch):
        monkeypatch.setattr(gate, "changed_paths",
                            lambda *a, **k: ["docs/AUTO_DEPLOY.md", "AGENTS.md"])
        assert gate.release_is_inert(ROOT, "a" * 40, "b" * 40) is True

    def test_not_inert_when_a_measured_page_changed(self, monkeypatch):
        monkeypatch.setattr(gate, "changed_paths",
                            lambda *a, **k: ["app/templates/teacher/results.html"])
        assert gate.release_is_inert(ROOT, "a" * 40, "b" * 40) is False

    def test_unknown_when_git_cannot_place_the_commits(self, monkeypatch):
        monkeypatch.setattr(gate, "changed_paths", lambda *a, **k: None)
        assert gate.release_is_inert(ROOT, "a" * 40, "b" * 40) is None

    def test_unknown_without_both_commits(self, monkeypatch):
        """An unnamed release is not an inert one — the same rule as a stale age."""
        monkeypatch.setattr(gate, "changed_paths", lambda *a, **k: ["app/x.py"])
        assert gate.release_is_inert(ROOT, "abc1234", "b" * 40) is None
        assert gate.release_is_inert(ROOT, None, "b" * 40) is None
        assert gate.release_is_inert(ROOT, "a" * 40, "") is None

    def test_changed_paths_says_unknown_rather_than_nothing_changed(self):
        """The real `git`, not a stub: a commit it cannot place is not a diff of []

        An empty list means "these two commits are the same tree", which would make
        every release look inert. Git failing to answer must be a different value, or
        the exemption would fire on a repository the gate cannot read at all.
        """
        assert gate.changed_paths(ROOT, "0" * 40, "1" * 40) is None, (
            "a commit git cannot resolve read as 'nothing changed between them'")

    @pytest.mark.skipif(not (ROOT / ".git").exists(), reason="needs a git checkout")
    def test_changed_paths_is_empty_for_the_same_commit(self):
        assert gate.changed_paths(ROOT, "HEAD", "HEAD") == []


# ── 2. the baseline survives a bad release ───────────────────────────────────

class TestTheBaseline:
    def test_a_missing_broken_or_empty_baseline_reads_as_absent(self, tmp_path):
        assert gate.load_baseline(tmp_path / "nope.json") is None
        broken = tmp_path / "broken.json"
        broken.write_text("{not json", encoding="utf-8")
        assert gate.load_baseline(broken) is None
        empty = tmp_path / "empty.json"
        empty.write_text(json.dumps({"commit": "x"}), encoding="utf-8")
        assert gate.load_baseline(empty) is None, "a baseline with no latency is not one"

    def test_saving_writes_the_record_and_leaves_no_partial_file(self, tmp_path):
        target = tmp_path / "perf" / "baseline.json"
        record = baseline_from(200, 400)
        assert "updated" in gate.save_baseline(target, record)
        assert json.loads(target.read_text(encoding="utf-8"))["commit"] == "abc1234"
        assert [p.name for p in target.parent.iterdir()] == ["baseline.json"], (
            "the temporary file used for the atomic write must not survive")

    def test_a_baseline_that_cannot_be_written_is_reported_not_raised(self, tmp_path):
        """Best-effort: a read-only /var/lib must not turn a passing release into a
        failed one, but it must not be silent either."""
        blocker = tmp_path / "not-a-dir"
        blocker.write_text("", encoding="utf-8")
        message = gate.save_baseline(blocker / "baseline.json", baseline_from(200, 400))
        assert "could not write" in message and "not fatal" in message

    def test_the_record_keeps_the_numbers_a_later_investigation_needs(self):
        measured = summary(180, 360)
        record = gate.baseline_record("deadbee", gate.shape_of(20, 2, 20.0, "https://x.test"),
                                      measured, claims.claim_latency(measured))
        assert record["commit"] == "deadbee"
        assert record["latency"]["p50_ms"] == 180 and record["latency"]["p95_ms"] == 360
        assert record["shape"]["sessions"] == 20
        assert record["measured_at"]


# ── 3. an unanswerable question is not a verdict ─────────────────────────────

class TestTheShape:
    def test_the_same_reference_load_compares_clean(self):
        baseline = baseline_from(200, 400)
        same = gate.shape_of(baseline["shape"]["sessions"], baseline["shape"]["teachers"],
                             baseline["shape"]["duration_s"],
                             baseline["shape"]["base"] + "/")   # a trailing slash is not a change
        assert gate.shape_mismatch(baseline, same) is None

    def test_a_changed_reference_load_says_which_setting_changed(self):
        baseline = baseline_from(200, 400)
        for field, value in (("sessions", 40), ("teachers", 5), ("duration_s", 60.0),
                             ("base", "https://other.test")):
            shape = dict(baseline["shape"], **{field: value})
            why = gate.shape_mismatch(baseline, shape)
            assert why, f"a different {field} must not be compared against the baseline"
            assert field in why, f"the message must name {field}: {why}"


# ── 4. the whole path, against a local server and a fake harness ─────────────

FAKE_HARNESS = '''\
import json, os, sys
from pathlib import Path
Path(os.environ["FAKE_MARKER"]).write_text(" ".join(sys.argv[1:]), encoding="utf-8")
out = sys.argv[sys.argv.index("--json") + 1]
Path(out).write_text(Path(os.environ["FAKE_SUMMARY"]).read_text(encoding="utf-8"),
                     encoding="utf-8")
'''


class _Health(BaseHTTPRequestHandler):
    #: A live flag dict rather than a class attribute, so a test can make the box
    #: busy *in place*. A second server would land on a different port, and the port
    #: is part of the reference load — so the gate would refuse the comparison
    #: before it ever probed, and a test about a busy box would never reach the busy
    #: path. (It didn't: that test was passing on the port mismatch.)
    #:
    #: `pages` is per-path, because the box can be busy on the page it renders while
    #: the machine endpoint stays instant — and *that* difference is what the quiet
    #: probe has to be able to see.
    flags: dict = {"status": 200, "slow_s": 0.0, "pages": {}}

    def do_GET(self):  # noqa: N802 - http.server's interface
        page = (self.flags.get("pages") or {}).get(self.path) or {}
        slow = float(page.get("slow_s", self.flags["slow_s"]))
        if slow:
            import time
            time.sleep(slow)
        self.send_response(int(page.get("status", self.flags["status"])))
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):  # keep the test output readable
        pass


@pytest.fixture
def health_server():
    """A loopback box, so the gate's 'is the box quiet' probe has an answer."""
    holder = {}

    def start(status=200, slow_s=0.0, pages=None):
        flags = {"status": status, "slow_s": slow_s, "pages": dict(pages or {})}
        handler = type("H", (_Health,), {"flags": flags})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        holder["server"] = server
        holder["flags"] = flags
        return f"http://127.0.0.1:{server.server_address[1]}"

    #: The same box, changing behaviour without changing address.
    start.now = lambda **kw: holder["flags"].update(kw)

    yield start
    holder.get("server") and holder["server"].shutdown()


@pytest.fixture
def workbench(tmp_path, monkeypatch):
    """A roster, a fake harness and a summary file the harness will echo back."""
    roster = tmp_path / "roster.json"
    roster.write_text(json.dumps(
        [{"role": "murid", "email": f"m{i}@x", "password": "p"} for i in range(25)]
        + [{"role": "guru", "email": f"g{i}@x", "password": "p"} for i in range(3)]),
        encoding="utf-8")

    harness = tmp_path / "fake_harness.py"
    harness.write_text(FAKE_HARNESS, encoding="utf-8")

    summary_file = tmp_path / "summary.json"
    summary_file.write_text(json.dumps(summary(200, 400)), encoding="utf-8")
    marker = tmp_path / "probe-ran.txt"

    monkeypatch.setenv("FAKE_SUMMARY", str(summary_file))
    monkeypatch.setenv("FAKE_MARKER", str(marker))

    return {
        "tmp": tmp_path,
        "roster": roster,
        "harness": harness,
        "summary": summary_file,
        "marker": marker,
        "baseline": tmp_path / "baseline.json",
        "evidence": tmp_path / "history.jsonl",
    }


def run_gate(bench, base, monkeypatch, overrides=None, *extra):
    """Call the real main() the way the deploy does: through the environment."""
    env = {"PERF_BASE_URL": base, "PERF_ROSTER": str(bench["roster"]),
           "PERF_SESSIONS": "20", "PERF_TEACHERS": "2", "PERF_DURATION": "1",
           "PERF_BASELINE": str(bench["baseline"]), "PERF_EVIDENCE": str(bench["evidence"])}
    env.update(overrides or {})
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    argv = ["perf_gate.py", "--harness", str(bench["harness"]), *extra]
    monkeypatch.setattr(sys, "argv", argv)
    return gate.main()


class TestTheGateEndToEnd:
    def test_the_first_run_becomes_the_baseline_and_passes(self, workbench, health_server,
                                                           monkeypatch, capsys):
        bench, base = workbench, health_server()
        assert run_gate(bench, base, monkeypatch) == gate.EXIT_OK
        out = capsys.readouterr().out
        assert "IS the baseline" in out, "a self-arming gate has to say so"
        assert bench["baseline"].exists()
        assert json.loads(bench["baseline"].read_text(encoding="utf-8"))["latency"]["p50_ms"] == 200

    def test_a_slower_release_is_refused_and_the_baseline_is_untouched(self, workbench,
                                                                      health_server,
                                                                      monkeypatch, capsys):
        bench, base = workbench, health_server()
        assert run_gate(bench, base, monkeypatch) == gate.EXIT_OK
        before = bench["baseline"].read_bytes()

        bench["summary"].write_text(json.dumps(summary(700, 1500)), encoding="utf-8")
        assert run_gate(bench, base, monkeypatch) == gate.EXIT_REGRESSED
        out = capsys.readouterr().out
        assert "REGRESSED" in out and "confirming with a second probe" in out
        assert "--rebaseline" in out, (
            "the way out of a deliberate slowdown has to be in the message")
        assert bench["baseline"].read_bytes() == before, (
            "a refused release must not become the yardstick for the next one")

    def test_a_release_inside_the_slack_is_still_accepted(self, workbench, health_server,
                                                          monkeypatch):
        bench, base = workbench, health_server()
        assert run_gate(bench, base, monkeypatch) == gate.EXIT_OK
        bench["summary"].write_text(json.dumps(summary(260, 500)), encoding="utf-8")
        assert run_gate(bench, base, monkeypatch) == gate.EXIT_OK

    def test_a_bigger_dataset_is_not_read_as_a_slower_release(self, workbench,
                                                              health_server, monkeypatch,
                                                              capsys):
        """The whole path: the baseline records the floor, and the school grows.

        Without the normalization this is 700/300 = 2.33x and a rollback; what the
        baseline page would cost on four times the rows is 100 + 200*4 = 900 ms.
        """
        bench, base = workbench, health_server()
        bench["summary"].write_text(json.dumps(two_pages(300, 450, slow_rows=10)),
                                    encoding="utf-8")
        assert run_gate(bench, base, monkeypatch) == gate.EXIT_OK
        baseline = json.loads(bench["baseline"].read_text(encoding="utf-8"))
        assert baseline["latency"]["floor_p50_ms"] == 80, (
            "the baseline did not record the floor, so the next release has nothing to "
            "explain a growth with")

        bench["summary"].write_text(json.dumps(two_pages(700, 1000, slow_rows=40)),
                                    encoding="utf-8")
        assert run_gate(bench, base, monkeypatch) == gate.EXIT_OK
        out = capsys.readouterr().out
        assert "rows" in out.lower() or "data accounts" in out.lower(), out

    def test_a_slower_page_on_the_same_dataset_is_still_refused(self, workbench,
                                                                health_server, monkeypatch):
        bench, base = workbench, health_server()
        bench["summary"].write_text(json.dumps(two_pages(300, 450, slow_rows=10)),
                                    encoding="utf-8")
        assert run_gate(bench, base, monkeypatch) == gate.EXIT_OK
        bench["summary"].write_text(json.dumps(two_pages(700, 1000, slow_rows=10)),
                                    encoding="utf-8")
        assert run_gate(bench, base, monkeypatch) == gate.EXIT_REGRESSED

    def test_a_faster_release_moves_the_baseline(self, workbench, health_server, monkeypatch):
        bench, base = workbench, health_server()
        assert run_gate(bench, base, monkeypatch) == gate.EXIT_OK
        bench["summary"].write_text(json.dumps(summary(90, 180)), encoding="utf-8")
        assert run_gate(bench, base, monkeypatch) == gate.EXIT_OK
        assert json.loads(bench["baseline"].read_text(encoding="utf-8"))["latency"]["p50_ms"] == 90

    def test_a_busy_box_is_not_a_verdict(self, workbench, health_server, monkeypatch,
                                         capsys):
        """The box, not the release: a 503 keeps the release.

        Same address throughout, busy by flipping the handler in place. A second
        server would sit on another port, and the port is part of the reference
        load — the gate would refuse on the shape before probing anything, which is
        what this test used to do while claiming to test a busy box.
        """
        bench, base = workbench, health_server()
        assert run_gate(bench, base, monkeypatch) == gate.EXIT_OK
        before = bench["baseline"].read_bytes()

        bench["summary"].write_text(json.dumps(summary(900, 1800)), encoding="utf-8")
        health_server.now(status=503)
        assert run_gate(bench, base, monkeypatch) == gate.EXIT_CANNOT_RUN
        assert "CANNOT MEASURE" in capsys.readouterr().out
        assert bench["baseline"].read_bytes() == before, (
            "a box that could not be measured wrote a result anyway")

    def test_a_box_busy_on_the_page_it_renders_is_not_a_verdict(self, workbench,
                                                               health_server,
                                                               monkeypatch, capsys):
        """The quiet check has to measure a page, or a busy box reads as a bad release.

        `/health` renders nothing and touches no page code, so a box whose workers
        are saturated rendering student pages still answers it instantly. Judging
        *that* says "idle", the gate loads a box it should have left alone, and the
        pages it then measures are the students' slow ones — a confirmed divergence,
        and a rollback for a release that changed nothing. `pages` makes the page
        busy while /health stays fast; the gate must stop before it places any load.
        """
        bench, base = workbench, health_server()
        assert run_gate(bench, base, monkeypatch) == gate.EXIT_OK
        before = bench["baseline"].read_bytes()
        bench["marker"].unlink(missing_ok=True)

        bench["summary"].write_text(json.dumps(summary(900, 1800)), encoding="utf-8")
        health_server.now(pages={"/": {"status": 503}})

        assert run_gate(bench, base, monkeypatch) == gate.EXIT_CANNOT_RUN
        out = capsys.readouterr().out
        assert "CANNOT MEASURE" in out, out
        assert not bench["marker"].exists(), (
            "the gate put load on a box it had already found busy")
        assert bench["baseline"].read_bytes() == before, (
            "a box that could not be measured wrote a result anyway")

    def test_a_changed_reference_load_is_refused_before_any_load_is_placed(self, workbench,
                                                                          health_server,
                                                                          monkeypatch, capsys):
        bench, base = workbench, health_server()
        assert run_gate(bench, base, monkeypatch) == gate.EXIT_OK
        bench["marker"].unlink(missing_ok=True)

        # Exit 4, not 2: the comparison this gate exists to make is not happening at
        # all, so the deploy must not treat the release as measured. The message is
        # the checker's own and names the remedy.
        assert run_gate(bench, base, monkeypatch, {"PERF_SESSIONS": "24"}) == gate.EXIT_NOT_ARMED
        out = capsys.readouterr().out
        assert "reference load changed" in out and "re-baseline" in out.replace("rebaseline",
                                                                                "re-baseline")
        assert not bench["marker"].exists(), (
            "the gate compared nothing and should not have put load on the box")

    def test_a_roster_too_small_for_the_reference_load_is_not_armed(self, workbench,
                                                                  health_server,
                                                                  monkeypatch, capsys):
        bench, base = workbench, health_server()
        # The roster holds 25 murid; this reference load cannot be staffed.
        assert run_gate(bench, base, monkeypatch, {"PERF_SESSIONS": "40"}) == gate.EXIT_NOT_ARMED
        assert "reusing logins" in capsys.readouterr().out

    def test_check_validates_the_plumbing_without_loading_anything(self, workbench,
                                                                  monkeypatch, capsys):
        bench = workbench
        monkeypatch.setenv("PERF_ROSTER", str(bench["roster"]))
        monkeypatch.setenv("PERF_SESSIONS", "20")
        monkeypatch.setenv("PERF_BASELINE", str(bench["baseline"]))
        monkeypatch.setattr(sys, "argv", ["perf_gate.py", "--harness", str(bench["harness"]),
                                          "--check"])
        assert gate.main() == gate.EXIT_OK
        assert "CHECK OK" in capsys.readouterr().out
        assert not bench["marker"].exists()

    def test_every_run_appends_evidence(self, workbench, health_server, monkeypatch):
        bench, base = workbench, health_server()
        assert run_gate(bench, base, monkeypatch) == gate.EXIT_OK
        bench["summary"].write_text(json.dumps(summary(700, 1500)), encoding="utf-8")
        assert run_gate(bench, base, monkeypatch) == gate.EXIT_REGRESSED
        lines = [json.loads(l) for l in bench["evidence"].read_text(encoding="utf-8").splitlines()]
        assert [l["verdict"] for l in lines] == ["baseline", "regressed"]
        assert lines[1]["reasons"], "a refusal has to leave its reasons in the history"

    def test_every_run_indexes_the_record_it_just_wrote(self, workbench, health_server,
                                                        monkeypatch):
        """The index is what lets the page find a judgement past its read window.

        Each offset has to point at that judgement's own line — an index that only
        roughly points is worse than none, because the reader trusts it.
        """
        bench, base = workbench, health_server()
        sha = "a" * 40
        assert run_gate(bench, base, monkeypatch, None, "--commit", sha) == gate.EXIT_OK
        bench["summary"].write_text(json.dumps(summary(700, 1500)), encoding="utf-8")
        assert run_gate(bench, base, monkeypatch, None, "--commit", sha) == gate.EXIT_REGRESSED

        index = gate.index_path(bench["evidence"])
        assert index.exists(), "no index was written beside the history"
        entries = [json.loads(l) for l in index.read_text(encoding="utf-8").splitlines()]
        assert len(entries) == 2, f"one entry per judgement expected, got {entries!r}"
        data = bench["evidence"].read_bytes()
        for entry in entries:
            line = data[entry["offset"]:].split(b"\n", 1)[0]
            record = json.loads(line.decode("utf-8"))
            assert record["commit"] == entry["commit"], (
                f"the index offset {entry['offset']} does not point at its own "
                f"record: {record.get('commit')!r}")

    def test_the_index_sits_beside_the_history_it_indexes(self):
        assert gate.index_path(Path("/x/perf/history.jsonl")) == \
            Path("/x/perf/history.jsonl.index")

    def test_a_judgement_with_no_commit_is_not_indexed(self, tmp_path):
        evidence = tmp_path / "history.jsonl"
        gate.record_evidence(evidence, {"verdict": "pass"})
        assert evidence.exists(), "the history is the evidence; it must still be written"
        assert not gate.index_path(evidence).exists(), (
            "a record with no commit names nothing a page could look up")

    def test_a_release_that_ships_a_heavier_page_is_refused(self, workbench, health_server,
                                                           monkeypatch, capsys):
        """The whole path: a page that got heavier, with latency unchanged.

        This is the release the latency axis cannot see. Same response time, 300 KB
        more down every student's phone.
        """
        bench, base = workbench, health_server()
        bench["summary"].write_text(
            json.dumps(summary(200, 400, payload=100_000, queries=6)), encoding="utf-8")
        assert run_gate(bench, base, monkeypatch) == gate.EXIT_OK
        before = bench["baseline"].read_bytes()
        assert json.loads(before)["page_cost"]["bytes"] == 100_000, (
            "the baseline has to record the cost, or there is nothing to compare next time")

        bench["summary"].write_text(
            json.dumps(summary(200, 400, payload=400_000, queries=6)), encoding="utf-8")
        assert run_gate(bench, base, monkeypatch) == gate.EXIT_REGRESSED
        out = capsys.readouterr().out
        assert "heaviest page" in out and "GET /student/dashboard" in out, out
        assert bench["baseline"].read_bytes() == before, (
            "a refused release must not become the yardstick")

    def test_a_release_that_adds_queries_is_refused(self, workbench, health_server,
                                                    monkeypatch, capsys):
        bench, base = workbench, health_server()
        bench["summary"].write_text(
            json.dumps(summary(200, 400, payload=100_000, queries=6)), encoding="utf-8")
        assert run_gate(bench, base, monkeypatch) == gate.EXIT_OK

        bench["summary"].write_text(
            json.dumps(summary(200, 400, payload=100_000, queries=12)), encoding="utf-8")
        assert run_gate(bench, base, monkeypatch) == gate.EXIT_REGRESSED
        out = capsys.readouterr().out
        assert "Supabase queries per render" in out, out
        assert "confirming with a second probe" in out, (
            "a cost number is one measurement too: it gets the same second probe as "
            "latency before a release is refused")

    def test_a_run_that_stops_reporting_cost_cannot_measure(self, workbench, health_server,
                                                           monkeypatch, capsys):
        """A harness that stopped reporting must not quietly retire half the gate.

        The baseline knows what a page costs; this run says nothing. Passing would
        mean the payload and query axes stopped being checked without anyone being
        told, which is the silent failure every gate here is built to avoid. Exit 2
        is not a rollback, so the release is kept and the journal says why.
        """
        bench, base = workbench, health_server()
        bench["summary"].write_text(
            json.dumps(summary(200, 400, payload=100_000, queries=6)), encoding="utf-8")
        assert run_gate(bench, base, monkeypatch) == gate.EXIT_OK
        before = bench["baseline"].read_bytes()

        bench["summary"].write_text(json.dumps(summary(200, 400)), encoding="utf-8")
        assert run_gate(bench, base, monkeypatch) == gate.EXIT_CANNOT_RUN
        out = capsys.readouterr().out
        assert "CANNOT MEASURE" in out and "round-trips" in out, out
        assert bench["baseline"].read_bytes() == before

    def _stale_baseline(self, bench):
        """Rewrite the baseline so it describes a box from years ago, not this one."""
        record = json.loads(bench["baseline"].read_text(encoding="utf-8"))
        record["measured_at"] = "2020-01-01T00:00:00+00:00"
        bench["baseline"].write_text(json.dumps(record), encoding="utf-8")
        return bench["baseline"].read_bytes()

    def _verdicts(self, bench) -> list[str]:
        return [json.loads(line)["verdict"]
                for line in bench["evidence"].read_text(encoding="utf-8").splitlines()]

    def test_a_stale_baseline_with_an_inert_release_is_not_quarantined(
            self, workbench, health_server, monkeypatch, capsys):
        """The whole point: a stalled box's old description must not refuse a release
        that changed nothing the gate measures.

        The baseline is rewritten on every passing release, so it ages exactly when
        releases stop passing. This release touches nothing under `app/` — the pages
        whose numbers diverged are the same bytes the baseline measured — so the
        divergence is the box, and the honest remedy is to describe the box as it is
        now rather than quarantine an innocent commit.
        """
        bench, base = workbench, health_server()
        assert run_gate(bench, base, monkeypatch) == gate.EXIT_OK
        self._stale_baseline(bench)
        monkeypatch.setattr(gate, "release_is_inert", lambda *a, **k: True)

        bench["summary"].write_text(json.dumps(summary(700, 1500)), encoding="utf-8")
        assert run_gate(bench, base, monkeypatch) == gate.EXIT_OK
        out = capsys.readouterr().out
        assert "stale" in out.lower() and "re-baselin" in out.lower(), out
        moved = json.loads(bench["baseline"].read_text(encoding="utf-8"))
        assert moved["latency"]["p50_ms"] == 700, (
            "the box as it is now is what the next release must be compared against")
        assert self._verdicts(bench)[-1] == "stale_baseline"

    def test_a_stale_baseline_still_refuses_a_release_that_changed_the_pages(
            self, workbench, health_server, monkeypatch, capsys):
        """Staleness alone is not an exemption: the release stays the suspect when it
        touched the code that renders the measured pages."""
        bench, base = workbench, health_server()
        assert run_gate(bench, base, monkeypatch) == gate.EXIT_OK
        before = self._stale_baseline(bench)
        monkeypatch.setattr(gate, "release_is_inert", lambda *a, **k: False)

        bench["summary"].write_text(json.dumps(summary(700, 1500)), encoding="utf-8")
        assert run_gate(bench, base, monkeypatch) == gate.EXIT_REGRESSED
        assert "REGRESSED" in capsys.readouterr().out
        assert bench["baseline"].read_bytes() == before, (
            "a refused release must not become the yardstick, stale baseline or not")

    def test_a_stale_baseline_git_cannot_place_still_refuses(
            self, workbench, health_server, monkeypatch):
        """A release the gate cannot diff is not an inert one."""
        bench, base = workbench, health_server()
        assert run_gate(bench, base, monkeypatch) == gate.EXIT_OK
        before = self._stale_baseline(bench)
        monkeypatch.setattr(gate, "release_is_inert", lambda *a, **k: None)

        bench["summary"].write_text(json.dumps(summary(700, 1500)), encoding="utf-8")
        assert run_gate(bench, base, monkeypatch) == gate.EXIT_REGRESSED
        assert bench["baseline"].read_bytes() == before

    def test_a_fresh_baseline_with_an_inert_release_is_still_refused(
            self, workbench, health_server, monkeypatch):
        """The exemption is licensed by staleness, and this is the limit that keeps it
        small: a fresh baseline means the box was measured recently, so the divergence
        is new information whose cause is worth an operator's eye."""
        bench, base = workbench, health_server()
        assert run_gate(bench, base, monkeypatch) == gate.EXIT_OK
        before = bench["baseline"].read_bytes()
        monkeypatch.setattr(gate, "release_is_inert", lambda *a, **k: True)

        bench["summary"].write_text(json.dumps(summary(700, 1500)), encoding="utf-8")
        assert run_gate(bench, base, monkeypatch) == gate.EXIT_REGRESSED
        assert bench["baseline"].read_bytes() == before

    def test_the_age_window_can_be_switched_off_from_the_command_line(
            self, workbench, health_server, monkeypatch):
        bench, base = workbench, health_server()
        assert run_gate(bench, base, monkeypatch) == gate.EXIT_OK
        before = self._stale_baseline(bench)
        monkeypatch.setattr(gate, "release_is_inert", lambda *a, **k: True)

        bench["summary"].write_text(json.dumps(summary(700, 1500)), encoding="utf-8")
        assert run_gate(bench, base, monkeypatch,
                        {"PERF_BASELINE_MAX_AGE": "0"}) == gate.EXIT_REGRESSED, (
            "a zero window disables the staleness exemption entirely")
        assert bench["baseline"].read_bytes() == before


# ── 5. the deploy script reads the exit codes the way the gate means them ────

class TestTheDeployWiring:
    script = DEPLOY.read_text(encoding="utf-8")

    def test_the_gate_runs_on_every_release(self):
        assert "deploy/perf_gate.py" in self.script
        assert "/etc/scangrade-perf.conf" in self.script

    def test_it_runs_after_the_reload_and_of_the_measured_code(self):
        """A probe before the reload would measure the previous release."""
        reload_at = self.script.index("reload_app")
        gate_at = self.script.index("deploy/perf_gate.py")
        assert gate_at > reload_at

    def test_it_sits_with_the_other_gates_before_the_release_is_declared_good(self):
        gate_at = self.script.index("# ── Gate 6")
        claims_at = self.script.index("# ── Gate 5")
        success_at = self.script.index('if [ "$HEALTHY" = "1" ]; then')
        assert claims_at < gate_at < success_at, (
            "Gate 6 must sit after the claims gate and before the block that declares the "
            "release deployed, or its verdict is written after the deploy already said OK")

    def test_a_confirmed_regression_rolls_back_only_when_asked(self):
        """Within the confirmed-regression arm, and only there.

        The gate has three other arms that roll back now — exit 4, and the two
        unarmed paths — so counting across the whole section would be measuring the
        wrong thing, and the ordering claim only makes sense inside `*)`.
        """
        block = self.script[self.script.index("# ── Gate 6"):]
        block = block[:block.index('if [ "$HEALTHY" = "1" ]; then')]
        assert 'PERF_ENFORCE' in block
        assert 'if [ "${PERF_ENFORCE:-false}" = "true" ]' in block, (
            "enforcement has to be an explicit switch, like CLAIMS_ENFORCE")
        confirmed = block[block.index("    *)"):]
        assert confirmed.count("HEALTHY=0") == 1
        assert confirmed.index('if [ "${PERF_ENFORCE:-false}" = "true" ]') \
            < confirmed.index("HEALTHY=0")
        assert 'keeping the release' in confirmed, (
            "with enforcement off the release is kept, and the log has to say that")

    def test_could_not_measure_is_logged_as_not_compared(self):
        """Exit 2 is \"the box was in the way\", and it still keeps the release.

        The slice ends at the `4)` arm on purpose: the two answers are adjacent in
        the case statement and mean opposite things, which is the whole point of
        splitting them — see `test_not_armed_does_roll_back`.
        """
        block = self.script[self.script.index("# ── Gate 6"):]
        block = block[:block.index('if [ "$HEALTHY" = "1" ]; then')]
        cant = block[block.index("    2)"):block.index("    4)")]
        assert "HEALTHY=0" not in cant, "exit 2 must never roll a release back"
        assert "NOT compared" in cant

    def test_not_armed_does_roll_back(self):
        """Exit 4 is the different answer: this release was never compared.

        It used to be exit 2 — the same arm as the busy box above — so a box with
        no roster deployed every commit while reporting a comparison it never
        made.
        """
        block = self.script[self.script.index("# ── Gate 6"):]
        block = block[:block.index('if [ "$HEALTHY" = "1" ]; then')]
        not_armed = block[block.index("    4)"):block.index("    *)")]
        assert "HEALTHY=0" in not_armed, (
            "a release that was never compared with the last one that passed is kept")
        assert "NOT ARMED" in not_armed

    def test_a_missing_conf_is_loud_rather_than_silent(self):
        block = self.script[self.script.index("# ── Gate 6"):]
        assert "no $PERF_CONF" in block, (
            "a gate that is not installed must say so on every release, not disappear")

    def test_the_payload_slacks_are_passed_through_to_the_gate(self):
        """Reading a conf and then not passing it is how the claims gate shipped off.

        `perf_gate.py` reads these from the environment; the deploy is the only thing
        that puts them there. A setting the conf names but the deploy drops is a
        setting an operator can change with no effect, which is worse than none.
        """
        loop = self.script[self.script.index("for v in PERF_BASE_URL"):]
        loop = loop[:loop.index("do")]
        for name in ("PERF_BYTES_SLACK", "PERF_ROUNDTRIPS_SLACK"):
            assert name in loop, f"{name} is read from the conf but never passed"

    def test_the_gate_reads_the_slacks_from_the_environment(self):
        source = GATE_PATH.read_text(encoding="utf-8")
        assert 'env_default("PERF_BYTES_SLACK"' in source
        assert 'env_default("PERF_ROUNDTRIPS_SLACK"' in source

    def test_the_gate_reads_the_baseline_age_window_from_the_environment(self):
        """The remedy for a stale baseline is an operator changing this number, and an
        operator changes it in the conf file — so the gate has to read it from there."""
        source = GATE_PATH.read_text(encoding="utf-8")
        assert 'env_default("PERF_BASELINE_MAX_AGE"' in source
        assert "--baseline-max-age" in source


class TestTheInstaller:
    installer = INSTALLER.read_text(encoding="utf-8")
    deploy = DEPLOY.read_text(encoding="utf-8")

    def test_the_installer_writes_the_conf_and_the_state_directory(self):
        assert "PERF_CONF=/etc/scangrade-perf.conf" in self.installer
        assert "/var/lib/scangrade-deploy/perf" in self.installer
        assert 'PERF_BASELINE="/var/lib/scangrade-deploy/perf/baseline.json"' in self.installer

    def test_the_installer_leaves_an_existing_conf_alone(self):
        """Re-running the installer must not silently reset numbers an operator tuned."""
        block = self.installer[self.installer.index("PERF_CONF=/etc"):]
        block = block[:block.index("# ── 4.")]
        assert "already exists — left untouched" in block

    def test_the_gate_is_armed_from_the_first_release(self):
        block = self.installer[self.installer.index("PERF_CONF=/etc"):]
        block = block[:block.index("# ── 4.")]
        assert 'PERF_ENFORCE="true"' in block, (
            "unlike the claims gate, arming this one cannot reject anything: with no "
            "baseline it writes one and passes")

    def test_the_conf_names_the_payload_limits_it_will_be_read_against(self):
        block = self.installer[self.installer.index("PERF_CONF=/etc"):]
        block = block[:block.index("# ── 4.")]
        assert "PERF_BYTES_SLACK=" in block
        assert "PERF_ROUNDTRIPS_SLACK=" in block
        assert "X-Supabase-Roundtrips" in block, (
            "an operator reading the conf should be told where the query count comes from")

    def test_the_conf_names_the_baseline_age_window(self):
        """The remedy for a stale baseline is an operator changing one number, so the
        installer has to write that number where the operator reads it."""
        block = self.installer[self.installer.index("PERF_CONF=/etc"):]
        block = block[:block.index("# ── 4.")]
        assert 'PERF_BASELINE_MAX_AGE="' in block, (
            "the setting exists but the installer never writes it, so the gate's default "
            "is the only value the box can ever take")

    def test_the_installer_proves_the_gate_can_run_with_the_conf_settings(self):
        block = self.installer[self.installer.index("Checking the performance gate"):]
        block = block[:block.index("# ── 4.")]
        assert "--check" in block
        for var in ("PERF_BASE_URL", "PERF_ROSTER", "PERF_SESSIONS", "PERF_TEACHERS",
                    "PERF_DURATION", "PERF_BASELINE", "PERF_EVIDENCE"):
            assert var in block, (
                f"the installer proves the gate runs without passing {var} — that is how a "
                "conf file is generated carefully and then ignored")


# ── 6. the conf file reaches the gate ────────────────────────────────────────

def _conf_names(installer_text: str, conf_var: str) -> set[str]:
    """The settings the installer actually writes into one generated conf file.

    Read out of the heredoc rather than the whole script: the installer also sets
    PERF_CONF, PERF_OUT and PERF_RC for its own use, and none of those belong in
    a conf file or in the gate.
    """
    import re
    heredoc = re.search(rf'cat > "\${conf_var}" <<EOF\n(.*?)\nEOF\n', installer_text, re.S)
    assert heredoc, f"the installer no longer writes {conf_var} from a heredoc"
    return set(re.findall(r"^([A-Z_]+)=", heredoc.group(1), re.M))


class TestTheConfReachesTheGate:
    """A conf file the gate does not read is a gate that is off.

    This is not hypothetical: /etc/scangrade-claims.conf has always been generated
    and passed as environment variables, and claims_gate.py only ever looked at
    argv — so every production run ended at "no --base URL", exit 2, on a gate
    that looked installed and healthy.
    """

    installer = INSTALLER.read_text(encoding="utf-8")
    deploy = DEPLOY.read_text(encoding="utf-8")

    @pytest.mark.parametrize("which", ["perf", "claims"])
    def test_every_generated_setting_is_read_by_its_gate(self, which):
        conf_var, source, switch = GATES[which]
        names = _conf_names(self.installer, conf_var)
        assert names, f"the installer writes no settings into {conf_var} - this test would be vacuous"
        ignored = sorted(n for n in names - {switch} if f'env_default("{n}"' not in source)
        assert not ignored, (
            f"the installer writes these into {conf_var} and the gate never reads them: "
            f"{ignored}. The deploy passes each one as an environment variable, so the gate "
            f"needs env_default() for it.")

    @pytest.mark.parametrize("which", ["perf", "claims"])
    def test_the_deploy_passes_every_generated_setting(self, which):
        conf_var, _, switch = GATES[which]
        names = _conf_names(self.installer, conf_var)
        block = self.deploy[self.deploy.index(f"{conf_var}="):]
        listed = re.search(r"for v in ([^;]+); do", block)
        assert listed, f"the deploy no longer passes {conf_var} settings through the environment"
        missing = sorted(n for n in names - {switch} if n not in listed.group(1))
        assert not missing, (
            f"the deploy sources the conf but does not pass these to the gate: {missing}")

    def test_the_verdict_names_what_was_compared(self):
        """The verdict rule is shared with the claims gate, so the nouns have to
        be this gate's: a refusal that says "the published numbers" would send
        someone looking for a claim to correct when the code is what changed."""
        _, why = gate.cg.verdict(["slow"], ["slow"], **gate.VERDICT_WORDS)
        assert "the previous release" in why and "published" not in why, why
        _, ok = gate.cg.verdict([], None, **gate.VERDICT_WORDS)
        assert "not slower" in ok, ok
        _, contention = gate.cg.verdict(["slow"], [], **gate.VERDICT_WORDS)
        assert "not as a regression" in contention, contention

    def test_the_two_gates_agree_on_what_an_exit_code_means(self):
        assert gate.EXIT_OK == claims.EXIT_OK == 0
        assert gate.EXIT_REGRESSED == claims.EXIT_DIVERGED == 1
        assert gate.EXIT_CANNOT_RUN == claims.EXIT_CANNOT_RUN == 2
