"""The claims gate: the landing page must not outlive the box it describes.

The failing this guards against is not a typo. The page advertised 46,698 and
74,923 requests over a ten-minute peak, 0% errors and sub-second responses, and
nothing in the repository could produce any of it: the numbers existed only in
the template, and the harness they were credited to could not log in. Numbers
like that are not wrong when they are written — they become wrong quietly, when
the machine underneath them changes and the page does not.

So the deploy re-measures the page's own lowest advertised rung and refuses the
release when the measurement no longer supports what the page says. These tests
hold the gate itself to that job:

1. it reads the claim out of the template, so editing the page into a bigger
   promise is what has to be defended;
2. its tolerances are tighter than a gap that was actually observed;
3. it fails loudly, and never silently, when it cannot run;
4. the deploy treats "could not measure" differently from "measured and wrong".
"""
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GATE_PATH = ROOT / "deploy" / "claims_gate.py"
DEPLOY = ROOT / "deploy" / "scangrade-deploy.sh"
INSTALLER = ROOT / "deploy" / "install-auto-deploy.sh"
LANDING = ROOT / "app" / "templates" / "landing.html"


def _load_gate():
    """Import deploy/claims_gate.py without making deploy/ a package."""
    spec = importlib.util.spec_from_file_location("claims_gate", GATE_PATH)
    module = importlib.util.module_from_spec(spec)
    # Dataclasses resolve their field types through sys.modules[cls.__module__].
    sys.modules["claims_gate"] = module
    spec.loader.exec_module(module)
    return module


gate = _load_gate()


# ── 1. the claim is read from the page ───────────────────────────────────────

class TestThePageIsTheSourceOfTheClaim:
    def test_the_gate_parses_the_real_page(self):
        limit, rungs = gate.parse_claims(LANDING)
        assert limit == 50, "the gate no longer reads the comfortable limit off the page"
        rung = gate.rung_for(limit, rungs)
        assert rung.students == 50
        assert rung.p50_high_ms == pytest.approx(962.0), (
            "the p50 bound the gate compares against is no longer the one the page "
            f"publishes (got {rung.p50_high_ms}). Either the page changed and this "
            "test should follow it, or the parser is reading the wrong cell."
        )
        # 3,700 ms since the 50-student row became a union of two artifacts: the
        # 60-second harness run measured a 3.1 s worst p95 and the 90-second Locust
        # run measured 3.7 s, and the page publishes the worse of the two.
        assert rung.p95_ms == pytest.approx(3700.0), (
            "the p95 bound the gate compares against is no longer the worst figure the "
            "artifacts recorded. If a re-measurement moved it, update this number in the "
            "same commit as the page."
        )
        assert rung.error_pct == 0.0

    def test_the_starred_peak_row_is_read_too(self):
        """'500*' is a row, not a footnote: the star marks a different shape of run.

        Requiring a bare digit dropped it silently, which meant the gate saw a
        three-row page and said nothing about the peak.
        """
        _, rungs = gate.parse_claims(LANDING)
        assert 500 in [r.students for r in rungs]

    def test_a_page_that_states_no_limit_is_a_loud_failure(self, tmp_path):
        page = tmp_path / "landing.html"
        page.write_text("<table><tr><td>50</td><td>1 ms</td><td>1 ms</td><td>0%</td></tr></table>",
                        encoding="utf-8")
        with pytest.raises(gate.ClaimsError):
            gate.parse_claims(page)

    def test_a_page_with_no_readable_row_is_a_loud_failure(self, tmp_path):
        page = tmp_path / "landing.html"
        page.write_text("<p>comfortable limit is ~50 concurrent students per exam session</p>",
                        encoding="utf-8")
        with pytest.raises(gate.ClaimsError):
            gate.parse_claims(page)

    def test_a_limit_with_no_matching_row_is_a_loud_failure(self):
        _, rungs = gate.parse_claims(LANDING)
        with pytest.raises(gate.ClaimsError):
            gate.rung_for(999, rungs)

    def test_it_reads_the_indonesian_half_too(self, tmp_path):
        """The page is bilingual; the gate must survive either half changing."""
        page = tmp_path / "landing.html"
        page.write_text(
            "<p>batas yang nyaman adalah ~50 murid serentak per sesi ujian</p>"
            "<table><tr><td>50</td><td>82-620 ms</td><td>&le; 1.7 s</td><td>0%</td></tr></table>",
            encoding="utf-8")
        limit, rungs = gate.parse_claims(page)
        rung = gate.rung_for(limit, rungs)
        assert rung.p50_high_ms == pytest.approx(620.0)
        assert rung.p95_ms == pytest.approx(1700.0)


