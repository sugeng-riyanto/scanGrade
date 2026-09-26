"""Concurrent load test: N murid + M guru sessions, one account each.

Each virtual user logs in for real, then performs RBAC-protected reads plus one
idempotent RBAC-guarded write (students only). Deliberately NO record creation
or deletion, so real exam/submission data is untouched.

Two things this harness is careful about, because an earlier run got them wrong:

1. **One account per session.** Simulated users must not share logins. The app
   rate-limits per identity (120 req/min), so 300 sessions spread over 16
   accounts produced ~49% 429s that looked like a server failure but were purely
   an artifact of the test. Provide a roster with >= as many accounts as
   sessions; the run is refused otherwise.
2. **Identity is verified by user id, not by name.** After login each session
   calls ``/auth/me`` and its ``user_id`` must equal the account it signed in
   with. That detects session leakage through the shared Supabase auth client,
   which is otherwise invisible.

Usage:
    python loadtest_concurrent.py                       # 300 murid + 30 guru
    python loadtest_concurrent.py 5 2                   # smoke test
    python loadtest_concurrent.py 300 30 --roster .freebuff/lt_roster.json
    python loadtest_concurrent.py 300 30 --base http://127.0.0.1:5000
The roster file is written by ``provision_loadtest.py``.

``--json PATH`` writes the same run as one machine-readable object, which is
what ``deploy/claims_gate.py`` compares the landing page against. It is emitted
from the same computation that prints the report below, so a reader and a gate
can never disagree about what the run measured.

Besides latency, each page records what it **cost**: the bytes it sent and the
Supabase round-trips the render spent, read from the app's own
``X-Supabase-Roundtrips`` header. Response time alone cannot see a release that
adds three queries to the student dashboard while still answering inside the
latency slack; these two can, and ``deploy/perf_gate.py`` compares both against
the last release that passed.
"""
import argparse
import asyncio
import json
import random
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import httpx

DEFAULT_ROSTER = Path(__file__).resolve().parent / ".freebuff" / "lt_roster.json"
DEMO_PASSWORD = "demo123"
LT_PASSWORD = "LoadTest123!"

STUDENT_READS = ["/student/dashboard", "/student/exams", "/student/results", "/student/settings"]
TEACHER_READS = ["/teacher/dashboard", "/teacher/exams", "/teacher/results", "/teacher/templates"]

CSRF_RE = re.compile(r'name="csrf-token"\s+content="([^"]+)"')
EXAM_RE = re.compile(r'/student/exams/([a-f0-9\-]{36})')

#: The header the app puts on every response: how many Supabase round-trips the
#: render spent. Declared here as the harness's own copy so this file can run
#: against any base URL without importing the app (which would need its .env).
#: `test_the_harness_reads_the_header_the_app_writes` asserts the two agree, so
#: the copy cannot drift from `app/utils/query_meter.py`'s.
#: The app reports three numbers per render, and they are three because one cannot
#: be attributed on its own: round-trips are *attempts* (the box's retries move
#: them), queries are what the render *issued* (only a release moves them), and rows
#: are how much data it read (only the school's size moves them). All three are
#: declared here as the harness's own copies so this file can run against any base
#: URL without importing the app (which would need its .env).
#: `test_the_harness_reads_the_header_the_app_writes` asserts the copies agree, so
#: none of them can drift from `app/utils/query_meter.py`'s.
ROUNDTRIP_HEADER = "X-Supabase-Roundtrips"
QUERY_HEADER = "X-Supabase-Queries"
ROW_HEADER = "X-Supabase-Rows"

#: The pages the published capacity claim is about, and therefore the ones whose
#: payload the gate watches. Same shape as claims_gate.CLAIM_ENDPOINT.
PAGE_ENDPOINT = re.compile(r"^GET /(student|teacher)/")

