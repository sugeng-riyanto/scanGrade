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
"""
import argparse
import asyncio
import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import httpx

DEFAULT_ROSTER = Path(__file__).resolve().parent / ".freebuff" / "lt_roster.json"
DEMO_PASSWORD = "demo123"
LT_PASSWORD = "LoadTest123!"

STUDENT_READS = ["/student/dashboard", "/student/exams", "/student/results", "/student/settings"]
TEACHER_READS = ["/teacher/dashboard", "/teacher/exams", "/teacher/results", "/teacher/templates"]

CSRF_RE = re.compile(r'name="csrf-token"\s+content="([^"]+)"')


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
        self.identity_checked = 0
        self.identity_ok = 0
        self.identity_wrong = Counter()
        self.login_ok = 0
        self.login_failed = 0
        # Where a login ended up when it did not reach a dashboard. Kept
        # separate from transport errors so the two are never conflated.
        self.login_missed = Counter()

    def rec(self, key, t0, resp):
        self.lat[key].append((time.perf_counter() - t0) * 1000)
        self.status[key][resp.status_code] += 1

    def err(self, key, exc):
        self.lat[key].append(float("nan"))
        self.errors[f"{key}: {type(exc).__name__}"] += 1
        self.status[key]["EXC"] += 1


def pct(values, p):
    vals = sorted(v for v in values if v == v)  # drop NaN
    if not vals:
        return 0.0
    idx = min(len(vals) - 1, max(0, int(round((p / 100.0) * len(vals) + 0.5)) - 1))
    return vals[idx]


async def session(r, acct, password, base, sem):
    role = acct["role"]
    reads = STUDENT_READS if role == "murid" else TEACHER_READS
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


def report(r, accounts_used, roster_src, wall, n_launched):
    print("\n" + "=" * 82)
    print("PER-ENDPOINT RESULTS  (milliseconds)")
    print("=" * 82)
    print(f"{'endpoint':<34}{'n':>5}{'p50':>8}{'p95':>8}{'p99':>8}   status")
    for key in r.lat:
        st = dict(r.status[key])
        print(f"{key:<34}{len(r.lat[key]):>5}{pct(r.lat[key], 50):>8.0f}"
              f"{pct(r.lat[key], 95):>8.0f}{pct(r.lat[key], 99):>8.0f}   {st}")

    total = sum(len(v) for v in r.lat.values())
    code_totals = Counter()
    for st in r.status.values():
        for k, v in st.items():
            code_totals[k] += v
    server_err = sum(v for k, v in code_totals.items() if isinstance(k, int) and k >= 500)
    bad = code_totals.get("EXC", 0) + server_err + code_totals.get(429, 0)

    print("\n" + "=" * 82)
    print(f"sessions launched  : {n_launched}")
    print(f"logins succeeded   : {r.login_ok}   failed: {r.login_failed}")
    print(f"roster             : {roster_src}  ({accounts_used})")
    print(f"TOTAL requests     : {total}")
    print(f"status breakdown   : {dict(code_totals)}")
    print(f"429 rate-limited   : {code_totals.get(429, 0)}")
    print(f"5xx server errors  : {server_err}")
    print(f"transport errors   : {sum(r.errors.values())}")
    if r.login_missed:
        print(f"logins not reaching a dashboard ({r.login_failed}):")
        for where, n in r.login_missed.most_common(5):
            print(f"    {n:>4} x {where}")
    print(f"error rate         : {bad}/{total} = {100.0 * bad / max(total, 1):.2f}%"
          f"  (429 + 5xx + transport)")
    print(f"identity verified  : {r.identity_ok}/{r.identity_checked} via /auth/me user_id")
    if r.identity_wrong:
        print(f"  !! WRONG IDENTITY : {dict(r.identity_wrong)}")
    if r.errors:
        print(f"app errors         : {dict(r.errors)}")
    print(f"wall clock         : {wall:.1f}s")
    print("=" * 82)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("n_students", nargs="?", type=int, default=300)
    ap.add_argument("n_teachers", nargs="?", type=int, default=30)
    ap.add_argument("--roster", default=str(DEFAULT_ROSTER))
    ap.add_argument("--base", default="https://scangrade.web.id")
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
    await asyncio.gather(*(session(r, a, pw, args.base, sem) for a, pw in roster))
    report(r, f"{len(students)} murid / {len(teachers)} guru", src,
           time.perf_counter() - t0, len(roster))
    return 0


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")  # verify=False on the public cert
    sys.exit(asyncio.run(main()))
