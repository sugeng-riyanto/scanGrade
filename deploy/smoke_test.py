#!/usr/bin/env python3
"""Post-deploy smoke test — sign in as each role and open the pages that matter.

Run by ``scangrade-deploy.sh`` right after the app has been reloaded, on the
version that was just pulled. It is the only check that proves the *whole* path
still works end to end: gunicorn, nginx, TLS, session cookies, the login routes,
and the RBAC guards on every role's area.

What it is not
--------------
It is not a test suite; it makes no assertions about content and it never writes.
Every request is a GET apart from the four logins, which have to POST to exist.
It also runs only when there is a release to verify, so it does not fill the
audit log on quiet days.

Options
-------
  --check-credentials   log in only, and require that EVERY configured account
                        signs in. Used by install-auto-deploy.sh to decide
                        whether it is safe to arm the rollback gate, because a
                        half-valid config must not be able to reject a release.

Exit codes
----------
  0  passed (possibly with warnings)
  1  failed — the deploy should roll back
  2  nothing could be tested (no credentials configured, or the base URL is
     unreachable) — NOT a rollback, and deliberately distinct from 1

Why the login result is not simply treated as a failure
------------------------------------------------------
A rejected password is indistinguishable from a broken login route, and rolling
production back because someone changed a demo password would be worse than the
outage it was trying to prevent. So:

* no role can log in      -> the login path is broken  -> FAIL
* some roles cannot log in -> credentials have drifted -> WARN, keep the deploy
* a page returns 5xx, or a role reaches another role's area -> FAIL

That way the dangerous case (login broken for everyone) still rolls back, while a
stale credential in the config file cannot.
"""

from __future__ import annotations

import os
import re
import sys
import time
from dataclasses import dataclass, field

import requests

CSRF_RE = re.compile(r'name="csrf-token"\s+content="([^"]+)"')
REQUEST_TIMEOUT = 20
CONNECT_TIMEOUT = 10


# ── what each role should be able to open ────────────────────────────────────
# Paths only, no ids: a page that needs a row to exist is a flaky check, not a
# smoke test. Every entry was verified to answer 200 for a seeded account.
ROLE_PAGES: dict[str, list[str]] = {
    "super_admin": [
        "/super-admin/dashboard",
        "/super-admin/schools",
        "/super-admin/users",
        "/super-admin/plans",
        "/super-admin/activation-codes",
        "/super-admin/privacy-settings",
        "/super-admin/demo-settings",
        "/super-admin/logs",
        "/super-admin/comms",
    ],
    "admin_sekolah": [
        "/admin-sekolah/dashboard",
        "/admin-sekolah/students",
        "/admin-sekolah/teachers",
        "/admin-sekolah/classes",
        "/admin-sekolah/subjects",
        "/admin-sekolah/import",
        "/admin-sekolah/subscription",
        "/admin-sekolah/comms",
    ],
    "guru": [
        "/teacher/dashboard",
        "/teacher/exams",
        "/teacher/grading",
        "/teacher/results",
        "/teacher/students",
        "/teacher/classes",
        "/teacher/ai-settings",
        "/teacher/settings",
        "/teacher/comms",
    ],
    "murid": [
        "/student/dashboard",
        "/student/exams",
        "/student/results",
        "/student/recover",
        "/student/settings",
        "/student/comms",
    ],
}

# Who must NOT be able to reach whose area. super_admin is allowed everywhere.
ROLE_AREAS = {
    "super_admin": "/super-admin/dashboard",
    "admin_sekolah": "/admin-sekolah/dashboard",
    "guru": "/teacher/dashboard",
    "murid": "/student/dashboard",
}
FORBIDDEN: dict[str, list[str]] = {
    "super_admin": [],
    "admin_sekolah": ["super_admin"],
    "guru": ["super_admin", "admin_sekolah"],
    "murid": ["super_admin", "admin_sekolah", "guru"],
}
LOGIN_PATHS = {
    "super_admin": "/auth/login",
    "admin_sekolah": "/auth/login",
    "guru": "/auth/login-user",
    "murid": "/auth/login-user",
}
ROLES = ("super_admin", "admin_sekolah", "guru", "murid")


@dataclass
class Result:
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checked: int = 0
    # Whether *any* HTTP response came back. Distinguishes "the app is broken"
    # from "this box cannot reach the app right now", which are not the same
    # problem and must not have the same answer.
    reachable: bool = False

    def fail(self, msg: str) -> None:
        self.failures.append(msg)
        print(f"   FAIL  {msg}")

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)
        print(f"   warn  {msg}")

    def ok(self, msg: str) -> None:
        self.checked += 1
        print(f"   ok    {msg}")


@dataclass
class Account:
    role: str
    email: str
    password: str


def creds_from_env() -> list[Account]:
    """``SMOKE_<ROLE>`` holds ``email:password`` — split on the FIRST colon."""
    accounts = []
    for role in ROLES:
        raw = os.environ.get(f"SMOKE_{role.upper()}", "").strip()
        if not raw:
            continue
        email, sep, password = raw.partition(":")
        if not sep or not email or not password:
            print(f"   warn  SMOKE_{role.upper()} is malformed (want email:password)")
            continue
        accounts.append(Account(role, email.strip(), password))
    return accounts