# The mix locustfile.py uses, so a sustained run here can be compared with a
# Locust run of the same shape: (weight, label, path). /student/exams/<id> needs
# an id read off the exam list, so it is handled separately in the loop.
STUDENT_MIX = [
    (5, "GET /student/dashboard", "/student/dashboard"),
    (8, "GET /student/exams", "/student/exams"),
    (1, "GET /student/results", "/student/results"),
    (3, "GET /health", "/health"),
]
STUDENT_MIX_TOTAL = sum(w for w, _, _ in STUDENT_MIX) + 4  # +4 = exam detail


def page_message(html):
    """The visible error text on a re-rendered login page, if any."""
    for m in re.finditer(r">([^<>]{10,180})<", html):
        t = m.group(1).strip()
        if any(k in t.lower() for k in
               ("salah", "gagal", "error", "tidak", "wajib", "coba", "sibuk", "batas")):
            return t[:120]
    return "(no message)"


def load_roster(path):
    """Return (accounts, password, source_label)."""
    p = Path(path)
    if p.exists():
        accounts = json.loads(p.read_text(encoding="utf-8"))
        if accounts:
            return accounts, LT_PASSWORD, str(p)
        print(f"!! roster {p} is empty")

    # Fallback: whatever real accounts the live database happens to have. This
    # is only for ad-hoc runs; it cannot give one account per session.
    from dotenv import load_dotenv
    load_dotenv(".env")
    from app.config import get_config
    from supabase import create_client

    cfg = get_config()
    svc = create_client(cfg.SUPABASE_URL, cfg.SUPABASE_SERVICE_KEY)
    profiles = {r["id"]: r for r in svc.table("profiles").select("id, role, full_name").execute().data}
    accounts = []
    for u in svc.auth.admin.list_users():
        email = getattr(u, "email", None) or ""
        pr = profiles.get(u.id)
        if pr and pr.get("role") in ("murid", "guru"):
            accounts.append({"email": email, "name": pr.get("full_name", ""),
                             "id": u.id, "role": pr["role"]})
    print(f"!! no roster file; fell back to live DB pool ({len(accounts)} accounts)")
    return accounts, DEMO_PASSWORD, "live DB (shared accounts)"


class Results:
    def __init__(self):
        self.lat = defaultdict(list)
        self.status = defaultdict(Counter)
        self.errors = Counter()
        # Full time series, so the report can say whether the server degraded
        # over the run or held steady: (seconds since start, key, ms, status).
        self.tl = []
        self.t0 = time.perf_counter()
        self.identity_checked = 0
        self.identity_ok = 0
        self.identity_wrong = Counter()
        self.login_ok = 0
        self.login_failed = 0
        # Where a login ended up when it did not reach a dashboard. Kept
        # separate from transport errors so the two are never conflated.
        self.login_missed = Counter()
        # What a page *costs*, not just how long it took. Bytes are the payload the
        # student downloads; round-trips are the Supabase queries the render spent,
        # read off the app's own header. Response time is the symptom of both.
        self.nbytes = defaultdict(list)
        self.trips = defaultdict(list)
        self.asked = defaultdict(list)
        self.rowsread = defaultdict(list)

    @staticmethod
    def _count(resp, header):
        """A header's integer value, or None when the app did not send one.

        `None` is "not measured" and is never read as zero: a page that reported
        nothing must not look like a free one, and an app too old to send the
        header is exactly that case.
        """
        value = resp.headers.get(header)
        if value is None or not value.strip().isdigit():
            return None
        return int(value.strip())

    def rec(self, key, t0, resp):
        ms = (time.perf_counter() - t0) * 1000
        self.lat[key].append(ms)
        self.status[key][resp.status_code] += 1
        self.tl.append((time.perf_counter() - self.t0, key, ms, resp.status_code))
        # Only a page that *answered*. A 302 or a 500 has a body of nothing, and
        # averaging those in would let a page that broke look cheap — the exact
        # shape of measurement that hides the defect it was taken to find.
        if resp.status_code == 200:
            self.nbytes[key].append(len(resp.content or b""))
            for header, bucket in ((ROUNDTRIP_HEADER, self.trips),
                                   (QUERY_HEADER, self.asked),
                                   (ROW_HEADER, self.rowsread)):
                count = self._count(resp, header)
                if count is not None:
                    bucket[key].append(count)

    def err(self, key, exc):
        self.lat[key].append(float("nan"))
        self.errors[f"{key}: {type(exc).__name__}"] += 1
        self.status[key]["EXC"] += 1
        self.tl.append((time.perf_counter() - self.t0, key, float("nan"), "EXC"))


