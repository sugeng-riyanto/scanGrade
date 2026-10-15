#!/usr/bin/env python3
"""Prove, against the live box, which school a cross-school teacher's paper is filed in.

The claim under test is the one `exams.school_id` decides: a paper written by a
teacher who belongs to two NPSNs is filed under the school **this request is
acting in** — never the school named in the request body, and never a school the
account is not a member of.

Why it runs against the deployed box rather than a test client: the rule has two
halves that only meet in production — the *resolution* (`resolve_for_request`,
read fresh per request from `teacher_school_membership`) and the *write*
(`filing_school_for_request`, which refuses rather than guesses). A unit test
hands the route a fake `g`; this hands it the same cookies a browser would and
then reads the row back out of the database with the service key.

The three states, because they are three different claims:

  1. **two active memberships** — with no switcher deployed, the resolution keeps
     the *home* school (`profiles.school_id`), so the paper must be filed there.
     This is the honest control: it is what production does today for a teacher
     who belongs to two schools.
  2. **the home membership closed, the other one active** — the request now
     resolves to the second school, and the paper must be filed **there**. This is
     the requested case: a paper created while acting in the second NPSN.
  3. **a body that names a school** — while acting in the second, a body naming
     the home school (and one naming a school the account has nothing to do with)
     is refused, and nothing is written.

Usage:
    python deploy/cross_school_filing_proof.py                 # prove, then clean up
    python deploy/cross_school_filing_proof.py --keep          # leave the fixture
    python deploy/cross_school_filing_proof.py --base-url https://scangrade.web.id

It reads the checkout's `.env` for the Supabase project (service key) and the
production host. Every row it creates is named `ZZ PROOF …` so a leftover is
obvious, and the default run removes all of it again — the fixture account, its
memberships and every paper it minted.

Exit codes, the way this repository's other gates answer: 0 every claim held ·
1 a claim did not · 2 it could not measure (no credentials, or a school the
project does not have).
"""
from __future__ import annotations

import argparse
import os
import re
import secrets
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv
from supabase import create_client

ROOT = Path(__file__).resolve().parents[1]

#: The two NPSNs. A is the *home* school on the profile; B is the second school,
#: the one the account is only a member of.
HOME_NPSN = "99887711"
SECOND_NPSN = "99887733"
#: A school neither membership names, for the refusal that has nothing to do with
#: the account at all.
FOREIGN_NPSN = "99887722"

TAG = "ZZ PROOF cross-school"

RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    RESULTS.append((bool(ok), label))
    print("  {} {}  {}".format("PASS" if ok else "FAIL", label,
                               ("— " + detail) if detail else ""))
    return bool(ok)


def school_by_npsn(svc, npsn):
    rows = svc.table("schools").select("id,npsn,name,status").eq("npsn", npsn).execute().data
    if not rows:
        print("cannot measure: no school with NPSN {} on this project".format(npsn))
        sys.exit(2)
    return rows[0]


def exam_row(svc, exam_id):
    rows = svc.table("exams").select("id,title,school_id,teacher_id").eq(
        "id", exam_id).execute().data
    return rows[0] if rows else None


def exams_of(svc, school_id):
    return svc.table("exams").select("id,title").eq("school_id", school_id).execute().data or []


# ── fixture ─────────────────────────────────────────────────────────────────

def make_fixture(svc, home, second, holder):
    """One guru account, on paper in `home`, active in both schools to begin with.

    `holder` is filled in as each row appears, so a failure halfway through still
    leaves the caller something to clean up — the auth user exists the moment
    Supabase answers, and an orphaned account is exactly the mess this fixture is
    not allowed to leave behind.
    """
    email = "proof-crossschool-{}@proof.scan-grade.app".format(secrets.token_hex(4))
    password = secrets.token_urlsafe(18) + "Aa1!"
    created = svc.auth.admin.create_user({
        "email": email, "password": password, "email_confirm": True,
        "user_metadata": {"role": "guru", "full_name": "ZZ PROOF cross-school guru"},
    })
    user_id = created.user.id
    holder.update({"user_id": user_id, "email": email, "password": password,
                   "home": home, "second": second, "exams": []})
    # `upsert`, not `insert`: this project's database creates the profile row from
    # the auth user (a trigger), so an insert meets its own row — an insert here
    # failed with `profiles_pkey` the first time this ran. The upsert also *is* the
    # fixture: the row has to say `guru`, `active` and the home school, whatever the
    # trigger left in it.
    svc.table("profiles").upsert({
        "id": user_id,
        "email": email,
        "full_name": "ZZ PROOF cross-school guru",
        "role": "guru",
        "status": "active",
        "school_id": home["id"],
        "must_change_password": False,
        "tz_offset": 7,
    }).execute()
    for school, status in ((home, "active"), (second, "active")):
        svc.table("teacher_school_membership").insert({
            "user_id": user_id,
            "school_id": school["id"],
            "school_role": "guru",
            "status": status,
        }).execute()
    print("  fixture: user {} <{}> home={} ({}) memberships={}/{}".format(
        user_id[:8], email, home["npsn"], home["name"], home["npsn"], second["npsn"]))
    return holder


