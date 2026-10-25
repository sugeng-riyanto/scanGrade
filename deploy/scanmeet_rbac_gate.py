#!/usr/bin/env python3
"""ScanMeet's authorization boundary, measured against the staging deployment.

Why this exists
---------------
The media gate proves a room works for the accounts it is meant to serve. It does
not prove the *door*: a pupil who must not open a room, a second school that must
not receive a token, a table the browser's own key must not read. Those are
authorization claims, and every one of them can be true while the UI looks
perfect — a hidden button is not a rule, and `service_role` bypasses RLS, so a
gate that only ever talks to the backend can report PASS about a policy that was
never exercised.

So each row is taken at the level the rule is enforced at:

* **sign-in and page access** through the app's own form and its own cookies,
  because that is where `login_required` and the role checks live;
* **the Live API** with the app's own CSRF token, because those routes decide
  from `g.user_role`/`g.user_school_id` — the session, not the request body;
* **PostgREST with the anon key and a real user JWT**, because that is the only
  context in which a row-level policy can be observed at all.

The fifteen rows
----------------
``teacher-sign-in``            the teacher's own credentials reach the teacher dashboard
``student-sign-in``            a pupil's credentials reach the pupil dashboard
``teacher-door-refuses-pupil`` a pupil GETting /teacher/dashboard never gets 200
``pupil-cannot-open-room``     a pupil POSTing the session route is refused (403)
``teacher-opens-room``         the same route creates the room for its owner (201)
``outsider-refused-status``    another school's pupil cannot read the exam's Live state
``pupil-cannot-end-room``      a pupil cannot end a room that exists
``outsider-refused-token``     another school's pupil cannot obtain a join token
``noschool-refused-paper``     a pupil who belongs to no school is not given the paper
``noschool-refused-token``     …and cannot obtain a join token either
``pupil-gets-token``           the enrolled pupil can, and the token publishes camera only
``teacher-ends-room``          the owner ends the room it opened
``rls-profiles-scoped``        anon key + pupil JWT reads only its own school's profiles
``rls-cross-school-invisible`` the two schools' answers share not one profile row
``rls-live-tables-invisible``  anon key + pupil JWT reads no live session/hand-raise row

Exit codes follow the other instruments here: 0 every row passed · 1 a row
disagreed · 2 could not measure (missing account, feature off, sign-in failed,
no `requests`). An incomplete run is **2**, never 0.

Limits, stated rather than discovered
-------------------------------------
* It reads the *shape* of the rules, not every route: the hand-raise and roster
  endpoints share `_check(..., staff_only=True)` with the two rows here, and a
  route that stopped using that helper would not be noticed by this file.
* The pupil token is decoded with signature verification **off** on purpose. The
  claim being read (`canPublishSources`) is written by the server, and the token
  was just handed to this process by that server; the check is that the *grant*
  is camera-only, not that the JWT was signed — LiveKit verifies that itself.
* Row 12 passes when PostgREST refuses the table or answers with no rows. Both
  are "the browser key cannot read pupil presence", which is the rule.

Usage (on the staging host, against the staging app's loopback port)::

    python -m deploy.scanmeet_rbac_gate --env ~/.config/scanmeet/staging.env \
        --url http://127.0.0.1:5055 --exam <exam uuid> \
        --teacher <email>:<password> --student <email>:<password> \
        --student2 <email>:<password> --outsider <email>:<password> \
        --noschool <email>:<password>

`--noschool` is required for the same reason `--outsider` is: an account with no
school is the one identity whose *absence* from the tenant is the rule under test,
and a gate that leaves it out silently is a gate that reports a full pass about
a boundary nobody probed.

No password, token or cookie is ever printed; the report names the row, the
verdict and the HTTP status it was taken from.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin

from deploy.livekit_preflight import parse_env

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"

#: The sign-in page carries its token in a meta tag and the form's own script
#: copies it in at submit time — there is no hidden input to read at load time,
#: which is why a gate that looked for one sent an empty token and read the
#: resulting 403 as a wrong password.
_CSRF = re.compile(r'<meta\s+name="csrf-token"\s+content="([^"]+)"')
_CSRF_INPUT = re.compile(r'name="_csrf_token"\s+value="([^"]+)"')


@dataclass
class Row:
    name: str
    verdict: str
    detail: str


@dataclass
class Report:
    rows: list[Row] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add(self, name: str, verdict: str, detail: str) -> None:
        self.rows.append(Row(name, verdict, detail))

    @property
    def failed(self) -> int:
        return sum(1 for row in self.rows if row.verdict == FAIL)

    @property
    def passed(self) -> int:
        return sum(1 for row in self.rows if row.verdict == PASS)


def identity(pair: str) -> tuple[str, str]:
    """`email:password` → its two halves, split once.

    A password is generated by `secrets.token_urlsafe`, which never emits a
    colon; splitting on the first one is therefore exact, and a malformed
    argument is a refusal rather than a half-credential sent somewhere.
    """
    email, sep, password = pair.partition(":")
    if not sep or not email or not password:
        raise SystemExit(f"identity must be email:password — got {email or 'empty'}")
    return email.strip().lower(), password


def claims(token: str) -> dict:
    """The payload of a JWT, unverified — see the module docstring."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


