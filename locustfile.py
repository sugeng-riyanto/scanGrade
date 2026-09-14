"""Locust load test — ScanGrade exam sessions, one real account per virtual user.

This harness was not merely unpolished; it could not measure anything, and the
figures once attributed to it (46,698 and 74,923 requests at a 0% error rate)
were withdrawn from the landing page for that reason. Three defects, each now a
rule enforced below:

1. **The login posted no CSRF token.** `POST /auth/login-user` without
   `_csrf_token` is rejected `403`, so every session was a logged-out session and
   every page it loaded was a redirect to `/auth/login`.
2. **A failed login counted as success.** The app re-renders the login page with
   HTTP **200** when the credentials are wrong, so `status_code in (200, 302)`
   counted each rejection as a signed-in user. Success is now decided by *where
   the response landed* (`/dashboard` in the final URL), never by the status code
   — and a login that does not land there is recorded as a Locust **failure**.
3. **The accounts did not exist.** It drew from `siswa1..siswa1000_smp`;
   production has six of those. Accounts now come from the provisioning roster
   (`.freebuff/lt_roster.json`, written by `provision_loadtest.py`), and each
   virtual user claims a *distinct* one. The run is refused, before any traffic,
   when `--users` exceeds the roster: reusing logins makes the per-identity rate
   limiter (120 req/min) look like a server failure, which is how an earlier run
   reported a fake 49% error rate.

Each session also confirms through `/auth/me` that it is not wearing someone
else's identity — a shared Supabase client could otherwise hand two sessions the
same user without any visible error.

If a login fails, that user stays logged out and does nothing: its only trace is
the failure record. That is deliberate — `--report` states how many sessions were
actually authenticated, because a partly-logged-out run flatters every latency
figure in it.

Usage (one account per user is mandatory, so `--users` must not exceed the roster):

    locust -f locustfile.py --host=https://scangrade.web.id \\
           --users=50 --spawn-rate=5 --run-time=90s --headless \\
           --csv=.freebuff/locust-050 \\
           --html=docs/measurements/locust-050.html \\
           --report=docs/measurements/locust-050.json

`--teachers K` takes K of the `--users` from the teacher pool instead of the
student pool; teachers load the teacher pages.
"""
import json
import os
import queue
import random
import re
import sys
import threading
import time
from collections import Counter, defaultdict

from locust import HttpUser, task, between, events
from locust.clients import HttpSession

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# The roster loader is shared with loadtest_concurrent.py on purpose: two
# harnesses that disagree about who the accounts are is how this file ended up
# inventing a thousand of them.
from loadtest_concurrent import DEFAULT_ROSTER, load_roster  # noqa: E402

CSRF_RE = re.compile(r'name="csrf-token"\s+content="([^"]+)"')
EXAM_RE = re.compile(r"/student/exams/([a-f0-9\-]{36})")

# Same task mix and wait time as loadtest_concurrent.py's endurance mode, so a
# Locust run and a harness run of the same rung describe the same workload.
ACCOUNT_POOL = queue.Queue()

LOCK = threading.Lock()
S = {
    "logins_ok": 0,
    "logins_failed": 0,
    "login_missed": Counter(),
    "identity_ok": 0,
    "identity_checked": 0,
    "identity_wrong": Counter(),
    "codes": defaultdict(Counter),
    "app_errors": Counter(),
    "spawned": 0,
    "started": None,
    "finished": None,
    "roster_source": "",
    "accounts_in_roster": 0,
    "users_requested": 0,
    "teachers_requested": 0,
}


@events.init_command_line_parser.add_listener
def _(parser):
    parser.add_argument(
        "--roster", default=str(DEFAULT_ROSTER),
        help="JSON roster from provision_loadtest.py (one account per session)")
    parser.add_argument(
        "--teachers", type=int, default=0,
        help="how many of --users should load teacher pages instead of student pages")
    parser.add_argument(
        "--report", default="",
        help="write the run summary to this path as JSON (the artifact)")