def close_membership(svc, user_id, school_id):
    svc.table("teacher_school_membership").update(
        {"status": "closed"}).eq("user_id", user_id).eq(
        "school_id", school_id).execute()


# ── the live session ────────────────────────────────────────────────────────

def sign_in(base_url, email, password):
    """Sign in the way the browser does: the page's token, the page's cookie jar."""
    s = requests.Session()
    page = s.get(base_url + "/auth/sign-in", timeout=30)
    page.raise_for_status()
    found = re.search(r'name="csrf-token" content="([^"]+)"', page.text)
    if not found:
        raise SystemExit("the sign-in page carried no csrf-token meta tag")
    post = s.post(base_url + "/auth/sign-in",
                  data={"email": email, "password": password, "_csrf_token": found.group(1)},
                  allow_redirects=False, timeout=30)
    return s, post, found.group(1)


def post_exam(session, base_url, csrf, title, school_id=None, **extra):
    """A draft paper through the JSON door, carrying no school of its own.

    The body names only columns this database actually has. That is not a detail:
    `WRITABLE_EXAM_FIELDS` on the deployed commit still lists six names
    production's `exams` does not carry (`mcq_percentage`, `essay_percentage`,
    `is_active`, `exam_rules`, `show_results`, `allow_retraction`), and PostgREST
    refuses the whole insert for one unknown name — which the door reports as a
    500, not a 400. A proof that sent those would be proving the schema drift.
    """
    body = {"title": title, "subject": "Matematika", "total_questions": 5,
            "duration_minutes": 60, "passing_score": 75}
    body.update(extra)
    if school_id is not None:
        body["school_id"] = school_id
    return session.post(base_url + "/exam/", json=body,
                        headers={"X-CSRF-Token": csrf}, timeout=30)


# ── cleanup ─────────────────────────────────────────────────────────────────