class Browser:
    """One signed-in session, over the app's own form and cookies."""

    def __init__(self, base: str, requests_mod):
        self.base = base.rstrip("/") + "/"
        self.s = requests_mod.Session()
        self.csrf = ""

    def get(self, path: str, **kw):
        return self.s.get(urljoin(self.base, path.lstrip("/")), timeout=30, **kw)

    def post_form(self, path: str, data: dict, **kw):
        return self.s.post(urljoin(self.base, path.lstrip("/")), data=data,
                           timeout=30, allow_redirects=False, **kw)

    def post_api(self, path: str, **kw):
        headers = {"X-CSRF-Token": self.csrf, "Content-Type": "application/json"}
        headers.update(kw.pop("headers", {}))
        return self.s.post(urljoin(self.base, path.lstrip("/")), headers=headers,
                           timeout=60, allow_redirects=False, **kw)

    def warm(self) -> None:
        """Load the sign-in page and keep the cookie and token it set."""
        page = self.get("/auth/sign-in")
        body = page.text or ""
        found = _CSRF.search(body) or _CSRF_INPUT.search(body)
        self.csrf = found.group(1) if found else ""

    def sign_in(self, email: str, password: str):
        return self.post_form("/auth/sign-in", {
            "email": email, "password": password, "_csrf_token": self.csrf})


def sign_in_row(report: Report, name: str, browser: Browser, email: str,
                password: str, landing: str, marker: str) -> bool:
    """Sign in through the form, then read the page the redirect points at."""
    browser.warm()
    response = browser.sign_in(email, password)
    if response.status_code not in (302, 303):
        report.add(name, FAIL,
                   f"sign-in answered {response.status_code}, not a redirect "
                   "(a wrong password renders the form again)")
        return False
    page = browser.get(landing)
    text = page.text or ""
    if page.status_code != 200:
        report.add(name, FAIL, f"{landing} answered {page.status_code} after sign-in")
        return False
    if marker and marker not in text:
        report.add(name, FAIL, f"{landing} rendered without its own marker")
        return False
    report.add(name, PASS, f"signed in, {landing} 200")
    return True


def paper_refusal(status: int, location: str) -> tuple[str, str]:
    """The no-school pupil's answer from the sitting door, as a row.

    A refusal there is a **redirect to the exam list**, and that landing is part
    of the claim: a 302 to anywhere else is a different door (a sign-in page, a
    SEB guide), and reading every redirect as a refusal would pass on a session
    that had simply expired. 200 is the failure this row exists for — the paper
    was served to an account with no tenant.
    """
    landed = (location or "").rstrip("/")
    if status in (302, 303) and landed.endswith("/student/exams"):
        return PASS, f"the no-school pupil was sent to the exam list ({status})"
    if status == 200:
        return FAIL, "the paper was served to an account that belongs to no school"
    return FAIL, (f"the no-school pupil got {status}"
                  + (f" landing {landed}" if landed else "") + ", not the exam list")


def supabase_login(settings: dict, email: str, password: str, requests_mod):
    """A real user JWT, from the same auth service the app signs in against."""
    url = settings["SUPABASE_URL"].rstrip("/") + "/auth/v1/token?grant_type=password"
    key = settings.get("SUPABASE_ANON_KEY") or settings.get("SUPABASE_PUBLISHABLE_KEY")
    response = requests_mod.post(url, timeout=30,
                                 headers={"apikey": key, "Content-Type": "application/json"},
                                 json={"email": email, "password": password})
    if response.status_code != 200:
        return None
    return (response.json() or {}).get("access_token")


def rest_get(settings: dict, path: str, token: str, requests_mod):
    """PostgREST as the *browser* would call it: anon key plus a user JWT."""
    url = settings["SUPABASE_URL"].rstrip("/") + "/rest/v1/" + path
    key = settings.get("SUPABASE_ANON_KEY") or settings.get("SUPABASE_PUBLISHABLE_KEY")
    return requests_mod.get(url, timeout=30, headers={
        "apikey": key, "Authorization": f"Bearer {token}",
        "Accept": "application/json"})


