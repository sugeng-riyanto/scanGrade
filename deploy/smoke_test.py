#!/usr/bin/env python3
"""Post-deploy smoke test — sign in as each role and open the pages that matter.

Run by ``scangrade-deploy.sh`` right after the app has been reloaded, on the
version that was just pulled. It is the only check that proves the *whole* path
still works end to end: gunicorn, nginx, TLS, session cookies, the login routes,
and the RBAC guards on every role's area.

What it is not
--------------
It is not a test suite: apart from the exam page and the write probe below it
asserts nothing about what a page *says*, only that it answers and that the right
role can open it.

Its writes, and why there are two
---------------------------------
Neither is decoration, and both are worth knowing about before changing this file.

*The student's exam page* is not a read even though it is a GET: opening an exam
opens the sitting that belongs to it (one row per student and exam, reused by every
later open). So the student check leaves one draft sitting behind, for the demo
account, on the demo fixture that `manage.py demo-exam` keeps sittable for exactly
this purpose.

*The school admin's subject probe* is the one deliberate write. A page list cannot
see a school that has quietly gone read-only — a database role without INSERT
answers every GET above perfectly — and a write route in this app reports its own
failure as a flash *and* a redirect, so a POST that stored nothing and one that
worked are the same two bytes. So one probe subject is created, read back off
`/admin-sekolah/subjects`, and deleted again in the same run, and the row it is
named by carries the UTC instant it was made so two runs cannot collide. Deleting a
subject created two seconds ago releases nothing: no teacher assignment and no exam
can name it. This is the smoke test's only audit noise — two rows, and only when
there is a release to verify — and a run removes any earlier run's leftovers first,
so a crash between the two writes heals on the next deploy instead of leaving a fake
subject in a real school.

Options
-------
  --check-credentials   log in only, and require that EVERY configured account
                        signs in. Used by install-auto-deploy.sh to decide
                        whether it is safe to arm the rollback gate, because a
                        half-valid config must not be able to reject a release.
                        It runs neither write: arming a gate is not a release.

Exit codes
----------
  0  passed (possibly with warnings)
  1  failed — the deploy should roll back
  2  nothing could be tested (no credentials configured, or the base URL is
     unreachable) — NOT a rollback, and deliberately distinct from 1

Why the login result is not simply treated as a failure
------------------------------------------------------
A *configured* account that cannot sign in is a failure, not a note.

This used to be the other way round: a rejected password was indistinguishable
from a broken login route, so only the all-roles-refused case rolled back and a
single stale credential was a warning. Production showed the cost of that — one
role's password had drifted, so every release was signed in against one fewer
role than the config promised and still reported PASS. The role was not checked,
and nothing said so. Now:

* no role can log in            -> the login path is broken     -> FAIL
* a configured role cannot log in -> the credential has drifted -> FAIL
* a malformed SMOKE_<ROLE> entry  -> the role would be dropped   -> FAIL
* a page returns 5xx, or a role reaches another role's area    -> FAIL

The one case that still rolls back without a real problem is a genuinely
unreachable box (a DNS, nginx or TLS fault), which is a SKIP — that is not the
release's fault and rolling back would not fix it. Arming additionally requires
all six roles to be configured (`--check-credentials`), so a box cannot be armed
to check a role it has no credential for.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import unescape
from pathlib import Path

import requests

CSRF_RE = re.compile(r'name="csrf-token"\s+content="([^"]+)"')
REQUEST_TIMEOUT = 20
CONNECT_TIMEOUT = 10

# ── the one page a page-list cannot check ────────────────────────────────────
# A student's exam page is where the anti-cheat lives: the fullscreen blocker and
# the away blur with its countdown exist nowhere else, and they are the whole of
# what a student sees of the supervision. A release that drops either one leaves
# every other check in this file green — the page still answers 200 — so the
# murid check opens a real exam and reads them out of the served document.
#
# The exam is the *fixture* `deploy/demo_exam_fixture.py` describes, not whatever
# paper the account happens to have: a real exam may legitimately have anti-cheat
# switched off, and failing a release over a teacher's own setting would be this
# check lying about what it measured.
EXAM_LINK_RE = re.compile(r'href="/student/exams/([0-9a-fA-F-]{36})"')
CARD_TITLE_RE = re.compile(r"<h3[^>]*>(.*?)</h3>", re.S)
TAG_RE = re.compile(r"<[^>]+>")
ANTI_CHEAT_RE = re.compile(r"antiCheat:\s*(\{[^{}]*\})", re.S)
GRACE_RE = re.compile(r"graceSeconds:\s*(\d+)")

#: Each marker is a fact about the page rather than a spelling of it: an `x-show`
#: that no longer names the flag is a panel nothing would ever reveal, and a panel
#: whose sentence was dropped is a blurred screen with nothing to read.
FULLSCREEN_PANEL = 'x-show="fullscreenBlocked && !submitted"'
FULLSCREEN_WORDS = "Ujian ini harus dikerjakan dalam layar penuh"
AWAY_PANEL = 'x-show="awayBlurred && !submitted"'
AWAY_WORDS = "soal diburamkan sampai Anda kembali"
FULLSCREEN_WATCH = "addEventListener('fullscreenchange'"
AWAY_WATCH = "addEventListener('visibilitychange'"


# ── the one page list that cannot see a broken write path ────────────────────
# Every GET below is a read, and a school whose database role lost INSERT answers
# all of them. `admin_subject_create` makes that worse rather than better: it
# catches its own error, flashes it, and redirects — so the POST that stored
# nothing replies exactly like the one that stored the row, and a probe that read
# the reply would be green on a read-only school.
#
# What can tell them apart is the page the school admin reads next, so the probe is
# a round trip: create, read the list, delete, read the list again. A subject is the
# smallest row a school admin owns with a create and a delete of its own, and a
# subject that existed for two seconds cannot be named by any teacher assignment or
# exam, so removing it releases nothing.
PROBE_PREFIX = "__smoke"
SUBJECTS_PATH = "/admin-sekolah/subjects"
SUBJECT_CREATE_PATH = "/admin-sekolah/subjects/create"
SUBJECT_DELETE_PATH = "/admin-sekolah/subjects/{subject_id}/delete"

#: `(id, name)` per subject card on the subjects page. The delete form is drawn
#: above the name in the card, so the id that belongs to a name is the last one
#: before it — pairing them any other way would let the probe delete a subject it
#: never created. `[^<]*` because the template escapes the name.
SUBJECT_ROW_RE = re.compile(
    r'action="/admin-sekolah/subjects/([0-9a-fA-F-]{36})/delete".*?'
    r'<p class="[^"]*font-extrabold[^"]*"[^>]*>([^<]*)</p>', re.S)


def probe_subject_name(now: datetime | None = None) -> str:
    """The name this run's probe subject is created under.

    Dated because it lands in the school's own list and in the audit log, where it
    has to be recognisable as not-a-subject — and unique because the create route
    refuses a duplicate name, which a second run in the same minute must not look
    like a broken route.
    """
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"{PROBE_PREFIX}_{stamp}"


def probe_subject_rows(html: str) -> list[tuple[str, str]]:
    """`(id, name)` for every subject card on the page, in the order drawn."""
    return [(m.group(1), unescape(m.group(2)).strip())
            for m in SUBJECT_ROW_RE.finditer(html)]


#: Every write route in `admin_sekolah` catches its own exception and flashes it as
#: `Gagal: …`, then redirects — which is exactly why the redirect is not evidence.
#: Quoted into a failure so the message names the cause rather than only the
#: symptom: a failed insert and a stored row answer the same `302`, but only one of
#: them has the exception's own words rendered on the page. Searched rather than
#: parsed, because the flash markup is the template's business, not this file's.
FAILED_FLASH_RE = re.compile(r"Gagal:\s*([^<]{1,300})")

#: How many times one probe write is attempted before the answer is called. Two,
#: because this app's link to Supabase drops replies — measured on this box, the
#: delete hit `Gagal: Server disconnected` in 3 of 3 live runs and the create in 1 of
#: 3 — so one failed attempt is a property of one connection and two in a row is a
#: property of the write path. Failing on the first would quarantine healthy
#: releases; passing on the first failure would call a write path green that stores
#: nothing. Both attempts use the same subject name, which is what makes the retry
#: safe: a duplicate cannot be inserted twice, the create refuses it by name.
PROBE_ATTEMPTS = 2

#: The route's own words for a write that never got an answer *out of the
#: connection*, as opposed to one the database refused.
#:
#: This distinction is load-bearing, and it comes from the app's own contract:
#: `app/utils/supabase_retry.py` retries every read and deliberately does not retry a
#: write — "a dropped connection does not say whether the server ran the statement, so
#: re-sending a write can apply it twice" — so on a lossy link writes keep failing
#: while reads quietly succeed. Holding a release for that would turn the app's
#: accepted asymmetry into "this box may never release", and rolling back cannot fix
#: a lost TCP reply.
#:
#: Everything else does hold the release, and those are the two failures this check
#: was asked for: a school whose database role was narrowed, or a page that drops what
#: it was sent, both come back with the database's own refusal (or with nothing said
#: at all) and neither heals on its own.
CONNECTION_LOST_RE = re.compile(
    r"(Server disconnected|Connection reset|Connection aborted|Connection refused|"
    r"ConnectionError|ConnectError|NewConnectionError|RemoteProtocolError|"
    r"Read timed out|timed out)", re.I)


def is_connection_loss(said: str) -> bool:
    """Did the route's own words describe a lost link rather than a refusal?"""
    return bool(CONNECTION_LOST_RE.search(said or ""))