def pct(values, p):
    vals = sorted(v for v in values if v == v)  # drop NaN
    if not vals:
        return 0.0
    idx = min(len(vals) - 1, max(0, int(round((p / 100.0) * len(vals) + 0.5)) - 1))
    return vals[idx]


async def session(r, acct, password, base, sem, duration=0.0):
    role = acct["role"]
    reads = STUDENT_READS if role == "murid" else TEACHER_READS
    exam_ids = []
    async with sem:
        async with httpx.AsyncClient(
            timeout=120.0, follow_redirects=True, verify=False,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=4),
        ) as client:
            # ── LOGIN ──
            t0 = time.perf_counter()
            try:
                page = await client.get(f"{base}/auth/login-user")
                csrf = (CSRF_RE.search(page.text) or [None, None])[1]
                resp = await client.post(
                    f"{base}/auth/login-user",
                    data={"email": acct["email"], "password": password, "_csrf_token": csrf or ""},
                    headers={"X-CSRF-Token": csrf or ""},
                )
                r.rec(f"LOGIN {role}", t0, resp)
                if "/dashboard" not in str(resp.url):
                    r.login_failed += 1
                    r.login_missed[f"{resp.status_code} | {page_message(resp.text)}"] += 1
                    return
                r.login_ok += 1
            except Exception as e:
                r.err(f"LOGIN {role}", e)
                r.login_failed += 1
                return

            # ── IDENTITY: the session must belong to this exact account ──
            t0 = time.perf_counter()
            try:
                me = await client.get(f"{base}/auth/me")
                r.rec("GET /auth/me", t0, me)
                if me.status_code == 200:
                    r.identity_checked += 1
                    got = (me.json() or {}).get("user_id")
                    if got == acct["id"]:
                        r.identity_ok += 1
                    else:
                        r.identity_wrong[f"{acct['email']} -> {got}"] += 1
            except Exception as e:
                r.err("GET /auth/me", e)

            # ── RBAC READS ──
            for path in reads:
                t0 = time.perf_counter()
                try:
                    resp = await client.get(base + path)
                    r.rec(f"GET {path}", t0, resp)
                    if path.endswith(("/student/exams", "/teacher/exams")) and resp.status_code == 200:
                        exam_ids = list(set(EXAM_RE.findall(resp.text)))[:3]
                except Exception as e:
                    r.err(f"GET {path}", e)

            # ── IDEMPOTENT RBAC WRITE (students) ──
            if role == "murid":
                t0 = time.perf_counter()
                try:
                    s = await client.get(f"{base}/student/settings")
                    csrf = (CSRF_RE.search(s.text) or [None, None])[1] or csrf
                    resp = await client.post(
                        f"{base}/student/settings/pdp-update",
                        json={"pdp_agreed": True},
                        headers={"X-CSRF-Token": csrf or ""},
                    )
                    r.rec("POST settings/pdp-update", t0, resp)
                except Exception as e:
                    r.err("POST settings/pdp-update", e)

            # ── SUSTAINED SESSION (opt-in) ──
            # The burst above is one page-load sequence. A landing-page claim of
            # "N students for M minutes" is an endurance claim, so --duration
            # keeps every session alive for M minutes doing an exam-shaped mix.
            # A failed login already returned above, so anything here is
            # authenticated -- which is the point: the published harness could
            # not tell a logged-in user from a logged-out one.
            if duration > 0:
                await sustain(r, client, base, role, exam_ids, duration)