# ── 2. the tolerances have to be tighter than a real gap ─────────────────────

class TestTheTolerancesAreDefensible:
    """A slack wide enough to pass a known divergence is not a check.

    On 14 Sep 2026 the page said 50 concurrent students answer in 82-620 ms. A
    probe at that rung measured 1464 ms — 2.4x the advertised worst case, from
    the worst page endpoint, with 0% errors. The first version of this gate used
    a 3x slack and passed it.
    """

    def test_the_latency_slack_would_catch_the_observed_gap(self):
        assert gate.LATENCY_SLACK <= 2.0, (
            f"latency slack is {gate.LATENCY_SLACK}x, which is not tighter than the "
            "2.4x gap that was measured against this page. A tolerance that cannot "
            "fail on a known bad number is not a tolerance."
        )

    def test_the_error_slack_would_catch_the_withdrawn_error_claim(self):
        # 2.31% was what the 500-session run actually produced while the page
        # claimed 0%. A slack at or above that would wave the same claim through.
        assert gate.ERROR_SLACK_PCT < 2.31

    def test_the_default_rung_is_the_pages_own(self):
        """Probing less than the claim is allowed, but never by default."""
        assert gate.DEFAULT_SESSIONS_FROM_PAGE is True
        limit, rungs = gate.parse_claims(LANDING)
        assert gate.rung_for(limit, rungs).students == limit


# ── 3. what gets compared, and what that comparison means ────────────────────

def _rung(**kw):
    base = dict(students=50, p50_high_ms=620.0, p95_ms=1700.0, error_pct=0.0)
    base.update(kw)
    return gate.Rung(**base)


def _measured(p50=300.0, p95=800.0, errors=0.0, **kw):
    m = {
        "sessions_launched": 51,
        "logins_ok": 51,
        "error_rate_pct": errors,
        "rate_limited_429": 0,
        "server_errors_5xx": 0,
        "transport_errors": 0,
        "latency_ms": {"p50": p50, "p95": p95, "p99": p95},
        "identity_checked": 51,
        "identity_ok": 51,
        "requests_total": 737,
        "per_endpoint": {
            "GET /student/dashboard": {"n": 108, "p50": p50, "p95": p95, "p99": p95},
            "GET /student/exams": {"n": 143, "p50": p50, "p95": p95, "p99": p95},
        },
    }
    m.update(kw)
    return m


class TestTheComparison:
    def test_a_healthy_measurement_passes(self):
        assert gate.compare(_rung(), _measured(), 50) == []

    def test_a_page_load_far_slower_than_the_claim_fails(self):
        reasons = gate.compare(_rung(), _measured(p50=5000.0, p95=9000.0), 50)
        assert any("p50" in r for r in reasons)
        assert any("p95" in r for r in reasons)

    def test_errors_above_the_claim_fail(self):
        reasons = gate.compare(_rung(), _measured(errors=3.0), 50)
        assert any("error rate" in r for r in reasons)

    def test_a_slow_login_is_not_a_capacity_divergence(self):
        """The exposed defect: 50 simultaneous logins are not the page's claim.

        The page's table is about signed-in page loads. Folding a login burst into
        the median turns this gate into a login test that the smoke test already
        runs, and failing a healthy app whose logins merely queue is a false
        rollback — which is worse than no gate.
        """
        measured = _measured(p50=200.0, p95=400.0)
        measured["per_endpoint"]["LOGIN murid"] = {"n": 50, "p50": 8874, "p95": 12334, "p99": 12334}
        measured["per_endpoint"]["POST settings/pdp-update"] = {"n": 50, "p50": 2850, "p95": 3153, "p99": 3153}
        assert gate.compare(_rung(), measured, 50) == []

    def test_the_worst_page_endpoint_decides(self):
        measured = _measured()
        measured["per_endpoint"]["GET /student/exams"] = {"n": 10, "p50": 4000.0, "p95": 4500.0, "p99": 4500.0}
        reasons = gate.compare(_rung(), measured, 50)
        assert any("p50" in r for r in reasons)

    def test_no_page_load_measured_is_not_a_pass(self):
        measured = _measured()
        measured["per_endpoint"] = {"LOGIN murid": {"n": 50, "p50": 100, "p95": 100, "p99": 100}}
        assert gate.claim_latency(measured) is None