@events.test_start.add_listener
def _fill_the_pool(environment, **_kwargs):
    opts = environment.parsed_options
    accounts, password, source = load_roster(opts.roster)
    students = [a for a in accounts if a.get("role") == "murid"]
    teachers = [a for a in accounts if a.get("role") == "guru"]
    users, want_teachers = opts.num_users or 0, opts.teachers or 0

    with LOCK:
        S["started"] = time.time()
        S["roster_source"] = source
        S["accounts_in_roster"] = len(accounts)
        S["users_requested"] = users
        S["teachers_requested"] = want_teachers

    n_teachers = min(want_teachers, len(teachers))
    n_students = users - n_teachers
    pool = students[:n_students] + teachers[:n_teachers]

    print(f"\nlocust: roster {len(students)} murid / {len(teachers)} guru  [{source}]")
    if len(pool) < users:
        # Refuse before a single request is sent. This is the check that makes
        # "one account per user" a property of the harness rather than a hope.
        print(f"locust: REFUSING — --users={users} but the roster supplies only "
              f"{len(pool)} accounts ({len(students)} murid + {len(teachers)} guru).")
        print("        Reusing a login makes the per-identity rate limit look like a")
        print("        server error. Provision more accounts "
              "(python provision_loadtest.py <murid> <guru>) or lower --users.")
        environment.process_exit_code = 2
        environment.runner.quit()
        return

    while not ACCOUNT_POOL.empty():
        ACCOUNT_POOL.get_nowait()
    for acct in pool:
        ACCOUNT_POOL.put((acct, password))
    print(f"locust: {len(pool)} distinct accounts queued "
          f"({n_students} murid + {n_teachers} guru) -> {opts.host}")


def build_report(stats, snapshot, host, started, finished):
    """The artifact: Locust's statistics plus this file's own counters.

    Split out of the quitting handler so it can be exercised without a server.
    The first version of this report named a key it never created, and a `name`
    that no longer existed, and lost a whole finished run's artifact to the
    traceback. A function is testable; an event handler is not.
    """
    total = stats.total
    entries = {}
    # `stats.entries` is keyed by `(name, method)`. Read the names off the entries
    # themselves: unpacking that tuple the other way round put every request into
    # buckets named "GET" and "POST".
    for entry in stats.entries.values():
        if not entry.num_requests and not entry.num_failures:
            continue
        entries[entry.name] = {
            "method": entry.method,
            "n": entry.num_requests,
            "failures": entry.num_failures,
            "p50": entry.median_response_time,
            "p95": entry.get_response_time_percentile(0.95),
            "p99": entry.get_response_time_percentile(0.99),
            "codes": dict(snapshot["codes"].get(entry.name, {})),
        }

    codes = Counter()
    for per_name in snapshot["codes"].values():
        codes.update(per_name)
    wall = (finished - started) if (started and finished) else 0.0
    failures = total.num_failures

    return {
        "tool": "locust",
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S",
                                     time.localtime(finished or time.time())),
        "base": host,
        "roster_source": snapshot["roster_source"],
        "accounts_in_roster": snapshot["accounts_in_roster"],
        "users_requested": snapshot["users_requested"],
        "teachers_requested": snapshot["teachers_requested"],
        "sessions_spawned": snapshot["spawned"],
        "logins_ok": snapshot["logins_ok"],
        "logins_failed": snapshot["logins_failed"],
        "login_missed": dict(snapshot["login_missed"]),
        "identity_ok": snapshot["identity_ok"],
        "identity_checked": snapshot["identity_checked"],
        "identity_wrong": dict(snapshot["identity_wrong"]),
        "requests_total": total.num_requests,
        "failures_total": failures,
        "status_breakdown": dict(codes),
        # The three below are *subsets* of failures_total, reported separately
        # for diagnosis; they are not added to it.
        "rate_limited_429": codes.get("429", 0),
        "server_errors_5xx": sum(v for k, v in codes.items()
                                 if k.isdigit() and int(k) >= 500),
        "transport_errors": sum(snapshot["app_errors"].values()),
        "error_rate_pct": round(100.0 * failures / max(total.num_requests, 1), 3),
        "latency_ms": {
            "p50": total.median_response_time,
            "p95": total.get_response_time_percentile(0.95),
            "p99": total.get_response_time_percentile(0.99),
        },
        "per_endpoint": entries,
        "wall_s": round(wall, 2),
        "throughput_rps": round(total.num_requests / wall, 2) if wall > 0 else 0.0,
    }