def route_said(response) -> str:
    """The route's own error sentence, when the page it rendered carries one."""
    match = FAILED_FLASH_RE.search(getattr(response, "text", "") or "")
    return match.group(0).strip() if match else ""


def _quote(said: str) -> str:
    """The route's words, framed for a failure message, or nothing."""
    return f" The route said: {said!r}." if said else ""


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
    "principal": [
        "/principal/dashboard",
        "/principal/analytics",
        "/principal/progress",
        "/principal/invigilation",
    ],
    "vice_principal": [
        "/vice-principal/dashboard",
        "/vice-principal/analytics",
        "/vice-principal/progress",
        "/vice-principal/invigilation",
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
    "principal": "/principal/dashboard",
    "vice_principal": "/vice-principal/dashboard",
    "guru": "/teacher/dashboard",
    "murid": "/student/dashboard",
}
# The two school officials are peers: each may open the other's dashboard (both
# stand on `school_official_required`), so neither is forbidden from the other.
# Everyone below them is forbidden from both; both are forbidden from everything
# above.
FORBIDDEN: dict[str, list[str]] = {
    "super_admin": [],
    "admin_sekolah": ["super_admin"],
    "principal": ["super_admin", "admin_sekolah"],
    "vice_principal": ["super_admin", "admin_sekolah"],
    "guru": ["super_admin", "admin_sekolah", "principal", "vice_principal"],
    "murid": ["super_admin", "admin_sekolah", "principal", "vice_principal",
              "guru"],
}
ROLES = ("super_admin", "admin_sekolah", "principal", "vice_principal",
         "guru", "murid")