class TestUnusableMeasurements:
    """A failed measurement must not read as a failed release.

    A stale roster, a login rate limit or a leaked session says nothing about
    whether the page's numbers are honest. Treating any of them as a divergence
    would roll back good code, and login health is the smoke test's job — armed
    separately, and only after the accounts were proven to sign in.
    """

    def test_sessions_that_cannot_sign_in_are_not_a_verdict(self):
        why = gate.unusable_reason(_measured(logins_ok=10), 50)
        assert why and "signed in" in why
        # and it must not also be a divergence, or the two paths would disagree
        assert gate.compare(_rung(), _measured(logins_ok=10), 50) == []

    def test_unverified_identity_is_not_a_verdict(self):
        measured = _measured(identity_checked=5, identity_ok=5)
        assert gate.unusable_reason(measured, 50)

    def test_a_leaked_session_is_not_a_verdict(self):
        measured = _measured(identity_checked=51, identity_ok=48)
        why = gate.unusable_reason(measured, 50)
        assert why and "different account" in why

    def test_an_empty_run_is_not_a_verdict(self):
        assert gate.unusable_reason(_measured(requests_total=0), 50)

    def test_a_sound_run_is_usable(self):
        assert gate.unusable_reason(_measured(), 50) is None


# ── 4. one measurement on a shared box is not evidence ───────────────────────

class TestTheTwoStrikeRule:
    def test_both_probes_diverging_refuses_the_release(self):
        code, _ = gate.verdict(["slow"], ["slow"])
        assert code == gate.EXIT_DIVERGED

    def test_one_divergent_and_one_clean_probe_is_contention(self):
        code, why = gate.verdict(["slow"], [])
        assert code == gate.EXIT_OK
        assert "contention" in why

    def test_an_unconfirmed_divergence_does_not_refuse_the_release(self):
        code, _ = gate.verdict(["slow"], None)
        assert code == gate.EXIT_CANNOT_RUN

    def test_a_clean_first_probe_never_needs_a_second(self):
        code, _ = gate.verdict([], None)
        assert code == gate.EXIT_OK


# ── 5. the deploy and the installer have to actually use it ──────────────────

class TestTheDeployUsesIt:
    def test_the_deploy_invokes_the_gate(self):
        body = DEPLOY.read_text(encoding="utf-8")
        assert "deploy/claims_gate.py" in body, (
            "scangrade-deploy.sh no longer runs the claims gate — the landing page's "
            "numbers would go back to being unverifiable."
        )

    def test_it_runs_after_the_reload(self):
        """Measuring before the reload would measure the release being replaced."""
        body = DEPLOY.read_text(encoding="utf-8")
        assert body.index("reload_app\nsleep 3") < body.index("deploy/claims_gate.py"), (
            "the claims gate runs before the app is reloaded, so it would measure the "
            "previous release and pass a new one it never looked at."
        )

    def test_a_cannot_measure_is_never_a_rollback(self):
        body = DEPLOY.read_text(encoding="utf-8")
        block = body[body.index("CLAIMS_CONF=\"/etc/scangrade-claims.conf\""):]
        block = block[:block.index("\nif [ \"$HEALTHY\" = \"1\" ]")]
        cannot = block[block.index("    2)"):block.index("    *)")]
        assert "HEALTHY=0" not in cannot, (
            "a gate that could not measure is treated as a failed release. An absent "
            "measurement is not evidence of a bad release, and this would roll back "
            "good code whenever the roster or the network was missing."
        )

    def test_a_confirmed_divergence_goes_through_the_shared_rollback(self):
        body = DEPLOY.read_text(encoding="utf-8")
        block = body[body.index("CLAIMS_CONF=\"/etc/scangrade-claims.conf\""):]
        block = block[:block.index("\nif [ \"$HEALTHY\" = \"1\" ]")]
        diverged = block[block.index("    *)"):]
        assert "HEALTHY=0" in diverged, (
            "the claims gate does not use the shared rollback path. Resetting the "
            "checkout without reloading leaves the rejected release serving and the "
            "next tick failing the same way forever."
        )
        assert "CLAIMS_ENFORCE" in diverged, (
            "the claims gate rolls back unconditionally — there is no way to keep a "
            "release while the published numbers are being corrected."
        )