# Every key the summary below prints. The report is checked against this list
# before it is written, so a key that was renamed cannot reach a finished run.
REPORT_KEYS = (
    "tool", "measured_at", "base", "roster_source", "accounts_in_roster",
    "users_requested", "teachers_requested", "sessions_spawned", "logins_ok",
    "logins_failed", "login_missed", "identity_ok", "identity_checked",
    "identity_wrong", "requests_total", "failures_total", "status_breakdown",
    "rate_limited_429", "server_errors_5xx", "transport_errors",
    "error_rate_pct", "latency_ms", "per_endpoint", "wall_s", "throughput_rps",
)


@events.quitting.add_listener
def _write_the_report(environment, **_kwargs):
    opts = environment.parsed_options
    with LOCK:
        S["finished"] = time.time()
        snapshot = json.loads(json.dumps(
            {k: v for k, v in S.items() if k not in ("started", "finished")},
            default=str))
        started, finished = S["started"], S["finished"]

    report = build_report(environment.stats, snapshot, opts.host, started, finished)
    missing = [k for k in REPORT_KEYS if k not in report]
    if missing:
        print(f"!! report is missing {missing} — not written, and that is a bug in "
              "locustfile.py, not in the run", file=sys.stderr)
        return
    failures = report["failures_total"]

    # The artifact is written *before* anything is printed: a bug in the summary
    # block once raised out of this handler, which lost the JSON and exited 1 —
    # the run had measured everything and thrown it away.
    if opts.report:
        with open(opts.report, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, sort_keys=True)

    # Printing is best-effort; measuring is not. The artifact above is already on
    # disk, so a mistake in this block costs a summary, never a run.
    try:
        print("\n" + "=" * 82)
        print(f"sessions spawned    : {report['sessions_spawned']}")
        print(f"logins succeeded    : {report['logins_ok']}   failed: {report['logins_failed']}")
        print(f"identity verified   : {report['identity_ok']}/"
              f"{report['identity_checked']} via /auth/me")
        if report["identity_wrong"]:
            print(f"  !! WRONG IDENTITY : {report['identity_wrong']}")
        if report["login_missed"]:
            print("logins that never reached a dashboard:")
            for where, n in sorted(report["login_missed"].items(), key=lambda kv: -kv[1])[:5]:
                print(f"    {n:>4} x {where}")
        print(f"requests            : {report['requests_total']}  failures: {failures}")
        print(f"status breakdown    : {report['status_breakdown']}")
        print(f"  429 / 5xx / transport (subsets): {report['rate_limited_429']} / "
              f"{report['server_errors_5xx']} / {report['transport_errors']}")
        print(f"error rate          : {report['error_rate_pct']:.2f}%")
        print(f"latency             : p50={report['latency_ms']['p50']}ms "
              f"p95={report['latency_ms']['p95']}ms p99={report['latency_ms']['p99']}ms")
        print(f"throughput          : {report['throughput_rps']} requests/s "
              f"over {report['wall_s']}s")
        print("=" * 82)
        if opts.report:
            print(f"summary written     : {opts.report}")
        # A run whose logins failed is not a slow server, and must not be read as one.
        if report["logins_failed"]:
            print(f"!! {report['logins_failed']} session(s) never authenticated — latency "
                  "above covers authenticated sessions only.")
    except Exception:  # noqa: BLE001 — never let the summary break the artifact
        import traceback
        print("!! the summary below could not be printed; the JSON artifact is intact",
              file=sys.stderr)
        traceback.print_exc()