def _weighted_choice():
    weights = [w for w, _, _ in STUDENT_MIX] + [4]
    idx = random.choices(range(len(weights)), weights=weights, k=1)[0]
    return None if idx == len(weights) - 1 else STUDENT_MIX[idx][1:]


async def sustain(r, client, base, role, exam_ids, duration):
    """Keep one already-authenticated session busy for `duration` seconds.

    Task mix and wait time mirror locustfile.py (weights 5/8/4/1/1/3, wait
    1-3s), so the numbers are comparable with the published ones. Teachers get
    the same shape over their own pages.
    """
    deadline = time.perf_counter() + duration
    while time.perf_counter() < deadline:
        if role == "murid":
            pick = _weighted_choice()
            if pick is None:
                path = f"/student/exams/{random.choice(exam_ids)}" if exam_ids else "/student/exams"
                label = "GET /student/exams/<id>"
            else:
                label, path = pick
        else:
            label = path = random.choice(TEACHER_READS)
            label = f"GET {path}"

        t0 = time.perf_counter()
        try:
            resp = await client.get(base + path)
            r.rec(label, t0, resp)
            if path.endswith(("/student/exams", "/teacher/exams")) and resp.status_code == 200:
                found = list(set(EXAM_RE.findall(resp.text)))[:3]
                if found:
                    exam_ids[:] = found
        except Exception as e:
            r.err(label, e)

        await asyncio.sleep(random.uniform(1.0, 3.0))


def heaviest_page(s):
    """The signed-in page that costs the most, as (endpoint, per_endpoint row).

    "Costs the most" is the largest payload, which is the grading the perf gate
    enforces — one rule, stated here so a human reading the report sees the same
    page the gate will judge. A page with no 200 answer is skipped rather than
    scored as zero bytes.

    Returns None when nothing qualifies (an unsigned run, or every page failed),
    which is a measurement gap and never a pass.
    """
    pages = {k: v for k, v in (s.get("per_endpoint") or {}).items()
             if PAGE_ENDPOINT.match(k) and v.get("bytes_p50")}
    if not pages:
        return None
    key = max(pages, key=lambda k: pages[k]["bytes_p50"])
    return key, pages[key]


def summary(r, wall, n_launched):
    """The whole run as one object — printed by report(), written by --json.

    One computation with two consumers. A gate that re-derived these numbers by
    scraping the printed report would drift from it the moment the wording
    changed, and would fail silently when it did.
    """
    # NaN marks a transport error; it is counted as an error, never as a latency.
    lat = [m for _, _, m, _ in r.tl if m == m]
    codes = Counter()
    for st in r.status.values():
        for k, v in st.items():
            codes[k] += v
    total = sum(len(v) for v in r.lat.values())
    server_err = sum(v for k, v in codes.items() if isinstance(k, int) and k >= 500)
    rate_limited = codes.get(429, 0)
    transport = sum(r.errors.values())
    bad = rate_limited + server_err + transport
    return {
        # The date the run happened, so the artifact can be dated by itself. The
        # evidence page falls back to this file's git commit date when it is absent
        # (`rung-050.json` predates the field) and says "date not recorded" when
        # neither exists — it must never fall back to the file's mtime, because a
        # fresh clone stamps every file with the checkout time.
        "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sessions_launched": n_launched,
        "logins_ok": r.login_ok,
        "logins_failed": r.login_failed,
        "requests_total": total,
        "status_breakdown": dict(codes),
        "rate_limited_429": rate_limited,
        "server_errors_5xx": server_err,
        "transport_errors": transport,
        "error_rate_pct": round(100.0 * bad / max(total, 1), 3),
        "latency_ms": {"p50": pct(lat, 50), "p95": pct(lat, 95), "p99": pct(lat, 99)},
        "identity_ok": r.identity_ok,
        "identity_checked": r.identity_checked,
        "identity_wrong": dict(r.identity_wrong),
        "per_endpoint": {
            k: {
                "n": len(v), "p50": pct(v, 50), "p95": pct(v, 95), "p99": pct(v, 99),
                # The median response size and the median round-trip count, so a
                # single outlier page cannot decide the verdict and neither can a
                # single cheap one. `None` when the endpoint answered nothing 200,
                # which the gate reads as "not measured" rather than as zero.
                "bytes_p50": pct(r.nbytes[k], 50) if r.nbytes[k] else None,
                "roundtrips_p50": pct(r.trips[k], 50) if r.trips[k] else None,
                # What the render issued and how much data it read. The gate needs
                # both to say whether a page that grew did so because of the release
                # or because the school did; `None` is not measured, never zero.
                "queries_p50": pct(r.asked[k], 50) if r.asked[k] else None,
                "rows_p50": pct(r.rowsread[k], 50) if r.rowsread[k] else None,
            }
            for k, v in r.lat.items()
        },
        "wall_s": round(wall, 2),
        "throughput_rps": round(total / wall, 2) if wall > 0 else 0.0,
    }