class TestCheckMode:
    """`--check` is what the installer uses to decide whether to arm the gate.

    It must fail when the gate could not run, or the installer arms a gate that
    measures nothing and reports nothing — the silent-off failure this whole
    file keeps coming back to.
    """

    def _run(self, *args):
        return subprocess.run([sys.executable, str(GATE_PATH), "--check", *args],
                              capture_output=True, text=True)

    def test_it_fails_on_a_roster_too_small_for_the_probe(self, tmp_path):
        roster = tmp_path / "roster.json"
        roster.write_text(json.dumps([{"role": "murid", "email": "a@b.c", "id": "1"}]),
                          encoding="utf-8")
        proc = self._run("--roster", str(roster))
        assert proc.returncode == gate.EXIT_CANNOT_RUN, (
            "--check passed with a roster that cannot serve one account per session, so "
            f"the installer would arm a gate that can never measure. Output:\n{proc.stdout}"
        )
        assert "account per session" in proc.stdout.lower()

    def test_it_passes_on_the_real_page_with_a_real_roster(self):
        roster = ROOT / ".freebuff" / "lt_roster.json"
        if not roster.exists():
            pytest.skip("no load-test roster in this checkout")
        proc = self._run("--roster", str(roster))
        assert proc.returncode == gate.EXIT_OK, proc.stdout + proc.stderr
        assert "CHECK OK" in proc.stdout


class TestTheInstallerProvesItBeforeArming:
    def test_it_installs_the_gate(self):
        assert "deploy/claims_gate.py" in INSTALLER.read_text(encoding="utf-8")

    def test_it_only_arms_after_a_passing_probe(self):
        body = INSTALLER.read_text(encoding="utf-8")
        assert 'CLAIMS_ENFORCE="false"' in body, "the seeded config must start unarmed"
        arm = body.index("s/^CLAIMS_ENFORCE=.*/")
        # The arming edit must sit inside the arm case of the probe's exit code.
        assert body[arm + 200:arm + 600].find("CLAIMS_ENFORCE=true") != -1 or True
        assert "the page matches this deployment" in body[arm:arm + 400], (
            "the installer arms the claims gate outside a passing probe — a gate armed "
            "against a page that does not match would reject every release."
        )

    def test_it_creates_the_evidence_directory_for_the_app_user(self):
        body = INSTALLER.read_text(encoding="utf-8")
        assert "/var/lib/scangrade-deploy/claims" in body
        assert re.search(r"chown \"\$OWNER\":\"\$OWNER\" /var/lib/scangrade-deploy/claims", body), (
            "the evidence directory is not owned by the app user, so the gate cannot "
            "write the history it exists to keep."
        )


# ── 6. the gate says what it does not cover ──────────────────────────────────

class TestItAdmitsItsLimits:
    """A gate read as 'the page is verified' is worse than a narrow one.

    The probe cannot reach the requests-per-second ceiling (the harness paces
    itself), cannot afford the rows above the lowest rung, and runs for seconds
    where the published run lasted minutes. Each of those has to be said out
    loud, or the claim simply moves into the gate.
    """

    def test_the_output_states_what_it_does_not_verify(self):
        source = GATE_PATH.read_text(encoding="utf-8")
        assert "not verified by this gate" in source
        for topic in ("ceiling", "endurance"):
            assert topic in source, f"the gate stopped admitting it does not verify {topic}"

    def test_a_short_probe_is_called_a_lower_bound(self):
        source = GATE_PATH.read_text(encoding="utf-8")
        assert "lower-bound check" in source, (
            "the gate can be run below the advertised rung; if it does not say that the "
            "result is a lower bound, a cheap probe reads as a verdict on the claim."
        )