def _page_message(html):
    """The visible error text on a re-rendered login page, if any."""
    for m in re.finditer(r">([^<>]{10,180})<", html or ""):
        text = m.group(1).strip()
        if any(k in text.lower() for k in
               ("salah", "gagal", "error", "tidak", "wajib", "coba", "sibuk", "batas")):
            return text[:120]
    return "(no message)"


class _RecordingSession(HttpSession):
    """An HttpSession that also counts the status code of every request.

    Locust's statistics record *that* a request failed, not with which code, so a
    report claiming "3 x 429, 1 x 500" has to collect the codes itself. Counting
    them here covers everything the session does; the first version recorded only
    the two requests the login flow touches, so its `status_breakdown` described
    8 of 64 requests and looked like a healthy server.
    """

    def request(self, method, url, name=None, catch_response=False, **kwargs):
        response = super().request(method, url, name=name,
                                   catch_response=catch_response, **kwargs)
        with LOCK:
            S["codes"][name or url][str(response.status_code)] += 1
        return response


class _ScanGradeSession(HttpUser):
    """The login both roles share: CSRF, a distinct account, verified identity."""

    abstract = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Same construction as HttpUser.__init__, with the recording client.
        self.client = _RecordingSession(
            base_url=self.host,
            request_event=self.environment.events.request,
            user=self,
            pool_manager=self.pool_manager,
        )
        self.client.trust_env = False

    # Declared here, not on the concrete classes, because a User with no wait_time
    # inherits `constant(0)` from Locust's base class — it then loops as fast as the
    # server answers. That is what turned a 2-user smoke test into ~180 req/s on one
    # account, which the app's per-identity limit (120/min) answered with 429s that
    # looked like a server failure. 1-3s also matches loadtest_concurrent.py.
    wait_time = between(1, 3)

    def on_start(self):
        self.logged_in = False
        self.exam_ids = []

        try:
            account, password = ACCOUNT_POOL.get_nowait()
        except queue.Empty:
            # More virtual users than accounts. Recorded as a failure with a name
            # of its own rather than silently sharing someone else's login.
            self._fire("LOGIN", "no distinct account left in the roster")
            return

        with LOCK:
            S["spawned"] += 1
        self.account = account
        self.role = account.get("role", "murid")

        # ── CSRF: the token is read off the page that carries the form ──
        token = ""
        try:
            page = self.client.get("/auth/login-user", name="GET login page")
            match = CSRF_RE.search(page.text)
            token = match.group(1) if match else ""
        except Exception as exc:  # noqa: BLE001 — recorded, not swallowed
            with LOCK:
                S["app_errors"][f"GET login page: {type(exc).__name__}"] += 1

        with self.client.post(
            "/auth/login-user",
            data={"email": account["email"], "password": password, "_csrf_token": token},
            headers={"X-CSRF-Token": token},
            name="LOGIN",
            catch_response=True,
        ) as resp:
            # Success is where it landed, not what status it returned: a wrong
            # password re-renders the login page with 200.
            if "/dashboard" not in str(resp.url):
                with LOCK:
                    S["logins_failed"] += 1
                    S["login_missed"][f"{resp.status_code} | {_page_message(resp.text)}"] += 1
                resp.failure(
                    f"login did not reach a dashboard: HTTP {resp.status_code} -> {resp.url}")
                return
            with LOCK:
                S["logins_ok"] += 1
            # Landing on a dashboard is what proves the credentials worked, and it
            # does so even when the dashboard itself then errored — `requests`
            # follows the POST's redirect, so a 500 on the page belongs to this
            # request. It is still a server error, and calling success() here would
            # be the original defect in a new place: a failure reading as a pass.
            if resp.status_code >= 400:
                resp.failure(f"authenticated, but {resp.url} answered HTTP "
                             f"{resp.status_code}")
            else:
                resp.success()
        self.logged_in = True

        # ── IDENTITY: this session must own the account it signed in as ──
        with self.client.get("/auth/me", name="GET /auth/me", catch_response=True) as me:
            if me.status_code >= 400:
                me.failure(f"/auth/me answered HTTP {me.status_code}; identity unverified")
            if me.status_code == 200:
                got = (me.json() or {}).get("user_id")
                with LOCK:
                    S["identity_checked"] += 1
                    if got == account["id"]:
                        S["identity_ok"] += 1
                    else:
                        S["identity_wrong"][f"{account['email']} -> {got}"] += 1
                if got == account["id"]:
                    me.success()
                else:
                    me.failure(f"session identity is {got}, expected {account['id']}")

        self.after_login()

    def after_login(self):
        """Role-specific warm-up; the base class has nothing to do."""

    def _fire(self, name, message):
        """Record a failure that is not the result of an HTTP request."""
        self.environment.events.request.fire(
            request_type="LOCUST", name=name, response_time=0.0,
            response_length=0, exception=RuntimeError(message), context={},
        )