def login(session: requests.Session, base: str, acct: Account, res: Result) -> str:
    """Return 'ok', 'rejected', 'broken' or 'unreachable'."""
    path = LOGIN_PATHS[acct.role]
    try:
        page = session.get(f"{base}{path}", timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        print(f"   warn  {acct.role}: cannot reach {base}{path} — "
              f"{type(exc).__name__}: {exc}")
        return "unreachable"

    res.reachable = True
    if page.status_code != 200:
        res.fail(f"{acct.role}: GET {path} -> {page.status_code} (expected 200)")
        return "broken"

    match = CSRF_RE.search(page.text)
    if not match:
        res.fail(f"{acct.role}: {path} carries no csrf-token meta tag")
        return "broken"

    try:
        response = session.post(
            f"{base}{path}",
            data={
                "_csrf_token": match.group(1),
                "email": acct.email,
                "password": acct.password,
            },
            timeout=REQUEST_TIMEOUT,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        # The page loaded, so the server is up: a failed POST here is the app
        # failing, not the network.
        res.fail(f"{acct.role}: POST {path} failed — {type(exc).__name__}: {exc}")
        return "broken"

    if response.status_code >= 500:
        res.fail(f"{acct.role}: POST {path} -> {response.status_code}")
        return "broken"

    # A successful login redirects; the login page re-renders with an inline
    # error when the credentials are refused.
    if response.status_code in (301, 302, 303, 307, 308):
        res.ok(f"{acct.role}: signed in as {acct.email}")
        return "ok"

    res.warn(f"{acct.role}: credentials refused for {acct.email} "
             f"(POST {path} -> {response.status_code})")
    return "rejected"


def check_pages(session: requests.Session, base: str, acct: Account, res: Result) -> None:
    for path in ROLE_PAGES[acct.role]:
        try:
            response = session.get(f"{base}{path}", timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            res.fail(f"{acct.role}: GET {path} failed — {type(exc).__name__}: {exc}")
            continue

        if response.status_code == 200:
            res.ok(f"{acct.role}: {path}")
        elif response.status_code in (301, 302, 307):
            # A bounce back to a login page means the session was not accepted;
            # a bounce anywhere else is just a redirect we did not expect here.
            location = response.headers.get("Location", "")
            if "login" in location:
                res.fail(f"{acct.role}: {path} -> {response.status_code} {location} "
                         f"(session not accepted)")
            else:
                res.warn(f"{acct.role}: {path} -> {response.status_code} {location}")
        else:
            res.fail(f"{acct.role}: {path} -> {response.status_code}")


def check_isolation(session: requests.Session, base: str, acct: Account, res: Result) -> None:
    """A role must not be able to open another role's landing page."""
    for other in FORBIDDEN[acct.role]:
        path = ROLE_AREAS[other]
        try:
            response = session.get(f"{base}{path}", timeout=REQUEST_TIMEOUT,
                                   allow_redirects=False)
        except requests.RequestException as exc:
            res.fail(f"{acct.role}: GET {path} failed — {type(exc).__name__}: {exc}")
            continue

        if response.status_code == 200:
            res.fail(f"RBAC LEAK: {acct.role} opened {path} belonging to {other}")
        else:
            res.ok(f"{acct.role}: {path} correctly refused ({response.status_code})")


def main() -> int:
    check_credentials = "--check-credentials" in sys.argv

    base = os.environ.get("SMOKE_BASE_URL", "https://scangrade.web.id").rstrip("/")
    verify_tls = os.environ.get("SMOKE_INSECURE", "").lower() not in ("1", "true", "yes")
    if not verify_tls:
        requests.packages.urllib3.disable_warnings()  # noqa: S001 - explicit opt-in

    accounts = creds_from_env()
    if not accounts:
        print("no SMOKE_* credentials configured — nothing to verify")
        print("   create /etc/scangrade-smoke.conf (see docs/AUTO_DEPLOY.md)")
        return 2

    unknown = [a.role for a in accounts if a.role not in ROLE_PAGES]
    if unknown:
        print(f"unknown role(s) in config: {unknown}")
        return 2

    print(f"smoke test against {base} — {len(accounts)} role(s)"
          f"{' (credentials only)' if check_credentials else ''}")

    res = Result()
    signed_in: list[Account] = []
    sessions: dict[str, requests.Session] = {}

    for acct in accounts:
        session = requests.Session()
        session.headers["User-Agent"] = "ScanGrade-SmokeTest/1"
        outcome = login(session, base, acct, res)
        if outcome == "ok":
            signed_in.append(acct)
            sessions[acct.role] = session

    # Nothing answered at all: a DNS, nginx or TLS problem. Rolling the release
    # back would not fix any of those, so this is not a failed deploy.
    if not res.reachable:
        print()
        print(f"RESULT: SKIP — {base} could not be reached from this host")
        return 2

    if not signed_in:
        print()
        print("RESULT: FAIL — no role could sign in; the login path is broken")
        print("   (every account was refused, which one changed password cannot explain)")
        return 1

    if len(signed_in) < len(accounts):
        print(f"   note: {len(accounts) - len(signed_in)} role(s) could not sign in — "
              "fix the credentials in /etc/scangrade-smoke.conf")
        if check_credentials:
            # A half-valid config is not good enough to arm a gate that can
            # reject a release: the missing role would go unchecked and nobody
            # would notice.
            print()
            print(f"RESULT: FAIL — only {len(signed_in)}/{len(accounts)} configured "
                  "account(s) signed in")
            return 1

    if not check_credentials:
        for acct in signed_in:
            session = sessions[acct.role]
            check_pages(session, base, acct, res)
            check_isolation(session, base, acct, res)

    print()
    if res.failures:
        print(f"RESULT: FAIL — {len(res.failures)} problem(s), {res.checked} check(s) passed")
        for item in res.failures:
            print(f"   - {item}")
        return 1

    suffix = f", {len(res.warnings)} warning(s)" if res.warnings else ""
    print(f"RESULT: PASS — {res.checked} check(s) green{suffix}")
    return 0


if __name__ == "__main__":
    started = time.time()
    try:
        code = main()
    except KeyboardInterrupt:
        code = 2
    print(f"({time.time() - started:.1f}s)")
    sys.exit(code)