def report(r, accounts_used, roster_src, wall, n_launched):
    s = summary(r, wall, n_launched)
    total = s["requests_total"]

    print("\n" + "=" * 82)
    print("PER-ENDPOINT RESULTS  (milliseconds)")
    print("=" * 82)
    print(f"{'endpoint':<34}{'n':>5}{'p50':>8}{'p95':>8}{'p99':>8}{'KB':>8}"
          f"{'queries':>9}{'rows':>8}{'tries':>7}   status")
    for key in r.lat:
        st = dict(r.status[key])
        kb = pct(r.nbytes[key], 50) / 1024.0 if r.nbytes[key] else 0.0
        trips = pct(r.trips[key], 50) if r.trips[key] else 0.0
        asked = pct(r.asked[key], 50) if r.asked[key] else 0.0
        rows = pct(r.rowsread[key], 50) if r.rowsread[key] else 0.0
        print(f"{key:<34}{len(r.lat[key]):>5}{pct(r.lat[key], 50):>8.0f}"
              f"{pct(r.lat[key], 95):>8.0f}{pct(r.lat[key], 99):>8.0f}"
              f"{kb:>8.1f}{asked:>9.0f}{rows:>8.0f}{trips:>7.0f}   {st}")

    print("\n" + "=" * 82)
    print(f"sessions launched  : {s['sessions_launched']}")
    print(f"logins succeeded   : {s['logins_ok']}   failed: {s['logins_failed']}")
    print(f"roster             : {roster_src}  ({accounts_used})")
    print(f"TOTAL requests     : {total}")
    print(f"status breakdown   : {s['status_breakdown']}")
    print(f"429 rate-limited   : {s['rate_limited_429']}")
    print(f"5xx server errors  : {s['server_errors_5xx']}")
    print(f"transport errors   : {s['transport_errors']}")
    if r.login_missed:
        print(f"logins not reaching a dashboard ({s['logins_failed']}):")
        for where, n in r.login_missed.most_common(5):
            print(f"    {n:>4} x {where}")
    print(f"error rate         : {s['rate_limited_429'] + s['server_errors_5xx'] + s['transport_errors']}"
          f"/{total} = {s['error_rate_pct']:.2f}%  (429 + 5xx + transport)")
    print(f"overall latency    : p50={s['latency_ms']['p50']:.0f}ms "
          f"p95={s['latency_ms']['p95']:.0f}ms p99={s['latency_ms']['p99']:.0f}ms")
    heaviest = heaviest_page(s)
    if heaviest:
        key, cost = heaviest
        # Each of the four may be None on a run whose app is too old to send that
        # header. Printing it as 0 would invent a measurement, so it says so.
        def num(value, places=0):
            return "n/a" if value is None else f"{value:.{places}f}"

        print(f"heaviest page      : {key} — {num(cost['bytes_p50'] / 1024.0, 1)} KB, "
              f"{num(cost['queries_p50'])} queries issued, {num(cost['rows_p50'])} rows "
              f"read, {num(cost['roundtrips_p50'])} round-trip(s) served "
              f"(the numbers the perf gate compares)")
    print(f"identity verified  : {s['identity_ok']}/{s['identity_checked']} via /auth/me user_id")
    if r.identity_wrong:
        print(f"  !! WRONG IDENTITY : {dict(r.identity_wrong)}")
    if r.errors:
        print(f"app errors         : {dict(r.errors)}")
    print(f"wall clock         : {wall:.1f}s")
    if wall > 0:
        print(f"throughput         : {s['throughput_rps']:.1f} requests/s")
    # Did it hold, or degrade? Compare the two halves of the run.
    if r.tl:
        span = max(t for t, _, _, _ in r.tl)
        mid = span / 2.0
        for label, lo, hi in (("first half", 0.0, mid), ("second half", mid, span + 1)):
            ms = [m for t, _, m, _ in r.tl if lo <= t < hi and m == m]
            bad_half = sum(1 for t, _, _, s in r.tl
                           if lo <= t < hi and (s == "EXC" or s == 429 or (isinstance(s, int) and s >= 500)))
            if ms:
                print(f"  {label:<12} n={len(ms):>6} p50={pct(ms, 50):>7.0f}ms "
                      f"p95={pct(ms, 95):>7.0f}ms  bad={bad_half}")
    print("=" * 82)
    return s


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("n_students", nargs="?", type=int, default=300)
    ap.add_argument("n_teachers", nargs="?", type=int, default=30)
    ap.add_argument("--roster", default=str(DEFAULT_ROSTER))
    ap.add_argument("--base", default="https://scangrade.web.id")
    ap.add_argument("--duration", type=float, default=0.0,
                    help="keep each session busy for N seconds (endurance mode)")
    ap.add_argument("--json", default="",
                    help="write the run summary to this path as JSON")
    args = ap.parse_args()

    accounts, password, src = load_roster(args.roster)
    students = [a for a in accounts if a["role"] == "murid"]
    teachers = [a for a in accounts if a["role"] == "guru"]
    print(f"roster: {len(students)} murid, {len(teachers)} guru  [{src}]")
    if not students or not teachers:
        print("!! need at least one murid and one guru account")
        return 1

    # Refuse to reuse accounts: that is what made an earlier run report a fake
    # 49% error rate (16 accounts answering for 300 sessions).
    if args.n_students > len(students) or args.n_teachers > len(teachers):
        print(f"!! refusing to reuse accounts: asked {args.n_students} murid / "
              f"{args.n_teachers} guru but roster has {len(students)}/{len(teachers)}.")
        print("   Reusing logins makes rate-limit artifacts look like server errors.")
        print("   Provision more accounts (provision_loadtest.py <n> <m>) or lower the counts.")
        return 2

    roster = [(a, password) for a in students[: args.n_students]] + \
             [(a, password) for a in teachers[: args.n_teachers]]

    r = Results()
    sem = asyncio.Semaphore(len(roster))
    print(f"launching {len(roster)} concurrent sessions "
          f"({args.n_students} murid + {args.n_teachers} guru) against {args.base} ...")
    t0 = time.perf_counter()
    await asyncio.gather(*(session(r, a, pw, args.base, sem, args.duration)
                           for a, pw in roster))
    s = report(r, f"{len(students)} murid / {len(teachers)} guru", src,
               time.perf_counter() - t0, len(roster))
    s["base"] = args.base
    s["duration_s"] = args.duration
    s["roster_source"] = src
    if args.json:
        Path(args.json).write_text(json.dumps(s, indent=2, sort_keys=True), encoding="utf-8")
        print(f"summary written     : {args.json}")
    return 0


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")  # verify=False on the public cert
    sys.exit(asyncio.run(main()))