class ScanGradeStudent(_ScanGradeSession):
    """A student working through their exams."""

    def after_login(self):
        self.refresh_exams()

    def refresh_exams(self):
        resp = self.client.get("/student/exams", name="GET /student/exams")
        if resp.status_code == 200:
            self.exam_ids = list(set(EXAM_RE.findall(resp.text)))[:3]

    @task(5)
    def view_dashboard(self):
        if self.logged_in:
            self.client.get("/student/dashboard", name="GET /student/dashboard")

    @task(8)
    def view_exams(self):
        if not self.logged_in:
            return
        resp = self.client.get("/student/exams", name="GET /student/exams")
        if resp.status_code == 200:
            ids = list(set(EXAM_RE.findall(resp.text)))
            if ids:
                self.exam_ids = ids[:3]

    @task(4)
    def open_exam(self):
        if not self.logged_in:
            return
        if self.exam_ids:
            self.client.get(f"/student/exams/{random.choice(self.exam_ids)}",
                            name="GET /student/exams/<id>")
        else:
            self.client.get("/student/exams", name="GET /student/exams")

    @task(1)
    def view_results(self):
        if self.logged_in:
            self.client.get("/student/results", name="GET /student/results")

    @task(1)
    def view_settings(self):
        # Loadtest_concurrent.py reads this page too, and it is the slowest of the
        # five student pages in its measurements. Leaving it out made this harness
        # comparable with a *lighter* workload, which is not comparable at all.
        if self.logged_in:
            self.client.get("/student/settings", name="GET /student/settings")

    @task(1)
    def view_result_detail(self):
        if not self.logged_in:
            return
        resp = self.client.get("/student/results", name="GET /student/results")
        if resp.status_code == 200:
            ids = re.findall(r"/student/results/([a-f0-9\-]{36})", resp.text)
            if ids:
                self.client.get(f"/student/results/{ids[0]}",
                                name="GET /student/results/<id>")

    @task(3)
    def health_check(self):
        self.client.get("/health", name="GET /health")


class ScanGradeTeacher(_ScanGradeSession):
    """A teacher: the pages a guru actually opens while an exam is running."""

    @task(5)
    def dashboard(self):
        if self.logged_in:
            self.client.get("/teacher/dashboard", name="GET /teacher/dashboard")

    @task(8)
    def exams(self):
        if self.logged_in:
            self.client.get("/teacher/exams", name="GET /teacher/exams")

    @task(4)
    def results(self):
        if self.logged_in:
            self.client.get("/teacher/results", name="GET /teacher/results")

    @task(1)
    def templates(self):
        if self.logged_in:
            self.client.get("/teacher/templates", name="GET /teacher/templates")

    @task(3)
    def health_check(self):
        self.client.get("/health", name="GET /health")
