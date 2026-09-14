"""The load-test harness has to be able to log in, or its numbers mean nothing.

`locustfile.py` was credited with 46,698 requests and a 0% error rate while being
unable to authenticate at all: it POSTed to `/auth/login-user` without a CSRF
token (rejected 403), accepted both 200 and 302 as "logged in" even though a
*rejected* login also answers 200, and drew its users from
`siswa1..siswa1000_smp`, of which six exist. Every figure attributed to it was
produced by a pool of logged-out visitors following redirects.

The rules below are those three defects plus the ones found while fixing it.

Source checks cover the login, because that is the part that must not silently
revert. The functional probes — the account pool, the refusal, the report — run
in a **child process**: importing Locust calls `gevent.monkey.patch_all()`, which
would patch sockets and ssl for every other test in the same interpreter.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LOCUSTFILE = ROOT / "locustfile.py"
SOURCE = LOCUSTFILE.read_text(encoding="utf-8")


# ── the login has to be a real login ─────────────────────────────────────────

class TestTheLoginIsReal:
    def test_it_sends_a_csrf_token(self):
        assert "_csrf_token" in SOURCE, (
            "the login posts without a CSRF token, so every attempt is rejected 403 and no "
            "run can produce a number worth publishing."
        )

    def test_it_reads_the_token_off_the_page_that_carries_the_form(self):
        assert "CSRF_RE.search" in SOURCE, (
            "no CSRF token is read from the login page, so _csrf_token can only be empty"
        )

    def test_success_is_where_it_landed_not_the_status_code(self):
        """A rejected login answers 200 — the status code cannot decide this."""
        assert '"/dashboard" not in str(resp.url)' in SOURCE, (
            "the login no longer judges success by the URL it ended on. A wrong password "
            "re-renders the login page with HTTP 200, so accepting the status code counts "
            "every rejected login as a signed-in user."
        )
        assert "resp.status_code in (200, 302)" not in SOURCE, (
            "the old status-code test is back"
        )

    def test_a_failed_login_is_a_recorded_failure(self):
        assert "resp.failure(" in SOURCE, (
            "a login that never reached a dashboard must be recorded as a failure, not skipped"
        )
        assert 'S["logins_failed"] += 1' in SOURCE

    def test_a_server_error_on_the_landing_page_is_not_a_pass(self):
        assert "resp.status_code >= 400" in SOURCE and "authenticated, but" in SOURCE, (
            "the login calls success() even when the dashboard it landed on answered 5xx — a "
            "server error reading as a pass, which is the defect this harness exists to avoid."
        )

    def test_it_verifies_which_account_it_is(self):
        assert "/auth/me" in SOURCE and "identity_wrong" in SOURCE, (
            "the session does not confirm through /auth/me that it owns the account it signed "
            "in as"
        )

    def test_no_accounts_are_invented_in_the_source(self):
        assert "siswa{i}" not in SOURCE and "DEMO_EMAILS" not in SOURCE, (
            "the harness is inventing account addresses again instead of reading the roster "
            "that provision_loadtest.py writes"
        )
        assert "load_roster" in SOURCE, "the roster loader is no longer used"

    def test_status_codes_are_collected_for_every_request(self):
        """Counting them in the login flow alone described 8 of 64 requests."""
        assert "class _RecordingSession(HttpSession)" in SOURCE, (
            "status codes are no longer recorded per request, so status_breakdown can only "
            "describe the requests the login flow happens to make"
        )

    def test_the_json_artifact_is_written_before_the_summary_prints(self):
        write = SOURCE.index("json.dump(report, handle")
        first_print = SOURCE.index('print("\\n" + "=" * 82)')
        assert write < first_print, (
            "the summary prints before the artifact is written, so a bug in the printing block "
            "costs the artifact — which is how a completed run lost its JSON"
        )


# ── everything that needs the module, in a child interpreter ─────────────────

PROBE = r'''
import importlib.util, json, sys, tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location("lf", sys.argv[1])
lf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lf)


class Opts:
    def __init__(self, roster, num_users, teachers=0):
        self.roster, self.num_users, self.teachers = roster, num_users, teachers
        self.host = "http://example.test"


class Runner:
    def __init__(self):
        self.quit_called = False

    def quit(self):
        self.quit_called = True


class Env:
    def __init__(self, opts):
        self.parsed_options = opts
        self.process_exit_code = None
        self.runner = Runner()


class Entry:
    def __init__(self, name, method, requests, failures):
        self.name, self.method = name, method
        self.num_requests, self.num_failures = requests, failures
        self.median_response_time = 12

    def get_response_time_percentile(self, percentile):
        return int(percentile * 1000)


class Stats:
    def __init__(self):
        self.entries = {
            ("GET /student/dashboard", "GET"): Entry("GET /student/dashboard", "GET", 10, 0),
            ("LOGIN", "POST"): Entry("LOGIN", "POST", 2, 0),
        }
        self.total = Entry("Aggregated", "", 12, 0)


td = Path(tempfile.mkdtemp())
roster = td / "roster.json"
roster.write_text(json.dumps(
    [{"email": f"s{i}@load.test", "name": f"M{i}", "id": f"id-s{i}", "role": "murid"}
     for i in range(3)] +
    [{"email": f"g{i}@load.test", "name": f"G{i}", "id": f"id-g{i}", "role": "guru"}
     for i in range(2)]), encoding="utf-8")

out = {}

# one account per user, distinct
env = Env(Opts(str(roster), num_users=4, teachers=1))
lf._fill_the_pool(env)
claimed = []
while not lf.ACCOUNT_POOL.empty():
    account, password = lf.ACCOUNT_POOL.get_nowait()
    claimed.append({"id": account["id"], "role": account["role"], "password": password})
out["pool"] = {"exit_code": env.process_exit_code, "claimed": claimed}

# teachers come from the teacher pool
env = Env(Opts(str(roster), num_users=3, teachers=2))
lf._fill_the_pool(env)
roles = []
while not lf.ACCOUNT_POOL.empty():
    account, _ = lf.ACCOUNT_POOL.get_nowait()
    roles.append(account["role"])
out["roles"] = roles

# refuse to reuse logins
env = Env(Opts(str(roster), num_users=50))
lf._fill_the_pool(env)
out["refusal"] = {"exit_code": env.process_exit_code,
                  "quit_called": env.runner.quit_called,
                  "pool_empty": lf.ACCOUNT_POOL.empty()}

# the report is complete, and keyed by endpoint name
snapshot = {k: v for k, v in lf.S.items()}
report = lf.build_report(Stats(), snapshot, "http://example.test", 100.0, 160.0)
out["report"] = {
    "missing": [k for k in lf.REPORT_KEYS if k not in report],
    "extra": sorted(set(report) - set(lf.REPORT_KEYS)),
    "endpoints": sorted(report["per_endpoint"]),
    "dashboard_n": report["per_endpoint"].get("GET /student/dashboard", {}).get("n"),
    "wall_s": report["wall_s"],
    "throughput_rps": report["throughput_rps"],
    "error_rate_pct": report["error_rate_pct"],
}

# pacing
pacing = {}
for cls in (lf.ScanGradeStudent, lf.ScanGradeTeacher):
    wait = getattr(cls, "wait_time", None)
    pacing[cls.__name__] = None if wait is None else min(wait(None) for _ in range(30))
out["pacing"] = pacing

print(json.dumps(out))
'''


@pytest.fixture(scope="module")
def probe():
    """Run the probes once, in a child interpreter, and hand back its findings."""
    result = subprocess.run(
        [sys.executable, "-c", PROBE, str(LOCUSTFILE)],
        capture_output=True, text=True, cwd=str(ROOT), timeout=180,
    )
    assert result.returncode == 0, (
        "the harness could not be imported or driven at all:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


class TestOneAccountPerUser:
    def test_the_pool_holds_distinct_accounts(self, probe):
        pool = probe["pool"]
        assert pool["exit_code"] is None, "a run the roster can serve was refused"
        claimed = pool["claimed"]
        assert len(claimed) == 4, f"expected one account per user, got {len(claimed)}"
        assert len({c["id"] for c in claimed}) == 4, (
            "two virtual users were handed the same account; the app rate-limits per identity, "
            "so the second one's requests turn into 429s that read as a server failure."
        )
        assert all(c["password"] for c in claimed), (
            "an account was queued without a password, so its login can only fail"
        )

    def test_teachers_are_taken_from_the_teacher_pool(self, probe):
        roles = probe["roles"]
        assert roles.count("guru") == 2 and roles.count("murid") == 1, (
            f"--teachers 2 should spend two of three users on teachers, got {roles}"
        )

    def test_it_refuses_to_reuse_accounts(self, probe):
        refusal = probe["refusal"]
        assert refusal["exit_code"] == 2, (
            "asking for 50 users with a 5-account roster must be refused, not served by reusing "
            "logins"
        )
        assert refusal["quit_called"], "the refused run was not stopped"
        assert refusal["pool_empty"], "accounts were queued for a run that was refused"


class TestTheReportAndThePacing:
    def test_the_report_carries_every_key_the_summary_prints(self, probe):
        """The real defect: the artifact named a key it never created.

        The quitting handler raised a KeyError after the run had finished
        measuring, and the whole artifact was lost.
        """
        report = probe["report"]
        assert not report["missing"], f"the report is missing {report['missing']}"
        assert not report["extra"], (
            f"the report has keys REPORT_KEYS does not list: {report['extra']}"
        )

    def test_it_does_not_count_a_finished_run_as_taking_no_time(self, probe):
        report = probe["report"]
        assert report["wall_s"] == 60.0, "the wall clock is not the run's elapsed time"
        assert report["throughput_rps"] > 0, "throughput collapsed to zero"

    def test_the_report_keys_endpoints_by_name_not_by_http_method(self, probe):
        """`stats.entries` is keyed `(name, method)`; unpacked backwards it buckets by verb."""
        report = probe["report"]
        assert "GET /student/dashboard" in report["endpoints"], (
            f"endpoints are keyed as {report['endpoints']}"
        )
        assert "GET" not in report["endpoints"], (
            "endpoints are keyed by HTTP method, so every un-named GET collapsed into one bucket "
            "and the per-endpoint table describes a fraction of the run"
        )
        assert report["dashboard_n"] == 10

    def test_every_user_class_waits_between_requests(self, probe):
        for name, minimum in probe["pacing"].items():
            assert minimum is not None, (
                f"{name} has no wait_time, so it inherits Locust's `constant(0)` and loops as "
                "fast as the server answers"
            )
            assert minimum >= 1.0, (
                f"{name} waits as little as {minimum}s — a 2-user run then fires ~180 req/s at "
                "one account and the rate limiter's 429s look like a server failure."
            )