#: The one page every reader signs in on. The app spells it once
#: (`app/utils/auth.py`), and this gate has to walk the page a reader is *actually*
#: sent to — signing in through an alias would prove the alias, not the merge.
LOGIN_URL = "/auth/sign-in"

#: The two URLs it replaced. They still answer, and that is a contract rather than a
#: courtesy: a school's printed login card names one, bookmarks point at them, and
#: `/tutorial/*` and the `/demo` cards link to them. `check_aliases` walks them.
LOGIN_ALIASES = ("/auth/login", "/auth/login-user")

#: role -> the page it signs in on, with its own `?role=` hint — the same hint the
#: app's own `login_door_for` hands a reader who arrives at a door, so the gate
#: exercises the page the way a teacher or a pupil really reaches it.
LOGIN_PATHS = {role: f"{LOGIN_URL}?role={role}" for role in ROLES}


@dataclass
class Result:
    failures: list[str] = field(default_factory=list)
    #: The role each failure belongs to, parallel to `failures`. A failure is a
    #: statement about one account, and the summary groups by it — so the record
    #: answers "which account could not be served" instead of leaving a reader to
    #: re-group a flat list by each line's prefix.
    failure_roles: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checked: int = 0
    # Whether *any* HTTP response came back. Distinguishes "the app is broken"
    # from "this box cannot reach the app right now", which are not the same
    # problem and must not have the same answer.
    reachable: bool = False

    def fail(self, msg: str, role: str = "") -> None:
        self.failures.append(msg)
        self.failure_roles.append(role)
        print(f"   FAIL  {msg}")

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)
        print(f"   warn  {msg}")

    def ok(self, msg: str) -> None:
        self.checked += 1
        print(f"   ok    {msg}")


def _role_of(msg: str) -> str:
    """The role a failure names at its front, if it names one.

    A call site passing `role=` is the contract; this is the belt to that braces,
    so a message that leads with its role still groups right if one is missed.
    """
    head = msg.split(": ", 1)[0]
    return head if head in ROLES else ""