def run(args, report: Report) -> int:
    try:
        import requests
    except ImportError:
        print("FAIL: the requests package is not installed in this interpreter")
        return 2
    try:
        settings = parse_env(Path(args.env).read_text(encoding="utf-8"))
    except OSError:
        print("FAIL: cannot read the private staging configuration")
        return 2

    teacher = identity(args.teacher)
    student = identity(args.student)
    outsider = identity(args.outsider)
    noschool = identity(args.noschool)

    live = args.url.rstrip("/") + f"/api/live/exams/{args.exam}"
    teacher_browser, student_browser, outsider_browser, noschool_browser = (
        Browser(args.url, requests), Browser(args.url, requests),
        Browser(args.url, requests), Browser(args.url, requests))

    # ── 1-3: the doors ───────────────────────────────────────────────────────
    if not sign_in_row(report, "teacher-sign-in", teacher_browser, *teacher,
                       landing="/teacher/dashboard", marker=""):
        report.notes.append("every later row needs a teacher session")
    if not sign_in_row(report, "student-sign-in", student_browser, *student,
                       landing="/student/dashboard", marker=""):
        report.notes.append("every later row needs a pupil session")
    outsider_browser.warm()
    outs = outsider_browser.sign_in(*outsider)

    # Not followed: an anonymous GET here is redirected to the sign-in page,
    # which answers 200 — so "200" has to mean the teacher page itself, not the
    # page a refusal lands on.
    pupil_door = student_browser.get("/teacher/dashboard", allow_redirects=False)
    verdict = PASS if pupil_door.status_code in (302, 303, 401, 403) else FAIL
    report.add("teacher-door-refuses-pupil", verdict,
               f"pupil got {pupil_door.status_code} from /teacher/dashboard")

    # ── 4: a pupil cannot open a room ────────────────────────────────────────
    if student_browser.csrf:
        pupil_create = student_browser.post_api(f"/api/live/exams/{args.exam}/sessions")
        report.add("pupil-cannot-open-room",
                   PASS if pupil_create.status_code == 403 else FAIL,
                   f"pupil POST /sessions answered {pupil_create.status_code} (want 403)")
    else:
        report.add("pupil-cannot-open-room", SKIP, "no CSRF token on the pupil session")

    # ── 5: the owner opens it ────────────────────────────────────────────────
    created = teacher_browser.post_api(f"/api/live/exams/{args.exam}/sessions")
    session_id = ""
    if created.status_code in (200, 201):
        session_id = (created.json() or {}).get("session_id", "")
    report.add("teacher-opens-room",
               PASS if session_id else FAIL,
               f"owner POST /sessions answered {created.status_code}"
               + ("" if session_id else " without a session"))

    # ── 6-8: the other school ────────────────────────────────────────────────
    outs_status = outsider_browser.get(f"/api/live/exams/{args.exam}?")
    report.add("outsider-refused-status",
               PASS if outs_status.status_code in (401, 403) else FAIL,
               f"outsider GET exam live state answered {outs_status.status_code} (want 401/403)")

    # ── 8b: an account that belongs to no school at all ──────────────────────
    # The sitting door reads the pupil's school and class from the profile; this
    # account has neither, which makes it the one identity whose *absence* is the
    # rule. It is measured at the door rather than at the API because that is
    # where a pupil would land: a redirect to the exam list is the refusal, and a
    # 200 would mean the paper was served to somebody with no tenant.
    noschool_browser.warm()
    ns_sign_in = noschool_browser.sign_in(*noschool)
    if ns_sign_in.status_code not in (302, 303):
        report.add("noschool-refused-paper", SKIP,
                   f"the no-school account could not sign in ({ns_sign_in.status_code})")
    else:
        paper = noschool_browser.get(f"/student/exams/{args.exam}",
                                     allow_redirects=False)
        verdict, detail = paper_refusal(paper.status_code,
                                        paper.headers.get("Location") or "")
        report.add("noschool-refused-paper", verdict, detail)

    if session_id:
        pupil_end = student_browser.post_api(f"/api/live/sessions/{session_id}/end")
        report.add("pupil-cannot-end-room",
                   PASS if pupil_end.status_code == 403 else FAIL,
                   f"pupil POST /end answered {pupil_end.status_code} (want 403)")

        outs_token = outsider_browser.post_api(f"/api/live/sessions/{session_id}/token")
        report.add("outsider-refused-token",
                   PASS if outs_token.status_code == 403 else FAIL,
                   f"outsider POST /token answered {outs_token.status_code} (want 403)")

        ns_token = noschool_browser.post_api(f"/api/live/sessions/{session_id}/token") \
            if ns_sign_in.status_code in (302, 303) else None
        report.add("noschool-refused-token",
                   SKIP if ns_token is None else
                   (PASS if ns_token.status_code == 403 else FAIL),
                   "the no-school account never signed in" if ns_token is None else
                   f"no-school POST /token answered {ns_token.status_code} (want 403)")

        pupil_token = student_browser.post_api(f"/api/live/sessions/{session_id}/token")
        sources = []
        if pupil_token.status_code == 200:
            sources = claims((pupil_token.json() or {}).get("token", "")).get(
                "video", {}).get("canPublishSources", []) or []
        good = pupil_token.status_code == 200 and sources == ["camera"]
        report.add("pupil-gets-token", PASS if good else FAIL,
                   f"pupil POST /token answered {pupil_token.status_code}"
                   + (f" with publish sources {sources}" if pupil_token.status_code == 200 else ""))

        ended = teacher_browser.post_api(f"/api/live/sessions/{session_id}/end")
        report.add("teacher-ends-room",
                   PASS if ended.status_code in (200, 202) else FAIL,
                   f"owner POST /end answered {ended.status_code}")
    else:
        for name in ("pupil-cannot-end-room", "outsider-refused-token",
                     "noschool-refused-token", "pupil-gets-token",
                     "teacher-ends-room"):
            report.add(name, SKIP, "no room to act on — the owner could not open one")

    # ── 9-10: row-level security, in a user's own context ────────────────────
    token = supabase_login(settings, *student, requests_mod=requests)
    if not token:
        for name in ("rls-profiles-scoped", "rls-live-tables-invisible"):
            report.add(name, SKIP, "no user JWT — the auth service refused the pupil")
    else:
        mine = rest_get(settings, "profiles?select=id,school_id,role", token, requests)
        rows = mine.json() if mine.status_code == 200 and mine.text.strip().startswith("[") else []
        schools = {str(row.get("school_id")) for row in rows}
        scoped = mine.status_code == 200 and rows and len(schools) == 1
        report.add("rls-profiles-scoped", PASS if scoped else FAIL,
                   f"anon key + pupil JWT read {len(rows)} profile rows across "
                   f"{len(schools)} school(s), status {mine.status_code}")

        # A policy that answers "nothing" is not a policy that scopes: the two
        # schools' sets have to be non-empty and disjoint, or this row would read
        # PASS on a table nobody can read at all.
        other = supabase_login(settings, *outsider, requests_mod=requests)
        other_rows = []
        if other:
            response = rest_get(settings, "profiles?select=id,school_id", other, requests)
            if response.status_code == 200:
                try:
                    other_rows = response.json() or []
                except Exception:
                    other_rows = []
        mine_ids = {str(row.get("id")) for row in rows}
        other_ids = {str(row.get("id")) for row in other_rows}
        overlap = mine_ids & other_ids
        report.add("rls-cross-school-invisible",
                   PASS if rows and other_rows and not overlap else FAIL,
                   f"two schools read {len(mine_ids)} and {len(other_ids)} profile rows, "
                   f"{len(overlap)} in common")

        live_rows = rest_get(settings, "scan_live_sessions?select=id", token, requests)
        hand_rows = rest_get(settings, "scan_live_hand_raises?select=id", token, requests)
        def readable(response) -> bool:
            if response.status_code != 200:
                return False
            try:
                return bool(response.json())
            except Exception:
                return False
        open_ = readable(live_rows) or readable(hand_rows)
        report.add("rls-live-tables-invisible", FAIL if open_ else PASS,
                   f"sessions answered {live_rows.status_code}, hand raises "
                   f"{hand_rows.status_code} — neither may return a row to a browser")

    return 1 if report.failed else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env", required=True, help="private staging env file")
    parser.add_argument("--url", required=True, help="the staging app's origin")
    parser.add_argument("--exam", required=True, help="the staged exam id")
    parser.add_argument("--teacher", required=True)
    parser.add_argument("--student", required=True)
    parser.add_argument("--student2", required=False,
                        help="second pupil, used by the media gate")
    parser.add_argument("--outsider", required=True)
    parser.add_argument("--noschool", required=True,
                        help="a synthetic account that belongs to no school")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = Report()
    try:
        code = run(args, report)
    except SystemExit as exc:
        print(exc)
        return 2

    if args.json:
        print(json.dumps({"rows": [row.__dict__ for row in report.rows],
                          "passed": report.passed, "failed": report.failed,
                          "notes": report.notes}, indent=2))
    else:
        print("ScanMeet RBAC gate")
        for row in report.rows:
            print(f"  {row.verdict:4}  {row.name:26} {row.detail}")
        print(f"\n  {report.passed}/{len(report.rows)} rows passed")
        for note in report.notes:
            print(f"  note: {note}")
    if report.failed:
        print("FAIL: an authorization row disagreed")
        return 1
    if any(row.verdict == SKIP for row in report.rows):
        print("INCOMPLETE: a row could not be measured")
        return 2
    print("PASS: every authorization row held")
    return code


if __name__ == "__main__":
    sys.exit(main())