def clean_up(svc, fixture):
    print("\ncleanup")
    for exam_id in fixture["exams"]:
        svc.table("exams").delete().eq("id", exam_id).execute()
        print("  deleted exam", exam_id[:8])
    svc.table("teacher_school_membership").delete().eq(
        "user_id", fixture["user_id"]).execute()
    svc.table("profiles").delete().eq("id", fixture["user_id"]).execute()
    try:
        svc.auth.admin.delete_user(fixture["user_id"])
    except Exception as exc:  # noqa: BLE001
        print("  auth user not deleted:", exc)
    print("  removed fixture user {} and its memberships".format(fixture["user_id"][:8]))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.environ.get(
        "PROOF_BASE_URL", "https://scangrade.web.id"))
    parser.add_argument("--keep", action="store_true",
                        help="leave the fixture account, its memberships and its papers")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        print("cannot measure: SUPABASE_URL / SUPABASE_SERVICE_KEY are not in "
              "{}/".format(ROOT))
        return 2
    svc = create_client(url, key)
    home = school_by_npsn(svc, HOME_NPSN)
    second = school_by_npsn(svc, SECOND_NPSN)
    foreign = school_by_npsn(svc, FOREIGN_NPSN)

    print("project : {}".format(url))
    print("box     : {}".format(args.base_url))
    print("home    : {} {} ({})".format(home["npsn"], home["name"], home["id"]))
    print("second  : {} {} ({})".format(second["npsn"], second["name"], second["id"]))

    fixture: dict = {}
    try:
        make_fixture(svc, home, second, fixture)
        session, post, csrf = sign_in(args.base_url, fixture["email"], fixture["password"])
        check(post.status_code in (301, 302) and "teacher" in (post.headers.get("Location") or ""),
              "the fixture account signs in on the live box",
              "{} -> {}".format(post.status_code, post.headers.get("Location")))
        if post.status_code not in (301, 302):
            print(post.text[:400])

        # ── 1. two active memberships: the home school is where a paper lands ──
        print("\n1. two active memberships (no switcher deployed)")
        before_home = len(exams_of(svc, home["id"]))
        res = post_exam(session, args.base_url, csrf, TAG + " home wins")
        check(res.status_code == 201, "the JSON door minted the paper",
              "HTTP {}".format(res.status_code))
        if res.status_code == 201:
            row = exam_row(svc, res.json()["id"])
            fixture["exams"].append(row["id"])
            fixture["paper_home"] = row["id"]
            check(row["school_id"] == home["id"],
                  "the stored row is filed under the HOME school",
                  "row.school_id={} home={}".format(row["school_id"][:8], home["id"][:8]))
            check(len(exams_of(svc, home["id"])) == before_home + 1,
                  "and it is visible in that school's exam list")
        else:
            print(res.text[:400])

        # ── 2. the home membership closed: the request acts in the second school ─
        print("\n2. the home membership closed — the request resolves to {}".format(SECOND_NPSN))
        close_membership(svc, fixture["user_id"], home["id"])
        res = post_exam(session, args.base_url, csrf, TAG + " honored in second")
        check(res.status_code == 201, "the JSON door minted the paper",
              "HTTP {}".format(res.status_code))
        if res.status_code == 201:
            row = exam_row(svc, res.json()["id"])
            fixture["exams"].append(row["id"])
            fixture["paper_second"] = row["id"]
            check(row["school_id"] == second["id"],
                  "the stored row is filed under the SECOND school",
                  "row.school_id={} second={}".format(row["school_id"][:8],
                                                      second["id"][:8]))
            check(row["school_id"] != home["id"],
                  "and NOT under the home school on the profile")
        else:
            print(res.text[:400])

        # ── 3. a body that names a school ──────────────────────────────────────
        print("\n3. a body that names a school, while acting in the second")
        before_home, before_second = len(exams_of(svc, home["id"])), len(exams_of(svc, second["id"]))
        res = post_exam(session, args.base_url, csrf, TAG + " body names home",
                        school_id=home["id"])
        check(res.status_code == 400, "a body naming the home school is refused",
              "HTTP {} {}".format(res.status_code, res.text[:80]))
        res = post_exam(session, args.base_url, csrf, TAG + " body names foreign",
                        school_id=foreign["id"])
        check(res.status_code == 400, "a body naming a school it is not a member of is refused",
              "HTTP {} {}".format(res.status_code, res.text[:80]))
        check(len(exams_of(svc, home["id"])) == before_home
              and len(exams_of(svc, second["id"])) == before_second,
              "and neither refusal wrote a row")

        # ── 4. the read side is scoped to the same school ──────────────────────
        print("\n4. the list this session reads")
        listing = session.get(args.base_url + "/exam/", timeout=30)
        check(listing.status_code == 200, "GET /exam/ answers", "HTTP {}".format(listing.status_code))
        if listing.status_code == 200:
            ids = {r.get("id") for r in listing.json()}
            # The session is acting in the second school now, so the read is scoped
            # to it: the paper minted there is in the list, and the one minted in
            # the home school — a paper this account owns — is not. Ownership does
            # not widen the read.
            check(fixture.get("paper_second") in ids,
                  "the paper minted in the second school is in the list it reads")
            check(fixture.get("paper_home") not in ids,
                  "the paper minted in the home school is NOT")
    finally:
        if not fixture.get("user_id"):
            print("\nfixture was never created; nothing to clean up")
        elif args.keep:
            print("\n--keep: fixture left in place (user {})".format(fixture["user_id"]))
        else:
            clean_up(svc, fixture)

    failed = [label for ok, label in RESULTS if not ok]
    print("\n{} checks, {} failed".format(len(RESULTS), len(failed)))
    for label in failed:
        print("  FAILED:", label)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
