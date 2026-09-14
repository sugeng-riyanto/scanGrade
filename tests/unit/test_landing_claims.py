"""The landing page's performance claims have to be checkable.

What went wrong here was not a typo. The page advertised 46,698 and 74,923
requests over a ten-minute peak with a 0% error rate and sub-second responses,
and nothing in the repository could produce those numbers: they appeared only in
the template, and the harness they were attributed to (``locustfile.py``) posts
to /auth/login-user without a CSRF token -- every login is rejected with 403 --
while treating both 200 and 302 as success, so a *failed* login also counted as
a logged-in user. The accounts it draws from (``siswa1..siswa1000_smp``) do not
exist past number six.

These tests do not try to measure production. They hold the page to two rules
that are cheap to check and that the old page broke:

1. a number that no committed measurement backs must not be advertised;
2. whatever the page credits as its evidence must actually be able to run.
"""
import importlib.util
import json
import re
import sys
from fnmatch import fnmatch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LANDING = ROOT / "app" / "templates" / "landing.html"
HARNESS = ROOT / "loadtest_concurrent.py"
LOCUST = ROOT / "locustfile.py"
MEASUREMENTS = ROOT / "docs" / "measurements"


def _load_gate():
    """The gate and this test read the page the same way, on purpose."""
    spec = importlib.util.spec_from_file_location(
        "claims_gate", ROOT / "deploy" / "claims_gate.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["claims_gate"] = module
    spec.loader.exec_module(module)
    return module


gate = _load_gate()


def page():
    return LANDING.read_text(encoding="utf-8")


# ── 1. no figure survives that nothing here can reproduce ────────────────────

class TestUnsupportedFiguresAreGone:
    """The two withdrawal figures and the 1,000-user block had no artifact.

    A repo-wide search found them only in this template. `docs/` has the 500
    target from the PRD, which is a target and says so; nothing measured them.
    """

    DEAD = ["46.698", "74.923", "Seratus persen request berstatus 2xx"]

    def test_the_withdrawn_numbers_are_not_back(self):
        body = page()
        back = [fig for fig in self.DEAD if fig in body]
        assert not back, (
            f"landing page advertises {back} again. These were removed because no "
            "committed artifact produces them -- if a measurement now exists, cite "
            "the harness and the run that produced it in the same block."
        )

    def test_the_thousand_user_block_is_not_back(self):
        body = page()
        assert ">1.000<" not in body and "1.000 con" not in body, (
            "the 1,000-concurrent-students figure is back. It was withdrawn: the "
            "harness behind it could not authenticate (no CSRF token), and only six "
            "of its thousand demo accounts existed."
        )


# ── 2. what the page credits must be able to run ─────────────────────────────

class TestThePageCreditsSomethingThatWorks:
    """A claim is only as good as the tool it names as evidence."""

    def test_the_locust_claim_is_only_made_if_locust_can_log_in(self):
        body = page()
        if "Locust" not in body and "locust" not in body:
            return
        source = LOCUST.read_text(encoding="utf-8")
        assert "_csrf_token" in source, (
            "the landing page credits Locust, but locustfile.py posts its login "
            "without a CSRF token: every attempt is rejected 403 and no run can "
            "produce the numbers the page shows. Fix the harness, or drop the name."
        )

    def test_the_named_harness_exists_and_can_sustain_a_run(self):
        body = page()
        if "harness" not in body.lower():
            return
        assert HARNESS.exists(), "the page names a load-test harness that is not in the repo"
        source = HARNESS.read_text(encoding="utf-8")
        # A ten-minute peak is an endurance claim, so the harness must have an
        # endurance mode. Without --duration it is a one-pass burst.
        assert "--duration" in source and "def sustain(" in source, (
            "the landing page shows a 10-minute peak, but the harness has no "
            "endurance mode -- its one-pass burst cannot produce that shape of load."
        )


# ── 3. the replacement block states its own terms ────────────────────────────

class TestTheCapacityBlockStatesItsTerms:
    """A capacity number without its configuration is the same claim again."""

    def test_it_names_the_configuration_it_was_measured_on(self):
        body = page()
        assert "1 vCPU" in body, "the capacity block does not say what it was measured on"

    def test_it_shows_the_curve_rather_than_a_single_peak(self):
        body = page()
        for students in (50, 100, 150, 500):
            assert f">{students}<" in body or f">{students}*<" in body, (
                f"the capacity table lost its {students}-student row; one endpoint "
                "without the curve is how the original claim became unfalsifiable."
            )

    def test_the_recommendation_does_not_outrun_the_measurement(self):
        """The page may not recommend more than the box was shown to carry."""
        body = page()
        limit = re.search(r"~(\d+) concurrent students per exam session", body)
        assert limit, "no stated comfortable limit"
        assert int(limit.group(1)) <= 50, (
            "the stated comfortable limit is higher than the highest rate this "
            "configuration was measured to hold. Re-measure before raising it."
        )


# ── 4. every published row is a measurement, and it is the one in the file ────

STUDENT_PAGE = re.compile(r"^GET /student/")


def _fmt_ms_range(values):
    """The page's two formats: milliseconds below a second, seconds above."""
    lo, hi = min(values), max(values)
    if hi < 1000:
        return f"{lo:.0f}\u2013{hi:.0f} ms"
    return f"{lo / 1000:.1f}\u2013{hi / 1000:.1f} s"


def _fmt_seconds(value):
    return f"\u2264 {value / 1000:.1f} s"


def _published_rows():
    """{students: (p50 text, p95 text, error text)} straight off the page."""
    _, rungs = gate.parse_claims(LANDING)
    return {r.students: (r.raw[1], r.raw[2], r.raw[3]) for r in rungs}


def _artifacts_for(rung):
    """Every committed artifact that measured this rung.

    A rung can have more than one: `rung-050.json` is loadtest_concurrent.py's
    60-second endurance run and `locust-050.json` is locustfile.py's. Both are
    measurements of the same load; neither is privileged.
    """
    return sorted(MEASUREMENTS.glob(f"*{rung:03d}.json"))


def _row_from_json(rung):
    """The published bound is the **worst** figure any artifact measured.

    Two harnesses measuring the same rung will not agree to the millisecond. Taking
    the better of the two would advertise a number the box has not been shown to
    hold, and the deploy gate holds the page to 2x of whatever it says — so a
    published p50 that the box has already exceeded is a rollback waiting to
    happen. The conservative union is the only honest reading of both.
    """
    artifacts = _artifacts_for(rung)
    assert artifacts, f"no artifact in docs/measurements/ measures the {rung}-student rung"
    p50, p95, errors = [], [], []
    for path in artifacts:
        data = json.loads(path.read_text(encoding="utf-8"))
        pages = {k: v for k, v in data["per_endpoint"].items() if STUDENT_PAGE.match(k)}
        assert pages, f"{path.name} recorded no student page load"
        p50 += [v["p50"] for v in pages.values()]
        p95 += [v["p95"] for v in pages.values()]
        errors.append(data["error_rate_pct"])
    return (_fmt_ms_range(p50), _fmt_seconds(max(p95)), f"{max(errors):g}%")


def _row_from_500_log():
    """The 500* row came from a 10-minute run; its table is the artifact."""
    text = (MEASUREMENTS / "rung-500-endurance.txt").read_text(encoding="utf-8")
    rows = []
    for line in text.splitlines():
        m = re.match(r"^(GET /student/\S+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s", line)
        if m:
            rows.append((int(m.group(3)), int(m.group(4))))  # p50, p95
    assert rows, "rung-500-endurance.txt has no student page rows to read"
    errors = re.search(r"error rate\s+:.*=\s*([\d.]+)%", text)
    assert errors, "rung-500-endurance.txt does not report an error rate"
    return (
        _fmt_ms_range([p50 for p50, _ in rows]),
        _fmt_seconds(max(p95 for _, p95 in rows)),
        f"{float(errors.group(1)):g}%",
    )


class TestEveryPublishedRowMatchesItsArtifact:
    """A row that cannot be recomputed from a file is a claim, not a measurement.

    The page carries four rows and each one now has an artifact beside it in
    docs/measurements/. These tests recompute the published text from those
    files, so the numbers cannot move in one place without the other — which is
    precisely how the withdrawn 46,698 and 74,923 figures survived: nothing
    connected the page to anything that could produce them.
    """

    def test_the_rows_are_exactly_the_ones_with_artifacts(self):
        published = set(_published_rows())
        assert published == {50, 100, 150, 500}, (
            f"the page publishes rows for {sorted(published)}. Every row needs an "
            "artifact in docs/measurements/ and a measurement that produced it."
        )
        for rung in (50, 100, 150):
            assert (MEASUREMENTS / f"rung-{rung:03d}.json").exists(), (
                f"the {rung}-student row is on the page but its artifact is missing"
            )
        assert (MEASUREMENTS / "rung-500-endurance.txt").exists(), (
            "the 500* row's evidence file is named .txt, not .log: .gitignore excludes "
            "*.log, so as a .log it could never be committed and every fresh clone would "
            "fail this very test."
        )

    def test_every_artifact_behind_a_row_actually_authenticated(self):
        """An artifact from a harness that could not log in measures redirects.

        That is what the withdrawn 46,698 figure was: a pool of logged-out sessions
        following `/auth/login` redirects. A measurement now has to prove it signed
        in before it may back a published number.
        """
        for rung in (50, 100, 150):
            for path in _artifacts_for(rung):
                data = json.loads(path.read_text(encoding="utf-8"))
                assert data["logins_ok"] > 0, f"{path.name} has no successful login"
                assert data["logins_failed"] == 0, (
                    f"{path.name} has {data['logins_failed']} failed logins: its latency figures "
                    "cover a partially logged-out population"
                )
                if "identity_checked" in data and data["identity_checked"]:
                    assert data["identity_ok"] == data["identity_checked"], (
                        f"{path.name} could not confirm that its sessions own the accounts they "
                        "signed in as"
                    )

    def test_each_measured_row_matches_its_json(self):
        rows = _published_rows()
        for rung in (50, 100, 150):
            assert rows[rung] == _row_from_json(rung), (
                f"the {rung}-student row says {rows[rung]} but the measurement in "
                f"docs/measurements/rung-{rung:03d}.json says {_row_from_json(rung)}. "
                "Re-measure or correct the page; do not round a row into agreement."
            )

    def test_the_500_row_matches_the_recorded_run(self):
        rows = _published_rows()
        assert rows[500] == _row_from_500_log(), (
            f"the 500* row says {rows[500]} but the recorded run says "
            f"{_row_from_500_log()}"
        )

    def test_the_headline_card_matches_the_50_row(self):
        """The card above the table is the same number written twice."""
        body = page()
        p50 = _published_rows()[50][0]
        assert f"p50 {p50}" in body, (
            f"the 'concurrent students' card does not show the measured p50 {p50} — "
            "the card and the table disagree, and the card is what people read."
        )

    def test_the_footnote_names_where_the_numbers_live(self):
        body = page()
        assert "docs/measurements/" in body, (
            "the page states numbers without saying where they come from. A reader "
            "cannot check a claim whose evidence is not named."
        )


# ── 5. an artifact git will not carry is not evidence ─────────────────────────

GITIGNORE = ROOT / ".gitignore"


def _ignored_by(patterns, relpath):
    """Apply `.gitignore` the way git does — closely enough for these files.

    Only the patterns that can plausibly match something under docs/ matter
    (`*.log` is the one that bit us), but the list is walked in order so a later
    `!` re-inclusion is honoured rather than assumed away.
    """
    ignored = False
    for raw in patterns:
        pattern = raw.strip()
        if not pattern or pattern.startswith("#"):
            continue
        negate = pattern.startswith("!")
        if negate:
            pattern = pattern[1:]
        pattern = pattern.rstrip("/").lstrip("/")
        matched = (
            relpath.match(pattern) if "/" in pattern
            else fnmatch(relpath.name, pattern)
        )
        if matched:
            ignored = not negate
    return ignored


class TestEvidenceSurvivesAFreshClone:
    """The 500* row and the Locust console output were both written as `.log`.

    `.gitignore` excludes `*.log`, so those files lived only on the machine that
    produced them: a fresh clone had a published row whose evidence did not exist,
    and the test that reads the run failed on checkout. The suffix is part of the
    claim, not a detail of it.
    """

    def test_the_ignore_rules_do_not_swallow_the_evidence(self):
        patterns = GITIGNORE.read_text(encoding="utf-8").splitlines()
        files = sorted(p for p in MEASUREMENTS.rglob("*") if p.is_file())
        assert files, "docs/measurements/ is empty; every published row needs an artifact"
        for path in files:
            rel = path.relative_to(ROOT)
            assert not _ignored_by(patterns, rel), (
                f"{rel} is excluded by .gitignore, so no fresh clone will have it and "
                "the row it backs cannot be checked. Rename it to a suffix git carries "
                "(a `.log` becomes `.txt`) or add an exception for it."
            )

    def test_the_rule_this_guards_still_exists(self):
        """Prove the check is not vacuous: `*.log` is exactly what it defends against."""
        patterns = GITIGNORE.read_text(encoding="utf-8").splitlines()
        assert _ignored_by(patterns, Path("docs/measurements/x.log")), (
            "this guard no longer demonstrates anything: `*.log` is not ignored any "
            "more. Update the guard to the rule that replaced it — do not delete it."
        )