def grouped_failures(res: "Result") -> list[str]:
    """One line per role: how many checks failed for it, and which.

    The record's cap is a handful of lines, so a heading per role would spend the
    budget on structure. One line per role keeps every failing account visible at
    a glance even when several fail at once, and the order is `ROLES` rather than
    first-seen so two runs with the same failures file the same record.
    """
    buckets: dict[str, list[str]] = {}
    seen: list[str] = []
    for role, msg in zip(res.failure_roles, res.failures):
        key = role or _role_of(msg) or "other"
        if key not in buckets:
            buckets[key] = []
            seen.append(key)
        prefix = f"{key}: "
        buckets[key].append(msg[len(prefix):] if msg.startswith(prefix) else msg)
    ordered = [r for r in ROLES if r in buckets] + [r for r in seen if r not in ROLES]
    return [f"   {r} ({len(buckets[r])}): " + "; ".join(buckets[r]) for r in ordered]


@dataclass
class Account:
    role: str
    email: str
    password: str


def creds_from_env() -> tuple[list[Account], list[str]]:
    """``SMOKE_<ROLE>`` holds ``email:password`` — split on the FIRST colon.

    Returns the accounts **and** the roles whose configured credential could not
    be parsed. A malformed entry used to be skipped with a warning, so the role
    quietly left the run and every later release was signed in against one fewer
    role than the operator believed; the caller now refuses to run with one.
    """
    accounts: list[Account] = []
    malformed: list[str] = []
    for role in ROLES:
        raw = os.environ.get(f"SMOKE_{role.upper()}", "").strip()
        if not raw:
            continue
        email, sep, password = raw.partition(":")
        if not sep or not email or not password:
            print(f"   warn  SMOKE_{role.upper()} is malformed (want email:password)")
            malformed.append(role)
            continue
        accounts.append(Account(role, email.strip(), password))
    return accounts, malformed


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
        res.fail(f"{acct.role}: GET {path} -> {page.status_code} (expected 200)",
                 role=acct.role)
        return "broken"

    match = CSRF_RE.search(page.text)
    if not match:
        res.fail(f"{acct.role}: {path} carries no csrf-token meta tag", role=acct.role)
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
        res.fail(f"{acct.role}: POST {path} failed — {type(exc).__name__}: {exc}",
                 role=acct.role)
        return "broken"

    if response.status_code >= 500:
        res.fail(f"{acct.role}: POST {path} -> {response.status_code}", role=acct.role)
        return "broken"

    # A successful login redirects; the login page re-renders with an inline
    # error when the credentials are refused.
    if response.status_code in (301, 302, 303, 307, 308):
        res.ok(f"{acct.role}: signed in as {acct.email}")
        return "ok"

    res.warn(f"{acct.role}: credentials refused for {acct.email} "
             f"(POST {path} -> {response.status_code})")
    return "rejected"


def check_aliases(base: str, acct: Account, res: Result) -> None:
    """The two published URLs still behave — a GET forwards, a POST signs in.

    Both halves matter and only one is obvious. A GET that 404s breaks a bookmark or
    a link on a page the school already has; a POST that *forwards* is worse, because
    the redirect throws the credentials away and the reader is handed an empty form
    with no idea why. So the alias contract is checked with a real sign-in through an
    alias, not by reading a redirect.

    The CSRF token comes from the merged page, which is where an alias forwards
    anyway — a token is per session, not per URL, so this is the same request a
    browser makes after following the forward.
    """
    session = requests.Session()
    session.headers["User-Agent"] = "ScanGrade-SmokeTest/1"

    for door in LOGIN_ALIASES:
        try:
            page = session.get(f"{base}{door}", timeout=REQUEST_TIMEOUT,
                               allow_redirects=False)
        except requests.RequestException as exc:
            res.fail(f"alias: GET {door} failed — {type(exc).__name__}: {exc}")
            continue
        location = page.headers.get("Location", "")
        if page.status_code in (301, 302, 303, 307, 308) and location.startswith(LOGIN_URL):
            res.ok(f"alias: GET {door} forwards to {LOGIN_URL}")
        else:
            res.fail(f"alias: GET {door} -> {page.status_code} {location!r} "
                     f"(published URL: expected a forward to {LOGIN_URL})")

    try:
        form = session.get(f"{base}{LOGIN_URL}", timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        res.fail(f"alias: GET {LOGIN_URL} failed — {type(exc).__name__}: {exc}")
        return
    match = CSRF_RE.search(form.text)
    if not match:
        res.fail(f"alias: {LOGIN_URL} carries no csrf-token meta tag")
        return

    door = LOGIN_ALIASES[-1]
    try:
        response = session.post(
            f"{base}{door}",
            data={"_csrf_token": match.group(1), "email": acct.email,
                  "password": acct.password},
            timeout=REQUEST_TIMEOUT, allow_redirects=False)
    except requests.RequestException as exc:
        res.fail(f"alias: POST {door} failed — {type(exc).__name__}: {exc}")
        return

    if response.status_code in (301, 302, 303, 307, 308):
        res.ok(f"alias: POST {door} signed {acct.role} in")
    else:
        res.fail(f"alias: POST {door} -> {response.status_code} (a redirect on this "
                 f"POST used to mean the credentials were dropped)")


def check_pages(session: requests.Session, base: str, acct: Account, res: Result) -> None:
    for path in ROLE_PAGES[acct.role]:
        try:
            response = session.get(f"{base}{path}", timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            res.fail(f"{acct.role}: GET {path} failed — {type(exc).__name__}: {exc}",
                     role=acct.role)
            continue

        if response.status_code == 200:
            res.ok(f"{acct.role}: {path}")
        elif response.status_code in (301, 302, 307):
            # A bounce back to a login page means the session was not accepted;
            # a bounce anywhere else is just a redirect we did not expect here.
            location = response.headers.get("Location", "")
            if "login" in location:
                res.fail(f"{acct.role}: {path} -> {response.status_code} {location} "
                         f"(session not accepted)", role=acct.role)
            else:
                res.warn(f"{acct.role}: {path} -> {response.status_code} {location}")
        else:
            res.fail(f"{acct.role}: {path} -> {response.status_code}", role=acct.role)


def _fixture():
    """The shared fixture spec, loaded by path.

    By path rather than by name because this file is also executed by a test
    harness that loads *it* by path, where its own directory is not on
    `sys.path` — and because the spec has to be the same object `manage.py`
    writes the row from, or the title this looks for and the title that exists
    are two strings that can drift apart.
    """
    path = Path(__file__).resolve().parent / "demo_exam_fixture.py"
    spec = importlib.util.spec_from_file_location("sg_demo_exam_fixture", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def sittable_exams(html: str) -> list[tuple[str, str]]:
    """``[(exam_id, title)]`` — the cards on a student's exam list.

    One card per offered exam, its title in an `<h3>` and the way in at the
    bottom, so a link's title is the last heading between it and the link above.
    """
    found: list[tuple[str, str]] = []
    marks = list(EXAM_LINK_RE.finditer(html))
    for index, mark in enumerate(marks):
        start = marks[index - 1].end() if index else 0
        titles = CARD_TITLE_RE.findall(html[start:mark.start()])
        title = unescape(TAG_RE.sub("", titles[-1])).strip() if titles else ""
        found.append((mark.group(1), title))
    return found


def check_exam_sitting(session: requests.Session, base: str, res: Result) -> None:
    """Open the demo exam as the student, and read its anti-cheat panels out.

    What "the panels appear" can mean over HTTP, and all of it: the document the
    browser is served carries both panels with their own words, the exam's own
    configuration arms them (anti-cheat off, or fullscreen not required, and
    neither panel would ever be revealed however intact the markup is), the
    countdown shows the service's own number, and the page watches the two events
    that set the flags. The render itself is what the preview is for.

    No fixture on the list is a *warning*, not a failure, with the command that
    puts one back: the demo schools are data, and a box whose demo data was
    cleared must not roll a healthy release back. A panel missing from a page that
    *was* served is a failure.
    """
    try:
        listing = session.get(f"{base}/student/exams", timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        res.fail(f"murid: GET /student/exams failed — {type(exc).__name__}: {exc}",
                 role="murid")
        return
    if listing.status_code != 200:
        res.fail(f"murid: /student/exams -> {listing.status_code}", role="murid")
        return

    fixture = _fixture()
    offered = [(exam_id, title) for exam_id, title in sittable_exams(listing.text)
               if fixture.is_fixture(title)]
    if not offered:
        res.warn("no sittable demo exam on the student's list — the exam page's "
                 "anti-cheat panels went unchecked")
        print("         put one back with: python manage.py demo-exam")
        return

    exam_id, title = offered[0]
    res.ok(f"murid: the list offers the demo exam ({title})")

    path = f"/student/exams/{exam_id}"
    try:
        page = session.get(f"{base}{path}", timeout=REQUEST_TIMEOUT, allow_redirects=False)
    except requests.RequestException as exc:
        res.fail(f"murid: GET {path} failed — {type(exc).__name__}: {exc}", role="murid")
        return
    if page.status_code != 200:
        where = page.headers.get("Location", "")
        res.fail(f"murid: {path} -> {page.status_code} {where}".rstrip() +
                 " (the demo exam would not open, so nothing on it could be read)",
                 role="murid")
        return
    res.ok(f"murid: {path}")

    html = page.text
    if f'data-exam-id="{exam_id}"' not in html or 'x-data="examApp(' not in html:
        res.fail(f"murid: {path} is not the sitting page for that exam", role="murid")
        return
    res.ok("murid: it is a sitting page for the exam that was asked for")

    found = ANTI_CHEAT_RE.search(html)
    settings = json.loads(found.group(1)) if found else {}
    if not settings.get("enabled") or not settings.get("fullscreen_required"):
        res.fail(f"murid: the exam page arms anti-cheat as {found.group(1) if found else 'nowhere'}"
                 " — both panels are revealed only when the exam asks for them",
                 role="murid")
    else:
        res.ok("murid: the page asks for anti-cheat and fullscreen")

    missing = []
    if FULLSCREEN_PANEL not in html or FULLSCREEN_WORDS not in html:
        missing.append("fullscreen blocker")
    if AWAY_PANEL not in html or AWAY_WORDS not in html:
        missing.append("away blur")
    if missing:
        res.fail(f"murid: the exam page is missing the {' and the '.join(missing)}",
                 role="murid")
    else:
        res.ok("murid: the fullscreen blocker and the away blur are on the page, "
               "each with its words")

    grace = GRACE_RE.search(html)
    if not grace or int(grace.group(1)) <= 0:
        res.fail("murid: the away blur has no countdown to show (graceSeconds="
                 f"{grace.group(1) if grace else 'absent'})", role="murid")
    else:
        res.ok(f"murid: the countdown reads {grace.group(1)}s — the service's own number")

    if FULLSCREEN_WATCH not in html or AWAY_WATCH not in html:
        res.fail("murid: the page does not watch fullscreenchange and "
                 "visibilitychange, so nothing would set either panel", role="murid")
    else:
        res.ok("murid: the page watches fullscreenchange and visibilitychange")


def _list_read(session, base, acct, res):
    """The subjects page and its csrf token, or `None` after reporting why not.

    Reading this page is not a formality: it is the only thing in the whole check
    that can distinguish a write which landed from one the route swallowed.
    """
    try:
        page = session.get(f"{base}{SUBJECTS_PATH}", timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        res.fail(f"{acct.role}: GET {SUBJECTS_PATH} failed — {type(exc).__name__}: {exc}",
                 role=acct.role)
        return None
    if page.status_code != 200:
        res.fail(f"{acct.role}: GET {SUBJECTS_PATH} -> {page.status_code} "
                 "(without the list, a write cannot be checked at all)", role=acct.role)
        return None
    token = CSRF_RE.search(page.text)
    if not token:
        res.fail(f"{acct.role}: {SUBJECTS_PATH} carries no csrf-token meta tag, so "
                 "the write cannot be armed", role=acct.role)
        return None
    return page, token.group(1)


@dataclass
class Probe:
    """One create attempt: what the page served afterwards, and what the route said.

    `rows` is carried out of the attempt because that read is also the only one
    needed to spot an earlier run's leftovers — asking for the same page twice would
    cost a request per release for nothing.
    """

    row_id: str | None = None
    rows: list[tuple[str, str]] = field(default_factory=list)
    said: str = ""
    #: The check must stop: an app that cannot be reached, or a reply that is not a
    #: redirect, is not a write that failed — it is a run that never asked.
    fatal: bool = False


def _probe_create(session, base, token, name, acct, res) -> Probe:
    """One create attempt, judged by the list rather than by the reply."""
    try:
        created = session.post(f"{base}{SUBJECT_CREATE_PATH}",
                               data={"_csrf_token": token, "name": name},
                               timeout=REQUEST_TIMEOUT, allow_redirects=False)
    except requests.RequestException as exc:
        res.fail(f"{acct.role}: POST {SUBJECT_CREATE_PATH} failed — "
                 f"{type(exc).__name__}: {exc}", role=acct.role)
        return Probe(fatal=True)
    # The route redirects whether it stored the row or caught an exception, so a
    # reply that is not a redirect is the write never being reached at all — a 403
    # from the app's CSRF hook, a 404 from a renamed route.
    if not 300 <= created.status_code < 400:
        res.fail(f"{acct.role}: POST {SUBJECT_CREATE_PATH} -> {created.status_code} "
                 f"{created.headers.get('Location', '')}".rstrip() +
                 " (expected a redirect; the write was never reached)", role=acct.role)
        return Probe(fatal=True)

    opened = _list_read(session, base, acct, res)
    if opened is None:
        return Probe(fatal=True)
    page, _ = opened
    rows = probe_subject_rows(page.text)
    for subject_id, row_name in rows:
        if row_name == name:
            return Probe(subject_id, rows, route_said(page))
    return Probe(None, rows, route_said(page) or "the list did not serve it")


def _probe_delete(session, base, token, subject_id, acct, res) -> str:
    """Delete one probe row and read the list to see whether it went.

    The answer is not evidence either. A dropped reply can happen *after* the row
    is already gone (`Server disconnected` is thrown while reading the response),
    and the route redirects after a flash exactly as it does after a delete — so
    this reads the page and returns what is left, or `""` when the row is gone.
    """
    why = ""
    for attempt in range(PROBE_ATTEMPTS):
        try:
            removed = session.post(
                f"{base}{SUBJECT_DELETE_PATH.format(subject_id=subject_id)}",
                data={"_csrf_token": token, "confirm": "1"},
                timeout=REQUEST_TIMEOUT, allow_redirects=False)
            why = "" if 300 <= removed.status_code < 400 else (
                f"DELETE -> {removed.status_code}")
        except requests.RequestException as exc:
            why = f"{type(exc).__name__}: {exc}"

        opened = _list_read(session, base, acct, res)
        if opened is None:
            return "the list could not be read to check"
        page, _ = opened
        if not any(sid == subject_id for sid, _ in probe_subject_rows(page.text)):
            return ""
        why = why or route_said(page) or "the list still serves it"
        if attempt + 1 < PROBE_ATTEMPTS:
            res.warn(f"{acct.role}: deleting probe subject {subject_id} did not take "
                     f"({why}) — retrying once")
    return why


def check_admin_write(session: requests.Session, base: str, acct: Account, res: Result) -> None:
    """Create one subject, prove the *page* serves it, delete it, prove it is gone.

    The POST's reply is never the evidence: `admin_subject_create` answers `302`
    whether it inserted a row or caught an exception and flashed it, so the only
    reader that distinguishes a working school from a read-only one is
    `/admin-sekolah/subjects` itself. A run that cannot read that page fails rather
    than skipping, because staying silent about the write path is how a read-only
    school reaches a user.

    Each write gets `PROBE_ATTEMPTS` goes, so one dropped reply cannot quarantine a
    release while two in a row still can.

    A write that fails and names a lost connection is reported without holding the
    release (`CONNECTION_LOST_RE`), because the app accepts that asymmetry on
    purpose and no release fixes it; anything else — a permission the database
    refused, a page that serves no row and says nothing — does hold it.

    It deletes exactly what it created — plus any probe row an earlier run left
    behind, which it reports, because a leftover that keeps coming back is a
    symptom worth reading.
    """
    opened = _list_read(session, base, acct, res)
    if opened is None:
        return
    _, token = opened

    name = probe_subject_name()
    probe, refusals = Probe(), []
    for attempt in range(PROBE_ATTEMPTS):
        probe = _probe_create(session, base, token, name, acct, res)
        if probe.fatal:
            return
        if probe.row_id is not None:
            break
        refusals.append(probe.said)
        if attempt + 1 < PROBE_ATTEMPTS:
            res.warn(f"{acct.role}: the create did not land ({probe.said}) — retrying once")

    row_id = probe.row_id
    if row_id is None:
        quote = "".join(_quote(s) for s in refusals)
        if refusals and all(is_connection_loss(s) for s in refusals):
            res.warn(f"{acct.role}: the probe subject could not be created in "
                     f"{PROBE_ATTEMPTS} attempts and the route named a lost "
                     "connection — the app retries reads and deliberately not "
                     f"writes, so this is the link, not the release ({name!r} was "
                     "not stored)." + quote)
        else:
            res.fail(f"{acct.role}: the subject {name!r} is not on {SUBJECTS_PATH} "
                     f"after {PROBE_ATTEMPTS} attempts — a read-only school answers "
                     "every POST and stores nothing. If a row with that name is in "
                     "the school's list, delete it." + quote, role=acct.role)
    else:
        res.ok(f"{acct.role}: created {name!r} and the page serves it")

    # One row is this run's; the rest are an earlier run's leftovers, and the prefix
    # is the only licence this check has to delete either.
    stale = [(sid, row_name) for sid, row_name in probe.rows
             if row_name.startswith(PROBE_PREFIX) and sid != row_id]
    if stale:
        res.warn(f"{acct.role}: {len(stale)} probe subject(s) left by an earlier run "
                 f"({', '.join(row_name for _, row_name in stale)}) — removing them")

    stubborn: list[tuple[str, str]] = []
    for subject_id in ([row_id] if row_id else []) + [sid for sid, _ in stale]:
        why = _probe_delete(session, base, token, subject_id, acct, res)
        if why:
            stubborn.append((subject_id, why))

    if stubborn:
        ids = ", ".join(sid for sid, _ in stubborn)
        quote = "".join(_quote(why) for _, why in stubborn)
        if all(is_connection_loss(why) for _, why in stubborn):
            res.warn(f"{acct.role}: {len(stubborn)} probe subject(s) could not be "
                     f"deleted in {PROBE_ATTEMPTS} attempts ({ids}) and the route "
                     "named a lost connection — the app retries reads and "
                     "deliberately not writes, so this is the link, not the "
                     "release. The next run clears them." + quote)
        else:
            res.fail(f"{acct.role}: {len(stubborn)} probe subject(s) are still in "
                     f"the school after being deleted ({ids}) — the delete path did "
                     f"not take. Remove them from {SUBJECTS_PATH}." + quote,
                     role=acct.role)
    elif row_id:
        res.ok(f"{acct.role}: deleted {name!r} and the page no longer serves it")


def check_isolation(session: requests.Session, base: str, acct: Account, res: Result) -> None:
    """A role must not be able to open another role's landing page."""
    for other in FORBIDDEN[acct.role]:
        path = ROLE_AREAS[other]
        try:
            response = session.get(f"{base}{path}", timeout=REQUEST_TIMEOUT,
                                   allow_redirects=False)
        except requests.RequestException as exc:
            res.fail(f"{acct.role}: GET {path} failed — {type(exc).__name__}: {exc}",
                     role=acct.role)
            continue

        if response.status_code == 200:
            res.fail(f"RBAC LEAK: {acct.role} opened {path} belonging to {other}",
                     role=acct.role)
        else:
            res.ok(f"{acct.role}: {path} correctly refused ({response.status_code})")


def main() -> int:
    check_credentials = "--check-credentials" in sys.argv

    base = os.environ.get("SMOKE_BASE_URL", "https://scangrade.web.id").rstrip("/")
    verify_tls = os.environ.get("SMOKE_INSECURE", "").lower() not in ("1", "true", "yes")
    if not verify_tls:
        requests.packages.urllib3.disable_warnings()  # noqa: S001 - explicit opt-in

    accounts, malformed = creds_from_env()
    if malformed:
        print(f"malformed credential(s) in config: {malformed}")
        print("   each SMOKE_<ROLE> must be email:password — refusing to run with "
              "a role silently dropped")
        return 1
    if not accounts:
        print("no SMOKE_* credentials configured — nothing to verify")
        print("   create /etc/scangrade-smoke.conf (see docs/AUTO_DEPLOY.md)")
        return 2

    unknown = [a.role for a in accounts if a.role not in ROLE_PAGES]
    if unknown:
        print(f"unknown role(s) in config: {unknown}")
        return 2

    # Arming a rollback gate on a half-valid config would let the missing role go
    # unchecked and nobody would notice, so arming needs every role configured.
    if check_credentials:
        configured = {a.role for a in accounts}
        missing = [r for r in ROLES if r not in configured]
        if missing:
            print(f"credential(s) missing for role(s): {missing}")
            print(f"   arming needs all {len(ROLES)} roles — see docs/AUTO_DEPLOY.md")
            return 1

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
        # A *configured* account that cannot sign in is a failure, not a note.
        # This used to be a warning in the gate path, and production carried a
        # role whose password had drifted: every release was signed in against
        # one fewer role than the config promised and reported PASS anyway.
        refused = [a.role for a in accounts if a not in signed_in]
        print(f"   {len(accounts) - len(signed_in)} configured role(s) could not "
              f"sign in: {refused}")
        print()
        print(f"RESULT: FAIL — only {len(signed_in)}/{len(accounts)} configured "
              "account(s) signed in")
        return 1

    if not check_credentials:
        # The published URLs, once, with the account that signed in first: they are
        # properties of the app rather than of a role, and a role that refused above
        # has already failed the run.
        check_aliases(base, signed_in[0], res)
        for acct in signed_in:
            session = sessions[acct.role]
            check_pages(session, base, acct, res)
            check_isolation(session, base, acct, res)
            # The exam page is the one that is not in the lists above: it needs a
            # row to exist and a class to be assigned, so it is opened by
            # discovery — and it is the only page whose *content* this asserts.
            if acct.role == "murid":
                check_exam_sitting(session, base, res)
            # And the school admin's list is the only page this file writes to. A
            # read-only school answers every check above, so the write path needs a
            # check of its own — see the probe's own note above.
            elif acct.role == "admin_sekolah":
                check_admin_write(session, base, acct, res)

    print()
    if res.failures:
        print(f"RESULT: FAIL — {len(res.failures)} problem(s), {res.checked} check(s) passed")
        for line in grouped_failures(res):
            print(line)
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
